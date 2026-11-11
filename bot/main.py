import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from dotenv import load_dotenv

from .db import DB
from .handlers import router


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN не задано. Створи файл .env (див. .env.example)")

    db_path = Path(os.environ.get("DB_PATH", "data/bot.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = DB(db_path)

    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage(), db=db)
    dp.include_router(router)
    await bot.set_my_commands([
        BotCommand(command="start", description="Мої об'єкти"),
        BotCommand(command="cancel", description="Скасувати введення"),
    ])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
