param(
    [Parameter(Mandatory=$true)][string]$OllamaDirectory,
    [string]$Python = "python",
    [string]$ISCC = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$runtimeRoot = (Resolve-Path -LiteralPath $OllamaDirectory).Path
if (!(Test-Path -LiteralPath (Join-Path $runtimeRoot 'ollama.exe')) -or
    !(Test-Path -LiteralPath (Join-Path $runtimeRoot 'lib'))) {
    throw 'Provide an extracted official Ollama Windows amd64 ZIP, including ollama.exe and lib.'
}
if (!(Test-Path -LiteralPath (Join-Path $runtimeRoot 'LICENSE'))) {
    throw 'Include the matching Ollama LICENSE in the runtime directory before building.'
}
if (!(Test-Path -LiteralPath $ISCC)) { throw 'Inno Setup 6 compiler not found. Pass -ISCC with its path.' }
& $Python -c "import tkinter, numpy, cv2, PIL.Image, pydantic, icalendar; t = tkinter.Tcl(); print('Native dependencies OK; Tcl', t.eval('info patchlevel'))"
if ($LASTEXITCODE -ne 0) { throw 'Native dependency check failed. Do not distribute an executable built in this environment.' }
& $Python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Setup tests failed.' }
& $Python -m PyInstaller --noconfirm --clean --onedir --windowed --name ScheduleScanner --icon schedule_scanner/images/CalGen.ico --collect-all customtkinter --collect-data tzdata --add-data 'schedule_scanner/images:schedule_scanner/images' release_launcher.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
$smoke = Start-Process -FilePath (Join-Path $projectRoot 'dist\ScheduleScanner\ScheduleScanner.exe') -ArgumentList '--self-test' -WindowStyle Hidden -Wait -PassThru
if ($smoke.ExitCode -ne 0) { throw 'Packaged application smoke test failed.' }
$target = Join-Path $projectRoot 'dist\ScheduleScanner\ollama'
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -LiteralPath (Join-Path $runtimeRoot 'ollama.exe') -Destination $target
Copy-Item -LiteralPath (Join-Path $runtimeRoot 'lib') -Destination $target -Recurse -Force
Copy-Item -LiteralPath (Join-Path $runtimeRoot 'LICENSE') -Destination $target
# Keep any additional notices shipped with the official runtime.
Get-ChildItem -LiteralPath $runtimeRoot -File | Where-Object { $_.Name -match 'license|notice|copying' } | Copy-Item -Destination $target -Force
& $Python -m pip freeze | Set-Content -Encoding utf8 dist\ScheduleScanner\build-requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not record build dependencies.' }
& $ISCC packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
Get-FileHash release\ScheduleScanner-Setup-v1.0.0.exe -Algorithm SHA256 | Format-List
