# Installs vcfa-folder-replication for the current user on Windows. No admin rights needed.
# Run from PowerShell:   powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$App  = Join-Path $env:LOCALAPPDATA 'vcfa-folder-replication'

# Find Python 3.10+ (the 'py' launcher first, then 'python')
$py = $null
foreach ($c in @(@('py','-3'), @('python'))) {
    try {
        $v = & $c[0] $c[1..($c.Length-1)] -c "import sys; print(int(sys.version_info >= (3,10)))" 2>$null
        if ($v -eq '1') { $py = $c; break }
    } catch {}
}
if (-not $py) {
    Write-Host 'Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then re-run this script.'
    exit 1
}
function Run-Py { param([string[]]$a) & $py[0] $py[1..($py.Length-1)] @a; if ($LASTEXITCODE) { throw "Python command failed" } }

Write-Host "Installing into $App ..."
Run-Py @('-m','venv',"$App\venv")
& "$App\venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& "$App\venv\Scripts\python.exe" -m pip install --quiet "$Here"
if ($LASTEXITCODE) { throw "pip install failed" }

# Start Menu + Desktop shortcuts (open the GUI; a console window shows the link and lets you quit with Ctrl+C)
$exe = "$App\venv\Scripts\vcfa-folders.exe"
$sh  = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {
    $lnk = $sh.CreateShortcut((Join-Path $dir 'vCenter Folder Replication.lnk'))
    $lnk.TargetPath = $exe; $lnk.Arguments = 'gui'; $lnk.WorkingDirectory = $env:USERPROFILE
    $lnk.Description = 'Copy VM folder structure between vCenters'; $lnk.Save()
}
Write-Host ''
Write-Host 'Done. Use the "vCenter Folder Replication" shortcut on your Desktop / Start Menu,'
Write-Host "or run:  & '$exe'"
Write-Host "Try it without a vCenter:  & '$exe' gui --demo"
Write-Host "To uninstall: delete $App and the two shortcuts."
