# Run on Windows: powershell -NoProfile -ExecutionPolicy Bypass -File .\bin\build-inkstitch-windows.ps1
# Installs missing tools using winget and builds origin/work in a fresh directory.
# Opens the output folder; run the generated installer to install Ink/Stitch.

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
    throw 'Run this script on 64-bit Windows.'
}

function Require-Tool($Id, $Paths, $Extra = @()) {
    $found = @($Paths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    if (-not $found.Count) {
        if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
            throw 'Install Microsoft App Installer (winget), then rerun this script.'
        }
        & winget.exe install --id $Id --exact --source winget --silent `
            --accept-package-agreements --accept-source-agreements @Extra | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Installation failed: $Id" }
        $found = @($Paths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    }
    if (-not $found.Count) { throw "Cannot find $Id. Check its installation path in this script." }
    return $found[0]
}

$python = Require-Tool 'Python.Python.3.11' @(
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:ProgramFiles\Python311\python.exe"
) @('--scope', 'user', '--architecture', 'x64')
$bash = Require-Tool 'MSYS2.MSYS2' @('C:\msys64\usr\bin\bash.exe')
$inno = Require-Tool 'JRSoftware.InnoSetup' @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 7\ISCC.exe"
)
$zip = Require-Tool '7zip.7zip' @("$env:ProgramFiles\7-Zip\7z.exe")

function Run-Bash($Command) {
    & $bash --login -c $Command
    if ($LASTEXITCODE -ne 0) { throw 'Build command failed; see the error above.' }
}

# Separate shells let MSYS2 runtime updates finish before installing the tools.
Run-Bash 'pacman -Syu --noconfirm'
Run-Bash 'pacman -Syu --noconfirm --needed make gettext xz git ca-certificates'

$run = Join-Path $env:LOCALAPPDATA ('InkStitchBuild\' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run -Force | Out-Null
$script = Join-Path $run 'build.sh'
$content = @'
#!/usr/bin/env bash
set -euo pipefail
cd "$(cygpath -u "$INKSTITCH_BUILD_RUN")"
python_exe=$(cygpath -u "$INKSTITCH_BUILD_PYTHON")
export PATH="$(cygpath -u "$INKSTITCH_BUILD_INNO"):$(cygpath -u "$INKSTITCH_BUILD_ZIP"):$PATH"
"$python_exe" -c 'import sys; assert sys.version_info[:2] == (3,11) and sys.maxsize > 2**32, "64-bit Python 3.11 required"'
git -c core.longpaths=true clone --recurse-submodules --branch work https://github.com/greubela/inkstitch.git inkstitch
cd inkstitch
"$python_exe" -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip wheel
python -m pip install wxPython==4.2.5
python -m pip install -r requirements.txt pyinstaller
python -m pytest
unset GITHUB_REF BUILD BUILD_DIST
make distlocal PLATFORM=windows PYTHON_EXECUTABLE=.venv/Scripts/python.exe
test -s artifacts/inkstitch-local-build-windows-64bit.exe
test -s artifacts/inkstitch-local-build-windows-64bit.zip
ls -lh artifacts/*.exe artifacts/*.zip
'@
[IO.File]::WriteAllText($script, $content.Replace("`r`n", "`n"), (New-Object Text.UTF8Encoding($false)))
$values = @{
    INKSTITCH_BUILD_RUN = $run
    INKSTITCH_BUILD_PYTHON = $python
    INKSTITCH_BUILD_INNO = (Split-Path $inno)
    INKSTITCH_BUILD_ZIP = (Split-Path $zip)
    INKSTITCH_BUILD_SCRIPT = $script
}
$previous = @{}
try {
    foreach ($name in $values.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process')
    }
    Run-Bash 'bash "$(cygpath -u "$INKSTITCH_BUILD_SCRIPT")"'
} finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
$artifacts = Join-Path $run 'inkstitch\artifacts'
Write-Host "Build finished: $artifacts" -ForegroundColor Green
Invoke-Item -LiteralPath $artifacts
