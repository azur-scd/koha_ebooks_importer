#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marc/koha_sru.py — Récupération de notices Koha via le protocole SRU
=====================================================================
Ce module interroge le catalogue Koha via son interface SRU (Search/Retrieve
via URL) pour récupérer les notices MARCXML correspondant à un EAN donné.

Protocole SRU :
  - Opération  : searchRetrieve
  - Index      : dc.identifier (contient l'EAN/ISBN)
  - Format     : marcxml
  - Namespace  : http://www.loc.gov/MARC21/slim (présent dans les réponses Koha)

La réponse est enveloppée dans :
  <zs:searchRetrieveResponse>
    <zs:records>
      <zs:record>
        <zs:recordData>
          <record xmlns="http://www.loc.gov/MARC21/slim">
            ...
          </record>
        </zs:recordData>
      </zs:record>
    </zs:records>
  </zs:searchRetrieveResponse>

Filtrage des notices retournées :
  On ne conserve que les notices dont :
    - 099$t == "LIVRE_EL"       (type de document : livre électronique)
    - 099$z == "biblioondemand" (source : biblioondemand)

  Ces critères permettent d'identifier les notices déjà présentes dans Koha
  pour le même ebook, afin d'éviter les doublons lors d'un import futur.

Configuration dans config.py :
  KOHA_SRU_BASE_URL : URL de base du serveur SRU Koha

Pour étendre :
  - Ajouter d'autres critères de filtrage via les paramètres `filter_t`
    et `filter_z` de `search_koha_by_ean()`.
  - Utiliser d'autres index SRU (dc.title, dc.creator…) en modifiant le
    paramètre `index`.
  - Enrichir les notices Koha trouvées avec des champs locaux dans une
    fonction dédiée appelée depuis app.py.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional
from xml.etree import ElementTree as ET

from marc.reader import MarcField, MarcRecord

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# URL de base du serveur SRU Koha (configurable dans config.py)
try:
    from config import KOHA_SRU_BASE_URL
except ImportError:
    KOHA_SRU_BASE_URL = (
        "https://catalogue-bu-univ-cotedazur.biblibre.fr/biblios"
    )

KOHA_SRU_TIMEOUT      = 15   # secondes
KOHA_SRU_MAX_RECORDS  = 10   # nombre maximum de notices demandées par requête

# Valeurs de filtrage par défaut
KOHA_FILTER_099T = "LIVRE_EL"        # 099$t : type de document
KOHA_FILTER_099Z = "biblioondemand"  # 099$z : source

# Namespaces XML présents dans les réponses SRU Koha
_NS_SRU  = "http://www.loc.gov/zing/srw/"
_NS_MARC = "http://www.loc.gov/MARC21/slim"
_NS = {
    "zs":   _NS_SRU,
    "marc": _NS_MARC,
}


# ---------------------------------------------------------------------------
# Structures de résultat
# ---------------------------------------------------------------------------

@dataclass
class KohaSearchResult:
    """
    Résultat d'une recherche SRU dans le catalogue Koha.

    Attributes:
        ean              : EAN recherché.
        total_found      : Nombre total de notices retournées par le serveur
                           (avant filtrage).
        matching_records : Notices conservées après filtrage (LIVRE_EL +
                           biblioondemand).
        all_records      : Toutes les notices retournées (sans filtrage),
                           utile pour diagnostic.
        error            : Message d'erreur si la requête a échoué, sinon "".
    """
    ean:              str
    total_found:      int               = 0
    matching_records: List[MarcRecord]  = field(default_factory=list)
    all_records:      List[MarcRecord]  = field(default_factory=list)
    error:            str               = ""

    @property
    def found(self) -> bool:
        """True si au moins une notice correspond aux critères de filtrage."""
        return len(self.matching_records) > 0


# ---------------------------------------------------------------------------
# Requête SRU
# ---------------------------------------------------------------------------

def search_koha_by_ean(
    ean:          str,
    base_url:     str = KOHA_SRU_BASE_URL,
    max_records:  int = KOHA_SRU_MAX_RECORDS,
    filter_t:     str = KOHA_FILTER_099T,
    filter_z:     str = KOHA_FILTER_099Z,
) -> KohaSearchResult:
    """
    Recherche des notices Koha correspondant à un EAN via le protocole SRU.

    Interroge l'index dc.identifier du catalogue Koha, récupère jusqu'à
    `max_records` notices, puis filtre celles dont :
      - 099$t == filter_t  (défaut : "LIVRE_EL")
      - 099$z == filter_z  (défaut : "biblioondemand")

    Args:
        ean         : EAN (ou ISBN) à rechercher.
        base_url    : URL de base du serveur SRU Koha.
        max_records : Nombre maximum de notices demandées (défaut : 10).
        filter_t    : Valeur attendue en 099$t (type de document).
        filter_z    : Valeur attendue en 099$z (source).

    Returns:
        KohaSearchResult avec les notices filtrées et les statistiques.
    """
    result = KohaSearchResult(ean=ean)

    # Construire l'URL SRU
    params = {
        "version":        "1.1",
        "operation":      "searchRetrieve",
        "recordSchema":   "marcxml",
        "maximumRecords": str(max_records),
        "query":          f"dc.identifier={ean}",
    }
    url = base_url.rstrip("?&") + "?" + urllib.parse.urlencode(params)

    # Requête HTTP
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "KohaEbookManager/1.0 (SRU search)"},
        )
        with urllib.request.urlopen(req, timeout=KOHA_SRU_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        result.error = f"HTTP {exc.code} {exc.reason}"
        return result
    except urllib.error.URLError as exc:
        result.error = f"Erreur réseau : {exc.reason}"
        return result
    except Exception as exc:
        result.error = f"Erreur inattendue : {exc}"
        return result

    # Parser le XML
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        result.error = f"XML invalide : {exc}"
        return result

    # Nombre total de résultats
    nb_el = root.find("zs:numberOfRecords", _NS)
    if nb_el is not None and nb_el.text:
        try:
            result.total_found = int(nb_el.text.strip())
        except ValueError:
            pass

    # Extraire les notices
    for rec_el in root.findall(".//zs:recordData/marc:record", _NS):
        record = _parse_marc_record(rec_el)
        if record:
            result.all_records.append(record)
            if _matches_filter(record, filter_t, filter_z):
                result.matching_records.append(record)

    # Fallback : namespace absent (au cas où Koha retourne du MARCXML brut)
    if not result.all_records:
        for rec_el in root.findall(".//record"):
            record = _parse_marc_record_no_ns(rec_el)
            if record:
                result.all_records.append(record)
                if _matches_filter(record, filter_t, filter_z):
                    result.matching_records.append(record)

    return result


# ---------------------------------------------------------------------------
# Filtrage
# ---------------------------------------------------------------------------

def _matches_filter(record: MarcRecord, filter_t: str, filter_z: str) -> bool:
    """
    Retourne True si la notice correspond aux critères de filtrage.

    Critères (insensibles à la casse) :
      - 099$t == filter_t  (type de document)
      - 099$z == filter_z  (source)
    """
    for zone_099 in record.get_fields("099"):
        t = (zone_099.get_subfield("t") or "").strip().lower()
        z = (zone_099.get_subfield("z") or "").strip().lower()
        if t == filter_t.lower() and z == filter_z.lower():
            return True
    return False


# ---------------------------------------------------------------------------
# Parsing MARCXML
# ---------------------------------------------------------------------------

def _parse_marc_record(rec_el: ET.Element) -> Optional[MarcRecord]:
    """
    Parse un élément <record> MARCXML avec namespace http://www.loc.gov/MARC21/slim.
    """
    ns = f"{{{_NS_MARC}}}"

    record  = MarcRecord()
    leader  = rec_el.find(f"{ns}leader")
    if leader is not None:
        record.leader = (leader.text or "").strip()

    for cf in rec_el.findall(f"{ns}controlfield"):
        record.add_field(MarcField(
            tag  = cf.get("tag", ""),
            data = (cf.text or "").strip(),
        ))

    for df in rec_el.findall(f"{ns}datafield"):
        f = MarcField(
            tag  = df.get("tag", ""),
            ind1 = df.get("ind1", " "),
            ind2 = df.get("ind2", " "),
        )
        for sf in df.findall(f"{ns}subfield"):
            f.add_subfield(sf.get("code", ""), (sf.text or "").strip())
        record.add_field(f)

    return record if record.fields else None


def _parse_marc_record_no_ns(rec_el: ET.Element) -> Optional[MarcRecord]:
    """
    Parse un élément <record> MARCXML sans namespace (fallback).
    """
    record = MarcRecord()
    leader = rec_el.find("leader")
    if leader is not None:
        record.leader = (leader.text or "").strip()

    for cf in rec_el.findall("controlfield"):
        record.add_field(MarcField(
            tag  = cf.get("tag", ""),
            data = (cf.text or "").strip(),
        ))

    for df in rec_el.findall("datafield"):
        f = MarcField(
            tag  = df.get("tag", ""),
            ind1 = df.get("ind1", " "),
            ind2 = df.get("ind2", " "),
        )
        for sf in df.findall("subfield"):
            f.add_subfield(sf.get("code", ""), (sf.text or "").strip())
        record.add_field(f)

    return record if record.fields else None
