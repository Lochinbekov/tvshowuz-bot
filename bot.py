# TvShowUzBot OMDB api kaliti kerak .env ga qoyiladi. tg bot api ham qoyilishi kerak 
import asyncio
import logging
import os
import re
import sqlite3
import time
from io import BytesIO
from math import cos, pi, sin
from datetime import datetime, timedelta
from html import escape, unescape as html_unescape
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ChatMemberUpdated,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultPhoto,
    BufferedInputFile,
    InputMediaPhoto,
    InputTextMessageContent,
    InlineKeyboardButton as B,
    InlineKeyboardMarkup as KB,
    Message,
)
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent")) #vaqt serialni qachon boshlahanu uchun kerak
DB_PATH = os.getenv("DB_PATH", "tvbot.db")
if not TOKEN:
    raise SystemExit("BOT_TOKEN topilmadi. .env faylga BOT_TOKEN=... deb yozing.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tvbot")
BOT_USERNAME = ""

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(
    """
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, active_chat INTEGER, post_on INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS chats(
    chat_id INTEGER PRIMARY KEY, title TEXT, kind TEXT, owner_id INTEGER);
CREATE TABLE IF NOT EXISTS shows(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, tvmaze_id INTEGER,
    name TEXT, poster TEXT, rating REAL, status TEXT, premiered TEXT,
    genres TEXT, summary TEXT, UNIQUE(user_id, tvmaze_id));
CREATE TABLE IF NOT EXISTS watched(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, show_id INTEGER,
    season INTEGER, number INTEGER, ep_name TEXT, runtime INTEGER,
    score INTEGER, ts INTEGER, UNIQUE(show_id, season, number));
"""
)


for _ddl in (
    "ALTER TABLE shows ADD COLUMN uz INTEGER DEFAULT 0",
    "ALTER TABLE watched ADD COLUMN post_chat INTEGER",
    "ALTER TABLE watched ADD COLUMN post_msg INTEGER",
    "ALTER TABLE watched ADD COLUMN post_kind TEXT",
    "ALTER TABLE watched ADD COLUMN past INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN use_photo INTEGER DEFAULT 1",
    "ALTER TABLE users ADD COLUMN auto_pin INTEGER DEFAULT 1",
    "ALTER TABLE shows ADD COLUMN post_chat INTEGER",
    "ALTER TABLE shows ADD COLUMN post_msg INTEGER",
    "ALTER TABLE shows ADD COLUMN pinned INTEGER DEFAULT 0",
    "ALTER TABLE shows ADD COLUMN fin_chat INTEGER",
    "ALTER TABLE shows ADD COLUMN fin_msg INTEGER",
    "ALTER TABLE shows ADD COLUMN fin_kind TEXT",
    "ALTER TABLE shows ADD COLUMN state TEXT",
    "ALTER TABLE shows ADD COLUMN total INTEGER",
    "ALTER TABLE shows ADD COLUMN aired INTEGER",
    "ALTER TABLE shows ADD COLUMN total_ts INTEGER",
):
    try:
        db.execute(_ddl)
        db.commit()
    except sqlite3.OperationalError:
        pass


db.executescript(
    """
CREATE TABLE IF NOT EXISTS catalog(
    tvmaze_id INTEGER PRIMARY KEY, name TEXT, genres TEXT, rating REAL, premiered TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS seeds_done(name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS seeds_imdb(name TEXT PRIMARY KEY);
"""
)


def q(sql, args=(), one=False):
    cur = db.execute(sql, args)
    db.commit()
    rows = cur.fetchall()
    if one:
        return rows[0] if rows else None
    return rows


if not q("SELECT 1 FROM seeds_imdb LIMIT 1", one=True):
    q("DELETE FROM catalog")


def user(uid):
    q("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))
    return q("SELECT * FROM users WHERE user_id=?", (uid,), one=True)


def show_row(sid):
    return q("SELECT * FROM shows WHERE id=?", (sid,), one=True)


_cache = {}


async def api(path):
    hit = _cache.get(path)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get("https://api.tvmaze.com" + path) as r:
                if r.status != 200:
                    return None
                data = await r.json()
    except Exception as e:
        log.warning("TVMaze xato: %s", e)
        return None
    _cache[path] = (time.time(), data)
    return data


async def episodes(tvid):
    data = await api(f"/shows/{tvid}/episodes") or []
    eps = [e for e in data if e.get("number") is not None]
    eps.sort(key=lambda e: (e["season"], e["number"]))
    return eps


OMDB_KEY = os.getenv("OMDB_API_KEY", "").strip()
_omdb_cache = {}
CATALOG_MIN = 7.5


async def omdb(**params):
    if not OMDB_KEY:
        return None
    ck = tuple(sorted(params.items()))
    hit = _omdb_cache.get(ck)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get("https://www.omdbapi.com/", params={"apikey": OMDB_KEY, **params}) as r:
                data = await r.json()
    except Exception as e:
        log.warning("OMDb xato: %s", e)
        return None
    if not isinstance(data, dict) or data.get("Response") == "False":
        return None
    _omdb_cache[ck] = (time.time(), data)
    return data


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def imdb_id_of(tvid):
    info = await api(f"/shows/{tvid}") or {}
    return (info.get("externals") or {}).get("imdb")


async def imdb_show_rating(tvid, imdb=None):
    imdb = imdb or await imdb_id_of(tvid)
    if not imdb:
        return None
    d = await omdb(i=imdb)
    return _num((d or {}).get("imdbRating"))


async def imdb_ep_rating(tvid, season, number):
    imdb = await imdb_id_of(tvid)
    if not imdb:
        return None
    d = await omdb(i=imdb, Season=season)
    for ep in (d or {}).get("Episodes") or []:
        if str(ep.get("Episode")) == str(number):
            return _num(ep.get("imdbRating"))
    return None


def stars(n):
    return "★" * n + "☆" * (10 - n) #yuldiuz


def clean(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def short(t, n=300):
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


UZ_GENRES = {
    "Drama": "Drama", "Comedy": "Komediya", "Crime": "Jinoyat", "Action": "Jangari",
    "Adventure": "Sarguzasht", "Anime": "Anime", "Children": "Bolalar uchun",
    "DIY": "O'zi qil", "Espionage": "Josuslik", "Family": "Oilaviy", "Fantasy": "Fantastika",
    "Food": "Taom", "History": "Tarixiy", "Horror": "Qo'rqinchli", "Legal": "Huquqiy",
    "Medical": "Tibbiy", "Music": "Musiqiy", "Mystery": "Sirli", "Nature": "Tabiat",
    "Romance": "Romantik", "Science-Fiction": "Ilmiy fantastika", "Sports": "Sport",
    "Supernatural": "G'ayritabiiy", "Thriller": "Triller", "Travel": "Sayohat",
    "War": "Urush", "Western": "Vestern",
}
UZ_STATUS = {
    "Running": "Davom etmoqda",
    "Ended": "Tugagan",
    "To Be Determined": "Hali noma'lum",
    "In Development": "Ishlab chiqilmoqda",
}


def uz_genres(g):
    return ", ".join(UZ_GENRES.get(x.strip(), x.strip()) for x in (g or "").split(",") if x.strip())


def _chunks(text, n=450):
    out, cur = [], ""
    for p in re.split(r"(?<=[.!?])\s+", text):
        if len(cur) + len(p) + 1 <= n:
            cur = (cur + " " + p).strip()
        else:
            if cur:
                out.append(cur)
            cur = p[:n]
    if cur:
        out.append(cur)
    return out


async def _tr_google(sess, text):
    async with sess.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "en", "tl": "uz", "dt": "t", "q": text},
        headers={"User-Agent": "Mozilla/5.0"},
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"google status {resp.status}")
        data = await resp.json(content_type=None)
    out = "".join(p[0] for p in data[0] if p and p[0]).strip()
    if not out:
        raise RuntimeError("google bo'sh javob")
    return out


async def _tr_mymemory(sess, text):
    async with sess.get(
        "https://api.mymemory.translated.net/get",
        params={"q": text, "langpair": "en|uz"},
    ) as resp:
        data = await resp.json(content_type=None)
    out = html_unescape((data.get("responseData") or {}).get("translatedText", "")).strip()
    if not out or out.upper().startswith("MYMEMORY WARNING"):
        raise RuntimeError("mymemory javob bermadi")
    return out


async def translate_uz(text):
    """Inglizcha -> o'zbekcha. Bir nechta xizmat bilan urinadi. Muvaffaqiyatsiz bo'lsa None."""
    if not text:
        return None
    parts = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as sess:
        for chunk in _chunks(text[:1200]):
            for fn in (_tr_google, _tr_mymemory):
                try:
                    parts.append(await fn(sess, chunk))
                    break
                except Exception as e:
                    log.warning("Tarjima (%s) xato: %s", fn.__name__, e)
            else:
                return None
    return " ".join(parts)


async def ensure_uz(sid):
    """Serial tavsifini o'zbekchaga o'giradi (bir marta) va bazaga saqlaydi."""
    s = show_row(sid)
    if s["summary"] and not s["uz"]:
        t = await translate_uz(s["summary"])
        if t:
            q("UPDATE shows SET summary=?, uz=1 WHERE id=?", (t, sid))
            s = show_row(sid)
    return s


def now():
    return datetime.now(TZ)


def today_ts():
    return int(now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def active_chat(uid):
    return q(
        "SELECT c.* FROM users u JOIN chats c ON c.chat_id=u.active_chat WHERE u.user_id=?",
        (uid,),
        one=True,
    )


async def post(bot, uid, text, photo=None):
    """True — yuborildi, False — xato, None — kanal ulanmagan yoki o'chirilgan."""
    u = user(uid)
    if not u["post_on"] or not u["active_chat"]:
        return None
    try:
        if photo and u["use_photo"]:
            try:
                await bot.send_photo(u["active_chat"], photo, caption=text)
                return True
            except Exception as e:
                log.warning("rasm yuborilmadi: %s", e)
        await bot.send_message(u["active_chat"], text)
        return True
    except Exception as e:
        log.warning("kanalga yozib bo'lmadi: %s", e)
        return False


async def send_rated(bot, chat_id, text, photos):
    """Rasmlarni ketma-ket sinaydi, bo'lmasa matn. -> (status, message_id, kind)"""
    for url in photos:
        try:
            m = await bot.send_photo(chat_id, url, caption=text)
            return True, m.message_id, "photo"
        except Exception as e:
            log.warning("rasm yuborilmadi: %s", e)
    try:
        m = await bot.send_message(chat_id, text)
        return True, m.message_id, "text"
    except Exception as e:
        log.warning("kanalga yozib bo'lmadi: %s", e)
        return False, None, None


async def edit_post(bot, chat_id, msg_id, kind, text):
    """Kanaldagi eski xabarni tahrirlaydi. Muvaffaqiyatli bo'lsa True."""
    try:
        if kind == "photo":
            await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=text)
        else:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id)
        return True
    except TelegramBadRequest as e:
        return "not modified" in str(e)
    except Exception as e:
        log.warning("tahrirlab bo'lmadi: %s", e)
        return False


PIN_NOTE = (
    "📌 Serial xabarini pin qilib bo'lmadi.\n"
    "Kanal sozlamalarida botga «Xabarlarni tahrirlash» (guruhda — «Xabarlarni pin qilish») huquqini bering."
)


async def announce_show(bot, uid, sid, caption):
    """Yangi serial kartasini kanalga yuboradi va (yoqilgan bo'lsa) pin qiladi. -> (status, pin_ok)"""
    u = user(uid)
    s = show_row(sid)
    if not u["post_on"] or not u["active_chat"]:
        return None, None
    chat = u["active_chat"]
    photos = [s["poster"]] if s["poster"] and u["use_photo"] else []
    ok, mid, _ = await send_rated(bot, chat, caption, photos)
    if not ok:
        return False, None
    q("UPDATE shows SET post_chat=?, post_msg=? WHERE id=?", (chat, mid, sid))
    if not u["auto_pin"]:
        return True, None
    for o in q("SELECT id, post_msg FROM shows WHERE user_id=? AND post_chat=? AND pinned=1 AND id!=?", (uid, chat, sid)):
        try:
            await bot.unpin_chat_message(chat, o["post_msg"])
        except Exception:
            pass
        q("UPDATE shows SET pinned=0 WHERE id=?", (o["id"],))
    try:
        await bot.pin_chat_message(chat, mid, disable_notification=True)
        q("UPDATE shows SET pinned=1 WHERE id=?", (sid,))
        return True, True
    except Exception as e:
        log.warning("pin qilib bo'lmadi: %s", e)
        return True, False


POST_STATUS = {
    True: "📢 Kanalga yuborildi ✅",
    False: "⚠️ Kanalga yuborib bo'lmadi — bot admin ekanini tekshiring",
    None: "ℹ️ Kanal ulanmagan yoki yuborish o'chirilgan",
}


async def screen(cb: CallbackQuery, text, kb=None):
    """Xabarni tahrirlaydi; rasmli xabar bo'lsa o'chirib, yangisini yuboradi."""
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            return
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.message.answer(text, reply_markup=kb)


def menu_kb():
    return KB(
        inline_keyboard=[
            [B(text="🔎 Serial qo'shish", callback_data="add")],
            [B(text="📺 Seriallarim", callback_data="my"), B(text="📊 Statistika", callback_data="st")],
            [B(text="🎯 Tavsiyalar", callback_data="reco"), B(text="⚙️ Sozlamalar", callback_data="cfg")],
            [B(text="📢 Kanal / guruh", callback_data="ch"), B(text="❓ Yordam", callback_data="help")],
        ]
    )


def back_kb():
    return KB(inline_keyboard=[[B(text="🏠 Menyu", callback_data="menu")]])


def view_menu(uid):
    c = active_chat(uid)
    ch = f"📢 Ulangan: <b>{escape(c['title'] or '')}</b>" if c else "📢 Kanal ulanmagan — «Kanal / guruh» tugmasini bosing"
    text = (
        "🎬 <b>TvShowUz</b>\n\n"
        "Serial ko'rganingizni epizod-epizod baholang — men kanalingizga avtomatik yozib boraman.\n\n"
        "🔎 Serial nomini shunchaki yozib yuboring (masalan: <i>Breaking Bad</i>).\n\n" + ch
    )
    return text, menu_kb()


STALE_DAYS = int(os.getenv("STALE_DAYS", "14"))
WEEKDAYS = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]
STATE_INFO = {
    "watch": ("▶️", "Ko'rilmoqda"),
    "wait": ("⏳", "Yangi epizod kutilmoqda"),
    "stale": ("⏸", "Chala qolgan"),
    "done": ("✅", "To'liq ko'rilgan"),
    "drop": ("❌", "Tashlab ketilgan"),
    "new": ("🆕", "Hali boshlanmagan"),
}
STATE_ORDER = ["watch", "wait", "stale", "done", "drop", "new"]
FILTERS = [
    ("all", "📚", "Hammasi", None),
    ("watch", "▶️", "Ko'rilmoqda", {"watch", "wait"}),
    ("stale", "⏸", "Chala", {"stale"}),
    ("done", "✅", "Tugagan", {"done"}),
    ("drop", "❌", "Tashlagan", {"drop"}),
    ("new", "🆕", "Yangi", {"new"}),
]
PAGE = 8


def bar(done, total, n=10):
    if not total:
        return f"{done} ep"
    f = min(n, round(n * done / total))
    return "▰" * f + "▱" * (n - f) + f" {min(100, round(100 * done / total))}%"


def show_state(s):
    c, total, aired, last = s["c"], s["total"], s["aired"], s["last"]
    if s["state"] == "drop":
        return "drop"
    if not c:
        return "new"
    if total and c >= total:
        return "done"
    if aired and c >= aired and s["status"] != "Ended":
        return "wait"
    if last and time.time() - last > STALE_DAYS * 86400:
        return "stale"
    return "watch"


def my_shows(uid):
    rows = q(
        "SELECT s.*, (SELECT COUNT(*) FROM watched w WHERE w.show_id=s.id) c, "
        "(SELECT AVG(score) FROM watched w WHERE w.show_id=s.id) a, "
        "(SELECT MAX(ts) FROM watched w WHERE w.show_id=s.id) last "
        "FROM shows s WHERE s.user_id=?",
        (uid,),
    )
    out = []
    for r_ in rows:
        d = dict(r_)
        d["st"] = show_state(d)
        out.append(d)
    out.sort(key=lambda d: (d["last"] or 0, d["id"]), reverse=True)
    return out


def save_totals(sid, eps):
    today = now().date().isoformat()
    aired = sum(1 for e in eps if e.get("airdate") and e["airdate"] <= today)
    q("UPDATE shows SET total=?, aired=?, total_ts=? WHERE id=?", (len(eps), aired, int(time.time()), sid))


async def sync_totals(uid):
    """Seriallardagi epizodlar sonini yangilab turadi (kuniga bir marta)."""
    old = int(time.time()) - 86400
    rows = q("SELECT id, tvmaze_id FROM shows WHERE user_id=? AND (total_ts IS NULL OR total_ts<?) LIMIT 12", (uid, old))
    if not rows:
        return
    sem = asyncio.Semaphore(4)

    async def one(row):
        async with sem:
            eps = await episodes(row["tvmaze_id"])
        if eps:
            save_totals(row["id"], eps)

    await asyncio.gather(*(one(x) for x in rows))


def ep_ranges(items):
    """[(1,1),(1,2),(1,3),(2,1)] -> 'S1E1–3 · S2E1'"""
    out = []
    for se in sorted({a for a, _ in items}):
        nums = sorted(n for a, n in items if a == se)
        parts, i = [], 0
        while i < len(nums):
            j = i
            while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
                j += 1
            parts.append(str(nums[i]) if i == j else f"{nums[i]}–{nums[j]}")
            i = j + 1
        out.append(f"S{se}E" + ",".join(parts))
    return " · ".join(out)


async def view_my(uid, flt="all", page=0):
    await sync_totals(uid)
    shows = my_shows(uid)
    if not shows:
        return "📺 Hali serial yo'q.\nSerial nomini yozing — qo'shib beraman.", back_kb()
    cnt = {k: 0 for k in STATE_INFO}
    for s_ in shows:
        cnt[s_["st"]] += 1
    codes = [f[0] for f in FILTERS]
    if flt not in codes:
        flt = "all"
    allowed = next(f[3] for f in FILTERS if f[0] == flt)
    items = [s_ for s_ in shows if allowed is None or s_["st"] in allowed]
    if flt == "all":
        items.sort(key=lambda d: STATE_ORDER.index(d["st"]))

    chips = []
    for code, em, name, sts in FILTERS:
        n = len(shows) if sts is None else sum(cnt[k] for k in sts)
        if sts is not None and not n and code != flt:
            continue
        mark = "• " if code == flt else ""
        chips.append(B(text=f"{mark}{em} {name} {n}", callback_data=f"my:{code}:0"))
    kb = [chips[i : i + 3] for i in range(0, len(chips), 3)]

    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    for s_ in items[page * PAGE : (page + 1) * PAGE]:
        em = STATE_INFO[s_["st"]][0]
        prog = f"{s_['c']}/{s_['total']}" if s_["total"] else f"{s_['c']} ep"
        kb.append([B(text=f"{em} {s_['name'][:36]} · {prog}", callback_data=f"sh:{s_['id']}")])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(B(text="◀️ Oldingi", callback_data=f"my:{flt}:{page - 1}"))
        if page < pages - 1:
            nav.append(B(text="Keyingi ▶️", callback_data=f"my:{flt}:{page + 1}"))
        kb.append(nav)
    kb.append([B(text="📊 Statistika", callback_data="st"), B(text="🏠 Menyu", callback_data="menu")])

    fname = next(f[2] for f in FILTERS if f[0] == flt)
    lines = [f"📺 <b>Seriallarim</b> · {len(shows)} ta", f"Bo'lim: <b>{fname}</b>" + (f" · sahifa {page + 1}/{pages}" if pages > 1 else "")]
    if not items:
        lines.append("\nBu bo'limda hozircha serial yo'q.")
    else:
        lines.append("\nSerialni bosing — ochiladi 👇")
    if flt in ("all", "stale") and cnt["stale"]:
        lines.append(f"\n<i>⏸ Chala — {STALE_DAYS} kundan beri ko'rilmagan seriallar.</i>")
    return "\n".join(lines), KB(inline_keyboard=kb)


async def view_stats(uid):
    kb = KB(
        inline_keyboard=[
            [B(text="📺 Seriallarim", callback_data="my"), B(text="🕘 Tarix", callback_data="hi")],
            [B(text="🏠 Menyu", callback_data="menu")],
        ]
    )
    rows = q(
        "SELECT w.*, s.name FROM watched w JOIN shows s ON s.id=w.show_id WHERE w.user_id=? ORDER BY w.ts",
        (uid,),
    )
    if not rows:
        return "📊 <b>Statistika</b>\n\nHali birorta epizod baholanmagan.\nSerial qo'shing va birinchi epizodni baholang 🎬", back_kb()
    await sync_totals(uid)
    shows = my_shows(uid)
    cnt = {k: 0 for k in STATE_INFO}
    for s_ in shows:
        cnt[s_["st"]] += 1

    today = now().date()
    by = {}
    for r_ in rows:
        if not r_["past"]:
            by.setdefault(datetime.fromtimestamp(r_["ts"], TZ).date(), []).append(r_)
    known = sorted(r_["runtime"] for r_ in rows if r_["runtime"])
    fb = known[len(known) // 2] if known else 40
    mins = sum(r_["runtime"] or fb for r_ in rows)
    week = sum(len(by.get(today - timedelta(days=i), [])) for i in range(7))
    month = sum(len(v) for d, v in by.items() if (d.year, d.month) == (today.year, today.month))
    d_ = today if today in by else today - timedelta(days=1)
    streak = 0
    while d_ in by:
        streak += 1
        d_ -= timedelta(days=1)

    lines = ["📊 <b>Statistika</b>\n", f"📺 <b>Seriallar: {len(shows)} ta</b>"]
    for k in ("done", "watch", "wait", "stale", "drop", "new"):
        if cnt[k]:
            em, name = STATE_INFO[k]
            lines.append(f"{em} {name}: <b>{cnt[k]}</b>")

    lines += [
        "\n🎞 <b>Epizodlar</b>",
        f"Jami: <b>{len(rows)}</b> · ⏱ {fmt_dur(mins)}",
        f"Bugun: <b>{len(by.get(today, []))}</b> · Hafta: <b>{week}</b> · Oy: <b>{month}</b>",
    ]
    if streak >= 2:
        lines.append(f"🔥 Ketma-ket: <b>{streak}</b> kun")

    lines += [
        "\n🗓 <b>Oxirgi 7 kun</b>",
    ]
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    mx = max([len(by.get(d, [])) for d in days] + [1])
    for d in days:
        n = len(by.get(d, []))
        w = max(1, round(10 * n / mx)) if n else 0
        lines.append(f"<code>{WEEKDAYS[d.weekday()]} {d:%d.%m}</code> {'█' * w or '·'} {n}")

    cand = [s_ for s_ in shows if s_["c"] >= 3] or [s_ for s_ in shows if s_["c"]]
    if len(cand) >= 2:
        lines.append("\n🏆 <b>Eng yaxshi seriallar</b>")
        for i, s_ in enumerate(sorted(cand, key=lambda x: (-x["a"], -x["c"]))[:3], 1):
            lines.append(f"{i}. {escape(s_['name'])} — {s_['a']:.1f} ({s_['c']} ep)")

    if today in by:
        grp = {}
        for r_ in by[today]:
            grp.setdefault(r_["name"], []).append(r_)
        lines.append("\n📌 <b>Bugun ko'rilganlar</b>")
        for name, lst in grp.items():
            a = sum(x["score"] for x in lst) / len(lst)
            lines.append(f"• {escape(name)} {ep_ranges([(x['season'], x['number']) for x in lst])} · ⭐{a:.1f}")
    return "\n".join(lines), kb


def view_hist(uid):
    rows = q(
        "SELECT w.*, s.name FROM watched w JOIN shows s ON s.id=w.show_id "
        "WHERE w.user_id=? AND w.past=0 ORDER BY w.ts DESC LIMIT 20",
        (uid,),
    )
    lines = ["🕘 <b>Oxirgi 20 ta epizod</b>\n"]
    for r in rows:
        t = datetime.fromtimestamp(r["ts"], TZ)
        lines.append(f"<code>{t:%d.%m %H:%M}</code> {escape(r['name'])} S{r['season']}E{r['number']} — {r['score']}/10")
    kb = KB(inline_keyboard=[[B(text="◀️ Statistika", callback_data="st")], [B(text="🏠 Menyu", callback_data="menu")]])
    return "\n".join(lines), kb


def view_chats(uid):
    u = user(uid)
    chats = q("SELECT * FROM chats WHERE owner_id=?", (uid,))
    lines = ["📢 <b>Kanal / guruhlar</b>\n"]
    kb = []
    if chats:
        for c in chats:
            mark = "🟢" if c["chat_id"] == u["active_chat"] else "⚪️"
            lines.append(f"{mark} {escape(c['title'] or '')}")
            kb.append([B(text=f"{mark} {c['title']}", callback_data=f"ac:{c['chat_id']}")])
        lines.append("\n🟢 — baholar shu yerga avtomatik yoziladi. Almashtirish uchun bosing.")
    else:
        lines.append("Hali hech narsa ulanmagan. Pastdagi tugma orqali qo'shing — bot o'zi admin bo'ladi.")
    kb.append(
        [
            B(text="➕ Kanalga qo'shish",
              url=f"https://t.me/{BOT_USERNAME}?startchannel=true&admin=post_messages+edit_messages+delete_messages"),
            B(text="➕ Guruhga qo'shish", url=f"https://t.me/{BOT_USERNAME}?startgroup=true"),
        ]
    )
    kb.append([B(text="◀️ Sozlamalar", callback_data="cfg"), B(text="🏠 Menyu", callback_data="menu")])
    return "\n".join(lines), KB(inline_keyboard=kb)


HELP = (
    "❓ <b>Qanday ishlaydi?</b>\n\n"
    "1️⃣ «Kanal / guruh» → botni kanalingizga qo'shing (admin avtomatik beriladi).\n"
    "2️⃣ Serial nomini yozing → tanlang. Kartasi kanalga tushadi va pin qilinadi 📌\n"
    "3️⃣ «Keyingi epizod» → 1–10 baho. Kanalga rasm bilan yoziladi. Bahoni o'zgartirsangiz, eski xabar yangilanadi.\n"
    "4️⃣ «Seriallarim» — seriallar holat bo'yicha: ▶️ ko'rilmoqda, ⏸ chala, ✅ tugagan, ❌ tashlab ketilgan, 🆕 yangi.\n   «Statistika» — jami vaqt, baholar, oxirgi 7 kun, eng yaxshi seriallar.\n"
    "5️⃣ «Tavsiyalar» — ko'rganlaringiz bo'yicha sizni tahlil qilib, yangi seriallar tavsiya qiladi.\n"
    "6️⃣ «Sozlamalar» — kanalga yozish, rasm va pin'ni yoqish/o'chirish.\n"
    "7️⃣ «📤 Do'stga ulashish» — serial rasm va «👀 Ko'rish» tugmasi bilan do'stingizga boradi. Istalgan chatda @TvShowUzBot yozib ham serial yuborsa bo'ladi.\n\n"
    "💡 Do'stingiz ham botni o'z kanaliga ulasa, bir-biringizning qaysi epizodda ekaningizni ko'rib turasiz."
)


SETTING_KEYS = {
    "post_on": "Kanalga yuborish",
    "use_photo": "Rasm bilan yuborish",
    "auto_pin": "Yangi serialni pin qilish",
}


def view_settings(uid):
    u = user(uid)
    c = active_chat(uid)
    ch = f"<b>{escape(c['title'] or '')}</b>" if c else "ulanmagan"
    text = f"⚙️ <b>Sozlamalar</b>\n\n📢 Kanal: {ch}\n\n✅ — yoqilgan, ❌ — o'chirilgan. Bosib almashtiring."
    kb = [[B(text=f"{'✅' if u[k] else '❌'} {label}", callback_data=f"tg:{k}")] for k, label in SETTING_KEYS.items()]
    kb.append([B(text="📢 Kanal / guruh", callback_data="ch")])
    kb.append([B(text="🧪 Test xabar", callback_data="tp"), B(text="🏠 Menyu", callback_data="menu")])
    return text, KB(inline_keyboard=kb)


def part_of_day(h):
    if 5 <= h < 11:
        return "ertalab"
    if 11 <= h < 17:
        return "kunduzi"
    if 17 <= h < 22:
        return "kechqurun"
    return "tunda"


def profile(uid):
    shows = [
        s
        for s in q(
            "SELECT s.*, (SELECT COUNT(*) FROM watched w WHERE w.show_id=s.id) c, "
            "(SELECT AVG(score) FROM watched w WHERE w.show_id=s.id) a FROM shows s WHERE s.user_id=?",
            (uid,),
        )
        if s["c"]
    ]
    rows = q("SELECT ts FROM watched WHERE user_id=? AND past=0", (uid,))
    if len(rows) < 3 or not shows:
        return None
    gw = {}
    for s in shows:
        w = (s["c"] ** 0.5) * max(s["a"] - 4, 0) / 6
        for g in (s["genres"] or "").split(","):
            g = g.strip()
            if g:
                gw[g] = gw.get(g, 0) + w
    rated = [s for s in shows if s["rating"]]
    delta = sum(s["a"] - s["rating"] for s in rated) / len(rated) if rated else 0
    big = [s for s in shows if s["c"] >= 3] or shows
    fav = max(big, key=lambda s: (s["a"], s["c"]))
    hours, days = {}, {}
    for r_ in rows:
        dt = datetime.fromtimestamp(r_["ts"], TZ)
        hours[dt.hour] = hours.get(dt.hour, 0) + 1
        days[dt.date()] = days.get(dt.date(), 0) + 1
    return {
        "shows": len(shows), "eps": len(rows), "gw": gw, "delta": delta, "fav": fav,
        "peak": max(hours, key=hours.get), "per_day": len(rows) / len(days), "max_day": max(days.values()),
    }


def profile_text(p):
    top = [uz_genres(g) for g, _ in sorted(p["gw"].items(), key=lambda x: -x[1])[:3]]
    d = p["delta"]
    st = next(n for lim, n in STYLES if d >= lim)
    if abs(d) < 0.4:
        style = f"{st} — baholaringiz umumiy reytingga yaqin"
    else:
        style = f"{st} — odatdagi reytingdan ~{abs(d):.1f} ball {'yuqori' if d > 0 else 'past'} berasiz"
    return (
        "🧠 <b>Siz haqingizda</b>\n"
        f"📺 {p['shows']} ta serial, {p['eps']} ta epizod\n"
        f"🎭 Yoqadigan janrlar: <b>{', '.join(top)}</b>\n"
        f"⭐ Eng yoqqani: <b>{escape(p['fav']['name'])}</b> (o'rtacha {p['fav']['a']:.1f})\n"
        f"📏 Baholash uslubi: {style}\n"
        f"🕘 Ko'proq {part_of_day(p['peak'])}, soat {p['peak']}:00 atrofida\n"
        f"🔥 Kuniga o'rtacha {p['per_day']:.1f}, eng ko'pi {p['max_day']} epizod"
    )


def view_reco(uid, page=0):
    p = profile(uid)
    if not p:
        return "🎯 Tavsiya berish uchun avval kamida 3 ta epizod baholang.", back_kb()
    head = profile_text(p)
    cat = q("SELECT * FROM catalog WHERE rating>=?", (CATALOG_MIN,))
    if not cat:
        return head + "\n\n⏳ Tavsiyalar bazasi tayyorlanmoqda. Bir necha daqiqadan keyin qayta oching.", back_kb()
    mine = {r["tvmaze_id"] for r in q("SELECT tvmaze_id FROM shows WHERE user_id=?", (uid,))}
    top = dict(sorted(p["gw"].items(), key=lambda x: -x[1])[:6])
    tot = sum(top.values()) or 1
    scored = []
    for c in cat:
        if c["tvmaze_id"] in mine:
            continue
        gl = [g.strip() for g in (c["genres"] or "").split(",") if g.strip()]
        match = min(sum(top.get(g, 0) for g in gl) / tot, 1)
        scored.append((0.65 * match + 0.35 * min(max((c["rating"] - 7) / 3, 0), 1), c))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return head + "\n\n🎉 Bazadagi barcha seriallarni ko'rib bo'libsiz!", back_kb()
    per = 5
    pages = (len(scored) + per - 1) // per
    page %= pages
    chunk = scored[page * per : (page + 1) * per]
    lines = [head, "\n🎯 <b>Sizga yoqishi mumkin:</b>"]
    kb = []
    for i, (_, c) in enumerate(chunk, 1):
        year = (c["premiered"] or "")[:4]
        lines.append(f"\n{i}. <b>{escape(c['name'])}</b>" + (f" ({year})" if year else "") + f" ⭐ {c['rating']}")
        lines.append("   " + escape(uz_genres(c["genres"])))
        kb.append([B(text=f"➕ {c['name']}"[:60], callback_data=f"sr:{c['tvmaze_id']}")])
    kb.append([B(text="🔄 Yana", callback_data=f"rc:{page + 1}"), B(text="🏠 Menyu", callback_data="menu")])
    return "\n".join(lines), KB(inline_keyboard=kb)


SEEDS = [
    ("Breaking Bad", 2008), ("Better Call Saul", 2015), ("The Wire", 2002), ("The Sopranos", 1999),
    ("Game of Thrones", 2011), ("Chernobyl", 2019), ("Band of Brothers", 2001), ("Sherlock", 2010),
    ("True Detective", 2014), ("Peaky Blinders", 2013), ("Narcos", 2015), ("Ozark", 2017),
    ("Mindhunter", 2017), ("Succession", 2018), ("Dark", 2017), ("Stranger Things", 2016),
    ("Black Mirror", 2011), ("House of Cards", 2013), ("The Last of Us", 2023), ("Severance", 2022),
    ("Rick and Morty", 2013), ("BoJack Horseman", 2014), ("Arcane", 2021), ("Attack on Titan", 2013),
    ("Death Note", 2006), ("Fullmetal Alchemist: Brotherhood", 2009), ("Hannibal", 2013), ("Mr. Robot", 2015),
    ("Westworld", 2016), ("The Boys", 2019), ("House", 2004), ("Lost", 2004), ("Prison Break", 2005),
    ("Vikings", 2013), ("The Office", 2005), ("Friends", 1994), ("Seinfeld", 1989), ("Dexter", 2006),
    ("Fargo", 2014), ("Money Heist", 2017), ("Squid Game", 2021), ("The Queen's Gambit", 2020),
    ("The Crown", 2016), ("Boardwalk Empire", 2010), ("Deadwood", 2004), ("Rome", 2005), ("The Shield", 2002),
    ("Oz", 1997), ("Six Feet Under", 2001), ("Mad Men", 2007), ("The Leftovers", 2014), ("Fringe", 2008),
    ("Person of Interest", 2011), ("Silicon Valley", 2014), ("Curb Your Enthusiasm", 2000),
    ("Parks and Recreation", 2009), ("Community", 2009), ("It's Always Sunny in Philadelphia", 2005),
    ("How I Met Your Mother", 2005), ("The Big Bang Theory", 2007), ("Brooklyn Nine-Nine", 2013),
    ("Ted Lasso", 2020), ("The Bear", 2022), ("Shogun", 2024), ("Fallout", 2024), ("Slow Horses", 2022),
    ("Reacher", 2022), ("Yellowstone", 2018), ("Justified", 2010), ("Sons of Anarchy", 2008),
    ("Daredevil", 2015), ("The Mandalorian", 2019), ("Andor", 2022), ("The Expanse", 2015),
    ("Battlestar Galactica", 2004), ("Firefly", 2002), ("Doctor Who", 2005), ("Downton Abbey", 2010),
    ("Line of Duty", 2012), ("Broadchurch", 2013), ("Luther", 2010), ("Happy Valley", 2014),
    ("Sharp Objects", 2018), ("Big Little Lies", 2017), ("The White Lotus", 2021), ("Euphoria", 2019),
    ("Yellowjackets", 2021), ("The Handmaid's Tale", 2017), ("Homeland", 2011), ("24", 2001),
    ("The Americans", 2013), ("Rectify", 2013), ("Atlanta", 2016), ("Barry", 2018), ("Fleabag", 2016),
    ("Normal People", 2020), ("Gomorrah", 2014), ("Cowboy Bebop", 1998), ("One Punch Man", 2015),
    ("Demon Slayer: Kimetsu no Yaiba", 2019), ("Hunter x Hunter", 2011), ("Steins;Gate", 2011),
    ("Code Geass: Lelouch of the Rebellion", 2006), ("Naruto", 2002), ("One Piece", 1999),
    ("Neon Genesis Evangelion", 1995), ("Vinland Saga", 2019), ("Jujutsu Kaisen", 2020),
    ("Avatar: The Last Airbender", 2005), ("The Simpsons", 1989), ("South Park", 1997), ("Suits", 2011),
    ("House of the Dragon", 2022), ("The Witcher", 2019), ("Loki", 2021), ("WandaVision", 2021),
    ("The Night Of", 2016), ("Mare of Easttown", 2021), ("Dopesick", 2021), ("The Pacific", 2010),
    ("Black Sails", 2014), ("Spartacus", 2010), ("Lupin", 2021), ("Sense8", 2015), ("Orphan Black", 2013),
    ("Misfits", 2009), ("Skins", 2007), ("Taboo", 2017), ("Generation Kill", 2008),
]

SEEDS += [
    ("Mindhunter", 2017), ("True Detective", 2014), ("Fargo", 2014), ("Narcos", 2015), ("Ozark", 2017),
    ("Peaky Blinders", 2013), ("Mr. Robot", 2015), ("Dark", 2017), ("Succession", 2018), ("The Last of Us", 2023),
    ("Severance", 2022), ("The Bear", 2022), ("Shogun", 2024), ("Andor", 2022), ("Arcane", 2021),
    ("Rick and Morty", 2013), ("BoJack Horseman", 2014), ("Stranger Things", 2016), ("Black Mirror", 2011),
    ("House", 2004), ("Dexter", 2006), ("Prison Break", 2005), ("Lost", 2004), ("Vikings", 2013), ("Westworld", 2016),
    ("Ted Lasso", 2020), ("Only Murders in the Building", 2021), ("Slow Horses", 2022), ("Reservation Dogs", 2021),
    ("What We Do in the Shadows", 2019), ("Derry Girls", 2018), ("The Office", 2005), ("Parks and Recreation", 2009),
    ("Community", 2009), ("Arrested Development", 2003), ("It's Always Sunny in Philadelphia", 2005),
    ("Curb Your Enthusiasm", 2000), ("Seinfeld", 1989), ("Friends", 1994), ("How I Met Your Mother", 2005),
    ("Brooklyn Nine-Nine", 2013), ("Schitt's Creek", 2015), ("Veep", 2012), ("Silicon Valley", 2014),
    ("Mad Men", 2007), ("Boardwalk Empire", 2010), ("Deadwood", 2004), ("Six Feet Under", 2001), ("The Shield", 2002),
    ("Oz", 1997), ("Justified", 2010), ("Sons of Anarchy", 2008), ("Hannibal", 2013), ("The Leftovers", 2014),
    ("Halt and Catch Fire", 2014), ("Killing Eve", 2018), ("Bodyguard", 2018), ("The Crown", 2016),
    ("Downton Abbey", 2010), ("The Expanse", 2015), ("Battlestar Galactica", 2004), ("Firefly", 2002),
    ("The Mandalorian", 2019), ("Foundation", 2021), ("Silo", 2023), ("Fallout", 2024), ("The Boys", 2019),
    ("Invincible", 2021), ("Peacemaker", 2022), ("Daredevil", 2015), ("Supernatural", 2005),
    ("Buffy the Vampire Slayer", 1997), ("The X-Files", 1993), ("Twin Peaks", 1990), ("Fringe", 2008),
    ("Person of Interest", 2011), ("Elementary", 2012), ("Doctor Who", 2005), ("Utopia", 2013), ("Black Books", 2000),
    ("Fawlty Towers", 1975), ("Blackadder", 1983), ("Planet Earth", 2006), ("Blue Planet II", 2017),
    ("Cosmos: A Spacetime Odyssey", 2014), ("The Last Dance", 2020), ("Making a Murderer", 2015),
    ("Squid Game", 2021), ("Money Heist", 2017), ("Narcos: Mexico", 2018), ("Kingdom", 2019),
    ("Alice in Borderland", 2020), ("Crash Landing on You", 2019), ("All of Us Are Dead", 2022),
    ("Vincenzo", 2021), ("My Mister", 2018), ("Goblin", 2016), ("Reply 1988", 2015), ("Itaewon Class", 2020),
    ("Sky Castle", 2018), ("Hospital Playlist", 2020), ("Extraordinary Attorney Woo", 2022), ("The Glory", 2022),
    ("Moving", 2023), ("Death Note", 2006), ("Attack on Titan", 2013), ("Fullmetal Alchemist: Brotherhood", 2009),
    ("My Hero Academia", 2016), ("Bleach", 2004), ("Dragon Ball Z", 1989), ("Tokyo Ghoul", 2014),
    ("Haikyu!!", 2014), ("Mob Psycho 100", 2016), ("Cyberpunk: Edgerunners", 2022), ("Chainsaw Man", 2022),
    ("Spy x Family", 2022), ("Frieren: Beyond Journey's End", 2023), ("Dr. Stone", 2019), ("Re:ZERO", 2016),
    ("Black Clover", 2017), ("Violet Evergarden", 2018), ("Your Lie in April", 2014), ("Clannad", 2007),
    ("Monster", 2004), ("Samurai Champloo", 2004), ("Trigun", 1998), ("Gintama", 2006), ("Berserk", 1997),
    ("Psycho-Pass", 2012), ("Parasyte: The Maxim", 2014), ("The Promised Neverland", 2019), ("Erased", 2016),
    ("Toradora!", 2008), ("Adventure Time", 2010), ("Gravity Falls", 2012), ("Futurama", 1999),
    ("Family Guy", 1999), ("Archer", 2009), ("Bob's Burgers", 2011), ("The Legend of Korra", 2012),
    ("Over the Garden Wall", 2014), ("Love, Death & Robots", 2019), ("Yellowstone", 2018), ("Reacher", 2022),
    ("Bosch", 2014), ("Lucifer", 2016), ("Shameless", 2011), ("Californication", 2007), ("Billions", 2016),
    ("Industry", 2020), ("Mythic Quest", 2020), ("Dead to Me", 2019), ("Sex Education", 2019),
    ("Heartstopper", 2022), ("The Umbrella Academy", 2019), ("Shadow and Bone", 2021), ("Wednesday", 2022),
    ("Bridgerton", 2020), ("Outlander", 2014), ("The Last Kingdom", 2015), ("Rome", 2005), ("Merlin", 2008),
    ("The Terror", 2018), ("Station Eleven", 2021), ("Hacks", 2021), ("Abbott Elementary", 2021),
    ("Shrinking", 2023), ("Baby Reindeer", 2024), ("Adolescence", 2025), ("The Penguin", 2024),
    ("Pluribus", 2025), ("Alien: Earth", 2025), ("Landman", 2024), ("Tulsa King", 2022),
]
SEEDS = list(dict.fromkeys(SEEDS))


async def add_to_catalog(sh):
    """IMDb reytingi bo'yicha katalogga qo'shadi. Reyting olinmasa False qaytaradi."""
    rt = await imdb_show_rating(sh["id"], (sh.get("externals") or {}).get("imdb"))
    if rt is None:
        return False
    if rt >= CATALOG_MIN:
        q(
            "INSERT OR REPLACE INTO catalog(tvmaze_id, name, genres, rating, premiered, status) VALUES(?,?,?,?,?,?)",
            (sh["id"], sh["name"], ", ".join(sh.get("genres") or []), rt, sh.get("premiered"), sh.get("status")),
        )
    return True


async def build_catalog():
    done = {r["name"] for r in q("SELECT name FROM seeds_imdb")}
    for name, year in SEEDS:
        if name in done:
            continue
        data = await api("/search/shows?q=" + quote(name))
        if data is None:
            await asyncio.sleep(5)
            continue
        ok = True
        for item in data:
            sh = item["show"]
            y = (sh.get("premiered") or "")[:4]
            if y.isdigit() and abs(int(y) - year) <= 1:
                ok = await add_to_catalog(sh)
                break
        if not ok:
            await asyncio.sleep(2)
            continue
        q("INSERT OR IGNORE INTO seeds_imdb(name) VALUES(?)", (name,))
        await asyncio.sleep(0.7)
    log.info("Tavsiyalar bazasi: %s ta serial", q("SELECT COUNT(*) c FROM catalog", one=True)["c"])


def share_button(tvid, *_args, **_kw):
    """Do'st/guruhni tanlash oynasini ochadi; serial rasm va «Ko'rish» tugmasi bilan yuboriladi (inline rejim)."""
    return B(text="📤 Do'stga ulashish", switch_inline_query=f"show_{tvid}")


def show_kb(sid):
    s = show_row(sid)
    dropped = s["state"] == "drop"
    toggle = B(text="Davom ettirish" if dropped else "Serialni to'xtatish", callback_data=f"dr:{sid}")
    return KB(
        inline_keyboard=[
            [B(text="▶️ Keyingi epizodni baholash", callback_data=f"n:{sid}")],
            [B(text="📚 Epizod tanlash", callback_data=f"ss:{sid}"), B(text="📢 Kanalga", callback_data=f"pc:{sid}")],
            [share_button(s["tvmaze_id"])],
            [toggle, B(text="🗑 O'chirish", callback_data=f"dl:{sid}")],
            [B(text="📺 Seriallarim", callback_data="my"), B(text="🏠 Menyu", callback_data="menu")],
        ]
    )


def base_info(s):
    year = (s["premiered"] or "")[:4]
    out = [f"<b>{escape(s['name'])}</b>" + (f" ({year})" if year else "")]
    meta = []
    if s["genres"]:
        meta.append("🎭 " + escape(uz_genres(s["genres"])))
    if s["status"]:
        meta.append("📡 " + escape(UZ_STATUS.get(s["status"], s["status"])))
    if meta:
        out.append(" · ".join(meta))
    if s["rating"]:
        out.append(f"⭐ {s['rating']}/10")
    return out


async def card_text(s):
    eps = await episodes(s["tvmaze_id"])
    if eps:
        save_totals(s["id"], eps)
        s = show_row(s["id"])
    w = q("SELECT COUNT(*) c, AVG(score) a, MAX(ts) last, SUM(runtime) m FROM watched WHERE show_id=?", (s["id"],), one=True)
    d = dict(s)
    d.update(c=w["c"], last=w["last"])
    st = show_state(d)
    em, label = STATE_INFO[st]
    out = base_info(s)
    if s["summary"]:
        out.append("\n" + escape(short(s["summary"], 260)))
    out.append(f"\n{em} <b>{label}</b>")
    if eps:
        out.append(f"{bar(w['c'], len(eps))} · {w['c']}/{len(eps)} epizod")
    else:
        out.append(f"👀 Ko'rilgan: <b>{w['c']}</b> epizod")
    if w["c"]:
        line = f"⭐ O'rtacha baho: <b>{w['a']:.1f}</b>"
        if w["m"]:
            line += f" · ⏱ {fmt_dur(w['m'])}"
        out.append(line)
        last = datetime.fromtimestamp(w["last"], TZ).date()
        ago = (now().date() - last).days
        out.append(f"🕘 Oxirgi: {last:%d.%m}" + (" (bugun)" if ago == 0 else f" ({ago} kun oldin)"))
    if st in ("watch", "stale", "new"):
        nxt = await next_episode(s["id"])
        if nxt:
            out.append(f"▶️ Keyingi: <b>S{nxt['season']}E{nxt['number']}</b>")
    return "\n".join(out)


async def send_card(msg: Message, sid, replace=True):
    s = await ensure_uz(sid)
    text = await card_text(s)
    if replace:
        try:
            await msg.delete()
        except Exception:
            pass
    if s["poster"]:
        try:
            await msg.answer_photo(s["poster"], caption=text, reply_markup=show_kb(sid))
            return
        except Exception as e:
            log.warning("kartada rasm xato: %s", e)
    await msg.answer(text, reply_markup=show_kb(sid))


def rate_kb(sid, season, number, past=0):
    row1 = [B(text=str(i), callback_data=f"r:{sid}:{season}:{number}:{i}:{past}") for i in range(1, 6)]
    row2 = [B(text=str(i), callback_data=f"r:{sid}:{season}:{number}:{i}:{past}") for i in range(6, 11)]
    if past:
        tgl = B(text="Hozir ko'ryapman", callback_data=f"e:{sid}:{season}:{number}:0")
    else:
        tgl = B(text="Oldin ko'rganman", callback_data=f"e:{sid}:{season}:{number}:1")
    return KB(inline_keyboard=[row1, row2, [tgl], [B(text="Orqaga", callback_data=f"sh:{sid}")]])


async def rate_screen(cb: CallbackQuery, sid, season, number, past=0):
    s = show_row(sid)
    eps = await episodes(s["tvmaze_id"])
    ep = next((e for e in eps if e["season"] == season and e["number"] == number), None)
    old = q("SELECT score FROM watched WHERE show_id=? AND season=? AND number=?", (sid, season, number), one=True)
    text = f"<b>{escape(s['name'])}</b>\nS{season}E{number}"
    if ep:
        text += f" - {escape(ep['name'] or '')}"
    if old:
        text += f"\nOldingi baho: {old['score']}/10"
    if past:
        text += "\n\nOldin ko'rgansiz. Baho saqlanadi, lekin kanalga yuborilmaydi."
    text += "\n\nBahoni tanlang (1-10):"
    await screen(cb, text, rate_kb(sid, season, number, past))


async def next_episode(sid):
    s = show_row(sid)
    eps = await episodes(s["tvmaze_id"])
    done = {(r["season"], r["number"]) for r in q("SELECT season, number FROM watched WHERE show_id=?", (sid,))}
    if not eps:
        return None
    if not done:
        return eps[0]
    last = max(done)
    return next((e for e in eps if (e["season"], e["number"]) > last), None)


r = Router()
r.message.filter(F.chat.type == "private")


@r.message(CommandStart(deep_link=True))
async def cmd_start_link(m: Message, command: CommandObject):
    uid = m.from_user.id
    user(uid)
    arg = command.args or ""
    if arg.startswith("show_") and arg[5:].isdigit():
        return await send_preview(m, uid, int(arg[5:]), "add", replace=False)
    text, kb = view_menu(uid)
    await m.answer(text, reply_markup=kb)


@r.message(CommandStart())
@r.message(Command("menu"))
async def cmd_menu(m: Message):
    user(m.from_user.id)
    text, kb = view_menu(m.from_user.id)
    await m.answer(text, reply_markup=kb)


@r.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(HELP, reply_markup=back_kb())


@r.message(Command("my"))
async def cmd_my(m: Message):
    text, kb = await view_my(m.from_user.id)
    await m.answer(text, reply_markup=kb)


@r.message(Command("stats"))
async def cmd_stats(m: Message):
    text, kb = await view_stats(m.from_user.id)
    await m.answer(text, reply_markup=kb)


@r.message(F.text & ~F.text.startswith("/"))
async def search(m: Message):
    query = m.text.strip()[:80]
    data = await api("/search/shows?q=" + quote(query))
    if data is None:
        return await m.answer("⚠️ Qidiruv hozir ishlamayapti, birozdan so'ng urinib ko'ring.")
    if not data:
        return await m.answer("😕 Topilmadi. Nomini inglizcha, to'liqroq yozib ko'ring.", reply_markup=back_kb())
    kb = []
    top = data[:6]
    imdb_rts = await asyncio.gather(
        *[imdb_show_rating(i["show"]["id"], (i["show"].get("externals") or {}).get("imdb")) for i in top]
    )
    for item, rt in zip(top, imdb_rts):
        sh = item["show"]
        year = (sh.get("premiered") or "")[:4]
        label = f"{sh['name']}" + (f" ({year})" if year else "") + (f" ⭐{rt}" if rt else "")
        kb.append([B(text=label[:60], callback_data=f"s:{sh['id']}")])
    kb.append([B(text="🏠 Menyu", callback_data="menu")])
    await m.answer(f"🔎 «{escape(query)}» bo'yicha natijalar:", reply_markup=KB(inline_keyboard=kb))


@r.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery):
    await cb.answer()
    text, kb = view_menu(cb.from_user.id)
    await screen(cb, text, kb)


@r.callback_query(F.data == "add")
async def cb_add(cb: CallbackQuery):
    await cb.answer()
    await screen(cb, "🔎 Serial nomini yozib yuboring.\nMasalan: <i>Dexter</i>, <i>Fargo</i>, <i>Breaking Bad</i>", back_kb())


@r.callback_query(F.data == "help")
async def cb_help(cb: CallbackQuery):
    await cb.answer()
    await screen(cb, HELP, back_kb())


@r.callback_query(F.data.regexp(r"^my(:|$)"))
async def cb_my(cb: CallbackQuery):
    await cb.answer()
    parts = cb.data.split(":")
    flt = parts[1] if len(parts) > 1 else "all"
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    await screen(cb, *(await view_my(cb.from_user.id, flt, page)))


@r.callback_query(F.data == "st")
async def cb_st(cb: CallbackQuery):
    await cb.answer()
    await screen(cb, *(await view_stats(cb.from_user.id)))


@r.callback_query(F.data == "hi")
async def cb_hi(cb: CallbackQuery):
    await cb.answer()
    await screen(cb, *view_hist(cb.from_user.id))


@r.callback_query(F.data == "ch")
async def cb_ch(cb: CallbackQuery):
    await cb.answer()
    await screen(cb, *view_chats(cb.from_user.id))


@r.callback_query(F.data.startswith("ac:"))
async def cb_ac(cb: CallbackQuery):
    cid = int(cb.data.split(":")[1])
    if q("SELECT 1 FROM chats WHERE chat_id=? AND owner_id=?", (cid, cb.from_user.id), one=True):
        user(cb.from_user.id)
        q("UPDATE users SET active_chat=? WHERE user_id=?", (cid, cb.from_user.id))
    await cb.answer("✅ Tanlandi")
    await screen(cb, *view_chats(cb.from_user.id))


@r.callback_query(F.data == "cfg")
async def cb_cfg(cb: CallbackQuery):
    await cb.answer()
    await screen(cb, *view_settings(cb.from_user.id))


@r.callback_query(F.data.startswith("tg:"))
async def cb_tg(cb: CallbackQuery):
    key = cb.data.split(":")[1]
    uid = cb.from_user.id
    if key in SETTING_KEYS:
        u = user(uid)
        q(f"UPDATE users SET {key}=? WHERE user_id=?", (0 if u[key] else 1, uid))
    await cb.answer()
    await screen(cb, *view_settings(uid))


@r.callback_query(F.data == "reco")
async def cb_reco(cb: CallbackQuery):
    await cb.answer()
    await screen(cb, *view_reco(cb.from_user.id, 0))


@r.callback_query(F.data.startswith("rc:"))
async def cb_reco_page(cb: CallbackQuery):
    await cb.answer()
    await screen(cb, *view_reco(cb.from_user.id, int(cb.data.split(":")[1])))


@r.callback_query(F.data == "tp")
async def cb_tp(cb: CallbackQuery):
    res = await post(cb.bot, cb.from_user.id, "🧪 Test xabar — TvShowUz bot ishlayapti! ✅")
    await cb.answer(POST_STATUS[res], show_alert=True)


@r.my_chat_member()
async def on_member(ev: ChatMemberUpdated):
    chat = ev.chat
    if chat.type == "private":
        return
    st = ev.new_chat_member.status
    uid = ev.from_user.id
    if st in ("administrator", "member"):
        if chat.type == "channel" and st != "administrator":
            return
        user(uid)
        q(
            "INSERT OR REPLACE INTO chats(chat_id, title, kind, owner_id) VALUES(?,?,?,?)",
            (chat.id, chat.title or "", chat.type, uid),
        )
        q("UPDATE users SET active_chat=? WHERE user_id=?", (chat.id, uid))
        msg = f"✅ <b>{escape(chat.title or '')}</b> ulandi! Endi baholar shu yerga yoziladi."
        if chat.type == "channel" and not getattr(ev.new_chat_member, "can_post_messages", True):
            msg += "\n⚠️ Botga «Xabar yuborish» huquqini bering."
        if chat.type == "channel" and not getattr(ev.new_chat_member, "can_edit_messages", True):
            msg += "\n📌 Yangi serialni pin qilish uchun «Xabarlarni tahrirlash» huquqi ham kerak."
        try:
            await ev.bot.send_message(uid, msg, reply_markup=back_kb())
        except Exception:
            pass
    elif st in ("left", "kicked"):
        q("DELETE FROM chats WHERE chat_id=?", (chat.id,))
        q("UPDATE users SET active_chat=NULL WHERE active_chat=?", (chat.id,))


_tr_cache = {}


def rt_avg(data):
    return (data.get("rating") or {}).get("average")


async def preview_text(data):
    tvid = data["id"]
    year = (data.get("premiered") or "")[:4]
    out = [f"<b>{escape(data['name'])}</b>" + (f" ({year})" if year else "")]
    meta = []
    if data.get("genres"):
        meta.append("🎭 " + escape(uz_genres(", ".join(data["genres"]))))
    if data.get("status"):
        meta.append("📡 " + escape(UZ_STATUS.get(data["status"], data["status"])))
    if meta:
        out.append(" · ".join(meta))
    rt = await imdb_show_rating(tvid, (data.get("externals") or {}).get("imdb"))
    if rt:
        out.append(f"⭐ IMDb {rt}/10")
    eps = await episodes(tvid)
    if eps:
        line = f"🎞 {len({e['season'] for e in eps})} fasl · {len(eps)} epizod"
        if data.get("averageRuntime"):
            line += f" · ⏱ ~{data['averageRuntime']} daq"
        out.append(line)
    net = ((data.get("network") or data.get("webChannel")) or {}).get("name")
    if net:
        out.append(f"📺 {escape(net)}")
    summ = clean(data.get("summary"))
    if summ:
        tr = _tr_cache.get(tvid)
        if not tr:
            tr = await translate_uz(summ)
            if tr:
                _tr_cache[tvid] = tr
        out.append("\n" + escape(short(tr or summ, 550)))
    return "\n".join(out)


async def send_preview(msg: Message, uid, tvid, back, replace=True):
    """Serial haqida ma'lumot (hali qo'shilmagan). Pastda «Boshlash» tugmasi."""
    mine = q("SELECT id FROM shows WHERE user_id=? AND tvmaze_id=?", (uid, tvid), one=True)
    if mine:
        return await send_card(msg, mine["id"], replace)
    data = await api(f"/shows/{tvid}")
    if not data:
        return await msg.answer("⚠️ Serial ma'lumotini olib bo'lmadi, qayta urinib ko'ring.")
    text = await preview_text(data)
    back_btn = B(text="◀️ Tavsiyalar", callback_data="reco") if back == "reco" else B(text="🔎 Boshqa serial", callback_data="add")
    kb = KB(inline_keyboard=[
        [B(text="▶️ Boshlash", callback_data=f"go:{tvid}")],
        [share_button(tvid, data["name"], data.get("premiered"), rt_avg(data), ", ".join(data.get("genres") or []))],
        [back_btn, B(text="🏠 Menyu", callback_data="menu")],
    ])
    if replace:
        try:
            await msg.delete()
        except Exception:
            pass
    img = data.get("image") or {}
    poster = img.get("original") or img.get("medium")
    if poster:
        try:
            await msg.answer_photo(poster, caption=text, reply_markup=kb)
            return
        except Exception as e:
            log.warning("oldindan ko'rishda rasm xato: %s", e)
    await msg.answer(text, reply_markup=kb)


@r.callback_query(F.data.startswith("s:"))
@r.callback_query(F.data.startswith("sr:"))
async def cb_preview(cb: CallbackQuery):
    await cb.answer()
    kind, tvid = cb.data.split(":")
    await send_preview(cb.message, cb.from_user.id, int(tvid), "reco" if kind == "sr" else "add")


def inline_result(uid, tvid, name, premiered, rating, genres, poster, thumb=None, status=None, summary=None):
    year = (premiered or "")[:4]
    lines = [f"🎬 <b>{escape(name)}</b>" + (f" ({year})" if year else "")]
    meta = []
    if genres:
        meta.append("🎭 " + escape(uz_genres(genres)))
    if status:
        meta.append("📡 " + escape(UZ_STATUS.get(status, status)))
    if meta:
        lines.append(" · ".join(meta))
    if rating:
        lines.append(f"⭐ {rating}/10")
    mine = q(
        "SELECT COUNT(*) c, AVG(w.score) a FROM watched w JOIN shows s ON s.id=w.show_id "
        "WHERE s.user_id=? AND s.tvmaze_id=?",
        (uid, tvid),
        one=True,
    )
    if mine and mine["c"]:
        lines.append(f"\n👀 Men {mine['c']} ta epizod ko'rdim · baho: <b>{mine['a']:.1f}/10</b>")
    if summary:
        lines.append("\n" + escape(short(summary, 350)))
    caption = "\n".join(lines)
    kb = KB(inline_keyboard=[[B(text="👀 Ko'rish", url=f"https://t.me/{BOT_USERNAME}?start=show_{tvid}")]])
    desc = uz_genres(genres) + (f" · ⭐ {rating}" if rating else "")
    if poster:
        return InlineQueryResultPhoto(
            id=str(tvid), photo_url=poster, thumbnail_url=thumb or poster, title=name, description=desc,
            caption=caption, parse_mode="HTML", reply_markup=kb,
        )
    return InlineQueryResultArticle(
        id=str(tvid), title=name, description=desc,
        input_message_content=InputTextMessageContent(message_text=caption, parse_mode="HTML"),
        reply_markup=kb,
    )


@r.inline_query()
async def on_inline(iq: InlineQuery):
    """Istalgan chatda: @bot serial nomi  — rasm va «Ko'rish» tugmali chiroyli karta."""
    uid = iq.from_user.id
    text = (iq.query or "").strip()
    results = []
    try:
        if text.startswith("show_") and text[5:].isdigit():
            d = await api(f"/shows/{int(text[5:])}")
            if d:
                summ = clean(d.get("summary"))
                tr = _tr_cache.get(d["id"])
                if summ and not tr:
                    try:
                        tr = await asyncio.wait_for(translate_uz(summ), 4)
                        if tr:
                            _tr_cache[d["id"]] = tr
                    except Exception:
                        tr = None
                img = d.get("image") or {}
                results.append(inline_result(
                    uid, d["id"], d["name"], d.get("premiered"), rt_avg(d), ", ".join(d.get("genres") or []),
                    img.get("original") or img.get("medium"), img.get("medium"), d.get("status"), tr or summ,
                ))
        elif text:
            data = await api("/search/shows?q=" + quote(text[:60])) or []
            for item in data[:8]:
                d = item["show"]
                img = d.get("image") or {}
                results.append(inline_result(
                    uid, d["id"], d["name"], d.get("premiered"), rt_avg(d), ", ".join(d.get("genres") or []),
                    img.get("original") or img.get("medium"), img.get("medium"), d.get("status"), None,
                ))
        else:
            for s in q("SELECT * FROM shows WHERE user_id=? ORDER BY id DESC LIMIT 20", (uid,)):
                results.append(inline_result(
                    uid, s["tvmaze_id"], s["name"], s["premiered"], s["rating"], s["genres"], s["poster"],
                    None, s["status"], None,
                ))
    except Exception as e:
        log.warning("inline xato: %s", e)
    await iq.answer(results, cache_time=5, is_personal=True)


@r.callback_query(F.data.startswith("go:"))
async def cb_pick(cb: CallbackQuery):
    await cb.answer()
    tvid = int(cb.data.split(":")[1])
    uid = cb.from_user.id
    data = await api(f"/shows/{tvid}")
    if not data:
        return await cb.message.answer("⚠️ Serial ma'lumotini olib bo'lmadi, qayta urinib ko'ring.")
    user(uid)
    pin_ok = None
    existing = q("SELECT id FROM shows WHERE user_id=? AND tvmaze_id=?", (uid, tvid), one=True)
    if existing:
        sid = existing["id"]
    else:
        img = data.get("image") or {}
        imdb_rt = await imdb_show_rating(tvid, (data.get("externals") or {}).get("imdb"))
        q(
            "INSERT INTO shows(user_id, tvmaze_id, name, poster, rating, status, premiered, genres, summary) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                uid, tvid, data["name"], img.get("original") or img.get("medium"),
                imdb_rt, data.get("status"), data.get("premiered"),
                ", ".join(data.get("genres") or []), clean(data.get("summary")),
            ),
        )
        sid = q("SELECT id FROM shows WHERE user_id=? AND tvmaze_id=?", (uid, tvid), one=True)["id"]
        s = await ensure_uz(sid)
        caption = "🎬 <b>Yangi serial boshladim!</b>\n\n" + "\n".join(base_info(s))
        if s["summary"]:
            caption += "\n\n" + escape(short(s["summary"], 250))
        await add_to_catalog(data)
        _, pin_ok = await announce_show(cb.bot, uid, sid, caption)
    await send_card(cb.message, sid)
    if pin_ok is False:
        await cb.message.answer(PIN_NOTE)


@r.callback_query(F.data.startswith("sh:"))
async def cb_show(cb: CallbackQuery):
    await cb.answer()
    await send_card(cb.message, int(cb.data.split(":")[1]))


@r.callback_query(F.data.startswith("pc:"))
async def cb_post_card(cb: CallbackQuery):
    s = await ensure_uz(int(cb.data.split(":")[1]))
    caption = "📺 <b>Hozir ko'rayotganim</b>\n\n" + "\n".join(base_info(s))
    res = await post(cb.bot, cb.from_user.id, caption, s["poster"])
    await cb.answer(POST_STATUS[res], show_alert=True)


@r.callback_query(F.data.startswith("dl:"))
async def cb_del(cb: CallbackQuery):
    await cb.answer()
    sid = int(cb.data.split(":")[1])
    s = show_row(sid)
    kb = KB(
        inline_keyboard=[
            [B(text="✅ Ha, o'chirish", callback_data=f"dy:{sid}"), B(text="❌ Yo'q", callback_data=f"sh:{sid}")]
        ]
    )
    await screen(cb, f"🗑 <b>{escape(s['name'])}</b> va barcha baholar o'chirilsinmi?", kb)


@r.callback_query(F.data.startswith("dy:"))
async def cb_del_yes(cb: CallbackQuery):
    await cb.answer("O'chirildi")
    sid = int(cb.data.split(":")[1])
    q("DELETE FROM watched WHERE show_id=? AND user_id=?", (sid, cb.from_user.id))
    q("DELETE FROM shows WHERE id=? AND user_id=?", (sid, cb.from_user.id))
    await screen(cb, *(await view_my(cb.from_user.id)))


@r.callback_query(F.data.startswith("dr:"))
async def cb_drop(cb: CallbackQuery):
    sid = int(cb.data.split(":")[1])
    s = show_row(sid)
    if not s or s["user_id"] != cb.from_user.id:
        return await cb.answer("Serial topilmadi", show_alert=True)
    if s["state"] == "drop":
        q("UPDATE shows SET state=NULL WHERE id=?", (sid,))
        await cb.answer("Davom ettiramiz")
        return await send_card(cb.message, sid)
    q("UPDATE shows SET state='drop' WHERE id=?", (sid,))
    has_eps = q("SELECT 1 FROM watched WHERE show_id=?", (sid,), one=True)
    sent = await post_finale(cb.bot, cb.from_user.id, sid, None, dropped=True) if has_eps else False
    await cb.answer("To'xtatildi. Statistika kanalga yuborildi" if sent else "Serial to'xtatildi", show_alert=bool(sent))
    await send_card(cb.message, sid)


@r.callback_query(F.data.startswith("n:"))
async def cb_next(cb: CallbackQuery):
    await cb.answer()
    sid = int(cb.data.split(":")[1])
    ep = await next_episode(sid)
    if not ep:
        kb = KB(inline_keyboard=[[B(text="📚 Epizod tanlash", callback_data=f"ss:{sid}")], [B(text="🏠 Menyu", callback_data="menu")]])
        return await screen(cb, "🎉 Barcha epizodlar ko'rib bo'lingan (yoki ro'yxat topilmadi).", kb)
    await rate_screen(cb, sid, ep["season"], ep["number"])


@r.callback_query(F.data.startswith("ss:"))
async def cb_seasons(cb: CallbackQuery):
    await cb.answer()
    sid = int(cb.data.split(":")[1])
    s = show_row(sid)
    eps = await episodes(s["tvmaze_id"])
    if not eps:
        return await screen(cb, "⚠️ Epizodlar ro'yxati topilmadi.", back_kb())
    done = {(x["season"], x["number"]) for x in q("SELECT season, number FROM watched WHERE show_id=?", (sid,))}
    seasons = sorted({e["season"] for e in eps})
    btns = []
    for se in seasons:
        total = sum(1 for e in eps if e["season"] == se)
        w = sum(1 for e in eps if e["season"] == se and (se, e["number"]) in done)
        btns.append(B(text=f"{se}-fasl ({w}/{total})", callback_data=f"se:{sid}:{se}"))
    rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
    rows.append([B(text="◀️ Orqaga", callback_data=f"sh:{sid}")])
    await screen(cb, f"📚 <b>{escape(s['name'])}</b>\nFaslni tanlang:", KB(inline_keyboard=rows))


@r.callback_query(F.data.startswith("se:"))
async def cb_season(cb: CallbackQuery):
    await cb.answer()
    _, sid, season = cb.data.split(":")
    await season_screen(cb, int(sid), int(season))


async def season_screen(cb: CallbackQuery, sid, season):
    s = show_row(sid)
    eps = [e for e in await episodes(s["tvmaze_id"]) if e["season"] == season][:90]
    done = {x["number"]: x["score"] for x in q("SELECT number, score FROM watched WHERE show_id=? AND season=?", (sid, season))}
    btns = [
        B(text=(f"{e['number']}✅" if e["number"] in done else str(e["number"])), callback_data=f"e:{sid}:{season}:{e['number']}")
        for e in eps
    ]
    rows = [btns[i : i + 5] for i in range(0, len(btns), 5)]
    rows.append([B(text="Butun faslni oldin ko'rganman", callback_data=f"sp:{sid}:{season}")])
    rows.append([B(text="Fasllar", callback_data=f"ss:{sid}")])
    await screen(cb, f"<b>{escape(s['name'])}</b> - {season}-fasl\nEpizodni tanlang (✅ - baholangan):", KB(inline_keyboard=rows))


@r.callback_query(F.data.startswith("sp:"))
async def cb_season_past(cb: CallbackQuery):
    """Butun faslni oldin ko'rilgan deb belgilash: bitta ball hamma baholanmagan epizodga qo'yiladi."""
    await cb.answer()
    _, sid, season = cb.data.split(":")
    sid, season = int(sid), int(season)
    s = show_row(sid)
    btns = [B(text=str(i), callback_data=f"spr:{sid}:{season}:{i}") for i in range(1, 11)]
    rows = [btns[:5], btns[5:], [B(text="Orqaga", callback_data=f"se:{sid}:{season}")]]
    await screen(
        cb,
        f"<b>{escape(s['name'])}</b> - {season}-fasl\n\n"
        "Bu faslni oldin ko'rgansiz. Umumiy ballni tanlang: u faslning baholanmagan epizodlariga qo'yiladi. "
        "Kanalga yuborilmaydi, lekin yakuniy statistikaga qo'shiladi.",
        KB(inline_keyboard=rows),
    )


@r.callback_query(F.data.startswith("spr:"))
async def cb_season_past_save(cb: CallbackQuery):
    _, sid, season, score = cb.data.split(":")
    sid, season, score = int(sid), int(season), int(score)
    s = show_row(sid)
    if not s or s["user_id"] != cb.from_user.id:
        return await cb.answer("Serial topilmadi", show_alert=True)
    all_eps = await episodes(s["tvmaze_id"])
    eps = [e for e in all_eps if e["season"] == season and e["number"] >= 1]
    if all_eps:
        save_totals(sid, all_eps)
    q("UPDATE shows SET state=NULL WHERE id=?", (sid,))
    ts = int(time.time())
    added = 0
    for e in eps:
        cur = db.execute(
            "INSERT OR IGNORE INTO watched(user_id, show_id, season, number, ep_name, runtime, score, ts, past) "
            "VALUES(?,?,?,?,?,?,?,?,1)",
            (cb.from_user.id, sid, season, e["number"], e.get("name") or "", e.get("runtime") or 0, score, ts),
        )
        added += cur.rowcount
    db.commit()
    await cb.answer(f"{added} ta epizod saqlandi")
    await season_screen(cb, sid, season)


@r.callback_query(F.data.startswith("e:"))
async def cb_ep(cb: CallbackQuery):
    await cb.answer()
    parts = cb.data.split(":")
    sid, season, number = int(parts[1]), int(parts[2]), int(parts[3])
    past = int(parts[4]) if len(parts) > 4 else 0
    await rate_screen(cb, sid, season, number, past)


def fmt_dur(mins):
    h, m = divmod(int(mins), 60)
    if h and m:
        return f"{h} soat {m} daqiqa"
    return f"{h} soat" if h else f"{m} daqiqa"


_FONTS = {
    True: ("Inter-Bold.ttf", "Inter-SemiBold.otf", "arialbd.ttf", "DejaVuSans-Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
           "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    False: ("Inter-Regular.ttf", "Inter-Regular.otf", "arial.ttf", "DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf"),
}


def _font(size, bold=True):
    for f in _FONTS[bold]:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


TIERS = (
    (9.7, "Absolute Cinema", (33, 150, 243), (255, 255, 255)),
    (9.0, "Awesome", (27, 107, 58), (255, 255, 255)),
    (8.0, "Great", (40, 185, 100), (15, 15, 15)),
    (7.0, "Good", (244, 208, 63), (15, 15, 15)),
    (6.0, "Average", (243, 156, 18), (15, 15, 15)),
    (5.0, "Bad", (231, 76, 60), (15, 15, 15)),
    (0.0, "Garbage", (108, 63, 128), (255, 255, 255)),
)


def _tier(v):
    return next(t for t in TIERS if v >= t[0] - 1e-9)


def _star(d, cx, cy, r, fill):
    pts = []
    for i in range(10):
        rad = r if i % 2 == 0 else r * 0.45
        a = -pi / 2 + i * pi / 5
        pts.append((cx + rad * cos(a), cy + rad * sin(a)))
    d.polygon(pts, fill=fill)


def render_grid(label, title, year, keys, values, avg_text, poster, owner_name=None, owner_avatar=None):
    """Fasl × epizod jadvalini PNG qilib qaytaradi."""
    seasons = sorted({s for s, _ in keys})
    rows_n = max(n for _, n in keys)
    ns = len(seasons)
    cw = 106 if ns <= 8 else max(52, int(106 * 8 / ns))
    gap = 12 if ns <= 8 else 8
    rh = 74 if rows_n <= 30 else max(28, int(74 * 30 / rows_n))
    rgap = 16 if rows_n <= 30 else 6
    cf = _font(max(14, int(min(cw * 0.40, rh * 0.58))))
    lf, hf = _font(26, False), _font(26, False)

    x0, bg = 560, (17, 17, 17)
    has_top = any(v is not None and v >= 9.7 for v in values.values())
    tiers = [t for t in TIERS if has_top or t[0] < 9.7]
    lg_w = sum(44 + int(lf.getlength(t[1])) + 36 for t in tiers)
    W = max(1000, x0 + ns * (cw + gap) + 40, 490 + lg_w + 20)
    grid_y = 290
    ph = 0
    if poster:
        ph = min(600, int(poster.height * 400 / poster.width))
    H = max(grid_y + rows_n * (rh + rgap) + 40, 150 + ph + 220)

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    d.text((32, 40), label, font=_font(40), fill=(235, 235, 235))

    if owner_name or owner_avatar:
        av = 84
        ax, ay = W - 40 - av, 28
        if owner_avatar:
            a = owner_avatar.resize((av, av), Image.LANCZOS)
            m = Image.new("L", (av * 4, av * 4), 0)
            ImageDraw.Draw(m).ellipse([0, 0, av * 4 - 1, av * 4 - 1], fill=255)
            m = m.resize((av, av), Image.LANCZOS)
            img.paste(a, (ax, ay), m)
        else:
            d.ellipse([ax, ay, ax + av, ay + av], fill=(60, 60, 60))
            ini = (owner_name or "?")[:1].upper()
            d.text((ax + av / 2, ay + av / 2), ini, font=_font(40), fill=(230, 230, 230), anchor="mm")
        if owner_name:
            nf, nsz = None, 32
            max_w = ax - 20 - 500
            while nsz > 18:
                nf = _font(nsz)
                if nf.getlength(owner_name) <= max_w:
                    break
                nsz -= 2
            nm = owner_name
            while len(nm) > 1 and nf.getlength(nm) > max_w:
                nm = nm[:-1]
            if nm != owner_name:
                nm = nm.rstrip() + "…"
            d.text((ax - 20, ay + av / 2), nm, font=nf, fill=(235, 235, 235), anchor="rm")

    y = 150
    if poster:
        p = poster.resize((400, ph), Image.LANCZOS)
        mask = Image.new("L", p.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, p.width, p.height], radius=18, fill=255)
        img.paste(p, (32, y), mask)
        y += ph + 36
    _star(d, 50, y + 22, 16, (255, 170, 0))
    d.text((78, y + 22), avg_text, font=_font(32), fill=(240, 240, 240), anchor="lm")
    y += 70
    tf, size = None, 46
    while size > 26:
        tf = _font(size)
        if tf.getlength(title) <= 400:
            break
        size -= 2
    d.text((32, y), title, font=tf, fill=(255, 255, 255))
    if year:
        d.text((32, y + size + 14), year, font=_font(26, False), fill=(150, 150, 150))

    lx = 490
    for _, name, col, _t in tiers:
        d.ellipse([lx, 164, lx + 30, 194], fill=col)
        d.text((lx + 44, 179), name, font=lf, fill=(225, 225, 225), anchor="lm")
        lx += 44 + int(lf.getlength(name)) + 36

    for ci, sn in enumerate(seasons):
        cx = x0 + ci * (cw + gap) + cw // 2
        d.text((cx, grid_y - 30), f"S{sn}", font=hf, fill=(215, 215, 215), anchor="mm")
    for n in range(1, rows_n + 1):
        cy = grid_y + (n - 1) * (rh + rgap)
        d.text((x0 - 24, cy + rh // 2), f"E{n}", font=_font(max(14, min(24, rh // 3)), False),
               fill=(215, 215, 215), anchor="rm")
        for ci, sn in enumerate(seasons):
            if (sn, n) not in keys:
                continue
            cx = x0 + ci * (cw + gap)
            v = values.get((sn, n))
            if v is None:
                d.rounded_rectangle([cx, cy, cx + cw, cy + rh], radius=10, fill=(190, 190, 190))
                d.text((cx + cw / 2, cy + rh / 2), "?", font=cf, fill=(60, 60, 60), anchor="mm")
            else:
                _, _, col, tc = _tier(v)
                d.rounded_rectangle([cx, cy, cx + cw, cy + rh], radius=10, fill=col)
                d.text((cx + cw / 2, cy + rh / 2), f"{v:.1f}", font=cf, fill=tc, anchor="mm")
    out = BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


async def fetch_owner(bot, uid):
    """Bot egasining (foydalanuvchining) ismi va profil rasmini qaytaradi."""
    name, avatar = None, None
    if not bot:
        return name, avatar
    try:
        ch = await bot.get_chat(uid)
        name = ch.full_name
    except Exception as e:
        log.warning("profil ismi olinmadi: %s", e)
    try:
        ph = await bot.get_user_profile_photos(uid, limit=1)
        if ph.total_count:
            buf = BytesIO()
            await bot.download(ph.photos[0][-1].file_id, destination=buf)
            buf.seek(0)
            avatar = Image.open(buf).convert("RGB")
    except Exception as e:
        log.warning("profil rasmi olinmadi: %s", e)
    return name, avatar


async def fetch_image(url):
    if not url:
        return None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as sess:
            async with sess.get(url) as resp:
                if resp.status != 200:
                    return None
                return Image.open(BytesIO(await resp.read())).convert("RGB")
    except Exception as e:
        log.warning("poster yuklanmadi: %s", e)
        return None


async def general_ratings(s, eps):
    """Epizodlarning umumiy baholari. OMDB_API_KEY bo'lsa IMDb'dan, bo'lmasa TVMaze'dan."""
    vals = {(e["season"], e["number"]): None for e in eps}
    label = "IMDb"
    key = OMDB_KEY
    if key:
        info = await api(f"/shows/{s['tvmaze_id']}") or {}
        imdb = (info.get("externals") or {}).get("imdb")
        if imdb:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as sess:
                    for sn in sorted({k[0] for k in vals}):
                        async with sess.get(
                            "https://www.omdbapi.com/", params={"apikey": key, "i": imdb, "Season": sn}
                        ) as resp:
                            data = await resp.json()
                        for ep in data.get("Episodes") or []:
                            try:
                                k, v = (sn, int(ep["Episode"])), float(ep["imdbRating"])
                            except (KeyError, ValueError, TypeError):
                                continue
                            if k in vals:
                                vals[k] = v
                label = "IMDb"
            except Exception as e:
                log.warning("OMDb xato: %s", e)
    return vals, label


STYLES = [
    (1.5, "Fanat"),
    (0.9, "Saxiy"),
    (0.4, "Mehribon"),
    (-0.4, "Adolatli"),
    (-1.2, "Qattiqqo'l"),
    (-99, "Shafqatsiz"),
]


def finale_caption(s, dropped=False):
    rows = q("SELECT * FROM watched WHERE show_id=? ORDER BY ts", (s["id"],))
    avg = sum(r["score"] for r in rows) / len(rows)
    known = sorted(r["runtime"] for r in rows if r["runtime"])
    fallback = known[len(known) // 2] if known else 0
    mins = sum(r["runtime"] or fallback for r in rows)
    days = {datetime.fromtimestamp(r["ts"], TZ).date() for r in rows if not r["past"]}
    span = (max(days) - min(days)).days + 1 if days else 0
    n_past = sum(1 for r in rows if r["past"])
    style = "—"
    if s["rating"]:
        d = avg - s["rating"]
        style = next(n for lim, n in STYLES if d >= lim)
        if abs(d) < 0.05:
            style += " — odatdagi reytingga teng"
        else:
            style += f" — odatdagi reytingdan ~{abs(d):.1f} ball {'yuqori' if d > 0 else 'past'}"
    head = "<b>To'xtatildi</b>" if dropped else "<b>Tugadi</b>"
    if span:
        head += f" ({span} kunda)"
    lines = [head, f"<b>Ko'rilgan:</b> {len(rows)} epizod" + (f" (oldin ko'rilgan: {n_past})" if n_past else "")]
    lines.append(f"<b>Jami vaqt:</b> {fmt_dur(mins)}")
    return "\n".join(lines) + f"\n\n<b>Baholash turi:</b> {style}"


async def finale_package(s, eps, bot=None, dropped=False):
    """-> (izoh, [umumiy baholar rasmi, mening baholarim rasmi])"""
    eps = eps or await episodes(s["tvmaze_id"])
    rows = q("SELECT * FROM watched WHERE show_id=?", (s["id"],))
    mine = {(r["season"], r["number"]): float(r["score"]) for r in rows}
    keys = {(e["season"], e["number"]) for e in eps if e["number"] >= 1} | set(mine)
    gen, gen_label = await general_ratings(s, eps)
    poster = await fetch_image(s["poster"])
    owner_name, owner_avatar = await fetch_owner(bot, s["user_id"])
    year = (s["premiered"] or "")[:4]
    avg = sum(mine.values()) / len(mine)
    gvals = [v for v in gen.values() if v]
    g_avg = s["rating"] or (sum(gvals) / len(gvals) if gvals else None)
    g_text = f"{g_avg:.1f}" if g_avg else "—"
    name = s["name"]
    imgs = await asyncio.gather(
        asyncio.to_thread(render_grid, gen_label, name, year, keys, gen, g_text, poster),
        asyncio.to_thread(render_grid, "Mening baholarim", name, year, keys, mine,
                          f"{avg:.1f} ({len(mine)} ta)", poster, owner_name, owner_avatar),
    )
    return finale_caption(s, dropped), list(imgs)


def _album(cap, imgs):
    return [
        InputMediaPhoto(
            media=BufferedInputFile(b, f"{i + 1}.png"),
            caption=cap if i == 0 else None,
            parse_mode=ParseMode.HTML,
        )
        for i, b in enumerate(imgs)
    ]


async def post_finale(bot, uid, sid, eps_all, dropped=False):
    """Serial tugagach kanalga ikkita rasm (albom) yuboradi. Qayta baholansa, shu albom yangilanadi."""
    u = user(uid)
    if not u["post_on"] or not u["active_chat"]:
        return None
    s = show_row(sid)
    chat = u["active_chat"]
    cap, imgs = await finale_package(s, eps_all, bot, dropped)
    kind = s["fin_kind"] or ""
    if s["fin_msg"] and s["fin_chat"] == chat and kind.startswith("album:"):
        ids = [s["fin_msg"], int(kind.split(":")[1])]
        ok = True
        for m, mid in zip(_album(cap, imgs), ids):
            try:
                await bot.edit_message_media(m, chat_id=chat, message_id=mid)
            except TelegramBadRequest as e:
                if "not modified" not in str(e):
                    ok = False
                    break
            except Exception as e:
                log.warning("albomni tahrirlab bo'lmadi: %s", e)
                ok = False
                break
        if ok:
            return True
        for mid in ids:
            try:
                await bot.delete_message(chat, mid)
            except Exception:
                pass
    try:
        msgs = await bot.send_media_group(chat, _album(cap, imgs))
    except Exception as e:
        log.warning("kanalga albom yuborib bo'lmadi: %s", e)
        return False
    q(
        "UPDATE shows SET fin_chat=?, fin_msg=?, fin_kind=? WHERE id=?",
        (chat, msgs[0].message_id, f"album:{msgs[1].message_id}", sid),
    )
    return True


@r.callback_query(F.data.startswith("r:"))
async def cb_rate(cb: CallbackQuery):
    parts = cb.data.split(":")
    sid, season, number, score = int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
    past = int(parts[5]) if len(parts) > 5 else 0
    uid = cb.from_user.id
    s = show_row(sid)
    if not s or s["user_id"] != uid:
        return await cb.answer("Serial topilmadi", show_alert=True)
    eps = await episodes(s["tvmaze_id"])
    ep = next((e for e in eps if e["season"] == season and e["number"] == number), {})
    ep_name = ep.get("name") or ""
    if eps:
        save_totals(sid, eps)
    q("UPDATE shows SET state=NULL WHERE id=?", (sid,))
    old = q("SELECT * FROM watched WHERE show_id=? AND season=? AND number=?", (sid, season, number), one=True)
    q(
        "INSERT INTO watched(user_id, show_id, season, number, ep_name, runtime, score, ts, past) "
        "VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(show_id, season, number) DO UPDATE SET score=excluded.score, "
        "ts=CASE WHEN watched.past=1 AND excluded.past=0 THEN excluded.ts ELSE watched.ts END, "
        "past=CASE WHEN watched.past=1 AND excluded.past=0 THEN 0 ELSE watched.past END",
        (uid, sid, season, number, ep_name, ep.get("runtime") or 0, score, int(time.time()), past),
    )
    await cb.answer(f"{score}/10")
    row = q("SELECT * FROM watched WHERE show_id=? AND season=? AND number=?", (sid, season, number), one=True)
    today_n = q("SELECT COUNT(*) c FROM watched WHERE user_id=? AND ts>=? AND past=0", (uid, today_ts()), one=True)["c"]
    text = (
        f"📺 <b>{escape(s['name'])}</b>\n"
        f"S{season}E{number}" + (f" · {escape(ep_name)}" if ep_name else "") + "\n\n"
        f"{stars(score)}  <b>{score}/10</b>"
    )
    imdb_r = await imdb_ep_rating(s["tvmaze_id"], season, number)
    if imdb_r:
        text += f"\n\nIMDb: <b>{imdb_r}/10</b>"

    res, edited = None, False
    u = user(uid)
    if u["post_on"] and u["active_chat"] and not past:
        if old and old["post_msg"] and old["post_chat"] == u["active_chat"]:
            if await edit_post(cb.bot, old["post_chat"], old["post_msg"], old["post_kind"], text):
                res, edited = True, True
        if not edited:
            img = ep.get("image") or {}
            photos = [x for x in (img.get("original"), img.get("medium"), s["poster"]) if x] if u["use_photo"] else []
            res, msg_id, kind = await send_rated(cb.bot, u["active_chat"], text, photos)
            if res:
                q(
                    "UPDATE watched SET post_chat=?, post_msg=?, post_kind=? WHERE show_id=? AND season=? AND number=?",
                    (u["active_chat"], msg_id, kind, sid, season, number),
                )

    nxt = await next_episode(sid)
    watched_set = {(x["season"], x["number"]) for x in q("SELECT season, number FROM watched WHERE show_id=?", (sid,))}
    finished = bool(eps) and all((e["season"], e["number"]) in watched_set for e in eps)
    fin_res = None
    if finished and not past:
        fin_res = await post_finale(cb.bot, uid, sid, eps)
    rows = []
    if nxt:
        rows.append([B(text=f"Keyingi: S{nxt['season']}E{nxt['number']}", callback_data=f"n:{sid}")])
    if finished:
        rows.append([B(text="📊 Serial statistikasi", callback_data=f"fin:{sid}")])
    rows.append([B(text="📺 Serial", callback_data=f"sh:{sid}"), B(text="🏠 Menyu", callback_data="menu")])
    if past:
        done_text = f"<b>S{season}E{number}</b> - {score}/10 saqlandi (oldin ko'rilgan, kanalga yuborilmadi)"
    else:
        status = "Kanaldagi xabar yangilandi" if edited else POST_STATUS[res]
        done_text = f"<b>S{season}E{number}</b> - {score}/10 saqlandi\nBugun: {today_n} epizod\n{status}"
    if finished:
        done_text += "\n\nSerial tugadi." + (" Yakuniy rasmlar kanalga yuborildi." if fin_res else "")
    await screen(cb, done_text, KB(inline_keyboard=rows))


@r.callback_query(F.data.startswith("fin:"))
async def cb_fin(cb: CallbackQuery):
    await cb.answer()
    sid = int(cb.data.split(":")[1])
    s = show_row(sid)
    if not s or s["user_id"] != cb.from_user.id or not q("SELECT 1 FROM watched WHERE show_id=?", (sid,), one=True):
        return
    kb = KB(inline_keyboard=[[B(text="📺 Serial", callback_data=f"sh:{sid}"), B(text="🏠 Menyu", callback_data="menu")]])
    cap, imgs = await finale_package(s, None, cb.bot)
    await cb.message.answer_media_group(_album(cap, imgs))
    await cb.message.answer("Yakuniy statistika", reply_markup=kb)


async def main():
    global BOT_USERNAME
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT_USERNAME = (await bot.get_me()).username
    await bot.set_my_commands(
        [
            BotCommand(command="menu", description="Asosiy menyu"),
            BotCommand(command="my", description="Seriallarim"),
            BotCommand(command="stats", description="Statistika"),
            BotCommand(command="help", description="Yordam"),
        ]
    )
    dp = Dispatcher()
    dp.include_router(r)
    catalog_task = asyncio.create_task(build_catalog())
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Bot ishga tushdi: @%s", BOT_USERNAME)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
