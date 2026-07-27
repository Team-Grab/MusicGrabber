# MusicGrabber — Resumen de sesión v2

Pegar entero al inicio de una nueva sesión para continuar el proyecto.

---

## Contexto

- **Repo proyecto**: https://github.com/Team-Grab/MusicGrabber (cuenta anónima Team-Grab, no vincular a maxisanturba).
- **Repo Cowork**: https://github.com/maxisanturba/claude-cowork (privado, para continuar trabajando con Claude).
- **Qué es**: app Python con TUI (Textual) que descarga música de YouTube/YouTube Music vía yt-dlp + ffmpeg.
- **Entorno**: Nobara Linux (Fedora). AMD Ryzen 5 7600X, RX 6750 XT. Trabaja desde Linux.
- **Archivos de trabajo en Cowork**: `RESULTADOS/musicgrabber-linux/` — esta carpeta ES el contenido del repo.

## Decisiones tomadas (no replantear)

1. **Un solo código base multiplataforma** en `main`. Detección de SO con `sys.platform` en runtime.
2. **Dependencias híbridas**: si `yt-dlp` y `ffmpeg` están en `$PATH`, se usan. Si no, se descargan a `.bin/` privado.
3. **Anonimato Team-Grab**: todos los créditos a "TeamGrab", nunca a Maxi/maxisanturba.
4. **Instaladores**: `install.sh` (Linux) e `install.ps1` (Windows). Sin PyInstaller ni Inno Setup.
5. **Carpeta de música**: la app pregunta al usuario en el primer arranque.
6. **Formato y calidad**: configurables desde la UI (F2). MP3 128/192/256/320, FLAC, OGG. Se persiste en `config.json`.

## Estado actual — v1.1.0 (sesiones 1 y 2 completadas)

### Archivos del proyecto

```
musicgrabber-linux/
├── main.py
├── requirements.txt          (yt-dlp>=2024.1.0, textual>=0.50.0)
├── install.sh                (Linux: venv, lanzador, .desktop, --uninstall)
├── install.ps1               (Windows: winget Python, lanzador cmd, Menú Inicio, -Uninstall)
├── README.md                 (actualizado a v1.1.0)
├── CHANGELOG.md              (nuevo en sesión 2)
├── assets/                   (logo.png, app.ico)
├── core/
│   ├── state.py              (AppState + load/save_config con quality/format)
│   ├── bootstrap.py          (híbrido: sistema o descarga a .bin/)
│   └── downloader.py         (motor completo con hooks, notificaciones, log de fallos)
└── ui/
    └── textual_app.py        (TUI completa: 7 pantallas)
```

### Lo que hace cada módulo clave

**`core/state.py`**
- `APP_DATA_DIR` resuelto por SO (XDG en Linux, LOCALAPPDATA en Windows, ~/Library en macOS).
- `AppState`: campos relevantes: `audio_quality` (192), `audio_format` (mp3), `pending_queue_count`, `session_start_time`.
- `save_config` / `load_config` persisten `library_path`, `audio_quality`, `audio_format`.

**`core/downloader.py`**
- `_send_notification()`: notify-send en Linux, PowerShell balloon en Windows.
- `_log_failure()`: escribe en `Failures_Log.txt` con timestamp y URL.
- `DaemonLogger.error()`: incrementa `global_stats["failed"]` e llama `_log_failure()`.
- `_clean_metadata` y `_progress_hook`: usan `state.cancel_requested` (bug original corregido).
- `_get_ydl_opts()`: usa `state.audio_format` y `state.audio_quality`; incluye `socket_timeout: 30`.
- `_worker_loop()`: acumula `session_start_time` (solo fija en la primera tarea); actualiza `pending_queue_count`; llama `_send_notification()` al finalizar.

**`ui/textual_app.py`** — pantallas:
- `BootScreen`: arranque con bootstrap.
- `SetupScreen`: selección de biblioteca (primer arranque o F2→cambiar carpeta).
- `HelpScreen` (F1): manual completo.
- `ConfigScreen` (F2): formato (mp3/flac/ogg), calidad (128/192/256/320), cambio de biblioteca.
- `BatchScreen` (F3): carga masiva de URLs.
- `HistoryScreen` (F4): últimas 200 entradas de `Library_Ledger.log`.
- `ResumeScreen`: recuperación de sesión interrumpida.
- `MusicGrabberTUI`: app principal. Monitorización de portapapeles cada 2s. Panel derecho con scroll, autoscroll y persistencia. Stats con timer acumulado y cola. F11 alterna header/footer.

### Atajos activos

F1 Ayuda | F2 Config | F3 Batch | F4 Historial | F5 Reintentar | F11 Pantalla completa | ESC Salir | Ctrl+C cancela (o sale si idle)

### Cómo probar en Nobara

```bash
# Copiar al entorno instalado:
cp ~/Claude-Cowork/RESULTADOS/musicgrabber-linux/core/state.py \
   ~/.local/share/MusicGrabber/app/core/state.py
cp ~/Claude-Cowork/RESULTADOS/musicgrabber-linux/core/downloader.py \
   ~/.local/share/MusicGrabber/app/core/downloader.py
cp ~/Claude-Cowork/RESULTADOS/musicgrabber-linux/ui/textual_app.py \
   ~/.local/share/MusicGrabber/app/ui/textual_app.py
musicgrabber
```

O reinstalar limpio desde el directorio de trabajo:

```bash
rm -rf ~/MusicGrabber-test
cp -r ~/Claude-Cowork/RESULTADOS/musicgrabber-linux ~/MusicGrabber-test
cd ~/MusicGrabber-test && bash install.sh
musicgrabber
```

## Fases siguientes (decidir orden al arrancar)

- **Fase 2a — AppImage**: ejecutable único para cualquier distro. Necesita `appimagetool`. Mejor relación valor/trabajo.
- **Fase 2b — PKGBUILD/AUR**: para Arch/Manjaro. Esfuerzo bajo, lo mantiene la comunidad.
- **Fase 2c — RPM + Copr**: Fedora/Nobara/RHEL. Spec file + cuenta Copr.
- **Fase 2d — DEB + PPA**: Debian/Ubuntu. Más fricción (repo HTTP firmado o Launchpad).
- **Fase 3 — GitHub Actions**: CI que prueba install.sh y install.ps1 en runners Linux/Windows.
- **Fase 4 — Pulir interfaz visual**: pendiente de definir qué se quiere cambiar exactamente.

Recomendación de orden: **AppImage** primero (cubre todas las distros), luego **PKGBUILD/AUR** (esfuerzo mínimo).

## Cómo actualizar los repos

Ver `RESULTADOS/proyectos-activos.md` para el flujo exacto de git.

## Preferencias del usuario (recordatorio)

- No es programador. Claude escribe el código entero, él lo ejecuta.
- Directo, sin preámbulos, sin adulación, sin emojis decorativos.
- Prosa, no listas, salvo pasos o comandos.
- Si el brief es ambiguo, preguntar antes de suponer.
