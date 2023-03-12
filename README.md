# np_workflows

This package contains all the Python code required to run Mindscope Neuropixels experiments.

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

To develop this package `git clone` then `poetry install`
