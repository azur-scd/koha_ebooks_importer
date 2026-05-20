#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/records_table.py — Tableau des notices MARC avec sélection, tri, filtre et export CSV
=========================================================================================
Composant Tkinter affichant des notices dans un Treeview multi-colonnes.

Fonctionnalités :
  - Sélection par case à cocher (clic ou barre espace)
  - Coloration alternée (zèbre)
  - Tri par colonne au clic sur l'en-tête (bascule croissant/décroissant)
  - Filtres de recherche par colonne (champs de saisie sous les en-têtes)
  - Défilement vertical et horizontal
  - Bouton "Exporter CSV" dans la barre inférieure
  - Colonnes configurables via un paramètre `columns`
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from config import COLORS, SOURCE_COLUMNS
from marc.reader import MarcRecord
from ui.csv_export import export_treeview_to_csv


class RecordsTable(tk.Frame):
    """
    Tableau de prévisualisation des notices MARC.

    Args:
        parent              : Widget parent Tkinter.
        columns             : Jeu de colonnes à afficher (défaut : SOURCE_COLUMNS).
        on_selection_change : Callback sélection (None = tableau lecture seule).
        csv_filename        : Nom de fichier CSV proposé par défaut.
    """

    _COL_CHECK = "#check"

    def __init__(
        self,
        parent: tk.Widget,
        columns: Optional[List[Dict]] = None,
        on_selection_change: Optional[Callable[[List[int]], None]] = None,
        csv_filename: str = "notices.csv",
        **kwargs,
    ):
        super().__init__(parent, bg=COLORS["bg"], **kwargs)
        self._columns      = columns if columns is not None else SOURCE_COLUMNS
        self._on_sel_cb    = on_selection_change
        self._selectable   = on_selection_change is not None
        self._csv_filename = csv_filename
        self._records:  List[MarcRecord] = []
        self._checked:  List[bool]       = []
        self._filtered: List[int]        = []   # indices dans self._records visibles
        self._sort_col: Optional[str]    = None
        self._sort_asc: bool             = True

        self._build_ui()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """
        Construit le tableau :
          - Barre de filtre (champs de saisie sous chaque colonne)
          - Treeview avec scrollbars V et H
          - Barre inférieure avec bouton CSV
        """
        col_ids = (
            [self._COL_CHECK] if self._selectable else []
        ) + [f"col_{i}" for i in range(len(self._columns))]

        # ── Initialisation des attributs référencés par les callbacks ──
        # _count_label doit exister avant que les StringVar déclenchent
        # _apply_filters via trace_add. Il sera reconfiguré dans bottom_bar.
        self._count_label = None  # initialisé avant trace_add, redéfini plus bas

        # ── Style Treeview ────────────────────────────────────────────
        style = ttk.Style()
        style.configure(
            "Records.Treeview",
            background=COLORS["table_odd"],
            fieldbackground=COLORS["table_odd"],
            foreground=COLORS["text"],
            rowheight=24,
            font=("Helvetica", 9),
        )
        style.configure(
            "Records.Treeview.Heading",
            background=COLORS["sidebar"],
            foreground=COLORS["sidebar_text"],
            font=("Helvetica", 9, "bold"),
            relief="flat",
        )
        style.map(
            "Records.Treeview",
            background=[("selected", COLORS["table_sel"])],
            foreground=[("selected", COLORS["text"])],
        )

        # ── Treeview ──────────────────────────────────────────────────
        self._tree = ttk.Treeview(
            self,
            columns=col_ids,
            show="headings",
            selectmode="browse",
            style="Records.Treeview",
        )

        if self._selectable:
            self._tree.heading(self._COL_CHECK, text="✓", anchor=tk.CENTER)
            self._tree.column(self._COL_CHECK, width=30, minwidth=30,
                              stretch=False, anchor=tk.CENTER)

        for i, col in enumerate(self._columns):
            col_id = f"col_{i}"
            self._tree.heading(
                col_id, text=col["label"], anchor=tk.W,
                command=lambda c=col_id, idx=i: self._sort_by_column(c, idx),
            )
            self._tree.column(
                col_id,
                width=col.get("width", 120),
                minwidth=60,
                stretch=col.get("stretch", False),
                anchor=tk.W,
            )

        vsb = ttk.Scrollbar(self, orient=tk.VERTICAL,   command=self._tree.yview)
        hsb = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # ── Barre de filtres ──────────────────────────────────────────
        # Placée dans un canvas avec scrollbar horizontale synchronisée
        # pour que les champs restent alignés avec les colonnes.
        filter_bar = tk.Frame(self, bg="#d8d4c8")
        filter_bar.grid(row=0, column=0, sticky="ew")

        if self._selectable:
            tk.Label(filter_bar, width=3, bg="#d8d4c8").pack(side=tk.LEFT)

        self._filter_vars: List[tk.StringVar] = []
        for i, col in enumerate(self._columns):
            var = tk.StringVar()
            var.trace_add("write", lambda *_, idx=i: self._apply_filters())
            self._filter_vars.append(var)
            entry = tk.Entry(
                filter_bar,
                textvariable=var,
                font=("Helvetica", 8),
                bg="#ccc8ba",
                fg=COLORS["text"],
                insertbackground=COLORS["text"],
                relief=tk.FLAT,
                width=max(6, col.get("width", 120) // 8),
            )
            entry.pack(side=tk.LEFT, padx=1, pady=1)
            # Placeholder "..."
            entry.insert(0, "...")
            entry.config(fg=COLORS["text_muted"])
            def _on_focus_in(e, v=var, en=entry):
                if en.get() == "...":
                    en.delete(0, tk.END)
                    en.config(fg=COLORS["text"])
            def _on_focus_out(e, v=var, en=entry):
                if not en.get():
                    en.insert(0, "...")
                    en.config(fg=COLORS["text_muted"])
                    v.set("")
            entry.bind("<FocusIn>",  _on_focus_in)
            entry.bind("<FocusOut>", _on_focus_out)

        # ── Barre inférieure CSV ──────────────────────────────────────
        bottom_bar = tk.Frame(self, bg=COLORS["bg"], pady=3)

        tk.Button(
            bottom_bar,
            text="Exporter CSV",
            command=self._export_csv,
            relief=tk.FLAT,
            bg=COLORS["sidebar"],
            fg=COLORS["sidebar_text"],
            font=("Helvetica", 8, "bold"),
            padx=8, pady=2,
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

        tk.Button(
            bottom_bar,
            text="Effacer filtres",
            command=self._clear_filters,
            relief=tk.FLAT,
            bg=COLORS["sidebar"],
            fg=COLORS["sidebar_text"],
            font=("Helvetica", 8),
            padx=8, pady=2,
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=2)

        self._count_label = tk.Label(
            bottom_bar, text="",
            bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=("Helvetica", 8),
        )
        self._count_label.pack(side=tk.LEFT, padx=6)

        # ── Disposition grid ──────────────────────────────────────────
        filter_bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")
        bottom_bar.grid(row=3, column=0, columnspan=2, sticky="ew")

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        if self._selectable:
            self._tree.bind("<ButtonRelease-1>", self._on_click)
            self._tree.bind("<space>",           self._on_space)

    # ------------------------------------------------------------------
    # Données
    # ------------------------------------------------------------------

    def load_records(self, records: List[MarcRecord]) -> None:
        """
        Charge une nouvelle liste de notices.
        Efface les filtres, les données précédentes et réinitialise les cases.
        """
        self._records  = records
        self._checked  = [False] * len(records)
        self._filtered = list(range(len(records)))
        self._clear_filters(refresh=False)
        self._refresh_tree()

    def _extract_values(self, record: MarcRecord) -> List[str]:
        """Extrait les valeurs de toutes les colonnes pour une notice."""
        values = []
        if self._selectable:
            values.append("")   # placeholder pour la case à cocher
        for col in self._columns:
            try:
                values.append(col["extract"](record))
            except Exception:
                values.append("")
        return values

    def _refresh_tree(self) -> None:
        """
        Reconstruit le Treeview depuis self._filtered (indices visibles).
        Applique la coloration zèbre et les cases à cocher.
        """
        self._tree.delete(*self._tree.get_children())

        for row_num, orig_idx in enumerate(self._filtered):
            record = self._records[orig_idx]
            values = self._extract_values(record)
            if self._selectable:
                values[0] = self._check_symbol(orig_idx)

            tag_row = "even" if row_num % 2 == 0 else "odd"
            self._tree.insert("", tk.END, iid=str(orig_idx),
                              values=values, tags=(tag_row,))

        self._tree.tag_configure("even", background=COLORS["table_even"])
        self._tree.tag_configure("odd",  background=COLORS["table_odd"])
        self._update_count_label()

    def _update_count_label(self) -> None:
        if self._count_label is None:
            return
        total   = len(self._records)
        visible = len(self._filtered)
        if visible < total:
            self._count_label.config(text=f"{visible} / {total} notice(s)")
        else:
            self._count_label.config(text=f"{total} notice(s)")

    # ------------------------------------------------------------------
    # Filtres
    # ------------------------------------------------------------------

    def _apply_filters(self) -> None:
        """
        Refiltre self._records selon les valeurs saisies dans la barre de filtres.
        La recherche est insensible à la casse et partielle (sous-chaîne).
        """
        # Ignorer "..." (placeholder) comme terme de filtre
        terms = [v.get().strip().lower() for v in self._filter_vars]
        terms = [t if t != "..." else "" for t in terms]
        has_filter = any(terms)

        if not has_filter:
            self._filtered = list(range(len(self._records)))
        else:
            self._filtered = []
            for i, record in enumerate(self._records):
                values = self._extract_values(record)
                # Décaler si colonne check présente
                data_offset = 1 if self._selectable else 0
                match = all(
                    not term or term in values[data_offset + j].lower()
                    for j, term in enumerate(terms)
                )
                if match:
                    self._filtered.append(i)

        self._refresh_tree()

    def _clear_filters(self, refresh: bool = True) -> None:
        """Efface tous les filtres et remet le placeholder '...'."""
        for var in self._filter_vars:
            var.set("")
        # Remettre le placeholder visuellement dans les Entry
        for widget in self.winfo_children():
            if isinstance(widget, tk.Frame):
                for child in widget.winfo_children():
                    if isinstance(child, tk.Entry):
                        if not child.get():
                            child.insert(0, "...")
                            child.config(fg=COLORS["text_muted"])
        if refresh:
            self._filtered = list(range(len(self._records)))
            self._refresh_tree()

    # ------------------------------------------------------------------
    # Tri
    # ------------------------------------------------------------------

    def _sort_by_column(self, col_id: str, col_idx: int) -> None:
        """
        Trie les notices visibles (self._filtered) par la colonne cliquée.
        Bascule croissant/décroissant à chaque clic.
        Met à jour le symbole ▲/▼ dans l'en-tête.
        """
        if self._sort_col == col_id:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col_id
            self._sort_asc = True

        extract = self._columns[col_idx]["extract"]

        def sort_key(orig_idx: int) -> str:
            try:
                return extract(self._records[orig_idx]).lower()
            except Exception:
                return ""

        self._filtered.sort(key=sort_key, reverse=not self._sort_asc)

        # Mettre à jour les en-têtes
        for i, col in enumerate(self._columns):
            cid   = f"col_{i}"
            label = col["label"]
            if cid == col_id:
                arrow = " ▲" if self._sort_asc else " ▼"
                self._tree.heading(cid, text=label + arrow)
            else:
                self._tree.heading(cid, text=label)

        self._refresh_tree()

    # ------------------------------------------------------------------
    # Sélection
    # ------------------------------------------------------------------

    def _check_symbol(self, index: int) -> str:
        return "☑" if self._checked[index] else "☐"

    def select_all(self) -> None:
        self._checked = [True] * len(self._records)
        self._update_check_column()
        self._notify()

    def deselect_all(self) -> None:
        self._checked = [False] * len(self._records)
        self._update_check_column()
        self._notify()

    def get_selected_indices(self) -> List[int]:
        return [i for i, c in enumerate(self._checked) if c]

    def _toggle_check(self, index: int) -> None:
        if 0 <= index < len(self._checked):
            self._checked[index] = not self._checked[index]
            iid = str(index)
            if self._tree.exists(iid):
                current    = list(self._tree.item(iid, "values"))
                current[0] = self._check_symbol(index)
                self._tree.item(iid, values=current)
            self._notify()

    def _update_check_column(self) -> None:
        for orig_idx in self._filtered:
            iid = str(orig_idx)
            if self._tree.exists(iid):
                current    = list(self._tree.item(iid, "values"))
                current[0] = self._check_symbol(orig_idx)
                self._tree.item(iid, values=current)

    def _notify(self) -> None:
        if self._on_sel_cb:
            self._on_sel_cb(self.get_selected_indices())

    # ------------------------------------------------------------------
    # Événements
    # ------------------------------------------------------------------

    def _on_click(self, event: tk.Event) -> None:
        region = self._tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self._tree.identify_column(event.x)
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        if col == "#1":
            self._toggle_check(int(iid))

    def _on_space(self, _event: tk.Event) -> None:
        iid = self._tree.focus()
        if iid:
            self._toggle_check(int(iid))

    # ------------------------------------------------------------------
    # Export CSV
    # ------------------------------------------------------------------

    def _export_csv(self) -> None:
        """Exporte le contenu visible (après filtrage, dans l'ordre de tri) en CSV."""
        labels  = [col["label"] for col in self._columns]
        col_ids = [f"col_{i}" for i in range(len(self._columns))]
        # Décaler si colonne check
        if self._selectable:
            col_ids_tv = [f"col_{i}" for i in range(len(self._columns))]
        else:
            col_ids_tv = col_ids
        export_treeview_to_csv(
            tree          = self._tree,
            column_labels = labels,
            column_ids    = col_ids_tv,
            default_name  = self._csv_filename,
        )
