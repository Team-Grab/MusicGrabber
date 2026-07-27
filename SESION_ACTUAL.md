# Music Grabber — Sesión actual

Documento maestro de transferencia entre sesiones. Pegar entero al inicio de cualquier sesión nueva. Última actualización: **2026-05-27**.

---

## 0. Resumen ejecutivo (para arrancar nueva sesión)

App Python con GUI tkinter que descarga música de YouTube/YouTube Music, organiza la biblioteca local con MusicBrainz y la reproduce con un reproductor integrado VLC. Estado: **fases 1, 2a, 2b, 2c y 2c.4 validadas (bloques A-M). Fase 2c.5 (modo fiesta con BPM via librosa) reescrita el 27/05 como autoplay con autollenado tras feedback de uso; pendiente de validar en bloque N**. Siguiente: 2d (smart playlists + letras LRCLIB).

Si arrancas nueva sesión: lee este documento entero, lee `PLAN_DE_PRUEBAS.md` para saber qué está validado, y `Reporte-de-fallos.txt` para ver los bugs reportados hasta la fecha. Si vas a tocar código, lee también el `CHANGELOG.md` que tiene los detalles técnicos de cada fix.

---

## 1. Contexto

- **Repo proyecto**: https://github.com/Team-Grab/MusicGrabber (cuenta anónima Team-Grab, no vincular a maxisanturba).
- **Repo Cowork**: https://github.com/maxisanturba/claude-cowork (privado).
- **Carpeta de trabajo**: `~/Claude-Cowork/RESULTADOS/musicgrabber-linux/`. Esta carpeta ES el repo del proyecto.
- **Entorno del usuario**: Nobara Linux (Fedora 43). Python 3.14, VLC 3.0.23, pipewire, KDE/Wayland.
- **Hardware**: AMD Ryzen 5 7600X, RX 6750 XT, 32 GB RAM.

---

## 2. Decisiones tomadas (no replantear)

1. Un solo código base multiplataforma (Linux principal, Windows soportado, macOS experimental).
2. Dependencias híbridas: `yt-dlp` y `ffmpeg` del sistema si están en PATH, si no se descargan al `.bin/` privado.
3. Anonimato **Team-Grab**: todos los créditos públicos a "TeamGrab", nunca a maxisanturba.
4. Instaladores `install.sh` (Linux) e `install.ps1` (Windows). Sin PyInstaller ni Inno Setup.
5. GUI tkinter como única interfaz, con tema oscuro definido en la constante `DARK` de `ui/gui_app.py`.
6. Spotify fuera de plan. Bloqueado por política de Spotify desde 2024-2025. Si se retoma se hará desde cero.
7. MusicBrainz como único organizador en v2. Activable con toggle en Ajustes (default OFF).
8. Estructura canónica con MB: `Artista / Año — Álbum / Pista. Título.ext` (em-dash). Sin MB: `Artista / Álbum / Título.ext`.
9. Índice SQLite (`APP_DATA_DIR/library.db`) como única fuente de verdad de la biblioteca.
10. Reproductor: **python-vlc**. Si VLC no está instalado, la app sigue funcionando y los controles del reproductor se deshabilitan con aviso en el log.
11. Crossfade y modo fiesta aplazados a fase 2c.4/2c.5 o integrados con 2d (smart playlists), porque requieren extraer BPM de MusicBrainz y/o doble MediaPlayer.

---

## 3. Estado actual del código

```
musicgrabber-linux/
├── main.py
├── requirements.txt          (yt-dlp, musicbrainzngs, mutagen, python-vlc)
├── install.sh / install.ps1  (detectan VLC y avisan si falta)
├── README.md
├── CHANGELOG.md
├── SESION_ACTUAL.md          (este archivo)
├── PLAN_DE_PRUEBAS.md
├── Reporte-de-fallos.txt
├── notas.txt
├── musicgrabber_vision_v2.docx
├── assets/                   (logo.png, app.ico)
├── core/
│   ├── state.py              (AppState + load/save_config con muchos campos persistidos)
│   ├── bootstrap.py          (yt-dlp + ffmpeg híbrido)
│   ├── downloader.py         (cola de descargas → _inbox → pipeline)
│   ├── musicbrainz.py        (cliente MB + Cover Art Archive)
│   ├── library.py            (índice SQLite, scan, prune, bandeja revisión)
│   ├── pipeline.py           (process_new_download + enrich_existing_track)
│   └── player.py             (VLC + cola + shuffle int. + repeat + sleep + EQ)
└── ui/
    └── gui_app.py            (3900+ líneas: tema oscuro, sidebar, vistas,
                               reproductor inferior, diálogos)
```

### Módulos clave

**`core/state.py`** — `AppState` (dataclass) + funciones `load_config` / `save_config`. Campos persistidos en `config.json`:
- `library_path`, `audio_quality`, `audio_format`, `musicbrainz_enabled`
- `window_geometry`, `sort_column`, `sort_reverse`, `last_view`, `eq_preset`
- `event_log: deque` (no persistido, solo en memoria, máx 500)
- Función `log_event(level, msg)` para añadir al log visible.

**`core/player.py`** — Singleton `player` con backend `python-vlc`. API pública:
- Transporte: `play`, `pause`, `stop`, `next`, `prev`, `seek_to_fraction`, `set_volume`.
- Cola: `load_queue`, `add_to_queue`, `append_after_current`, `clear_queue`, `get_queue`, `move_in_queue`, `remove_from_queue`.
- Shuffle / repeat: `set_shuffle`, `set_repeat`, `cycle_repeat`. Shuffle inteligente (evita pistas seguidas del mismo artista).
- Sleep timer: `set_sleep_timer(seconds, after_track)`, `sleep_seconds_left`.
- Ecualizador: `eq_preset_names`, `set_equalizer_preset`, `set_equalizer_band`, `set_equalizer_preamp`, `reset_equalizer`, `get_equalizer_state`. **Usa funciones libvlc_* directas, NO `vlc.AudioEqualizer(idx)` que segfaultea.** Handle EQ pre-armado en `__init__` y aplicado a AMBOS players para evitar el bug "primer set_equalizer mid-stream silencia".
- Crossfade (2c.4): `set_crossfade(enabled, seconds)`, `get_crossfade()`. Dos `media_player` con swap atómico tras rampa lineal en hilo. Polling decide cuándo arrancar via `_maybe_start_crossfade`. Cancelado por next/prev/pause/stop/seek/clear/load/remove/move.
- Modo fiesta (2c.5 v2 reescrito 27/05): `set_party_mode(enabled, min_bpm=None, max_bpm=None, party_crossfade_s=None) -> pool_size`, `get_party_mode() -> (enabled, min, max)`. Autoplay puro: vacía la cola, mete semilla de 3 pistas aleatorias del rango BPM, autollena 2 por delante en cada poll vía `_maybe_refill_party_queue`. Pool fresco en `_party_played: set`; reinicia al agotarse. Shuffle inteligente al picar (evita repetir artista de la última pista). Al desactivar: vacía cola, stop, restaura crossfade. `load_queue()` desactiva la fiesta automáticamente.
- Snapshot: `get_state() -> PlayerState`.

**`core/bpm.py` (2c.5)** — Wrapper de librosa. `is_available()`, `availability_message()`, `compute_bpm(path, timeout_s=60)`. Carga perezosa de librosa con try/except: si falla, módulo en modo "no disponible". Análisis de 90 s de audio a 22050 Hz mono (saltando 15 s iniciales). Timeout en hilo. Devuelve float o None.

**`ui/gui_app.py`** — Una sola clase principal `MusicGrabberGUI(tk.Tk)`. Sidebar tipo Treeview con secciones VISTAS / GÉNEROS. Tres vistas conmutables (Descargar / Biblioteca / Sin metadatos). Barra inferior con reproductor completo. Diálogos: `WelcomeDialog`, `SettingsDialog`, `ManualMBSearchDialog`, `ManualTagsDialog`, `EnrichProgressDialog`, `QueueDialog`, `SleepTimerDialog`, `EqualizerDialog`. Todos los Toplevel usan `_dark_toplevel(self)` y siguen el patrón "widgets en `_build()` separado, `grab_set` al final".

---

## 4. Funcionalidades validadas en uso real

Bloques A-K marcados con `[x]` en `PLAN_DE_PRUEBAS.md`.

- **A-I (fase 1)**: setup de bienvenida, descarga con/sin MB, biblioteca con ordenación + filtros (por texto sin acentos, por género desde sidebar, "Limpiar filtros"), menú contextual con 9+ entradas, atajos F5/F2/Enter/Del/Ctrl+D/L/R, MusicBrainz on/off con indicador en barra, enriquecimiento masivo con diálogo de progreso, bandeja de revisión con búsqueda manual + edición manual + candidatos, persistencia de geometría/columnas/vista activa, prune automático del índice al arrancar, "Vaciar índice" como reset duro.
- **J (fase 2a)**: reproductor VLC con cola, transporte completo, slider con click + drag + debounce, doble clic carga vista filtrada como cola, menú contextual con cola, auto-advance, `play_count` persistido.
- **K (fase 2b)**: botones "Mezclar", "Repetir" (off/lista/pista), "Cola" en la barra. Panel de cola con drag & drop preview en vivo. Resumen de lote al terminar descargas.

---

## 5. Pendiente de validar

**Bloque N del plan de pruebas (reescrito 27/05)** — entero. Cubre fase 2c.5 v2:
- N1: migración suave de la columna `bpm` en DB legacy.
- N2: cálculo automático en el pipeline de descarga.
- N3: botón "Calcular BPM" masivo con modal de progreso.
- N4: modo fiesta como autoplay (activar sin cola previa, autollenado, shuffle inteligente, pool fresco, crossfade forzado, salida con stop, doble clic en biblioteca desactiva fiesta).
- N5: range slider con dos thumbs + 4 presets (Chill, Pop-Rock, Bailable, Cardio).
- N6: columna BPM visible en la biblioteca con sort numérico.

Requiere `pip install librosa` (ya en `requirements.txt`, `install.sh` lo recoge).

Bloques A-M validados.

Detalles técnicos en `CHANGELOG.md` (todas las fases, en particular "Fase 2c.5 v2 + fix").

---

## 6. Plan v2.0 — fases siguientes

- **2c.4** — ✅ Implementado el 24/05 y validado el 25/05 (bloque M).
- **2c.5** — ✅ Implementado el 25/05 (pendiente de validar en bloque N). Modo fiesta con BPM calculado en local con librosa. AcousticBrainz cerró en 2022, así que el BPM se calcula al descargar + bajo demanda con botón masivo.
- **2d** — Smart playlists: top mensual, recientes, no escuchadas en X meses, BPM alto para entrenamiento, mezcla de géneros favoritos, "hoy hace X años". Reglas combinables. Letras LRCLIB sincronizadas.
- **2e** — Scrobbling Last.fm opcional.
- **3+** — Servidor HTTP + Android. Sync offline. Acceso remoto. Android Auto.

---

## 7. Lecciones aprendidas (no repetir errores)

- **VLC + pipewire**: `libvlc_audio_set_volume` revienta si se llama sin stream activo. Solución: aplicar solo tras `play()` con `_safe_set_volume_now()` que comprueba `get_state()`. **NO repetir en crossfade.**
- **`vlc.AudioEqualizer(idx)`**: el constructor del wrapper Python con un `int` provoca segfault en VLC 3.0.23. **Usar siempre `libvlc_audio_equalizer_*` directos.**
- **Preamp de VLC no es ganancia**: es compensación interna. Flat tiene +12, no 0. Cada preset trae su propio preamp. Resetear a 0 silencia el audio.
- **Tkinter `<<TreeviewSelect>>` es async**: `selection_set()` programático no se atrapa con flag síncrono. Usar `after_idle` para limpiar el flag.
- **`grab_set` antes de los widgets** bloquea el render del Toplevel. Patrón correcto: widgets en `_build()` → `update_idletasks()` → `_center_on` → `grab_set` al final.
- **`ttk.Scale` click default** salta por "page increments". Para "jump to click": bindear Button-1 + B1-Motion + ButtonRelease-1, devolver `"break"`, hacer seek una sola vez en release. Debounce de 80 ms para evitar saturar VLC con clicks rápidos.
- **VLC `State.Ended` persiste** hasta que llamas `stop()`. Si no lo llamas, el polling vuelve a entrar a `_auto_advance` con `index=-1` y arranca la pista 0 → bucle. Solución: `stop()` tras poner `index=-1`.
- **`libvlc_media_player_set_equalizer` mid-stream silencia la primera vez**: en VLC 3.0.23 sobre pipewire, si el media_player nunca recibió un set_equalizer ANTES del primer play, el primer set_equalizer llamado con audio ya sonando silencia el bus de audio hasta reiniciar la app. Solución: pre-armar la pipeline en `__init__` con `libvlc_audio_equalizer_new()` (handle Flat vacío) aplicado a ambos `media_player` antes del primer play. Reaplicar tras cada `set_media`. Al cambiar de preset, aplicar el handle nuevo ANTES de liberar el viejo (no dejar al player sin equalizer).
- **Crossfade: aplicar EQ a ambos `media_player`**. Si solo se aplica al activo, tras un swap del fade el sonido entrante puede salir sin ecualizar o (peor) con silencio momentáneo. Los métodos `set_equalizer_preset/band/preamp` aplican al activo Y al "otro" bajo `_lock`.
- **AcousticBrainz cerró en 2022**: ya no se puede obtener BPM/key/mood gratis vía web service. Para BPM toca calcularlo en local con librosa (o aubio). librosa funciona bien en Py3.14, pesa ~80 MB con deps. Cargar perezosamente para no penalizar el arranque de la app si no se usa.
- **Modo fiesta NO restaura la cola al salir**: filtrar la cola al activar es destructivo, pero restaurar al desactivar sería más disruptivo aún (cambiaría las pistas que vienen, podría revivir pistas que ya sonaron). Decisión: la cola filtrada se queda; solo se restaura el crossfade previo.
- **`libvlc_media_player_set_equalizer` aplicado en ambos players incluso sin crossfade**: cuesta lo mismo y elimina dependencias temporales entre features. Vale para el patrón general "todos los cambios de player se aplican a ambos slots".
- **Bucle de `save_config`**: si `_switch_view` se llama desde el handler de `<<TreeviewSelect>>` que se disparó por `selection_set` programático, se encadena. Solución: early-return si la vista no cambia + flag con `after_idle`.
- **Emojis Unicode** (🔀 🔁 📋) requieren fuente con soporte de emoji en la cadena de fallback tkinter. En Fedora vainilla no aparecen. **Usar texto** (" Mezclar ", " Repetir ", " Cola "). Los símbolos Misc Technical (⏮ ▶ ⏸ ⏭ ■) sí están en fuentes default.
- **Polling del portapapeles** cada 2 s provoca parpadeos en KDE/Wayland Nobara. Eliminado del todo; Ctrl+V manual sigue funcionando.

---

## 8. Cómo probar en Nobara

```bash
rm -rf ~/MusicGrabber-test
cp -r ~/Claude-Cowork/RESULTADOS/musicgrabber-linux ~/MusicGrabber-test
cd ~/MusicGrabber-test && bash install.sh
musicgrabber
```

Para forzar primer arranque limpio:

```bash
rm -f ~/.local/share/MusicGrabber/config.json
rm -f ~/.local/share/MusicGrabber/library.db
rm -rf ~/.local/share/MusicGrabber/_inbox
```

VLC es necesario para el reproductor integrado:

```bash
sudo dnf install vlc
```

---

## 9. Cómo continuar en una sesión nueva

1. **Lee este documento entero.**
2. **Lee `PLAN_DE_PRUEBAS.md`** para saber qué bloques están validados (`[x]`) y cuáles pendientes.
3. **Lee `Reporte-de-fallos.txt`** para ver el historial completo de bugs reportados y el contexto en que aparecieron.
4. **No leas `RESULTADOS/` enteros ni `PLANTILLAS/`** salvo que el usuario lo pida (sigue las reglas de `CLAUDE.md` y `SOBRE-MI/sobre-mi.md`).
5. Si vas a tocar código, **lee `CHANGELOG.md`** con los detalles técnicos por fase.
6. **No reabras decisiones cerradas** (sección 2 de este documento).
7. **El usuario es Maxi**, en Valencia. No es programador. Quiere directo, sin preámbulos, sin adulación, sin emojis decorativos. Prosa, no listas excepto pasos o comandos. Si el brief es ambiguo, pregunta. Detalles en `~/Claude-Cowork/SOBRE-MI/sobre-mi.md`.

### Siguiente paso prioritario

Validar el bloque N (2c.5). Si pasa:
- Pasar a 2d (smart playlists + letras LRCLIB). Aprovecha play_count + BPM ya disponibles en la DB.

Si N falla, anotar en `Reporte-de-fallos.txt` y arreglar antes de seguir.
