from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# UI Theme
COLORS = {
    "bg": "#121212",
    "panel": "#1e1e1e",
    "input": "#2a2a2a",
    "border": "#2d2d2d",
    "text": "#eaeaea",
    "muted": "#a8a8a8",
    "accent": "#3a86ff",
    "success": "#4caf50",
    "warning": "#ff9800",
    "error": "#ef5350",
}

FONT = "Segoe UI"
MONO = "Consolas"


def apply_ttk_style(root: tk.Tk) -> ttk.Style:
    """Dark styling for the ttk widgets (tabs, trees, dropdowns, scrollbars)."""
    style = ttk.Style(root)
    style.theme_use("clam")
    c = COLORS
    # Tk 9 draws a light focus outline round tk widgets by default; widgets
    # that want a border set highlightthickness themselves
    root.option_add("*highlightThickness", 0)

    style.configure(".", background=c["panel"], foreground=c["text"], bordercolor=c["border"],
                    lightcolor=c["panel"], darkcolor=c["panel"], troughcolor=c["bg"],
                    focuscolor=c["accent"], font=(FONT, 10))
    style.configure("TFrame", background=c["panel"])
    style.configure("TPanedwindow", background=c["bg"])

    style.configure("TNotebook", background=c["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=c["panel"], foreground=c["muted"],
                    padding=(16, 8), borderwidth=0, lightcolor=c["panel"],
                    bordercolor=c["border"])
    style.map("TNotebook.Tab",
              background=[("selected", c["input"])],
              foreground=[("selected", c["text"])],
              lightcolor=[("selected", c["input"])],
              padding=[("selected", (16, 8))],
              expand=[("selected", (0, 0, 0, 0))])

    style.configure("Treeview", background=c["input"], fieldbackground=c["input"],
                    foreground=c["text"], rowheight=24, borderwidth=0)
    style.map("Treeview",
              background=[("selected", c["accent"])],
              foreground=[("selected", "white")])
    style.configure("Treeview.Heading", background=c["panel"], foreground=c["muted"],
                    relief="flat", font=(FONT, 9, "bold"))
    style.map("Treeview.Heading", background=[("active", c["input"])])

    style.configure("TCombobox", fieldbackground=c["input"], background=c["panel"],
                    foreground=c["text"], arrowcolor=c["text"], selectbackground=c["input"],
                    selectforeground=c["text"])
    style.map("TCombobox",
              fieldbackground=[("readonly", c["input"])],
              foreground=[("readonly", c["text"])])
    root.option_add("*TCombobox*Listbox.background", c["input"])
    root.option_add("*TCombobox*Listbox.foreground", c["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])

    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", background="#444", troughcolor=c["input"],
                        bordercolor=c["input"], lightcolor="#444", darkcolor="#444",
                        arrowcolor=c["muted"], borderwidth=0)
        style.map(f"{orient}.TScrollbar", background=[("active", "#555")])
    style.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["input"],
                    bordercolor=c["input"], lightcolor=c["accent"], darkcolor=c["accent"])
    return style


def button(parent: tk.Misc, text: str, command, primary: bool = False) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=COLORS["accent"] if primary else "#444",
        fg="white",
        activebackground=COLORS["accent"],
        activeforeground="white",
        relief="flat",
        highlightthickness=0,
        padx=12,
        pady=6,
        cursor="hand2",
    )


def entry(parent: tk.Misc, variable: tk.StringVar, width: int = 20) -> tk.Entry:
    return tk.Entry(
        parent,
        textvariable=variable,
        width=width,
        bg=COLORS["input"],
        fg=COLORS["text"],
        insertbackground=COLORS["text"],
        relief="flat",
        highlightthickness=0,
        bd=6,
        font=(FONT, 10),
    )


def label(parent: tk.Misc, text: str = "", muted: bool = False, bold: bool = False, **kwargs) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=kwargs.pop("bg", COLORS["panel"]),
        fg=COLORS["muted"] if muted else COLORS["text"],
        font=(FONT, 10, "bold") if bold else (FONT, 9 if muted else 10),
        **kwargs,
    )
