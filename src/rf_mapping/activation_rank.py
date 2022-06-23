"""
Code to find the image patches that drive the units the most/least. 

Tony Fu, Jun 22, 2022
"""
import os
import math
from heapq import heapify, heappush, heappushpop, nlargest       


import numpy as np
# from numpy import unravel_index
import torch
import torch.nn as nn
from torchvision import models


class LayerOutputInspector:
    """
    A class that "peeks" inside the outputs of all the layers with the
    specified layer type, one image at a time.
    """
    def __init__(self, model, layer_type=nn.Conv2d):
        self.model = model
        self.layer_type = layer_type
        self.layer_outputs = []
        self.register_forward_hook_to_layers(self.model)
        
    def hook_function(self, module, ten_in, ten_out):
        self.layer_outputs.append(ten_out.clone().detach().numpy())

    def register_forward_hook_to_layers(self, layer):      
        # If "model" is a leave node and matches the layer_type, register hook.
        if (len(list(layer.children())) == 0):
            if (isinstance(layer, self.layer_type)):
                layer.register_forward_hook(self.hook_function)

        # ...recurse otherwise.
        else:
            for i, sublayer in enumerate(layer.children()):
                self.register_forward_hook_to_layers(sublayer)
                
    def inspect(self, image):
        """
        Given an image, returns the output activation volumes of all the layers
        of the type <layer_type>.

        Parameters
        ----------
        image : numpy.array
            Input image, most likely with the dimension: [3, 2xx, 2xx].

        Returns
        -------
        layer_outputs : list of numpy.arrays
            Each item is an output activation volume of a target layer.
        """
        # Image preprocessing
        norm_image = image - image.min()
        norm_image = norm_image/norm_image.max()
        norm_image = np.expand_dims(norm_image, axis=0)
        image_tensor = torch.from_numpy(norm_image).type('torch.FloatTensor')
        
        # Forward pass
        _ = self.model(image_tensor)
        
        return self.layer_outputs


class MaxHeap():
    """
    A priority queue with fixed size.
    Credit: @CyanoKobalamyne on stackoverflow.
    """
    def __init__(self, N):
        self.h = []
        self.length = N
        heapify(self.h)
        
    def add(self, element):
        if len(self.h) < self.length:
            heappush(self.h, element)
        else:
            heappushpop(self.h, element)
            
    def getTop(self):
        return nlargest(self.length, self.h)


def top_bottom_N_image_patches(model, layer_type, image_dir, image_names):
    model.eval()
    inspector = LayerOutputInspector(model, layer_type)
    image = np.load(f"{image_dir}/{image_names[0]}")
    layer_outputs = inspector.inspect(image)
    for layer_output in layer_outputs: 
        print(layer_output.max())
    

if __name__ == '__main__':
    model = models.alexnet(pretrained=True)
    repo_dir = os.path.abspath(os.path.join(__file__, "../../.."))
    image_dir = f"{repo_dir}/data/imagenet"
    image_names = ["0.npy"]
    top_bottom_N_image_patches(model, nn.Conv2d, image_dir, image_names)


class SpatialIndexConversion:
    """
    A class containing the model- and image-shape-specific transformations
    of the spatial indicies across different layers. Useful for receptive
    field mapping and other tasks that involve the mappings of spatial
    locations onto an shallower or deeper layer.
    
    This class assumes the image size can only shrink in forward passes.
    Therefore, given a spatial location, the forward_projection method will
    return a point, whereas the backward_projection method will return a "box"
    in (vx_min, hx_min, vx_max, hx_max) format. Note that the returned point(s)
    here are cooridinates with respect to the destination layer.
    """
    def __init__(self, model, image_shape):
        """
        Constructs a SpatialIndexConversion object.

        Parameters
        ----------
        model : torchvision.models
            The neural network.
        image_shape : tuple of int
            (vertical_dimension, horizontal_dimension) in pixels.
        """
        self.model = model
        self.image_shape = image_shape
        self.layer_indicies = []
        self.output_shapes = []
        self.rf_sizes = []
        
    def _one_forward_projection(self, layer, vx, hx):
        non_spatial_transform_types = (nn.Sequential,
                                      nn.ModuleList,
                                      nn.Sigmoid,
                                      nn.ReLU,
                                      nn.Tanh,
                                      nn.Softmax2d,
                                      nn.BatchNorm2d,
                                      nn.Dropout2d,)

        if (isinstance(layer, non_spatial_transform_types)):
            return vx, hx
        
        if (isinstance(layer, nn.Conv2d)):
            # def transform(x, stride, kernel_size, padding):
            #     return (x*stride) + (kernel_size-1)/2 - padding
            
            vx = transform(vx, layer.stride[0], layer.kernel_size[0], layer.padding[0])
            hx = transform(hx, layer.stride[1], layer.kernel_size[1], layer.padding[1])
            return vx, hx
        
        if (isinstance(layer, (nn.MaxPool2d, nn.AvgPool2d))):
            # def transform(x, stride, kernel_size, padding):
            #     return (x*stride) + (kernel_size-1)/2 - padding
            
            vx = transform(vx, layer.stride[0], layer.kernel_size[0], layer.padding[0])
            hx = transform(hx, layer.stride[1], layer.kernel_size[1], layer.padding[1])
            return vx, hx
        
        # catch-all
        print(f"{type(layer)} is currently not supported.")
        raise ValueError
    
    def _one_backward_projection(self, layer, vx, hx):
        projection = None
        
        if (isinstance(layer, nn.Conv2d)):
            def transform(x, stride, kernel_size, padding):
                return (x*stride) + (kernel_size-1)/2 - padding
            
            vx = transform(vx, layer.stride[0], layer.kernel_size[0], layer.padding[0])
            hx = transform(hx, layer.stride[1], layer.kernel_size[1], layer.padding[1])
            return vx, hx
        
        if (isinstance(layer, nn.MaxPool2d)):
            def transform(x, stride, kernel_size, padding):
                return (x*stride) + (kernel_size-1)/2 - padding
            
            vx = transform(vx, layer.stride[0], layer.kernel_size[0], layer.padding[0])
            hx = transform(hx, layer.stride[1], layer.kernel_size[1], layer.padding[1])
            return vx, hx
        
        # catch-all
        return vx, hx
    
    def _process_index(self, index):
        """
        Make sure that the index is a tuple of two indicies. Unravel from 1D
        to 2D indexing if necessary.
        """
        if index.isnumeric():
            return np.unravel_index(index, self.image_shape)
        if (len(index)==2):
            return index
    
    def forward_projection(self, index, start_layer, end_layer):
        index = self._process_index(index)
        
        
    def backward_projection(self, index, start_layer, end_layer=0):
        index = self._process_index(index)