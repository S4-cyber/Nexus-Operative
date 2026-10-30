param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$InstalledExecutable,
    [Parameter(Mandatory=$true)][string]$DataDirectory,
    [Parameter(Mandatory=$true)][string]$Version,
    [Parameter(Mandatory=$true)][string]$SourceCommit,
    [Parameter(Mandatory=$true)][string]$Output
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$limitations = 'Owner-operated single-profile effective-access probe only; no usernames, SIDs, paths, account data or credentials retained. Self-reported machine evidence is not independent attestation, native-window, power-loss, broker, market, funds, full-machine or release acceptance.'
if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)) { throw 'Windows is required' }
if ($Version -cnotmatch '^0\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') { throw 'Invalid development version binding' }
if ($SourceCommit -cnotmatch '^[0-9a-f]{40}$') { throw 'Invalid source commit binding' }
if (Test-Path -LiteralPath $Output) { throw 'Output already exists' }

$installerPath = (Resolve-Path -LiteralPath $Installer -ErrorAction Stop).Path
$executablePath = (Resolve-Path -LiteralPath $InstalledExecutable -ErrorAction Stop).Path
$localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$expectedExecutable = Join-Path $localAppData 'Programs\Nexus Operative\NexusOperative.exe'
$expectedData = Join-Path $localAppData 'NexusOperative'
$resolvedData = [IO.Path]::GetFullPath($DataDirectory).TrimEnd([IO.Path]::DirectorySeparatorChar)
if (-not [String]::Equals($executablePath, [IO.Path]::GetFullPath($expectedExecutable), [StringComparison]::OrdinalIgnoreCase)) { throw 'Installed executable is outside the default per-user location' }
if (-not [String]::Equals($resolvedData, [IO.Path]::GetFullPath($expectedData).TrimEnd([IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) { throw 'Data directory is outside the default per-user location' }

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Probe must run with an ordinary non-elevated token' }
if ($null -eq $identity.User) { throw 'Current Windows identity has no SID' }

$sessionId = [Guid]::NewGuid().ToString('D')
$probeRoot = Join-Path $resolvedData ".owner-profile-probe-$sessionId"
$target = Join-Path $probeRoot 'target.bin'
$replacement = Join-Path $probeRoot 'replacement.bin'
$backup = Join-Path $probeRoot 'backup.bin'
$renamed = Join-Path $probeRoot 'renamed.bin'
$directoryAclInheritance = $false
$fileAclInheritance = $false
$identityOwnsProbe = $false
$createWriteRead = $false
$exclusiveLock = $false
$replace = $false
$renameDelete = $false
$cleanup = $false

try {
    [IO.Directory]::CreateDirectory($probeRoot) | Out-Null
    $directoryAcl = Get-Acl -LiteralPath $probeRoot
    $directoryAclInheritance = -not $directoryAcl.AreAccessRulesProtected
    $ownerSid = $directoryAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    $identityOwnsProbe = [String]::Equals($ownerSid, $identity.User.Value, [StringComparison]::OrdinalIgnoreCase)
    if (-not $directoryAclInheritance -or -not $identityOwnsProbe) { throw 'Probe directory ACL is not inherited and current-user-owned' }

    [IO.File]::WriteAllBytes($target, [byte[]](1, 2, 3, 4))
    if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($target)) -cne [Convert]::ToBase64String([byte[]](1, 2, 3, 4))) { throw 'Probe readback failed' }
    $createWriteRead = $true
    $fileAcl = Get-Acl -LiteralPath $target
    $fileAclInheritance = -not $fileAcl.AreAccessRulesProtected
    if (-not $fileAclInheritance) { throw 'Probe file ACL inheritance is disabled' }

    $held = [IO.File]::Open($target, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        try {
            $unexpected = [IO.File]::Open($target, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
            $unexpected.Dispose()
            throw 'Exclusive file lock was not enforced'
        } catch [IO.IOException] {
            $exclusiveLock = $true
        } catch [System.UnauthorizedAccessException] {
            $exclusiveLock = $true
        }
    } finally {
        $held.Dispose()
    }

    [IO.File]::WriteAllBytes($replacement, [byte[]](5, 6, 7, 8))
    [IO.File]::Replace($replacement, $target, $backup, $true)
    if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($target)) -cne [Convert]::ToBase64String([byte[]](5, 6, 7, 8))) { throw 'Probe replacement failed' }
    if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($backup)) -cne [Convert]::ToBase64String([byte[]](1, 2, 3, 4))) { throw 'Probe replacement backup failed' }
    [IO.File]::Delete($backup)
    $replace = $true

    [IO.File]::Move($target, $renamed)
    [IO.File]::Delete($renamed)
    if ([IO.File]::Exists($target) -or [IO.File]::Exists($renamed)) { throw 'Probe rename/delete failed' }
    $renameDelete = $true
} finally {
    if ([IO.Directory]::Exists($probeRoot)) { Remove-Item -LiteralPath $probeRoot -Recurse -Force }
    $cleanup = -not [IO.Directory]::Exists($probeRoot)
}
if (-not $cleanup) { throw 'Probe cleanup failed' }

$record = [ordered]@{
    profile = 'nexus.windows-owner-profile-probe.v1'
    version = $Version
    source_commit = $SourceCommit
    installer_sha256 = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    installed_executable_sha256 = (Get-FileHash -LiteralPath $executablePath -Algorithm SHA256).Hash.ToLowerInvariant()
    probe_script_sha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    session_id = $sessionId
    timestamp_utc = [DateTime]::UtcNow.ToString('o')
    windows_version = [Environment]::OSVersion.Version.ToString()
    architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    ordinary_user_token = $true
    local_appdata_install = $true
    local_appdata_data = $true
    current_identity_owns_probe = $identityOwnsProbe
    directory_acl_inheritance_enabled = $directoryAclInheritance
    file_acl_inheritance_enabled = $fileAclInheritance
    create_write_read_verified = $createWriteRead
    exclusive_lock_verified = $exclusiveLock
    replace_verified = $replace
    rename_delete_verified = $renameDelete
    cleanup_verified = $cleanup
    private_paths_retained = $false
    actual_broker_acceptance = $false
    live_orders_enabled = $false
    money_movement_enabled = $false
    release_approved = $false
    limitations = $limitations
}
$parent = Split-Path -Parent ([IO.Path]::GetFullPath($Output))
if (-not [String]::IsNullOrEmpty($parent)) { [IO.Directory]::CreateDirectory($parent) | Out-Null }
$json = $record | ConvertTo-Json -Compress
$temporary = Join-Path $parent ('.owner-profile-probe-' + [Guid]::NewGuid().ToString('N') + '.tmp')
try {
    [IO.File]::WriteAllText($temporary, $json + "`n", [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($temporary, [IO.Path]::GetFullPath($Output))
} finally {
    if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
}
Write-Output 'OWNER_WINDOWS_PROFILE_PROBE_RECORDED'
