import asyncio
import hashlib
import logging
import os
import time
import traceback
from contextlib import suppress
from datetime import timedelta

import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, ChatJoinRequest, ErrorEvent, LabeledPrice,
                           Message, PreCheckoutQuery)
from aiogram.utils.deep_linking import create_start_link
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
DB = "kluchnik.db"
PRICE_STARS = 100
SUB_DAYS = 30
KEYS = ["src:vorota-7", "jr:human:12", "Apply:waiting_from", "ads:3",
        "101-104-103", "gate", "conflict"]

router = Router()


# ---------- урок 4: база ----------
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                source TEXT,
                joined TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS subs (
                user_id INTEGER PRIMARY KEY,
                until TEXT NOT NULL,
                reminded INTEGER DEFAULT 0
            );
            """
        )
        await db.commit()


async def save_user(user_id: int, source: str | None):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, source) VALUES (?, ?)",
            (user_id, source),
        )
        await db.commit()


async def sources_report():
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT COALESCE(source, 'без метки'), COUNT(*) AS c FROM users "
            "GROUP BY source ORDER BY c DESC"
        )
        return await cur.fetchall()


# ---------- урок 5: подписки ----------
async def extend_sub(user_id: int, days: int) -> str:
    period = f"+{days} days"
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO subs (user_id, until) VALUES (?, datetime('now', ?)) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "until = datetime(max(until, datetime('now')), ?), reminded = 0",
            (user_id, period, period),
        )
        await db.commit()
        cur = await db.execute("SELECT until FROM subs WHERE user_id = ?", (user_id,))
        return (await cur.fetchone())[0]


async def sub_until(user_id: int) -> str | None:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT until FROM subs WHERE user_id = ? AND until > datetime('now')",
            (user_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def check_subs(bot: Bot):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT user_id, until FROM subs WHERE reminded = 0 "
            "AND until > datetime('now') AND until <= datetime('now', '+3 days')"
        )
        for user_id, until in await cur.fetchall():
            with suppress(TelegramAPIError):
                await bot.send_message(user_id, f"Доступ заканчивается {until} UTC. Продлить: /buy")
            await db.execute("UPDATE subs SET reminded = 1 WHERE user_id = ?", (user_id,))

        cur = await db.execute("SELECT user_id FROM subs WHERE until <= datetime('now')")
        for (user_id,) in await cur.fetchall():
            try:
                await bot.ban_chat_member(CHAT_ID, user_id)
                await bot.unban_chat_member(CHAT_ID, user_id, only_if_banned=True)
            except TelegramBadRequest as e:
                logging.warning("Не смог удалить %s: %s", user_id, e)
            await db.execute("DELETE FROM subs WHERE user_id = ?", (user_id,))
        await db.commit()


async def watch_subs(bot: Bot):
    while True:
        try:
            await check_subs(bot)
        except Exception:
            logging.exception("Проверка подписок упала")
        await asyncio.sleep(600)


# ---------- урок 2: заявки ----------
class Gate(CallbackData, prefix="jr"):
    act: str
    user_id: int


# ---------- урок 3: анкета ----------
class Apply(StatesGroup):
    waiting_who = State()
    waiting_from = State()


# ---------- урок 6: надёжность ----------
class AntiFlood(BaseMiddleware):
    def __init__(self, delay: float = 0.7):
        self.delay = delay
        self.last: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            now = time.monotonic()
            if now - self.last.get(user.id, 0) < self.delay:
                return  # слишком часто: молча пропускаем
            self.last[user.id] = now
        return await handler(event, data)


router.message.outer_middleware(AntiFlood())


# ---------- урок 8: пропуск ----------
def make_proof(user_id: int, bot_id: int, word: str) -> str:
    raw = "|".join([*KEYS, str(user_id), str(bot_id), word])
    return hashlib.sha256(raw.encode()).hexdigest()[:10].upper()


# ---------- команды: порядок важен ----------
@router.message(CommandStart(deep_link=True, deep_link_encoded=True))
async def start_from_link(message: Message, command: CommandObject):
    await save_user(message.from_user.id, command.args)
    await message.answer(f"Привет! Ты пришёл по метке: {command.args}")


@router.message(CommandStart())
async def start(message: Message):
    await save_user(message.from_user.id, None)
    await message.answer("Привет! Я Ключник, бот закрытого чата.\n/buy — купить доступ\n/status — мой доступ")


@router.message(Command("id"))
async def my_id(message: Message):
    await message.answer(f"Твой ID: {message.from_user.id}")


@router.message(Command("chatid"))
async def chat_id(message: Message):
    await message.answer(f"ID этого чата: {message.chat.id}")


@router.message(Command("link"), F.from_user.id == OWNER_ID)
async def make_link(message: Message, command: CommandObject, bot: Bot):
    if not command.args:
        await message.answer("Формат: /link метка, например /link youtube")
        return
    await message.answer(await create_start_link(bot, command.args, encode=True))


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменил.")


@router.message(Command("buy"))
async def buy(message: Message):
    await message.answer_invoice(
        title="Доступ в закрытый чат",
        description=f"{SUB_DAYS} дней доступа",
        payload=f"sub:{SUB_DAYS}",
        currency="XTR",
        prices=[LabeledPrice(label="Доступ", amount=PRICE_STARS)],
    )


@router.message(Command("status"))
async def status(message: Message):
    until = await sub_until(message.from_user.id)
    await message.answer(f"Доступ до {until} UTC." if until else "Доступа нет. Купить: /buy")


@router.message(Command("admin"), F.from_user.id == OWNER_ID)
async def admin(message: Message):
    rows = await sources_report()
    lines = [f"{source}: {count}" for source, count in rows] or ["пока никого"]
    await message.answer("Откуда пришли:\n" + "\n".join(lines))


@router.message(Command("refund"), F.from_user.id == OWNER_ID)
async def refund(message: Message, command: CommandObject, bot: Bot):
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("Формат: /refund USER_ID CHARGE_ID")
        return
    await bot.refund_star_payment(int(parts[0]), parts[1])
    await message.answer("Звёзды возвращены.")


@router.message(Command("proof"), F.from_user.id == OWNER_ID)
async def proof(message: Message, command: CommandObject, bot: Bot):
    if not command.args:
        await message.answer("Формат: /proof СЛОВО_ОТ_ПРИВРАТНИКА")
        return
    word = command.args.strip()
    code = make_proof(message.from_user.id, bot.id, word)
    await message.answer(f"PROOF {word} {code}")


# ---------- заявки и анкета ----------
@router.chat_join_request(F.chat.id == CHAT_ID)
async def on_join_request(request: ChatJoinRequest, bot: Bot):
    kb = InlineKeyboardBuilder()
    kb.button(text="Я человек", callback_data=Gate(act="human", user_id=request.from_user.id))
    await bot.send_message(
        request.user_chat_id,
        f"Ты подал заявку в «{request.chat.title}». Нажми кнопку, чтобы продолжить.",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(Gate.filter(F.act == "human"))
async def human(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Apply.waiting_who)
    await callback.message.edit_text("Спасибо! Два вопроса.\n1. Кто ты и чем занимаешься?")
    await callback.answer()


@router.callback_query(Gate.filter(F.act.in_({"ok", "no"})), F.from_user.id == OWNER_ID)
async def decide(callback: CallbackQuery, callback_data: Gate, bot: Bot):
    uid, ok = callback_data.user_id, callback_data.act == "ok"
    try:
        if ok:
            await bot.approve_chat_join_request(CHAT_ID, uid)
        else:
            await bot.decline_chat_join_request(CHAT_ID, uid)
    except TelegramBadRequest as e:
        await callback.answer(f"Не вышло: {e.message}"[:200], show_alert=True)
        return
    with suppress(TelegramAPIError):
        await bot.send_message(uid, "Заявка принята. Добро пожаловать!" if ok else "Заявка отклонена.")
    verdict = "принята" if ok else "отклонена"
    await callback.message.edit_text(f"{callback.message.text}\n\nЗаявка {verdict}.")
    await callback.answer()


@router.message(Apply.waiting_who, F.text)
async def apply_who(message: Message, state: FSMContext):
    await state.update_data(who=message.text)
    await state.set_state(Apply.waiting_from)
    await message.answer("2. Откуда узнал о нас?")


@router.message(Apply.waiting_from, F.text)
async def apply_from(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    user = message.from_user
    nick = f" @{user.username}" if user.username else ""
    kb = InlineKeyboardBuilder()
    kb.button(text="Принять", callback_data=Gate(act="ok", user_id=user.id))
    kb.button(text="Отклонить", callback_data=Gate(act="no", user_id=user.id))
    await bot.send_message(
        OWNER_ID,
        f"Заявка: {user.full_name}{nick}, id {user.id}\n\n"
        f"Кто: {data['who']}\nОткуда: {message.text}",
        reply_markup=kb.as_markup(),
    )
    await message.answer("Анкета у админа. Ответ придёт сюда.")


# ---------- оплата ----------
@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def paid(message: Message, bot: Bot):
    pay = message.successful_payment
    until = await extend_sub(message.from_user.id, SUB_DAYS)
    link = await bot.create_chat_invite_link(CHAT_ID, member_limit=1, expire_date=timedelta(days=1))
    await message.answer(f"Оплата прошла. Доступ до {until} UTC.\nТвоя одноразовая ссылка: {link.invite_link}")
    await bot.send_message(
        OWNER_ID,
        f"+{pay.total_amount} звёзд от {message.from_user.id}\n"
        f"Возврат: /refund {message.from_user.id} {pay.telegram_payment_charge_id}",
    )


# ---------- ошибки и всё остальное ----------
@router.errors()
async def on_error(event: ErrorEvent, bot: Bot):
    logging.error("Ошибка: %r", event.exception, exc_info=event.exception)
    tb = "".join(traceback.format_exception(event.exception))
    with suppress(TelegramAPIError):
        await bot.send_message(OWNER_ID, f"Ошибка в боте: {event.exception!r}\n\n{tb[-3500:]}")


@router.message(F.chat.type == "private")
async def fallback(message: Message):
    await message.answer("Не понял. Команды: /buy, /status")


async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    bot = Bot(os.environ["BOT_TOKEN"])
    dp = Dispatcher()
    dp.include_router(router)
    watcher = asyncio.create_task(watch_subs(bot))  # держим ссылку, иначе задачу съест сборщик мусора
    await dp.start_polling(bot)
    watcher.cancel()


if __name__ == "__main__":
    asyncio.run(main())
