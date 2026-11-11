import pytest

from bot.db import DB
from bot.phases import DEFAULT_PHASES, checklist_for, question_to_task


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "t.db")


def test_object_gets_default_phases(db):
    obj = db.add_object(1, "Кв. 45", "вул. Шевченка")
    phases = db.list_phases(obj)
    assert [p["name"] for p in phases] == [name for _, name in DEFAULT_PHASES]
    assert db.list_objects(1)[0]["current_phase_name"] == DEFAULT_PHASES[0][1]


def test_users_are_isolated(db):
    obj = db.add_object(1, "Мій")
    phase = db.list_phases(obj)[0]["id"]
    db.add_items(phase, ["Розетки 24"])
    item = db.list_items(phase)[0]["id"]
    assert db.list_objects(2) == []
    assert db.get_object(2, obj) is None
    assert db.get_phase(2, phase) is None
    assert db.get_item(2, item) is None
    db.delete_object(2, obj)
    assert db.get_object(1, obj) is not None


def test_items_notes_photos_and_status(db):
    obj = db.add_object(1, "X")
    phase = db.list_phases(obj)[0]["id"]
    assert db.add_items(phase, ["Розетки — 24 шт", "", "  - Кабель 150 м"]) == 2
    assert [i["text"] for i in db.list_items(phase)] == ["Розетки — 24 шт", "Кабель 150 м"]
    assert db.get_phase(1, phase)["status"] == "doing"
    item = db.list_items(phase)[0]["id"]
    db.toggle_item(item)
    assert db.list_items(phase)[0]["done"] == 1
    db.add_note(phase, text="не забути")
    db.add_photo(phase, "file123", "щит")
    assert db.count("notes", phase) == 1 and db.count("photos", phase) == 1


def test_custom_phase_and_current_phase(db):
    obj = db.add_object(1, "X")
    custom = db.add_phase(obj, "🔧 Слабкострум")
    phases = db.list_phases(obj)
    assert phases[-1]["id"] == custom and phases[-1]["kind"] is None
    db.set_current_phase(1, custom)
    assert db.get_current_phase(1)["id"] == custom
    db.delete_object(1, obj)  # cascades
    assert db.get_current_phase(1) is None


def test_finished_phases_move_current_stage(db):
    obj = db.add_object(1, "X")
    for p in db.list_phases(obj):
        db.set_phase_status(p["id"], "done")
    assert db.list_objects(1)[0]["current_phase_name"] is None


def test_checklists():
    assert checklist_for("cable") != checklist_for(None)
    assert question_to_task("Лінії підписані?") == "⚠️ Перевірити: лінії підписані"
