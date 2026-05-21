#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/main_window.py — Fenêtre principale de l'application
========================================================
Assemble :
  - Toolbar (barre d'outils générale, haut)
  - Panneau de statistiques (gauche, fixe) : compteurs et état courant
  - Notebook avec trois onglets :
      • "Données source"    : notices UNIMARC importées, sélectionnables
                              (mini-barre ☑/☐ au-dessus du tableau)
      • "Données préparées" : notices après traitement Koha + enrichissements,
                              lecture seule, rafraîchi après chaque étape
      • "Données OAI-PMH"   : notices Dublin Core collectées, lecture seule
  - StatusBar (bas) : message courant et compteur de sélection

Ce module ne contient que la logique de présentation.
Toute logique applicative reste dans app.py.

Pour ajouter un onglet : créer un tableau dans _build_notebook() et
exposer les méthodes de chargement/reset dans l'API publique.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from config import COLORS, APP_TITLE, APP_VERSION, SOURCE_COLUMNS, PREPARED_COLUMNS, OAI_COLUMNS
from marc.reader import MarcRecord
from oai.harvester import OaiRecord
from ui.toolbar import Toolbar
from ui.records_table import RecordsTable
from ui.oai_table import OaiTable
from ui.statusbar import StatusBar


class MainWindow:
    """
    Vue principale.

    Args:
        root                : Fenêtre Tk racine.
        callbacks           : Dict action → callable (transmis à Toolbar).
        on_selection_change : Appelé quand la sélection de l'onglet source change.
    """

    def __init__(
        self,
        root: tk.Tk,
        callbacks: Dict[str, Callable],
        on_selection_change: Callable[[List[int]], None],
    ):
        self._root = root
        self._root.title(f"{APP_TITLE}  v{APP_VERSION}")
        self._root.configure(bg=COLORS["bg"])

        self._build_ui(callbacks, on_selection_change)

        # Focus initial sur le bouton Importer
        self._root.after(100, self._set_initial_focus)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(
        self,
        callbacks: Dict[str, Callable],
        on_selection_change: Callable,
    ) -> None:
        """Construit et positionne tous les widgets."""

        # Toolbar (haut)
        self._toolbar = Toolbar(self._root, callbacks=callbacks)
        self._toolbar.pack(side=tk.TOP, fill=tk.X)

        # StatusBar (bas — déclaré avant le centre pour le pack order)
        self._statusbar = StatusBar(self._root)
        self._statusbar.pack(side=tk.BOTTOM, fill=tk.X)

        # Zone centrale
        center = tk.Frame(self._root, bg=COLORS["bg"])
        center.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Panneau de statistiques (gauche, largeur fixe)
        self._stats_panel = self._build_stats_panel(center)
        self._stats_panel.pack(side=tk.LEFT, fill=tk.Y)

        # Notebook avec les onglets (droite, extensible)
        # On extrait les callbacks de sélection pour la mini-barre de l'onglet source
        on_sel_cbs = {
            "select_all":   callbacks.get("select_all",   lambda: None),
            "deselect_all": callbacks.get("deselect_all", lambda: None),
        }
        self._build_notebook(center, on_selection_change, on_sel_cbs)

    def _build_stats_panel(self, parent: tk.Widget) -> tk.Frame:
        """Construit le panneau latéral de statistiques."""
        panel = tk.Frame(parent, bg=COLORS["sidebar"], width=170)
        panel.pack_propagate(False)

        tk.Label(
            panel, text="STATISTIQUES",
            bg=COLORS["sidebar"], fg=COLORS["sidebar_text"],
            font=("Courier", 8, "bold"), pady=10,
        ).pack(fill=tk.X, padx=10)

        ttk.Separator(panel, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10)

        self._stat_vars: Dict[str, tk.StringVar] = {}

        for key, label, default in [
            ("total",    "Notices chargées", "0"),
            ("selected", "Sélectionnées",    "0"),
            ("prepared", "Préparées",        "0"),
            ("oai",      "Notices OAI",      "0"),
            ("matched",  "Appariées",        "0"),
            ("sudoc",    "PPN Sudoc",        "0"),
        ]:
            var = tk.StringVar(value=default)
            self._stat_vars[key] = var

            row = tk.Frame(panel, bg=COLORS["sidebar"])
            row.pack(fill=tk.X, padx=12, pady=6)
            tk.Label(
                row, text=label,
                bg=COLORS["sidebar"], fg=COLORS["text_muted"],
                font=("Helvetica", 8), anchor=tk.W,
            ).pack(fill=tk.X)
            tk.Label(
                row, textvariable=var,
                bg=COLORS["sidebar"], fg=COLORS["sidebar_text"],
                font=("Helvetica", 18, "bold"), anchor=tk.W,
            ).pack(fill=tk.X)

        ttk.Separator(panel, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=6)

        self._state_var = tk.StringVar(value="En attente")
        tk.Label(
            panel, text="ÉTAT",
            bg=COLORS["sidebar"], fg=COLORS["text_muted"],
            font=("Courier", 8, "bold"),
        ).pack(padx=10, anchor=tk.W)
        self._state_label = tk.Label(
            panel, textvariable=self._state_var,
            bg=COLORS["sidebar"], fg=COLORS["accent"],
            font=("Helvetica", 9, "bold"), wraplength=140, justify=tk.LEFT,
        )
        self._state_label.pack(padx=12, pady=4, anchor=tk.W)

        return panel

    def _build_notebook(
        self,
        parent: tk.Widget,
        on_selection_change: Callable,
        on_selection_callbacks: Dict[str, Callable],
    ) -> None:
        """
        Construit le Notebook (onglets) et les cinq tableaux.

        Onglet 0 — "Données source"    : notices UNIMARC importées, sélectionnables.
        Onglet 1 — "Données préparées" : notices après préparation Koha.
        Onglet 2 — "Données OAI-PMH"   : notices Dublin Core collectées.
        Onglet 3 — "Croisement OAI"    : notices après croisement UNIMARC/OAI.
        Onglet 4 — "Enrichissement Sudoc" : notices après enrichissement Sudoc.

        Les onglets 1-4 affichent un placeholder jusqu'au premier chargement.
        """
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("App.TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure(
            "App.TNotebook.Tab",
            background=COLORS["sidebar"], foreground=COLORS["sidebar_text"],
            font=("Helvetica", 9, "bold"), padding=(12, 5),
        )
        style.map(
            "App.TNotebook.Tab",
            background=[("selected", COLORS["bg"])],
            foreground=[("selected", COLORS["text"])],
        )

        notebook_frame = tk.Frame(parent, bg=COLORS["bg"])
        notebook_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)

        tk.Label(
            notebook_frame, text="Données",
            bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=("Helvetica", 9, "bold"), anchor=tk.W,
        ).pack(side=tk.TOP, fill=tk.X, padx=2)

        self._notebook = ttk.Notebook(notebook_frame, style="App.TNotebook")
        self._notebook.pack(fill=tk.BOTH, expand=True)

        # ── Onglet 0 : Données source ──────────────────────────────────
        tab_source = tk.Frame(self._notebook, bg=COLORS["bg"])
        self._notebook.add(tab_source, text="  Données source  ")

        # Mini-barre de sélection — visible uniquement sur cet onglet
        sel_bar = tk.Frame(tab_source, bg=COLORS["bg"], pady=3, padx=4)
        sel_bar.pack(side=tk.TOP, fill=tk.X)

        self._btn_select_all = tk.Button(
            sel_bar,
            text="☑  Tout sélectionner",
            command=on_selection_callbacks.get("select_all", lambda: None),
            relief=tk.FLAT, cursor="hand2",
            font=("Helvetica", 8, "bold"),
            bg=COLORS["btn_primary"], fg=COLORS["sidebar_text"],
            activebackground=COLORS["accent"], activeforeground="#ffffff",
            padx=8, pady=2, state=tk.DISABLED,
        )
        self._btn_select_all.pack(side=tk.LEFT, padx=(0, 4))

        self._btn_deselect_all = tk.Button(
            sel_bar,
            text="☐  Tout désélectionner",
            command=on_selection_callbacks.get("deselect_all", lambda: None),
            relief=tk.FLAT, cursor="hand2",
            font=("Helvetica", 8, "bold"),
            bg=COLORS["btn_primary"], fg=COLORS["sidebar_text"],
            activebackground=COLORS["accent"], activeforeground="#ffffff",
            padx=8, pady=2, state=tk.DISABLED,
        )
        self._btn_deselect_all.pack(side=tk.LEFT)

        self._table_source = RecordsTable(
            tab_source, columns=SOURCE_COLUMNS,
            on_selection_change=on_selection_change,
            csv_filename="notices_source.csv",
        )
        self._table_source.pack(fill=tk.BOTH, expand=True)

        # ── Onglet 1 : Données préparées ───────────────────────────────
        tab_prepared = tk.Frame(self._notebook, bg=COLORS["bg"])
        self._notebook.add(tab_prepared, text="  Données préparées  ")

        self._prepared_placeholder = tk.Label(
            tab_prepared,
            text="Cliquez sur « ⚙  Préparer » pour afficher les notices traitées.",
            bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=("Helvetica", 10, "italic"),
        )
        self._prepared_placeholder.pack(expand=True)

        self._table_prepared = RecordsTable(
            tab_prepared, columns=PREPARED_COLUMNS, on_selection_change=None,
            csv_filename="notices_preparees.csv",
        )
        self._prepared_table_visible = False

        # ── Onglet 2 : Données OAI-PMH ─────────────────────────────────
        tab_oai = tk.Frame(self._notebook, bg=COLORS["bg"])
        self._notebook.add(tab_oai, text="  Données OAI-PMH  ")

        self._oai_placeholder = tk.Label(
            tab_oai,
            text="Cliquez sur « 🌐  Récupérer OAI-PMH » pour afficher les notices Dublin Core.",
            bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=("Helvetica", 10, "italic"),
        )
        self._oai_placeholder.pack(expand=True)

        self._table_oai = OaiTable(tab_oai, csv_filename="notices_oai.csv")
        self._oai_table_visible = False

        # ── Onglet 3 : Données croisées UNIMARC/OAI ────────────────────
        tab_crossed = tk.Frame(self._notebook, bg=COLORS["bg"])
        self._notebook.add(tab_crossed, text="  Croisement OAI  ")

        self._crossed_placeholder = tk.Label(
            tab_crossed,
            text="Cliquez sur « 🔗  Croiser UNIMARC / OAI » pour afficher les notices après croisement.",
            bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=("Helvetica", 10, "italic"),
        )
        self._crossed_placeholder.pack(expand=True)

        self._table_crossed = RecordsTable(
            tab_crossed, columns=PREPARED_COLUMNS, on_selection_change=None,
            csv_filename="notices_croisees.csv",
        )
        self._crossed_table_visible = False

        # ── Onglet 4 : Données enrichies Sudoc ─────────────────────────
        tab_sudoc = tk.Frame(self._notebook, bg=COLORS["bg"])
        self._notebook.add(tab_sudoc, text="  Enrichissement Sudoc  ")

        self._sudoc_placeholder = tk.Label(
            tab_sudoc,
            text="Cliquez sur « 📚  Enrichissement Sudoc » pour afficher les notices après enrichissement.",
            bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=("Helvetica", 10, "italic"),
        )
        self._sudoc_placeholder.pack(expand=True)

        self._table_sudoc = RecordsTable(
            tab_sudoc, columns=PREPARED_COLUMNS, on_selection_change=None,
            csv_filename="notices_sudoc.csv",
        )
        self._sudoc_table_visible = False

        # Onglet source sélectionné par défaut
        self._notebook.select(0)

    def _set_initial_focus(self) -> None:
        """Met le focus sur le bouton Importer au démarrage."""
        btn = self._toolbar.get_button("import")
        if btn:
            btn.focus_set()

    # ------------------------------------------------------------------
    # API publique — appelée par app.py
    # ------------------------------------------------------------------

    # ── Onglet source ──────────────────────────────────────────────────

    def load_records(self, records: List[MarcRecord]) -> None:
        """
        Charge les notices brutes dans l'onglet source et remet à zéro
        tous les compteurs liés aux étapes suivantes (préparation, OAI,
        croisement, Sudoc), puisqu'un nouvel import invalide ces données.
        """
        self._table_source.load_records(records)
        self.update_stat("total",    len(records))
        self.update_stat("selected", 0)
        self.update_stat("prepared", 0)
        self.update_stat("oai",      0)
        self.update_stat("matched",  0)
        self.update_stat("sudoc",    0)
        self.set_state("Chargé", color=COLORS["info"])
        self._notebook.select(0)

    def get_selected_indices(self) -> List[int]:
        """Retourne les indices des notices cochées dans l'onglet source."""
        return self._table_source.get_selected_indices()

    def select_all(self) -> None:
        self._table_source.select_all()

    def deselect_all(self) -> None:
        self._table_source.deselect_all()

    # ── Onglet préparé ─────────────────────────────────────────────────

    def load_prepared_records(self, records: List[MarcRecord]) -> None:
        """
        Charge les notices préparées dans l'onglet dédié et bascule dessus.

        Lors du premier appel, le placeholder est remplacé par le tableau.
        """
        if not self._prepared_table_visible:
            self._prepared_placeholder.pack_forget()
            self._table_prepared.pack(fill=tk.BOTH, expand=True)
            self._prepared_table_visible = True

        self._table_prepared.load_records(records)
        self._notebook.select(1)

    def reset_prepared_tab(self) -> None:
        """Vide l'onglet préparé et réaffiche le placeholder."""
        if self._prepared_table_visible:
            self._table_prepared.pack_forget()
            self._prepared_placeholder.pack(expand=True)
            self._prepared_table_visible = False
        self._table_prepared.load_records([])

    # ── Onglet OAI ─────────────────────────────────────────────────────

    def load_oai_records(self, records: List[OaiRecord]) -> None:
        """
        Charge les notices OAI dans l'onglet dédié et bascule dessus.
        Lors du premier appel, le placeholder est remplacé par le tableau.
        """
        if not self._oai_table_visible:
            self._oai_placeholder.pack_forget()
            self._table_oai.pack(fill=tk.BOTH, expand=True)
            self._oai_table_visible = True

        self._table_oai.load_records(records)
        self.update_stat("oai", len(records))
        self._notebook.select(2)

    def reset_oai_tab(self) -> None:
        """Vide l'onglet OAI et réaffiche le placeholder."""
        if self._oai_table_visible:
            self._table_oai.pack_forget()
            self._oai_placeholder.pack(expand=True)
            self._oai_table_visible = False
        self._table_oai.load_records([])
        self.update_stat("oai", 0)

    # ── Onglet croisement OAI ──────────────────────────────────────────

    def load_oai_enriched_records(self, records: List[MarcRecord]) -> None:
        """
        Charge les notices après croisement UNIMARC/OAI dans l'onglet dédié.
        Bascule sur cet onglet au premier appel.
        """
        if not self._crossed_table_visible:
            self._crossed_placeholder.pack_forget()
            self._table_crossed.pack(fill=tk.BOTH, expand=True)
            self._crossed_table_visible = True
        self._table_crossed.load_records(records)
        self._notebook.select(3)

    def reset_oai_enriched_tab(self) -> None:
        """Vide l'onglet croisement et réaffiche le placeholder."""
        if self._crossed_table_visible:
            self._table_crossed.pack_forget()
            self._crossed_placeholder.pack(expand=True)
            self._crossed_table_visible = False
        self._table_crossed.load_records([])

    # ── Onglet enrichissement Sudoc ────────────────────────────────────

    def load_sudoc_records(self, records: List[MarcRecord]) -> None:
        """
        Charge les notices après enrichissement Sudoc dans l'onglet dédié.
        Bascule sur cet onglet au premier appel.
        """
        if not self._sudoc_table_visible:
            self._sudoc_placeholder.pack_forget()
            self._table_sudoc.pack(fill=tk.BOTH, expand=True)
            self._sudoc_table_visible = True
        self._table_sudoc.load_records(records)
        self._notebook.select(4)

    def reset_sudoc_tab(self) -> None:
        """Vide l'onglet Sudoc et réaffiche le placeholder."""
        if self._sudoc_table_visible:
            self._table_sudoc.pack_forget()
            self._sudoc_placeholder.pack(expand=True)
            self._sudoc_table_visible = False
        self._table_sudoc.load_records([])

    # ── Statistiques et état ───────────────────────────────────────────

    def update_stat(self, key: str, value: int) -> None:
        """Met à jour un compteur du panneau de statistiques."""
        if key in self._stat_vars:
            self._stat_vars[key].set(str(value))

    def set_state(self, text: str, color: Optional[str] = None) -> None:
        """Met à jour le libellé d'état dans le panneau de statistiques."""
        self._state_var.set(text)
        if color:
            self._state_label.config(fg=color)

    # ── StatusBar ──────────────────────────────────────────────────────

    def set_status(self, text: str, level: str = "info") -> None:
        self._statusbar.set_message(text, level)

    def set_counts(self, total: int, selected: int) -> None:
        self._statusbar.set_counts(total, selected)

    # ── Toolbar ────────────────────────────────────────────────────────

    def set_button_enabled(self, key: str, enabled: bool) -> None:
        """Active ou désactive un bouton — toolbar ou mini-barre de sélection."""
        state = tk.NORMAL if enabled else tk.DISABLED
        if key == "select_all" and hasattr(self, "_btn_select_all"):
            self._btn_select_all.config(state=state)
        elif key == "deselect_all" and hasattr(self, "_btn_deselect_all"):
            self._btn_deselect_all.config(state=state)
        else:
            self._toolbar.set_enabled(key, enabled)
