#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oai/harvester.py — Collecte OAI-PMH en Dublin Core (oai_dc)
=============================================================
Ce module fournit :

  - `harvest_oai()`        : collecte complète d'un entrepôt OAI-PMH, sans
                             filtre de set par défaut (set_spec=None), en gérant
                             automatiquement la pagination via resumptionToken.
  - `deduplicate_oai()`    : dédoublonnage par identifiant de header (première
                             occurrence conservée).
  - `OaiRecord`            : représentation d'une notice Dublin Core récupérée.
  - `OaiHarvestError`      : exception levée en cas d'erreur réseau ou OAI.
  - `DeduplicationResult`  : résultat du dédoublonnage avec statistiques.

Protocole OAI-PMH utilisé :
  - Verbe : ListRecords
  - Format : oai_dc (Dublin Core simple)
  - Itération : la réponse contient un <resumptionToken> tant qu'il reste
    des pages. On le réutilise jusqu'à l'obtenir vide ou absent.
  - Sans set_spec : collecte tout l'entrepôt (paramètre "set" omis de l'URL).

Paramètres configurables dans config.py :
  OAI_BASE_URL, OAI_SET (non utilisé par défaut), OAI_METADATA_PREFIX

Pour étendre :
  - Ajouter d'autres verbes (ListIdentifiers, GetRecord…) comme fonctions
    supplémentaires dans ce module.
  - Ajouter un filtre par date via les paramètres `from_date` / `until_date`.
"""

from __future__ import annotations

import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from xml.etree import ElementTree as ET

from config import OAI_BASE_URL, OAI_SET, OAI_METADATA_PREFIX

# Espace de noms XML Dublin Core et OAI
_NS = {
    "oai":  "http://www.openarchives.org/OAI/2.0/",
    "dc":   "http://purl.org/dc/elements/1.1/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
}

# Délai d'attente réseau en secondes
_HTTP_TIMEOUT = 30


class OaiHarvestError(Exception):
    """Erreur levée en cas d'échec de la collecte OAI-PMH."""
    pass


@dataclass
class OaiRecord:
    """
    Notice Dublin Core récupérée via OAI-PMH.

    Attributs :
        identifier  : Identifiant OAI unique (ex. oai:xxx:yyy)
        status      : "deleted" si la notice est supprimée, sinon ""
        dc          : Dict des éléments Dublin Core (clé = nom d'élément,
                      valeur = liste de chaînes). Ex : {"title": ["Mon titre"],
                      "identifier": ["ISBN:...", "https://..."]}
    """
    identifier: str
    status:     str = ""
    dc:         dict = field(default_factory=dict)

    def get(self, element: str) -> List[str]:
        """Retourne la liste de valeurs pour un élément DC donné."""
        return self.dc.get(element, [])

    def first(self, element: str) -> str:
        """Retourne la première valeur d'un élément DC, ou chaîne vide."""
        vals = self.dc.get(element, [])
        return vals[0] if vals else ""

    def __repr__(self) -> str:
        return f"<OaiRecord {self.identifier!r} dc={list(self.dc.keys())}>"


def harvest_oai(
    base_url:        str = OAI_BASE_URL,
    set_spec:        Optional[str] = None,
    metadata_prefix: str = OAI_METADATA_PREFIX,
    from_date:       Optional[str] = None,
    until_date:      Optional[str] = None,
    progress_cb:     Optional[Callable[[int, Optional[int]], None]] = None,
) -> List[OaiRecord]:
    """
    Collecte toutes les notices d'un entrepôt OAI-PMH via le verbe ListRecords.

    Itère automatiquement sur toutes les pages grâce au resumptionToken
    retourné par le serveur OAI. S'arrête quand le token est absent ou vide.

    Args:
        base_url        : URL de base du serveur OAI.
        set_spec        : Identifiant du set à collecter (ex. "UNSA_ALL").
                          Si None ou chaîne vide, collecte tout l'entrepôt
                          sans filtre de set (paramètre "set" omis de l'URL).
        metadata_prefix : Format de métadonnées (ex. "oai_dc").
        from_date       : Date de début au format AAAA-MM-JJ (optionnel).
        until_date      : Date de fin au format AAAA-MM-JJ (optionnel).
        progress_cb     : Callback de progression appelé après chaque page.
                          Signature : (nb_collectes, total_ou_None).

    Returns:
        Liste de OaiRecord bruts (avec doublons éventuels).
        Utiliser deduplicate_oai() pour dédoublonner avant usage.

    Raises:
        OaiHarvestError : Erreur réseau, XML invalide, ou erreur OAI.
    """
    records: List[OaiRecord] = []
    resumption_token: Optional[str] = None

    while True:
        url = _build_url(
            base_url         = base_url,
            set_spec         = set_spec or "",
            metadata_prefix  = metadata_prefix,
            from_date        = from_date,
            until_date       = until_date,
            resumption_token = resumption_token,
        )

        xml_bytes = _fetch(url)
        root      = _parse_xml(xml_bytes)
        _check_oai_errors(root)

        new_records, total = _extract_records(root)
        records.extend(new_records)

        if progress_cb:
            progress_cb(len(records), total)

        resumption_token = _get_resumption_token(root)
        if not resumption_token:
            break

    return records


@dataclass
class DeduplicationResult:
    """
    Résultat du dédoublonnage d'une liste de OaiRecord.

    Attributes:
        records      : Liste dédoublonnée (première occurrence conservée).
        n_raw        : Nombre de notices brutes avant dédoublonnage.
        n_duplicates : Nombre de notices supprimées (doublons).
        duplicate_ids: Liste des identifiants qui avaient des doublons.
    """
    records:       List[OaiRecord]
    n_raw:         int
    n_duplicates:  int
    duplicate_ids: List[str]


def deduplicate_oai(records: List[OaiRecord]) -> DeduplicationResult:
    """
    Dédoublonne une liste de OaiRecord en se basant sur l'identifiant du header.

    En cas de doublons, la première occurrence rencontrée est conservée.
    L'ordre global de la liste est préservé.

    Args:
        records : Liste brute de OaiRecord (telle que retournée par harvest_oai).

    Returns:
        DeduplicationResult avec la liste dédoublonnée et les statistiques.
    """
    seen:          dict[str, int] = {}   # identifiant → index première occurrence
    unique:        List[OaiRecord] = []
    duplicate_ids: List[str] = []

    for rec in records:
        ident = rec.identifier
        if ident not in seen:
            seen[ident] = len(unique)
            unique.append(rec)
        else:
            if ident not in duplicate_ids:
                duplicate_ids.append(ident)

    n_raw        = len(records)
    n_duplicates = n_raw - len(unique)

    return DeduplicationResult(
        records       = unique,
        n_raw         = n_raw,
        n_duplicates  = n_duplicates,
        duplicate_ids = duplicate_ids,
    )


# ---------------------------------------------------------------------------
# Fonctions internes
# ---------------------------------------------------------------------------

def _build_url(
    base_url:         str,
    set_spec:         str,
    metadata_prefix:  str,
    from_date:        Optional[str],
    until_date:       Optional[str],
    resumption_token: Optional[str],
) -> str:
    """
    Construit l'URL de la requête OAI-PMH.

    Quand un resumptionToken est présent, seuls verb + token sont envoyés
    (les autres paramètres sont ignorés par le serveur, conformément à la spec).
    """
    if resumption_token:
        params = {
            "verb":             "ListRecords",
            "resumptionToken":  resumption_token,
        }
    else:
        params = {
            "verb":           "ListRecords",
            "metadataPrefix": metadata_prefix,
        }
        # set_spec vide = pas de filtre de set (tout l'entrepôt)
        if set_spec:
            params["set"] = set_spec
        if from_date:
            params["from"]  = from_date
        if until_date:
            params["until"] = until_date

    # base_url se termine déjà par "verb=", on enlève ce suffixe si présent
    # pour reconstruire proprement avec urllib
    clean_base = base_url.rstrip("?&")
    if clean_base.endswith("verb="):
        clean_base = clean_base[: -len("verb=")]
    # Supprimer le "?" ou "&" terminal
    clean_base = clean_base.rstrip("?&")

    return clean_base + "?" + urllib.parse.urlencode(params)


def _fetch(url: str) -> bytes:
    """Télécharge l'URL et retourne le contenu en bytes."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "KohaEbookManager/1.0 (OAI-PMH harvester)"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return resp.read()
    except Exception as exc:
        raise OaiHarvestError(f"Erreur réseau lors de la requête OAI : {exc}") from exc


def _parse_xml(data: bytes) -> ET.Element:
    """Parse les bytes XML et retourne l'élément racine."""
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise OaiHarvestError(f"Réponse OAI invalide (XML malformé) : {exc}") from exc


def _check_oai_errors(root: ET.Element) -> None:
    """Lève OaiHarvestError si la réponse contient une balise <error>."""
    errors = root.findall("oai:error", _NS)
    if errors:
        msgs = [f"{e.get('code', '?')}: {e.text or ''}" for e in errors]
        raise OaiHarvestError("Erreur OAI-PMH : " + " | ".join(msgs))


def _extract_records(root: ET.Element) -> tuple[List[OaiRecord], Optional[int]]:
    """
    Extrait les OaiRecord depuis les balises <record> de la réponse.

    Returns:
        (liste_de_records, total) où total est le nombre total annoncé
        par le serveur dans resumptionToken@completeListSize, ou None.
    """
    records: List[OaiRecord] = []

    # Total annoncé par le serveur (peut être absent)
    total: Optional[int] = None
    token_el = root.find(".//oai:resumptionToken", _NS)
    if token_el is not None:
        try:
            total = int(token_el.get("completeListSize", ""))
        except (ValueError, TypeError):
            pass

    for rec_el in root.findall(".//oai:record", _NS):
        oai_rec = _parse_record(rec_el)
        records.append(oai_rec)

    return records, total


def _parse_record(rec_el: ET.Element) -> OaiRecord:
    """Parse un élément <record> OAI en OaiRecord."""
    # Identifiant
    header   = rec_el.find("oai:header", _NS)
    id_el    = header.find("oai:identifier", _NS) if header is not None else None
    ident    = id_el.text.strip() if id_el is not None and id_el.text else ""

    # Statut (deleted ?)
    status = (header.get("status", "") if header is not None else "")

    # Métadonnées Dublin Core
    dc: dict[str, list[str]] = {}
    metadata_el = rec_el.find("oai:metadata", _NS)
    if metadata_el is not None:
        dc_container = metadata_el.find("oai_dc:dc", _NS)
        if dc_container is not None:
            for child in dc_container:
                # Nom de l'élément : "title", "creator", "identifier"…
                local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                value = (child.text or "").strip()
                if value:
                    dc.setdefault(local, []).append(value)

    return OaiRecord(identifier=ident, status=status, dc=dc)


def _get_resumption_token(root: ET.Element) -> Optional[str]:
    """
    Retourne le contenu du resumptionToken (str non vide), ou None si absent/vide.
    Un token vide (<resumptionToken/>) signifie que c'est la dernière page.
    """
    el = root.find(".//oai:resumptionToken", _NS)
    if el is None:
        return None
    token = (el.text or "").strip()
    return token if token else None
