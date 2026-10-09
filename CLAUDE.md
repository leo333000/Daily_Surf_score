# Project rules

## Python environment
This project uses a virtual environment in `.venv/` (Python 3.13). Always use it; never install packages into the system Python.
- Run Python with `.venv/bin/python`
- Install packages with `.venv/bin/pip install <package>`, then add them to `requirements.txt`
- Run tests with `.venv/bin/python -m pytest`
