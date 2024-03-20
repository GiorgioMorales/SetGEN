import glob

import sympy
import torch
import omegaconf
from torch import nn
from tqdm import trange
from src.utils import *
from torch import optim
import matplotlib.pyplot as plt
from torch.nn.utils.rnn import pad_sequence
from torch.utils.tensorboard import SummaryWriter
from src.EquationLearning.Transformers.model import Model
from src.EquationLearning.Transformers.GenerateTransformerData import Dataset, de_tokenize


def open_pickle(path):
    with open(path, 'rb') as file:
        block = pickle.load(file)
    return block


def open_h5(path):
    block = []
    with h5py.File(path, "r") as hf:
        # Iterate through the groups in the HDF5 file (group names are integers)
        for group_name in hf:
            group = hf[group_name]
            # Read data from the group
            X = group["X"][:]
            Y = group["Y"][:]
            # Load 'tokenized' as a list of integers
            tokenized = list(group["tokenized"])
            # Load 'exprs' as a string
            exprs = group["exprs"][()].tobytes().decode("utf-8")
            # Load 'sampled_exprs' as a list of sympy expressions
            sampled_exprs = [expr_str for expr_str in group["sampled_exprs"][:].astype(str)]
            block.append([X, Y, tokenized, exprs, sampled_exprs])
    return block


def seq2equation(tokenized, id2word, printFlag=False):
    prefix = de_tokenize(tokenized, id2word)
    if printFlag:
        print("Prefix notation: " + str(prefix))
    env, param, config_dict = create_env(os.path.join(get_project_root(), "dataset_configuration.json"))
    infix = env.prefix_to_infix(prefix, coefficients=env.coefficients, variables=env.variables)
    return infix


def loss_sample(output, trg):
    """Loss function that combines cross-entropy and information entropy for a single sample"""
    ce = nn.CrossEntropyLoss(ignore_index=0)
    ce.cuda()
    return ce(output, trg)


class TransformerTrainer:
    """Pre-train transformer model using generated equations"""

    def __init__(self):
        """
        Initialize TransformerTrainer class
        """
        # Read config yaml
        try:
            self.cfg = omegaconf.OmegaConf.load("src/EquationLearning/Transformers/config.yaml")
        except FileNotFoundError:
            self.cfg = omegaconf.OmegaConf.load("../Transformers/config.yaml")

        # Read all equations
        self.sampledData_train_path = 'src/EquationLearning/Data/sampled_data/' + self.cfg.dataset + '/training'
        self.sampledData_val_path = 'src/EquationLearning/Data/sampled_data/' + self.cfg.dataset + '/validation'
        self.data_train_path = self.cfg.train_path
        self.training_dataset = Dataset(self.data_train_path, self.cfg.dataset_train, mode="train")
        self.word2id = self.training_dataset.word2id
        self.id2word = self.training_dataset.id2word

        # Load model
        self.model = Model(cfg=self.cfg.architecture, cfg_inference=self.cfg.inference, word2id=self.word2id,
                           loss=loss_sample)
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        self.model.cuda()
        if torch.cuda.device_count() > 1:
            print("Let's use", torch.cuda.device_count(), "GPUs!")
            self.model = nn.DataParallel(self.model)

        # Training parameters
        self.optimizer = optim.Adadelta(self.model.parameters(), lr=self.cfg.architecture.lr)
        self.writer = SummaryWriter('runs')
        self.lambda_ = self.cfg.dataset_train.lambda_

    def sample_domain(self, Xs, Ys, equations):
        """Use a random domain (e.g., between -10 and 10, or -5 and 5, etc)"""
        dva = np.random.randint(3, 10)
        X, Y = np.zeros((self.cfg.architecture.block_size, self.cfg.architecture.number_of_sets)), np.zeros(
            (self.cfg.architecture.block_size, self.cfg.architecture.number_of_sets))
        ns = 0
        while ns < self.cfg.architecture.number_of_sets:
            minX, maxX = -dva, dva
            # Select rows where the value of the first column is between minX and maxX
            selected_rows_indices = np.where((Xs[:, ns] >= minX) & (Xs[:, ns] <= maxX))[0]
            remaining = self.cfg.architecture.block_size - len(selected_rows_indices)
            # Randomly select 'self.cfg.architecture.block_size' rows from the selected rows
            if len(selected_rows_indices) > self.cfg.architecture.block_size:
                selected_rows_indices = np.random.choice(selected_rows_indices, self.cfg.architecture.block_size, replace=False)
            elif len(selected_rows_indices) < self.cfg.architecture.block_size and remaining < 200:
                try:
                    selected_rows_indices = list(selected_rows_indices)
                    selected_rows_indices += list(np.random.choice(np.array(selected_rows_indices), remaining, replace=False))
                    selected_rows_indices = np.array(selected_rows_indices)
                except ValueError:
                    ns, dva = 0, dva + 1  # If it failed, try with a larger domain
                    continue
            elif len(selected_rows_indices) < self.cfg.architecture.block_size and remaining >= 200:
                ns, dva = 0, dva + 1  # If it failed, try with a larger domain
                continue

            X[:, ns] = Xs[:, ns][selected_rows_indices]
            scaling_factor = 20 / (np.max(X[:, ns]) - np.min(X[:, ns]))
            X[:, ns] = (X[:, ns] - np.min(X[:, ns])) * scaling_factor - 10
            Y[:, ns] = Ys[:, ns][selected_rows_indices]
            ns += 1
        # With a chance of 0.3, fix all sets to the same function
        if np.random.random(1) < 0.3:
            ns = np.random.randint(0, self.cfg.architecture.number_of_sets)
            X[:, 0:] = X[:, ns][:, np.newaxis]
            Y[:, 0:] = Y[:, ns][:, np.newaxis]
            equations = [equations[ns]] * self.cfg.architecture.number_of_sets
        return X, Y, equations

    def fit(self):
        """Implement main training loop"""
        epochs = self.cfg.epochs
        batch_size = self.cfg.batch_size
        # Get names of training and val blocks
        train_files = glob.glob(os.path.join(self.sampledData_train_path, '*.h5'))
        val_files = glob.glob(os.path.join(self.sampledData_val_path, '*.h5'))
        # Prepare list of indexes for shuffling
        indexes = np.arange(len(train_files))

        # self.model.load_state_dict(torch.load('src/EquationLearning/models/saved_models/Model-' + self.cfg.dataset))
        print("""""""""""""""""""""""""""""")
        print("Start training")
        print("""""""""""""""""""""""""""""")
        global_batch_count = 0
        for epoch in range(epochs):  # Epoch loop
            # Shuffle indices
            np.random.shuffle(indexes)

            batch_count = 0
            for b_ind in indexes:  # Block loop (each block contains 8000 inputs)
                # Read block
                block = open_h5(train_files[b_ind])

                # Format elements in the block as torch Tensors
                XY_block = torch.zeros((len(block), self.cfg.architecture.block_size, 2, self.cfg.architecture.number_of_sets))
                skeletons_block = []
                xpr_block = []
                remove_indices = []
                for ib, b in enumerate(block):
                    Xs, Ys, tokenized, xpr, equations = b
                    Xs = Xs[:, :self.cfg.architecture.number_of_sets]
                    Ys = Ys[:, :self.cfg.architecture.number_of_sets]
                    Xs, Ys, _ = self.sample_domain(Xs, Ys, equations)

                    # Shuffle data
                    for d in range(self.cfg.architecture.number_of_sets):
                        indices = np.arange(Xs.shape[0])
                        np.random.shuffle(indices)
                        Xs[:, d] = Xs[indices, d]
                        Ys[:, d] = Ys[indices, d]
                    # Normalize data
                    means, std = np.mean(Ys, axis=0), np.std(Ys, axis=0)
                    Ys = (Ys - means) / std

                    if np.isnan(Ys).any() or np.min(std) < 0.01 or 'E' in xpr:
                        remove_indices.append(ib)
                    else:
                        if isinstance(Xs, np.ndarray):  # Some blocks were stored as numpy arrays and others as tensors
                            Xs, Ys = torch.from_numpy(Xs), torch.from_numpy(Ys)
                        XY_block[ib, :, 0, :] = Xs
                        XY_block[ib, :, 1, :] = Ys
                        skeletons_block.append(torch.tensor(tokenized).long().cuda())
                        xpr_block.append(xpr)

                # Create a mask to exclude rows with specified indices
                mask = torch.ones(XY_block.shape[0], dtype=torch.bool, device=XY_block.device)
                mask[remove_indices] = 0
                # Use torch.index_select to select rows based on the mask
                XY_block = torch.index_select(XY_block, dim=0, index=mask.nonzero().squeeze()).cuda()

                if torch.cuda.device_count() > 1:
                    self.model.module.set_train()  # Sets training mode
                else:
                    self.model.set_train()  # Sets training mode
                running_loss = 0.0
                inds = np.arange(XY_block.shape[0])
                np.random.shuffle(inds)
                T = np.ceil(1.0 * XY_block.shape[0] / batch_size).astype(np.int32)
                for step in range(T):  # Batch loop

                    # Generate indexes of the batch
                    batch_inds = inds[step * batch_size:(step + 1) * batch_size]
                    print("Block " + str(train_files[b_ind]) + " Sample " + str(batch_inds[0]) + " Expr: " + str(xpr_block[batch_inds[0]]))
                    # Extract slices
                    XY_batch = XY_block[batch_inds, :, :, :]
                    skeletons_batch = [skeletons_block[i] for i in batch_inds]
                    # Check that there's no skeleton larger than the maximum length
                    valid_inds = [i for i in range(len(skeletons_batch)) if len(skeletons_batch[i]) < self.cfg.architecture.length_eq]
                    XY_batch = XY_batch[valid_inds, :, :, :]
                    skeletons_batch = [skeletons_batch[i] for i in valid_inds]

                    # Find the maximum skeleton length
                    max_length = max(len(sk) for sk in skeletons_batch)
                    # Pad the skeletons to match the maximum length
                    padded_tensors = [torch.cat((sk, torch.zeros(max_length - len(sk)).cuda())) for sk in
                                      skeletons_batch]
                    # Combine the padded skeletons into a single tensor
                    skeletons_batch = pad_sequence(padded_tensors, batch_first=True).type(torch.int).cuda()

                    # Zero the parameter gradients
                    self.optimizer.zero_grad()

                    # Forward pass
                    if torch.cuda.device_count() > 1:
                        output, z_sets, L1 = self.model.forward(XY_batch.cuda(), skeletons_batch.cuda())
                        # Aggregate loss terms in the batch
                        L1 = L1.sum()
                    else:
                        output, z_sets = self.model.forward(XY_batch.cuda(), skeletons_batch.cuda())
                        # Loss calculation
                        L1 = torch.zeros(1).cuda()
                        for bi in range(output.shape[1]):
                            out = output[:, bi, :].contiguous().view(-1, output.shape[-1])
                            tokenized = skeletons_batch[bi, :][1:].contiguous().view(-1)
                            L1s = loss_sample(out, tokenized.long())
                            L1 += L1s

                    loss = L1 / len(valid_inds)  # + self.lambda_ * L2) / (batch_size - skipped)
                    # Gradient computation
                    loss.backward()
                    # Optimization step
                    self.optimizer.step()

                    # Print statistics
                    batch_count += 1
                    global_batch_count += 1
                    running_loss += loss.item()
                    if batch_count % 5 == 0:
                        print('[%d, %5d] loss: %.5f' % (epoch + 1, batch_count, running_loss / 5))
                        self.writer.add_scalar('training loss', running_loss / 5, global_batch_count)
                        running_loss = 0.0

            if epoch == 0:  # Save model at the end of the first epoch in case there's an error during validation
                torch.save(self.model.state_dict(),
                           'src/EquationLearning/models/saved_models/Model-' + self.cfg.dataset)
            #########################################################################
            # Validation step
            #########################################################################
            indexes2 = np.arange(len(val_files))
            batch_val_size = 1
            if torch.cuda.device_count() > 1:
                self.model.module.set_eval()
            else:
                self.model.set_eval()
            L1v, L2v, iv = 0, 0, 0
            prev_loss = np.inf

            cc = 0
            for b_ind in indexes2:  # Block loop (each block contains 1000 inputs)
                # Read block
                block = open_h5(val_files[b_ind])

                # Format elements in the block as torch Tensors
                XY_block = torch.zeros(
                    (len(block), self.cfg.architecture.block_size, 2, self.cfg.architecture.number_of_sets))
                skeletons_block = []
                remove_indices = []
                for ib, b in enumerate(block):
                    Xs, Ys, tokenized, xpr, equations = b
                    # Normalize data
                    Xs = Xs[:, :self.cfg.architecture.number_of_sets]
                    Ys = Ys[:, :self.cfg.architecture.number_of_sets]
                    Xs, Ys, _ = self.sample_domain(Xs, Ys, equations)
                    means, std = np.mean(Ys, axis=0), np.std(Ys, axis=0)
                    Ys = (Ys - means) / std
                    if isinstance(Xs, np.ndarray):  # Some blocks were stored as numpy arrays and others as tensors
                        Xs, Ys = torch.from_numpy(Xs), torch.from_numpy(Ys)
                    XY_block[ib, :, 0, :] = Xs[:, :self.cfg.architecture.number_of_sets]
                    XY_block[ib, :, 1, :] = Ys[:, :self.cfg.architecture.number_of_sets]
                    skeletons_block.append(torch.tensor(tokenized).long().cuda())

                # Create a mask to exclude rows with specified indices
                mask = torch.ones(XY_block.shape[0], dtype=torch.bool, device=XY_block.device)
                mask[remove_indices] = 0
                # Use torch.index_select to select rows based on the mask
                XY_block = torch.index_select(XY_block, dim=0, index=mask.nonzero().squeeze()).cuda()

                inds = np.arange(XY_block.shape[0])
                T = np.ceil(1.0 * XY_block.shape[0] / batch_val_size).astype(np.int32)
                for step in range(T):  # Batch loop
                    # Generate indexes of the batch
                    batch_inds = inds[step * batch_val_size:(step + 1) * batch_val_size]
                    # Extract slices
                    XY_batch = XY_block[batch_inds, :, :, :]
                    skeletons_batch = [skeletons_block[i] for i in batch_inds]
                    # Check that there's no skeleton larger than the maximum length
                    valid_inds = [i for i in range(len(skeletons_batch)) if
                                  len(skeletons_batch[i]) < self.cfg.architecture.length_eq]
                    XY_batch = XY_batch[valid_inds, :, :, :]
                    skeletons_batch = [skeletons_batch[i] for i in valid_inds]

                    # Find the maximum skeleton length
                    max_length = max(len(sk) for sk in skeletons_batch)
                    # Pad the skeletons to match the maximum length
                    padded_tensors = [torch.cat((sk, torch.zeros(max_length - len(sk)).cuda())) for sk in
                                      skeletons_batch]
                    # Combine the padded skeletons into a single tensor
                    skeletons_batch = pad_sequence(padded_tensors, batch_first=True).type(torch.int)
                    # Forward pass
                    # R = self.model.inference(XY_batch[0:1, :, :, :])
                    output = self.model.validation_step(XY_batch, skeletons_batch)
                    # Loss calculation
                    for bi in range(output.shape[1]):
                        out = output[:, bi, :].contiguous().view(-1, output.shape[-1])
                        tokenized = skeletons_batch[bi, :][1:].contiguous().view(-1)
                        L1s = loss_sample(out, tokenized.long())
                        L1v += L1s
                        iv += 1
                        try:
                            res = output.cpu().numpy()[:, 0, :]
                            max_indices = np.argmax(res, axis=1)
                            # prefix = self.model.inference(XY_batch[step:step + 1, :, :, :])[1][1].cpu().numpy()[1:]
                            infix = sympy.sympify(seq2equation(max_indices, self.id2word))
                            # detok = de_tokenize(list(skeletons_block[step].cpu().numpy())[1:], self.id2word)
                            infixT = sympy.sympify(
                                seq2equation(list(skeletons_block[step].cpu().numpy())[1:], self.id2word))
                            print("Target: " + str(infixT) + " . Pred: " + str(infix))
                        except:
                            print()

                        # print("\tValidation " + str(iv), end='\r')
                cc += 1
                with open('src/EquationLearning/models/saved_models/validation_performance.txt', 'w') as file:
                    file.write(str(L1v / (5000 * cc)))

            # Aggregate loss terms in the batch
            loss = L1v / iv
            self.writer.add_scalar('validation loss', loss, global_batch_count)
            if loss < prev_loss:
                prev_loss = np.copy(loss)
                torch.save(self.model.state_dict(),
                           'src/EquationLearning/models/saved_models/Model-' + self.cfg.dataset)
                with open('src/EquationLearning/models/saved_models/validation_performance.txt', 'w') as file:
                    file.write(str(loss))
            print('[%d] validation loss: %.5f. Best validation loss: %.5f' % (epoch + 1, loss, prev_loss))


if __name__ == '__main__':
    trainer = TransformerTrainer()
    trainer.fit()
