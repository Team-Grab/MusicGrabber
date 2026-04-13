import os
import threading
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Set, Tuple, Optional
from pathlib import Path

# Setup de Logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("State")

# 1. LA RUTA (Para archivos)
APP_DATA_DIR = Path(os.getenv('LOCALAPPDATA', '.')) / "MusicGrabber"
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP_DATA_DIR / "config.json"

@dataclass
class AppState:
    is_running: bool = True
    library_path: str = ""
    active_downloads: Dict[str, Any] = field(default_factory=dict)
    recent_finishes: List[Tuple] = field(default_factory=list) 
    session_status: str = "READY // SISTEMA EN ESPERA"
    session_errors: List[str] = field(default_factory=list)
    failed_vids: Set[str] = field(default_factory=set)
    current_task: Optional[Dict[str, str]] = None 
    current_playlist_cache: List[Dict[str, Any]] = field(default_factory=list)
    cancel_requested: bool = False
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
                state.library_path = data.get("library_path", "")
        except Exception as e:
            logger.error(f"Error al leer configuración: {e}")

def save_config(path: Optional[str] = None) -> None:
    if path: state.library_path = path
    data = {"library_path": state.library_path}
    tmp_file = CONFIG_FILE.with_suffix(".tmp")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        tmp_file.replace(CONFIG_FILE)
    except Exception as e:
        logger.error(f"Fallo al guardar configuración: {e}")