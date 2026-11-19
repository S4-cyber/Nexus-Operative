from pathlib import Path
import re


ROOT=Path(__file__).resolve().parents[1]
PRIOR_RECOVERY_FIXTURE_COMMIT='5660ec19d66af6401d3bd4e6c271b6e8dc2bf461'


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
    assert "all_external_processes_bounded = $true" in script
    assert "phase_evidence_recorded = $true" in script
    assert "maximum_phase_timeout_seconds = 300" in script
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


def test_windows_workflow_retains_bounded_release_tail_and_always_uploads_diagnostics():
    workflow=(ROOT/'.github/workflows/windows-release.yml').read_text(encoding='utf-8')
    assert 'timeout-minutes: 150' in workflow
    assert 'name: Create distribution checksum manifest\n        timeout-minutes: 20' in workflow
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
