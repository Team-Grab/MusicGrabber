import os
import sys
import time
import threading
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Any, List, Set, Tuple, Optional, FrozenSet, Deque
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
    # Si True, tras cada descarga se consulta MusicBrainz para mejorar tags.
    # Si False (default), las pistas se guardan con los metadatos de yt-dlp
    # y la mejora se hace bajo demanda con "Enriquecer con MusicBrainz".
    musicbrainz_enabled: bool = False
    # Persistencia de la GUI entre sesiones
    window_geometry: str = ""              # formato "WxH+X+Y"
    sort_column:    str  = "artist"
    sort_reverse:   bool = False
    last_view:      str  = "download"      # "download" | "library" | "review"
    eq_preset:      str  = "Flat"          # preset del ecualizador persistido
    crossfade_enabled: bool = False        # fundido encadenado entre pistas
    crossfade_seconds: int  = 4            # duración del fundido (1-12 s)
    party_min_bpm:     int  = 110          # extremo inferior del rango BPM (60-200)
    party_max_bpm:     int  = 140          # extremo superior del rango BPM (60-200)
    party_crossfade_s: int  = 6            # crossfade que aplica el modo fiesta
    active_downloads: Dict[str, Any] = field(default_factory=dict)
    recent_finishes: List[Tuple] = field(default_factory=list)
    # Log de eventos detallado para mostrar en la GUI (ts, level, msg).
    # Se trunca a 500 entradas para no consumir memoria sin control.
    event_log: Deque[Tuple[float, str, str]] = field(
        default_factory=lambda: deque(maxlen=500)
    )
    session_status: str = "READY // SISTEMA EN ESPERA"
    session_errors: List[str] = field(default_factory=list)
    failed_vids: Set[str] = field(default_factory=set)
    current_task: Optional[Dict[str, str]] = None
    current_playlist_cache: List[Dict[str, Any]] = field(default_factory=list)
    cancel_requested: bool = False
    pending_queue_count: int = 0       # tareas en cola pendientes de empezar
    session_start_time: float = 0.0    # marca de inicio de la primera tarea (no se resetea)
    history_cache: Set[str] = field(default_factory=set)
    global_stats: Dict[str, Any] = field(default_factory=lambda: {
        "success": 0, "skipped": 0, "failed": 0, "start_time": 0.0, "total_time": "0s"
    })
    lock: threading.Lock = field(default_factory=threading.Lock)


def log_event(level: str, msg: str) -> None:
    """
    Añade un evento al log visible en la GUI. Llamable desde cualquier
    thread; la deque es thread-safe para append.
    level: 'info' | 'ok' | 'warn' | 'err' | 'mb'
    """
    state.event_log.append((time.time(), level, msg))

# 2. EL ESTADO (Para lógica)
state = AppState()

def load_config() -> None:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                state.library_path        = data.get("library_path", "")
                state.audio_quality       = data.get("audio_quality", "192")
                state.audio_format        = data.get("audio_format", "mp3")
                state.musicbrainz_enabled = bool(data.get("musicbrainz_enabled", False))
                state.window_geometry     = data.get("window_geometry", "")
                state.sort_column         = data.get("sort_column", "artist")
                state.sort_reverse        = bool(data.get("sort_reverse", False))
                state.last_view           = data.get("last_view", "download")
                state.eq_preset           = data.get("eq_preset", "Flat")
                state.crossfade_enabled   = bool(data.get("crossfade_enabled", False))
                cf = int(data.get("crossfade_seconds", 4))
                state.crossfade_seconds   = max(1, min(12, cf))
                pb = int(data.get("party_min_bpm", 110))
                state.party_min_bpm       = max(60, min(200, pb))
                # Compatibilidad: configs previas a 2c.5 v2 no tenían max.
                # Default razonable: min+30, recortado a 200.
                pmax = int(data.get("party_max_bpm", state.party_min_bpm + 30))
                state.party_max_bpm       = max(state.party_min_bpm, min(200, pmax))
                pcf = int(data.get("party_crossfade_s", 6))
                state.party_crossfade_s   = max(1, min(12, pcf))
        except Exception as e:
            logger.error(f"Error al leer configuración: {e}")

def save_config(
    path: Optional[str] = None,
    quality: Optional[str] = None,
    fmt: Optional[str] = None,
    musicbrainz_enabled: Optional[bool] = None,
    window_geometry: Optional[str] = None,
    sort_column: Optional[str] = None,
    sort_reverse: Optional[bool] = None,
    last_view: Optional[str] = None,
    eq_preset: Optional[str] = None,
    crossfade_enabled: Optional[bool] = None,
    crossfade_seconds: Optional[int] = None,
    party_min_bpm: Optional[int] = None,
    party_max_bpm: Optional[int] = None,
    party_crossfade_s: Optional[int] = None,
) -> None:
    if path                is not None: state.library_path        = path
    if quality             is not None: state.audio_quality       = quality
    if fmt                 is not None: state.audio_format        = fmt
    if musicbrainz_enabled is not None: state.musicbrainz_enabled = bool(musicbrainz_enabled)
    if window_geometry     is not None: state.window_geometry     = window_geometry
    if sort_column         is not None: state.sort_column         = sort_column
    if sort_reverse        is not None: state.sort_reverse        = bool(sort_reverse)
    if last_view           is not None: state.last_view           = last_view
    if eq_preset           is not None: state.eq_preset           = eq_preset
    if crossfade_enabled   is not None: state.crossfade_enabled   = bool(crossfade_enabled)
    if crossfade_seconds   is not None: state.crossfade_seconds   = max(1, min(12, int(crossfade_seconds)))
    if party_min_bpm       is not None: state.party_min_bpm       = max(60, min(200, int(party_min_bpm)))
    if party_max_bpm       is not None: state.party_max_bpm       = max(60, min(200, int(party_max_bpm)))
    if party_crossfade_s   is not None: state.party_crossfade_s   = max(1, min(12, int(party_crossfade_s)))
    # Sanity: garantizar min <= max
    if state.party_max_bpm < state.party_min_bpm:
        state.party_max_bpm = state.party_min_bpm
    data = {
        "library_path":        state.library_path,
        "audio_quality":       state.audio_quality,
        "audio_format":        state.audio_format,
        "musicbrainz_enabled": state.musicbrainz_enabled,
        "window_geometry":     state.window_geometry,
        "sort_column":         state.sort_column,
        "sort_reverse":        state.sort_reverse,
        "last_view":           state.last_view,
        "eq_preset":           state.eq_preset,
        "crossfade_enabled":   state.crossfade_enabled,
        "crossfade_seconds":   state.crossfade_seconds,
        "party_min_bpm":       state.party_min_bpm,
        "party_max_bpm":       state.party_max_bpm,
        "party_crossfade_s":   state.party_crossfade_s,
    }
    tmp_file = CONFIG_FILE.with_suffix(".tmp")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        tmp_file.replace(CONFIG_FILE)
    except Exception as e:
        logger.error(f"Fallo al guardar configuración: {e}")
