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

Les filtres sont des Entry placées dans un Canvas superposé juste sous les
en-têtes du Treeview. La synchronisation position/largeur se fait en lisant
les coordonnées réelles des colonnes (tree.bbox ou tree.column("width"))
après chaque redimensionnement, ce qui garantit un alignement pixel-perfect
même quand l'utilisateur redimensionne une colonne à la souris.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from config import COLORS, SOURCE_COLUMNS
from marc.reader import MarcRecord
from ui.csv_export import export_treeview_to_csv

_FILTER_H   = 22   # Hauteur fixe de la barre de filtres en pixels
_FILTER_BG  = "#b8b4a8"
_ENTRY_BG   = "#ccc8ba"
_PLACEHOLDER = "filtre"




class RecordsTable(tk.Frame):
    """
    Tableau MARC avec sélection, tri par colonne, filtres alignés pixel-perfect
    et export CSV.

    Architecture des filtres :
      Un Canvas de hauteur fixe (_FILTER_H) est placé entre les en-têtes du
      Treeview et ses données. Les Entry de filtre y sont positionnées via
      canvas.place(x=..., width=...) en lisant les largeurs réelles des colonnes
      du Treeview. La synchronisation se déclenche sur :
        - <Configure>        : redimensionnement global du widget
        - <ButtonRelease-1>  : relâchement après drag de séparateur de colonne
      Les deux événements sont liés au Treeview et appellent
      _sync_filter_positions() via after(10) pour laisser Tk finaliser le rendu.
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
        self._filtered: List[int]        = []
        self._sort_col: Optional[str]    = None
        self._sort_asc: bool             = True
        self._count_label: Optional[tk.Label] = None
        self._filter_vars:    List[tk.StringVar] = []
        self._filter_entries: List[tk.Entry]     = []

        self._build_ui()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        col_ids = (
            [self._COL_CHECK] if self._selectable else []
        ) + [f"col_{i}" for i in range(len(self._columns))]

        # ── Style ─────────────────────────────────────────────────────
        style = ttk.Style()
        style.configure("Records.Treeview",
            background=COLORS["table_odd"], fieldbackground=COLORS["table_odd"],
            foreground=COLORS["text"], rowheight=24, font=("Helvetica", 9))
        style.configure("Records.Treeview.Heading",
            background=COLORS["sidebar"], foreground=COLORS["sidebar_text"],
            font=("Helvetica", 9, "bold"), relief="flat")
        style.map("Records.Treeview",
            background=[("selected", COLORS["table_sel"])],
            foreground=[("selected", COLORS["text"])])

        # ── Treeview ──────────────────────────────────────────────────
        self._tree = ttk.Treeview(self, columns=col_ids, show="headings",
                                   selectmode="browse", style="Records.Treeview")

        if self._selectable:
            self._tree.heading(self._COL_CHECK, text="✓", anchor=tk.CENTER)
            self._tree.column(self._COL_CHECK, width=30, minwidth=30,
                              stretch=False, anchor=tk.CENTER)

        for i, col in enumerate(self._columns):
            cid = f"col_{i}"
            self._tree.heading(cid, text=col["label"], anchor=tk.W,
                               command=lambda c=cid, idx=i: self._sort_by_column(c, idx))
            self._tree.column(cid, width=col.get("width", 120), minwidth=60,
                              stretch=col.get("stretch", False), anchor=tk.W)

        vsb = ttk.Scrollbar(self, orient=tk.VERTICAL,   command=self._tree.yview)
        hsb = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # ── Canvas des filtres ────────────────────────────────────────
        # Placé entre les en-têtes (gérés par le Treeview) et les données.
        # height fixe = _FILTER_H px ; les Entry y sont positionnées en absolu.
        self._filter_canvas = tk.Canvas(
            self, height=_FILTER_H, bg=_FILTER_BG,
            highlightthickness=0, bd=0,
        )

        # Créer une Entry par colonne de données (pas pour la colonne check)
        for i, col in enumerate(self._columns):
            var = tk.StringVar()
            # trace_add déclenche _apply_filters à chaque frappe
            var.trace_add("write", lambda *_, idx=i: self._apply_filters())
            self._filter_vars.append(var)

            entry = tk.Entry(
                self._filter_canvas,
                textvariable=var,
                font=("Helvetica", 8),
                bg=_ENTRY_BG,
                fg=COLORS["text_muted"],
                insertbackground=COLORS["text"],
                relief=tk.FLAT,
                bd=1,
            )
            # Placeholder comportement
            entry.insert(0, _PLACEHOLDER)

            def _focus_in(e, en=entry, v=var):
                if en.get() == _PLACEHOLDER:
                    en.delete(0, tk.END)
                    en.config(fg=COLORS["text"])

            def _focus_out(e, en=entry, v=var):
                if not en.get().strip():
                    en.delete(0, tk.END)
                    en.insert(0, _PLACEHOLDER)
                    en.config(fg=COLORS["text_muted"])
                    v.set("")

            entry.bind("<FocusIn>",  _focus_in)
            entry.bind("<FocusOut>", _focus_out)
            self._filter_entries.append(entry)

        # ── Barre inférieure ──────────────────────────────────────────
        bottom_bar = tk.Frame(self, bg=COLORS["bg"], pady=3)

        tk.Button(bottom_bar, text="Exporter CSV", command=self._export_csv,
                  relief=tk.FLAT, bg=COLORS["sidebar"], fg=COLORS["sidebar_text"],
                  font=("Helvetica", 8, "bold"), padx=8, pady=2,
                  cursor="hand2").pack(side=tk.RIGHT, padx=4)

        tk.Button(bottom_bar, text="Effacer filtres", command=self._clear_filters,
                  relief=tk.FLAT, bg=COLORS["sidebar"], fg=COLORS["sidebar_text"],
                  font=("Helvetica", 8), padx=8, pady=2,
                  cursor="hand2").pack(side=tk.RIGHT, padx=2)

        self._count_label = tk.Label(bottom_bar, text="",
            bg=COLORS["bg"], fg=COLORS["text_muted"], font=("Helvetica", 8))
        self._count_label.pack(side=tk.LEFT, padx=6)

        # ── Disposition grid ──────────────────────────────────────────
        # row 0 : Treeview (avec ses en-têtes intégrés)
        # row 1 : Canvas des filtres
        # row 2 : scrollbar horizontale
        # row 3 : barre inférieure
        self._tree.grid(         row=0, column=0, sticky="nsew")
        vsb.grid(                row=0, column=1, sticky="ns")
        self._filter_canvas.grid(row=1, column=0, sticky="ew")
        hsb.grid(                row=2, column=0, sticky="ew")
        bottom_bar.grid(         row=3, column=0, columnspan=2, sticky="ew")

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── Synchronisation position/largeur des Entry ────────────────
        # On programme la première synchro après que Tk a rendu le widget.
        self._tree.bind("<Configure>",       self._schedule_sync)
        self._tree.bind("<ButtonRelease-1>", self._schedule_sync)
        self.after(100, self._sync_filter_positions)

        if self._selectable:
            self._tree.bind("<ButtonRelease-1>", self._on_click_and_sync)
            self._tree.bind("<space>", self._on_space)

    def _on_click_and_sync(self, event: tk.Event) -> None:
        """Gère le clic (sélection) ET synchronise les filtres."""
        self._on_click(event)
        self._schedule_sync(event)

    def _schedule_sync(self, event=None) -> None:
        """Programme _sync_filter_positions dans 10 ms (laisse Tk finir le rendu)."""
        self.after(10, self._sync_filter_positions)

    def _sync_filter_positions(self) -> None:
        """
        Relit les largeurs réelles des colonnes du Treeview et repositionne
        chaque Entry de filtre via canvas.place(x=..., width=...).

        Méthode :
          tree.column(col_id, "width") retourne la largeur en pixels de
          chaque colonne. On cumule pour obtenir le x de chaque Entry.
          On soustrait le décalage dû au scroll horizontal courant.
        """
        # Offset horizontal courant (scroll)
        try:
            xview_start = self._tree.xview()[0]
            total_width = sum(
                self._tree.column(f"col_{i}", "width")
                for i in range(len(self._columns))
            )
            if self._selectable:
                total_width += self._tree.column(self._COL_CHECK, "width")
            x_offset = int(xview_start * total_width)
        except Exception:
            x_offset = 0

        # Largeur de la colonne check (si présente)
        check_w = 0
        if self._selectable:
            try:
                check_w = self._tree.column(self._COL_CHECK, "width")
            except Exception:
                check_w = 30

        x = check_w - x_offset
        canvas_h = _FILTER_H

        for i, entry in enumerate(self._filter_entries):
            try:
                col_w = self._tree.column(f"col_{i}", "width")
            except Exception:
                col_w = 120

            if x + col_w > 0:   # visible
                entry.place(x=x + 1, y=1, width=col_w - 2, height=canvas_h - 2)
            else:
                entry.place_forget()

            x += col_w

    # ------------------------------------------------------------------
    # Données
    # ------------------------------------------------------------------

    def load_records(self, records: List[MarcRecord]) -> None:
        self._records  = records
        self._checked  = [False] * len(records)
        self._filtered = list(range(len(records)))
        self._clear_filters(refresh=False)
        self._refresh_tree()
        self.after(50, self._sync_filter_positions)

    def _extract_values(self, record: MarcRecord) -> List[str]:
        values = []
        if self._selectable:
            values.append("")
        for col in self._columns:
            try:
                values.append(col["extract"](record))
            except Exception:
                values.append("")
        return values

    def _refresh_tree(self) -> None:
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
        total, visible = len(self._records), len(self._filtered)
        self._count_label.config(
            text=f"{visible} / {total} notice(s)" if visible < total
                 else f"{total} notice(s)"
        )

    # ------------------------------------------------------------------
    # Filtres
    # ------------------------------------------------------------------

    def _apply_filters(self) -> None:
        terms = [v.get().strip().lower() for v in self._filter_vars]
        terms = ["" if t == _PLACEHOLDER.lower() else t for t in terms]
        if not any(terms):
            self._filtered = list(range(len(self._records)))
        else:
            offset = 1 if self._selectable else 0
            self._filtered = [
                i for i, rec in enumerate(self._records)
                if all(
                    not t or t in self._extract_values(rec)[offset + j].lower()
                    for j, t in enumerate(terms)
                )
            ]
        self._refresh_tree()

    def _clear_filters(self, refresh: bool = True) -> None:
        for var in self._filter_vars:
            var.set("")
        for entry in self._filter_entries:
            entry.delete(0, tk.END)
            entry.insert(0, _PLACEHOLDER)
            entry.config(fg=COLORS["text_muted"])
        if refresh:
            self._filtered = list(range(len(self._records)))
            self._refresh_tree()

    # ------------------------------------------------------------------
    # Tri
    # ------------------------------------------------------------------

    def _sort_by_column(self, col_id: str, col_idx: int) -> None:
        if self._sort_col == col_id:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col, self._sort_asc = col_id, True

        extract = self._columns[col_idx]["extract"]
        self._filtered.sort(
            key=lambda i: (lambda v: v.lower() if isinstance(v, str) else "")(
                self._try_extract(extract, i)),
            reverse=not self._sort_asc,
        )
        for i, col in enumerate(self._columns):
            cid = f"col_{i}"
            arrow = (" ▲" if self._sort_asc else " ▼") if cid == col_id else ""
            self._tree.heading(cid, text=col["label"] + arrow)
        self._refresh_tree()

    def _try_extract(self, extract, idx: int) -> str:
        try:
            return extract(self._records[idx])
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Sélection
    # ------------------------------------------------------------------

    def _check_symbol(self, i: int) -> str:
        return "☑" if self._checked[i] else "☐"

    def select_all(self) -> None:
        self._checked = [True] * len(self._records)
        self._update_check_column(); self._notify()

    def deselect_all(self) -> None:
        self._checked = [False] * len(self._records)
        self._update_check_column(); self._notify()

    def get_selected_indices(self) -> List[int]:
        return [i for i, c in enumerate(self._checked) if c]

    def _toggle_check(self, index: int) -> None:
        if 0 <= index < len(self._checked):
            self._checked[index] = not self._checked[index]
            iid = str(index)
            if self._tree.exists(iid):
                vals    = list(self._tree.item(iid, "values"))
                vals[0] = self._check_symbol(index)
                self._tree.item(iid, values=vals)
            self._notify()

    def _update_check_column(self) -> None:
        for i in self._filtered:
            iid = str(i)
            if self._tree.exists(iid):
                vals    = list(self._tree.item(iid, "values"))
                vals[0] = self._check_symbol(i)
                self._tree.item(iid, values=vals)

    def _notify(self) -> None:
        if self._on_sel_cb:
            self._on_sel_cb(self.get_selected_indices())

    def _on_click(self, event: tk.Event) -> None:
        if self._tree.identify_region(event.x, event.y) != "cell":
            return
        col = self._tree.identify_column(event.x)
        iid = self._tree.identify_row(event.y)
        if iid and col == "#1":
            self._toggle_check(int(iid))

    def _on_space(self, _event: tk.Event) -> None:
        iid = self._tree.focus()
        if iid:
            self._toggle_check(int(iid))

    # ------------------------------------------------------------------
    # Export CSV
    # ------------------------------------------------------------------

    def _export_csv(self) -> None:
        labels  = [col["label"] for col in self._columns]
        col_ids = [f"col_{i}" for i in range(len(self._columns))]
        export_treeview_to_csv(
            tree=self._tree, column_labels=labels,
            column_ids=col_ids, default_name=self._csv_filename,
        )
