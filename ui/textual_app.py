import os
import sys
import string
import shutil
import subprocess
import time
from pathlib import Path


def _open_in_file_manager(path: str) -> None:
    """Abre una ruta (carpeta o fichero) con el manejador por defecto del SO."""
    if sys.platform.startswith("win"):
        os.startfile(path)          # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _get_clipboard() -> str:
    """Devuelve el contenido del portapapeles. Cadena vacía si falla."""
    try:
        if sys.platform.startswith("win"):
            result = subprocess.run(
                ["powershell", "-Command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip()
        else:
            # Wayland primero, luego X11
            for cmd in [["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"], ["xdotool", "getactivewindow"]]:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                    if result.returncode == 0:
                        return result.stdout.strip()
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
    except Exception:
        pass
    return ""


from textual import work
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Header, Footer, Input, Button, Static, Select, RichLog, Label, DirectoryTree, TextArea
from textual.containers import Vertical, Horizontal, Grid, Container, VerticalScroll
from core.state import state, APP_DATA_DIR, load_config, save_config
from core.downloader import add_download, start_download_worker, load_queue_from_disk, has_pending_session


def get_drives():
    home  = Path.home()
    music = home
    for candidate in ("Music", "Música", "Musica", "Musik", "Musique", "My Music"):
        p = home / candidate
        if p.exists() and p.is_dir():
            music = p
            break

    drives = [("🎵 Mi Música", str(music)), ("🏠 Carpeta Personal", str(home))]

    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append((f"💿 Disco {letter}:", drive))
    else:
        # Fallback seguro para el nombre de usuario (os.getlogin() falla en algunos entornos)
        username = os.environ.get("USER") or os.environ.get("LOGNAME") or Path.home().name
        for mount_base in (f"/run/media/{username}", f"/media/{username}", "/mnt"):
            if os.path.isdir(mount_base):
                try:
                    for entry in sorted(os.listdir(mount_base)):
                        full = os.path.join(mount_base, entry)
                        if os.path.isdir(full):
                            drives.append((f"💿 {entry}", full))
                except PermissionError:
                    pass
    return drives


class FolderTree(DirectoryTree):
    def filter_paths(self, paths):
        return [path for path in paths if path.is_dir()]


# ---------------------------------------------------------------------------
# SetupScreen — F2 / primer arranque
# ---------------------------------------------------------------------------

class SetupScreen(ModalScreen):
    CSS = """
    SetupScreen { align: center middle; background: rgba(0, 0, 0, 0.8); }
    #setup_dialog { width: 85%; height: 85%; border: double #00ffcc; background: #111; padding: 1; layout: grid; grid-size: 2; grid-columns: 1fr 1fr; }
    #tree_zone { border-right: solid #00ffcc; padding-right: 1; height: 100%; }
    #action_zone { padding-left: 1; height: 100%; }
    .setup-title { text-style: bold; color: #ff00ff; margin-bottom: 1; }
    #drive_select { margin-bottom: 1; }
    #selected_path_display { background: #000; color: #00ffcc; padding: 1; margin-bottom: 2; border: tall #ff00ff; height: auto; }
    Button { margin-bottom: 1; width: 100%; }
    #btn_save_path { background: #00ffcc; color: black; text-style: bold; }
    #btn_create_folder { background: #ff00ff; color: white; text-style: bold; }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        home = Path.home()
        potential_paths = [home / "Music", home / "Música", home / "My Music"]
        self.music_dir = home
        for p in potential_paths:
            if p.exists() and p.is_dir():
                self.music_dir = p
                break
        self.current_selected_path = self.music_dir

    def compose(self) -> ComposeResult:
        with Container(id="setup_dialog"):
            with Vertical(id="tree_zone"):
                yield Label("1. EXPLORADOR DE SISTEMA", classes="setup-title")
                yield Select(get_drives(), id="drive_select", value=str(self.music_dir), prompt="Cambiar ubicación")
                yield FolderTree(str(self.music_dir), id="dir_tree")
            with Vertical(id="action_zone"):
                yield Label("2. CONFIGURACIÓN DE RUTA", classes="setup-title")
                yield Label("Directorio seleccionado:")
                yield Static(str(self.current_selected_path), id="selected_path_display")
                yield Button("✅ USAR ESTE DIRECTORIO", id="btn_save_path")
                yield Button("📁 CREAR SUB-CARPETA 'MusicGrabber_Library'", id="btn_create_folder")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.control.id == "drive_select":
            safe_path = str(self.music_dir) if event.value is Select.NULL else event.value
            try:
                self.query_one("#dir_tree", FolderTree).path = safe_path
                self.current_selected_path = Path(safe_path)
                self.query_one("#selected_path_display", Static).update(str(self.current_selected_path))
            except Exception as e:
                self.app.notify(f"Error: {e}", severity="error")

    def on_tree_node_highlighted(self, event: DirectoryTree.NodeHighlighted) -> None:
        path = event.node.data.path
        self.current_selected_path = path if path.is_dir() else path.parent
        self.query_one("#selected_path_display", Static).update(str(self.current_selected_path))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        target_path = self.current_selected_path
        if event.button.id == "btn_create_folder":
            target_path = target_path / "MusicGrabber_Library"
        try:
            target_path.mkdir(parents=True, exist_ok=True)
            save_config(path=str(target_path.absolute()))
            self.app.pop_screen()
            self.app.notify(f"Biblioteca anclada en: {target_path.name}", severity="information")
            self.app.update_header_info()
            self.app.query_one("#url_input", Input).focus()
        except Exception as e:
            self.app.notify(f"Error: {e}", severity="error")


# ---------------------------------------------------------------------------
# BootScreen — arranque
# ---------------------------------------------------------------------------

class BootScreen(ModalScreen):
    CSS = """
    BootScreen { align: center middle; background: #0a0a0a; }
    #boot_container { width: 75%; height: 65%; border: double #00ffcc; background: #111; padding: 1; }
    .ascii_logo { text-align: left; margin-bottom: 1; padding-left: 2; }
    #boot_log { height: 1fr; border: solid #333; background: #000; }
    """

    def compose(self) -> ComposeResult:
        with Container(id="boot_container"):
            ascii_logo = """
[bold cyan]   ███╗   ███╗██╗   ██╗███████╗██╗ ██████╗ [/bold cyan]  [bold magenta]MUSIC GRABBER v1.0.0[/bold magenta]
[bold cyan]   ████╗ ████║██║   ██║██╔════╝██║██╔════╝ [/bold cyan]
[bold cyan]   ██╔████╔██║██║   ██║███████╗██║██║      [/bold cyan]
[bold cyan]   ██║╚██╔╝██║██║   ██║╚════██║██║██║      [/bold cyan]
[bold cyan]   ██║ ╚═╝ ██║╚██████╔╝███████║██║╚██████╗ [/bold cyan]
[bold cyan]   ╚═╝     ╚═╝ ╚═════╝ ╚══════╝╚═╝ ╚═════╝ [/bold cyan]
[bold magenta]██████╗ ██████╗  █████╗ ██████╗ ██████╗ ███████╗██████╗ [/bold magenta]
[bold magenta]██╔════╝ ██╔══██╗██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔══██╗[/bold magenta]
[bold magenta]██║  ███╗██████╔╝███████║██████╔╝██████╔╝█████╗  ██████╔╝[/bold magenta]
[bold magenta]██║   ██║██╔══██╗██╔══██║██╔══██╗██╔══██╗██╔══╝  ██╔══██╗[/bold magenta]
[bold magenta]╚██████╔╝██║  ██║██║  ██║██████╔╝██████╔╝███████╗██║  ██║[/bold magenta]
[bold magenta] ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝[/bold magenta]
"""
            yield Static(ascii_logo, classes="ascii_logo")
            yield RichLog(id="boot_log", markup=True)

    def on_mount(self) -> None:
        self.run_bootstrap()

    @work(thread=True)
    def run_bootstrap(self):
        from core.bootstrap import ensure_dependencies

        def ui_log(msg):
            self.app.call_from_thread(self.query_one("#boot_log", RichLog).write, msg)

        ui_log("[bold cyan]Comprobando integridad del motor...[/bold cyan]")
        ensure_dependencies(ui_log)
        ui_log("[bold green]Sistemas en línea. Arrancando orquestador...[/bold green]")

        start_download_worker()
        time.sleep(1.5)

        def transition():
            self.app.pop_screen()
            self.app.check_library()

        self.app.call_from_thread(transition)


# ---------------------------------------------------------------------------
# HelpScreen — F1
# ---------------------------------------------------------------------------

class HelpScreen(ModalScreen):
    CSS = """
    HelpScreen { align: center middle; background: rgba(0,0,0,0.9); }
    #help_panel { width: 85%; height: 90%; border: double #00ffcc; background: #111; padding: 1 2; }
    #help_scroll { width: 100%; height: 1fr; padding-right: 1; }
    .help_title  { text-style: bold; color: #ff00ff; margin-bottom: 1; text-align: center; width: 100%; }
    .help_h2     { text-style: bold; color: #00ffcc; margin-top: 1; width: 100%; border-bottom: solid #333; }
    .help_text   { color: white; margin-bottom: 1; width: 100%; }
    .help_path   { color: #ffff00; text-style: italic; margin-bottom: 1; width: 100%; }
    .help_bullet { color: white; width: 100%; }
    .about_box   { background: #000; border: tall #333; padding: 1; margin-top: 2; width: 100%; height: auto; }
    .manifesto   { text-style: italic; color: #00ffcc; text-align: center; width: 100%; margin: 1 0; }
    #close_help  { margin-top: 1; width: 100%; text-style: bold; background: #ff00ff; color: white; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help_panel"):
            yield Label("◢◤ MANUAL DE OPERACIONES Y SISTEMA", classes="help_title")
            with VerticalScroll(id="help_scroll"):
                mini_logo = """
[bold cyan]  __  __  ____  [/bold cyan]
[bold cyan] |  \\/  |/ ___| [/bold cyan]  [bold magenta]MUSIC GRABBER v1.0.0[/bold magenta]
[bold cyan] | |\\/| | |  _  [/bold cyan]  [dim]Orquestador de Preservación Digital[/dim]
[bold cyan] | |  | | |_| | [/bold cyan]  [dim]Resiliencia & Metadatos Pro[/dim]
[bold cyan] |_|  |_|\\____| [/bold cyan]
"""
                yield Static(mini_logo, classes="ascii_logo_mini")

                yield Label("ATAJOS DE SISTEMA", classes="help_h2")
                yield Label("\\[F1] Manual e Info | \\[F2] Configuración | \\[F3] Carga por Lotes | \\[F4] Historial | \\[F5] Reintentar Fallos | \\[F11] Pantalla Completa | \\[ESC] Salir", classes="help_text")

                yield Label("1. FILOSOFÍA Y ESTRUCTURA DE ARCHIVOS", classes="help_h2")
                yield Label("Define cómo el orquestador organiza tu biblioteca física:", classes="help_text")
                yield Label(" • \\[1] Álbum: Descarga un álbum completo y lo organiza por número de pista. Prioriza la cronología del artista.", classes="help_bullet")
                yield Label("   Ruta: Artista / Año - Álbum / Nº Track - Canción.mp3", classes="help_path")
                yield Label(" • \\[2] Recopilatorio: Para discos Tributo o Grandes Éxitos de varios artistas. Centraliza todo bajo el nombre del álbum.", classes="help_bullet")
                yield Label("   Ruta: Varios Artistas / Álbum / Nº Track - Artista - Canción.mp3", classes="help_path")
                yield Label(" • \\[3] Playlist: Modo inteligente. Disuelve listas masivas reubicando tracks en sus carpetas de Artista originales. Genera lista .m3u8.", classes="help_bullet")
                yield Label("   Ruta: Artista / Año - Álbum / Canción.mp3", classes="help_path")
                yield Label(" • \\[4] Mix: Concentra toda la lista en una carpeta común. Ideal para DJs. Genera lista .m3u8.", classes="help_bullet")
                yield Label("   Ruta: _Mix / Nombre de Lista / Canción.mp3", classes="help_path")
                yield Label(" • \\[5] Discografía: Escanea el canal del artista y extrae exclusivamente álbumes oficiales (omite singles/videos).", classes="help_bullet")
                yield Label("   Ruta: Artista / Año - Álbum / Nº Track - Canción.mp3", classes="help_path")
                yield Label(" • \\[6] Huérfano: Descarga rápida para contenido sin metadatos claros. Ignora carátulas y orden.", classes="help_bullet")
                yield Label("   Ruta: _Huérfanos / Título.mp3", classes="help_path")

                yield Label("2. GESTIÓN DE VELOCIDAD (ANTIBAN)", classes="help_h2")
                yield Label("Controla las pausas entre peticiones para evitar bloqueos 403 de YouTube:", classes="help_text")
                yield Label(" • \\[1] Rápido: Sin esperas. Máximo 2 pistas aisladas.", classes="help_bullet")
                yield Label(" • \\[2] Seguro: Pausa aleatoria (1-5s). Uso general.", classes="help_bullet")
                yield Label(" • \\[3] Nocturno: Pausa larga (5-10s). Para discografías masivas o Modo Batch.", classes="help_bullet")

                yield Label("3. ACERCA DE MUSIC GRABBER", classes="help_h2")
                with Vertical(classes="about_box"):
                    yield Label("MUSIC GRABBER no es un simple descargador; es un orquestador de preservación digital. En un mundo de streaming efímero, esta herramienta te devuelve la soberanía sobre tus archivos con metadatos precisos, portadas en alta resolución y una arquitectura de archivos indestructible.", classes="manifesto")
                    yield Label("[bold cyan]Arquitectura de Resiliencia:[/bold cyan]")
                    yield Label(" • [b]Persistencia:[/b] Cola de tareas indestructible via Escritura Atómica.")
                    yield Label(" • [b]Integridad:[/b] Protocolo de Rollback preventivo en reinicio.")
                    yield Label(" • [b]Motor:[/b] yt-dlp Integrado (Core) y FFmpeg (Post-procesado).")
                    yield Label(" • [b]Interfaz:[/b] Textual Framework (TUI).")
                    yield Label("")
                    yield Label("[bold magenta]Créditos y Autoría:[/bold magenta]")
                    yield Label(" • [b]Arquitectura y Lógica Core:[/b] \\[TeamGrab]")
                    yield Label(" • [b]Code Generation & UI/UX:[/b] Asistencia por IA")
                    yield Label(" • [b]Terceros:[/b] yt-dlp (Extracción), FFmpeg (Procesado), Textual (Motor Gráfico).")

            yield Button("ENTENDIDO", id="close_help")

    def on_button_pressed(self):
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# ConfigScreen — F2
# ---------------------------------------------------------------------------

class ConfigScreen(ModalScreen):
    CSS = """
    ConfigScreen { align: center middle; background: rgba(0,0,0,0.85); }
    #config_panel { width: 60; height: auto; border: double #ff00ff; background: #111; padding: 1 2; }
    .cfg_title   { text-style: bold; color: #00ffcc; margin-bottom: 1; text-align: center; width: 100%; }
    .cfg_label   { color: #aaaaaa; margin-top: 1; }
    .cfg_text    { color: white; margin-bottom: 2; text-align: center; width: 100%; }
    Select       { margin-bottom: 1; }
    Button       { margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="config_panel"):
            yield Label("⚙️ PREFERENCIAS DEL SISTEMA", classes="cfg_title")

            yield Label("Formato de audio:", classes="cfg_label")
            yield Select(
                [("MP3", "mp3"), ("FLAC (sin pérdida)", "flac"), ("OGG Vorbis", "ogg")],
                id="sel_format",
                value=state.audio_format,
                prompt="Formato",
            )

            yield Label("Calidad MP3 / OGG (kbps):", classes="cfg_label")
            yield Select(
                [("128 kbps", "128"), ("192 kbps — estándar", "192"), ("256 kbps", "256"), ("320 kbps — máxima", "320")],
                id="sel_quality",
                value=state.audio_quality,
                prompt="Calidad",
            )

            yield Label("(La calidad no aplica a FLAC)", classes="cfg_text")
            yield Button("📂 CAMBIAR CARPETA DE BIBLIOTECA", id="btn_reset_lib", variant="primary")
            yield Button("💾 GUARDAR Y CERRAR", id="btn_save_cfg", variant="success")
            yield Button("❌ CANCELAR", variant="error", id="btn_close_cfg")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_reset_lib":
            self.app.pop_screen()
            self.app.push_screen(SetupScreen())
        elif event.button.id == "btn_save_cfg":
            fmt     = str(self.query_one("#sel_format",  Select).value)
            quality = str(self.query_one("#sel_quality", Select).value)
            if fmt     not in (Select.BLANK, ""):
                save_config(fmt=fmt)
            if quality not in (Select.BLANK, ""):
                save_config(quality=quality)
            self.app.notify("Configuración guardada.", severity="information")
            self.app.pop_screen()
        elif event.button.id == "btn_close_cfg":
            self.app.pop_screen()


# ---------------------------------------------------------------------------
# BatchScreen — F3
# ---------------------------------------------------------------------------

class BatchScreen(ModalScreen):
    CSS = """
    BatchScreen { align: center middle; background: rgba(0,0,0,0.8); }
    #batch_panel { width: 70%; height: 75%; border: double #00ffcc; background: #111; padding: 1; }
    .batch_title { text-style: bold; color: #ff00ff; margin-bottom: 1; text-align: center; }
    TextArea { height: 1fr; border: solid #333; margin-bottom: 1; }
    TextArea:focus { border: solid #00ffcc; }
    #batch_controls { height: auto; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="batch_panel"):
            yield Label("◢◤ MODO BATCH // CARGA MASIVA", classes="batch_title")
            yield Label("Pega aquí todos tus enlaces (uno por línea):")
            yield TextArea(id="batch_input", language="markdown")
            with Horizontal(id="batch_controls"):
                yield Select(
                    [("1-Álbum", "1"), ("2-Recop", "2"), ("3-Playlist", "3"), ("4-Mix", "4"), ("5-Disco", "5"), ("6-Huérfano", "6")],
                    prompt="MODO GLOBAL", id="batch_mode", value="3",
                )
                yield Select(
                    [("1-Rápido", "1"), ("2-Seguro", "2"), ("3-Nocturno", "3")],
                    prompt="SPEED GLOBAL", id="batch_speed", value="2",
                )
            yield Button("🚀 ENCOLAR TODO EL LOTE", variant="success", id="btn_enqueue_batch")
            yield Button("❌ CANCELAR", variant="error", id="btn_cancel_batch")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_cancel_batch":
            self.app.pop_screen()
        elif event.button.id == "btn_enqueue_batch":
            text  = self.query_one("#batch_input", TextArea).text
            mode  = str(self.query_one("#batch_mode",  Select).value)
            speed = str(self.query_one("#batch_speed", Select).value)
            urls  = [u.strip() for u in text.split("\n") if u.strip().startswith("http")]
            if not urls:
                self.app.notify("No se encontraron URLs válidas.", severity="error")
                return
            for u in urls:
                add_download(u, mode, speed)
            self.app.notify(f"{len(urls)} enlaces añadidos a la cola.", severity="information")
            self.app.pop_screen()


# ---------------------------------------------------------------------------
# HistoryScreen — F4
# ---------------------------------------------------------------------------

class HistoryScreen(ModalScreen):
    CSS = """
    HistoryScreen { align: center middle; background: rgba(0,0,0,0.9); }
    #history_panel { width: 85%; height: 88%; border: double #00ffcc; background: #111; padding: 1 2; }
    .hist_title  { text-style: bold; color: #ff00ff; margin-bottom: 1; text-align: center; width: 100%; }
    #history_log { height: 1fr; border: solid #333; background: #000; }
    #close_hist  { margin-top: 1; width: 100%; text-style: bold; background: #ff00ff; color: white; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="history_panel"):
            yield Label("◢◤ HISTORIAL DE DESCARGAS (últimas 200 entradas)", classes="hist_title")
            yield RichLog(id="history_log", markup=True, highlight=True)
            yield Button("CERRAR", id="close_hist")

    def on_mount(self) -> None:
        self._load_history()

    def _load_history(self) -> None:
        log = self.query_one("#history_log", RichLog)
        if not state.library_path:
            log.write("[red]Sin biblioteca configurada.[/red]")
            return

        ledger_path = Path(state.library_path) / "Library_Ledger.log"
        if not ledger_path.exists():
            log.write("[dim]El historial está vacío — aún no hay descargas registradas.[/dim]")
            return

        try:
            lines = ledger_path.read_text(encoding="utf-8").splitlines()
            entries = lines[-200:]  # últimas 200 entradas
            for i, line in enumerate(reversed(entries), 1):
                # Formato: youtube <vid> "<ruta>"
                import re
                m = re.match(r'^youtube ([a-zA-Z0-9_-]{11}) "(.*)"$', line.strip())
                if m:
                    vid      = m.group(1)
                    filepath = m.group(2)
                    filename = Path(filepath).name
                    url      = f"https://music.youtube.com/watch?v={vid}"
                    log.write(f"[dim]{i:>3}[/dim]  [bold green]{filename}[/bold green]  [dim cyan][link={url}]{vid}[/link][/dim cyan]")
                else:
                    log.write(f"[dim]{line}[/dim]")
            log.write(f"\n[dim]── {len(entries)} entrada(s) mostrada(s) ──[/dim]")
        except Exception as e:
            log.write(f"[red]Error al leer el historial: {e}[/red]")

    def on_button_pressed(self):
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# ResumeScreen
# ---------------------------------------------------------------------------

class ResumeScreen(ModalScreen):
    CSS = """
    ResumeScreen { align: center middle; background: rgba(0,0,0,0.85); }
    #resume_panel { width: 60; height: auto; border: double #ff0033; background: #111; padding: 1 2; }
    .resume_title { text-style: bold; color: #ffff00; margin-bottom: 1; text-align: center; width: 100%; }
    .resume_text  { margin-bottom: 1; }
    .btn_resume   { width: 100%; margin-top: 1; text-style: bold; }
    #btn_yes { background: #00ffcc; color: black; }
    #btn_no  { background: #ff0033; color: white; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="resume_panel"):
            yield Label("⚠️ ANOMALÍA DETECTADA: SESIÓN INTERRUMPIDA", classes="resume_title")
            yield Label("El sistema se cerró inesperadamente mientras había pistas descargándose o en espera.", classes="resume_text")
            yield Label("¿Deseas aplicar el protocolo de Rollback y reanudar las descargas automáticamente?", classes="resume_text")
            yield Button("✅ SÍ, REANUDAR DESCARTANDO ERRORES", id="btn_yes", classes="btn_resume")
            yield Button("❌ NO, DESCARTAR LA COLA Y LIMPIAR ESTADO", id="btn_no", classes="btn_resume")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_yes":
            load_queue_from_disk(resume_requested=True)
            self.app.notify("Rollback iniciado. Retomando tareas...", severity="information")
        else:
            load_queue_from_disk(resume_requested=False)
            self.app.notify("Cola descartada. Sistema limpio.", severity="warning")
        self.app.pop_screen()
        self.app.query_one("#url_input", Input).focus()


# ---------------------------------------------------------------------------
# MusicGrabberTUI — app principal
# ---------------------------------------------------------------------------

class MusicGrabberTUI(App):
    TITLE = "MUSIC GRABBER v1.0.0"
    _last_capture_text: str = ""
    _last_clipboard: str = ""

    CSS = """
    Screen { background: #0a0a0a; color: #00ffcc; }

    #top_info { layout: horizontal; height: 3; border-bottom: solid #00ffcc; padding: 0 1; }
    .info_tag { width: 1fr; text-align: center; color: #ff00ff; text-style: bold; padding-top: 1; }

    #main_grid { layout: grid; grid-size: 2; grid-columns: 1fr 2fr; padding: 1; }
    .panel { border: solid #333; background: #111; padding: 0 1; }
    .panel-title { text-style: bold; color: #ff00ff; padding-bottom: 1; border-bottom: dashed #333; width: 100%; margin-bottom: 1; }

    /* COL IZQUIERDA */
    #left_col { height: 100%; border-right: solid #00ffcc; }
    #log_descargas { height: 1fr; }
    #os_buttons { height: auto; margin-top: 1; }
    .btn_os       { width: 100%; background: #222; color: #00ffcc; border: none; margin-bottom: 1; }
    .btn_os:focus { background: #00ffcc; color: black; text-style: bold; }
    .btn_quit       { color: #ff4444; }
    .btn_quit:hover { background: #ff4444; color: white; }

    /* COL DERECHA */
    #right_col { height: 100%; }
    #input_area { height: auto; margin-bottom: 1; }
    Input        { border: tall #ff00ff; background: #000; color: white; width: 1fr; }
    Input:focus  { border: double #00ffcc; }

    #controls { height: auto; margin-bottom: 1; }
    Select  { width: 1fr; margin-right: 1; }
    Button  { background: #00ffcc; color: black; text-style: bold; width: 100%; margin-top: 1; }

    #capture_scroll { height: 1fr; border: solid #333; background: #000; margin-bottom: 1; }
    #active_capture { width: 100%; padding: 1; }

    #stats_panel  { height: auto; background: #000; padding: 1; border: solid #ff00ff; }
    #session_msg  { background: #000; color: #ff00ff; text-style: bold; padding: 0 1; margin-bottom: 1; border: solid #ff00ff; }
    """

    BINDINGS = [
        ("f1",     "help",      "F1 AYUDA / INFO"),
        ("f2",     "config",    "F2 CONFIGURAR"),
        ("f3",     "batch",     "F3 CARGA MASIVA"),
        ("f4",     "history",   "F4 HISTORIAL"),
        ("f5",     "retry",     "F5 REINTENTAR"),
        ("f11",    "maximize",  "F11 PANTALLA COMPLETA"),
        ("ctrl+c", "cancelar",  "CTRL+C ABORTAR / SALIR"),
        ("escape", "quit_app",  "ESC SALIR"),
    ]

    def action_help(self):    self.push_screen(HelpScreen())
    def action_config(self):  self.push_screen(ConfigScreen())
    def action_batch(self):   self.push_screen(BatchScreen())
    def action_history(self): self.push_screen(HistoryScreen())

    def action_maximize(self) -> None:
        """F11 — alterna cabecera y pie para ganar espacio vertical."""
        header  = self.query_one(Header)
        footer  = self.query_one(Footer)
        header.visible = not header.visible
        footer.visible = not footer.visible

    def action_quit_app(self) -> None:
        """ESC — sale de la app limpiamente."""
        self.exit()

    def action_cancelar(self) -> None:
        """Ctrl+C — cancela si hay descarga activa; sale si está idle."""
        with state.lock:
            is_active = "SCANNING" in state.session_status or "LINKED" in state.session_status
        if is_active:
            with state.lock:
                state.cancel_requested = True
            self.notify("🛑 Cancelación forzada enviada al motor.", severity="warning")
        else:
            self.exit()

    def action_retry(self):
        with state.lock:
            if not state.failed_vids:
                self.notify("No hay fallos para reintentar.", severity="warning")
                return
            for vid in state.failed_vids:
                url = f"https://music.youtube.com/watch?v={vid}"
                add_download(url, "6", "3")
            count = len(state.failed_vids)
            state.failed_vids.clear()
            state.session_errors.clear()
            self.notify(f"Reencolando {count} pistas fallidas en Modo Recuperación...", severity="information")

    def on_mount(self) -> None:
        self.push_screen(BootScreen())
        self.set_interval(0.5, self.refresh_state)
        self.set_interval(2.0, self._check_clipboard)

    def _check_clipboard(self) -> None:
        """Detecta URLs de YouTube en el portapapeles y las pega en el input."""
        try:
            clip = _get_clipboard()
            if clip == self._last_clipboard:
                return
            self._last_clipboard = clip
            if clip.startswith("http") and ("youtube.com" in clip or "youtu.be" in clip or "music.youtube.com" in clip):
                inp = self.query_one("#url_input", Input)
                if not inp.value:
                    inp.value = clip
                    self.notify("URL detectada en el portapapeles.", severity="information")
        except Exception:
            pass

    def check_library(self, _=None):
        load_config()
        if not state.library_path:
            self.push_screen(SetupScreen())
        else:
            self.update_header_info()
            if has_pending_session():
                self.push_screen(ResumeScreen())
            else:
                load_queue_from_disk(resume_requested=False)
                self.query_one("#url_input", Input).focus()

    def update_header_info(self, _=None):
        lib_path = state.library_path or "Desconocida"
        display_path = ("..." + lib_path[-27:]) if len(lib_path) > 30 else lib_path
        self.query_one("#info_lib", Static).update(f"[ BIBLIOTECA: {display_path} ]")
        try:
            total, used, free = shutil.disk_usage(state.library_path)
            free_gb = free // (2**30)
            self.query_one("#info_space", Static).update(f"[ ESPACIO: {free_gb}GB Libres ]")
        except Exception:
            self.query_one("#info_space", Static).update("[ ESPACIO: Error de lectura ]")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal(id="top_info"):
            yield Static("[ BIBLIOTECA: Cargando... ]",   id="info_lib",    classes="info_tag")
            yield Static("[ ESPACIO: Calculando... ]",    id="info_space",  classes="info_tag")
            yield Static("[ MOTOR: yt-dlp Integrado ]",  id="info_engine", classes="info_tag")

        with Grid(id="main_grid"):
            with Vertical(id="left_col", classes="panel"):
                yield Label("ÚLTIMAS DESCARGAS", classes="panel-title")
                yield RichLog(id="log_descargas", highlight=True, markup=True)
                with Vertical(id="os_buttons"):
                    yield Button("📂 ABRIR BIBLIOTECA", id="btn_open_folder", classes="btn_os")
                    yield Button("📜 VER HISTORIAL",    id="btn_open_ledger", classes="btn_os")
                    yield Button("✖ SALIR",             id="btn_quit",        classes="btn_os btn_quit")

            with Vertical(id="right_col", classes="panel"):
                yield Label("URL DE CAPTURA", classes="panel-title")
                with Horizontal(id="input_area"):
                    yield Input(placeholder="Pegar enlace aquí...", id="url_input")

                with Horizontal(id="controls"):
                    yield Select(
                        [("1-Álbum", "1"), ("2-Recop", "2"), ("3-Playlist", "3"),
                         ("4-Mix", "4"), ("5-Disco", "5"), ("6-Huérfano", "6")],
                        prompt="MODO", id="select_mode",
                    )
                    yield Select(
                        [("1-Rápido", "1"), ("2-Seguro", "2"), ("3-Nocturno", "3")],
                        prompt="SPEED", id="select_speed",
                    )

                yield Button("INICIAR PROTOCOLO", id="btn_download")
                yield Static("READY // SISTEMA EN ESPERA", id="session_msg")
                with VerticalScroll(id="capture_scroll"):
                    yield Static("Esperando enlace de datos...\n───────────────────────────────────────────────────", id="active_capture")

                yield Label("ESTADÍSTICAS DE SESIÓN", classes="panel-title")
                yield Static("✅ 0 Descargadas  |  ⏭️ 0 Saltadas  |  ❌ 0 Fallos", id="stats_panel")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_download":
            with state.lock:
                is_active = "SCANNING" in state.session_status or "LINKED" in state.session_status
            if is_active:
                with state.lock:
                    state.cancel_requested = True
                self.notify("🛑 Abortando protocolo... (Por favor espera)", severity="warning")
            else:
                self.procesar_input()

        elif event.button.id == "btn_open_folder":
            if state.library_path and os.path.exists(state.library_path):
                _open_in_file_manager(state.library_path)
            else:
                self.notify("Ruta no encontrada.", severity="error")

        elif event.button.id == "btn_open_ledger":
            ledger = Path(state.library_path) / "Library_Ledger.log"
            if ledger.exists():
                _open_in_file_manager(str(ledger))
            else:
                self.notify("Aún no hay registros.", severity="warning")

        elif event.button.id == "btn_quit":
            self.exit()

    def procesar_input(self) -> None:
        self._last_capture_text = ""
        inp = self.query_one("#url_input", Input)
        url = inp.value.strip()
        if not url:
            return

        select_mode  = self.query_one("#select_mode",  Select)
        select_speed = self.query_one("#select_speed", Select)

        if (str(select_mode.value)  not in ["1", "2", "3", "4", "5", "6"] or
                str(select_speed.value) not in ["1", "2", "3"]):
            self.notify("⚠️ Selecciona MODO y SPEED antes de capturar.", severity="error")
            return

        add_download(url, str(select_mode.value), str(select_speed.value))
        inp.value = ""

    def refresh_state(self) -> None:
        capture_panel = self.query_one("#active_capture", Static)
        stats_panel   = self.query_one("#stats_panel",    Static)
        btn_action    = self.query_one("#btn_download",   Button)

        with state.lock:
            is_active = "SCANNING" in state.session_status or "LINKED" in state.session_status

            # Botón dinámico
            if is_active:
                btn_action.label = "🛑 DETENER PROTOCOLO"
                btn_action.styles.background = "#ff0033"
                btn_action.styles.color = "white"
            else:
                btn_action.label = "INICIAR PROTOCOLO"
                btn_action.styles.background = "#00ffcc"
                btn_action.styles.color = "black"

            # Status
            status_msg = self.query_one("#session_msg", Static)
            status_msg.update(state.session_status)
            if   "SCANNING"   in state.session_status: status_msg.styles.color = "#ffff00"
            elif "LINKED"     in state.session_status: status_msg.styles.color = "#00ffcc"
            elif "COMPLETED"  in state.session_status: status_msg.styles.color = "#00ff00"
            elif "ERROR"      in state.session_status: status_msg.styles.color = "#ff0000"
            else:                                       status_msg.styles.color = "#ff00ff"

            # Panel izquierdo: pistas terminadas
            while state.recent_finishes:
                ticket = state.recent_finishes.pop(0)
                log = self.query_one("#log_descargas", RichLog)
                if ticket[0] == "PARENT_LINK":
                    _, title, url = ticket
                    if url:
                        log.write(f"\n[bold cyan]Sincronizando:[/bold cyan] [link={url}][u bright_white]{title[:30]}[/u bright_white][/link]")
                    else:
                        log.write(f"\n[bold cyan]Sincronizando:[/bold cyan] {title[:30]}")
                elif ticket[0] == "SKIPPED":
                    _, title = ticket
                    log.write(f" • [bold yellow]EXISTE:[/bold yellow] {title[:40]}")
                elif ticket[0] == "M3U8":
                    _, p_name = ticket
                    log.write(f" • [bold magenta]PLAYLIST M3U8:[/bold magenta] {p_name}")
                else:
                    title, url = ticket
                    if url:
                        log.write(f" • [bold green]OK:[/bold green] [link={url}][u bright_white]{title[:40]}[/u bright_white][/link]")
                    else:
                        log.write(f" • [bold green]OK:[/bold green] {title[:40]}")

            # Estadísticas con timer acumulado y cola pendiente
            s = state.global_stats
            if is_active and s.get("start_time", 0.0) > 0:
                elapsed  = time.time() - s["start_time"]
                time_str = f"{elapsed:.1f}s"
            else:
                time_str = s.get("total_time", "0s")

            session_elapsed = ""
            if state.session_start_time > 0:
                total_s = int(time.time() - state.session_start_time)
                h, rem  = divmod(total_s, 3600)
                m, sec  = divmod(rem, 60)
                session_elapsed = f"  |  🕐 Sesión {h:02d}:{m:02d}:{sec:02d}" if h else f"  |  🕐 Sesión {m:02d}:{sec:02d}"

            queue_str = f"  |  📋 Cola: {state.pending_queue_count}" if state.pending_queue_count > 0 else ""

            stats_panel.update(
                f"✅ {s['success']} Descargadas  |  "
                f"⏭️ {s['skipped']} Saltadas  |  "
                f"❌ {s['failed']} Fallos  |  "
                f"⏱️ {time_str}"
                f"{session_elapsed}"
                f"{queue_str}"
            )

            # Panel derecho: progreso activo
            if not state.active_downloads and not state.session_errors:
                if self._last_capture_text:
                    capture_panel.update(self._last_capture_text)
                else:
                    capture_panel.update("[dim]Sistema inactivo. Esperando tareas.[/dim]\n───────────────────────────────────────────────────")
            else:
                display_text = "Sincronizando pistas:\n───────────────────────────────────────────────────\n"
                now = time.time()
                for vid, data in state.active_downloads.items():
                    title    = data.get("title", "Desconocido")[:38].replace("[", "(").replace("]", ")")
                    prog     = data.get("progress", 0.0)
                    bar_len  = 20
                    filled   = int((prog / 100) * bar_len)
                    bar      = "▓" * filled + "░" * (bar_len - filled)
                    stuck    = ""
                    last_t   = data.get("last_progress", now)
                    if now - last_t > 300 and prog < 100:   # 5 min sin avance
                        stuck = " [bold yellow]⚠ posiblemente colgado[/bold yellow]"
                    display_text += f"│ {title:<38} │ {prog:>4.1f}% \\[{bar}]{stuck}\n"

                if state.session_errors:
                    display_text += "\n[bold red]── REGISTRO DE ALERTAS ──[/bold red]\n"
                    for err in state.session_errors:
                        display_text += f"❌ [bold red]ERROR:[/bold red] {err}\n"
                    if not state.active_downloads and state.failed_vids:
                        display_text += "\n[bold yellow]⚠️ Presiona \\[F5] para reintentar los fallos en Modo Nocturno.[/bold yellow]\n"

                display_text += "───────────────────────────────────────────────────"
                self._last_capture_text = display_text
                capture_panel.update(display_text)
                self.query_one("#capture_scroll", VerticalScroll).scroll_end(animate=False)


def run_tui():
    app = MusicGrabberTUI()
    app.run()
