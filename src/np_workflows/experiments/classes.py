from __future__ import annotations

import contextlib
import datetime
import pickle
import functools
import pathlib
import typing
from typing import Any, Optional, Protocol, Union, Sequence, ClassVar, Type

import np_config
import np_session

from ..services.protocols import Service
from ..services.proxies import ImageMVR, VideoMVR, Sync, Camstim, JsonRecorder, NewScaleCoordinateRecorder
from ..services import open_ephys as OpenEphys
from .baseclasses import WithLims


class PretestNP2(WithLims):
    _project = 'Pretest_NP2'
    services: tuple[Service, ...] = (
        OpenEphys, ImageMVR, VideoMVR, Sync, Camstim, JsonRecorder, NewScaleCoordinateRecorder,
    )
    config: ClassVar[dict[str, Any]]  = (
        np_config.from_zk('/projects/np_workflows/defaults/configuration') 
        | np_config.from_zk('/rigs/NP.2')
    )
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_services()

    def configue_services(self):
        OpenEphys.folder = self.folder