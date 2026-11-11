import os
import threading

import cv2
import numpy as np

MAX_WIDTH = 1400  # largeur de travail de la detection (son temps croit avec le nombre de pixels)
# La lecture des lignes se fait dans l'image d'origine, jusqu'a cette largeur : reduite a MAX_WIDTH, une
# carte scannee petite sur une page A4 perd les espaces entre les mots ("YAOOUNABOUE" pour "YAO OUNABOUE")
READ_MAX_WIDTH = 4000
# Threads ONNX Runtime par moteur : 4 est le plus rapide mesure (plus de threads se genent)
OCR_THREADS = int(os.environ.get("OCR_THREADS", "4"))


class OcrEngine:
    """Enveloppe PaddleOCR : charge le modele une fois, serialise les appels."""

    def __init__(self) -> None:
        from paddleocr import PaddleOCR  # import tardif : le modele est lourd

        # Modeles "small" : ~4x plus rapides que "medium" sur CPU, MRZ lue a l'identique ("tiny" la perd)
        self._ocr = PaddleOCR(
            text_detection_model_name="PP-OCRv6_small_det",
            text_recognition_model_name="PP-OCRv6_small_rec",
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            # ONNX Runtime : ~3x plus rapide que Paddle Inference sur CPU (dont oneDNN plante), meme texte lu
            engine="onnxruntime",
            engine_config={"intra_op_num_threads": OCR_THREADS},
        )
        self._lock = threading.Lock()

    def read_lines(self, image: np.ndarray) -> list[str]:
        """Retourne les lignes de texte en ordre de lecture (fragments d'une meme ligne rejoints)."""
        h, w = image.shape[:2]
        if w > READ_MAX_WIDTH:
            h, w = round(h * READ_MAX_WIDTH / w), READ_MAX_WIDTH
            image = cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA)
        # Detection a MAX_WIDTH de large (taille passee par son plus grand cote), lecture en pleine resolution
        det = {"text_det_limit_type": "max", "text_det_limit_side_len": round(MAX_WIDTH * max(h, w) / w)} \
            if w > MAX_WIDTH else {}
        with self._lock:
            results = self._ocr.predict(image, **det)
        width = image.shape[1]
        items = []
        for res in results:
            for text, poly in zip(res["rec_texts"], res["rec_polys"]):
                poly = np.asarray(poly)
                items.append((text, poly[:, 0].min() / width, poly[:, 1].mean(), np.ptp(poly[:, 1]),
                              poly[:, 0].max() / width))
        return _group_lines(items)

    def read_lines_bottom(self, image: np.ndarray, fraction: float = 0.4) -> list[str]:
        """Seconde passe sur le bas du document (ou se trouve la MRZ), agrandi si besoin."""
        h, w = image.shape[:2]
        crop = image[int(h * (1 - fraction)):, :]
        if w < MAX_WIDTH:
            scale = MAX_WIDTH / w
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        return self.read_lines(crop)


class Line(str):
    """Ligne de texte qui garde ses fragments (texte, x gauche relatif a la largeur de la page).

    Les positions servent a la zone visuelle des passeports, ou la valeur est imprimee sous son libelle.
    """
    fragments: tuple[tuple[str, float], ...] = ()


def _overlap(a: tuple, b: tuple) -> bool:
    """Les deux fragments se superposent horizontalement (x droit facultatif : 5e element)."""
    a_right, b_right = (a[4] if len(a) > 4 else a[1]), (b[4] if len(b) > 4 else b[1])
    return a[1] < b_right and b[1] < a_right


def _group_lines(items: list[tuple]) -> list[Line]:
    """Regroupe les fragments (texte, x, y_centre, hauteur[, x_droit]) situes sur la meme ligne.

    Deux fragments qui se superposent horizontalement ne sont jamais sur la meme ligne : sur une photo
    inclinee, les lignes voisines se chevauchent en hauteur et se fondraient sinon en une seule.
    """
    rows: list[list] = []
    for item in sorted(items, key=lambda i: i[2]):
        near = [(abs(item[2] - row[-1][2]), k) for k, row in enumerate(rows)
                if abs(item[2] - row[-1][2]) < 0.5 * max(item[3], row[-1][3])
                and not any(_overlap(item, other) for other in row)]
        if near:
            rows[min(near)[1]].append(item)
        else:
            rows.append([item])
    lines = []
    for row in rows:
        row = sorted(row, key=lambda i: i[1])
        line = Line(("" if _is_mrz_row(row) else " ").join(t for t, *_ in row))
        line.fragments = tuple((t, x) for t, x, *_ in row)
        lines.append(line)
    return lines


def _is_mrz_row(row: list) -> bool:
    return any("<" in t or "«" in t for t, *_ in row)
