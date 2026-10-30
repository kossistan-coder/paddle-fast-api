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
