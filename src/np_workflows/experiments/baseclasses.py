from __future__ import annotations
import functools

from typing import Any, Optional, Protocol, Union, Sequence, ClassVar, Type

import np_config
import np_session

from ..services.protocols import Service

class Experiment(Protocol):
    session: str
    "Unique id for the session, e.g. lims ecephys session id, datetime"
    services: Sequence[Service]
    "Devices, databases etc."
    config: dict[str, Any]
    "For rig, session, experiment, etc."

class WithLims(Experiment):
    "Provides lims info properties"
    
    def __init__(self,
        labtracks_mouse_id: int | str,
        lims_user_id: str,
        lims_session_id: Optional[int] = None,
    ):
        self.mouse = labtracks_mouse_id
        self.operator = lims_user_id
        if lims_session_id is None:
            lims_session_id = self.generate_ecephys_session(self.mouse, self.operator)
        self.session = lims_session_id

    @staticmethod
    def generate_ecephys_session(
        labtracks_mouse_id: int | str,
        lims_user_id: str,
    ) -> np_session.lims.SessionInfo:
        return np_session.lims.generate_ecephys_session(mouse=labtracks_mouse_id, user=lims_user_id)

    @property
    def session(self) -> np_session.lims.SessionInfo:
        if not hasattr(self, '_session'):
            self.generate_ecephys_session(str(self.mouse), str(self.operator))
        return self._session

    @session.setter
    def session(self, value: str | np_session.lims.SessionInfo):
        if not isinstance(value, np_session.lims.SessionInfo):
            value = np_session.lims.SessionInfo(value)
        self._session = value

    @property
    def mouse(self) -> np_session.lims.MouseInfo:
        return self._mouse

    @mouse.setter
    def mouse(self, value: str | int):
        self._mouse = np_session.lims.MouseInfo(value)

    @property
    def operator(self) -> np_session.lims.UserInfo:
        return self._operator

    @operator.setter
    def operator(self, value: str):
        self._operator = np_session.lims.UserInfo(value)

    @property
    def project(self) -> str:
        "Primarily used for file manifest"
        if not hasattr(self, '_project'):
            self._project = self.mouse.project
        return self._project

    @project.setter
    def project(self, value: str):
        self._project = value

    @functools.cached_property
    def folder(self) -> str:
        return np_session.folder_from_lims_id(str(self.session))

    @functools.cached_property
    def files(self) -> dict[str, dict[str, str]]:
        "Expected manifest of files from experiment"
        return np_session.files_manifest(str(self.project), self.folder, 'D1')
