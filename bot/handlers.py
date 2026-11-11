"""Екрани та обробники бота."""

import html

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db import DB
from .phases import checklist_for, question_to_task

router = Router()

STATUS_ICON = {"todo": "⬜", "doing": "🔄", "done": "✅"}
STATUS_TEXT = {"todo": "не почато", "doing": "в роботі", "done": "готово"}
MAX_ITEM_BUTTONS = 40
MAX_TEXT = 3900  # Telegram limit is 4096


# ---------- callback data ----------
class Nav(CallbackData, prefix="n"):
    to: str  # menu | newobj


class Obj(CallbackData, prefix="o"):
    a: str  # open | newphase | del | delok
    id: int


class Ph(CallbackData, prefix="p"):
    a: str  # open | items | note | photos | notes | edit | finish | reopen | del | delok
    id: int


class It(CallbackData, prefix="i"):
    a: str  # toggle | del
    id: int


class Fin(CallbackData, prefix="f"):
    p: int  # phase id
    s: int  # question index
    m: int  # misses so far
    a: str  # start | y | n


class Txt(CallbackData, prefix="t"):
    a: str  # items | note | drop


# ---------- states ----------
class NewObject(StatesGroup):
    name = State()
    address = State()


class Input(StatesGroup):
    items = State()
    note = State()
    phase_name = State()


# ---------- helpers ----------
def esc(text: str | None) -> str:
    return html.escape(text or "")


def clip(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


async def show(target: Message | CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    """Edit the screen in place for button presses, send a new message otherwise."""
    text = clip(text)
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                await target.message.answer(text, reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb)


def back_kb(cb: CallbackData, label: str = "⬅️ Назад") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=label, callback_data=cb)
    return b.as_markup()


# ---------- main menu ----------
async def menu_screen(target: Message | CallbackQuery, db: DB, user_id: int) -> None:
    objects = db.list_objects(user_id)
    b = InlineKeyboardBuilder()
    for o in objects:
        stage = o["current_phase_name"] or "✅ все готово"
        b.button(text=f"{o['name']} · {stage}", callback_data=Obj(a="open", id=o["id"]))
    b.button(text="➕ Новий об'єкт", callback_data=Nav(to="newobj"))
    b.adjust(1)
    text = "🏠 <b>Мої об'єкти</b>\n\n"
    text += "Обери об'єкт:" if objects else "Поки порожньо. Додай перший об'єкт 👇"
    await show(target, text, b.as_markup())


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(message: Message, state: FSMContext, db: DB) -> None:
    await state.clear()
    await menu_screen(message, db, message.from_user.id)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, db: DB) -> None:
    await state.clear()
    await message.answer("Скасовано.")
    await menu_screen(message, db, message.from_user.id)


@router.callback_query(Nav.filter(F.to == "menu"))
async def cb_menu(cb: CallbackQuery, state: FSMContext, db: DB) -> None:
    await state.clear()
    await menu_screen(cb, db, cb.from_user.id)


# ---------- new object ----------
@router.callback_query(Nav.filter(F.to == "newobj"))
async def cb_newobj(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewObject.name)
    await show(cb, "✍️ Напиши назву об'єкта (наприклад: <i>ЖК Сонячний, кв. 45</i>)",
               back_kb(Nav(to="menu"), "❌ Скасувати"))


@router.message(NewObject.name, F.text)
async def newobj_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip()[:100])
    await state.set_state(NewObject.address)
    b = InlineKeyboardBuilder()
    b.button(text="⏭ Пропустити", callback_data=Txt(a="drop"))
    await message.answer("📍 Адреса або телефон замовника? (або пропусти)", reply_markup=b.as_markup())


async def _create_object(target: Message | CallbackQuery, state: FSMContext, db: DB, address: str | None) -> None:
    data = await state.get_data()
    await state.clear()
    obj_id = db.add_object(target.from_user.id, data["name"], address)
    await object_screen(target, db, target.from_user.id, obj_id)


@router.message(NewObject.address, F.text)
async def newobj_address(message: Message, state: FSMContext, db: DB) -> None:
    await _create_object(message, state, db, message.text.strip()[:300])


@router.callback_query(NewObject.address, Txt.filter(F.a == "drop"))
async def newobj_skip_address(cb: CallbackQuery, state: FSMContext, db: DB) -> None:
    await _create_object(cb, state, db, None)


# ---------- object ----------
async def object_screen(target: Message | CallbackQuery, db: DB, user_id: int, obj_id: int) -> None:
    obj = db.get_object(user_id, obj_id)
    if not obj:
        await menu_screen(target, db, user_id)
        return
    phases = db.list_phases(obj_id)
    b = InlineKeyboardBuilder()
    for p in phases:
        counter = f" ({p['items_done']}/{p['items_total']})" if p["items_total"] else ""
        b.button(text=f"{STATUS_ICON[p['status']]} {p['name']}{counter}", callback_data=Ph(a="open", id=p["id"]))
    b.button(text="➕ Своя фаза", callback_data=Obj(a="newphase", id=obj_id))
    b.button(text="🗑 Видалити об'єкт", callback_data=Obj(a="del", id=obj_id))
    b.button(text="⬅️ Об'єкти", callback_data=Nav(to="menu"))
    b.adjust(1)
    done = sum(p["status"] == "done" for p in phases)
    text = f"🏠 <b>{esc(obj['name'])}</b>\n"
    if obj["address"]:
        text += f"📍 {esc(obj['address'])}\n"
    text += f"\nГотово фаз: {done}/{len(phases)}\nОбери фазу:"
    await show(target, text, b.as_markup())


@router.callback_query(Obj.filter(F.a == "open"))
async def cb_obj_open(cb: CallbackQuery, callback_data: Obj, state: FSMContext, db: DB) -> None:
    await state.clear()
    await object_screen(cb, db, cb.from_user.id, callback_data.id)


@router.callback_query(Obj.filter(F.a == "del"))
async def cb_obj_del(cb: CallbackQuery, callback_data: Obj, db: DB) -> None:
    obj = db.get_object(cb.from_user.id, callback_data.id)
    if not obj:
        return await menu_screen(cb, db, cb.from_user.id)
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Так, видалити", callback_data=Obj(a="delok", id=obj["id"]))
    b.button(text="⬅️ Ні", callback_data=Obj(a="open", id=obj["id"]))
    b.adjust(1)
    await show(cb, f"Видалити <b>{esc(obj['name'])}</b> разом з усіма фото і примітками?", b.as_markup())


@router.callback_query(Obj.filter(F.a == "delok"))
async def cb_obj_delok(cb: CallbackQuery, callback_data: Obj, db: DB) -> None:
    db.delete_object(cb.from_user.id, callback_data.id)
    await menu_screen(cb, db, cb.from_user.id)


@router.callback_query(Obj.filter(F.a == "newphase"))
async def cb_newphase(cb: CallbackQuery, callback_data: Obj, state: FSMContext, db: DB) -> None:
    if not db.get_object(cb.from_user.id, callback_data.id):
        return await menu_screen(cb, db, cb.from_user.id)
    await state.set_state(Input.phase_name)
    await state.update_data(obj_id=callback_data.id)
    await show(cb, "✍️ Назва нової фази (наприклад: <i>Слабкострум</i>)",
               back_kb(Obj(a="open", id=callback_data.id), "❌ Скасувати"))


@router.message(Input.phase_name, F.text)
async def newphase_name(message: Message, state: FSMContext, db: DB) -> None:
    data = await state.get_data()
    await state.clear()
    obj_id = data["obj_id"]
    if db.get_object(message.from_user.id, obj_id):
        db.add_phase(obj_id, "🔧 " + message.text.strip()[:60])
    await object_screen(message, db, message.from_user.id, obj_id)


# ---------- phase ----------
async def phase_screen(target: Message | CallbackQuery, db: DB, user_id: int, phase_id: int) -> None:
    phase = db.get_phase(user_id, phase_id)
    if not phase:
        await menu_screen(target, db, user_id)
        return
    db.set_current_phase(user_id, phase_id)
    items = db.list_items(phase_id)
    notes = db.list_notes(phase_id)
    photos = db.count("photos", phase_id)

    text = (f"🏠 {esc(phase['object_name'])} › <b>{esc(phase['name'])}</b>\n"
            f"Стан: {STATUS_ICON[phase['status']]} {STATUS_TEXT[phase['status']]}\n\n")
    if items:
        done = sum(i["done"] for i in items)
        text += f"📋 <b>Що треба</b> ({done}/{len(items)}) — тисни на пункт, щоб відмітити:\n"
    else:
        text += "📋 Пунктів ще нема. Додай, що треба зробити, і кількість.\n"
    text_notes = [n for n in notes if n["text"]]
    if text_notes:
        text += "\n📝 <b>Примітки:</b>\n"
        for n in text_notes[-5:]:
            text += f"• {esc(n['text'])}\n"
        if len(text_notes) > 5:
            text += f"…ще {len(text_notes) - 5}\n"
    voices = len(notes) - len(text_notes)
    text += f"\n📷 Фото: {photos}" + (f" · 🎤 Голосових: {voices}" if voices else "")
    text += "\n\n<i>Фото і голосові можна просто кидати в чат — збережу в цю фазу.</i>"

    b = InlineKeyboardBuilder()
    for i in items[:MAX_ITEM_BUTTONS]:
        b.button(text=f"{'✅' if i['done'] else '⬜'} {i['text'][:60]}", callback_data=It(a="toggle", id=i["id"]))
    rows = [1] * min(len(items), MAX_ITEM_BUTTONS)
    b.button(text="➕ Пункти", callback_data=Ph(a="items", id=phase_id))
    b.button(text="📝 Примітка", callback_data=Ph(a="note", id=phase_id))
    b.button(text=f"📷 Фото ({photos})", callback_data=Ph(a="photos", id=phase_id))
    b.button(text="🗂 Всі примітки", callback_data=Ph(a="notes", id=phase_id))
    rows += [2, 2]
    if items:
        b.button(text="✏️ Видалити пункти", callback_data=Ph(a="edit", id=phase_id))
        rows.append(1)
    if phase["status"] == "done":
        b.button(text="↩️ Відкрити фазу знову", callback_data=Ph(a="reopen", id=phase_id))
    else:
        b.button(text="🏁 Завершити фазу", callback_data=Fin(p=phase_id, s=0, m=0, a="start"))
    b.button(text="🗑 Видалити фазу", callback_data=Ph(a="del", id=phase_id))
    b.button(text="⬅️ До об'єкта", callback_data=Obj(a="open", id=phase["object_id"]))
    rows += [1, 1, 1]
    b.adjust(*rows)
    await show(target, text, b.as_markup())


@router.callback_query(Ph.filter(F.a == "open"))
async def cb_phase_open(cb: CallbackQuery, callback_data: Ph, state: FSMContext, db: DB) -> None:
    await state.clear()
    await phase_screen(cb, db, cb.from_user.id, callback_data.id)


@router.callback_query(It.filter(F.a == "toggle"))
async def cb_item_toggle(cb: CallbackQuery, callback_data: It, db: DB) -> None:
    item = db.get_item(cb.from_user.id, callback_data.id)
    if not item:
        return await cb.answer("Пункт не знайдено")
    db.toggle_item(item["id"])
    db.mark_started(item["phase_id"])
    await phase_screen(cb, db, cb.from_user.id, item["phase_id"])


@router.callback_query(Ph.filter(F.a == "edit"))
async def cb_phase_edit(cb: CallbackQuery, callback_data: Ph, db: DB) -> None:
    if not db.get_phase(cb.from_user.id, callback_data.id):
        return await menu_screen(cb, db, cb.from_user.id)
    b = InlineKeyboardBuilder()
    for i in db.list_items(callback_data.id)[:MAX_ITEM_BUTTONS]:
        b.button(text=f"🗑 {i['text'][:60]}", callback_data=It(a="del", id=i["id"]))
    b.button(text="✔️ Готово", callback_data=Ph(a="open", id=callback_data.id))
    b.adjust(1)
    await show(cb, "Тисни на пункт, щоб видалити його:", b.as_markup())


@router.callback_query(It.filter(F.a == "del"))
async def cb_item_del(cb: CallbackQuery, callback_data: It, db: DB) -> None:
    item = db.get_item(cb.from_user.id, callback_data.id)
    if not item:
        return await cb.answer("Пункт не знайдено")
    db.delete_item(item["id"])
    await cb_phase_edit(cb, Ph(a="edit", id=item["phase_id"]), db)


@router.callback_query(Ph.filter(F.a.in_({"items", "note"})))
async def cb_phase_input(cb: CallbackQuery, callback_data: Ph, state: FSMContext, db: DB) -> None:
    if not db.get_phase(cb.from_user.id, callback_data.id):
        return await menu_screen(cb, db, cb.from_user.id)
    await state.update_data(phase_id=callback_data.id)
    if callback_data.a == "items":
        await state.set_state(Input.items)
        text = ("✍️ Напиши пункти — <b>кожен з нового рядка</b>, можна з кількістю:\n\n"
                "<i>Розетки — 24 шт\nВимикачі — 8 шт\nКабель 3×2.5 — 150 м\nКоробки — 6 шт</i>")
    else:
        await state.set_state(Input.note)
        text = "✍️ Напиши примітку (або надішли голосове):"
    await show(cb, text, back_kb(Ph(a="open", id=callback_data.id), "❌ Скасувати"))


@router.message(Input.items, F.text)
async def input_items(message: Message, state: FSMContext, db: DB) -> None:
    phase_id = (await state.get_data())["phase_id"]
    await state.clear()
    if db.get_phase(message.from_user.id, phase_id):
        n = db.add_items(phase_id, message.text.splitlines())
        await message.answer(f"Додано пунктів: {n}")
    await phase_screen(message, db, message.from_user.id, phase_id)


@router.message(Input.note, F.text)
async def input_note(message: Message, state: FSMContext, db: DB) -> None:
    phase_id = (await state.get_data())["phase_id"]
    await state.clear()
    if db.get_phase(message.from_user.id, phase_id):
        db.add_note(phase_id, text=message.text.strip())
        await message.answer("📝 Примітку збережено")
    await phase_screen(message, db, message.from_user.id, phase_id)


@router.callback_query(Ph.filter(F.a == "reopen"))
async def cb_phase_reopen(cb: CallbackQuery, callback_data: Ph, db: DB) -> None:
    if db.get_phase(cb.from_user.id, callback_data.id):
        db.set_phase_status(callback_data.id, "doing")
    await phase_screen(cb, db, cb.from_user.id, callback_data.id)


@router.callback_query(Ph.filter(F.a == "del"))
async def cb_phase_del(cb: CallbackQuery, callback_data: Ph, db: DB) -> None:
    phase = db.get_phase(cb.from_user.id, callback_data.id)
    if not phase:
        return await menu_screen(cb, db, cb.from_user.id)
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Так, видалити", callback_data=Ph(a="delok", id=phase["id"]))
    b.button(text="⬅️ Ні", callback_data=Ph(a="open", id=phase["id"]))
    b.adjust(1)
    await show(cb, f"Видалити фазу <b>{esc(phase['name'])}</b> з усіма пунктами, фото і примітками?",
               b.as_markup())


@router.callback_query(Ph.filter(F.a == "delok"))
async def cb_phase_delok(cb: CallbackQuery, callback_data: Ph, db: DB) -> None:
    phase = db.get_phase(cb.from_user.id, callback_data.id)
    if not phase:
        return await menu_screen(cb, db, cb.from_user.id)
    db.delete_phase(phase["id"])
    await object_screen(cb, db, cb.from_user.id, phase["object_id"])


# ---------- viewing photos & notes ----------
@router.callback_query(Ph.filter(F.a == "photos"))
async def cb_photos(cb: CallbackQuery, callback_data: Ph, db: DB) -> None:
    phase = db.get_phase(cb.from_user.id, callback_data.id)
    if not phase:
        return await menu_screen(cb, db, cb.from_user.id)
    photos = db.list_photos(phase["id"])
    if not photos:
        return await cb.answer("Фото ще нема — просто надішли їх у чат", show_alert=True)
    await cb.answer()
    for start in range(0, len(photos), 10):  # media group limit is 10
        chunk = photos[start:start + 10]
        await cb.message.answer_media_group(
            [InputMediaPhoto(media=p["file_id"], caption=(p["caption"] or "")[:1000] or None) for p in chunk]
        )
    await phase_screen(cb.message, db, cb.from_user.id, phase["id"])


@router.callback_query(Ph.filter(F.a == "notes"))
async def cb_notes(cb: CallbackQuery, callback_data: Ph, db: DB) -> None:
    phase = db.get_phase(cb.from_user.id, callback_data.id)
    if not phase:
        return await menu_screen(cb, db, cb.from_user.id)
    notes = db.list_notes(phase["id"])
    if not notes:
        return await cb.answer("Приміток ще нема", show_alert=True)
    await cb.answer()
    lines = [f"🗂 <b>{esc(phase['name'])}</b> — всі примітки:\n"]
    for n in notes:
        if n["text"]:
            lines.append(f"<i>{n['created_at'][:16]}</i>\n{esc(n['text'])}\n")
    if len(lines) > 1:
        await cb.message.answer(clip("\n".join(lines)))
    for n in notes:
        if n["voice_file_id"]:
            await cb.message.answer_voice(n["voice_file_id"], caption=n["created_at"][:16])
    await phase_screen(cb.message, db, cb.from_user.id, phase["id"])


# ---------- finishing a phase: "нічого не проєбав?" ----------
@router.callback_query(Fin.filter())
async def cb_finish(cb: CallbackQuery, callback_data: Fin, db: DB) -> None:
    phase = db.get_phase(cb.from_user.id, callback_data.p)
    if not phase:
        return await menu_screen(cb, db, cb.from_user.id)
    questions = checklist_for(phase["kind"])
    step, misses = callback_data.s, callback_data.m

    if callback_data.a == "start":
        open_items = [i for i in db.list_items(phase["id"]) if not i["done"]]
        if open_items and step == 0:
            b = InlineKeyboardBuilder()
            b.button(text="Все одно перевірити", callback_data=Fin(p=phase["id"], s=1, m=0, a="start"))
            b.button(text="⬅️ Назад до фази", callback_data=Ph(a="open", id=phase["id"]))
            b.adjust(1)
            listing = "\n".join(f"⬜ {esc(i['text'])}" for i in open_items[:20])
            return await show(cb, f"⚠️ Не відмічені пункти ({len(open_items)}):\n{listing}", b.as_markup())
        step = 0
    else:
        # Answer to question `step`; a "no" becomes a task in this phase right away.
        if callback_data.a == "n":
            db.add_items(phase["id"], [question_to_task(questions[step])])
            misses += 1
        step += 1

    if step < len(questions):
        b = InlineKeyboardBuilder()
        b.button(text="✅ Так", callback_data=Fin(p=phase["id"], s=step, m=misses, a="y"))
        b.button(text="❌ Ні", callback_data=Fin(p=phase["id"], s=step, m=misses, a="n"))
        b.button(text="⬅️ Перервати", callback_data=Ph(a="open", id=phase["id"]))
        b.adjust(2, 1)
        text = (f"🤔 <b>{esc(phase['name'])}</b> — нічого не проєбав?\n\n"
                f"Питання {step + 1}/{len(questions)}:\n<b>{esc(questions[step])}</b>")
        return await show(cb, text, b.as_markup())

    if misses:
        db.set_phase_status(phase["id"], "doing")
        await cb.message.answer(f"❗️ Є що доробити ({misses}) — додав у список фази. Фаза поки не закрита.")
    else:
        db.set_phase_status(phase["id"], "done")
        await cb.message.answer(f"✅ <b>{esc(phase['name'])}</b> — закрито. Красава 💪")
    await cb.answer()
    await phase_screen(cb.message, db, cb.from_user.id, phase["id"])


# ---------- free input: photos, voice, text ----------
async def _current_phase_or_hint(message: Message, db: DB):
    phase = db.get_current_phase(message.from_user.id)
    if not phase:
        await message.answer("Спочатку відкрий об'єкт і фазу — тоді збережу туди. /start")
    return phase


@router.message(StateFilter(None, Input.note), F.photo)
@router.message(StateFilter(None), F.document & F.document.mime_type.startswith("image/"))
async def on_photo(message: Message, db: DB) -> None:
    phase = await _current_phase_or_hint(message, db)
    if not phase:
        return
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    db.add_photo(phase["id"], file_id, message.caption)
    if message.media_group_id:
        return  # don't reply to every photo of an album
    b = InlineKeyboardBuilder()
    b.button(text="↩️ До фази", callback_data=Ph(a="open", id=phase["id"]))
    await message.reply(f"📷 Збережено в «{esc(phase['name'])}» ({esc(phase['object_name'])})",
                        reply_markup=b.as_markup())


@router.message(StateFilter(None, Input.note), F.voice)
async def on_voice(message: Message, state: FSMContext, db: DB) -> None:
    await state.clear()
    phase = await _current_phase_or_hint(message, db)
    if not phase:
        return
    db.add_note(phase["id"], voice_file_id=message.voice.file_id)
    b = InlineKeyboardBuilder()
    b.button(text="↩️ До фази", callback_data=Ph(a="open", id=phase["id"]))
    await message.reply(f"🎤 Голосову примітку збережено в «{esc(phase['name'])}»", reply_markup=b.as_markup())


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_free_text(message: Message, state: FSMContext, db: DB) -> None:
    phase = await _current_phase_or_hint(message, db)
    if not phase:
        return
    await state.update_data(pending_text=message.text, phase_id=phase["id"])
    b = InlineKeyboardBuilder()
    b.button(text="📝 Як примітку", callback_data=Txt(a="note"))
    b.button(text="📋 Як пункти", callback_data=Txt(a="items"))
    b.button(text="✖️ Не зберігати", callback_data=Txt(a="drop"))
    b.adjust(2, 1)
    await message.reply(f"Куди зберегти у «{esc(phase['name'])}» ({esc(phase['object_name'])})?",
                        reply_markup=b.as_markup())


@router.callback_query(StateFilter(None), Txt.filter())
async def cb_free_text(cb: CallbackQuery, callback_data: Txt, state: FSMContext, db: DB) -> None:
    data = await state.get_data()
    await state.clear()
    text, phase_id = data.get("pending_text"), data.get("phase_id")
    if callback_data.a == "drop" or not text or not db.get_phase(cb.from_user.id, phase_id):
        await cb.message.delete()
        return await cb.answer()
    if callback_data.a == "note":
        db.add_note(phase_id, text=text)
    else:
        db.add_items(phase_id, text.splitlines())
    await cb.message.delete()
    await cb.answer("Збережено")
    await phase_screen(cb.message, db, cb.from_user.id, phase_id)
