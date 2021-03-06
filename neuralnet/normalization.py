import numpy as np
import theano
from theano import tensor as T

from neuralnet import utils
from neuralnet.layers import *

__all__ = ['BatchNormLayer', 'BatchRenormLayer', 'DecorrBatchNormLayer', 'GroupNormLayer',
           'AdaptiveInstanceNorm2DLayer', 'InstanceNormLayer', 'LayerNormLayer', 'AdaIN2DLayer',
           'ConditionalInstanceNorm2DLayer']


class BatchNormLayer(Layer):
    def __init__(self, input_shape, layer_name='BN', epsilon=1e-4, running_average_factor=1e-1, axes='spatial',
                 activation='relu', no_scale=False, **kwargs):
        '''

        :param input_shape: (int, int, int, int) or (int, int)
        :param layer_name: str
        :param epsilon: float
        :param running_average_factor: float
        :param axes: 'spatial' or 'per-activation'
        '''
        super(BatchNormLayer, self).__init__(input_shape, layer_name)
        self.epsilon = np.float32(epsilon)
        self.running_average_factor = running_average_factor
        self.activation = utils.function[activation] if not callable(activation) else activation
        self.no_scale = no_scale
        self.axes = (0,) + tuple(range(2, len(input_shape))) if axes == 'spatial' else (0,)
        self.shape = (self.input_shape[1],) if axes == 'spatial' else self.input_shape[1:]
        self.kwargs = kwargs

        self.gamma_values = np.ones(self.shape, dtype=theano.config.floatX)
        self.gamma = theano.shared(np.copy(self.gamma_values), name=layer_name + '/gamma', borrow=True)

        self.beta_values = np.zeros(self.shape, dtype=theano.config.floatX)
        self.beta = theano.shared(np.copy(self.beta_values), name=layer_name + '/beta', borrow=True)

        self.running_mean = theano.shared(np.zeros(self.shape, dtype=theano.config.floatX),
                                          name=layer_name + '/running_mean', borrow=True)
        self.running_var = theano.shared(np.zeros(self.shape, dtype=theano.config.floatX),
                                         name=layer_name + '/running_var', borrow=True)

        self.params += [self.running_mean, self.running_var, self.gamma, self.beta]
        self.trainable += [self.beta] if self.no_scale else [self.beta, self.gamma]
        self.regularizable += [self.gamma] if not self.no_scale else []

        if activation == 'prelu':
            self.alpha = theano.shared(np.float32(.1), layer_name + '/alpha')
            self.params += [self.alpha]
            self.trainable += [self.alpha]
            self.kwargs['alpha'] = self.alpha

        self.descriptions = '{} Batch Norm Layer: {} -> {} running_average_factor = {:.4f} activation: {}' \
            .format(layer_name, self.input_shape, self.output_shape, self.running_average_factor, activation)

    def batch_normalization_train(self, input):
        out, _, _, mean_, var_ = T.nnet.bn.batch_normalization_train(input, self.gamma, self.beta, self.axes,
                                                                     self.epsilon, self.running_average_factor,
                                                                     self.running_mean, self.running_var)

        # Update running mean and variance
        # Tricks adopted from Lasagne implementation
        # http://lasagne.readthedocs.io/en/latest/modules/layers/normalization.html
        running_mean = theano.clone(self.running_mean, share_inputs=False)
        running_var = theano.clone(self.running_var, share_inputs=False)
        running_mean.default_update = mean_
        running_var.default_update = var_
        out += 0 * (running_mean + running_var)
        return out

    def batch_normalization_test(self, input):
        out = T.nnet.bn.batch_normalization_test(input, self.gamma, self.beta, self.running_mean, self.running_var,
                                                 axes=self.axes, epsilon=self.epsilon)
        return out

    def get_output(self, input, *args, **kwargs):
        return self.activation(self.batch_normalization_train(input) if self.training_flag
                               else self.batch_normalization_test(input), **self.kwargs)

    @property
    @utils.validate
    def output_shape(self):
        return tuple(self.input_shape)

    def reset(self):
        self.gamma.set_value(np.copy(self.gamma_values))
        self.beta.set_value(np.copy(self.beta_values))
        if self.activation is utils.function['prelu']:
            self.alpha.set_value(np.float32(.1))


class DecorrBatchNormLayer(BatchNormLayer):
    """
    From the paper "Decorrelated Batch Normalization" - Lei Huang, Dawei Yang, Bo Lang, Jia Deng
    """

    def __init__(self, input_shape, layer_name='DBN', epsilon=1e-4, running_average_factor=1e-1, activation='relu',
                 no_scale=False, **kwargs):
        """

        :param input_shape:
        :param layer_name:
        :param epsilon:
        :param running_average_factor:
        :param activation:
        :param no_scale:
        :param kwargs:
        """
        super(DecorrBatchNormLayer, self).__init__(input_shape, layer_name, epsilon, running_average_factor,
                                                   'per-activation', activation, no_scale, **kwargs)
        self.descriptions = '{} Decorrelated Batch Norm Layer: {} -> {} running_average_factor = {:.4f} activation: {}'.format(
            layer_name, self.input_shape, self.output_shape, self.running_average_factor, activation)

    def get_output(self, input, *args, **kwargs):
        eigh = T.nlinalg.Eigh()
        m, c, h, w = T.shape(input)

        X = input.dimshuffle((1, 0, 2, 3))
        X = X.flatten(2)
        Muy = T.mean(X, axis=1)
        X_centered = X - Muy
        Sigma = 1. / m * T.dot(X_centered, X_centered.T)
        W, D = eigh(Sigma)
        Z = T.dot(T.dot(D, T.nlinalg.diag(T.sqrt(W))), D.T)
        X = T.dot(Z, X)
        out = self.activation(self.batch_normalization_train(X.T) if self.training_flag
                              else self.batch_normalization_test(X.T), **self.kwargs)
        out = T.reshape(out.T, (c, m, h, w))
        out = out.dimshuffle((1, 0, 2, 3))
        return out


class GroupNormLayer(Layer):
    """
    Implementation of the paper "Group Normalization" - Wu et al.
    group = 1 -> Layer Normalization
    group = input_shape[1] -> Instance Normalization
    """

    def __init__(self, input_shape, layer_name='GN', groups=32, epsilon=1e-4, activation='relu', **kwargs):
        assert input_shape[1] / groups == input_shape[1] // groups, 'groups must divide the number of input channels.'

        super(GroupNormLayer, self).__init__(tuple(input_shape), layer_name)
        self.groups = groups
        self.epsilon = np.float32(epsilon)
        self.activation = utils.function[activation] if not callable(activation) else activation
        self.kwargs = kwargs
        self.gamma_values = np.ones(self.input_shape[1], dtype=theano.config.floatX)
        self.gamma = theano.shared(np.copy(self.gamma_values), name=layer_name + '/gamma', borrow=True)

        self.beta_values = np.zeros(self.input_shape[1], dtype=theano.config.floatX)
        self.beta = theano.shared(np.copy(self.beta_values), name=layer_name + '/beta', borrow=True)

        self.params += [self.gamma, self.beta]
        self.trainable += [self.gamma, self.beta]
        self.regularizable += [self.gamma]

        if activation == 'prelu':
            self.alpha = theano.shared(np.float32(.1), layer_name + '/alpha')
            self.params += [self.alpha]
            self.trainable += [self.alpha]
            self.kwargs['alpha'] = self.alpha

        self.descriptions = '{} Group Norm Layer: shape: {} -> {} activation: {}' \
            .format(layer_name, self.input_shape, self.output_shape, activation)

    def get_output(self, input, *args, **kwargs):
        gamma = self.gamma.dimshuffle('x', 0, 'x', 'x')
        beta = self.beta.dimshuffle('x', 0, 'x', 'x')
        if self.groups == 1:
            input_ = input.dimshuffle(1, 0, 2, 3)
            ones = T.ones_like(T.mean(input_, (0, 2, 3), keepdims=True), theano.config.floatX)
            zeros = T.zeros_like(T.mean(input_, (0, 2, 3), keepdims=True), theano.config.floatX)
            output, _, _ = T.nnet.bn.batch_normalization_train(input_, ones, zeros, 'spatial', self.epsilon)
            output = gamma * output.dimshuffle(1, 0, 2, 3) + beta
        elif self.groups == self.input_shape[1]:
            ones = T.ones_like(T.mean(input, (2, 3), keepdims=True), theano.config.floatX)
            zeros = T.zeros_like(T.mean(input, (2, 3), keepdims=True), theano.config.floatX)
            output, _, _ = T.nnet.bn.batch_normalization_train(input, ones, zeros, (2, 3))
            output = gamma * output + beta
        else:
            n, c, h, w = T.shape(input)
            input_ = T.reshape(input, (n, self.groups, -1, h, w))
            mean = T.mean(input_, (2, 3, 4), keepdims=True)
            var = T.var(input_, (2, 3, 4), keepdims=True)
            input_ = (input_ - mean) / T.sqrt(var + self.epsilon)
            input_ = T.reshape(input_, (n, c, h, w))
            output = gamma * input_ + beta
        return self.activation(output, **self.kwargs)

    @property
    @utils.validate
    def output_shape(self):
        return tuple(self.input_shape)

    def reset(self):
        self.gamma.set_value(np.copy(self.gamma_values))
        self.beta.set_value(np.copy(self.beta_values))
        if self.activation is utils.function['prelu']:
            self.alpha.set_value(np.float32(.1))


class BatchRenormLayer(BatchNormLayer):
    def __init__(self, input_shape, layer_name='BRN', epsilon=1e-4, r_max=1, d_max=0, running_average_factor=0.1,
                 axes='spatial', activation='relu', **kwargs):
        '''

        :param input_shape: (int, int, int, int) or (int, int)
        :param layer_name: str
        :param epsilon: float
        :param running_average_factor: float
        :param axes: 'spatial' or 'per-activation'
        '''
        super(BatchRenormLayer, self).__init__(input_shape, layer_name, epsilon, running_average_factor, axes,
                                               activation, False, **kwargs)
        self.r_max = theano.shared(np.float32(r_max), name=layer_name + 'rmax')
        self.d_max = theano.shared(np.float32(d_max), name=layer_name + 'dmax')
        self.descriptions = '{} Batch Renorm Layer: {} -> {} running_average_factor = {:.4f}'.format(layer_name,
                                                                                                     self.input_shape,
                                                                                                     self.output_shape,
                                                                                                     self.running_average_factor)

    def get_output(self, input, *args, **kwargs):
        batch_mean = T.mean(input, axis=self.axes)
        batch_std = T.sqrt(T.var(input, axis=self.axes) + 1e-10)
        r = T.clip(batch_std / T.sqrt(self.running_var + 1e-10), -self.r_max, self.r_max)
        d = T.clip((batch_mean - self.running_mean) / T.sqrt(self.running_var + 1e-10), -self.d_max, self.d_max)
        out = T.nnet.bn.batch_normalization_test(input, self.gamma, self.beta, batch_mean - d * batch_std / (r + 1e-10),
                                                 T.sqr(batch_std / (r + 1e-10)), axes=self.axes, epsilon=self.epsilon)
        if self.training_flag:
            # Update running mean and variance
            # Tricks adopted from Lasagne implementation
            # http://lasagne.readthedocs.io/en/latest/modules/layers/normalization.html
            m = T.cast(T.prod(input.shape) / T.prod(self.gamma.shape), theano.config.floatX)
            running_mean = theano.clone(self.running_mean, share_inputs=False)
            running_var = theano.clone(self.running_var, share_inputs=False)
            running_mean.default_update = running_mean + self.running_average_factor * (batch_mean - running_mean)
            running_var.default_update = running_var * (1. - self.running_average_factor) + \
                                         self.running_average_factor * (m / (m - 1)) * T.sqr(batch_std)
            out += 0 * (running_mean + running_var)
        return self.activation(out)


class AdaptiveInstanceNorm2DLayer(Layer):
    def __init__(self, input_shape, layer_name='Adaptive Instance Norm', epsilon=1e-5):
        super(AdaptiveInstanceNorm2DLayer, self).__init__(input_shape, layer_name)
        self.epsilon = epsilon
        self.descriptions = '{} Adaptive Instance Norm layer: {} -> {}'.format(layer_name, input_shape,
                                                                               self.output_shape)

    def get_output(self, input, params, *args, **kwargs):
        assert params.ndim == 2 or params.ndim == 4, \
            'The second element in input should either be a feature map or a concatenated matrix.'

        if params.ndim == 4:
            scale, bias = T.sqrt(T.var(params, (2, 3)) + 1e-8), T.mean(params, (2, 3))
        else:
            scale = params[:, :self.input_shape[1]].dimshuffle(0, 1, 'x', 'x')
            bias = params[:, self.input_shape[1]:].dimshuffle(0, 1, 'x', 'x')
        output, _, _ = T.nnet.bn.batch_normalization_train(input, scale, bias, (2, 3))
        return output

    @property
    @utils.validate
    def output_shape(self):
        return self.input_shape


class ConditionalInstanceNorm2DLayer(AdaptiveInstanceNorm2DLayer):
    def __init__(self, input_shape, noise_dim, layer_name='Conditional Instance Norm', epsilon=1e-5, activation='relu'):
        super(ConditionalInstanceNorm2DLayer, self).__init__(input_shape, layer_name, epsilon)
        self.noise_dim = noise_dim
        self.shift_conv = Conv2DLayer((None, noise_dim, 1, 1), input_shape[1], 1, no_bias=False, activation=activation,
                                      layer_name=layer_name + '/shift')
        self.scale_conv = Conv2DLayer((None, noise_dim, 1, 1), input_shape[1], 1, no_bias=False, activation=activation,
                                      layer_name=layer_name + '/scale')
        self.params += self.shift_conv.params + self.scale_conv.params
        self.trainable += self.shift_conv.trainable + self.scale_conv.trainable
        self.regularizable += self.shift_conv.regularizable + self.scale_conv.regularizable
        self.descriptions = '{} Conditional Instance Norm layer: {} -> {}'.format(layer_name, input_shape,
                                                                                  self.output_shape)

    def get_output(self, input, noise, *args, **kwargs):
        shift = T.addbroadcast(self.shift_conv(noise.dimshuffle(0, 1, 'x', 'x')), 2, 3)
        scale = T.addbroadcast(self.scale_conv(noise.dimshuffle(0, 1, 'x', 'x')), 2, 3)
        mean, std = T.mean(input, (2, 3), keepdims=True), T.sqrt(T.var(input, (2, 3), keepdims=True) + self.epsilon)
        normed = (input - mean) / std
        return normed * scale + shift


AdaIN2DLayer = AdaptiveInstanceNorm2DLayer


class InstanceNormLayer(GroupNormLayer):
    def __init__(self, input_shape, layer_name='IN', epsilon=1e-4, activation='relu', **kwargs):
        super(InstanceNormLayer, self).__init__(input_shape, layer_name, input_shape[1], epsilon, activation, **kwargs)
        self.descriptions = '{} Instance Norm Layer: shape: {} -> {} activation: {}' \
            .format(layer_name, self.input_shape, self.output_shape, activation)


class LayerNormLayer(GroupNormLayer):
    def __init__(self, input_shape, layer_name='LN', epsilon=1e-4, activation='relu', **kwargs):
        super(LayerNormLayer, self).__init__(input_shape, layer_name, 1, epsilon, activation, **kwargs)
        self.descriptions = '{} Layer Norm Layer: shape: {} -> {} activation: {}' \
            .format(layer_name, self.input_shape, self.output_shape, activation)
