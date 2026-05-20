#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/csv_export.py — Export CSV d'un Treeview Tkinter
====================================================
Fournit la fonction `export_treeview_to_csv()` utilisée par tous les
tableaux de l'application pour télécharger leur contenu en CSV.

L'export reflète l'état courant du tableau :
  - Colonnes dans leur ordre d'affichage (hors colonne de sélection ☑/☐)
  - Lignes dans l'ordre de tri courant (tel qu'affiché à l'écran)
  - Encodage UTF-8 avec BOM pour compatibilité Excel

Pour étendre :
  - Ajouter d'autres formats (TSV, XLSX) en suivant le même pattern.
"""

from __future__ import annotations

import csv
from tkinter import filedialog, messagebox
from tkinter import ttk
from typing import List


def export_treeview_to_csv(
    tree:          ttk.Treeview,
    column_labels: List[str],
    column_ids:    List[str],
    default_name:  str = "export.csv",
) -> None:
    """
    Ouvre un dialogue de sauvegarde et écrit le contenu du Treeview en CSV.

    Args:
        tree          : Le widget Treeview à exporter.
        column_labels : En-têtes des colonnes (dans l'ordre de column_ids).
        column_ids    : Identifiants internes des colonnes Treeview à exporter.
                        La colonne de sélection (☑/☐) est exclue automatiquement
                        si son id n'est pas dans cette liste.
        default_name  : Nom de fichier proposé par défaut.
    """
    if not tree.get_children():
        messagebox.showwarning("Tableau vide", "Aucune donnée à exporter.")
        return

    path = filedialog.asksaveasfilename(
        title="Enregistrer le tableau en CSV",
        defaultextension=".csv",
        initialfile=default_name,
        filetypes=[("Fichiers CSV", "*.csv"), ("Tous les fichiers", "*.*")],
    )
    if not path:
        return

    try:
        # UTF-8 avec BOM pour compatibilité Excel (reconnaît l'encodage)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";", quoting=csv.QUOTE_ALL)

            # En-tête
            writer.writerow(column_labels)

            # Lignes dans l'ordre courant du Treeview (après tri éventuel)
            for iid in tree.get_children(""):
                row = [tree.set(iid, col_id) for col_id in column_ids]
                writer.writerow(row)

        messagebox.showinfo("Export CSV", f"Fichier exporté :\n{path}")

    except Exception as exc:
        messagebox.showerror("Erreur d'export CSV", str(exc))
