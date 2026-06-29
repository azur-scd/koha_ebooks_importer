#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — Contrôleur principal de l'application
===============================================
Orchestre les interactions entre la vue (ui/), la couche MARC (marc/)
et la couche OAI

Gestion des états des boutons :
  Démarrage          : seul "Importer" est actif, focus dessus.
  Après import       : "Tout sélect.", "Tout désélect." actifs.
                       "Préparer" actif seulement si >= 1 notice sélectionnée.
                       "Exporter" inactif.
  Après préparation  : "Exporter" actif.
  Réinitialiser      : disponible à tout moment, remet l'état initial.

Pour étendre :
  - Nouvelle action : ajouter _action_xxx() + l'enregistrer dans _build_callbacks().
  - Nouveau format d'export : étendre marc/exporters.EXPORTERS.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox
from typing import List, Optional

from config import COLORS, MESSAGES
from marc.reader import MarcRecord, parse_iso2709
from marc.transformations import prepare_records_for_koha
from marc.deduplicator import deduplicate_marc, DeduplicationReport, generate_dedup_report
from marc.sudoc_enricher import enrich_with_sudoc, SudocEnrichmentReport, generate_sudoc_report
from marc.koha_search import search_and_update_001, KohaSearchReport, generate_koha_search_report
from marc.exporters import EXPORTERS
from oai.harvester import harvest_oai, deduplicate_oai, OaiRecord, OaiHarvestError, DeduplicationResult
from oai.matcher   import match_records, MatchResult
from oai.match_logger import generate_match_report
from oai.enricher  import enrich_prepared_records, EnrichmentReport
from ui.main_window import MainWindow


class KohaEbookApp:
    """
    Contrôleur principal (pattern MVC simplifié).

    Modèle     : self._records (bruts) + self._prepared (enrichis) + self._oai_enriched + self._sudoc_enriched 
    Vue        : self._view (MainWindow)
    Contrôleur : cette classe
    """

    def __init__(self, root: tk.Tk):
        self._root = root

        # --- Modèle ---
        self._records:     List[MarcRecord] = []
        self._prepared:    List[MarcRecord] = []
        self._selected:    List[int]        = []
        self._oai_records: List[OaiRecord]  = []   # Notices Dublin Core collectées
        self._match_result:  Optional[MatchResult]      = None
        self._enrich_result: Optional[EnrichmentReport] = None
        self._oai_enriched:       List[MarcRecord]            = []   # Notices après croisement OAI
        self._sudoc_enriched:    List[MarcRecord] = []
        self._sudoc_report:  Optional[SudocEnrichmentReport] = None
        self._koha_records:  List[MarcRecord]                 = []
        self._koha_report:   Optional[KohaSearchReport]       = None


        # --- Vue ---
        self._view = MainWindow(
            root,
            callbacks=self._build_callbacks(),
            on_selection_change=self._on_selection_change,
        )

        # État initial : seul "Importer" est actif
        self._set_initial_state()

    # ------------------------------------------------------------------
    # Initialisation / réinitialisation
    # ------------------------------------------------------------------

    def _build_callbacks(self) -> dict:
        return {
            "import":       self._action_import,
            "select_all":   self._action_select_all,
            "deselect_all": self._action_deselect_all,
            "prepare":      self._action_prepare,
            "oai_fetch":    self._action_oai_fetch,
            "oai_match":    self._action_oai_match,
            "sudoc_enrich": self._action_sudoc_enrich,
            "koha_search":  self._action_koha_search,
            "export":       self._action_export,
            "reset":        self._action_reset,
            "quit":         self._action_quit,
        }

    def _set_initial_state(self) -> None:
        """
        Désactive tous les boutons sauf Importer et Réinitialiser.
        Appelé au démarrage et après une réinitialisation.
        """
        self._view.set_button_enabled("import",       True)
        self._view.set_button_enabled("select_all",   False)
        self._view.set_button_enabled("deselect_all", False)
        self._view.set_button_enabled("prepare",      False)
        self._view.set_button_enabled("oai_fetch",    False)
        self._view.set_button_enabled("oai_match",    False)
        self._view.set_button_enabled("sudoc_enrich", False)
        self._view.set_button_enabled("koha_search",  False)
        self._view.set_button_enabled("export",       False)
        self._view.set_button_enabled("reset",        True)
        self._view.set_button_enabled("quit",         True)

    # ------------------------------------------------------------------
    # Actions utilisateur
    # ------------------------------------------------------------------

    def _action_import(self) -> None:
        """Ouvre un dialogue de sélection de fichier et charge les notices."""
        path = filedialog.askopenfilename(
            title="Importer un fichier UNIMARC ISO2709",
            filetypes=[
                ("Fichiers MARC", "*.mrc *.iso *.unimarc *.marc"),
                ("Tous les fichiers", "*.*"),
            ],
        )
        if not path:
            self._view.set_status(MESSAGES["no_file"], level="idle")
            return

        try:
            self._view.set_status("Lecture du fichier en cours…", level="info")
            self._root.update_idletasks()

            raw_records = parse_iso2709(path)
            n_raw = len(raw_records)

            # Dédoublonnage à l'import (001 puis EAN)
            self._view.set_status("Contrôle des doublons…", level="info")
            self._root.update_idletasks()
            dedup = deduplicate_marc(raw_records)
            self._records  = dedup.records
            self._prepared = []
            self._selected = []

            self._view.load_records(self._records)
            self._view.reset_prepared_tab()
            self._view.set_counts(len(self._records), 0)

            # Message de statut
            if dedup.has_issues():
                status_msg = (
                    f"{n_raw} notice(s) lues — "
                    f"{dedup.n_removed} doublon(s) supprimé(s), "
                    f"{dedup.n_false} faux doublon(s) — "
                    f"{dedup.n_final} notice(s) conservée(s)."
                )
                self._view.set_status(status_msg, level="warning")
            else:
                self._view.set_status(
                    MESSAGES["import_success"].format(n=len(self._records)),
                    level="success",
                )

            # Affichage du rapport de dédoublonnage si nécessaire
            if dedup.has_issues():
                lines = dedup.summary_lines()
                if len(lines) > 30:
                    lines = lines[:30]
                    lines.append("…")
                lines.append("")
                lines.append("Voulez-vous télécharger le rapport détaillé ?")
                download = messagebox.askyesno(
                    "Doublons détectés à l'import",
                    "\n".join(lines),
                )
                if download:
                    self._download_dedup_log(dedup)

            # Après import : sélection dispo, préparer et export toujours off
            has = len(self._records) > 0
            self._view.set_button_enabled("select_all",   has)
            self._view.set_button_enabled("deselect_all", has)
            self._view.set_button_enabled("prepare",      False)
            self._view.set_button_enabled("export",       False)

        except Exception as exc:
            self._view.set_status(
                MESSAGES["parse_error"].format(err=str(exc)), level="error"
            )
            messagebox.showerror(
                "Erreur de lecture",
                MESSAGES["parse_error"].format(err=str(exc)),
            )

    def _action_select_all(self) -> None:
        self._view.select_all()

    def _action_deselect_all(self) -> None:
        self._view.deselect_all()

    def _action_prepare(self) -> None:
        """Prépare les notices sélectionnées et affiche le résultat dans l'onglet dédié."""
        if not self._selected:
            self._view.set_status(MESSAGES["no_selection"], level="warning")
            messagebox.showwarning("Aucune sélection", MESSAGES["no_selection"])
            return

        try:
            self._view.set_status("Préparation des notices en cours…", level="info")
            self._root.update_idletasks()

            self._prepared = prepare_records_for_koha(self._records, self._selected)

            n = len(self._prepared)
            self._view.update_stat("prepared", n)
            self._view.set_state("Prêt à exporter", color=COLORS["success"])
            self._view.set_status(
                MESSAGES["prepare_success"].format(n=n), level="success"
            )

            # Afficher les notices préparées dans l'onglet 2
            self._view.load_prepared_records(self._prepared)

            # Activer l'export, la collecte OAI, l'enrichissement Sudoc, la recherche Koha
            self._view.set_button_enabled("export",       True)
            self._view.set_button_enabled("oai_fetch",    True)
            self._view.set_button_enabled("sudoc_enrich", True)
            self._view.set_button_enabled("koha_search",  True)

        except Exception as exc:
            self._view.set_status(f"Erreur : {exc}", level="error")
            messagebox.showerror("Erreur de préparation", str(exc))

    def _action_export(self) -> None:
        """Exporte les notices préparées ou enrichies par le Sudoc en MARCXML."""
        if not self._prepared:
            self._view.set_status(MESSAGES["no_prepared"], level="warning")
            messagebox.showwarning("Rien à exporter", MESSAGES["no_prepared"])
            return
        
        if self._sudoc_enriched:
            notices_exportees = self._sudoc_enriched
            niveau_enrichissement = "notices UNIMARC BOOD enrichies par OAI et par le Sudoc"
        elif self._oai_enriched:
            notices_exportees = self._oai_enriched
            niveau_enrichissement = "notices UNIMARC BOOD enrichies par OAI"
        else :
            notices_exportees = self._prepared
            niveau_enrichissement = "notices UNIMARC BOOD"

            

        filetypes = [
            (label, f"*{ext}")
            for _key, (label, ext, _fn) in EXPORTERS.items()
        ]
        filetypes.append(("Tous les fichiers", "*.*"))

        path = filedialog.asksaveasfilename(
            title="Exporter les notices en MARCXML",
            defaultextension=".xml",
            filetypes=filetypes,
        )
        if not path:
            return

        export_fn = EXPORTERS["marcxml"][2]
        for _key, (_label, ext, fn) in EXPORTERS.items():
            if path.endswith(ext):
                export_fn = fn
                break

        try:
            export_fn(notices_exportees, path)
            self._view.set_status(
                MESSAGES["export_success"].format(path=path), level="success"
            )
            messagebox.showinfo(
                "Export réussi : " + niveau_enrichissement ,
                MESSAGES["export_success"].format(path=path),
            )
        except Exception as exc:
            self._view.set_status(
                MESSAGES["export_error"].format(err=str(exc)), level="error"
            )
            messagebox.showerror("Erreur d'export", MESSAGES["export_error"].format(err=str(exc)))

    def _action_oai_fetch(self) -> None:
        """
        Collecte toutes les notices de l'entrepôt OAI-PMH (sans filtre de set),
        dédoublonne par identifiant de header (première occurrence conservée),
        affiche le rapport de dédoublonnage, et stocke le résultat dédoublonné.

        La progression est affichée dans la barre de statut après chaque page.
        """
        if not self._prepared:
            messagebox.showwarning(
                "Préparation requise",
                "Veuillez d'abord préparer les notices avant de lancer la collecte OAI.",
            )
            return

        self._view.set_button_enabled("oai_fetch", False)
        self._view.set_status("Connexion au serveur OAI-PMH…", level="info")
        self._root.update_idletasks()

        def progress(collected: int, total) -> None:
            if total:
                msg = f"Collecte OAI-PMH : {collected} / {total} notices…"
            else:
                msg = f"Collecte OAI-PMH : {collected} notices récupérées…"
            self._view.set_status(msg, level="info")
            self._root.update_idletasks()

        try:
            # Collecte globale — pas de set_spec = tout l'entrepôt
            raw_records = harvest_oai(set_spec=None, progress_cb=progress)

            # Dédoublonnage par identifiant de header
            self._view.set_status("Dédoublonnage en cours…", level="info")
            self._root.update_idletasks()
            dedup = deduplicate_oai(raw_records)

            # Stocker uniquement les notices dédoublonnées
            self._oai_records = dedup.records
            self._view.load_oai_records(self._oai_records)
            self._view.set_button_enabled("oai_match", True)

            # Rapport de dédoublonnage
            lines = [
                f"Notices brutes collectées : {dedup.n_raw}",
                f"Doublons supprimés        : {dedup.n_duplicates}",
                f"Notices uniques conservées: {len(dedup.records)}",
            ]
            if dedup.duplicate_ids:
                # N'afficher que les 10 premiers identifiants pour ne pas
                # surcharger la boîte de dialogue
                sample = dedup.duplicate_ids[:10]
                lines.append(
                    f"\nExemples d'identifiants en doublon ({len(dedup.duplicate_ids)} au total) :"
                )
                lines.extend(f"  • {eid}" for eid in sample)
                if len(dedup.duplicate_ids) > 10:
                    lines.append(f"  … et {len(dedup.duplicate_ids) - 10} autre(s)")

            self._view.set_status(
                f"OAI-PMH : {dedup.n_raw} collectées, "
                f"{dedup.n_duplicates} doublons supprimés, "
                f"{len(dedup.records)} uniques.",
                level="success",
            )
            messagebox.showinfo("Collecte OAI-PMH — Dédoublonnage", "\n".join(lines))

        except OaiHarvestError as exc:
            self._view.set_status(f"Erreur OAI-PMH : {exc}", level="error")
            messagebox.showerror("Erreur OAI-PMH", str(exc))
        finally:
            self._view.set_button_enabled("oai_fetch", True)

    def _action_oai_match(self) -> None:
        """
        Croise les notices UNIMARC préparées avec les notices OAI-PMH collectées.

        Clé de croisement : EAN (073$a) côté UNIMARC ↔ identifiant OAI normalisé.
        Affiche un résumé et propose de télécharger un rapport détaillé.
        """
        if not self._prepared:
            messagebox.showwarning("Données manquantes",
                "Veuillez d'abord préparer les notices UNIMARC.")
            return
        if not self._oai_records:
            messagebox.showwarning("Données manquantes",
                "Veuillez d'abord récupérer les données OAI-PMH.")
            return

        self._view.set_status("Croisement UNIMARC / OAI-PMH en cours…", level="info")
        self._root.update_idletasks()

        try:
            self._match_result = match_records(self._prepared, self._oai_records)
            n_matched        = self._match_result.n_matched
            n_unmatched_marc = self._match_result.n_unmatched_marc
            n_unmatched_oai  = self._match_result.n_unmatched_oai
            n_dup            = len(self._match_result.duplicate_oai)

            # Enrichissement sur des CLONES — self._prepared n'est pas modifié
            self._view.set_status("Enrichissement des notices appariées…", level="info")
            self._root.update_idletasks()
            self._oai_enriched = [rec.clone() for rec in self._prepared]
            self._enrich_result = enrich_prepared_records(
                self._oai_enriched, self._match_result
            )
            # Afficher les données croisées dans leur onglet dédié uniquement
            self._view.load_oai_enriched_records(self._oai_enriched)

            self._view.update_stat("matched", n_matched)
            self._view.set_status(
                f"Croisement + enrichissement : {n_matched} appariée(s), "
                f"{self._enrich_result.n_enriched} enrichie(s), "
                f"{n_unmatched_marc} UNIMARC sans correspondance.",
                level="success",
            )

            # Résumé dans la boîte de dialogue
            lines = [
                f"Notices UNIMARC préparées          : {len(self._prepared)}",
                f"Notices OAI collectées (dédupl.)   : {len(self._oai_records)}",
                "",
                f"✅ Appariées                        : {n_matched}",
                f"✅ Enrichies (≥ 1 champ modifié)   : {self._enrich_result.n_enriched}",
                f"❌ UNIMARC sans correspondance OAI  : {n_unmatched_marc}",
                f"⚠  OAI non appariées               : {n_unmatched_oai}",
            ]
            if n_dup:
                lines.append(f"⚠  Doublons normalisés OAI         : {n_dup}")
            lines.append("")
            lines.append("Voulez-vous télécharger le rapport détaillé ?")

            download = messagebox.askyesno(
                "Résultat du croisement", "\n".join(lines)
            )

            if download:
                self._download_match_log()

        except Exception as exc:
            self._view.set_status(f"Erreur croisement : {exc}", level="error")
            messagebox.showerror("Erreur de croisement", str(exc))

    def _download_match_log(self) -> None:
        """
        Ouvre un dialogue de sauvegarde et écrit le rapport de croisement
        dans un fichier texte UTF-8 téléchargeable.
        """
        if self._match_result is None:
            return

        import datetime
        default_name = f"croisement_{datetime.date.today()}.txt"

        path = filedialog.asksaveasfilename(
            title="Enregistrer le rapport de croisement",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[
                ("Fichiers texte", "*.txt"),
                ("Tous les fichiers", "*.*"),
            ],
        )
        if not path:
            return

        try:
            generate_match_report(
                prepared    = self._prepared,
                oai_records = self._oai_records,
                result      = self._match_result,
                path        = path,
            )
            self._view.set_status(f"Rapport exporté : {path}", level="success")
            messagebox.showinfo("Rapport exporté", f"Rapport enregistré :\n{path}")
        except Exception as exc:
            messagebox.showerror("Erreur d'export", str(exc))

    def _download_dedup_log(self, report: DeduplicationReport) -> None:
        """Ouvre un dialogue de sauvegarde et écrit le rapport de dédoublonnage."""
        import datetime
        default_name = f"dedup_import_{datetime.date.today()}.txt"
        path = filedialog.asksaveasfilename(
            title="Enregistrer le rapport de dédoublonnage",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[
                ("Fichiers texte", "*.txt"),
                ("Tous les fichiers", "*.*"),
            ],
        )
        if not path:
            return
        try:
            generate_dedup_report(report, path)
            self._view.set_status(f"Rapport dédoublonnage exporté : {path}", level="success")
            messagebox.showinfo("Rapport exporté", f"Rapport enregistré :\n{path}")
        except Exception as exc:
            messagebox.showerror("Erreur d'export", str(exc))

    def _action_sudoc_enrich(self) -> None:
        """
        Enrichit les notices avec les PPN du Sudoc via ISBN2PPN,
        dans un thread séparé pour ne pas geler l'interface.

        Source utilisée :
        - self._oai_enriched si un enrichissement OAI a déjà été fait
        - sinon self._prepared

        Le résultat final est stocké dans self._sudoc_enriched.

        Le thread de travail communique avec le thread UI via root.after(),
        qui est la seule méthode thread-safe pour mettre à jour Tkinter.
        """
        source_records = self._oai_enriched if self._oai_enriched else self._prepared

        if not source_records:
            messagebox.showwarning(
                "Données manquantes",
                "Veuillez d'abord préparer les notices avant de lancer l'enrichissement Sudoc.",
            )
            return

        # Copie des notices pour enrichissement Sudoc
        self._sudoc_enriched = [rec.clone() for rec in source_records]
        self._sudoc_report = None

        n = len(self._sudoc_enriched)
        source_label = "OAI" if self._oai_enriched else "préparées"

        confirm = messagebox.askyesno(
            "Enrichissement Sudoc",
            f"{n} notice(s) à traiter (source : {source_label}).\n\n"
            "L'opération interroge le webservice Sudoc notice par notice.\n"
            "Elle peut prendre plusieurs minutes selon le volume.\n\n"
            "Lancer l'enrichissement Sudoc ?",
        )
        if not confirm:
            self._sudoc_enriched = []
            return

        # Désactiver les boutons pendant le traitement
        for key in (
            "sudoc_enrich",
            "export",
            "prepare",
            "import",
            "oai_fetch",
            "oai_match",
            "reset",
            "quit",
        ):
            self._view.set_button_enabled(key, False)

        self._view.set_status("Enrichissement Sudoc en cours…", level="info")

        def _update_progress(n_done: int, n_total: int) -> None:
            """Appelé depuis le thread UI via root.after — thread-safe."""
            n_found_so_far = sum(
                1
                for d in self._sudoc_report.details
                if d.status in ("found_unique", "found_multiple")
            ) if self._sudoc_report else 0

            self._view.set_status(
                f"Enrichissement Sudoc : {n_done} / {n_total} notices traitées"
                f" ({n_found_so_far} PPN trouvé(s))…",
                level="info",
            )
            self._view.update_stat("sudoc", n_found_so_far)

        def _progress_cb(n_done: int, n_total: int) -> None:
            """Callback appelé depuis le thread de travail — délègue à root.after."""
            self._root.after(0, _update_progress, n_done, n_total)

        def _worker() -> None:
            """Travail réseau dans le thread de fond."""
            try:
                self._sudoc_report = enrich_with_sudoc(
                    self._sudoc_enriched,
                    progress_cb=_progress_cb,
                )
                self._root.after(0, _on_success)
            except Exception as exc:
                self._root.after(0, _on_error, str(exc))

        def _on_success() -> None:
            """Appelé dans le thread UI à la fin du traitement."""
            rep = self._sudoc_report
            if rep is None:
                _on_error("Rapport Sudoc introuvable.")
                return

            self._view.update_stat("sudoc", rep.n_found)

            # Afficher les données enrichies dans l'onglet dédié
            self._view.load_sudoc_enriched_records(self._sudoc_enriched)

            self._view.set_status(
                f"Enrichissement Sudoc terminé : {rep.n_found} PPN trouvé(s), "
                f"{rep.n_marc_fetched} notice(s) MARC récupérée(s) "
                f"sur {rep.n_total} notice(s) traitée(s).",
                level="success",
            )

            # Réactiver l'interface utile après traitement
            self._set_initial_state()
            self._view.set_button_enabled("export", True)
            self._view.set_button_enabled("oai_fetch", True)
            self._view.set_button_enabled("sudoc_enrich", True)
            self._view.set_button_enabled("koha_search", True)

            lines = rep.summary_lines()
            lines.append("")
            lines.append("Voulez-vous télécharger le rapport détaillé ?")
            if messagebox.askyesno("Enrichissement Sudoc — Résultat", "\n".join(lines)):
                self._download_sudoc_log()

        def _on_error(err: str) -> None:
            """Appelé dans le thread UI en cas d'exception."""
            self._view.set_status(f"Erreur Sudoc : {err}", level="error")
            messagebox.showerror("Erreur Sudoc", err)

            # En cas d'échec, on invalide le résultat Sudoc
            self._sudoc_enriched = []
            self._sudoc_report = None

            # Réactiver les boutons même en cas d'erreur
            self._set_initial_state()
            self._view.set_button_enabled("export", True)
            self._view.set_button_enabled("oai_fetch", True)
            self._view.set_button_enabled("sudoc_enrich", True)
            self._view.set_button_enabled("koha_search", True)

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    def _download_sudoc_log(self) -> None:
        """Ouvre un dialogue de sauvegarde et écrit le rapport Sudoc."""
        if self._sudoc_report is None:
            return

        import datetime
        default_name = f"sudoc_{datetime.date.today()}.txt"

        path = filedialog.asksaveasfilename(
            title="Enregistrer le rapport Sudoc",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[
                ("Fichiers texte", "*.txt"),
                ("Tous les fichiers", "*.*"),
            ],
        )
        if not path:
            return

        try:
            generate_sudoc_report(self._sudoc_report, path)
            self._view.set_status(f"Rapport Sudoc exporté : {path}", level="success")
            messagebox.showinfo("Rapport exporté", f"Rapport enregistré :\n{path}")
        except Exception as exc:
            messagebox.showerror("Erreur d'export", str(exc))

    def _action_koha_search(self) -> None:
        """
        Recherche les notices dans Koha par EAN et met à jour le 001,
        dans un thread séparé pour ne pas geler l'interface.

        Travaille sur des COPIES des notices enrichies par le Sudoc.
        Si exactement 1 notice Koha filtrée est trouvée, le 001 est remplacé.
        Affiche les résultats dans l'onglet "Recherche Koha".
        """
        source_records = self._sudoc_enriched if self._sudoc_enriched else self._prepared

        if not source_records:
            messagebox.showwarning("Données manquantes",
                "Veuillez d'abord enrichir les notices avec le Sudoc.")
            return

        n = len(source_records)
        confirm = messagebox.askyesno(
            "Recherche Koha",
            f"{n} notice(s) à traiter.\n\n"
            "L'opération interroge le catalogue Koha notice par notice.\n"
            "Elle peut prendre plusieurs minutes selon le volume.\n\n"
            "Lancer la recherche Koha ?",
        )
        if not confirm:
            return

        for key in ("koha_search", "sudoc_enrich", "export", "prepare",
                    "import", "oai_fetch", "oai_match", "reset", "quit"):
            self._view.set_button_enabled(key, False)

        self._view.set_status("Recherche Koha en cours…", level="info")

        def _progress_cb(n_done: int, n_total: int) -> None:
            self._root.after(0, lambda: self._view.set_status(
                f"Recherche Koha : {n_done} / {n_total} notices traitées…",
                level="info",
            ))

        def _worker() -> None:
            try:
                copies, report = search_and_update_001(
                    source_records, progress_cb=_progress_cb,
                )
                self._root.after(0, _on_success, copies, report)
            except Exception as exc:
                self._root.after(0, _on_error, str(exc))

        def _on_success(copies, report) -> None:
            self._koha_records = copies
            self._koha_report  = report

            self._view.load_koha_records(self._koha_records)
            self._view.set_status(
                f"Recherche Koha terminée : {report.n_updated} 001 mis à jour "
                f"sur {report.n_total} notice(s).",
                level="success",
            )

            self._set_initial_state()
            self._view.set_button_enabled("export",       True)
            self._view.set_button_enabled("oai_fetch",    True)
            self._view.set_button_enabled("sudoc_enrich", True)
            self._view.set_button_enabled("koha_search",  True)

            lines = report.summary_lines()
            lines.append("")
            lines.append("Voulez-vous télécharger le rapport détaillé ?")
            if messagebox.askyesno("Recherche Koha — Résultat", "\n".join(lines)):
                self._download_koha_log()

        def _on_error(err: str) -> None:
            self._view.set_status(f"Erreur Koha : {err}", level="error")
            messagebox.showerror("Erreur Koha", err)
            self._set_initial_state()
            self._view.set_button_enabled("export",       True)
            self._view.set_button_enabled("oai_fetch",    True)
            self._view.set_button_enabled("sudoc_enrich", True)
            self._view.set_button_enabled("koha_search",  True)

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    def _download_koha_log(self) -> None:
        """Ouvre un dialogue de sauvegarde et écrit le rapport Koha."""
        if self._koha_report is None:
            return
        import datetime
        default_name = f"koha_search_{datetime.date.today()}.txt"
        path = filedialog.asksaveasfilename(
            title="Enregistrer le rapport de recherche Koha",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("Fichiers texte", "*.txt"), ("Tous les fichiers", "*.*")],
        )
        if not path:
            return
        try:
            generate_koha_search_report(self._koha_report, path)
            self._view.set_status(f"Rapport Koha exporté : {path}", level="success")
            messagebox.showinfo("Rapport exporté", f"Rapport enregistré :\n{path}")
        except Exception as exc:
            messagebox.showerror("Erreur d'export", str(exc))

    def _action_quit(self) -> None:
        """Ferme l'application proprement après confirmation."""
        if messagebox.askokcancel("Quitter", "Quitter l'application ?"):
            self._root.destroy()

    def _action_reset(self) -> None:
        """
        Réinitialise complètement l'application :
        vide le modèle, remet les tableaux et compteurs à zéro,
        et restaure l'état initial des boutons.
        """
        self._records      = []
        self._prepared     = []
        self._selected     = []
        self._oai_records  = []
        self._match_result  = None
        self._enrich_result = None
        self._oai_enriched       = []
        self._sudoc_enriched       = []
        self._sudoc_report  = None
        self._koha_records  = []
        self._koha_report   = None

        # Vider les tableaux
        self._view.load_records([])
        self._view.reset_prepared_tab()
        self._view.reset_oai_tab()
        self._view.reset_oai_enriched_tab()
        self._view.reset_sudoc_tab()
        self._view.reset_koha_tab()


        # Remettre les compteurs et l'état
        self._view.update_stat("total",    0)
        self._view.update_stat("selected", 0)
        self._view.update_stat("prepared", 0)
        self._view.update_stat("matched",  0)
        self._view.update_stat("sudoc",    0)
        self._view.set_state("En attente", color=COLORS["accent"])
        self._view.set_status(MESSAGES["reset_done"], level="idle")
        self._view.set_counts(0, 0)

        self._set_initial_state()

    # ------------------------------------------------------------------
    # Callback interne — changement de sélection
    # ------------------------------------------------------------------

    def _on_selection_change(self, selected_indices: List[int]) -> None:
        """
        Appelé par RecordsTable à chaque changement de sélection.
        Active "Préparer" si au moins une notice est cochée.
        Invalide les notices préparées si la sélection change après une préparation.
        """
        self._selected = selected_indices
        n_sel = len(selected_indices)

        self._view.update_stat("selected", n_sel)
        self._view.set_counts(len(self._records), n_sel)

        # Préparer actif seulement si sélection non vide ET notices chargées
        self._view.set_button_enabled("prepare", n_sel > 0)

        # Si la sélection change après une préparation, on invalide l'export
        if self._prepared:
            self._prepared = []
            self._view.update_stat("prepared", 0)
            self._view.set_state("Sélection modifiée", color=COLORS["warning"])
            self._view.set_button_enabled("export", False)
