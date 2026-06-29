#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Koha BOOD Ebook Importer
==================
Outil graphique de gestion de l'import d'ebooks BOOD dans le catalogue Koha.

Fonctionnalités principales :
- Import de fichiers UNIMARC ISO2709 (UTF-8)
- Affichage et sélection des notices
- Préparation des notices pour Koha (zones 099, 995, 039, 801)
- Export en MARCXML UTF-8

Architecture :
- main.py           : Point d'entrée, instanciation de l'application
- app.py            : Contrôleur principal (logique applicative)
- ui/               : Composants de l'interface graphique
- marc/             : Couche de traitement MARC (lecture, transformation, écriture)
- config.py         : Paramètres configurables

Pour faire évoluer l'outil :
- Ajouter de nouveaux traitements dans marc/transformations.py
- Ajouter de nouveaux formats d'export dans marc/exporters.py
- Étendre l'interface dans ui/
- Modifier les paramètres dans config.py

Usage:
    python main.py                # Lance avec l'URL Koha de production
    python main.py --kohatest     # Lance avec l'URL Koha de test
"""

import sys
import tkinter as tk
from app import KohaEbookApp


def main():
    """Point d'entrée principal de l'application."""
    # Déterminer si on utilise Koha test
    use_koha_test = "--kohatest" in sys.argv
    
    root = tk.Tk()
    root.title("Koha BOOD Ebook Importer")
    root.minsize(1100, 650)

    # Centrage de la fenêtre au démarrage
    root.update_idletasks()
    w = root.winfo_screenwidth()
    h = root.winfo_screenheight()
    root.geometry(f"1200x750+{(w - 1200) // 2}+{(h - 750) // 2}")

    app = KohaEbookApp(root, use_koha_test=use_koha_test)
    root.mainloop()


if __name__ == "__main__":
    main()
