# np_workflows

This package contains all the Python code required to run Mindscope Neuropixels
experiments.

[![PyPI](https://img.shields.io/pypi/v/np-workflows.svg?label=PyPI&color=blue)](https://pypi.org/project/np-workflows/)
[![Python version](https://img.shields.io/pypi/pyversions/np-workflows)](https://pypi.org/project/np-workflows/)

<!-- [![Coverage](https://img.shields.io/codecov/c/github/alleninstitute/np_workflows?logo=codecov)](https://app.codecov.io/github/AllenInstitute/np_workflows) -->
[![CI/CD](https://img.shields.io/github/actions/workflow/status/alleninstitute/np_workflows/publish.yml?label=CI/CD&logo=github)](https://github.com/alleninstitute/np_workflows/actions/workflows/publish.yml)
[![GitHub issues](https://img.shields.io/github/issues/alleninstitute/np_workflows?logo=github)](https://github.com/alleninstitute/np_workflows/issues)

Experiment workflows and related tasks are coordinated by Jupyter notebooks maintained here:
https://github.com/AllenInstitute/np_notebooks

Running the notebooks requires a Python environment with:
- Python >= 3.11
- np_workflows
- Jupyter / JupyterLab

```
git clone https://github.com/AllenInstitute/np_notebooks
conda create -n workflows python=3.11
pip install np_workflows
pip install jupyterlab
```

Keep the `np_workflows` package up-to-date by running `pip install
np_workflows -U` before each use

To develop this package `git clone` then `pdm install`

## NP. rig setup
.venv is currently used, with PDM to update packages before each experiment:
1. install JupyterLab desktop (Win 64-bit)
2. clone `np_notebooks` (not `np_workflows`) `cd c:\Users\svc_neuropix\Documents\github & git clone https://github.com/AllenInstitute/np_notebooks`
5. find the path to python.exe 3.11.*, or install it manually
6. create a new venv in the `np_notebooks` dir: `cd np_notebooks & path-to-python.exe -m venv .venv`
7. activate venv: `.venv\scripts\activate` (terminal should now report `(np_notebooks-3.11)`)
8. install PDM in venv: `path-to-python.exe -m pip install pdm`
9. install `np_notebooks` requirements: `pdm install`
10. in JupyterLab, set the default to Python environment to use `c:\Users\svc_neuropix\Documents\github\np_notebooks\.venv\scripts\python.exe`
11. open up any workflow in the `np_notebooks` dir and check that the initial cell with imports works
