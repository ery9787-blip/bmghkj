"""
smm.py — LockSMM (SMM panel API v2) xizmatlarini botga ulovchi modul.

Nima qiladi:
  • LockSMM `services` ro'yxatini DAVRIY (standart: har 3 daqiqada) yuklab, bazadagi
    nusxa bilan solishtiradi: yangi xizmat — menyuga o'zi qo'shiladi, o'chirilgan —
    menyudan o'zi yashiriladi, narx o'zgarsa — yangilanadi. Adminga qisqa hisobot boradi.
  • Menyu (Tarmoq → Katalog → Bo'lim → Xizmat → Xizmat kartasi) avtomatik quriladi.
  • Buyurtma: miqdor → havola → tasdiqlash. Pul AVVAL atomik ushlab turiladi,
    API xato qilsa — qaytariladi (balans manfiyga tushmaydi, ikki marta yechilmaydi).
  • Buyurtma holati fonda kuzatiladi (bitta so'rovda 100 tagacha). Tugasa/bekor bo'lsa/
    qisman bajarilsa foydalanuvchiga xabar boradi, qaytariladigan pul avtomatik qaytadi.
  • Buyurtmalarim: ro'yxat, holat, qayta tiklash (refill), bekor qilish.
  • Admin: sinxronlash, marja, bo'limlarni sozlash, xizmat narxi/yashirish, chegirmalar.

Ulash: bot.py da `smm.setup(...)`, routerlarni qo'shish va `await smm.init()` /
`smm.start_tasks()` (qarang: bot.py ichidagi "SMM" izohlari).

.env:
    LOCKSMM_API_KEY=...        (MAJBURIY — kodga yozmang!)
    LOCKSMM_URL=https://locksmm.com/api/v2   (ixtiyoriy)
"""
from __future__ import annotations

import asyncio
import html
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

LOCKSMM_API_KEY = os.getenv("LOCKSMM_API_KEY", "")
LOCKSMM_URL = os.getenv("LOCKSMM_URL", "https://locksmm.com/api/v2")
TASHKENT_TZ = ZoneInfo("Asia/Tashkent")

router = Router(name="smm_user")
admin_router = Router(name="smm_admin")

_ctx: dict = {}


def setup(*, db, bot, admin_id, E, ib_button, notify_orders, log_event):
    """bot.py dan chaqiriladi. Bot.py ni import qilmaymiz (aylanma import bo'lmasligi uchun)."""
    _ctx.update(db=db, bot=bot, admin_id=int(admin_id), E=E, B=ib_button,
                notify_orders=notify_orders, log_event=log_event)


def E(slug: str) -> str:
    return _ctx["E"](slug)


def B(label: str, slug: str | None = None, style: str | None = None, **kw):
    return _ctx["B"](label, slug, style=style, **kw)


def _db():
    return _ctx["db"]


def _pool():
    return _ctx["db"].pool


# ══════════════════════════════════════════════════════════════
#  PREMIUM EMOJI SLUGLARI (bot.py dagi PREMIUM_EMOJI_SLUGS ga qo'shiladi)
#  Admin /setemoji <slug> <id> bilan haqiqiy Premium emojiga bog'laydi.
#  Bog'lanmaguncha oddiy (fallback) emoji ko'rinadi.
# ══════════════════════════════════════════════════════════════
SMM_EMOJI_SLUGS = {
    # tarmoqlar
    "net_telegram": "✈️", "net_instagram": "📸", "net_tiktok": "🎵", "net_youtube": "▶️",
    "net_stars": "⭐", "net_gifts": "🧸", "net_nft": "🎁", "net_game": "🎮",
    "net_idsearch": "🔎", "net_special": "🛠", "net_other": "🌐",
    # kataloglar
    "cat_followers": "👤", "cat_premium": "💜", "cat_views": "👁", "cat_story": "📖",
    "cat_reactions": "🔥", "cat_bot": "🤖", "cat_comments": "💬", "cat_share": "📨",
    "cat_boost": "🚀", "cat_poll": "📊", "cat_uzbek": "🇺🇿", "cat_likes": "❤️",
    "cat_other": "🗂",
    # xizmat kartasi / buyurtma
    "smm_menu": "🛍", "smm_id": "🔑", "smm_price": "💵", "smm_time": "⌛",
    "smm_refill": "♻️", "smm_cancel": "🚫", "smm_count": "🎯", "smm_discount": "🏷",
    "smm_qty": "🔢", "smm_link": "📎", "smm_rules": "📜", "smm_order": "📦",
}

NETWORK_SLUGS = {
    "Telegram": "net_telegram", "Instagram": "net_instagram", "TikTok": "net_tiktok",
    "YouTube": "net_youtube", "Stars | Premium": "net_stars", "Oddiy Giftlar": "net_gifts",
    "NFT Giftlar": "net_nft", "Game Donat": "net_game", "ID Qidiruv": "net_idsearch",
    "Maxsus Xizmatlar": "net_special",
}
CATALOG_SLUGS = {
    "Oddiy Obunachilar": "cat_followers", "Premium Obunachilar": "cat_premium",
    "Ko'rishlar ( Prasmotr )": "cat_views", "Istoriya xizmatlari": "cat_story",
    "Reaksiyalar": "cat_reactions", "Bot uchun ( Obunachilar )": "cat_bot",
    "Kamentariya | Sharhlar": "cat_comments", "Ulshishlar ( Share )": "cat_share",
    "Boost ( Kanal/Guruhga )": "cat_boost", "So'rovnomaga ovoz | Like": "cat_poll",
    "O'zbek Xizmatlar": "cat_uzbek", "Layklar": "cat_likes",
}

# Menyudagi tartib (siz yuborgan rasmlardagi tartib bo'yicha)
NETWORKS = ["Telegram", "Instagram", "TikTok", "YouTube", "Stars | Premium", "Oddiy Giftlar",
            "NFT Giftlar", "Game Donat", "ID Qidiruv", "Maxsus Xizmatlar"]
CATALOGS = ["Oddiy Obunachilar", "Premium Obunachilar", "Ko'rishlar ( Prasmotr )",
            "Istoriya xizmatlari", "Reaksiyalar", "Bot uchun ( Obunachilar )",
            "Kamentariya | Sharhlar", "Ulshishlar ( Share )", "Boost ( Kanal/Guruhga )",
            "So'rovnomaga ovoz | Like", "O'zbek Xizmatlar", "Layklar", "Boshqa xizmatlar"]
# Katalog bo'linishi bor tarmoqlar (qolganlarida to'g'ridan-to'g'ri bo'limlar/xizmatlar chiqadi)
CATALOG_NETWORKS = {"Telegram", "Instagram", "TikTok", "YouTube"}

SUPPORTED_TYPES = {"default", "package", "custom comments", "poll"}
FINAL_STATUSES = ("Completed", "Canceled", "Partial", "Failed")
PAGE_SIZE = 10

# ══════════════════════════════════════════════════════════════
#  AVTOMATIK TASNIFLASH (Tarmoq → Katalog)
#  Bo'lim (3-pog'ona) = LockSMM'ning o'z `category` nomi (tozalangan).
#  Qoidalarni shu yerda o'zgartirish mumkin; admin esa har bir kategoriyani
#  paneldan qo'lda ham ko'chira oladi (qo'lda ko'chirilgani hech qachon
#  avtomatik o'zgarmaydi).
# ══════════════════════════════════════════════════════════════
_APOS = str.maketrans({"’": "'", "‘": "'", "ʻ": "'", "ʼ": "'", "`": "'", "´": "'"})


def _norm(s: str) -> str:
    return (s or "").translate(_APOS).lower()


def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.I | re.U)


NETWORK_RULES: list[tuple[str, re.Pattern]] = [
    ("NFT Giftlar", _rx(r"\bnft\b")),
    ("Stars | Premium", _rx(r"\bstars?\b|звезд|(telegram|tg)\s*premium(?!\s*(obunachi|follow|member|subscri|a'zo))|premium\s*(\d+\s*)?(oy|month|мес|1|3|6|12)\b")),
    ("Oddiy Giftlar", _rx(r"\bgifts?\b|sovg'?a|подар")),
    ("Game Donat", _rx(r"pubg|\buc\b|free\s*fire|mobile\s*legends|\bmlbb\b|brawl|clash|roblox|robux|genshin|steam|fortnite|v-?bucks|donat|cod\s*mobile|standoff|valorant|almaz|diamond")),
    ("ID Qidiruv", _rx(r"id\s*qidiruv|id\s*search|find\s*id|username\s*(search|qidir)")),
    ("Telegram", _rx(r"telegram|\btg\b|t\.me|телеграм")),
    ("Instagram", _rx(r"instagram|\big\b|insta\b|reels")),
    ("TikTok", _rx(r"tik\s*tok|\btt\b|тикток")),
    ("YouTube", _rx(r"youtube|\byt\b|ютуб|shorts")),
]

# (katalog nomi, regex, faqat shu tarmoqlarda (None = hammasida))
CATALOG_RULES: list[tuple[str, re.Pattern, set | None]] = [
    ("O'zbek Xizmatlar", _rx(r"o'?zbek|uzbek|🇺🇿|узбек|\buz\b"), {"Telegram"}),
    ("Premium Obunachilar", _rx(r"premium.*(obunachi|follow|member|subscri|a'zo)|(obunachi|follow|member|subscri|a'zo).*premium"), None),
    ("Boost ( Kanal/Guruhga )", _rx(r"\bboost"), None),
    ("Reaksiyalar", _rx(r"reaction|reaksiya|реакц"), None),
    ("Istoriya xizmatlari", _rx(r"stor(y|ies)|istoriya|сторис|история"), None),
    ("Kamentariya | Sharhlar", _rx(r"comment|komment|kamentariya|sharh|коммент"), None),
    ("Ulshishlar ( Share )", _rx(r"\bshares?\b|ulashish|ulishish|ulshish|forward|repost|репост"), None),
    ("So'rovnomaga ovoz | Like", _rx(r"\bpoll|\bvote|ovoz|so'rovnoma|опрос|голос|\blikes?\b|layk"), {"Telegram"}),
    ("Layklar", _rx(r"\blikes?\b|layk|лайк"), None),
    ("Bot uchun ( Obunachilar )", _rx(r"\bbot\b|бот"), {"Telegram"}),
    ("Ko'rishlar ( Prasmotr )", _rx(r"view|ko'rish|korish|prasmotr|просмотр|watch"), None),
    ("Oddiy Obunachilar", _rx(r"obunachi|follow|member|subscrib|a'zo|участник|подписчик|channel|group|kanal|guruh"), None),
]


def classify(category: str, sample_names: list[str]) -> tuple[str, str | None, bool]:
    """(tarmoq, katalog yoki None, tekshiruv_kerak) qaytaradi."""
    c = _norm(category)
    review = False
    network = None
    for net, rx in NETWORK_RULES:
        if rx.search(c):
            network = net
            break
    if network is None:  # kategoriya nomida yo'q — xizmat nomlaridan qidiramiz
        joined = _norm(" ".join(sample_names[:5]))
        for net, rx in NETWORK_RULES:
            if rx.search(joined):
                network = net
                break
    if network is None:
        return "Maxsus Xizmatlar", None, True

    if network not in CATALOG_NETWORKS:
        return network, None, False

    for name, rx, only in CATALOG_RULES:
        if only is not None and network not in only:
            continue
        if rx.search(c):
            return network, name, False
    # kategoriya nomida topilmadi — xizmat nomlaridan urinib ko'ramiz
    joined = _norm(" ".join(sample_names[:5]))
    for name, rx, only in CATALOG_RULES:
        if only is not None and network not in only:
            continue
        if name == "O'zbek Xizmatlar":  # xizmat nomida 'uzbek' bo'lishi ko'p — kategoriya bo'yicha qaraymiz
            continue
        if rx.search(joined):
            return network, name, True
    return network, "Boshqa xizmatlar", True


_NET_WORDS = _rx(r"^\s*(telegram|tg|instagram|insta|ig|tik\s*tok|tt|youtube|yt)\b[\s:|>\-–—•·»/]*")


def clean_section(category: str) -> str:
    s = _NET_WORDS.sub("", category or "").strip(" |>-–—•·»/:")
    return (s or category or "Xizmatlar").strip()[:60]


# ══════════════════════════════════════════════════════════════
#  YORDAMCHILAR
# ══════════════════════════════════════════════════════════════
def fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def now_tk() -> datetime:
    return datetime.now(TASHKENT_TZ)


def _truthy(v) -> bool:
    return v is True or (isinstance(v, (int, float)) and v == 1) or str(v).strip().lower() in ("true", "1", "yes", "ha")


def _to_int(v, default=0) -> int:
    try:
        return int(float(str(v).strip()))
    except Exception:
        return default


def _to_float(v, default=0.0) -> float:
    try:
        return float(str(v).strip().replace(",", "."))
    except Exception:
        return default


async def _gs(key: str, default):
    """Sozlamani turini saqlagan holda o'qiydi."""
    try:
        val = await _db().get_setting(key)
    except Exception:
        return default
    if val is None or val == "":
        return default
    try:
        return type(default)(val)
    except Exception:
        return default


_alert_last: dict[str, float] = {}


async def alert_admin(key: str, text: str, ttl: int = 900):
    """Bir xil ogohlantirishni spam qilmaydi (ttl soniya ichida bir marta)."""
    now = time.time()
    if now - _alert_last.get(key, 0) < ttl:
        return
    _alert_last[key] = now
    try:
        await _ctx["bot"].send_message(_ctx["admin_id"], text)
    except Exception as e:
        print(f"⚠️ SMM: adminga xabar yuborib bo'lmadi: {e}")


async def _send(uid: int, text: str, kb=None):
    try:
        await _ctx["bot"].send_message(uid, text, reply_markup=kb)
    except Exception:
        pass  # foydalanuvchi botni bloklagan bo'lishi mumkin


async def _show(target, text: str, kb=None):
    """CallbackQuery bo'lsa xabarni tahrirlaydi, Message bo'lsa yangi yuboradi."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return
            try:
                await target.message.answer(text, reply_markup=kb)
            except Exception:
                pass
    else:
        await target.answer(text, reply_markup=kb)


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _is_cancel(text: str | None) -> bool:
    t = _norm(text or "")
    return t.startswith("/cancel") or "bekor qilish" in t or t.strip() in ("bekor", "❌ bekor")


# ══════════════════════════════════════════════════════════════
#  LOCKSMM API
# ══════════════════════════════════════════════════════════════
async def api_call(action: str, params: dict | None = None, post: bool = False, timeout: int = 30):
    """Muvaffaqiyatsiz bo'lsa {"error": ..., "_net": True} qaytaradi (tarmoq xatosi)."""
    if not LOCKSMM_API_KEY:
        return {"error": "LOCKSMM_API_KEY sozlanmagan", "_net": True}
    data = {"key": LOCKSMM_API_KEY, "action": action}
    if params:
        data.update(params)
    try:
        async with aiohttp.ClientSession() as s:
            tmo = aiohttp.ClientTimeout(total=timeout)
            if post:
                ctx = s.post(LOCKSMM_URL, data=data, timeout=tmo)
            else:
                ctx = s.get(LOCKSMM_URL, params=data, timeout=tmo)
            async with ctx as r:
                txt = await r.text()
                status = r.status
        try:
            return json.loads(txt)
        except Exception:
            return {"error": f"Noto'g'ri javob (HTTP {status})", "_net": True}
    except asyncio.TimeoutError:
        return {"error": "timeout", "_net": True}
    except Exception as e:
        return {"error": str(e).replace(LOCKSMM_API_KEY, "***")[:200], "_net": True}


def friendly_error(err: str) -> tuple[str, bool, bool]:
    """(foydalanuvchi uchun matn, adminga xabar kerakmi, xizmatlarni qayta yuklash kerakmi)."""
    e = _norm(err)
    if "not enough funds" in e or "insufficient" in e or "balance" in e:
        return "Xizmat vaqtincha mavjud emas. Iltimos, birozdan so'ng urinib ko'ring.", True, False
    if "api key" in e or "incorrect request" in e:
        return "Xizmat vaqtincha mavjud emas. Iltimos, birozdan so'ng urinib ko'ring.", True, False
    if "less than minimal" in e or "minimal" in e:
        return "Miqdor minimal chegaradan kam.", False, True
    if "more than maximal" in e or "maximal" in e:
        return "Miqdor maksimal chegaradan ko'p.", False, True
    if "duplicate" in e or "active order" in e or "already" in e:
        return "Bu havolaga buyurtma hozir bajarilmoqda. Bajarilganidan so'ng qayta urinib ko'ring.", False, False
    if "link" in e:
        return "Havola noto'g'ri yoki yopiq. Havolani tekshirib, qaytadan kiriting.", False, False
    if "incorrect service" in e or "service" in e:
        return "Bu xizmat hozir mavjud emas. Boshqa xizmatni tanlang.", False, True
    return "Buyurtmani yuborishda xatolik yuz berdi. Mablag'ingiz qaytarildi.", True, False


# ══════════════════════════════════════════════════════════════
#  BAZA
# ══════════════════════════════════════════════════════════════
async def _create_tables():
    async with _pool().acquire() as c:
        await c.execute("""
            CREATE TABLE IF NOT EXISTS smm_services (
                service_id   BIGINT PRIMARY KEY,
                category_raw TEXT NOT NULL DEFAULT '',
                name         TEXT NOT NULL DEFAULT '',
                rate         DOUBLE PRECISION NOT NULL DEFAULT 0,
                min_q        BIGINT DEFAULT 0,
                max_q        BIGINT DEFAULT 0,
                type         TEXT DEFAULT 'Default',
                refill       BOOLEAN DEFAULT FALSE,
                cancel       BOOLEAN DEFAULT FALSE,
                dripfeed     BOOLEAN DEFAULT FALSE,
                extra        JSONB DEFAULT '{}'::jsonb,
                pos          INT DEFAULT 0,
                network      TEXT DEFAULT '',
                node_id      INT,
                active       BOOLEAN DEFAULT TRUE,
                hidden       BOOLEAN DEFAULT FALSE,
                price_override BIGINT,
                description_override TEXT,
                first_seen   TIMESTAMPTZ DEFAULT NOW(),
                last_seen    TIMESTAMPTZ DEFAULT NOW(),
                removed_at   TIMESTAMPTZ
            )""")
        await c.execute("CREATE INDEX IF NOT EXISTS idx_smm_services_node ON smm_services (node_id)")
        await c.execute("""
            CREATE TABLE IF NOT EXISTS smm_nodes (
                id        SERIAL PRIMARY KEY,
                parent_id INT NOT NULL DEFAULT 0,
                level     SMALLINT NOT NULL DEFAULT 1,
                name      TEXT NOT NULL,
                sort      INT DEFAULT 1000,
                UNIQUE (parent_id, name)
            )""")
        await c.execute("""
            CREATE TABLE IF NOT EXISTS smm_cat_map (
                id           SERIAL PRIMARY KEY,
                category_raw TEXT UNIQUE NOT NULL,
                network      TEXT NOT NULL,
                catalog      TEXT,
                manual       BOOLEAN DEFAULT FALSE,
                review       BOOLEAN DEFAULT FALSE
            )""")
        await c.execute("""
            CREATE TABLE IF NOT EXISTS smm_orders (
                id           SERIAL PRIMARY KEY,
                user_id      BIGINT NOT NULL,
                service_id   BIGINT NOT NULL,
                service_name TEXT DEFAULT '',
                api_order    BIGINT,
                link         TEXT DEFAULT '',
                quantity     BIGINT DEFAULT 0,
                qty_based    BOOLEAN DEFAULT TRUE,
                price        BIGINT NOT NULL,
                charge       DOUBLE PRECISION,
                status       TEXT DEFAULT 'New',
                start_count  BIGINT,
                remains      BIGINT,
                refunded     BIGINT DEFAULT 0,
                refill_ok    BOOLEAN DEFAULT FALSE,
                cancel_ok    BOOLEAN DEFAULT FALSE,
                notified     TEXT DEFAULT '',
                refill_id    TEXT,
                err_count    INT DEFAULT 0,
                created_at   TIMESTAMPTZ DEFAULT NOW(),
                updated_at   TIMESTAMPTZ DEFAULT NOW(),
                finished_at  TIMESTAMPTZ
            )""")
        await c.execute("CREATE INDEX IF NOT EXISTS idx_smm_orders_user ON smm_orders (user_id)")
        await c.execute("CREATE INDEX IF NOT EXISTS idx_smm_orders_status ON smm_orders (status)")
        await c.execute("CREATE INDEX IF NOT EXISTS idx_smm_orders_service ON smm_orders (service_id)")


# ══════════════════════════════════════════════════════════════
#  MENYU KESHI
# ══════════════════════════════════════════════════════════════
_menu = {"nodes": {}, "children": {}, "count": {}}


async def load_menu():
    async with _pool().acquire() as c:
        nodes = await c.fetch("SELECT id, parent_id, level, name, sort FROM smm_nodes")
        cnt = await c.fetch(
            "SELECT node_id, COUNT(*) AS n FROM smm_services "
            "WHERE active AND NOT hidden AND node_id IS NOT NULL GROUP BY node_id")
    nd = {r["id"]: dict(r) for r in nodes}
    total: dict[int, int] = {}
    for r in cnt:
        nid = r["node_id"]
        n = r["n"]
        while nid and nid in nd:
            total[nid] = total.get(nid, 0) + n
            nid = nd[nid]["parent_id"]
    children: dict[int, list[int]] = {}
    for nid, n in nd.items():
        if total.get(nid, 0) > 0:
            children.setdefault(n["parent_id"], []).append(nid)
    for k in children:
        children[k].sort(key=lambda i: (nd[i]["sort"], nd[i]["name"]))
    _menu["nodes"], _menu["children"], _menu["count"] = nd, children, total


# ══════════════════════════════════════════════════════════════
#  SINXRONLASH (xizmatlarni avtomatik yangilab turish)
# ══════════════════════════════════════════════════════════════
_sync_lock = asyncio.Lock()
_last_sync_ts = 0.0
_last_sync_info: dict = {}
_shrink_strikes = 0


def _parse_service(el: dict, pos: int):
    try:
        sid = int(el["service"])
    except Exception:
        return None
    known = {"service", "category", "name", "rate", "min", "max", "type", "refill", "cancel", "dripfeed"}
    extra = {k: v for k, v in el.items() if k not in known}
    return {
        "id": sid,
        "category": str(el.get("category") or "").strip(),
        "name": str(el.get("name") or f"Xizmat {sid}").strip(),
        "rate": _to_float(el.get("rate")),
        "min": _to_int(el.get("min")),
        "max": _to_int(el.get("max")),
        "type": str(el.get("type") or "Default").strip(),
        "refill": _truthy(el.get("refill")),
        "cancel": _truthy(el.get("cancel")),
        "dripfeed": _truthy(el.get("dripfeed")),
        "extra": extra,
        "pos": pos,
    }


async def sync_services(manual: bool = False) -> dict:
    """LockSMM'dan ro'yxatni olib, bazaga qo'llaydi. Natija: {"ok", "new", "removed", ...}"""
    global _last_sync_ts, _last_sync_info, _shrink_strikes
    async with _sync_lock:
        data = await api_call("services", {"lang": "uz"}, timeout=90)
        if not isinstance(data, list) or not data:
            err = data.get("error") if isinstance(data, dict) else "bo'sh javob"
            await alert_admin("sync_fail", f"{E('warn')} <b>LockSMM xizmatlarini yuklab bo'lmadi</b>\n<code>{esc(err)}</code>")
            return {"ok": False, "error": str(err)}
        parsed = [p for p in (_parse_service(el, i) for i, el in enumerate(data) if isinstance(el, dict)) if p]
        if not parsed:
            return {"ok": False, "error": "xizmatlar topilmadi"}

        async with _pool().acquire() as c:
            old_rows = await c.fetch(
                "SELECT service_id, name, rate, min_q, max_q, category_raw, active FROM smm_services")
        old = {r["service_id"]: r for r in old_rows}
        old_active = sum(1 for r in old_rows if r["active"])

        # Himoya: API vaqtincha kamaytirib yuborsa, hamma xizmatni o'chirib yubormaylik
        if old_active >= 20 and len(parsed) < old_active * 0.5 and not manual:
            _shrink_strikes += 1
            if _shrink_strikes < 3:
                await alert_admin(
                    "shrink",
                    f"{E('warn')} LockSMM ro'yxati keskin qisqardi ({old_active} → {len(parsed)}). "
                    f"Xatolik bo'lishi mumkin — hozircha eski ro'yxat saqlandi. Rost bo'lsa, «Hozir sinxronlash» ni bosing.")
                return {"ok": False, "error": "ro'yxat shubhali darajada qisqargan"}
        _shrink_strikes = 0

        new_items, back_items, price_changes, moved = [], [], [], []
        for p in parsed:
            o = old.get(p["id"])
            if o is None:
                new_items.append(p)
            else:
                if not o["active"]:
                    back_items.append(p)
                if abs(float(o["rate"]) - p["rate"]) > 1e-9:
                    price_changes.append((p, float(o["rate"])))
                if o["category_raw"] != p["category"]:
                    moved.append(p)
        seen_ids = [p["id"] for p in parsed]

        async with _pool().acquire() as c:
            async with c.transaction():
                await c.executemany("""
                    INSERT INTO smm_services
                      (service_id, category_raw, name, rate, min_q, max_q, type, refill, cancel,
                       dripfeed, extra, pos, active, last_seen, removed_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,TRUE,NOW(),NULL)
                    ON CONFLICT (service_id) DO UPDATE SET
                       category_raw=EXCLUDED.category_raw, name=EXCLUDED.name, rate=EXCLUDED.rate,
                       min_q=EXCLUDED.min_q, max_q=EXCLUDED.max_q, type=EXCLUDED.type,
                       refill=EXCLUDED.refill, cancel=EXCLUDED.cancel, dripfeed=EXCLUDED.dripfeed,
                       extra=EXCLUDED.extra, pos=EXCLUDED.pos, active=TRUE, last_seen=NOW(),
                       removed_at=NULL
                """, [(p["id"], p["category"], p["name"], p["rate"], p["min"], p["max"], p["type"],
                       p["refill"], p["cancel"], p["dripfeed"], json.dumps(p["extra"], ensure_ascii=False),
                       p["pos"]) for p in parsed])
                gone = await c.fetch(
                    "UPDATE smm_services SET active=FALSE, removed_at=NOW() "
                    "WHERE active AND service_id <> ALL($1::bigint[]) RETURNING service_id, name",
                    seen_ids)

                # kategoriyalar xaritasi
                by_cat: dict[str, list[str]] = {}
                for p in parsed:
                    by_cat.setdefault(p["category"], []).append(p["name"])
                existing_cat = {r["category_raw"] for r in await c.fetch("SELECT category_raw FROM smm_cat_map")}
                new_cats = []
                for cat, names in by_cat.items():
                    net, catalog, review = classify(cat, names)
                    if cat not in existing_cat:
                        new_cats.append((cat, net, catalog, review))
                    await c.execute("""
                        INSERT INTO smm_cat_map (category_raw, network, catalog, review)
                        VALUES ($1,$2,$3,$4)
                        ON CONFLICT (category_raw) DO UPDATE SET
                           network=EXCLUDED.network, catalog=EXCLUDED.catalog, review=EXCLUDED.review
                        WHERE NOT smm_cat_map.manual
                    """, cat, net, catalog, review)

        await rebuild_nodes()
        await load_menu()
        _last_sync_ts = time.time()

        info = {"ok": True, "total": len(parsed), "new": len(new_items), "removed": len(gone),
                "back": len(back_items), "price": len(price_changes), "moved": len(moved),
                "new_cats": len(new_cats), "first": not old}
        _last_sync_info = {**info, "at": now_tk().strftime("%d.%m.%Y %H:%M:%S")}

        # ── adminga hisobot (faqat o'zgarish bo'lsa) ──
        try:
            await _report_changes(info, new_items, gone, back_items, price_changes, new_cats, len(parsed))
        except Exception as e:
            print(f"⚠️ SMM hisobotini yuborishda xatolik: {e}")
        return info


async def _report_changes(info, new_items, gone, back_items, price_changes, new_cats, total):
    if info["first"]:
        await _ctx["bot"].send_message(
            _ctx["admin_id"],
            f"{E('check')} <b>LockSMM: dastlabki yuklash</b>\n{E('smm_menu')} Xizmatlar: <b>{total}</b> ta, "
            f"kategoriyalar: <b>{len({p['category'] for p in new_items})}</b> ta.\n\n"
            f"Menyu avtomatik tuzildi. Joylashuvni «SMM sozlamalari → Bo'limlarni sozlash» orqali tekshiring.")
        return
    if not (new_items or gone or back_items or price_changes or new_cats):
        return
    lines = [f"{E('bell')} <b>LockSMM xizmatlari yangilandi</b>"]
    if new_items:
        lines.append(f"\n➕ Yangi: <b>{len(new_items)}</b> ta")
        for p in new_items[:6]:
            lines.append(f"  • <code>{p['id']}</code> {esc(p['name'][:50])}")
    if back_items:
        lines.append(f"\n♻️ Qayta paydo bo'ldi: <b>{len(back_items)}</b> ta")
    if gone:
        lines.append(f"\n➖ O'chirildi (menyudan yashirildi): <b>{len(gone)}</b> ta")
        for r in gone[:6]:
            lines.append(f"  • <code>{r['service_id']}</code> {esc(r['name'][:50])}")
    if price_changes:
        lines.append(f"\n{E('smm_price')} Narxi o'zgargan: <b>{len(price_changes)}</b> ta")
        for p, oldr in price_changes[:6]:
            lines.append(f"  • <code>{p['id']}</code> {oldr:g} → {p['rate']:g}")
        if total and len(price_changes) > total * 0.3:
            lines.append(f"\n{E('warn')} <b>Narxlar keskin o'zgardi</b> — marjani tekshiring!")
    if new_cats:
        need = [x for x in new_cats if x[3]]
        lines.append(f"\n🗂 Yangi kategoriyalar: <b>{len(new_cats)}</b> ta"
                     + (f", shundan <b>{len(need)}</b> tasi aniq joylashmadi (qarang: Bo'limlarni sozlash)" if need else ""))
    await _ctx["bot"].send_message(_ctx["admin_id"], "\n".join(lines)[:3900])


async def rebuild_nodes():
    """kategoriya xaritasi + xizmatlar asosida menyu daraxtini (nodes) qayta quradi."""
    async with _pool().acquire() as c:
        cats = await c.fetch("SELECT category_raw, network, catalog FROM smm_cat_map")
        svcs = await c.fetch(
            "SELECT service_id, category_raw, pos, type FROM smm_services WHERE active ORDER BY pos")
    cmap = {r["category_raw"]: (r["network"], r["catalog"]) for r in cats}

    # (tarmoq, katalog) -> {kategoriya: min_pos}
    groups: dict[tuple, dict[str, int]] = {}
    supported: list = []
    for s in svcs:
        if str(s["type"]).strip().lower() not in SUPPORTED_TYPES:
            continue
        net, catalog = cmap.get(s["category_raw"], ("Maxsus Xizmatlar", None))
        g = groups.setdefault((net, catalog), {})
        g[s["category_raw"]] = min(g.get(s["category_raw"], 10 ** 9), s["pos"])
        supported.append(s)

    async with _pool().acquire() as c:
        async with c.transaction():
            node_ids: dict[tuple, int] = {}
            used: set[int] = set()

            async def ensure(parent: int, level: int, name: str, sort: int) -> int:
                key = (parent, name)
                if key in node_ids:
                    return node_ids[key]
                nid = await c.fetchval("""
                    INSERT INTO smm_nodes (parent_id, level, name, sort) VALUES ($1,$2,$3,$4)
                    ON CONFLICT (parent_id, name) DO UPDATE SET sort=EXCLUDED.sort, level=EXCLUDED.level
                    RETURNING id""", parent, level, name, sort)
                node_ids[key] = nid
                return nid

            path_of: dict[tuple, int] = {}   # (net, catalog, category) -> node_id
            for (net, catalog), cat_pos in groups.items():
                net_sort = NETWORKS.index(net) if net in NETWORKS else 900
                net_id = await ensure(0, 1, net, net_sort)
                used.add(net_id)
                parent, level = net_id, 2
                if catalog:
                    cat_sort = CATALOGS.index(catalog) if catalog in CATALOGS else 900 + min(cat_pos.values()) // 1000
                    parent = await ensure(net_id, 2, catalog, cat_sort)
                    used.add(parent)
                    level = 3
                if len(cat_pos) > 1:
                    for cat, pmin in cat_pos.items():
                        sec_id = await ensure(parent, level, clean_section(cat), 1000 + pmin)
                        used.add(sec_id)
                        path_of[(net, catalog, cat)] = sec_id
                else:
                    for cat in cat_pos:
                        path_of[(net, catalog, cat)] = parent

            sids, nids = [], []
            for s in supported:
                net, catalog = cmap.get(s["category_raw"], ("Maxsus Xizmatlar", None))
                nid = path_of.get((net, catalog, s["category_raw"]))
                if nid:
                    sids.append(s["service_id"])
                    nids.append(nid)
            await c.execute("UPDATE smm_services SET node_id=NULL, network=''")
            if sids:
                await c.execute("""
                    UPDATE smm_services s SET node_id=v.nid
                    FROM (SELECT unnest($1::bigint[]) AS sid, unnest($2::int[]) AS nid) v
                    WHERE s.service_id=v.sid""", sids, nids)
                await c.execute("""
                    UPDATE smm_services s SET network=m.network
                    FROM smm_cat_map m WHERE s.category_raw=m.category_raw""")
            # ishlatilmayotgan tugunlarni o'chiramiz (ota-tugunlari bilan birga qoldiramiz)
            keep = set(nids) | used
            if keep:
                await c.execute("DELETE FROM smm_nodes WHERE NOT (id = ANY($1::int[]))", list(keep))
            else:
                await c.execute("DELETE FROM smm_nodes")


async def ensure_fresh(max_age: int = 60):
    """Buyurtma oldidan ro'yxat juda eskirgan bo'lsa, yangilab oladi."""
    if time.time() - _last_sync_ts > max_age:
        try:
            await sync_services()
        except Exception as e:
            print(f"⚠️ SMM ensure_fresh: {e}")


async def sync_loop():
    await asyncio.sleep(3)
    while True:
        try:
            await sync_services()
        except Exception as e:
            print(f"⚠️ SMM sinxronlash xatoligi: {e}")
        interval = max(60, await _gs("smm_sync_interval", 180))
        await asyncio.sleep(interval)


# ══════════════════════════════════════════════════════════════
#  NARX, CHEGIRMA
# ══════════════════════════════════════════════════════════════
def unit_price(svc, margin: float, step: int = 10) -> int:
    """1000 ta uchun (Package uchun — paket uchun) bazaviy narx, so'm."""
    if svc["price_override"]:
        return int(svc["price_override"])
    raw = float(svc["rate"]) * (1 + margin / 100.0)
    return max(1, int(math.ceil(raw / step) * step))


def order_price(svc, margin: float, qty: int, pct: int, step: int = 10) -> int:
    unit = unit_price(svc, margin, step)
    pct = max(0, min(90, pct))
    if str(svc["type"]).lower() == "package":
        return max(1, -(-(unit * (100 - pct)) // 100))
    return max(1, -(-(unit * qty * (100 - pct)) // 100000))


def _tiers(raw: str) -> list[tuple[int, int]]:
    out = []
    for part in (raw or "").split(","):
        if ":" in part:
            a, b = part.split(":", 1)
            try:
                out.append((int(a.strip()), int(b.strip())))
            except ValueError:
                pass
    out.sort()
    return out or [(0, 0)]


DEFAULT_TIERS = "0:0,200000:1,1000000:2,3000000:3,10000000:5"


async def user_discount(uid: int) -> tuple[int, int, tuple[int, int] | None]:
    """(chegirma %, jami sarflangan, keyingi bosqich (summa, %) yoki None)"""
    async with _pool().acquire() as c:
        spent = await c.fetchval(
            "SELECT COALESCE(SUM(price - refunded),0) FROM smm_orders "
            "WHERE user_id=$1 AND status NOT IN ('Failed','New')", uid) or 0
    tiers = _tiers(await _gs("smm_discount_tiers", DEFAULT_TIERS))
    pct, nxt = 0, None
    for amount, p in tiers:
        if spent >= amount:
            pct = p
        elif nxt is None:
            nxt = (amount, p)
    return pct, int(spent), nxt


async def _price_cfg() -> tuple[float, int]:
    return await _gs("smm_margin", 30.0), max(1, await _gs("smm_round", 10))


# ══════════════════════════════════════════════════════════════
#  MENYU (foydalanuvchi)
# ══════════════════════════════════════════════════════════════
class SmmOrder(StatesGroup):
    qty = State()
    link = State()
    comments = State()
    answer = State()
    confirm = State()


class SmmFind(StatesGroup):
    service_id = State()
    order_id = State()


DEFAULT_INTRO = (
    "🌟 <b>Tezkor, arzon va sifatli xizmatlar!</b>\n\n"
    "⚠️ Buyurtma berishdan oldin «Qoidalar» bilan tanishib chiqing!\n\n"
    "👇 Quyidagi tarmoqlardan birini tanlang:"
)
DEFAULT_RULES = (
    "• Buyurtma berish paytida havolani to'g'ri kiritayotganingizga ishonch hosil qiling. "
    "Aks holda buyurtma yakunlangan ko'rinishga keladi va pul qaytarilmaydi.\n"
    "• Havola yopiq (private) bo'lmasligi kerak.\n"
    "• Buyurtma bajarilayotgan vaqtda havolani o'zgartirmang yoki o'chirmang — buyurtma "
    "tugallangan holatga o'zgaradi va to'lov qaytarilmaydi.\n"
    "• Bir havolaga oldingi buyurtma bajarilmasdan turib yangi buyurtma bermang.\n"
    "• Buyurtmani faqat «Bekor qilish» imkoni mavjud xizmatlarda bekor qilish mumkin. "
    "Bekor qilingan yoki to'liq bajarilmagan buyurtma uchun mablag' avtomatik hisobingizga qaytariladi."
)
CARD_WARN = (
    "⚠️ Buyurtma vaqtida havolani o'zgartirsangiz yoki noto'g'ri havola bersangiz — buyurtma "
    "tugallangan holatga o'zgaradi va biz to'lovni qaytarmaymiz!"
)


async def home_text_kb():
    nets = _menu["children"].get(0, [])
    rows, pair = [], []
    for nid in nets:
        n = _menu["nodes"][nid]
        pair.append(B(n["name"], NETWORK_SLUGS.get(n["name"], "net_other"), callback_data=f"sm:n:{nid}:0"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([B("Buyurtmalarim", "smm_order", callback_data="sm:my:0"),
                 B("ID bo'yicha qidirish", "search", callback_data="sm:find")])
    rows.append([B("Qoidalar", "smm_rules", callback_data="sm:rules")])
    text = await _gs("smm_intro_text", DEFAULT_INTRO)
    if not nets:
        text += "\n\n⏳ Xizmatlar hozircha yuklanmoqda, birozdan so'ng qayta urinib ko'ring."
    return text, _kb(rows)


@router.message(F.text.contains("Xizmatlar"))
async def smm_home_msg(msg: Message, state: FSMContext):
    await state.clear()
    text, kb = await home_text_kb()
    await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data == "sm:home")
async def smm_home_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = await home_text_kb()
    await _show(call, text, kb)
    await call.answer()


@router.callback_query(F.data == "sm:rules")
async def smm_rules(call: CallbackQuery):
    text = f"{E('smm_rules')} <b>Qoidalar</b>\n\n{esc(await _gs('smm_rules_text', DEFAULT_RULES))}"
    await _show(call, text, _kb([[B("Orqaga", "arrow_l", callback_data="sm:home")]]))
    await call.answer()


@router.callback_query(F.data.startswith("sm:n:"))
async def smm_node(call: CallbackQuery, state: FSMContext):
    await state.clear()
    _, _, nid, page = call.data.split(":")
    nid, page = int(nid), int(page)
    node = _menu["nodes"].get(nid)
    if not node or _menu["count"].get(nid, 0) == 0:
        await call.answer("Bu bo'lim hozir mavjud emas.", show_alert=True)
        return await smm_home_cb(call, state)
    back = node["parent_id"]
    back_cb = f"sm:n:{back}:0" if back else "sm:home"
    kids = _menu["children"].get(nid, [])
    if kids:
        rows = []
        for k in kids:
            kn = _menu["nodes"][k]
            slug = CATALOG_SLUGS.get(kn["name"]) if kn["level"] == 2 else None
            rows.append([B(kn["name"], slug, callback_data=f"sm:n:{k}:0")])
        rows.append([B("Orqaga", "arrow_l", callback_data=back_cb)])
        title = "Kerakli katalogni tanlang:" if node["level"] == 1 else "Kerakli bo'lim turini tanlang:"
        await _show(call, f"{E('smm_menu')} <b>{esc(node['name'])}</b>\n\n{title}", _kb(rows))
        return await call.answer()

    margin, step = await _price_cfg()
    pct, _, _ = await user_discount(call.from_user.id)
    async with _pool().acquire() as c:
        rows_db = await c.fetch(
            "SELECT service_id, name, rate, type, price_override FROM smm_services "
            "WHERE node_id=$1 AND active AND NOT hidden ORDER BY pos, service_id OFFSET $2 LIMIT $3",
            nid, page * PAGE_SIZE, PAGE_SIZE + 1)
    has_more = len(rows_db) > PAGE_SIZE
    rows = []
    for r in rows_db[:PAGE_SIZE]:
        unit = unit_price(r, margin, step)
        shown = -(-(unit * (100 - pct)) // 100)
        rows.append([B(f"{r['name'][:46]} - {fmt(shown)} so'm", None, callback_data=f"sm:s:{r['service_id']}")])
    nav = []
    if page > 0:
        nav.append(B("Oldingi", "arrow_l", callback_data=f"sm:n:{nid}:{page - 1}"))
    if has_more:
        nav.append(B("Keyingi", "arrow_r", callback_data=f"sm:n:{nid}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([B("Orqaga", "arrow_l", callback_data=back_cb)])
    text = (f"{E('point')} <b>Marhamat, kerakli xizmatni tanlang!</b>\n\n"
            f"{E('chart')} Narxlar <b>1000 tasi</b> uchun berilgan!")
    await _show(call, text, _kb(rows))
    await call.answer()


# ── xizmat kartasi ────────────────────────────────────────────
async def get_service(sid: int):
    async with _pool().acquire() as c:
        return await c.fetchrow("SELECT * FROM smm_services WHERE service_id=$1", sid)


def _fmt_secs(sec: float) -> str:
    sec = int(sec)
    if sec < 90:
        return "1 daqiqadan kam"
    m = sec // 60
    if m < 60:
        return f"{m} daqiqa"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h} soat" + (f" {m} daqiqa" if m else "")
    return f"{h // 24} kun"


def _extra(svc) -> dict:
    ex = svc["extra"]
    if isinstance(ex, str):
        try:
            ex = json.loads(ex)
        except Exception:
            ex = {}
    return ex or {}


async def build_card(svc, uid: int) -> tuple[str, InlineKeyboardMarkup]:
    margin, step = await _price_cfg()
    pct, spent, nxt = await user_discount(uid)
    sid = svc["service_id"]
    is_pkg = str(svc["type"]).lower() == "package"
    base = unit_price(svc, margin, step)
    disc = -(-(base * (100 - pct)) // 100)

    async with _pool().acquire() as c:
        cnt = await c.fetchval(
            "SELECT COUNT(*) FROM smm_orders WHERE service_id=$1 AND status NOT IN ('Failed','New')", sid)
        avg = await c.fetchval("""
            SELECT AVG(EXTRACT(EPOCH FROM (finished_at - created_at))) FROM (
                SELECT finished_at, created_at FROM smm_orders
                WHERE service_id=$1 AND status='Completed' AND finished_at IS NOT NULL
                ORDER BY id DESC LIMIT 20) t""", sid)

    ex = _extra(svc)
    avg_txt = _fmt_secs(avg) if avg else (str(ex.get("average_time") or ex.get("avg_time") or "").strip() or None)
    desc = (svc["description_override"] or ex.get("description") or ex.get("desc") or ex.get("note") or "").strip()

    unit_label = "paket" if is_pkg else "1000 tasi"
    if pct > 0:
        price_line = f"{E('smm_price')} <b>Narxi:</b> ({unit_label}) — <s>{fmt(base)}</s> → <b>{fmt(disc)} so'm</b>"
    else:
        price_line = f"{E('smm_price')} <b>Narxi:</b> ({unit_label}) — <b>{fmt(base)} so'm</b>"

    lines = [
        f"<blockquote>{E('smm_menu')} <b>Xizmat nomi:</b> {esc(svc['name'])}</blockquote>",
        "",
        f"{E('smm_id')} <b>Xizmat IDsi:</b> <code>{sid}</code>",
        price_line,
    ]
    if not is_pkg:
        lines.append(f"{E('smm_qty')} <b>Miqdor:</b> {fmt(svc['min_q'])} — {fmt(svc['max_q'])} ta")
    if avg_txt:
        lines.append(f"{E('smm_time')} <b>Bajarilish vaqti:</b> {esc(avg_txt)}")
    lines.append(f"{E('smm_refill')} <b>Qayta tiklash:</b> " + ("Mavjud ✓" if svc["refill"] else "Mavjud emas!"))
    lines.append(f"{E('smm_cancel')} <b>Bekor qilish:</b> " + ("Mavjud ✓" if svc["cancel"] else "Mavjud emas!"))
    if cnt:
        lines.append(f"{E('smm_count')} <b>Buyurtma berilgan:</b> {fmt(cnt)} marotaba!")
    lines.append(f"{E('smm_discount')} <b>Chegirmangiz:</b> {pct}%")
    if nxt:
        lines.append(f"<i>Yana {fmt(max(0, nxt[0] - spent))} so'mlik buyurtma bersangiz, chegirmangiz {nxt[1]}% bo'ladi.</i>")
    if desc:
        lines += ["", f"<blockquote expandable>{esc(desc)}</blockquote>"]
    lines += ["", f"{E('warn')} {esc(CARD_WARN.replace('⚠️ ', ''))}", "",
              f"{E('point')} <b>Ta'rif bilan tanishib chiqib «Buyurtma berish» tugmasini bosing.</b>"]

    back = f"sm:n:{svc['node_id']}:0" if svc["node_id"] else "sm:home"
    me = await _bot_username()
    share = ("https://t.me/share/url?url=" + f"https://t.me/{me}" +
             "&text=" + re.sub(r"\s+", "%20", f"{svc['name'][:60]} — {fmt(disc)} so'm / {unit_label}"))
    kb = _kb([
        [B("Buyurtma berish", "check", style="success", callback_data=f"sm:o:{sid}")],
        [B("Orqaga", "arrow_l", callback_data=back), B("Ulashish", "link", url=share)],
    ])
    return "\n".join(lines), kb


_bot_un: str = ""


async def _bot_username() -> str:
    global _bot_un
    if not _bot_un:
        try:
            _bot_un = (await _ctx["bot"].get_me()).username or ""
        except Exception:
            _bot_un = ""
    return _bot_un


@router.callback_query(F.data.startswith("sm:s:"))
async def smm_service(call: CallbackQuery, state: FSMContext):
    await state.clear()
    svc = await get_service(int(call.data.split(":")[2]))
    if not svc or not svc["active"] or svc["hidden"] or str(svc["type"]).lower() not in SUPPORTED_TYPES:
        await call.answer("Bu xizmat hozir mavjud emas.", show_alert=True)
        text, kb = await home_text_kb()
        return await _show(call, text, kb)
    text, kb = await build_card(svc, call.from_user.id)
    await _show(call, text, kb)
    await call.answer()


# ── ID bo'yicha qidirish ──────────────────────────────────────
@router.callback_query(F.data == "sm:find")
async def smm_find(call: CallbackQuery, state: FSMContext):
    await state.set_state(SmmFind.service_id)
    await call.message.answer(f"{E('search')} <b>Xizmat ID raqamini kiriting:</b>",
                              reply_markup=_kb([[B("Bekor qilish", "cross", callback_data="sm:home")]]))
    await call.answer()


@router.message(SmmFind.service_id, F.text)
async def smm_find_got(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await smm_home_msg(msg, state)
    digits = re.sub(r"\D", "", msg.text)
    svc = await get_service(int(digits)) if digits else None
    if not svc or not svc["active"] or svc["hidden"] or str(svc["type"]).lower() not in SUPPORTED_TYPES:
        return await msg.answer(f"{E('cross')} Bu ID bo'yicha xizmat topilmadi. Qaytadan kiriting:",
                                reply_markup=_kb([[B("Bekor qilish", "cross", callback_data="sm:home")]]))
    await state.clear()
    text, kb = await build_card(svc, msg.from_user.id)
    await msg.answer(text, reply_markup=kb)


# ══════════════════════════════════════════════════════════════
#  BUYURTMA BERISH
# ══════════════════════════════════════════════════════════════
STATE_TTL = 15 * 60
_locks: dict[int, asyncio.Lock] = {}


def _user_lock(uid: int) -> asyncio.Lock:
    lk = _locks.get(uid)
    if lk is None:
        lk = _locks[uid] = asyncio.Lock()
    return lk


def _cancel_row():
    return [B("Bekor qilish", "cross", callback_data="sm:x")]


@router.callback_query(F.data == "sm:x")
async def smm_cancel_flow(call: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = await home_text_kb()
    await _show(call, text, kb)
    await call.answer("Bekor qilindi")


async def _alive(state: FSMContext, msg_or_call) -> dict | None:
    d = await state.get_data()
    if not d or time.time() - d.get("ts", 0) > STATE_TTL:
        await state.clear()
        m = msg_or_call.message if isinstance(msg_or_call, CallbackQuery) else msg_or_call
        await m.answer("⌛ Buyurtma vaqti tugadi. Iltimos, xizmatni qaytadan tanlang.")
        return None
    return d


def _parse_qty(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


@router.callback_query(F.data.startswith("sm:o:"))
async def smm_order_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    svc = await get_service(int(call.data.split(":")[2]))
    if not svc or not svc["active"] or svc["hidden"] or str(svc["type"]).lower() not in SUPPORTED_TYPES:
        await call.answer("Bu xizmat hozir mavjud emas.", show_alert=True)
        return
    margin, step = await _price_cfg()
    pct, _, _ = await user_discount(call.from_user.id)
    bal = await _db().get_balance(call.from_user.id)
    typ = str(svc["type"]).lower()
    min_price = order_price(svc, margin, max(1, svc["min_q"]), pct, step)
    if bal < min_price:
        await call.message.answer(
            f"{E('cross')} <b>Balans yetarli emas</b>\n\n"
            f"{E('smm_price')} Eng kam buyurtma narxi: <b>{fmt(min_price)} so'm</b>\n"
            f"{E('wallet')} Sizning balansingiz: <b>{fmt(bal)} so'm</b>",
            reply_markup=_kb([[B("Hisob to'ldirish", "card", style="primary", callback_data="goto_topup")]]))
        return await call.answer()

    d = {"sid": svc["service_id"], "ts": time.time(), "net": svc["network"] or ""}
    await state.update_data(**d)
    if typ == "package":
        await state.set_state(SmmOrder.link)
        await call.message.answer(_ask_link_text(svc), reply_markup=_kb([_cancel_row()]))
    elif typ == "custom comments":
        await state.set_state(SmmOrder.link)
        await call.message.answer(_ask_link_text(svc), reply_markup=_kb([_cancel_row()]))
    else:
        await state.set_state(SmmOrder.qty)
        await call.message.answer(
            f"{E('smm_qty')} <b>Kerakli buyurtma miqdorini kiriting:</b>\n\n"
            f"Minimal: <b>{fmt(svc['min_q'])}</b> ta · Maksimal: <b>{fmt(svc['max_q'])}</b> ta\n"
            f"📑 Namuna: <code>{fmt(max(svc['min_q'], 1000))}</code>",
            reply_markup=_kb([_cancel_row()]))
    await call.answer()


def _ask_link_text(svc) -> str:
    net = (svc["network"] or "")
    hint = {
        "Telegram": "Kanal/guruh/post havolasi, masalan: <code>https://t.me/kanal_nomi</code>",
        "Instagram": "Profil yoki post havolasi, masalan: <code>https://instagram.com/profil</code>",
        "TikTok": "Profil yoki video havolasi",
        "YouTube": "Kanal yoki video havolasi",
    }.get(net, "Havolani (https://...) yuboring")
    return (f"{E('smm_link')} <b>Kerakli havolani kiriting</b> (https://):\n\n{hint}\n\n"
            f"{E('warn')} Havola to'g'riligiga ishonch hosil qiling va yopiq (private) bo'lmasin.")


@router.message(SmmOrder.qty, F.text)
async def smm_got_qty(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await smm_home_msg(msg, state)
    d = await _alive(state, msg)
    if d is None:
        return
    svc = await get_service(d["sid"])
    if not svc or not svc["active"]:
        await state.clear()
        return await msg.answer("Bu xizmat hozir mavjud emas.")
    qty = _parse_qty(msg.text)
    if qty is None or qty < svc["min_q"] or (svc["max_q"] and qty > svc["max_q"]):
        return await msg.answer(
            f"{E('warn')} <b>Miqdor noto'g'ri kiritildi</b>\n\n"
            f"Minimal: <b>{fmt(svc['min_q'])}</b> ta · Maksimal: <b>{fmt(svc['max_q'])}</b> ta\n\n"
            f"✍️ Boshqa miqdor kiriting:", reply_markup=_kb([_cancel_row()]))
    margin, step = await _price_cfg()
    pct, _, _ = await user_discount(msg.from_user.id)
    price = order_price(svc, margin, qty, pct, step)
    bal = await _db().get_balance(msg.from_user.id)
    if bal < price:
        return await msg.answer(
            f"{E('cross')} <b>Balans yetarli emas</b>\n\n"
            f"{E('smm_price')} Buyurtma narxi: <b>{fmt(price)} so'm</b>\n"
            f"{E('wallet')} Sizning balansingiz: <b>{fmt(bal)} so'm</b>\n\n"
            f"<i>Kamroq miqdor kiriting yoki hisobingizni to'ldiring:</i>",
            reply_markup=_kb([[B("Hisob to'ldirish", "card", style="primary", callback_data="goto_topup")], _cancel_row()]))
    await state.update_data(qty=qty, ts=time.time())
    await state.set_state(SmmOrder.link)
    await msg.answer(f"{E('check')} <b>«{fmt(qty)}» saqlandi!</b>\n\n{_ask_link_text(svc)}", reply_markup=_kb([_cancel_row()]))


_LINK_RE = re.compile(r"^(https?://\S+|(www\.)?t\.me/\S+|@[A-Za-z0-9_.]{3,64})$", re.I)


def normalize_link(text: str, network: str) -> str | None:
    t = (text or "").strip()
    if not t or len(t) > 500 or not _LINK_RE.match(t):
        return None
    if t.startswith("@"):
        u = t[1:]
        base = {"Telegram": "https://t.me/", "Instagram": "https://instagram.com/",
                "TikTok": "https://www.tiktok.com/@"}.get(network)
        return (base + u) if base else None
    if not t.lower().startswith("http"):
        t = "https://" + t
    return t


@router.message(SmmOrder.link, F.text)
async def smm_got_link(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await smm_home_msg(msg, state)
    d = await _alive(state, msg)
    if d is None:
        return
    svc = await get_service(d["sid"])
    if not svc or not svc["active"]:
        await state.clear()
        return await msg.answer("Bu xizmat hozir mavjud emas.")
    link = normalize_link(msg.text, d.get("net", ""))
    if not link:
        return await msg.answer(f"{E('warn')} <b>Havola noto'g'ri kiritildi.</b>\n\n"
                                f"Havola <code>https://</code> bilan boshlanishi kerak. Qaytadan kiriting:",
                                reply_markup=_kb([_cancel_row()]))
    typ = str(svc["type"]).lower()
    await state.update_data(link=link, ts=time.time())
    if typ == "custom comments":
        await state.set_state(SmmOrder.comments)
        return await msg.answer(
            f"💬 <b>Izohlarni yuboring</b> — har bir izoh <b>alohida qatorda</b> bo'lsin.\n"
            f"Soni: <b>{fmt(svc['min_q'])}</b> — <b>{fmt(svc['max_q'])}</b> ta.",
            reply_markup=_kb([_cancel_row()]))
    if typ == "poll":
        await state.set_state(SmmOrder.answer)
        return await msg.answer("📊 <b>Qaysi javobga ovoz berilsin?</b> Javob tartib raqamini kiriting (masalan: <code>1</code>):",
                                reply_markup=_kb([_cancel_row()]))
    await _show_confirm(msg, state)


@router.message(SmmOrder.comments, F.text)
async def smm_got_comments(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await smm_home_msg(msg, state)
    d = await _alive(state, msg)
    if d is None:
        return
    svc = await get_service(d["sid"])
    if not svc or not svc["active"]:
        await state.clear()
        return await msg.answer("Bu xizmat hozir mavjud emas.")
    lines = [ln.strip() for ln in msg.text.splitlines() if ln.strip()]
    n = len(lines)
    if n < max(1, svc["min_q"]) or (svc["max_q"] and n > svc["max_q"]):
        return await msg.answer(f"{E('warn')} Izohlar soni <b>{fmt(svc['min_q'])}</b> — <b>{fmt(svc['max_q'])}</b> orasida bo'lishi kerak "
                                f"(hozir: {n}). Qaytadan yuboring:", reply_markup=_kb([_cancel_row()]))
    await state.update_data(comments="\n".join(lines), qty=n, ts=time.time())
    await _show_confirm(msg, state)


@router.message(SmmOrder.answer, F.text)
async def smm_got_answer(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await smm_home_msg(msg, state)
    d = await _alive(state, msg)
    if d is None:
        return
    a = _parse_qty(msg.text)
    if a is None or a < 1:
        return await msg.answer("Javob tartib raqamini kiriting (masalan: 1):", reply_markup=_kb([_cancel_row()]))
    await state.update_data(answer=a, ts=time.time())
    await _show_confirm(msg, state)


async def _show_confirm(msg: Message, state: FSMContext, notice: str = ""):
    d = await state.get_data()
    svc = await get_service(d["sid"])
    margin, step = await _price_cfg()
    pct, _, _ = await user_discount(msg.chat.id)
    typ = str(svc["type"]).lower()
    qty = d.get("qty") or 0
    price = order_price(svc, margin, qty or 1, pct, step)
    bal = await _db().get_balance(msg.chat.id)
    if bal < price:
        await state.clear()
        return await msg.answer(
            f"{E('cross')} <b>Balans yetarli emas</b>\n\n{E('smm_price')} Narx: <b>{fmt(price)} so'm</b>\n"
            f"{E('wallet')} Balans: <b>{fmt(bal)} so'm</b>",
            reply_markup=_kb([[B("Hisob to'ldirish", "card", style="primary", callback_data="goto_topup")]]))
    await state.update_data(price=price, ts=time.time())
    await state.set_state(SmmOrder.confirm)
    parts = [notice] if notice else []
    parts += [
        f"📑 <b>Ma'lumotlarni tekshirib chiqing:</b>\n",
        f"{E('smm_menu')} <b>Xizmat:</b> {esc(svc['name'])}",
    ]
    if typ != "package":
        parts.append(f"{E('smm_qty')} <b>Miqdor:</b> {fmt(qty)} ta")
    parts.append(f"{E('smm_link')} <b>Havola:</b> <code>{esc(d['link'])}</code>")
    if pct:
        parts.append(f"{E('smm_discount')} <b>Chegirma:</b> {pct}%")
    parts += [
        f"{E('smm_price')} <b>Narxi:</b> {fmt(price)} so'm",
        f"{E('wallet')} <b>Balansingiz:</b> {fmt(bal)} → {fmt(bal - price)} so'm",
        "",
        "<i>To'g'ri bo'lsa «Tasdiqlash» tugmasini bosing — hisobingizdan "
        f"{fmt(price)} so'm yechiladi va buyurtma yuboriladi.</i>",
        "",
        (f"{E('warn')} <b>Buyurtma boshlangandan keyin " +
         ("bekor qilish mumkin (xizmatda «Bekor qilish» mavjud)." if svc["cancel"]
          else "bekor qilish imkoni yo'q.") + "</b>"),
    ]
    await msg.answer("\n".join(parts), reply_markup=_kb([
        [B("Tasdiqlash", "check", style="success", callback_data="sm:c")],
        _cancel_row()]))


@router.callback_query(F.data == "sm:c", SmmOrder.confirm)
async def smm_confirm(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    lk = _user_lock(uid)
    if lk.locked():
        return await call.answer("⏳ Oldingi buyurtmangiz yuborilmoqda, biroz kuting.", show_alert=True)
    async with lk:
        d = await _alive(state, call)
        if d is None:
            return await call.answer()
        await call.answer()
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await ensure_fresh(60)  # eskirgan narx/xizmat bilan buyurtma bermaslik uchun
        svc = await get_service(d["sid"])
        if not svc or not svc["active"] or svc["hidden"]:
            await state.clear()
            return await call.message.answer(f"{E('cross')} Afsuski, bu xizmat hozir mavjud emas. Boshqa xizmatni tanlang.")
        margin, step = await _price_cfg()
        pct, _, _ = await user_discount(uid)
        typ = str(svc["type"]).lower()
        qty = d.get("qty") or 0
        price = order_price(svc, margin, qty or 1, pct, step)
        if price != d.get("price") or (typ != "package" and (qty < svc["min_q"] or (svc["max_q"] and qty > svc["max_q"]))):
            if typ != "package" and (qty < svc["min_q"] or (svc["max_q"] and qty > svc["max_q"])):
                await state.clear()
                return await call.message.answer(
                    f"{E('warn')} Xizmat chegaralari o'zgargan ({fmt(svc['min_q'])}–{fmt(svc['max_q'])}). Iltimos, qaytadan buyurtma bering.")
            return await _show_confirm(call.message, state,
                                       notice=f"{E('warn')} <b>Narx yangilandi.</b> Iltimos, qayta tekshirib tasdiqlang.\n")

        params = {"service": svc["service_id"], "link": d["link"]}
        if typ == "custom comments":
            params["comments"] = d["comments"]
        elif typ != "package":
            params["quantity"] = qty
            if typ == "poll":
                params["answer_number"] = d.get("answer", 1)

        # 1) pulni atomik ushlab turamiz + buyurtma yozuvi (bitta tranzaksiya)
        async with _pool().acquire() as c:
            async with c.transaction():
                row = await c.fetchrow(
                    "UPDATE users SET balance = balance - $1 WHERE user_id=$2 AND balance >= $1 RETURNING balance",
                    price, uid)
                if row is None:
                    oid = None
                else:
                    oid = await c.fetchval("""
                        INSERT INTO smm_orders (user_id, service_id, service_name, link, quantity, qty_based,
                                                price, status, refill_ok, cancel_ok)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,'New',$8,$9) RETURNING id""",
                        uid, svc["service_id"], svc["name"], d["link"], qty, typ != "package",
                        price, bool(svc["refill"]), bool(svc["cancel"]))
                    await c.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES ($1,'smm_order',$2,$3)",
                                    uid, -price, f"smm#{oid}:{svc['service_id']}")
        if oid is None:
            await state.clear()
            return await call.message.answer(f"{E('cross')} Balans yetarli emas.")

        await call.message.answer("⏳ Buyurtma yuborilmoqda... Iltimos, kuting.")
        res = await api_call("add", params, post=True, timeout=45)

        if isinstance(res, dict) and res.get("order") and not res.get("error"):
            charge = _to_float(res.get("charge"), None) if res.get("charge") is not None else None
            async with _pool().acquire() as c:
                await c.execute("UPDATE smm_orders SET api_order=$1, charge=$2, status='Pending', updated_at=NOW() WHERE id=$3",
                                _to_int(res["order"]), charge, oid)
            await state.clear()
            await call.message.answer(
                f"{E('check')} <b>Buyurtma qabul qilindi!</b>\n\n"
                f"{E('smm_id')} Buyurtma raqami: <b>#{oid}</b>\n"
                f"{E('smm_menu')} {esc(svc['name'])}\n"
                + (f"{E('smm_qty')} Miqdor: {fmt(qty)} ta\n" if typ != "package" else "")
                + f"{E('smm_price')} Narxi: {fmt(price)} so'm\n\n"
                f"{E('bell')} Buyurtma holati o'zgarganda sizga xabar beramiz.",
                reply_markup=_kb([[B("Buyurtma holati", "smm_order", style="primary", callback_data=f"sm:ov:{oid}")],
                                  [B("Yangi buyurtma", "cart", callback_data="sm:home")]]))
            if charge is not None and charge > price:
                await alert_admin(f"loss{oid}", f"{E('warn')} <b>Zarar!</b> Buyurtma #{oid}: sotilgan {fmt(price)} so'm, "
                                                f"LockSMM narxi {charge:g}. Marjani/narxni tekshiring.", ttl=1)
            try:
                user = call.from_user
                await _ctx["notify_orders"](
                    f"{E('cart')} <b>Yangi SMM buyurtma</b>\n\n"
                    f"🆔 Buyurtma: <code>#{oid}</code> (LockSMM: <code>{esc(res['order'])}</code>)\n"
                    f"{E('profile')} Foydalanuvchi: <a href='tg://user?id={uid}'>{esc(user.full_name)}</a> (<code>{uid}</code>)\n"
                    f"{E('smm_menu')} Xizmat: <code>{svc['service_id']}</code> {esc(svc['name'][:60])}\n"
                    + (f"{E('smm_qty')} Miqdor: {fmt(qty)} ta\n" if typ != "package" else "")
                    + f"{E('smm_link')} Havola: <code>{esc(d['link'])}</code>\n"
                    f"{E('money')} Narx: {fmt(price)} so'm"
                    + (f" (LockSMM: {charge:g})" if charge is not None else ""))
            except Exception:
                pass
            return

        err = str(res.get("error") if isinstance(res, dict) else res)
        if isinstance(res, dict) and res.get("_net"):
            # Javob kelmadi — buyurtma LockSMM'da yaratilgan bo'lishi MUMKIN. Pulni qaytarmaymiz,
            # admin tekshiradi (ikki marta buyurtma/zarar bo'lmasligi uchun).
            await state.clear()
            await alert_admin(f"net{oid}", f"{E('warn')} <b>SMM buyurtma #{oid}: javob olinmadi</b>\n"
                                           f"Foydalanuvchi: <code>{uid}</code>, summa: {fmt(price)} so'm.\n"
                                           f"Sabab: <code>{esc(err)}</code>\nLockSMM panelida tekshiring.", ttl=1)
            return await call.message.answer(
                f"{E('hourglass')} <b>Buyurtma #{oid} yuborildi, tasdiqlanishi kutilmoqda.</b>\n\n"
                f"Agar bir necha daqiqada holat o'zgarmasa, adminga murojaat qiling.",
                reply_markup=_kb([[B("Buyurtmalarim", "smm_order", callback_data="sm:my:0")]]))

        # aniq xato — pulni qaytaramiz
        async with _pool().acquire() as c:
            async with c.transaction():
                await c.execute("UPDATE users SET balance = balance + $1 WHERE user_id=$2", price, uid)
                await c.execute("UPDATE smm_orders SET status='Failed', refunded=$1, notified='Failed', updated_at=NOW() WHERE id=$2",
                                price, oid)
                await c.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES ($1,'smm_refund',$2,$3)",
                                uid, price, f"smm#{oid}:failed")
        await state.clear()
        user_msg, notify, resync = friendly_error(err)
        if notify:
            await alert_admin(f"adderr:{err[:40]}", f"{E('warn')} <b>LockSMM buyurtma xatosi</b>\n<code>{esc(err)}</code>\n"
                                                    f"Xizmat: <code>{svc['service_id']}</code>")
        if resync:
            asyncio.create_task(sync_services(manual=True))
        await call.message.answer(f"{E('cross')} <b>{esc(user_msg)}</b>\n\nMablag'ingiz yechilmadi.",
                                  reply_markup=_kb([[B("Xizmatlarga qaytish", "arrow_l", callback_data="sm:home")]]))


@router.callback_query(F.data == "sm:c")
async def smm_confirm_stale(call: CallbackQuery):
    await call.answer("Bu tugma eskirgan. Iltimos, xizmatni qaytadan tanlang.", show_alert=True)


# ══════════════════════════════════════════════════════════════
#  BUYURTMALARIM
# ══════════════════════════════════════════════════════════════
STATUS_UZ = {
    "New": "🕐 Yuborilmoqda", "Pending": "🕐 Kutilmoqda", "Processing": "🔄 Bajarilmoqda",
    "In progress": "🔄 Bajarilmoqda", "Completed": "✅ Bajarildi", "Partial": "⚠️ Qisman bajarildi",
    "Canceled": "⛔ Bekor qilindi", "Failed": "❌ Xatolik (pul qaytarildi)",
}


def _st(s: str) -> str:
    return STATUS_UZ.get(s, f"❔ {s}")


@router.callback_query(F.data.startswith("sm:my:"))
async def smm_my(call: CallbackQuery, state: FSMContext):
    await state.clear()
    page = int(call.data.split(":")[2])
    uid = call.from_user.id
    async with _pool().acquire() as c:
        rows = await c.fetch("SELECT id, service_name, status, quantity, price FROM smm_orders "
                             "WHERE user_id=$1 ORDER BY id DESC OFFSET $2 LIMIT $3", uid, page * 5, 6)
        total = await c.fetchval("SELECT COUNT(*) FROM smm_orders WHERE user_id=$1", uid)
    if not rows and page == 0:
        return await _show(call, f"{E('smm_order')} Siz hali buyurtma bermagansiz.",
                           _kb([[B("Xizmatlar", "cart", callback_data="sm:home")]])) or await call.answer()
    btns = []
    for r in rows[:5]:
        btns.append([B(f"#{r['id']} · {_st(r['status'])} · {r['service_name'][:22]}", None, callback_data=f"sm:ov:{r['id']}")])
    nav = []
    if page > 0:
        nav.append(B("Oldingi", "arrow_l", callback_data=f"sm:my:{page - 1}"))
    if len(rows) > 5:
        nav.append(B("Keyingi", "arrow_r", callback_data=f"sm:my:{page + 1}"))
    if nav:
        btns.append(nav)
    btns.append([B("ID bo'yicha qidirish", "search", callback_data="sm:findo"), B("Orqaga", "arrow_l", callback_data="sm:home")])
    await _show(call, f"{E('smm_order')} <b>Buyurtmalarim</b> ({total} ta)\n\nKerakli buyurtmani tanlang:", _kb(btns))
    await call.answer()


@router.callback_query(F.data == "sm:findo")
async def smm_find_order(call: CallbackQuery, state: FSMContext):
    await state.set_state(SmmFind.order_id)
    await call.message.answer(f"{E('search')} <b>Buyurtma raqamini kiriting:</b>",
                              reply_markup=_kb([[B("Bekor qilish", "cross", callback_data="sm:my:0")]]))
    await call.answer()


@router.message(SmmFind.order_id, F.text)
async def smm_find_order_got(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await smm_home_msg(msg, state)
    digits = re.sub(r"\D", "", msg.text)
    async with _pool().acquire() as c:
        row = await c.fetchrow("SELECT * FROM smm_orders WHERE id=$1 AND user_id=$2", int(digits), msg.from_user.id) if digits else None
    if not row:
        return await msg.answer(f"{E('cross')} Buyurtma topilmadi. Qaytadan kiriting:",
                                reply_markup=_kb([[B("Bekor qilish", "cross", callback_data="sm:my:0")]]))
    await state.clear()
    text, kb = _order_card(row)
    await msg.answer(text, reply_markup=kb)


def _order_card(o) -> tuple[str, InlineKeyboardMarkup]:
    created = o["created_at"].astimezone(TASHKENT_TZ).strftime("%d.%m.%Y %H:%M") if o["created_at"] else "—"
    lines = [
        f"{E('smm_id')} <b>Buyurtma:</b> #{o['id']}",
        f"{E('chart')} <b>Holat:</b> {_st(o['status'])}",
        f"{E('smm_menu')} <b>Xizmat:</b> {esc(o['service_name'])}",
        f"{E('smm_link')} <b>Havola:</b> <code>{esc(o['link'])}</code>",
    ]
    if o["qty_based"]:
        lines.append(f"{E('smm_qty')} <b>Miqdor:</b> {fmt(o['quantity'])} ta")
        if o["remains"] is not None and o["status"] in ("Processing", "In progress", "Completed", "Partial"):
            done = max(0, (o["quantity"] or 0) - (o["remains"] or 0))
            lines.append(f"✅ <b>Bajarildi:</b> {fmt(done)} ta · Qoldi: {fmt(o['remains'])} ta")
    lines.append(f"{E('smm_price')} <b>Narxi:</b> {fmt(o['price'])} so'm")
    if o["refunded"]:
        lines.append(f"↩️ <b>Qaytarildi:</b> {fmt(o['refunded'])} so'm")
    lines.append(f"{E('clock')} <b>Vaqti:</b> {created}")
    rows = []
    if o["status"] not in FINAL_STATUSES:
        rows.append([B("Holatni yangilash", "refresh", style="primary", callback_data=f"sm:ur:{o['id']}")])
    if o["api_order"] and o["refill_ok"] and o["status"] in ("Completed", "Partial"):
        rows.append([B("Qayta tiklash (refill)", "smm_refill", callback_data=f"sm:rf:{o['id']}")])
    if o["api_order"] and o["cancel_ok"] and o["status"] in ("Pending", "Processing", "In progress"):
        rows.append([B("Buyurtmani bekor qilish", "smm_cancel", style="danger", callback_data=f"sm:cn:{o['id']}")])
    rows.append([B("Orqaga", "arrow_l", callback_data="sm:my:0"), B("Yangi buyurtma", "cart", callback_data="sm:home")])
    return "\n".join(lines), _kb(rows)


async def _get_own_order(oid: int, uid: int):
    async with _pool().acquire() as c:
        return await c.fetchrow("SELECT * FROM smm_orders WHERE id=$1 AND user_id=$2", oid, uid)


@router.callback_query(F.data.startswith("sm:ov:"))
async def smm_order_view(call: CallbackQuery):
    o = await _get_own_order(int(call.data.split(":")[2]), call.from_user.id)
    if not o:
        return await call.answer("Buyurtma topilmadi.", show_alert=True)
    text, kb = _order_card(o)
    await _show(call, text, kb)
    await call.answer()


@router.callback_query(F.data.startswith("sm:ur:"))
async def smm_order_refresh(call: CallbackQuery):
    oid = int(call.data.split(":")[2])
    o = await _get_own_order(oid, call.from_user.id)
    if not o:
        return await call.answer("Buyurtma topilmadi.", show_alert=True)
    if o["api_order"]:
        await poll_orders(only_ids=[o["api_order"]])
        o = await _get_own_order(oid, call.from_user.id)
    text, kb = _order_card(o)
    await _show(call, text, kb)
    await call.answer("Yangilandi")


@router.callback_query(F.data.startswith("sm:rf:"))
async def smm_order_refill(call: CallbackQuery):
    o = await _get_own_order(int(call.data.split(":")[2]), call.from_user.id)
    if not o or not o["api_order"] or not o["refill_ok"]:
        return await call.answer("Bu buyurtma uchun qayta tiklash mavjud emas.", show_alert=True)
    if o["refill_id"]:
        return await call.answer("Bu buyurtma uchun qayta tiklash so'rovi allaqachon yuborilgan.", show_alert=True)
    res = await api_call("refill", {"order": o["api_order"]})
    if isinstance(res, dict) and res.get("refill") and not res.get("error"):
        async with _pool().acquire() as c:
            await c.execute("UPDATE smm_orders SET refill_id=$1 WHERE id=$2", str(res["refill"]), o["id"])
        return await call.answer("✅ Qayta tiklash so'rovi yuborildi.", show_alert=True)
    err = str(res.get("error") if isinstance(res, dict) else res)[:150]
    await call.answer(f"❌ Hozircha qayta tiklash mumkin emas: {err}", show_alert=True)


@router.callback_query(F.data.startswith("sm:cn:"))
async def smm_order_cancel_ask(call: CallbackQuery):
    oid = int(call.data.split(":")[2])
    await _show(call, f"{E('warn')} <b>#{oid} buyurtmani bekor qilmoqchimisiz?</b>\n\n"
                      f"Bekor qilish bajarilgan qismga ta'sir qilmaydi. Qaytariladigan summa avtomatik hisobingizga tushadi.",
                _kb([[B("Ha, bekor qilish", "check", style="danger", callback_data=f"sm:cy:{oid}")],
                     [B("Yo'q", "arrow_l", callback_data=f"sm:ov:{oid}")]]))
    await call.answer()


@router.callback_query(F.data.startswith("sm:cy:"))
async def smm_order_cancel_do(call: CallbackQuery):
    oid = int(call.data.split(":")[2])
    o = await _get_own_order(oid, call.from_user.id)
    if not o or not o["api_order"] or not o["cancel_ok"]:
        return await call.answer("Bu buyurtmani bekor qilib bo'lmaydi.", show_alert=True)
    res = await api_call("cancel", {"orders": o["api_order"]})
    ok, err = False, ""
    if isinstance(res, list) and res:
        c0 = res[0].get("cancel") if isinstance(res[0], dict) else None
        if isinstance(c0, dict) and c0.get("error"):
            err = str(c0["error"])
        elif c0:
            ok = True
    elif isinstance(res, dict) and res.get("error"):
        err = str(res["error"])
    if not ok:
        return await call.answer(f"❌ Bekor qilib bo'lmadi: {err[:150] or 'xizmat ruxsat bermadi'}", show_alert=True)
    await poll_orders(only_ids=[o["api_order"]])
    o = await _get_own_order(oid, call.from_user.id)
    text, kb = _order_card(o)
    await _show(call, text, kb)
    await call.answer("✅ Bekor qilish so'rovi yuborildi.", show_alert=True)


# ══════════════════════════════════════════════════════════════
#  BUYURTMA HOLATINI KUZATISH (fon)
# ══════════════════════════════════════════════════════════════
def norm_status(s) -> str:
    t = str(s or "").strip().lower().replace("_", " ")
    return {
        "completed": "Completed", "complete": "Completed", "success": "Completed", "done": "Completed",
        "partial": "Partial",
        "canceled": "Canceled", "cancelled": "Canceled", "refunded": "Canceled", "fail": "Canceled",
        "failed": "Canceled", "error": "Canceled",
        "in progress": "In progress", "inprogress": "In progress", "running": "In progress",
        "processing": "Processing", "pending": "Pending",
    }.get(t, str(s or "Pending").strip().title())


async def _credit_and_close(oid: int, uid: int, status: str, refund: int, note: str) -> bool:
    """Yakuniy holatni va qaytariladigan pulni BIR MARTA qo'llaydi (qayta ishlasa ikki marta qaytmaydi)."""
    async with _pool().acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                "UPDATE smm_orders SET status=$1, refunded=$2, notified=$1, finished_at=NOW(), updated_at=NOW() "
                "WHERE id=$3 AND notified NOT IN ('Canceled','Partial','Completed') RETURNING id", status, refund, oid)
            if not row:
                return False
            if refund > 0:
                await c.execute("UPDATE users SET balance = balance + $1 WHERE user_id=$2", refund, uid)
                await c.execute("INSERT INTO transactions (user_id, type, amount, note) VALUES ($1,'smm_refund',$2,$3)",
                                uid, refund, f"smm#{oid}:{note}")
    return True


async def _apply_status(o, item: dict):
    oid, uid = o["id"], o["user_id"]
    st = norm_status(item.get("status"))
    start = item.get("start_count")
    remains = item.get("remains")
    start_i = _to_int(start, None) if start not in (None, "") else None
    rem_i = _to_int(remains, None) if remains not in (None, "") else None
    async with _pool().acquire() as c:
        await c.execute("UPDATE smm_orders SET start_count=COALESCE($1,start_count), remains=COALESCE($2,remains), "
                        "updated_at=NOW(), err_count=0 WHERE id=$3", start_i, rem_i, oid)

    sname = esc(o["service_name"])
    again = _kb([[B("Buyurtma holati", "smm_order", callback_data=f"sm:ov:{oid}")]])
    if st == "Completed":
        if await _credit_and_close(oid, uid, "Completed", 0, "done"):
            await _send(uid, f"{E('check')} <b>#{oid} raqamli buyurtmangiz bajarildi!</b>\n\n{E('smm_menu')} {sname}\n\n"
                             f"{E('party')} Xizmatlarimizdan foydalanganingiz uchun rahmat!",
                        _kb([[B("Buyurtma holati", "smm_order", callback_data=f"sm:ov:{oid}")],
                             [B("Yangi buyurtma", "cart", style="primary", callback_data="sm:home")]]))
    elif st == "Canceled":
        if await _credit_and_close(oid, uid, "Canceled", int(o["price"]), "canceled"):
            await _send(uid, f"{E('cross')} <b>#{oid} raqamli buyurtmangiz bekor qilindi.</b>\n\n{E('smm_menu')} {sname}\n\n"
                             f"{E('money')} Hisobingizga <b>{fmt(o['price'])} so'm</b> qaytarildi.", again)
    elif st == "Partial":
        refund = 0
        if o["qty_based"] and o["quantity"] and rem_i:
            refund = min(int(o["price"]), int(o["price"] * rem_i // o["quantity"]))
        if await _credit_and_close(oid, uid, "Partial", refund, "partial"):
            done = max(0, (o["quantity"] or 0) - (rem_i or 0))
            await _send(uid, f"{E('warn')} <b>#{oid} raqamli buyurtma qisman bajarildi.</b>\n\n{E('smm_menu')} {sname}\n"
                             + (f"✅ Bajarildi: {fmt(done)} ta · Bajarilmadi: {fmt(rem_i or 0)} ta\n" if o["qty_based"] else "")
                             + (f"{E('money')} Bajarilmagan qism uchun <b>{fmt(refund)} so'm</b> hisobingizga qaytarildi." if refund
                                else ""), again)
            if not o["qty_based"] and rem_i:
                await alert_admin(f"partialpkg{oid}", f"{E('warn')} Paket buyurtma #{oid} Partial bo'ldi — qo'lda ko'rib chiqing.", ttl=1)
    elif st in ("In progress", "Processing", "Pending"):
        async with _pool().acquire() as c:
            changed = await c.fetchval(
                "UPDATE smm_orders SET status=$1, updated_at=NOW() WHERE id=$2 AND status<>$1 RETURNING id", st, oid)
            if st in ("In progress", "Processing") and o["notified"] == "":
                got = await c.fetchval("UPDATE smm_orders SET notified='started' WHERE id=$1 AND notified='' RETURNING id", oid)
                if got:
                    await _send(uid, f"{E('rocket')} <b>#{oid} raqamli buyurtmangiz bajarilishni boshladi!</b>\n\n{E('smm_menu')} {sname}", again)
    else:
        async with _pool().acquire() as c:
            await c.execute("UPDATE smm_orders SET status=$1, updated_at=NOW() WHERE id=$2", st, oid)


async def poll_orders(only_ids: list[int] | None = None):
    async with _pool().acquire() as c:
        if only_ids:
            rows = await c.fetch("SELECT * FROM smm_orders WHERE api_order = ANY($1::bigint[])", only_ids)
        else:
            rows = await c.fetch(
                "SELECT * FROM smm_orders WHERE api_order IS NOT NULL AND status <> ALL($1::text[]) "
                "ORDER BY id LIMIT 600", list(FINAL_STATUSES))
    if not rows:
        return
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        ids = [str(r["api_order"]) for r in chunk]
        res = await api_call("status", {"orders": ",".join(ids)})
        if not isinstance(res, dict):
            continue
        single = len(ids) == 1 and "status" in res
        if res.get("error") and not single and not any(k in res for k in ids):
            continue  # umumiy xato (tarmoq/kalit) — keyingi siklda urinamiz
        for r in chunk:
            item = res if single else res.get(str(r["api_order"]))
            if not isinstance(item, dict):
                continue
            if item.get("error"):
                async with _pool().acquire() as c:
                    n = await c.fetchval("UPDATE smm_orders SET err_count=err_count+1 WHERE id=$1 RETURNING err_count", r["id"])
                if n == 5:
                    await alert_admin(f"ordunk{r['id']}", f"{E('warn')} Buyurtma #{r['id']} (LockSMM {r['api_order']}) holatini olib bo'lmadi: "
                                                           f"<code>{esc(item['error'])}</code>", ttl=1)
                continue
            try:
                await _apply_status(r, item)
            except Exception as e:
                print(f"⚠️ SMM holatni qo'llashda xatolik (#{r['id']}): {e}")


async def status_loop():
    await asyncio.sleep(10)
    while True:
        try:
            await poll_orders()
            async with _pool().acquire() as c:
                stuck = await c.fetch("SELECT id, user_id, price FROM smm_orders "
                                      "WHERE status='New' AND api_order IS NULL AND created_at < NOW() - INTERVAL '3 minutes' "
                                      "AND notified=''")
            for r in stuck:
                await alert_admin(f"stuck{r['id']}", f"{E('warn')} SMM buyurtma #{r['id']} (user <code>{r['user_id']}</code>, "
                                                     f"{fmt(r['price'])} so'm) LockSMM'ga yuborilganini tasdiqlab bo'lmadi. Panelda tekshiring.",
                                  ttl=3600)
        except Exception as e:
            print(f"⚠️ SMM holat kuzatuvchisi xatoligi: {e}")
        await asyncio.sleep(max(20, await _gs("smm_status_interval", 45)))


# ══════════════════════════════════════════════════════════════
#  ADMIN
# ══════════════════════════════════════════════════════════════
class SmmAdmin(StatesGroup):
    margin = State()
    interval = State()
    tiers = State()
    service_id = State()
    price = State()
    desc = State()
    intro = State()
    rules = State()


def _is_admin(obj) -> bool:
    u = getattr(obj, "from_user", None)
    return bool(u and u.id == _ctx.get("admin_id"))


admin_router.message.filter(_is_admin)
admin_router.callback_query.filter(_is_admin)


async def admin_home_text_kb():
    bal = await api_call("balance")
    bal_txt = (f"{bal.get('balance')} {bal.get('currency', '')}" if isinstance(bal, dict) and "balance" in bal
               else f"xato: {esc(bal.get('error') if isinstance(bal, dict) else bal)}")
    margin, _ = await _price_cfg()
    interval = max(60, await _gs("smm_sync_interval", 180))
    async with _pool().acquire() as c:
        n_active = await c.fetchval("SELECT COUNT(*) FROM smm_services WHERE active AND node_id IS NOT NULL")
        n_unsup = await c.fetchval("SELECT COUNT(*) FROM smm_services WHERE active AND node_id IS NULL")
        n_rev = await c.fetchval("SELECT COUNT(*) FROM smm_cat_map WHERE review AND NOT manual")
        today = await c.fetchrow("SELECT COUNT(*) AS n, COALESCE(SUM(price-refunded),0) AS s FROM smm_orders "
                                 "WHERE created_at >= date_trunc('day', NOW() AT TIME ZONE 'Asia/Tashkent') AT TIME ZONE 'Asia/Tashkent' "
                                 "AND status NOT IN ('Failed')")
    last = _last_sync_info.get("at", "—")
    text = (
        f"{E('admin')} <b>SMM sozlamalari (LockSMM)</b>\n\n"
        f"{E('money')} LockSMM balansi: <b>{bal_txt}</b>\n"
        f"{E('smm_menu')} Faol xizmatlar: <b>{n_active}</b> (menyuga chiqmaydigan: {n_unsup})\n"
        f"🗂 Tekshirish kerak kategoriyalar: <b>{n_rev}</b>\n"
        f"{E('smm_price')} Marja: <b>{margin:g}%</b>\n"
        f"{E('refresh')} Sinxronlash: har <b>{interval}</b> soniyada (oxirgisi: {last})\n"
        f"{E('smm_order')} Bugun: <b>{today['n']}</b> ta buyurtma, <b>{fmt(today['s'])}</b> so'm"
    )
    kb = _kb([
        [B("Hozir sinxronlash", "refresh", style="primary", callback_data="sa:sync")],
        [B("Bo'limlarni sozlash", "cat_other", callback_data="sa:cats:r:0"),
         B("Xizmat (ID bo'yicha)", "search", callback_data="sa:svc")],
        [B("Marja", "smm_price", callback_data="sa:margin"), B("Sinxron oralig'i", "clock", callback_data="sa:interval")],
        [B("Chegirmalar", "smm_discount", callback_data="sa:tiers"), B("Statistika", "chart", callback_data="sa:stats")],
        [B("Bosh sahifa matni", "announce", callback_data="sa:intro"), B("Qoidalar matni", "smm_rules", callback_data="sa:rules")],
        [B("Admin panel", "arrow_l", callback_data="adm_refresh")],
    ])
    return text, kb


@admin_router.callback_query(F.data == "sa:home")
async def sa_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = await admin_home_text_kb()
    await _show(call, text, kb)
    await call.answer()


@admin_router.callback_query(F.data == "smm_admin")
async def sa_entry(call: CallbackQuery, state: FSMContext):
    await sa_home(call, state)


@admin_router.callback_query(F.data == "sa:sync")
async def sa_sync(call: CallbackQuery):
    await call.answer("⏳ Sinxronlanmoqda...")
    res = await sync_services(manual=True)
    if res.get("ok"):
        await call.message.answer(
            f"{E('check')} Sinxronlandi: jami {res['total']}, yangi {res['new']}, o'chirilgan {res['removed']}, "
            f"narxi o'zgargan {res['price']}, yangi kategoriya {res['new_cats']}.")
    else:
        await call.message.answer(f"{E('cross')} Xatolik: <code>{esc(res.get('error'))}</code>")
    text, kb = await admin_home_text_kb()
    await _show(call, text, kb)


# ── kategoriyalar ─────────────────────────────────────────────
@admin_router.callback_query(F.data.startswith("sa:cats:"))
async def sa_cats(call: CallbackQuery):
    _, _, flt, page = call.data.split(":")
    page = int(page)
    where = "WHERE m.review AND NOT m.manual" if flt == "r" else ""
    async with _pool().acquire() as c:
        rows = await c.fetch(f"""
            SELECT m.id, m.category_raw, m.network, m.catalog, m.manual,
                   (SELECT COUNT(*) FROM smm_services s WHERE s.category_raw=m.category_raw AND s.active) AS n
            FROM smm_cat_map m {where} ORDER BY m.network, m.catalog NULLS FIRST, m.category_raw
            OFFSET $1 LIMIT $2""", page * 8, 9)
    rows = [r for r in rows]
    btns = []
    for r in rows[:8]:
        if r["n"] == 0:
            continue
        place = r["network"] + (f" › {r['catalog']}" if r["catalog"] else "")
        btns.append([B(f"{r['category_raw'][:28]} ({r['n']}) → {place[:24]}", None, callback_data=f"sa:cat:{r['id']}")])
    nav = []
    if page > 0:
        nav.append(B("Oldingi", "arrow_l", callback_data=f"sa:cats:{flt}:{page - 1}"))
    if len(rows) > 8:
        nav.append(B("Keyingi", "arrow_r", callback_data=f"sa:cats:{flt}:{page + 1}"))
    if nav:
        btns.append(nav)
    other = "a" if flt == "r" else "r"
    btns.append([B("Hammasini ko'rsatish" if flt == "r" else "Faqat tekshirilishi kerak", "search",
                   callback_data=f"sa:cats:{other}:0")])
    btns.append([B("Orqaga", "arrow_l", callback_data="sa:home")])
    title = "Tekshirilishi kerak kategoriyalar" if flt == "r" else "Barcha kategoriyalar"
    body = "" if btns[:-2] else "\n\n✅ Hozircha tekshirish kerak bo'lgan kategoriya yo'q."
    await _show(call, f"🗂 <b>{title}</b>\n\nKategoriyani bosib, qaysi tarmoq/katalogga tegishli ekanini belgilang.{body}", _kb(btns))
    await call.answer()


@admin_router.callback_query(F.data.startswith("sa:cat:"))
async def sa_cat(call: CallbackQuery):
    cid = int(call.data.split(":")[2])
    async with _pool().acquire() as c:
        m = await c.fetchrow("SELECT * FROM smm_cat_map WHERE id=$1", cid)
        names = await c.fetch("SELECT name FROM smm_services WHERE category_raw=$1 AND active ORDER BY pos LIMIT 4", m["category_raw"] if m else "")
    if not m:
        return await call.answer("Topilmadi", show_alert=True)
    text = (f"🗂 <b>{esc(m['category_raw'])}</b>\n\n"
            f"Hozir: <b>{esc(m['network'])}</b>" + (f" › <b>{esc(m['catalog'])}</b>" if m["catalog"] else "") +
            (" (qo'lda belgilangan)" if m["manual"] else " (avtomatik)") + "\n\nNamunalar:\n" +
            "\n".join(f"• {esc(r['name'][:60])}" for r in names) + "\n\n<b>Tarmoqni tanlang:</b>")
    btns, pair = [], []
    for i, n in enumerate(NETWORKS):
        pair.append(B(n, NETWORK_SLUGS.get(n), callback_data=f"sa:cn:{cid}:{i}"))
        if len(pair) == 2:
            btns.append(pair)
            pair = []
    if pair:
        btns.append(pair)
    btns.append([B("Avtomatikka qaytarish", "refresh", callback_data=f"sa:ca:{cid}")])
    btns.append([B("Orqaga", "arrow_l", callback_data="sa:cats:r:0")])
    await _show(call, text, _kb(btns))
    await call.answer()


@admin_router.callback_query(F.data.startswith("sa:cn:"))
async def sa_cat_net(call: CallbackQuery):
    _, _, cid, ni = call.data.split(":")
    cid, ni = int(cid), int(ni)
    net = NETWORKS[ni]
    if net not in CATALOG_NETWORKS:
        return await _sa_cat_save(call, cid, net, None)
    btns = [[B(n, CATALOG_SLUGS.get(n), callback_data=f"sa:cc:{cid}:{ni}:{i}")] for i, n in enumerate(CATALOGS)]
    btns.append([B("Orqaga", "arrow_l", callback_data=f"sa:cat:{cid}")])
    await _show(call, f"<b>{esc(net)}</b> — katalogni tanlang:", _kb(btns))
    await call.answer()


@admin_router.callback_query(F.data.startswith("sa:cc:"))
async def sa_cat_catalog(call: CallbackQuery):
    _, _, cid, ni, ci = call.data.split(":")
    await _sa_cat_save(call, int(cid), NETWORKS[int(ni)], CATALOGS[int(ci)])


async def _sa_cat_save(call: CallbackQuery, cid: int, net: str, catalog: str | None):
    async with _pool().acquire() as c:
        await c.execute("UPDATE smm_cat_map SET network=$1, catalog=$2, manual=TRUE, review=FALSE WHERE id=$3", net, catalog, cid)
    await rebuild_nodes()
    await load_menu()
    await call.answer("✅ Saqlandi", show_alert=False)
    call.data = "sa:cats:r:0"
    await sa_cats(call)


@admin_router.callback_query(F.data.startswith("sa:ca:"))
async def sa_cat_auto(call: CallbackQuery):
    cid = int(call.data.split(":")[2])
    async with _pool().acquire() as c:
        m = await c.fetchrow("SELECT category_raw FROM smm_cat_map WHERE id=$1", cid)
        if m:
            names = [r["name"] for r in await c.fetch("SELECT name FROM smm_services WHERE category_raw=$1 ORDER BY pos LIMIT 5", m["category_raw"])]
            net, catalog, review = classify(m["category_raw"], names)
            await c.execute("UPDATE smm_cat_map SET network=$1, catalog=$2, review=$3, manual=FALSE WHERE id=$4", net, catalog, review, cid)
    await rebuild_nodes()
    await load_menu()
    await call.answer("♻️ Avtomatik qoidaga qaytarildi")
    call.data = f"sa:cat:{cid}"
    await sa_cat(call)


# ── sozlamalar (marja, interval, chegirma, matnlar) ───────────
async def _ask(call: CallbackQuery, state: FSMContext, st, text: str):
    await state.set_state(st)
    await call.message.answer(text + "\n\n<i>Bekor qilish uchun /cancel yuboring.</i>")
    await call.answer()


@admin_router.callback_query(F.data == "sa:margin")
async def sa_margin(call: CallbackQuery, state: FSMContext):
    m, _ = await _price_cfg()
    await _ask(call, state, SmmAdmin.margin, f"{E('smm_price')} Joriy marja: <b>{m:g}%</b>\nYangi marjani kiriting (masalan: <code>30</code>):")


@admin_router.message(SmmAdmin.margin, F.text)
async def sa_margin_got(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await msg.answer("Bekor qilindi.")
    v = _to_float(msg.text.replace("%", ""), -1)
    if v < 0 or v > 1000:
        return await msg.answer("0 dan 1000 gacha son kiriting:")
    await _db().set_setting("smm_margin", str(v))
    await state.clear()
    await msg.answer(f"{E('check')} Marja <b>{v:g}%</b> qilib saqlandi.")
    text, kb = await admin_home_text_kb()
    await msg.answer(text, reply_markup=kb)


@admin_router.callback_query(F.data == "sa:interval")
async def sa_interval(call: CallbackQuery, state: FSMContext):
    await _ask(call, state, SmmAdmin.interval,
               f"{E('refresh')} Xizmatlarni necha soniyada bir tekshirib tursin? (kamida 60, tavsiya: 180)")


@admin_router.message(SmmAdmin.interval, F.text)
async def sa_interval_got(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await msg.answer("Bekor qilindi.")
    v = _parse_qty(msg.text)
    if v is None or v < 60:
        return await msg.answer("Kamida 60 soniya kiriting:")
    await _db().set_setting("smm_sync_interval", str(v))
    await state.clear()
    await msg.answer(f"{E('check')} Saqlandi: har <b>{v}</b> soniyada.")


@admin_router.callback_query(F.data == "sa:tiers")
async def sa_tiers(call: CallbackQuery, state: FSMContext):
    cur = await _gs("smm_discount_tiers", DEFAULT_TIERS)
    await _ask(call, state, SmmAdmin.tiers,
               f"{E('smm_discount')} <b>Chegirma bosqichlari</b> (jami SMM xaridi : chegirma %)\n"
               f"Joriy: <code>{esc(cur)}</code>\n\nYangisini shu formatda yuboring, masalan:\n"
               f"<code>0:0,200000:1,1000000:2,3000000:3,10000000:5</code>\nChegirmani o'chirish uchun: <code>0:0</code>")


@admin_router.message(SmmAdmin.tiers, F.text)
async def sa_tiers_got(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await msg.answer("Bekor qilindi.")
    raw = msg.text.strip().replace(" ", "")
    t = _tiers(raw)
    if not raw or ":" not in raw or any(p < 0 or p > 50 for _, p in t):
        return await msg.answer("Format noto'g'ri yoki foiz 50 dan oshdi. Qaytadan yuboring:")
    await _db().set_setting("smm_discount_tiers", ",".join(f"{a}:{b}" for a, b in t))
    await state.clear()
    await msg.answer(f"{E('check')} Chegirma bosqichlari saqlandi.")


@admin_router.callback_query(F.data == "sa:intro")
async def sa_intro(call: CallbackQuery, state: FSMContext):
    await _ask(call, state, SmmAdmin.intro, "Xizmatlar bosh sahifasi matnini yuboring (HTML mumkin). Standart matnga qaytarish: <code>-</code>")


@admin_router.message(SmmAdmin.intro, F.text)
async def sa_intro_got(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await msg.answer("Bekor qilindi.")
    await _db().set_setting("smm_intro_text", "" if msg.text.strip() == "-" else msg.html_text)
    await state.clear()
    await msg.answer(f"{E('check')} Saqlandi.")


@admin_router.callback_query(F.data == "sa:rules")
async def sa_rules(call: CallbackQuery, state: FSMContext):
    await _ask(call, state, SmmAdmin.rules, "Qoidalar matnini yuboring (oddiy matn). Standart matnga qaytarish: <code>-</code>")


@admin_router.message(SmmAdmin.rules, F.text)
async def sa_rules_got(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await msg.answer("Bekor qilindi.")
    await _db().set_setting("smm_rules_text", "" if msg.text.strip() == "-" else msg.text)
    await state.clear()
    await msg.answer(f"{E('check')} Saqlandi.")


# ── bitta xizmat bilan ishlash ────────────────────────────────
@admin_router.callback_query(F.data == "sa:svc")
async def sa_svc(call: CallbackQuery, state: FSMContext):
    await _ask(call, state, SmmAdmin.service_id, f"{E('search')} Xizmat ID raqamini kiriting:")


async def _sa_svc_card(target, sid: int):
    svc = await get_service(sid)
    if not svc:
        return await _show(target, "Xizmat topilmadi.")
    margin, step = await _price_cfg()
    text = (f"{E('smm_menu')} <b>{esc(svc['name'])}</b>\n\n"
            f"ID: <code>{sid}</code> · tur: {esc(svc['type'])}\n"
            f"Kategoriya: {esc(svc['category_raw'])}\n"
            f"LockSMM narxi (1000): <b>{svc['rate']:g}</b>\n"
            f"Bizning narx (1000): <b>{fmt(unit_price(svc, margin, step))}</b>"
            + (" (qo'lda belgilangan)" if svc["price_override"] else "") + "\n"
            f"Holat: {'🟢 faol' if svc['active'] else '🔴 LockSMM da yo‘q'}"
            f"{' · 🙈 yashirilgan' if svc['hidden'] else ''}"
            f"{'' if str(svc['type']).lower() in SUPPORTED_TYPES else ' · ⚠️ turi qo‘llab-quvvatlanmaydi'}\n"
            f"Tavsif: {'bor' if svc['description_override'] else 'yo‘q'}")
    kb = _kb([
        [B("Ko'rsatish" if svc["hidden"] else "Yashirish", "search", callback_data=f"sa:hide:{sid}"),
         B("Narx belgilash", "smm_price", callback_data=f"sa:price:{sid}")],
        [B("Tavsif yozish", "guide", callback_data=f"sa:desc:{sid}")],
        [B("Orqaga", "arrow_l", callback_data="sa:home")],
    ])
    await _show(target, text, kb)


@admin_router.message(SmmAdmin.service_id, F.text)
async def sa_svc_got(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await msg.answer("Bekor qilindi.")
    sid = _parse_qty(msg.text)
    if sid is None or not await get_service(sid):
        return await msg.answer("Bunday xizmat topilmadi. Qaytadan kiriting:")
    await state.clear()
    await _sa_svc_card(msg, sid)


@admin_router.callback_query(F.data.startswith("sa:hide:"))
async def sa_hide(call: CallbackQuery):
    sid = int(call.data.split(":")[2])
    async with _pool().acquire() as c:
        await c.execute("UPDATE smm_services SET hidden = NOT hidden WHERE service_id=$1", sid)
    await load_menu()
    await call.answer("Bajarildi")
    await _sa_svc_card(call, sid)


@admin_router.callback_query(F.data.startswith("sa:price:"))
async def sa_price(call: CallbackQuery, state: FSMContext):
    sid = int(call.data.split(":")[2])
    await state.update_data(sid=sid)
    await _ask(call, state, SmmAdmin.price,
               f"{E('smm_price')} Xizmat <code>{sid}</code> uchun 1000 ta narxini (so'mda) kiriting.\n"
               f"Avtomatik narxga (marja bo'yicha) qaytarish: <code>0</code>")


@admin_router.message(SmmAdmin.price, F.text)
async def sa_price_got(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await msg.answer("Bekor qilindi.")
    v = _parse_qty(msg.text)
    if v is None:
        return await msg.answer("Son kiriting:")
    sid = (await state.get_data())["sid"]
    async with _pool().acquire() as c:
        await c.execute("UPDATE smm_services SET price_override=$1 WHERE service_id=$2", v or None, sid)
    await state.clear()
    await _sa_svc_card(msg, sid)


@admin_router.callback_query(F.data.startswith("sa:desc:"))
async def sa_desc(call: CallbackQuery, state: FSMContext):
    sid = int(call.data.split(":")[2])
    await state.update_data(sid=sid)
    await _ask(call, state, SmmAdmin.desc,
               f"Xizmat <code>{sid}</code> uchun tavsif/qoidalar matnini yuboring (oddiy matn, kartada ochiladigan blok bo'lib chiqadi).\n"
               f"O'chirish: <code>-</code>")


@admin_router.message(SmmAdmin.desc, F.text)
async def sa_desc_got(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        await state.clear()
        return await msg.answer("Bekor qilindi.")
    sid = (await state.get_data())["sid"]
    async with _pool().acquire() as c:
        await c.execute("UPDATE smm_services SET description_override=$1 WHERE service_id=$2",
                        None if msg.text.strip() == "-" else msg.text.strip()[:2500], sid)
    await state.clear()
    await _sa_svc_card(msg, sid)


@admin_router.callback_query(F.data == "sa:stats")
async def sa_stats(call: CallbackQuery):
    async with _pool().acquire() as c:
        by = await c.fetch("SELECT status, COUNT(*) n FROM smm_orders GROUP BY status ORDER BY n DESC")
        tot = await c.fetchrow("""
            SELECT COALESCE(SUM(price-refunded),0) AS revenue,
                   COALESCE(SUM(CASE WHEN charge IS NOT NULL THEN charge END),0) AS cost,
                   COUNT(DISTINCT user_id) AS users
            FROM smm_orders WHERE status NOT IN ('Failed','New')""")
    lines = [f"{E('chart')} <b>SMM statistikasi</b>\n"]
    lines += [f"{_st(r['status'])}: <b>{r['n']}</b>" for r in by] or ["Hali buyurtma yo'q."]
    lines.append(f"\n{E('referral')} Xaridorlar: <b>{tot['users']}</b>")
    lines.append(f"{E('money')} Tushum (qaytarilganlarsiz): <b>{fmt(tot['revenue'])}</b> so'm")
    lines.append(f"LockSMM'ga to'langan (taxminan): <b>{fmt(tot['cost'])}</b> so'm")
    await _show(call, "\n".join(lines), _kb([[B("Orqaga", "arrow_l", callback_data="sa:home")]]))
    await call.answer()


# ══════════════════════════════════════════════════════════════
#  ISHGA TUSHIRISH
# ══════════════════════════════════════════════════════════════
async def init():
    """db.init() dan keyin chaqiring."""
    await _create_tables()
    await load_menu()
    if not LOCKSMM_API_KEY:
        print("ℹ️ LOCKSMM_API_KEY sozlanmagan — SMM xizmatlari yangilanmaydi")


def start_tasks():
    """Fon sikllarini ishga tushiradi (bot.py main() ichida chaqiring)."""
    asyncio.create_task(sync_loop())
    asyncio.create_task(status_loop())
