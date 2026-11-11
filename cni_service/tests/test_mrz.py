from datetime import date

from app.matching import similarity
from app.mrz import parse_mrz

TD3 = ["P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
       "L898902C36UTO7408122F1204159ZE184226B<<<<<10"]
TD1 = ["I<UTOD231458907<<<<<<<<<<<<<<<",
       "7408122F1204159UTO<<<<<<<<<<<6",
       "ERIKSSON<<ANNA<MARIA<<<<<<<<<<"]


def test_td3():
    m = parse_mrz(["bruit", *TD3])
    assert m.format == "TD3" and m.checks_valid
    assert m.surname == "ERIKSSON" and m.given_names == ["ANNA", "MARIA"]
    assert m.birth_date == date(1974, 8, 12)
    assert m.personal_number == "ZE184226B" and m.checks["personal_number"]  # donnees facultatives


def test_td3_personal_number_check():
    empty = TD3[1][:28] + "<" * 14 + "<" + TD3[1][43]  # sans numero personnel : chiffre "<" accepte
    assert parse_mrz([TD3[0], empty]).personal_number == ""
    assert parse_mrz([TD3[0], empty]).checks["personal_number"]
    altered = TD3[1].replace("ZE184226B", "ZE184227B")  # un caractere mal lu : controle en echec
    assert not parse_mrz([TD3[0], altered]).checks["personal_number"]


def test_td1():
    m = parse_mrz(TD1)
    assert m.format == "TD1" and m.checks_valid
    assert m.surname == "ERIKSSON" and m.given_names == ["ANNA", "MARIA"]
    assert m.personal_number == ""


def test_ocr_noise():
    noisy = [TD3[0].replace("<<<<<<<<<<<<<<<<<<<", "KKKKKKKKKKKKKKKKKKK").lower(), TD3[1].replace("7408122", "74O8I22")]
    m = parse_mrz(noisy)
    assert m.surname == "ERIKSSON" and m.birth_date == date(1974, 8, 12) and m.checks_valid


def test_similarity():
    assert similarity("Éric-Jean", "ERIC JEAN") == 1.0
    assert similarity("Maria Anna", "ANNA MARIA") == 1.0
    assert similarity("DUPONT", "MARTIN") < 0.5


def test_first_name_check():
    from app.main import _first_name_check

    given = ["KOSSI", "STAN"]
    for provided in ("Kossi", "stan", "kossi stan", "Stan Kossi"):
        c = _first_name_check(provided, given, 0.85)
        assert c.match and c.score == 1.0, provided
    assert _first_name_check("Kossi", given, 0.85).extracted == "KOSSI"
    assert _first_name_check("Stan", given, 0.85).extracted == "STAN"
    assert not _first_name_check("Pierre", given, 0.85).match
    assert _first_name_check("Kossi", [], 0.85).extracted is None


def test_visual_zone():
    from app.viz import parse_visual_zone

    v = parse_visual_zone(["CARTE NATIONALE D'IDENTITE", "Nom: DUPONT", "Prénom: JEAN PAUL",
                           "Né le: 01-02-1990 Sexe: M", "A: LOME /GOLFE", "Profession: MEDECIN",
                           "Taille: 1, 80 Groupe sanguin: A+ Domicile: BE KPOTA",
                           "Père: DUPONT, MARC Mère:DOE, ANNE", "I<TGO0000000000<<<<<<<<<<<<<<<"])
    assert v["last_name"] == "DUPONT" and v["first_names"] == "JEAN PAUL"  # "Nom" non confondu avec "Prénom"
    assert v["birth_date"] == "1990-02-01" and v["sex"] == "M"  # date au format de la MRZ
    assert v["place_of_birth"] == "LOME /GOLFE" and v["profession"] == "MEDECIN"
    assert v["blood_group"] == "A+" and v["address"] == "BE KPOTA"
    assert v["father"] == "DUPONT, MARC" and v["mother"] == "DOE, ANNE"


def test_visual_zone_card_number():
    """Le numero de carte, imprime seul au bout de la ligne, ne se colle pas aux signes particuliers."""
    from app.ocr_engine import _group_lines
    from app.viz import parse_visual_zone

    v = parse_visual_zone(_group_lines([
        ("Numéro: 0000-000-0000", 0.06, 100, 20),
        ("Signes particuliers:NEANT", 0.06, 200, 20), ("23145890", 0.73, 200, 20),
        ("Domicile: BE KPOTA 90000000", 0.06, 300, 20),  # chiffres dans une valeur : non concernes
        ("I<UTOD231458907<<<<<<<<<<<<<<<", 0.06, 400, 20),
    ]))
    assert v["distinguishing_marks"] == "NEANT" and v["document_number"] == "23145890"
    assert v["national_number"] == "0000-000-0000" and v["address"] == "BE KPOTA 90000000"


def test_visual_zone_passport():
    from app.ocr_engine import _group_lines
    from app.viz import parse_visual_zone

    def row(y, *frags):  # (texte, x relatif) -> elements (texte, x, y_centre, hauteur) de l'OCR
        return [(t, x, y, 20) for t, x in frags]

    lines = _group_lines([
        *row(100, ("PASSEPORT / PASSPORT", 0.30)),
        *row(150, ("Nom / Surname", 0.30)),
        *row(180, ("ERIKSSON", 0.30)),
        *row(230, ("Date de naissance / Date of birth", 0.30), ("Sexe / Sex", 0.62)),
        *row(260, ("12 AOUT/AUG 1974", 0.30), ("F", 0.62)),
        *row(310, ("Lieu de naissance / Place of birth", 0.30), ("Nationalité / Nationality", 0.62)),
        *row(340, ("ZENITH", 0.31), ("UTOPIENNE", 0.63)),
        *row(390, ("Date de délivrance / Date of issue", 0.30), ("Lieu de délivrance / Place of issue", 0.62)),
        *row(420, ("16 AVR/APR 2007", 0.30), ("ZENITH", 0.62)),
        *row(470, ("Autorité / Authority", 0.30)),
        *row(500, ("PASSPORT OFFICE", 0.30)),
        *row(600, ("P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<", 0.05)),
    ])
    v = parse_visual_zone(lines)
    assert v["place_of_birth"] == "ZENITH" and v["nationality"] == "UTOPIENNE"
    assert v["place_of_issue"] == "ZENITH" and v["issuing_authority"] == "PASSPORT OFFICE"
    assert v["issue_date"] == "2007-04-16" and v["birth_date"] == "1974-08-12" and v["sex"] == "F"
    assert v["last_name"] == "ERIKSSON"
    # valeur sur la meme ligne que le libelle
    assert parse_visual_zone(["Place of birth: ZENITH"])["place_of_birth"] == "ZENITH"


def test_visual_zone_passport_photo_ocr_errors():
    """Libelles mal lus sur une photo (espaces avales, "/" lu "I") et lignes voisines melangees."""
    from app.ocr_engine import _group_lines
    from app.viz import parse_visual_zone

    def row(y, *frags):
        return [(t, x, y, 12) for t, x in frags]

    lines = _group_lines([
        *row(100, ("PRENONS IGIVEN NAMES", 0.39)),
        *row(130, ("ANNA MARIA", 0.39)),
        *row(160, ("NATIONAUITE I NATIONALITY", 0.39)),
        *row(190, ("UTOPIENNE", 0.39), ("DATE DE NAISSANCE IDATE OF BIRTH", 0.385)),  # lignes fusionnees
        *row(220, ("12/08/1974", 0.37)),
        *row(250, ("SEXE/5EX", 0.385), ("LEUDE NAISSANCE I PLACE OF BIRTH", 0.50)),
        *row(280, ("F", 0.387), ("ZENITH,UTOPIA", 0.502)),
        *row(310, ("DATE DE DÉINRANCE /DATE OF ISSUE", 0.385), ("LEUD/ÉMISSIONIPLACE OFISSUE", 0.66)),
        *row(340, ("16/04/2007", 0.387), ("PASSPORT OFFICE, ZENITH", 0.661)),
        *row(370, ("DATE D'EXPIRATION IDATE OF EXPIRY", 0.383)),
        *row(400, ("15/04/2012", 0.385)),
    ])
    assert parse_visual_zone(lines) == {
        "first_names": "ANNA MARIA", "nationality": "UTOPIENNE", "birth_date": "1974-08-12", "sex": "F",
        "place_of_birth": "ZENITH,UTOPIA", "issue_date": "2007-04-16",
        "place_of_issue": "PASSPORT OFFICE, ZENITH", "expiry_date": "2012-04-15",
    }


def test_visual_zone_passport_damaged_labels():
    """Libelles tres abimes (repli approximatif), renvois "(4)", dates sans separateur ou espacees."""
    from app.ocr_engine import _group_lines
    from app.viz import parse_visual_zone

    def row(y, *frags):
        return [(t, x, y, 12) for t, x in frags]

    lines = _group_lines([
        *row(80, ("PASSEPORT", 0.06)),
        *row(100, ("PASSPORT", 0.06), ("Type/ype", 0.33), ("Passeport n° /fossport no", 0.70)),
        *row(130, ("P", 0.33), ("L898902C3", 0.70)),
        *row(160, ("Natlonallte/Notionality (3)", 0.33),
             ("Sexe/Sex (5) Tallle/Hcight2) Couleur des yeuxCofourol ecs(13)", 0.53)),  # 3 libelles, 1 fragment
        *row(190, ("Utopienne", 0.33), ("F", 0.534), ("1,70 m", 0.60), ("VERTS", 0.70)),
        *row(220, ("Date de nalssance/Dote of birth (4)", 0.33), ("Ueu de naissance/Ploce of birth (6)", 0.53)),
        *row(250, ("12081974", 0.33), ("ZENITH", 0.534)),
        *row(280, ("Date de délivrance/Date of issue (7)", 0.33)),
        *row(310, ("16 042007", 0.33)),
        *row(340, ("Autorlté/Auchority (9)", 0.33), ("UTOPIA", 0.645)),  # valeur de l'autorite non lue
        *row(370, ("Date d'explration/Dote of expiry (8)", 0.33)),
        *row(400, ("15 04 2012", 0.33)),
    ])
    assert parse_visual_zone(lines) == {
        "document_number": "L898902C3", "nationality": "Utopienne", "sex": "F", "height": "1,70 m",
        "eye_color": "VERTS", "birth_date": "1974-08-12",
        "place_of_birth": "ZENITH", "issue_date": "2007-04-16", "expiry_date": "2012-04-15",
    }  # pas d'autorite inventee a partir de la date d'expiration lue deux lignes plus bas


def test_visual_zone_tilted_photo_other_languages():
    """Photo inclinee (lignes voisines qui se chevauchent en hauteur), libelles portugais, mois "SET"."""
    from app.ocr_engine import _group_lines
    from app.viz import parse_visual_zone

    def frag(text, x, y, x2):  # (texte, x, y_centre, hauteur, x_droit)
        return (text, x, y, 20, x2)

    lines = _group_lines([  # chaque ligne descend vers la droite : 7 px par colonne
        frag("NOMEIGIVENNAMES", 0.35, 100, 0.45),
        frag("ANNA MARIA", 0.35, 112, 0.45),
        frag("NACIONALIDADE/NATIONALITY", 0.35, 124, 0.50), frag("CPF/PERSONAL NUMBER", 0.50, 131, 0.60),
        frag("UTOPIENSE", 0.35, 136, 0.45), frag("123456789 00", 0.50, 143, 0.60),
        frag("SEXO/SEX", 0.35, 148, 0.40), frag("NATURALIDADEPLACE OF BIRTH", 0.41, 155, 0.55),
        frag("F", 0.35, 160, 0.36), frag("ZENITH/UT", 0.41, 167, 0.50),
        frag("DATA DE EXPEDIÇÃO/ DATE OF ISSUE", 0.35, 172, 0.50),
        frag("16 SET/SEP 2007", 0.35, 184, 0.45),
        frag("VÁLIDO ATÉ/ DATE OF EXPIRY", 0.35, 196, 0.50),
        frag("15 OUT/OCT 2012", 0.35, 208, 0.45),
    ])
    assert parse_visual_zone(lines) == {
        "first_names": "ANNA MARIA", "nationality": "UTOPIENSE", "personal_number": "12345678900", "sex": "F",
        "place_of_birth": "ZENITH/UT", "issue_date": "2007-09-16", "expiry_date": "2012-10-15",
    }


def test_values_checked_against_field_shape():
    """Libelle "Sexe" illisible (hologramme) : sa valeur "M" ne decale pas la taille et la couleur des yeux ;
    un morceau de libelle "...names (2)" n'est pas pris pour les prenoms."""
    from app.ocr_engine import _group_lines
    from app.viz import parse_visual_zone

    def row(y, *frags):
        return [(t, x, y, 12) for t, x in frags]

    lines = _group_lines([
        *row(100, ("Prénoms/Cl", 0.336)),
        *row(130, ("wen domes (2)", 0.40)),
        *row(160, ("Anna,Maria", 0.336)),
        *row(190, ("Nationalite/vota", 0.333), ("Taille/rieight (12) Couleur des yeux/Colour of eyes (13)", 0.596)),
        *row(220, ("Utopienne", 0.331), ("F", 0.531), ("1,70 m", 0.598), ("VERTS", 0.697)),
    ])
    v = parse_visual_zone(lines)
    assert v["first_names"] == "Anna,Maria" and v["nationality"] == "Utopienne"
    assert v["height"] == "1,70 m" and v["eye_color"] == "VERTS" and "sex" not in v


def test_detection_reduced_reading_full_resolution():
    """La detection voit MAX_WIDTH px de large, la lecture recoit l'image d'origine (espaces entre les mots)."""
    import threading

    import numpy as np

    from app import ocr_engine

    calls = []

    class FakePaddle:
        def predict(self, image, **kwargs):
            calls.append((image.shape, kwargs))
            return []

    engine = ocr_engine.OcrEngine.__new__(ocr_engine.OcrEngine)
    engine._ocr, engine._lock = FakePaddle(), threading.Lock()
    engine.read_lines(np.zeros((3500, 2500, 3), np.uint8))  # page A4 a 300 DPI, en portrait
    shape, kwargs = calls[-1]
    assert shape == (3500, 2500, 3)  # non reduite
    assert kwargs == {"text_det_limit_type": "max", "text_det_limit_side_len": 1960}  # 1400 px de large
    engine.read_lines(np.zeros((900, 1200, 3), np.uint8))
    assert calls[-1][1] == {}  # deja assez petite : reglage par defaut


def test_label_word_inside_sentence_is_not_a_label():
    from app.viz import parse_visual_zone

    assert parse_visual_zone(["Pagina riservata all'autorità", "Opmerkingen van bevoegde instanties"]) == {}


def test_normalize_date():
    from app.viz import normalize_date

    assert normalize_date("12-08-1974") == normalize_date("12/08/1974") == "1974-08-12"
    assert normalize_date("12 08 1974") == normalize_date("12081974") == normalize_date("12 081974") == "1974-08-12"
    assert normalize_date("15-04-2012/060") == "2012-04-15"  # suffixe imprime apres la date
    assert normalize_date("12 AOUT/AUG 1974") == normalize_date("12 aug 1974") == "1974-08-12"
    assert normalize_date("1974-08-12") == "1974-08-12"
    assert normalize_date("31-02-1974") == "31-02-1974"  # date impossible : valeur brute conservee
    assert normalize_date("IDATE OF BIRTH") == "IDATE OF BIRTH"


def test_preferred_format():
    lines = [*TD1, *TD3]  # deux MRZ valides dans le meme texte
    assert parse_mrz(lines, {"TD3"}).format == "TD3"
    assert parse_mrz(lines, {"TD1", "TD2"}).format == "TD1"
    assert parse_mrz(TD1, {"TD3"}).format == "TD1"  # format prefere absent : repli sur ce qui est lu
