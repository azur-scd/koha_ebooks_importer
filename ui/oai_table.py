#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/oai_table.py — Tableau des notices OAI-PMH (Dublin Core) avec tri, filtre et export CSV
============================================================================================
Composant Tkinter affichant des OaiRecord dans un Treeview.
Lecture seule (pas de sélection), avec tri par colonne, filtres de recherche
et export CSV.
"""

import tkinter as tk
from tkinter import ttk
from typing import List, Optional

from config import COLORS, OAI_COLUMNS
from oai.harvester import OaiRecord
from ui.csv_export import export_treeview_to_csv


class OaiTable(tk.Frame):
    """
    Tableau de prévisualisation des notices Dublin Core OAI-PMH.
    Lecture seule, avec tri, filtres par colonne et export CSV.

    Args:
        parent       : Widget parent Tkinter.
        csv_filename : Nom de fichier CSV proposé par défaut.
    """

    def __init__(self, parent: tk.Widget, csv_filename: str = "notices_oai.csv", **kwargs):
        super().__init__(parent, bg=COLORS["bg"], **kwargs)
        self._records:  List[OaiRecord] = []
        self._filtered: List[int]       = []
        self._csv_filename = csv_filename
        self._sort_col: Optional[str]   = None
        self._sort_asc: bool            = True
        self._build_ui()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        col_ids = [f"col_{i}" for i in range(len(OAI_COLUMNS))]

        style = ttk.Style()
        style.configure(
            "Oai.Treeview",
            background=COLORS["table_odd"],
            fieldbackground=COLORS["table_odd"],
            foreground=COLORS["text"],
            rowheight=24,
            font=("Helvetica", 9),
        )
        style.configure(
            "Oai.Treeview.Heading",
            background=COLORS["sidebar"],
            foreground=COLORS["sidebar_text"],
            font=("Helvetica", 9, "bold"),
            relief="flat",
        )
        style.map(
            "Oai.Treeview",
            background=[("selected", COLORS["table_sel"])],
            foreground=[("selected", COLORS["text"])],
        )

        self._tree = ttk.Treeview(
            self,
            columns=col_ids,
            show="headings",
            selectmode="browse",
            style="Oai.Treeview",
        )

        for i, col in enumerate(OAI_COLUMNS):
            col_id = f"col_{i}"
            self._tree.heading(
                col_id, text=col["label"], anchor=tk.W,
                command=lambda c=col_id, idx=i: self._sort_by_column(c, idx),
            )
            self._tree.column(
                col_id,
                width=col.get("width", 120),
                minwidth=50,
                stretch=col.get("stretch", False),
                anchor=tk.W,
            )

        vsb = ttk.Scrollbar(self, orient=tk.VERTICAL,   command=self._tree.yview)
        hsb = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # ── Barre de filtres ──────────────────────────────────────────
        filter_bar = tk.Frame(self, bg=COLORS["bg"])
        self._filter_vars: List[tk.StringVar] = []
        for i, col in enumerate(OAI_COLUMNS):
            var = tk.StringVar()
            var.trace_add("write", lambda *_, idx=i: self._apply_filters())
            self._filter_vars.append(var)
            tk.Entry(
                filter_bar,
                textvariable=var,
                font=("Helvetica", 8),
                bg="#f0f0e8",
                fg=COLORS["text"],
                relief=tk.FLAT,
                width=max(6, col.get("width", 120) // 8),
            ).pack(side=tk.LEFT, padx=1, pady=1)

        # ── Barre inférieure ──────────────────────────────────────────
        bottom_bar = tk.Frame(self, bg=COLORS["bg"], pady=3)

        tk.Button(
            bottom_bar,
            text="Exporter CSV",
            command=self._export_csv,
            relief=tk.FLAT,
            bg=COLORS["sidebar"],
            fg=COLORS["sidebar_text"],
            font=("Helvetica", 8, "bold"),
            padx=8, pady=2, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

        tk.Button(
            bottom_bar,
            text="Effacer filtres",
            command=self._clear_filters,
            relief=tk.FLAT,
            bg=COLORS["sidebar"],
            fg=COLORS["sidebar_text"],
            font=("Helvetica", 8),
            padx=8, pady=2, cursor="hand2",
        ).pack(side=tk.RIGHT, padx=2)

        self._count_label = tk.Label(
            bottom_bar, text="",
            bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=("Helvetica", 8),
        )
        self._count_label.pack(side=tk.LEFT, padx=6)

        # ── Disposition grid ──────────────────────────────────────────
        filter_bar.grid(row=0, column=0, sticky="ew")
        self._tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")
        bottom_bar.grid(row=3, column=0, columnspan=2, sticky="ew")

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

    # ------------------------------------------------------------------
    # Données
    # ------------------------------------------------------------------

    def load_records(self, records: List[OaiRecord]) -> None:
        """Charge une liste de notices OAI. Efface filtres et données précédentes."""
        self._records  = records
        self._filtered = list(range(len(records)))
        self._clear_filters(refresh=False)
        self._refresh_tree()

    def _extract_values(self, record: OaiRecord) -> List[str]:
        values = []
        for col in OAI_COLUMNS:
            try:
                values.append(col["extract"](record))
            except Exception:
                values.append("")
        return values

    def _refresh_tree(self) -> None:
        self._tree.delete(*self._tree.get_children())
        for row_num, orig_idx in enumerate(self._filtered):
            record  = self._records[orig_idx]
            values  = self._extract_values(record)
            tag_row = "even" if row_num % 2 == 0 else "odd"
            self._tree.insert("", tk.END, iid=str(orig_idx),
                              values=values, tags=(tag_row,))
        self._tree.tag_configure("even", background=COLORS["table_even"])
        self._tree.tag_configure("odd",  background=COLORS["table_odd"])
        total   = len(self._records)
        visible = len(self._filtered)
        self._count_label.config(
            text=f"{visible} / {total} notice(s)" if visible < total else f"{total} notice(s)"
        )

    # ------------------------------------------------------------------
    # Filtres
    # ------------------------------------------------------------------

    def _apply_filters(self) -> None:
        """Refiltre selon les saisies (insensible à la casse, sous-chaîne)."""
        terms = [v.get().strip().lower() for v in self._filter_vars]
        if not any(terms):
            self._filtered = list(range(len(self._records)))
        else:
            self._filtered = []
            for i, record in enumerate(self._records):
                values = self._extract_values(record)
                if all(not t or t in values[j].lower() for j, t in enumerate(terms)):
                    self._filtered.append(i)
        self._refresh_tree()

    def _clear_filters(self, refresh: bool = True) -> None:
        for var in self._filter_vars:
            var.set("")
        if refresh:
            self._filtered = list(range(len(self._records)))
            self._refresh_tree()

    # ------------------------------------------------------------------
    # Tri
    # ------------------------------------------------------------------

    def _sort_by_column(self, col_id: str, col_idx: int) -> None:
        """Trie les notices visibles par la colonne cliquée (▲/▼)."""
        if self._sort_col == col_id:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col_id
            self._sort_asc = True

        extract = OAI_COLUMNS[col_idx]["extract"]

        def sort_key(orig_idx: int) -> str:
            try:
                return extract(self._records[orig_idx]).lower()
            except Exception:
                return ""

        self._filtered.sort(key=sort_key, reverse=not self._sort_asc)

        for i, col in enumerate(OAI_COLUMNS):
            cid   = f"col_{i}"
            label = col["label"]
            arrow = (" ▲" if self._sort_asc else " ▼") if cid == col_id else ""
            self._tree.heading(cid, text=label + arrow)

        self._refresh_tree()

    # ------------------------------------------------------------------
    # Export CSV
    # ------------------------------------------------------------------

    def _export_csv(self) -> None:
        """Exporte le contenu visible (après filtrage, dans l'ordre de tri) en CSV."""
        labels  = [col["label"] for col in OAI_COLUMNS]
        col_ids = [f"col_{i}" for i in range(len(OAI_COLUMNS))]
        export_treeview_to_csv(
            tree=self._tree, column_labels=labels,
            column_ids=col_ids, default_name=self._csv_filename,
        )
