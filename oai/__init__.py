# oai/__init__.py
"""Package oai — Collecte OAI-PMH et traitement Dublin Core."""
from .harvester import harvest_oai, deduplicate_oai, OaiRecord, OaiHarvestError, DeduplicationResult
from .matcher   import match_records, MatchResult
