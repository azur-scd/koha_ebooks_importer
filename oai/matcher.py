#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oai/matcher.py — Croisement des notices UNIMARC et OAI-PMH
===========================================================
Ce module fait correspondre les notices UNIMARC préparées avec les notices
Dublin Core collectées via OAI-PMH.

Clé de croisement :
  - Côté UNIMARC  : EAN de la notice (073$a)
  - Côté OAI-PMH  : identifiant du header OAI, après suppression du suffixe
                    éventuel ajouté après un underscore.

Exemple de normalisation du header OAI :
  "9782763756776_2"  →  "9782763756776"
  "9782763756776"    →  "9782763756776"

Résultat du croisement :
  Un dict {index_unimarc: OaiRecord} associant chaque notice UNIMARC à la
  notice OAI correspondante. Seules les correspondances exactes 1-pour-1
  sont retenues (si plusieurs notices OAI partagent le même identifiant
  normalisé, aucune n'est associée — cas anormal signalé en log).

Pour étendre :
  - Ajouter d'autres clés de croisement (ISBN en 010$a, titre…) en cas
    d'EAN absent.
  - Intégrer le résultat dans les notices UNIMARC via une fonction
    d'enrichissement dans marc/transformations.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from marc.reader import MarcRecord
from oai.harvester import OaiRecord

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    """
    Résultat du croisement UNIMARC ↔ OAI-PMH.

    Attributes:
        matches        : Dict {index_unimarc (int) → OaiRecord} pour les
                         notices appariées avec succès.
        unmatched_marc : Liste des indices UNIMARC sans correspondance OAI.
        unmatched_oai  : Liste des identifiants OAI normalisés sans
                         correspondance UNIMARC.
        duplicate_oai  : Dict {ean → [identifiants OAI]} pour les EAN
                         présents plusieurs fois côté OAI (anomalie).
    """
    matches:        Dict[int, OaiRecord]      = field(default_factory=dict)
    unmatched_marc: List[int]                 = field(default_factory=list)
    unmatched_oai:  List[str]                 = field(default_factory=list)
    duplicate_oai:  Dict[str, List[str]]      = field(default_factory=dict)

    @property
    def n_matched(self) -> int:
        """Nombre de correspondances réussies."""
        return len(self.matches)

    @property
    def n_unmatched_marc(self) -> int:
        return len(self.unmatched_marc)

    @property
    def n_unmatched_oai(self) -> int:
        return len(self.unmatched_oai)


def _normalize_oai_id(identifier: str) -> str:
    """
    Normalise un identifiant OAI en supprimant le suffixe après underscore.

    Le préfixe OAI ("oai:serveur:") est également retiré si présent,
    pour ne garder que la partie significative (ex. l'EAN).

    Exemples :
      "9782763756776_2"              →  "9782763756776"
      "9782763756776"                →  "9782763756776"
      "oai:server.com:9782763756776" →  "9782763756776"
      "oai:server.com:9782763756776_2" →  "9782763756776"
    """
    # Supprimer le préfixe OAI "oai:<serveur>:" si présent
    if identifier.startswith("oai:"):
        parts = identifier.split(":", 2)
        identifier = parts[2] if len(parts) == 3 else identifier

    # Supprimer le suffixe après underscore
    if "_" in identifier:
        identifier = identifier.rsplit("_", 1)[0]

    return identifier.strip()


def build_oai_index(oai_records: List[OaiRecord]) -> tuple[Dict[str, OaiRecord], Dict[str, List[str]]]:
    """
    Construit un index {ean_normalise → OaiRecord} depuis la liste OAI.

    En cas de doublons (plusieurs notices OAI avec le même EAN normalisé),
    aucune des notices en doublon n'est indexée (on ne sait pas laquelle choisir)
    et elles sont signalées dans le second dict retourné.

    Args:
        oai_records : Liste complète des OaiRecord collectés.

    Returns:
        (index, duplicates) où :
          index      : {ean_normalise → OaiRecord} — correspondances uniques
          duplicates : {ean_normalise → [identifiants bruts]} — doublons
    """
    seen: Dict[str, List[OaiRecord]] = {}

    for rec in oai_records:
        # Ignorer les notices supprimées
        if rec.status == "deleted":
            continue
        key = _normalize_oai_id(rec.identifier)
        seen.setdefault(key, []).append(rec)

    index:      Dict[str, OaiRecord]      = {}
    duplicates: Dict[str, List[str]]      = {}

    for key, recs in seen.items():
        if len(recs) == 1:
            index[key] = recs[0]
        else:
            duplicates[key] = [r.identifier for r in recs]
            logger.warning(
                "EAN '%s' présent %d fois dans l'index OAI : %s",
                key, len(recs), duplicates[key],
            )

    return index, duplicates


def match_records(
    prepared: List[MarcRecord],
    oai_records: List[OaiRecord],
) -> MatchResult:
    """
    Croise les notices UNIMARC préparées avec les notices OAI-PMH.

    Algorithme :
      1. Construire un index OAI normalisé {ean → OaiRecord}.
      2. Pour chaque notice UNIMARC, extraire l'EAN (073$a).
      3. Chercher l'EAN dans l'index OAI.
      4. En cas de correspondance unique, l'enregistrer dans MatchResult.matches.

    Args:
        prepared    : Notices UNIMARC après préparation Koha.
        oai_records : Notices Dublin Core collectées via OAI-PMH.

    Returns:
        MatchResult avec les statistiques de croisement.
    """
    result = MatchResult()
    oai_index, result.duplicate_oai = build_oai_index(oai_records)

    matched_oai_keys = set()

    for idx, marc_rec in enumerate(prepared):
        ean = marc_rec.get_value("073", "a").strip()

        if not ean:
            # Pas d'EAN dans la notice UNIMARC
            result.unmatched_marc.append(idx)
            logger.debug("Notice UNIMARC #%d : EAN absent.", idx)
            continue

        oai_rec = oai_index.get(ean)

        if oai_rec is not None:
            result.matches[idx] = oai_rec
            matched_oai_keys.add(ean)
            logger.debug("Notice UNIMARC #%d (EAN=%s) → OAI %s", idx, ean, oai_rec.identifier)
        else:
            result.unmatched_marc.append(idx)
            logger.debug("Notice UNIMARC #%d (EAN=%s) : pas de correspondance OAI.", idx, ean)

    # Notices OAI non appariées
    result.unmatched_oai = [
        key for key in oai_index
        if key not in matched_oai_keys
    ]

    logger.info(
        "Croisement : %d/%d UNIMARC appariées, %d sans correspondance, "
        "%d OAI non appariées, %d doublons OAI.",
        result.n_matched, len(prepared),
        result.n_unmatched_marc,
        result.n_unmatched_oai,
        len(result.duplicate_oai),
    )

    return result
