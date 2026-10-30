from pathlib import Path
import re


ROOT=Path(__file__).resolve().parents[1]


def test_installer_smoke_bounds_every_external_process_and_records_phases():
    script=(ROOT/'tools/windows_installer_smoke.ps1').read_text(encoding='utf-8')
    assert 'function Invoke-BoundedProcess' in script
    assert 'installer-phases.jsonl' in script
    assert "if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 300)" in script
    assert '$Process.Kill($true)' in script
    assert "if (!$terminalRecorded) { Write-Phase $Label 'failed' $TimeoutSeconds }" in script
    assert script.index("Write-Phase $Label 'timed_out'") < script.index('Stop-BoundedProcess $process')
    assert 'Start-Process' not in script and ' -Wait' not in script
    assert not re.search(r'(?m)^python tools/',script)
    labels=re.findall(r"Invoke-BoundedProcess .*? '([^']+)' (\d+)$",script,re.MULTILINE)
    assert labels==[
        ('installed-paper-tools','300'),('first-installed-start','120'),
        ('reinstalled-start','120'),('packaged-recovery','150'),
        ('prior-version-recovery','150'),('default-data-first-start','120'),
        ('default-data-restart','120'),('uninstall','180')]
    assert "Invoke-BoundedProcess $Installer" in script
    assert "all_external_processes_bounded=$true" in script
    assert "phase_evidence_recorded=$true" in script
    assert "maximum_phase_timeout_seconds=300" in script


def test_windows_workflow_retains_outer_timeout_and_always_uploads_diagnostics():
    workflow=(ROOT/'.github/workflows/windows-release.yml').read_text(encoding='utf-8')
    assert 'timeout-minutes: 60' in workflow
    assert './tools/windows_installer_smoke.ps1' in workflow
    assert 'name: Validate installer phase evidence\n        timeout-minutes: 1' in workflow
    assert 'python tools/windows_installer_phase_review.py --input' in workflow
    assert 'installer-phase-review.json' in workflow
    assert 'name: Preserve installer acceptance evidence\n        if: always()' in workflow
    assert '${{ runner.temp }}/nexus-installer-evidence' in workflow
