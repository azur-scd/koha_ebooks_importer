#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marc/deduplicator.py — Dédoublonnage des notices UNIMARC à l'import
====================================================================
Détecte et traite les doublons dans un fichier UNIMARC importé selon
deux critères successifs :

  1. Doublons sur la zone 001 (identifiant de la notice)
  2. Doublons sur la zone 073$a (EAN)

Pour chaque paire candidate, on compare le lien d'accès (zone 856 sans
$x "vignette") :
  - Même URL non vide → vrai doublon : seule la première occurrence est
    conservée, les suivantes sont supprimées.
  - URL différentes, ou l'une absente → faux doublon : les deux notices
    sont conservées et le cas est signalé à l'utilisateur.

IMPORTANT : deux notices toutes deux sans 856 ne sont PAS traitées comme
doublons — on ne peut pas trancher sans information d'accès.

Pour étendre :
  - Ajouter d'autres critères de dédoublonnage en ajoutant une passe
    dans deduplicate_marc() avec une nouvelle fonction de clé.
  - Modifier la règle de suppression dans _dedup_pass().
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from marc.reader import MarcRecord


def _get_access_url(record: MarcRecord) -> str:
    """URL de la 856 d'acces (hors vignette). Chaine vide si absente."""
    for f in record.fields:
        if f.tag == "856":
            x = (f.get_subfield("x") or "").strip().lower()
            if x != "vignette":
                return (f.get_subfield("u") or "").strip()
    return ""


def _get_001(record: MarcRecord) -> str:
    return record.get_value("001").strip()


def _get_ean(record: MarcRecord) -> str:
    return record.get_value("073", "a").strip()


def _get_titre(record: MarcRecord) -> str:
    return record.get_value("200", "a").strip() or "(sans titre)"


@dataclass
class DuplicateCase:
    """Decrit un cas de doublon (reel ou faux)."""
    key_type:          str
    key_value:         str
    idx_kept:          int
    idx_removed:       Optional[int]
    url_kept:          str
    url_other:         str
    titre_kept:        str
    titre_other:       str
    is_false_duplicate: bool


@dataclass
class DeduplicationReport:
    """Resultat complet du dedoublonnage."""
    records:          List[MarcRecord]    = field(default_factory=list)
    n_original:       int                 = 0
    true_duplicates:  List[DuplicateCase] = field(default_factory=list)
    false_duplicates: List[DuplicateCase] = field(default_factory=list)

    @property
    def n_removed(self) -> int:
        return len(self.true_duplicates)

    @property
    def n_false(self) -> int:
        return len(self.false_duplicates)

    @property
    def n_final(self) -> int:
        return len(self.records)

    def has_issues(self) -> bool:
        return self.n_removed > 0 or self.n_false > 0

    def summary_lines(self) -> List[str]:
        lines = [
            f"Notices importees                 : {self.n_original}",
            f"Vrais doublons supprimes          : {self.n_removed}",
            f"Faux doublons conserves (signales): {self.n_false}",
            f"Notices conservees                : {self.n_final}",
        ]
        if self.true_duplicates:
            lines.append("")
            lines.append("-- Vrais doublons supprimes --")
            for case in self.true_duplicates:
                lines.append(
                    f"  [{case.key_type}] {case.key_value!r}"
                    f" -- notice #{case.idx_removed + 1} supprimee"
                )
        if self.false_duplicates:
            lines.append("")
            lines.append("-- Faux doublons conserves (URLs differentes) --")
            for case in self.false_duplicates:
                lines.append(f"  [{case.key_type}] {case.key_value!r}")
                lines.append(f"    Titre reference : {case.titre_kept}")
                lines.append(f"    URL reference   : {case.url_kept or '(absente)'}")
                lines.append(f"    Titre doublon   : {case.titre_other}")
                lines.append(f"    URL doublon     : {case.url_other or '(absente)'}")
        return lines


def generate_dedup_report(report: DeduplicationReport, path) -> None:
    """
    Ecrit le rapport de dedoublonnage dans un fichier texte UTF-8.
    Inclut les titres des notices pour faciliter l'identification.
    """
    import datetime
    from pathlib import Path

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []

    def h1(title):
        lines.append("")
        lines.append("=" * 70)
        lines.append(f"  {title}")
        lines.append("=" * 70)

    lines.append("RAPPORT DE DEDOUBLONNAGE UNIMARC A L'IMPORT")
    lines.append(f"Genere le : {now}")
    lines.append("")

    h1("RESUME")
    lines.append(f"  Notices lues dans le fichier source    : {report.n_original}")
    lines.append(f"  Vrais doublons supprimes               : {report.n_removed}")
    lines.append(f"  Faux doublons conserves (signales)     : {report.n_false}")
    lines.append(f"  Notices conservees apres dedoublonnage : {report.n_final}")

    h1(f"VRAIS DOUBLONS SUPPRIMES ({report.n_removed})")
    if report.true_duplicates:
        lines.append("  Meme cle ET meme URL d'acces (non vide) => 2e occurrence supprimee.")
        lines.append("")
        for c in report.true_duplicates:
            lines.append(f"  Critere           : {c.key_type}")
            lines.append(f"  Valeur            : {c.key_value!r}")
            lines.append(f"  Notice gardee     (pos. #{c.idx_kept + 1}) : {c.titre_kept}")
            lines.append(f"  Notice supprimee  (pos. #{c.idx_removed + 1}) : {c.titre_other}")
            lines.append(f"  URL commune       : {c.url_kept or '(absente)'}")
            lines.append("")
    else:
        lines.append("  (aucun)")

    h1(f"FAUX DOUBLONS CONSERVES ({report.n_false})")
    if report.false_duplicates:
        lines.append("  Meme cle MAIS URLs differentes ou l'une absente => les deux conservees.")
        lines.append("  A verifier manuellement.")
        lines.append("")
        for c in report.false_duplicates:
            lines.append(f"  Critere           : {c.key_type}")
            lines.append(f"  Valeur            : {c.key_value!r}")
            lines.append(f"  Notice reference  (pos. #{c.idx_kept + 1}) : {c.titre_kept}")
            lines.append(f"    URL             : {c.url_kept or '(absente)'}")
            lines.append(f"  Notice candidate  : {c.titre_other}")
            lines.append(f"    URL             : {c.url_other or '(absente)'}")
            lines.append("")
    else:
        lines.append("  (aucun)")

    lines.append("")
    lines.append("-" * 70)
    lines.append("Fin du rapport")

    Path(path).write_text("\n".join(lines), encoding="utf-8")


def deduplicate_marc(records: List[MarcRecord]) -> DeduplicationReport:
    """
    Dedoublonne une liste de notices UNIMARC.

    Deux passes successives : zone 001 puis EAN (073$a).

    Regle de suppression :
      Les deux URL doivent etre identiques ET non vides pour constituer un
      vrai doublon. Si l'une des deux URL est vide ou differente, on signale
      sans supprimer (faux doublon).

    Cela evite de supprimer par erreur des notices sans zone 856.
    """
    report = DeduplicationReport(n_original=len(records))
    working: List[Tuple[int, MarcRecord]] = list(enumerate(records))

    working, cases_1 = _dedup_pass(working, key_fn=_get_001, key_type="001")
    report.true_duplicates.extend(c for c in cases_1 if not c.is_false_duplicate)
    report.false_duplicates.extend(c for c in cases_1 if c.is_false_duplicate)

    working, cases_2 = _dedup_pass(working, key_fn=_get_ean, key_type="EAN")
    report.true_duplicates.extend(c for c in cases_2 if not c.is_false_duplicate)
    report.false_duplicates.extend(c for c in cases_2 if c.is_false_duplicate)

    report.records = [rec for _, rec in working]
    return report


def _dedup_pass(
    working:  List[Tuple[int, MarcRecord]],
    key_fn:   callable,
    key_type: str,
) -> Tuple[List[Tuple[int, MarcRecord]], List[DuplicateCase]]:
    """
    Une passe de dedoublonnage.

    Vrai doublon : meme cle ET meme URL non vide.
    Faux doublon : meme cle, URLs differentes ou l'une absente.
    """
    seen: Dict[str, Tuple[int, MarcRecord]] = {}
    to_remove: set = set()
    cases: List[DuplicateCase] = []

    for orig_idx, rec in working:
        key = key_fn(rec)
        if not key:
            continue

        if key not in seen:
            seen[key] = (orig_idx, rec)
        else:
            first_orig_idx, first_rec = seen[key]
            url_first   = _get_access_url(first_rec)
            url_curr    = _get_access_url(rec)
            titre_first = _get_titre(first_rec)
            titre_curr  = _get_titre(rec)

            # Vrai doublon UNIQUEMENT si les deux URL sont identiques ET non vides
            if url_first and url_curr and url_first == url_curr:
                to_remove.add(orig_idx)
                cases.append(DuplicateCase(
                    key_type=key_type, key_value=key,
                    idx_kept=first_orig_idx, idx_removed=orig_idx,
                    url_kept=url_first, url_other=url_curr,
                    titre_kept=titre_first, titre_other=titre_curr,
                    is_false_duplicate=False,
                ))
            else:
                cases.append(DuplicateCase(
                    key_type=key_type, key_value=key,
                    idx_kept=first_orig_idx, idx_removed=None,
                    url_kept=url_first, url_other=url_curr,
                    titre_kept=titre_first, titre_other=titre_curr,
                    is_false_duplicate=True,
                ))

    result = [(i, r) for i, r in working if i not in to_remove]
    return result, cases
