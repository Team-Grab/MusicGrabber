# Changelog — Music Grabber

## v1.1.1 (2026-05-14)

### Bugs corregidos
- **Clipboard Windows**: reemplazado subprocess PowerShell por `ctypes` — elimina el lag de 300-800ms que se acumulaba cada 2 segundos.
- **Clipboard Linux**: eliminado fallback `xdotool getactivewindow` que devolvía el ID de la ventana activa en lugar del contenido del portapapeles.
- **Ledger e historial escritos antes de que ffmpeg terminase**: movida la escritura de `Library_Ledger.log` y `.historial_descargas.txt` a `_postprocessor_hook` — ahora solo se registra cuando el postprocesado está confirmado. Evita entradas huérfanas que bloqueaban la re-descarga si ffmpeg fallaba a mitad.
- **Cleanup agresivo en biblioteca**: el `rglob` del finally de `_worker_loop` borraba `.webp`, `.vtt` y `.srt` en toda la biblioteca del usuario. Ahora solo borra `.part` y `.ytdl` (inequívocamente temporales). Mismo fix en `load_queue_from_disk` (antes borraba también `.webm` y `.m4a`).
- **BootScreen invisible en ventana pequeña**: añadido `min-height: 6` al `#boot_log` y `min-height: 22` al contenedor para garantizar que el log de bootstrap siempre sea visible.
- **Versión desincronizada**: `TITLE`, BootScreen y HelpScreen mostraban `v1.0.0`. Corregido a `v1.1.0` en los tres lugares.
- **`import re` dentro de método**: movido al top del módulo en `textual_app.py`.
- **robocopy en `install.ps1`**: añadida verificación de `$LASTEXITCODE >= 8` (códigos 0-7 son éxito parcial en robocopy, no errores).
- **Icono del acceso directo en `install.ps1`**: buscaba `assets\logo.ico` pero el archivo es `assets\app.ico`.

### Mejoras
- **Caché de historial en memoria**: `_clean_metadata` ya no lee `.historial_descargas.txt` una vez por pista. El historial se carga en memoria al inicio de cada tarea (`state.history_cache`) y se actualiza en tiempo real. Mejora notable en playlists grandes.
- **Selects con valor por defecto**: Modo 3 (Playlist) y Speed 2 (Seguro) preseleccionados al arrancar.
- **Validación de URL**: `procesar_input` ahora rechaza entradas que no sean URLs de YouTube antes de encolarlas.
- **Acceso directo Windows usa Windows Terminal** si está disponible (`wt.exe`), con fallback a `cmd.exe`. Mejor renderizado de la TUI.
- **`install.sh`**: rsync excluye `notas.txt` para no copiar archivos de trabajo al directorio de instalación.

---

## v1.1.0 (2026-05-14)

### Nuevas features
- **Formato y calidad configurables** (F2): MP3 128/192/256/320 kbps, FLAC sin pérdida, OGG Vorbis. Se persiste en `config.json`.
- **F4 — Visor de historial** integrado en la TUI: muestra las últimas 200 entradas del `Library_Ledger.log` con enlaces clicables.
- **Detección de portapapeles**: si se copia una URL de YouTube/YouTube Music con el campo de entrada vacío, se pega automáticamente (Linux: `wl-paste`/`xclip`; Windows: PowerShell).
- **Notificaciones de escritorio** al terminar un batch: Linux via `notify-send`; Windows via balloon tooltip (PowerShell, sin dependencias extra).
- **Failures_Log.txt**: cada pista fallida queda registrada con timestamp, URL e ID de vídeo.
- **Botón SALIR** visible en la columna izquierda.
- **F11 — Pantalla completa**: alterna visibilidad de cabecera y pie para maximizar el área de trabajo.

### Mejoras
- **Estadísticas de sesión ampliadas**: timer acumulado de sesión (no se resetea entre tareas del batch), contador de cola pendiente y tiempo de la última tarea.
- **Panel derecho persistente**: el contenido no se borra al terminar una descarga; el panel tiene scroll y autoscroll.
- **Detección de descarga colgada**: aviso visual si una pista lleva más de 5 minutos sin progresar.
- **`socket_timeout: 30`** en yt-dlp para evitar cuelgues de red indefinidos.
- Los errores de batch ya no se borran entre tareas: se acumulan en el panel durante toda la sesión.

### Bugs corregidos
- `self.exit()` en lugar de `self.action_quit()` — SALIR y Ctrl+C ahora funcionan correctamente.
- `state.cancel_requested` en `_clean_metadata` y `_progress_hook` (antes apuntaba erróneamente a `APP_DATA_DIR`, lo que impedía cancelar descargas en ciertos puntos del ciclo).
- `global_stats["failed"]` ahora se incrementa por cada pista fallida individualmente, no solo ante fallos críticos del sistema.
- `os.getlogin()` reemplazado por fallback seguro (`USER` / `LOGNAME` / `Path.home().name`) — evitaba crash en algunas terminales y sesiones SSH.
- 13 `SyntaxWarning` por secuencias de escape inválidas (`\[`, `\/`, `\_`) en `textual_app.py` — corregidas con doble backslash.
- F11 implementado (antes estaba declarado en `BINDINGS` pero el método `action_maximize` no existía).
- Extensión de archivo en rutas M3U8 ahora refleja el formato configurado.

---

## v1.0.0 (2026-05) — Adaptación Linux

Partiendo de la versión original Windows-only:

### Añadido
- `core/state.py`: `APP_DATA_DIR` resuelto por SO (Linux: `$XDG_DATA_HOME/MusicGrabber`; Windows: `%LOCALAPPDATA%\MusicGrabber`; macOS: `~/Library/Application Support/MusicGrabber`).
- `core/bootstrap.py`: lógica híbrida — usa binarios del sistema si están en `$PATH`; si no, los descarga a `.bin/` privado. Auto-update de yt-dlp standalone. URLs BtbN para ffmpeg en Linux y Windows.
- `core/downloader.py`: `get_ffmpeg_path()` en lugar de ruta Windows hardcodeada.
- `ui/textual_app.py`: `_open_in_file_manager()` cross-platform; `get_drives()` con detección de carpeta música locale-agnostic y puntos de montaje Linux (`/run/media`, `/media`, `/mnt`).
- `install.sh`: instalador Linux genérico (Python ≥ 3.9, venv, lanzador en `~/.local/bin`, entrada `.desktop`, soporte `--uninstall`).
- `install.ps1`: instalador Windows (Python via winget, venv, lanzador en `WindowsApps`, acceso directo Menú Inicio, soporte `-Uninstall`).
- `requirements.txt`.

### Eliminado
- `innoSetupScript.iss` y `MusicGrabber_Setup_v1.0.0.exe` (sustituidos por los scripts de instalación).
