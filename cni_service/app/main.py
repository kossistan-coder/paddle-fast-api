from contextlib import asynccontextmanager
from datetime import date

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .matching import best_line_match, similarity
from .mrz import MrzData, parse_mrz
from .ocr_engine import OcrEngine

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


@app.post("/verify", response_model=VerifyResponse)
def verify(
    file: UploadFile = File(..., description="Image ou PDF de la CNI (face MRZ) ou de la page photo du passeport"),
    firstName: str = Form(..., description="Premier prenom"),
    lastName: str = Form(...),
    birthDate: date = Form(..., description="YYYY-MM-DD"),
    otherFirstNames: str | None = Form(None, description="Autres prenoms, separes par des espaces"),
    profession: str | None = Form(None),
    threshold: float = Form(0.85, ge=0.5, le=1.0, description="Score minimal de similarite des noms"),
):
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Fichier trop volumineux (10 Mo max).")
    ocr = state["ocr"]
    lines: list[str] = []
    mrz = None
    for image in _decode(data):  # une seule iteration pour une image, une par page pour un PDF
        page_lines = ocr.read_lines(image)
        mrz = parse_mrz(page_lines)
        if mrz is None:
            bottom = ocr.read_lines_bottom(image)
            mrz = parse_mrz(bottom)
            page_lines += bottom
        lines += page_lines
        if mrz is not None:
            break
    if mrz is None:
        return VerifyResponse(decision="MRZ_NOT_FOUND")

    given = mrz.given_names
    resp = VerifyResponse(
        decision="",
        mrz_checksums_valid=mrz.checks_valid,
        mrz=_mrz_info(mrz),
        last_name=_name_check(lastName, mrz.surname, threshold),
        first_name=_name_check(firstName, given[0] if given else None, threshold),
    )
    if otherFirstNames and otherFirstNames.strip():
        resp.other_first_names = _name_check(otherFirstNames, " ".join(given[1:]) or None, threshold)

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
