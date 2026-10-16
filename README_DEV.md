# Nexus Operative Windows preview (v1.7.2)

This branch is a small, buildable **simulated PORTAL desktop preview**, imported directly through the connected GitHub tools. It **is not the full v1.7.1 research engine**, whose additional modules and tests are not imported yet.

## Windows CI

The GitHub Actions workflow is **manual only** to avoid repeated incomplete-build notifications. It installs dependencies, runs pytest, builds the PyInstaller EXE, verifies the HTTP API and PORTAL UI, then builds the optional Inno Setup installer. Artifacts are not signed; Windows SmartScreen may warn.

## Security boundaries

No Plaid Link, brokerage connection, autonomous remote worker, ACH deposit or live order route is implemented. Funding previews do not transfer money. No raw financial credentials are accepted. Do not store secrets in GitHub.

## Launch from Python

`python -m pip install -e ".[dev]"`
`python portable/desktop.py`

To run tests: `python -m pytest -q`

The source has not been tested as a Windows EXE yet.
