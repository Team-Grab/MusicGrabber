import re
import os
import threading
import queue
import time
import json
import yt_dlp
import logging
from pathlib import Path
from core.state import state, APP_DATA_DIR
from yt_dlp.utils import sanitize_filename

# Rutas de archivos
BIN_DIR = APP_DATA_DIR / ".bin"
QUEUE_FILE = APP_DATA_DIR / ".queue.json"

download_queue = queue.Queue()
logger = logging.getLogger("Downloader")

def _save_queue_to_disk() -> None:
    """Guarda la cola pendiente Y la tarea activa en el disco de forma ATÓMICA."""
    with state.lock:
        pending_tasks = list(download_queue.queue)
        data = {
            "current": state.current_task,
            "pending": pending_tasks
        }
        
        try:
            tmp_file = QUEUE_FILE.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            tmp_file.replace(QUEUE_FILE)
        except Exception as e:
            logger.error(f"Error de E/S al escribir la cola de descargas: {e}")

def _rollback_last_download() -> None:
    """Elimina el último archivo registrado y sus entradas en el historial si hubo un corte abrupto."""
    import re
    if not state.library_path: return
    
    base_lib = Path(state.library_path)
    ledger_path = base_lib / "Library_Ledger.log"
    history_path = base_lib / ".historial_descargas.txt"

    if not ledger_path.exists(): return

    try:
        with open(ledger_path, "r", encoding='utf-8') as f:
            lines = f.readlines()

        if not lines: return

        last_line = lines[-1].strip()
        match = re.search(r'^youtube ([a-zA-Z0-9_-]{11}) "(.*)"', last_line)
        
        if match:
            vid, filepath = match.group(1), match.group(2)
            file_to_delete = Path(filepath)
            
            if file_to_delete.exists():
                try: 
                    file_to_delete.unlink()
                    logger.warning(f"Rollback físico: Archivo sospechoso eliminado -> {file_to_delete.name}")
                except Exception: pass
            
            with open(ledger_path, "w", encoding='utf-8') as f:
                f.writelines(lines[:-1])
                
            if history_path.exists():
                with open(history_path, "r", encoding='utf-8') as f:
                    hist_lines = f.readlines()
                with open(history_path, "w", encoding='utf-8') as f:
                    f.writelines([l for l in hist_lines if vid not in l])
                    
            logger.info("Rollback preventivo completado. Pizarra limpia para reanudar.")
    except Exception as e:
        logger.error(f"Fallo al ejecutar el rollback preventivo: {e}")

def has_pending_session() -> bool:
    if QUEUE_FILE.exists():
        try:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("current") or data.get("pending"):
                    return True
        except Exception: pass
    return False

def load_queue_from_disk(resume_requested: bool = True) -> None:
    if state.library_path:
        base_lib = Path(state.library_path)
        if base_lib.exists():
            for ext in ["*.part", "*.ytdl", "*.webm", "*.m4a"]:
                for temp_file in base_lib.rglob(ext):
                    try: temp_file.unlink()
                    except Exception: pass

    if not QUEUE_FILE.exists(): return

    try:
        if resume_requested:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            if data.get("current"):
                logger.warning("Cierre abrupto detectado. Ejecutando Rollback...")
                _rollback_last_download()
                download_queue.put(data["current"])
                
            for t in data.get("pending", []):
                download_queue.put(t)
                
            logger.info("Cola de sesión recuperada e inyectada.")
        else:
            logger.info("El usuario ha decidido descartar la sesión anterior.")
            
        QUEUE_FILE.unlink() 
        _save_queue_to_disk()
        
    except Exception as e:
        logger.error(f"Fallo al procesar la cola de recuperación: {e}")

class DaemonLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg):
        import re
        clean_msg = re.sub(r'\x1b\[[0-9;]*m', '', msg).strip()
        
        error_text = "Fallo desconocido"
        match = re.search(r'([a-zA-Z0-9_-]{11}): (.*)', clean_msg)
        
        if match:
            vid, raw_err = match.group(1), match.group(2)
            if "Requested format is not available" in raw_err: error_text = f"Formato no válido ({vid})"
            elif "Sign in to confirm" in raw_err: error_text = f"Restringido/Requiere cuenta ({vid})"
            elif "Video unavailable" in raw_err or "Private" in raw_err: error_text = f"Privado/Borrado ({vid})"
            else: error_text = f"Error de extracción ({vid}) - {raw_err[:30]}..."
            with state.lock: state.failed_vids.add(vid)
        else:
            if "Requested format is not available" in clean_msg: error_text = "Formato no válido"
            else: error_text = f"Error general: {clean_msg[:40]}..."

        with state.lock:
            state.session_errors.append(error_text)

def _clean_metadata(info, *args, **kwargs):
    if getattr(APP_DATA_DIR, 'cancel_requested', False):
        return "Protocolo Abortado por el Usuario"

    video_id = info.get('id')
    
    if info.get('_type') == 'playlist' or not video_id or not info.get('title'):
        return None 
    
    for key in ['artist', 'album_artist', 'uploader']:
        val = info.get(key)
        if isinstance(val, list) and val: val = str(val[0])
        if val and isinstance(val, str): info[key] = val.split(',')[0].strip()

    for key in ['playlist_title', 'title', 'album']:
        val = info.get(key)
        if val and isinstance(val, str) and val.startswith('Album - '):
            info[key] = val.replace('Album - ', '', 1)

    title = info.get('title', f"Track ({video_id})")

    with state.lock:
        already_in_cache = any(e.get('id') == video_id for e in state.current_playlist_cache)
        if not already_in_cache:
            state.current_playlist_cache.append(info.copy())

    if video_id:
        base_lib = Path(state.library_path)
        history_path = base_lib / ".historial_descargas.txt"
        if history_path.exists():
            try:
                content = history_path.read_text(encoding='utf-8')
                if f"youtube {video_id}" in content:
                    with state.lock:
                        if not already_in_cache:
                            state.global_stats["skipped"] += 1
                            state.recent_finishes.append(("SKIPPED", title))
                    return "El archivo ya está en el historial"
            except Exception: pass
            
    return None

def _progress_hook(d):
    if getattr(APP_DATA_DIR, 'cancel_requested', False):
        raise Exception("Protocolo Abortado por el Usuario")
    
    info = d.get('info_dict', {})
    video_id = info.get('id', 'desconocido')
    track_title = info.get('title') or d.get('title') or "Track desconocido"
    track_title = track_title.replace('\x1b[0;94m', '').replace('\x1b[0m', '')

    if d['status'] == 'downloading':
        percent_str = d.get('_percent_str', '0%').replace('\x1b[0;94m', '').replace('\x1b[0m', '').strip()
        try: percent = float(percent_str.replace('%', ''))
        except ValueError: percent = 0.0

        with state.lock:
            if video_id not in state.active_downloads:
                state.active_downloads[video_id] = {"title": track_title}
            state.active_downloads[video_id]["progress"] = percent
            state.active_downloads[video_id]["status"] = "Descargando..."

    elif d['status'] == 'finished':
        filepath = d.get('filename')
        base_lib = Path(state.library_path)
        
        if filepath:
            final_mp3 = Path(filepath).with_suffix('.mp3')
            
            ledger_path = base_lib / "Library_Ledger.log"
            line = f"youtube {video_id} \"{final_mp3}\"\n"
            try:
                content = ledger_path.read_text(encoding='utf-8') if ledger_path.exists() else ""
                if video_id not in content:
                    with open(ledger_path, "a", encoding='utf-8') as f: f.write(line)
            except Exception: pass

            history_path = base_lib / ".historial_descargas.txt"
            try:
                with open(history_path, "a", encoding='utf-8') as f: 
                    f.write(f"youtube {video_id}\n")
            except Exception: pass

        with state.lock:
            if video_id in state.active_downloads:
                state.active_downloads[video_id]["progress"] = 95.0
                state.active_downloads[video_id]["status"] = "Extrayendo Audio..."

def _postprocessor_hook(d: dict) -> None:
    info = d.get('info_dict', {})
    video_id = info.get('id')
    if not video_id: return

    with state.lock:
        if video_id in state.active_downloads:
            if d['status'] == 'started':
                pp_name = d.get('postprocessor', 'Procesador')
                state.active_downloads[video_id]["status"] = f"Cocinando: {pp_name}..."
                state.active_downloads[video_id]["progress"] = 99.0 
                
            elif d['status'] == 'finished':
                pp_name = d.get('postprocessor', '')
                if pp_name in ['Metadata', 'FFmpegMetadata', 'MoveFiles']:
                    
                    state.active_downloads[video_id]["progress"] = 100.0
                    state.active_downloads[video_id]["status"] = "¡Completado!"
                    
                    if not state.active_downloads[video_id].get("notified"):
                        state.active_downloads[video_id]["notified"] = True
                        
                        track_title = state.active_downloads[video_id].get("title", "Track desconocido")
                        original_url = info.get('webpage_url', '')
                        
                        state.recent_finishes.append((track_title, original_url))
                        state.global_stats["success"] += 1

def generate_m3u8(playlist_title: str, mode: str, library_path: str):
    if mode not in ["3", "4"]: return
    if not state.current_playlist_cache: return
    
    p_name = sanitize_filename(playlist_title.replace('Album - ', '', 1), restricted=False)
    playlists_dir = Path(library_path) / "_Playlist"
    playlists_dir.mkdir(parents=True, exist_ok=True)
    m3u_file = playlists_dir / f"{p_name}.m3u8"
    
    ledger_db = {}
    ledger_path = Path(library_path) / "Library_Ledger.log"
    if ledger_path.exists():
        content = ledger_path.read_text(encoding='utf-8')
        for match in re.finditer(r'^youtube ([a-zA-Z0-9_-]{11}) "(.*)"', content, re.MULTILINE):
            ledger_db[match.group(1)] = Path(match.group(2))
    
    try:
        with open(m3u_file, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            for i, entry in enumerate(state.current_playlist_cache):
                
                video_id = entry.get('id')
                raw_artist = str(entry.get('artist') or entry.get('uploader') or 'NA').split(',')[0].strip()
                raw_title = entry.get('title', 'Track')
                duration = int(entry.get('duration', 0))
                
                f.write(f"#EXTINF:{duration},{raw_artist} - {raw_title}\n")
                
                if video_id in ledger_db:
                    abs_path = ledger_db[video_id]
                    try:
                        rel_path = os.path.relpath(abs_path, playlists_dir)
                        rel_path_str = Path(rel_path).as_posix()
                    except ValueError:
                        rel_path_str = abs_path.as_posix()
                else:
                    artist = sanitize_filename(raw_artist, restricted=False)
                    title = sanitize_filename(raw_title, restricted=False)
                    raw_album = (entry.get('album') or 'Singles').replace('Album - ', '', 1)
                    raw_year = str(entry.get('release_year') or entry.get('upload_date', '0000'))[:4]
                    
                    if mode == "3":   rel_path = Path("..") / artist / f"{raw_year} - {sanitize_filename(raw_album, restricted=False)}" / f"{title}.mp3"
                    elif mode == "4": rel_path = Path("..") / "_Mix" / p_name / f"{title}.mp3"
                    else:             rel_path = Path("..") / artist / p_name / f"{title}.mp3"
                    
                    rel_path_str = rel_path.as_posix()
                    
                f.write(f"{rel_path_str}\n")
        
        with state.lock:
            state.recent_finishes.append(("M3U8", p_name))
    except Exception as e:
        logger.error(f"Fallo al generar M3U8 para {p_name}: {e}")

def _get_ydl_opts(mode: str, speed: str) -> dict:
    base_lib = Path(state.library_path)
    base = str(base_lib) + "/"
    
    # Índice solo presente para MODO 1, 2 y 5.
    idx_fmt = "%(playlist_index&{} - |)s"

    templates = {
        "1": base + "%(artist)s/%(release_year,upload_date.0:4)s - %(playlist_title,Album)s/" + idx_fmt + "%(title)s.%(ext)s",
        "2": base + "Varios Artistas/%(playlist_title,Recopilatorio)s/" + idx_fmt + "%(artist)s - %(title)s.%(ext)s",
        "3": base + "%(artist)s/%(release_year,upload_date.0:4)s - %(album,Singles)s/%(title)s.%(ext)s",
        "4": base + "_Mix/%(playlist_title,Mix Pleno)s/%(title)s.%(ext)s",
        "5": base + "%(uploader,Artista)s/%(playlist_title,Album)s/" + idx_fmt + "%(title)s.%(ext)s",
        "6": base + "_Huérfanos/%(title)s.%(ext)s"
    }

    opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'nooverwrites': True,
        'extract_flat': False,
        'allow_playlist_files': False, 
        'outtmpl': templates.get(mode, templates["6"]),
        'ffmpeg_location': str((BIN_DIR / "ffmpeg.exe").absolute()),
        'match_filter': _clean_metadata,
        'logger': DaemonLogger(),
        'progress_hooks': [_progress_hook],
        'postprocessor_hooks': [_postprocessor_hook],
    }

    if speed == "2": opts.update({'sleep_interval_requests': 1, 'sleep_interval': 2, 'max_sleep_interval': 5})
    elif speed == "3": opts.update({'sleep_interval_requests': 2, 'sleep_interval': 5, 'max_sleep_interval': 10})

    opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
    
    if mode != "6":
        opts['writethumbnail'] = True
        opts['postprocessors'] += [
            {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'},
            {'key': 'EmbedThumbnail'},
            {'key': 'FFmpegMetadata', 'add_metadata': True},
        ]
        opts['postprocessor_args'] = {'thumbnailsconvertor': ['-vf', 'crop=ih:ih'], 'ffmpeg': ['-id3v2_version', '3']}

    return opts

def _worker_loop():
    while state.is_running:
        try: 
            task = download_queue.get(timeout=1.0)
            with state.lock:
                state.current_task = task
        except queue.Empty: continue

        _save_queue_to_disk()
            
        url, mode, speed = task.get("url"), task.get("mode"), task.get("speed")
        
        try:
            with state.lock:
                state.cancel_requested = False 
                state.session_status = "SCANNING // ANALIZANDO ENLACE..."
                state.session_errors.clear()
                state.current_playlist_cache.clear() 

                state.global_stats["start_time"] = time.time()

            with yt_dlp.YoutubeDL(_get_ydl_opts(mode, speed)) as ydl:
                info = ydl.extract_info(url, download=False)
                parent_title = info.get('title', 'Enlace')

                with state.lock:
                    if not state.cancel_requested:
                        state.session_status = "LINKED // DESCARGANDO LOTE..."
                        webpage_url = info.get('webpage_url', url)
                        state.recent_finishes.append(("PARENT_LINK", parent_title, webpage_url))

                if not state.cancel_requested:
                    ydl.download([url])

                if not state.cancel_requested:
                    generate_m3u8(parent_title, mode, state.library_path)

            # FIN EXITOSO
            with state.lock:
                state.session_status = "ABORTED // DETENIDO" if state.cancel_requested else "COMPLETED // FINALIZADO"
                state.global_stats["total_time"] = f"{time.time() - state.global_stats['start_time']:.1f}s"
                state.global_stats["start_time"] = 0.0
                state.current_task = None

            _save_queue_to_disk()

        except Exception as e:
            # FALLO CRÍTICO
            with state.lock:
                state.current_task = None
                state.session_status = "ABORTED // DETENIDO" if state.cancel_requested else "ERROR // FALLIDO"
                if not state.cancel_requested: 
                    state.global_stats["failed"] += 1
                    state.session_errors.append(f"Fallo del sistema: {str(e)[:50]}...")
            _save_queue_to_disk()
            
        finally:
            time.sleep(3)
            with state.lock:
                if state.cancel_requested:
                    while not download_queue.empty():
                        try: download_queue.get_nowait(); download_queue.task_done()
                        except: break
                
                try:
                    base_lib = Path(state.library_path)
                    for ext in ["*.part", "*.ytdl", "*.webp", "*.vtt", "*.srt"]:
                        for temp_file in base_lib.rglob(ext):
                            try: temp_file.unlink()
                            except Exception: pass
                except Exception: pass
                
                for k in list(state.active_downloads.keys()): state.active_downloads.pop(k, None)
                if not state.active_downloads: state.session_status = "READY // SISTEMA EN ESPERA"
            download_queue.task_done()

def start_download_worker():
    threading.Thread(target=_worker_loop, daemon=True).start()

def add_download(url: str, mode: str, speed: str):
    download_queue.put({"url": url, "mode": mode, "speed": speed})
    _save_queue_to_disk()