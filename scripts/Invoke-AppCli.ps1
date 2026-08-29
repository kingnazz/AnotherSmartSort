<#
.SYNOPSIS
    Runs a AS Resume Sorter command-line switch and waits for it to finish.

.DESCRIPTION
    AS Resume Sorter is a windowed (GUI subsystem) application, because it is a
    desktop program and must not flash a console window when a user launches it.

    A consequence of that on Windows: `cmd` and PowerShell do NOT wait for a GUI
    application to exit. Writing

        & SmartPDFSorter.exe --smoke-test
        if ($LASTEXITCODE -ne 0) { ... }        # WRONG

    reads $LASTEXITCODE before the application has even run, so the check is
    meaningless -- it can report success while the app is still starting, or
    fail using a previous command's exit code.

    This script does it correctly: it starts the executable, waits for it,
    prints whatever it wrote, and exits with the application's real exit code.
    Use it from CI, from build scripts, and from deployment tooling.

.PARAMETER Exe
    Path to SmartPDFSorter.exe.

.PARAMETER AppArgs
    Arguments to pass, for example --version, --smoke-test or --ocr-info.

.PARAMETER TimeoutSeconds
    Fail if the application has not exited within this many seconds.

.EXAMPLE
    .\scripts\Invoke-AppCli.ps1 -Exe "C:\Program Files\AS Resume Sorter\SmartPDFSorter.exe" -AppArgs '--smoke-test'
    if ($LASTEXITCODE -ne 0) { throw "smoke test failed" }

.EXAMPLE
    $output = .\scripts\Invoke-AppCli.ps1 -Exe $exe -AppArgs '--ocr-info' -PassOutput
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string[]]$AppArgs,
    [int]$TimeoutSeconds = 300,
    [switch]$PassOutput
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Exe)) {
    Write-Error "Executable not found: $Exe"
    exit 127
}

$stdout = [System.IO.Path]::GetTempFileName()
$stderr = [System.IO.Path]::GetTempFileName()

try {
    $process = Start-Process -FilePath $Exe `
        -ArgumentList $AppArgs `
        -PassThru `
        -NoNewWindow `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr

    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch { }
        Write-Error "$([System.IO.Path]::GetFileName($Exe)) $AppArgs did not exit within $TimeoutSeconds seconds."
        exit 124
    }

    # WaitForExit(int) can return before the redirected streams are flushed.
    $process.WaitForExit()

    $outText = (Get-Content $stdout -Raw -ErrorAction SilentlyContinue)
    $errText = (Get-Content $stderr -Raw -ErrorAction SilentlyContinue)

    if ($PassOutput) {
        # Emit to the pipeline so a caller can capture and inspect it.
        if ($outText) { $outText }
    } else {
        if ($outText) { Write-Host $outText.TrimEnd() }
    }
    if ($errText -and $errText.Trim()) { Write-Host $errText.TrimEnd() }

    exit $process.ExitCode
}
finally {
    Remove-Item $stdout, $stderr -Force -ErrorAction SilentlyContinue
}
