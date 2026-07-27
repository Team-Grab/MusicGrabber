# Music Grabber - instalador para Windows (PowerShell)
#
# Uso (desde PowerShell, no necesita admin):
#   irm https://raw.githubusercontent.com/Team-Grab/MusicGrabber/main/install.ps1 | iex
# o, ya teniendo el repo clonado:
#   .\install.ps1
#   .\install.ps1 -Uninstall
#
# Qué hace:
#   - Comprueba que existe Python 3.9+. Si falta, lo instala con winget.
#   - Copia o clona el repo en   %LOCALAPPDATA%\MusicGrabber\app
#   - Crea venv en               %LOCALAPPDATA%\MusicGrabber\venv
#   - Instala requirements.txt.
#   - Crea un .cmd en            %LOCALAPPDATA%\Microsoft\WindowsApps\musicgrabber.cmd
#     (esa carpeta YA está en el PATH del usuario en Windows 10/11)
#   - Crea acceso directo en el Menu Inicio.
#
# Lo que NO hace:
#   - No instala ffmpeg ni yt-dlp en el sistema; la app los detecta o los
#     descarga al primer arranque.

[CmdletBinding()]
param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$AppName     = "MusicGrabber"
$RepoUrl     = "https://github.com/Team-Grab/MusicGrabber.git"
$InstallDir  = Join-Path $env:LOCALAPPDATA $AppName
$AppDir      = Join-Path $InstallDir "app"
$VenvDir     = Join-Path $InstallDir "venv"
$BinDir      = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
$Launcher    = Join-Path $BinDir "musicgrabber.cmd"
$StartMenu   = [Environment]::GetFolderPath("Programs")
$Shortcut    = Join-Path $StartMenu "Music Grabber.lnk"

function Info($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok($msg)    { Write-Host "OK  $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "!   $msg" -ForegroundColor Yellow }
function Fail($msg)  { Write-Host "X   $msg" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------
# UNINSTALL
# ---------------------------------------------------------------------------
if ($Uninstall) {
    Info "Desinstalando $AppName..."
    Remove-Item -Force -ErrorAction SilentlyContinue $Launcher
    Remove-Item -Force -ErrorAction SilentlyContinue $Shortcut
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $VenvDir
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $AppDir
    Ok "Eliminado. Tu configuracion y biblioteca de musica se conservan en $InstallDir"
    Ok "Para borrar tambien esos restos:  Remove-Item -Recurse -Force '$InstallDir'"
    exit 0
}

# ---------------------------------------------------------------------------
# 1. Python
# ---------------------------------------------------------------------------
Info "Comprobando Python..."
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Warn "Python no detectado. Intentando instalar con winget..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "winget no disponible. Instala Python 3.10+ manualmente desde https://www.python.org/downloads/ y vuelve a ejecutar este instalador."
    }
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    # Refrescar PATH en la sesion actual
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Fail "Python sigue sin detectarse despues de instalar. Reabre PowerShell y reintenta."
    }
}

$pyVer = & python -c "import sys;print('%d.%d'%sys.version_info[:2])"
$verParts = $pyVer.Split('.')
if ([int]$verParts[0] -lt 3 -or ([int]$verParts[0] -eq 3 -and [int]$verParts[1] -lt 9)) {
    Fail "Python 3.9+ requerido. Detectado: $pyVer"
}
Ok "Python $pyVer"

# ---------------------------------------------------------------------------
# 2. Copiar o clonar el codigo
# ---------------------------------------------------------------------------
Info "Instalando $AppName en $AppDir..."
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null

$ScriptDir = $PSScriptRoot
if ($ScriptDir -and (Test-Path (Join-Path $ScriptDir "main.py"))) {
    # Modo local: ejecutado desde un clon.
    Info "Copiando desde $ScriptDir..."
    robocopy $ScriptDir $AppDir /E /NFL /NDL /NJH /NJS /NP /XD .git venv .venv __pycache__ /XF "*.pyc" | Out-Null
    # robocopy usa códigos 0-7 para éxito parcial; solo >=8 indica error real.
    if ($LASTEXITCODE -ge 8) { Fail "Error al copiar archivos (robocopy exit $LASTEXITCODE)" }
    $LASTEXITCODE = 0
} else {
    # Modo remoto (irm | iex): clonar.
    Info "Clonando $RepoUrl..."
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Fail "git no disponible. Instalalo con:  winget install -e --id Git.Git"
    }
    if (Test-Path (Join-Path $AppDir ".git")) {
        Push-Location $AppDir
        git pull --ff-only
        Pop-Location
    } else {
        # Borra y reclona limpio
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $AppDir
        git clone --depth 1 $RepoUrl $AppDir
    }
}
Ok "Codigo desplegado."

# ---------------------------------------------------------------------------
# 3. Entorno virtual
# ---------------------------------------------------------------------------
Info "Creando entorno virtual en $VenvDir..."
if (-not (Test-Path $VenvDir)) {
    & python -m venv $VenvDir
}
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet -r (Join-Path $AppDir "requirements.txt")
Ok "Dependencias instaladas."

# ---------------------------------------------------------------------------
# 4. Lanzador musicgrabber.cmd en PATH del usuario
# ---------------------------------------------------------------------------
Info "Creando lanzador en $Launcher..."
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$cmdContent = @"
@echo off
"$VenvPython" "$(Join-Path $AppDir 'main.py')" %*
"@
Set-Content -Path $Launcher -Value $cmdContent -Encoding ASCII
Ok "Comando 'musicgrabber' disponible."

# ---------------------------------------------------------------------------
# 5. Acceso directo en Menu Inicio
# ---------------------------------------------------------------------------
Info "Creando acceso directo en Menu Inicio..."
$wsh = New-Object -ComObject WScript.Shell
$sc  = $wsh.CreateShortcut($Shortcut)
# Preferir Windows Terminal (mejor soporte ANSI/Unicode para la TUI).
# Fallback a cmd.exe si wt.exe no está disponible.
$wtExe = (Get-Command wt.exe -ErrorAction SilentlyContinue)
if ($wtExe) {
    $sc.TargetPath = $wtExe.Source
    $sc.Arguments  = "-- `"$Launcher`""
} else {
    $sc.TargetPath = "$env:WINDIR\System32\cmd.exe"
    $sc.Arguments  = "/k `"$Launcher`""
}
$sc.WorkingDirectory = $AppDir
$iconPath = Join-Path $AppDir "assets\app.ico"
if (Test-Path $iconPath) { $sc.IconLocation = $iconPath }
$sc.Description = "Music Grabber - Orquestador de preservacion digital"
$sc.Save()
Ok "Acceso directo creado."

Write-Host ""
Ok "Instalacion completada."
Write-Host "    Ejecuta:    musicgrabber"
Write-Host "    Desinstalar: .\install.ps1 -Uninstall"
Write-Host ""
Warn "Si el comando 'musicgrabber' no se reconoce, cierra y abre PowerShell."
