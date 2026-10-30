"""Normalisation et comparaison floue des noms saisis avec ceux de la MRZ."""
import re
import unicodedata
from difflib import SequenceMatcher


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ß", "SS").replace("Æ", "AE").replace("Œ", "OE")
    return re.sub(r"[^A-Z0-9]+", " ", s.upper()).strip()


def similarity(a: str, b: str) -> float:
    """Ratio [0,1], insensible a la casse, aux accents et a l'ordre des mots."""
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    direct = SequenceMatcher(None, a, b).ratio()
    reordered = SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
    return max(direct, reordered)


def best_line_match(needle: str, lines: list[str]) -> tuple[float, str | None]:
    """Meilleure correspondance de `needle` dans les lignes OCR (sous-chaine floue incluse)."""
    n = normalize(needle)
    best, best_line = 0.0, None
    for line in lines:
        h = normalize(line)
        if not h or not n:
            continue
        if n in h:
            return 1.0, line
        score = SequenceMatcher(None, n, h).ratio()
        # fenetre glissante pour une ligne plus longue que la valeur cherchee
        if len(h) > len(n):
            for i in range(len(h) - len(n) + 1):
                score = max(score, SequenceMatcher(None, n, h[i:i + len(n)]).ratio())
        if score > best:
            best, best_line = score, line
    return best, best_line
