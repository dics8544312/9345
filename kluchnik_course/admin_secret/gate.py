import asyncio
import hashlib
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, MessageOriginUser
from dotenv import load_dotenv

load_dotenv()
GRAD_CHAT_ID = int(os.environ["GRAD_CHAT_ID"])
KEYS = ["src:vorota-7", "jr:human:12", "Apply:waiting_from", "ads:3",
        "101-104-103", "gate", "conflict"]
WORD_TTL = 15 * 60
PROOF_RE = re.compile(r"PROOF ([0-9a-f]{8}) ([0-9A-F]{10})")

db = sqlite3.connect("gate.db")
db.execute("CREATE TABLE IF NOT EXISTS words (user_id INTEGER PRIMARY KEY, word TEXT, issued REAL)")
db.execute("CREATE TABLE IF NOT EXISTS grads (bot_id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE, ts REAL)")
db.commit()

router = Router()


def make_proof(user_id: int, bot_id: int, word: str) -> str:
    raw = "|".join([*KEYS, str(user_id), str(bot_id), word])
    return hashlib.sha256(raw.encode()).hexdigest()[:10].upper()


@router.message(CommandStart(), F.chat.type == "private")
async def start(message: Message):
    await message.answer("Я Привратник. Готов к экзамену — отправь /gate.")


@router.message(Command("chatid"))
async def chat_id(message: Message):
    await message.answer(f"<code>{message.chat.id}</code>")


@router.message(Command("gate"), F.chat.type == "private")
async def gate(message: Message):
    word = secrets.token_hex(4)
    db.execute(
        "INSERT OR REPLACE INTO words VALUES (?, ?, ?)",
        (message.from_user.id, word, time.time()),
    )
    db.commit()
    await message.answer(
        f"Твоё слово: <code>{word}</code>\n"
        f"Живёт 15 минут. Отправь своему боту /proof {word} "
        "и перешли мне его ответ. Перешли, а не скопируй."
    )


@router.message(F.chat.type == "private", F.forward_origin)
async def check(message: Message, bot: Bot):
    origin = message.forward_origin
    if not isinstance(origin, MessageOriginUser) or not origin.sender_user.is_bot:
        await message.answer("Это пересылка не от бота. Перешли ответ своего бота.")
        return
    found = PROOF_RE.fullmatch((message.text or "").strip())
    if not found:
        await message.answer("Не вижу пропуска. Нужен ответ вида: PROOF слово КОД")
        return
    word, code = found.groups()
    user_id, bot_id = message.from_user.id, origin.sender_user.id

    if db.execute("SELECT 1 FROM grads WHERE user_id = ?", (user_id,)).fetchone():
        await message.answer("Ты уже выпускник. Потерял ссылку — напиши админу.")
        return
    row = db.execute("SELECT word, issued FROM words WHERE user_id = ?", (user_id,)).fetchone()
    if row is None or row[0] != word:
        await message.answer("Это слово выдано не тебе. Возьми своё: /gate")
        return
    if time.time() - row[1] > WORD_TTL:
        await message.answer("Слово протухло. Возьми новое: /gate")
        return
    if db.execute("SELECT 1 FROM grads WHERE bot_id = ?", (bot_id,)).fetchone():
        await message.answer("Этот бот уже провёл другого человека. Нужен свой бот.")
        return
    if not secrets.compare_digest(code, make_proof(user_id, bot_id, word)):
        logging.info("fail user=%s bot=%s", user_id, bot_id)
        await message.answer("Код не сходится. Проверь ключи, их порядок и регистр.")
        return

    db.execute("INSERT INTO grads VALUES (?, ?, ?)", (bot_id, user_id, time.time()))
    db.execute("DELETE FROM words WHERE user_id = ?", (user_id,))
    db.commit()
    link = await bot.create_chat_invite_link(
        GRAD_CHAT_ID, name=f"grad {user_id}", member_limit=1, expire_date=timedelta(days=1)
    )
    await message.answer(
        f"Дверь открыта. Ссылка одноразовая и живёт сутки:\n{link.invite_link}"
    )


@router.message(F.chat.type == "private")
async def fallback(message: Message):
    if (message.text or "").startswith("PROOF"):
        await message.answer("Это копия. Перешли ответ бота кнопкой «Переслать».")
        return
    await message.answer("Для экзамена отправь /gate.")


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(os.environ["GATE_TOKEN"], default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
