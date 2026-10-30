import threading

import cv2
import numpy as np

MAX_WIDTH = 1400  # largeur de travail : les pages plus larges sont reduites (temps d'OCR ~ nb de pixels)


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
            enable_mkldnn=False,  # evite le bug oneDNN/PIR de Paddle 3 sur CPU
        )
        self._lock = threading.Lock()

    def read_lines(self, image: np.ndarray) -> list[str]:
        """Retourne les lignes de texte en ordre de lecture (fragments d'une meme ligne rejoints)."""
        h, w = image.shape[:2]
        if w > MAX_WIDTH:  # le temps d'OCR croit avec le nombre de pixels
            image = cv2.resize(image, (MAX_WIDTH, round(h * MAX_WIDTH / w)), interpolation=cv2.INTER_AREA)
        with self._lock:
            results = self._ocr.predict(image)
        items = []
        for res in results:
            for text, poly in zip(res["rec_texts"], res["rec_polys"]):
                poly = np.asarray(poly)
                items.append((text, poly[:, 0].min(), poly[:, 1].mean(), np.ptp(poly[:, 1])))
        return _group_lines(items)

    def read_lines_bottom(self, image: np.ndarray, fraction: float = 0.4) -> list[str]:
        """Seconde passe sur le bas du document (ou se trouve la MRZ), agrandi si besoin."""
        h, w = image.shape[:2]
        crop = image[int(h * (1 - fraction)):, :]
        if w < MAX_WIDTH:
            scale = MAX_WIDTH / w
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        return self.read_lines(crop)


def _group_lines(items: list[tuple[str, float, float, float]]) -> list[str]:
    """Regroupe les fragments (texte, x, y_centre, hauteur) situes sur la meme ligne."""
    rows: list[list] = []
    for item in sorted(items, key=lambda i: i[2]):
        if rows and abs(item[2] - rows[-1][-1][2]) < 0.5 * max(item[3], rows[-1][-1][3]):
            rows[-1].append(item)
        else:
            rows.append([item])
    return ["".join(t for t, *_ in sorted(row, key=lambda i: i[1])) if _is_mrz_row(row)
            else " ".join(t for t, *_ in sorted(row, key=lambda i: i[1]))
            for row in rows]


def _is_mrz_row(row: list) -> bool:
    return any("<" in t or "«" in t for t, *_ in row)
