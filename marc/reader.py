#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marc/reader.py — Lecture de fichiers UNIMARC ISO2709 (UTF-8)
=============================================================
Ce module fournit :
- La fonction `parse_iso2709(path)` qui lit un fichier ISO2709 et retourne
  une liste de MarcRecord.
- La classe legere `MarcRecord` qui represente une notice MARC en memoire.

IMPORTANT — Gestion correcte de l'UTF-8 en ISO2709
---------------------------------------------------
Le format ISO2709 stocke dans le leader et le repertoire des longueurs et
des offsets exprimes en OCTETS (bytes), pas en caracteres Unicode.
En UTF-8, un caractere accentue (e, a, u...) occupe 2 a 4 octets.

Si l'on decodait le fichier en chaine Python AVANT de lire le repertoire,
les indices de caracteres ne correspondraient plus aux offsets en octets :
toutes les zones contenant des caracteres non-ASCII auraient leurs donnees
decalees, tronquees ou "mangees".

La regle appliquee ici est donc :
  1. Lire le fichier en bytes bruts (pas de decode global).
  2. Lire le leader et le repertoire en bytes (ils sont pur ASCII).
  3. Decouper chaque champ en bytes en utilisant les offsets du repertoire.
  4. Decoder en UTF-8 (ou autre encodage) uniquement la valeur de chaque
     champ individuel, une fois le decoupage correct effectue.

Evolution possible :
- Gerer d'autres encodages (Latin-1, Marc-8) via le parametre `encoding`.
- Etendre MarcRecord avec des methodes de comparaison, deduplication, etc.
"""

from __future__ import annotations
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


# Separateurs ISO2709 (en bytes et en str, pour les deux usages)
IS3_BYTE = b"\x1e"   # Fin de champ / fin de repertoire
IS2_BYTE = b"\x1f"   # Separateur de sous-zone
IS3      = "\x1e"
IS2      = "\x1f"


@dataclass
class SubField:
    """Sous-zone MARC : un code (1 caractere) + sa valeur textuelle."""
    code:  str
    value: str

    def __repr__(self):
        return f"${self.code}:{self.value!r}"


@dataclass
class MarcField:
    """
    Champ MARC.

    - Pour les zones de controle (tag 001-009) : `data` contient la valeur
      brute et `subfields` est vide.
    - Pour les autres zones : `ind1`, `ind2` portent les indicateurs,
      `subfields` porte les sous-zones.
    """
    tag:       str
    ind1:      str = " "
    ind2:      str = " "
    data:      str = ""                          # Zones de controle uniquement
    subfields: List[SubField] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Acces pratiques
    # ------------------------------------------------------------------

    def get_subfield(self, code: str) -> Optional[str]:
        """Retourne la valeur de la premiere sous-zone $code, ou None."""
        for sf in self.subfields:
            if sf.code == code:
                return sf.value
        return None

    def get_all_subfields(self, code: str) -> List[str]:
        """Retourne toutes les valeurs de la sous-zone $code."""
        return [sf.value for sf in self.subfields if sf.code == code]

    def set_subfield(self, code: str, value: str) -> None:
        """Met a jour la premiere sous-zone $code (ou la cree si absente)."""
        for sf in self.subfields:
            if sf.code == code:
                sf.value = value
                return
        self.subfields.append(SubField(code, value))

    def add_subfield(self, code: str, value: str) -> None:
        """Ajoute une sous-zone $code (meme si elle existe deja)."""
        self.subfields.append(SubField(code, value))

    def __repr__(self):
        if self.data:
            return f"[{self.tag}] {self.data!r}"
        sfs = " ".join(str(s) for s in self.subfields)
        return f"[{self.tag} {self.ind1}{self.ind2}] {sfs}"


class MarcRecord:
    """
    Notice MARC en memoire.

    Attributes:
        leader   : Les 24 caracteres du leader ISO2709 (decoded ASCII).
        fields   : Liste ordonnee de MarcField.
        raw      : Bytes bruts de l'enregistrement (pour debug).
    """

    def __init__(self, leader: str = ""):
        self.leader: str = leader
        self.fields: List[MarcField] = []
        self.raw:    bytes = b""

    # ------------------------------------------------------------------
    # Acces aux champs
    # ------------------------------------------------------------------

    def get_fields(self, tag: str) -> List[MarcField]:
        """Retourne tous les champs du tag donne."""
        return [f for f in self.fields if f.tag == tag]

    def get_field(self, tag: str) -> Optional[MarcField]:
        """Retourne le premier champ du tag donne, ou None."""
        for f in self.fields:
            if f.tag == tag:
                return f
        return None

    def get_value(self, tag: str, subfield_code: Optional[str] = None) -> str:
        """
        Raccourci : retourne la valeur d'un champ/sous-zone.
        - Zone de controle (001-009) : retourne `data`.
        - Autres zones : retourne la premiere sous-zone $subfield_code, ou
          la concatenation de toutes les sous-zones si subfield_code est None.
        """
        f = self.get_field(tag)
        if f is None:
            return ""
        if f.data:
            return f.data
        if subfield_code:
            return f.get_subfield(subfield_code) or ""
        return " ".join(sf.value for sf in f.subfields)

    def add_field(self, field_obj: MarcField) -> None:
        """Ajoute un champ (en fin de liste)."""
        self.fields.append(field_obj)

    def remove_fields(self, tag: str) -> None:
        """Supprime tous les champs du tag donne."""
        self.fields = [f for f in self.fields if f.tag != tag]

    def clone(self) -> "MarcRecord":
        """Retourne une copie profonde de la notice."""
        return copy.deepcopy(self)

    def __repr__(self):
        ctrl = self.get_value("001")
        return f"<MarcRecord 001={ctrl!r} fields={len(self.fields)}>"


# ---------------------------------------------------------------------------
# Parsing ISO2709
# ---------------------------------------------------------------------------

def parse_iso2709(path: str | Path, encoding: str = "utf-8") -> List[MarcRecord]:
    """
    Lit un fichier ISO2709 et retourne la liste des notices.

    Le decoupage est effectue entierement en bytes pour respecter les offsets
    du repertoire, qui sont exprimes en octets (pas en caracteres Unicode).
    Chaque valeur de champ est decodee individuellement apres decoupage.

    Args:
        path:     Chemin vers le fichier .mrc / .iso / .unimarc.
        encoding: Encodage du contenu des champs (defaut : utf-8).

    Returns:
        Liste de MarcRecord (vide si le fichier est vide).

    Raises:
        ValueError: Si un enregistrement est malformé.
        FileNotFoundError, IOError: Si le fichier est illisible.
    """
    path = Path(path)
    raw_bytes = path.read_bytes()
    records: List[MarcRecord] = []

    pos = 0
    while pos < len(raw_bytes):
        if pos + 5 > len(raw_bytes):
            break

        # Les 5 premiers octets sont toujours des chiffres ASCII : safe a decoder
        try:
            rec_len = int(raw_bytes[pos: pos + 5])
        except ValueError:
            # Sauter les octets parasites entre notices (CR, LF, SUB)
            if raw_bytes[pos] in (0x0A, 0x0D, 0x1A):
                pos += 1
                continue
            raise ValueError(
                f"Longueur d'enregistrement invalide a l'offset {pos} : "
                f"{raw_bytes[pos:pos+5]!r}"
            )

        rec_raw = raw_bytes[pos: pos + rec_len]
        pos += rec_len

        try:
            record = _parse_single_record(rec_raw, encoding)
            record.raw = rec_raw
            records.append(record)
        except Exception as exc:
            raise ValueError(
                f"Erreur de decodage de la notice a l'offset {pos - rec_len} : {exc}"
            ) from exc

    return records


def _parse_single_record(raw: bytes, encoding: str) -> MarcRecord:
    """
    Parse un seul enregistrement ISO2709 (bytes) en MarcRecord.

    Principe cle :
      - Le leader (24 octets) et le repertoire sont pur ASCII : on les decode
        immediatement en str pour lire les metadonnees numeriques.
      - Les longueurs et offsets du repertoire sont en OCTETS : le decoupage
        des champs de donnees s'effectue sur les bytes bruts.
      - Chaque champ est decode en UTF-8 (ou autre encodage) seulement apres
        avoir ete extrait correctement par son offset en octets.
    """
    if len(raw) < 24:
        raise ValueError("Enregistrement trop court (< 24 octets).")

    # Leader : 24 octets ASCII
    leader_bytes = raw[:24]
    try:
        leader = leader_bytes.decode("ascii", errors="replace")
    except Exception:
        leader = leader_bytes.decode("latin-1")

    record = MarcRecord(leader=leader)

    # Adresse de base des donnees : positions 12-16 du leader (octets = caracteres ici)
    try:
        base_addr = int(leader[12:17])
    except ValueError:
        raise ValueError(f"Adresse de base invalide dans le leader : {leader[12:17]!r}")

    # ---- Repertoire (bytes 24 a base_addr-1, pur ASCII) --------------------
    # Le repertoire se termine par IS3 (0x1E), d'ou le -1
    dir_bytes = raw[24: base_addr - 1]

    if len(dir_bytes) % 12 != 0:
        raise ValueError(
            f"Taille du repertoire non multiple de 12 ({len(dir_bytes)} octets)."
        )

    entries: List[Tuple[str, int, int]] = []
    for i in range(0, len(dir_bytes), 12):
        entry = dir_bytes[i: i + 12].decode("ascii")
        tag    = entry[0:3]
        length = int(entry[3:7])   # longueur EN OCTETS
        offset = int(entry[7:12])  # offset EN OCTETS depuis base_addr
        entries.append((tag, length, offset))

    # ---- Section des donnees (bytes a partir de base_addr) -----------------
    data_section = raw[base_addr:]

    for tag, length, offset in entries:
        # Extraction en bytes avec les offsets/longueurs OCTETS du repertoire
        field_bytes = data_section[offset: offset + length]

        # Retirer le IS3 final (0x1E) s'il est present
        if field_bytes.endswith(IS3_BYTE):
            field_bytes = field_bytes[:-1]

        # Decoder en UTF-8 (ou l'encodage specifie) APRES le decoupage correct
        try:
            field_str = field_bytes.decode(encoding, errors="replace")
        except Exception:
            field_str = field_bytes.decode("latin-1", errors="replace")

        if tag < "010":
            # Zone de controle : pas d'indicateurs, pas de sous-zones
            field_obj = MarcField(tag=tag, data=field_str)
        else:
            # Zone ordinaire : 2 indicateurs puis IS2 + code + valeur
            ind1 = field_str[0] if len(field_str) > 0 else " "
            ind2 = field_str[1] if len(field_str) > 1 else " "
            field_obj = MarcField(tag=tag, ind1=ind1, ind2=ind2)

            subfield_data = field_str[2:]   # Apres les 2 indicateurs
            parts = subfield_data.split(IS2)
            for part in parts:
                if len(part) < 1:
                    continue
                sf_code  = part[0]
                sf_value = part[1:]
                field_obj.subfields.append(SubField(code=sf_code, value=sf_value))

        record.fields.append(field_obj)

    return record
