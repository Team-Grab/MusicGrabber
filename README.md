<p align="center">
  <img src="assets/logo.png" width="400" alt="Music Grabber Logo">
</p>

# Music Grabber

**Music Grabber** es una aplicación de escritorio para descargar música de YouTube y YouTube Music, organizarla automáticamente con MusicBrainz y gestionar tu biblioteca local. Sin suscripciones, sin DRM y sin enviar datos a terceros.

Compatible con **Linux** (Fedora/Nobara, Ubuntu/Debian/Mint, Arch/Manjaro, openSUSE y derivadas) y **Windows 10/11**.

> v2.0 en desarrollo activo. La fase 1 (pipeline MusicBrainz + biblioteca + bandeja de revisión + escaneo y enriquecimiento de pistas existentes) está implementada. Reproductor integrado, app Android y sincronización vienen en fases posteriores. Ver `SESION_ACTUAL.md`.

## Características

* **Descarga simple**: pega una URL de YouTube y pulsa Descargar. Sin modos manuales.
* **Organización automática con MusicBrainz**: tras la descarga, cada pista se enriquece con metadatos canónicos (artista normalizado, álbum, año, género, carátula) y se mueve a `Artista / Año — Álbum / Pista. Título.ext`.
* **Bandeja de revisión**: las pistas con match ambiguo o sin match se aparcan en una vista dedicada con los candidatos sugeridos; el usuario decide.
* **Biblioteca integrada**: índice SQLite con todas las pistas. Vista de tabla con filtro en tiempo real. Doble clic para reproducir con el reproductor por defecto del sistema.
* **Escaneo de biblioteca previa**: si ya tienes música organizada de antes, se indexa sin tocar archivos. Botón "Enriquecer con MusicBrainz" para mejorar metadatos de pistas que no los tengan.
* **Formatos configurables**: MP3 (128 / 192 / 256 / 320 kbps), FLAC sin pérdida, OGG Vorbis.
* **Bootstrap híbrido**: detecta `yt-dlp` y `ffmpeg` del sistema; si no existen, los descarga al directorio privado de la app.
* **Detección de portapapeles**: si copias una URL de YouTube con el campo vacío, se pega automáticamente.
* **Notificaciones de escritorio** al terminar un lote.

## Instalación

### Linux (cualquier distribución con Python 3.9+)

```bash
git clone https://github.com/Team-Grab/MusicGrabber.git
cd MusicGrabber
bash install.sh
```

El script comprueba `python3` y `venv`, crea un entorno virtual aislado en `~/.local/share/MusicGrabber/venv`, instala las dependencias, deja el comando `musicgrabber` en `~/.local/bin/` y crea una entrada en el menú de aplicaciones.

Recomendado instalar `ffmpeg` y `yt-dlp` desde el gestor de paquetes:

| Distribución    | Comando                                    |
|-----------------|--------------------------------------------|
| Fedora / Nobara | `sudo dnf install ffmpeg yt-dlp`           |
| Debian / Ubuntu | `sudo apt install ffmpeg yt-dlp`           |
| Arch / Manjaro  | `sudo pacman -S ffmpeg yt-dlp`             |
| openSUSE        | `sudo zypper install ffmpeg yt-dlp`        |

Si no los instalas, Music Grabber los descarga al primer arranque en `~/.local/share/MusicGrabber/.bin/`.

Para desinstalar (conserva tu biblioteca y configuración):

```bash
bash install.sh --uninstall
```

### Windows 10 / 11

```powershell
irm https://raw.githubusercontent.com/Team-Grab/MusicGrabber/main/install.ps1 | iex
```

El script comprueba Python 3.9+ (lo instala con `winget` si falta), despliega la app en `%LOCALAPPDATA%\MusicGrabber\app`, crea venv, añade el comando `musicgrabber` al PATH del usuario y un acceso directo en el Menú Inicio.

Para desinstalar:

```powershell
& "$env:LOCALAPPDATA\MusicGrabber\app\install.ps1" -Uninstall
```

## Uso

```bash
musicgrabber
```

Al primer arranque la app pide dónde guardar la biblioteca. Después: pega una URL de YouTube o YouTube Music, elige modo y velocidad, y pulsa **Descargar**. Para cargar varias URLs a la vez, usa el botón **Lote**.

## Estructura de archivos

```
MusicGrabber/
├── main.py                 # punto de entrada
├── core/
│   ├── state.py            # estado global + configuración cross-platform
│   ├── bootstrap.py        # detección/descarga de yt-dlp y ffmpeg
│   ├── downloader.py       # motor de descargas (yt-dlp → _inbox/)
│   ├── musicbrainz.py      # cliente MusicBrainz + Cover Art Archive
│   ├── library.py          # índice SQLite + escaneo + bandeja de revisión
│   └── pipeline.py         # orquestación post-descarga + enriquecimiento
├── ui/
│   └── gui_app.py          # GUI tkinter (sidebar Descargar/Biblioteca/Sin metadatos)
├── assets/                 # logos e iconos
├── install.sh              # instalador Linux
├── install.ps1             # instalador Windows
└── requirements.txt        # dependencias pip
```

Datos de usuario:

* **Linux:** `~/.local/share/MusicGrabber/`
* **Windows:** `%LOCALAPPDATA%\MusicGrabber\`
* **macOS:** `~/Library/Application Support/MusicGrabber/` (experimental — `ffmpeg` requiere `brew install ffmpeg`)

Archivos generados:

| Archivo                              | Contenido                                              |
|--------------------------------------|--------------------------------------------------------|
| `APP_DATA_DIR/library.db`            | Índice SQLite de la biblioteca (v2.0)                  |
| `APP_DATA_DIR/_inbox/`               | Carpeta temporal para descargas en proceso             |
| `{biblioteca}/_inbox_review/`        | Pistas con match ambiguo o sin match, a revisar        |
| `{biblioteca}/Failures_Log.txt`      | Fallos de descarga con timestamp, URL e ID             |

## Tecnologías

* **Lenguaje:** Python 3.9+
* **Interfaz:** tkinter (incluido en la stdlib de Python)
* **Motores:** yt-dlp + FFmpeg
* **Metadatos:** MusicBrainz API + Cover Art Archive (datos abiertos, sin clave)
* **Tags y formato:** mutagen
* **Índice:** SQLite (stdlib)
* **Instaladores:** Bash (`install.sh`) y PowerShell (`install.ps1`)

## Créditos

* **Arquitectura y lógica core:** TeamGrab
* **Asistencia en desarrollo y UI/UX:** IA
* **Librerías de terceros:** yt-dlp, FFmpeg

---
*Este software ha sido creado con fines educativos y de preservación personal de archivos digitales.*
