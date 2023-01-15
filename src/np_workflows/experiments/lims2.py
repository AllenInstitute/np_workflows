"""
Critical components of an NP experiment workflow that come from .

- user/operator info
- mouse info 
    - isi experiment
    - project name

Combined, these are sufficient to create a new ecephys session entry in lims2.

Tools are also provided to construct commonly used abbreviations or folder names, and to
reverse these 'NP' formats to reconstruct the Info objects used during an
experiment workflow.

"""

import abc
import collections
import datetime
import functools
import json
import pathlib
import re
from typing import Callable, Dict, Tuple, Type, Union
import sys

sys.path.append("c:/progra~1/aibs_mpe/workflow_launcher")

import requests
from mpetk import limstk


# add ecephys query with the same format as other limstk functions
limstk.ecephys_sessions = functools.partial(
    limstk.lims_requests.request, "http://lims2/ecephys_sessions.json?id={}"
)


class LIMS2InfoBaseClass(collections.UserDict, abc.ABC):
    "Store  details for an object plus the commonly-used np format of its name, e.g. the labtracks mouse ID (366122)"

    _type: Type = NotImplemented
    _get_info: Callable = NotImplemented

    def __init__(self, np_id: Union[str, int]):
        np_id = self.__class__._type(np_id)
        info = self.info_from_lims(np_id)
        super().__init__(dict(np_id=np_id, **info))

    def __str__(self):
        return str(self.np_id)

    def __repr__(self):
        return f"{self.__class__.__name__}('{self.np_id}')"

    def info_from_lims(self, np_id) -> Dict:
        "Return the object's info from lims database or raise an error if not found."
        try:
            return self._get_info(str(np_id))[0]
        except IndexError:
            raise ValueError(
                f"Could not find {self.__class__.__name__} {np_id} in "
            ) from None

    @property
    def np_id(self):
        "Commonly-used format of the object's value in the neuropixels team e.g. for a mouse - the labtracks ID (366122)."
        return self["np_id"]

    @abc.abstractproperty
    def lims_id(self):
        "LIMS2 ID for the object, usually different to the np_id."
        return NotImplemented

    # end of baseclass properties & methods ------------------------------ #


class MouseInfo(LIMS2InfoBaseClass):
    "Store  details for a mouse."

    _type = int
    _get_info = limstk.donor_info

    @property
    def np_id(self) -> int:
        "Commonly-used np format: the labtracks mouse ID (e.g. 366122)."
        return self["np_id"]

    @property
    def lims_id(self) -> int:
        return self["specimens"][0]["id"]

    # end of baseclass properties & methods ------------------------------ #

    @property
    def isi_info(self) -> Union[Dict, None]:
        "Info from lims about the mouse's ISI experiments."
        if not hasattr(self, "_isi_info"):
            if response := limstk.isi_experiment_prod(str(self.np_id)):
                self._isi_info = response[0]
            else:
                self._isi_info = None
        return self._isi_info

    @property
    def isi_id(self) -> int:
        "ID of the mouse's most recent ISI experiment not marked `failed`."
        exps: list = self.isi_info["isi_experiments"]
        exps.sort(key=lambda x: x["id"], reverse=True)
        for exp in exps:
            if exp["workflow_state"] != "failed":
                return exp["id"]
        return None

    @property
    def project_id(self) -> int:
        " ID of the the project the mouse belongs to."
        return self["specimens"][0]["project_id"]

    @property
    def project_name(self) -> str:
        "PascalCase name of the project the mouse belongs to."
        return self["specimens"][0]["project"]["code"]

    @property
    def lims_dir(self) -> pathlib.Path:
        "Allen network dir where the mouse's data is stored."
        return self["specimens"][0]["storage_directory"]


class UserInfo(LIMS2InfoBaseClass):
    "Store  details for a user/operator."

    _type = str
    _get_info = limstk.user_details

    @property
    def np_id(self) -> str:
        "Commonly-used np format: log-in name."
        return self["np_id"]

    @property
    def lims_id(self) -> int:
        return self["id"]

    # end of baseclass properties & methods ------------------------------ #


class EcephysSessionInfo(LIMS2InfoBaseClass):
    "Store  details for an ecephys session."

    _type = int
    _get_info = limstk.ecephys_sessions

    def __str__(self):
        return f"{self.np_id}_{self['specimen']['external_specimen_name']}_{self['name'][:8]}"

    @property
    def np_id(self) -> int:
        "Commonly-used np format: lims session ID."
        return self["np_id"]

    @property
    def lims_id(self) -> int:
        return self.np_id

    # end of baseclass properties & methods ------------------------------ #


def generate_ecephys_session(
    mouse: Union[str, int, MouseInfo], user: Union[str, UserInfo],
) -> EcephysSessionInfo:
    "Create a new session and return an object instance with its info."

    if not isinstance(mouse, MouseInfo):
        mouse = MouseInfo(mouse)
    if not isinstance(user, UserInfo):
        user = UserInfo(user)

    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    request_json = {
        "specimen_id": mouse.lims_id,
        "project_id": mouse.project_id,
        "isi_experiment_id": mouse.isi_id,
        "name": f"{timestamp}_{user.lims_id}",
        "operator_id": user.lims_id,
    }
    url = "http:///observatory/ecephys_session/create"
    response = requests.post(url, json=request_json)
    decoded_dict = json.loads(response.content.decode("utf-8"))
    return EcephysSessionInfo(decoded_dict["id"])


def find_session_folder_string(path: Union[str, pathlib.Path]) -> Union[str, None]:
    """Extract [8+digit session ID]_[6-digit mouse ID]_[6-digit date
        str] from a file or folder path"""
    session_reg_exp = R"[0-9]{8,}_[0-9]{6}_[0-9]{8}"
    session_folders = re.findall(session_reg_exp, str(path))
    if session_folders:
        if not all(s == session_folders[0] for s in session_folders):
            raise ValueError(
                f"Mismatch between session folder strings - file may be in the wrong folder: {path}"
            )
        return session_folders[0]
    return None


def info_classes_from_session_folder(
    session_folder: str,
) -> Tuple[EcephysSessionInfo, MouseInfo, UserInfo]:
    "Reconstruct Info objects from a session folder string."
    if not (folder := find_session_folder_string(session_folder)):
        raise ValueError(f"{session_folder} is not a valid session folder")

    session = EcephysSessionInfo(folder.split("_")[0])
    mouse = MouseInfo(folder.split("_")[1])
    user = UserInfo(session["operator"]["login"])

    return (session, mouse, user)


if __name__ == "__main__":
    mouse = MouseInfo(366122)
    info_classes_from_session_folder("1190094328_611166_20220707")
