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
    assert v["birth_date"] == "01-02-1990" and v["sex"] == "M"
    assert v["place_of_birth"] == "LOME /GOLFE" and v["profession"] == "MEDECIN"
    assert v["blood_group"] == "A+" and v["address"] == "BE KPOTA"
    assert v["father"] == "DUPONT, MARC" and v["mother"] == "DOE, ANNE"



def test_preferred_format():
    lines = [*TD1, *TD3]  # deux MRZ valides dans le meme texte
    assert parse_mrz(lines, {"TD3"}).format == "TD3"
    assert parse_mrz(lines, {"TD1", "TD2"}).format == "TD1"
    assert parse_mrz(TD1, {"TD3"}).format == "TD1"  # format prefere absent : repli sur ce qui est lu
