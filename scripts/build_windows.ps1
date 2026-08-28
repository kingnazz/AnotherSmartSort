<#
.SYNOPSIS
    Produces the complete AS Resume Sorter Windows distribution.

.DESCRIPTION
    One command, everything needed to deploy to clients:

        artifacts\SmartPDFSorter-Setup-<version>.msi      (primary, installed)
        artifacts\SmartPDFSorter-Portable-<version>.exe   (secondary, portable)
        artifacts\SHA256SUMS.txt

    Sequence:
      1.  validate the Windows build environment
      2.  verify Python
      3.  create or reuse the virtual environment
      4.  install dependencies
      5.  run the complete test suite (build stops here if anything fails)
      6.  clean previous production output
      7.  stage the bundled OCR runtime
      8.  build the installed (onedir) application
      9.  build the portable (onefile) application
      10. validate both application builds by running their smoke tests
      11. build the MSI with WiX
      12. validate the MSI
      13. write SHA-256 checksums
      14. report artifact paths

.PARAMETER Clean
    Remove build\, dist\ and artifacts\ before building.

.PARAMETER SkipTests
    Build without running the test suite. Not recommended.

.PARAMETER SkipPortable
    Build only the installed application and the MSI.

.PARAMETER Sign
    Authenticode-sign the binaries and the MSI. Requires signing configuration
    (see the Code signing section below). Without it, unsigned builds are still
    produced normally.

.PARAMETER SignToolPath
    Explicit path to signtool.exe. Discovered automatically when omitted.

.EXAMPLE
    .\scripts\build_windows.ps1 -Clean

.EXAMPLE
    .\scripts\build_windows.ps1 -Clean -Sign

.NOTES
    Code signing
    ------------
    Signing happens at two points, in this order:
        1. after the application binaries are built  (installed EXE + portable EXE)
        2. after the MSI is built                    (the MSI itself)
    That order matters: signing the MSI first would be invalidated by changing
    its payload afterwards.

    Provide credentials through environment variables. Never commit a
    certificate, a private key, or a password to this repository.

        $env:SPS_SIGN_CERT_PATH      = 'C:\secure\codesign.pfx'
        $env:SPS_SIGN_CERT_PASSWORD  = '...'          # or use SPS_SIGN_THUMBPRINT
        $env:SPS_SIGN_THUMBPRINT     = 'ABCD...'      # cert already in the store
        $env:SPS_SIGN_TIMESTAMP_URL  = 'http://timestamp.digicert.com'

    In CI, prefer a certificate installed into the machine store (thumbprint) or
    a cloud signing service, so no secret ever touches the working directory.
#>

[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$SkipTests,
    [switch]$SkipPortable,
    [switch]$Sign,
    [string]$SignToolPath
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$RepoRoot     = Split-Path -Parent $PSScriptRoot
$VenvPath     = Join-Path $RepoRoot ".venv"
$DistPath     = Join-Path $RepoRoot "dist"
$BuildPath    = Join-Path $RepoRoot "build"
$ArtifactPath = Join-Path $RepoRoot "artifacts"
$OcrPath      = Join-Path $RepoRoot "ocr"
$InstalledDir = Join-Path $DistPath "SmartPDFSorter"
$MinimumPython = [Version]"3.11"

$script:StepNumber = 0

function Write-Step {
    param([string]$Message)
    $script:StepNumber++
    Write-Host ""
    Write-Host ("[{0,2}] {1}" -f $script:StepNumber, $Message) -ForegroundColor Cyan
}
function Write-Ok   { param([string]$m) Write-Host "     $m" -ForegroundColor Green }
function Write-Info { param([string]$m) Write-Host "     $m" -ForegroundColor Gray }
function Write-Warn { param([string]$m) Write-Host "     $m" -ForegroundColor Yellow }
function Fail {
    param([string]$Message)
    Write-Host ""
    Write-Host "BUILD FAILED: $Message" -ForegroundColor Red
    exit 1
}

function Invoke-Checked {
    param([string]$Description, [scriptblock]$Action)
    & $Action
    if ($LASTEXITCODE -ne 0) { Fail "$Description (exit code $LASTEXITCODE)" }
}

function Find-SignTool {
    param([string]$Explicit)
    if ($Explicit) {
        if (Test-Path $Explicit) { return $Explicit }
        Fail "signtool.exe was not found at the supplied path: $Explicit"
    }
    $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    # Newest Windows SDK build tools first.
    $roots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "$env:ProgramFiles\Windows Kits\10\bin"
    ) | Where-Object { $_ -and (Test-Path $_) }

    foreach ($root in $roots) {
        $candidate = Get-ChildItem $root -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\x64\\" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }
    return $null
}

function Invoke-Signing {
    <#
        Authenticode-signs the supplied files.

        Credentials come from the environment so nothing secret is ever written
        into the repository or passed on a command line that ends up in logs:
          SPS_SIGN_THUMBPRINT    - certificate already in the certificate store
          SPS_SIGN_CERT_PATH     - path to a .pfx  (with SPS_SIGN_CERT_PASSWORD)
          SPS_SIGN_TIMESTAMP_URL - RFC 3161 timestamp server
    #>
    param(
        [string[]]$Paths,
        [string]$SignToolPath
    )

    $tool = Find-SignTool -Explicit $SignToolPath
    if (-not $tool) {
        Fail "signtool.exe was not found. Install the Windows SDK, or pass -SignToolPath."
    }

    $timestamp = $env:SPS_SIGN_TIMESTAMP_URL
    if (-not $timestamp) { $timestamp = "http://timestamp.digicert.com" }

    $common = @("sign", "/fd", "SHA256", "/tr", $timestamp, "/td", "SHA256")

    if ($env:SPS_SIGN_THUMBPRINT) {
        $common += @("/sha1", $env:SPS_SIGN_THUMBPRINT)
    } elseif ($env:SPS_SIGN_CERT_PATH) {
        if (-not (Test-Path $env:SPS_SIGN_CERT_PATH)) {
            Fail "SPS_SIGN_CERT_PATH points at a file that does not exist."
        }
        $common += @("/f", $env:SPS_SIGN_CERT_PATH)
        if ($env:SPS_SIGN_CERT_PASSWORD) { $common += @("/p", $env:SPS_SIGN_CERT_PASSWORD) }
    } else {
        Fail @"
-Sign was requested but no signing credentials were provided.
Set one of:
  `$env:SPS_SIGN_THUMBPRINT = '<certificate thumbprint in the store>'
  `$env:SPS_SIGN_CERT_PATH  = '<path to .pfx>'   (plus SPS_SIGN_CERT_PASSWORD)
"@
    }

    foreach ($path in $Paths) {
        if (-not (Test-Path $path)) { Fail "Cannot sign a file that does not exist: $path" }
        & $tool @common $path
        if ($LASTEXITCODE -ne 0) { Fail "Signing failed for $path" }
        Write-Info "Signed $(Split-Path $path -Leaf)"
    }
}

Push-Location $RepoRoot
try {
    Write-Host "AS Resume Sorter - Windows production build" -ForegroundColor White
    Write-Host "===========================================" -ForegroundColor White

    # ---------------------------------------------------------------- 1. env
    Write-Step "Validating the build environment"
    if (-not $IsWindows -and $PSVersionTable.PSVersion.Major -ge 6) {
        Fail "This script builds Windows artifacts and must run on Windows."
    }
    if ([Environment]::Is64BitOperatingSystem -ne $true) {
        Fail "A 64-bit Windows build host is required."
    }
    Write-Ok "Windows x64 build host"

    # ------------------------------------------------------------- 2. python
    Write-Step "Checking Python"
    $pythonCommand = $null
    foreach ($candidate in @("py -3.12", "py -3.11", "python", "python3")) {
        $parts = $candidate.Split(" ")
        $exe = $parts[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $extra = @()
        if ($parts.Length -gt 1) { $extra = $parts[1..($parts.Length - 1)] }
        try {
            $version = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch { continue }
        if ($LASTEXITCODE -ne 0 -or -not $version) { continue }
        if ([Version]$version -ge $MinimumPython) {
            $pythonCommand = $candidate
            Write-Ok "Using $candidate (Python $version)"
            break
        }
    }
    if (-not $pythonCommand) {
        Fail "Python $MinimumPython or newer was not found. Install it from https://www.python.org/downloads/"
    }
    $pythonParts = $pythonCommand.Split(" ")
    $pythonExe = $pythonParts[0]
    $pythonArgs = @()
    if ($pythonParts.Length -gt 1) { $pythonArgs = $pythonParts[1..($pythonParts.Length - 1)] }

    # --------------------------------------------------------------- 3. venv
    Write-Step "Preparing the virtual environment"
    if (-not (Test-Path $VenvPath)) {
        Invoke-Checked "Creating the virtual environment" { & $pythonExe @pythonArgs -m venv $VenvPath }
        Write-Ok "Created .venv"
    } else {
        Write-Ok "Reusing the existing .venv"
    }
    $VenvPython = Join-Path $VenvPath "Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) { Fail "The virtual environment is incomplete: $VenvPython is missing." }

    # ------------------------------------------------------- 4. dependencies
    Write-Step "Installing dependencies"
    Invoke-Checked "Upgrading pip" { & $VenvPython -m pip install --upgrade pip --quiet }
    Invoke-Checked "Installing requirements" {
        & $VenvPython -m pip install -r (Join-Path $RepoRoot "requirements-dev.txt") --quiet
    }
    Write-Ok "Dependencies are up to date"

    # ------------------------------------------------------------ version
    $Version = (& $VenvPython -m app.version).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $Version) { Fail "Could not read the application version from app/version.py" }
    Write-Ok "Building version $Version"

    $MsiName      = "SmartPDFSorter-Setup-$Version.msi"
    $PortableName = "SmartPDFSorter-Portable-$Version.exe"

    # -------------------------------------------------------------- 5. tests
    if ($SkipTests) {
        Write-Step "Skipping tests (-SkipTests)"
        Write-Warn "Artifacts are being produced without a passing test run."
    } else {
        Write-Step "Running the test suite"
        $env:QT_QPA_PLATFORM = "offscreen"
        & $VenvPython -m pytest -q
        $testExit = $LASTEXITCODE
        Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        if ($testExit -ne 0) { Fail "Tests failed. Fix them, or re-run with -SkipTests to build anyway." }
        Write-Ok "All tests passed"
    }

    # -------------------------------------------------------------- 6. clean
    Write-Step "Preparing output directories"
    if ($Clean) {
        foreach ($path in @($BuildPath, $DistPath, $ArtifactPath)) {
            if (Test-Path $path) { Remove-Item $path -Recurse -Force; Write-Info "Removed $path" }
        }
    }
    New-Item -ItemType Directory -Force -Path $ArtifactPath | Out-Null
    Write-Ok "Artifacts will be written to $ArtifactPath"

    # ---------------------------------------------------------------- 7. OCR
    Write-Step "Staging the bundled OCR runtime"
    Invoke-Checked "Fetching the OCR runtime" {
        & $VenvPython (Join-Path $RepoRoot "scripts\fetch_ocr_runtime.py") --output $OcrPath
    }
    $ocrExe = Join-Path $OcrPath "tesseract.exe"
    if (-not (Test-Path $ocrExe)) { Fail "The OCR runtime was not staged: $ocrExe is missing." }
    $ocrTrained = Join-Path $OcrPath "tessdata\eng.traineddata"
    if (-not (Test-Path $ocrTrained)) { Fail "English OCR data is missing: $ocrTrained" }
    Write-Ok "OCR runtime staged ($([Math]::Round((Get-ChildItem $OcrPath -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)) MB)"

    # ------------------------------------------------- 8. installed (onedir)
    Write-Step "Building the installed application (onedir)"
    Invoke-Checked "PyInstaller (installed build)" {
        & $VenvPython -m PyInstaller (Join-Path $RepoRoot "SmartPDFSorter.spec") --noconfirm --clean
    }
    $InstalledExe = Join-Path $InstalledDir "SmartPDFSorter.exe"
    if (-not (Test-Path $InstalledExe)) { Fail "The installed build did not produce $InstalledExe" }

    # The OCR runtime sits NEXT TO the executable, not inside _internal, which
    # is where the application looks for it in an installed layout.
    $InstalledOcr = Join-Path $InstalledDir "ocr"
    if (Test-Path $InstalledOcr) { Remove-Item $InstalledOcr -Recurse -Force }
    Copy-Item $OcrPath $InstalledOcr -Recurse
    Copy-Item (Join-Path $RepoRoot "THIRD_PARTY_NOTICES.md") $InstalledDir -Force
    Write-Ok "Installed build ready: $InstalledDir"

    # ------------------------------------------------- 9. portable (onefile)
    if ($SkipPortable) {
        Write-Step "Skipping the portable build (-SkipPortable)"
        $PortableExe = $null
    } else {
        Write-Step "Building the portable application (onefile)"
        Invoke-Checked "PyInstaller (portable build)" {
            & $VenvPython -m PyInstaller (Join-Path $RepoRoot "SmartPDFSorter-Portable.spec") --noconfirm --clean
        }
        $PortableExe = Join-Path $DistPath "SmartPDFSorter-Portable.exe"
        if (-not (Test-Path $PortableExe)) { Fail "The portable build did not produce $PortableExe" }
        Write-Ok "Portable build ready: $PortableExe"
    }

    # ------------------------------------------------------ 10. validate apps
    Write-Step "Validating the application builds"

    # These executables are GUI-subsystem, so PowerShell does NOT wait for them.
    # Invoke-AppCli.ps1 starts them, waits, and returns the real exit code;
    # calling them with & would test a stale $LASTEXITCODE and pass spuriously.
    $AppCli = Join-Path $PSScriptRoot "Invoke-AppCli.ps1"

    $reportedVersion = (& $AppCli -Exe $InstalledExe -AppArgs '--version' -PassOutput | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { Fail "The installed application failed to report its version." }
    if ($reportedVersion -notmatch [regex]::Escape($Version)) {
        Fail "Version mismatch: the installed build reports '$reportedVersion' but the build is $Version."
    }
    Write-Ok "Installed build reports: $reportedVersion"

    & $AppCli -Exe $InstalledExe -AppArgs '--smoke-test'
    if ($LASTEXITCODE -ne 0) { Fail "The installed application failed its smoke test." }
    Write-Ok "Installed build passed its smoke test"

    $ocrInfo = (& $AppCli -Exe $InstalledExe -AppArgs '--ocr-info' -PassOutput | Out-String)
    if ($ocrInfo -notmatch "bundled") {
        Write-Warn "The installed build did not resolve the bundled OCR runtime:"
        Write-Warn $ocrInfo.Trim()
        Fail "Bundled OCR was not detected; scanned PDFs would not work after install."
    }
    Write-Ok "Installed build resolves its bundled OCR runtime"

    if ($PortableExe) {
        & $AppCli -Exe $PortableExe -AppArgs '--smoke-test'
        if ($LASTEXITCODE -ne 0) { Fail "The portable application failed its smoke test." }
        Write-Ok "Portable build passed its smoke test"
    }

    # -------------------------------------------------- 10b. sign binaries
    if ($Sign) {
        Write-Step "Signing the application binaries"
        $toSign = @($InstalledExe)
        if ($PortableExe) { $toSign += $PortableExe }
        Invoke-Signing -Paths $toSign -SignToolPath $SignToolPath
        Write-Ok "Binaries signed"
    }

    # ----------------------------------------------------------- 11. the MSI
    Write-Step "Building the MSI"
    $wix = Get-Command wix -ErrorAction SilentlyContinue
    if (-not $wix) {
        Write-Info "Installing the WiX toolset as a dotnet global tool..."
        if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
            Fail "Neither wix nor dotnet was found. Install the .NET SDK, then: dotnet tool install --global wix"
        }
        Invoke-Checked "Installing WiX" { dotnet tool install --global wix --version 5.0.2 }
        $env:PATH = "$env:PATH;$env:USERPROFILE\.dotnet\tools"
        $wix = Get-Command wix -ErrorAction SilentlyContinue
        if (-not $wix) { Fail "WiX was installed but 'wix' is still not on PATH. Open a new shell and retry." }
    }
    Write-Info "Using $($wix.Source)"

    # The UI extension supplies the standard install-directory dialog set.
    & wix extension add --global WixToolset.UI.wixext/5.0.2 2>&1 | Out-Null

    $MsiPath = Join-Path $ArtifactPath $MsiName
    Invoke-Checked "WiX build" {
        & wix build (Join-Path $RepoRoot "installer\Package.wxs") `
            -arch x64 `
            -d "Version=$Version" `
            -d "HarvestPath=$InstalledDir" `
            -d "RepoRoot=$RepoRoot" `
            -ext WixToolset.UI.wixext `
            -o $MsiPath
    }

    # ------------------------------------------------------ 12. validate MSI
    Write-Step "Validating the MSI"
    if (-not (Test-Path $MsiPath)) { Fail "The MSI was not produced at $MsiPath" }
    $msiSizeMb = [Math]::Round((Get-Item $MsiPath).Length / 1MB, 1)
    if ($msiSizeMb -lt 20) { Fail "The MSI is only $msiSizeMb MB, which is too small to contain the application." }

    try {
        $installer = New-Object -ComObject WindowsInstaller.Installer
        $database = $installer.GetType().InvokeMember(
            "OpenDatabase", "InvokeMethod", $null, $installer, @($MsiPath, 0))
        function Get-MsiProperty {
            param($Database, [string]$Name)
            $view = $Database.GetType().InvokeMember("OpenView", "InvokeMethod", $null, $Database,
                @("SELECT Value FROM Property WHERE Property = '$Name'"))
            $view.GetType().InvokeMember("Execute", "InvokeMethod", $null, $view, $null) | Out-Null
            $record = $view.GetType().InvokeMember("Fetch", "InvokeMethod", $null, $view, $null)
            if ($null -eq $record) { return $null }
            return $record.GetType().InvokeMember("StringData", "GetProperty", $null, $record, 1)
        }
        $msiVersion = Get-MsiProperty $database "ProductVersion"
        $msiName    = Get-MsiProperty $database "ProductName"
        $msiUpgrade = Get-MsiProperty $database "UpgradeCode"

        if ($msiVersion -ne $Version) { Fail "MSI ProductVersion is '$msiVersion' but should be '$Version'." }
        Write-Ok "ProductName    : $msiName"
        Write-Ok "ProductVersion : $msiVersion"
        Write-Ok "UpgradeCode    : $msiUpgrade"
    } catch {
        Write-Warn "Could not read MSI properties for verification: $_"
    }
    Write-Ok "MSI built: $MsiPath ($msiSizeMb MB)"

    if ($Sign) {
        Write-Step "Signing the MSI"
        Invoke-Signing -Paths @($MsiPath) -SignToolPath $SignToolPath
        Write-Ok "MSI signed"
    }

    # --------------------------------------------------- 13. collect + hash
    Write-Step "Collecting artifacts and writing checksums"
    if ($PortableExe) {
        Copy-Item $PortableExe (Join-Path $ArtifactPath $PortableName) -Force
    }

    # WiX writes a .wixpdb debug symbol file beside the MSI; it is a build
    # artefact rather than something clients receive.
    Remove-Item (Join-Path $ArtifactPath "*.wixpdb") -Force -ErrorAction SilentlyContinue

    $checksumFile = Join-Path $ArtifactPath "SHA256SUMS.txt"
    $lines = @()
    foreach ($file in Get-ChildItem $ArtifactPath -File | Where-Object { $_.Name -ne "SHA256SUMS.txt" } | Sort-Object Name) {
        $hash = (Get-FileHash $file.FullName -Algorithm SHA256).Hash.ToLower()
        $lines += "$hash  $($file.Name)"
        Write-Info "$($file.Name)  ($([Math]::Round($file.Length / 1MB, 1)) MB)"
    }
    $lines -join "`n" | Set-Content -Path $checksumFile -Encoding ascii
    Write-Ok "Checksums written to $checksumFile"

    # ------------------------------------------------------------ 14. report
    Write-Host ""
    Write-Host "BUILD SUCCEEDED" -ForegroundColor Green
    Write-Host "===============" -ForegroundColor Green
    Write-Host ""
    Write-Host "Primary deliverable (install on client PCs):" -ForegroundColor White
    Write-Host "  $MsiPath"
    if ($PortableExe) {
        Write-Host ""
        Write-Host "Secondary deliverable (no installation):" -ForegroundColor White
        Write-Host "  $(Join-Path $ArtifactPath $PortableName)"
    }
    Write-Host ""
    Write-Host "Checksums:" -ForegroundColor White
    Write-Host "  $checksumFile"
    Write-Host ""
    Write-Host "Silent install:" -ForegroundColor White
    Write-Host "  msiexec /i `"$MsiName`" /qn /norestart"
    Write-Host "Silent install with a desktop shortcut:" -ForegroundColor White
    Write-Host "  msiexec /i `"$MsiName`" /qn /norestart INSTALLDESKTOPSHORTCUT=1"
    Write-Host "Silent uninstall:" -ForegroundColor White
    Write-Host "  msiexec /x `"$MsiName`" /qn /norestart"
    Write-Host ""
    if (-not $Sign) {
        Write-Host "These artifacts are unsigned. Windows SmartScreen will warn on first run." -ForegroundColor Yellow
        Write-Host "Re-run with -Sign once a code-signing certificate is configured." -ForegroundColor Yellow
    }
}
finally {
    Pop-Location
}
