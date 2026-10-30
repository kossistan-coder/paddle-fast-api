"""Parsing et validation de la zone MRZ (ICAO 9303) : TD1 (CNI 3 lignes), TD2 et TD3 (passeport)."""
import re
from dataclasses import dataclass, field
from datetime import date

WEIGHTS = (7, 3, 1)
LENGTHS = {30: "TD1", 36: "TD2", 44: "TD3"}
LINE_COUNT = {"TD1": 3, "TD2": 2, "TD3": 2}

_TO_DIGIT = str.maketrans("OQDILZSBG", "000112586")
_TO_ALPHA = str.maketrans("012586", "OIZSBG")


def _char_value(c: str) -> int:
    if c.isdigit():
        return int(c)
    if c == "<":
        return 0
    return ord(c) - 55


def check_digit(data: str) -> str:
    return str(sum(_char_value(c) * WEIGHTS[i % 3] for i, c in enumerate(data)) % 10)


def clean_line(text: str) -> str:
    text = text.upper().replace(" ", "").replace("«", "<").replace("‹", "<")
    return re.sub(r"[^A-Z0-9<]", "", text)


def _fit(line: str) -> str | None:
    """Ramene une ligne candidate a 30/36/44 caracteres (tolerance de 4 caracteres)."""
    best = min(LENGTHS, key=lambda n: abs(n - len(line)))
    if abs(best - len(line)) > 4:
        return None
    return line[:best].ljust(best, "<")


def _digits(s: str) -> str:
    return s.translate(_TO_DIGIT)


def _alpha(s: str) -> str:
    return s.translate(_TO_ALPHA)


def _parse_date(s: str, birth: bool) -> date | None:
    s = _digits(s)
    if not s.isdigit() or len(s) != 6:
        return None
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:])
    year = (1900 if yy > date.today().year % 100 else 2000) + yy if birth else 2000 + yy
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def _parse_names(field_: str) -> tuple[str, list[str]]:
    # L'OCR lit souvent le remplissage "<<<<" comme "KKKK", "CCCC" ou "LLLL".
    field_ = re.sub(r"[KCL]{3,}$", lambda m: "<" * len(m.group()), field_)
    field_ = _alpha(field_)
    surname, _, given = field_.partition("<<")
    clean = lambda s: [w for w in s.split("<") if w]
    return " ".join(clean(surname)), clean(given)


@dataclass
class MrzData:
    format: str
    doc_type: str
    issuer: str
    surname: str
    given_names: list[str]
    doc_number: str
    nationality: str
    birth_date: date | None
    sex: str
    expiry_date: date | None
    checks: dict[str, bool] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)

    @property
    def checks_valid(self) -> bool:
        return all(self.checks.values())


def _chk(value: str, digit: str) -> bool:
    return check_digit(value) == _digits(digit)


def _parse_td3(l1: str, l2: str) -> MrzData:
    surname, given = _parse_names(l1[5:44])
    checks = {
        "doc_number": _chk(l2[0:9], l2[9]),
        "birth_date": _chk(_digits(l2[13:19]), l2[19]),
        "expiry_date": _chk(_digits(l2[21:27]), l2[27]),
        "composite": _chk(l2[0:10] + _digits(l2[13:20]) + _digits(l2[21:28]) + l2[28:43], l2[43]),
    }
    return MrzData("TD3", l1[0:2].strip("<"), _alpha(l1[2:5]), surname, given, l2[0:9].strip("<"),
                   _alpha(l2[10:13]), _parse_date(l2[13:19], True), l2[20],
                   _parse_date(l2[21:27], False), checks, [l1, l2])


def _parse_td2(l1: str, l2: str) -> MrzData:
    surname, given = _parse_names(l1[5:36])
    checks = {
        "doc_number": _chk(l2[0:9], l2[9]),
        "birth_date": _chk(_digits(l2[13:19]), l2[19]),
        "expiry_date": _chk(_digits(l2[21:27]), l2[27]),
        "composite": _chk(l2[0:10] + _digits(l2[13:20]) + _digits(l2[21:28]) + l2[28:35], l2[35]),
    }
    return MrzData("TD2", l1[0:2].strip("<"), _alpha(l1[2:5]), surname, given, l2[0:9].strip("<"),
                   _alpha(l2[10:13]), _parse_date(l2[13:19], True), l2[20],
                   _parse_date(l2[21:27], False), checks, [l1, l2])


def _parse_td1(l1: str, l2: str, l3: str) -> MrzData:
    surname, given = _parse_names(l3)
    checks = {
        "doc_number": _chk(l1[5:14], l1[14]),
        "birth_date": _chk(_digits(l2[0:6]), l2[6]),
        "expiry_date": _chk(_digits(l2[8:14]), l2[14]),
        "composite": _chk(l1[5:30] + _digits(l2[0:7]) + _digits(l2[8:15]) + l2[18:29], l2[29]),
    }
    return MrzData("TD1", l1[0:2].strip("<"), _alpha(l1[2:5]), surname, given, l1[5:14].strip("<"),
                   _alpha(l2[15:18]), _parse_date(l2[0:6], True), l2[7],
                   _parse_date(l2[8:14], False), checks, [l1, l2, l3])


def parse_mrz(raw_lines: list[str], preferred: set[str] | None = None) -> MrzData | None:
    """Cherche dans les lignes OCR (ordre de lecture) un bloc MRZ valide et le parse.

    Si plusieurs blocs sont possibles, un format de `preferred` (ex. {"TD3"}) l'emporte,
    puis celui dont les check digits passent.
    """
    candidates = []
    for raw in raw_lines:
        line = clean_line(raw)
        if len(line) >= 26 and "<" in line:
            fitted = _fit(line)
            if fitted:
                candidates.append(fitted)

    parsers = {"TD1": _parse_td1, "TD2": _parse_td2, "TD3": _parse_td3}
    results = []
    for i in range(len(candidates)):
        for fmt, n in LINE_COUNT.items():
            group = candidates[i:i + n]
            if len(group) == n and all(LENGTHS[len(g)] == fmt for g in group):
                results.append(parsers[fmt](*group))
    if not results:
        return None
    return max(results, key=lambda m: (bool(preferred) and m.format in preferred, sum(m.checks.values())))
