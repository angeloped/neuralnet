import utils
from buildstructure import Structure
from buildcost import Optimization


class Model(Optimization, Structure):
    def __init__(self, config_file, **kwargs):
        super(Model, self).__init__(config_file, **kwargs)
        self.config = utils.load_configuration(config_file)
        self.build_model(**kwargs)
        self.n_epochs = self.config['training']['n_epochs']
        self.continue_training = self.config['training']['continue']
        self.multi_gpus = self.config['training']['multi_gpus']
        self.display_cost = self.config['training']['display_cost']
        self.batch_size_testing = self.config['testing']['batch_size']
        self.get_test_output = self.config['testing']['get_output']
        self.summary_dir = self.config['summary_dir']
        self.save_model = self.config['save_load']['save_model']
        self.checkpoint = self.config['save_load']['checkpoint']
        self.checkpoint_dir = self.config['save_load']['checkpoint_dir']
        self.extract_params = self.config['save_load']['extract_params']
        self.param_file_to_save = self.config['save_load']['param_file']

    def __iter__(self):
        return self

    def next(self):
        if len(self.layers) == 0 or self.index == len(self.layers):
            raise StopIteration
        else:
            self.index += 1
            return self.layers[self.index - 1]

    def build_model(self, **kwargs):
        super(Model, self).build_model(**kwargs)

    def inference(self, input, **kwargs):
        return super(Model, self).inference(input=input)

    def build_cost(self, y, **kwargs):
        y_pred = kwargs.get('output', self.output[-1])
        kwargs['model'] = self.layers if 'model' not in kwargs else kwargs['model']
        return super(Model, self).build_cost(y_pred, y, **kwargs)

    def build_optimization(self, **kwargs):
        cost_to_optimize = self.cost if 'cost' not in kwargs else kwargs['cost']
        kwargs['model'] = self.layers if 'model' not in kwargs else kwargs['model']
        return super(Model, self).build_optimization(cost_to_optimize, **kwargs)
