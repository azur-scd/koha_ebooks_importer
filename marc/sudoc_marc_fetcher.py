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

  4. Remplacer dans la notice locale les zones listées dans SUDOC_PRINT_REPLACE_TAGS
     par celles de la notice Sudoc (la notice Sudoc fait autorité).

  5. Convertir la zone 215 (description matérielle) en note 307.

Pour étendre :
  - Modifier SUDOC_PRINT_REPLACE_TAGS pour ajouter/retirer des zones à remplacer.
  - Modifier PAGINATION_PREFIX pour changer le libellé de la note 307.
"""

from __future__ import annotations

import copy
import urllib.error
import urllib.request
from typing import List, Optional, Set
from xml.etree import ElementTree as ET

from marc.reader import MarcField, MarcRecord

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# URL de la notice MARCXML par PPN (sans namespace)
SUDOC_MARC_URL = "https://www.sudoc.fr/{ppn}.xml"
SUDOC_TIMEOUT  = 15  # secondes

# Zones à remplacer dans la notice locale par celles de la notice Sudoc.
# Si la zone est absente dans la notice Sudoc, les zones locales sont supprimées.
# NOTE : la zone 215 est exclue — elle est traitée séparément par
# convert_215_to_307() qui reformule son $a en note 307.
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
    "700", "701", "702", "710", "711", "712", "720", "721", "722",
}

SUDOC_EBOOK_REPLACE_TAGS: Set[str] = {
    "101", "102", "105",
    "200", "205",
    "225",
    "300", "304", "305", "306", "307", "308",
    "314", "320", "327", "330", "333", "334", "359",
    "454",
    "500", "503", "510", "512", "513", "514", "515", "517",
    "600", "601", "604", "605", "606", "607", "608", "610", "616",
    "676",
    "700", "701", "702", "710", "711", "712", "720", "721", "722",
}

# Préfixe inséré devant le contenu de 215$a dans la note 307 générée
PAGINATION_PREFIX = "La pagination de l'édition imprimée correspondante est de : "


# ---------------------------------------------------------------------------
# Récupération d'une notice par PPN
# ---------------------------------------------------------------------------

def fetch_sudoc_marc(ppn: str) -> Optional[MarcRecord]:
    """
    Télécharge la notice MARCXML du Sudoc pour un PPN donné.

    Le Sudoc retourne du MARCXML sans namespace (balises brutes : <record>,
    <leader>, <controlfield>, <datafield>, <subfield>).

    Args:
        ppn : Identifiant PPN de la notice Sudoc.

    Returns:
        MarcRecord, ou None si la notice est introuvable ou illisible.
    """
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
    """
    Parse du MARCXML sans namespace (format retourné par le Sudoc).

    Gère deux structures :
      - <record> à la racine
      - <collection><record>...</record></collection>

    Args:
        data : Bytes du MARCXML.

    Returns:
        MarcRecord, ou None si le XML est invalide ou vide.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None

    rec_el = root if root.tag == "record" else root.find("record")
    if rec_el is None:
        return None

    record = MarcRecord()

    leader_el = rec_el.find("leader")
    if leader_el is not None:
        record.leader = (leader_el.text or "")

    for cf in rec_el.findall("controlfield"):
        record.add_field(MarcField(
            tag=(cf.get("tag", "")),
            data=(cf.text or "")
        ))

    for df in rec_el.findall("datafield"):
        field = MarcField(
            tag=df.get("tag", ""),
            ind1=df.get("ind1", " "),
            ind2=df.get("ind2", " ")
        )
        for sf in df.findall("subfield"):
            field.add_subfield(sf.get("code", ""), (sf.text or ""))
        record.add_field(field)

    return record if record.fields else None


# ---------------------------------------------------------------------------
# Analyse du type de document
# ---------------------------------------------------------------------------

def _get_doc_type(record: MarcRecord) -> str:
    """
    Détermine le type de document depuis le leader (positions 5-7).
    Ex : <leader>     clm0 22        450 </leader>

    En UNIMARC :
      "cam" → livre imprimé (monographie physique)
      "clm" → ebook (ressource électronique)
      autre → inconnu

    Returns:
        "print", "ebook", ou "unknown".
    """
    leader = record.leader or ""
    if len(leader) < 8:
        return "unknown"
    ldr = leader[5:8]
    if ldr == "cam":
        return "print"
    if ldr == "clm":
        return "ebook"
    return "unknown"


# ---------------------------------------------------------------------------
# Téléchargement et sélection de la meilleure notice
# ---------------------------------------------------------------------------

def fetch_all_sudoc_records(ppns: List[str]) -> List[tuple]:
    """
    Télécharge toutes les notices MARCXML pour une liste de PPN.

    Chaque notice est analysée pour déterminer son type (print/ebook/unknown).
    Les notices indisponibles (erreur réseau, 404…) ont record=None.

    Args:
        ppns : Liste de PPN dans l'ordre retourné par le webservice ISBN2PPN.

    Returns:
        Liste de tuples (ppn, record_ou_None, doc_type) dans l'ordre des PPN.
    """
    results = []
    for ppn in ppns:
        rec   = fetch_sudoc_marc(ppn)
        dtype = _get_doc_type(rec) if rec is not None else "unknown"
        results.append((ppn, rec, dtype))
    return results


def select_best_record(fetched: List[tuple]) -> tuple:
    """
    Choisit la notice la plus appropriée parmi celles récupérées.

    Règle de sélection :
      - Si au moins un livre imprimé ET au moins un ebook :
          → 1er livre imprimé (fait autorité pour la description physique)
      - Que des ebooks :
          → 1er ebook
      - Que des livres imprimés (ou types inconnus) :
          → 1er disponible
      - Aucune notice disponible :
          → (None, None, "unknown")

    Args:
        fetched : Résultat de fetch_all_sudoc_records().

    Returns:
        Tuple (ppn_retenu, record_retenu, doc_type_retenu).
    """
    available = [(ppn, rec, dt) for ppn, rec, dt in fetched if rec is not None]
    if not available:
        return None, None, "unknown"

    prints = [(ppn, rec, dt) for ppn, rec, dt in available if dt == "print"]
    ebooks = [(ppn, rec, dt) for ppn, rec, dt in available if dt == "ebook"]

    if prints and ebooks:
        return prints[0]   # Mélange → 1er livre imprimé
    if ebooks:
        return ebooks[0]   # Que des ebooks → 1er
    return available[0]    # Que des prints (ou inconnus) → 1er


# ---------------------------------------------------------------------------
# Conversion 215 → 307
# ---------------------------------------------------------------------------

def convert_215_to_307(local: MarcRecord, sudoc: MarcRecord) -> bool:
    """
    Extrait le $a de la zone 215 de la notice Sudoc et l'ajoute comme
    nouvelle note 307$a dans la notice locale, avec un libellé d'introduction.

    La zone 215 décrit la description matérielle de l'édition imprimée
    (pagination, illustrations, format). On la reformule en note pour l'ebook.

    Format de la note :
      "La pagination de l'édition imprimée correspondante est de : <215$a>"

    Comportement :
      - Les zones 307 existantes dans la notice locale sont conservées.
      - La nouvelle 307 est ajoutée en plus (pas de remplacement).
      - Les zones sont retriées après ajout.
      - Si la notice Sudoc n'a pas de 215 ou pas de $a : ne fait rien.

    Args:
        local : Notice locale à modifier (en place).
        sudoc : Notice Sudoc source.

    Returns:
        True si une zone 307 a été ajoutée, False sinon.
    """
    zone_215 = sudoc.get_field("215")
    if zone_215 is None:
        return False

    pagination = (zone_215.get_subfield("a") or "").strip()
    if not pagination:
        return False

    new_307 = MarcField(tag="307", ind1=" ", ind2=" ")
    new_307.add_subfield("a", PAGINATION_PREFIX + pagination)
    local.add_field(new_307)
    local.fields.sort(key=lambda f: f.tag)
    return True


# ---------------------------------------------------------------------------
# Remplacement des zones par celles de la notice Sudoc
# ---------------------------------------------------------------------------

def replace_fields_from_sudoc(
    local_record: MarcRecord,
    sudoc_record: MarcRecord,
    doc_type:  str
) -> tuple[MarcRecord, List[str]]:
    """
    Remplace dans `local` les zones dont le tag figure dans `tags` par
    celles de la notice Sudoc.

    Règles :
      - Toutes les zones du tag dans `local` sont supprimées.
      - Les zones du même tag dans `sudoc` sont copiées dans `local`.
      - Si le tag est dans `tags` mais absent de `sudoc`, les zones locales
        sont simplement supprimées (la notice Sudoc fait autorité).
      - Les zones hors de `tags` ne sont jamais modifiées.
      - Les zones sont triées par tag (001 → 999) après remplacement.

    Args:
        local : Notice locale à modifier.
        sudoc : Notice Sudoc source.
        doc_type  : type de notice (print, ebook, unknown)

    Returns:
        Notice modifiée ; Liste des tags effectivement modifiés (utile pour le rapport de log).
    """

    result_record = copy.deepcopy(local_record)

    if doc_type == "ebook":
        tags = SUDOC_EBOOK_REPLACE_TAGS
    else:
        tags = SUDOC_PRINT_REPLACE_TAGS

    modified_tags: List[str] = []

# Attention : cette version gère mal le cas des zones répétées, mais vu qu'il ne devrait pas y en avoir dans les données sources
# ça ne devrait pas être un problème
    for item in sorted(tags):
        # Cas d'une zone avec sous-champs, ex. "214ac"
        if len(item) > 3:
            tag = item[:3]
            subfields_to_replace = set(item[3:])

            local_fields = result_record.get_fields(tag)
            sudoc_fields = sudoc_record.get_fields(tag)

            if not local_fields and not sudoc_fields:
                continue

            # On traite les zones par position
            max_fields = max(len(local_fields), len(sudoc_fields))

            for i in range(max_fields):

                if i >= len(local_fields):
                    # Zone absente localement : on copie toute la zone Sudoc
                    result_record.add_field(copy.deepcopy(sudoc_fields[i]))
                    continue

                local_field = local_fields[i]

                # Suppression des sous-champs à remplacer
                local_field.subfields = [
                    sf for sf in local_field.subfields
                    if sf.code not in subfields_to_replace
                ]

                # Ajout des sous-champs provenant du Sudoc
                if i < len(sudoc_fields):
                    for sf in sudoc_fields[i].subfields:
                        if sf.code in subfields_to_replace:
                            local_field.add_subfield(
                                sf.code,
                                sf.value
                            )

            modified_tags.append(item)

        # Cas normal : remplacement complet de la zone
        else:
            tag = item

            local_fields = result_record.get_fields(tag)
            sudoc_fields = sudoc_record.get_fields(tag)

            if not local_fields and not sudoc_fields:
                continue

            result_record.remove_fields(tag)

            for f in sudoc_fields:
                result_record.add_field(copy.deepcopy(f))

            modified_tags.append(tag)

    result_record.fields.sort(key=lambda f: f.tag)
    return result_record, modified_tags