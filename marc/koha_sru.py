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
    - 801$b contient le mot "biblioondemand" (source d'import — peut être parmi d'autres mots)

  Ces critères permettent d'identifier les notices déjà présentes dans Koha
  pour le même ebook, afin d'éviter les doublons lors d'un import futur.

Configuration dans config.py :
  KOHA_SRU_BASE_URL : URL de base du serveur SRU Koha production
  KOHA_TEST_SRU_BASE_URL : URL de base du serveur SRU Koha test

Pour étendre :
  - Ajouter d'autres critères de filtrage via les paramètres de `search_koha_by_ean()`.
  - Utiliser d'autres index SRU (dc.title, dc.creator…) en modifiant le paramètre `query`.
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

# URL de base du serveur SRU Koha (importée depuis config.py)
from config import KOHA_SRU_BASE_URL, KOHA_TEST_SRU_BASE_URL

KOHA_SRU_TIMEOUT      = 15   # secondes
KOHA_SRU_MAX_RECORDS  = 10   # nombre maximum de notices demandées par requête

# Valeurs de filtrage par défaut
KOHA_FILTER_099T = "LIVRE_EL"           # 099$t : type de document (requis)
KOHA_FILTER_801B = "biblioondemand"     # 801$b : doit contenir ce mot (peut être parmi d'autres)

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
                           biblioondemand dans 801$b).
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
    base_url:     str = None,
    max_records:  int = KOHA_SRU_MAX_RECORDS,
    filter_t:     str = KOHA_FILTER_099T,
    filter_b:     str = KOHA_FILTER_801B,
    use_koha_test: bool = False,
) -> KohaSearchResult:
    """
    Recherche des notices Koha correspondant à un EAN via le protocole SRU.

    Interroge l'index dc.identifier du catalogue Koha, récupère jusqu'à
    `max_records` notices, puis filtre celles dont :
      - 099$t == filter_t  (défaut : "LIVRE_EL" — type de document requis)
      - 801$b contient le mot filter_b  (défaut : "biblioondemand" — source d'import)

    Args:
        ean           : EAN (ou ISBN) à rechercher.
        base_url      : URL de base du serveur SRU Koha (optionnel, déterminée automatiquement).
        max_records   : Nombre maximum de notices demandées (défaut : 10).
        filter_t      : Valeur attendue en 099$t (type de document, requis).
        filter_b      : Mot à rechercher dans 801$b (source d'import, défaut : "biblioondemand").
        use_koha_test : Si True, utilise l'URL de Koha test. Si False, utilise la production.

    Returns:
        KohaSearchResult avec les notices filtrées et les statistiques.
    """
    # Déterminer l'URL à utiliser si non spécifiée
    if base_url is None:
        base_url = KOHA_TEST_SRU_BASE_URL if use_koha_test else KOHA_SRU_BASE_URL
    
    result = KohaSearchResult(ean=ean)

    print(f"\n[SRU SEARCH] ────────────────────────────────────────────────")
    print(f"[SRU] EAN recherché: {ean}")
    print(f"[SRU] URL base: {base_url}")
    print(f"[SRU] Mode: {'TEST' if use_koha_test else 'PRODUCTION'}")
    print(f"[SRU] Filtres attendus: 099$t='{filter_t}', 801$b doit contenir '{filter_b}'")

    # Construire l'URL SRU
    params = {
        "version":        "1.1",
        "operation":      "searchRetrieve",
        "recordSchema":   "marcxml",
        "maximumRecords": str(max_records),
        "query":          f"dc.identifier={ean}",
    }
    url = base_url.rstrip("?&") + "?" + urllib.parse.urlencode(params)
    print(f"[SRU] URL complète: {url}")

    # Requête HTTP
    try:
        print(f"[SRU] Envoi de la requête...")
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "KohaEbookManager/1.0 (SRU search)"},
        )
        with urllib.request.urlopen(req, timeout=KOHA_SRU_TIMEOUT) as resp:
            raw = resp.read()
        print(f"[SRU] ✅ Réponse reçue: {len(raw)} bytes")
        raw_text = raw.decode("utf-8", errors="replace")
        print(f"[SRU] Contenu brut de la réponse:\n{raw_text}")
    except urllib.error.HTTPError as exc:
        result.error = f"HTTP {exc.code} {exc.reason}"
        print(f"[SRU] ❌ Erreur HTTP: {result.error}")
        return result
    except urllib.error.URLError as exc:
        result.error = f"Erreur réseau : {exc.reason}"
        print(f"[SRU] ❌ Erreur réseau: {result.error}")
        return result
    except Exception as exc:
        result.error = f"Erreur inattendue : {exc}"
        print(f"[SRU] ❌ Erreur inattendue: {result.error}")
        return result

    # Parser le XML
    try:
        print(f"[SRU] Parsing du XML...")
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        result.error = f"XML invalide : {exc}"
        print(f"[SRU] ❌ Erreur XML: {result.error}")
        return result

    # Nombre total de résultats
    nb_el = root.find("zs:numberOfRecords", _NS)
    if nb_el is not None and nb_el.text:
        try:
            result.total_found = int(nb_el.text.strip())
        except ValueError:
            pass
    print(f"[SRU] Nombre de notices trouvées (avant filtrage): {result.total_found}")

    # Extraire les notices
    print(f"[SRU] Extraction des notices...")
    notices_avec_ns = 0
    notices_sans_ns = 0
    
    for idx, rec_el in enumerate(root.findall(".//zs:recordData/marc:record", _NS)):
        notices_avec_ns += 1
        record = _parse_marc_record(rec_el)
        if record:
            result.all_records.append(record)
            print(f"[SRU]   Notice #{idx + 1} (avec namespace):")
            print(f"[SRU]     - 001: {record.get_value('001')}")
            print(f"[SRU]     - 200$a (titre): {record.get_value('200', 'a')}")
            
            # Afficher le 099$t
            champs_099 = record.get_fields("099")
            if champs_099:
                for zone_idx, zone_099 in enumerate(champs_099):
                    t_val = (zone_099.get_subfield("t") or "").strip()
                    print(f"[SRU]     - 099#{zone_idx + 1}$t: '{t_val}'")
            else:
                print(f"[SRU]     - ⚠️  Aucun champ 099 trouvé")
            
            # Afficher le 801$b
            champs_801 = record.get_fields("801")
            if champs_801:
                for zone_idx, zone_801 in enumerate(champs_801):
                    b_val = (zone_801.get_subfield("b") or "").strip()
                    print(f"[SRU]     - 801#{zone_idx + 1}$b: '{b_val}'")
            else:
                print(f"[SRU]     - ⚠️  Aucun champ 801 trouvé")
            
            if _matches_filter(record, filter_t, filter_b):
                result.matching_records.append(record)
                print(f"[SRU]     ✅ Conservée (critères de filtrage OK)")
            else:
                print(f"[SRU]     ❌ Rejetée (ne satisfait pas les critères)")

    # Fallback : namespace absent (au cas où Koha retourne du MARCXML brut)
    for idx, rec_el in enumerate(root.findall(".//record")):
        # Vérifier que ce n'est pas un record déjà traité avec namespace
        if rec_el.find(f"{{{_NS_MARC}}}leader") is None:
            notices_sans_ns += 1
            record = _parse_marc_record_no_ns(rec_el)
            if record:
                result.all_records.append(record)
                print(f"[SRU]   Notice #{notices_avec_ns + idx + 1} (SANS namespace - fallback):")
                print(f"[SRU]     - 001: {record.get_value('001')}")
                print(f"[SRU]     - 200$a (titre): {record.get_value('200', 'a')}")
                
                champs_099 = record.get_fields("099")
                if champs_099:
                    for zone_idx, zone_099 in enumerate(champs_099):
                        t_val = (zone_099.get_subfield("t") or "").strip()
                        print(f"[SRU]     - 099#{zone_idx + 1}$t: '{t_val}'")
                else:
                    print(f"[SRU]     - ⚠️  Aucun champ 099 trouvé")
                
                champs_801 = record.get_fields("801")
                if champs_801:
                    for zone_idx, zone_801 in enumerate(champs_801):
                        b_val = (zone_801.get_subfield("b") or "").strip()
                        print(f"[SRU]     - 801#{zone_idx + 1}$b: '{b_val}'")
                else:
                    print(f"[SRU]     - ⚠️  Aucun champ 801 trouvé")
                
                if _matches_filter(record, filter_t, filter_b):
                    result.matching_records.append(record)
                    print(f"[SRU]     ✅ Conservée (critères de filtrage OK)")
                else:
                    print(f"[SRU]     ❌ Rejetée (ne satisfait pas les critères)")

    print(f"\n[SRU] Résumé:")
    print(f"[SRU] - Notices totales retournées: {len(result.all_records)}")
    print(f"[SRU] - Notices conservées après filtrage: {len(result.matching_records)}")
    if result.matching_records:
        for m_idx, m_rec in enumerate(result.matching_records):
            print(f"[SRU]   → Notice #{m_idx + 1} conservée: 001={m_rec.get_value('001')}")
    print(f"[SRU] ────────────────────────────────────────────────────────\n")

    return result


# ---------------------------------------------------------------------------
# Filtrage
# ---------------------------------------------------------------------------

def _matches_filter(
    record:   MarcRecord, 
    filter_t: str, 
    filter_b: str,
) -> bool:
    """
    Retourne True si la notice correspond aux critères de filtrage.

    Critères :
      - 099$t == filter_t  (type de document — requis, insensible à la casse)
      - 801$b contient le mot filter_b (source d'import — insensible à la casse, 
        peut être parmi d'autres mots séparés par des espaces/tirets)

    Args:
        record   : Notice MARC à vérifier
        filter_t : Valeur attendue pour 099$t (ex: "LIVRE_EL")
        filter_b : Mot à rechercher dans 801$b (ex: "biblioondemand")
    
    Returns:
        True si la notice satisfait tous les critères
    """
    # Vérifier 099$t (obligatoire)
    has_valid_099t = False
    for zone_099 in record.get_fields("099"):
        t = (zone_099.get_subfield("t") or "").strip().lower()
        if t == filter_t.lower():
            has_valid_099t = True
            break
    
    if not has_valid_099t:
        return False
    
    # Vérifier 801$b contient le mot filter_b (obligatoire)
    filter_b_lower = filter_b.lower()
    for zone_801 in record.get_fields("801"):
        b = (zone_801.get_subfield("b") or "").strip().lower()
        # Chercher filter_b comme un mot complet dans b
        # Utiliser des espaces/tirets comme séparateurs
        words = b.replace("-", " ").split()
        if filter_b_lower in words:
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
