# MusicGrabber — Resumen para continuar en nueva sesión

Documento para retomar el proyecto de adaptación de MusicGrabber a Linux en una nueva conversación con Claude Sonnet. Pegar entero al inicio.

---

## Contexto

- **Repo original**: https://github.com/Team-Grab/MusicGrabber (cuenta alternativa anónima del usuario, mantener esta autoría).
- **Qué es**: app Python con TUI (Textual) que descarga música de YouTube/YouTube Music vía yt-dlp + ffmpeg. Diseñada inicialmente para Windows con instalador Inno Setup.
- **Objetivo**: adaptarla a Linux **manteniendo Windows funcional** con un solo código base.
- **Entorno del usuario**: Nobara Linux (basada en Fedora). AMD Ryzen 5 7600X, RX 6750 XT. Trabaja desde Linux, no quiere VM Windows para builds.

## Decisiones tomadas (NO replantear)

1. **Un solo código base multiplataforma** en `main`, sin ramas separadas. Detección de SO con `sys.platform` en runtime.
2. **Dependencias híbridas**: si `yt-dlp` y `ffmpeg` están en `$PATH`, usar las del sistema. Si no, descargar al `.bin/` privado de la app y mantenerlas (yt-dlp con `-U`, ffmpeg manual).
3. **Anonimato Team-Grab**: mantener todos los créditos a "TeamGrab", no a Maxi/maxisanturba.
4. **Windows**: descartado el `.exe` con PyInstaller+Inno Setup. Se sustituye por `install.ps1` (PowerShell, una línea desde la terminal). Razón: mantener desde Linux sin VM ni compilación.
5. **Linux**: `install.sh` genérico (cualquier distro con python3+pip). Paquetes nativos (AppImage, RPM, DEB, AUR) en fases posteriores, una por sesión.
6. **Carpeta de música**: la app sigue preguntando al usuario en el primer arranque (sin asumir locale).

## Estado al final de la sesión anterior — Fase 1 COMPLETADA

Trabajado todo dentro de `/home/maxi/Claude-Cowork/RESULTADOS/musicgrabber-linux/`.

### Cambios de código

- **`core/state.py`**: `APP_DATA_DIR` resuelto por SO.
  - Linux: `$XDG_DATA_HOME/MusicGrabber` (default `~/.local/share/MusicGrabber`).
  - Windows: `%LOCALAPPDATA%\MusicGrabber`.
  - macOS: `~/Library/Application Support/MusicGrabber`.
- **`core/bootstrap.py`** reescrito completo con lógica híbrida. Expone API pública `get_ytdlp_path()` y `get_ffmpeg_path()`. Auto-update solo del yt-dlp descargado por nosotros (no del sistema). URLs de descarga:
  - yt-dlp: `https://github.com/yt-dlp/yt-dlp/releases/latest/download/<filename>` con filename `yt-dlp.exe` / `yt-dlp_linux` / `yt-dlp_macos`.
  - ffmpeg Linux: BtbN `linux64-gpl.tar.xz`. Windows: BtbN `win64-gpl.zip`. macOS: NO se autodescarga, avisa de `brew install ffmpeg`.
- **`core/downloader.py`**: `ffmpeg_location: str((BIN_DIR / "ffmpeg.exe").absolute())` reemplazado por `get_ffmpeg_path()`. Import añadido.
- **`ui/textual_app.py`**:
  - Nueva función `_open_in_file_manager(path)` que usa `os.startfile` / `open` / `xdg-open` según SO. Aplicada en botones "ABRIR BIBLIOTECA" y "VER HISTORIAL".
  - `get_drives()` ampliada: detecta carpeta música locale-agnostic (Music, Música, Musica, Musik, Musique, My Music). En Linux añade discos en `/run/media/$USER`, `/media/$USER`, `/mnt`.

### Ficheros nuevos / borrados

- **Nuevos**: `requirements.txt` (yt-dlp + textual), `install.sh` (Linux), `install.ps1` (Windows).
- **Borrado**: `innoSetupScript.iss`, `MusicGrabber_Setup_v1.0.0.exe`, `.git`.
- **Actualizado**: `README.md` (dos secciones Linux/Windows), `.gitignore`.

### install.sh — qué hace

1. Verifica `python3 >= 3.9` y módulo `venv`. Mensaje específico por familia de distro si falta.
2. Avisa (no obliga) si faltan ffmpeg/yt-dlp del sistema con el comando exacto por distro.
3. Copia el código a `$XDG_DATA_HOME/MusicGrabber/app` (rsync si está disponible, cp si no).
4. Crea venv en `$XDG_DATA_HOME/MusicGrabber/venv`, instala `requirements.txt`.
5. Lanzador `~/.local/bin/musicgrabber` (chmod +x).
6. Entrada `.desktop` en `~/.local/share/applications/musicgrabber.desktop`.
7. Avisa si `~/.local/bin` no está en `$PATH`.
8. Soporta `bash install.sh --uninstall` que NO toca la biblioteca de música ni la config.

### install.ps1 — qué hace

Análogo en Windows. Una línea: `irm .../install.ps1 | iex`. Auto-instala Python con `winget` si falta. Clona el repo en `%LOCALAPPDATA%\MusicGrabber\app`, crea venv, deja `musicgrabber.cmd` en `%LOCALAPPDATA%\Microsoft\WindowsApps\` (ya está en el PATH de usuario en Win10/11), crea acceso directo en Menú Inicio. Soporta `-Uninstall`.

### Verificación pasada

`py_compile` de los 5 módulos OK. Import en proceso fresco con XDG_DATA_HOME en /tmp OK. `get_ffmpeg_path()` y `_get_ydl_opts(...)` devuelven rutas válidas.

### Detalles cosméticos pendientes (no bloquean)

13 `SyntaxWarning` por escapes `\[` en strings de Textual en `ui/textual_app.py`. Vienen del código original. Funcionan en Python 3.12 pero serán SyntaxError en Python futuro. Limpieza fácil: añadir `r` prefix o doble backslash. No tocado para no alterar comportamiento de la TUI sin que el usuario lo apruebe.

## Cómo se prueba en Nobara (siguiente paso del usuario)

```bash
cp -r "~/Claude-Cowork/RESULTADOS/musicgrabber-linux" ~/MusicGrabber-test
cd ~/MusicGrabber-test
bash install.sh
musicgrabber
```

Esperado: la app arranca, detecta ffmpeg/yt-dlp del sistema (en Nobara casi seguro están), entra a `BootScreen`, pide carpeta de biblioteca, y queda esperando enlaces.

## Fases siguientes (sesiones aparte)

Decidir orden cuando arranque la nueva sesión:

- **Fase 2a — AppImage**: un solo fichero ejecutable para CUALQUIER distro. Mejor relación valor/trabajo. Necesita `appimagetool` o `appimage-builder`. Bundlear Python + venv + libs.
- **Fase 2b — RPM + Copr**: paquete nativo para Fedora/Nobara/RHEL/derivadas. Spec file + cuenta en `copr.fedorainfracloud.org`. Resultado: `sudo dnf copr enable team-grab/musicgrabber && sudo dnf install musicgrabber`.
- **Fase 2c — DEB + repo apt**: paquete nativo para Debian/Ubuntu/Mint. Requiere infraestructura propia (repo HTTP firmado) o usar Launchpad PPA. Más fricción.
- **Fase 2d — PKGBUILD en AUR**: para Arch/Manjaro. Una vez subido lo mantiene la comunidad. Bajo esfuerzo.
- **Fase 3 — GitHub Actions** (opcional): workflow que ejecuta `install.sh` y `install.ps1` en runners Linux y Windows en cada push para detectar regresiones.
- **Fase 4 — Limpieza cosmética**: arreglar los 13 SyntaxWarning de escapes en `textual_app.py`.

Recomendación: empezar por **AppImage** (cubre todas las distros con un solo fichero, valor inmediato) y **PKGBUILD/AUR** (esfuerzo bajo). DEB y Copr cuando haya demanda.

## Preferencias del usuario (recordatorio para la nueva sesión)

Está todo en `~/Claude-Cowork/SOBRE-MI/sobre-mi.md` y `CLAUDE.md`. Resumen útil para esta tarea:

- No es programador. No le pidas que rellene/corrija código. Si hay que ajustar algo, lo ajusta Claude y entrega final.
- Directo, sin preámbulos, sin adulación, sin emojis decorativos.
- Prosa, no listas, salvo que la lista sea esencial (pasos, comandos).
- Si el brief es ambiguo, preguntar antes de suponer.
- Trabaja siempre desde `~/Claude-Cowork`. Entregas en `RESULTADOS/<proyecto>/`.
