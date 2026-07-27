"""
Bootstrap de dependencias — yt-dlp y ffmpeg.

Estrategia híbrida:
  1. Si el binario está en $PATH, se usa el del sistema (gestionado por el
     gestor de paquetes — dnf, apt, pacman, winget, brew). No se autoactualiza
     desde aquí.
  2. Si no está, se descarga al directorio privado de la app (BIN_DIR) y se
     mantiene con `yt-dlp -U`. Para ffmpeg no hay autoupdate: cuando se
     necesite una versión nueva basta con borrar el binario y reabrir la app.

Cross-platform:
  - Linux  : yt-dlp_linux + ffmpeg static build de BtbN (linux64-gpl).
  - Windows: yt-dlp.exe + ffmpeg-...-win64-gpl.zip de BtbN.
  - macOS  : yt-dlp_macos. ffmpeg en macOS NO se autodescarga (no hay build
             oficial estable comparable a BtbN); se requiere `brew install
             ffmpeg`. La app avisa.
"""

import sys
import shutil
import tarfile
import zipfile
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, Callable

from core.state import APP_DATA_DIR

BIN_DIR = APP_DATA_DIR / ".bin"

# Indica si la última resolución encontró el binario en el sistema (no en BIN_DIR).
_ytdlp_is_system: bool = False
_ffmpeg_is_system: bool = False


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def ensure_dependencies(log: Callable[[str], None] = print) -> None:
    """Garantiza que yt-dlp y ffmpeg están disponibles. Llamar al arrancar."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    _resolve_ytdlp(log)
    _resolve_ffmpeg(log)


def get_ytdlp_path() -> str:
    """Devuelve la ruta absoluta o el comando de yt-dlp a invocar."""
    path = _find_local_ytdlp()
    if path:
        return str(path)
    sys_bin = shutil.which("yt-dlp")
    if sys_bin:
        return sys_bin
    return _expected_ytdlp_filename()  # falla ruidoso si no existe


def get_ffmpeg_path() -> str:
    """Devuelve la ruta absoluta a ffmpeg (o el comando si está en PATH)."""
    path = _find_local_ffmpeg()
    if path:
        return str(path)
    sys_bin = shutil.which("ffmpeg")
    if sys_bin:
        return sys_bin
    return _expected_ffmpeg_filename()


# ---------------------------------------------------------------------------
# Resolución y descarga — yt-dlp
# ---------------------------------------------------------------------------

def _resolve_ytdlp(log: Callable[[str], None]) -> None:
    global _ytdlp_is_system

    # 1. Sistema
    sys_bin = shutil.which("yt-dlp")
    if sys_bin:
        _ytdlp_is_system = True
        log(f"[Bootstrap] yt-dlp del sistema detectado: {sys_bin}")
        return

    # 2. Local (descargado por nosotros antes)
    local_bin = _find_local_ytdlp()
    if local_bin:
        _ytdlp_is_system = False
        log("[Bootstrap] Verificando actualizaciones de yt-dlp...")
        _try_self_update_ytdlp(local_bin, log)
        return

    # 3. Descargar
    log("[Bootstrap] Descargando motor de extracción (yt-dlp)...")
    target = BIN_DIR / _expected_ytdlp_filename()
    url = _ytdlp_download_url()
    try:
        urllib.request.urlretrieve(url, target)
        if not sys.platform.startswith("win"):
            target.chmod(target.stat().st_mode | 0o755)
        log("[Bootstrap] yt-dlp descargado correctamente.")
    except Exception as e:
        log(f"[Error] Falló la descarga de yt-dlp: {e}")


def _try_self_update_ytdlp(local_bin: Path, log: Callable[[str], None]) -> None:
    """Ejecuta `yt-dlp -U`. Sólo válido para el binario standalone descargado por nosotros."""
    kwargs = {"capture_output": True, "text": True, "timeout": 60}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run([str(local_bin), "-U"], **kwargs)
        out = (result.stdout or "").lower()
        if "up to date" in out or "is the latest" in out:
            log("[Bootstrap] yt-dlp ya está en la última versión.")
        else:
            log("[Bootstrap] yt-dlp actualizado a la versión más reciente.")
    except Exception as e:
        log(f"[Advertencia] No se pudo comprobar la actualización: {e}")


# ---------------------------------------------------------------------------
# Resolución y descarga — ffmpeg
# ---------------------------------------------------------------------------

def _resolve_ffmpeg(log: Callable[[str], None]) -> None:
    global _ffmpeg_is_system

    sys_bin = shutil.which("ffmpeg")
    if sys_bin:
        _ffmpeg_is_system = True
        log(f"[Bootstrap] FFmpeg del sistema detectado: {sys_bin}")
        return

    local_bin = _find_local_ffmpeg()
    if local_bin:
        _ffmpeg_is_system = False
        log("[Bootstrap] FFmpeg local detectado y listo.")
        return

    if sys.platform == "darwin":
        log("[Error] FFmpeg no está instalado. Instálalo con: brew install ffmpeg")
        return

    log("[Bootstrap] Descargando procesador de audio (ffmpeg)...")
    try:
        if sys.platform.startswith("win"):
            _download_ffmpeg_windows(log)
        else:
            _download_ffmpeg_linux(log)
        log("[Bootstrap] FFmpeg instalado correctamente.")
    except Exception as e:
        log(f"[Error] Falló la descarga de ffmpeg: {e}")


def _download_ffmpeg_windows(log: Callable[[str], None]) -> None:
    url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    zip_path = BIN_DIR / "ffmpeg.zip"
    urllib.request.urlretrieve(url, zip_path)
    log("[Bootstrap] Extrayendo ffmpeg.exe...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.filename.endswith("ffmpeg.exe"):
                info.filename = "ffmpeg.exe"
                zf.extract(info, BIN_DIR)
                break
    zip_path.unlink()


def _download_ffmpeg_linux(log: Callable[[str], None]) -> None:
    url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
    tar_path = BIN_DIR / "ffmpeg.tar.xz"
    urllib.request.urlretrieve(url, tar_path)
    log("[Bootstrap] Extrayendo ffmpeg...")
    extracted = None
    with tarfile.open(tar_path, "r:xz") as tf:
        for member in tf.getmembers():
            if member.name.endswith("/bin/ffmpeg") and member.isfile():
                member.name = "ffmpeg"
                tf.extract(member, BIN_DIR)
                extracted = BIN_DIR / "ffmpeg"
                break
    tar_path.unlink()
    if extracted and extracted.exists():
        extracted.chmod(extracted.stat().st_mode | 0o755)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _find_local_ytdlp() -> Optional[Path]:
    candidate = BIN_DIR / _expected_ytdlp_filename()
    return candidate if candidate.exists() else None


def _find_local_ffmpeg() -> Optional[Path]:
    candidate = BIN_DIR / _expected_ffmpeg_filename()
    return candidate if candidate.exists() else None


def _expected_ytdlp_filename() -> str:
    if sys.platform.startswith("win"):
        return "yt-dlp.exe"
    if sys.platform == "darwin":
        return "yt-dlp_macos"
    return "yt-dlp_linux"


def _expected_ffmpeg_filename() -> str:
    return "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"


def _ytdlp_download_url() -> str:
    base = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
    return base + _expected_ytdlp_filename()
