"""Фінанси: витрати зі своїх, отримані гроші, дні роботи, середній заробіток."""

from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db import DB
from .handlers import Fn, Nav, Obj, back_kb, esc, menu_screen, show
from .money import days_between, fmt, local_today, parse_amount, parse_date

router = Router()

DAYS_IN_MONTH = 30.4
MONTHS = ["січ", "лют", "бер", "кві", "тра", "чер", "лип", "сер", "вер", "жов", "лис", "гру"]


class FinInput(StatesGroup):
    amount = State()
    agreed = State()
    start = State()


def _d(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


# ---------- object finances ----------
async def object_finance_screen(target: Message | CallbackQuery, db: DB, user_id: int, obj_id: int) -> None:
    obj = db.get_object(user_id, obj_id)
    if not obj:
        return await menu_screen(target, db, user_id)
    got, spent = db.money_totals(obj_id)
    net = got - spent
    days = days_between(obj["started_on"], obj["finished_on"])

    text = f"💰 <b>Фінанси: {esc(obj['name'])}</b>\n\n"
    text += f"📅 Почав: {_d(obj['started_on'])}"
    if obj["finished_on"]:
        text += f" · здав: {_d(obj['finished_on'])}"
    text += f"\n⏱ Днів на об'єкті: <b>{days}</b>" + ("" if obj["finished_on"] else " (ще в роботі)") + "\n\n"
    if obj["agreed_cents"]:
        text += f"🤝 Домовились: {fmt(obj['agreed_cents'])}\n"
    text += f"✅ Отримав: <b>{fmt(got)}</b>\n"
    if obj["agreed_cents"]:
        left = obj["agreed_cents"] - got
        text += f"⏳ Ще мають доплатити: <b>{fmt(left)}</b>\n" if left > 0 else "👌 Розрахувались повністю\n"
    text += f"🛒 Витратив своїх: {fmt(spent)}\n"
    text += f"\n📈 Чистими: <b>{fmt(net)}</b>\n"
    text += f"📆 В середньому за день: <b>{fmt(net / days)}</b>"

    b = InlineKeyboardBuilder()
    b.button(text="➕ Отримав гроші", callback_data=Fn(a="in", id=obj_id))
    b.button(text="🛒 Купив за свої", callback_data=Fn(a="out", id=obj_id))
    b.button(text="🤝 Домовлена сума", callback_data=Fn(a="agreed", id=obj_id))
    b.button(text="📅 Дата початку", callback_data=Fn(a="start", id=obj_id))
    b.button(text="📜 Історія", callback_data=Fn(a="hist", id=obj_id))
    if obj["finished_on"]:
        b.button(text="↩️ Ще в роботі", callback_data=Fn(a="undone", id=obj_id))
    else:
        b.button(text="🏁 Об'єкт здано", callback_data=Fn(a="done", id=obj_id))
    b.button(text="⬅️ До об'єкта", callback_data=Obj(a="open", id=obj_id))
    b.adjust(2, 2, 2, 1)
    await show(target, text, b.as_markup())


@router.callback_query(Fn.filter(F.a == "obj"))
async def cb_obj_finance(cb: CallbackQuery, callback_data: Fn, state: FSMContext, db: DB) -> None:
    await state.clear()
    await object_finance_screen(cb, db, cb.from_user.id, callback_data.id)


@router.callback_query(Fn.filter(F.a.in_({"done", "undone"})))
async def cb_done(cb: CallbackQuery, callback_data: Fn, db: DB) -> None:
    value = local_today().isoformat() if callback_data.a == "done" else None
    db.set_object_field(cb.from_user.id, callback_data.id, "finished_on", value)
    await object_finance_screen(cb, db, cb.from_user.id, callback_data.id)


PROMPTS = {
    "in": "💵 Скільки отримав? Можна з приміткою:\n<i>15000 аванс</i>",
    "out": "🛒 Скільки витратив своїх і на що?\n<i>1250 кабель 3×2.5</i>",
    "agreed": "🤝 На яку суму домовились за весь об'єкт?\n<i>50000</i>  (0 — прибрати)",
    "start": "📅 Коли почав роботу на об'єкті? Формат: <i>05.11.2026</i> або <i>5.11</i>",
}


@router.callback_query(Fn.filter(F.a.in_(PROMPTS.keys())))
async def cb_fin_input(cb: CallbackQuery, callback_data: Fn, state: FSMContext, db: DB) -> None:
    if not db.get_object(cb.from_user.id, callback_data.id):
        return await menu_screen(cb, db, cb.from_user.id)
    state_for = {"in": FinInput.amount, "out": FinInput.amount, "agreed": FinInput.agreed, "start": FinInput.start}
    await state.set_state(state_for[callback_data.a])
    await state.update_data(obj_id=callback_data.id, kind=callback_data.a)
    await show(cb, PROMPTS[callback_data.a], back_kb(Fn(a="obj", id=callback_data.id), "❌ Скасувати"))


async def _get_obj_id(message: Message, state: FSMContext, db: DB) -> tuple[int, str] | None:
    data = await state.get_data()
    if not db.get_object(message.from_user.id, data.get("obj_id", 0)):
        await state.clear()
        await menu_screen(message, db, message.from_user.id)
        return None
    return data["obj_id"], data["kind"]


@router.message(FinInput.amount, F.text)
async def input_amount(message: Message, state: FSMContext, db: DB) -> None:
    got = await _get_obj_id(message, state, db)
    if not got:
        return
    obj_id, kind = got
    parsed = parse_amount(message.text)
    if not parsed:
        return await message.answer("Не зрозумів суму 🤔 Напиши число на початку, наприклад: <i>1500 автомати</i>")
    cents, note = parsed
    db.add_money(obj_id, kind, cents, note)
    await state.clear()
    await message.answer(("✅ Записав надходження " if kind == "in" else "🛒 Записав витрату ") + fmt(cents))
    await object_finance_screen(message, db, message.from_user.id, obj_id)


@router.message(FinInput.agreed, F.text)
async def input_agreed(message: Message, state: FSMContext, db: DB) -> None:
    got = await _get_obj_id(message, state, db)
    if not got:
        return
    obj_id = got[0]
    if message.text.strip() == "0":
        cents = None
    else:
        parsed = parse_amount(message.text)
        if not parsed:
            return await message.answer("Не зрозумів суму 🤔 Напиши число, наприклад: <i>50000</i>")
        cents = parsed[0]
    db.set_object_field(message.from_user.id, obj_id, "agreed_cents", cents)
    await state.clear()
    await object_finance_screen(message, db, message.from_user.id, obj_id)


@router.message(FinInput.start, F.text)
async def input_start(message: Message, state: FSMContext, db: DB) -> None:
    got = await _get_obj_id(message, state, db)
    if not got:
        return
    obj_id = got[0]
    day = parse_date(message.text)
    if not day or day > local_today():
        return await message.answer("Не зрозумів дату 🤔 Напиши так: <i>05.11.2026</i>")
    db.set_object_field(message.from_user.id, obj_id, "started_on", day.isoformat())
    await state.clear()
    await object_finance_screen(message, db, message.from_user.id, obj_id)


# ---------- history ----------
async def history_screen(cb: CallbackQuery, db: DB, obj_id: int) -> None:
    rows = db.list_money(obj_id, limit=20)
    if not rows:
        return await cb.answer("Записів ще нема", show_alert=True)
    b = InlineKeyboardBuilder()
    for r in rows:
        sign = "➕" if r["kind"] == "in" else "➖"
        label = f"🗑 {_d(r['day'])[:5]} {sign}{fmt(r['cents'])} {r['note'] or ''}"
        b.button(text=label[:60], callback_data=Fn(a="del", id=r["id"]))
    b.button(text="⬅️ Назад", callback_data=Fn(a="obj", id=obj_id))
    b.adjust(1)
    await show(cb, "📜 <b>Останні записи</b>\n➕ отримав · ➖ витратив своїх\n\nТисни на запис, щоб видалити його:",
               b.as_markup())


@router.callback_query(Fn.filter(F.a == "hist"))
async def cb_history(cb: CallbackQuery, callback_data: Fn, db: DB) -> None:
    if not db.get_object(cb.from_user.id, callback_data.id):
        return await menu_screen(cb, db, cb.from_user.id)
    await history_screen(cb, db, callback_data.id)


@router.callback_query(Fn.filter(F.a == "del"))
async def cb_money_del(cb: CallbackQuery, callback_data: Fn, db: DB) -> None:
    row = db.get_money(cb.from_user.id, callback_data.id)
    if not row:
        return await cb.answer("Запис не знайдено")
    db.delete_money(row["id"])
    if db.list_money(row["object_id"], limit=1):
        await history_screen(cb, db, row["object_id"])
    else:
        await object_finance_screen(cb, db, cb.from_user.id, row["object_id"])


# ---------- overall stats ----------
@router.callback_query(Fn.filter(F.a == "stats"))
async def cb_stats(cb: CallbackQuery, state: FSMContext, db: DB) -> None:
    await state.clear()
    user_id = cb.from_user.id
    months = db.user_money_by_month(user_id)
    first = db.user_first_start(user_id)
    back = back_kb(Nav(to="menu"))
    if not months or not first:
        return await show(cb, "📊 Фінансових записів ще нема.\n\nВідкрий об'єкт → 💰 Фінанси об'єкта.", back)

    got = sum(m["i"] for m in months)
    spent = sum(m["o"] for m in months)
    net = got - spent
    days = days_between(first)
    per_day = net / days

    text = "📊 <b>Мої фінанси</b>\n\n"
    text += f"✅ Отримав всього: {fmt(got)}\n🛒 Витратив своїх: {fmt(spent)}\n📈 Чистими: <b>{fmt(net)}</b>\n\n"
    text += f"⏱ Працюю з {_d(first)} — {days} дн.\n"
    text += f"📆 Середня в день: <b>{fmt(per_day)}</b>\n"
    text += f"🗓 Середня в місяць: <b>{fmt(per_day * DAYS_IN_MONTH)}</b>"
    if days < 30:
        text += "\n<i>(менше місяця даних — місячна сума приблизна)</i>"

    text += "\n\n<b>По місяцях</b> (чистими):\n"
    for m in months[:12]:
        year, month = m["month"].split("-")
        text += f"{MONTHS[int(month) - 1]} {year}: {fmt(m['i'] - m['o'])}\n"
    await show(cb, text, back)
