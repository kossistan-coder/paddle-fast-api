"""Tests des routes avec un faux moteur OCR (aucun modele charge)."""
import queue
import time

import cv2
import numpy as np
import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import main
from tests.test_mrz import TD1

KEY = "test-key"
NAMES = {"firstName": "Anna", "lastName": "Eriksson"}


class FakeOcr:
    """Renvoie la MRZ TD1 pour une page sombre, du texte sans MRZ pour une page claire."""

    def __init__(self, delay: float = 0):
        self.calls, self.delay = 0, delay

    def read_lines(self, image):
        self.calls += 1
        time.sleep(self.delay)
        return list(TD1) if image.mean() < 128 else ["CARTE NATIONALE D'IDENTITE"]

    def read_lines_bottom(self, image):
        return []


@pytest.fixture
def ocr(monkeypatch):
    monkeypatch.setenv("API_KEY", KEY)
    engine = FakeOcr()
    pool = queue.Queue()
    pool.put(engine)
    main.state["pool"] = pool
    return engine


client = TestClient(main.app)  # sans "with" : le lifespan (vrais modeles) n'est pas lance


def png(dark: bool) -> bytes:
    return cv2.imencode(".png", np.full((20, 20, 3), 0 if dark else 255, np.uint8))[1].tobytes()


def pdf(*dark_pages: bool) -> bytes:
    doc = pymupdf.open()
    for dark in dark_pages:
        page = doc.new_page(width=72, height=72)
        if dark:
            page.draw_rect(page.rect, color=(0, 0, 0), fill=(0, 0, 0))
    return doc.tobytes()


def post(route="/verify", content=None, key=KEY, **form):
    headers = {"x-api-key": key} if key is not None else {}
    data = {**NAMES, **form} if route == "/verify" else form
    return client.post(route, files={"file": ("doc", content or png(True))}, data=data, headers=headers)


def test_backward_compatible_call(ocr):
    r = post(birthDate="1974-08-12")  # l'appel actuel de la plateforme
    assert r.status_code == 200
    d = r.json()
    assert d["decision"] == "VERIFIED" and d["birth_date"]["match"]
    assert d["document_number"] is None and d["expiry_date"] is None
    assert post(documentType="", documentNumber="", documentExpiresAt="").json()["decision"] == "VERIFIED"


def test_document_number_normalized_and_informative(ocr):
    assert main.normalize_document_number(" d23-145.890 ") == "D23145890"
    d = post(documentNumber="d23 145-890").json()
    assert d["document_number"]["match"] and d["document_number"]["extracted"] == "D23145890"
    d = post(documentNumber="1161-634-2064 RT").json()  # numero imprime different de la MRZ
    assert not d["document_number"]["match"] and d["decision"] == "VERIFIED"


def test_expiry_date(ocr):
    d = post(documentExpiresAt="2012-04-15").json()
    assert d["expiry_date"] == {"provided": "2012-04-15", "extracted": "2012-04-15", "score": 1.0, "match": True}
    assert d["decision"] == "VERIFIED"
    d = post(documentExpiresAt="2012-04-16").json()
    assert not d["expiry_date"]["match"] and d["decision"] == "REJECTED"


def test_two_page_pdf(ocr):
    d = post(content=pdf(False, True)).json()  # MRZ au verso (page 2)
    assert d["decision"] == "VERIFIED" and ocr.calls == 2
    ocr.calls = 0
    assert post(content=pdf(True, False)).json()["decision"] == "VERIFIED"
    assert ocr.calls == 1  # arret a la premiere page portant une MRZ
    assert post(content=pdf(False, False)).json()["decision"] == "MRZ_NOT_FOUND"


def test_document_limits(ocr):
    assert post(content=pdf(*[False] * 5)).status_code == 422  # plus de 4 pages
    assert post(content=b"GIF89a" + b"\0" * 100).status_code == 415
    assert post(content=b"%PDX-1.4 pas un pdf").status_code == 415  # le nom ou le type declare ne comptent pas
    assert post(content=b"\x89PNG\r\n\x1a\n" + b"\0" * 100).status_code == 422  # en-tete PNG, contenu illisible
    assert post(content=b"%PDF-" + b"\0" * (main.MAX_UPLOAD_BYTES)).status_code == 413


def test_api_key(ocr, monkeypatch):
    for route in ("/verify", "/extract"):
        assert post(route, key=None).status_code == 401
        assert post(route, key="mauvaise").status_code == 401
        assert post(route).status_code == 200
    monkeypatch.delenv("API_KEY")
    assert post().status_code == 401  # sans API_KEY configuree, rien n'est accepte
    assert client.get("/health").json() == {"status": "ok"}  # sans cle


def test_busy_and_timeout(ocr, monkeypatch):
    main.state["pool"] = queue.Queue()  # aucun moteur libre
    r = post()
    assert r.status_code == 503 and r.headers["Retry-After"] == str(main.RETRY_AFTER_S)

    slow, pool = FakeOcr(delay=0.5), queue.Queue()
    pool.put(slow)
    main.state["pool"] = pool
    monkeypatch.setattr(main, "REQUEST_TIMEOUT_S", 0.1)
    assert post().status_code == 504
    time.sleep(0.6)
    assert pool.qsize() == 1  # moteur rendu au pool une fois l'OCR termine


def test_openapi_documents_decisions():
    schema = main.app.openapi()["components"]["schemas"]["VerifyResponse"]["properties"]["decision"]
    assert schema["enum"] == ["VERIFIED", "REVIEW", "REJECTED", "MRZ_NOT_FOUND"]
