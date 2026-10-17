# Nexus Operative — pre-release development v0.57.0

This branch provides a **simulation-only** Windows PORTAL research application. The development snapshot previously labeled v3.8.0 was renumbered **v0.38.0** so v1.0.0 can be reserved for the first completed, reviewed launch. See [VERSIONING.md](VERSIONING.md) for historical labels, future prerelease numbering, and required release gates.

## Windows CI
Every push to `development/windows-portal-foundation` automatically runs Python tests, validates PORTAL JavaScript, packages the Windows standalone EXE, verifies startup, and produces the Inno Setup installer if all steps pass. Artifacts are unsigned and temporary; check the relevant GitHub Actions run for results.

## Safety
Public-data ingestion and strategy evaluations are research-only. No authorized live brokerage orders, transfers, bank linkage or autonomous live trading are present. Successful simulation or CI tests do **not** constitute production approval.

## Run locally
```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python portable/desktop.py
```

