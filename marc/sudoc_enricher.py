#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marc/sudoc_enricher.py — Enrichissement Sudoc via le webservice ISBN2PPN
=========================================================================
Interroge le webservice du Sudoc (Système Universitaire de Documentation)
pour récupérer le PPN (Pica Production Number) correspondant à un ISBN.

URL du webservice :
  https://www.sudoc.fr/services/isbn2ppn/<isbn>&format=text/json

Comportement :
  - Pour chaque notice préparée, extrait l'ISBN (010$a).
  - Interroge le webservice Sudoc.
  - Si un PPN est trouvé (premier élément du bloc "result"), modifie le 801$b
    en y ajoutant " ; enrichi par PPN <ppn>".
  - Les erreurs HTTP 404/500 et les réponses sans bloc "result" sont traitées
    comme "PPN non trouvé" (sans erreur fatale).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from marc.reader import MarcRecord
from marc.sudoc_marc_fetcher import (
    convert_215_to_307,
    fetch_all_sudoc_records,
    replace_fields_from_sudoc,
    select_best_record,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUDOC_ISBN2PPN_URL = "https://www.sudoc.fr/services/isbn2ppn/{isbn}&format=text/json"
SUDOC_TIMEOUT = 15  # secondes


@dataclass
class SudocDetail:
    """Résultat de la recherche Sudoc pour une notice."""
    marc_index: int
    isbn: str
    best_ppn: str
    status: str
    error_msg: str = ""
    titre: str = ""
    marc_fetched: bool = False
    tags_replaced: List[str] = field(default_factory=list)
    all_ppns: List[str] = field(default_factory=list)
    ref_locale: str = ""
    ref_sudoc: str = ""
    doc_type: str = ""


@dataclass
class SudocEnrichmentReport:
    """Rapport global de l'enrichissement Sudoc."""
    details: List[SudocDetail] = field(default_factory=list)
    n_total: int = 0
    n_found: int = 0
    n_not_found: int = 0
    n_no_isbn: int = 0
    n_error: int = 0
    n_marc_fetched: int = 0

    def summary_lines(self) -> List[str]:
        n_unique = sum(1 for d in self.details if d.status == "found_unique")
        n_multiple = sum(1 for d in self.details if d.status == "found_multiple")
        return [
            f"Notices traitées                      : {self.n_total}",
            f"PPN trouvés (total)                   : {self.n_found}",
            f"  dont réponse unique                 : {n_unique}",
            f"  dont réponses multiples (1er retenu): {n_multiple}",
            f"  dont notices MARC récupérées        : {self.n_marc_fetched}",
            f"ISBN non trouvés dans le Sudoc        : {self.n_not_found}",
            f"Notices sans ISBN                     : {self.n_no_isbn}",
            f"Erreurs réseau / serveur              : {self.n_error}",
        ]


def enrich_with_sudoc(
    local_records: List[MarcRecord],
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> SudocEnrichmentReport:
    """Enrichit les notices UNIMARC préparées avec les données Sudoc."""
    report = SudocEnrichmentReport(n_total=len(local_records))

    for idx, local_record in enumerate(local_records):
        if progress_cb:
            progress_cb(idx + 1, len(local_records))

        isbn = (local_record.get_value("010", "a") or "").strip()
        if not isbn:
            report.n_no_isbn += 1
            report.details.append(
                SudocDetail(
                    marc_index=idx,
                    isbn="",
                    best_ppn="",
                    status="no_isbn",
                    titre=(local_record.get_value("200", "a") or "").strip(),
                    ref_locale=(local_record.get_value("001") or "").strip(),
                )
            )
            continue

        print(f"[SUDOC] Recherche ISBN={isbn} (notice #{idx + 1})")

        try:
            req = urllib.request.Request(
                SUDOC_ISBN2PPN_URL.format(isbn=isbn),
                headers={"User-Agent": "KohaEbookImporter/1.0 (Sudoc ISBN2PPN)"},
            )
            with urllib.request.urlopen(req, timeout=SUDOC_TIMEOUT) as resp:
                payload = resp.read()
            data = json.loads(payload.decode("utf-8", errors="replace"))
            ppns = data.get("result") or []
        except Exception as exc:
            report.n_error += 1
            report.details.append(
                SudocDetail(
                    marc_index=idx,
                    isbn=isbn,
                    best_ppn="",
                    status="error",
                    error_msg=str(exc),
                    titre=(local_record.get_value("200", "a") or "").strip(),
                    ref_locale=(local_record.get_value("001") or "").strip(),
                )
            )
            print(f"[SUDOC] Erreur ISBN={isbn} : {exc}")
            continue

        if not ppns:
            report.n_not_found += 1
            report.details.append(
                SudocDetail(
                    marc_index=idx,
                    isbn=isbn,
                    best_ppn="",
                    status="not_found",
                    titre=(local_record.get_value("200", "a") or "").strip(),
                    ref_locale=(local_record.get_value("001") or "").strip(),
                )
            )
            print(f"[SUDOC] Aucun PPN pour ISBN={isbn}")
            continue

        fetched = fetch_all_sudoc_records(ppns)
        best_ppn, best_record, doc_type = select_best_record(fetched)

        report.n_found += 1
        detail = SudocDetail(
            marc_index=idx,
            isbn=isbn,
            best_ppn=best_ppn,
            status="found_unique" if len(ppns) == 1 else "found_multiple",
            titre=(local_record.get_value("200", "a") or "").strip(),
            marc_fetched=best_record is not None,
            all_ppns=list(ppns),
            ref_locale=(local_record.get_value("001") or "").strip(),
            doc_type=doc_type,
        )

        if best_record is not None:
            report.n_marc_fetched += 1
            updated_record, tags = replace_fields_from_sudoc(local_record, best_record, doc_type)
            convert_215_to_307(updated_record, best_record)
            detail.tags_replaced = tags
            detail.ref_sudoc = (best_record.get_value("001") or "").strip()

        report.details.append(detail)
        print(f"[SUDOC] ISBN={isbn} → PPN={best_ppn} ({doc_type})")

    return report
