from typing import Any, Dict, Optional, Tuple, List, Union

import csv
import glob
import os
from PIL import Image, ImageOps
import tqdm
import gc
import random
import torch
from pytorch_lightning import LightningDataModule
from torch.utils.data import ConcatDataset, DataLoader, Dataset, random_split
from torchvision.datasets import MNIST
from torchvision.transforms import transforms


class BabyDataModule(LightningDataModule):
    """LightningDataModule for Baby dataset, with point detection.

    A DataModule implements 5 key methods:

        def prepare_data(self):
            # things to do on 1 GPU/TPU (not on every GPU/TPU in DDP)
            # download data, pre-process, split, save to disk, etc...
        def setup(self, stage):
            # things to do on every process in DDP
            # load data, set variables, etc...
        def train_dataloader(self):
            # return train dataloader
        def val_dataloader(self):
            # return validation dataloader
        def test_dataloader(self):
            # return test dataloader
        def teardown(self):
            # called on every process in DDP
            # clean up after fit or test

    This allows you to share a full dataset without explaining how to download,
    split, transform and process the data.

    Read the docs:
        https://pytorch-lightning.readthedocs.io/en/latest/extensions/datamodules.html
    """

    def __init__(
        self,
        data_dir: str = "data/",
        image_preprocessor: transforms.Compose=transforms.Compose([]),
        label_preprocessor: transforms.Compose=transforms.Compose([]),
        augmentations: Tuple[transforms.Compose, ...]=(transforms.Compose([]),),
        batch_size: int = 64,
        num_workers: int = 0,
        pin_memory: bool = False,
        image_max_size: Tuple[int, int] = (960, 1728),
        resize_input: Union[Tuple[int, int], None] = None,
        white_pixel: Tuple[int, int, int, int] = (253, 231, 36, 255),
        lazy_load: bool = False,
    ):
        super().__init__()

        # this line allows to access init params wit[transforms.ToTensor(), transforms.Normalize((0.1307,), (0.3081,))]h 'self.hpimage_max_siarams' attribute
        # also ensures init params will be stored in ckpt
        self.save_hyperparameters(logger=False)

        # data transformations
        self.image_preprocessor = image_preprocessor
        self.label_preprocessor = label_preprocessor
        self.augmentations = augmentations
        self.lazy_load = lazy_load
        self.resize_input = resize_input

        self.data_train: Optional[Dataset] = None
        self.data_val: Optional[Dataset] = None
        self.data_test: Optional[Dataset] = None


    def prepare_data(self):
        """Download data if needed.

        Do not use it to assign state (self.x = y).
        """
        pass


    def get_pad_sequence(self, img_width, img_height, pad_width, pad_height):
        """
        Return the pad sequence needed to pad from (img_width, img_height) -> (pad_width, pad_height)
        """
        horizontal, vertical = pad_width-img_width, pad_height-img_height
        
        l = horizontal // 2
        r = l + horizontal % 2
        
        u = vertical // 2
        d = u + vertical % 2
        
        pad_sequence = [l, u, r, d]
        return pad_sequence

    def read_image(self, img_path, greyscale=False, scale_factor=256):
        """
        Read image from path as rgb
        Return tensor(1 x w x h): RGB image tensor
        """
        image = Image.open(img_path)
        
        if greyscale:
            image = ImageOps.grayscale(image)

        transformations = [
            transforms.PILToTensor(),
            transforms.Pad(
                self.get_pad_sequence(
                    image.width, 
                    image.height, 
                    self.hparams.image_max_size[1], 
                    self.hparams.image_max_size[0]
                )
            ),
        ]
        if self.hparams.resize_input:
            transformations.append(
                transforms.Resize([320, 544])
                # transforms.Resize([self.hparams.resize_input[0], self.hparams.resize_input[1]])
            )

        transform = transforms.Compose(transformations)

        img_tensor = transform(image)

        return img_tensor/scale_factor


    def read_label(self, img_path):
        """ Read label image and convert to greyscale
        
        Return tensor(1 x w x h): Greyscale image tensor
        """
        img_tensor = self.read_image(img_path, scale_factor=1.)
        
        label_tensor = torch.all(img_tensor.permute(1,2,0) == torch.tensor(self.hparams.white_pixel), dim=-1).unsqueeze(0)

        return label_tensor


    def augment_tensors(self, tensors: torch.Tensor):
        """
        tensors: Tuple of tensors shaped (channel x h x w)
        Return list of tensors augmented from tensors
        """
        if len(self.augmentations) == 0:
            return [tensors]

        augmented_tensors = [
            transform(tensors)
            for transform in self.augmentations
        ]

        return augmented_tensors


    def setup(self, stage: Optional[str] = None):
        """Load data. Set variables: `self.data_train`, `self.data_val`, `self.data_test`.

        This method is called by lightning with both `trainer.fit()` and `trainer.test()`, so be
        careful not to execute things like random split twice!
        """
        print("Setup baby", self.hparams.data_dir)

        self.data_train = self.load_data_from_dir(
            os.path.join(self.hparams.data_dir, "train"), 
            augment = True, 
            lazy_load=self.lazy_load
        )
        self.data_val = self.load_data_from_dir(
            os.path.join(self.hparams.data_dir, "val"), 
            lazy_load=self.lazy_load
        )
        self.data_test = self.load_data_from_dir(
            os.path.join(self.hparams.data_dir, "test"), 
            greyscale=True, 
            lazy_load=self.lazy_load
        )
        
        self.loader_train = DataLoader(
            dataset=self.data_train,
            batch_size=self.hparams.batch_size,
            num_workers=self.hparams.num_workers,
            pin_memory=self.hparams.pin_memory,
            shuffle=True,
        )
        self.loader_val = DataLoader(
            dataset=self.data_val,
            batch_size=self.hparams.batch_size,
            num_workers=self.hparams.num_workers,
            pin_memory=self.hparams.pin_memory,
            shuffle=False,
        )
        self.loader_test = DataLoader(
            dataset=self.data_test,
            batch_size=self.hparams.batch_size,
            num_workers=self.hparams.num_workers,
            pin_memory=self.hparams.pin_memory,
            shuffle=False,
        )
        

    def train_dataloader(self):
        return self.loader_train

    def val_dataloader(self):
        return self.loader_val

    def test_dataloader(self):
        return self.loader_test

    def teardown(self, stage: Optional[str] = None):
        """Clean up after fit or test."""
        pass

    def state_dict(self):
        """Extra things to save to checkpoint."""
        return {}

    def load_state_dict(self, state_dict: Dict[str, Any]):
        """Things to do when loading checkpoint."""
        pass



class BabyTupleDataset(torch.utils.data.Dataset):
    def __init__(self, *tuples: Tuple[torch.Tensor]):
        """
        tuples: tuple of tensors (channel x h x w)
        """
        assert all(len(tuples[0]) == len(t) for t in tuples), "Size mismatch between tensors"
        self.tuples = tuples

    def __getitem__(self, idx):
        return tuple(t[idx] for t in self.tuples)

    def __len__(self):
        return len(self.tuples[0])


class BabyLazyLoadDataset(torch.utils.data.Dataset):
    def __init__(self, 
        img_paths: List[str], 
        label_paths: List[str], 
        augment: bool = False, 
        data_module_obj: BabyDataModule = None, 
        greyscale: bool = False
    ):
        self.img_paths = img_paths
        self.label_paths = label_paths
        self.augment = augment
        self.data_module_obj = data_module_obj
        self.greyscale = greyscale
    
    def __getitem__(self, idx):
        img = self.data_module_obj.read_image(self.img_paths[idx], greyscale=self.greyscale)
        label = self.data_module_obj.read_label(self.label_paths[idx])

        img = self.data_module_obj.image_preprocessor(img)
        label = self.data_module_obj.label_preprocessor(label)

        if not self.augment:
            return (img, label)
        
        augmented_tensors = self.data_module_obj.augment_tensors(torch.stack([img, label]))

        augmented_tensor = random.choice(augmented_tensors)

        augmented_img = augmented_tensor[0]
        augmented_label = augmented_tensor[1]

        return augmented_img, augmented_label


    def __len__(self):
        return len(self.img_paths)
