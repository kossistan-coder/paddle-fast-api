"""Champs de la zone visuelle (texte imprime hors MRZ), reperes par leurs libelles.

Calibre sur la CNI togolaise ; les libelles absents d'un document sont simplement ignores.
Ces valeurs ne sont pas protegees par des chiffres de controle : moins fiables que la MRZ.
"""
import re

_L = r"(?<![A-Za-zÀ-ÿ])"  # le libelle ne doit pas etre la fin d'un autre mot (ex. "Nom" dans "Prénom")
_LABELS = {
    "national_number": _L + r"Num[eé]ro",
    "last_name": _L + r"Noms?",
    "first_names": _L + r"Pr[eé]noms?",
    "birth_date": _L + r"N[eé]\(?e?\)?\s+le",
    "place_of_birth": r"(?:^\s*[AÀ]|" + _L + r"N[eé]\(?e?\)?\s+[àa]|" + _L + r"Lieu\s+de\s+naissance)",
    "sex": _L + r"Sexe",
    "profession": _L + r"Profession",
    "issue_date": _L + r"(?:Fait|D[eé]livr[eé]e?)\s+le",
    "expiry_date": _L + r"Expire\s+le",
    "height": _L + r"Taille",
    "blood_group": _L + r"Groupe\s+sanguin",
    "address": _L + r"Domicile",
    "distinguishing_marks": _L + r"Signes\s+particuliers",
    "father": _L + r"P[eè]re",
    "mother": _L + r"M[eè]re",
    "emergency_contact": _L + r"Personne\s+[àa]\s+pr[eé]venir",
}
_PATTERNS = {k: re.compile(p + r"\s*:", re.IGNORECASE) for k, p in _LABELS.items()}


def parse_visual_zone(lines: list[str]) -> dict[str, str]:
    """{champ: valeur} ; une ligne peut porter plusieurs libelles ("Né le: ... Sexe: M")."""
    fields: dict[str, str] = {}
    for line in lines:
        hits = sorted((m.start(), m.end(), key) for key, p in _PATTERNS.items() for m in p.finditer(line))
        for i, (_, end, key) in enumerate(hits):
            stop = hits[i + 1][0] if i + 1 < len(hits) else len(line)
            value = line[end:stop].strip(" ,;:")
            if value and key not in fields:
                fields[key] = value
    return fields
