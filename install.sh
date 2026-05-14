#!/usr/bin/env bash
# Music Grabber — instalador para Linux (cualquier distro con python3 + pip)
#
# Uso:
#   bash install.sh              # instala
#   bash install.sh --uninstall  # desinstala (deja la biblioteca de música intacta)
#
# Qué hace:
#   - Comprueba que existen python3 (>=3.9), pip y venv.
#   - Crea un entorno virtual en  $XDG_DATA_HOME/MusicGrabber/venv
#   - Instala las dependencias de requirements.txt.
#   - Genera un lanzador en      ~/.local/bin/musicgrabber
#   - Genera un acceso .desktop  ~/.local/share/applications/musicgrabber.desktop
#
# Lo que NO hace:
#   - No instala ffmpeg ni yt-dlp en el sistema. La app los detecta o los
#     descarga al primer arranque.
#   - No borra tu biblioteca de música ni tu configuración al desinstalar.

set -euo pipefail

APP_NAME="MusicGrabber"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
INSTALL_DIR="$XDG_DATA_HOME/$APP_NAME"
APP_DIR="$INSTALL_DIR/app"
VENV_DIR="$INSTALL_DIR/venv"

BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/musicgrabber"
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/musicgrabber.desktop"

color()   { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
info()    { color "1;36" "==> $1"; }
ok()      { color "1;32" "✓  $1"; }
warn()    { color "1;33" "!  $1"; }
err()     { color "1;31" "✗  $1" >&2; }

uninstall() {
    info "Desinstalando $APP_NAME..."
    rm -f "$LAUNCHER" "$DESKTOP_FILE"
    rm -rf "$VENV_DIR" "$APP_DIR"
    # Mantiene config.json, .bin/, biblioteca de música — el usuario decide.
    ok "Eliminado. Tu biblioteca de música y configuración se conservan en $INSTALL_DIR"
    ok "Para borrar también esos restos:  rm -rf '$INSTALL_DIR'"
}

if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
    exit 0
fi

# ---------------------------------------------------------------------------
# 1. Dependencias del sistema
# ---------------------------------------------------------------------------
info "Comprobando dependencias del sistema..."

if ! command -v python3 >/dev/null 2>&1; then
    err "python3 no está instalado. Instálalo con tu gestor de paquetes:"
    err "  Fedora/Nobara: sudo dnf install python3 python3-pip"
    err "  Debian/Ubuntu: sudo apt install python3 python3-pip python3-venv"
    err "  Arch/Manjaro : sudo pacman -S python python-pip"
    err "  openSUSE     : sudo zypper install python3 python3-pip"
    exit 1
fi

PY_VER=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
PY_MAJOR=${PY_VER%.*}
PY_MINOR=${PY_VER#*.}
if [[ $PY_MAJOR -lt 3 ]] || { [[ $PY_MAJOR -eq 3 ]] && [[ $PY_MINOR -lt 9 ]]; }; then
    err "Python 3.9+ requerido. Detectado: $PY_VER"
    exit 1
fi
ok "Python $PY_VER"

if ! python3 -c "import venv" 2>/dev/null; then
    err "Falta el módulo venv. En Debian/Ubuntu:  sudo apt install python3-venv"
    exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    warn "ffmpeg no detectado en el sistema."
    warn "Recomendado instalarlo (más estable que la descarga interna):"
    warn "  Fedora/Nobara: sudo dnf install ffmpeg"
    warn "  Debian/Ubuntu: sudo apt install ffmpeg"
    warn "  Arch/Manjaro : sudo pacman -S ffmpeg"
    warn "Si lo omites, $APP_NAME lo descargará al primer arranque."
else
    ok "ffmpeg detectado: $(command -v ffmpeg)"
fi

if ! command -v yt-dlp >/dev/null 2>&1; then
    warn "yt-dlp no detectado en el sistema. $APP_NAME lo descargará al primer arranque."
else
    ok "yt-dlp detectado: $(command -v yt-dlp)"
fi

# ---------------------------------------------------------------------------
# 2. Copia de la app
# ---------------------------------------------------------------------------
info "Instalando $APP_NAME en $APP_DIR..."
mkdir -p "$APP_DIR"
# rsync si está disponible; cp como fallback.
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='venv' --exclude='.venv' \
        "$SCRIPT_DIR/" "$APP_DIR/"
else
    cp -r "$SCRIPT_DIR"/. "$APP_DIR/"
fi
ok "Código copiado."

# ---------------------------------------------------------------------------
# 3. Entorno virtual
# ---------------------------------------------------------------------------
info "Creando entorno virtual en $VENV_DIR..."
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet -r "$APP_DIR/requirements.txt"
deactivate
ok "Dependencias instaladas."

# ---------------------------------------------------------------------------
# 4. Lanzador en ~/.local/bin
# ---------------------------------------------------------------------------
info "Creando lanzador en $LAUNCHER..."
mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" "$APP_DIR/main.py" "\$@"
EOF
chmod +x "$LAUNCHER"
ok "Comando 'musicgrabber' disponible."

# ---------------------------------------------------------------------------
# 5. Acceso .desktop (para menú de aplicaciones)
# ---------------------------------------------------------------------------
info "Creando entrada de menú..."
mkdir -p "$DESKTOP_DIR"
ICON_PATH="$APP_DIR/assets/logo.png"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Music Grabber
Comment=Orquestador de preservación digital (TUI)
Exec=$LAUNCHER
Icon=$ICON_PATH
Terminal=true
Categories=AudioVideo;Audio;Utility;
Keywords=music;download;youtube;yt-dlp;
EOF
ok "Entrada de menú creada."

# ---------------------------------------------------------------------------
# 6. Aviso si ~/.local/bin no está en PATH
# ---------------------------------------------------------------------------
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        warn "~/.local/bin no está en tu PATH."
        warn "Añádelo a tu shell rc (bash/zsh):"
        warn "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
        ;;
esac

echo
ok "Instalación completada."
echo "    Ejecuta:   musicgrabber"
echo "    Desinstalar: bash $SCRIPT_DIR/install.sh --uninstall"
