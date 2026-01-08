import matplotlib.pyplot as plt
import numpy as np
import sympy as sp

class Visualizer():
    def __init__(self,*,linspace = True,N=200,style = None, colormap = "viridis"):
        """
        Docstring for __init__
        
        :param linspace: Control if data is evenly spaced or random
        :param N: Number of iterations
        :param style: Style
        :param colormap: Colormap
        """
        
        self.linspace = linspace
        self.N = N
        self.style = style
        self.colormap = colormap
    
    def visualize(self,fn,*,dim,linspace = True, show=False):
        """
        Docstring for visualize
        
        :param fn: function to be visualized, can be standard python syntax or sympy object
        :param dim: dimensionality of function
        :param linspace: evenly spaced data
        :param show: set method to automatically show plot

        return: None
        """

    def visualize_vf(self,fn,*,dim,linspace = True, show=False):
        """
        Docstring for visualize_vf
        
        :param fn: vectorfield function to be visualized
        :param dim: dimensionality of field
        :param linspace: evenly spaced data
        :param show: set method to automatically show plot

        """

    