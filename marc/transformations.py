#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marc/transformations.py — Enrichissement et nettoyage des notices UNIMARC importées
==============================================================================
Ce module contient toutes les transformations appliquées aux notices MARC
en vue de leur import dans Koha et avant les éventuels enrichissement par 
la notice en Dublin Core et la notice Sudoc.

Chaque transformation est une fonction autonome qui reçoit un MarcRecord
(et éventuellement des paramètres) et le modifie en place.

La fonction principale `prepare_record_for_koha()` orchestre l'ensemble
des transformations dans le bon ordre.

Pour ajouter un nouveau traitement :
  1. Ecrire une fonction `transform_xxx(record, ...)` dans ce fichier.
  2. L'appeler depuis `prepare_record_for_koha()` dans la section correspondante.
  3. Si le traitement est optionnel, l'activer via un parametre de config.py.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import List, Optional

from config import ZONE_099, ZONE_995, ZONE_801, ZONE_830_UNIMARC, BARCODE_PREFIX
from marc.reader import MarcField, MarcRecord, SubField


# ===========================================================================
# Constantes et expressions regulieres (niveau module pour eviter
# la recompilation a chaque appel)
# ===========================================================================

# Tags de zones codees (contenu positionnel) : on n'y condense pas les espaces.
# Inclut tous les 0XX et quelques zones specifiques.
_CODED_TAGS = {"100", "105", "106", "135"}

def _is_coded_field(tag: str) -> bool:
    """Retourne True si le champ est une zone codee (positionnelle)."""
    return tag.startswith("0") or tag in _CODED_TAGS


# Regex : annee a 4 chiffres (1000-2099)
_RE_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")

# ---------------------------------------------------------------------------
# Regex pour la detection des mentions d'edition dans 200$a
# ---------------------------------------------------------------------------
# Noyau commun : reconnaît 'éd.' 'éd' 'édition' 'ed.' 'ed' 'edition'
# ainsi que les suffixes ordinaux e/è/é/ème/eme/st/nd/rd/th.
# IMPORTANT : les caractères accentués sont écrits directement (pas en \uXXXX)
# pour que re.IGNORECASE les reconnaisse correctement.
_EDITION_CORE = (
    r"\d+"                           # numéro d'ordre (1, 2, 10...)
    "(?:e|\u00e8|\u00e9|eme|\u00e8me|\u00e9me|st|nd|rd|th)?"  # suffixe ordinal
    r"\s+"                           # espace obligatoire
    "(?:\u00e9d(?:ition)?|ed(?:ition)?)"  # éd/éd./édition/ed/ed./edition
    r"\.?"                           # point final optionnel
)

# Cas 1 : mention en FIN de titre (tiret optionnel avant)
# Ex : 'Mon titre - 4e éd.'  |  'Mon titre 2e édition'
_RE_EDITION_END = re.compile(
    r"\s*(?:-\s*)?"    # tiret optionnel
    + _EDITION_CORE
    + r"$",              # fin de chaîne
    re.IGNORECASE,
)

# Cas 2 : mention INTERCALÉE entre titre et sous-titre
# Ex : 'Titre - 4e éd. - Sous-titre'
# Groupe 1 = mention d'édition, groupe 2 = sous-titre
_RE_EDITION_MIDDLE = re.compile(
    r"\s*-\s*"         # tiret gauche obligatoire
    r"("                 # groupe 1 : mention
    + _EDITION_CORE
    + r")"
    r"\s*-\s*"         # tiret droit obligatoire
    r"(.+)"              # groupe 2 : sous-titre
    r"$",                # fin de chaîne
    re.IGNORECASE,
)

# Suffixes commerciaux à supprimer de la fin du 200$a (insensible à la casse).
# Ordonnés du plus long au plus court pour éviter les correspondances partielles.
_RE_EBOOK_SUFFIX = re.compile(
    r"\s*-?\s*(?:ebook\s+epub|ebook|epub)\s*$",
    re.IGNORECASE,
)

# Regex : balises HTML
_RE_HTML_TAG = re.compile(r"<[^>]+>")

# Entites HTML frequentes dans les donnees bibliographiques
_HTML_ENTITIES = {
    "&amp;":  "&",
    "&lt;":   "<",
    "&gt;":   ">",
    "&quot;": '"',
    "&apos;": "'",
    "&nbsp;": " ",
}

# Sauts de ligne et tabulations
_RE_WHITESPACE = re.compile(r"[\r\n\t]+")

# Espaces multiples consecutifs
_RE_MULTI_SPACES = re.compile(r"  +")

# Caracteres NBSP et apparentes (U+00A0, U+202F)
_NBSP_CHARS = "\u00a0\u202f"


# ===========================================================================
# 1. Nettoyage des donnees
# ===========================================================================

def clean_subfields(record: MarcRecord) -> None:
    """
    Nettoie le contenu de toutes les sous-zones et zones de controle :

      1. Remplace les NBSP (U+00A0, U+202F) par un espace ordinaire.
      2. Convertit les entites HTML courantes (&amp; -> &, &nbsp; -> espace...).
      3. Supprime les balises HTML (<br/>, <p>, <strong>...</strong>...).
      4. Remplace les sauts de ligne et tabulations par un espace simple.
      5. Pour les zones NON codees uniquement : condense les espaces multiples.
      6. Supprime les espaces en debut et fin de valeur.

    Les zones codees (0XX, 100, 105, 106, 135) sont exemptees de l'etape 5
    car leur contenu est positionnel.
    """
    def _clean(text: str, is_coded: bool = False) -> str:
        # Etape 1 - NBSP -> espace ordinaire
        for ch in _NBSP_CHARS:
            text = text.replace(ch, " ")
        # Etape 2 - Entites HTML
        for entity, replacement in _HTML_ENTITIES.items():
            text = text.replace(entity, replacement)
        # Etape 3 - Balises HTML -> espace
        text = _RE_HTML_TAG.sub(" ", text)
        # Etape 4 - Sauts de ligne et tabulations -> espace
        text = _RE_WHITESPACE.sub(" ", text)
        # Etape 5 - Espaces multiples (zones non codees uniquement)
        if not is_coded:
            text = _RE_MULTI_SPACES.sub(" ", text)
        return text.strip()

    for field in record.fields:
        coded = _is_coded_field(field.tag)
        if field.data:
            field.data = _clean(field.data, is_coded=coded)
        for sf in field.subfields:
            sf.value = _clean(sf.value, is_coded=coded)


# ===========================================================================
# 2. Transformations bibliographiques
# ===========================================================================

def _extract_year(date_str: str) -> str:
    """
    Extrait l'annee a 4 chiffres d'une chaine de date quelconque.

    Exemples :
      "15 juin 2024"  -> "2024"
      "2024-06-15"    -> "2024"
      "2024"          -> "2024"
      "sans date"     -> "sans date"
    """
    match = _RE_YEAR.search(date_str)
    return match.group(1) if match else date_str


def convert_210_to_214(record: MarcRecord) -> None:
    """
    Si la notice ne contient pas de zone 214, transforme chaque zone 210
    en zone 214 en conservant indicateurs et sous-zones.

    Le $d (date) est nettoye : si la valeur contient plus qu'une annee,
    seule l'annee a 4 chiffres est conservee. Si aucune annee n'est trouvee,
    $d est laisse intact.

    Ne fait rien si au moins une zone 214 est deja presente.
    """
    if record.get_field("214"):
        return

    zones_210 = record.get_fields("210")
    if not zones_210:
        return

    record.remove_fields("210")

    for zone in zones_210:
        new_field = MarcField(tag="214", ind1=zone.ind1, ind2=zone.ind2)
        for sf in zone.subfields:
            value = sf.value
            if sf.code == "d":
                value = _extract_year(value)
            new_field.add_subfield(sf.code, value)
        record.add_field(new_field)


def normalize_214_date(record: MarcRecord) -> None:
    """
    Nettoie le $d de toutes les zones 214 : ne conserve que l'année à 4 chiffres.

    S'applique aux 214 déjà présentes dans la source ET à celles issues de
    la conversion 210→214. Couvre les cas : "15 juin 2024", "2024-06-15",
    "copyright 2024", etc. Si aucune année n'est détectable, $d est conservé.
    """
    for zone in record.get_fields("214"):
        d = zone.get_subfield("d")
        if d:
            zone.set_subfield("d", _extract_year(d))


def ensure_214_lieu(record: MarcRecord, default: str = "[Lieu inconnu]") -> None:
    """
    S'assure que chaque zone 214 possede un $a (lieu de publication).

    Si une 214 n'a pas de $a, un $a est insere en premiere position avec
    la valeur par defaut "[Lieu inconnu]" (norme ISBD pour lieu non identifie).

    S'applique aux 214 deja presentes dans la source ET a celles generees
    par convert_210_to_214().

    Args:
        record  : La notice a verifier.
        default : Valeur a utiliser si $a absent (defaut : "[Lieu inconnu]").
    """
    for zone in record.get_fields("214"):
        if not zone.get_subfield("a"):
            zone.subfields.insert(0, SubField(code="a", value=default))


def extract_edition_from_200a(record: MarcRecord) -> None:
    """
    Détecte et extrait la mention d'édition du 200$a vers 205$a.

    Deux cas sont gérés :

    Cas 1 — mention en FIN de titre (tiret optionnel) :
      "Mon titre - 4e éd."   -> 200$a "Mon titre"            + 205$a "4e éd."
      "Mon titre 2e édition" -> 200$a "Mon titre"            + 205$a "2e édition"

    Cas 2 — mention INTERCALÉE entre titre et sous-titre :
      "Titre - 4e éd. - Sous-titre"
        -> 200$a "Titre : Sous-titre"  + 205$a "4e éd."
      Le sous-titre est rattaché au titre avec " : " comme séparateur.

    Le cas 2 est testé en premier (il est plus spécifique).
    Si une zone 205 existe déjà, son $a est écrasé.
    Si aucune mention n'est trouvée, ne fait rien.
    """
    zone_200 = record.get_field("200")
    if zone_200 is None:
        return

    titre = zone_200.get_subfield("a")
    if not titre:
        return

    mention = None
    titre_propre = None

    # ── Cas 2 : titre - édition - sous-titre ──────────────────────────
    m = _RE_EDITION_MIDDLE.search(titre)
    if m:
        # Tout ce qui précède le premier tiret = titre principal
        titre_propre = titre[: m.start()].strip().rstrip("-").strip()
        mention      = m.group(1).strip()
        sous_titre   = m.group(2).strip()
        # Titre final : titre principal + " : " + sous-titre
        titre_propre = f"{titre_propre} : {sous_titre}"

    else:
        # ── Cas 1 : mention en fin de titre ───────────────────────────
        m = _RE_EDITION_END.search(titre)
        if m:
            mention      = m.group(0).strip().lstrip("- ").strip()
            titre_propre = titre[: m.start()].strip().rstrip("-").strip()

    if mention is None:
        return  # Aucun cas détecté

    zone_200.set_subfield("a", titre_propre)

    zone_205 = record.get_field("205")
    if zone_205 is None:
        zone_205 = MarcField(tag="205", ind1=" ", ind2=" ")
        record.add_field(zone_205)
    zone_205.set_subfield("a", mention)


def strip_ebook_suffix_from_200a(record: MarcRecord) -> None:
    """
    Supprime les suffixes commerciaux liés au format en fin de 200$a.

    Suffixes reconnus (insensibles à la casse, tiret optionnel) :
      "- Ebook epub", "-Ebook epub"
      "- Ebook",      "-Ebook"
      "- EPUB",       "-EPUB"

    La recherche est faite par regex, du plus long au plus court, pour éviter
    de supprimer "Ebook" sans avoir d'abord tenté "Ebook epub".

    Ne fait rien si 200$a est absent ou si aucun suffixe n'est détecté.
    """
    zone_200 = record.get_field("200")
    if zone_200 is None:
        return
    titre = zone_200.get_subfield("a")
    if not titre:
        return

    nouveau = _RE_EBOOK_SUFFIX.sub("", titre).strip()
    if nouveau != titre:
        zone_200.set_subfield("a", nouveau)


def strip_subfield_from_6xx_7xx(record: MarcRecord, code: str) -> None:
    """
    Supprime toutes les occurrences de la sous-zone $<code> dans les zones
    6XX (indexation sujet) et 7XX (responsabilite).

    Args:
        record : La notice a traiter.
        code   : Le code de sous-zone a supprimer (ex. "3", "o").
    """
    for field in record.fields:
        if field.tag.startswith("6") or field.tag.startswith("7"):
            field.subfields = [sf for sf in field.subfields if sf.code != code]


def split_606_by_comma(record: MarcRecord) -> None:
    """
    Si la notice contient une seule zone 606 dont le $a contient plusieurs
    valeurs separees par des virgules, eclate ces valeurs en autant de zones
    606 distinctes (un $a par zone).

    Exemple :
      606$a "Philosophie, Histoire, Francais"
      -> 606$a "Philosophie" + 606$a "Histoire" + 606$a "Francais"

    Ne fait rien si la notice contient zero ou plusieurs zones 606, ou si
    le $a ne contient pas de virgule.
    """
    zones_606 = record.get_fields("606")
    if len(zones_606) != 1:
        return

    zone = zones_606[0]
    valeur_a = zone.get_subfield("a") or ""

    if "," not in valeur_a:
        return

    termes = [t.strip() for t in valeur_a.split(",") if t.strip()]
    if len(termes) <= 1:
        return

    record.remove_fields("606")

    for terme in termes:
        new_606 = MarcField(tag="606", ind1=zone.ind1, ind2=zone.ind2)
        new_606.add_subfield("a", terme)
        record.add_field(new_606)


def copy_ean_to_isbn(record: MarcRecord) -> None:
    """
    Copie l'EAN (073$a) dans le champ ISBN (010$a), en ecrasant l'eventuel
    ISBN deja present.

    - Si aucun 073$a : ne fait rien.
    - Si une zone 010 existe : son $a est mis a jour (autres sous-zones conservees).
    - Si aucune zone 010 : elle est creee avec le seul $a.
    """
    ean = record.get_value("073", "a")
    if not ean:
        return

    zone_010 = record.get_field("010")
    if zone_010 is None:
        zone_010 = MarcField(tag="010", ind1=" ", ind2=" ")
        record.add_field(zone_010)

    zone_010.set_subfield("a", ean)


def remove_215(record: MarcRecord) -> None:
    """
    Supprime toutes les zones 215 (Description materielle).
    Pour les ebooks, cette zone n'est pas pertinente.
    """
    record.remove_fields("215")


def remove_106(record: MarcRecord) -> None:
    """
    Supprime toutes les zones 106 (Donnees codees - texte).
    Remplacee par les zones 181/182/183 (RDA).
    """
    record.remove_fields("106")


def ensure_language(record: MarcRecord, default_lang: str = "fre") -> None:
    """
    S'assure qu'une langue est renseignee en 101$a.
    Si absente ou sans $a, ajoute la langue par defaut.

    Args:
        record       : La notice a verifier.
        default_lang : Code langue UNIMARC a 3 lettres (defaut : "fre").
    """
    zone_101 = record.get_field("101")
    if zone_101 is None:
        zone_101 = MarcField(tag="101", ind1=" ", ind2=" ")
        record.add_field(zone_101)

    if not zone_101.get_subfield("a"):
        zone_101.add_subfield("a", default_lang)


def ensure_country(record: MarcRecord, default_country: str = "FR") -> None:
    """
    S'assure qu'un pays est renseigne en 102$a.
    Si absent ou sans $a, ajoute le pays par defaut.

    Args:
        record          : La notice a verifier.
        default_country : Code pays ISO 3166-1 alpha-2 (defaut : "FR").
    """
    zone_102 = record.get_field("102")
    if zone_102 is None:
        zone_102 = MarcField(tag="102", ind1=" ", ind2=" ")
        record.add_field(zone_102)

    if not zone_102.get_subfield("a"):
        zone_102.add_subfield("a", default_country)


def rebuild_zone_100(record: MarcRecord) -> None:
    """
    Supprime la zone 100 existante, puis en recrée une dont le $a contient
    l'annee de publication extraite de la zone 210$d ou 214$d.

    La zone 100$a est une zone codee de 39 caracteres positionnels (UNIMARC).
    Structure :
      pos 0-7  : date de saisie (AAAAMMJJ)
      pos 8    : type de date ("0" = date certaine)
      pos 9-12 : annee de publication
      pos 13-16: date de fin ("    ")
      pos 17-38: codes fixes : "    k  y0frey50      ba"

    Exemple : "202605020202400000000    k  y0frey50      ba"

    Si aucune annee n'est trouvee, "0000" est utilise.
    """
    record.remove_fields("100")

    annee = ""
    for tag in ("210", "214"):
        for zone in record.get_fields(tag):
            d = zone.get_subfield("d")
            if d:
                match = _RE_YEAR.search(d)
                if match:
                    annee = match.group(1)
                    break
        if annee:
            break

    annee = annee or "0000"
    today = date.today().strftime("%Y%m%d")

    # pos 0-7 : date saisie | pos 8 : type | pos 9-12 : annee pub
    # pos 13-16 : date fin  | pos 17-38 : codes fixes
    valeur = f"{today}0{annee}    " + "k  y0frey50      ba"

    zone_100 = MarcField(tag="100", ind1=" ", ind2=" ")
    zone_100.add_subfield("a", valeur)
    record.add_field(zone_100)


def fix_leader(record: MarcRecord) -> None:
    """
    Verifie que le leader contient "clm" aux positions 5, 6, 7.

    En UNIMARC :
      pos 5 : statut de l'enregistrement -> "c" (corrige/modifie)
      pos 6 : type de document            -> "l" (ressource textuelle)
      pos 7 : niveau bibliographique      -> "m" (monographie)

    Si les trois caracteres ne sont pas "clm", ils sont corriges.
    Les 5 premiers chiffres (longueur) et le reste du leader sont conserves.
    """
    if len(record.leader) < 8:
        return

    if record.leader[5:8] != "clm":
        record.leader = record.leader[:5] + "clm" + record.leader[8:]


# ===========================================================================
# 3. Ajout de zones normalisees (RDA, types, acces...)
# ===========================================================================

def add_zones_181_182_183(record: MarcRecord) -> None:
    """
    Ajoute les zones RDA de contenu/media/support (181, 182, 183).

    Remplace la zone 106 (supprimee par remove_106).
    Les zones sont supprimes puis recreees pour garantir la coherence.

    Structure creee :
      181 ind1=" " ind2=" " : $6 z01 $c txt $2 rdacontent
      181 ind1=" " ind2="1" : $6 z01 $a i# $b xxxe##
      182 ind1=" " ind2=" " : $6 z01 $c c $2 rdamedia
      182 ind1=" " ind2="1" : $6 z01 $a b
      183 ind1=" " ind2=" " : $6 z01 $a ceb $2 RDAfrCarrier
    """
    for tag in ("181", "182", "183"):
        record.remove_fields(tag)

    specs = [
        ("181", " ", " ", [("6", "z01"), ("c", "txt"),  ("2", "rdacontent")]),
        ("181", " ", "1", [("6", "z01"), ("a", "i#"),   ("b", "xxxe##")]),
        ("182", " ", " ", [("6", "z01"), ("c", "c"),    ("2", "rdamedia")]),
        ("182", " ", "1", [("6", "z01"), ("a", "b")]),
        ("183", " ", " ", [("6", "z01"), ("a", "ceb"),  ("2", "RDAfrCarrier")]),
    ]

    for tag, ind1, ind2, subfields in specs:
        field = MarcField(tag=tag, ind1=ind1, ind2=ind2)
        for code, value in subfields:
            field.add_subfield(code, value)
        record.add_field(field)


def add_zones_105_135(record: MarcRecord) -> None:
    """
    Ajoute les zones codees 105 (donnees textuelles) et 135 (ressource
    electronique), en ecrasant les eventuelles occurrences existantes.

    Valeurs fixes pour un ebook texte en ligne :
      105$a : "a   ea  001yy"
      135$a : "vrmn#|||mnnan"
    """
    record.remove_fields("105")
    record.remove_fields("135")

    for tag, value in (("105", "a   ea  001yy"), ("135", "vrmn#|||mnnan")):
        field = MarcField(tag=tag, ind1=" ", ind2=" ")
        field.add_subfield("a", value)
        record.add_field(field)


def move_330_to_349(record: MarcRecord) -> None:
    """
    Déplace le résumé source (330$a) en zone 349.

    La zone 349 est une zone locale bibliothèque utilisée pour les résumés
    issus de sources externes (fournisseur, OAI…). La zone 330 est ainsi
    réservée aux résumés issus du Sudoc (récupérés via MARC).
    """
    for field in record.fields:
        if field.tag == "330":
            field.tag = "349"
            field.add_subfield("2","UNIMARC biblioondemand")

def detect_platform(record: MarcRecord) -> str:
    """
    Identifie la plateforme de consultation a partir de l'URL du 856 principal
    (le 856 d'acces au document, pas celui de la vignette).

    Regles de detection (par ordre de priorite) :
      URL contient "consult-brownsbooks" -> "brownsbooks"
      URL contient "consult-immanens"    -> "immanens"
      URL contient "consult-pnb"         -> "PNB"
      Sinon                              -> "" (plateforme inconnue)

    Args:
        record : La notice a analyser (apres conversion jackets -> 859,
                 ou avant — la fonction ignore les 859 et les 856 jackets).

    Returns:
        Nom de la plateforme (str), ou chaine vide si non detectee.
    """
    # Correspondances URL -> nom de plateforme (ordre important)
    _PLATFORM_RULES = [
        ("consult-brownsbooks", "brownsbooks"),
        ("consult-immanens",    "immanens"),
        ("consult-pnb",         "PNB"),
    ]

    for field in record.fields:
        # Ignorer les 859 (vignettes deja converties) et les 856 marques vignette
        if field.tag not in ("856",):
            continue
        x = (field.get_subfield("x") or "").strip().lower()
        if x == "vignette":
            continue  # lien de couverture, pas un lien d'acces
        url = field.get_subfield("u") or ""
        for fragment, nom in _PLATFORM_RULES:
            if fragment in url:
                return nom

    return ""


def parse_zone_917(record: MarcRecord) -> dict:
    """
    Extrait les informations de la zone 917 (conditions de licences e-book).

    Structure attendue (exemple) :
      917 $a1825 $b09 $c999999 $d09 $e30 $f1 $g1 $h03

    Sous-zones exploitees :
      $e : nombre de telechargements autorises (int)
      $f : nombre d'acces simultanes (int)

    Args:
        record : La notice a analyser.

    Returns:
        Dictionnaire avec les cles presentes parmi :
          "telechargements" : valeur de $e
          "simultanees"     : valeur de $f
        Les cles absentes dans la notice sont absentes du dictionnaire.
    """
    result = {}
    zone_917 = record.get_field("917")
    if zone_917 is None:
        return result

    val_e = zone_917.get_subfield("e")
    val_f = zone_917.get_subfield("f")

    if val_e is not None:
        result["telechargements"] = val_e.strip()
    
    if val_f is not None:
        result["simultanees"] = val_f.strip()
        
    return result

def _build_access_note(plateforme: str, licence: dict) -> str:
    """
    Construit le texte de la note d'accès (371$a) selon les règles métier.

    Logique :
      - Si nb accès simultanés présent et < 1000 :
          "Accès sur authentification. Consultation sur la plateforme <P>.
           <F> accès simultanés."
      - Sinon 
           "Accès sur authentification. Consultation sur la plateforme <P>.

    La mention de la plateforme est incluse seulement si elle est connue.
    """
    plateforme_str = (
        f"Consultation sur la plateforme {plateforme}. "
        if plateforme else ""
    )
    try:
        nb_acces_simultanes = int(licence.get("simultanees"))
    except ValueError:
        nb_acces_simultanes = None

    if nb_acces_simultanes is None : 
        acces_simultanes_str = ""
    elif nb_acces_simultanes == 1:
        acces_simultanes_str = f"{nb_acces_simultanes} accès simultané. "
    elif nb_acces_simultanes > 1000 :
        acces_simultanes_str = ""
    else:
        acces_simultanes_str = f"{nb_acces_simultanes} accès simultanés. "
        
    return (f"Accès sur authentification. {plateforme_str} {acces_simultanes_str}")

def add_zone_371(record: MarcRecord, plateforme: str, licence: dict) -> str:
    """
    Crée (ou recrée) la zone 371$a avec la note d'accès enrichie.

    La note est construite depuis la plateforme détectée et les conditions
    de licence extraites de la zone 917. Elle est ensuite propagée en
    856$z et 995$z par les fonctions appelantes.

    Returns:
        Le texte de la note (pour propagation vers 856$z et 995$z).
    """
    record.remove_fields("371")
    note = _build_access_note(plateforme, licence)
    field = MarcField(tag="371", ind1=" ", ind2=" ")
    field.add_subfield("a", note)
    record.add_field(field)
    return note


def move_coverURL_856_to_859(record: MarcRecord) -> None:
    """
    Transforme en zone 859 tout 856 dont le $x vaut "vignette".

    Convention Koha/UNIMARC :
      856 = URL d'acces au document
      859 = URL de vignette / image de couverture

    La detection se fait sur la presence de $x "vignette" (insensible
    a la casse), et non plus sur le contenu de l'URL.
    Les indicateurs et toutes les sous-zones sont conserves.
    """
    for field in record.fields:
        if field.tag == "856":
            x = (field.get_subfield("x") or "").strip().lower()
            if x == "vignette":
                field.tag = "859"


# ===========================================================================
# 4. Ajout de zones Koha (039, 099, 801, 995)
# ===========================================================================

def copy_001_to_039a(record: MarcRecord) -> None:
    """
    Copie la valeur du champ 001 dans la sous-zone $a du champ 039.

    La zone 039 stocke l'identifiant fournisseur d'origine dans Koha.
    Si la zone 039 existe deja, un $a est ajoute sans supprimer l'existant.
    """
    id_001 = record.get_value("001")
    if not id_001:
        return

    zone_039 = record.get_field("039")
    if zone_039 is None:
        zone_039 = MarcField(tag="039", ind1=" ", ind2=" ")
        record.add_field(zone_039)

    zone_039.add_subfield("a", id_001)


def add_zone_099(record: MarcRecord, today: date | None = None) -> None:
    """
    Ajoute la zone 099 (type de document / provenance) a la notice.

    Sous-zones creees (cf. config.ZONE_099) :
      $t : type de document  (ex. LIVRE_EL)
      $u : mode d'acces      (ex. en ligne)
      $y : source fournisseur
      $z : source secondaire
      $c : date du jour au format AAAA-MM-JJ

    Si la zone 099 existe deja, elle est d'abord supprimee.
    """
    if today is None:
        today = date.today()

    record.remove_fields("099")

    field = MarcField(tag="099", ind1=" ", ind2=" ")
    for code, value in ZONE_099.items():
        field.add_subfield(code, value)
    field.add_subfield("c", today.strftime("%Y-%m-%d"))

    record.add_field(field)


def add_zone_801(record: MarcRecord) -> None:
    """
    Ajoute la zone 801 (source de catalogage) a la notice.

    Indicateur 2 = '2' : source de catalogage courante (norme UNIMARC).
    Sous-zones : $a pays, $b agence (cf. config.ZONE_801).
    Remplace toute 801 ind2=2 existante.
    """
    record.fields = [
        f for f in record.fields
        if not (f.tag == "801" and f.ind2 == "2")
    ]

    field = MarcField(tag="801", ind1=" ", ind2="2")
    for code, value in ZONE_801.items():
        field.add_subfield(code, value)

    record.add_field(field)

def add_zone_830(record: MarcRecord) -> None:
    """
    Ajoute la zone 830 (note de catalogage) a la notice.

    """
    record.fields = [
        f for f in record.fields
        if not (f.tag == "830")
    ]

    field = MarcField(tag="830", ind1=" ", ind2=" ")
    for code, value in ZONE_830_UNIMARC.items():
        field.add_subfield(code, value)

    record.add_field(field)


def add_zone_995(
    record: MarcRecord,
    order_index: int,
    timestamp: datetime | None = None,
    note_acces: str = "",
    label_max_telechargements : str = ""
) -> None:
    """
    Ajoute la zone 995 (donnees d'exemplaire Koha) a la notice.

    Code-barres ($f) : <BARCODE_PREFIX> + <073$a>
    
    Si le champ 073$a est absent, revient au format par défaut:
    <BARCODE_PREFIX> + <AAMMJJHHmmss> + <ordre 4 chiffres>
    
    Exemple (073$a présent) : BOD9782075126655
    Exemple (073$a absent)  : BOD2606151423590042  (19 caracteres, limite Koha : 20)

    Sous-zones : $f code-barres, puis toutes les sous-zones de config.ZONE_995,
    et si note_acces est fournie, un $z avec cette note.

    Args:
        record      : La notice a enrichir.
        order_index : Numero d'ordre dans le lot (commence a 1).
        timestamp   : Horodatage a utiliser (defaut : maintenant).
        note_acces  : Texte de la note 371$a a copier en $z (peut etre vide).
    """
    if timestamp is None:
        timestamp = datetime.now()

    record.remove_fields("995")

    # Chercher le contenu de 073$a (EAN)
    ean = record.get_value("073", "a")
    
    if ean and ean.strip():
        # Si 073$a existe et n'est pas vide, l'utiliser directement
        barcode = f"{BARCODE_PREFIX}{ean.strip()}"
    else:
        # Sinon, revenir au format par défaut avec timestamp
        barcode = f"{BARCODE_PREFIX}{timestamp.strftime('%y%m%d%H%M%S')}{order_index:04d}"

    field = MarcField(tag="995", ind1=" ", ind2=" ")
    field.add_subfield("f", barcode)
    for code, value in ZONE_995.items():
        field.add_subfield(code, value)

    if note_acces:
        field.add_subfield("z", note_acces +". " + label_max_telechargements)

    record.add_field(field)


def propagate_access_note_to_856z(record: MarcRecord, note_acces: str = "", label_max_telechargements:str = "") -> None:
    """
    Copie la note d'acces (371$a) en $z sur tous les 856 d'acces au document
    en ajoutant le nombre de téléchargements max
    (les 856 de vignettes ont deja ete convertis en 859 a ce stade).

    Args:
        record     : La notice a enrichir.
        note_acces : Texte a placer en $z.
    """
    if not note_acces:
        return
    for field in record.fields:
        if field.tag == "856":
            field.set_subfield("z", note_acces + label_max_telechargements)


# ===========================================================================
# 5. Orchestrateur principal
# ===========================================================================

def prepare_record_for_koha(
    record: MarcRecord,
    order_index: int,
    timestamp: datetime | None = None,
) -> MarcRecord:
    """
    Applique l'ensemble des transformations Koha a une notice et retourne
    une copie enrichie (la notice originale n'est pas modifiee).

    Ordre des transformations :
      -- Nettoyage -----------------------------------------------------------
       1. NBSP, HTML, whitespace, espaces multiples (hors zones codees)
      -- Corrections bibliographiques ----------------------------------------
       2. Leader : positions 5-7 = "clm"
       3. 210 -> 214 si pas de 214, nettoyage date $d
       4. Extraction mention d'edition de 200$a -> 205$a
       5. Suppression $3 en 6XX/7XX
       6. Suppression $o en 6XX/7XX
       7. Eclatement 606$a multi-valeurs en plusieurs 606
       8. Copie EAN (073$a) -> ISBN (010$a)
       9. Suppression 215
      10. Suppression 106
      11. Langue par defaut en 101$a
      12. Pays par defaut en 102$a
      13. Reconstruction zone 100 (date saisie + annee publication)
      -- Zones normalisees ---------------------------------------------------
      14. Zones RDA 181, 182, 183
      15. Zones codees 105, 135
      16. Conversion Résumé source 330 → 349 avec $2 "site du fournisseur"
      17. Conversion 856 jackets -> 859
      18. Detection plateforme (856) + analyse licence (917)
      19. Zone 371$a (note d'acces enrichie avec plateforme et conditions)
      20. Propagation note en 856$z
      -- Zones Koha ----------------------------------------------------------
      21. Copie 001 -> 039$a
      22. Zone 099 (type document)
      23. Zone 801 (source catalogage)
      24. Zone 830 (note de catalogage)
      25. Zone 995 (exemplaire / code-barres + note en $z)

    Args:
        record      : Notice source (non modifiee).
        order_index : Position dans le lot (commence a 1), pour le code-barres.
        timestamp   : Horodatage de reference (defaut : maintenant).

    Returns:
        Nouvelle MarcRecord enrichie, prete pour l'export.
    """
    if timestamp is None:
        timestamp = datetime.now()

    prepared = record.clone()

    # -- Suppressions prealables ---------------------------------------------
    # Zones qui seront recreees : on supprime les versions source pour eviter
    # les doublons si elles etaient deja presentes dans le fichier fourni.
    prepared.remove_fields("371")
    prepared.remove_fields("995")
    # Zone 686 (classification libre) : non pertinente pour Koha
    prepared.remove_fields("686")

    # -- Nettoyage -----------------------------------------------------------
    clean_subfields(prepared)

    # -- Corrections bibliographiques ----------------------------------------
    fix_leader(prepared)
    convert_210_to_214(prepared)
    normalize_214_date(prepared)
    ensure_214_lieu(prepared)
    extract_edition_from_200a(prepared)
    strip_ebook_suffix_from_200a(prepared)
    strip_subfield_from_6xx_7xx(prepared, "3")
    strip_subfield_from_6xx_7xx(prepared, "o")
    strip_subfield_from_6xx_7xx(prepared, "c")   # $c des 7XX (mention de responsabilité redondante)
    split_606_by_comma(prepared)
    copy_ean_to_isbn(prepared)
    remove_215(prepared)
    remove_106(prepared)
    ensure_language(prepared)
    ensure_country(prepared)
    rebuild_zone_100(prepared)

    # -- Zones normalisees ---------------------------------------------------
    add_zones_181_182_183(prepared)
    add_zones_105_135(prepared)
    move_330_to_349(prepared)

    # Conversion jackets -> 859 EN PREMIER (avant detect_platform et propagation)
    # pour que les 856 restants soient bien les liens d'acces au document.
    move_coverURL_856_to_859(prepared)

    # Analyse de la plateforme et des conditions de licence
    plateforme = detect_platform(prepared)
    licence    = parse_zone_917(prepared)

    # Zone 371 enrichie + recuperation du texte pour propagation
    note_acces = add_zone_371(prepared, plateforme=plateforme, licence=licence)
    label_max_telechargements = f"{licence.get("telechargements")} téléchargements max"

    # Propagation de la note d'accès en 856$z (sur les 856 d'acces restants)
    propagate_access_note_to_856z(prepared, note_acces, label_max_telechargements)

    # -- Zones Koha ----------------------------------------------------------
    copy_001_to_039a(prepared)
    add_zone_099(prepared, today=timestamp.date())
    add_zone_801(prepared)
    add_zone_830(prepared)
    # La note est passee a add_zone_995 pour etre copiee en 995$z
    add_zone_995(prepared, order_index=order_index, timestamp=timestamp,
                 note_acces=note_acces, label_max_telechargements=label_max_telechargements)

    # -- Tri final par ordre numerique de tag (001 -> 999) -------------------
    # Les zones de controle (001-009, tag < "010") sont placees en tete,
    # suivies des zones ordinaires triees par tag.
    prepared.fields.sort(key=lambda f: f.tag)

    return prepared


def prepare_records_for_koha(
    records: List[MarcRecord],
    selected_indices: List[int],
) -> List[MarcRecord]:
    """
    Prepare un sous-ensemble de notices et retourne la liste enrichie.

    Un seul timestamp est genere pour tout le lot afin que les codes-barres
    partagent la meme date/heure.

    Args:
        records          : Liste complete des notices importees.
        selected_indices : Indices (base 0) des notices a preparer.

    Returns:
        Liste de MarcRecord prepares, dans l'ordre de selection.
    """
    batch_timestamp = datetime.now()
    result: List[MarcRecord] = []

    for order, idx in enumerate(selected_indices, start=1):
        prepared = prepare_record_for_koha(
            records[idx],
            order_index=order,
            timestamp=batch_timestamp,
        )
        result.append(prepared)

    return result
