#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marc/sudoc_enricher.py — Enrichissement Sudoc via le webservice ISBN2PPN
=========================================================================
Interroge le webservice du Sudoc (Système Universitaire de Documentation)
pour récupérer le PPN (Pica Production Number) correspondant à un ISBN.

URL du webservice :
  https://www.sudoc.fr/services/isbn2ppn/<isbn>&format=text/json

Comportement :
  - Pour chaque notice préparée, extrait l'ISBN (010$a).
  - Interroge le webservice Sudoc.
  - Si un PPN est trouvé (premier élément du bloc "result"), modifie le 801$b
    en y ajoutant " ; enrichi par PPN <ppn>".
  - Les erreurs HTTP 404/500 et les réponses sans bloc "result" sont traitées
    comme "PPN non trouvé" (sans erreur fatale).
  - Un délai configurable entre les requêtes évite de surcharger le serveur.

Pour étendre :
  - Ajouter d'autres webservices Sudoc (PPN2ISBN, etc.) dans ce module.
  - Ajouter une mise en cache pour éviter de réinterroger les mêmes ISBN.
"""

from __future__ import annotations
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from marc.reader import MarcRecord

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUDOC_ISBN2PPN_URL  = "https://www.sudoc.fr/services/isbn2ppn/{isbn}&format=text/json"
SUDOC_REQUEST_DELAY = 0.3   # Délai en secondes entre chaque requête (politesse)
SUDOC_TIMEOUT       = 8    # Timeout HTTP en secondes (réduit pour éviter les blocages)


# ---------------------------------------------------------------------------
# Structures de résultat
# ---------------------------------------------------------------------------

@dataclass
class SudocDetail:
    """Résultat de la recherche Sudoc pour une notice."""
    marc_index:      int
    isbn:            str
    ppn:             str          # PPN retenu (premier), ou chaîne vide
    status:          str          # "found_unique", "found_multiple", "not_found", "error", "no_isbn"
    error_msg:       str = ""     # Détail de l'erreur si status == "error"
    titre:           str = ""     # Titre de la notice (200$a) pour le log
    marc_fetched:    bool = False  # True si la notice MARC Sudoc a été récupérée
    tags_replaced:   List[str] = field(default_factory=list)  # Tags remplacés
    all_ppns:        List[str] = field(default_factory=list)  # Tous les PPN retournés
    ref_locale:      str = ""     # Référence bibliographique avant enrichissement
    ref_sudoc:       str = ""     # Référence bibliographique de la notice Sudoc
    doc_type_sudoc:  str = ""     # "print", "ebook" ou "unknown" pour la notice Sudoc retenue


@dataclass
class SudocEnrichmentReport:
    """Rapport global de l'enrichissement Sudoc."""
    details:         List[SudocDetail] = field(default_factory=list)
    n_total:         int = 0
    n_found:         int = 0
    n_not_found:     int = 0
    n_no_isbn:       int = 0
    n_error:         int = 0
    n_marc_fetched:  int = 0   # Notices dont la notice MARC Sudoc a été récupérée

    def summary_lines(self) -> List[str]:
        n_unique   = sum(1 for d in self.details if d.status == "found_unique")
        n_multiple = sum(1 for d in self.details if d.status == "found_multiple")
        return [
            f"Notices traitées                      : {self.n_total}",
            f"PPN trouvés (total)                   : {self.n_found}",
            f"  dont réponse unique                 : {n_unique}",
            f"  dont réponses multiples (1er retenu): {n_multiple}",
            f"  dont notices MARC récupérées        : {self.n_marc_fetched}",
            f"ISBN non trouvés dans le Sudoc        : {self.n_not_found}",
            f"Notices sans ISBN                     : {self.n_no_isbn}",
            f"Erreurs réseau / serveur              : {self.n_error}",
        ]


# ---------------------------------------------------------------------------
# Appel au webservice
# ---------------------------------------------------------------------------

def _fetch_ppn(isbn: str) -> tuple:
    """
    Interroge le webservice Sudoc ISBN2PPN.

    Retourne un tuple (ppn, status, all_ppns, error_msg) :
      ppn       : Premier PPN retenu, ou chaîne vide.
      status    : "found_unique"   — exactement 1 PPN dans "result"
                  "found_multiple" — plusieurs PPN dans "result" (1er retenu)
                  "not_found"      — réponse valide mais aucun PPN
                  "error"          — erreur réseau, HTTP ou JSON invalide
      all_ppns  : Liste de tous les PPN retournés (vide si non trouvé).
      error_msg : Description de l'erreur si status == "error", sinon "".
    """
    import urllib.parse
    url = SUDOC_ISBN2PPN_URL.format(isbn=urllib.parse.quote(isbn, safe="-"))

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "KohaEbookManager/1.0 (Sudoc enrichment)"},
        )
        with urllib.request.urlopen(req, timeout=SUDOC_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Le Sudoc retourne un 404 avec un message JSON explicatif
            # quand l'ISBN n'est pas connu. On tente de lire le message.
            try:
                body = exc.read().decode("utf-8", errors="replace")
                data = json.loads(body)
                msg  = data.get("sudoc", {}).get("error", "")
                return "", "not_found", [], msg or "ISBN non trouvé (404)"
            except Exception:
                return "", "not_found", [], "ISBN non trouvé (404)"
        # Toute autre erreur HTTP (500, 503…) est une vraie erreur technique
        return "", "error", [], f"HTTP {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        return "", "error", [], f"Erreur réseau : {exc.reason}"
    except Exception as exc:
        return "", "error", [], f"Erreur inattendue : {exc}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return "", "error", [], f"JSON invalide : {exc}"

    try:
        result = data["sudoc"]["query"]["result"]
    except (KeyError, TypeError):
        return "", "not_found", [], ""

    if not result:
        return "", "not_found", [], ""

    # Le Sudoc retourne "result" sous deux formes selon le nombre de PPN :
    #   - Un seul PPN  : {"ppn": "260136441"}          (dict)
    #   - Plusieurs PPN: [{"ppn": "260136441"}, ...]   (liste)
    if isinstance(result, dict):
        ppn = result.get("ppn", "").strip()
        if not ppn:
            return "", "not_found", [], ""
        return ppn, "found_unique", [ppn], ""

    # result est une liste
    all_ppns = [r.get("ppn", "").strip() for r in result if r.get("ppn", "").strip()]
    if not all_ppns:
        return "", "not_found", [], ""

    ppn    = all_ppns[0]
    status = "found_unique" if len(all_ppns) == 1 else "found_multiple"
    return ppn, status, all_ppns, ""


# ---------------------------------------------------------------------------
# Enrichissement de la zone 801$b
# ---------------------------------------------------------------------------

def _append_ppn_to_801(record: MarcRecord, ppn: str) -> None:
    """
    Ajoute la mention PPN dans le 801$b de la notice.

    Format ajouté : " ; enrichi par PPN <ppn>"
    Si la zone 801 ind2="2" n'existe pas, elle est créée avec juste la mention.
    """
    zone_801 = None
    for f in record.fields:
        if f.tag == "801" and f.ind2 == "2":
            zone_801 = f
            break

    if zone_801 is None:
        zone_801 = MarcField(tag="801", ind1=" ", ind2="2")
        record.add_field(zone_801)

    current_b = zone_801.get_subfield("b") or ""
    mention   = f" ; enrichi par PPN {ppn}"

    # Éviter les doublons si l'enrichissement est relancé
    if mention not in current_b:
        zone_801.set_subfield("b", current_b + mention)

# ---------------------------------------------------------------------------
# Enrichissement de la zone 830$a
# ---------------------------------------------------------------------------

def _append_ppn_to_830(record: MarcRecord, ppn: str) -> None:
    """
    Ajoute la mention PPN dans le 830$a de la notice.

    Format ajouté : " ; enrichi par PPN Sudoc <ppn>"
    Si la zone 830 n'existe pas, elle est créée avec juste la mention.
    """
    zone_830 = None
    for f in record.fields:
        if f.tag == "830":
            zone_830 = f
            break

    if zone_830 is None:
        zone_830 = MarcField(tag="830", ind1=" ", ind2=" ")
        record.add_field(zone_830)

    current_a = zone_830.get_subfield("a") or ""
    mention   = f" ; enrichi par PPN Sudoc {ppn}"

    # Éviter les doublons si l'enrichissement est relancé
    if mention not in current_a:
        zone_830.set_subfield("b", current_a + mention)



# ---------------------------------------------------------------------------
# Orchestrateur principal
# ---------------------------------------------------------------------------

def _build_ref(record: "MarcRecord") -> str:
    """
    Construit une référence bibliographique courte sur une ligne depuis un MarcRecord.

    Format : Auteur. Titre. Mention d'édition. — Éditeur, Date. ISBN.

    Chaque élément est omis s'il est absent.
    """
    titre    = record.get_value("200", "a").strip()
    auteur   = record.get_value("700", "a").strip()
    prenom   = record.get_value("700", "b").strip()
    edition  = record.get_value("205", "a").strip()
    editeur  = (record.get_value("214", "c") or record.get_value("210", "c")).strip()
    isbn     = record.get_value("010", "a").strip()
    date = ""
    for field in record.get_fields("214"):
        value = field.get_subfield("d")
        if value and value[0].strip():
            date = value[0].strip()
            break
    if not date:
        date = (record.get_value("210", "d") or "").strip()

    auteur_str  = f"{auteur}{', ' + prenom if prenom else ''}" if auteur else ""
    edition_str = f" ({edition})" if edition else ""
    pub_str     = ""
    if editeur and date:
        pub_str = f" — {editeur}, {date}"
    elif editeur:
        pub_str = f" — {editeur}"
    elif date:
        pub_str = f" — {date}"
    isbn_str = f". ISBN {isbn}" if isbn else ""

    parts = []
    if auteur_str:
        parts.append(auteur_str + ". ")
    parts.append(titre + edition_str + pub_str + isbn_str)
    return "".join(parts)


def enrich_with_sudoc(
    prepared:    List[MarcRecord],
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> SudocEnrichmentReport:
    """
    Enrichit les notices UNIMARC préparées avec les données Sudoc.

    Pour chaque notice :
      1. Extrait l'ISBN depuis 010$a.
      2. Interroge le webservice Sudoc ISBN2PPN pour obtenir le PPN.
      3. Si un PPN est trouvé :
         a. Ajoute la mention PPN dans 801$b.
         b. Récupère la notice MARC complète depuis le Sudoc (.xml, MARCXML sans namespace).
         c. Remplace les zones définies dans SUDOC_REPLACE_TAGS par celles
            de la notice Sudoc (la notice Sudoc fait autorité sur ces zones).

    Les notices sans ISBN ou sans PPN ne sont pas modifiées.
    Les erreurs réseau n'interrompent pas le traitement.

    Args:
        prepared    : Liste des notices UNIMARC (modifiées en place).
        progress_cb : Callback(n_done, n_total) appelé après chaque notice.

    Returns:
        SudocEnrichmentReport avec les statistiques et le détail notice par notice.
    """
    from marc.sudoc_marc_fetcher import (
        fetch_all_sudoc_records, select_best_record,
        replace_fields_from_sudoc, convert_215_to_307,
    )

    report   = SudocEnrichmentReport(n_total=len(prepared))
    n_total  = len(prepared)
    last_req = 0.0

    for idx, record in enumerate(prepared):
        isbn  = record.get_value("010", "a").strip()
        titre = record.get_value("200", "a").strip() or "(sans titre)"

        if not isbn:
            report.details.append(SudocDetail(
                marc_index=idx, isbn="", ppn="", status="no_isbn",
                titre=titre, ref_locale=_build_ref(record),
            ))
            report.n_no_isbn += 1
            if progress_cb:
                progress_cb(idx + 1, n_total)
            continue

        # Délai de politesse entre requêtes
        elapsed = time.monotonic() - last_req
        if elapsed < SUDOC_REQUEST_DELAY:
            time.sleep(SUDOC_REQUEST_DELAY - elapsed)

        ppn, status, all_ppns, error_msg = _fetch_ppn(isbn)
        last_req = time.monotonic()

        detail = SudocDetail(
            marc_index=idx, isbn=isbn, ppn=ppn,
            status=status, error_msg=error_msg,
            titre=titre, all_ppns=all_ppns,
            ref_locale=_build_ref(record),   # capturé pour tous les statuts
        )

        if status in ("found_unique", "found_multiple"):
            report.n_found += 1
            # Télécharger TOUTES les notices correspondant aux PPN retournés
            # et choisir la plus appropriée selon le type de document
            fetched = fetch_all_sudoc_records(all_ppns)
            best_ppn, sudoc_rec, doc_type = select_best_record(fetched)

            # Mettre à jour le PPN retenu si la sélection diffère du premier
            if best_ppn and best_ppn != ppn:
                detail.ppn = best_ppn

            if sudoc_rec is not None:
                print (detail.ppn)
                _append_ppn_to_801(record, detail.ppn)
                _append_ppn_to_830(record, detail.ppn)
                tags_replaced = replace_fields_from_sudoc(record, sudoc_rec)
                if convert_215_to_307(record, sudoc_rec):
                    tags_replaced.append("215→307")
                detail.marc_fetched   = True
                detail.tags_replaced  = tags_replaced
                detail.doc_type_sudoc = doc_type
                detail.ref_sudoc      = _build_ref(sudoc_rec)
                report.n_marc_fetched += 1

        elif status == "not_found":
            report.n_not_found += 1
        else:  # "error"
            report.n_error += 1

        report.details.append(detail)

        if progress_cb:
            progress_cb(idx + 1, n_total)

    return report


# ---------------------------------------------------------------------------
# Rapport téléchargeable
# ---------------------------------------------------------------------------

def generate_sudoc_report(
    report: SudocEnrichmentReport,
    path:   "str | Path",
) -> None:
    """
    Écrit le rapport d'enrichissement Sudoc dans un fichier texte UTF-8.
    Inclut pour chaque notice : titre, ISBN, PPN(s) retournés, zones remplacées,
    détail des erreurs.
    """
    import datetime
    from pathlib import Path

    now   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []

    def h1(title: str) -> None:
        lines.append("")
        lines.append("=" * 70)
        lines.append(f"  {title}")
        lines.append("=" * 70)

    lines.append("RAPPORT D'ENRICHISSEMENT SUDOC (ISBN2PPN + MARC)")
    lines.append(f"Généré le : {now}")
    lines.append("")

    h1("RÉSUMÉ")
    for l in report.summary_lines():
        lines.append(f"  {l}")

    # ── PPN uniques avec notice MARC ───────────────────────────────────
    unique_with_marc = [d for d in report.details
                        if d.status == "found_unique" and d.marc_fetched]
    h1(f"PPN UNIQUE — NOTICE MARC RÉCUPÉRÉE ({len(unique_with_marc)})")
    if unique_with_marc:
        for d in unique_with_marc:
            dtype_label = {"print": "livre imprimé", "ebook": "ebook"}.get(d.doc_type_sudoc, "")
            dtype_str   = f" [{dtype_label}]" if dtype_label else ""
            lines.append(f"  #{d.marc_index + 1:>4}  PPN {d.ppn}{dtype_str}")
            lines.append(f"    Avant  : {d.ref_locale}")
            lines.append(f"    Sudoc  : {d.ref_sudoc}{dtype_str}")
            lines.append(f"    Zones  : {', '.join(d.tags_replaced) or '(aucune)'}")
            lines.append("")
    else:
        lines.append("  (aucune)")

    # ── PPN multiples avec notice MARC ─────────────────────────────────
    multi_with_marc = [d for d in report.details
                       if d.status == "found_multiple" and d.marc_fetched]
    h1(f"PPN MULTIPLES — 1ER RETENU — NOTICE MARC RÉCUPÉRÉE ({len(multi_with_marc)})")
    if multi_with_marc:
        lines.append("  Plusieurs PPN retournés : le premier a été utilisé.")
        lines.append("")
        for d in multi_with_marc:
            dtype_label = {"print": "livre imprimé", "ebook": "ebook"}.get(d.doc_type_sudoc, "")
            dtype_str   = f" [{dtype_label}]" if dtype_label else ""
            others      = [p for p in d.all_ppns if p != d.ppn]
            lines.append(f"  #{d.marc_index + 1:>4}  PPN {d.ppn}{dtype_str}  (autres : {', '.join(others)})")
            lines.append(f"    Avant  : {d.ref_locale}")
            lines.append(f"    Sudoc  : {d.ref_sudoc}{dtype_str}")
            lines.append(f"    Zones  : {', '.join(d.tags_replaced) or '(aucune)'}")
            lines.append("")
    else:
        lines.append("  (aucune)")

    # ── PPN trouvés mais notice MARC indisponible ──────────────────────
    found_no_marc = [d for d in report.details
                     if d.status in ("found_unique","found_multiple") and not d.marc_fetched]
    h1(f"PPN TROUVÉS — NOTICE MARC INDISPONIBLE ({len(found_no_marc)})")
    if found_no_marc:
        lines.append("  PPN ajouté en 801$b mais zones non remplacées.")
        lines.append("")
        for d in found_no_marc:
            unique = "unique" if d.status == "found_unique" else "multiple"
            lines.append(f"  #{d.marc_index + 1:>4}  PPN {d.ppn} ({unique})")
            lines.append(f"    Avant  : {d.ref_locale}")
            if len(d.all_ppns) > 1:
                lines.append(f"    Autres PPN : {', '.join(d.all_ppns[1:])}")
            lines.append("")
    else:
        lines.append("  (aucun)")

    # ── ISBN non trouvés ───────────────────────────────────────────────
    not_found = [d for d in report.details if d.status == "not_found"]
    h1(f"ISBN NON TROUVÉS DANS LE SUDOC ({len(not_found)})")
    if not_found:
        for d in not_found:
            msg = f"  ({d.error_msg})" if d.error_msg else ""
            lines.append(f"  #{d.marc_index + 1:>4}  {d.ref_locale}{msg}")
    else:
        lines.append("  (aucun)")

    no_isbn = [d for d in report.details if d.status == "no_isbn"]
    h1(f"NOTICES SANS ISBN ({len(no_isbn)})")
    if no_isbn:
        for d in no_isbn:
            lines.append(f"  #{d.marc_index + 1:>4}  {d.ref_locale or d.titre}")
    else:
        lines.append("  (aucune)")

    errors = [d for d in report.details if d.status == "error"]
    h1(f"ERREURS RÉSEAU / SERVEUR ({len(errors)})")
    if errors:
        for d in errors:
            lines.append(f"  #{d.marc_index + 1:>4}  {d.ref_locale}")
            lines.append(f"    Erreur : {d.error_msg or '(inconnue)'}")
            lines.append("")
    else:
        lines.append("  (aucune)")

    lines.append("")
    lines.append("─" * 70)
    lines.append("Fin du rapport")

    Path(path).write_text("\n".join(lines), encoding="utf-8")
    import datetime
    from pathlib import Path

    now   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []

    def h1(title: str) -> None:
        lines.append("")
        lines.append("=" * 70)
        lines.append(f"  {title}")
        lines.append("=" * 70)

    lines.append("RAPPORT D'ENRICHISSEMENT SUDOC (ISBN2PPN + MARC)")
    lines.append(f"Généré le : {now}")

# Import nécessaire pour _fetch_ppn
import urllib.parse
from marc.reader import MarcField
