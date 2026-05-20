# marc/__init__.py
"""
Package marc — Couche de traitement MARC
=========================================
Modules :
  reader.py             : Parseur ISO2709 → MarcRecord (UTF-8 correct)
  transformations.py    : Enrichissements Koha (099, 995, 801, 039, etc.)
  exporters.py          : Export MARCXML
  deduplicator.py       : Dédoublonnage à l'import (001, EAN, URL)
  sudoc_enricher.py     : Enrichissement via webservice ISBN2PPN du Sudoc
  sudoc_marc_fetcher.py : Récupération et intégration des notices MARCXML Sudoc
"""
from marc.reader import MarcRecord, MarcField, SubField, parse_iso2709
from marc.exporters import export_marcxml, EXPORTERS

__all__ = [
    "MarcRecord", "MarcField", "SubField", "parse_iso2709",
    "export_marcxml", "EXPORTERS",
]
