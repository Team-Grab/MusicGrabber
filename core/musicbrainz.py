"""
Cliente de MusicBrainz para Music Grabber.

Política de uso:
  - Rate limit auto-respetado (1 req/s anonymous) vía musicbrainzngs.set_rate_limit.
  - User-Agent identificable (requisito del servicio).
  - Búsqueda primaria por título + artista. Fallback por ISRC si está disponible.
  - Descarga de carátula desde Cover Art Archive (sin clave).

API pública:
  configure(version: str) -> None
  search_recording(title, artist, duration_s=None, isrc=None) -> MatchResult | None
  fetch_cover_art(release_id: str) -> bytes | None

MatchResult contiene los campos canónicos que el pipeline aplica al archivo.
"""

import re
import time
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import musicbrainzngs

logger = logging.getLogger("MusicBrainz")

# Confianza mínima para aplicar match automáticamente (escala 0-100 de MB).
# 85 da buenos resultados sin demasiados falsos positivos. 90 era demasiado
# estricto y rechazaba matches válidos por diferencias menores en el título.
SCORE_AUTO_THRESHOLD   = 85
SCORE_REVIEW_THRESHOLD = 60

# Configuración por defecto del cliente. Se actualiza desde configure().
_USER_AGENT_APP     = "MusicGrabber"
_USER_AGENT_VERSION = "2.0-dev"
_USER_AGENT_CONTACT = "https://github.com/Team-Grab/MusicGrabber"

COVER_ART_URL = "https://coverartarchive.org/release/{release_id}/front"


# ---------------------------------------------------------------------------
# Estructura de resultado
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    """Resultado canónico de una búsqueda en MusicBrainz."""
    recording_id: str
    title:        str
    artist:       str
    album:        Optional[str]      = None
    release_id:   Optional[str]      = None      # para Cover Art Archive
    year:         Optional[str]      = None
    track_number: Optional[int]      = None
    total_tracks: Optional[int]      = None
    genre:        Optional[str]      = None
    isrc:         Optional[str]      = None
    score:        int                = 0          # 0-100
    candidates:   List[Dict[str, Any]] = field(default_factory=list)  # si hay ambigüedad

    @property
    def is_auto(self) -> bool:
        return self.score >= SCORE_AUTO_THRESHOLD

    @property
    def is_ambiguous(self) -> bool:
        return SCORE_REVIEW_THRESHOLD <= self.score < SCORE_AUTO_THRESHOLD

    @property
    def is_unreliable(self) -> bool:
        return self.score < SCORE_REVIEW_THRESHOLD


# ---------------------------------------------------------------------------
# Configuración inicial
# ---------------------------------------------------------------------------

def configure(version: str = "2.0-dev") -> None:
    """Llamar una vez al arrancar la app."""
    global _USER_AGENT_VERSION
    _USER_AGENT_VERSION = version
    musicbrainzngs.set_useragent(_USER_AGENT_APP, _USER_AGENT_VERSION, _USER_AGENT_CONTACT)
    musicbrainzngs.set_rate_limit(limit_or_interval=1.0, new_requests=1)
    # Silenciar los "INFO: in <ws2:recording>, uncaught <first-release-date>"
    # que ensucian la consola cuando se procesan muchas pistas.
    logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
    logger.info(f"MusicBrainz cliente configurado: {_USER_AGENT_APP}/{_USER_AGENT_VERSION}")


# ---------------------------------------------------------------------------
# Búsqueda principal
# ---------------------------------------------------------------------------

def search_recording(
    title: str,
    artist: str,
    duration_s: Optional[int] = None,
    isrc: Optional[str]       = None,
) -> Optional[MatchResult]:
    """
    Busca una grabación en MusicBrainz.
    Prioridad:
      1) Si hay ISRC válido, búsqueda por ISRC (más fiable, score forzado a 100).
      2) Búsqueda por título + artista.
    Devuelve un MatchResult o None si no hay nada utilizable.
    """
    if isrc:
        match = _search_by_isrc(isrc)
        if match:
            return match

    return _search_by_title_artist(title, artist, duration_s)


def _search_by_isrc(isrc: str) -> Optional[MatchResult]:
    try:
        result = musicbrainzngs.get_recordings_by_isrc(
            isrc,
            includes=["releases", "release-groups", "tags", "artist-credits"],
        )
    except musicbrainzngs.WebServiceError as e:
        logger.warning(f"ISRC {isrc}: {e}")
        return None
    except urllib.error.URLError as e:
        logger.warning(f"Red al buscar ISRC {isrc}: {e}")
        return None

    recordings = result.get("isrc", {}).get("recording-list", [])
    if not recordings:
        return None

    # Con ISRC casi siempre devuelve una sola grabación. Aceptamos la primera.
    return _build_match_result(recordings[0], score=100, source_isrc=isrc)


def _search_by_title_artist(
    title: str,
    artist: str,
    duration_s: Optional[int] = None,
) -> Optional[MatchResult]:
    # Título obligatorio; artista opcional (los resultados sin artista son
    # menos precisos pero la búsqueda manual puede no tenerlo).
    if not title:
        return None

    # Sin comillas: la búsqueda fuzzy de MB ya tolera variaciones. Las
    # comillas exigían coincidencia exacta y descartaban matches válidos
    # con sufijos como "(Remastered 2011)" o "- Live at Wembley".
    # Limpiar caracteres que rompen la query Lucene.
    q_title  = re.sub(r'[\\/"\[\]:()]', " ", title).strip()
    q_artist = re.sub(r'[\\/"\[\]:()]', " ", artist or "").strip()
    if not q_title:
        return None
    if q_artist:
        query = f'recording:{q_title} AND artist:{q_artist}'
    else:
        query = f'recording:{q_title}'

    try:
        result = musicbrainzngs.search_recordings(query=query, limit=5)
    except musicbrainzngs.WebServiceError as e:
        logger.warning(f"MB: {e}")
        return None
    except urllib.error.URLError as e:
        logger.warning(f"Red al buscar '{title}' / '{artist}': {e}")
        return None

    recordings = result.get("recording-list", [])
    if not recordings:
        return None

    # Filtrado por duración suave: si encaja, prioriza; si no, no descarta.
    # ±10 s para tolerar variaciones entre ediciones (intros, fadeouts).
    if duration_s:
        with_dur = []
        for r in recordings:
            length_ms = r.get("length")
            if length_ms and abs(int(length_ms) / 1000 - duration_s) <= 10:
                with_dur.append(r)
        if with_dur:
            recordings = with_dur

    top = recordings[0]
    score = int(top.get("ext:score", 0))

    match = _build_match_result(top, score=score)
    # Siempre exponer candidatos (no solo cuando is_ambiguous) — la bandeja
    # los aprovecha para revisión manual incluso cuando el match top es bueno.
    if len(recordings) > 0:
        match.candidates = [
            {
                "title":        _safe_get_field(r, "title"),
                "artist":       _format_artist_credit(r.get("artist-credit", [])),
                "album":        _safe_release_field(r, "title"),
                "year":         _safe_release_field(r, "date", first=4),
                "recording_id": r.get("id", ""),
                "release_id":   _safe_release_field(r, "id"),
                "score":        int(r.get("ext:score", 0)),
            }
            for r in recordings[:5]
        ]
    return match


def _safe_get_field(d, key) -> str:
    v = d.get(key, "")
    return str(v) if v else ""


def _safe_release_field(recording, key, first: Optional[int] = None) -> str:
    releases = recording.get("release-list", [])
    if not releases:
        return ""
    val = releases[0].get(key, "")
    if not val:
        return ""
    val = str(val)
    return val[:first] if first else val


# ---------------------------------------------------------------------------
# Construcción de MatchResult desde respuesta MB
# ---------------------------------------------------------------------------

def _build_match_result(
    recording: Dict[str, Any],
    score: int,
    source_isrc: Optional[str] = None,
) -> MatchResult:
    rec_id   = recording.get("id", "")
    title    = recording.get("title", "")
    artist   = _format_artist_credit(recording.get("artist-credit", []))

    album, release_id, year, track_number, total_tracks = _pick_best_release(recording)

    genre = None
    tags  = recording.get("tag-list", [])
    if tags:
        # Tag con mayor count
        try:
            tags_sorted = sorted(tags, key=lambda t: int(t.get("count", 0)), reverse=True)
            genre = tags_sorted[0].get("name")
        except (ValueError, TypeError):
            genre = tags[0].get("name") if tags else None

    isrc_list = recording.get("isrc-list", [])
    isrc      = source_isrc or (isrc_list[0] if isrc_list else None)

    return MatchResult(
        recording_id = rec_id,
        title        = title,
        artist       = artist,
        album        = album,
        release_id   = release_id,
        year         = year,
        track_number = track_number,
        total_tracks = total_tracks,
        genre        = genre,
        isrc         = isrc,
        score        = score,
    )


def _format_artist_credit(credit_list: List[Any]) -> str:
    """Una grabación puede tener varios artistas (feat.). Devuelve el principal."""
    if not credit_list:
        return ""
    first = credit_list[0]
    if isinstance(first, dict):
        return first.get("artist", {}).get("name", "") or first.get("name", "")
    return str(first)


def _pick_best_release(recording: Dict[str, Any]):
    """
    Devuelve (album, release_id, year, track_number, total_tracks) del
    primer release con datos completos. Prefiere álbumes oficiales sobre
    singles o recopilaciones.
    """
    releases = recording.get("release-list", [])
    if not releases:
        return None, None, None, None, None

    def _release_priority(r):
        group = r.get("release-group", {})
        primary = (group.get("primary-type") or "").lower()
        # Albums oficiales primero, luego EPs, luego singles, luego el resto
        return {"album": 0, "ep": 1, "single": 2}.get(primary, 3)

    releases_sorted = sorted(releases, key=_release_priority)
    best = releases_sorted[0]

    album      = best.get("title")
    release_id = best.get("id")
    year       = _extract_year(best, releases_sorted)

    track_number = None
    total_tracks = None
    medium_list = best.get("medium-list", [])
    for medium in medium_list:
        for track in medium.get("track-list", []) or []:
            if track.get("recording", {}).get("id") == recording.get("id"):
                pos = track.get("position") or track.get("number")
                if pos:
                    try:
                        track_number = int(pos)
                    except ValueError:
                        pass
                count = medium.get("track-count")
                if count:
                    try:
                        total_tracks = int(count)
                    except ValueError:
                        pass
                break
        if track_number:
            break

    return album, release_id, year, track_number, total_tracks


def _extract_year(best_release: Dict[str, Any], all_releases: List[Dict[str, Any]]) -> Optional[str]:
    """
    Extrae el año del release elegido. Fallbacks:
      1) best_release['date']
      2) best_release['release-group']['first-release-date']
      3) cualquier 'date' en all_releases (el más antiguo)
    Devuelve un string de 4 dígitos o None.
    """
    # 1) Fecha del release elegido
    date = (best_release.get("date") or "").strip()
    if len(date) >= 4 and date[:4].isdigit():
        return date[:4]

    # 2) Fecha canónica del release-group
    group_date = (best_release.get("release-group", {}).get("first-release-date") or "").strip()
    if len(group_date) >= 4 and group_date[:4].isdigit():
        return group_date[:4]

    # 3) Buscar en cualquier release alternativo
    years = []
    for r in all_releases:
        d = (r.get("date") or "").strip()
        if len(d) >= 4 and d[:4].isdigit():
            years.append(d[:4])
        gd = (r.get("release-group", {}).get("first-release-date") or "").strip()
        if len(gd) >= 4 and gd[:4].isdigit():
            years.append(gd[:4])
    if years:
        return min(years)  # el más antiguo (suele ser la primera publicación)

    return None


# ---------------------------------------------------------------------------
# Cover Art Archive
# ---------------------------------------------------------------------------

def fetch_cover_art(release_id: str, timeout: int = 15) -> Optional[bytes]:
    """
    Descarga la carátula frontal de un release desde Cover Art Archive.
    Devuelve bytes (jpg/png) o None si no hay portada.
    """
    if not release_id:
        return None

    url = COVER_ART_URL.format(release_id=release_id)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"{_USER_AGENT_APP}/{_USER_AGENT_VERSION} ({_USER_AGENT_CONTACT})"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        logger.warning(f"Cover Art Archive {release_id}: HTTP {e.code}")
        return None
    except urllib.error.URLError as e:
        logger.warning(f"Cover Art Archive {release_id}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Cover Art Archive {release_id}: {e}")
        return None
