import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import time
import sys
import os
import subprocess
import shutil
from pathlib import Path

from core.state import state, load_config, save_config
from core.downloader import add_download, start_download_worker, load_queue_from_disk, has_pending_session

APP_VERSION = "v1.1.1"

MODES  = ["1 — Álbum", "2 — Recopilatorio", "3 — Playlist",
          "4 — Mix", "5 — Discografía", "6 — Huérfano"]
SPEEDS = ["1 — Rápido", "2 — Seguro", "3 — Nocturno"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_clipboard() -> str:
    """Devuelve el contenido del portapapeles sin subprocesos pesados."""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            CF_UNICODETEXT = 13
            if not ctypes.windll.user32.OpenClipboard(0):
                return ""
            try:
                handle = ctypes.windll.user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return ""
                p = ctypes.windll.kernel32.GlobalLock(handle)
                if not p:
                    return ""
                try:
                    return ctypes.wstring_at(p).strip()
                finally:
                    ctypes.windll.kernel32.GlobalUnlock(handle)
            finally:
                ctypes.windll.user32.CloseClipboard()
        else:
            for cmd in [["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"]]:
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                    if r.returncode == 0:
                        return r.stdout.strip()
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
    except Exception:
        pass
    return ""


def _open_path(path: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)          # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _center_on(win: tk.Toplevel, parent: tk.Tk) -> None:
    win.update_idletasks()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    px, py = parent.winfo_x(), parent.winfo_y()
    w, h   = win.winfo_width(), win.winfo_height()
    win.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")


# ---------------------------------------------------------------------------
# Diálogo: Ajustes
# ---------------------------------------------------------------------------

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self.title("Ajustes")
        self.resizable(False, False)
        self.grab_set()
        self._parent = parent
        self._build()
        _center_on(self, parent)

    def _build(self) -> None:
        pad = dict(padx=20, pady=6)

        ttk.Label(self, text="Formato de audio").grid(row=0, column=0, sticky="w", **pad)
        self._fmt = tk.StringVar(value=state.audio_format)
        ttk.Combobox(self, textvariable=self._fmt, state="readonly", width=20,
                     values=["mp3", "flac", "ogg"]).grid(row=0, column=1, **pad)

        ttk.Label(self, text="Calidad MP3 / OGG (kbps)").grid(row=1, column=0, sticky="w", **pad)
        self._quality = tk.StringVar(value=state.audio_quality)
        ttk.Combobox(self, textvariable=self._quality, state="readonly", width=20,
                     values=["128", "192", "256", "320"]).grid(row=1, column=1, **pad)

        ttk.Label(self, text="(irrelevante para FLAC)",
                  foreground="gray").grid(row=2, column=0, columnspan=2, padx=20, pady=(0, 6))

        ttk.Separator(self, orient="horizontal").grid(row=3, column=0, columnspan=2, sticky="ew", padx=8)

        btn = ttk.Frame(self)
        btn.grid(row=4, column=0, columnspan=2, padx=16, pady=12, sticky="ew")
        ttk.Button(btn, text="Cambiar biblioteca", command=self._change_lib).pack(side="left")
        ttk.Button(btn, text="Cancelar",           command=self.destroy).pack(side="right")
        ttk.Button(btn, text="Guardar",            command=self._save).pack(side="right", padx=6)

    def _save(self) -> None:
        save_config(fmt=self._fmt.get(), quality=self._quality.get())
        self.destroy()

    def _change_lib(self) -> None:
        self.destroy()
        path = filedialog.askdirectory(title="Seleccionar carpeta de biblioteca", parent=self._parent)
        if path:
            save_config(path=path)
            self._parent._refresh_infobar()


# ---------------------------------------------------------------------------
# Diálogo: Lote
# ---------------------------------------------------------------------------

class BatchDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self.title("Carga por lotes")
        self.geometry("520x400")
        self.grab_set()
        self._build()
        _center_on(self, parent)

    def _build(self) -> None:
        ttk.Label(self, text="Pega aquí los enlaces (uno por línea):").pack(
            anchor="w", padx=16, pady=(12, 4))

        txt_frame = ttk.Frame(self)
        txt_frame.pack(fill="both", expand=True, padx=16)
        self._text = tk.Text(txt_frame, wrap="none", font=("", 9))
        sb = ttk.Scrollbar(txt_frame, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=sb.set)
        self._text.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        ctrl = ttk.Frame(self)
        ctrl.pack(fill="x", padx=16, pady=8)

        ttk.Label(ctrl, text="Modo:").grid(row=0, column=0, sticky="w")
        self._mode = tk.StringVar()
        mode_cb = ttk.Combobox(ctrl, textvariable=self._mode, state="readonly",
                                values=MODES, width=20)
        mode_cb.current(2)
        mode_cb.grid(row=0, column=1, padx=(8, 0), pady=3)

        ttk.Label(ctrl, text="Velocidad:").grid(row=1, column=0, sticky="w")
        self._speed = tk.StringVar()
        speed_cb = ttk.Combobox(ctrl, textvariable=self._speed, state="readonly",
                                 values=SPEEDS, width=20)
        speed_cb.current(1)
        speed_cb.grid(row=1, column=1, padx=(8, 0), pady=3)

        btn = ttk.Frame(ctrl)
        btn.grid(row=2, column=0, columnspan=2, pady=(8, 0), sticky="ew")
        ttk.Button(btn, text="Cancelar",            command=self.destroy).pack(side="right")
        ttk.Button(btn, text="Encolar todo el lote", command=self._enqueue).pack(side="right", padx=6)

    def _enqueue(self) -> None:
        text  = self._text.get("1.0", "end")
        mode  = self._mode.get().split(" ")[0]
        speed = self._speed.get().split(" ")[0]
        urls  = [u.strip() for u in text.splitlines() if u.strip().startswith("http")]
        if not urls:
            messagebox.showwarning("Sin URLs", "No se encontraron URLs válidas.", parent=self)
            return
        for u in urls:
            add_download(u, mode, speed)
        messagebox.showinfo("Encolado", f"{len(urls)} enlace(s) añadidos a la cola.", parent=self)
        self.destroy()


# ---------------------------------------------------------------------------
# Diálogo: Historial
# ---------------------------------------------------------------------------

class HistoryDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self.title("Historial de descargas")
        self.geometry("660x440")
        self.grab_set()
        self._build()
        _center_on(self, parent)
        self._load()

    def _build(self) -> None:
        ttk.Label(self, text="Últimas 200 descargas registradas").pack(
            anchor="w", padx=16, pady=(12, 4))

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=16)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self._text = tk.Text(frame, state="disabled", wrap="none",
                             font=("Courier", 9), relief="groove", borderwidth=1)
        sb_y = ttk.Scrollbar(frame, orient="vertical",   command=self._text.yview)
        sb_x = ttk.Scrollbar(frame, orient="horizontal", command=self._text.xview)
        self._text.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        self._text.grid(row=0, column=0, sticky="nsew")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")

        ttk.Button(self, text="Cerrar", command=self.destroy).pack(pady=8)

    def _load(self) -> None:
        import re
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")

        if not state.library_path:
            self._text.insert("end", "Sin biblioteca configurada.")
            self._text.configure(state="disabled")
            return

        ledger = Path(state.library_path) / "Library_Ledger.log"
        if not ledger.exists():
            self._text.insert("end", "El historial está vacío.")
            self._text.configure(state="disabled")
            return

        try:
            lines   = ledger.read_text(encoding="utf-8").splitlines()
            entries = lines[-200:]
            for i, line in enumerate(reversed(entries), 1):
                m = re.match(r'^youtube ([a-zA-Z0-9_-]{11}) "(.*)"$', line.strip())
                if m:
                    vid      = m.group(1)
                    filename = Path(m.group(2)).name
                    self._text.insert("end", f"{i:>3}.  {filename}  [{vid}]\n")
                else:
                    self._text.insert("end", f"      {line}\n")
            self._text.insert("end", f"\n── {len(entries)} entrada(s) mostrada(s) ──")
        except Exception as e:
            self._text.insert("end", f"Error al leer historial: {e}")

        self._text.configure(state="disabled")


# ---------------------------------------------------------------------------
# Diálogo: Reanudar sesión interrumpida
# ---------------------------------------------------------------------------

class ResumeDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, on_resume, on_discard):
        super().__init__(parent)
        self.title("Sesión interrumpida")
        self.resizable(False, False)
        self.grab_set()
        self._on_resume  = on_resume
        self._on_discard = on_discard
        self._build()
        _center_on(self, parent)

    def _build(self) -> None:
        ttk.Label(self, text="Sesión interrumpida detectada",
                  font=("", 11, "bold")).pack(padx=28, pady=(20, 8))
        ttk.Label(self,
                  text="El sistema se cerró mientras había descargas en curso.\n"
                       "¿Aplicar rollback y reanudar la cola automáticamente?",
                  justify="center").pack(padx=28, pady=(0, 16))

        btn = ttk.Frame(self)
        btn.pack(pady=(0, 16))
        ttk.Button(btn, text="Sí, reanudar",  command=self._resume).pack(side="left", padx=8)
        ttk.Button(btn, text="No, descartar", command=self._discard).pack(side="left", padx=8)

    def _resume(self) -> None:
        self.destroy()
        self._on_resume()

    def _discard(self) -> None:
        self.destroy()
        self._on_discard()


# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------

class MusicGrabberGUI(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(f"Music Grabber {APP_VERSION}")
        self.geometry("800x530")
        self.minsize(660, 460)
        self._last_clipboard = ""
        self._build_ui()
        self._setup_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Arrancar bootstrap tras el primer ciclo de eventos
        self.after(100, self._run_bootstrap)

    def _setup_icon(self) -> None:
        icon = Path(__file__).parent.parent / "assets" / "app.ico"
        if icon.exists():
            try:
                if sys.platform.startswith("win"):
                    self.iconbitmap(str(icon))
                else:
                    img = tk.PhotoImage(file=str(
                        Path(__file__).parent.parent / "assets" / "logo.png"))
                    self.iconphoto(True, img)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Construcción de la UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ── Barra de info ──────────────────────────────────────────────
        info = ttk.Frame(self)
        info.pack(fill="x", padx=8, pady=(6, 0))

        self._lib_lbl   = ttk.Label(info, text="Biblioteca: —",         font=("", 9))
        self._space_lbl = ttk.Label(info, text="Espacio libre: —",      font=("", 9))
        self._engine_lbl= ttk.Label(info, text="Motor: yt-dlp",         font=("", 9))
        self._lib_lbl.pack(side="left", padx=(0, 16))
        self._space_lbl.pack(side="left", padx=(0, 16))
        self._engine_lbl.pack(side="right")

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=(6, 0))

        # ── Barra de herramientas ──────────────────────────────────────
        tb = ttk.Frame(self)
        tb.pack(fill="x", padx=8, pady=4)
        ttk.Button(tb, text="Lote",        command=self._open_batch).pack(side="left", padx=2)
        ttk.Button(tb, text="Historial",   command=self._open_history).pack(side="left", padx=2)
        ttk.Button(tb, text="Reintentar",  command=self._retry_failed).pack(side="left", padx=2)
        ttk.Button(tb, text="Ajustes",     command=self._open_settings).pack(side="right", padx=2)

        ttk.Separator(self, orient="horizontal").pack(fill="x")

        # ── Cuerpo principal ───────────────────────────────────────────
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        self._build_left(body)
        self._build_right(body)

    def _build_left(self, parent: ttk.Frame) -> None:
        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="Últimas descargas",
                  font=("", 9, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))

        log_frame = ttk.Frame(left, relief="groove", borderwidth=1)
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self._log = tk.Text(log_frame, state="disabled", wrap="word",
                            font=("", 9), relief="flat", padx=6, pady=4,
                            cursor="arrow")
        sb = ttk.Scrollbar(log_frame, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        self._log.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        self._log.tag_configure("ok",   foreground="#3B6D11")
        self._log.tag_configure("skip", foreground="#854F0B")
        self._log.tag_configure("err",  foreground="#A32D2D")
        self._log.tag_configure("head", foreground="#185FA5", font=("", 9, "bold"))
        self._log.tag_configure("dim",  foreground="gray")

        ttk.Button(left, text="Abrir biblioteca",
                   command=self._open_library_folder).grid(
                   row=2, column=0, sticky="ew", pady=(6, 0))

    def _build_right(self, parent: ttk.Frame) -> None:
        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        # URL
        ttk.Label(right, text="URL de YouTube o YouTube Music",
                  font=("", 9)).grid(row=0, column=0, sticky="w")
        self._url_var = tk.StringVar()
        self._url_entry = ttk.Entry(right, textvariable=self._url_var, font=("", 10))
        self._url_entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self._url_entry.bind("<Return>", lambda _: self._on_download_click())

        # Selects
        sel = ttk.Frame(right)
        sel.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        sel.columnconfigure(0, weight=1)
        sel.columnconfigure(1, weight=1)

        ttk.Label(sel, text="Modo",      font=("", 9)).grid(row=0, column=0, sticky="w")
        ttk.Label(sel, text="Velocidad", font=("", 9)).grid(row=0, column=1, sticky="w", padx=(8, 0))

        self._mode_var = tk.StringVar()
        self._mode_cb  = ttk.Combobox(sel, textvariable=self._mode_var,
                                       state="readonly", values=MODES)
        self._mode_cb.current(2)
        self._mode_cb.grid(row=1, column=0, sticky="ew", pady=(3, 0))

        self._speed_var = tk.StringVar()
        self._speed_cb  = ttk.Combobox(sel, textvariable=self._speed_var,
                                        state="readonly", values=SPEEDS)
        self._speed_cb.current(1)
        self._speed_cb.grid(row=1, column=1, sticky="ew", pady=(3, 0), padx=(8, 0))

        # Botón de descarga
        self._dl_btn = ttk.Button(right, text="Descargar",
                                   command=self._on_download_click)
        self._dl_btn.grid(row=3, column=0, sticky="ew", pady=(10, 0), ipady=4)

        # Estado
        self._status_var = tk.StringVar(value="Iniciando...")
        ttk.Label(right, textvariable=self._status_var,
                  font=("", 9), foreground="gray").grid(
                  row=4, column=0, sticky="w", pady=(6, 0))

        # Progreso
        prog = ttk.Frame(right, relief="groove", borderwidth=1)
        prog.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        prog.columnconfigure(0, weight=1)

        self._prog_name = tk.StringVar(value="—")
        ttk.Label(prog, textvariable=self._prog_name,
                  font=("", 9), anchor="w").grid(
                  row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(6, 2))

        self._progressbar = ttk.Progressbar(prog, orient="horizontal",
                                             mode="determinate", maximum=100)
        self._progressbar.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 2))

        self._prog_pct = tk.StringVar(value="")
        ttk.Label(prog, textvariable=self._prog_pct,
                  font=("", 9), width=5, anchor="e").grid(row=1, column=1, padx=(0, 8))

        self._prog_status = tk.StringVar(value="")
        ttk.Label(prog, textvariable=self._prog_status,
                  font=("", 8), foreground="gray", anchor="w").grid(
                  row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 6))

        # Stats
        stats = ttk.Frame(right)
        stats.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        for i in range(4):
            stats.columnconfigure(i, weight=1)

        self._s_ok   = tk.StringVar(value="0")
        self._s_skip = tk.StringVar(value="0")
        self._s_err  = tk.StringVar(value="0")
        self._s_time = tk.StringVar(value="—")

        for col, (var, label, color) in enumerate([
            (self._s_ok,   "Descargadas", "#3B6D11"),
            (self._s_skip, "Saltadas",    "#854F0B"),
            (self._s_err,  "Fallos",      "#A32D2D"),
            (self._s_time, "Sesión",      None),
        ]):
            box = ttk.Frame(stats, relief="groove", borderwidth=1)
            box.grid(row=0, column=col, sticky="ew", padx=3)
            kw = {"foreground": color} if color else {}
            tk.Label(box, textvariable=var, font=("", 13, "bold"), **kw).pack(pady=(4, 0))
            tk.Label(box, text=label,       font=("", 8), foreground="gray").pack(pady=(0, 4))

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _run_bootstrap(self) -> None:
        self._status_var.set("Comprobando motor...")
        self._dl_btn.state(["disabled"])

        def worker():
            from core.bootstrap import ensure_dependencies
            ensure_dependencies(
                lambda msg: self.after(0, lambda m=msg: self._status_var.set(m))
            )
            start_download_worker()
            self.after(0, self._after_bootstrap)

        threading.Thread(target=worker, daemon=True).start()

    def _after_bootstrap(self) -> None:
        load_config()
        self._refresh_infobar()
        self._dl_btn.state(["!disabled"])

        if not state.library_path:
            self._ask_library(first_run=True)
        elif has_pending_session():
            ResumeDialog(
                self,
                on_resume=lambda:  load_queue_from_disk(resume_requested=True),
                on_discard=lambda: load_queue_from_disk(resume_requested=False),
            )
        else:
            load_queue_from_disk(resume_requested=False)

        self._status_var.set("Listo — esperando enlace")
        self.after(500,  self._poll_state)
        self.after(2000, self._poll_clipboard)

    def _ask_library(self, first_run: bool = False) -> None:
        msg = ("Es la primera vez que arranca Music Grabber.\n"
               "Selecciona dónde quieres guardar tu biblioteca de música.") if first_run else \
              "Selecciona la carpeta de biblioteca."
        messagebox.showinfo("Biblioteca de música", msg, parent=self)
        path = filedialog.askdirectory(title="Seleccionar carpeta de biblioteca", parent=self)
        if path:
            save_config(path=path)
            self._refresh_infobar()
        elif first_run:
            # Sin biblioteca la app no puede funcionar
            messagebox.showerror("Necesario", "Debes seleccionar una carpeta para continuar.", parent=self)
            self._ask_library(first_run=True)

    def _refresh_infobar(self) -> None:
        lib = state.library_path or "—"
        display = ("…" + lib[-32:]) if len(lib) > 35 else lib
        self._lib_lbl.configure(text=f"Biblioteca: {display}")
        try:
            _, _, free = shutil.disk_usage(lib)
            self._space_lbl.configure(text=f"Espacio libre: {free // (2 ** 30)} GB")
        except Exception:
            self._space_lbl.configure(text="Espacio libre: —")

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll_state(self) -> None:
        try:
            self._update_from_state()
        except Exception:
            pass
        self.after(500, self._poll_state)

    def _poll_clipboard(self) -> None:
        try:
            clip = _get_clipboard()
            if clip != self._last_clipboard:
                self._last_clipboard = clip
                is_yt = any(d in clip for d in ("youtube.com", "youtu.be", "music.youtube.com"))
                if clip.startswith("http") and is_yt and not self._url_var.get():
                    self._url_var.set(clip)
                    self._status_var.set("URL detectada en el portapapeles")
        except Exception:
            pass
        self.after(2000, self._poll_clipboard)

    def _update_from_state(self) -> None:
        with state.lock:
            is_active = "SCANNING" in state.session_status or "LINKED" in state.session_status

            # Botón
            self._dl_btn.configure(text="Detener" if is_active else "Descargar")

            # Mensaje de estado
            for key, label in [
                ("SCANNING",  "Analizando enlace..."),
                ("LINKED",    "Descargando..."),
                ("COMPLETED", "Completado"),
                ("ABORTED",   "Detenido"),
                ("ERROR",     "Error en la descarga"),
                ("READY",     "Listo — esperando enlace"),
            ]:
                if key in state.session_status:
                    self._status_var.set(label)
                    break

            # Log
            while state.recent_finishes:
                ticket = state.recent_finishes.pop(0)
                self._log.configure(state="normal")
                if ticket[0] == "PARENT_LINK":
                    _, title, _ = ticket
                    self._log.insert("end", f"\n▶ {title[:42]}\n", "head")
                elif ticket[0] == "SKIPPED":
                    _, title = ticket
                    self._log.insert("end", f"  Existe: {title[:40]}\n", "skip")
                elif ticket[0] == "M3U8":
                    _, name = ticket
                    self._log.insert("end", f"  Playlist: {name}\n", "dim")
                else:
                    title, _ = ticket
                    self._log.insert("end", f"  OK: {title[:40]}\n", "ok")
                self._log.see("end")
                self._log.configure(state="disabled")

            # Progreso (muestra la primera descarga activa)
            if state.active_downloads:
                vid, data = next(iter(state.active_downloads.items()))
                self._prog_name.set(data.get("title", "—")[:50])
                prog = data.get("progress", 0.0)
                self._progressbar["value"] = prog
                self._prog_pct.set(f"{prog:.0f}%")
                self._prog_status.set(data.get("status", ""))
            else:
                self._prog_name.set("—")
                self._progressbar["value"] = 0
                self._prog_pct.set("")
                self._prog_status.set("")

            # Estadísticas
            s = state.global_stats
            self._s_ok.set(str(s["success"]))
            self._s_skip.set(str(s["skipped"]))
            self._s_err.set(str(s["failed"]))

            if state.session_start_time > 0:
                total = int(time.time() - state.session_start_time)
                h, r  = divmod(total, 3600)
                m, s_ = divmod(r, 60)
                self._s_time.set(f"{h:02d}:{m:02d}:{s_:02d}" if h else f"{m:02d}:{s_:02d}")
            else:
                self._s_time.set("—")

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------

    def _on_download_click(self) -> None:
        with state.lock:
            is_active = "SCANNING" in state.session_status or "LINKED" in state.session_status
        if is_active:
            with state.lock:
                state.cancel_requested = True
            self._status_var.set("Cancelando...")
        else:
            self._process_input()

    def _process_input(self) -> None:
        url = self._url_var.get().strip()
        if not url:
            return

        is_yt = any(d in url for d in ("youtube.com", "youtu.be", "music.youtube.com"))
        if not url.startswith("http") or not is_yt:
            messagebox.showwarning(
                "URL no reconocida",
                "Debe ser un enlace de YouTube o YouTube Music.",
                parent=self,
            )
            return

        mode  = self._mode_var.get().split(" ")[0]
        speed = self._speed_var.get().split(" ")[0]
        add_download(url, mode, speed)
        self._url_var.set("")

    def _open_library_folder(self) -> None:
        if state.library_path and Path(state.library_path).exists():
            _open_path(state.library_path)
        else:
            messagebox.showwarning("Sin biblioteca",
                                   "Configura primero una carpeta de biblioteca.", parent=self)

    def _open_batch(self)    -> None: BatchDialog(self)
    def _open_history(self)  -> None: HistoryDialog(self)
    def _open_settings(self) -> None: SettingsDialog(self)

    def _retry_failed(self) -> None:
        with state.lock:
            if not state.failed_vids:
                messagebox.showinfo("Sin fallos",
                                    "No hay pistas fallidas para reintentar.", parent=self)
                return
            count = len(state.failed_vids)
            for vid in state.failed_vids:
                add_download(f"https://music.youtube.com/watch?v={vid}", "6", "3")
            state.failed_vids.clear()
            state.session_errors.clear()
        messagebox.showinfo("Reencolado",
                            f"{count} pista(s) añadidas en Modo Nocturno.", parent=self)

    def _on_close(self) -> None:
        state.is_running = False
        self.destroy()


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def run_gui() -> None:
    app = MusicGrabberGUI()
    app.mainloop()
