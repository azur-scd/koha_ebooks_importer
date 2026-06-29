#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/toolbar.py — Barre d'outils de l'application
"""

import tkinter as tk
from typing import Callable, Dict
from config import COLORS, APP_TITLE, APP_VERSION


class Toolbar(tk.Frame):
    """
    Barre d'outils horizontale.

    callbacks : dict  nom_action → callable
        Clés : 'import', 'prepare', 'oai_fetch', 'oai_match',
                'sudoc_enrich', 'export', 'reset', 'quit'
    """

    _BUTTONS = [
        ("Importer fichier UNIMARC", "import",       "primary", False),
        ("Préparer",                  "prepare",      "primary", True),
        ("Récupérer OAI-PMH",        "oai_fetch",    "primary", True),
        ("Croiser UNIMARC / OAI",    "oai_match",    "primary", False),
        ("Enrichissement Sudoc",      "sudoc_enrich", "primary", False),
        ("Recherche Koha",             "koha_search",  "primary", False),
        ("Exporter en MARCXML",       "export",       "primary", True),
        ("Réinitialiser",             "reset",        "danger",  True),
        ("Quitter",                   "quit",         "danger",  True),
    ]

    def __init__(self, parent: tk.Widget, callbacks: Dict[str, Callable], **kwargs):
        super().__init__(parent, bg=COLORS["sidebar"], padx=6, pady=4, **kwargs)
        self._buttons: Dict[str, tk.Button] = {}
        self._build(callbacks)

    def _build(self, callbacks: Dict[str, Callable]) -> None:
        # ── Titre + version ───────────────────────────────────────────
        words = APP_TITLE.upper().split()
        mid   = len(words) // 2
        title_frame = tk.Frame(self, bg=COLORS["sidebar"], padx=8)
        title_frame.pack(side=tk.LEFT)
        tk.Label(
            title_frame,
            text=" ".join(words[:mid]) + "\n" + " ".join(words[mid:]),
            bg=COLORS["sidebar"], fg=COLORS["sidebar_text"],
            font=("Courier", 8, "bold"), justify=tk.LEFT,
        ).pack(anchor=tk.W)
        tk.Label(
            title_frame,
            text=f"v{APP_VERSION}",
            bg=COLORS["sidebar"], fg=COLORS["text_muted"],
            font=("Courier", 7), justify=tk.LEFT,
        ).pack(anchor=tk.W)

        # ── Séparateur vertical ───────────────────────────────────────
        tk.Frame(self, width=1, bg=COLORS["border"]).pack(
            side=tk.LEFT, fill=tk.Y, padx=6, pady=2
        )

        # ── Boutons ───────────────────────────────────────────────────
        for label, key, style, sep in self._BUTTONS:
            if sep:
                tk.Frame(self, width=1, bg=COLORS["border"]).pack(
                    side=tk.LEFT, fill=tk.Y, padx=4, pady=2
                )
            btn = tk.Button(
                self,
                text=label,
                command=callbacks.get(key, lambda: None),
                relief=tk.RAISED,          # Cadre visible → bouton cliquable
                bd=1,
                cursor="hand2",
                font=("Helvetica", 8, "bold"),
                padx=7, pady=3,
                **self._btn_style(style),
            )
            btn.pack(side=tk.LEFT, padx=2)
            self._bind_hover(btn, style)
            self._buttons[key] = btn

    @staticmethod
    def _btn_style(style: str) -> dict:
        if style == "primary":
            return {
                "bg": COLORS["btn_primary"], "fg": COLORS["sidebar_text"],
                "activebackground": COLORS["accent"],
                "activeforeground": "#ffffff",
            }
        # danger
        return {
            "bg": "#6b2020", "fg": "#ffcccc",
            "activebackground": "#8b2020",
            "activeforeground": "#ffffff",
        }

    def _bind_hover(self, btn: tk.Button, style: str) -> None:
        s = self._btn_style(style)
        def on_enter(_):
            if btn["state"] != tk.DISABLED:
                btn.config(bg=s["activebackground"], fg=s["activeforeground"])
        def on_leave(_):
            if btn["state"] != tk.DISABLED:
                btn.config(bg=s["bg"], fg=s["fg"])
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)

    def set_enabled(self, key: str, enabled: bool) -> None:
        btn = self._buttons.get(key)
        if btn:
            btn.config(state=tk.NORMAL if enabled else tk.DISABLED)

    def get_button(self, key: str) -> tk.Button:
        """Retourne le widget Button associé à une clé (pour focus, etc.)."""
        return self._buttons.get(key)

    def get_button(self, key: str) -> tk.Button:
        return self._buttons.get(key)
