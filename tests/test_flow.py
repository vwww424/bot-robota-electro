"""Сценарій: створити об'єкт → додати пункти → фото → завершити фазу з перевіркою."""

from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User

from bot.db import DB
from bot.handlers import Fin, Nav, Ph, Txt, router
from bot.phases import checklist_for

USER = User(id=7, is_bot=False, first_name="Майстер")
CHAT = Chat(id=7, type="private")


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if method.__returning__ is bool:
            return True
        msg = Message(message_id=99, date=datetime.now(), chat=CHAT, text=getattr(method, "text", None))
        return [msg] if "list" in str(method.__returning__) else msg

    async def stream_content(self, *a, **kw):
        raise NotImplementedError

    async def close(self):
        pass


# A router can only be attached once, so all tests share one dispatcher.
DP = Dispatcher(storage=MemoryStorage())
DP.include_router(router)


@pytest.fixture
def env(tmp_path):
    db = DB(tmp_path / "flow.db")
    dp = DP
    dp["db"] = db
    session = FakeSession()
    bot = Bot("42:TEST", session=session, default=DefaultBotProperties(parse_mode="HTML"))
    return db, dp, bot, session


_uid = iter(range(1, 10_000))


def msg(**kw):
    return Update(update_id=next(_uid), message=Message(
        message_id=next(_uid), date=datetime.now(), chat=CHAT, from_user=USER, **kw))


def press(data):
    original = Message(message_id=5, date=datetime.now(), chat=CHAT, from_user=USER, text="screen")
    return Update(update_id=next(_uid), callback_query=CallbackQuery(
        id=str(next(_uid)), from_user=USER, chat_instance="c", message=original, data=data.pack()))


def last_text(session):
    return next(c.text for c in reversed(session.calls) if getattr(c, "text", None))


async def test_full_flow(env):
    db, dp, bot, session = env
    await dp.feed_update(bot, msg(text="/start"))
    assert "Мої об'єкти" in last_text(session)

    await dp.feed_update(bot, press(Nav(to="newobj")))
    await dp.feed_update(bot, msg(text="Кв. 45"))
    await dp.feed_update(bot, press(Txt(a="drop")))  # skip address
    obj = db.list_objects(7)[0]
    assert obj["name"] == "Кв. 45"
    assert "Обери фазу" in last_text(session)

    phase = db.list_phases(obj["id"])[1]  # cable
    await dp.feed_update(bot, press(Ph(a="open", id=phase["id"])))
    await dp.feed_update(bot, press(Ph(a="items", id=phase["id"])))
    await dp.feed_update(bot, msg(text="Розетки — 24 шт\nКабель 3×2.5 — 150 м"))
    assert len(db.list_items(phase["id"])) == 2

    await dp.feed_update(bot, msg(photo=[PhotoSize(file_id="F1", file_unique_id="u", width=1, height=1)],
                                  caption="траса"))
    assert db.list_photos(phase["id"])[0]["file_id"] == "F1"

    await dp.feed_update(bot, msg(text="купити дюбелі"))
    await dp.feed_update(bot, press(Txt(a="note")))
    assert db.list_notes(phase["id"])[0]["text"] == "купити дюбелі"

    # Finish with open items: warns first, then asks the checklist; one "no".
    questions = checklist_for("cable")
    await dp.feed_update(bot, press(Fin(p=phase["id"], s=0, m=0, a="start")))
    assert "Не відмічені" in last_text(session)
    await dp.feed_update(bot, press(Fin(p=phase["id"], s=1, m=0, a="start")))
    assert questions[0] in last_text(session)
    misses = 0
    for step in range(len(questions)):
        answer = "n" if step == 1 else "y"
        await dp.feed_update(bot, press(Fin(p=phase["id"], s=step, m=misses, a=answer)))
        misses += answer == "n"
    assert db.get_phase(7, phase["id"])["status"] == "doing"
    assert any("Перевірити" in i["text"] for i in db.list_items(phase["id"]))

    # All "yes" closes the phase.
    for step in range(len(questions)):
        await dp.feed_update(bot, press(Fin(p=phase["id"], s=step, m=0, a="y")))
    assert db.get_phase(7, phase["id"])["status"] == "done"


async def test_other_user_cannot_open_phase(env):
    db, dp, bot, session = env
    obj = db.add_object(1, "Чужий")
    phase = db.list_phases(obj)[0]
    await dp.feed_update(bot, press(Ph(a="open", id=phase["id"])))
    assert "Чужий" not in last_text(session)
    assert "Мої об'єкти" in last_text(session)
