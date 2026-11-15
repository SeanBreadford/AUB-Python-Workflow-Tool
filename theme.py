from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# UI Theme (the project browser's web page uses the same palette in web/app.css)
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


def apply_ttk_style(root: tk.Tk) -> ttk.Style:
    """Dark styling for the few ttk widgets the desktop app uses."""
    style = ttk.Style(root)
    style.theme_use("clam")
    # Tk 9 draws a light focus outline round tk widgets by default; widgets
    # that want a border set highlightthickness themselves
    root.option_add("*highlightThickness", 0)
    c = COLORS
    style.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["input"],
                    bordercolor=c["input"], lightcolor=c["accent"], darkcolor=c["accent"])
    return style
