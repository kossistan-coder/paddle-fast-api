"""Champs de la zone visuelle (texte imprime hors MRZ), reperes par leurs libelles.

Deux mises en page : CNI togolaise ("Libelle: valeur" sur une ligne) et passeport (libelle bilingue
"Lieu de naissance / Place of birth", valeur imprimee sous le libelle). Les libelles absents sont ignores.
Ces valeurs ne sont pas protegees par des chiffres de controle : moins fiables que la MRZ.
"""
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher

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


# Libelles de passeport, sans deux-points obligatoires. La norme ICAO 9303 impose des libelles en anglais,
# francais ou espagnol en plus de la langue nationale : ces trois langues couvrent tout passeport conforme ;
# le portugais, l'italien et l'allemand sont reconnus en plus. Imprimes en petit, les libelles sont souvent
# mal lus : espaces avales ("PLACE OFISSUE"), "/" lu "I" ("IDATE OF BIRTH"), lettres confondues
# ("LEUDE NAISSANCE", "PRENONS", "NATIONAUITE", "5EX") ; une seule langue lisible suffit.
_PASSPORT_LABELS = {
    "last_name": r"Noms?|Surnames?|Apellidos?|Sobrenomes?|Cognome|Nachname|Familienname|(?<!Given\s)(?<!Given)Name",
    "first_names": r"Pr[eé]no[mn]s?|Given\s*names?|Nombres?|Nomes?|Nomi|Vornamen?",
    "nationality": r"Nationa[lu]it[eé]|Nationality|Nacionalidad(?:e)?|Nazionalit[aà]|Cittadinanza|Staatsangeh[oö]rigkeit",
    "birth_date": r"Date\s*de\s*naissance|Date\s*of\s*birth|Fecha\s*de\s*nacimiento|Data\s*d[eoi]\s*nas[cç]\w*"
                  r"|Geburts(?:datum|tag)",
    "place_of_birth": r"L[i1]?eu\s*de?\s*naissance|Place\s*of\s*birth|Lugar\s*de\s*nacimiento|Natu?ra?l[i1]?dade"  # "NATRALDADE"
                      r"|Local\s*de\s*nascimento|Luogo\s*di\s*nascita|Geburtsort",
    "sex": r"[S5]exe?|Sexo|Sesso|Geschlecht",
    "issue_date": r"Date\s*(?:de\s*)?(?:d[eé]l\w*ance|l?['/]?\s*[eé]mission)|Date\s*of\s*issue"
                  r"|Fecha\s*de\s*(?:expedici[oó]n|emisi[oó]n)|Data\s*de\s*(?:expedi[cç][aã]o|emiss[aã]o)"
                  r"|Data\s*di\s*rilascio|Ausstellungsdatum|Datum\s*der\s*Ausstellung",
    "expiry_date": r"Date\s*d['/]?\s*expiration|Date\s*of\s*expiry|Fecha\s*de\s*(?:caducidad|vencimiento|expiraci[oó]n)"
                   r"|V[aá]lido\s*at[eé]|Data\s*de\s*validade|Data\s*di\s*scadenza|G[uü]ltig\s*bis",
    "place_of_issue": r"L[i1]?eu\s*(?:de\s*)?(?:d[eé]l\w*ance|d?['/]?\s*[eé]mission)|Place\s*of\s*issue"
                      r"|Lugar\s*de\s*(?:expedici[oó]n|emisi[oó]n)|Local\s*de\s*(?:emiss[aã]o|expedi[cç][aã]o)"
                      r"|Luogo\s*di\s*rilascio|Ausstellungsort",
    "issuing_authority": r"Autorit[eé](?:\s*de\s*d[eé]livrance)?|(?:Issuing\s*)?Authority|D[eé]livr[eé]\s*par"
                         r"|Autoridad(?:e)?|Autorit[aà]|Beh[oö]rde",
    "document_number": r"N[°o]\s*(?:du\s*)?passeport|Passe?port\s*(?:No|N[°o]|number)|Pas{1,2}aporte?\s*N[°o]"
                       r"|N[uú]mero\s*de\s*pasaporte|Passaporto\s*N|Pass(?:nummer|-Nr)",
    "personal_number": r"Personal\s*(?:No|N[°o]|number)|CPF|N[uú]mero\s*personal|Pers[oö]nliche\s*Nummer",
    "profession": r"Profession|Occupation|Profesi[oó]n|Profiss[aã]o|Beruf",
    "height": r"Ta[il1]{2,3}e|H[ce][il1]?ght|Altura|Statura|Gr[oö](?:ß|ss)e",  # "Tallle/Hcight"
    "eye_color": r"Couleur\s*des\s*yeux|Colou?r\s*of\s*eyes|Color\s*de\s*(?:los\s*)?ojos|Cor\s*dos\s*olhos"
                 r"|Colore\s*degli\s*occhi|Augenfarbe",
}
# Comme _L, mais un "I" (barre oblique mal lue) ou un chiffre peut preceder le libelle
_LP = r"(?<![A-HJ-Za-hj-zÀ-ÿ])"
# Libelles assez distinctifs pour etre reconnus meme colles au mot suivant ("Couleur des yeuxColour of eyes",
# "NATURALIDADEPLACE OF BIRTH")
_GLUED_OK = {"eye_color", "place_of_birth"}
_PASSPORT = {k: re.compile(_LP + r"(?:" + p + r")" + ("" if k in _GLUED_OK else r"(?![A-Za-zÀ-ÿ])"), re.IGNORECASE)
             for k, p in _PASSPORT_LABELS.items()}
_SEPARATORS = " /:.,;|-"
_MAX_COLUMN_SHIFT = 0.12  # ecart horizontal maximal (en largeur de page) entre un libelle et sa valeur
_ROWS_BELOW = 2  # la valeur est cherchee sur les 2 lignes suivantes (photo inclinee : lignes melangees)


# Repli approximatif pour les libelles trop abimes pour les motifs ("Natlonallte/Notionality",
# "Ueu de naissance/Ploce of birth", "Autorlté/Auchority") : comparaison a ces textes, sans accents.
# Les cles "_" sont des libelles sans champ renvoye : reconnus pour ne pas etre pris pour des valeurs.
_LABEL_TEXTS = {
    "first_names": ("PRENOMS", "GIVEN NAMES", "NOMBRES"),
    "nationality": ("NATIONALITE", "NATIONALITY", "NACIONALIDAD", "NACIONALIDADE", "CITTADINANZA"),
    "birth_date": ("DATE DE NAISSANCE", "DATE OF BIRTH", "FECHA DE NACIMIENTO", "DATA DE NASCIMENTO",
                   "DATA DO NASCIMENTO", "DATA DI NASCITA"),
    "place_of_birth": ("LIEU DE NAISSANCE", "PLACE OF BIRTH", "LUGAR DE NACIMIENTO", "NATURALIDADE",
                       "LUOGO DI NASCITA"),
    "issue_date": ("DATE DE DELIVRANCE", "DATE D EMISSION", "DATE OF ISSUE", "FECHA DE EXPEDICION",
                   "DATA DE EXPEDICAO", "DATA DI RILASCIO"),
    "expiry_date": ("DATE D EXPIRATION", "DATE OF EXPIRY", "FECHA DE CADUCIDAD", "FECHA DE VENCIMIENTO",
                    "VALIDO ATE", "DATA DI SCADENZA"),
    "place_of_issue": ("LIEU DE DELIVRANCE", "LIEU D EMISSION", "PLACE OF ISSUE", "LUGAR DE EXPEDICION"),
    "issuing_authority": ("AUTORITE", "AUTHORITY", "ISSUING AUTHORITY", "AUTORIDAD", "AUTORIDADE"),
    "personal_number": ("PERSONAL NUMBER", "PERSONAL NO"),
    "profession": ("PROFESSION", "OCCUPATION", "PROFESION", "PROFISSAO"),
    "height": ("TAILLE", "HEIGHT"),
    "eye_color": ("COULEUR DES YEUX", "COLOUR OF EYES", "COLOR OF EYES"),
    # "PASSEPORT" seul est le titre de la page : trop proche de "Passeport n°", le numero n'a pas de repli
    "_other": ("PASSEPORT", "PASSPORT", "PASAPORTE", "PASSAPORTE", "CODE DU PAYS", "COUNTRY CODE",
               "DOMICILE", "RESIDENCE", "SIGNATURE DU TITULAIRE", "HOLDER S SIGNATURE",
               "FILIATION", "FILIACAO", "FILIACION"),
}
_FUZZY_MIN = 0.82


def _plain(text: str) -> str:
    """Majuscules sans accents, renvois "(4)" retires, ponctuation remplacee par des espaces."""
    text = unicodedata.normalize("NFKD", re.sub(r"\(?\d{1,2}\)", " ", text))
    text = "".join(c for c in text if not unicodedata.combining(c)).upper()
    return " ".join(re.sub(r"[^A-Z]+", " ", text).split())


def _fuzzy_keys(text: str) -> list[str]:
    parts = {_plain(p) for p in re.split(r"/|\s+I(?=[A-Z])", text)} | {_plain(text)}
    best = max(((SequenceMatcher(None, p, label).ratio(), key)
                for p in parts if p for key, labels in _LABEL_TEXTS.items() for label in labels), default=(0, None))
    return [best[1]] if best[0] >= _FUZZY_MIN else []


def _is_label_start(text: str, start: int) -> bool:
    """Un libelle ouvre le fragment, ou suit un "/" de libelle bilingue ; pas un mot au milieu d'une phrase
    ("Pagina riservata all'autorità" n'est pas le libelle "Autorité")."""
    prefix = text[:start].strip(_SEPARATORS)
    bilingual = "/" in text[:start] or prefix.endswith(("I", "l", "|"))  # "/" souvent lu "I" ("NOMEIGIVEN NAMES")
    return len(prefix) <= 3 or (bilingual and len(prefix.split()) <= 4)


def _label_keys(text: str) -> tuple[list[str], str]:
    """Champs de passeport nommes dans le fragment (de gauche a droite), et ce qui reste sans les libelles."""
    starts = {k: m.start() for k, p in _PASSPORT.items() if (m := p.search(text))}
    if starts and not _is_label_start(text, min(starts.values())):
        return [], text
    keys = sorted(starts, key=starts.get)
    rest = text
    for k in keys:
        rest = _PASSPORT[k].sub(" ", rest)
    return keys or _fuzzy_keys(text), rest.strip(_SEPARATORS)


def _fragments(line: str) -> tuple[tuple[str, float], ...]:
    return getattr(line, "fragments", ()) or ((line, 0.0),)


# Forme attendue de chaque valeur : une valeur qui ne colle pas est ignoree au lieu d'etre attribuee a tort
# (taille lue sous "Couleur des yeux" quand le libelle "Sexe" voisin n'a pas ete lu, morceau de libelle...)
_SEX_WORDS = r"MASCULIN[OE]?|F[EÉ]MININ[OE]?|FEMENINO|MALE|FEMALE|M[AÄ]NNLICH|WEIBLICH"
_PLAUSIBLE = {
    "sex": re.compile(r"[MFX](?:\s*/\s*[MFX])?|" + _SEX_WORDS, re.IGNORECASE),
    "height": re.compile(r".*\d.*(?:\bm|cm)\b.*|\d[.,]\d{2}", re.IGNORECASE),  # "1,79 m", "179 cm", "1.79"
    "eye_color": re.compile(r"[^\d]{3,}"),
    "birth_date": re.compile(r".*\d{4}.*"), "issue_date": re.compile(r".*\d{4}.*"), "expiry_date": re.compile(r".*\d{4}.*"),
    "document_number": re.compile(r"(?=.*\d)[A-Z0-9 ]{6,12}", re.IGNORECASE),
    "personal_number": re.compile(r".*\d{4}.*"),
    "last_name": re.compile(r"[^\d]+"), "first_names": re.compile(r"[^\d]+"),
}
_LABEL_REF = re.compile(r"\(\s*\d{1,2}\s*\)|\d{1,2}\)\s*$")  # renvoi "(2)" : morceau de libelle, jamais une valeur


def _plausible(key: str, value: str) -> bool:
    if _LABEL_REF.search(value):
        return False
    pattern = _PLAUSIBLE.get(key)
    return pattern is None or bool(pattern.fullmatch(value.strip()))


def _value_below(rows: list, i: int, x: float, key: str) -> str | None:
    """Premier fragment aligne sur x dans les lignes suivantes, ni libelle ni MRZ, de la forme attendue."""
    for row in rows[i + 1:i + 1 + _ROWS_BELOW]:
        aligned = [(abs(bx - x), bt.strip(_SEPARATORS)) for bt, bx in row
                   if "<" not in bt and bt.strip(_SEPARATORS) and abs(bx - x) <= _MAX_COLUMN_SHIFT]
        labels = [a for a in aligned if _label_keys(a[1])[0]]
        values = [a for a in aligned if a not in labels and _plausible(key, a[1])]
        if values:
            return min(values)[1]
        if labels:  # un autre libelle occupe la colonne : la valeur n'a pas ete lue
            return None
    return None


def _inline_value(text: str, key: str) -> str | None:
    """Valeur ecrite apres le libelle dans le meme fragment ("Place of birth: LOME")."""
    if key not in _PASSPORT or not _PASSPORT[key].match(text.lstrip(_SEPARATORS)):
        return None
    end = max(m.end() for p in _PASSPORT.values() for m in p.finditer(text))
    value = text[end:].strip(_SEPARATORS)
    return value if value and _plausible(key, value) else None


def _values_below(rows: list, i: int, x: float, right: float) -> list[str]:
    """Valeurs (non libelles) de la premiere ligne suivante qui en a entre x et la colonne suivante, de gauche a droite."""
    for row in rows[i + 1:i + 1 + _ROWS_BELOW]:
        values = sorted((bx, bt.strip(_SEPARATORS)) for bt, bx in row
                        if "<" not in bt and bt.strip(_SEPARATORS) and x - _MAX_COLUMN_SHIFT <= bx < right
                        and not _label_keys(bt)[0])
        if values:
            return [v for _, v in values]
    return []


def _passport_fields(lines: list[str]) -> dict[str, str]:
    """Valeur imprimee sous le libelle (mise en page des passeports), sinon apres lui dans le meme fragment."""
    fields: dict[str, str] = {}
    rows = [_fragments(line) for line in lines]
    for i, row in enumerate(rows):
        for text, x in row:
            keys, _ = _label_keys(text)
            if len(keys) > 1:  # "Sexe/Sex Taille/Height Couleur des yeux" lus en un fragment, valeurs separees
                right = min((ox for _, ox in row if ox > x), default=float("inf"))
                values = iter(_values_below(rows, i, x, right))
                for key in keys:  # dans l'ordre, en sautant les valeurs qui n'ont pas la forme du champ
                    value = next((v for v in values if _plausible(key, v)), None)
                    if value is None:
                        break
                    if not key.startswith("_"):
                        fields.setdefault(key, value)
                continue
            if not keys or keys[0] in fields or keys[0].startswith("_"):
                continue
            value = _value_below(rows, i, x, keys[0]) or _inline_value(text, keys[0])
            if value:
                fields[keys[0]] = value
    return fields


# Numero de la carte (celui de la MRZ, parfois sans zero de tete), imprime seul, sans libelle,
# au bout d'une ligne qui porte un autre champ ("Signes particuliers: NEANT        03632219")
_CARD_NUMBER = re.compile(r"\d{8,10}")


def _split_card_number(line: str) -> tuple[str, str | None]:
    """Retire de la ligne un fragment isole fait uniquement de chiffres ; renvoie (ligne, numero)."""
    frags = _fragments(line)
    numbers = [t for t, _ in frags if _CARD_NUMBER.fullmatch(t.strip()) and normalize_date(t) == t]  # pas une date
    has_label = any(p.search(line) for p in _PATTERNS.values())  # ligne "Libelle: valeur" de la CNI
    if len(frags) < 2 or not numbers or "<" in line or not has_label:
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
    if "personal_number" in fields:  # meme format que la MRZ : "024.487.232-57" -> "02448723257"
        fields["personal_number"] = re.sub(r"[^A-Z0-9]", "", fields["personal_number"].upper())
    return fields


# Dates au format de la MRZ (AAAA-MM-JJ) ; une date illisible est laissee telle quelle
_DATE_FIELDS = {"birth_date", "issue_date", "expiry_date"}
_MONTHS = {  # FR, EN, ES, PT, IT, DE, abreviations comprises ("12 AOUT/AUG 1974", "11 SET/SEP 2026")
    1: ("JAN", "JANV", "JANVIER", "JANUARY", "ENE", "ENERO", "JANEIRO", "GEN", "GENNAIO", "JANUAR"),
    2: ("FEB", "FEV", "FÉV", "FEVR", "FÉVR", "FEVRIER", "FÉVRIER", "FEBRUARY", "FEBRERO", "FEVEREIRO",
        "FEBBRAIO", "FEBRUAR"),
    3: ("MAR", "MARS", "MARCH", "MARZO", "MARÇO", "MARCO", "MÄR", "MRZ", "MÄRZ", "MAERZ"),
    4: ("APR", "AVR", "AVRIL", "APRIL", "ABR", "ABRIL", "APRILE"),
    5: ("MAY", "MAI", "MAYO", "MAIO", "MAG", "MAGGIO"),
    6: ("JUN", "JUIN", "JUNE", "JUNIO", "JUNHO", "GIU", "GIUGNO", "JUNI"),
    7: ("JUL", "JUIL", "JUILLET", "JULY", "JULIO", "JULHO", "LUG", "LUGLIO", "JULI"),
    8: ("AUG", "AOUT", "AOÛT", "AUGUST", "AGO", "AGOSTO"),
    9: ("SEP", "SEPT", "SEPTEMBRE", "SEPTEMBER", "SEPTIEMBRE", "SET", "SETEMBRO", "SETTEMBRE"),
    10: ("OCT", "OCTOBRE", "OCTOBER", "OCTUBRE", "OUT", "OUTUBRO", "OTT", "OTTOBRE", "OKT", "OKTOBER"),
    11: ("NOV", "NOVEMBRE", "NOVEMBER", "NOVIEMBRE", "NOVEMBRO"),
    12: ("DEC", "DÉC", "DECEMBRE", "DÉCEMBRE", "DECEMBER", "DIC", "DICIEMBRE", "DEZ", "DEZEMBRO", "DICEMBRE",
         "DEZEMBER"),
}
_MONTH_NUMBER = {name: num for num, names in _MONTHS.items() for name in names}
# JJ-MM-AAAA, JJ/MM/AAAA, "JJ MM AAAA", ou chiffres colles par l'OCR ("22051987", "15 032007")
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4})(?!\d)"
                           r"|(?<!\d)(\d{2})\s*(\d{2})\s*(\d{4})(?!\d)")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_TEXT_DATE = re.compile(r"(?<!\d)(\d{1,2})\s+([A-ZÀ-Ü]{3,9})\.?(?:\s*/\s*[A-ZÀ-Ü]{3,9}\.?)?\s+(\d{4})(?!\d)", re.IGNORECASE)


def normalize_date(text: str) -> str:
    """'18-06-2000', '21/06/2003', '23-10-2024/060' ou '12 AOUT/AUG 1974' -> 'AAAA-MM-JJ'."""
    if m := _ISO_DATE.search(text):
        y, mo, d = m.groups()
    elif m := _NUMERIC_DATE.search(text):
        d, mo, y = m.groups()[:3] if m.group(1) else m.groups()[3:]
    elif (m := _TEXT_DATE.search(text)) and m.group(2).upper() in _MONTH_NUMBER:
        d, mo, y = m.group(1), _MONTH_NUMBER[m.group(2).upper()], m.group(3)
    else:
        return text
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:  # jour ou mois impossible : lecture douteuse, valeur brute conservee
        return text
