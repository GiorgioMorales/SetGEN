# MSSP: Multi-Set Symbolic Skeleton Prediction for Symbolic Regression

## Description

We present a method that, given a multivariate regression problem, generates univariate symbolic skeletons that aim to describe 
the functional relation between each input variable and the system's response.
To do this, we introduce a new SR problem called Multi-Set symbolic skeleton prediction (MSSP). It receives multiple 
sets of input--response pairs, where all sets correspond to the same functional form but use different equation constants, 
and outputs a common skeleton expression, as follows:

<p align="center">
  <img src="figs/MSSP_definition.jpg" alt="alt text" width="400">
</p>

We present a novel transformer model called "Multi-Set Transformer" to solve the MSSP problem. The model is pre-trained 
on a large dataset of synthetic symbolic expressions using an entropy-based loss function. The 
identification process of the functional form between each variable and the system's response is viewed as a sequence 
of MSSP problems:

<p align="center">
  <img src="figs/Skeleton.png" alt="alt text" width="500">
</p>

Our method generates univariate skeletons that are more similar to those corresponding to the underlying equations in comparison to other SR methods.
From an interpretability standpoint, producing more faithful univariate skeletons means that we are able to provide better explanations of how each variable is related to the system's response.
In addition, the generated skeletons may be used as building blocks that could be used to estimate the overall function of the system (future work). 

## Usage

This repository contains the following main scripts:

* `Main.py`: Generates multiple symbolic skeletons that explain the functional form between each variable of the system and the system's response.        
* `Comparison.py`: Compares the symbolic skeletons generated by our Multi-Set Transformer and other methods (Use `pip install pymoo==0.6.0` to avoid errors with the PyMOO library).
* `DemoMSSP.ipynb`: Jupyter notebook demo that demonstrates the symbolic skeleton generation for each system's variable.

Other important scripts:

* `/src/Trainer/TrainMultiSetTRansformer`: Trains the Multi-Set Transformer to solve the MSSP based on a large dataset of pre-generated mathematical expressions.
* `/src/Trainer/TrainNNmodel`: Trains the NN model $\hat{f}$ that acts as a black-box approximation of the system's underlying function $f$ and that is used to generate the artificial multiple sets used for MSSP.

The datasets are available online at: [https://huggingface.co/datasets/AnonymousGM/MultiSetTransformerData](https://huggingface.co/datasets/AnonymousGM/MultiSetTransformerData).
To replicate the training process, download the datasets and paste them in the `/src/data/sampled_data` folder
