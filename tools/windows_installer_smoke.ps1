param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$Version,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [Parameter(Mandatory=$true)][string]$UpgradeSource,
    [Parameter(Mandatory=$true)][string]$UpgradeCommit,
    [Parameter(Mandatory=$true)][string]$SourceCommit
)
$ErrorActionPreference = 'Stop'
$installDir = Join-Path $env:RUNNER_TEMP 'nexus-install-smoke'
$dataDir = Join-Path $env:RUNNER_TEMP 'nexus-install-smoke-data'
New-Item -ItemType Directory -Force $EvidenceDirectory, $dataDir | Out-Null
$phaseEvidence = Join-Path $EvidenceDirectory 'installer-phases.jsonl'
$resultEvidence = Join-Path $EvidenceDirectory 'installer-result.json'
$python = (Get-Command python -ErrorAction Stop).Source
if ($Version -cnotmatch '^0\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') { throw 'Invalid development version binding' }
if ($SourceCommit -cnotmatch '^[0-9a-f]{40}$') { throw 'Invalid source commit binding' }
if (Test-Path $phaseEvidence) { throw 'Installer phase evidence already exists' }
if (Test-Path $resultEvidence) { throw 'Installer result evidence already exists' }
$installerSha256 = (Get-FileHash $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
$phaseSessionId = [Guid]::NewGuid().ToString('D')

function Write-Phase([string]$Label, [string]$Status, [int]$TimeoutSeconds) {
    $record = [ordered]@{
        mode = 'INSTALLER_SMOKE_PHASE'
        label = $Label
        status = $Status
        timeout_seconds = $TimeoutSeconds
        output_drain_timeout_seconds = 10
        timestamp_utc = [DateTime]::UtcNow.ToString('o')
        session_id = $phaseSessionId
        source_commit = $SourceCommit
        version = $Version
        installer_sha256 = $installerSha256
    }
    Add-Content -Path $phaseEvidence -Value ($record | ConvertTo-Json -Compress) -Encoding UTF8
    Write-Host "Nexus installer smoke phase $Status`: $Label"
}

. "$PSScriptRoot/windows_bounded_process.ps1"

$sentinel = Join-Path $dataDir 'preserve-data.txt'
Set-Content -Path $sentinel -Value 'Nexus installer must preserve user data' -Encoding UTF8
$sentinelHash = (Get-FileHash $sentinel -Algorithm SHA256).Hash
function Install-Nexus([string]$Label) {
    $log = Join-Path $EvidenceDirectory "$Label.log"
    Invoke-BoundedProcess $Installer @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',"/DIR=$installDir","/LOG=$log") $Label 180
    if (!(Test-Path "$installDir/NexusOperative.exe")) { throw 'Installed executable missing' }
}
Install-Nexus 'install'
Invoke-BoundedProcess $python @('tools/packaged_paper_tools_smoke.py','--executable',"$installDir/NexusOperative.exe",'--output',"$EvidenceDirectory/installed-paper-tools.json") 'installed-paper-tools' 300
Invoke-BoundedProcess $python @('tools/installed_smoke.py','--exe',"$installDir/NexusOperative.exe",'--data',"$dataDir",'--version',$Version,'--setup-check','first-run','--output',"$EvidenceDirectory/first-start.json") 'first-installed-start' 120
$journal = Join-Path $dataDir 'order_lifecycle.sqlite3'
if (!(Test-Path $journal)) { throw 'Durable halt journal missing' }
$journalHash = (Get-FileHash $journal -Algorithm SHA256).Hash
$settings = Join-Path $dataDir 'portal_settings.sqlite3'
$settingsHash = (Get-FileHash $settings -Algorithm SHA256).Hash
Install-Nexus 'reinstall'
if ((Get-FileHash $journal -Algorithm SHA256).Hash -ne $journalHash) { throw 'Reinstall modified user journal' }
if ((Get-FileHash $settings -Algorithm SHA256).Hash -ne $settingsHash) { throw 'Reinstall modified saved setup' }
Invoke-BoundedProcess $python @('tools/installed_smoke.py','--exe',"$installDir/NexusOperative.exe",'--data',"$dataDir",'--version',$Version,'--expect-halted','--setup-check','saved','--output',"$EvidenceDirectory/reinstalled-start.json") 'reinstalled-start' 120
Invoke-BoundedProcess $python @('tools/packaged_recovery_smoke.py','--exe',"$installDir/NexusOperative.exe",'--version',$Version,'--evidence',"$EvidenceDirectory") 'packaged-recovery' 150
Invoke-BoundedProcess $python @('tools/packaged_recovery_smoke.py','--exe',"$installDir/NexusOperative.exe",'--version',$Version,'--evidence',"$EvidenceDirectory/cross-version",'--fixture-source',"$UpgradeSource",'--fixture-commit',$UpgradeCommit) 'prior-version-recovery' 150
$defaultRoot = Join-Path $env:RUNNER_TEMP 'nexus-default-data-smoke'
Invoke-BoundedProcess $python @('tools/installed_smoke.py','--exe',"$installDir/NexusOperative.exe",'--default-data-root',"$defaultRoot",'--version',$Version,'--setup-check','first-run','--output',"$EvidenceDirectory/default-data-first.json") 'default-data-first-start' 120
Invoke-BoundedProcess $python @('tools/installed_smoke.py','--exe',"$installDir/NexusOperative.exe",'--default-data-root',"$defaultRoot",'--version',$Version,'--expect-halted','--setup-check','saved','--output',"$EvidenceDirectory/default-data-restart.json") 'default-data-restart' 120
$defaultJournal = Join-Path $defaultRoot 'NexusOperative/storage/order_lifecycle.sqlite3'
$defaultHash = (Get-FileHash $defaultJournal -Algorithm SHA256).Hash
$defaultSettings = Join-Path $defaultRoot 'NexusOperative/storage/portal_settings.sqlite3'
$defaultSettingsHash = (Get-FileHash $defaultSettings -Algorithm SHA256).Hash
$settingsHash = (Get-FileHash $settings -Algorithm SHA256).Hash
if (Test-Path "$installDir/storage/order_lifecycle.sqlite3") { throw 'Installed app wrote data under its installation directory' }
$journalHash = (Get-FileHash $journal -Algorithm SHA256).Hash
$uninstallLog = Join-Path $EvidenceDirectory 'uninstall.log'
Invoke-BoundedProcess "$installDir/unins000.exe" @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/LOG=$uninstallLog") 'uninstall' 180
if (Test-Path "$installDir/NexusOperative.exe") { throw 'Uninstall left application executable' }
if ((Get-FileHash $defaultJournal -Algorithm SHA256).Hash -ne $defaultHash) { throw 'Uninstall modified default Windows data journal' }
if ((Get-FileHash $sentinel -Algorithm SHA256).Hash -ne $sentinelHash) { throw 'User sentinel changed' }
if ((Get-FileHash $journal -Algorithm SHA256).Hash -ne $journalHash) { throw 'Uninstall modified user journal' }
if ((Get-FileHash $settings -Algorithm SHA256).Hash -ne $settingsHash) { throw 'Uninstall modified saved setup' }
if ((Get-FileHash $defaultSettings -Algorithm SHA256).Hash -ne $defaultSettingsHash) { throw 'Uninstall modified default saved setup' }
$result = [ordered]@{
    mode = 'INSTALLER_SMOKE_RESULT'
    version = $Version
    source_commit = $SourceCommit
    installer_sha256 = $installerSha256
    session_id = $phaseSessionId
    first_run_paper_defaults = $true
    saved_setup_survived_reinstall = $true
    saved_setup_survived_restart = $true
    setup_preserved_on_uninstall = $true
    default_windows_data_recovery = $true
    default_data_preserved_on_uninstall = $true
    prior_version_data_recovery = $true
    packaged_backup_recovery = $true
    same_version_reinstall = $true
    halt_recovered = $true
    uninstall_preserved_data = $true
    external_process_output_drains_bounded = $true
    phase_evidence_recorded = $true
    maximum_root_process_timeout_seconds = 300
    output_drain_timeout_seconds = 10
    cross_version_upgrade_verified = $false
    process_tree_containment_verified = $false
    native_window_verified = $false
    native_windows_acceptance = $false
    actual_broker_acceptance = $false
    live_orders_enabled = $false
    money_movement_enabled = $false
    release_approved = $false
    limitations = 'Successful CI installer semantic checks with bounded redirected-output drainage only; descendant containment is not verified; not clean-machine, native-window, ordinary-user, power-loss, broker, market, funds or release acceptance.'
}
$temporaryResult = Join-Path $EvidenceDirectory ('.installer-result-' + [Guid]::NewGuid().ToString('N') + '.tmp')
try {
    $utf8NoBom = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText($temporaryResult, (($result | ConvertTo-Json) + [Environment]::NewLine), $utf8NoBom)
    [IO.File]::Move($temporaryResult, $resultEvidence)
} finally {
    if (Test-Path $temporaryResult) { Remove-Item -LiteralPath $temporaryResult -Force }
}
