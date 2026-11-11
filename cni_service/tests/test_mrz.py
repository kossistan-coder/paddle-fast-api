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


def test_td1():
    m = parse_mrz(TD1)
    assert m.format == "TD1" and m.checks_valid
    assert m.surname == "ERIKSSON" and m.given_names == ["ANNA", "MARIA"]


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


def test_normalize_date():
    from app.viz import normalize_date

    assert normalize_date("12-08-1974") == normalize_date("12/08/1974") == "1974-08-12"
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
