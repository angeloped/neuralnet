import json
import numpy as np
import threading
import theano
from theano import tensor as T
from theano.sandbox.rng_mrg import MRG_RandomStreams as RandomStreams
thread_lock = threading.Lock()


class Thread(threading.Thread):
    def __init__(self, id, name, func):
        threading.Thread.__init__(self)
        self.id = id
        self.name = name
        self.func = func

    def run(self):
        print 'Starting ' + self.name
        thread_lock.acquire()
        self.outputs = self.func()
        thread_lock.release()


def load_configuration(file):
    try:
        with open(file) as f:
            data = json.load(f)
        print('Config file loaded successfully')
    except:
        raise NameError('Unable to open config file!!!')
    return data


def load_weights(weight_file, model):
    weights = np.load(weight_file).item()
    keys = sorted(weights.keys())
    num_weights = len(keys)
    j = 0
    for i, layer in enumerate(model):
        if j > num_weights - 1:
            break
        try:
            # w = weights[keys[j]]
            layer.b.set_value(weights[keys[j]][1])
            print('@ Layer %d %s %s' % (i, keys[j], np.shape(weights[keys[j]][1])))
            layer.W.set_value(weights[keys[j]][0])
            print('@ Layer %d %s %s' % (i, keys[j], np.shape(weights[keys[j]][0])))
        except:
            W_converted = fully_connected_to_convolution(weights[keys[j]], layer.filter_shape) \
                if hasattr(layer, 'filter_shape') else None

            if W_converted is not None:
                if W_converted.shape == layer.filter_shape:
                    layer.W.set_value(W_converted)
                    print('@ Layer %d %s %s' % (i, keys[j], layer.filter_shape))
                else:
                    print('No compatible parameters for layer %d %s found. '
                          'Random initialization is used' % (i, keys[j]))
            else:
                print('No compatible parameters for layer %d %s found. '
                      'Random initialization is used' % (i, keys[j]))
        j += 1
    print('Loaded successfully!')


# def load_weight2()

def fully_connected_to_convolution(weight, prev_layer_shape):
    weight = np.asarray(weight, 'float32')
    shape = weight.shape
    filter_size_square = shape[0] / prev_layer_shape[-2]
    filter_size = np.sqrt(filter_size_square)
    if filter_size == int(filter_size):
        filter_size = int(filter_size)
        return np.reshape(weight, [filter_size, filter_size, prev_layer_shape[-2], -1])
    else:
        return None


def unpool_2by2(input):
    return input.repeat(2, axis=2).repeat(2, axis=3)


def dropout(input, rng, dropout_on, p=0.5):
    """
    p: the probablity of dropping a unit
    """
    srng = RandomStreams(rng.randint(999999))

    # p=1-p because 1's indicate keep and p is prob of dropping
    mask = srng.binomial(n=1, p=1-p, size=input.shape)
    # The cast is important because
    # int * float32 = float64 which pulls things off the gpu
    if dropout_on:
        return input * T.cast(mask, theano.config.floatX)
    else:
        return input * (1.0 - p)


def maxout(input, maxout_size=4):
    maxout_out = None
    for i in xrange(maxout_size):
        t = input[:, i::maxout_size]
        if maxout_out is None:
            maxout_out = t
        else:
            maxout_out = T.maximum(maxout_out, t)
    return maxout_out


def lrelu(x, alpha=1e-2):
    return T.nnet.relu(x, alpha=alpha)


def linear(x):
    return x


def ramp(x):
    left = T.switch(x < 0, 0, x)
    return T.switch(left > 1, 1, left)


def prelu(x, alpha=0.01):
    f1 = 0.5 * (1 + alpha)
    f2 = 0.5 * (1 - alpha)
    return f1 * x + f2 * abs(x)


def update_input(data, shared_vars, no_response=False, **kwargs):
    if not no_response:
        x, y = data
        shape_y = shared_vars[1].get_value().shape if 'shape_y' not in kwargs else kwargs['shape_y']
        shape_x = shared_vars[0].get_value().shape if 'shape_x' not in kwargs else kwargs['shape_x']
        try:
            x = np.reshape(np.asarray(x, dtype=theano.config.floatX), shape_x)
            y = np.reshape(np.asarray(y, dtype=shared_vars[1].dtype), shape_y)
        except ValueError:
            raise ValueError('Input of the shared variable must have the same shape with the shared variable')
        shared_vars[0].set_value(x, borrow=True)
        shared_vars[1].set_value(y, borrow=True)
    else:
        x = data
        shape_x = shared_vars.get_value().shape if 'shape_x' not in kwargs else kwargs['shape_x']
        try:
            x = np.reshape(np.asarray(x, dtype=theano.config.floatX), shape_x)
        except ValueError:
            raise ValueError('Input of the shared variable must have the same shape with the shared variable')
        shared_vars.set_value(x, borrow=True)


def generator(data, batch_size, no_response=False):
    if not no_response:
        x, y = data
    else:
        x = data
    num_batch = np.asarray(x).shape[0] / batch_size
    for i in xrange(num_batch):
        yield (x[i*batch_size:(i+1)*batch_size], y[i*batch_size:(i+1)*batch_size]) if not no_response \
            else x[i*batch_size:(i+1)*batch_size]


def shared_dataset(data_xy):
    data_x, data_y = data_xy
    shared_x = theano.shared(np.asarray(data_x, dtype=theano.config.floatX))
    shared_y = theano.shared(np.asarray(data_y, dtype=theano.config.floatX))
    return shared_x, T.cast(shared_y, 'int32')

function = {'relu': T.nnet.relu, 'sigmoid': T.nnet.sigmoid, 'tanh': T.tanh, 'lrelu': lrelu,
            'softmax': T.nnet.softmax, 'linear': linear, 'elu': T.nnet.elu, 'ramp': ramp, 'maxout': maxout,
            'sin': T.sin, 'cos': T.cos}
