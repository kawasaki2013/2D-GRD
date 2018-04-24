from logzero import logger
import argparse
import numpy as np
import pandas as pd
import os

import chainer
import chainer.functions as F
import chainer.links as L
from chainer import training
from chainer import serializers
from chainer.training import extensions
from chainer.datasets import tuple_dataset


class Network(chainer.Chain):

    def __init__(self, n_units, n_out):
        super(Network, self).__init__()
        with self.init_scope():
            self.lstm = L.LSTM(None, n_out)
            self.fc = L.Linear(None, n_out)

    def __call__(self, x):
        self.lstm.reset_state()
        h = self.lstm(x)
        return self.fc(h)

    def reset_state(self):
        self.lstm.reset_state()


class LSTMUpdater(training.updaters.StandardUpdater):

    def __init__(self, train_iter, optimizer, device):
        super(LSTMUpdater, self).__init__(
            train_iter, optimizer, device=device)

    # The core part of the update routine can be customized by overriding.
    def update_core(self):
        train_iter = self.get_iterator('main')
        optimizer = self.get_optimizer('main')

        batch = train_iter.__next__()
        x, t = self.converter(batch, self.device)
        loss = optimizer.target(chainer.Variable(x), chainer.Variable(t))

        optimizer.target.cleargrads()  # Clear the parameter gradients
        loss.backward()  # Backprop
        loss.unchain_backward()  # Truncate the graph
        optimizer.update()  # Update the parameters


class LSTM:
    def __init__(self, config):
        self._process = config.process
        self._batch_size = config.batch_size
        self._epochs = config.epochs
        self._learning_rate = config.learning_rate
        self._model_params_path = config.model_params_path
        self._cols_size = config.columns_size
        self._x_input_length = config.x_input_length
        self._x_split_step = config.x_split_step
        self._lstm_units = config.lstm_units
        self._training_dataset_path = None
        if hasattr(config, 'training_dataset_path') :
            self._training_dataset_path = config.training_dataset_path
        self._validation_dataset_path = None
        if hasattr(config, 'validation_dataset_path') :
            self._validation_dataset_path = config.validation_dataset_path
        self._evaluation_dataset_path = None
        if hasattr(config, 'evaluation_dataset_path') :
            self._evaluation_dataset_path = config.evaluation_dataset_path 
        self._frequency = -1
        if hasattr(config, 'frequency') :
            self._frequency = config.frequency
        self._gpu = -1
        if hasattr(config, 'gpu') :
            self._gpu = config.gpu
        self._out = ''
        if hasattr(config, 'out') :
            self._out = config.out
        self._resume = ''
        if hasattr(config, 'resume') :
            self._resume = config.resume
        self._plot = ''
        if hasattr(config, 'plot') :
            self._plot = config.plot
        self._model = None

    def create_model(self, n_units, n_out):
        self._model = L.Classifier(Network(n_units, n_out), lossfun=F.mean_squared_error)
        self._model.compute_accuracy = False
        return self._model

    def load_dataset(self, dataset_path, x_col_name='x', x_input_length=16, x_split_step=1):
        logger.info("Load a dataset from {}.".format(dataset_path))
        dataset_dirpath = os.path.dirname(dataset_path)
        xinlist = []
        xoutlist = []
        indexcsv = pd.read_csv(dataset_path)
        for cell in indexcsv[x_col_name]:
            df = pd.read_csv(os.path.join(dataset_dirpath, cell), header=None)
            series = np.float32(df.as_matrix())
            i_last = series.shape[0] - x_input_length - 1
            for i in range(0, i_last, x_split_step):
                past = series[i:i+x_input_length,:]
                current = series[i+x_input_length-1,:]
                future = series[i+x_input_length:i+x_input_length+1,:]
                xin = np.subtract(past, current)
                xout = np.subtract(future, current)
                xinlist.append(xin)
                xoutlist.append(xout.flatten())
        return tuple_dataset.TupleDataset(xinlist, xoutlist)

    # Load the model
    def _load_model(self, model_params_path='model.npz'):
        serializers.load_npz(model_params_path, self._model)
        return self._model

    # Save the model
    def _save_model(self, model_params_path='model.npz'):
        return serializers.save_npz(model_params_path, self._model)

    # Training
    def train(self):
        self._model = self.create_model(self._lstm_units, self._cols_size)
        if self._gpu >= 0:
            # Make a specified GPU current
            chainer.backends.cuda.get_device_from_id(self._gpu).use()
            self._model.to_gpu()  # Copy the model to the GPU

        optimizer = chainer.optimizers.Adam(alpha=self._learning_rate)
        optimizer.setup(self._model)

        train_dataset = self.load_dataset(self._training_dataset_path)
        valid_dataset = self.load_dataset(self._validation_dataset_path)
        train_iter = chainer.iterators.SerialIterator(train_dataset, self._batch_size)
        valid_iter = chainer.iterators.SerialIterator(valid_dataset, self._batch_size,
                                                 repeat=False, shuffle=False)

        eval_model = self._model.copy()
        eval_pred = eval_model.predictor
        eval_pred.train = False

        # Set up a trainer
        updater = LSTMUpdater(train_iter, optimizer, device=self._gpu)
        trainer = training.Trainer(updater, (self._epochs, 'epoch'), out=self._out)
        trainer.extend(extensions.Evaluator(valid_iter, eval_model, device=self._gpu, eval_hook=lambda _: eval_pred.reset_state()))
        trainer.extend(extensions.dump_graph('main/loss'))
        frequency = self._epochs if self._frequency == -1 else max(1, self._frequency)
        trainer.extend(extensions.snapshot(), trigger=(frequency, 'epoch'))
        trainer.extend(extensions.LogReport())
        if self._plot and extensions.PlotReport.available():
            trainer.extend(
                extensions.PlotReport(['main/loss', 'validation/main/loss'],
                                      'epoch', file_name='loss.png'))
        trainer.extend(extensions.PrintReport(
            ['epoch', 'main/loss', 'validation/main/loss', 'elapsed_time']))
        trainer.extend(extensions.ProgressBar())
        if self._resume:
            chainer.serializers.load_npz(self._resume, trainer)
        trainer.run()
        self._save_model(self._model_params_path)

    # Evaluation
    def evaluate(self):
        test_dataset = self.load_dataset(self._evaluation_dataset_path)
        if self._gpu >= 0:
            # Make a specified GPU current
            chainer.backends.cuda.get_device_from_id(self._gpu).use()
            self._model.to_gpu()  # Copy the model to the GPU
        self._model = self.create_model(self._lstm_units, self._cols_size)
        self._model = self._load_model(self._model_params_path)

        test_iter = chainer.iterators.SerialIterator(test_dataset, self._batch_size,
                                                 repeat=False, shuffle=False)

        self._model.predictor.reset_state()
        test_evaluator = extensions.Evaluator(test_iter, self._model, device=self._gpu)
        results = test_evaluator()
        print("test loss : ", results['main/loss'])

    # Initializer for inference
    def init_for_infer(self):
        self._model = self.create_model(self._lstm_units, self._cols_size)
        if self._gpu >= 0:
            # Make a specified GPU current
            chainer.backends.cuda.get_device_from_id(self._gpu).use()
            self._model.to_gpu()  # Copy the model to the GPU
        self._model = self._load_model(self._model_params_path)

    # Inference
    def infer(self, x):
        x = np.float32(x)
        x = self._model.xp.asarray(x.reshape(1, x.shape[0], x.shape[1]))
        self._model.predictor.reset_state()
        with chainer.using_config('train', False), \
                    chainer.using_config('enable_backprop', False):
            y = self._model.predictor(x)
        result = chainer.backends.cuda.to_cpu(y.data)
        return result

def get_args(model_params_path='model.npz', training_dataset_path="trining.csv",
        validation_dataset_path="validation.csv", evaluation_dataset_path="evaluation.csv",
        epochs=100, learning_rate=0.001, batch_size=100, columns_size=2,
        x_input_length=16, x_split_step=1, lstm_units=32, gpu=-1, process="train", 
        description=None):
    if description is None:
        description = "LSTM"
    parser = argparse.ArgumentParser(description)
    parser.add_argument("--batch-size", "-b", type=int, default=batch_size)
    parser.add_argument("--learning-rate", "-r",
                        type=float, default=learning_rate)
    parser.add_argument("--epochs", "-e", type=int, default=epochs,
                        help='Epochs of training.')
    parser.add_argument("--model-params-path", "-m",
                        type=str, default=model_params_path,
                        help='Path of the model parameters file.')
    parser.add_argument("--training-dataset-path", "-dt",
                        type=str, default=training_dataset_path,
                        help='Path of the training dataset.')
    parser.add_argument("--validation-dataset-path", "-dv",
                        type=str, default=validation_dataset_path,
                        help='Path of the validation dataset.')
    parser.add_argument("--evaluation-dataset-path", "-de",
                        type=str, default=evaluation_dataset_path,
                        help='Path of the evaluation dataset.')
    parser.add_argument('--process', '-p', type=str,
                        default='train', help="(train|evaluate|infer).")
    parser.add_argument("--x-input-length", "-xil", type=int, default=x_input_length,
                        help='Length of time-series into the network.')
    parser.add_argument("--x-split-step", "-xss", type=int, default=x_split_step,
                        help='Step size to split time-series.')
    parser.add_argument("--columns-size", "-cs", type=int, default=columns_size,
                        help='Columns size of time-series matrix.')
    parser.add_argument("--lstm-units", "-lstmu", type=int, default=lstm_units,
                        help='The number of LSTM units.')
    parser.add_argument('--frequency', '-f', type=int, default=-1,
                        help='Frequency of taking a snapshot')
    parser.add_argument('--gpu', '-g', type=int, default=gpu,
                        help='GPU ID (negative value indicates CPU)')
    parser.add_argument('--out', '-o', default='chainer_lstm_result',
                        help='Directory to output the result')
    parser.add_argument('--resume', '-re', default='',
                        help='Resume the training from snapshot')
    parser.add_argument('--plot', action='store_true',
                        help='Enable PlotReport extension')
    args = parser.parse_args()
    return args


if __name__ == '__main__':
    config = get_args()

    model = LSTM(config)
    if config.process == 'train':
        model.train()
    elif config.process == 'evaluate':
        model.evaluate()
    elif config.process == 'infer':
        logger.info("Load a dataset from {}.".format(config.evaluation_dataset_path))
        model.init_for_infer()
        test_dataset = model.load_dataset(config.evaluation_dataset_path)
        for i in range(len(test_dataset)):
            x = test_dataset[i]
            result = model.infer(x[0])
            print("inference result = {}, true label = {}".format(result, x[1]))
