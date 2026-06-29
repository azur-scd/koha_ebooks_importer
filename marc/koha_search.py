#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marc/koha_search.py — Recherche et mise à jour 001 depuis le catalogue Koha
============================================================================
Pour chaque notice préparée, interroge le catalogue Koha via SRU (EAN → 001)
et met à jour la zone 001 si exactement une notice Koha est trouvée.

Règles :
  - 0 notice Koha trouvée  → 001 inchangé
  - 1 notice Koha trouvée  → 001 remplacé par celui de la notice Koha
  - >1 notices trouvées    → 001 inchangé (ambiguïté)

Les modifications sont effectuées sur des COPIES des notices (non en place).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from marc.reader import MarcRecord, MarcField
from marc.koha_sru import search_koha_by_ean, KohaSearchResult

KOHA_REQUEST_DELAY = 0.2  # secondes entre chaque requête


@dataclass
class KohaSearchDetail:
    """Résultat de la recherche Koha pour une notice."""
    marc_index:    int
    ean:           str
    titre:         str
    id001_avant:   str
    id001_apres:   str
    n_found:       int     # Nombre de notices Koha trouvées (avant filtrage)
    n_matching:    int     # Nombre après filtrage LIVRE_EL/biblioondemand
    status:        str     # "updated", "not_found", "ambiguous", "no_ean", "error"
    error_msg:     str = ""


@dataclass
class KohaSearchReport:
    """Rapport global de la recherche Koha."""
    details:    List[KohaSearchDetail] = field(default_factory=list)
    n_total:    int = 0
    n_updated:  int = 0   # 001 mis à jour (1 seule notice trouvée)
    n_not_found:int = 0   # Aucune notice Koha
    n_ambiguous:int = 0   # Plusieurs notices Koha
    n_no_ean:   int = 0   # Pas d'EAN
    n_error:    int = 0

    def summary_lines(self) -> List[str]:
        return [
            f"Notices traitées                    : {self.n_total}",
            f"001 mis à jour (1 notice Koha)      : {self.n_updated}",
            f"Aucune notice Koha trouvée          : {self.n_not_found}",
            f"Ambiguïté (plusieurs notices Koha)  : {self.n_ambiguous}",
            f"Notices sans EAN                    : {self.n_no_ean}",
            f"Erreurs réseau                      : {self.n_error}",
        ]


def search_and_update_001(
    records:        List[MarcRecord],
    progress_cb:    Optional[callable] = None,
    use_koha_test:  bool = False,
) -> tuple[List[MarcRecord], KohaSearchReport]:
    """
    Pour chaque notice, cherche dans Koha par EAN et met à jour le 001
    si exactement une notice filtrée est trouvée.

    Travaille sur des COPIES des notices (les originales ne sont pas modifiées).

    Args:
        records        : Liste de MarcRecord (notices enrichies Sudoc).
        progress_cb    : Callback(n_done, n_total) pour la progression.
        use_koha_test  : Si True, utilise l'URL de Koha test au lieu de la production.

    Returns:
        (liste_copiée_et_mise_à_jour, rapport)
    """
    import copy
    copies  = [r.clone() for r in records]
    report  = KohaSearchReport(n_total=len(copies))
    last_req = 0.0

    for idx, record in enumerate(copies):
        ean   = record.get_value("073", "a").strip()
        titre = record.get_value("200", "a").strip() or "(sans titre)"
        id001 = record.get_value("001")

        if not ean:
            report.details.append(KohaSearchDetail(
                marc_index=idx, ean="", titre=titre,
                id001_avant=id001, id001_apres=id001,
                n_found=0, n_matching=0, status="no_ean",
            ))
            report.n_no_ean += 1
            if progress_cb:
                progress_cb(idx + 1, len(copies))
            continue

        # Délai de politesse
        elapsed = time.monotonic() - last_req
        if elapsed < KOHA_REQUEST_DELAY:
            time.sleep(KOHA_REQUEST_DELAY - elapsed)

        result = search_koha_by_ean(ean, use_koha_test=use_koha_test)
        last_req = time.monotonic()

        if result.error:
            report.details.append(KohaSearchDetail(
                marc_index=idx, ean=ean, titre=titre,
                id001_avant=id001, id001_apres=id001,
                n_found=0, n_matching=0,
                status="error", error_msg=result.error,
            ))
            report.n_error += 1

        elif len(result.matching_records) == 1:
            # Cas nominal : 1 seule notice Koha → mettre à jour le 001
            koha_001 = result.matching_records[0].get_value("001")
            record.remove_fields("001")
            f001 = MarcField(tag="001", data=koha_001)
            record.fields.insert(0, f001)
            record.fields.sort(key=lambda f: f.tag)

            report.details.append(KohaSearchDetail(
                marc_index=idx, ean=ean, titre=titre,
                id001_avant=id001, id001_apres=koha_001,
                n_found=result.total_found, n_matching=len(result.matching_records),
                status="updated",
            ))
            report.n_updated += 1

        elif len(result.matching_records) == 0:
            report.details.append(KohaSearchDetail(
                marc_index=idx, ean=ean, titre=titre,
                id001_avant=id001, id001_apres=id001,
                n_found=result.total_found, n_matching=0,
                status="not_found",
            ))
            report.n_not_found += 1

        else:
            # Plusieurs notices Koha → ambiguïté
            report.details.append(KohaSearchDetail(
                marc_index=idx, ean=ean, titre=titre,
                id001_avant=id001, id001_apres=id001,
                n_found=result.total_found, n_matching=len(result.matching_records),
                status="ambiguous",
            ))
            report.n_ambiguous += 1

        if progress_cb:
            progress_cb(idx + 1, len(copies))

    return copies, report


def generate_koha_search_report(
    report: KohaSearchReport,
    path:   "str | Path",
) -> None:
    """Écrit le rapport de recherche Koha dans un fichier texte UTF-8."""
    import datetime
    from pathlib import Path

    now   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []

    def h1(title):
        lines.extend(["", "=" * 70, f"  {title}", "=" * 70])

    lines.append("RAPPORT DE RECHERCHE KOHA (SRU)")
    lines.append(f"Généré le : {now}")
    lines.append("")
    h1("RÉSUMÉ")
    for l in report.summary_lines():
        lines.append(f"  {l}")

    updated = [d for d in report.details if d.status == "updated"]
    h1(f"001 MIS À JOUR ({len(updated)})")
    if updated:
        for d in updated:
            lines.append(f"  #{d.marc_index + 1:>4}  EAN : {d.ean}")
            lines.append(f"    Titre     : {d.titre}")
            lines.append(f"    001 avant : {d.id001_avant}")
            lines.append(f"    001 après : {d.id001_apres}")
            lines.append("")
    else:
        lines.append("  (aucun)")

    ambiguous = [d for d in report.details if d.status == "ambiguous"]
    h1(f"AMBIGUÏTÉS — PLUSIEURS NOTICES KOHA ({len(ambiguous)})")
    if ambiguous:
        lines.append("  001 non modifié — vérification manuelle nécessaire.")
        lines.append("")
        for d in ambiguous:
            lines.append(f"  #{d.marc_index + 1:>4}  EAN : {d.ean}  ({d.n_matching} notices filtrées)")
            lines.append(f"    Titre : {d.titre}")
            lines.append("")
    else:
        lines.append("  (aucune)")

    not_found = [d for d in report.details if d.status == "not_found"]
    h1(f"NON TROUVÉES DANS KOHA ({len(not_found)})")
    if not_found:
        for d in not_found:
            lines.append(f"  #{d.marc_index + 1:>4}  EAN : {d.ean:<20}  Titre : {d.titre}")
    else:
        lines.append("  (aucune)")

    no_ean = [d for d in report.details if d.status == "no_ean"]
    h1(f"NOTICES SANS EAN ({len(no_ean)})")
    if no_ean:
        for d in no_ean:
            lines.append(f"  #{d.marc_index + 1:>4}  Titre : {d.titre}")
    else:
        lines.append("  (aucune)")

    errors = [d for d in report.details if d.status == "error"]
    h1(f"ERREURS RÉSEAU ({len(errors)})")
    if errors:
        for d in errors:
            lines.append(f"  #{d.marc_index + 1:>4}  EAN : {d.ean}")
            lines.append(f"    Titre  : {d.titre}")
            lines.append(f"    Erreur : {d.error_msg}")
            lines.append("")
    else:
        lines.append("  (aucune)")

    lines.extend(["", "─" * 70, "Fin du rapport"])
    Path(path).write_text("\n".join(lines), encoding="utf-8")
