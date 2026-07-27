# Changelog — Music Grabber

## v2.0-dev — Fase 2c.5 v2 + fix (2026-05-27)

### Reescritura del modo fiesta (autoplay con pool fresco)

La versión 1 del modo fiesta filtraba la cola actual. Tras feedback de uso,
se rediseña como autoplay puro: el botón "Fiesta" toma toda la biblioteca,
escoge un rango de BPM y reproduce indefinidamente con shuffle inteligente.

- **`core/player.py`**: `set_party_mode(enabled, min_bpm, max_bpm, party_crossfade_s) -> pool_size`. Al activar: vacía la cola, mete una semilla de 3 pistas aleatorias del rango y arranca. `_maybe_refill_party_queue()` hook en el poll: mientras `len(_queue) - _index - 1 < 2`, añade otra pista. Pool fresco vía `_party_played: set` que registra paths ya emitidos; cuando se agota, se reinicia (log "pool agotado, reiniciando rotación"). Shuffle inteligente integrado: cada pick evita repetir el artista de la última pista de la cola. Al desactivar: vacía cola + `stop()` + restaura crossfade previo + limpia `_party_played`.
- **`load_queue`**: si el modo fiesta está activo y el usuario hace doble clic en una pista, se desactiva la fiesta automáticamente (intención clara distinta). Se restaura el crossfade previo sin pasar por la rama de vaciar cola.
- **`core/library.py`**: helper `tracks_in_bpm_range(min, max)` (filas con `bpm IS NOT NULL AND bpm BETWEEN ...`).
- **`core/state.py`**: `party_max_bpm: int = 140` nuevo. Rango total 60–200 (antes era 80–200, solo mínimo). Compatibilidad con configs viejas: si solo está `party_min_bpm`, default `party_max_bpm = min + 30` (clamped). Sanity-check `min <= max` en save_config.
- **`PlayerState`**: añade `party_max_bpm`. `get_party_mode() -> (enabled, min, max)`.

### UI

- **Range slider custom (`RangeSlider`)**: widget sobre Canvas con dos thumbs draggable. Track gris + segmento activo entre thumbs. `get()`, `set(min, max)`, callback `on_change(min, max)` en vivo. tkinter no trae range slider nativo; ~80 líneas.
- **`SettingsDialog`**: sección "Modo fiesta" reescrita. Botones preset: Chill (70–100), Pop-Rock (100–130), Bailable (120–145), Cardio (140–180). Cada preset reposiciona los thumbs del range slider. Slider "Crossfade en fiesta" se mantiene.
- **Botón "Fiesta"**: label cambia a `"Fiesta 110–140"` cuando está activo. El handler pasa `min_bpm` y `max_bpm` al player. Si pool=0, aviso con sugerencia de calcular BPM o cambiar el rango.

### Fix (24/05/2026, reportado el 27)

- **Columna BPM no aparecía en la biblioteca**: añadida al treeview con ancho 55px, alineación center, ordenación numérica. Valor vacío para pistas con `bpm=NULL`. En el sort se usa una clave numérica separada (`bpm_v`) para que NULL queden ordenadas al final (-1.0) en asc y al principio en desc.

---

## v2.0-dev — Fase 2c.5 (2026-05-25)

### Modo fiesta (BPM + crossfade obligatorio)

- **`core/bpm.py` nuevo**: backend principal librosa, cargado perezosamente. Si la importación falla, módulo en modo "no disponible" (`is_available()` → False) sin romper la app. `compute_bpm(path, timeout_s=60)` carga 90 s de audio a 22050 Hz mono (saltando 15 s iniciales para evitar intros silenciosos) y usa `librosa.beat.beat_track`. Devuelve un float redondeado o None. Timeout en hilo para evitar bloqueos en archivos corruptos. Arquitectura preparada para fallback a aubio sin tocar los callers.
- **`core/library.py`**: columna `bpm REAL` en `tracks`, migración suave con `ALTER TABLE` idempotente (lee `PRAGMA table_info` antes de añadir). Índice `idx_bpm`. Helper `tracks_without_bpm()`. `upsert_track(bpm=...)` válido.
- **`core/pipeline.py`**: `_compute_bpm_safe(path)` se llama tras mover el archivo al destino final en los tres caminos (sin MB / MB auto / MB ambiguo). Si librosa no está, devuelve None silenciosamente; si está y falla, log warn y la pista entra con `bpm=NULL` (recuperable con el botón masivo).
- **Botón "Calcular BPM" en barra superior**: junto a "Enriquecer con MusicBrainz". Pre-check con `bpm.is_available()` y mensaje si no. Confirmación con número de pistas y tiempo estimado (~3 s/pista). Modal de progreso reutilizando `EnrichProgressDialog` parametrizado (`window_title`, `heading`, `subheading`, `finish_template`). Procesado en hilo, cancelable.
- **Modo fiesta en `core/player.py`**: `set_party_mode(enabled, min_bpm=None, party_crossfade_s=None) -> (kept, filtered)`. Al activar: guarda config de crossfade actual en `_party_pre_crossfade`, fuerza crossfade ON con duración festiva, filtra `_queue` dejando solo pistas con `bpm >= min_bpm` (saltando pistas con `bpm=NULL`). Si la pista actual no cumple, salta a la primera del subset. Al desactivar: restaura crossfade previo (la cola NO se restaura, sería disruptivo). `PlayerState` expone `party_enabled` y `party_min_bpm`.
- **Botón "Fiesta" en barra inferior**: junto a "EQ". Toggle. Cuando ON, label "Fiesta ≥110" en color destacado. Si al activar no hay pistas que cumplan el umbral, mensaje sugiriendo calcular BPM o bajar el umbral en Ajustes, y se deshace.
- **Ajustes**: secciones nuevas en `SettingsDialog`. Slider "BPM mínimo" (80-200) y slider "Crossfade en fiesta" (1-12 s). Persistidos en `config.json` (`party_min_bpm`, `party_crossfade_s`). Si el modo fiesta está activo al guardar Ajustes, el cambio de crossfade general no se aplica al player (lo gestiona party_mode).
- **`requirements.txt`**: añadido `librosa>=0.10.0`.

---

## v2.0-dev — Pulido (2026-05-25)

- **Botón MB: ON/OFF**: click ahora hace toggle directo del ajuste `musicbrainz_enabled` sin abrir el diálogo Ajustes. El botón Ajustes sigue funcionando para ese y el resto de opciones. Razón: con el botón Ajustes ya cubriendo lo mismo, abrir el diálogo desde el indicador era redundante.
- **Bloque M validado** en uso real: fix EQ + crossfade 2c.4 pasan todas las pruebas.

---

## v2.0-dev — Fase 2c.4 (2026-05-24)

### Crossfade entre pistas

- **`core/player.py`**: dos `media_player` (`_vlc_player_a` y `_vlc_player_b`). `self._vlc_player` es un pointer al activo y rota con cada swap del fade. API nueva: `set_crossfade(enabled, seconds)`, `get_crossfade()`. Helpers `_other_player_locked` / `_both_players_locked`.
- **Polling**: `get_state()` llama a `_maybe_start_crossfade()` cuando el estado es "playing". Si el `remaining_s` cae por debajo de `crossfade_seconds` (con margen 0.5 s) y aún no hay fade en curso, arranca el siguiente en el secundario a volumen 0 y lanza `_fade_loop` en hilo. Rampa lineal a ~20 pasos/s. Al terminar, swap atómico bajo `_lock` y `_touch_played` de la nueva pista.
- **Guards**: no se hace crossfade si `repeat=track`, si el sleep timer "tras pista" está pendiente, si la pista actual dura menos de 1.5× el crossfade, o si no hay siguiente pista.
- **Cancelación**: `next`, `prev`, `stop`, `pause`, `seek_to_fraction`, `clear_queue`, `load_queue`, `remove_from_queue` (sobre la actual o la siguiente) y `move_in_queue` (sobre la siguiente) llaman a `_cancel_crossfade()` para evitar inconsistencias. `set_volume` durante un fade no machaca la rampa.
- **EQ + crossfade**: el EQ se aplica a ambos `media_player` en `set_equalizer_preset` / `set_equalizer_band` / `set_equalizer_preamp`. Garantiza que tras un swap el sonido siga ecualizado sin ventana de silencio.
- **Persistencia**: `crossfade_enabled: bool` y `crossfade_seconds: int (1-12)` en `config.json`. Restaurados en `_init_runtime` con `player.set_crossfade(...)`.
- **GUI**: `SettingsDialog` añade checkbox "Fundido encadenado entre pistas (crossfade)" + slider 1–12 s con label dinámica. El slider se deshabilita si el checkbox está OFF. Al guardar, aplica al player en caliente.

### Fix (24/05/2026)

- **L3 — EQ silencia en primer cambio (primer arranque limpio)**: el media_player de VLC arrancaba sin equalizer asociado. El primer `libvlc_media_player_set_equalizer()` llamado mid-stream en VLC 3.0.23 sobre pipewire silenciaba el bus de audio hasta reiniciar la app. Tras reinicio, `config.json` traía el preset persistido y `_init_runtime` lo aplicaba ANTES del primer `_play_current()`, así que ya no era un cambio mid-stream y todo funcionaba.
- **Solución**: pre-armar la pipeline de EQ en `_Player.__init__` aplicando un handle Flat vacío a ambos `media_player` antes de cualquier reproducción. Reaplicar el handle tras cada `set_media` en `_play_current` como defensa. En `set_equalizer_preset` aplicar el handle nuevo ANTES de liberar el viejo (sin ventana sin equalizer) y aplicar a ambos players. `_init_runtime` ahora aplica el preset persistido sin la condición `!= "Flat"`.

---

## v2.0-dev — Fase 2c (2026-05-21)

### Shuffle inteligente, sleep timer, ecualizador

- **2c.1 — Shuffle inteligente** (`core/player.py`): `_smart_shuffle()` greedy con restricción "no dos pistas seguidas del mismo artista". Precachea artistas por path para no consultar la DB N veces. Si no hay alternativa (todas las restantes son del mismo artista), acepta la repetición.
- **2c.2 — Sleep timer**: `set_sleep_timer(seconds, after_track)` con dos modos. Modo cronométrico usa `threading.Timer`; modo "tras pista actual" usa flag `_sleep_stop_pending` consumido en `_auto_advance`. `SleepTimerDialog` con radios + input + Iniciar/Cancelar. Indicador en la barra: "Timer" / "Sleep 12:34" / "Sleep tras pista" según estado.
- **2c.3 — Ecualizador 10 bandas**: 18 presets de VLC (Flat, Rock, Pop, Classical, Bass, Treble, Headphones, Party, Reggae, etc.), preamp -20 a +20 dB, sliders por banda (60 Hz – 16 kHz). `EqualizerDialog` con dropdown + sliders verticales + Reset. Preset persistido en `config.json` (campo nuevo `eq_preset`), restaurado al arrancar.

### Fixes (21/05/2026, pendientes de validar en uso)

- **L3.2 — Crash al aplicar preset EQ**: `vlc.AudioEqualizer(idx)` provoca segfault en VLC 3.0.23. Sustituido por funciones libvlc directas: `libvlc_audio_equalizer_new_from_preset`, `libvlc_audio_equalizer_get_preamp`, `libvlc_audio_equalizer_get_amp_at_index`, `libvlc_audio_equalizer_set_amp_at_index`, `libvlc_audio_equalizer_set_preamp`, `libvlc_media_player_set_equalizer`. Handle gestionado en `self._eq_handle` y liberado con `libvlc_audio_equalizer_release` antes de crear uno nuevo. Los 18 presets se aplican en secuencia sin crash.
- **L3 — Preamp silenciaba**: yo inicializaba `_eq_preamp = 12.0` esperando que fuera "amplificación", pero el preamp de VLC es compensatorio interno; 0 dB causa atenuación. Default bajado a 0.0 (se sobreescribirá con el valor del preset elegido). `reset_equalizer` ahora delega en `set_equalizer_preset("Flat")` que respeta los +12 dB internos que VLC pone para Flat.
- **L2 (Sleep timer) y MB:OFF (Settings) vacíos**: Toplevels que abrían sin contenido. Causa: orden de construcción. `grab_set` antes de crear los widgets bloqueaba el render en algunas combinaciones tcl/tk. Solución: widgets en `_build()` separado, `update_idletasks()` para forzar layout, `grab_set` al final. Además `SleepTimerDialog` ya no llama a `player.get_state()` durante `__init__` (eso atravesaba a VLC y podía bloquear); lee `player._sleep_after_track` cached directamente. Mismo tratamiento a `SettingsDialog` y `EqualizerDialog`. Cada `_build()` envuelto en `try/except` con `traceback.print_exc()` para que futuras excepciones silenciosas se vean en consola en lugar de en un diálogo vacío.
- **L3.5 — Persistencia EQ**: era consecuencia del L3.2 (la app crasheaba antes de guardar). Con el crash arreglado, `save_config(eq_preset=...)` se llama en cada cambio y `_after_bootstrap` restaura el preset al arrancar.

---

## v2.0-dev — Fase 2b (2026-05-19)

### Shuffle, repeat, panel de cola con drag & drop

- **Backend (`core/player.py`)**: `set_shuffle`, `set_repeat`, `cycle_repeat`, `_shuffle_remaining_locked`, `_restore_order_locked`. `_auto_advance` respeta `repeat=track/list/off`. Métodos de gestión de cola: `get_queue`, `move_in_queue`, `remove_from_queue`. `PlayerState` ahora expone `shuffle` y `repeat`.
- **GUI**: botones "Mezclar", "Repetir" y "Cola" en la barra inferior. Estado visible por color y por texto ("Repetir lista" / "Repetir pista").
- **QueueDialog**: ventana flotante con tabla de cola, drag & drop con preview en vivo, ↑/↓/Quitar/Reproducir aquí/Vaciar. Auto-refresco cada 800 ms.

### Fixes
- **K6 — Bucle infinito al final con repeat=off**: `_auto_advance` no detenía VLC al poner `index=-1`. El polling siguiente veía `State.Ended` y entraba de nuevo, avanzando a `index=0`. Solución: `vlc.stop()` tras poner `index=-1` + guarda `if self._index < 0: return` al inicio de `_auto_advance`.
- **K7 — QueueDialog truncado**: geometría inicial subida a 620x580, `minsize(560, 460)`.
- **K9 — Drag sin feedback**: cursor `fleur`, tag `dragging` con fondo distinto, preview en vivo (la pista se mueve en la cola conforme el ratón pasa por filas).
- **Race save_config**: `_switch_view(initial)` en `__init__` disparaba un bucle entre `selection_set` async y `_on_sidebar_select`. Solución: early-return si la vista no cambia + flag `_suppress_sidebar_event` limpiado con `after_idle`.

---

## v2.0-dev — Fase 2a (2026-05-17)

### Reproductor integrado VLC

- **`core/player.py`** nuevo: singleton con `python-vlc`. Cola en memoria, transporte completo (play/pause/stop/next/prev/seek/volume), `PlayerState` para la GUI. Si VLC no está instalado, el módulo se carga y los controles se deshabilitan con aviso en el log.
- **Barra inferior**: carátula placeholder ♪, título + artista, controles ⏮ ▶ ⏭ ■, slider de progreso con tiempos `M:SS`, control de volumen. Polling cada 300 ms.
- **Integración con biblioteca**: doble clic carga la vista actual filtrada como cola. Menú contextual extendido con "Añadir al final de la cola", "Reproducir a continuación", "Abrir con reproductor externo".
- **play_count y last_played_at**: cada reproducción actualiza estos campos en la DB para usarlos en fase 2d (smart playlists).
- **`requirements.txt`**: añadido `python-vlc>=3.0.0`.
- **`install.sh`**: detecta VLC en el sistema y avisa con el comando de instalación según distro si falta.

### Fixes en 2a
- **Segfault VLC + pipewire**: `libvlc_audio_set_volume` revienta si se llama sin stream activo. Solución: nunca llamar a `audio_set_volume` antes del primer `play()`. `_safe_set_volume_now()` comprueba `get_state()` y solo aplica si está en Playing/Paused/Buffering. `_spawn_volume_apply()` reintenta tras `play()` durante 2 s.
- **J7/J8 — Sliders**: click + drag funcionando, debounce de 80 ms en el seek para evitar saturar VLC con clicks rápidos.
- **J9 — Filtros**: click en género limpia el filtro de texto. Búsqueda ignora acentos vía `unicodedata.normalize("NFKD")`.
- **Bloque K — Emojis**: 🔀 🔁 📋 sustituidos por texto "Mezclar"/"Repetir"/"Cola" porque las fuentes default de tkinter en Fedora no traen glyphs para esos emojis.

### Pulido v2.0-dev
- Persistencia de geometría de ventana, orden de columnas activo, vista activa al cerrar.
- Atajos de teclado globales: F5, F2, Enter, Del, Ctrl+D/L/R.
- Indicador permanente "MB: ON/OFF" en la barra superior (clicable → Ajustes).
- Tema oscuro completo: ttk.Style + `option_add` para widgets vanilla. `_dark_toplevel()` para Toplevels (sin esto los diálogos salían en blanco).
- Setup de bienvenida (WelcomeDialog) en primer arranque con carpeta, formato, calidad y toggle MB.
- Resumen de lote al terminar la cola con tiempo, descargas, duplicados, fallos.
- Menú contextual en biblioteca con 7 entradas.
- Ordenación por columnas (Título, Artista, Álbum, Año, MB, Duración) con flecha ▲/▼.
- Auto-purga de entradas obsoletas del índice al arrancar (con guarda si la raíz no existe).
- Búsqueda con tolerancia a acentos y limpieza de filtros con un botón.

---

## v2.0-dev — Fase 1 (2026-05-15)

### Pipeline MusicBrainz + biblioteca + bandeja de revisión

- **Modos de orquestación eliminados**. Los 6 modos manuales del v1.x (Álbum, Recopilatorio, Playlist, Mix, Discografía, Huérfano) y `generate_m3u8` desaparecen. MusicBrainz es ahora el único organizador.
- **Nuevo módulo `core/musicbrainz.py`**: cliente con rate limit 1 req/s y User-Agent identificable. Búsqueda por título+artista con filtrado por duración (±5 s), fallback por ISRC. Descarga de carátulas desde Cover Art Archive. `MatchResult` con umbrales `is_auto` (≥90) / `is_ambiguous` (70-89) / `is_unreliable` (<70).
- **Nuevo módulo `core/library.py`**: índice SQLite en `APP_DATA_DIR/library.db`. Tablas `tracks` y `pending_review`. Función `scan(library_root)` que indexa archivos `.mp3 .flac .ogg .oga .m4a .opus .wav` sin moverlos, respetando MusicBrainz IDs presentes en tags (TXXX, Vorbis).
- **Nuevo módulo `core/pipeline.py`**: `process_new_download()` aplica tags base con mutagen, consulta MB, mueve a `{artist}/{year} — {album}/{track:02d}. {title}.{ext}` si el match es claro; los matches ambiguos van a `{biblioteca}/_inbox_review/` y a la bandeja. `enrich_existing_track()` enriquece pistas sin Recording ID sin mover el archivo.
- **Refactor `core/downloader.py`**: yt-dlp baja a `APP_DATA_DIR/_inbox/`. El postprocessor_hook apila el path; al final del `download()`, `_process_pipeline_batch` envía cada archivo al pipeline. Se eliminan `_get_ydl_opts(mode, ...)`, `generate_m3u8` y el rollback contra ledger. `Library_Ledger.log` y `.historial_descargas.txt` ya no se generan (sustituidos por el índice SQLite).
- **Reescritura completa de `ui/gui_app.py`**: layout con sidebar (Descargar / Biblioteca / Sin metadatos). Vista Descargar minimalista. Vista Biblioteca con `ttk.Treeview` y filtro en tiempo real. Vista Sin metadatos con la bandeja y la tabla de candidatos. Botones "Escanear biblioteca" y "Enriquecer con MusicBrainz". Doble clic en pista o "Reproducir selección" lanza el archivo con `xdg-open` / `start` / `open`.
- **`requirements.txt`**: añadidos `musicbrainzngs>=0.7.1` y `mutagen>=1.47.0`.

---

## v2.0-dev — Limpieza previa (2026-05-15)

- **TUI Textual descartada**. Se elimina `ui/textual_app.py`, el flag `--tui` de `main.py` y la dependencia `textual` de `requirements.txt`. La app arranca directamente en GUI tkinter.
- **Integración spotDL (Spotify) eliminada**. Spotify bloqueó el acceso a su Web API para cuentas gratuitas en 2024-2025, lo que dejó la integración inservible en la práctica. Se quitan los campos `spotify_client_id` y `spotify_client_secret` de `state.py`, las funciones `is_spotify_url`, `_extract_spotify_id`, `_download_with_spotdl` de `downloader.py`, la rama Spotify de `_worker_loop`, las funciones `_resolve_spotdl`, `get_spotdl_path`, `is_spotdl_available` de `bootstrap.py`, y `spotdl` de `requirements.txt`.
- **Resúmenes de sesión consolidados** en un único `SESION_ACTUAL.md`.

---

## v1.1.1 (2026-05-14)

### Bugs corregidos
- **Clipboard Windows**: reemplazado subprocess PowerShell por `ctypes` — elimina el lag de 300-800ms que se acumulaba cada 2 segundos.
- **Clipboard Linux**: eliminado fallback `xdotool getactivewindow` que devolvía el ID de la ventana activa en lugar del contenido del portapapeles.
- **Ledger e historial escritos antes de que ffmpeg terminase**: movida la escritura de `Library_Ledger.log` y `.historial_descargas.txt` a `_postprocessor_hook` — ahora solo se registra cuando el postprocesado está confirmado. Evita entradas huérfanas que bloqueaban la re-descarga si ffmpeg fallaba a mitad.
- **Cleanup agresivo en biblioteca**: el `rglob` del finally de `_worker_loop` borraba `.webp`, `.vtt` y `.srt` en toda la biblioteca del usuario. Ahora solo borra `.part` y `.ytdl` (inequívocamente temporales). Mismo fix en `load_queue_from_disk` (antes borraba también `.webm` y `.m4a`).
- **BootScreen invisible en ventana pequeña**: añadido `min-height: 6` al `#boot_log` y `min-height: 22` al contenedor para garantizar que el log de bootstrap siempre sea visible.
- **Versión desincronizada** en distintas pantallas. Corregido en los tres lugares.
- **robocopy en `install.ps1`**: añadida verificación de `$LASTEXITCODE >= 8` (códigos 0-7 son éxito parcial en robocopy, no errores).
- **Icono del acceso directo en `install.ps1`**: buscaba `assets\logo.ico` pero el archivo es `assets\app.ico`.

### Mejoras
- **Caché de historial en memoria**: `_clean_metadata` ya no lee `.historial_descargas.txt` una vez por pista. El historial se carga en memoria al inicio de cada tarea (`state.history_cache`) y se actualiza en tiempo real. Mejora notable en playlists grandes.
- **Selects con valor por defecto**: Modo 3 (Playlist) y Speed 2 (Seguro) preseleccionados al arrancar.
- **Validación de URL**: `procesar_input` ahora rechaza entradas que no sean URLs de YouTube antes de encolarlas.
- **Acceso directo Windows usa Windows Terminal** si está disponible (`wt.exe`), con fallback a `cmd.exe`. Mejor renderizado.
- **`install.sh`**: rsync excluye `notas.txt` para no copiar archivos de trabajo al directorio de instalación.

---

## v1.1.0 (2026-05-14)

### Nuevas features
- **Formato y calidad configurables**: MP3 128/192/256/320 kbps, FLAC sin pérdida, OGG Vorbis. Se persiste en `config.json`.
- **Visor de historial** integrado: muestra las últimas 200 entradas del `Library_Ledger.log`.
- **Detección de portapapeles**: si se copia una URL de YouTube/YouTube Music con el campo de entrada vacío, se pega automáticamente (Linux: `wl-paste`/`xclip`; Windows: PowerShell).
- **Notificaciones de escritorio** al terminar un batch: Linux via `notify-send`; Windows via balloon tooltip (PowerShell, sin dependencias extra).
- **Failures_Log.txt**: cada pista fallida queda registrada con timestamp, URL e ID de vídeo.

### Mejoras
- **Estadísticas de sesión ampliadas**: timer acumulado de sesión, contador de cola pendiente y tiempo de la última tarea.
- **Detección de descarga colgada**: aviso visual si una pista lleva más de 5 minutos sin progresar.
- **`socket_timeout: 30`** en yt-dlp para evitar cuelgues de red indefinidos.

### Bugs corregidos
- `state.cancel_requested` en `_clean_metadata` y `_progress_hook` (antes apuntaba erróneamente a `APP_DATA_DIR`, lo que impedía cancelar descargas en ciertos puntos del ciclo).
- `global_stats["failed"]` ahora se incrementa por cada pista fallida individualmente, no solo ante fallos críticos del sistema.
- `os.getlogin()` reemplazado por fallback seguro (`USER` / `LOGNAME` / `Path.home().name`) — evitaba crash en algunas terminales y sesiones SSH.
- Extensión de archivo en rutas M3U8 ahora refleja el formato configurado.

---

## v1.0.0 (2026-05) — Adaptación Linux

Partiendo de la versión original Windows-only:

### Añadido
- `core/state.py`: `APP_DATA_DIR` resuelto por SO (Linux: `$XDG_DATA_HOME/MusicGrabber`; Windows: `%LOCALAPPDATA%\MusicGrabber`; macOS: `~/Library/Application Support/MusicGrabber`).
- `core/bootstrap.py`: lógica híbrida — usa binarios del sistema si están en `$PATH`; si no, los descarga a `.bin/` privado. Auto-update de yt-dlp standalone. URLs BtbN para ffmpeg en Linux y Windows.
- `core/downloader.py`: `get_ffmpeg_path()` en lugar de ruta Windows hardcodeada.
- `ui/`: `_open_in_file_manager()` cross-platform; `get_drives()` con detección de carpeta música locale-agnostic y puntos de montaje Linux (`/run/media`, `/media`, `/mnt`).
- `install.sh`: instalador Linux genérico (Python ≥ 3.9, venv, lanzador en `~/.local/bin`, entrada `.desktop`, soporte `--uninstall`).
- `install.ps1`: instalador Windows (Python via winget, venv, lanzador en `WindowsApps`, acceso directo Menú Inicio, soporte `-Uninstall`).
- `requirements.txt`.

### Eliminado
- `innoSetupScript.iss` y `MusicGrabber_Setup_v1.0.0.exe` (sustituidos por los scripts de instalación).
