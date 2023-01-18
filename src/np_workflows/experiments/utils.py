from __future__ import annotations

import datetime
import pathlib
import typing
from typing import Any, Optional, Protocol, Union, Sequence, ClassVar, Type

import np_config
import np_session

from .baseclasses import Experiment
from ..services.protocols import Service

RESUME_ROOT = pathlib.Path(np_config.from_zk('/projects/np_workflows/defaults/configuration')['workflow_resume_root'])
RESUME_DATETIME_FMT = '%Y-%m-%d_%H%M%S'

def save(experiment: Experiment, suffix: Optional[str] = None):
    path = RESUME_ROOT / experiment.__class__.__name__ / experiment.session 
    filename = datetime.datetime.now().strftime(RESUME_DATETIME_FMT)
    if suffix:
        filename = f'{filename}_{suffix}'
    path.mkdir(exist_ok=True, parents=True)
    pickle.dump(experiment, path / f'{filename}.pkl')

def sorted_dirs(path: pathlib.Path) -> list[pathlib.Path]:
    return sorted([p for p in path.iterdir() if p.is_dir()], key=lambda _:_.stat().ctime, reverse=True)

def most_recent_session(experiment_type: Type[Experiment]) -> pathlib.Path | None:
    root = RESUME_ROOT / experiment_type.__name__
    if root.exists() and (dirs := sorted_dirs(root)):
        return dirs[0]
    return None 
        
def saves_from_most_recent_session(experiment_type: Type[Experiment]) -> list[pathlib.Path] | None:
    if session_dir := most_recent_session(experiment_type):
        return sorted_dirs(session_dir)
    return None 

def most_recent_save(experiment_type: Type[Experiment]) -> pathlib.Path | None:
    "Path to most recent save state"
    if previous := saves_from_most_recent_session(experiment_type):
        return sorted(previous.glob('*.pkl'), key=lambda _:_.name)
    return None

def load(path: pathlib.Path):
    return pickle.load(path)

        