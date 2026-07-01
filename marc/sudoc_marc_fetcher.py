#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marc/sudoc_marc_fetcher.py — Récupération et intégration des notices UNIMARC Sudoc
================================================================================
Ce module fournit les fonctions nécessaires pour :

  1. Télécharger une notice MARCXML depuis le Sudoc par PPN :
       https://www.sudoc.fr/<ppn>.xml
     Le Sudoc retourne du MARCXML sans namespace.

  2. Analyser le type de document (livre imprimé "cam" ou ebook "clm")
     à partir du leader de la notice.

  3. Sélectionner la meilleure notice parmi plusieurs PPN retournés par
     le webservice ISBN2PPN, selon la règle :
       - Mélange print + ebook  → 1er livre imprimé
       - Que des ebooks          → 1er ebook
       - Que des livres imprimés → 1er disponible
"""

from __future__ import annotations

import copy
import urllib.error
import urllib.request
from typing import List, Optional, Set
from xml.etree import ElementTree as ET

from marc.reader import MarcField, MarcRecord

SUDOC_MARC_URL = "https://www.sudoc.fr/{ppn}.xml"
SUDOC_TIMEOUT = 15

SUDOC_PRINT_REPLACE_TAGS: Set[str] = {
    "101", "102", "105",
    "200", "205", "210ac", "214ac",
    "225",
    "300", "304", "305", "306", "307", "308",
    "314", "320", "327", "330", "333", "334", "359",
    "454",
    "500", "503", "510", "512", "513", "514", "515", "517",
    "600", "601", "604", "605", "606", "607", "608", "610", "616",
    "676",
}

SUDOC_EBOOK_REPLACE_TAGS: Set[str] = {
    "101", "102", "105",
    "200", "205", "210ac", "214ac",
    "225",
    "300", "304", "305", "306", "307", "308",
    "314", "320", "327", "330", "333", "334", "359",
    "454",
    "500", "503", "510", "512", "513", "514", "515", "517",
    "600", "601", "604", "605", "606", "607", "608", "610", "616",
    "676",
}

PAGINATION_PREFIX = "La pagination de l'édition imprimée correspondante est de : "


def fetch_sudoc_marc(ppn: str) -> Optional[MarcRecord]:
    url = SUDOC_MARC_URL.format(ppn=ppn.strip())
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "KohaEbookManager/1.0 (Sudoc MARC fetch)"},
        )
        with urllib.request.urlopen(req, timeout=SUDOC_TIMEOUT) as resp:
            raw_bytes = resp.read()
    except Exception:
        return None

    return _parse_marcxml_no_ns(raw_bytes)


def _parse_marcxml_no_ns(data: bytes) -> Optional[MarcRecord]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None

    rec_el = root if root.tag == "record" else root.find("record")
    if rec_el is None:
        return None

    record = MarcRecord()
    leader = rec_el.find("leader")
    if leader is not None:
        record.leader = (leader.text or "").strip()

    for cf in rec_el.findall("controlfield"):
        record.add_field(MarcField(tag=cf.get("tag", ""), data=(cf.text or "").strip()))

    for df in rec_el.findall("datafield"):
        f = MarcField(tag=df.get("tag", ""), ind1=df.get("ind1", " "), ind2=df.get("ind2", " "))
        for sf in df.findall("subfield"):
            f.add_subfield(sf.get("code", ""), (sf.text or "").strip())
        record.add_field(f)

    return record if record.fields else None


def _get_doc_type(record: MarcRecord) -> str:
    if record is None or not record.leader:
        return "unknown"
    return "ebook" if record.leader[7:8] == "m" else "print"


def fetch_all_sudoc_records(ppns: List[str]) -> List[tuple]:
    results = []
    for ppn in ppns:
        print(f"[SUDOC] Téléchargement notice MARC pour PPN={ppn}")
        rec = fetch_sudoc_marc(ppn)
        dtype = _get_doc_type(rec) if rec is not None else "unknown"
        print(f"[SUDOC] PPN={ppn} → {'OK' if rec is not None else 'absent'} ({dtype})")
        results.append((ppn, rec, dtype))
    return results


def select_best_record(fetched: List[tuple]) -> tuple:
    for ppn, rec, dtype in fetched:
        if rec is not None and dtype == "print":
            return ppn, rec, dtype
    for ppn, rec, dtype in fetched:
        if rec is not None:
            return ppn, rec, dtype
    return "", None, "unknown"


def convert_215_to_307(local: MarcRecord, sudoc: MarcRecord) -> bool:
    return False


def replace_fields_from_sudoc(
    local_record: MarcRecord,
    sudoc_record: MarcRecord,
    doc_type: str
) -> tuple[MarcRecord, List[str]]:
    return local_record, []
