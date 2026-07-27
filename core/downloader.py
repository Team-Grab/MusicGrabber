"""
Motor de descargas v2.0.

Cada URL pasa por:
  1. yt-dlp descarga el audio a APP_DATA_DIR/_inbox/.
  2. ffmpeg extrae al formato configurado (mp3 / flac / ogg).
  3. pipeline.process_new_download() aplica tags, consulta MusicBrainz y
     mueve el archivo al árbol canónico de la biblioteca.

Los 6 modos de orquestación del v1.x (Álbum, Recopilatorio, Playlist, Mix,
Discografía, Huérfano) desaparecen. MusicBrainz es ahora el único organizador.

API pública:
  start_download_worker()
  add_download(url, speed, mode=None)
  load_queue_from_disk(resume_requested=True)
  has_pending_session() -> bool
"""

import os
import re
import sys
import threading
import queue
import time
import json
import tempfile
import datetime
import subprocess
import logging
from pathlib import Path
from typing import Optional

import yt_dlp

from core.state import state, APP_DATA_DIR, log_event
from core.bootstrap import get_ffmpeg_path
from core import pipeline, library

logger = logging.getLogger("Downloader")

QUEUE_FILE = APP_DATA_DIR / ".queue.json"
INBOX_DIR  = APP_DATA_DIR / "_inbox"

download_queue: queue.Queue = queue.Queue()

# Lock para escrituras atómicas de la cola persistente.
# Sin esto, el thread principal (add_download) y el worker pueden
# pisarse mutuamente al escribir el .tmp y hacer replace.
_queue_io_lock = threading.Lock()

# Contador de intentos por sesión, para detectar fallos silenciosos de yt-dlp
# (vídeos que no llegan al postprocessor y no pasan por DaemonLogger.error).
_attempted_in_batch: set[str] = set()
_processed_in_batch: set[str] = set()


# ---------------------------------------------------------------------------
# Notificación de escritorio
# ---------------------------------------------------------------------------

def _send_notification(title: str, message: str) -> None:
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(
                ["notify-send", "-a", "Music Grabber", title, message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif sys.platform.startswith("win"):
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$n=[System.Windows.Forms.NotifyIcon]::new();"
                "$n.Icon=[System.Drawing.SystemIcons]::Information;"
                "$n.Visible=$true;"
                f'$n.ShowBalloonTip(5000,"{title}","{message}",'
                "[System.Windows.Forms.ToolTipIcon]::Info);"
                "Start-Sleep 6;$n.Dispose()"
            )
            subprocess.Popen(
                ["powershell", "-WindowStyle", "Hidden", "-Command", ps],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Log de fallos
# ---------------------------------------------------------------------------

def _log_failure(vid: str, error_text: str) -> None:
    if not state.library_path:
        return
    try:
        fail_log = Path(state.library_path) / "Failures_Log.txt"
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        url = f"https://music.youtube.com/watch?v={vid}" if len(vid) == 11 else "URL desconocida"
        line = f"[{timestamp}] {vid} | {url} | {error_text}\n"
        with open(fail_log, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Persistencia de cola
# ---------------------------------------------------------------------------

def _save_queue_to_disk() -> None:
    with state.lock:
        pending = list(download_queue.queue)
        data = {"current": state.current_task, "pending": pending}

    # Lock dedicado a la I/O: dos threads no pueden escribir/renombrar a la vez.
    # tempfile.mkstemp garantiza un nombre único; os.replace es atómico.
    with _queue_io_lock:
        try:
            APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=".queue-", suffix=".tmp", dir=str(APP_DATA_DIR)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
                os.replace(tmp_path, QUEUE_FILE)
            except Exception:
                # Si falla algo, limpiar el tmp huérfano
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.error(f"Error al escribir la cola de descargas: {e}")


def has_pending_session() -> bool:
    if not QUEUE_FILE.exists():
        return False
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("current") or data.get("pending"))
    except Exception:
        return False


def load_queue_from_disk(resume_requested: bool = True) -> None:
    # Limpiar _inbox: cualquier archivo aquí es de procesos anteriores
    # cortados a mitad. La biblioteca canónica no se toca.
    _clean_inbox()

    if not QUEUE_FILE.exists():
        return

    try:
        if resume_requested:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("current"):
                logger.warning("Cierre abrupto detectado. Reencolando tarea activa.")
                download_queue.put(data["current"])
            for t in data.get("pending", []):
                download_queue.put(t)
            logger.info("Cola de sesión recuperada.")
        else:
            logger.info("El usuario descartó la sesión anterior.")

        QUEUE_FILE.unlink()
        _save_queue_to_disk()
    except Exception as e:
        logger.error(f"Fallo al procesar la cola de recuperación: {e}")


def _clean_inbox() -> None:
    if INBOX_DIR.exists():
        for f in INBOX_DIR.iterdir():
            try:
                if f.is_file():
                    f.unlink()
            except Exception:
                pass
    else:
        INBOX_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Hooks de yt-dlp
# ---------------------------------------------------------------------------

_SKIP_KEYWORDS = (
    "video unavailable", "private", "sign in", "members-only", "members only",
    "this video is not available", "removed by the uploader", "geo restricted",
    "geo-restricted", "this video is restricted", "video is age restricted",
    "premieres in", "is not available in your country",
)


class DaemonLogger:
    """Recoge errores y warnings de yt-dlp y los registra en el event_log."""

    def debug(self, msg):   pass
    def info(self, msg):    pass

    def warning(self, msg):
        # yt-dlp envía warnings importantes aquí: "Video unavailable",
        # "This video is private", "Sign in to confirm your age", etc.
        clean = re.sub(r'\x1b\[[0-9;]*m', '', msg).strip()
        lower = clean.lower()
        if any(kw in lower for kw in _SKIP_KEYWORDS):
            vid = self._extract_id(clean)
            label = f" ({vid})" if vid else ""
            log_event("warn", f"Saltado por yt-dlp{label}: {clean[:120]}")
            with state.lock:
                state.global_stats["failed"] += 1
                state.session_errors.append(clean[:120])
                if vid:
                    state.failed_vids.add(vid)
            if vid:
                _log_failure(vid, clean[:200])

    def error(self, msg):
        clean = re.sub(r'\x1b\[[0-9;]*m', '', msg).strip()
        vid = self._extract_id(clean)
        if vid:
            if "Requested format is not available" in clean:
                error_text = f"Formato no válido ({vid})"
            elif "Sign in to confirm" in clean:
                error_text = f"Restringido/Requiere cuenta ({vid})"
            elif "Video unavailable" in clean or "Private" in clean:
                error_text = f"Privado/Borrado ({vid})"
            else:
                error_text = f"Error de extracción ({vid}) - {clean[:60]}"
            with state.lock:
                state.failed_vids.add(vid)
        else:
            error_text = f"Error general: {clean[:80]}"

        log_event("err", error_text)
        with state.lock:
            state.session_errors.append(error_text)
            state.global_stats["failed"] += 1
        if vid:
            _log_failure(vid, error_text)

    @staticmethod
    def _extract_id(text: str) -> Optional[str]:
        m = re.search(r'([a-zA-Z0-9_-]{11})', text)
        return m.group(1) if m else None


def _progress_hook(d):
    if state.cancel_requested:
        raise Exception("Protocolo Abortado por el Usuario")

    info        = d.get("info_dict", {})
    video_id    = info.get("id", "desconocido")
    track_title = info.get("title") or d.get("title") or "Track desconocido"
    track_title = track_title.replace("\x1b[0;94m", "").replace("\x1b[0m", "")

    if d["status"] == "downloading":
        percent_str = (
            d.get("_percent_str", "0%")
            .replace("\x1b[0;94m", "").replace("\x1b[0m", "").strip()
        )
        try:
            percent = float(percent_str.replace("%", ""))
        except ValueError:
            percent = 0.0
        with state.lock:
            if video_id not in state.active_downloads:
                state.active_downloads[video_id] = {"title": track_title}
                log_event("info", f"Descargando: {track_title}")
            state.active_downloads[video_id]["progress"]      = percent
            state.active_downloads[video_id]["status"]        = "Descargando..."
            state.active_downloads[video_id]["last_progress"] = time.time()

    elif d["status"] == "finished":
        log_event("info", f"Extrayendo audio: {track_title}")
        with state.lock:
            if video_id in state.active_downloads:
                state.active_downloads[video_id]["progress"] = 90.0
                state.active_downloads[video_id]["status"]   = "Extrayendo audio..."


def _postprocessor_hook(d: dict) -> None:
    """Cuando ffmpeg termina la extracción de audio, se apila el path para
    que el pipeline lo procese al final del download()."""
    info     = d.get("info_dict", {})
    video_id = info.get("id")
    if not video_id:
        return

    pp_name = d.get("postprocessor", "")
    status  = d.get("status", "")

    if status == "started":
        with state.lock:
            if video_id in state.active_downloads:
                state.active_downloads[video_id]["status"] = f"Procesando: {pp_name}"

    elif status == "finished" and pp_name in ("FFmpegExtractAudio", "MoveFiles"):
        filepath = info.get("filepath") or d.get("info_dict", {}).get("filepath")
        if not filepath:
            return
        _processed_in_batch.add(video_id)
        with state.lock:
            if video_id not in state.active_downloads:
                state.active_downloads[video_id] = {}
            state.active_downloads[video_id]["pending_filepath"] = filepath
            state.active_downloads[video_id]["progress"] = 95.0
            state.active_downloads[video_id]["status"]   = "Audio extraído"

        # Procesar el archivo en cuanto está listo, sin esperar al resto del lote.
        # Si MB está apagado es casi instantáneo; si está activo, espera el rate
        # limit de MB pero la siguiente pista de yt-dlp arranca en paralelo.
        threading.Thread(
            target=_process_one_track,
            args=(video_id, filepath),
            daemon=True,
        ).start()


def _process_one_track(video_id: str, filepath: str) -> None:
    """Procesa un solo archivo recién extraído por ffmpeg con el pipeline."""
    path = Path(filepath)
    with state.lock:
        data = dict(state.active_downloads.get(video_id, {}))
        if data.get("processed"):
            return

    base_meta = {
        "title":    data.get("yt_title")    or path.stem,
        "artist":   data.get("yt_artist")   or "",
        "album":    data.get("yt_album")    or "",
        "duration": data.get("yt_duration"),
        "isrc":     data.get("yt_isrc"),
    }
    try:
        final_path, applied, status_text = pipeline.process_new_download(path, base_meta)
    except Exception as e:
        logger.error(f"Pipeline falló para {path.name}: {e}")
        log_event("err", f"Pipeline falló: {path.name} — {e}")
        return

    with state.lock:
        if video_id in state.active_downloads:
            state.active_downloads[video_id]["processed"] = True
            state.active_downloads[video_id]["progress"]  = 100.0
            state.active_downloads[video_id]["status"]    = (
                "¡Completado!" if applied or "OK" in status_text else status_text
            )
        state.global_stats["success"] += 1
        title_short = base_meta["title"][:60]
        if applied:
            state.recent_finishes.append((title_short, str(final_path)))
        elif "OK" in status_text:
            state.recent_finishes.append((title_short, str(final_path)))
        else:
            state.recent_finishes.append(("REVIEW", title_short))


# ---------------------------------------------------------------------------
# Opciones yt-dlp
# ---------------------------------------------------------------------------

def _get_ydl_opts(speed: str) -> dict:
    ext = state.audio_format
    outtmpl = str(INBOX_DIR / "%(id)s - %(title).80s.%(ext)s")

    opts: dict = {
        "format":              "bestaudio/best",
        "quiet":               True,
        "no_warnings":         True,
        "ignoreerrors":        True,
        "nooverwrites":        True,
        "extract_flat":        False,
        "outtmpl":             outtmpl,
        "ffmpeg_location":     get_ffmpeg_path(),
        "logger":              DaemonLogger(),
        "progress_hooks":      [_progress_hook],
        "postprocessor_hooks": [_postprocessor_hook],
        "match_filter":        _attach_meta_filter,
        "socket_timeout":      30,
    }

    if speed == "2":
        opts.update({"sleep_interval_requests": 1, "sleep_interval": 2, "max_sleep_interval": 5})
    elif speed == "3":
        opts.update({"sleep_interval_requests": 2, "sleep_interval": 5, "max_sleep_interval": 10})

    if ext == "flac":
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "flac"}]
    elif ext == "ogg":
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "vorbis",
                                    "preferredquality": state.audio_quality}]
    else:
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                                    "preferredquality": state.audio_quality}]

    return opts


# ---------------------------------------------------------------------------
# Bucle de trabajo
# ---------------------------------------------------------------------------

def _worker_loop():
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    library.init_db()

    while state.is_running:
        try:
            task = download_queue.get(timeout=1.0)
            with state.lock:
                state.current_task        = task
                state.pending_queue_count = download_queue.qsize()
        except queue.Empty:
            continue

        _save_queue_to_disk()

        url   = task.get("url")
        speed = task.get("speed", "2")

        try:
            with state.lock:
                state.cancel_requested = False
                state.session_status   = "SCANNING // ANALIZANDO ENLACE..."
                if state.session_start_time == 0.0:
                    state.session_start_time = time.time()
                state.global_stats["start_time"] = time.time()
                state.active_downloads.clear()

            _attempted_in_batch.clear()
            _processed_in_batch.clear()
            # Snapshot de stats al inicio del lote para poder calcular diferencias
            with state.lock:
                stats_at_start = dict(state.global_stats)
                stats_at_start.setdefault("duplicates", 0)
            batch_start_ts = time.time()
            batch_failures_log: list[str] = []

            log_event("info", f"Analizando enlace: {url[:80]}")

            with yt_dlp.YoutubeDL(_get_ydl_opts(speed)) as ydl:
                # process=False evita visitar cada entrada antes de descargar.
                try:
                    head = ydl.extract_info(url, download=False, process=False)
                except Exception as e:
                    head = None
                    log_event("warn", f"No se pudo leer cabecera: {e}")
                parent_title = (head.get("title") if head else None) or "Enlace"
                webpage_url  = (head.get("webpage_url") if head else None) or url

                with state.lock:
                    if not state.cancel_requested:
                        state.session_status = "LINKED // DESCARGANDO..."
                        state.recent_finishes.append(("PARENT_LINK", parent_title, webpage_url))

                log_event("info", f"Lista detectada: {parent_title}")

                if not state.cancel_requested:
                    ydl.download([url])

            # Esperar a que las pistas individuales (procesadas en threads)
            # terminen el pipeline antes de cerrar el lote.
            if not state.cancel_requested:
                _wait_for_pipeline_completion()
                _count_silent_failures()

            with state.lock:
                elapsed = time.time() - state.global_stats["start_time"]
                state.session_status = (
                    "ABORTED // DETENIDO" if state.cancel_requested else "COMPLETED // FINALIZADO"
                )
                state.global_stats["total_time"] = f"{elapsed:.1f}s"
                state.global_stats["start_time"] = 0.0
                state.pending_queue_count        = download_queue.qsize()
                state.current_task               = None

            if not state.cancel_requested:
                _emit_batch_summary(stats_at_start, batch_start_ts)
                s = state.global_stats
                _send_notification(
                    "Music Grabber — Lote completado",
                    f"✅ {s['success']}  ❌ {s['failed']}",
                )

            _save_queue_to_disk()

        except Exception as e:
            logger.error(f"Fallo en worker: {e}", exc_info=True)
            with state.lock:
                state.current_task   = None
                state.session_status = (
                    "ABORTED // DETENIDO" if state.cancel_requested else "ERROR // FALLIDO"
                )
                if not state.cancel_requested:
                    state.global_stats["failed"] += 1
                    state.session_errors.append(f"Fallo del sistema: {str(e)[:50]}...")
                state.pending_queue_count = download_queue.qsize()
            _save_queue_to_disk()

        finally:
            time.sleep(2)
            with state.lock:
                if state.cancel_requested:
                    while not download_queue.empty():
                        try:
                            download_queue.get_nowait()
                            download_queue.task_done()
                        except Exception:
                            break

                state.active_downloads.clear()
                state.session_status = "READY // SISTEMA EN ESPERA"

                # Si la cola se vacía, parar el reloj acumulado de sesión.
                # Vuelve a arrancar en la próxima tarea (state.session_start_time == 0.0).
                if download_queue.empty():
                    state.session_start_time = 0.0

            _clean_inbox()
            download_queue.task_done()


def _attach_meta_filter(info, *args, **kwargs):
    """match_filter de yt-dlp: registra metadatos en state para que el
    pipeline los tenga disponibles. Siempre devuelve None (no filtra).
    También cuenta la pista como 'intentada' para detectar fallos silenciosos."""
    if state.cancel_requested:
        return "Protocolo Abortado por el Usuario"

    vid = info.get("id")
    if not vid:
        return None
    if info.get("_type") == "playlist":
        return None

    _attempted_in_batch.add(vid)

    # yt-dlp puede dar 'artist' como str o lista
    raw_artist = info.get("artist") or info.get("uploader") or ""
    if isinstance(raw_artist, list) and raw_artist:
        raw_artist = raw_artist[0]
    raw_artist = str(raw_artist).split(",")[0].strip()

    title = info.get("title", "")
    album = info.get("album", "") or ""
    if album.startswith("Album - "):
        album = album[len("Album - "):]

    with state.lock:
        if vid not in state.active_downloads:
            state.active_downloads[vid] = {"title": title}
        state.active_downloads[vid]["yt_title"]    = title
        state.active_downloads[vid]["yt_artist"]   = raw_artist
        state.active_downloads[vid]["yt_album"]    = album
        state.active_downloads[vid]["yt_duration"] = info.get("duration")
        state.active_downloads[vid]["yt_isrc"]     = info.get("isrc")

    return None


def _wait_for_pipeline_completion(timeout_s: int = 120) -> None:
    """Espera a que todas las pistas en active_downloads terminen el pipeline.
    Necesario porque _process_one_track corre en threads independientes."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with state.lock:
            pending = [
                vid for vid, d in state.active_downloads.items()
                if d.get("pending_filepath") and not d.get("processed")
            ]
        if not pending:
            return
        time.sleep(0.5)
    log_event("warn", f"Timeout esperando pipeline de {len(pending)} pista(s)")


def _emit_batch_summary(stats_at_start: dict, batch_start_ts: float) -> None:
    """Emite al log_event un resumen del lote: tiempo, éxitos, fallos, duplicados,
    y la lista de errores ocurridos."""
    elapsed = time.time() - batch_start_ts
    mm, ss = divmod(int(elapsed), 60)
    hh, mm = divmod(mm, 60)
    time_str = f"{hh}h {mm:02d}m {ss:02d}s" if hh else (f"{mm}m {ss:02d}s" if mm else f"{ss}s")

    with state.lock:
        s = state.global_stats
        d_success    = s.get("success", 0) - stats_at_start.get("success", 0)
        d_failed     = s.get("failed",  0) - stats_at_start.get("failed",  0)
        d_duplicates = s.get("duplicates", 0) - stats_at_start.get("duplicates", 0)
        last_errors  = list(state.session_errors)[-d_failed:] if d_failed > 0 else []

    log_event("info", "─" * 50)
    log_event("info", "Resumen del lote")
    log_event("info", f"  Tiempo:     {time_str}")
    log_event("ok",   f"  Descargas:  {d_success}")
    if d_duplicates:
        log_event("warn", f"  Duplicados: {d_duplicates}  (guardados con ' (N)' añadido al nombre)")
    if d_failed:
        log_event("err", f"  Fallos:     {d_failed}")
        for err in last_errors[:8]:
            log_event("err", f"    · {err[:120]}")
        if len(last_errors) > 8:
            log_event("err", f"    · ... y {len(last_errors) - 8} más (ver Failures_Log.txt)")
    log_event("info", "─" * 50)


def _count_silent_failures() -> None:
    """yt-dlp con ignoreerrors=True puede saltarse vídeos (privados, geo-bloqueados,
    'sign in to confirm'...) sin pasar por DaemonLogger.error. Comparamos las
    pistas que entraron al match_filter con las que terminaron extracción para
    detectar esas pérdidas y contabilizarlas."""
    missed = _attempted_in_batch - _processed_in_batch
    if not missed:
        return
    with state.lock:
        state.global_stats["failed"] += len(missed)
        for vid in missed:
            state.session_errors.append(f"Saltado por yt-dlp: {vid}")
            _log_failure(vid, "Saltado por yt-dlp (probable restricción geográfica / privado / requiere sesión)")


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def start_download_worker():
    threading.Thread(target=_worker_loop, daemon=True).start()


def add_download(url: str, speed: str = "2", mode=None):
    """
    Encola una URL para descarga.
    `mode` se acepta por compatibilidad con código antiguo y se ignora;
    en v2.0 ya no hay modos manuales.
    """
    download_queue.put({"url": url, "speed": speed})
    with state.lock:
        state.pending_queue_count = download_queue.qsize()
    _save_queue_to_disk()
