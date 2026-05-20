#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marc/exporters.py — Export des notices vers différents formats
=============================================================
Formats disponibles :
  - MARCXML (UTF-8) : `export_marcxml(records, path)`

Pour ajouter un nouveau format d'export :
  1. Créer une fonction `export_xxx(records, path, **kwargs)` dans ce module.
  2. L'exposer dans `EXPORTERS` (dictionnaire en bas de fichier) pour que
     l'interface puisse la proposer dynamiquement.

Note : le MARCXML produit inclut l'espace de noms MARC21slim en attribut de
la balise <collection>, mais les notices elles-mêmes n'en ont pas — ce qui
correspond au format attendu par Koha et au format retourné par le Sudoc.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

from marc.reader import MarcRecord


# ---------------------------------------------------------------------------
# MARCXML
# ---------------------------------------------------------------------------

_MARCXML_NS = "http://www.loc.gov/MARC21/slim"
_MARCXML_SCHEMA = (
    "http://www.loc.gov/MARC21/slim "
    "http://www.loc.gov/standards/marcxml/schema/MARC21slim.xsd"
)


def export_marcxml(records: List[MarcRecord], path: str | Path) -> None:
    """
    Exporte une liste de notices au format MARCXML (UTF-8).

    Le fichier produit est un document XML valide avec la déclaration
    d'encodage UTF-8 et l'espace de noms MARC21slim.

    Args:
        records : Liste de MarcRecord à exporter.
        path    : Chemin de destination du fichier .xml.

    Raises:
        IOError : En cas d'erreur d'écriture.
    """
    path = Path(path)

    # Élément racine <collection>
    collection = ET.Element("collection")
    collection.set("xmlns", _MARCXML_NS)
    collection.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    collection.set("xsi:schemaLocation", _MARCXML_SCHEMA)

    for record in records:
        record_el = ET.SubElement(collection, "record")

        # --- Leader ---
        if record.leader:
            leader_el = ET.SubElement(record_el, "leader")
            leader_el.text = record.leader

        # --- Champs ---
        for field in record.fields:
            if field.data:
                # Zone de contrôle (001-009)
                ctrl = ET.SubElement(record_el, "controlfield")
                ctrl.set("tag", field.tag)
                ctrl.text = field.data
            else:
                # Zone ordinaire avec indicateurs et sous-zones
                data_field = ET.SubElement(record_el, "datafield")
                data_field.set("tag",  field.tag)
                data_field.set("ind1", field.ind1 if field.ind1 else " ")
                data_field.set("ind2", field.ind2 if field.ind2 else " ")

                for sf in field.subfields:
                    sf_el = ET.SubElement(data_field, "subfield")
                    sf_el.set("code", sf.code)
                    sf_el.text = sf.value

    # Indentation (Python 3.9+)
    try:
        ET.indent(collection, space="  ")
    except AttributeError:
        pass   # Python < 3.9 : pas d'indentation automatique

    tree = ET.ElementTree(collection)
    tree.write(
        str(path),
        encoding="utf-8",
        xml_declaration=True,
    )


# ---------------------------------------------------------------------------
# Registre des exporteurs (pour évolution future de l'interface)
# ---------------------------------------------------------------------------
# Chaque entrée : (label_menu, extension_par_défaut, fonction)
# L'interface peut itérer sur ce dict pour proposer les formats disponibles.

EXPORTERS = {
    "marcxml": (
        "MARCXML (UTF-8)",    # Libellé affiché dans l'interface
        ".xml",               # Extension par défaut
        export_marcxml,       # Fonction d'export
    ),
    # Exemples de futurs formats :
    # "iso2709": ("ISO 2709 (UTF-8)", ".mrc", export_iso2709),
    # "csv":     ("CSV tabulaire",    ".csv", export_csv),
}
