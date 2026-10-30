from contextlib import asynccontextmanager
from datetime import date

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .matching import best_line_match, similarity
from .mrz import MrzData, parse_mrz
from .ocr_engine import OcrEngine
from .viz import parse_visual_zone

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 4
PDF_DPI = 200
state: dict[str, OcrEngine] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    state["ocr"] = OcrEngine()
    yield
    state.clear()


app = FastAPI(title="Verification CNI / Passeport (MRZ)", lifespan=lifespan)


class FieldCheck(BaseModel):
    provided: str
    extracted: str | None
    score: float
    match: bool


class MrzInfo(BaseModel):
    format: str
    document_type: str
    issuer: str
    nationality: str
    document_number: str
    surname: str
    given_names: list[str]
    birth_date: date | None
    expiry_date: date | None
    sex: str
    checks: dict[str, bool]
    lines: list[str]


class VerifyResponse(BaseModel):
    decision: str  # VERIFIED | REVIEW | REJECTED | MRZ_NOT_FOUND
    mrz_checksums_valid: bool | None = None
    last_name: FieldCheck | None = None
    first_name: FieldCheck | None = None
    other_first_names: FieldCheck | None = None
    birth_date: FieldCheck | None = None
    profession: FieldCheck | None = None  # absente de la MRZ : recherche indicative dans le texte OCR
    mrz: MrzInfo | None = None


class ExtractResponse(BaseModel):
    found: bool  # MRZ trouvee
    mrz_checksums_valid: bool | None = None
    mrz: MrzInfo | None = None
    # Texte imprime hors MRZ (lieu de naissance, profession...) : non verifie par checksum
    visual_zone: dict[str, str] = {}


def _decode(data: bytes) -> list[np.ndarray]:
    """Image unique, ou pages d'un PDF rendues en image (BGR)."""
    if data.startswith(b"%PDF"):
        import pymupdf

        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception:
            raise HTTPException(422, "PDF illisible ou protege par mot de passe.")
        if doc.needs_pass:
            raise HTTPException(422, "PDF illisible ou protege par mot de passe.")
        pages = []
        for i in range(min(len(doc), MAX_PDF_PAGES)):
            page = doc[i]
            pix = page.get_pixmap(dpi=PDF_DPI, colorspace=pymupdf.csRGB, alpha=False)
            rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
            pages.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        if not pages:
            raise HTTPException(422, "PDF vide.")
        return pages
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(422, "Fichier illisible (formats acceptes : PDF, JPEG, PNG, BMP, WEBP).")
    return [image]


def _mrz_info(m: MrzData) -> MrzInfo:
    return MrzInfo(format=m.format, document_type=m.doc_type, issuer=m.issuer,
                   nationality=m.nationality, document_number=m.doc_number, surname=m.surname,
                   given_names=m.given_names, birth_date=m.birth_date, expiry_date=m.expiry_date,
                   sex=m.sex, checks=m.checks, lines=m.lines)


def _name_check(provided: str, extracted: str | None, threshold: float) -> FieldCheck:
    score = similarity(provided, extracted) if extracted else 0.0
    return FieldCheck(provided=provided, extracted=extracted, score=round(score, 3),
                      match=score >= threshold)


def _read_mrz(file: UploadFile) -> tuple[MrzData | None, list[str]]:
    """OCR de toutes les pages (le recto peut porter des champs utiles) puis MRZ ; renvoie aussi le texte lu.

    La seconde passe, agrandie sur le bas des pages, n'a lieu que si aucune page n'a livre de MRZ.
    """
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Fichier trop volumineux (10 Mo max).")
    ocr = state["ocr"]
    images = _decode(data)  # une image, ou une par page pour un PDF
    pages = [ocr.read_lines(image) for image in images]
    lines = [line for page in pages for line in page]
    mrz = next((m for m in map(parse_mrz, pages) if m is not None), None)
    for image in images if mrz is None else []:
        bottom = ocr.read_lines_bottom(image)
        lines += bottom
        mrz = parse_mrz(bottom)
        if mrz is not None:
            break
    return mrz, lines


def _first_name_check(provided: str, given: list[str], threshold: float) -> FieldCheck:
    """Compare a chaque prenom de la MRZ et a l'ensemble des prenoms ; garde le meilleur score."""
    candidates = [*given, " ".join(given)] if len(given) > 1 else given
    best = max(candidates, key=lambda c: similarity(provided, c), default=None)
    return _name_check(provided, best, threshold)


@app.post("/verify", response_model=VerifyResponse)
def verify(
    file: UploadFile = File(..., description="Image ou PDF de la CNI (face MRZ) ou de la page photo du passeport"),
    firstName: str = Form(..., description="Un des prenoms, ou tous les prenoms separes par des espaces"),
    lastName: str = Form(...),
    birthDate: date | None = Form(None, description="YYYY-MM-DD ; verifiee seulement si fournie"),
    otherFirstNames: str | None = Form(None, description="Autres prenoms, separes par des espaces"),
    profession: str | None = Form(None),
    threshold: float = Form(0.85, ge=0.5, le=1.0, description="Score minimal de similarite des noms"),
):
    mrz, lines = _read_mrz(file)
    if mrz is None:
        return VerifyResponse(decision="MRZ_NOT_FOUND")

    given = mrz.given_names
    resp = VerifyResponse(
        decision="",
        mrz_checksums_valid=mrz.checks_valid,
        mrz=_mrz_info(mrz),
        last_name=_name_check(lastName, mrz.surname, threshold),
        first_name=_first_name_check(firstName, given, threshold),
    )
    if otherFirstNames and otherFirstNames.strip():
        resp.other_first_names = _name_check(otherFirstNames, " ".join(given[1:]) or None, threshold)

    if birthDate is not None:
        birth_ok = mrz.birth_date == birthDate
        resp.birth_date = FieldCheck(
            provided=birthDate.isoformat(),
            extracted=mrz.birth_date.isoformat() if mrz.birth_date else None,
            score=1.0 if birth_ok else 0.0, match=birth_ok)

    if profession and profession.strip():
        score, line = best_line_match(profession, lines)
        resp.profession = FieldCheck(provided=profession, extracted=line, score=round(score, 3),
                                     match=score >= threshold)

    # La profession est informative uniquement (absente de la MRZ).
    required = [resp.last_name, resp.first_name, resp.birth_date, resp.other_first_names]
    all_match = all(c.match for c in required if c is not None)
    if all_match and mrz.checks_valid:
        resp.decision = "VERIFIED"
    elif all_match:
        resp.decision = "REVIEW"  # concordance mais lecture MRZ douteuse (check digits)
    else:
        resp.decision = "REJECTED"
    return resp


@app.post("/extract", response_model=ExtractResponse)
def extract(
    file: UploadFile = File(..., description="Image ou PDF de la CNI (face MRZ) ou de la page photo du passeport"),
):
    mrz, lines = _read_mrz(file)
    visual_zone = parse_visual_zone(lines)
    if mrz is None:
        return ExtractResponse(found=False, visual_zone=visual_zone)
    return ExtractResponse(found=True, mrz_checksums_valid=mrz.checks_valid, mrz=_mrz_info(mrz),
                           visual_zone=visual_zone)
