"""
Índice persistente de la biblioteca local.

Base SQLite en APP_DATA_DIR/library.db. Una tabla `tracks` y una tabla
`pending_review` para matches ambiguos que el usuario debe resolver.

API pública:
  init_db()                                     → garantiza esquema
  upsert_track(...)                             → añadir o actualizar
  get_track_by_path(path)                       → fila o None
  get_track_by_recording_id(rec_id)             → fila o None
  all_tracks()                                  → iter de filas
  tracks_without_recording_id()                 → iter de filas
  add_to_review(path, candidates_json, reason)  → meter en bandeja
  pending_review()                              → iter de filas
  remove_from_review(path)                      → quitar de bandeja
  scan(library_root, on_progress=None)          → recorrer y poblar
  delete_track(path)                            → quitar del índice

Las filas se devuelven como sqlite3.Row (acceso por nombre).
"""

import os
import json
import sqlite3
import logging
from pathlib import Path
from typing import Iterable, Optional, Callable

from mutagen import File as MutagenFile

from core.state import APP_DATA_DIR

logger = logging.getLogger("Library")

DB_PATH = APP_DATA_DIR / "library.db"

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".oga", ".m4a", ".opus", ".wav"}


# ---------------------------------------------------------------------------
# Inicialización
# ---------------------------------------------------------------------------

_db_initialised = False


def _connect() -> sqlite3.Connection:
    # Auto-init lazy: cualquier operación sobre la DB la crea si no existe.
    # Evita tener que recordar llamar init_db() desde cada punto de entrada.
    if not _db_initialised:
        init_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Garantiza que las tablas existen. Idempotente. Aplica migraciones
    suaves sobre la tabla tracks (ALTER TABLE solo si la columna no existe)."""
    global _db_initialised
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                path             TEXT PRIMARY KEY,
                title            TEXT,
                artist           TEXT,
                album            TEXT,
                year             TEXT,
                track_number     INTEGER,
                genre            TEXT,
                duration_s       INTEGER,
                mb_recording_id  TEXT,
                mb_release_id    TEXT,
                isrc             TEXT,
                added_at         TEXT DEFAULT CURRENT_TIMESTAMP,
                last_played_at   TEXT,
                play_count       INTEGER DEFAULT 0,
                bpm              REAL
            )
        """)
        # Migración suave: añadir columna bpm a DBs preexistentes.
        cur.execute("PRAGMA table_info(tracks)")
        cols = {row[1] for row in cur.fetchall()}
        if "bpm" not in cols:
            cur.execute("ALTER TABLE tracks ADD COLUMN bpm REAL")
            logger.info("Migración DB: columna 'bpm' añadida")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_recording ON tracks(mb_recording_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artist    ON tracks(artist)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_album     ON tracks(album)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bpm       ON tracks(bpm)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_review (
                path        TEXT PRIMARY KEY,
                candidates  TEXT,    -- JSON con MatchResult.candidates
                reason      TEXT,    -- 'ambiguous' | 'no_match' | 'error'
                added_at    TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        _db_initialised = True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Operaciones sobre tracks
# ---------------------------------------------------------------------------

def upsert_track(path: str, **fields) -> None:
    """
    Insert o update por path. Solo los campos pasados se modifican.
    Campos válidos: title, artist, album, year, track_number, genre,
                    duration_s, mb_recording_id, mb_release_id, isrc.
    """
    valid = {
        "title", "artist", "album", "year", "track_number", "genre",
        "duration_s", "mb_recording_id", "mb_release_id", "isrc",
        "last_played_at", "play_count", "bpm",
    }
    cleaned = {k: v for k, v in fields.items() if k in valid}

    conn = _connect()
    try:
        cur = conn.cursor()
        # ¿Existe?
        cur.execute("SELECT path FROM tracks WHERE path = ?", (path,))
        existing = cur.fetchone()

        if existing:
            if cleaned:
                set_clause = ", ".join(f"{k} = ?" for k in cleaned.keys())
                values = list(cleaned.values()) + [path]
                cur.execute(f"UPDATE tracks SET {set_clause} WHERE path = ?", values)
        else:
            cols   = ["path"] + list(cleaned.keys())
            qmarks = ", ".join(["?"] * len(cols))
            values = [path] + list(cleaned.values())
            cur.execute(f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({qmarks})", values)

        conn.commit()
    finally:
        conn.close()


def get_track_by_path(path: str) -> Optional[sqlite3.Row]:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tracks WHERE path = ?", (path,))
        return cur.fetchone()
    finally:
        conn.close()


def get_track_by_recording_id(rec_id: str) -> Optional[sqlite3.Row]:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tracks WHERE mb_recording_id = ?", (rec_id,))
        return cur.fetchone()
    finally:
        conn.close()


def all_tracks() -> Iterable[sqlite3.Row]:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tracks ORDER BY artist, album, track_number, title")
        return list(cur.fetchall())
    finally:
        conn.close()


def tracks_without_recording_id() -> Iterable[sqlite3.Row]:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tracks WHERE mb_recording_id IS NULL OR mb_recording_id = ''")
        return list(cur.fetchall())
    finally:
        conn.close()


def tracks_in_bpm_range(min_bpm: float, max_bpm: float) -> Iterable[sqlite3.Row]:
    """Pistas con BPM entre [min_bpm, max_bpm] inclusive. Usado por modo fiesta."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tracks WHERE bpm IS NOT NULL AND bpm >= ? AND bpm <= ?",
            (float(min_bpm), float(max_bpm)),
        )
        return list(cur.fetchall())
    finally:
        conn.close()


def tracks_without_bpm() -> Iterable[sqlite3.Row]:
    """Pistas con BPM NULL. Usado por 'Calcular BPM faltantes'."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tracks WHERE bpm IS NULL ORDER BY artist, album, title")
        return list(cur.fetchall())
    finally:
        conn.close()


def delete_track(path: str) -> None:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM tracks WHERE path = ?", (path,))
        cur.execute("DELETE FROM pending_review WHERE path = ?", (path,))
        conn.commit()
    finally:
        conn.close()


def prune_missing(library_root: Optional[str] = None) -> int:
    """
    Elimina del índice las entradas cuyo archivo físico ya no existe.
    Guarda: si library_root se proporciona y no existe (unidad desmontada),
    no se toca nada para evitar borrar todo accidentalmente.
    Devuelve el número de entradas eliminadas.
    """
    if library_root and not Path(library_root).exists():
        logger.warning(f"Raíz no encontrada ({library_root}); prune cancelado")
        return 0

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT path FROM tracks")
        missing = [r["path"] for r in cur.fetchall() if not Path(r["path"]).exists()]
        for p in missing:
            cur.execute("DELETE FROM tracks WHERE path = ?", (p,))
            cur.execute("DELETE FROM pending_review WHERE path = ?", (p,))
        conn.commit()
    finally:
        conn.close()
    if missing:
        logger.info(f"Prune: {len(missing)} entrada(s) obsoleta(s) eliminadas")
    return len(missing)


def clear_index() -> int:
    """Vacía totalmente las tablas tracks y pending_review.
    Devuelve el número de entradas que había antes."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM tracks")
        n = cur.fetchone()["n"] or 0
        cur.execute("DELETE FROM tracks")
        cur.execute("DELETE FROM pending_review")
        conn.commit()
    finally:
        conn.close()
    logger.info(f"clear_index: {n} entrada(s) eliminadas")
    return n


# ---------------------------------------------------------------------------
# Bandeja de revisión
# ---------------------------------------------------------------------------

def add_to_review(path: str, candidates: list, reason: str) -> None:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO pending_review (path, candidates, reason) VALUES (?, ?, ?)",
            (path, json.dumps(candidates, ensure_ascii=False), reason),
        )
        conn.commit()
    finally:
        conn.close()


def pending_review() -> Iterable[sqlite3.Row]:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM pending_review ORDER BY added_at DESC")
        return list(cur.fetchall())
    finally:
        conn.close()


def remove_from_review(path: str) -> None:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM pending_review WHERE path = ?", (path,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Escaneo de biblioteca existente
# ---------------------------------------------------------------------------

def scan(library_root: str, on_progress: Optional[Callable[[int, int, str], None]] = None) -> int:
    """
    Recorre library_root y puebla el índice con los archivos de audio encontrados.
    No mueve ni modifica archivos. Sí lee los tags existentes con mutagen.

    Si un archivo ya tiene mb_recording_id en sus tags (de descargas previas),
    se respeta y se guarda en la DB.

    Devuelve el número total de pistas procesadas.

    on_progress(actual, total, path) se invoca por cada pista (si se proporciona).
    """
    if not library_root or not Path(library_root).exists():
        return 0

    init_db()
    # Antes de añadir archivos nuevos, purgar los que ya no existen.
    prune_missing(library_root)
    root = Path(library_root)

    audio_files = [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    ]
    total = len(audio_files)

    for idx, path in enumerate(audio_files, 1):
        try:
            _index_one(path)
            if on_progress:
                on_progress(idx, total, str(path))
        except Exception as e:
            logger.warning(f"Scan: fallo al indexar {path}: {e}")

    return total


def _index_one(path: Path) -> None:
    """Lee tags del archivo y guarda en la DB."""
    mf = MutagenFile(path)
    if mf is None:
        return

    tags = mf.tags or {}
    duration_s = None
    if hasattr(mf, "info") and getattr(mf.info, "length", None):
        duration_s = int(mf.info.length)

    title  = _read_tag(tags, ["TIT2", "title", "\xa9nam"])
    artist = _read_tag(tags, ["TPE1", "artist", "\xa9ART"])
    album  = _read_tag(tags, ["TALB", "album", "\xa9alb"])
    year   = _read_tag(tags, ["TDRC", "TDRL", "TYER", "date", "\xa9day"])
    if year and len(year) >= 4:
        year = year[:4]
    genre = _read_tag(tags, ["TCON", "genre", "\xa9gen"])

    track_number_raw = _read_tag(tags, ["TRCK", "tracknumber", "trkn"])
    track_number = None
    if track_number_raw:
        try:
            # Formato común: "5/12" → 5
            track_number = int(str(track_number_raw).split("/")[0])
        except ValueError:
            pass

    # MB Recording ID puede estar en TXXX:MusicBrainz Track Id (ID3),
    # UFID, o en una clave Vorbis 'musicbrainz_trackid'
    mb_recording_id = (
        _read_tag(tags, ["musicbrainz_trackid", "MUSICBRAINZ_TRACKID"]) or
        _read_id3_txxx(tags, "MusicBrainz Track Id") or
        _read_id3_txxx(tags, "MusicBrainz Recording Id")
    )
    mb_release_id = (
        _read_tag(tags, ["musicbrainz_albumid", "MUSICBRAINZ_ALBUMID"]) or
        _read_id3_txxx(tags, "MusicBrainz Album Id")
    )
    isrc = (
        _read_tag(tags, ["TSRC", "isrc", "ISRC"]) or
        _read_id3_txxx(tags, "ISRC")
    )

    upsert_track(
        str(path),
        title=title, artist=artist, album=album, year=year,
        track_number=track_number, genre=genre, duration_s=duration_s,
        mb_recording_id=mb_recording_id, mb_release_id=mb_release_id,
        isrc=isrc,
    )


def _read_tag(tags, keys) -> Optional[str]:
    for k in keys:
        if k in tags:
            val = tags[k]
            if isinstance(val, list) and val:
                val = val[0]
            if hasattr(val, "text"):
                v = val.text
                if isinstance(v, list) and v:
                    return str(v[0])
                return str(v)
            return str(val) if val else None
    return None


def _read_id3_txxx(tags, desc: str) -> Optional[str]:
    """Lee un TXXX (custom text frame) de ID3 por descripción."""
    for k, val in tags.items() if hasattr(tags, "items") else []:
        if k.startswith("TXXX:") and k.endswith(desc):
            if hasattr(val, "text") and val.text:
                return str(val.text[0])
    return None
