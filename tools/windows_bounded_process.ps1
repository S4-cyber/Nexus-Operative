$ProcessOutputDrainTimeoutMilliseconds = 10000

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
            throw "Installer smoke phase timed out: $Label"
        }
        $drainTask = [System.Threading.Tasks.Task]::WhenAll(
            [System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask)
        )
        if (!$drainTask.Wait($ProcessOutputDrainTimeoutMilliseconds)) {
            throw "Installer smoke phase output streams did not close within the fixed bound: $Label"
        }
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
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
        try { Stop-BoundedProcess $process }
        finally {
            try { $process.StandardOutput.Close() } catch {}
            try { $process.StandardError.Close() } catch {}
            $process.Dispose()
        }
    }
}
