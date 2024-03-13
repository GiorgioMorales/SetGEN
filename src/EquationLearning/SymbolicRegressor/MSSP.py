import sys
import torch
import omegaconf
import sympy as sp
from src.utils import *
from sklearn.metrics import r2_score
from sklearn.linear_model import LinearRegression
from src.EquationLearning.models.NNModel import NNModel
from src.EquationLearning.Transformers.model import Model
from src.EquationLearning.Data.GenerateDatasets import DataLoader
from src.EquationLearning.Optimization.CoefficientFitting import FitGA
from src.EquationLearning.Transformers.GenerateTransformerData import Dataset
from src.EquationLearning.Trainer.TrainMultiSetTransformer import seq2equation


class SymbolicRegressor:

    def __init__(self, dataset: str = 'E1'):
        """Distills symbolic skeleton expressions given an experimental dataset"""
        # Define problem
        self.dataset = dataset
        dataLoader = DataLoader(name=self.dataset)
        self.X, self.Y, self.var_names, self.types = dataLoader.X, dataLoader.Y, dataLoader.names, dataLoader.types
        self.target_function = dataLoader.expr
        self.f_lambdified = sp.lambdify(sp.utilities.iterables.flatten(sp.sympify(dataLoader.names)), dataLoader.expr)
        self.limits = dataLoader.limits
        self.modelType = dataLoader.modelType
        self.n_features = self.X.shape[1]
        self.symbols = sp.symbols("{}:{}".format('x', self.n_features))
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        # Read config yaml
        try:
            self.cfg = omegaconf.OmegaConf.load("src/EquationLearning/Transformers/config.yaml")
        except FileNotFoundError:
            self.cfg = omegaconf.OmegaConf.load("../Transformers/config.yaml")

        # Initialize MST
        self.data_train_path = self.cfg.train_path
        self.training_dataset = Dataset(self.data_train_path, self.cfg.dataset_train, mode="train")
        self.word2id = self.training_dataset.word2id
        self.id2word = self.training_dataset.id2word
        self.model = Model(cfg=self.cfg.architecture, cfg_inference=self.cfg.inference, word2id=self.word2id)

        # Load models
        self._load_models()

        # Initialize class variables
        self.univariate_skeletons = []
        self.merged_expressions = []
        self.n_sets = 10
        self.n_samples = 3000

    def _load_models(self):
        # Define NN and load weights
        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        root = get_project_root()
        folder = os.path.join(root, "src//EquationLearning//models//saved_NNs//" + self.dataset)
        self.filepath = folder + "//weights-NN-" + self.dataset
        if os.path.exists(self.filepath.replace("weights", "NNModel") + '.pth'):
            # If this file exists, it means we saved the whole model
            network = torch.load(self.filepath.replace("weights", "NNModel") + '.pth')
            self.nn_model = NNModel(device=device, n_features=self.n_features, loaded_NN=network)
        elif os.path.exists(self.filepath):
            # If this file exists, initiate a model and load the weigths
            self.nn_model = NNModel(device=device, n_features=self.n_features, NNtype=self.modelType)
            self.nn_model.loadModel(self.filepath)
        else:
            # If neither files exist, we haven't trained a NN for this problem yet
            if self.n_features > 1:
                sys.exit("We haven't trained a NN for this problem yet. Use the TrainNNModel.py file first.")
        # Load weights of MST
        MST_path = os.path.join(root, "src//EquationLearning//models//saved_models/Model512-batch_12-Q1")
        self.model.load_state_dict(torch.load(MST_path))
        self.model.cuda()

    def get_skeleton(self):
        """Retrieve the estimated symbolic equation"""
        # Analyze each variable and obtain univariate expressions
        for iv, va in enumerate(self.symbols):
            print("********************************")
            print("Analyzing variable " + str(va))
            print("********************************")
            if iv >= 0:
                Rs = np.zeros(self.n_sets)
                # Generate multiple sets of data where only the current variable is allowed to vary
                if len(self.symbols) == 1:
                    if len(self.X) > self.n_samples:
                        ra = np.arange(0, len(self.X))
                        np.random.shuffle(ra)
                        ra = ra[0:self.n_samples]
                        self.X, self.Y = self.X[ra, :], self.Y[ra]
                    Xs = np.repeat(self.X, self.n_sets, axis=1)
                    Ys = np.repeat(self.Y[:, None], self.n_sets, axis=1)
                    Ys_real = Ys
                else:
                    Xs = np.zeros((self.n_samples, self.n_sets))
                    Ys = np.zeros((self.n_samples, self.n_sets))
                    Ys_real = np.zeros((self.n_samples, self.n_sets))
                    for ns in range(self.n_sets):
                        # Repeat the sampling process a few times and keep the one the looks more different from a line
                        R2s, XXs, YYs, valuess = [], [], [], []
                        for it in range(10):
                            # Sample random values for all the variables
                            values = np.zeros((len(self.symbols)))
                            for isy in range(len(self.symbols)):
                                if self.types[isy] == 'continuous':
                                    values[isy] = np.random.uniform(self.limits[isy][0], self.limits[isy][1])
                                else:
                                    range_values = np.linspace(self.limits[isy][0], self.limits[isy][1], 100)
                                    values[isy] = np.random.choice(range_values)
                            values = np.repeat(values[:, None], self.n_samples, axis=1)
                            # Sample values of the variable that is being analyzed
                            sample = np.random.uniform(self.limits[iv][0], self.limits[iv][1], self.n_samples)
                            values[iv, :] = sample
                            # Estimate the response of the generated set
                            Y = np.array(self.nn_model.evaluateFold(values.T, batch_size=values.shape[1]))[:, 0]
                            X = sample
                            # Fit linear regression model and calculate R2
                            model = LinearRegression()
                            model.fit(X[:, None], Y)
                            Y_pred = model.predict(X[:, None])
                            r2 = r2_score(Y, Y_pred)
                            if np.std(Y) < 0.3:
                                R2s.append(0)
                            else:
                                R2s.append(r2)
                            XXs.append(X.copy())
                            YYs.append(Y.copy())
                            valuess.append(values.copy())
                        sorted_indices = np.argsort(np.array(R2s))
                        ind = sorted_indices[3]
                        best_X, best_Y, best_values = XXs[ind], YYs[ind], valuess[ind]
                        Ys[:, ns] = best_Y
                        Xs[:, ns] = best_X
                        Ys_real[:, ns] = np.array(self.f_lambdified(*list(best_values)))
                        Rs[ns] = R2s[ind]
                # Normalize data
                sorted_indices = np.argsort(np.array(Rs))
                ind = sorted_indices[2]
                Xi, Yi = Xs[:, ind].copy(), Ys[:, ind].copy()
                means, std = np.mean(Ys, axis=0), np.std(Ys, axis=0)
                Ys = (Ys - means) / std
                means, std = np.mean(Ys_real, axis=0), np.std(Ys_real, axis=0)
                Ys_real = (Ys_real - means) / std

                # Format the data as inputs to the Multi-set transformer
                scaling_factor = 20 / (np.max(Xs) - np.min(Xs))
                Xs = (Xs - np.min(Xs)) * scaling_factor - 10
                # Xs = (Xs - np.mean(Xs))  # Xs = (Xs - np.min(Xs)) * scaling_factor - 10
                XY_block = torch.zeros((1, self.n_samples, 2, self.n_sets)).to(self.device)
                Xs, Ys = torch.from_numpy(Xs), torch.from_numpy(Ys)
                Xs = Xs.to(self.device)
                Ys = Ys.to(self.device)
                XY_block[0, :, 0, :] = Xs
                XY_block[0, :, 1, :] = Ys

                # Perform Multi-Set Skeleton Prediction
                preds = self.model.inference(XY_block)
                pred_skeletons = []
                for ip, pred in enumerate(preds):
                    try:
                        tokenized = list(pred[1].cpu().numpy())[1:]
                        skeleton = seq2equation(tokenized, self.id2word, printFlag=True)
                        skeleton = sp.sympify(skeleton.replace('x_1', str(va)))
                        pred_skeletons.append(skeleton)
                        print('Predicted skeleton ' + str(ip + 1) + ' for variable ' + str(va) + ': ' + str(skeleton))
                    except:  # TypeError:
                        print("Invalid response created by the model")

                print("\n Choosing the best skeleton...")
                best_error, best_sk = np.Infinity, ''
                # for ip, skeleton in enumerate(pred_skeletons):
                #     # Fit coefficients of the estimated skeletons
                #     problem = FitGA(skeleton, Xi, Yi, [np.min(Xi), np.max(Xi)], [-20, 20], max_it=100)
                #     est_expr, error = problem.run()
                #     print("\tSkeleton: " + str(skeleton) + ". Error: " + str(error))
                #     if error < best_error:
                #         best_error = error
                #         best_sk = skeleton
                #         if error < 0.001:  # If the error is very low, assume this is the best
                #             break
                # print("Selected skeleton: " + str(best_sk))
                #
                # self.univariate_skeletons.append(best_sk)

        return self.univariate_skeletons


if __name__ == '__main__':
    import matplotlib.pyplot as plt

    regressor = SymbolicRegressor(dataset='E8')
    regressor.get_skeleton()
