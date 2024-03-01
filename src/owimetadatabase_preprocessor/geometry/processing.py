"Module containing the processing functions for the geometry data."

from copy import deepcopy
from typing import List, Union

import numpy as np
import pandas as pd

import warnings

from owimetadatabase_preprocessor.geometry.structures import SubAssembly
from owimetadatabase_preprocessor.utils import deepcompare, custom_formatwarning


warnings.simplefilter('always')
warnings.formatwarning = custom_formatwarning


ATTR_PROC = [
    "pile_toe", "rna", "tower", "transition_piece", "monopile", "tw_lumped_mass",
    "tp_lumped_mass", "mp_lumped_mass", "tp_distributed_mass", "mp_distributed_mass", "grout"
]
ATTR_SPEC = ["full_structure", "tp_skirt", "substructure"]
ATTR_FULL = ["all_tubular_structures", "all_distributed_mass", "all_lumped_mass", "all_turbines"]


class OWT(object):
    """Class to process the geometry data of a single OWT."""

    def __init__(
        self,
        api,
        materials: pd.DataFrame,
        subassemblies: pd.DataFrame,
        location: pd.DataFrame,
        tower_base: Union[float, None] = None,
        pile_head: Union[float, None] = None,
    ) -> None:
        """Get all subassemblies for a given Turbine.

        :param subassemblies: Pandas dataframe with the subassemblies data for a given turbine.
        :param tower_base: Elevation of the OWT tower base in mLAT.
        :param pile_head: Elevation of the pile head in mLAT.

        :return:
        """
        self._init_proc = False
        self._init_spec_part = False
        self._init_spec_full = False
        self.api = api
        self.materials = materials
        self._set_subassemblies(subassemblies)
        self.tower_sub_assemblies = None
        self.tp_sub_assemblies = None
        self.mp_sub_assemblies = None
        self._set_members()
        for attr in ATTR_PROC:
            setattr(self, attr, None)
        for attr in ATTR_SPEC:
            setattr(self, attr, None)
        self.water_depth = location["elevation"].values[0]
        if not tower_base or not pile_head:
            self.tower_base = self.sub_assemblies["TW"].absolute_bottom
            self.pile_head = self.sub_assemblies["MP"].absolute_top
        else:
            self.tower_base = tower_base
            self.pile_head = pile_head

    def _set_subassemblies(self, subassemblies: pd.DataFrame) -> None:
        """Create a dictionary containing the subassemblies of the OWT."""
        subassemblies_types = [
            sa["subassembly_type"] for _, sa in subassemblies.iterrows()
        ]
        subassemblies_list = [
            SubAssembly(self.materials, sa.to_dict(), api_object=self.api)
            for _, sa in subassemblies.iterrows()
        ]
        self.sub_assemblies = {
            k: v for (k, v) in zip(subassemblies_types, subassemblies_list)
        }

    def _set_members(self) -> None:
        """Identify and stores in separate data frames each part of the support structure (tower=TW, transition piece=TP,
        monopile=MP).
        """
        for k, v in self.sub_assemblies.items():
            if k == "TW":
                self.tower_sub_assemblies = v.as_df()
            if k == "TP":
                self.tp_sub_assemblies = v.as_df()
            if k == "MP":
                self.mp_sub_assemblies = v.as_df()

    def set_df_structure(self, idx: str) -> pd.DataFrame:
        """Calculate and/or converts geometrical data of subassemblies from the database.

        :param idx: Possible index to identify corresponding subassembly.
        :return: Data frame containing geometry data from database wth z in mLAT system.
        """
        cols = ["OD", "height", "mass", "volume", "wall_thickness", "x", "y", "z"]
        if idx == "tw":
            df_index = self.tower_sub_assemblies.index.str.contains(idx)
            df = deepcopy(self.tower_sub_assemblies.loc[df_index, cols])
            depth_to = self.tower_base + df.z * 1e-3
            depth_from = depth_to + df.height * 1e-3
        elif idx == "tp":
            # We don't take into account the grout, this element will be modelled as a distributed lumped mass.
            df_index = (self.tp_sub_assemblies.index.str.contains(idx)) & (
                ~self.tp_sub_assemblies.index.str.contains("grout")
            )
            df = deepcopy(self.tp_sub_assemblies.loc[df_index, cols])
            bottom_tp = self.tower_base - df["height"].sum() * 1e-3
            depth_to = bottom_tp + df.z * 1e-3
            depth_from = depth_to + df.height * 1e-3
        elif idx == "mp":
            df_index = self.mp_sub_assemblies.index.str.contains(idx)
            df = deepcopy(self.mp_sub_assemblies.loc[df_index, cols])
            toe = self.pile_head - df["height"].sum() * 1e-3
            self.pile_toe = round(toe, 3)
            depth_to = toe + df.z * 1e-3
            depth_from = depth_to + df.height * 1e-3
        else:
            raise ValueError("Unknown index.")
        df["Depth from [mLAT]"] = depth_from
        df["Depth to [mLAT]"] = depth_to
        # Round elevations to mm to avoid numerical inconsistencies later when setting altitude values to apply loads.
        df = df.round({"Depth from [mLAT]": 3, "Depth to [mLAT]": 3})
        return df

    def process_structure_geometry(self, idx: str) -> pd.DataFrame:
        """Calculate and/or converts geometrical data of subassemblies from the database to use as input for FE models.

        :param idx: Possible index to identify corresponding subassembly.
        :return: Dataframe consisting of the required data to build FE models.
        """
        df = self.set_df_structure(idx)
        df["height"] = pd.to_numeric(df["height"])
        df["wall_thickness"] = pd.to_numeric(df["wall_thickness"])
        df.rename(columns={"wall_thickness": "Wall thickness [mm]"}, inplace=True)
        df.rename(columns={"volume": "Volume [m3]"}, inplace=True)
        d_to = [d.split("/", 1)[0] for d in df["OD"].values]
        d_from = [
            d.split("/", 1)[1] if len(d.split("/", 1)) > 1 else d.split("/", 1)[0]
            for d in df["OD"].values
        ]
        df["Diameter from [m]"] = np.array(d_from, dtype=float) * 1e-3
        df["Diameter to [m]"] = np.array(d_to, dtype=float) * 1e-3
        df["rho [t/m]"] = df["mass"] / df["height"]
        df["Mass [t]"] = df["mass"] * 1e-3
        df["Height [m]"] = df["height"] * 1e-3
        df["Youngs modulus [GPa]"] = 210
        df["Poissons ratio [-]"] = 0.3
        cols = [
            "Depth from [mLAT]",
            "Depth to [mLAT]",
            "Height [m]",
            "Diameter from [m]",
            "Diameter to [m]",
            "Volume [m3]",
            "Wall thickness [mm]",
            "Youngs modulus [GPa]",
            "Poissons ratio [-]",
            "Mass [t]",
            "rho [t/m]",
        ]
        return df[cols]

    def process_rna(self) -> None:
        """Set dataframe containing the required properties to model the RNA system.

        :return:
        """
        rna_index = self.tower_sub_assemblies.index.str.contains("RNA")
        rna = deepcopy(
            self.tower_sub_assemblies.loc[
                rna_index, ["mass", "moment_of_inertia", "x", "y", "z"]
            ]
        )
        mi = rna["moment_of_inertia"].values
        i_xx, i_yy, i_zz = [], [], []
        for m in mi:
            i_xx.append(m["x"] * 1e-3)
            i_yy.append(m["y"] * 1e-3)
            i_zz.append(m["z"] * 1e-3)
        rna["Ixx [tm2]"] = i_xx
        rna["Iyy [tm2]"] = i_yy
        rna["Izz [tm2]"] = i_zz
        rna["Mass [t]"] = rna["mass"] * 1e-3
        rna["X [m]"] = rna["x"] * 1e-3
        rna["Y [m]"] = rna["y"] * 1e-3
        rna["Z [mLAT]"] = self.tower_base + rna["z"] * 1e-3
        cols = [
            "X [m]",
            "Y [m]",
            "Z [mLAT]",
            "Mass [t]",
            "Ixx [tm2]",
            "Iyy [tm2]",
            "Izz [tm2]",
        ]
        self.rna = rna[cols]

    def set_df_appurtenances(self, idx: str, add_descr: bool = False) -> pd.DataFrame:
        """Set dataframe containing the required properties to model concentrated masses from database subassemblies.

        :param idx: Index to identify corresponding subassembly with possible values: 'TW', 'TP', 'MP'.
        :return: Data frame containing lumped masses data from database with Z coordinates in mLAT system.
        """
        cols = ["mass", "x", "y", "z"]
        if add_descr:
            cols += ["description"]
        if idx == "TW":
            df_index = self.tower_sub_assemblies.index.str.contains(idx)
            df = deepcopy(self.tower_sub_assemblies.loc[df_index, cols])
            df["Z [mLAT]"] = self.tower_base + df["z"] * 1e-3
        elif idx == "TP":
            df_index = self.tp_sub_assemblies.index.str.contains(idx)
            df = deepcopy(self.tp_sub_assemblies.loc[df_index, cols + ["height"]])
            # Lumped masses have 'None' height whereas distributed masses present not 'None' values
            df["height"] = pd.to_numeric(df["height"])
            df = df[df["height"].isnull()]
            bottom = self.tower_base - self.tp_sub_assemblies.iloc[0]["z"] * 1e-3
            df["Z [mLAT]"] = bottom + df["z"] * 1e-3
        elif idx == "MP":
            df_index = self.mp_sub_assemblies.index.str.contains(idx)
            df = deepcopy(self.mp_sub_assemblies.loc[df_index, cols + ["height"]])
            # Lumped masses have 'None' height whereas distributed masses present not 'None' values
            df["height"] = pd.to_numeric(df["height"])
            df = df[df["height"].isnull()]
            bottom = self.pile_toe
            df["Z [mLAT]"] = bottom + df["z"] * 1e-3
        else:
            raise ValueError("Unknown index.")
        return df

    def process_lumped_masses(self, idx: str, add_descr: bool = False) -> pd.DataFrame:
        """Create dataframe containing the required properties to model lumped mass appurtenances. Note that
        if the preprocessor package does not find any appurtenances it'll return an empty dataframe.

        :param idx:  Index to identify corresponding subassembly with possible values: 'TW', 'TP', 'MP'.
        :return: Dataframe.
        """
        df = self.set_df_appurtenances(idx, add_descr=add_descr)
        df["Mass [t]"] = df.mass * 1e-3
        df["X [m]"] = df.x * 1e-3
        df["Y [m]"] = df.y * 1e-3
        cols = ["X [m]", "Y [m]", "Z [mLAT]", "Mass [t]"]
        if add_descr:
            cols.append("Description")
        return df[cols]

    def set_df_distributed_appurtenances(self, idx: str) -> pd.DataFrame:
        """Set dataframe containing the required properties to model distributed lumped masses from database.

        :param idx: Index to identify corresponding subassembly with possible values: 'TW', 'TP', 'MP'.
        :return: Dataframe containing distributed lumped masses data from database. Z coordinates in mLAT system.
        """
        cols = ["mass", "x", "y", "z", "height", "volume", "description"]
        if idx == "TP":
            df_index = self.tp_sub_assemblies.index.str.contains(idx)
            df = deepcopy(self.tp_sub_assemblies.loc[df_index, cols])
            # Lumped masses have 'None' height whereas distributed masses present not 'None' values
            df["height"] = pd.to_numeric(df["height"])
            df = df[df["height"].notnull()]
            bottom_tp = self.tower_base - self.tp_sub_assemblies.iloc[0]["z"] * 1e-3
            df["Z [mLAT]"] = bottom_tp + df["z"] * 1e-3
        elif idx == "MP":
            df_index = self.mp_sub_assemblies.index.str.contains(idx)
            df = deepcopy(self.mp_sub_assemblies.loc[df_index, cols])
            # Lumped masses have 'None' height whereas distributed masses present not 'None' values
            df["height"] = pd.to_numeric(df["height"])
            df = df[df["height"].notnull()]
            bottom = self.pile_toe
            df["Z [mLAT]"] = bottom + df["z"] * 1e-3
        elif idx == "grout":
            df_index = self.tp_sub_assemblies.index.str.contains(idx)
            df = deepcopy(self.tp_sub_assemblies.loc[df_index, cols])
            # Lumped masses have 'None' height whereas distributed masses present not 'None' values
            df["height"] = pd.to_numeric(df["height"])
            df = df[df["height"].notnull()]
            bottom_tp = self.tower_base - self.tp_sub_assemblies.iloc[0]["z"] * 1e-3
            df["Z [mLAT]"] = bottom_tp + df["z"] * 1e-3
        else:
            raise ValueError(
                "Unknown index or non distributed lumped masses located outside the transition piece."
            )
        return df

    def process_distributed_lumped_masses(self, idx: str) -> pd.DataFrame:
        """Create dataframe containing the required properties to model uniformly distributed appurtenances. Note that
        if the preprocessor package does not find any appurtenances it'll return an empty dataframe.

        :param idx: Index to identify corresponding subassembly with possible values: 'TP', 'MP'.
        :return: Dataframe.
        """
        df = self.set_df_distributed_appurtenances(idx)
        df["Mass [t]"] = df["mass"] * 1e-3
        df["X [m]"] = df["x"] * 1e-3
        df["Y [m]"] = df["y"] * 1e-3
        df["Height [m]"] = df["height"] * 1e-3
        df.rename(columns={"volume": "Volume [m3]"}, inplace=True)
        cols = [
            "X [m]",
            "Y [m]",
            "Z [mLAT]",
            "Height [m]",
            "Mass [t]",
            "Volume [m3]",
            "description",
        ]
        return df[cols]

    def process_structure(self, option="full") -> None:
        """Set dataframe containing the required properties to model the tower geometry, including the RNA system.

        :param option: Option to process the data for a specific subassembly. Possible values:

            - "full": To process all the data for all subassemblies.
            - "tower": To process only the data for the tower subassembly.
            - "TP": To process only the data for the transition piece subassembly.
            - "monopile": To process only the data for the monopile foundation subassembly.
        :return:
        """
        self._init_proc = True
        if option == "full":
            self.process_rna()
            self.tower = self.process_structure_geometry("tw")
            self.transition_piece = self.process_structure_geometry("tp")
            self.monopile = self.process_structure_geometry("mp")
            self.tw_lumped_mass = self.process_lumped_masses("TW")
            self.tp_lumped_mass = self.process_lumped_masses("TP")
            self.mp_lumped_mass = self.process_lumped_masses("MP")
            self.tp_distributed_mass = self.process_distributed_lumped_masses("TP")
            self.mp_distributed_mass = self.process_distributed_lumped_masses("MP")
            self.grout = self.process_distributed_lumped_masses("grout")
        elif option == "tower":
            self.process_rna()
            self.tower = self.process_structure_geometry("tw")
            self.tw_lumped_mass = self.process_lumped_masses("TW")
        elif option == "TP":
            self.transition_piece = self.process_structure_geometry("tp")
            self.tp_lumped_mass = self.process_lumped_masses("TP")
            self.tp_distributed_mass = self.process_distributed_lumped_masses("TP")
            self.grout = self.process_distributed_lumped_masses("grout")
        elif option == "monopile":
            self.monopile = self.process_structure_geometry("mp")
            self.mp_lumped_mass = self.process_lumped_masses("MP")
            self.mp_distributed_mass = self.process_distributed_lumped_masses("MP")

    @staticmethod
    def can_adjust_properties(row: pd.Series) -> pd.Series:
        """Recalculation of can properties based on section properties and can elevations: height [m],
        volume [m3], mass [t], rho [t/m].

        :param row: Original can properties.
        :return: Recalculated can properties.
        """
        density = row["Mass [t]"] / row["Volume [m3]"]
        height = row["Depth from [mLAT]"] - row["Depth to [mLAT]"]
        r1 = row["Diameter from [m]"] / 2
        r2 = row["Diameter to [m]"] / 2
        volume_out = 1 / 3 * np.pi * (r1**2 + r1 * r2 + r2**2) * height
        wall_thickness = row["Wall thickness [mm]"] * 1e-3
        r1 = r1 - wall_thickness
        r2 = r2 - wall_thickness
        volume_in = 1 / 3 * np.pi * (r1**2 + r1 * r2 + r2**2) * height
        volume = volume_out - volume_in
        mass = volume * density
        rho_m = mass / height
        can_properties = pd.Series(
            data=[height, volume, mass, rho_m],
            index=["Height [m]", "Volume [m3]", "Mass [t]", "rho [t/m]"],
        )
        return can_properties

    def can_modification(
        self, df: pd.DataFrame, altitude: np.float64, position: str = "bottom"
    ) -> pd.DataFrame:
        """Change can properties based on the altitude.

        :param df: Dataframe containing the can properties.
        :param altitude: Altitude in mLAT.
        :param position: Position of the can with respect to the altitude with possible values: "bottom" or "top".
        :return: Dataframe with the modified can properties.
        """
        if position == "bottom":
            ind = -1
            _col = " to "
        else:
            ind = 0
            _col = " from "
        df.loc[df.index[ind], "Depth" + _col + "[mLAT]"] = altitude
        elevation = [df.iloc[ind]["Depth from [mLAT]"], df.iloc[ind]["Depth to [mLAT]"]]
        diameters = [df.iloc[ind]["Diameter from [m]"], df.iloc[ind]["Diameter to [m]"]]
        df.loc[df.index[ind], "Diameter" + _col + "[m]"] = np.interp(
            [altitude], elevation, diameters
        )[0]
        cols = ["Height [m]", "Volume [m3]", "Mass [t]", "rho [t/m]"]
        df.loc[df.index[ind], cols] = self.can_adjust_properties(df.iloc[ind])
        return df

    def assembly_tp_mp(self) -> None:
        """Process TP structural item to assembly with MP foundation ensuring continuity. TP skirt is processed
        as well.

        :return:
        """
        self._init_spec_part = True
        if (self.transition_piece is not None) and (self.monopile is not None):
            mp_head = self.pile_head
            tp = self.transition_piece
            df = deepcopy(tp[tp["Depth from [mLAT]"] > mp_head])
            if df.loc[df.index[0], "Depth to [mLAT]"] != mp_head:
                # Not bolted connection (i.e. Rentel) preprocessing needed
                tp1 = self.can_modification(df, mp_head, position="bottom")
                self.substructure = pd.concat([tp1, deepcopy(self.monopile)])
            else:
                # Bolted connection, nothing to do
                self.substructure = pd.concat([df, deepcopy(self.monopile)])
            df = deepcopy(tp[tp["Depth to [mLAT]"] < mp_head])
            self.tp_skirt = self.can_modification(df, mp_head, position="top")
        else:
            raise TypeError("TP or MP items need to be processed before!")

    def assembly_full_structure(self) -> None:
        """Process the full structure of the OWT: tower + tp combiantion with monopile.

        :return:
        """
        self._init_spec_full = True
        if self.substructure is not None:
            if self.tower is not None:
                self.full_structure = pd.concat(
                    [self.tower, self.substructure]
                )
            else:
                raise TypeError("Tower needs to be processed before!")
        else:
            raise TypeError("Substructure needs to be processed before!")
    
    def extend_dfs(self):
        for sa in ["TW", "TP", "MP"]:
            self.process_lumped_masses(sa, add_descr=True)
        self.tower["Subassembly"] = "TW"
        self.transition_piece["Subassembly"] = "TP"
        self.monopile["Subassembly"] = "MP"
        self.tw_lumped_mass["Subassembly"] = "TW"
        self.tp_lumped_mass["Subassembly"] = "TP"
        self.mp_lumped_mass["Subassembly"] = "MP"
        self.tp_distributed_mass["Subassembly"] = "TP"
        self.mp_distributed_mass["Subassembly"] = "MP"
        self.grout["Subassembly"] = "TP"
        self.rna["Subassembly"] = "TW"
        self.assembly_tp_mp()
        self.assembly_full_structure()

    def transform_monopile_geometry(
        self,
        cutoff_point: np.float64 = np.nan,
    ) -> pd.DataFrame:
        """Returns a dataframe with the monopile geometry with the mudline as reference

        :param projectsite: Title of the projectsite.
        :param assetlocation: Title of the turbine.
        :return: Dataframe with the monopile geometry.
        """
        toe_depth_lat = self.sub_assemblies["MP"].position.z
        penetration = -((1e-3 * toe_depth_lat) - self.water_depth)
        pile = pd.DataFrame()
        df = self.mp_sub_assemblies.copy()
        df.reset_index(inplace=True)
        for i, row in df.iterrows():
            if i != 0:
                pile.loc[i, "Depth from [m]"] = penetration - 1e-3 * df["z"].iloc[i - 1]
                pile.loc[i, "Depth to [m]"] = penetration - 1e-3 * row["z"]
                pile.loc[i, "Pile material"] = (
                    self.sub_assemblies["MP"].bb[0].material.title
                )
                pile.loc[i, "Pile material submerged unit weight [kN/m3]"] = (
                    1e-2 * self.sub_assemblies["MP"].bb[0].material.density - 10
                )
                pile.loc[i, "Wall thickness [mm]"] = row["wall_thickness"]
                bot_od = row["OD"].split("/")[0] if "/" in row["OD"] else row["OD"]
                top_od = row["OD"].split("/")[1] if "/" in row["OD"] else row["OD"]
                pile.loc[i, "Diameter [m]"] = (
                    1e-3 * 0.5 * (float(bot_od) + float(top_od))
                )
                pile.loc[i, "Youngs modulus [GPa]"] = (
                    self.sub_assemblies["MP"].bb[0].material.young_modulus
                )
                pile.loc[i, "Poissons ratio [-]"] = (
                    self.sub_assemblies["MP"].bb[0].material.poisson_ratio
                )
        if not np.math.isnan(cutoff_point):
            pile = pile.loc[pile["Depth to [m]"] > cutoff_point].reset_index(drop=True)
            pile.loc[0, "Depth from [m]"] = cutoff_point
        return pile

    def __eq__(self, other) -> bool:
        if isinstance(other, type(self)):
            return deepcompare(self, other)
        elif isinstance(other, dict):
            return deepcompare(self.__dict__, other)
        else:
            return False
    
    def __getattribute__(self, name):
        if name in ATTR_PROC and not self._init_proc:
            warnings.warn(f"Attribute '{name}' accessed before processing. \
                    Run process_structure() first if you want to process values.")
        elif name in ATTR_SPEC and not self._init_spec_part:
            warnings.warn(f"Attribute '{name}' accessed before processing. \
                    Run assembly_tp_mp() first if you want to process values.")
        elif name in ATTR_SPEC and not self._init_spec_full:
            warnings.warn(f"Attribute '{name}' accessed before processing. \
                    Run assembly_full_structure() first if you want to process values.")          
        return object.__getattribute__(self, name)


class OWTs(object):
    """Class to process the geometry data of multiple OWTs."""

    def __init__(
        self,
        turbines: List[str],
        owts: List[OWT],
    ) -> None:
        self.owts = {k: v for k, v in zip(turbines, owts)}
        self.api = self.owts[turbines[0]].api
        self.materials = self.owts[turbines[0]].materials
        for attr in ["sub_assemblies", "tower_base", "pile_head", "water_depth"]:
            dict_ = {
                k: getattr(owt, attr) for k, owt in zip(turbines, self.owts.values())
            }
            setattr(self, attr, dict_)
        for attr in ["tower_sub_assemblies", "tp_sub_assemblies", "mp_sub_assemblies"]:
            df = pd.concat(
                [getattr(owt, attr) for owt in self.owts.values()]
            )
            setattr(self, attr, df)
        for attr in ATTR_PROC:
            setattr(self, attr, [])
        for attr in ATTR_SPEC:
            setattr(self, attr, [])
        for attr in ATTR_FULL:
            setattr(self, attr, [])
        self._init = False

    def _concat_list(self, attr_list) -> None:
        """Internal method to concatenate lists of dataframes for attributes.

        :param attr_list: List of attributes to concatenate.
        """
        for attr in attr_list:
            setattr(self, attr, pd.concat(getattr(self, attr)))

    def assembly_turbine(self) -> None:
        """Method to assemble general geometry data of all specified turbines."""
        cols = [
            "Turbine name",
            "Water depth [m]",
            "Monopile toe [m]",
            "Monopile head [m]",
            "Tower base [m]",
            "Monopile height [m]",
            "Monopile mass [t]",
            "Transition piece height [m]",
            "Transition piece mass [t]",
            "Tower height [m]",
            "Tower mass [t]",
        ]
        df_list = []
        for turb in self.owts.keys():
            df_list.append(
                [
                    turb,
                    self.water_depth[turb],
                    self.pile_toe[turb],
                    self.pile_head[turb],
                    self.tower_base[turb],
                    self.owts[turb].monopile["Height [m]"].sum(),
                    (
                        self.owts[turb].monopile["Mass [t]"].sum()
                        + self.owts[turb].mp_distributed_mass["Mass [t]"].sum()
                        + self.owts[turb].mp_lumped_mass["Mass [t]"].sum()
                    ),
                    self.owts[turb].transition_piece["Height [m]"].sum(),
                    (
                        self.owts[turb].transition_piece["Mass [t]"].sum()
                        + self.owts[turb].tp_distributed_mass["Mass [t]"].sum()
                        + self.owts[turb].tp_lumped_mass["Mass [t]"].sum()
                        + self.owts[turb].grout["Mass [t]"].sum()
                    ),
                    self.owts[turb].tower["Height [m]"].sum(),
                    (
                        self.owts[turb].tower["Mass [t]"].sum()
                        + self.owts[turb].tw_lumped_mass["Mass [t]"].sum()
                        + self.owts[turb].rna["Mass [t]"].sum()
                    ),
                ]
            )
        df = pd.DataFrame(df_list, columns=cols)
        self.all_turbines = df.round(2)

    def process_structures(self) -> None:
        """Set dataframes containing the required properties to model the tower geometry, including the RNA system."""
        attr_list = ATTR_PROC + ATTR_SPEC + ATTR_FULL
        attr_list.remove("all_turbines")
        if self._init:
            return
        self._init = True
        for owt in self.owts.values():
            owt.process_structure()
            owt.extend_dfs()
            for attr in attr_list:
                if attr == "pile_toe":
                    self.pile_toe.append(getattr(owt, attr))
                elif attr == "all_tubular_structures":
                    self.all_tubular_structures.extend(
                        [owt.tower, owt.transition_piece, owt.monopile]
                    )
                elif attr == "all_distributed_mass":
                    self.all_distributed_mass.extend(
                        [
                            owt.tp_distributed_mass,
                            owt.grout,
                            owt.mp_distributed_mass,
                        ]
                    )
                elif attr == "all_lumped_mass":
                    cols = ["X [m]", "Y [m]", "Z [mLAT]", "Mass [t]"]
                    self.all_lumped_mass.extend(
                        [
                            owt.rna[cols],
                            owt.tw_lumped_mass,
                            owt.tp_lumped_mass,
                            owt.mp_lumped_mass,
                        ]
                    )
                else:
                    attr_val = getattr(self, attr)
                    owt_attr_val = getattr(owt, attr)
                    attr_val.append(owt_attr_val)
        attr_list.remove("pile_toe")
        self.pile_toe = {k: v for k, v in zip(self.owts.keys(), self.pile_toe)}
        self._concat_list(attr_list)
        self.assembly_turbine()

    def select_owt(self, turbine: Union[str, int]) -> OWT:
        """Select OWT object from the OWTs object.

        :param turbine: Title of the turbine or itss index in the original list of turbine titles (from get method).
        :return: OWT object.
        """
        if isinstance(turbine, int):
            return self.owts[list(self.owts.keys())[turbine]]
        elif isinstance(turbine, str):
            return self.owts[turbine]
        else:
            raise ValueError(
                "You must specify a single turbine title or \
                its index from the the get method input turbine list."
            )

    def __eq__(self, other) -> bool:
        if isinstance(other, type(self)):
            return deepcompare(self, other)
        elif isinstance(other, dict):
            return deepcompare(self.__dict__, other)
        else:
            return False
    
    def __getattribute__(self, name):
        if name in ATTR_PROC + ATTR_SPEC + ATTR_FULL and not self._init:
            warnings.warn(f"Attribute '{name}' accessed before processing. \
                    Run process_structures() first if you want to process values.")       
        return object.__getattribute__(self, name)
