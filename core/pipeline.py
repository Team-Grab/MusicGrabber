"""
Pipeline post-descarga: aplica metadatos canónicos y mueve a destino final.

Flujo:
  1. yt-dlp ha dejado un archivo en APP_DATA_DIR/_inbox/<algo>.<ext>
  2. mutagen aplica metadatos base (los que vinieron de yt-dlp).
  3. Se consulta MusicBrainz con título + artista (+ ISRC si lo hay).
  4. Si el match es claro (score >= SCORE_AUTO_THRESHOLD), se aplican tags
     canónicos, se incrusta la carátula y el archivo se mueve a
     {library_root}/{artist}/{year} — {album}/{track:02d}. {title}.{ext}
  5. Si el match es ambiguo o no hay, el archivo se mueve a
     {library_root}/_inbox_review/ y se añade a la bandeja pending_review.
  6. Se actualiza el índice SQLite.

API pública:
  process_new_download(audio_path, base_meta) -> tuple[Path, bool, str]
      Devuelve (ruta_final, applied_mb, status_text).

  enrich_existing_track(track_row) -> bool
      Procesa una pista ya en la biblioteca. Devuelve True si MB encontró
      match aplicable. NO mueve el archivo (solo escribe tags y actualiza
      el índice).
"""

import re
import shutil
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from mutagen import File as MutagenFile
from mutagen.id3 import (
    ID3, ID3NoHeaderError,
    TIT2, TPE1, TALB, TDRC, TRCK, TCON, TSRC, TXXX, APIC,
)
from mutagen.flac import FLAC, Picture
from mutagen.oggvorbis import OggVorbis

from core import musicbrainz as mb
from core import library
from core import bpm
from core.state import APP_DATA_DIR, state, log_event

logger = logging.getLogger("Pipeline")

INBOX_DIR = APP_DATA_DIR / "_inbox"

# Caracteres no permitidos en nombres de archivo (Windows + sentido común)
_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ---------------------------------------------------------------------------
# Punto de entrada para descargas nuevas
# ---------------------------------------------------------------------------

def process_new_download(audio_path: Path, base_meta: Dict[str, Any]) -> Tuple[Path, bool, str]:
    """
    Procesa un archivo recién bajado por yt-dlp.

    base_meta debe incluir al menos:
      title, artist, album (opcional), duration (s), isrc (opcional)

    Si state.musicbrainz_enabled es False (default): aplica los tags base de
    yt-dlp y mueve el archivo a {lib}/{artist}/{album}/{title}.{ext}. Sin red.
    Si está True: además consulta MusicBrainz y mueve a la estructura canónica
    cuando hay match claro; los match ambiguos van a la bandeja.

    Devuelve (ruta_final, applied_mb, status_text).
    """
    if not audio_path.exists():
        return audio_path, False, "archivo no encontrado"

    library_root = state.library_path
    if not library_root:
        return audio_path, False, "biblioteca no configurada"

    title  = base_meta.get("title")  or audio_path.stem
    artist = base_meta.get("artist") or "Desconocido"
    album  = base_meta.get("album")  or ""

    # 1) Tags base con mutagen — siempre
    _write_tags(audio_path, base_meta)
    log_event("info", f"Tags base aplicados: {title}")

    # 2) Si MB está desactivado, mover a layout simple usando metadatos yt-dlp
    if not state.musicbrainz_enabled:
        final = _move_to_simple_layout(audio_path, artist, album, title, library_root)
        track_bpm = _compute_bpm_safe(final)
        library.upsert_track(
            str(final),
            title=title, artist=artist, album=(album or None),
            duration_s=base_meta.get("duration"),
            isrc=base_meta.get("isrc"),
            bpm=track_bpm,
        )
        log_event("ok", f"Archivo en biblioteca: {Path(final).relative_to(library_root)}")
        return final, False, "OK (sin MB)"

    # 3) Consultar MusicBrainz
    log_event("mb", f"Buscando en MusicBrainz: {title} — {artist}")
    match = mb.search_recording(
        title      = title,
        artist     = artist,
        duration_s = base_meta.get("duration"),
        isrc       = base_meta.get("isrc"),
    )

    if match and match.is_auto:
        final = _apply_match_and_move(audio_path, match, library_root)
        track_bpm = _compute_bpm_safe(final)
        library.upsert_track(
            str(final),
            title=match.title, artist=match.artist, album=match.album,
            year=match.year, track_number=match.track_number, genre=match.genre,
            duration_s=base_meta.get("duration"),
            mb_recording_id=match.recording_id, mb_release_id=match.release_id,
            isrc=match.isrc,
            bpm=track_bpm,
        )
        log_event("ok", f"MB match (score {match.score}): {match.title} — {match.artist}")
        log_event("ok", f"Archivo en biblioteca: {Path(final).relative_to(library_root)}")
        return final, True, f"OK (MB score {match.score})"

    # Match ambiguo o sin match: mover a layout simple y registrar en bandeja
    final = _move_to_simple_layout(audio_path, artist, album, title, library_root)
    track_bpm = _compute_bpm_safe(final)
    library.upsert_track(
        str(final),
        title=title, artist=artist, album=(album or None),
        duration_s=base_meta.get("duration"),
        isrc=base_meta.get("isrc"),
        bpm=track_bpm,
    )
    candidates = match.candidates if (match and match.candidates) else (
        [{"title": match.title, "artist": match.artist, "album": match.album,
          "year": match.year, "recording_id": match.recording_id, "score": match.score}]
        if match else []
    )
    reason = "ambiguous" if (match and match.is_ambiguous) else "no_match"
    library.add_to_review(str(final), candidates, reason)
    log_event("warn",
              f"MB {'ambiguo' if reason == 'ambiguous' else 'sin match'}: "
              f"{title} — {artist} (a bandeja de revisión)")
    return final, False, f"a bandeja ({reason})"


def _compute_bpm_safe(path: Path) -> Optional[float]:
    """Calcula BPM con manejo defensivo: si el backend no está, devuelve None
    sin avisos ruidosos; si está y falla, lo loguea como warn pero no rompe
    el pipeline. La pista entra en la DB con bpm=NULL y se podrá calcular
    después con el botón 'Calcular BPM faltantes'."""
    if not bpm.is_available():
        return None
    log_event("info", f"Calculando BPM: {path.name}")
    try:
        val = bpm.compute_bpm(str(path))
        if val:
            log_event("ok", f"BPM: {val:.1f} ({path.name})")
        else:
            log_event("warn", f"BPM no detectado: {path.name}")
        return val
    except Exception as e:
        log_event("warn", f"BPM error en {path.name}: {e}")
        return None


def _move_to_simple_layout(
    audio_path: Path, artist: str, album: str, title: str, library_root: str
) -> Path:
    """Layout sin MB: {lib}/{artist}/{album|Singles}/{title}.{ext}"""
    safe_artist = _sanitize(artist)
    safe_album  = _sanitize(album) if album else "Singles"
    safe_title  = _sanitize(title)
    dest = Path(library_root) / safe_artist / safe_album / f"{safe_title}{audio_path.suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    return _move_avoiding_overwrite(audio_path, dest)


# ---------------------------------------------------------------------------
# Enriquecimiento de pistas existentes
# ---------------------------------------------------------------------------

def enrich_existing_track(track_row) -> bool:
    """
    Aplica MB a una pista que ya está en la biblioteca, sin moverla.
    Solo actualiza tags del archivo y el índice.
    """
    path = Path(track_row["path"])
    if not path.exists():
        library.delete_track(str(path))
        return False

    if track_row["mb_recording_id"]:
        # Ya tiene match; no se reprocesa salvo que el usuario lo fuerce.
        return False

    match = mb.search_recording(
        title      = track_row["title"]  or "",
        artist     = track_row["artist"] or "",
        duration_s = track_row["duration_s"],
        isrc       = track_row["isrc"],
    )
    if not match or not match.is_auto:
        # Si es ambiguo, añadir a bandeja para revisión manual
        if match and match.is_ambiguous:
            library.add_to_review(
                str(path),
                match.candidates or [],
                "ambiguous",
            )
        return False

    # Aplicar tags MB sin mover el archivo
    new_meta = {
        "title":           match.title,
        "artist":          match.artist,
        "album":           match.album,
        "year":            match.year,
        "track_number":    match.track_number,
        "genre":           match.genre,
        "isrc":            match.isrc,
        "mb_recording_id": match.recording_id,
        "mb_release_id":   match.release_id,
    }
    _write_tags(path, new_meta, embed_cover=True)
    library.upsert_track(
        str(path),
        title=match.title, artist=match.artist, album=match.album,
        year=match.year, track_number=match.track_number, genre=match.genre,
        mb_recording_id=match.recording_id, mb_release_id=match.release_id,
        isrc=match.isrc,
    )
    return True


# ---------------------------------------------------------------------------
# Aplicación de match + movimiento al destino canónico
# ---------------------------------------------------------------------------

def _apply_match_and_move(audio_path: Path, match: mb.MatchResult, library_root: str) -> Path:
    meta = {
        "title":           match.title,
        "artist":          match.artist,
        "album":           match.album,
        "year":            match.year,
        "track_number":    match.track_number,
        "genre":           match.genre,
        "isrc":            match.isrc,
        "mb_recording_id": match.recording_id,
        "mb_release_id":   match.release_id,
    }
    _write_tags(audio_path, meta, embed_cover=True)

    destination = _build_canonical_path(library_root, match, audio_path.suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    return _move_avoiding_overwrite(audio_path, destination)


def _build_canonical_path(library_root: str, match: mb.MatchResult, ext: str) -> Path:
    artist = _sanitize(match.artist or "Artista desconocido")
    album  = _sanitize(match.album  or "Sin álbum")
    title  = _sanitize(match.title  or "Sin título")

    # Sin año: el directorio se llama solo "Álbum" sin prefijo "—".
    # Evita la fea cadena "0000 — Album" cuando MB no tiene fecha.
    if match.year:
        album_dir = f"{match.year} — {album}"
    else:
        album_dir = album

    if match.track_number:
        filename = f"{match.track_number:02d}. {title}{ext}"
    else:
        filename = f"{title}{ext}"

    return Path(library_root) / artist / album_dir / filename


def _sanitize(name: str) -> str:
    name = _FORBIDDEN_CHARS.sub("_", name).strip().strip(".")
    return name or "_"


def _move_avoiding_overwrite(src: Path, dst: Path) -> Path:
    """Si dst existe, añade ' (N)' al nombre y registra el evento como duplicado.
    Devuelve la ruta final."""
    original_dst = dst
    if dst.exists():
        base = dst.stem
        ext  = dst.suffix
        i = 2
        while dst.exists():
            dst = dst.with_name(f"{base} ({i}){ext}")
            i += 1
        log_event("warn",
                  f"Duplicado detectado: ya existía '{original_dst.name}'. "
                  f"Guardado como '{dst.name}'.")
        state.global_stats.setdefault("duplicates", 0)
        state.global_stats["duplicates"] += 1
    shutil.move(str(src), str(dst))
    return dst


# ---------------------------------------------------------------------------
# Escritura de tags por formato
# ---------------------------------------------------------------------------

def _write_tags(path: Path, meta: Dict[str, Any], embed_cover: bool = False) -> None:
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            _write_tags_mp3(path, meta, embed_cover)
        elif ext == ".flac":
            _write_tags_flac(path, meta, embed_cover)
        elif ext in (".ogg", ".oga"):
            _write_tags_ogg(path, meta, embed_cover)
        else:
            # Para m4a/opus/wav usamos la API genérica de mutagen
            mf = MutagenFile(path, easy=True)
            if mf is not None:
                _apply_easy(mf, meta)
                mf.save()
    except Exception as e:
        logger.warning(f"No se pudieron escribir tags en {path.name}: {e}")


def _apply_easy(mf, meta: Dict[str, Any]) -> None:
    mapping = {
        "title":        "title",
        "artist":       "artist",
        "album":        "album",
        "year":         "date",
        "genre":        "genre",
        "track_number": "tracknumber",
        "isrc":         "isrc",
    }
    for k, v in meta.items():
        easy_key = mapping.get(k)
        if easy_key and v:
            try:
                mf[easy_key] = str(v)
            except Exception:
                pass


def _write_tags_mp3(path: Path, meta: Dict[str, Any], embed_cover: bool) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    if meta.get("title"):        tags["TIT2"] = TIT2(encoding=3, text=str(meta["title"]))
    if meta.get("artist"):       tags["TPE1"] = TPE1(encoding=3, text=str(meta["artist"]))
    if meta.get("album"):        tags["TALB"] = TALB(encoding=3, text=str(meta["album"]))
    if meta.get("year"):         tags["TDRC"] = TDRC(encoding=3, text=str(meta["year"]))
    if meta.get("genre"):        tags["TCON"] = TCON(encoding=3, text=str(meta["genre"]))
    if meta.get("track_number"): tags["TRCK"] = TRCK(encoding=3, text=str(meta["track_number"]))
    if meta.get("isrc"):         tags["TSRC"] = TSRC(encoding=3, text=str(meta["isrc"]))

    if meta.get("mb_recording_id"):
        tags.add(TXXX(encoding=3, desc="MusicBrainz Track Id",  text=str(meta["mb_recording_id"])))
    if meta.get("mb_release_id"):
        tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id",  text=str(meta["mb_release_id"])))

    if embed_cover and meta.get("mb_release_id"):
        cover = mb.fetch_cover_art(meta["mb_release_id"])
        if cover:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))

    tags.save(path)


def _write_tags_flac(path: Path, meta: Dict[str, Any], embed_cover: bool) -> None:
    audio = FLAC(path)

    keys = {
        "title": "title", "artist": "artist", "album": "album",
        "year": "date", "genre": "genre", "track_number": "tracknumber",
        "isrc": "isrc",
        "mb_recording_id": "musicbrainz_trackid",
        "mb_release_id":   "musicbrainz_albumid",
    }
    for k, v in meta.items():
        flac_key = keys.get(k)
        if flac_key and v:
            audio[flac_key] = str(v)

    if embed_cover and meta.get("mb_release_id"):
        cover = mb.fetch_cover_art(meta["mb_release_id"])
        if cover:
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.desc = "Cover"
            pic.data = cover
            audio.clear_pictures()
            audio.add_picture(pic)

    audio.save()


def _write_tags_ogg(path: Path, meta: Dict[str, Any], embed_cover: bool) -> None:
    audio = OggVorbis(path)
    keys = {
        "title": "title", "artist": "artist", "album": "album",
        "year": "date", "genre": "genre", "track_number": "tracknumber",
        "isrc": "isrc",
        "mb_recording_id": "musicbrainz_trackid",
        "mb_release_id":   "musicbrainz_albumid",
    }
    for k, v in meta.items():
        ogg_key = keys.get(k)
        if ogg_key and v:
            audio[ogg_key] = str(v)
    audio.save()
    # OGG con cover requiere base64 + metadata_block_picture; lo dejamos
    # fuera por ahora — los OGG son minoritarios en este flujo.
