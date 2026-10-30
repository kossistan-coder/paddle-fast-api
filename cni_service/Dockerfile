FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # modeles integres a l'image : pas de verification reseau au demarrage
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

# libgl1/libglib2.0-0 : OpenCV ; libgomp1 : Paddle
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY requirements.lock .
# Deux paquets OpenCV fournissent le module cv2 : on garde celui de l'environnement teste (4.10 contrib).
RUN pip install -r requirements.lock \
    && pip install --no-deps --force-reinstall opencv-contrib-python==4.10.0.84

USER app

# Telechargement des modeles OCR dans l'image (couche mise en cache tant que ocr_engine.py ne change pas)
COPY --chown=app app/__init__.py app/ocr_engine.py ./app/
RUN python -c "from app.ocr_engine import OcrEngine; OcrEngine()"

COPY --chown=app app ./app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
