# bot.py
# aiogram 3.x (executor ishlatilmaydi). Async SQLite bilan (aiosqlite).
# Python 3.9+ tavsiya qilinadi.

import asyncio
import os
from datetime import datetime, date
import aiosqlite
import pytz
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
import sqlite3

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN") #or 
ADMIN_ID = int(os.getenv("ADMIN_ID")) #or
DB_PATH = os.getenv("DB_PATH") #or 
TIMEZONE = os.getenv("TIMEZONE") #or "Asia/Tashkent"

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# ---------- Init bot & dispatcher ----------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
tz = pytz.timezone(TIMEZONE)
scheduler = AsyncIOScheduler(timezone=tz)


# ---------- Keyboards ----------
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Rejalarni ro‘yxatga olish")],
        [KeyboardButton(text="🎯 Kunlik kvestni olish")],
        [KeyboardButton(text="📊 Hisobotni olish")],
       
    ],
    resize_keyboard=True
)
main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="O'zgarish")],
       
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

report_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔥 Faollar"), KeyboardButton(text="✅ Yakunlanganlar")],
        [KeyboardButton(text="⬅️ Orqaga")]
    ],
    resize_keyboard=True
)


# ---------- Database helpers ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            phone TEXT,
            join_date TEXT,
            state TEXT DEFAULT 'idle'  -- idle | adding
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            seq INTEGER,           -- sequence per user (increasing)
            content TEXT,
            created_at TEXT,
            status TEXT DEFAULT 'active', -- active | completed
            origin_id INTEGER DEFAULT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS daily_sent (
            user_id INTEGER,
            date TEXT,
            PRIMARY KEY (user_id, date)
        )
        """)
        await db.commit()


async def is_registered(user_id: int) -> bool:
    """Return True if user has phone saved (registered)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT phone FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
    return bool(row and row[0])


async def ensure_user_record(u: types.User):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users WHERE user_id=?", (u.id,))
        row = await cur.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO users(user_id, full_name, join_date) VALUES(?, ?, ?)",
                (u.id, u.full_name, datetime.now(tz).isoformat())
            )
            await db.commit()


async def update_user_phone(user_id: int, phone: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
        await db.commit()


async def set_user_state(user_id: int, state: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET state=? WHERE user_id=?", (state, user_id))
        await db.commit()


async def get_user_state(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT state FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else "idle"


async def next_seq(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT MAX(seq) FROM tasks WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        maxseq = row[0] if row and row[0] is not None else 0
        return maxseq + 1


async def add_task(user_id: int, content: str, origin_id: int = None):
    seq = await next_seq(user_id)
    now = datetime.now(tz).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO tasks(user_id, seq, content, created_at, status, origin_id) VALUES(?,?,?,?,?,?)",
            (user_id, seq, content, now, "active", origin_id)
        )
        await db.commit()
        return cur.lastrowid, seq, now


async def get_next_n_active(user_id: int, n: int = 3):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id, seq, content, created_at 
            FROM tasks 
            WHERE user_id=? AND status='active' AND origin_id IS NULL
            ORDER BY seq ASC
            LIMIT ?
            """,
            (user_id, n)
        )
        rows = await cur.fetchall()
        return rows




async def mark_completed(task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tasks SET status='completed' WHERE id=?", (task_id,))
        await db.commit()


async def re_register(task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        # eski reja ma'lumotini olish
        cur = await db.execute("SELECT user_id, content FROM tasks WHERE id=?", (task_id,))
        row = await cur.fetchone()
        if not row:
            return None
        user_id, content = row

        # eski rejani o'chirish
        await db.execute("DELETE FROM tasks WHERE id=?", (task_id,))

        # yangi reja qo'shish
        seq_cur = await db.execute("SELECT MAX(seq) FROM tasks WHERE user_id=?", (user_id,))
        maxseq = await seq_cur.fetchone()
        next_seq = (maxseq[0] or 0) + 1

        now = datetime.now().isoformat()
        cur = await db.execute(
            "INSERT INTO tasks(user_id, seq, content, created_at, status) VALUES(?,?,?,?,?)",
            (user_id, next_seq, content, now, "active")
        )
        await db.commit()
        return cur.lastrowid, next_seq, now




async def list_tasks_by_status(user_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, seq, content, created_at, origin_id FROM tasks WHERE user_id=? AND status=? ORDER BY seq ASC",
            (user_id, status)
        )
        rows = await cur.fetchall()
        return rows


async def list_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, full_name, phone FROM users ORDER BY user_id ASC")
        rows = await cur.fetchall()
        return rows


async def has_daily_sent(user_id: int, dt: date) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM daily_sent WHERE user_id=? AND date=?", (user_id, dt.isoformat()))
        return await cur.fetchone() is not None


async def mark_daily_sent(user_id: int, dt: date):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO daily_sent(user_id, date) VALUES(?,?)", (user_id, dt.isoformat()))
        await db.commit()


# ---------- Helper: pretty date ----------
def pretty_dt(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts)
        return dt.strftime("%d.%m.%Y")  # Masalan: 08.11.2025
    except Exception:
        return iso_ts




# ---------- Handlers ----------
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    # foydalanuvchini DBga ro'yxatga olish (avvalgi funksiya saqlanadi)
    await ensure_user_record(message.from_user)
    # darhol asosiy menyuni ko'rsatamiz — telefon majburiy emas
    await message.answer("Hayotingizni o'zgartiruvchi tugma turibdi uni bosing", parse_mode="HTML", reply_markup=main_menu_kb)



@dp.message(F.text == "📝 Rejalarni ro‘yxatga olish")
async def enter_adding_mode(message: types.Message):
    await set_user_state(message.from_user.id, "adding")
    await message.answer(
        "Rejalarni yozing — har bir yangi xabar yangi reja bo‘ladi.\n"
        "Tamom bo‘lgach `/stop` yozing.",
        reply_markup=types.ReplyKeyboardRemove()
    )


@dp.message()
async def catch_all_messages(message: types.Message):
    # generic handler: if user is in 'adding' state, save plans; else ignore or reply main menu
    state = await get_user_state(message.from_user.id)
    text = message.text.strip()
    if state == "adding":
        if text.lower() == "/stop":
            await set_user_state(message.from_user.id, "idle")
            await message.answer("✅ Rejalarni qoʻshish yakunlandi.", reply_markup=main_menu)
            return
        # add task
        tid, seq, created_at = await add_task(message.from_user.id, text)
        pretty = pretty_dt(created_at)
        await message.answer(f"➕ Saqlandi:\n №{seq}. {pretty}. \"{text}\"")
        return
    # If not adding: react to main menu texts if they match
    if text == "🎯 Kunlik kvestni olish":
        # route to daily quest handler
        await send_daily_to_user(message.from_user.id, explicit=True)
        return
    if text == "📊 Hisobotni olish":
        await message.answer("Hisobot turi:", reply_markup=report_menu)
        return
    if text == "✅ Yakunlanganlar":
        rows = await list_tasks_by_status(message.from_user.id, "completed")
        if not rows:
            await message.answer("Yakunlanganlar topilmadi.", reply_markup=main_menu)
            return
        text_out = "✅ Yakunlanganlar:\n"
        for r in rows:
            tid, seq, content, created_at, origin = r
            text_out += f"№{seq}. {pretty_dt(created_at)}. \"{content}\"\n"
        await message.answer(text_out, reply_markup=main_menu)
        return
    # if text == "🔁 Qayta hisobga olinganlar" or text == "🔁 Qayta hisobga olinganlar":
    #     async with aiosqlite.connect(DB_PATH) as db:
    #         cur = await db.execute(
    #             "SELECT id, seq, content, created_at, origin_id FROM tasks WHERE user_id=? AND origin_id IS NOT NULL ORDER BY seq ASC",
    #             (message.from_user.id,)
    #         )
    #        rows = await cur.fetchall()
        if not rows:
            await message.answer("Qayta ro'yxatga olinganlar yo'q.", reply_markup=main_menu)
            return
        out = "🔁 Qayta ro'yxatga olinganlar:\n"
        for r in rows:
            tid, seq, content, created_at, origin = r
            out += f"№{seq}. {pretty_dt(created_at)}. \"{content}\" (manba id:{origin})\n"
        await message.answer(out, reply_markup=main_menu)
        return
    if text == "🔥 Faollar":
        rows = await list_tasks_by_status(message.from_user.id, "active")
        if not rows:
            await message.answer("Faol rejalar yo'q.", reply_markup=main_menu)
            return
        out = "🔥 Faol rejalar:\n"
        for r in rows:
            tid, seq, content, created_at, origin = r
            out += f"№{seq}. {pretty_dt(created_at)}. \"{content}\"\n"
        await message.answer(out, reply_markup=main_menu)
        return
    if text == "⬅️ Orqaga":
        await message.answer("🔙 Asosiy menyu", reply_markup=main_menu)
        return
    # admin route
    if text == "/admin" and message.from_user.id == ADMIN_ID:
        await admin_menu(message)
        return
    # default small reply
    await message.answer("Mayda va siz ahamyatsiz deb hisoblagan\nAmmo siz qilishiz kerak bo'lgan ishlarni\nRejalashtirishingiz kerak", reply_markup=main_menu)


# ---------- Inline callback handlers ----------
@dp.callback_query(F.data.startswith("done:"))
async def on_done(cb: types.CallbackQuery):
    # data format: done:<task_id>
    task_id = int(cb.data.split(":", 1)[1])
    # ensure this task belongs to user
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, content FROM tasks WHERE id=?", (task_id,))
        row = await cur.fetchone()
    if not row:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    owner_id, content = row
    if owner_id != cb.from_user.id and cb.from_user.id != ADMIN_ID:
        await cb.answer("Bu tugma sizga tegishli emas.", show_alert=True)
        return
    await mark_completed(task_id)
    await cb.message.edit_text(f"✅ \"{content}\" bajarildi.")
    await cb.answer("Bajarildi ✅")


@dp.callback_query(F.data.startswith("readd:"))
async def on_readd(cb: types.CallbackQuery):
    # data format: readd:<task_id>
    task_id = int(cb.data.split(":", 1)[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, content FROM tasks WHERE id=?", (task_id,))
        row = await cur.fetchone()
    if not row:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    owner_id, content = row
    if owner_id != cb.from_user.id and cb.from_user.id != ADMIN_ID:
        await cb.answer("Bu tugma sizga tegishli emas.", show_alert=True)
        return
    new = await re_register(task_id)
    if new:
        new_id, new_seq, created_at = new
        await cb.message.edit_text(f"🔁 \"{content}\" qayta ro'yxatga olindi (№{new_seq}).")
        await cb.answer("Qayta ro'yxatga olindi")
    else:
        await cb.answer("Xatolik yuz berdi.", show_alert=True)


# ---------- Sending daily tasks ----------
async def send_daily_to_user(user_id: int, explicit: bool = False):
    # explicit True when user pressed button (do not check daily_sent)
    rows = await get_next_n_active(user_id, 3)
    if not rows:
        if explicit:
            await bot.send_message(user_id, "Sizda faol rejalar topilmadi.", reply_markup=main_menu)
        return
    for r in rows:
        tid, seq, content, created_at = r
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Bajarildi", callback_data=f"done:{tid}")
        builder.button(text="🔁 Qayta registratsiya", callback_data=f"readd:{tid}")
        markup = builder.as_markup()
        await bot.send_message(user_id, f"№{seq}. {pretty_dt(created_at)}. \"{content}\"", reply_markup=markup)
    if not explicit:
        # mark daily sent for auto-sends
        await mark_daily_sent(user_id, date.today())


async def daily_auto_job():
    # runs at TIMEZONE 09:00
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
    today = date.today()
    for (uid,) in rows:
        try:
            if not await has_daily_sent(uid, today):
                await send_daily_to_user(uid, explicit=False)
        except Exception:
            # ignore individual failures
            continue


# ---------- Admin ----------
async def admin_menu(message: types.Message):
    rows = await list_all_users()
    if not rows:
        await message.answer("Foydalanuvchilar topilmadi.")
        return
    text = "👥 Foydalanuvchilar:\n\n"
    for r in rows:
        uid, full_name, phone = r
        text += f"ID: {uid}\nIsm: {full_name}\n\n"
    await message.answer(text)


# ---------- Startup ----------
async def on_startup():
    await init_db()
    # schedule daily job at 09:00
    scheduler.add_job(daily_auto_job, "cron", hour=9, minute=0)
    scheduler.start()


async def main():
    await on_startup()
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
