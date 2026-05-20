#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/statusbar.py — Barre de statut
===================================
Affiche :
  - Un message textuel (info, succès, avertissement, erreur)
  - Le nombre de notices chargées / sélectionnées
  - Un indicateur coloré selon le type de message
"""

import tkinter as tk
from config import COLORS


class StatusBar(tk.Frame):
    """
    Barre de statut en bas de la fenêtre principale.

    Usage :
        bar = StatusBar(parent)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        bar.set_message("Fichier chargé.", level="success")
        bar.set_counts(total=42, selected=10)
    """

    # Couleurs par niveau de message
    _LEVEL_COLORS = {
        "info":    COLORS["info"],
        "success": COLORS["success"],
        "warning": COLORS["warning"],
        "error":   COLORS["error"],
        "idle":    COLORS["text_muted"],
    }

    def __init__(self, parent: tk.Widget, **kwargs):
        super().__init__(parent, bd=1, relief=tk.SUNKEN,
                         bg=COLORS["bg"], **kwargs)

        # Indicateur coloré (petit carré)
        self._indicator = tk.Label(
            self, text="●", width=2,
            bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=("Helvetica", 10),
        )
        self._indicator.pack(side=tk.LEFT, padx=(6, 2))

        # Message principal
        self._msg_var = tk.StringVar(value="Prêt.")
        self._msg_label = tk.Label(
            self, textvariable=self._msg_var,
            bg=COLORS["bg"], fg=COLORS["text_muted"],
            anchor=tk.W, font=("Helvetica", 9),
        )
        self._msg_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        # Compteurs notices
        self._counts_var = tk.StringVar(value="")
        self._counts_label = tk.Label(
            self, textvariable=self._counts_var,
            bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=("Helvetica", 9),
        )
        self._counts_label.pack(side=tk.RIGHT, padx=10)

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def set_message(self, text: str, level: str = "info") -> None:
        """
        Affiche un message dans la barre de statut.

        Args:
            text  : Texte à afficher.
            level : Niveau parmi 'info', 'success', 'warning', 'error', 'idle'.
        """
        color = self._LEVEL_COLORS.get(level, self._LEVEL_COLORS["info"])
        self._msg_var.set(text)
        self._msg_label.config(fg=color)
        self._indicator.config(fg=color)

    def set_counts(self, total: int = 0, selected: int = 0) -> None:
        """Met à jour l'affichage des compteurs de notices."""
        if total == 0:
            self._counts_var.set("")
        else:
            self._counts_var.set(f"{selected} / {total} notice(s) sélectionnée(s)")

    def clear(self) -> None:
        """Remet la barre dans l'état initial."""
        self.set_message("Prêt.", level="idle")
        self.set_counts(0, 0)
