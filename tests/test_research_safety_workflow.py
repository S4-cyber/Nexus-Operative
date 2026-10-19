"""Release-gate topology for the Windows safety workflow."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "research-safety-windows.yml"


def _job_block(text: str, name: str, following: str | None) -> str:
    start = text.index(f"  {name}:\n")
    end = text.index(f"  {following}:\n", start) if following else len(text)
    return text[start:end]


def test_long_running_safety_gates_have_independent_windows_job_budgets():
    text = WORKFLOW.read_text(encoding="utf-8")
    regression = _job_block(text, "offline-regression", "offline-release-evidence")
    evidence = _job_block(text, "offline-release-evidence", "portal-browser-help")
    browser = _job_block(text, "portal-browser-help", None)

    assert "timeout-minutes: 25" in regression
    assert "python -m pytest -q" in regression
    assert "nexus.release_trials" not in regression
    assert "portal_browser_smoke.py" not in regression

    assert "timeout-minutes: 15" in evidence
    assert "python -m nexus.release_trials" in evidence
    assert "python -m nexus.release_evidence" in evidence
    assert "if: always()" in evidence
    assert "python -m pytest -q" not in evidence

    assert "timeout-minutes: 15" in browser
    assert "python -m playwright install chromium --only-shell" in browser
    assert "python tools/portal_browser_smoke.py" in browser
    assert "if: always()" in browser
    assert "python -m pytest -q" not in browser


def test_each_safety_job_uses_pinned_actions_and_nonpersistent_checkout_auth():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5") == 3
    assert text.count("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065") == 3
    assert text.count("persist-credentials: false") == 3
    assert text.count("cache-dependency-path: pyproject.toml") == 3
