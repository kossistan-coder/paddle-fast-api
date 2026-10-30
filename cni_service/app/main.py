import asyncio
import hmac
import logging
import os
import queue
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from difflib import SequenceMatcher
from typing import Literal

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Security, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from starlette.formparsers import MultiPartParser

from .matching import best_line_match, similarity
from .mrz import MrzData, parse_mrz
from .ocr_engine import OcrEngine
from .viz import parse_visual_zone

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 4
PDF_DPI = 200
MAX_CONCURRENT_ANALYSES = 2  # un moteur OCR (~1,1 Go de RAM) par analyse simultanee
REQUEST_TIMEOUT_S = 25
RETRY_AFTER_S = 10
API_KEY_ENV = "API_KEY"
# Formats MRZ attendus par type de document : cartes (TD1, TD2), passeport (TD3, 2 x 44 caracteres)
PREFERRED_FORMATS = {"cni": {"TD1", "TD2"}, "passport": {"TD3"}}

# Les documents restent en memoire : Starlette ne bascule plus l'upload dans un fichier temporaire.
MultiPartParser.spool_max_size = MAX_UPLOAD_BYTES + 1

log = logging.getLogger("uvicorn.error.cni")  # herite de la configuration de log d'uvicorn
state: dict[str, "queue.Queue[OcrEngine]"] = {}
_api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)


def require_api_key(key: str | None = Security(_api_key_header)) -> None:
    """Exige l'en-tete x-api-key egal a API_KEY ; sans API_KEY definie, tout est refuse."""
    expected = os.environ.get(API_KEY_ENV)
    if not (expected and key and hmac.compare_digest(key.encode(), expected.encode())):
        raise HTTPException(401, "Cle API absente ou invalide (en-tete x-api-key).")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not os.environ.get(API_KEY_ENV):
        log.error("%s non defini : toutes les requetes sur /verify et /extract seront refusees.", API_KEY_ENV)
    pool: queue.Queue[OcrEngine] = queue.Queue()
    for _ in range(MAX_CONCURRENT_ANALYSES):
        pool.put(OcrEngine())
    state["pool"] = pool
    yield
    state.clear()


app = FastAPI(title="Verification CNI / Passeport (MRZ)", lifespan=lifespan)


@app.middleware("http")
async def _limit_and_log(request: Request, call_next):
    """Refuse les corps trop gros avant lecture ; journalise id, duree et decision (jamais de donnees perso)."""
    request_id, start = uuid.uuid4().hex[:12], time.perf_counter()
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_UPLOAD_BYTES + 64 * 1024:  # marge pour les champs du formulaire
        response = JSONResponse({"detail": "Fichier trop volumineux (10 Mo max)."}, 413)
    else:
        response = await call_next(request)
    if request.url.path in ("/verify", "/extract"):
        log.info("request_id=%s path=%s status=%d decision=%s duration_ms=%d", request_id, request.url.path,
                 response.status_code, getattr(request.state, "decision", "-"),
                 (time.perf_counter() - start) * 1000)
    return response


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


Decision = Literal["VERIFIED", "REVIEW", "REJECTED", "MRZ_NOT_FOUND"]


class VerifyResponse(BaseModel):
    decision: Decision = Field(description=(
        "VERIFIED : tous les champs compares concordent et les chiffres de controle de la MRZ sont valides. "
        "REVIEW : tous les champs compares concordent mais un chiffre de controle de la MRZ est faux "
        "(lecture douteuse, a verifier manuellement). "
        "REJECTED : au moins un champ compare (nom, prenoms, date de naissance, date d'expiration) differe. "
        "MRZ_NOT_FOUND : aucune MRZ lisible dans le document ; aucun champ n'est compare. "
        "La profession et le numero de document sont informatifs et n'influencent jamais la decision."))
    mrz_checksums_valid: bool | None = None
    last_name: FieldCheck | None = None
    first_name: FieldCheck | None = None
    other_first_names: FieldCheck | None = None
    birth_date: FieldCheck | None = None
    profession: FieldCheck | None = None  # absente de la MRZ : recherche indicative dans le texte OCR
    mrz: MrzInfo | None = None
    document_number: FieldCheck | None = Field(None, description=(
        "Informatif : le numero imprime sur une carte peut differer de celui de la MRZ."))
    expiry_date: FieldCheck | None = None


class ExtractResponse(BaseModel):
    found: bool  # MRZ trouvee
    mrz_checksums_valid: bool | None = None
    mrz: MrzInfo | None = None
    # Texte imprime hors MRZ (lieu de naissance, profession...) : non verifie par checksum
    visual_zone: dict[str, str] = {}


def _sniff(data: bytes) -> Literal["pdf", "image"]:
    """Format reconnu par les octets d'en-tete, jamais par le type declare."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")) or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return "image"
    raise HTTPException(415, "Format non pris en charge (acceptes : PDF, JPEG, PNG, WebP).")


def _decode(data: bytes) -> list[np.ndarray]:
    """Image unique, ou pages d'un PDF rendues en image (BGR), en memoire uniquement."""
    if _sniff(data) == "pdf":
        import pymupdf

        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception:
            raise HTTPException(422, "PDF illisible ou protege par mot de passe.")
        try:
            if doc.needs_pass:
                raise HTTPException(422, "PDF illisible ou protege par mot de passe.")
            if len(doc) == 0:
                raise HTTPException(422, "PDF vide.")
            if len(doc) > MAX_PDF_PAGES:
                raise HTTPException(422, f"PDF de {len(doc)} pages : {MAX_PDF_PAGES} pages maximum.")
            pages = []
            for page in doc:
                pix = page.get_pixmap(dpi=PDF_DPI, colorspace=pymupdf.csRGB, alpha=False)
                rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
                pages.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            return pages
        finally:
            doc.close()
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(422, "Image illisible ou corrompue.")
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


def _date_check(provided: date, extracted: date | None) -> FieldCheck:
    ok = provided == extracted
    return FieldCheck(provided=provided.isoformat(), extracted=extracted.isoformat() if extracted else None,
                      score=1.0 if ok else 0.0, match=ok)


def normalize_document_number(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _document_number_check(provided: str, extracted: str) -> FieldCheck:
    a, b = normalize_document_number(provided), normalize_document_number(extracted)
    score = SequenceMatcher(None, a, b).ratio() if a and b else 0.0
    return FieldCheck(provided=provided, extracted=extracted, score=round(score, 3), match=bool(a) and a == b)


async def _read_upload(file: UploadFile) -> bytes:
    try:
        data = await file.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await file.close()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Fichier trop volumineux (10 Mo max).")
    _sniff(data)  # 415 avant d'occuper un moteur OCR
    return data


async def _analyze(data: bytes, document_type: str | None, stop_early: bool) -> tuple[MrzData | None, list[str]]:
    """Analyse sur un moteur libre du pool : 503 si tous sont occupes, 504 au-dela du timeout."""
    pool = state["pool"]
    try:
        engine = pool.get_nowait()
    except queue.Empty:
        raise HTTPException(503, "Service occupe, reessayez plus tard.", headers={"Retry-After": str(RETRY_AFTER_S)})

    def job():
        try:
            return _read_mrz(engine, _decode(data), PREFERRED_FORMATS.get(document_type), stop_early)
        finally:
            pool.put(engine)  # rendu meme apres un timeout, une fois l'OCR en cours termine

    try:
        return await asyncio.wait_for(asyncio.to_thread(job), REQUEST_TIMEOUT_S)
    except TimeoutError:
        raise HTTPException(504, f"Analyse trop longue (plus de {REQUEST_TIMEOUT_S} s).")


def _read_mrz(ocr: OcrEngine, images: list[np.ndarray], preferred: set[str] | None,
              stop_early: bool) -> tuple[MrzData | None, list[str]]:
    """OCR page par page (une image, ou une par page de PDF) puis MRZ ; renvoie aussi le texte lu.

    stop_early : s'arreter a la premiere page portant une MRZ (du format prefere s'il est donne) ;
    sinon toutes les pages sont lues (le recto peut porter des champs utiles a /extract).
    La seconde passe, agrandie sur le bas des pages, n'a lieu que si la premiere n'a livre aucune MRZ.
    """
    lines: list[str] = []
    found = None
    for bottom_pass, read in enumerate((ocr.read_lines, ocr.read_lines_bottom)):
        for image in images:
            page = read(image)
            lines += page
            mrz = parse_mrz(page, preferred)
            if mrz and (found is None or (preferred and mrz.format in preferred and found.format not in preferred)):
                found = mrz
            if found and (stop_early or bottom_pass) and (not preferred or found.format in preferred):
                return found, lines
        if found:
            break
    return found, lines


def _first_name_check(provided: str, given: list[str], threshold: float) -> FieldCheck:
    """Compare a chaque prenom de la MRZ et a l'ensemble des prenoms ; garde le meilleur score."""
    candidates = [*given, " ".join(given)] if len(given) > 1 else given
    best = max(candidates, key=lambda c: similarity(provided, c), default=None)
    return _name_check(provided, best, threshold)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/verify", dependencies=[Depends(require_api_key)], response_model=VerifyResponse)
async def verify(
    request: Request,
    file: UploadFile = File(..., description="Image ou PDF (4 pages max) : recto et verso de la CNI, "
                                             "ou page photo du passeport"),
    firstName: str = Form(..., description="Un des prenoms, ou tous les prenoms separes par des espaces"),
    lastName: str = Form(...),
    birthDate: date | None = Form(None, description="YYYY-MM-DD ; verifiee seulement si fournie"),
    otherFirstNames: str | None = Form(None, description="Autres prenoms, separes par des espaces"),
    profession: str | None = Form(None),
    threshold: float = Form(0.85, ge=0.5, le=1.0, description="Score minimal de similarite des noms"),
    documentType: Literal["cni", "passport"] | None = Form(
        None, description="Format MRZ cherche en priorite : cni (TD1/TD2) ou passport (TD3)"),
    documentNumber: str | None = Form(None, description="Numero de la piece ; comparaison informative"),
    documentExpiresAt: date | None = Form(None, description="YYYY-MM-DD ; verifiee seulement si fournie"),
):
    data = await _read_upload(file)
    mrz, lines = await _analyze(data, documentType, stop_early=True)
    if mrz is None:
        request.state.decision = "MRZ_NOT_FOUND"
        return VerifyResponse(decision="MRZ_NOT_FOUND")

    given = mrz.given_names
    resp = VerifyResponse(
        decision="REJECTED",
        mrz_checksums_valid=mrz.checks_valid,
        mrz=_mrz_info(mrz),
        last_name=_name_check(lastName, mrz.surname, threshold),
        first_name=_first_name_check(firstName, given, threshold),
    )
    if otherFirstNames and otherFirstNames.strip():
        resp.other_first_names = _name_check(otherFirstNames, " ".join(given[1:]) or None, threshold)

    if birthDate is not None:
        resp.birth_date = _date_check(birthDate, mrz.birth_date)
    if documentExpiresAt is not None:
        resp.expiry_date = _date_check(documentExpiresAt, mrz.expiry_date)
    if documentNumber and documentNumber.strip():
        resp.document_number = _document_number_check(documentNumber, mrz.doc_number)

    if profession and profession.strip():
        score, line = best_line_match(profession, lines)
        resp.profession = FieldCheck(provided=profession, extracted=line, score=round(score, 3),
                                     match=score >= threshold)

    # La profession et le numero de document sont informatifs uniquement.
    required = [resp.last_name, resp.first_name, resp.birth_date, resp.other_first_names, resp.expiry_date]
    all_match = all(c.match for c in required if c is not None)
    if all_match and mrz.checks_valid:
        resp.decision = "VERIFIED"
    elif all_match:
        resp.decision = "REVIEW"  # concordance mais lecture MRZ douteuse (check digits)
    request.state.decision = resp.decision
    return resp


@app.post("/extract", dependencies=[Depends(require_api_key)], response_model=ExtractResponse)
async def extract(
    request: Request,
    file: UploadFile = File(..., description="Image ou PDF de la CNI (face MRZ) ou de la page photo du passeport"),
):
    data = await _read_upload(file)
    mrz, lines = await _analyze(data, None, stop_early=False)
    visual_zone = parse_visual_zone(lines)
    request.state.decision = "MRZ_FOUND" if mrz else "MRZ_NOT_FOUND"
    if mrz is None:
        return ExtractResponse(found=False, visual_zone=visual_zone)
    return ExtractResponse(found=True, mrz_checksums_valid=mrz.checks_valid, mrz=_mrz_info(mrz),
                           visual_zone=visual_zone)
