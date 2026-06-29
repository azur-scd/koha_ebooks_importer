#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py — Paramètres configurables de l'application
======================================================
Modifier ce fichier pour adapter l'outil sans toucher au code applicatif.

Colonnes des tableaux
---------------------
Chaque colonne est un dict avec :
  "label"     : str   — en-tête affiché
  "extract"   : callable(MarcRecord) -> str — valeur à afficher
  "width"     : int   — largeur en pixels
  "stretch"   : bool  — la colonne s'étire-t-elle ?
"""

from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from marc.reader import MarcRecord

# ---------------------------------------------------------------------------
# Identité de l'application
# ---------------------------------------------------------------------------
APP_TITLE   = "Signalement ebooks biblioondemand"
APP_VERSION = "1.0.3"

WINDOW_MIN_W = 1100
WINDOW_MIN_H = 650

# ---------------------------------------------------------------------------
# Paramètres des zones Koha
# ---------------------------------------------------------------------------
ZONE_099 = {
    "t": "LIVRE_EL",
    "u": "en ligne",
    "y": "biblioondemand",
    "z": "biblioondemand",
}

ZONE_995 = {
    "b": "BIBEL",
    "c": "BIBEL",
    "d": "LELEC",
    "k": "Accès en ligne",
    "8": "LELEC",
    "r": "EL",
    "o": "0",
}

BARCODE_PREFIX = "BOD"

ZONE_801 = {
    "a": "FR",
    "b": "biblioondemand",
}

ZONE_830_UNIMARC = {
    "a": "Notice UNIMARC biblioondemand",
}

ZONE_830_UNIMARC_ET_DC = {
    "a": "Notice UNIMARC biblioondemand + enrichissement par Dublin Core (EAN, liens, résumé)",
}

ZONE_830_UNIMARC_ET_DC_ET_SUDOC_PAPIER = {
    "a": "Notice UNIMARC biblioondemand + enrichissement par Dublin Core (EAN, liens, résumé) + enrichissement Sudoc (notice papier PPN {ppn})",
}

ZONE_830_UNIMARC_ET_DC_ET_SUDOC_EBOOK = {
    "a": "Notice UNIMARC biblioondemand + enrichissement par Dublin Core (EAN, liens, résumé) + enrichissement Sudoc (notice ebook PPN {ppn})",
}

# ---------------------------------------------------------------------------
# Paramètres SRU Koha
# ---------------------------------------------------------------------------
# URL de base du serveur SRU Koha (sans paramètres)
KOHA_SRU_BASE_URL = "https://catalogue-bu-univ-cotedazur.biblibre.fr/biblios"
# URL de base du serveur SRU Koha test (sans paramètres)
KOHA_TEST_SRU_BASE_URL = "https://catalogue-bu-cotedazur-koha.test.biblibre.eu/"
 
# Critères de filtrage des notices Koha récupérées
KOHA_FILTER_099T = "LIVRE_EL"        # 099$t : type de document attendu
KOHA_FILTER_099Z = "biblioondemand"  # 099$z : source attendue

# ---------------------------------------------------------------------------
# Paramètres OAI-PMH
# ---------------------------------------------------------------------------
# URL de base du serveur OAI. Doit se terminer par "verb=" ou être une URL
# de base sans paramètres (les paramètres sont ajoutés par le harvester).
OAI_BASE_URL        = "https://univ-cotedazur.biblioondemand.com/oaiserver.ashx?verb="

# Set OAI à collecter
OAI_SET             = "UNSA_ALL"

# Format de métadonnées OAI (Dublin Core)
OAI_METADATA_PREFIX = "oai_dc"

# ---------------------------------------------------------------------------
# Extracteurs réutilisables
# ---------------------------------------------------------------------------

def _all_856_urls(record: "MarcRecord") -> str:
    """Toutes les URL de toutes les zones 856, séparées par ' | '."""
    urls = []
    for f in record.fields:
        if f.tag == "856":
            u = f.get_subfield("u")
            if u:
                urls.append(u)
    return " | ".join(urls)


def _856_doc_url(record: "MarcRecord") -> str:
    """URL du 1er 856 d'accès au document (sans $x vignette)."""
    for f in record.fields:
        if f.tag == "856":
            x = (f.get_subfield("x") or "").strip().lower()
            if x != "vignette":
                return f.get_subfield("u") or ""
    return ""


def _856_cover_url(record: "MarcRecord") -> str:
    """URL du 1er 856 de couverture ($x = vignette)."""
    for f in record.fields:
        if f.tag == "856":
            x = (f.get_subfield("x") or "").strip().lower()
            if x == "vignette":
                return f.get_subfield("u") or ""
    return ""

def _859_cover_url(record: "MarcRecord") -> str:
    """URL du 1er 859 de couverture."""
    for f in record.fields:
        if f.tag == "859":
            return f.get_subfield("u") or ""
    return ""


def _first_non_empty_214_d(record: "MarcRecord") -> str:
    """Première valeur non vide de 214$d."""
    for field in record.get_fields("214"):
        value = field.get_subfield("d")
        if value and value.strip():
            return value.strip()
    return ""

# ---------------------------------------------------------------------------
# Colonnes du tableau "Données source"
# ---------------------------------------------------------------------------
SOURCE_COLUMNS = [
    {
        "label":   "Identifiant (001)",
        "extract": lambda r: r.get_value("001"),
        "width":   130, "stretch": False,
    },
    {
        "label":   "EAN (073$a)",
        "extract": lambda r: r.get_value("073", "a"),
        "width":   120, "stretch": False,
    },
    {
        "label":   "ISBN (010$a)",
        "extract": lambda r: r.get_value("010", "a"),
        "width":   120, "stretch": False,
    },
    {
        "label":   "Titre (220$a)",
        "extract": lambda r: r.get_value("200", "a"),
        "width":   220, "stretch": True,
    },
    {
        "label":   "Éditeur (210/214$c)",
        "extract": lambda r: r.get_value("210", "c") or r.get_value("214", "c"),
        "width":   130, "stretch": False,
    },
    {
        "label":   "Date (210/214$d)",
        "extract": lambda r: r.get_value("210", "d") or r.get_value("214", "d"),
        "width":    70, "stretch": False,
    },
    {
        "label":   "URL doc (856)",
        "extract": _856_doc_url,
        "width":   200, "stretch": True,
    },
    {
        "label":   "URL couv (856 vignette)",
        "extract": _856_cover_url,
        "width":   200, "stretch": True,
    },
]

# ---------------------------------------------------------------------------
# Colonnes du tableau "Données préparées"
# ---------------------------------------------------------------------------
PREPARED_COLUMNS = [
    {
        "label":   "Identifiant (001)",
        "extract": lambda r: r.get_value("001"),
        "width":   130, "stretch": False,
    },
    {
        "label":   "EAN (073$a)",
        "extract": lambda r: r.get_value("073", "a"),
        "width":   120, "stretch": False,
    },
    {
        "label":   "ISBN (010$a)",
        "extract": lambda r: r.get_value("010", "a"),
        "width":   120, "stretch": False,
    },
    {
        "label":   "Titre (200$a)",
        "extract": lambda r: r.get_value("200", "a"),
        "width":   220, "stretch": True,
    },
    {
        "label":   "Mention d'édition (205$a)",
        "extract": lambda r: r.get_value("205", "a"),
        "width":   140, "stretch": False,
    },
    {
        "label":   "Éditeur (214$c)",
        "extract": lambda r: r.get_value("214", "c"),
        "width":   130, "stretch": False,
    },
    {
        "label":   "Date (214$d)",
        "extract": lambda r: r.get_value("214", "d"),
        "width":    70, "stretch": False,
    },
    {
        "label":   "URL doc (856)",
        "extract": _856_doc_url,
        "width":   200, "stretch": True,
    },
    {
        "label":   "URL couv (859)",
        "extract": _859_cover_url,
        "width":   200, "stretch": True,
    },
    {
        "label":   "Note d'accès (371$a)",
        "extract": lambda r: r.get_value("371", "a"),
        "width":   250, "stretch": True,
    },
    {
        "label":   "Source catalogage (801$b)",
        "extract": lambda r: r.get_value("801", "b"),
        "width":   200, "stretch": False,
    },
    {
        "label":   "Note catalogage (830$a)",
        "extract": lambda r: r.get_value("830", "a"),
        "width":   300, "stretch": False,
    },
]


# ---------------------------------------------------------------------------
# Colonnes du tableau "Enrichissement SUDOC"
# ---------------------------------------------------------------------------
SUDOC_COLUMNS = [
    {
        "label":   "Identifiant (001)",
        "extract": lambda r: r.get_value("001"),
        "width":   130, "stretch": False,
    },
    {
        "label":   "EAN (073$a)",
        "extract": lambda r: r.get_value("073", "a"),
        "width":   120, "stretch": False,
    },
    {
        "label":   "ISBN (010$a)",
        "extract": lambda r: r.get_value("010", "a"),
        "width":   120, "stretch": False,
    },
    {
        "label":   "Titre (200$a)",
        "extract": lambda r: r.get_value("200", "a"),
        "width":   220, "stretch": True,
    },
    {
        "label":   "Mention d'édition (205$a)",
        "extract": lambda r: r.get_value("205", "a"),
        "width":   140, "stretch": False,
    },
    {
        "label":   "Éditeur (214$c)",
        "extract": lambda r: r.get_value("214", "c"),
        "width":   130, "stretch": False,
    },
    {
        "label":   "Date (214$d)",
        "extract": _first_non_empty_214_d,
        "width":    70, "stretch": False,
    },
    {
        "label":   "URL doc (856)",
        "extract": _856_doc_url,
        "width":   200, "stretch": True,
    },
    {
        "label":   "URL couv (859)",
        "extract": _859_cover_url,
        "width":   200, "stretch": True,
    },
    {
        "label":   "Note d'accès (371$a)",
        "extract": lambda r: r.get_value("371", "a"),
        "width":   250, "stretch": True,
    },
    {
        "label":   "Source catalogage (801$b)",
        "extract": lambda r: r.get_value("801", "b"),
        "width":   200, "stretch": False,
    },
    {
        "label":   "Note catalogage (830$a)",
        "extract": lambda r: r.get_value("830", "a"),
        "width":   300, "stretch": False,
    },
]

# ---------------------------------------------------------------------------
# Colonnes du tableau "Données OAI-PMH" (Dublin Core)
# ---------------------------------------------------------------------------
# Les extracteurs reçoivent un OaiRecord.
# - record.identifier : identifiant OAI du header (ex. oai:server:12345)
# - record.first(element) : première valeur d'un champ DC
# - record.get(element)   : toutes les valeurs (liste)
# Pour les champs multi-valeurs (identifier, relation), on joint par " | ".
OAI_COLUMNS = [
    {
        "label":   "OAI Identifier",
        "extract": lambda r: r.identifier,
        "width":   200, "stretch": False,
    },
    {
        "label":   "Titre (dc:title)",
        "extract": lambda r: r.first("title"),
        "width":   220, "stretch": True,
    },
    {
        "label":   "Type (dc:type)",
        "extract": lambda r: r.first("type"),
        "width":    90, "stretch": False,
    },
    {
        "label":   "Éditeur (dc:publisher)",
        "extract": lambda r: r.first("publisher"),
        "width":   130, "stretch": False,
    },
    {
        "label":   "Date (dc:date)",
        "extract": lambda r: r.first("date"),
        "width":    70, "stretch": False,
    },
    {
        "label":   "Langue (dc:language)",
        "extract": lambda r: r.first("language"),
        "width":    80, "stretch": False,
    },
    {
        "label":   "Relation (dc:relation)",
        "extract": lambda r: " | ".join(r.get("relation")),
        "width":   180, "stretch": False,
    },
    {
        "label":   "Identifier DC (dc:identifier)",
        "extract": lambda r: " | ".join(r.get("identifier")),
        "width":   220, "stretch": True,
    },
    {
        "label":   "ISBN (dc:isbn)",
        "extract": lambda r: " | ".join(r.get("isbn")),
        "width":   130, "stretch": False,
    },
]

# ---------------------------------------------------------------------------
# Alias pour compatibilité (utilisé si du code importe encore PREVIEW_COLUMNS)
# ---------------------------------------------------------------------------
PREVIEW_COLUMNS = SOURCE_COLUMNS

# ---------------------------------------------------------------------------
# Couleurs du thème
# ---------------------------------------------------------------------------
COLORS = {
    "bg":            "#f5f4f0",
    "sidebar":       "#2b3a4a",
    "sidebar_text":  "#e8e0d0",
    "accent":        "#c0392b",
    "accent_hover":  "#a93226",
    "btn_primary":   "#2b3a4a",
    "btn_secondary": "#e8e0d0",
    "table_odd":     "#ffffff",
    "table_even":    "#f0ede6",
    "table_sel":     "#d4e6f1",
    "border":        "#ccc4b4",
    "text":          "#1a1a1a",
    "text_muted":    "#666655",
    "success":       "#27ae60",
    "warning":       "#e67e22",
    "error":         "#c0392b",
    "info":          "#2980b9",
    "idle":          "#888877",
}

# ---------------------------------------------------------------------------
# Messages de l'interface
# ---------------------------------------------------------------------------
MESSAGES = {
    "import_success":  "Fichier importé : {n} notice(s) chargée(s).",
    "no_file":         "Aucun fichier sélectionné.",
    "no_selection":    "Veuillez sélectionner au moins une notice.",
    "prepare_success": "{n} notice(s) préparée(s) avec succès.",
    "export_success":  "Fichier exporté : {path}",
    "export_error":    "Erreur lors de l'export : {err}",
    "parse_error":     "Erreur de lecture du fichier : {err}",
    "no_prepared":     "Aucune notice préparée. Lancez d'abord la préparation.",
    "reset_done":      "Application réinitialisée.",
}
