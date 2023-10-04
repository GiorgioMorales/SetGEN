import pickle
import omegaconf
from tqdm import trange
from src.utils import *
from src.EquationLearning.Transformers.GenerateTransformerData import Dataset, evaluate_and_wrap


def create_pickle_from_data(block, path, idx):
    with open(os.path.join(path, str(idx) + ".pkl"), 'wb') as file:
        pickle.dump(block, file)


class SampleData:
    """Pre-train transformer model using generated equations"""

    def __init__(self):
        """
        Initialize TransformerTrainer class
        """
        # Read config yaml
        try:
            self.cfg = omegaconf.OmegaConf.load("src/EquationLearning/Transformers/config.yaml")
        except FileNotFoundError:
            self.cfg = omegaconf.OmegaConf.load("config.yaml")

        # Read all equations
        self.data_train_path = self.cfg.train_path
        self.data_val_path = self.cfg.val_path
        self.training_dataset = Dataset(self.data_train_path, self.cfg.dataset_train, mode="train")
        self.validation_dataset = Dataset(self.data_val_path, self.cfg.dataset_val, mode="val")
        self.word2id = self.training_dataset.word2id

    def run(self):
        """Implement main training loop"""
        # Prepare list of indexes for shuffling

        batch = []
        count = 0
        n_batch = 0
        for step in trange(len(self.training_dataset)):  # Batch loop
            # print(step)
            try:
                sampled_data = evaluate_and_wrap(self.training_dataset[step], self.cfg.dataset_train, self.word2id)
            except:
                sampled_data = None

            if sampled_data is not None:
                count += 1
                batch.append(sampled_data)
                if count % 1000 == 0:
                    create_pickle_from_data(batch, "src/EquationLearning/Data/sampled_data/training", n_batch)
                    n_batch += 1
                    batch = []

        ########################################################################
        # Validation step
        ########################################################################
        # batch = []
        # count = 0
        # n_batch = 0
        # for step in trange(5000, len(self.validation_dataset)):
        #     try:
        #         sampled_data = evaluate_and_wrap(self.validation_dataset[step], self.cfg.dataset_train, self.word2id)
        #     except:
        #         sampled_data = None
        #
        #     if sampled_data is not None:
        #         count += 1
        #         batch.append(sampled_data)
        #         if count % 1000 == 0:
        #             len(batch)
        #             create_pickle_from_data(batch, "src/EquationLearning/Data/sampled_data/validation", n_batch)
        #             n_batch += 1
        #             batch = []


if __name__ == '__main__':
    sampler = SampleData()
    sampler.run()
