import os
import urllib.request
import zipfile
import subprocess
from pathlib import Path
from core.state import APP_DATA_DIR

BIN_DIR = APP_DATA_DIR / ".bin"

def ensure_dependencies(log_callback=print) -> None:
    """Verifica, descarga y actualiza las dependencias."""
    if not BIN_DIR.exists():
        BIN_DIR.mkdir(parents=True)
        
    _download_ytdlp(log_callback)
    _download_ffmpeg(log_callback)

def _download_ytdlp(log_callback) -> None:
    ytdlp_path = BIN_DIR / "yt-dlp.exe"
    if not ytdlp_path.exists():
        log_callback("[Bootstrap] Descargando motor de extracción (yt-dlp)...")
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
        try:
            urllib.request.urlretrieve(url, ytdlp_path)
            log_callback("[Bootstrap] yt-dlp descargado correctamente.")
        except Exception as e:
            log_callback(f"[Error] Falló la descarga de yt-dlp: {e}")
    else:
        log_callback("[Bootstrap] Verificando actualizaciones de yt-dlp...")
        try:
            # Usamos el actualizador integrado de yt-dlp. CREATE_NO_WINDOW evita que parpadee la consola negra.
            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            result = subprocess.run(
                [str(ytdlp_path), "-U"], 
                capture_output=True, text=True, creationflags=creation_flags
            )
            if "up to date" in result.stdout.lower():
                log_callback("[Bootstrap] yt-dlp ya está en la última versión.")
            else:
                log_callback("[Bootstrap] yt-dlp actualizado a la versión más reciente.")
        except Exception as e:
            log_callback(f"[Advertencia] No se pudo comprobar la actualización: {e}")

def _download_ffmpeg(log_callback) -> None:
    ffmpeg_path = BIN_DIR / "ffmpeg.exe"
    if not ffmpeg_path.exists():
        log_callback("[Bootstrap] Descargando procesador de audio (ffmpeg)...")
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        zip_path = BIN_DIR / "ffmpeg.zip"
        
        try:
            urllib.request.urlretrieve(url, zip_path)
            log_callback("[Bootstrap] Extrayendo ffmpeg.exe...")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file_info in zip_ref.infolist():
                    if file_info.filename.endswith("ffmpeg.exe"):
                        file_info.filename = "ffmpeg.exe"
                        zip_ref.extract(file_info, BIN_DIR)
            zip_path.unlink()
            log_callback("[Bootstrap] FFmpeg instalado correctamente.")
        except Exception as e:
            log_callback(f"[Error] Falló la descarga de ffmpeg: {e}")
    else:
        log_callback("[Bootstrap] FFmpeg detectado y listo.")