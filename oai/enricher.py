#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oai/enricher.py — Enrichissement des notices UNIMARC depuis les données OAI-PMH
=================================================================================
Pour chaque notice UNIMARC appariée avec succès à une notice OAI, remplace ou
enrichit certaines données UNIMARC avec celles issues du Dublin Core OAI :

  1. ISBN (010$a) : remplacé par dc:isbn, ou par dc:identifier si celui-ci
     contient un identifiant ISBN (préfixe "ISBN:" ou format EAN-13 978/979).
     Le champ EAN (073$a) n'est jamais modifié.

  2. Lien d'accès (856$u) : remplacé par dc:identifier HTTP

  3. Lien de couverture (859$u) : remplacé par dc:relation HTTP

  4. Résumé (349$a) : dc:description copié dans une zone 349 locale avec
     $2 "oai-pmh biblioondemand". Ajouté en plus des 349 existantes.
     La zone 330 est réservée aux résumés issus du Sudoc.

Les notices non appariées (absentes de match_result.matches) ne sont pas
modifiées.

Pour étendre :
  - Ajouter d'autres champs à enrichir en créant une fonction _update_xxx()
    et en l'appelant depuis enrich_prepared_records().
    
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import List

from marc.reader import MarcField, MarcRecord
from oai.harvester import OaiRecord
from oai.matcher import MatchResult
from config import ZONE_830_UNIMARC_ET_DC


# ---------------------------------------------------------------------------
# Extraction des données OAI utiles
# ---------------------------------------------------------------------------

def _extract_isbn_from_oai(oai: OaiRecord) -> str:
    """
    Extrait l'ISBN depuis les champs Dublin Core.

    Priorité :
      1. dc:isbn (champ non standard, présent chez biblioondemand)
      2. dc:identifier commençant par "ISBN:" → valeur sans le préfixe
      3. dc:identifier au format EAN-13 (978/979 + 10 chiffres)

    Returns:
        ISBN nettoyé, ou chaîne vide si non trouvé.
    """
    isbn = oai.first("isbn").strip()
    if isbn:
        return isbn
    for val in oai.get("identifier"):
        val = val.strip()
        if val.upper().startswith("ISBN:"):
            return val[5:].strip()
        if re.match(r"^97[89]\d{10}$", val):
            return val
    return ""


def _extract_access_url_from_oai(oai: OaiRecord) -> str:
    """
    Extrait l'URL d'accès au document depuis les champs Dublin Core.

    Convention biblioondemand :
      dc:identifier HTTP → URL d'accès au document (lien de consultation)
      dc:relation HTTP   → URL de couverture (image)

    """
    for val in oai.get("identifier"):
        val = val.strip()
        if _is_http_url(val):
            return val
    return ""


def _extract_cover_url_from_oai(oai: OaiRecord) -> str:
    """
    Extrait l'URL de couverture depuis les champs Dublin Core.

    Convention biblioondemand :
      dc:relation HTTP → URL de couverture (image liée au document)

    """
    for val in oai.get("relation"):
        val = val.strip()
        if _is_http_url(val):
            return val
    return ""


def _is_http_url(val: str) -> bool:
    return val.startswith("http://") or val.startswith("https://")



# ---------------------------------------------------------------------------
# Enrichissement d'une notice
# ---------------------------------------------------------------------------

def _update_isbn(marc: MarcRecord, oai: OaiRecord) -> bool:
    """
    Remplace le 010$a par l'ISBN issu de l'OAI.
    Ne touche pas au 073$a (EAN). Retourne True si une modification est faite.
    """
    isbn = _extract_isbn_from_oai(oai)
    if not isbn:
        return False
    zone_010 = marc.get_field("010")
    if zone_010 is None:
        zone_010 = MarcField(tag="010", ind1=" ", ind2=" ")
        marc.add_field(zone_010)
    old = zone_010.get_subfield("a") or ""
    zone_010.set_subfield("a", isbn)
    return old != isbn


def _update_856(marc: MarcRecord, oai: OaiRecord) -> bool:
    """
    Remplace la zone 856 d'accès par l'URL dc:identifier HTTP de l'OAI.
    Toutes les 856 existantes sont supprimées avant création.
    Retourne True si une URL d'accès a été trouvée et insérée.
    """
    url = _extract_access_url_from_oai(oai)
    if not url:
        return False
    marc.remove_fields("856")
    field = MarcField(tag="856", ind1=" ", ind2=" ")
    field.add_subfield("u", url)
    marc.add_field(field)
    return True


def _update_859(marc: MarcRecord, oai: OaiRecord) -> bool:
    """
    Remplace la zone 859 (couverture) par l'URL dc:relation HTTP de l'OAI.
    Si aucune URL de couverture n'est trouvée, la 859 existante est conservée.
    Retourne True si une URL de couverture a été trouvée et insérée.
    """
    url = _extract_cover_url_from_oai(oai)
    if not url:
        return False
    marc.remove_fields("859")
    field = MarcField(tag="859", ind1=" ", ind2=" ")
    field.add_subfield("u", url)
    marc.add_field(field)
    return True


def _update_349(marc: MarcRecord, oai: OaiRecord) -> bool:
    """
    Copie dc:description de l'OAI dans une zone 349$a locale.
    Ecrase le 349 récupéré en UNIMARC
    Un $2 "oai-pmh biblioondemand" est ajouté pour indiquer la provenance.
    La zone 330 reste réservée aux résumés issus du Sudoc.

    Retourne True si une zone 349 a été ajoutée.
    """
    description = oai.first("description").strip()
    if not description:
        return False
    marc.remove_fields("349")
    zone_349 = MarcField(tag="349", ind1=" ", ind2=" ")
    zone_349.add_subfield("a", description)
    zone_349.add_subfield("2", "DC biblioondemand")
    marc.add_field(zone_349)
    return True

def _update_830(marc: MarcRecord, oai: OaiRecord) -> bool:
    """
    Remplace la zone 830 (note de catalogage).
    """
    marc.remove_fields("830")
    field = MarcField(tag="830", ind1=" ", ind2=" ")
    for code, value in ZONE_830_UNIMARC_ET_DC.items():
        field.add_subfield(code, value)
    marc.add_field(field)
    return

# ---------------------------------------------------------------------------
# Orchestrateur
# ---------------------------------------------------------------------------

def enrich_prepared_records(
    prepared:     List[MarcRecord],
    match_result: MatchResult,
) -> "EnrichmentReport":
    """
    Enrichit les notices UNIMARC préparées avec les données OAI pour toutes
    les correspondances trouvées par match_records().

    Modifie les notices en place dans la liste `prepared`.
    Les notices non appariées ne sont pas touchées.

    Champs enrichis pour chaque notice appariée :
      - 010$a : ISBN OAI
      - 856   : URL d'accès OAI (dc:identifier)
      - 859   : URL de couverture OAI (dc:relation)
      - 349   : Résumé OAI (dc:description) avec $2

    Args:
        prepared     : Liste des notices UNIMARC (modifiée en place).
        match_result : Résultat du croisement (MatchResult).

    Returns:
        EnrichmentReport avec les statistiques d'enrichissement.
    """
    report = EnrichmentReport(n_matched=match_result.n_matched)

    for idx, oai_rec in match_result.matches.items():
        marc           = prepared[idx]
        changed_fields = []

        if _update_isbn(marc, oai_rec):
            changed_fields.append("010$a")
        if _update_856(marc, oai_rec):
            changed_fields.append("856")
        if _update_859(marc, oai_rec):
            changed_fields.append("859")
        if _update_349(marc, oai_rec):
            changed_fields.append("349")

        report.details.append(EnrichmentDetail(
            marc_index     = idx,
            ean            = marc.get_value("073", "a"),
            oai_id         = oai_rec.identifier,
            changed_fields = changed_fields,
        ))
        if changed_fields:
            report.n_enriched += 1
            _update_830(marc, oai_rec)

    return report


# ---------------------------------------------------------------------------
# Structures de résultat
# ---------------------------------------------------------------------------

@dataclass
class EnrichmentDetail:
    """Détail de l'enrichissement d'une notice."""
    marc_index:     int
    ean:            str
    oai_id:         str
    changed_fields: List[str]   # ex. ["010$a", "856", "859", "349"]


@dataclass
class EnrichmentReport:
    """Résultat global de l'enrichissement OAI."""
    n_matched:  int                     = 0
    n_enriched: int                     = 0
    details:    List[EnrichmentDetail]  = dc_field(default_factory=list)

    def summary_lines(self) -> List[str]:
        n_no_change = self.n_matched - self.n_enriched
        return [
            f"Notices appariées              : {self.n_matched}",
            f"Notices enrichies (≥1 champ)   : {self.n_enriched}",
            f"Notices sans modification OAI  : {n_no_change}",
            "",
            "Champs enrichis : 010$a (ISBN), 856 (accès), 859 (couverture), 349 (résumé OAI)",
        ]
