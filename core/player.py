"""
Reproductor de audio integrado — fase 2a.

Backend: python-vlc. Requiere VLC instalado en el sistema. Si VLC no está
disponible, la app sigue funcionando pero el reproductor permanece inactivo y
los controles muestran un aviso al usuario.

API pública:
  player.is_available() -> bool                — backend operativo
  player.load_queue(paths, start_index=0)      — carga lista y empieza
  player.play()                                — resume / arranca primero
  player.pause()                               — alterna pausa
  player.stop()                                — para y limpia
  player.next() / player.prev()                — navegar cola
  player.seek_to_fraction(0..1)                — ir a posición
  player.set_volume(0..100)                    — volumen
  player.get_state() -> PlayerState            — snapshot para la GUI
  player.add_to_queue(path)                    — añadir al final
  player.append_after_current(path)            — siguiente tras la actual
  player.clear_queue()
"""

from __future__ import annotations
import logging
import random
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Callable, Tuple

from core import library
from core.state import log_event, state

logger = logging.getLogger("Player")

# Intento de importar vlc. Si falla, el módulo se carga igual pero el
# reproductor queda en modo "no disponible".
try:
    import vlc  # type: ignore
    _VLC_AVAILABLE = True
except (ImportError, OSError) as _e:
    vlc = None  # type: ignore
    _VLC_AVAILABLE = False
    _VLC_IMPORT_ERROR = str(_e)
else:
    _VLC_IMPORT_ERROR = ""


# ---------------------------------------------------------------------------
# Snapshot legible para la GUI
# ---------------------------------------------------------------------------

@dataclass
class PlayerState:
    available:        bool = False
    status:           str  = "stopped"   # stopped | playing | paused | error
    current_path:     str  = ""
    current_title:    str  = ""
    current_artist:   str  = ""
    current_album:    str  = ""
    duration_s:       int  = 0
    position_s:       int  = 0
    position_fraction: float = 0.0       # 0..1, útil para el slider
    queue_length:     int  = 0
    queue_index:      int  = -1          # 0-based; -1 si no hay nada cargado
    volume:           int  = 80          # 0..100
    shuffle:          bool = False
    repeat:           str  = "off"       # "off" | "list" | "track"
    sleep_seconds_left: Optional[int] = None  # countdown del timer cronométrico
    sleep_after_track: bool = False           # modo "parar tras pista actual"
    eq_preset:        str  = "Flat"
    party_enabled:    bool = False
    party_min_bpm:    float = 110.0
    party_max_bpm:    float = 140.0
    last_error:       str  = ""


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

class _Player:
    """Singleton. Usa `player` exportado al final del módulo."""

    def __init__(self):
        self._lock = threading.RLock()
        self._queue: List[str] = []
        self._original_order: List[str] = []   # antes de shuffle
        self._shuffle: bool = False
        self._repeat: str = "off"              # "off" | "list" | "track"
        self._index: int = -1
        self._volume: int = 80
        self._last_error: str = ""
        self._vlc_instance = None
        self._vlc_player = None
        # Sleep timer
        self._sleep_timer: Optional[threading.Timer] = None
        self._sleep_deadline: Optional[float] = None     # epoch
        self._sleep_after_track: bool = False
        self._sleep_stop_pending: bool = False
        # Ecualizador. Usamos handles directos de libvlc (no el wrapper
        # vlc.AudioEqualizer porque su constructor con int provoca segfault
        # en VLC 3.0.23 en algunas combinaciones).
        self._eq_handle = None
        self._eq_preset_name: str = "Flat"
        # 10 bandas, en dB. Inicialmente todas a 0 (Flat).
        self._eq_bands: List[float] = [0.0] * 10
        self._eq_preamp: float = 0.0  # default 0, no 12: 12 amplifica fuerte
        # Crossfade (fase 2c.4). Mantenemos dos media_players: el "activo"
        # apunta a self._vlc_player; el "otro" se usa para preparar la
        # siguiente pista y hacer el fundido. Tras el fade, se hace swap.
        self._vlc_player_a = None
        self._vlc_player_b = None
        self._crossfade_enabled: bool = False
        self._crossfade_seconds: int = 4
        self._crossfade_in_progress: bool = False
        self._crossfade_thread: Optional[threading.Thread] = None
        # Modo fiesta (fase 2c.5)
        # Comportamiento: autoplay desde toda la biblioteca con pool fresco.
        # Al activar: vacía la cola, mete 3 semillas y se autollena en cada
        # poll manteniendo 2 pistas por delante del índice activo. Al
        # desactivar: vacía la cola, stop, restaura crossfade previo.
        self._party_enabled: bool = False
        self._party_min_bpm: float = 110.0
        self._party_max_bpm: float = 140.0
        self._party_played: set = set()       # paths ya emitidos esta sesión
        # Cuántas pistas se mantienen siempre por delante del índice activo
        self._PARTY_LOOKAHEAD = 2
        # Semilla inicial al activar la fiesta
        self._PARTY_SEED_SIZE = 3

        if _VLC_AVAILABLE:
            try:
                # --no-video evita ventana de vídeo emergente para audio puro
                self._vlc_instance = vlc.Instance("--no-video", "--quiet")
                # Dos media_players para soportar crossfade. El "activo" es
                # self._vlc_player (pointer que rota con el swap del fade).
                self._vlc_player_a = self._vlc_instance.media_player_new()
                self._vlc_player_b = self._vlc_instance.media_player_new()
                self._vlc_player   = self._vlc_player_a
                # No llamar a audio_set_volume aquí: en VLC 3.x sobre pipewire,
                # set_volume antes de que haya un stream activo provoca segfault
                # en libaout_pipewire_plugin.vlc_pw_stream_set_volume.
                # Se aplica en _safe_set_volume_now() tras play().
                #
                # Pre-armar la pipeline del ecualizador con un handle Flat
                # ANTES del primer play en AMBOS players. Si no lo hacemos,
                # el primer libvlc_media_player_set_equalizer() llamado
                # mid-stream en VLC 3.0.23 sobre pipewire silencia el bus
                # hasta reiniciar la app. Aplicar un handle Flat sin stream
                # activo es seguro (a diferencia de audio_set_volume).
                try:
                    self._eq_handle = vlc.libvlc_audio_equalizer_new()
                    if self._eq_handle:
                        for mp in (self._vlc_player_a, self._vlc_player_b):
                            vlc.libvlc_media_player_set_equalizer(
                                mp, self._eq_handle
                            )
                except Exception as _eq_e:
                    logger.debug(f"Pre-armado de EQ falló (no crítico): {_eq_e}")
                logger.info("Reproductor VLC inicializado")
            except Exception as e:
                self._last_error = f"VLC no se pudo inicializar: {e}"
                logger.warning(self._last_error)
                self._vlc_player = None
                self._vlc_player_a = None
                self._vlc_player_b = None
        else:
            self._last_error = (
                f"python-vlc no disponible ({_VLC_IMPORT_ERROR}). "
                "Instala VLC con tu gestor de paquetes."
            )

    # --------------------------- Estado --------------------------------

    def is_available(self) -> bool:
        return self._vlc_player is not None

    def get_state(self) -> PlayerState:
        with self._lock:
            available = self.is_available()
            st = PlayerState(available=available, volume=self._volume,
                             queue_length=len(self._queue), queue_index=self._index,
                             last_error=self._last_error)
            if not available:
                st.status = "error"
                return st

            st.shuffle = self._shuffle
            st.repeat  = self._repeat
            st.sleep_seconds_left = self.sleep_seconds_left()
            st.sleep_after_track  = self._sleep_after_track
            st.eq_preset = self._eq_preset_name
            st.party_enabled = self._party_enabled
            st.party_min_bpm = self._party_min_bpm
            st.party_max_bpm = self._party_max_bpm

            cur = self._current_path()
            if cur:
                st.current_path = cur
                row = library.get_track_by_path(cur)
                if row:
                    st.current_title  = row["title"]  or Path(cur).stem
                    st.current_artist = row["artist"] or ""
                    st.current_album  = row["album"]  or ""
                else:
                    st.current_title = Path(cur).stem

            try:
                length_ms = self._vlc_player.get_length()
                pos_ms    = self._vlc_player.get_time()
                st.duration_s = max(0, length_ms // 1000) if length_ms > 0 else 0
                st.position_s = max(0, pos_ms      // 1000) if pos_ms    > 0 else 0
                if st.duration_s > 0:
                    st.position_fraction = min(1.0, st.position_s / st.duration_s)

                vlc_state = self._vlc_player.get_state()
                if vlc_state in (vlc.State.Playing,):
                    st.status = "playing"
                elif vlc_state in (vlc.State.Paused,):
                    st.status = "paused"
                elif vlc_state in (vlc.State.Ended,):
                    st.status = "stopped"
                    # Auto-next al terminar la pista
                    self._auto_advance()
                else:
                    st.status = "stopped"
                # Hook de modo fiesta: rellenar cola si faltan pistas por
                # delante. Hay que rellenar ANTES del crossfade para que el
                # fade tenga material en self._queue[_index+1].
                if self._party_enabled:
                    self._maybe_refill_party_queue()
                # Hook de crossfade: si está activado y queda poco para el
                # final, lanzar el fundido con la siguiente pista.
                if st.status == "playing":
                    self._maybe_start_crossfade()
            except Exception as e:
                st.status = "error"
                st.last_error = str(e)
            return st

    # --------------------------- Cola ----------------------------------

    def load_queue(self, paths: List[str], start_index: int = 0):
        """Reemplaza la cola con las rutas dadas y arranca en start_index.
        Si el modo fiesta está activo, lo desactiva (cargar una cola explícita
        es una intención clara distinta a "seguir con la fiesta")."""
        if not self.is_available():
            self._warn_unavailable("cargar cola")
            return
        valid = [p for p in paths if p and Path(p).exists()]
        if not valid:
            return
        start = max(0, min(start_index, len(valid) - 1))
        with self._lock:
            if self._party_enabled:
                # Desactivar fiesta SIN vaciar cola (la vamos a reemplazar
                # ahora mismo) ni hacer stop (vamos a llamar a _play_current).
                self._party_enabled = False
                self._party_played = set()
                self._crossfade_enabled = state.crossfade_enabled
                self._crossfade_seconds = state.crossfade_seconds
                log_event("info", "Modo fiesta OFF (cola reemplazada)")
            self._cancel_crossfade()
            self._queue = list(valid)
            self._original_order = list(valid)   # base para shuffle/restore
            self._index = start
            if self._shuffle:
                self._shuffle_remaining_locked()
            self._play_current()

    def add_to_queue(self, path: str):
        if not path or not Path(path).exists():
            return
        with self._lock:
            self._queue.append(path)
            if self._index < 0:
                self._index = 0
                self._play_current()

    def append_after_current(self, path: str):
        if not path or not Path(path).exists():
            return
        with self._lock:
            if self._index < 0:
                self._queue.append(path)
                self._index = 0
                self._play_current()
            else:
                self._queue.insert(self._index + 1, path)

    def clear_queue(self):
        with self._lock:
            self._cancel_crossfade()
            self._vlc_player.stop() if self.is_available() else None
            self._queue.clear()
            self._original_order.clear()
            self._index = -1

    def get_queue(self) -> List[Tuple[int, str, bool]]:
        """Devuelve lista (index, path, is_current) para que la UI la pinte."""
        with self._lock:
            return [
                (i, p, i == self._index)
                for i, p in enumerate(self._queue)
            ]

    def remove_from_queue(self, idx: int):
        """Quita la pista de la posición idx. Si era la actual, salta a la siguiente."""
        with self._lock:
            if not (0 <= idx < len(self._queue)):
                return
            # Si quitamos la actual o la siguiente, cancelar fade en curso
            if idx == self._index or idx == self._index + 1:
                self._cancel_crossfade()
            path = self._queue[idx]
            # Mantener el original_order coherente
            if path in self._original_order:
                self._original_order.remove(path)

            if idx == self._index:
                # Quitar y reproducir la siguiente (que ahora ocupa el mismo índice)
                self._queue.pop(idx)
                if self._index >= len(self._queue):
                    self._index = -1
                    if self.is_available():
                        self._vlc_player.stop()
                else:
                    self._play_current()
            elif idx < self._index:
                self._queue.pop(idx)
                self._index -= 1
            else:
                self._queue.pop(idx)

    def move_in_queue(self, src: int, dst: int):
        """Mueve la pista de la posición src a dst (drag & drop)."""
        with self._lock:
            n = len(self._queue)
            if not (0 <= src < n):
                return
            dst = max(0, min(dst, n - 1))
            if src == dst:
                return
            # Si tocamos la siguiente pista (la candidata al fade), cancelar
            next_idx = self._index + 1 if self._index >= 0 else -1
            if src == next_idx or dst == next_idx:
                self._cancel_crossfade()
            path = self._queue.pop(src)
            self._queue.insert(dst, path)
            # Recalcular el índice de la pista activa
            if self._index == src:
                self._index = dst
            elif src < self._index <= dst:
                self._index -= 1
            elif dst <= self._index < src:
                self._index += 1

    # --------------------------- Sleep timer ----------------------------

    def set_sleep_timer(self, seconds: Optional[int], after_track: bool = False):
        """
        Programa un sleep timer. seconds=None cancela.
        Si after_track=True, ignora seconds y para al terminar la pista actual.
        """
        # Cancelar el timer en curso si lo hay
        if self._sleep_timer is not None:
            try:
                self._sleep_timer.cancel()
            except Exception:
                pass
            self._sleep_timer = None
        self._sleep_deadline = None
        self._sleep_after_track = False
        self._sleep_stop_pending = False

        if seconds is None and not after_track:
            log_event("info", "Sleep timer cancelado")
            return

        if after_track:
            self._sleep_after_track = True
            self._sleep_stop_pending = True
            log_event("info", "Sleep timer: parar al terminar la pista actual")
            return

        # Timer cronométrico
        import time as _t
        self._sleep_deadline = _t.time() + int(seconds)
        self._sleep_timer = threading.Timer(int(seconds), self._sleep_expired)
        self._sleep_timer.daemon = True
        self._sleep_timer.start()
        log_event("info", f"Sleep timer activado: {int(seconds)} s")

    def _sleep_expired(self):
        """Callback del timer: parar la reproducción."""
        log_event("info", "Sleep timer: tiempo agotado, deteniendo reproducción")
        self._sleep_timer = None
        self._sleep_deadline = None
        try:
            self.stop()
        except Exception:
            pass

    def sleep_seconds_left(self) -> Optional[int]:
        """Segundos restantes del timer cronométrico, o None si no hay."""
        if self._sleep_deadline is None:
            return None
        import time as _t
        left = int(self._sleep_deadline - _t.time())
        return max(0, left)

    # --------------------------- Ecualizador ----------------------------

    # Nombres y frecuencias de las 10 bandas estándar de VLC.
    EQ_BAND_FREQS = ("60", "170", "310", "600", "1k", "3k", "6k", "12k", "14k", "16k")

    @staticmethod
    def eq_preset_names() -> List[str]:
        """Lista de presets disponibles en VLC."""
        if not _VLC_AVAILABLE:
            return ["Flat"]
        try:
            count = vlc.libvlc_audio_equalizer_get_preset_count()
            return [
                vlc.libvlc_audio_equalizer_get_preset_name(i).decode("utf-8")
                for i in range(count)
            ]
        except Exception:
            return ["Flat"]

    def _ensure_eq_handle(self):
        """Crea un handle de equalizer vacío si no existe."""
        if self._eq_handle is None:
            try:
                self._eq_handle = vlc.libvlc_audio_equalizer_new()
            except Exception as e:
                log_event("warn", f"Ecualizador: no se pudo crear: {e}")

    def _free_eq_handle(self):
        if self._eq_handle is not None:
            try:
                vlc.libvlc_audio_equalizer_release(self._eq_handle)
            except Exception:
                pass
            self._eq_handle = None

    def set_equalizer_preset(self, name: str) -> bool:
        """Aplica un preset por nombre usando libvlc_audio_equalizer_new_from_preset.
        Más seguro que vlc.AudioEqualizer(idx) que provoca segfault en VLC 3.0.23."""
        if not self.is_available():
            return False
        try:
            names = self.eq_preset_names()
            if name not in names:
                log_event("warn", f"Ecualizador: preset '{name}' no existe")
                return False
            idx = names.index(name)
            # Crear el handle NUEVO antes de liberar el anterior: así nunca
            # dejamos al media_player sin equalizer asignado (ventana
            # transitoria que en VLC 3.0.23+pipewire silencia el bus).
            new_handle = vlc.libvlc_audio_equalizer_new_from_preset(idx)
            if not new_handle:
                log_event("warn", f"Ecualizador: preset '{name}' no se creó")
                return False
            # Aplicar a AMBOS players ANTES de liberar el anterior. Aplicar a
            # los dos garantiza que tras un swap por crossfade el EQ siga
            # activo sin ventana de silencio.
            rc = vlc.libvlc_media_player_set_equalizer(self._vlc_player, new_handle)
            if rc != 0:
                log_event("warn", f"Ecualizador: set_equalizer rc={rc}")
            other = self._other_player_locked()
            if other is not None:
                try:
                    vlc.libvlc_media_player_set_equalizer(other, new_handle)
                except Exception:
                    pass
            # Sustituir handle: liberar el viejo y guardar el nuevo
            old_handle = self._eq_handle
            self._eq_handle = new_handle
            if old_handle is not None:
                try:
                    vlc.libvlc_audio_equalizer_release(old_handle)
                except Exception:
                    pass
            # Cachear valores leídos del handle nuevo
            self._eq_preamp = float(vlc.libvlc_audio_equalizer_get_preamp(self._eq_handle))
            self._eq_bands = [
                float(vlc.libvlc_audio_equalizer_get_amp_at_index(self._eq_handle, i))
                for i in range(10)
            ]
            self._eq_preset_name = name
            log_event("info", f"Ecualizador: preset '{name}' aplicado")
            return True
        except Exception as e:
            log_event("warn", f"Ecualizador: '{name}': {e}")
            return False

    def set_equalizer_band(self, band_idx: int, db: float):
        """Ajusta una banda individual (0..9). Pasa el preset a 'Personalizado'."""
        if not self.is_available() or not (0 <= band_idx < 10):
            return
        try:
            self._ensure_eq_handle()
            if self._eq_handle is None:
                return
            vlc.libvlc_audio_equalizer_set_amp_at_index(
                self._eq_handle, float(db), band_idx
            )
            self._eq_bands[band_idx] = float(db)
            self._eq_preset_name = "Personalizado"
            for mp in self._both_players_locked():
                if mp is not None:
                    vlc.libvlc_media_player_set_equalizer(mp, self._eq_handle)
        except Exception as e:
            log_event("warn", f"Ecualizador: banda {band_idx}: {e}")

    def set_equalizer_preamp(self, db: float):
        """Ajusta el pre-amp."""
        if not self.is_available():
            return
        try:
            self._ensure_eq_handle()
            if self._eq_handle is None:
                return
            vlc.libvlc_audio_equalizer_set_preamp(self._eq_handle, float(db))
            self._eq_preamp = float(db)
            for mp in self._both_players_locked():
                if mp is not None:
                    vlc.libvlc_media_player_set_equalizer(mp, self._eq_handle)
        except Exception as e:
            log_event("warn", f"Ecualizador: preamp: {e}")

    def reset_equalizer(self):
        """Aplica el preset 'Flat' de VLC. Importante: 'Flat' no es 0 dB de
        preamp sino +12 dB, que es el preamp compensatorio interno que VLC
        usa como neutro. Reutilizamos set_equalizer_preset para no replicar."""
        self.set_equalizer_preset("Flat")

    def get_equalizer_state(self) -> dict:
        """Snapshot del estado del EQ para la UI."""
        return {
            "preset":  self._eq_preset_name,
            "preamp":  self._eq_preamp,
            "bands":   list(self._eq_bands),
            "freqs":   list(self.EQ_BAND_FREQS),
            "presets": self.eq_preset_names(),
        }

    # --------------------------- Shuffle / Repeat -----------------------

    def set_shuffle(self, on: bool):
        with self._lock:
            if on == self._shuffle:
                return
            self._shuffle = on
            if on:
                # Guardar orden original y mezclar el resto de la cola
                self._original_order = list(self._queue)
                self._shuffle_remaining_locked()
            else:
                # Restaurar orden original, conservando la pista actual
                self._restore_order_locked()
        log_event("info", f"Modo mezcla: {'ON' if on else 'OFF'}")

    def _shuffle_remaining_locked(self):
        """Mezcla las pistas posteriores a la actual con shuffle inteligente:
        evita pistas consecutivas del mismo artista cuando es posible.
        Las anteriores ya 'sonaron' en orden y no las tocamos."""
        if not self._queue:
            return
        if self._index < 0:
            self._queue = self._smart_shuffle(self._queue, prev_artist=None)
            return
        head = self._queue[: self._index + 1]
        tail = self._queue[self._index + 1 :]
        prev_artist = self._artist_of(head[-1]) if head else None
        self._queue = head + self._smart_shuffle(tail, prev_artist=prev_artist)

    def _smart_shuffle(self, tracks: List[str], prev_artist: Optional[str]) -> List[str]:
        """
        Shuffle greedy con restricción: evita pistas consecutivas del mismo
        artista. Si no hay alternativa (todas las restantes son del mismo
        artista), acepta la repetición.

        Algoritmo: barajamos primero, luego construimos la salida eligiendo
        siempre el primer candidato cuyo artista difiere del último colocado.
        """
        if len(tracks) < 2:
            return list(tracks)

        # Precarga de artistas por path para no consultar la DB N veces.
        artist_cache: dict[str, str] = {p: self._artist_of(p) for p in tracks}

        pool = list(tracks)
        random.shuffle(pool)

        result: List[str] = []
        last_artist = prev_artist
        while pool:
            # Buscar el primer elemento cuyo artista no coincide con last_artist
            chosen_idx = None
            for i, p in enumerate(pool):
                if artist_cache.get(p, "") != last_artist:
                    chosen_idx = i
                    break
            if chosen_idx is None:
                # Todas las restantes son del mismo artista; aceptar
                chosen_idx = 0
            picked = pool.pop(chosen_idx)
            result.append(picked)
            last_artist = artist_cache.get(picked, "")
        return result

    def _artist_of(self, path: str) -> str:
        """Devuelve el artista normalizado de una pista (cadena vacía si no se sabe)."""
        try:
            row = library.get_track_by_path(path)
            if row and row["artist"]:
                return str(row["artist"]).strip().lower()
        except Exception:
            pass
        return ""

    def _restore_order_locked(self):
        """Vuelve al orden original; la pista actual se relocaliza por path."""
        if not self._original_order:
            return
        current_path = self._queue[self._index] if 0 <= self._index < len(self._queue) else None
        self._queue = list(self._original_order)
        if current_path and current_path in self._queue:
            self._index = self._queue.index(current_path)
        elif self._queue and self._index < 0:
            self._index = 0

    def set_repeat(self, mode: str):
        if mode not in ("off", "list", "track"):
            return
        with self._lock:
            self._repeat = mode

    def cycle_repeat(self) -> str:
        """Cicla off→list→track→off. Devuelve el modo nuevo."""
        nxt = {"off": "list", "list": "track", "track": "off"}
        with self._lock:
            old = self._repeat
            self._repeat = nxt.get(self._repeat, "off")
            new = self._repeat
        log_event("info", f"Modo repetir: {old} → {new}")
        return new

    # --------------------------- Transporte -----------------------------

    def play(self):
        if not self.is_available():
            self._warn_unavailable("reproducir")
            return
        with self._lock:
            if self._index < 0 and self._queue:
                self._index = 0
                self._play_current()
            else:
                self._vlc_player.play()

    def pause(self):
        """Alterna pausa/reanuda."""
        if not self.is_available():
            return
        with self._lock:
            # Pausar en medio de un fade dejaría dos streams a volumen
            # parcial: cancelar y dejar solo el activo.
            self._cancel_crossfade()
            self._vlc_player.pause()

    def stop(self):
        if not self.is_available():
            return
        with self._lock:
            self._cancel_crossfade()
            self._vlc_player.stop()

    def next(self):
        if not self.is_available():
            return
        with self._lock:
            self._cancel_crossfade()
            if self._index + 1 < len(self._queue):
                self._index += 1
                self._play_current()
            else:
                self._vlc_player.stop()
                self._index = -1

    def prev(self):
        if not self.is_available():
            return
        with self._lock:
            # Si llevas más de 3 segundos de la pista, prev reinicia la actual
            try:
                pos = self._vlc_player.get_time() // 1000
            except Exception:
                pos = 0
            if pos > 3 and self._index >= 0 and not self._crossfade_in_progress:
                self.seek_to_fraction(0.0)
                return
            self._cancel_crossfade()
            if self._index > 0:
                self._index -= 1
                self._play_current()

    def seek_to_fraction(self, frac: float):
        if not self.is_available():
            return
        frac = max(0.0, min(1.0, float(frac)))
        with self._lock:
            # Si seekeamos durante un fade, cancelar: el "remaining" cambia
            # y la rampa quedaría descolocada.
            self._cancel_crossfade()
            try:
                self._vlc_player.set_position(frac)
            except Exception:
                pass

    def set_volume(self, vol: int):
        vol = max(0, min(100, int(vol)))
        with self._lock:
            self._volume = vol
            in_fade = self._crossfade_in_progress
        # Si hay un fade en curso, no machacar la rampa: el fade lee
        # self._volume como objetivo y lo aplicará al cerrar.
        if in_fade:
            return
        # Solo aplicar si ya hay stream activo. Si no, se guarda y se aplicará
        # en _safe_set_volume_now() tras la próxima reproducción.
        self._safe_set_volume_now()

    def _safe_set_volume_now(self) -> bool:
        """
        Aplica el volumen al player VLC SOLO si hay un stream activo.
        Es seguro contra el bug de pipewire que mata el proceso si se llama
        audio_set_volume sin stream activo en VLC 3.x.
        Devuelve True si se aplicó, False si todavía no era seguro.
        """
        if not self.is_available():
            return False
        try:
            st = self._vlc_player.get_state()
            if st in (vlc.State.Playing, vlc.State.Paused, vlc.State.Buffering):
                self._vlc_player.audio_set_volume(self._volume)
                return True
        except Exception as e:
            logger.debug(f"set_volume diferido: {e}")
        return False

    # --------------------------- Crossfade (fase 2c.4) ------------------

    def set_crossfade(self, enabled: bool, seconds: Optional[int] = None):
        """Activa/desactiva el fundido y fija la duración (1-12 s)."""
        with self._lock:
            self._crossfade_enabled = bool(enabled)
            if seconds is not None:
                self._crossfade_seconds = max(1, min(12, int(seconds)))
        log_event("info",
                  f"Crossfade: {'ON' if enabled else 'OFF'} "
                  f"({self._crossfade_seconds}s)")

    def get_crossfade(self) -> Tuple[bool, int]:
        return self._crossfade_enabled, self._crossfade_seconds

    def _other_player_locked(self):
        """Devuelve el media_player no activo (asumir _lock tomado)."""
        if self._vlc_player_a is None or self._vlc_player_b is None:
            return None
        return self._vlc_player_b if self._vlc_player is self._vlc_player_a else self._vlc_player_a

    def _both_players_locked(self):
        return (self._vlc_player_a, self._vlc_player_b)

    def _cancel_crossfade(self):
        """Cancela un crossfade en curso y para el secundario."""
        was_in_progress = self._crossfade_in_progress
        self._crossfade_in_progress = False
        if not was_in_progress:
            return
        other = self._other_player_locked()
        if other is not None:
            try:
                other.stop()
            except Exception:
                pass

    def _maybe_start_crossfade(self):
        """Llamado desde get_state durante el polling. Decide si arrancar
        el fade antes de que la pista actual termine."""
        if not self._crossfade_enabled or self._crossfade_seconds <= 0:
            return
        if self._crossfade_in_progress:
            return
        if self._repeat == "track":
            return  # no tiene sentido cruzar con la misma pista
        if self._sleep_stop_pending:
            return  # va a parar al fin de pista
        if self._index < 0:
            return
        # Determinar la siguiente pista (igual que _auto_advance)
        if self._index + 1 < len(self._queue):
            next_idx = self._index + 1
        elif self._repeat == "list" and self._queue:
            next_idx = 0
        else:
            return  # no hay siguiente
        # Comprobar posición / duración
        try:
            length_ms = self._vlc_player.get_length()
            pos_ms    = self._vlc_player.get_time()
        except Exception:
            return
        if length_ms <= 0 or pos_ms < 0:
            return
        remaining_s = (length_ms - pos_ms) / 1000.0
        if remaining_s > self._crossfade_seconds + 0.5:
            return  # todavía no toca
        # Evitar arrancar fade si la pista es más corta que el crossfade:
        # mejor un cut limpio que un fundido sin tiempo. Umbral 1.5x.
        if length_ms / 1000.0 < self._crossfade_seconds * 1.5:
            return
        if remaining_s < 0.2:
            return  # demasiado tarde, lo recogerá _auto_advance
        next_path = self._queue[next_idx]
        if not Path(next_path).exists():
            return
        fade_seconds = max(0.5, min(remaining_s, float(self._crossfade_seconds)))
        self._start_crossfade(next_idx, next_path, fade_seconds)

    def _start_crossfade(self, next_idx: int, next_path: str, fade_seconds: float):
        """Arranca la siguiente pista en el player secundario a volumen 0 y
        lanza el hilo de fade."""
        other = self._other_player_locked()
        if other is None:
            return
        try:
            media = self._vlc_instance.media_new(next_path)
            other.set_media(media)
            # Asegurar EQ aplicado al secundario
            if self._eq_handle is not None:
                try:
                    vlc.libvlc_media_player_set_equalizer(other, self._eq_handle)
                except Exception:
                    pass
            other.play()
            self._crossfade_in_progress = True
            self._crossfade_thread = threading.Thread(
                target=self._fade_loop,
                args=(self._vlc_player, other, fade_seconds, next_idx, next_path),
                daemon=True,
            )
            self._crossfade_thread.start()
            log_event("info",
                      f"Crossfade: arrancando → {Path(next_path).name} ({fade_seconds:.1f}s)")
        except Exception as e:
            log_event("warn", f"Crossfade: no se pudo iniciar: {e}")
            self._crossfade_in_progress = False

    def _fade_loop(self, fading_out, fading_in, total_s: float,
                   next_idx: int, next_path: str):
        """Rampa de volumen entre dos players en hilo. Hace swap al final."""
        import time as _t
        target_vol = self._volume
        # Esperar a que el secundario esté listo para set_volume sin segfault
        ready = False
        for _ in range(40):  # hasta 2 s
            if not self._crossfade_in_progress:
                return
            try:
                st = fading_in.get_state()
                if st in (vlc.State.Playing, vlc.State.Buffering):
                    ready = True
                    break
            except Exception:
                pass
            _t.sleep(0.05)
        if not ready:
            # Pipewire no levantó el stream a tiempo: cancelar y dejar que
            # _auto_advance haga el cambio limpio.
            self._crossfade_in_progress = False
            try:
                fading_in.stop()
            except Exception:
                pass
            log_event("warn", "Crossfade: stream secundario no listo, cancelado")
            return
        try:
            fading_in.audio_set_volume(0)
        except Exception:
            pass
        # Rampa lineal
        steps = max(10, int(total_s * 20))  # ~20 pasos/s
        step_dt = total_s / steps
        for i in range(1, steps + 1):
            if not self._crossfade_in_progress:
                return
            frac = i / steps
            try:
                fading_in.audio_set_volume(int(round(target_vol * frac)))
                fading_out.audio_set_volume(int(round(target_vol * (1.0 - frac))))
            except Exception:
                pass
            _t.sleep(step_dt)
        # Swap atómico
        with self._lock:
            if not self._crossfade_in_progress:
                return
            try:
                fading_out.stop()
            except Exception:
                pass
            self._vlc_player = fading_in
            self._index = next_idx
            try:
                fading_in.audio_set_volume(target_vol)
            except Exception:
                pass
            self._crossfade_in_progress = False
        self._touch_played(next_path)
        log_event("info", f"Crossfade: completado → {Path(next_path).name}")

    # --------------------------- Modo fiesta (fase 2c.5) ----------------

    def set_party_mode(self, enabled: bool,
                       min_bpm: Optional[float] = None,
                       max_bpm: Optional[float] = None,
                       party_crossfade_s: Optional[int] = None) -> int:
        """
        Activa o desactiva el modo fiesta.

        Comportamiento al activar:
          1. Vacía la cola actual completamente (ignora lo que hubiera).
          2. Toma la biblioteca entera, filtra por BPM en [min, max], y mete
             una semilla de N pistas aleatorias (shuffle inteligente: evita
             pistas seguidas del mismo artista).
          3. Arranca a reproducir.
          4. En cada poll, mientras quede menos de LOOKAHEAD pistas por
             delante del índice activo, añade una pista nueva al final.
             Las pistas se eligen de un pool "fresco" (las que no han sonado
             en esta sesión). Cuando se agotan, el pool se reinicia.
          5. Fuerza crossfade ON con `party_crossfade_s` (default 6).

        Comportamiento al desactivar:
          - Vacía la cola y para la reproducción.
          - Restaura crossfade a la config previa.
          - Limpia el pool de la sesión.

        Devuelve el número de pistas total disponibles en el rango (el
        "tamaño del pool"). Si es 0, no se activa nada.
        """
        with self._lock:
            if enabled:
                if min_bpm is not None:
                    self._party_min_bpm = float(min_bpm)
                if max_bpm is not None:
                    self._party_max_bpm = float(max_bpm)
                if self._party_max_bpm < self._party_min_bpm:
                    self._party_min_bpm, self._party_max_bpm = (
                        self._party_max_bpm, self._party_min_bpm,
                    )
                pool = self._party_query_pool()
                if not pool:
                    log_event("warn",
                              f"Modo fiesta: ninguna pista con BPM "
                              f"∈[{self._party_min_bpm:.0f}, {self._party_max_bpm:.0f}].")
                    return 0
                # Forzar crossfade festivo (al desactivar se restaura el
                # crossfade GENERAL vigente en state, no uno "capturado" al
                # activar — si el usuario lo cambia en Ajustes mientras la
                # fiesta está sonando, ese cambio debe aplicarse al salir).
                cf_s = int(party_crossfade_s) if party_crossfade_s is not None else 6
                self._crossfade_enabled = True
                self._crossfade_seconds = max(1, min(12, cf_s))
                # Vaciar todo lo anterior
                self._cancel_crossfade()
                self._queue.clear()
                self._original_order.clear()
                self._index = -1
                self._party_played = set()
                self._party_enabled = True
                # Semilla inicial: N pistas aleatorias del pool
                for _ in range(self._PARTY_SEED_SIZE):
                    self._party_append_one_locked(pool)
                if not self._queue:
                    self._party_enabled = False
                    return 0
                self._index = 0
                self._play_current()
                log_event("info",
                          f"Modo fiesta ON: BPM ∈[{self._party_min_bpm:.0f}, "
                          f"{self._party_max_bpm:.0f}], pool={len(pool)}, "
                          f"crossfade {self._crossfade_seconds}s")
                return len(pool)
            else:
                # Desactivar: stop + cola limpia + restaurar crossfade
                self._cancel_crossfade()
                try:
                    if self.is_available():
                        self._vlc_player.stop()
                except Exception:
                    pass
                self._queue.clear()
                self._original_order.clear()
                self._index = -1
                self._party_enabled = False
                self._party_played = set()
                self._crossfade_enabled = state.crossfade_enabled
                self._crossfade_seconds = state.crossfade_seconds
                log_event("info", "Modo fiesta OFF")
                return 0

    def get_party_mode(self) -> Tuple[bool, float, float]:
        return self._party_enabled, self._party_min_bpm, self._party_max_bpm

    def _party_query_pool(self) -> List[Tuple[str, str]]:
        """Devuelve [(path, artist_normalized)] de pistas en el rango BPM."""
        try:
            rows = library.tracks_in_bpm_range(self._party_min_bpm, self._party_max_bpm)
        except Exception as e:
            log_event("warn", f"Modo fiesta: query falló: {e}")
            return []
        out = []
        for r in rows:
            p = r["path"]
            if not p or not Path(p).exists():
                continue
            a = (r["artist"] or "").strip().lower()
            out.append((p, a))
        return out

    def _party_append_one_locked(self, pool: Optional[List[Tuple[str, str]]] = None):
        """Añade UNA pista al final de la cola, escogida del pool fresco.
        Reinicia el set de 'ya sonadas' si se agota. Shuffle inteligente:
        evita repetir el artista de la última pista de la cola. Asume _lock."""
        if pool is None:
            pool = self._party_query_pool()
            if not pool:
                return
        # Filtrar candidatos "frescos"
        fresh = [(p, a) for (p, a) in pool if p not in self._party_played]
        if not fresh:
            # Pool agotado en esta sesión → resetear
            self._party_played.clear()
            fresh = list(pool)
            log_event("info", "Modo fiesta: pool agotado, reiniciando rotación")
        # Artista de la última pista en cola, para no repetir seguidos
        last_artist = None
        if self._queue:
            last_path = self._queue[-1]
            for (p, a) in pool:
                if p == last_path:
                    last_artist = a
                    break
        candidates = [(p, a) for (p, a) in fresh if a != last_artist] or fresh
        path, _artist = random.choice(candidates)
        self._queue.append(path)
        self._original_order.append(path)
        self._party_played.add(path)

    def _maybe_refill_party_queue(self):
        """Llamado desde get_state mientras _party_enabled. Mantiene siempre
        LOOKAHEAD pistas por delante del índice activo."""
        if not self._party_enabled:
            return
        if self._index < 0:
            return
        ahead = len(self._queue) - self._index - 1
        if ahead >= self._PARTY_LOOKAHEAD:
            return
        pool = self._party_query_pool()
        if not pool:
            return
        needed = self._PARTY_LOOKAHEAD - ahead
        for _ in range(needed):
            self._party_append_one_locked(pool)

    # --------------------------- Internos ------------------------------

    def _current_path(self) -> Optional[str]:
        if 0 <= self._index < len(self._queue):
            return self._queue[self._index]
        return None

    def _play_current(self):
        path = self._current_path()
        if not path:
            return
        # Si llegamos aquí en medio de un fade (cambio manual de pista,
        # reload de cola, etc.) cancelar y parar el secundario.
        if self._crossfade_in_progress:
            self._cancel_crossfade()
        try:
            media = self._vlc_instance.media_new(path)
            self._vlc_player.set_media(media)
            self._vlc_player.play()
            # Reaplicar el equalizer tras set_media. set_media puede resetear
            # asociaciones en el media_player; reaplicar asegura que la
            # pipeline de EQ siga activa para esta pista. Defensa adicional
            # contra el bug "primer set_equalizer mid-stream silencia".
            if self._eq_handle is not None:
                try:
                    vlc.libvlc_media_player_set_equalizer(
                        self._vlc_player, self._eq_handle
                    )
                except Exception as _eq_e:
                    logger.debug(f"Reaplicar EQ tras set_media falló: {_eq_e}")
            log_event("info", f"Reproduciendo: {Path(path).name}")
            # Aplicar el volumen en background tras play(): pipewire necesita
            # unos ms para que el stream esté listo. Reintentamos hasta 2 s.
            self._spawn_volume_apply()
            # Incrementar play_count y last_played_at
            self._touch_played(path)
        except Exception as e:
            self._last_error = str(e)
            log_event("err", f"Reproductor: {e}")

    def _spawn_volume_apply(self):
        """Aplica el volumen actual al stream una vez esté listo, en hilo."""
        import time as _t

        def _apply():
            for _ in range(20):  # hasta 2 s en total
                if self._safe_set_volume_now():
                    return
                _t.sleep(0.1)

        threading.Thread(target=_apply, daemon=True).start()

    def _touch_played(self, path: str):
        from datetime import datetime
        try:
            row = library.get_track_by_path(path)
            if row:
                pc = (row["play_count"] or 0) + 1
                library.upsert_track(
                    path,
                    play_count=pc,
                    last_played_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
        except Exception:
            pass

    def _auto_advance(self):
        """Llamado desde get_state cuando la pista termina. Respeta repeat
        y el sleep timer en modo 'tras pista actual'."""
        with self._lock:
            # Guarda: si ya estábamos parados, no avanzar.
            if self._index < 0:
                return
            # Si un crossfade está en curso, el swap ya está haciendo el
            # cambio: dejar que termine sin interferir.
            if self._crossfade_in_progress:
                return
            # Sleep timer "tras pista actual": parar aquí y limpiar flag.
            if self._sleep_stop_pending:
                log_event("info", "Sleep timer: fin de pista alcanzado, deteniendo")
                self._sleep_stop_pending = False
                self._sleep_after_track = False
                self._index = -1
                try:
                    if self._vlc_player is not None:
                        self._vlc_player.stop()
                except Exception:
                    pass
                return
            mode = self._repeat
            if mode == "track":
                log_event("info", "Auto-next: repetir pista actual")
                self._play_current()
                return
            if self._index + 1 < len(self._queue):
                self._index += 1
                log_event("info",
                          f"Auto-next: {self._index + 1}/{len(self._queue)}  (repeat={mode})")
                self._play_current()
            elif mode == "list" and self._queue:
                self._index = 0
                log_event("info", "Auto-next: vuelta al principio (repeat=list)")
                self._play_current()
            else:
                log_event("info", f"Fin de cola (repeat={mode})")
                self._index = -1
                # Forzar Stopped en VLC: si dejamos el state en Ended, el
                # polling siguiente volvería a entrar a _auto_advance y, con
                # index=-1, arrancaría la pista 0 (bug del bucle "repeat=list
                # implícito" reportado en K6).
                try:
                    if self._vlc_player is not None:
                        self._vlc_player.stop()
                except Exception:
                    pass

    def _warn_unavailable(self, action: str):
        log_event("warn",
                  f"Reproductor: no se puede {action} — VLC no disponible. "
                  "Instala VLC con tu gestor de paquetes (Fedora: sudo dnf install vlc).")


# Singleton exportado
player = _Player()


# ---------------------------------------------------------------------------
# Atajos de conveniencia para la GUI
# ---------------------------------------------------------------------------

def is_available() -> bool:
    return player.is_available()


def availability_message() -> str:
    if player.is_available():
        return ""
    return (
        "Reproductor inactivo: no se encontró VLC en el sistema. "
        "Instala VLC con tu gestor de paquetes y reinicia la app.\n"
        "  Fedora/Nobara : sudo dnf install vlc\n"
        "  Debian/Ubuntu : sudo apt install vlc\n"
        "  Arch/Manjaro  : sudo pacman -S vlc"
    )
