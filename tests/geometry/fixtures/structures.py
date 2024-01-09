import json
from copy import deepcopy
from pathlib import Path

from typing import  Any, Callable, Dict, List, Tuple, Union
from unittest import mock

import numpy as np
import pandas as pd
import pytest
import requests

from owimetadatabase_preprocessor.geometry.io import GeometryAPI
from owimetadatabase_preprocessor.geometry.processing import OWT
from owimetadatabase_preprocessor.geometry.structures import Material, Position, BuildingBlock, SubAssembly

from owimetadatabase_preprocessor.utils import dict_generator


@pytest.fixture(scope="module")
def data():
    file_dir = Path(__file__).parent.parent
    data_path = file_dir / "data"
    data_type = {
        "mat": "materials",
        "sa": "subassemblies",
        "bb": "building_blocks",
        "bb_prop": "properties_bb",
        "sa_prop": "properties_sa"
    }
    data = {}
    for d in data_type.keys():
        with open(data_path / (data_type[d] + ".json")) as f:
            data[d] = json.load(f)
    return data

@pytest.fixture(scope="function")
def material_main(data):
    return dict_generator(data["mat"][0], keys_=["slug"], method_="exclude")

@pytest.fixture(scope="function")
def material_main_dict(data):
    return dict_generator(data["mat"][0], keys_=["id", "density", "slug"], method_="exclude")

@pytest.fixture(scope="function")
def position(data):
    data_ = dict_generator(
        data["bb"][0],
        keys_=["alpha", "beta", "gamma", "x_position", "y_position", "z_position", "vertical_position_reference_system"],
        method_="include"
    )
    return {
        "x": data_["x_position"],
        "y": data_["y_position"],
        "z": data_["z_position"],
        "alpha": data_["alpha"],
        "beta": data_["beta"],
        "gamma": data_["gamma"],
        "reference_system": data_["vertical_position_reference_system"]
    }
