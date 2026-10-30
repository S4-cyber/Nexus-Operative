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
$phaseEvidence = Join-Path $EvidenceDirectory 'installer-phases.jsonl'
$python = (Get-Command python -ErrorAction Stop).Source

function Write-Phase([string]$Label, [string]$Status, [int]$TimeoutSeconds) {
    $record = [ordered]@{
        mode = 'INSTALLER_SMOKE_PHASE'
        label = $Label
        status = $Status
        timeout_seconds = $TimeoutSeconds
        timestamp_utc = [DateTime]::UtcNow.ToString('o')
    }
    Add-Content -Path $phaseEvidence -Value ($record | ConvertTo-Json -Compress) -Encoding UTF8
    Write-Host "Nexus installer smoke phase $Status`: $Label"
}

function Stop-BoundedProcess([System.Diagnostics.Process]$Process) {
    if ($null -eq $Process) { return }
    try { if ($Process.HasExited) { return } }
    catch { return }
    try { $Process.Kill($true) }
    catch { try { $Process.Kill() } catch {} }
    if (!$Process.WaitForExit(10000)) {
        throw 'Installer smoke child tree did not stop'
    }
}

function Invoke-BoundedProcess(
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$Label,
    [int]$TimeoutSeconds
) {
    if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 300) {
        throw 'Installer smoke phase timeout is outside the fixed bound'
    }
    Write-Phase $Label 'started' $TimeoutSeconds
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FilePath
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $start
    $terminalRecorded = $false
    try {
        if (!$process.Start()) { throw "Installer smoke phase failed to start: $Label" }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (!$process.WaitForExit($TimeoutSeconds * 1000)) {
            Write-Phase $Label 'timed_out' $TimeoutSeconds
            $terminalRecorded = $true
            Stop-BoundedProcess $process
            throw "Installer smoke phase timed out: $Label"
        }
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($stdout.Length -gt 0) { Write-Host $stdout.TrimEnd() }
        if ($stderr.Length -gt 0) { Write-Error $stderr.TrimEnd() -ErrorAction Continue }
        if ($process.ExitCode -ne 0) {
            Write-Phase $Label 'failed' $TimeoutSeconds
            $terminalRecorded = $true
            throw "Installer smoke phase failed ($($process.ExitCode)): $Label"
        }
        Write-Phase $Label 'completed' $TimeoutSeconds
        $terminalRecorded = $true
    } catch {
        if (!$terminalRecorded) { Write-Phase $Label 'failed' $TimeoutSeconds }
        throw
    } finally {
        Stop-BoundedProcess $process
        $process.Dispose()
    }
}

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
@{first_run_paper_defaults=$true;saved_setup_survived_reinstall=$true;saved_setup_survived_restart=$true;setup_preserved_on_uninstall=$true;default_windows_data_recovery=$true;default_data_preserved_on_uninstall=$true;prior_version_data_recovery=$true;packaged_backup_recovery=$true;same_version_reinstall=$true;halt_recovered=$true;uninstall_preserved_data=$true;all_external_processes_bounded=$true;phase_evidence_recorded=$true;maximum_phase_timeout_seconds=300;cross_version_upgrade_verified=$false;native_window_verified=$false} | ConvertTo-Json | Set-Content "$EvidenceDirectory/installer-result.json" -Encoding UTF8
