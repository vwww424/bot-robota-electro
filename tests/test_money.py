import sqlite3

from bot.db import DB
from bot.money import days_between, fmt, parse_amount, parse_date


def test_parse_amount():
    assert parse_amount("1500 кабель") == (150000, "кабель")
    assert parse_amount("1 500,5 грн — автомати") == (150050, "автомати")
    assert parse_amount("20000") == (2000000, "")
    assert parse_amount("кабель 1500") is None
    assert parse_amount("0") is None


def test_fmt_and_dates():
    assert fmt(1234567) == "12 345,67 грн"
    assert fmt(-50000) == "-500 грн"
    assert parse_date("05.11.2026").isoformat() == "2026-11-05"
    assert parse_date("31.02.2026") is None
    assert days_between("2026-11-01", "2026-11-10") == 10


def test_money_isolated_and_totals(tmp_path):
    db = DB(tmp_path / "m.db")
    obj = db.add_object(1, "X")
    db.add_money(obj, "in", 1000, day="2026-10-05")
    db.add_money(obj, "in", 500, day="2026-11-01")
    db.add_money(obj, "out", 200, day="2026-11-02")
    assert db.money_totals(obj) == (1500, 200)
    months = db.user_money_by_month(1)
    assert [(m["month"], m["i"], m["o"]) for m in months] == [("2026-11", 500, 200), ("2026-10", 1000, 0)]
    assert db.user_money_by_month(2) == []
    rid = db.list_money(obj)[0]["id"]
    assert db.get_money(2, rid) is None


def test_migrates_old_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE objects (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
                 "name TEXT NOT NULL, address TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO objects(user_id, name, created_at) VALUES (1, 'old', '2026-11-01 10:00:00')")
    conn.commit()
    conn.close()
    db = DB(path)
    assert db.get_object(1, 1)["started_on"] == "2026-11-01"
