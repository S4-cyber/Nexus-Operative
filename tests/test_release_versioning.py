"""Pre-1.0 development release consistency and safety contract."""
import re
import tomllib
from pathlib import Path
from nexus.app import app, status

ROOT=Path(__file__).resolve().parents[1]

def test_release_versions_match_and_remain_prerelease():
    package=tomllib.loads((ROOT/"pyproject.toml").read_text(encoding="utf-8"))
    version=package["project"]["version"]
    installer=(ROOT/"installer"/"NexusOperative.iss").read_text(encoding="utf-8")
    assert re.fullmatch(r"0\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",version), "Production 1.x requires explicit launch approval"
    assert f'#define AppVersion "{version}"' in installer
    assert f'OutputBaseFilename=NexusOperative-Setup-{version}' in installer
    assert app.version==version
    assert status()["version"]==version

def test_development_release_never_claims_live_authority():
    s=status()
    assert s["live_orders_enabled"] is False
    assert s["money_movement_enabled"] is False
    assert s["broker_connected"] is False
