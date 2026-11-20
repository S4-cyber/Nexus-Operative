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

    assert "timeout-minutes: 120" in regression
    assert "name: offline-regression-${{ matrix.shard }}-of-2" in regression
    assert "fail-fast: false" in regression
    assert "shard: [0, 1]" in regression
    assert "python -m pytest -q -p ops.pytest_shard" in regression
    assert "--nexus-shard-index ${{ matrix.shard }}" in regression
    assert "--nexus-shard-count 2" in regression
    assert "nexus.release_trials" not in regression
    assert "portal_browser_smoke.py" not in regression

    assert "timeout-minutes: 45" in evidence
    assert "python -m nexus.release_trials" in evidence
    assert "python -m nexus.release_evidence" in evidence
    assert "if: always()" in evidence
    assert "python -m pytest -q" not in evidence

    assert "timeout-minutes: 15" in browser
    assert "python -m playwright install chromium --only-shell" in browser
    assert "python tools/portal_browser_smoke.py" in browser
    assert '--source-commit "${{ github.event.pull_request.head.sha || github.sha }}"' in browser
    assert "if: always()" in browser
    assert "python -m pytest -q" not in browser


def test_each_safety_job_uses_pinned_actions_and_nonpersistent_checkout_auth():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5") == 3
    assert text.count("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065") == 3
    assert text.count("persist-credentials: false") == 3
    assert text.count("cache-dependency-path: pyproject.toml") == 3
    assert text.count("ref: ${{ github.event.pull_request.head.sha || github.sha }}") == 3
    assert text.count("name: Bind job to exact source commit") == 3
    assert text.count("EXPECTED_SOURCE_COMMIT: ${{ github.event.pull_request.head.sha || github.sha }}") == 3
    assert text.count("$actual = (git rev-parse HEAD).Trim()") == 3
    assert text.count("$actual -cne $env:EXPECTED_SOURCE_COMMIT") == 3
    assert "python -m compileall -q nexus tests ops" in text


def test_browser_source_commit_validation_is_exact_and_fail_closed():
    from tools.portal_browser_smoke import require_source_commit

    source = "a" * 40
    assert require_source_commit(source) == source
    for invalid in (None, "", "A" * 40, "a" * 39, "a" * 41, "g" * 40):
        try:
            require_source_commit(invalid)
        except ValueError as error:
            assert str(error) == "BROWSER_SOURCE_COMMIT_INVALID"
        else:
            raise AssertionError("invalid browser evidence source was accepted")
