"""
Cálculo de BPM (tempo) en local — fase 2c.5.

Backend principal: librosa. Se carga perezosamente: si la importación falla
(librosa no instalada o problema con dependencias nativas), el módulo se
queda en modo "no disponible" y `compute_bpm()` devuelve None sin romper.

Arquitectura preparada para fallback a aubio en el futuro: la función
pública `compute_bpm(path)` abstrae el backend.

API pública:
  is_available() -> bool
  availability_message() -> str
  compute_bpm(path, *, timeout_s=60) -> Optional[float]
"""

from __future__ import annotations
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("BPM")

# Carga perezosa de librosa. Importarla puede costar 300-700 ms (numpy,
# numba, scipy, audioread). Lo hacemos diferido para no penalizar el
# arranque de la app si el usuario no usa BPM nunca.
_librosa = None
_librosa_load_attempted = False
_librosa_error: str = ""


def _try_load_librosa() -> bool:
    """Intenta importar librosa. Cachea el resultado. Idempotente."""
    global _librosa, _librosa_load_attempted, _librosa_error
    if _librosa_load_attempted:
        return _librosa is not None
    _librosa_load_attempted = True
    try:
        import librosa  # type: ignore
        _librosa = librosa
        logger.info(f"BPM backend: librosa {librosa.__version__}")
        return True
    except Exception as e:  # ImportError, OSError de deps nativas, etc.
        _librosa_error = str(e)
        logger.warning(f"BPM backend (librosa) no disponible: {e}")
        return False


def is_available() -> bool:
    """True si hay un backend de BPM operativo."""
    return _try_load_librosa()


def availability_message() -> str:
    if is_available():
        return ""
    return (
        "Cálculo de BPM inactivo: librosa no está disponible.\n"
        f"  Detalle: {_librosa_error}\n"
        "  Instala con: pip install --user librosa"
    )


def compute_bpm(path: str, *, timeout_s: float = 60.0) -> Optional[float]:
    """
    Calcula el BPM de un archivo de audio. Devuelve un float redondeado a un
    decimal o None si falla. Con timeout de seguridad para pistas largas o
    archivos corruptos: si el cálculo tarda más de `timeout_s`, devuelve None.

    Notas:
    - librosa.beat.beat_track devuelve un tempo global. Es suficiente para
      filtrar por "fiesta": no necesitamos un BPM con precisión de productor.
    - Cargamos el audio a 22050 Hz mono (lo suficiente para estimar tempo) y
      sin más de 90 s de duración para acelerar pistas largas. El BPM global
      se estima bien con un fragmento central de un par de minutos.
    """
    if not _try_load_librosa():
        return None
    p = Path(path)
    if not p.exists():
        logger.debug(f"compute_bpm: no existe {path}")
        return None

    result: dict = {"bpm": None, "err": None}

    def _worker():
        try:
            # offset=15s para saltar intros silenciosos; duration=90s para
            # acelerar pistas largas sin perder representatividad.
            y, sr = _librosa.load(
                str(p), sr=22050, mono=True, offset=15.0, duration=90.0,
            )
            if y is None or len(y) < sr * 5:
                # Si el fragmento desde el offset es muy corto, volver a
                # cargar desde el principio sin offset.
                y, sr = _librosa.load(
                    str(p), sr=22050, mono=True, duration=90.0,
                )
            if y is None or len(y) < sr * 3:
                result["err"] = "audio demasiado corto"
                return
            tempo, _ = _librosa.beat.beat_track(y=y, sr=sr)
            # librosa devuelve a veces numpy.ndarray; normalizar a float
            try:
                tempo_f = float(tempo)
            except Exception:
                tempo_f = float(tempo[0]) if hasattr(tempo, "__len__") else 0.0
            if tempo_f <= 0:
                result["err"] = "tempo no detectado"
                return
            result["bpm"] = round(tempo_f, 1)
        except Exception as e:
            result["err"] = str(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        logger.warning(f"compute_bpm: timeout ({timeout_s}s) en {p.name}")
        return None
    if result["err"]:
        logger.debug(f"compute_bpm: {p.name} → {result['err']}")
        return None
    return result["bpm"]
