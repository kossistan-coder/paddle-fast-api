"""Champs de la zone visuelle (texte imprime hors MRZ), reperes par leurs libelles.

Deux mises en page : CNI togolaise ("Libelle: valeur" sur une ligne) et passeport (libelle bilingue
"Lieu de naissance / Place of birth", valeur imprimee sous le libelle). Les libelles absents sont ignores.
Ces valeurs ne sont pas protegees par des chiffres de controle : moins fiables que la MRZ.
"""
import re
from datetime import date

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


# Libelles de passeport (francais et/ou anglais), sans deux-points obligatoires. Imprimes en petit, ils sont
# souvent mal lus : espaces avales ("PLACE OFISSUE"), "/" lu "I" ("IDATE OF BIRTH"), I/M/L/S confondus
# ("LEUDE NAISSANCE", "PRENONS", "NATIONAUITE", "5EX") ; une seule des deux langues suffit.
_PASSPORT_LABELS = {
    "last_name": r"Noms?|Surnames?",
    "first_names": r"Pr[eé]no[mn]s?|Given\s*names?",
    "nationality": r"Nationa[lu]it[eé]|Nationality",
    "birth_date": r"Date\s*de\s*naissance|Date\s*of\s*birth",
    "place_of_birth": r"L[i1]?eu\s*de?\s*naissance|Place\s*of\s*birth",
    "sex": r"[S5]exe?",
    "issue_date": r"Date\s*(?:de\s*)?(?:d[eé]l\w*ance|l?['/]?\s*[eé]mission)|Date\s*of\s*issue",
    "expiry_date": r"Date\s*d['/]?\s*expiration|Date\s*of\s*expiry",
    "place_of_issue": r"L[i1]?eu\s*(?:de\s*)?(?:d[eé]l\w*ance|d?['/]?\s*[eé]mission)|Place\s*of\s*issue",
    "issuing_authority": r"Autorit[eé](?:\s*de\s*d[eé]livrance)?|(?:Issuing\s*)?Authority|D[eé]livr[eé]\s*par",
    "document_number": r"N[°o]\s*(?:du\s*)?passeport|Passport\s*(?:No|N[°o]|number)",
    "profession": r"Profession|Occupation",
}
# Comme _L, mais un "I" (barre oblique mal lue) ou un chiffre peut preceder le libelle
_LP = r"(?<![A-HJ-Za-hj-zÀ-ÿ])"
_PASSPORT = {k: re.compile(_LP + r"(?:" + p + r")(?![A-Za-zÀ-ÿ])", re.IGNORECASE) for k, p in _PASSPORT_LABELS.items()}
_SEPARATORS = " /:.,;|-"
_MAX_COLUMN_SHIFT = 0.12  # ecart horizontal maximal (en largeur de page) entre un libelle et sa valeur
_ROWS_BELOW = 2  # la valeur est cherchee sur les 2 lignes suivantes (photo inclinee : lignes melangees)


def _label_keys(text: str) -> tuple[list[str], str]:
    """Champs de passeport nommes dans le fragment, et ce qui reste une fois les libelles retires."""
    keys = [k for k, p in _PASSPORT.items() if p.search(text)]
    rest = text
    for k in keys:
        rest = _PASSPORT[k].sub(" ", rest)
    return keys, rest.strip(_SEPARATORS)


def _fragments(line: str) -> tuple[tuple[str, float], ...]:
    return getattr(line, "fragments", ()) or ((line, 0.0),)


def _value_below(rows: list, i: int, x: float) -> str | None:
    """Premier fragment aligne sur x dans les lignes suivantes, qui ne soit ni un libelle ni la MRZ."""
    for row in rows[i + 1:i + 1 + _ROWS_BELOW]:
        below = [(abs(bx - x), bt.strip(_SEPARATORS)) for bt, bx in row
                 if "<" not in bt and not _label_keys(bt)[0] and bt.strip(_SEPARATORS)]
        if below and min(below)[0] <= _MAX_COLUMN_SHIFT:
            return min(below)[1]
    return None


def _inline_value(text: str, key: str) -> str | None:
    """Valeur ecrite apres le libelle dans le meme fragment ("Place of birth: LOME")."""
    if not _PASSPORT[key].match(text.lstrip(_SEPARATORS)):
        return None
    end = max(m.end() for p in _PASSPORT.values() for m in p.finditer(text))
    return text[end:].strip(_SEPARATORS) or None


def _passport_fields(lines: list[str]) -> dict[str, str]:
    """Valeur imprimee sous le libelle (mise en page des passeports), sinon apres lui dans le meme fragment."""
    fields: dict[str, str] = {}
    rows = [_fragments(line) for line in lines]
    for i, row in enumerate(rows):
        for text, x in row:
            keys, _ = _label_keys(text)
            if not keys or keys[0] in fields:
                continue
            value = _value_below(rows, i, x) or _inline_value(text, keys[0])
            if value:
                fields[keys[0]] = value
    return fields


# Numero de la carte (celui de la MRZ, parfois sans zero de tete), imprime seul, sans libelle,
# au bout d'une ligne qui porte un autre champ ("Signes particuliers: NEANT        03632219")
_CARD_NUMBER = re.compile(r"\d{8,10}")


def _split_card_number(line: str) -> tuple[str, str | None]:
    """Retire de la ligne un fragment isole fait uniquement de chiffres ; renvoie (ligne, numero)."""
    frags = _fragments(line)
    numbers = [t for t, _ in frags if _CARD_NUMBER.fullmatch(t.strip())]
    if len(frags) < 2 or not numbers or "<" in line:
        return line, None
    return " ".join(t for t, _ in frags if t not in numbers), numbers[0].strip()


def parse_visual_zone(lines: list[str]) -> dict[str, str]:
    """{champ: valeur} ; une ligne peut porter plusieurs libelles ("Né le: ... Sexe: M")."""
    fields: dict[str, str] = {}
    for line in lines:
        line, card_number = _split_card_number(line)
        if card_number:
            fields.setdefault("document_number", card_number)
        hits = sorted((m.start(), m.end(), key) for key, p in _PATTERNS.items() for m in p.finditer(line))
        for i, (_, end, key) in enumerate(hits):
            stop = hits[i + 1][0] if i + 1 < len(hits) else len(line)
            value = line[end:stop].strip(" ,;:")
            if value and key not in fields:
                fields[key] = value
    for key, value in _passport_fields(lines).items():  # complete sans ecraser les champs de la CNI
        fields.setdefault(key, value)
    for key in _DATE_FIELDS & fields.keys():
        fields[key] = normalize_date(fields[key])
    return fields


# Dates au format de la MRZ (AAAA-MM-JJ) ; une date illisible est laissee telle quelle
_DATE_FIELDS = {"birth_date", "issue_date", "expiry_date"}
_MONTHS = {  # francais et anglais, abreviations comprises ("12 AOUT/AUG 1974")
    1: ("JAN", "JANV", "JANVIER", "JANUARY"), 2: ("FEB", "FEV", "FÉV", "FEVR", "FÉVR", "FEVRIER", "FÉVRIER", "FEBRUARY"),
    3: ("MAR", "MARS", "MARCH"), 4: ("APR", "AVR", "AVRIL", "APRIL"), 5: ("MAY", "MAI"),
    6: ("JUN", "JUIN", "JUNE"), 7: ("JUL", "JUIL", "JUILLET", "JULY"), 8: ("AUG", "AOUT", "AOÛT", "AUGUST"),
    9: ("SEP", "SEPT", "SEPTEMBRE", "SEPTEMBER"), 10: ("OCT", "OCTOBRE", "OCTOBER"),
    11: ("NOV", "NOVEMBRE", "NOVEMBER"), 12: ("DEC", "DÉC", "DECEMBRE", "DÉCEMBRE", "DECEMBER"),
}
_MONTH_NUMBER = {name: num for num, names in _MONTHS.items() for name in names}
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4})(?!\d)")  # JJ-MM-AAAA
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_TEXT_DATE = re.compile(r"(?<!\d)(\d{1,2})\s+([A-ZÀ-Ü]{3,9})\.?(?:\s*/\s*[A-ZÀ-Ü]{3,9}\.?)?\s+(\d{4})(?!\d)", re.IGNORECASE)


def normalize_date(text: str) -> str:
    """'18-06-2000', '21/06/2003', '23-10-2024/060' ou '12 AOUT/AUG 1974' -> 'AAAA-MM-JJ'."""
    if m := _ISO_DATE.search(text):
        y, mo, d = m.groups()
    elif m := _NUMERIC_DATE.search(text):
        d, mo, y = m.groups()
    elif (m := _TEXT_DATE.search(text)) and m.group(2).upper() in _MONTH_NUMBER:
        d, mo, y = m.group(1), _MONTH_NUMBER[m.group(2).upper()], m.group(3)
    else:
        return text
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:  # jour ou mois impossible : lecture douteuse, valeur brute conservee
        return text
