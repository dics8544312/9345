"""Считает все 7 ключей курса тем же кодом, что в уроках. Запускай перед каждым потоком."""
import asyncio
import sqlite3
from datetime import datetime

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Chat, Message, Update, User
from aiogram.utils.payload import decode_payload

# Ключ 1: метка, зашитая в ссылку из урока 1
k1 = decode_payload("c3JjOnZvcm90YS03")


# Ключ 2: упаковка CallbackData
class Gate(CallbackData, prefix="jr"):
    act: str
    user_id: int


k2 = Gate(act="human", user_id=12).pack()


# Ключ 3: имя состояния
class Apply(StatesGroup):
    waiting_who = State()
    waiting_from = State()


k3 = Apply.waiting_from.state

# Ключ 4: первое касание
db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, source TEXT)")
db.executemany(
    "INSERT OR IGNORE INTO users VALUES (?, ?)",
    [(1, "yt"), (2, "ads"), (1, "ads"), (3, "yt"), (4, None), (2, "yt"), (5, "ads"), (6, "ads")],
)
source, count = db.execute(
    "SELECT COALESCE(source, 'none'), COUNT(*) AS c FROM users "
    "GROUP BY source ORDER BY c DESC, source LIMIT 1"
).fetchone()
k4 = f"{source}:{count}"

# Ключ 5: кому напомнить о продлении
db.execute("CREATE TABLE subs (user_id INTEGER PRIMARY KEY, until TEXT)")
db.executemany("INSERT INTO subs VALUES (?, ?)", [
    (101, "2026-10-02 10:00:00"), (102, "2026-09-30 23:00:00"),
    (103, "2026-10-04 09:00:00"), (104, "2026-10-03 18:00:00"),
    (105, "2026-11-01 00:00:00"),
])
now = "2026-10-01 12:00:00"
rows = db.execute(
    "SELECT user_id FROM subs WHERE until > ? AND until <= datetime(?, '+3 days') ORDER BY until",
    (now, now),
).fetchall()
k5 = "-".join(str(r[0]) for r in rows)

# Ключ 6: порядок middleware
log = []


class Mark(BaseMiddleware):
    def __init__(self, before, after):
        self.before, self.after = before, after

    async def __call__(self, handler, event, data):
        log.append(self.before)
        result = await handler(event, data)
        log.append(self.after)
        return result


router = Router()
router.message.outer_middleware(Mark("g", "e"))
router.message.outer_middleware(Mark("a", "t"))
router.message.middleware(Mark("r", "d"))


@router.message(F.text == "тук")
async def knock(message: Message):
    log.append("o")


async def k6():
    dp = Dispatcher()
    dp.include_router(router)
    bot = Bot("42:TEST")
    await dp.feed_update(bot, Update(update_id=1, message=Message(
        message_id=1, date=datetime.now(), text="привет", chat=Chat(id=1, type="private"),
        from_user=User(id=1, is_bot=False, first_name="Test"))))
    await bot.session.close()
    return "".join(log)


# Ключ 7: первое слово ошибки Telegram при двух запущенных ботах (409 Conflict)
k7 = "conflict"

keys = [k1, k2, k3, k4, k5, asyncio.run(k6()), k7]
print("KEYS =", keys)
