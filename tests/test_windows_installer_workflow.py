import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import pytest


ROOT=Path(__file__).resolve().parents[1]
PRIOR_RECOVERY_FIXTURE_COMMIT='5660ec19d66af6401d3bd4e6c271b6e8dc2bf461'


def test_installer_smoke_bounds_root_wait_and_output_drains_and_records_phases():
    script=(ROOT/'tools/windows_installer_smoke.ps1').read_text(encoding='utf-8')
    process_script=(ROOT/'tools/windows_bounded_process.ps1').read_text(encoding='utf-8')
    assert 'function Invoke-BoundedProcess' in process_script
    assert 'installer-phases.jsonl' in script
    assert "if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 300)" in process_script
    assert '$Process.Kill($true)' in process_script
    assert '. "$PSScriptRoot/windows_bounded_process.ps1"' in script
    assert '$ProcessOutputDrainTimeoutMilliseconds = 10000' in process_script
    assert '[System.Threading.Tasks.Task]::WhenAll' in process_script
    assert '.Wait($ProcessOutputDrainTimeoutMilliseconds)' in process_script
    assert '$process.WaitForExit()' not in process_script
    assert '.GetAwaiter().GetResult()' not in process_script
    assert "Installer smoke phase output streams did not close within the fixed bound" in process_script
    cleanup = process_script.index('finally {', process_script.index('function Invoke-BoundedProcess'))
    assert process_script.index('try { Stop-BoundedProcess $process }', cleanup) < process_script.index('finally {', cleanup + 1)
    assert process_script.index('$process.StandardOutput.Close()', cleanup) < process_script.index('$process.Dispose()', cleanup)
    assert "if (!$terminalRecorded) { Write-Phase $Label 'failed' $TimeoutSeconds }" in process_script
    assert process_script.index("Write-Phase $Label 'timed_out'") < process_script.index('Stop-BoundedProcess $process')
    assert 'Start-Process' not in process_script and ' -Wait' not in process_script
    assert not re.search(r'(?m)^python tools/',script)
    labels=re.findall(r"Invoke-BoundedProcess .*? '([^']+)' (\d+)$",script,re.MULTILINE)
    assert labels==[
        ('installed-paper-tools','300'),('first-installed-start','120'),
        ('reinstalled-start','120'),('packaged-recovery','150'),
        ('prior-version-recovery','150'),('default-data-first-start','120'),
        ('default-data-restart','120'),('uninstall','180')]
    assert "Invoke-BoundedProcess $Installer" in script
    assert "external_process_output_drains_bounded = $true" in script
    assert "process_tree_containment_verified = $false" in script
    assert "all_external_processes_bounded" not in script
    assert "phase_evidence_recorded = $true" in script
    assert "maximum_root_process_timeout_seconds = 300" in script
    assert "output_drain_timeout_seconds = 10" in script
    assert "output_drain_timeout_seconds = 10" in script[script.index("function Write-Phase"):script.index('. "$PSScriptRoot/windows_bounded_process.ps1"')]
    assert "maximum_phase_timeout_seconds" not in script
    assert "if (Test-Path $phaseEvidence) { throw 'Installer phase evidence already exists' }" in script
    assert "if (Test-Path $resultEvidence) { throw 'Installer result evidence already exists' }" in script
    assert "[Guid]::NewGuid().ToString('D')" in script
    assert 'source_commit = $SourceCommit' in script
    assert 'version = $Version' in script
    assert 'installer_sha256 = $installerSha256' in script
    assert "mode = 'INSTALLER_SMOKE_RESULT'" in script
    assert 'session_id = $phaseSessionId' in script
    assert 'native_windows_acceptance = $false' in script
    assert 'actual_broker_acceptance = $false' in script
    assert 'live_orders_enabled = $false' in script
    assert 'money_movement_enabled = $false' in script
    assert 'release_approved = $false' in script
    assert '[IO.File]::Move($temporaryResult, $resultEvidence)' in script
    assert 'Set-Content "$EvidenceDirectory/installer-result.json"' not in script


@pytest.mark.skipif(sys.platform != 'win32', reason='PowerShell process-handle behavior requires Windows')
@pytest.mark.parametrize(('mode','expected_status','expected_exit','result_exists'), (
    ('normal','completed',0,True),
    ('inherited-handle','failed',7,False),
))
def test_bounded_process_executes_normal_and_inherited_handle_cases(
        tmp_path, mode, expected_status, expected_exit, result_exists):
    harness=tmp_path/'bounded-process-harness.ps1'
    evidence=tmp_path/f'{mode}.jsonl'
    result=tmp_path/f'{mode}.result'
    error_result=tmp_path/f'{mode}.error'
    child_pid=tmp_path/f'{mode}.pid'
    child_release=tmp_path/f'{mode}.release'
    child_exited=tmp_path/f'{mode}.exited'
    parent_script=tmp_path/'spawn_inherited_child.py'
    child_script=tmp_path/'hold_inherited_handles.py'
    parent_script.write_text(
        "import subprocess,sys\nsubprocess.Popen([sys.executable,*sys.argv[1:]],"
        "stdout=sys.stdout,stderr=sys.stderr)\n",encoding='utf-8')
    child_script.write_text(
        "import os,sys,time\n"
        "open(sys.argv[1],'w',encoding='utf-8').write(str(os.getpid()))\n"
        "deadline=time.monotonic()+20\n"
        "while not os.path.exists(sys.argv[2]) and time.monotonic() < deadline:\n"
        "    time.sleep(0.05)\n"
        "if os.path.exists(sys.argv[2]):\n"
        "    temporary=sys.argv[3]+'.tmp'\n"
        "    with open(temporary,'w',encoding='utf-8') as marker:\n"
        "        marker.write('released')\n"
        "    os.replace(temporary,sys.argv[3])\n",encoding='utf-8')
    harness.write_text(r'''param(
    [Parameter(Mandatory=$true)][string]$Module,
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Evidence,
    [Parameter(Mandatory=$true)][string]$Result,
    [Parameter(Mandatory=$true)][string]$ErrorResult,
    [Parameter(Mandatory=$true)][string]$ChildPid,
    [Parameter(Mandatory=$true)][string]$ChildRelease,
    [Parameter(Mandatory=$true)][string]$ChildExited,
    [Parameter(Mandatory=$true)][string]$ParentScript,
    [Parameter(Mandatory=$true)][string]$ChildScript,
    [Parameter(Mandatory=$true)][string]$Mode
)
$ErrorActionPreference = 'Stop'
$phaseEvidence = $Evidence
$phaseSessionId = [Guid]::NewGuid().ToString('D')
$SourceCommit = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
$Version = '0.340.404'
$installerSha256 = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
function Write-Phase([string]$Label, [string]$Status, [int]$TimeoutSeconds) {
    $record = [ordered]@{ label=$Label; status=$Status; timeout_seconds=$TimeoutSeconds }
    Add-Content -LiteralPath $phaseEvidence -Value ($record | ConvertTo-Json -Compress) -Encoding UTF8
}
. $Module
try {
    if ($Mode -eq 'inherited-handle') {
        Invoke-BoundedProcess $Python @($ParentScript,$ChildScript,$ChildPid,$ChildRelease,$ChildExited) 'fixture' 2
    } else {
        Invoke-BoundedProcess $Python @('-c',"print('ok')") 'fixture' 2
    }
    [IO.File]::WriteAllText($Result, 'completed')
    exit 0
} catch {
    [IO.File]::WriteAllText($ErrorResult, $_.Exception.Message)
    exit 7
}
''',encoding='utf-8')
    completed=subprocess.run([
        'pwsh','-NoProfile','-File',os.fspath(harness),
        '-Module',os.fspath(ROOT/'tools/windows_bounded_process.ps1'),
        '-Python',sys.executable,'-Evidence',os.fspath(evidence),
        '-Result',os.fspath(result),'-ErrorResult',os.fspath(error_result),
        '-ChildPid',os.fspath(child_pid),'-ChildRelease',os.fspath(child_release),
        '-ChildExited',os.fspath(child_exited),
        '-ParentScript',os.fspath(parent_script),'-ChildScript',os.fspath(child_script),
        '-Mode',mode,
    ],timeout=18,check=False,capture_output=True,text=True)
    assert completed.returncode == expected_exit, (completed.stdout,completed.stderr)
    records=[json.loads(line) for line in evidence.read_text(encoding='utf-8-sig').splitlines()]
    assert [record['status'] for record in records] == ['started',expected_status]
    assert result.exists() is result_exists
    if mode == 'inherited-handle':
        assert error_result.read_text(encoding='utf-8') == (
            'Installer smoke phase output streams did not close within the fixed bound: fixture')
        assert int(child_pid.read_text(encoding='utf-8')) > 0
        assert not child_exited.exists()
        child_release.write_text('release',encoding='utf-8')
        deadline=time.monotonic()+5
        while time.monotonic() < deadline and not child_exited.exists():
            time.sleep(0.05)
        assert child_exited.read_text(encoding='utf-8') == 'released'
    else:
        assert not error_result.exists()


def test_windows_workflow_retains_bounded_release_tail_and_always_uploads_diagnostics():
    workflow=(ROOT/'.github/workflows/windows-release.yml').read_text(encoding='utf-8')
    assert 'timeout-minutes: 150' in workflow
    assert 'name: Create distribution checksum manifest\n        timeout-minutes: 20' in workflow
    assert 'python -m nexus.release_trials --expected-version $version --output release/release-trial-evidence.json' in workflow
    assert 'python -m nexus.release_evidence release/release-trial-evidence.json --expected-version $version' in workflow
    assert 'old 120m deadline cancelled' in workflow
    assert "source_commit:\n        description: 'Optional exact 40-character commit descended from the current development head'" in workflow
    assert "required: false\n        default: ''" in workflow
    assert 'fetch-depth: 0' in workflow
    assert "REQUESTED_SOURCE_COMMIT: ${{ inputs.source_commit }}" in workflow
    assert "if ($requested -cnotmatch '^[0-9a-f]{40}$')" in workflow
    assert 'git fetch --no-tags origin development/windows-portal-foundation:refs/remotes/origin/development/windows-portal-foundation' in workflow
    assert 'git merge-base --is-ancestor $development $actual' in workflow
    assert 'Requested source commit is not descended from the current development head' in workflow
    assert 'Mutable development dispatch did not resolve to its refreshed head' in workflow
    assert workflow.index('git merge-base --is-ancestor $development $actual') < workflow.index('NEXUS_SOURCE_COMMIT=$actual')
    assert './tools/windows_installer_smoke.ps1' in workflow
    assert 'name: Validate installer phase evidence\n        timeout-minutes: 1' in workflow
    assert 'python tools/windows_installer_phase_review.py --input' in workflow
    assert 'NEXUS_SOURCE_COMMIT=$actual' in workflow
    assert '-SourceCommit $env:NEXUS_SOURCE_COMMIT' in workflow
    assert '--installer $installer --expected-version $version' in workflow
    assert '--expected-source-commit $env:NEXUS_SOURCE_COMMIT' in workflow
    assert 'installer-phase-review.json' in workflow
    assert 'python tools/windows_installer_result_review.py --input' in workflow
    assert '--phase-review "$env:RUNNER_TEMP/nexus-installer-evidence/installer-phase-review.json"' in workflow
    assert 'installer-result-review.json' in workflow
    assert 'python tools/windows_distribution_binding_review.py --phase-review' in workflow
    assert '--installer-result-review release/installer-result-review.json' in workflow
    assert '--artifact-manifest release/artifact-manifest.json' in workflow
    assert '--expected-source-commit $env:NEXUS_SOURCE_COMMIT' in workflow
    assert 'distribution-binding-review.json' in workflow
    assert 'Copy-Item tools/windows_owner_profile_probe.ps1 release/windows-owner-profile-probe.ps1' in workflow
    assert 'Copy-Item tools/windows_owner_profile_review.py release/windows-owner-profile-review.py' in workflow
    assert 'release/windows-owner-profile-probe.ps1 release/windows-owner-profile-review.py' in workflow
    assert 'release/release-trial-evidence.json release/installer-result-review.json release/windows-owner-profile-probe.ps1' in workflow
    assert 'Copy-Item "$env:RUNNER_TEMP/nexus-installer-evidence/installer-result-review.json" release/installer-result-review.json' in workflow
    assert 'Copy-Item "$env:RUNNER_TEMP/nexus-installer-evidence/distribution-binding-review.json" release/distribution-binding-review.json' in workflow
    envelope_create='python -m nexus.artifact_manifest create --version $version --commit $sourceCommit --output release/distribution-envelope.json release/artifact-manifest.json release/distribution-binding-review.json'
    envelope_verify='python -m nexus.artifact_manifest verify release/distribution-envelope.json release'
    assert envelope_create in workflow
    assert envelope_verify in workflow
    assert 'release/windows-owner-profile-probe.ps1' in workflow
    assert 'release/windows-owner-profile-review.py' in workflow
    assert workflow.index('python -m nexus.artifact_manifest verify') < workflow.index('python tools/windows_distribution_binding_review.py')
    assert workflow.index('python tools/windows_installer_phase_review.py') < workflow.index('python tools/windows_installer_result_review.py')
    assert workflow.index('python tools/windows_installer_result_review.py') < workflow.index('Copy-Item "$env:RUNNER_TEMP/nexus-installer-evidence/installer-result-review.json" release/installer-result-review.json')
    assert workflow.index('Copy-Item "$env:RUNNER_TEMP/nexus-installer-evidence/installer-result-review.json" release/installer-result-review.json') < workflow.index('python -m nexus.artifact_manifest create')
    assert workflow.index('python tools/windows_distribution_binding_review.py') < workflow.index('Copy-Item "$env:RUNNER_TEMP/nexus-installer-evidence/distribution-binding-review.json" release/distribution-binding-review.json')
    assert workflow.index('Copy-Item "$env:RUNNER_TEMP/nexus-installer-evidence/distribution-binding-review.json" release/distribution-binding-review.json') < workflow.index(envelope_create)
    assert workflow.index(envelope_create) < workflow.index(envelope_verify)
    exact_set='name: Validate exact installer upload set'
    assert exact_set in workflow
    assert "Get-ChildItem release -File -Filter 'NexusOperative-Setup-*.exe'" in workflow
    assert '$installers.Count -ne 1 -or $installers[0].FullName -cne $expected' in workflow
    assert workflow.index(envelope_verify) < workflow.index(exact_set)
    upload='name: Upload distribution with source and checksum manifest'
    exact_index=workflow.index(exact_set)
    upload_index=workflow.index(upload)
    final_inner=workflow.index('python -m nexus.artifact_manifest verify release/artifact-manifest.json release',exact_index)
    final_envelope=workflow.index('python -m nexus.artifact_manifest verify release/distribution-envelope.json release',exact_index)
    assert exact_index < final_inner < final_envelope < upload_index
    assert 'NexusOperative-Standalone-Windows' not in workflow
    assert 'name: Upload standalone EXE' not in workflow
    assert 'NexusOperative-Windows-Installer' not in workflow
    upload_blocks=re.findall(
        r'(?ms)^      - (?:name: [^\n]+\n        )?uses: actions/upload-artifact@[^\n]+\n.*?(?=^      - |\Z)',
        workflow,
    )
    executable_uploads=[
        block for block in upload_blocks
        if 'release/NexusOperative.exe' in block
        or 'release/NexusOperative-Setup-*.exe' in block
    ]
    assert len(executable_uploads)==1
    distribution_upload=executable_uploads[0]
    assert 'release/NexusOperative.exe' in distribution_upload
    assert 'release/NexusOperative-Setup-*.exe' in distribution_upload
    assert 'release/artifact-manifest.json' in distribution_upload
    assert 'release/distribution-binding-review.json' in distribution_upload
    assert 'release/distribution-envelope.json' in distribution_upload
    assert 'release/installer-result-review.json' in distribution_upload
    assert 'name: Preserve installer acceptance evidence\n        if: always()' in workflow
    assert '${{ runner.temp }}/nexus-installer-evidence' in workflow
    assert workflow.count(PRIOR_RECOVERY_FIXTURE_COMMIT)==2
    assert '09bb7280cd2e654018d491db52e19660b2b8e180' not in workflow
    assert 'trusted terminal-clock' in workflow


def test_owner_instructions_require_the_manifest_bound_installer_result_review():
    windows=(ROOT/'WINDOWS_ACCEPTANCE.md').read_text(encoding='utf-8')
    owner=(ROOT/'OWNER_PAPER_ACCEPTANCE.md').read_text(encoding='utf-8')
    for text in (windows,owner):
        assert 'installer-result-review.json' in text
        assert '--installer-result-review' in text
    assert '--distribution-envelope' in windows
    assert '--expected-source-commit' in windows
    assert '$expectedVersion = [string]$manifest.version' in windows
    assert '$expectedSourceCommit = [string]$manifest.source_commit' in windows
    assert '--expected-version $expectedVersion' in windows
    assert '--expected-source-commit $expectedSourceCommit' in windows
    assert '--expected-version 0.340.364' not in windows
    assert 'independently compare `$expectedSourceCommit`' in windows
    assert 'terminates the entire onefile process tree' not in windows
    assert 'attempts descendant cleanup' in windows
