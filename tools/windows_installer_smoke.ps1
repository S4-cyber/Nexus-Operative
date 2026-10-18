param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$Version,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [Parameter(Mandatory=$true)][string]$UpgradeSource,
    [Parameter(Mandatory=$true)][string]$UpgradeCommit
)
$ErrorActionPreference = 'Stop'
$installDir = Join-Path $env:RUNNER_TEMP 'nexus-install-smoke'
$dataDir = Join-Path $env:RUNNER_TEMP 'nexus-install-smoke-data'
New-Item -ItemType Directory -Force $EvidenceDirectory, $dataDir | Out-Null
$sentinel = Join-Path $dataDir 'preserve-data.txt'
Set-Content -Path $sentinel -Value 'Nexus installer must preserve user data' -Encoding UTF8
$sentinelHash = (Get-FileHash $sentinel -Algorithm SHA256).Hash
function Install-Nexus([string]$Label) {
    $log = Join-Path $EvidenceDirectory "$Label.log"
    $proc = Start-Process -FilePath $Installer -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',"/DIR=`"$installDir`"","/LOG=`"$log`"") -PassThru -Wait
    if ($proc.ExitCode -ne 0) { throw "Installer failed: $($proc.ExitCode)" }
    if (!(Test-Path "$installDir/NexusOperative.exe")) { throw 'Installed executable missing' }
}
Install-Nexus 'install'
python tools/packaged_paper_tools_smoke.py --executable "$installDir/NexusOperative.exe" --output "$EvidenceDirectory/installed-paper-tools.json"
if ($LASTEXITCODE -ne 0) { throw 'Installed paper operator tools failed' }
python tools/installed_smoke.py --exe "$installDir/NexusOperative.exe" --data "$dataDir" --version $Version --setup-check first-run --output "$EvidenceDirectory/first-start.json"
if ($LASTEXITCODE -ne 0) { throw 'First installed startup failed' }
$journal = Join-Path $dataDir 'order_lifecycle.sqlite3'
if (!(Test-Path $journal)) { throw 'Durable halt journal missing' }
$journalHash = (Get-FileHash $journal -Algorithm SHA256).Hash
$settings = Join-Path $dataDir 'portal_settings.sqlite3'
$settingsHash = (Get-FileHash $settings -Algorithm SHA256).Hash
Install-Nexus 'reinstall'
if ((Get-FileHash $journal -Algorithm SHA256).Hash -ne $journalHash) { throw 'Reinstall modified user journal' }
if ((Get-FileHash $settings -Algorithm SHA256).Hash -ne $settingsHash) { throw 'Reinstall modified saved setup' }
python tools/installed_smoke.py --exe "$installDir/NexusOperative.exe" --data "$dataDir" --version $Version --expect-halted --setup-check saved --output "$EvidenceDirectory/reinstalled-start.json"
if ($LASTEXITCODE -ne 0) { throw 'Reinstalled halt recovery failed' }
python tools/packaged_recovery_smoke.py --exe "$installDir/NexusOperative.exe" --version $Version --evidence "$EvidenceDirectory"
if ($LASTEXITCODE -ne 0) { throw 'Packaged backup recovery failed' }
python tools/packaged_recovery_smoke.py --exe "$installDir/NexusOperative.exe" --version $Version --evidence "$EvidenceDirectory/cross-version" --fixture-source "$UpgradeSource" --fixture-commit $UpgradeCommit
if ($LASTEXITCODE -ne 0) { throw 'Pinned prior-version data recovery failed' }
$defaultRoot = Join-Path $env:RUNNER_TEMP 'nexus-default-data-smoke'
python tools/installed_smoke.py --exe "$installDir/NexusOperative.exe" --default-data-root "$defaultRoot" --version $Version --setup-check first-run --output "$EvidenceDirectory/default-data-first.json"
if ($LASTEXITCODE -ne 0) { throw 'Default Windows data layout startup failed' }
python tools/installed_smoke.py --exe "$installDir/NexusOperative.exe" --default-data-root "$defaultRoot" --version $Version --expect-halted --setup-check saved --output "$EvidenceDirectory/default-data-restart.json"
if ($LASTEXITCODE -ne 0) { throw 'Default Windows data halt recovery failed' }
$defaultJournal = Join-Path $defaultRoot 'NexusOperative/storage/order_lifecycle.sqlite3'
$defaultHash = (Get-FileHash $defaultJournal -Algorithm SHA256).Hash
$defaultSettings = Join-Path $defaultRoot 'NexusOperative/storage/portal_settings.sqlite3'
$defaultSettingsHash = (Get-FileHash $defaultSettings -Algorithm SHA256).Hash
$settingsHash = (Get-FileHash $settings -Algorithm SHA256).Hash
if (Test-Path "$installDir/storage/order_lifecycle.sqlite3") { throw 'Installed app wrote data under its installation directory' }
$journalHash = (Get-FileHash $journal -Algorithm SHA256).Hash
$uninstallLog = Join-Path $EvidenceDirectory 'uninstall.log'
$proc = Start-Process -FilePath "$installDir/unins000.exe" -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/LOG=`"$uninstallLog`"") -PassThru -Wait
if ($proc.ExitCode -ne 0) { throw "Uninstaller failed: $($proc.ExitCode)" }
if (Test-Path "$installDir/NexusOperative.exe") { throw 'Uninstall left application executable' }
if ((Get-FileHash $defaultJournal -Algorithm SHA256).Hash -ne $defaultHash) { throw 'Uninstall modified default Windows data journal' }
if ((Get-FileHash $sentinel -Algorithm SHA256).Hash -ne $sentinelHash) { throw 'User sentinel changed' }
if ((Get-FileHash $journal -Algorithm SHA256).Hash -ne $journalHash) { throw 'Uninstall modified user journal' }
if ((Get-FileHash $settings -Algorithm SHA256).Hash -ne $settingsHash) { throw 'Uninstall modified saved setup' }
if ((Get-FileHash $defaultSettings -Algorithm SHA256).Hash -ne $defaultSettingsHash) { throw 'Uninstall modified default saved setup' }
@{first_run_paper_defaults=$true;saved_setup_survived_reinstall=$true;saved_setup_survived_restart=$true;setup_preserved_on_uninstall=$true;default_windows_data_recovery=$true;default_data_preserved_on_uninstall=$true;prior_version_data_recovery=$true;packaged_backup_recovery=$true;same_version_reinstall=$true;halt_recovered=$true;uninstall_preserved_data=$true;cross_version_upgrade_verified=$false;native_window_verified=$false} | ConvertTo-Json | Set-Content "$EvidenceDirectory/installer-result.json" -Encoding UTF8
