#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oai/match_logger.py — Génération du rapport de croisement UNIMARC/OAI
======================================================================
Produit un fichier texte téléchargeable détaillant le résultat du croisement
notice par notice, pour permettre à l'utilisateur de diagnostiquer les
correspondances manquantes.

Structure du rapport :
  - En-tête : date, nombre de notices traitées
  - Résumé chiffré
  - Section "Notices appariées" : EAN, identifiant OAI, titre DC
  - Section "Notices UNIMARC sans correspondance OAI" : EAN, titre UNIMARC
  - Section "Notices OAI non appariées" : identifiant normalisé
  - Section "Doublons OAI" (normalisés) : identifiants en conflit
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import List

from marc.reader import MarcRecord
from oai.harvester import OaiRecord
from oai.matcher import MatchResult


def generate_match_report(
    prepared:    List[MarcRecord],
    oai_records: List[OaiRecord],
    result:      MatchResult,
    path:        str | Path,
) -> None:
    """
    Écrit le rapport de croisement dans un fichier texte UTF-8.

    Args:
        prepared    : Notices UNIMARC préparées (liste complète, pas seulement les matchées).
        oai_records : Notices OAI dédoublonnées.
        result      : Résultat du croisement (MatchResult).
        path        : Chemin du fichier à écrire.
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: List[str] = []

    def h1(title: str) -> None:
        lines.append("")
        lines.append("=" * 70)
        lines.append(f"  {title}")
        lines.append("=" * 70)

    def h2(title: str) -> None:
        lines.append("")
        lines.append(f"── {title}")
        lines.append("-" * 60)

    # ── En-tête ────────────────────────────────────────────────────────
    lines.append("RAPPORT DE CROISEMENT UNIMARC / OAI-PMH")
    lines.append(f"Généré le : {now}")
    lines.append("")

    # ── Résumé ─────────────────────────────────────────────────────────
    h1("RÉSUMÉ")
    lines.append(f"  Notices UNIMARC préparées          : {len(prepared)}")
    lines.append(f"  Notices OAI collectées (dédupl.)   : {len(oai_records)}")
    lines.append("")
    lines.append(f"  ✅ Appariées                        : {result.n_matched}")
    lines.append(f"  ❌ UNIMARC sans correspondance OAI  : {result.n_unmatched_marc}")
    lines.append(f"  ⚠  OAI non appariées               : {result.n_unmatched_oai}")
    lines.append(f"  ⚠  Doublons normalisés côté OAI    : {len(result.duplicate_oai)}")

    # ── Notices appariées ──────────────────────────────────────────────
    h1(f"NOTICES APPARIÉES ({result.n_matched})")
    if result.matches:
        for idx in sorted(result.matches):
            marc_rec = prepared[idx]
            oai_rec  = result.matches[idx]
            ean      = marc_rec.get_value("073", "a") or "(absent)"
            titre_marc = marc_rec.get_value("200", "a") or "(sans titre)"
            titre_dc   = oai_rec.first("title") or "(sans titre DC)"
            oai_id     = oai_rec.identifier
            lines.append(f"  EAN          : {ean}")
            lines.append(f"  Titre UNIMARC: {titre_marc}")
            lines.append(f"  Titre DC     : {titre_dc}")
            lines.append(f"  OAI header   : {oai_id}")
            lines.append("")
    else:
        lines.append("  (aucune)")

    # ── UNIMARC sans correspondance ────────────────────────────────────
    h1(f"NOTICES UNIMARC SANS CORRESPONDANCE OAI ({result.n_unmatched_marc})")
    if result.unmatched_marc:
        for idx in result.unmatched_marc:
            marc_rec = prepared[idx]
            ean      = marc_rec.get_value("073", "a") or "(EAN absent)"
            titre    = marc_rec.get_value("200", "a") or "(sans titre)"
            id001    = marc_rec.get_value("001") or "(sans 001)"
            lines.append(f"  001          : {id001}")
            lines.append(f"  EAN (073$a)  : {ean}")
            lines.append(f"  Titre        : {titre}")
            lines.append("")
    else:
        lines.append("  (aucune)")

    # ── OAI non appariées ──────────────────────────────────────────────
    h1(f"NOTICES OAI NON APPARIÉES ({result.n_unmatched_oai})")
    if result.unmatched_oai:
        # Chercher le titre DC pour chaque identifiant non apparié
        # On construit un index id_normalise → OaiRecord pour la recherche
        from oai.matcher import _normalize_oai_id
        oai_by_norm = {_normalize_oai_id(r.identifier): r for r in oai_records}
        for norm_id in sorted(result.unmatched_oai):
            oai_rec   = oai_by_norm.get(norm_id)
            titre_dc  = oai_rec.first("title") if oai_rec else "(non trouvé)"
            oai_id    = oai_rec.identifier if oai_rec else norm_id
            lines.append(f"  OAI header   : {oai_id}")
            lines.append(f"  ID normalisé : {norm_id}")
            lines.append(f"  Titre DC     : {titre_dc}")
            lines.append("")
    else:
        lines.append("  (aucune)")

    # ── Doublons OAI normalisés ────────────────────────────────────────
    h1(f"DOUBLONS OAI (identifiant normalisé en double) ({len(result.duplicate_oai)})")
    if result.duplicate_oai:
        lines.append("  Ces EAN avaient plusieurs notices OAI après normalisation.")
        lines.append("  Aucune n'a été retenue dans l'index — elles n'ont pas pu être appariées.")
        lines.append("")
        for norm_id, raw_ids in sorted(result.duplicate_oai.items()):
            lines.append(f"  EAN normalisé  : {norm_id}")
            for raw in raw_ids:
                lines.append(f"    → {raw}")
            lines.append("")
    else:
        lines.append("  (aucun)")

    lines.append("")
    lines.append("─" * 70)
    lines.append("Fin du rapport")

    Path(path).write_text("\n".join(lines), encoding="utf-8")
