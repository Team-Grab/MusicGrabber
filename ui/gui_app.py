"""
GUI principal de Music Grabber v2.0.

Layout:
  PanedWindow horizontal: sidebar (vistas) | panel central (contenido).
  Barra superior: ruta de biblioteca + espacio libre + acciones.
  Barra inferior: estado de descarga + barra de progreso.

Vistas:
  • Descargar       — campo URL + botón. Sin modos manuales.
  • Biblioteca      — tabla con pistas del índice SQLite. Doble clic → reproducir.
  • Sin metadatos   — bandeja de revisión. Aceptar candidato MB o dejar como está.
"""

import json
import os
import sys
import shutil
import subprocess
import threading
import time
import tkinter as tk
import unicodedata
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
from typing import Optional


def _normalize(s) -> str:
    """Normaliza texto para comparación: quita acentos y pasa a minúsculas.
    'Tío Sam' → 'tio sam' → encontrable con 'tio'."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()

from core.state import state, load_config, save_config, log_event
from core.downloader import (
    add_download, start_download_worker, load_queue_from_disk, has_pending_session
)
from core import library, pipeline, musicbrainz, player as player_mod, bpm as bpm_mod
from core.player import player

APP_VERSION = "v2.0-dev"


# ---------------------------------------------------------------------------
# Paleta oscura
# ---------------------------------------------------------------------------

DARK = {
    "bg":           "#1f2024",
    "panel":        "#2a2b30",
    "panel_alt":    "#33343a",
    "border":       "#3a3b40",
    "fg":           "#e8e8e8",
    "muted":        "#8a8b90",
    "accent":       "#4a90e2",
    "accent_fg":    "#ffffff",
    "selected_bg":  "#3b4a66",
    "selected_fg":  "#ffffff",
    "hover":        "#373840",
    "success_bg":   "#1f3a1f",
    "success_fg":   "#a3e0a3",
    "warn_fg":      "#f0ad4e",
    "err_fg":       "#ff7a7a",
    "info_fg":      "#cfd0d4",
    "mb_fg":        "#7ab6f0",
    "section":      "#6a6b70",
}


def _apply_dark_theme(root: tk.Tk) -> None:
    """Aplica una paleta oscura coherente a todos los widgets ttk y tk."""
    style = ttk.Style(root)
    # 'clam' es el theme ttk más customizable en Linux.
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    bg, panel, fg, muted = DARK["bg"], DARK["panel"], DARK["fg"], DARK["muted"]
    border, accent = DARK["border"], DARK["accent"]
    sel_bg, sel_fg = DARK["selected_bg"], DARK["selected_fg"]

    root.configure(bg=bg)

    # Defaults para los widgets tk vanilla (Text, Listbox, Menu)
    root.option_add("*Background",            bg)
    root.option_add("*Foreground",            fg)
    root.option_add("*selectBackground",      sel_bg)
    root.option_add("*selectForeground",      sel_fg)
    root.option_add("*Text.Background",       panel)
    root.option_add("*Text.Foreground",       fg)
    root.option_add("*Text.insertBackground", fg)
    root.option_add("*Listbox.Background",    panel)
    root.option_add("*Listbox.Foreground",    fg)
    root.option_add("*Menu.Background",       panel)
    root.option_add("*Menu.Foreground",       fg)
    root.option_add("*Menu.activeBackground", accent)
    root.option_add("*Menu.activeForeground", DARK["accent_fg"])

    # ttk styles
    style.configure(".",         background=bg, foreground=fg,
                                   fieldbackground=panel, bordercolor=border,
                                   focuscolor=accent)
    style.configure("TFrame",    background=bg)
    style.configure("TLabel",    background=bg, foreground=fg)
    style.configure("TSeparator", background=border)

    style.configure("TButton",   background=panel, foreground=fg,
                                   bordercolor=border, relief="flat",
                                   padding=6)
    style.map("TButton",
              background=[("active", DARK["hover"]), ("pressed", accent)],
              foreground=[("disabled", muted)])

    style.configure("TEntry",
                    fieldbackground=panel, foreground=fg,
                    insertcolor=fg, bordercolor=border, lightcolor=border)
    style.map("TEntry",
              fieldbackground=[("focus", DARK["panel_alt"])])

    style.configure("TCombobox",
                    fieldbackground=panel, foreground=fg, background=panel,
                    bordercolor=border, arrowcolor=fg, selectbackground=sel_bg,
                    selectforeground=sel_fg)
    root.option_add("*TCombobox*Listbox.background", panel)
    root.option_add("*TCombobox*Listbox.foreground", fg)
    root.option_add("*TCombobox*Listbox.selectBackground", accent)

    style.configure("TCheckbutton", background=bg, foreground=fg,
                                       focuscolor=accent)
    style.map("TCheckbutton",
              background=[("active", bg)],
              foreground=[("disabled", muted)])

    style.configure("TRadiobutton", background=bg, foreground=fg)
    style.map("TRadiobutton", background=[("active", bg)])

    style.configure("TProgressbar",
                    background=accent, troughcolor=panel, bordercolor=border)

    style.configure("Treeview",
                    background=panel, fieldbackground=panel, foreground=fg,
                    bordercolor=border, rowheight=24)
    style.configure("Treeview.Heading",
                    background=DARK["panel_alt"], foreground=fg,
                    bordercolor=border, relief="flat", padding=4)
    style.map("Treeview.Heading",
              background=[("active", DARK["hover"])])
    style.map("Treeview",
              background=[("selected", sel_bg)],
              foreground=[("selected", sel_fg)])

    style.configure("Vertical.TScrollbar",
                    background=panel, troughcolor=bg,
                    bordercolor=border, arrowcolor=fg)
    style.configure("Horizontal.TScrollbar",
                    background=panel, troughcolor=bg,
                    bordercolor=border, arrowcolor=fg)
    style.map("Vertical.TScrollbar",
              background=[("active", DARK["hover"])])
    style.map("Horizontal.TScrollbar",
              background=[("active", DARK["hover"])])

    style.configure("TPanedWindow", background=bg)
    style.configure("Sash",         background=border)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Nota: el polling automático del portapapeles (cada 2 s) se eliminó porque
# en algunos compositores Wayland (Nobara/KDE) llamar a wl-paste con esa
# frecuencia provocaba parpadeos en las ventanas del escritorio. El usuario
# puede pegar con Ctrl+V en el campo URL como en cualquier otra app.

def _open_path(path: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _center_on(win: tk.Toplevel, parent: tk.Tk) -> None:
    win.update_idletasks()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    px, py = parent.winfo_x(), parent.winfo_y()
    w, h = win.winfo_width(), win.winfo_height()
    win.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")


def _dark_toplevel(win: tk.Toplevel) -> None:
    """Aplica el fondo oscuro a un Toplevel. Sin esto el bg queda blanco
    porque los Toplevel no heredan el bg del root, y los Labels (que sí
    heredan style ttk con fg claro) acaban siendo invisibles sobre blanco."""
    try:
        win.configure(bg=DARK["bg"])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Diálogo: Ajustes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Widget custom: range slider de doble thumb sobre Canvas
# ---------------------------------------------------------------------------

class RangeSlider(tk.Canvas):
    """Slider horizontal con dos thumbs. tkinter no trae un range slider
    nativo. Implementación mínima sobre Canvas: track gris, dos óvalos
    draggable, callback `on_change(min, max)` en vivo.

    API: get() -> (min, max), set(min, max).
    """
    def __init__(self, parent, *, from_=0, to=100,
                 initial_min=0, initial_max=100,
                 width=240, height=34,
                 on_change=None,
                 bg=None, track="#444", track_active="#3a72c4", thumb="#cfd6e3"):
        super().__init__(parent, width=width, height=height,
                         highlightthickness=0,
                         bg=bg or parent.cget("background"))
        self._from = float(from_)
        self._to   = float(to)
        self._w    = int(width)
        self._h    = int(height)
        self._pad  = 12  # margen lateral para que el thumb no se salga
        self._track_color = track
        self._active_color = track_active
        self._thumb_color = thumb
        self._on_change = on_change
        self._min = max(self._from, min(self._to, float(initial_min)))
        self._max = max(self._min,  min(self._to, float(initial_max)))
        self._dragging = None  # "min" | "max" | None
        # Geometría del track
        self._ty = self._h // 2
        self._tx0 = self._pad
        self._tx1 = self._w - self._pad
        self._tw = self._tx1 - self._tx0
        # Bindings
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._redraw()

    def get(self):
        return self._min, self._max

    def set(self, min_v, max_v):
        self._min = max(self._from, min(self._to, float(min_v)))
        self._max = max(self._min,  min(self._to, float(max_v)))
        self._redraw()
        if self._on_change:
            self._on_change(self._min, self._max)

    # ---- internos ----
    def _val_to_x(self, v):
        frac = (v - self._from) / (self._to - self._from) if self._to > self._from else 0
        return self._tx0 + frac * self._tw

    def _x_to_val(self, x):
        x = max(self._tx0, min(self._tx1, x))
        frac = (x - self._tx0) / self._tw if self._tw else 0
        return self._from + frac * (self._to - self._from)

    def _nearest_thumb(self, x):
        dmin = abs(x - self._val_to_x(self._min))
        dmax = abs(x - self._val_to_x(self._max))
        # Si están encima, preferir el que mueva en dirección útil
        if dmin <= dmax:
            return "min"
        return "max"

    def _on_click(self, ev):
        which = self._nearest_thumb(ev.x)
        self._dragging = which
        self._update_from_event(ev)

    def _on_drag(self, ev):
        if self._dragging:
            self._update_from_event(ev)

    def _on_release(self, _ev):
        self._dragging = None

    def _update_from_event(self, ev):
        v = round(self._x_to_val(ev.x))
        if self._dragging == "min":
            v = min(v, self._max)
            if v != self._min:
                self._min = v
                self._redraw()
                if self._on_change:
                    self._on_change(self._min, self._max)
        elif self._dragging == "max":
            v = max(v, self._min)
            if v != self._max:
                self._max = v
                self._redraw()
                if self._on_change:
                    self._on_change(self._min, self._max)

    def _redraw(self):
        self.delete("all")
        # Track de fondo
        self.create_rectangle(self._tx0, self._ty - 3, self._tx1, self._ty + 3,
                              fill=self._track_color, outline="")
        # Track activo entre los thumbs
        xa = self._val_to_x(self._min)
        xb = self._val_to_x(self._max)
        self.create_rectangle(xa, self._ty - 3, xb, self._ty + 3,
                              fill=self._active_color, outline="")
        # Thumbs
        r = 8
        for x in (xa, xb):
            self.create_oval(x - r, self._ty - r, x + r, self._ty + r,
                             fill=self._thumb_color, outline="#222", width=1)


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self.title("Ajustes")
        self.resizable(False, False)
        _dark_toplevel(self)
        self.transient(parent)
        self._parent = parent
        # IMPORTANTE: construir widgets ANTES de grab_set / center_on.
        # En el orden contrario, en algunas combinaciones tcl/tk el grab
        # bloquea el render y la ventana sale vacía hasta redimensionar.
        try:
            self._build()
        except Exception as e:
            # Sin try/except, una excepción aquí dejaba la ventana vacía
            # y sin pista para diagnosticar.
            import traceback
            log_event("err", f"SettingsDialog._build: {e}")
            traceback.print_exc()
        self.update_idletasks()
        _center_on(self, parent)
        self.grab_set()

    def _build(self):
        pad = dict(padx=20, pady=6)

        ttk.Label(self, text="Formato de audio").grid(row=0, column=0, sticky="w", **pad)
        self._fmt = tk.StringVar(value=state.audio_format)
        ttk.Combobox(self, textvariable=self._fmt, state="readonly", width=20,
                     values=["mp3", "flac", "ogg"]).grid(row=0, column=1, **pad)

        ttk.Label(self, text="Calidad MP3 / OGG (kbps)").grid(row=1, column=0, sticky="w", **pad)
        self._quality = tk.StringVar(value=state.audio_quality)
        ttk.Combobox(self, textvariable=self._quality, state="readonly", width=20,
                     values=["128", "192", "256", "320"]).grid(row=1, column=1, **pad)

        ttk.Label(self, text="(irrelevante para FLAC)", foreground="gray").grid(
            row=2, column=0, columnspan=2, padx=20, pady=(0, 6))

        ttk.Separator(self, orient="horizontal").grid(row=3, column=0, columnspan=2, sticky="ew", padx=8)

        self._mb_enabled = tk.BooleanVar(value=state.musicbrainz_enabled)
        ttk.Checkbutton(
            self, variable=self._mb_enabled,
            text="Mejorar tags con MusicBrainz tras descargar",
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=20, pady=(10, 0))
        ttk.Label(
            self,
            text=("Si está activado, cada descarga se consulta en MusicBrainz para\n"
                  "aplicar metadatos canónicos y carátula. Tarda ~1 s extra por pista."),
            foreground="gray", justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=40, pady=(0, 8))

        ttk.Separator(self, orient="horizontal").grid(row=6, column=0, columnspan=2, sticky="ew", padx=8)

        # Crossfade (fase 2c.4)
        self._cf_enabled = tk.BooleanVar(value=state.crossfade_enabled)
        ttk.Checkbutton(
            self, variable=self._cf_enabled,
            text="Fundido encadenado entre pistas (crossfade)",
            command=self._on_cf_toggle,
        ).grid(row=7, column=0, columnspan=2, sticky="w", padx=20, pady=(10, 0))

        cf_row = ttk.Frame(self)
        cf_row.grid(row=8, column=0, columnspan=2, sticky="ew", padx=40, pady=(2, 8))
        ttk.Label(cf_row, text="Duración:").pack(side="left")
        self._cf_seconds = tk.IntVar(value=state.crossfade_seconds)
        self._cf_lbl = tk.StringVar(value=f"{state.crossfade_seconds} s")
        self._cf_scale = ttk.Scale(
            cf_row, from_=1, to=12, length=180, orient="horizontal",
            variable=self._cf_seconds,
            command=lambda v: self._cf_lbl.set(f"{int(float(v))} s"),
        )
        self._cf_scale.pack(side="left", padx=8)
        ttk.Label(cf_row, textvariable=self._cf_lbl, width=6,
                  foreground="gray").pack(side="left")
        # Estado coherente con el checkbox al abrir
        self._on_cf_toggle()

        ttk.Separator(self, orient="horizontal").grid(row=9, column=0, columnspan=2, sticky="ew", padx=8)

        # Modo fiesta (fase 2c.5) — range slider + presets
        ttk.Label(self, text="Modo fiesta", font=("", 9, "bold")).grid(
            row=10, column=0, sticky="w", padx=20, pady=(10, 0))

        # Presets de "feeling"
        presets_row = ttk.Frame(self)
        presets_row.grid(row=11, column=0, columnspan=2, sticky="ew", padx=40, pady=(2, 2))
        self._party_presets = [
            ("Chill",     70, 100),
            ("Pop-Rock", 100, 130),
            ("Bailable", 120, 145),
            ("Cardio",   140, 180),
        ]
        for name, lo, hi in self._party_presets:
            ttk.Button(presets_row, text=name,
                       command=lambda l=lo, h=hi: self._apply_party_preset(l, h)
                       ).pack(side="left", padx=2)

        # Range slider con doble thumb
        range_row = ttk.Frame(self)
        range_row.grid(row=12, column=0, columnspan=2, sticky="ew", padx=40, pady=(6, 2))
        ttk.Label(range_row, text="Rango BPM:").pack(side="left")
        self._party_range_lbl = tk.StringVar(
            value=f"{state.party_min_bpm} – {state.party_max_bpm}")
        # bg explícito para que el Canvas no salga gris claro en tema oscuro
        bg_dark = DARK["bg"]
        self._party_range = RangeSlider(
            range_row,
            from_=60, to=200,
            initial_min=state.party_min_bpm,
            initial_max=state.party_max_bpm,
            width=240, height=30,
            on_change=lambda mn, mx: self._party_range_lbl.set(
                f"{int(mn)} – {int(mx)}"),
            bg=bg_dark,
        )
        self._party_range.pack(side="left", padx=8)
        ttk.Label(range_row, textvariable=self._party_range_lbl, width=10,
                  foreground="gray").pack(side="left")

        pcf_row = ttk.Frame(self)
        pcf_row.grid(row=13, column=0, columnspan=2, sticky="ew", padx=40, pady=(2, 8))
        ttk.Label(pcf_row, text="Crossfade en fiesta:").pack(side="left")
        self._party_cf = tk.IntVar(value=state.party_crossfade_s)
        self._party_cf_lbl = tk.StringVar(value=f"{state.party_crossfade_s} s")
        ttk.Scale(
            pcf_row, from_=1, to=12, length=180, orient="horizontal",
            variable=self._party_cf,
            command=lambda v: self._party_cf_lbl.set(f"{int(float(v))} s"),
        ).pack(side="left", padx=8)
        ttk.Label(pcf_row, textvariable=self._party_cf_lbl, width=6,
                  foreground="gray").pack(side="left")

        ttk.Separator(self, orient="horizontal").grid(row=14, column=0, columnspan=2, sticky="ew", padx=8)

        btn = ttk.Frame(self)
        btn.grid(row=15, column=0, columnspan=2, padx=16, pady=12, sticky="ew")
        ttk.Button(btn, text="Cambiar biblioteca", command=self._change_lib).pack(side="left")
        ttk.Button(btn, text="Cancelar",           command=self.destroy).pack(side="right")
        ttk.Button(btn, text="Guardar",            command=self._save).pack(side="right", padx=6)

    def _on_cf_toggle(self):
        # Deshabilitar visualmente el slider si el crossfade está OFF
        try:
            self._cf_scale.configure(
                state="normal" if self._cf_enabled.get() else "disabled"
            )
        except Exception:
            pass

    def _apply_party_preset(self, lo: int, hi: int):
        """Reposiciona los thumbs del range slider en los valores del preset."""
        self._party_range.set(lo, hi)

    def _save(self):
        cf_seconds = int(self._cf_seconds.get())
        party_min, party_max = self._party_range.get()
        party_min = int(round(party_min))
        party_max = int(round(party_max))
        party_cf = int(self._party_cf.get())
        save_config(
            fmt=self._fmt.get(),
            quality=self._quality.get(),
            musicbrainz_enabled=self._mb_enabled.get(),
            crossfade_enabled=self._cf_enabled.get(),
            crossfade_seconds=cf_seconds,
            party_min_bpm=party_min,
            party_max_bpm=party_max,
            party_crossfade_s=party_cf,
        )
        try:
            # Solo aplicar crossfade del player si NO está activo el modo
            # fiesta: en fiesta el crossfade lo gestiona party_mode.
            st = player.get_state()
            if not st.party_enabled:
                player.set_crossfade(self._cf_enabled.get(), cf_seconds)
        except Exception:
            pass
        self.destroy()

    def _change_lib(self):
        self.destroy()
        path = filedialog.askdirectory(title="Seleccionar carpeta de biblioteca", parent=self._parent)
        if path:
            save_config(path=path)
            self._parent._refresh_infobar()


# ---------------------------------------------------------------------------
# Diálogo: progreso de enriquecimiento MusicBrainz
# ---------------------------------------------------------------------------

class EnrichProgressDialog(tk.Toplevel):
    """
    Modal con barra de progreso, contador y label de pista actual.
    Botón Cancelar marca self._cancelled; el thread debe consultarlo.
    """

    def __init__(self, parent: tk.Tk, total: int, *,
                 window_title: str = "Enriquecer con MusicBrainz",
                 heading: str = "Consultando MusicBrainz",
                 subheading: Optional[str] = None,
                 finish_template: str = "{applied} de {total} pistas enriquecidas con éxito."):
        super().__init__(parent)
        self.title(window_title)
        self.resizable(False, False)
        _dark_toplevel(self)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._total = total
        self._cancelled = False
        self._finish_template = finish_template

        frm = ttk.Frame(self, padding=20)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=heading,
                  font=("", 11, "bold")).pack(anchor="w")
        ttk.Label(frm,
                  text=subheading or (
                      f"{total} pistas en cola. Se respeta el rate limit de "
                      f"MusicBrainz (1 consulta/segundo)."),
                  foreground="gray", wraplength=440, justify="left").pack(
                       anchor="w", pady=(2, 14))

        self._count_var = tk.StringVar(value=f"0 / {total}")
        ttk.Label(frm, textvariable=self._count_var, font=("", 10)).pack(anchor="w")

        self._bar = ttk.Progressbar(frm, orient="horizontal",
                                    mode="determinate", maximum=total, length=460)
        self._bar.pack(fill="x", pady=(4, 10))

        self._track_var = tk.StringVar(value="—")
        ttk.Label(frm, textvariable=self._track_var,
                  foreground="#185FA5", wraplength=460, justify="left").pack(anchor="w")

        self._result_var = tk.StringVar(value="")
        ttk.Label(frm, textvariable=self._result_var,
                  foreground="gray").pack(anchor="w", pady=(2, 12))

        self._cancel_btn = ttk.Button(frm, text="Cancelar", command=self._on_cancel)
        self._cancel_btn.pack(anchor="e")

        _center_on(self, parent)

    def update_progress(self, done: int, current_title: str, last_result: str = ""):
        """Llamar desde el hilo principal vía .after(0, ...)."""
        self._count_var.set(f"{done} / {self._total}")
        self._bar["value"] = done
        self._track_var.set(f"Procesando: {current_title[:80]}")
        if last_result:
            self._result_var.set(last_result)

    def finish(self, applied: int, total: int, was_cancelled: bool = False):
        self._cancel_btn.configure(text="Cerrar", command=self.destroy)
        if was_cancelled:
            self._track_var.set("Cancelado por el usuario.")
        else:
            self._track_var.set("Proceso terminado.")
        self._result_var.set(self._finish_template.format(applied=applied, total=total))
        self._bar["value"] = self._bar["maximum"]

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def _on_cancel(self):
        self._cancelled = True
        self._cancel_btn.configure(text="Cerrando...", state="disabled")


# ---------------------------------------------------------------------------
# Diálogo: búsqueda manual en MusicBrainz
# ---------------------------------------------------------------------------

class ManualMBSearchDialog(tk.Toplevel):
    """Permite al usuario editar título/artista y buscar manualmente en MB.
    Si elige un candidato, se aplican los tags y se quita de la bandeja."""

    def __init__(self, parent, track_path: str, initial_title: str, initial_artist: str):
        super().__init__(parent)
        self.title("Buscar manualmente en MusicBrainz")
        self.geometry("760x560")
        self.minsize(640, 480)
        _dark_toplevel(self)
        self.transient(parent)
        self.grab_set()
        self._parent = parent
        self._path = track_path
        self._candidates: list[dict] = []

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        # Campos editables
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="Título:", width=10).pack(side="left")
        self._title_var = tk.StringVar(value=initial_title)
        ttk.Entry(row, textvariable=self._title_var).pack(side="left", fill="x", expand=True)

        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="Artista:", width=10).pack(side="left")
        self._artist_var = tk.StringVar(value=initial_artist)
        ttk.Entry(row, textvariable=self._artist_var).pack(side="left", fill="x", expand=True)

        ttk.Button(frm, text="Buscar en MusicBrainz",
                   command=self._do_search).pack(anchor="w", pady=(0, 8))

        self._status = ttk.Label(frm, text="", foreground="gray")
        self._status.pack(anchor="w", pady=(0, 4))

        # Tabla de resultados
        cols = ("title", "artist", "album", "year", "score")
        self._tree = ttk.Treeview(frm, columns=cols, show="headings", height=10)
        for col, label, w in [("title", "Título", 220), ("artist", "Artista", 140),
                                ("album", "Álbum", 200), ("year", "Año", 50),
                                ("score", "Score", 50)]:
            self._tree.heading(col, text=label)
            self._tree.column(col, width=w)
        self._tree.pack(fill="both", expand=True, pady=(0, 8))

        btn = ttk.Frame(frm)
        btn.pack(fill="x")
        ttk.Button(btn, text="Aplicar candidato", command=self._apply).pack(side="left")
        ttk.Button(btn, text="Cancelar", command=self.destroy).pack(side="right")

        _center_on(self, parent)

    def _do_search(self):
        title  = self._title_var.get().strip()
        artist = self._artist_var.get().strip()
        if not title:
            self._status.configure(text="El título es obligatorio.")
            return
        warn = "" if artist else "  (sin artista, los resultados serán menos precisos)"
        self._status.configure(text=f"Buscando...{warn}")
        self.update_idletasks()

        def worker():
            match = musicbrainz.search_recording(title=title, artist=artist)
            cands = match.candidates if match else []
            self.after(0, lambda: self._fill_results(cands))

        threading.Thread(target=worker, daemon=True).start()

    def _fill_results(self, cands: list[dict]):
        self._candidates = cands
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        for c in cands:
            self._tree.insert("", "end", values=(
                c.get("title", "—"), c.get("artist", "—"), c.get("album", "—"),
                c.get("year", ""),    c.get("score", ""),
            ))
        self._status.configure(text=f"{len(cands)} resultado(s).")

    def _apply(self):
        sel = self._tree.selection()
        if not sel:
            self._status.configure(text="Selecciona un candidato.")
            return
        idx = self._tree.index(sel[0])
        chosen = self._candidates[idx]
        # Aplicar y persistir
        new_meta = {
            "title":           chosen.get("title"),
            "artist":          chosen.get("artist"),
            "album":           chosen.get("album"),
            "year":            chosen.get("year"),
            "mb_recording_id": chosen.get("recording_id"),
            "mb_release_id":   chosen.get("release_id"),
        }
        try:
            pipeline._write_tags(Path(self._path), new_meta, embed_cover=True)
            library.upsert_track(
                self._path,
                title=chosen.get("title"),
                artist=chosen.get("artist"),
                album=chosen.get("album"),
                year=chosen.get("year"),
                mb_recording_id=chosen.get("recording_id"),
                mb_release_id=chosen.get("release_id"),
            )
            library.remove_from_review(self._path)
            self.destroy()
            self._parent._refresh_review_view()
            self._parent._refresh_library_view()
        except Exception as e:
            self._status.configure(text=f"Error al aplicar: {e}")


# ---------------------------------------------------------------------------
# Diálogo: edición manual de tags
# ---------------------------------------------------------------------------

class ManualTagsDialog(tk.Toplevel):
    """Editor de tags simple para una pista. Sin MB. El usuario edita y guarda."""

    FIELDS = [
        ("title",        "Título"),
        ("artist",       "Artista"),
        ("album",        "Álbum"),
        ("year",         "Año"),
        ("track_number", "Nº pista"),
        ("genre",        "Género"),
    ]

    def __init__(self, parent, track_path: str):
        super().__init__(parent)
        self.title("Editar tags a mano")
        _dark_toplevel(self)
        self.transient(parent)
        self.grab_set()
        self._parent = parent
        self._path = track_path

        row = library.get_track_by_path(track_path)
        if row is None:
            row = {}

        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=Path(track_path).name,
                  foreground="gray", wraplength=440).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        self._vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate(self.FIELDS, start=1):
            ttk.Label(frm, text=f"{label}:").grid(row=i, column=0, sticky="w", padx=(0, 12), pady=3)
            var = tk.StringVar(value=str(row[key] or "") if isinstance(row, dict) is False and row[key] is not None else (row.get(key, "") if isinstance(row, dict) else ""))
            try:
                v = row[key]
                var = tk.StringVar(value=str(v) if v is not None else "")
            except (KeyError, TypeError):
                var = tk.StringVar(value="")
            self._vars[key] = var
            ttk.Entry(frm, textvariable=var, width=42).grid(row=i, column=1, sticky="ew", pady=3)

        btn = ttk.Frame(frm)
        btn.grid(row=99, column=0, columnspan=2, pady=(14, 0), sticky="ew")
        ttk.Button(btn, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(btn, text="Guardar y quitar de bandeja",
                   command=self._save).pack(side="right", padx=6)

        _center_on(self, parent)

    def _save(self):
        meta = {}
        for key, _ in self.FIELDS:
            v = self._vars[key].get().strip()
            if v:
                meta[key] = int(v) if key == "track_number" and v.isdigit() else v
        try:
            pipeline._write_tags(Path(self._path), meta, embed_cover=False)
            library.upsert_track(self._path, **meta)
            library.remove_from_review(self._path)
            self.destroy()
            self._parent._refresh_review_view()
            self._parent._refresh_library_view()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudieron guardar los tags:\n{e}", parent=self)


# ---------------------------------------------------------------------------
# Diálogo: bienvenida (primer arranque)
# ---------------------------------------------------------------------------

class WelcomeDialog(tk.Toplevel):
    """Setup inicial: carpeta de biblioteca, formato, calidad, toggle MB.
    Hasta que el usuario complete el formulario y pulse 'Empezar', la app
    no continúa cargando."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Bienvenido a Music Grabber")
        self.resizable(False, False)
        _dark_toplevel(self)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._parent = parent
        self._completed = False

        frm = ttk.Frame(self, padding=18)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Bienvenido a Music Grabber",
                  font=("", 13, "bold")).pack(anchor="w")
        ttk.Label(frm,
                  text=("Antes de empezar, configura dónde se guardará la música y\n"
                        "qué formato prefieres. Podrás cambiarlo en Ajustes."),
                  foreground="gray", justify="left").pack(anchor="w", pady=(4, 14))

        # Biblioteca
        ttk.Label(frm, text="Carpeta de biblioteca",
                  font=("", 9, "bold")).pack(anchor="w")
        lib_row = ttk.Frame(frm)
        lib_row.pack(fill="x", pady=(2, 12))
        self._lib_var = tk.StringVar(value=str(Path.home() / "Music"))
        ttk.Entry(lib_row, textvariable=self._lib_var, width=42).pack(side="left", fill="x", expand=True)
        ttk.Button(lib_row, text="Examinar...", command=self._browse).pack(side="left", padx=(6, 0))

        # Formato y calidad
        grid = ttk.Frame(frm)
        grid.pack(fill="x", pady=(0, 12))

        ttk.Label(grid, text="Formato:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._fmt_var = tk.StringVar(value="mp3")
        ttk.Combobox(grid, textvariable=self._fmt_var, state="readonly", width=12,
                     values=["mp3", "flac", "ogg"]).grid(row=0, column=1, sticky="w", pady=2)

        ttk.Label(grid, text="Calidad (kbps):").grid(row=1, column=0, sticky="w", padx=(0, 8))
        self._q_var = tk.StringVar(value="320")
        ttk.Combobox(grid, textvariable=self._q_var, state="readonly", width=12,
                     values=["128", "192", "256", "320"]).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(grid, text="(ignorado en FLAC)",
                  foreground="gray").grid(row=1, column=2, sticky="w", padx=(8, 0))

        # Toggle MB
        self._mb_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, variable=self._mb_var,
                        text="Mejorar tags con MusicBrainz tras cada descarga"
                        ).pack(anchor="w", pady=(6, 0))
        ttk.Label(frm,
                  text=("Si lo activas, cada pista se consulta en MusicBrainz al terminar la\n"
                        "descarga para aplicar metadatos canónicos (~1 s extra por pista).\n"
                        "Si lo dejas apagado, podrás enriquecer todo más tarde con un solo botón."),
                  foreground="gray", justify="left",
                  wraplength=420).pack(anchor="w", padx=24, pady=(2, 14))

        # Botones
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Salir", command=self._cancel).pack(side="left")
        self._start_btn = ttk.Button(btns, text="Empezar", command=self._submit)
        self._start_btn.pack(side="right")

        _center_on(self, parent)
        self.bind("<Return>", lambda _: self._submit())

    def _browse(self):
        p = filedialog.askdirectory(title="Carpeta de biblioteca", parent=self,
                                     initialdir=self._lib_var.get())
        if p:
            self._lib_var.set(p)

    def _submit(self):
        path = self._lib_var.get().strip()
        if not path:
            messagebox.showwarning("Falta carpeta",
                                   "Selecciona una carpeta para la biblioteca.",
                                   parent=self)
            return
        # Crear la carpeta si no existe
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("No se pudo crear la carpeta", str(e), parent=self)
            return
        save_config(
            path=path,
            fmt=self._fmt_var.get(),
            quality=self._q_var.get(),
            musicbrainz_enabled=self._mb_var.get(),
        )
        self._completed = True
        self.destroy()

    def _cancel(self):
        if messagebox.askyesno(
            "Salir",
            "Sin biblioteca configurada Music Grabber no puede funcionar. "
            "¿Salir de la aplicación?",
            parent=self,
        ):
            self._completed = False
            self.destroy()
            self._parent.destroy()

    @property
    def completed(self) -> bool:
        return self._completed


# ---------------------------------------------------------------------------
# Diálogo: Sleep timer
# ---------------------------------------------------------------------------

class SleepTimerDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Sleep timer")
        self.resizable(False, False)
        _dark_toplevel(self)
        self.transient(parent)

        # Construir widgets ANTES de grab_set para evitar ventanas vacías.
        try:
            self._build()
        except Exception as e:
            import traceback
            log_event("err", f"SleepTimerDialog._build: {e}")
            traceback.print_exc()
        self.update_idletasks()
        _center_on(self, parent)
        self.grab_set()

    def _build(self):
        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Detener reproducción automática",
                  font=("", 11, "bold")).pack(anchor="w")

        # Modo
        self._mode_var = tk.StringVar(value="minutes")
        ttk.Radiobutton(frm, variable=self._mode_var, value="minutes",
                        text="Detener tras X minutos").pack(anchor="w", pady=(10, 2))

        row = ttk.Frame(frm)
        row.pack(anchor="w", padx=24, pady=(0, 4))
        ttk.Label(row, text="Minutos:").pack(side="left")
        self._minutes_var = tk.StringVar(value="30")
        ttk.Entry(row, textvariable=self._minutes_var, width=8).pack(side="left", padx=(6, 0))

        ttk.Radiobutton(frm, variable=self._mode_var, value="after_track",
                        text="Detener al terminar la pista actual").pack(anchor="w", pady=(8, 0))

        # Estado del timer activo. Leemos campos cached del player, NO
        # llamamos a player.get_state() que iría a VLC y podría bloquear
        # el render del Toplevel en algunas combinaciones.
        left = player.sleep_seconds_left()
        after = getattr(player, "_sleep_after_track", False)
        if left is not None:
            m, s = divmod(left, 60)
            info = f"Timer activo: {m:02d}:{s:02d} restantes"
        elif after:
            info = "Timer activo: parar tras la pista actual"
        else:
            info = "Sin timer activo"
        ttk.Label(frm, text=info, foreground=DARK["muted"]).pack(anchor="w", pady=(10, 0))

        btn = ttk.Frame(frm)
        btn.pack(fill="x", pady=(14, 0))
        ttk.Button(btn, text="Cancelar timer", command=self._cancel_timer).pack(side="left")
        ttk.Button(btn, text="Cerrar", command=self.destroy).pack(side="right")
        ttk.Button(btn, text="Iniciar", command=self._start).pack(side="right", padx=6)

    def _start(self):
        mode = self._mode_var.get()
        if mode == "after_track":
            player.set_sleep_timer(None, after_track=True)
            self.destroy()
            return
        try:
            minutes = int(self._minutes_var.get())
        except ValueError:
            messagebox.showwarning("Valor inválido", "Introduce un número de minutos.",
                                   parent=self)
            return
        if minutes <= 0:
            messagebox.showwarning("Valor inválido", "El número de minutos debe ser positivo.",
                                   parent=self)
            return
        player.set_sleep_timer(minutes * 60, after_track=False)
        self.destroy()

    def _cancel_timer(self):
        player.set_sleep_timer(None, after_track=False)
        self.destroy()


# ---------------------------------------------------------------------------
# Diálogo: Ecualizador 10 bandas
# ---------------------------------------------------------------------------

class EqualizerDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Ecualizador")
        self.resizable(False, False)
        _dark_toplevel(self)
        self.transient(parent)

        try:
            self._build()
        except Exception as e:
            import traceback
            log_event("err", f"EqualizerDialog._build: {e}")
            traceback.print_exc()
        self.update_idletasks()
        _center_on(self, parent)

    def _build(self):
        st = player.get_equalizer_state()

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        # Preset
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="Preset:").pack(side="left", padx=(0, 6))
        self._preset_var = tk.StringVar(value=st["preset"])
        cb = ttk.Combobox(row, textvariable=self._preset_var, state="readonly",
                          values=st["presets"], width=20)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _: self._apply_preset())
        ttk.Button(row, text="Reset", command=self._reset).pack(side="right")

        # Preamp
        pa = ttk.Frame(frm)
        pa.pack(fill="x", pady=(0, 6))
        ttk.Label(pa, text="Pre-amp:").pack(side="left")
        self._preamp_var = tk.DoubleVar(value=st["preamp"])
        self._preamp_lbl_var = tk.StringVar(value=f"{st['preamp']:+.1f} dB")
        ttk.Scale(pa, from_=-20.0, to=20.0, length=240,
                  variable=self._preamp_var,
                  command=self._on_preamp_change).pack(side="left", padx=8)
        ttk.Label(pa, textvariable=self._preamp_lbl_var, width=10,
                  foreground=DARK["muted"]).pack(side="left")

        # 10 bandas verticales
        bands_row = ttk.Frame(frm)
        bands_row.pack(fill="both", expand=True, pady=(8, 0))
        self._band_vars: list[tk.DoubleVar] = []
        self._band_lbls: list[tk.StringVar] = []
        for i, freq in enumerate(st["freqs"]):
            col = ttk.Frame(bands_row)
            col.pack(side="left", padx=6)
            v_lbl = tk.StringVar(value=f"{st['bands'][i]:+.0f}")
            self._band_lbls.append(v_lbl)
            ttk.Label(col, textvariable=v_lbl,
                      width=4, foreground=DARK["muted"]).pack()
            var = tk.DoubleVar(value=st["bands"][i])
            self._band_vars.append(var)
            ttk.Scale(col, from_=20.0, to=-20.0, length=160, orient="vertical",
                      variable=var,
                      command=lambda v, idx=i: self._on_band_change(idx, v)
                      ).pack()
            ttk.Label(col, text=freq, width=4,
                      foreground=DARK["muted"]).pack()

        # Cerrar
        btn = ttk.Frame(frm)
        btn.pack(fill="x", pady=(12, 0))
        ttk.Button(btn, text="Cerrar", command=self.destroy).pack(side="right")

    def _apply_preset(self):
        name = self._preset_var.get()
        if player.set_equalizer_preset(name):
            st = player.get_equalizer_state()
            self._preamp_var.set(st["preamp"])
            self._preamp_lbl_var.set(f"{st['preamp']:+.1f} dB")
            for i, v in enumerate(st["bands"]):
                self._band_vars[i].set(v)
                self._band_lbls[i].set(f"{v:+.0f}")
        self._save_persisted()

    def _on_preamp_change(self, v):
        try:
            db = float(v)
        except ValueError:
            return
        self._preamp_lbl_var.set(f"{db:+.1f} dB")
        player.set_equalizer_preamp(db)
        # Cambia el preset a "Personalizado" implícitamente; refrescar combo
        self._preset_var.set(player.get_equalizer_state()["preset"])

    def _on_band_change(self, idx, v):
        try:
            db = float(v)
        except ValueError:
            return
        self._band_lbls[idx].set(f"{db:+.0f}")
        player.set_equalizer_band(idx, db)
        self._preset_var.set(player.get_equalizer_state()["preset"])
        self._save_persisted()

    def _reset(self):
        player.reset_equalizer()
        st = player.get_equalizer_state()
        self._preset_var.set(st["preset"])
        self._preamp_var.set(st["preamp"])
        self._preamp_lbl_var.set(f"{st['preamp']:+.1f} dB")
        for i, v in enumerate(st["bands"]):
            self._band_vars[i].set(v)
            self._band_lbls[i].set(f"{v:+.0f}")
        self._save_persisted()

    def _save_persisted(self):
        try:
            save_config(eq_preset=player.get_equalizer_state()["preset"])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Diálogo: cola de reproducción
# ---------------------------------------------------------------------------

class QueueDialog(tk.Toplevel):
    """
    Cola de reproducción con drag & drop manual.

    El drag & drop se implementa con bindings Button-1 / B1-Motion /
    ButtonRelease-1 porque tkinter no tiene DnD nativo en Treeview.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Cola de reproducción")
        self.geometry("620x580")
        self.minsize(560, 460)
        _dark_toplevel(self)
        self.transient(parent)
        self._parent = parent
        self._drag_src: Optional[int] = None

        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 6))
        ttk.Label(header, text="Cola actual",
                  font=("", 11, "bold")).pack(side="left")
        self._count_var = tk.StringVar(value="0 pistas")
        ttk.Label(header, textvariable=self._count_var,
                  foreground=DARK["muted"]).pack(side="left", padx=8)
        ttk.Button(header, text="Vaciar", command=self._clear).pack(side="right")

        # Treeview
        tree_frame = ttk.Frame(outer)
        tree_frame.pack(fill="both", expand=True)
        cols = ("idx", "title", "artist")
        self._tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                                   selectmode="browse")
        self._tree.heading("idx",    text="#")
        self._tree.heading("title",  text="Título")
        self._tree.heading("artist", text="Artista")
        self._tree.column("idx",    width=40,  anchor="e")
        self._tree.column("title",  width=240, anchor="w")
        self._tree.column("artist", width=180, anchor="w")
        self._tree.tag_configure("current", foreground=DARK["accent"],
                                  font=("", 9, "bold"))
        self._tree.tag_configure("dragging", background=DARK["panel_alt"],
                                  foreground=DARK["accent"])

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Bindings de drag & drop
        self._tree.bind("<ButtonPress-1>",   self._on_drag_start)
        self._tree.bind("<B1-Motion>",       self._on_drag_motion)
        self._tree.bind("<ButtonRelease-1>", self._on_drag_release)
        self._tree.bind("<Double-1>",        self._on_double_click)

        # Botones inferiores
        btn = ttk.Frame(outer)
        btn.pack(fill="x", pady=(6, 0))
        ttk.Button(btn, text="↑ Subir",   command=self._move_up   ).pack(side="left")
        ttk.Button(btn, text="↓ Bajar",  command=self._move_down ).pack(side="left", padx=4)
        ttk.Button(btn, text="Quitar",   command=self._remove    ).pack(side="left", padx=4)
        ttk.Button(btn, text="Reproducir aquí",
                   command=self._jump_here).pack(side="left", padx=4)
        ttk.Button(btn, text="Cerrar",   command=self.destroy).pack(side="right")

        _center_on(self, parent)
        self._refresh()
        self.after(800, self._auto_refresh)

    # ----------------- refresco -----------------

    def _refresh(self):
        try:
            sel_iid = self._tree.selection()[0] if self._tree.selection() else None
        except Exception:
            sel_iid = None

        for iid in self._tree.get_children():
            self._tree.delete(iid)

        q = player.get_queue()
        for idx, path, is_current in q:
            row = library.get_track_by_path(path)
            title  = (row["title"]  if row else None) or Path(path).stem
            artist = (row["artist"] if row else None) or ""
            tags = ("current",) if is_current else ()
            self._tree.insert(
                "", "end", iid=str(idx),
                values=(idx + 1, title, artist),
                tags=tags,
            )

        self._count_var.set(f"{len(q)} pistas")
        if sel_iid and self._tree.exists(sel_iid):
            self._tree.selection_set(sel_iid)

    def _auto_refresh(self):
        if not self.winfo_exists():
            return
        try:
            self._refresh()
        except Exception:
            pass
        self.after(800, self._auto_refresh)

    # ----------------- acciones -----------------

    def _selected_idx(self) -> Optional[int]:
        sel = self._tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except ValueError:
            return None

    def _move_up(self):
        idx = self._selected_idx()
        if idx is None or idx == 0:
            return
        player.move_in_queue(idx, idx - 1)
        self._refresh()
        try:
            self._tree.selection_set(str(idx - 1))
        except tk.TclError:
            pass

    def _move_down(self):
        idx = self._selected_idx()
        if idx is None:
            return
        player.move_in_queue(idx, idx + 1)
        self._refresh()
        try:
            self._tree.selection_set(str(idx + 1))
        except tk.TclError:
            pass

    def _remove(self):
        idx = self._selected_idx()
        if idx is None:
            return
        player.remove_from_queue(idx)
        self._refresh()

    def _jump_here(self):
        """Salta a la pista seleccionada de la cola."""
        idx = self._selected_idx()
        if idx is None:
            return
        # Hack: aprovechamos load_queue para forzar reproducción desde idx.
        # Las pistas anteriores se mantienen como están.
        q = [path for _, path, _ in player.get_queue()]
        player.load_queue(q, start_index=idx)

    def _clear(self):
        if not messagebox.askyesno(
            "Vaciar cola",
            "¿Vaciar la cola y detener la reproducción?",
            parent=self,
        ):
            return
        player.clear_queue()
        self._refresh()

    # ----------------- drag & drop -----------------

    def _on_drag_start(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid:
            self._drag_src = None
            return
        try:
            self._drag_src = int(iid)
        except ValueError:
            self._drag_src = None
            return
        # Cursor "fleur" (cruz de 4 direcciones) en toda la ventana
        # y resaltado de la fila origen.
        self.configure(cursor="fleur")
        try:
            self._tree.item(iid, tags=("dragging",))
        except tk.TclError:
            pass

    def _on_drag_motion(self, event):
        """Preview en vivo: si el ratón pasa por una fila distinta, mover la
        pista ahí en la cola y redibujar. Da feedback claro de cómo quedará."""
        if self._drag_src is None:
            return
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        try:
            hover = int(iid)
        except ValueError:
            return
        if hover == self._drag_src:
            return
        # Mover en cola en vivo; el nuevo "origen" del drag es la nueva posición.
        player.move_in_queue(self._drag_src, hover)
        self._drag_src = hover
        self._refresh()
        # Re-marcar la fila arrastrada con tag y selección
        try:
            self._tree.item(str(hover), tags=("dragging",))
            self._tree.selection_set(str(hover))
            self._tree.see(str(hover))
        except tk.TclError:
            pass

    def _on_drag_release(self, _event):
        """Soltar: la pista ya está en su posición final (movida durante el
        motion). Solo limpiar cursor y refrescar para quitar el tag dragging."""
        if self._drag_src is None:
            self.configure(cursor="")
            return
        final_idx = self._drag_src
        self._drag_src = None
        self.configure(cursor="")
        self._refresh()
        try:
            self._tree.selection_set(str(final_idx))
        except tk.TclError:
            pass

    def _on_double_click(self, _event):
        self._jump_here()


# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------

class MusicGrabberGUI(tk.Tk):

    VIEW_DOWNLOAD = "download"
    VIEW_LIBRARY  = "library"
    VIEW_REVIEW   = "review"

    def __init__(self):
        super().__init__()
        self.title(f"Music Grabber {APP_VERSION}")
        self.minsize(880, 540)
        self._views: dict[str, ttk.Frame] = {}
        self._current_view: str = self.VIEW_DOWNLOAD
        self._library_genre_filter: str = ""

        load_config()
        musicbrainz.configure(APP_VERSION)
        library.init_db()
        _apply_dark_theme(self)

        # Restaurar geometría si hay guardada, si no usar default razonable
        if state.window_geometry:
            try:
                self.geometry(state.window_geometry)
            except Exception:
                self.geometry("1100x680")
        else:
            self.geometry("1100x680")

        self._build_ui()
        self._setup_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._refresh_infobar()
        self._refresh_mb_indicator()
        self._bind_global_shortcuts()
        self.after(100, self._run_bootstrap)

    def _setup_icon(self):
        try:
            base = Path(__file__).parent.parent / "assets"
            if sys.platform.startswith("win") and (base / "app.ico").exists():
                self.iconbitmap(str(base / "app.ico"))
            elif (base / "logo.png").exists():
                img = tk.PhotoImage(file=str(base / "logo.png"))
                self.iconphoto(True, img)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Construcción
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Barra superior — infobar + toolbar
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 0))

        self._lib_lbl   = ttk.Label(top, text="Biblioteca: —",    font=("", 9))
        self._space_lbl = ttk.Label(top, text="Espacio libre: —", font=("", 9))
        self._lib_lbl.pack(side="left", padx=(0, 16))
        self._space_lbl.pack(side="left", padx=(0, 16))

        # Indicador MB clicable: click directamente togglea el ajuste
        # "Mejorar tags con MusicBrainz tras descargar" sin abrir Ajustes.
        self._mb_indicator = tk.Label(
            top, text="MB: OFF", font=("", 9, "bold"),
            padx=8, pady=2, cursor="hand2",
        )
        self._mb_indicator.pack(side="left", padx=(0, 16))
        self._mb_indicator.bind("<Button-1>", lambda _: self._toggle_mb())

        ttk.Button(top, text="Ajustes",                    command=self._open_settings).pack(side="right")
        ttk.Button(top, text="Calcular BPM",               command=self._compute_bpm_library).pack(side="right", padx=6)
        ttk.Button(top, text="Enriquecer con MusicBrainz", command=self._enrich_library).pack(side="right", padx=6)
        ttk.Button(top, text="Escanear biblioteca",        command=self._scan_library).pack(side="right")

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=(6, 0))

        # Cuerpo: sidebar + panel central
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=8)

        # Sidebar: Treeview con secciones VISTAS / GÉNEROS / ESTADO
        sidebar_wrap = ttk.Frame(body, width=240)
        sidebar_wrap.pack(side="left", fill="y")
        sidebar_wrap.pack_propagate(False)

        self._sidebar = ttk.Treeview(
            sidebar_wrap, show="tree", selectmode="browse",
            columns=("count",),
        )
        self._sidebar.column("#0",     width=160, anchor="w")
        self._sidebar.column("count",  width=60,  anchor="e")
        self._sidebar.pack(fill="both", expand=True)

        # Estilos: secciones en gris y sin selección
        self._sidebar.tag_configure("section", foreground=DARK["section"], font=("", 9, "bold"))
        self._sidebar.tag_configure("warn",    foreground=DARK["warn_fg"])

        # Items de sección (no clicables — se ignoran al seleccionar)
        self._sidebar.insert("", "end", iid="sec_views",    text="VISTAS",   tags=("section",))
        self._sidebar.insert("", "end", iid=self.VIEW_DOWNLOAD, text="Descargar",     values=("",))
        self._sidebar.insert("", "end", iid=self.VIEW_LIBRARY,  text="Biblioteca",    values=("",))
        self._sidebar.insert("", "end", iid=self.VIEW_REVIEW,   text="Sin metadatos", values=("",), tags=("warn",))
        self._sidebar.insert("", "end", iid="sec_genres",   text="GÉNEROS",  tags=("section",))
        # Los géneros se rellenan dinámicamente al refrescar la biblioteca

        self._sidebar.bind("<<TreeviewSelect>>", self._on_sidebar_select)
        # Seleccionar Descargar por defecto
        self._sidebar.selection_set(self.VIEW_DOWNLOAD)

        # Separador vertical
        ttk.Separator(body, orient="vertical").pack(side="left", fill="y", padx=4)

        # Contenedor de vistas
        self._content = ttk.Frame(body)
        self._content.pack(side="left", fill="both", expand=True)

        self._build_view_download()
        self._build_view_library()
        self._build_view_review()
        # Restaurar la última vista activa (default: descargar)
        initial = state.last_view if state.last_view in self._views else self.VIEW_DOWNLOAD
        self._switch_view(initial)

        # Barra de estado de descarga + progreso
        ttk.Separator(self, orient="horizontal").pack(fill="x")
        status_bar = ttk.Frame(self)
        status_bar.pack(fill="x", padx=8, pady=(6, 2))

        self._status_var = tk.StringVar(value="Iniciando...")
        ttk.Label(status_bar, textvariable=self._status_var, foreground=DARK["muted"]).pack(side="left")

        self._prog_pct_var = tk.StringVar(value="")
        ttk.Label(status_bar, textvariable=self._prog_pct_var, font=("", 9, "bold")).pack(side="right")
        self._progressbar = ttk.Progressbar(status_bar, orient="horizontal", mode="determinate",
                                             maximum=100, length=200)
        self._progressbar.pack(side="right", padx=8)

        # Barra inferior — reproductor integrado (fase 2a)
        self._build_player_bar()

    # ---- Vista: Descargar ----------------------------------------------

    def _build_view_download(self):
        f = ttk.Frame(self._content)
        self._views[self.VIEW_DOWNLOAD] = f

        ttk.Label(f, text="Descargar música",
                  font=("", 12, "bold")).pack(anchor="w", pady=(4, 12))

        ttk.Label(f, text="URL de YouTube o YouTube Music").pack(anchor="w")
        self._url_var = tk.StringVar()
        entry = ttk.Entry(f, textvariable=self._url_var, font=("", 10))
        entry.pack(fill="x", pady=(4, 8))
        entry.bind("<Return>", lambda _: self._on_download_click())

        self._dl_btn = ttk.Button(f, text="Descargar", command=self._on_download_click)
        self._dl_btn.pack(fill="x", ipady=4)

        # Stats de sesión
        stats = ttk.Frame(f)
        stats.pack(fill="x", pady=(20, 8))
        for i in range(3):
            stats.columnconfigure(i, weight=1)

        self._s_ok  = tk.StringVar(value="0")
        self._s_err = tk.StringVar(value="0")
        self._s_t   = tk.StringVar(value="—")
        for col, (var, label, color) in enumerate([
            (self._s_ok,  "Descargadas", DARK["success_fg"]),
            (self._s_err, "Fallos",      DARK["err_fg"]),
            (self._s_t,   "Sesión",      None),
        ]):
            box = ttk.Frame(stats, relief="groove", borderwidth=1)
            box.grid(row=0, column=col, sticky="ew", padx=4)
            kw = {"foreground": color} if color else {}
            tk.Label(box, textvariable=var, font=("", 14, "bold"), **kw).pack(pady=(6, 0))
            tk.Label(box, text=label, foreground="gray").pack(pady=(0, 6))

        # Log detallado de eventos
        log_header = ttk.Frame(f)
        log_header.pack(fill="x", pady=(16, 4))
        ttk.Label(log_header, text="Log de eventos", font=("", 9, "bold")).pack(side="left")
        ttk.Button(log_header, text="Limpiar", command=self._clear_log).pack(side="right")

        log_frame = ttk.Frame(f, relief="groove", borderwidth=1)
        log_frame.pack(fill="both", expand=True)
        self._log = tk.Text(log_frame, state="disabled", wrap="none", font=("Courier", 9),
                            relief="flat", padx=6, pady=4, cursor="arrow", height=14)
        sb_y = ttk.Scrollbar(log_frame, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=sb_y.set)
        self._log.pack(side="left", fill="both", expand=True)
        sb_y.pack(side="right", fill="y")
        self._log.tag_configure("info", foreground=DARK["info_fg"])
        self._log.tag_configure("ok",   foreground=DARK["success_fg"])
        self._log.tag_configure("warn", foreground=DARK["warn_fg"])
        self._log.tag_configure("err",  foreground=DARK["err_fg"])
        self._log.tag_configure("mb",   foreground=DARK["mb_fg"])
        self._log_last_seen = 0  # cuántos eventos del state.event_log ya están renderizados

    # ---- Vista: Biblioteca ---------------------------------------------

    def _build_view_library(self):
        f = ttk.Frame(self._content)
        self._views[self.VIEW_LIBRARY] = f

        header = ttk.Frame(f)
        header.pack(fill="x", pady=(4, 8))
        ttk.Label(header, text="Biblioteca", font=("", 12, "bold")).pack(side="left")
        self._lib_count_var = tk.StringVar(value="—")
        ttk.Label(header, textvariable=self._lib_count_var, foreground="gray").pack(side="left", padx=12)
        ttk.Button(header, text="Vaciar índice", command=self._clear_index).pack(side="right")
        ttk.Button(header, text="Refrescar",
                   command=self._refresh_library_with_prune).pack(side="right", padx=6)
        ttk.Button(header, text="Reproducir selección",
                   command=self._play_selected).pack(side="right", padx=6)
        ttk.Button(header, text="Limpiar filtros",
                   command=self._clear_lib_filters).pack(side="right", padx=6)

        # Filtro
        filt = ttk.Frame(f)
        filt.pack(fill="x", pady=(0, 6))
        ttk.Label(filt, text="Filtro:").pack(side="left")
        self._lib_filter_var = tk.StringVar()
        self._lib_filter_var.trace_add("write", lambda *a: self._refresh_library_view())
        ttk.Entry(filt, textvariable=self._lib_filter_var).pack(side="left", fill="x", expand=True, padx=6)

        # Treeview
        tree_frame = ttk.Frame(f)
        tree_frame.pack(fill="both", expand=True)

        cols = ("title", "artist", "album", "year", "mb", "bpm", "duration")
        self._tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        self._col_labels = {
            "title": "Título", "artist": "Artista", "album": "Álbum",
            "year": "Año",     "mb": "MB",          "bpm": "BPM",
            "duration": "Duración",
        }
        self._sort_state: tuple[str, bool] = (state.sort_column, state.sort_reverse)
        for col, w, anchor in [
            ("title",    280, "w"),
            ("artist",   180, "w"),
            ("album",    220, "w"),
            ("year",      60, "center"),
            ("mb",        40, "center"),
            ("bpm",       55, "center"),
            ("duration",  70, "center"),
        ]:
            self._tree.heading(col, text=self._col_labels[col],
                               command=lambda c=col: self._sort_by_column(c))
            self._tree.column(col, width=w, anchor=anchor)

        sb_y = ttk.Scrollbar(tree_frame, orient="vertical",   command=self._tree.yview)
        sb_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self._tree.bind("<Double-1>", lambda _: self._play_selected())

        # Menú contextual (click derecho). En macOS el botón secundario es 2.
        self._lib_ctx_menu = tk.Menu(self, tearoff=0)
        self._lib_ctx_menu.add_command(label="Reproducir",
                                       command=self._play_selected)
        self._lib_ctx_menu.add_command(label="Añadir al final de la cola",
                                       command=self._ctx_add_to_queue)
        self._lib_ctx_menu.add_command(label="Reproducir a continuación",
                                       command=self._ctx_play_next)
        self._lib_ctx_menu.add_separator()
        self._lib_ctx_menu.add_command(label="Abrir con reproductor externo",
                                       command=self._ctx_play_external)
        self._lib_ctx_menu.add_separator()
        self._lib_ctx_menu.add_command(label="Editar tags a mano...",
                                       command=self._ctx_edit_tags)
        self._lib_ctx_menu.add_command(label="Buscar en MusicBrainz...",
                                       command=self._ctx_mb_search)
        self._lib_ctx_menu.add_command(label="Reenriquecer con MusicBrainz",
                                       command=self._ctx_reenrich)
        self._lib_ctx_menu.add_separator()
        self._lib_ctx_menu.add_command(label="Abrir carpeta contenedora",
                                       command=self._ctx_open_folder)
        self._lib_ctx_menu.add_separator()
        self._lib_ctx_menu.add_command(label="Quitar del índice",
                                       command=self._ctx_remove_from_index)
        self._lib_ctx_menu.add_command(label="Eliminar archivo del disco...",
                                       command=self._ctx_delete_file)

        self._tree.bind("<Button-3>", self._show_lib_context_menu)
        self._tree.bind("<Control-Button-1>", self._show_lib_context_menu)  # macOS

    # ---- Vista: Sin metadatos ------------------------------------------

    def _build_view_review(self):
        f = ttk.Frame(self._content)
        self._views[self.VIEW_REVIEW] = f

        header = ttk.Frame(f)
        header.pack(fill="x", pady=(4, 8))
        ttk.Label(header, text="Bandeja de revisión",
                  font=("", 12, "bold")).pack(side="left")
        ttk.Button(header, text="Refrescar",
                   command=self._refresh_review_view).pack(side="right")

        body = ttk.PanedWindow(f, orient="horizontal")
        body.pack(fill="both", expand=True)

        # Izquierda: lista de pistas pendientes
        left = ttk.Frame(body)
        body.add(left, weight=1)

        self._review_list = tk.Listbox(left, font=("", 9))
        self._review_list.pack(fill="both", expand=True)
        self._review_list.bind("<<ListboxSelect>>", lambda _: self._show_review_candidates())

        # Derecha: candidatos del match seleccionado
        right = ttk.Frame(body)
        body.add(right, weight=2)

        ttk.Label(right, text="Candidatos de MusicBrainz",
                  font=("", 9, "bold")).pack(anchor="w", pady=(0, 4))

        cand_frame = ttk.Frame(right)
        cand_frame.pack(fill="both", expand=True)

        cols = ("title", "artist", "album", "year", "score")
        self._cand_tree = ttk.Treeview(cand_frame, columns=cols, show="headings")
        for col, label, w in [("title", "Título", 200), ("artist", "Artista", 140),
                               ("album", "Álbum", 180), ("year", "Año", 50),
                               ("score", "Score", 50)]:
            self._cand_tree.heading(col, text=label)
            self._cand_tree.column(col, width=w)
        sb = ttk.Scrollbar(cand_frame, orient="vertical", command=self._cand_tree.yview)
        self._cand_tree.configure(yscrollcommand=sb.set)
        self._cand_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Dos filas de acciones para no apretar los botones
        action_row1 = ttk.Frame(right)
        action_row1.pack(fill="x", pady=(8, 2))
        ttk.Button(action_row1, text="Aplicar candidato seleccionado",
                   command=self._apply_selected_candidate).pack(side="left")
        ttk.Button(action_row1, text="Buscar manualmente en MB",
                   command=self._manual_mb_search).pack(side="left", padx=6)
        ttk.Button(action_row1, text="Editar tags a mano",
                   command=self._manual_edit_tags).pack(side="left")

        action_row2 = ttk.Frame(right)
        action_row2.pack(fill="x", pady=(2, 0))
        ttk.Button(action_row2, text="Dejar como está (quitar de bandeja)",
                   command=self._mark_as_is).pack(side="left")
        ttk.Button(action_row2, text="Abrir archivo",
                   command=self._open_review_file).pack(side="right")

        self._review_paths: list[str] = []  # paralelo a Listbox

    # ------------------------------------------------------------------
    # Conmutación de vistas
    # ------------------------------------------------------------------

    def _switch_view(self, key: str):
        # 1ª capa antibucle: si la vista no cambia y no es un filtro de género,
        # no hacer nada. Evita el bucle save_config → selection_set → handler →
        # save_config disparado por el evento async de Treeview.
        # Excepción: durante la primera llamada (desde __init__), siempre dejar
        # pasar para inicializar la sidebar y el frame visible.
        first_time = not getattr(self, "_view_initialised", False)
        if not first_time and key == self._current_view and not key.startswith("genre::"):
            return
        self._view_initialised = True

        for k, frame in self._views.items():
            frame.pack_forget()
        self._views[key].pack(fill="both", expand=True)
        self._current_view = key

        # Persistir vista activa para restaurarla en el próximo arranque
        try:
            save_config(last_view=key)
        except Exception:
            pass

        # Sincronizar selección de la sidebar. El flag _suppress_sidebar_event
        # se limpia con after_idle: TreeviewSelect se entrega en el SIGUIENTE
        # ciclo del mainloop, no inmediatamente. Si lo limpiásemos en finally
        # (síncrono), el handler async vería el flag ya en False.
        self._suppress_sidebar_event = True
        try:
            self._sidebar.item(key)  # lanza TclError si el item no existe
            self._sidebar.selection_set(key)
            self._sidebar.see(key)
        except (AttributeError, tk.TclError):
            pass
        self.after_idle(self._clear_suppress_sidebar_flag)

        if key == self.VIEW_LIBRARY:
            self._refresh_library_view()
        elif key == self.VIEW_REVIEW:
            self._refresh_review_view()

    def _clear_suppress_sidebar_flag(self):
        self._suppress_sidebar_event = False

    def _on_sidebar_select(self, _event=None):
        if getattr(self, "_suppress_sidebar_event", False):
            return
        sel = self._sidebar.selection()
        if not sel:
            return
        iid = sel[0]

        # 2ª capa antibucle: si el evento corresponde a la vista ya activa,
        # no hacer nada (esto cubre el caso en que el flag de supresión llegue
        # tarde por orden de eventos del mainloop).
        if iid == self._current_view and not iid.startswith("genre::"):
            return

        # Las secciones no son seleccionables
        if iid.startswith("sec_"):
            # Volver a la selección anterior según vista activa
            if self._library_genre_filter:
                self._sidebar.selection_set(f"genre::{self._library_genre_filter}")
            else:
                self._sidebar.selection_set(self._current_view)
            return

        if iid.startswith("genre::"):
            self._library_genre_filter = iid[len("genre::"):]
            # Al activar un género, limpiar el filtro de texto: si el usuario
            # quiere combinar, primero el género y luego escribe el texto.
            self._lib_filter_var.set("")
            # Si ya estábamos en biblioteca, _switch_view haría early-return
            # y no refrescaría la tabla con el nuevo filtro. Refrescamos a mano.
            if self._current_view == self.VIEW_LIBRARY:
                self._refresh_library_view()
            else:
                self._switch_view(self.VIEW_LIBRARY)
            return

        # Vista normal
        if iid in (self.VIEW_DOWNLOAD, self.VIEW_LIBRARY, self.VIEW_REVIEW):
            had_genre = bool(self._library_genre_filter)
            self._library_genre_filter = ""
            if self._current_view == iid and had_genre:
                # Misma vista pero veníamos de un filtro de género: refrescar.
                self._refresh_library_view()
            else:
                self._switch_view(iid)

    def _refresh_sidebar_counts(self):
        """Actualiza conteos en la sidebar y la lista de géneros."""
        tracks = list(library.all_tracks())
        total  = len(tracks)
        review = len(list(library.pending_review()))

        # Conteos en VISTAS
        self._sidebar.set(self.VIEW_LIBRARY, "count", str(total))
        self._sidebar.set(self.VIEW_REVIEW,  "count", str(review) if review else "")

        # Géneros con conteos. Quitar los antiguos antes de repoblar.
        for iid in self._sidebar.get_children(""):
            if iid.startswith("genre::"):
                self._sidebar.delete(iid)

        genre_counts: dict[str, int] = {}
        for t in tracks:
            g = (t["genre"] or "").strip()
            if not g:
                continue
            genre_counts[g] = genre_counts.get(g, 0) + 1

        for genre in sorted(genre_counts.keys(), key=lambda g: g.lower()):
            self._sidebar.insert(
                "", "end",
                iid=f"genre::{genre}",
                text=genre,
                values=(str(genre_counts[genre]),),
            )

    # ------------------------------------------------------------------
    # Reproductor (fase 2a)
    # ------------------------------------------------------------------

    def _build_player_bar(self):
        ttk.Separator(self, orient="horizontal").pack(fill="x")
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(4, 8))

        # Bloque izquierdo: carátula placeholder + título/artista
        left = ttk.Frame(bar)
        left.pack(side="left", fill="x", expand=True)

        # Mini-cover placeholder (48x48). En fase 2d se sustituye por la real.
        self._cover = tk.Label(left, text="♪", font=("", 22), width=3,
                                bg=DARK["panel"], fg=DARK["muted"])
        self._cover.pack(side="left", padx=(0, 10))

        info = ttk.Frame(left)
        info.pack(side="left", fill="x", expand=True)
        self._player_title_var  = tk.StringVar(value="—")
        self._player_artist_var = tk.StringVar(value="Sin reproducción")
        ttk.Label(info, textvariable=self._player_title_var,
                  font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(info, textvariable=self._player_artist_var,
                  foreground=DARK["muted"]).pack(anchor="w")

        # Bloque centro: controles + slider de progreso
        center = ttk.Frame(bar)
        center.pack(side="left", padx=10)

        ctrl = ttk.Frame(center)
        ctrl.pack()
        # Texto en lugar de emojis: las fuentes default de tkinter en Linux no
        # siempre tienen glyphs para 🔀 🔁 📋. Texto es portable y legible.
        self._shuffle_btn = tk.Label(ctrl, text=" Mezclar ", font=("", 9, "bold"),
                                      bg=DARK["panel"], fg=DARK["muted"],
                                      padx=8, pady=4, cursor="hand2",
                                      borderwidth=1, relief="solid")
        self._shuffle_btn.pack(side="left", padx=2)
        self._shuffle_btn.bind("<Button-1>", lambda _: self._player_toggle_shuffle())

        ttk.Button(ctrl, text="⏮",  width=3, command=self._player_prev ).pack(side="left", padx=2)
        self._play_pause_btn = ttk.Button(ctrl, text="▶", width=3,
                                           command=self._player_play_pause)
        self._play_pause_btn.pack(side="left", padx=2)
        ttk.Button(ctrl, text="⏭",  width=3, command=self._player_next ).pack(side="left", padx=2)
        ttk.Button(ctrl, text="■",  width=3, command=self._player_stop ).pack(side="left", padx=2)

        self._repeat_btn = tk.Label(ctrl, text=" Repetir ", font=("", 9, "bold"),
                                     bg=DARK["panel"], fg=DARK["muted"],
                                     padx=8, pady=4, cursor="hand2",
                                     borderwidth=1, relief="solid")
        self._repeat_btn.pack(side="left", padx=2)
        self._repeat_btn.bind("<Button-1>", lambda _: self._player_cycle_repeat())

        self._queue_btn = tk.Label(ctrl, text=" Cola ", font=("", 9, "bold"),
                                    bg=DARK["panel"], fg=DARK["fg"],
                                    padx=8, pady=4, cursor="hand2",
                                    borderwidth=1, relief="solid")
        self._queue_btn.pack(side="left", padx=2)
        self._queue_btn.bind("<Button-1>", lambda _: self._player_open_queue())

        prog = ttk.Frame(center)
        prog.pack(pady=(4, 0))
        self._player_pos_var = tk.StringVar(value="0:00")
        self._player_dur_var = tk.StringVar(value="0:00")
        ttk.Label(prog, textvariable=self._player_pos_var, width=5,
                  foreground=DARK["muted"]).pack(side="left")
        self._player_scale = ttk.Scale(prog, from_=0.0, to=1.0, length=300,
                                        command=self._player_user_seek)
        self._player_scale.pack(side="left", padx=4)
        self._player_seeking = False
        # Click: mover thumb a la posición. Drag (B1-Motion): seguir moviéndolo.
        # Release: hacer el seek REAL una sola vez. Esto evita el spam de seeks
        # a VLC que provocaba la sensación de reproducción ralentizada.
        self._player_scale.bind("<Button-1>",        self._on_progress_click)
        self._player_scale.bind("<B1-Motion>",       self._on_progress_motion)
        self._player_scale.bind("<ButtonRelease-1>", self._on_progress_release)
        ttk.Label(prog, textvariable=self._player_dur_var, width=5,
                  foreground=DARK["muted"]).pack(side="left")

        # Bloque derecho: Timer + EQ + volumen
        right = ttk.Frame(bar)
        right.pack(side="left", padx=(10, 0))

        self._timer_btn = tk.Label(right, text=" Timer ", font=("", 9, "bold"),
                                    bg=DARK["panel"], fg=DARK["muted"],
                                    padx=8, pady=4, cursor="hand2",
                                    borderwidth=1, relief="solid")
        self._timer_btn.pack(side="left", padx=(0, 4))
        self._timer_btn.bind("<Button-1>", lambda _: self._player_open_timer())

        self._eq_btn = tk.Label(right, text=" EQ ", font=("", 9, "bold"),
                                 bg=DARK["panel"], fg=DARK["muted"],
                                 padx=8, pady=4, cursor="hand2",
                                 borderwidth=1, relief="solid")
        self._eq_btn.pack(side="left", padx=(0, 4))
        self._eq_btn.bind("<Button-1>", lambda _: self._player_open_eq())

        # Botón Fiesta (fase 2c.5). Click: toggle modo fiesta con BPM mínimo
        # configurado en Ajustes. Click derecho: ajustar BPM mínimo en vivo.
        self._party_btn = tk.Label(right, text=" Fiesta ", font=("", 9, "bold"),
                                    bg=DARK["panel"], fg=DARK["muted"],
                                    padx=8, pady=4, cursor="hand2",
                                    borderwidth=1, relief="solid")
        self._party_btn.pack(side="left", padx=(0, 8))
        self._party_btn.bind("<Button-1>", lambda _: self._player_toggle_party())

        ttk.Label(right, text="Vol", foreground=DARK["muted"]).pack(side="left", padx=(0, 4))
        self._volume_scale = ttk.Scale(right, from_=0, to=100, length=110,
                                        command=self._player_volume_changed)
        self._volume_scale.set(80)
        self._volume_scale.pack(side="left")
        # Click + drag, ambos cambian el volumen en vivo.
        self._volume_scale.bind("<Button-1>",  self._on_volume_click)
        self._volume_scale.bind("<B1-Motion>", self._on_volume_click)

        # Si VLC no está, deshabilitar visualmente y avisar una sola vez en el log
        if not player.is_available():
            self._player_artist_var.set("VLC no disponible — controles desactivados")
            for child in ctrl.winfo_children():
                try: child.state(["disabled"])
                except Exception: pass
            self._volume_scale.state(["disabled"])
            self._player_scale.state(["disabled"])
            log_event("warn", player_mod.availability_message())

    # ---- handlers de los controles ------------------------------------

    def _player_play_pause(self):
        st = player.get_state()
        if st.status == "playing":
            player.pause()
        elif st.status == "paused":
            player.pause()  # vlc.pause() alterna
        else:
            player.play()

    def _player_prev(self): player.prev()
    def _player_next(self): player.next()
    def _player_stop(self): player.stop()

    def _player_toggle_shuffle(self):
        st = player.get_state()
        player.set_shuffle(not st.shuffle)

    def _player_cycle_repeat(self):
        player.cycle_repeat()

    def _player_open_queue(self):
        # Reabrir si ya existe
        if hasattr(self, "_queue_window") and self._queue_window and \
                self._queue_window.winfo_exists():
            self._queue_window.lift()
            self._queue_window.focus_set()
            return
        self._queue_window = QueueDialog(self)

    def _player_open_timer(self):
        if not player.is_available():
            messagebox.showinfo("Reproductor inactivo",
                                "VLC no disponible.", parent=self)
            return
        SleepTimerDialog(self)

    def _player_open_eq(self):
        if not player.is_available():
            messagebox.showinfo("Reproductor inactivo",
                                "VLC no disponible.", parent=self)
            return
        EqualizerDialog(self)

    def _player_toggle_party(self):
        if not player.is_available():
            messagebox.showinfo("Reproductor inactivo",
                                "VLC no disponible.", parent=self)
            return
        st = player.get_state()
        if st.party_enabled:
            player.set_party_mode(False)
            return
        pool_size = player.set_party_mode(
            True,
            min_bpm=state.party_min_bpm,
            max_bpm=state.party_max_bpm,
            party_crossfade_s=state.party_crossfade_s,
        )
        if pool_size == 0:
            messagebox.showwarning(
                "Modo fiesta sin pistas",
                f"Ninguna pista de la biblioteca tiene BPM en "
                f"[{state.party_min_bpm}, {state.party_max_bpm}].\n\n"
                "Pulsa 'Calcular BPM' para analizar pistas sin BPM, "
                "o cambia el rango en Ajustes.",
                parent=self,
            )
            return
        log_event("info",
                  f"Modo fiesta: pool de {pool_size} pistas en el rango")

    def _player_volume_changed(self, v):
        try:
            player.set_volume(int(float(v)))
        except Exception:
            pass

    def _player_user_seek(self, v):
        # callback del Scale; no hace nada. El seek real se hace al soltar.
        pass

    def _on_progress_click(self, event):
        """Click en el slider de progreso: empieza drag, mueve el thumb a esa
        fracción. NO hace seek aún (se hace en el release para evitar spam)."""
        if not player.is_available():
            return "break"
        w = max(1, self._player_scale.winfo_width())
        frac = max(0.0, min(1.0, event.x / w))
        self._player_seeking = True
        self._player_scale.set(frac)
        return "break"   # anula el "page increment" default de ttk.Scale

    def _on_progress_motion(self, event):
        """Drag mientras está pulsado: mueve el thumb siguiendo al ratón."""
        if not self._player_seeking:
            return
        w = max(1, self._player_scale.winfo_width())
        frac = max(0.0, min(1.0, event.x / w))
        self._player_scale.set(frac)
        return "break"

    def _on_progress_release(self, _event):
        """Al soltar: programar un seek con debounce de ~80 ms.
        Si el usuario vuelve a clicar antes de ese tiempo, cancelamos el seek
        anterior y solo aplicamos el último. Evita saturar VLC con seeks
        rapidísimos que provocan el "blip" de velocidad reportado."""
        if not self._player_seeking:
            return
        try:
            frac = float(self._player_scale.get())
        except Exception:
            return
        # Cancelar seek pendiente si lo hay
        prev = getattr(self, "_seek_after_id", None)
        if prev is not None:
            try:
                self.after_cancel(prev)
            except Exception:
                pass
        self._seek_after_id = self.after(80, lambda f=frac: self._do_seek(f))

    def _do_seek(self, frac: float):
        """Ejecuta el seek una vez expirado el debounce."""
        self._seek_after_id = None
        player.seek_to_fraction(frac)
        # Mantener el flag _player_seeking activo un momento para que el
        # polling no sobrescriba el thumb antes de que VLC asiente la posición.
        self.after(200, lambda: setattr(self, "_player_seeking", False))

    def _on_volume_click(self, event):
        """Click + drag del slider de volumen: aplicar volumen en vivo."""
        w = max(1, self._volume_scale.winfo_width())
        frac = max(0.0, min(1.0, event.x / w))
        new_vol = int(round(frac * 100))
        self._volume_scale.set(new_vol)
        player.set_volume(new_vol)
        return "break"

    # ---- polling de estado --------------------------------------------

    def _poll_player(self):
        try:
            st = player.get_state()
            if st.available:
                self._player_title_var.set(st.current_title or "—")
                subtitle = " — ".join(x for x in (st.current_artist, st.current_album) if x)
                self._player_artist_var.set(subtitle or "Sin reproducción")
                self._play_pause_btn.configure(
                    text="⏸" if st.status == "playing" else "▶"
                )
                self._player_pos_var.set(self._fmt_secs(st.position_s))
                self._player_dur_var.set(self._fmt_secs(st.duration_s))
                if not self._player_seeking:
                    self._player_scale.set(st.position_fraction)

                # Indicadores de shuffle/repeat
                self._shuffle_btn.configure(
                    fg=DARK["accent"] if st.shuffle else DARK["muted"]
                )
                if st.repeat == "track":
                    self._repeat_btn.configure(text=" Repetir pista ", fg=DARK["accent"])
                elif st.repeat == "list":
                    self._repeat_btn.configure(text=" Repetir lista ", fg=DARK["accent"])
                else:
                    self._repeat_btn.configure(text=" Repetir ",        fg=DARK["muted"])

                # Indicador del sleep timer
                if st.sleep_seconds_left is not None:
                    m, s = divmod(st.sleep_seconds_left, 60)
                    self._timer_btn.configure(text=f" Sleep {m:02d}:{s:02d} ",
                                               fg=DARK["accent"])
                elif st.sleep_after_track:
                    self._timer_btn.configure(text=" Sleep tras pista ",
                                               fg=DARK["accent"])
                else:
                    self._timer_btn.configure(text=" Timer ", fg=DARK["muted"])

                # Indicador EQ
                if st.eq_preset and st.eq_preset != "Flat":
                    self._eq_btn.configure(text=f" EQ: {st.eq_preset[:8]} ",
                                            fg=DARK["accent"])
                else:
                    self._eq_btn.configure(text=" EQ ", fg=DARK["muted"])

                # Indicador modo fiesta
                if st.party_enabled:
                    self._party_btn.configure(
                        text=f" Fiesta {int(st.party_min_bpm)}–{int(st.party_max_bpm)} ",
                        fg=DARK["accent"],
                    )
                else:
                    self._party_btn.configure(text=" Fiesta ", fg=DARK["muted"])
        except Exception:
            pass
        self.after(300, self._poll_player)

    @staticmethod
    def _fmt_secs(s: int) -> str:
        s = max(0, int(s))
        m, sec = divmod(s, 60)
        return f"{m}:{sec:02d}"

    # ------------------------------------------------------------------
    # Bootstrap y polling
    # ------------------------------------------------------------------

    def _run_bootstrap(self):
        self._status_var.set("Comprobando motor...")
        self._dl_btn.state(["disabled"])

        def worker():
            from core.bootstrap import ensure_dependencies
            ensure_dependencies(lambda msg: self.after(0, lambda m=msg: self._status_var.set(m)))
            start_download_worker()
            self.after(0, self._after_bootstrap)

        threading.Thread(target=worker, daemon=True).start()

    def _after_bootstrap(self):
        self._refresh_infobar()
        self._dl_btn.state(["!disabled"])

        # Sincronizar el índice con disco: si la carpeta raíz existe, eliminar
        # entradas cuyo archivo físico ya no esté. Si la raíz no existe (unidad
        # desmontada), no se purga nada para no perder índice por accidente.
        if state.library_path:
            try:
                removed = library.prune_missing(state.library_path)
                if removed:
                    log_event("info", f"Sincronización inicial: {removed} entrada(s) obsoleta(s) eliminada(s)")
            except Exception as e:
                log_event("warn", f"No se pudo sincronizar índice: {e}")

        if not state.library_path:
            self._ask_library(first_run=True)
        elif has_pending_session():
            if messagebox.askyesno(
                "Sesión interrumpida",
                "Se detectó una sesión interrumpida.\n¿Reanudar la cola pendiente?",
                parent=self,
            ):
                load_queue_from_disk(resume_requested=True)
            else:
                load_queue_from_disk(resume_requested=False)
        else:
            load_queue_from_disk(resume_requested=False)

        self._update_review_badge()
        # Restaurar preset de ecualizador persistido (si VLC está disponible).
        # Aplicar SIEMPRE, incluido "Flat": el __init__ del player ya pre-arma
        # la pipeline con Flat, pero si el usuario guardó un preset distinto
        # hay que aplicarlo aquí antes del primer _play_current().
        if player.is_available() and state.eq_preset:
            try:
                player.set_equalizer_preset(state.eq_preset)
            except Exception:
                pass
        # Restaurar config de crossfade
        if player.is_available():
            try:
                player.set_crossfade(state.crossfade_enabled, state.crossfade_seconds)
            except Exception:
                pass
        self._status_var.set("Listo")
        self.after(500, self._poll_state)
        self.after(300, self._poll_player)

    def _ask_library(self, first_run: bool = False):
        if first_run:
            dlg = WelcomeDialog(self)
            self.wait_window(dlg)
            if dlg.completed:
                self._refresh_infobar()
        else:
            path = filedialog.askdirectory(title="Carpeta de biblioteca", parent=self)
            if path:
                save_config(path=path)
                self._refresh_infobar()

    def _refresh_infobar(self):
        lib = state.library_path or "—"
        disp = ("…" + lib[-40:]) if len(lib) > 43 else lib
        self._lib_lbl.configure(text=f"Biblioteca: {disp}")
        try:
            _, _, free = shutil.disk_usage(lib)
            self._space_lbl.configure(text=f"Espacio libre: {free // (2 ** 30)} GB")
        except Exception:
            self._space_lbl.configure(text="Espacio libre: —")

    def _poll_state(self):
        try:
            self._update_from_state()
        except Exception:
            pass
        self.after(500, self._poll_state)

    def _update_from_state(self):
        with state.lock:
            is_active = any(k in state.session_status
                            for k in ("SCANNING", "LINKED", "MUSICBRAINZ"))
            self._dl_btn.configure(text="Detener" if is_active else "Descargar")

            for key, label in [
                ("SCANNING",     "Analizando enlace..."),
                ("LINKED",       "Descargando..."),
                ("MUSICBRAINZ",  state.session_status.split("//", 1)[-1].strip()),
                ("COMPLETED",    "Completado"),
                ("ABORTED",      "Detenido"),
                ("ERROR",        "Error"),
                ("READY",        "Listo"),
            ]:
                if key in state.session_status:
                    self._status_var.set(label)
                    break

            # Volcar nuevos eventos del event_log al cuadro de texto
            current_len = len(state.event_log)
            if current_len > self._log_last_seen:
                # Tomar los eventos nuevos (snapshot para no mantener lock)
                snap = list(state.event_log)[self._log_last_seen:]
                self._log_last_seen = current_len
                self._log.configure(state="normal")
                for ts, level, msg in snap:
                    h, m, s = time.strftime("%H:%M:%S", time.localtime(ts)).split(":")
                    line = f"[{h}:{m}:{s}] {msg}\n"
                    self._log.insert("end", line, level)
                self._log.see("end")
                self._log.configure(state="disabled")

            if state.active_downloads:
                cur = None
                for v, d in state.active_downloads.items():
                    if d.get("status", "") != "¡Completado!":
                        cur = (v, d); break
                if cur is None:
                    cur = list(state.active_downloads.items())[-1]
                _, data = cur
                self._progressbar["value"] = data.get("progress", 0)
                self._prog_pct_var.set(f"{int(data.get('progress', 0))}%")
            else:
                self._progressbar["value"] = 0
                self._prog_pct_var.set("")

            s = state.global_stats
            self._s_ok.set(str(s["success"]))
            self._s_err.set(str(s["failed"]))
            if state.session_start_time > 0:
                total = int(time.time() - state.session_start_time)
                h, r = divmod(total, 3600)
                m, s_ = divmod(r, 60)
                self._s_t.set(f"{h:02d}:{m:02d}:{s_:02d}" if h else f"{m:02d}:{s_:02d}")
            else:
                self._s_t.set("—")

            # Si hay pistas recientemente completadas y estamos en vista
            # biblioteca, refrescar
            if not is_active and self._current_view == self.VIEW_LIBRARY:
                # Refresco ligero solo si el número de filas cambió
                pass

    # ------------------------------------------------------------------
    # Acciones — Descargar
    # ------------------------------------------------------------------

    def _on_download_click(self):
        with state.lock:
            is_active = any(k in state.session_status
                            for k in ("SCANNING", "LINKED", "MUSICBRAINZ"))
        if is_active:
            with state.lock:
                state.cancel_requested = True
            self._status_var.set("Cancelando...")
        else:
            self._enqueue_download()

    def _enqueue_download(self):
        url = self._url_var.get().strip()
        if not url:
            return
        is_yt = any(d in url for d in ("youtube.com", "youtu.be", "music.youtube.com"))
        if not url.startswith("http") or not is_yt:
            messagebox.showwarning("URL no reconocida",
                                   "Debe ser un enlace de YouTube o YouTube Music.",
                                   parent=self)
            return
        add_download(url, speed="2")
        self._url_var.set("")

    # ------------------------------------------------------------------
    # Acciones — Biblioteca
    # ------------------------------------------------------------------

    def _refresh_library_view(self):
        for iid in self._tree.get_children():
            self._tree.delete(iid)

        # Normalización: ignora acentos y mayúsculas para el filtro de texto.
        filt  = _normalize(self._lib_filter_var.get())
        gfilt = _normalize(self._library_genre_filter)
        rows = []
        for t in library.all_tracks():
            title  = t["title"]  or "—"
            artist = t["artist"] or "—"
            album  = t["album"]  or "—"
            year   = t["year"]   or ""
            mb_ok  = "✦" if t["mb_recording_id"] else ""
            dur    = self._fmt_duration(t["duration_s"])
            genre  = _normalize(t["genre"])

            if gfilt and genre != gfilt:
                continue
            if filt and not any(filt in _normalize(s) for s in (title, artist, album)):
                continue

            bpm_val = t["bpm"]
            bpm_txt = f"{bpm_val:.0f}" if bpm_val is not None else ""
            rows.append({
                "path":  t["path"],
                "title": title, "artist": artist, "album": album,
                "year": year,   "mb": mb_ok,     "duration": dur,
                "duration_s": t["duration_s"] or 0,
                "bpm": bpm_txt,
                "bpm_v": bpm_val if bpm_val is not None else -1.0,
            })

        # Ordenar según el estado actual
        col, reverse = self._sort_state
        def keyf(r):
            if col == "duration":
                return r["duration_s"]
            if col == "bpm":
                return r["bpm_v"]
            if col == "year":
                return r["year"] or ""
            return (r.get(col) or "").lower()
        rows.sort(key=keyf, reverse=reverse)

        for r in rows:
            self._tree.insert("", "end", iid=r["path"],
                              values=(r["title"], r["artist"], r["album"],
                                      r["year"], r["mb"], r["bpm"], r["duration"]))

        # Actualizar los headers con la flecha de ordenación activa
        for c, base_label in self._col_labels.items():
            arrow = ("  ▼" if reverse else "  ▲") if c == col else ""
            self._tree.heading(c, text=base_label + arrow)

        label = f"{len(rows)} pistas"
        if self._library_genre_filter:
            label += f" — Género: {self._library_genre_filter}"
        self._lib_count_var.set(label)
        self._refresh_sidebar_counts()

    def _sort_by_column(self, col: str):
        cur_col, cur_rev = self._sort_state
        # Si pulsas la misma columna, invierte. Si pulsas otra, ascendente.
        self._sort_state = (col, not cur_rev) if col == cur_col else (col, False)
        save_config(sort_column=self._sort_state[0], sort_reverse=self._sort_state[1])
        self._refresh_library_view()

    def _fmt_duration(self, s):
        if not s:
            return ""
        m, sec = divmod(int(s), 60)
        return f"{m}:{sec:02d}"

    def _play_selected(self):
        """Reproduce la selección en el reproductor integrado y carga el resto
        de la vista actual como cola a partir de ella. Si VLC no está, cae al
        reproductor externo del sistema."""
        sel = self._tree.selection()
        if not sel:
            return
        start_path = sel[0]
        if not Path(start_path).exists():
            return

        if not player.is_available():
            _open_path(start_path)
            return

        # Cargar la vista actual como cola, empezando por la selección
        visible_paths = list(self._tree.get_children())
        try:
            start_idx = visible_paths.index(start_path)
        except ValueError:
            start_idx = 0
        player.load_queue(visible_paths, start_index=start_idx)

    def _show_lib_context_menu(self, event):
        iid = self._tree.identify_row(event.y)
        if iid:
            # Si no estaba seleccionada, la seleccionamos para que el menú apunte
            # a la fila bajo el cursor.
            if iid not in self._tree.selection():
                self._tree.selection_set(iid)
            self._lib_ctx_menu.tk_popup(event.x_root, event.y_root)

    def _selected_path(self) -> Optional[str]:
        sel = self._tree.selection()
        return sel[0] if sel else None

    def _ctx_add_to_queue(self):
        path = self._selected_path()
        if not path: return
        if not player.is_available():
            messagebox.showinfo("Reproductor inactivo",
                                player_mod.availability_message(), parent=self)
            return
        player.add_to_queue(path)
        log_event("info", f"Añadido a la cola: {Path(path).name}")

    def _ctx_play_next(self):
        path = self._selected_path()
        if not path: return
        if not player.is_available():
            messagebox.showinfo("Reproductor inactivo",
                                player_mod.availability_message(), parent=self)
            return
        player.append_after_current(path)
        log_event("info", f"Próximo en cola: {Path(path).name}")

    def _ctx_play_external(self):
        path = self._selected_path()
        if path and Path(path).exists():
            _open_path(path)

    def _ctx_edit_tags(self):
        path = self._selected_path()
        if not path:
            return
        ManualTagsDialog(self, path)

    def _ctx_mb_search(self):
        path = self._selected_path()
        if not path:
            return
        row = library.get_track_by_path(path)
        t = (row["title"]  if row else None) or Path(path).stem
        a = (row["artist"] if row else None) or ""
        ManualMBSearchDialog(self, path, t, a)

    def _ctx_reenrich(self):
        path = self._selected_path()
        if not path:
            return
        row = library.get_track_by_path(path)
        if not row:
            return
        # Forzar reenriquecimiento aunque ya tenga MB ID: limpiar el MB ID
        # antes de llamar a enrich.
        library.upsert_track(path, mb_recording_id=None, mb_release_id=None)
        row = library.get_track_by_path(path)
        self._status_var.set("Consultando MusicBrainz...")

        def worker():
            try:
                ok = pipeline.enrich_existing_track(row)
                self.after(0, lambda: self._status_var.set(
                    "Reenriquecido" if ok else "Sin match en MusicBrainz"))
            except Exception as e:
                self.after(0, lambda: self._status_var.set(f"Error: {e}"))
            self.after(0, self._refresh_library_view)

        threading.Thread(target=worker, daemon=True).start()

    def _ctx_open_folder(self):
        path = self._selected_path()
        if not path:
            return
        folder = str(Path(path).parent)
        if Path(folder).exists():
            _open_path(folder)

    def _ctx_remove_from_index(self):
        path = self._selected_path()
        if not path:
            return
        if not messagebox.askyesno(
            "Quitar del índice",
            f"¿Quitar '{Path(path).name}' del índice?\n\n"
            "El archivo físico NO se elimina.",
            parent=self,
        ):
            return
        library.delete_track(path)
        self._refresh_library_view()

    def _ctx_delete_file(self):
        path = self._selected_path()
        if not path:
            return
        if not messagebox.askyesno(
            "Eliminar archivo",
            f"Se ELIMINARÁ el archivo físico:\n\n{path}\n\n"
            "Esta acción no se puede deshacer. ¿Continuar?",
            parent=self,
            icon="warning",
        ):
            return
        try:
            Path(path).unlink(missing_ok=True)
            library.delete_track(path)
            self._refresh_library_view()
            log_event("warn", f"Archivo eliminado: {Path(path).name}")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo eliminar:\n{e}", parent=self)

    def _clear_lib_filters(self):
        """Vacía el filtro de texto y el filtro de género activo."""
        self._lib_filter_var.set("")
        self._library_genre_filter = ""
        # Desmarcar género en la sidebar
        try:
            self._suppress_sidebar_event = True
            self._sidebar.selection_set(self.VIEW_LIBRARY)
            self.after_idle(self._clear_suppress_sidebar_flag)
        except tk.TclError:
            pass
        self._refresh_library_view()

    def _refresh_library_with_prune(self):
        """Purga entradas inexistentes y repinta. Más lento que repintar a secas,
        pero refleja el estado real del disco."""
        if state.library_path:
            try:
                removed = library.prune_missing(state.library_path)
                if removed:
                    self._status_var.set(f"Refrescado — {removed} entradas obsoletas eliminadas")
                    log_event("info", f"Refrescar: {removed} entrada(s) obsoleta(s) eliminada(s)")
                else:
                    self._status_var.set("Refrescado")
            except Exception as e:
                self._status_var.set(f"Error al refrescar: {e}")
        self._refresh_library_view()

    def _clear_index(self):
        if not messagebox.askyesno(
            "Vaciar índice",
            "Se borrarán TODAS las entradas del índice de la biblioteca "
            "(library.db) y de la bandeja de revisión.\n\n"
            "Los archivos físicos NO se tocan.\n\n"
            "Útil para empezar desde cero. ¿Continuar?",
            parent=self,
        ):
            return
        try:
            n = library.clear_index()
            self._status_var.set(f"Índice vaciado — {n} entradas eliminadas")
            log_event("info", f"Índice vaciado: {n} entrada(s)")
            self._refresh_library_view()
            self._refresh_review_view()
            self._update_review_badge()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo vaciar el índice:\n{e}", parent=self)

    def _scan_library(self):
        if not state.library_path:
            messagebox.showwarning("Sin biblioteca",
                                   "Configura primero una carpeta de biblioteca.", parent=self)
            return
        self._status_var.set("Escaneando biblioteca...")

        def worker():
            try:
                total = library.scan(
                    state.library_path,
                    on_progress=lambda i, n, p:
                        self.after(0, lambda i=i, n=n: self._status_var.set(
                            f"Escaneando {i}/{n}")),
                )
                self.after(0, lambda: self._status_var.set(f"Escaneo OK — {total} pistas indexadas"))
                self.after(0, self._refresh_library_view)
            except Exception as e:
                self.after(0, lambda: self._status_var.set(f"Error en escaneo: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _enrich_library(self):
        if not state.library_path:
            messagebox.showwarning("Sin biblioteca",
                                   "Configura primero una carpeta de biblioteca.", parent=self)
            return

        pending = list(library.tracks_without_recording_id())
        if not pending:
            messagebox.showinfo("Nada que enriquecer",
                                "Todas las pistas ya tienen un MusicBrainz Recording ID.",
                                parent=self)
            return

        total = len(pending)
        if not messagebox.askyesno(
            "Confirmar enriquecimiento",
            f"Se consultarán {total} pistas en MusicBrainz (≈1 segundo por pista, "
            f"unos {total} segundos en total).\n"
            "Los archivos se quedan en su sitio actual; solo se actualizan tags y el índice.\n\n"
            "¿Continuar?",
            parent=self,
        ):
            return

        dlg = EnrichProgressDialog(self, total)

        def worker():
            applied = 0
            done = 0
            for t in pending:
                if dlg.cancelled:
                    break
                title = (t["title"] or Path(t["path"]).name)
                self.after(0, lambda d=done, ti=title:
                           dlg.update_progress(d, ti))
                try:
                    ok = pipeline.enrich_existing_track(t)
                    if ok:
                        applied += 1
                        result = f"✔ Match: {title}"
                    else:
                        result = f"✗ Sin match: {title}"
                except Exception as e:
                    result = f"✗ Error: {title} — {e}"
                done += 1
                self.after(0, lambda d=done, ti=title, r=result:
                           dlg.update_progress(d, ti, r))
            self.after(0, lambda: dlg.finish(applied, total, dlg.cancelled))
            self.after(0, self._refresh_library_view)
            self.after(0, self._refresh_review_view)
            self.after(0, self._update_review_badge)

        threading.Thread(target=worker, daemon=True).start()

    def _compute_bpm_library(self):
        """Calcula el BPM de todas las pistas que tienen bpm IS NULL."""
        if not state.library_path:
            messagebox.showwarning("Sin biblioteca",
                                   "Configura primero una carpeta de biblioteca.", parent=self)
            return

        if not bpm_mod.is_available():
            messagebox.showwarning(
                "Cálculo de BPM no disponible",
                bpm_mod.availability_message(),
                parent=self,
            )
            return

        pending = list(library.tracks_without_bpm())
        if not pending:
            messagebox.showinfo("Nada que calcular",
                                "Todas las pistas tienen BPM ya calculado.",
                                parent=self)
            return

        total = len(pending)
        # ~3 s/pista en un Ryzen moderno con librosa
        est_min = max(1, round(total * 3 / 60))
        if not messagebox.askyesno(
            "Confirmar cálculo de BPM",
            f"Se analizarán {total} pistas (~{est_min} min en total con librosa).\n"
            "Los archivos no se mueven; solo se actualiza el índice.\n\n"
            "¿Continuar?",
            parent=self,
        ):
            return

        dlg = EnrichProgressDialog(
            self, total,
            window_title="Calcular BPM",
            heading="Calculando BPM (librosa)",
            subheading=f"{total} pistas en cola. ~2-5 s por pista. Puedes cancelar en cualquier momento.",
            finish_template="{applied} de {total} pistas con BPM calculado.",
        )

        def worker():
            applied = 0
            done = 0
            for t in pending:
                if dlg.cancelled:
                    break
                title = (t["title"] or Path(t["path"]).name)
                self.after(0, lambda d=done, ti=title:
                           dlg.update_progress(d, ti))
                try:
                    val = bpm_mod.compute_bpm(t["path"])
                    if val:
                        library.upsert_track(t["path"], bpm=val)
                        applied += 1
                        result = f"✔ {val:.1f} BPM: {title}"
                    else:
                        result = f"✗ Sin BPM: {title}"
                except Exception as e:
                    result = f"✗ Error: {title} — {e}"
                done += 1
                self.after(0, lambda d=done, ti=title, r=result:
                           dlg.update_progress(d, ti, r))
            self.after(0, lambda: dlg.finish(applied, total, dlg.cancelled))
            self.after(0, self._refresh_library_view)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Acciones — Bandeja de revisión
    # ------------------------------------------------------------------

    def _refresh_review_view(self):
        self._review_list.delete(0, "end")
        self._review_paths.clear()
        for row in library.pending_review():
            path = row["path"]
            name = Path(path).name
            self._review_list.insert("end", f"{name}  [{row['reason']}]")
            self._review_paths.append(path)
        self._update_review_badge()

        # Limpiar candidatos
        for iid in self._cand_tree.get_children():
            self._cand_tree.delete(iid)

    def _update_review_badge(self):
        n = len(list(library.pending_review()))
        try:
            self._sidebar.set(self.VIEW_REVIEW, "count", str(n) if n else "")
        except tk.TclError:
            pass

    def _show_review_candidates(self):
        sel = self._review_list.curselection()
        if not sel:
            return
        path = self._review_paths[sel[0]]
        row = next(
            (r for r in library.pending_review() if r["path"] == path),
            None,
        )
        for iid in self._cand_tree.get_children():
            self._cand_tree.delete(iid)
        if not row:
            return
        try:
            candidates = json.loads(row["candidates"] or "[]")
        except json.JSONDecodeError:
            candidates = []
        for c in candidates:
            self._cand_tree.insert("", "end", values=(
                c.get("title", "—"), c.get("artist", "—"), c.get("album", "—"),
                c.get("year", ""),   c.get("score", ""),
            ), tags=(c.get("recording_id", ""),))

    def _apply_selected_candidate(self):
        sel_list = self._review_list.curselection()
        sel_cand = self._cand_tree.selection()
        if not sel_list or not sel_cand:
            messagebox.showinfo("Selección incompleta",
                                "Selecciona una pista en la lista y un candidato en la tabla.",
                                parent=self)
            return

        path = self._review_paths[sel_list[0]]
        values = self._cand_tree.item(sel_cand[0], "values")
        # values: (title, artist, album, year, score)
        title, artist, album, year, _ = values

        # Tomar el resto de campos del JSON original
        row = next((r for r in library.pending_review() if r["path"] == path), None)
        candidates = json.loads(row["candidates"] or "[]") if row else []
        chosen = next((c for c in candidates if c.get("title") == title
                       and c.get("artist") == artist), None)
        if not chosen:
            return

        new_meta = {
            "title":           chosen.get("title"),
            "artist":          chosen.get("artist"),
            "album":           chosen.get("album"),
            "year":            chosen.get("year"),
            "track_number":    chosen.get("track_number"),
            "genre":           chosen.get("genre"),
            "isrc":            chosen.get("isrc"),
            "mb_recording_id": chosen.get("recording_id"),
            "mb_release_id":   chosen.get("release_id"),
        }
        pipeline._write_tags(Path(path), new_meta, embed_cover=True)
        library.upsert_track(path, **{k: v for k, v in new_meta.items() if k != "mb_recording_id"
                                       and k != "mb_release_id"},
                              mb_recording_id=chosen.get("recording_id"),
                              mb_release_id=chosen.get("release_id"))
        library.remove_from_review(path)
        self._refresh_review_view()
        self._refresh_library_view()
        messagebox.showinfo("Candidato aplicado", f"Tags actualizados: {title} — {artist}",
                            parent=self)

    def _mark_as_is(self):
        sel = self._review_list.curselection()
        if not sel:
            return
        path = self._review_paths[sel[0]]
        library.remove_from_review(path)
        self._refresh_review_view()

    def _manual_mb_search(self):
        sel = self._review_list.curselection()
        if not sel:
            messagebox.showinfo("Selecciona pista",
                                "Selecciona primero una pista en la lista de la izquierda.",
                                parent=self)
            return
        path = self._review_paths[sel[0]]
        row = library.get_track_by_path(path)
        initial_title  = (row["title"]  if row else None) or Path(path).stem
        initial_artist = (row["artist"] if row else None) or ""
        ManualMBSearchDialog(self, path, initial_title, initial_artist)

    def _manual_edit_tags(self):
        sel = self._review_list.curselection()
        if not sel:
            messagebox.showinfo("Selecciona pista",
                                "Selecciona primero una pista en la lista de la izquierda.",
                                parent=self)
            return
        path = self._review_paths[sel[0]]
        ManualTagsDialog(self, path)

    def _open_review_file(self):
        sel = self._review_list.curselection()
        if not sel:
            return
        path = self._review_paths[sel[0]]
        if Path(path).exists():
            _open_path(path)

    # ------------------------------------------------------------------
    # Otros
    # ------------------------------------------------------------------

    def _open_settings(self):
        dlg = SettingsDialog(self)
        # Cuando se cierre, refrescar el indicador (puede haber cambiado el toggle)
        self.wait_window(dlg)
        self._refresh_mb_indicator()

    def _toggle_mb(self):
        """Toggle directo de musicbrainz_enabled desde el indicador MB."""
        new = not state.musicbrainz_enabled
        save_config(musicbrainz_enabled=new)
        self._refresh_mb_indicator()
        log_event("info", f"MusicBrainz: {'ON' if new else 'OFF'}")

    def _refresh_mb_indicator(self):
        if not hasattr(self, "_mb_indicator"):
            return
        if state.musicbrainz_enabled:
            self._mb_indicator.configure(
                text="MB: ON",
                fg=DARK["success_fg"], bg=DARK["success_bg"],
            )
        else:
            self._mb_indicator.configure(
                text="MB: OFF",
                fg=DARK["muted"], bg=DARK["panel"],
            )

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
        state.event_log.clear()
        self._log_last_seen = 0

    def _on_close(self):
        # Persistir geometría actual antes de cerrar
        try:
            save_config(window_geometry=self.geometry())
        except Exception:
            pass
        state.is_running = False
        self.destroy()

    # ------------------------------------------------------------------
    # Atajos de teclado
    # ------------------------------------------------------------------

    def _bind_global_shortcuts(self):
        # Vistas
        self.bind_all("<Control-d>", lambda _: self._switch_view(self.VIEW_DOWNLOAD))
        self.bind_all("<Control-l>", lambda _: self._switch_view(self.VIEW_LIBRARY))
        self.bind_all("<Control-r>", lambda _: self._switch_view(self.VIEW_REVIEW))
        # Atajos sobre la biblioteca (solo cuando la vista está activa)
        self.bind_all("<F5>",     self._shortcut_refresh)
        self.bind_all("<F2>",     self._shortcut_edit_tags)
        self.bind_all("<Return>", self._shortcut_enter)
        self.bind_all("<Delete>", self._shortcut_delete)

    def _shortcut_refresh(self, _event=None):
        if self._current_view == self.VIEW_LIBRARY:
            self._refresh_library_with_prune()
        elif self._current_view == self.VIEW_REVIEW:
            self._refresh_review_view()

    def _shortcut_edit_tags(self, event=None):
        if self._current_view == self.VIEW_LIBRARY:
            self._ctx_edit_tags()
        elif self._current_view == self.VIEW_REVIEW:
            self._manual_edit_tags()

    def _shortcut_enter(self, event=None):
        # Enter en campos de texto debe seguir funcionando como Enter normal.
        focus = self.focus_get()
        if isinstance(focus, (tk.Entry, tk.Text)):
            return
        if self._current_view == self.VIEW_LIBRARY:
            self._play_selected()

    def _shortcut_delete(self, event=None):
        focus = self.focus_get()
        if isinstance(focus, (tk.Entry, tk.Text)):
            return  # respeta la edición de texto
        if self._current_view == self.VIEW_LIBRARY and self._selected_path():
            self._ctx_remove_from_index()


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def run_gui() -> None:
    app = MusicGrabberGUI()
    app.mainloop()
