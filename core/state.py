import os
import sys
import threading
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Set, Tuple, Optional, FrozenSet
from pathlib import Path

# Setup de Logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("State")

# 1. LA RUTA (Para archivos) — Resolución cross-platform.
#    Windows : %LOCALAPPDATA%\MusicGrabber
#    Linux   : $XDG_DATA_HOME/MusicGrabber       (default ~/.local/share/MusicGrabber)
#    macOS   : ~/Library/Application Support/MusicGrabber
def _resolve_app_data_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "MusicGrabber"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MusicGrabber"
    base = os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "MusicGrabber"

APP_DATA_DIR = _resolve_app_data_dir()
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP_DATA_DIR / "config.json"

@dataclass
class AppState:
    is_running: bool = True
    library_path: str = ""
    audio_quality: str = "192"        # kbps para MP3/OGG; ignorado en FLAC
    audio_format: str = "mp3"         # mp3 | flac | ogg
    active_downloads: Dict[str, Any] = field(default_factory=dict)
    recent_finishes: List[Tuple] = field(default_factory=list)
    session_status: str = "READY // SISTEMA EN ESPERA"
    session_errors: List[str] = field(default_factory=list)
    failed_vids: Set[str] = field(default_factory=set)
    current_task: Optional[Dict[str, str]] = None
    current_playlist_cache: List[Dict[str, Any]] = field(default_factory=list)
    cancel_requested: bool = False
    pending_queue_count: int = 0       # tareas en cola pendientes de empezar
    session_start_time: float = 0.0    # marca de inicio de la primera tarea (no se resetea)
    history_cache: Set[str] = field(default_factory=set)  # IDs ya descargados (cargados en memoria al inicio de cada tarea)
    global_stats: Dict[str, Any] = field(default_factory=lambda: {
        "success": 0, "skipped": 0, "failed": 0, "start_time": 0.0, "total_time": "0s"
    })
    lock: threading.Lock = field(default_factory=threading.Lock)

# 2. EL ESTADO (Para lógica)
state = AppState()

def load_config() -> None:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                state.library_path  = data.get("library_path", "")
                state.audio_quality = data.get("audio_quality", "192")
                state.audio_format  = data.get("audio_format", "mp3")
        except Exception as e:
            logger.error(f"Error al leer configuración: {e}")

def save_config(
    path: Optional[str] = None,
    quality: Optional[str] = None,
    fmt: Optional[str] = None,
) -> None:
    if path    is not None: state.library_path  = path
    if quality is not None: state.audio_quality = quality
    if fmt     is not None: state.audio_format  = fmt
    data = {
        "library_path":  state.library_path,
        "audio_quality": state.audio_quality,
        "audio_format":  state.audio_format,
    }
    tmp_file = CONFIG_FILE.with_suffix(".tmp")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        tmp_file.replace(CONFIG_FILE)
    except Exception as e:
        logger.error(f"Fallo al guardar configuración: {e}")
