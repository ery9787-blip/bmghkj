from __future__ import annotations  # Python 3.9 va undan eskilarida ham
                                     # "str | None" kabi zamonaviy type hint
                                     # sintaksisi xato bermasligi uchun kerak.
import asyncio
import os
import re
import random
import secrets
import aiohttp
from aiohttp import web
from datetime import datetime, date, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, types, F, Router, BaseMiddleware
from aiogram.filters import CommandStart, StateFilter
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    Message, CallbackQuery, TelegramObject,
    ReplyKeyboardRemove, ChatMemberUpdated,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
import humo_listener
import tolov_api

from database import Database

# ─── SOZLAMALAR ────────────────────────────────────────────────
API_TOKEN        = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_ID         = int(os.getenv("ADMIN_ID", "123456789"))
CHANNEL_ID       = int(os.getenv("CHANNEL_ID", "-1001234567890"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "yourchannel")
CARD_NUMBER      = os.getenv("CARD_NUMBER", "8600000000000000")
CARD_OWNER       = os.getenv("CARD_OWNER", "Familiya I")
TGLION_API_KEY   = os.getenv("TGLION_API_KEY", "YOUR_TGLION_KEY")
TGLION_YOUR_ID   = os.getenv("TGLION_YOUR_ID", "YOUR_TGLION_ID")
TGLION_BASE      = "https://TG-Lion.net"
ORDERS_CHANNEL   = os.getenv("ORDERS_CHANNEL", "-1001234567890")

BLOCKED_COUNTRIES = {"CO", "NG", "ZW"}
TASHKENT_TZ = ZoneInfo("Asia/Tashkent")

# ─── BOT SOZLASH ───────────────────────────────────────────────
bot          = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="html"))
storage      = MemoryStorage()
dp           = Dispatcher(storage=storage)
router       = Router()
admin_router = Router()

db = Database()

# ─── HOLATLAR ──────────────────────────────────────────────────
class PayStates(StatesGroup):
    wait_amount = State()
    wait_check  = State()

class AutoPayStates(StatesGroup):
    wait_amount = State()

class HumoSetupStates(StatesGroup):
    wait_api_id  = State()
    wait_api_hash = State()
    wait_phone   = State()
    wait_code    = State()
    wait_password = State()

class TolovSetupStates(StatesGroup):
    wait_shop_id  = State()
    wait_shop_key = State()
    wait_api_url  = State()

class BalanceChangeState(StatesGroup):
    wait_user_id = State()
    wait_amount  = State()

class AdminSearchState(StatesGroup):
    wait_phone      = State()
    wait_profile_id = State()

class BroadcastState(StatesGroup):
    wait_message = State()

class PhoneState(StatesGroup):
    wait_phone = State()

class AdminSettingsState(StatesGroup):
    wait_daily_bonus             = State()
    wait_referral_bonus          = State()
    wait_bulk_percent            = State()
    wait_channel_id              = State()
    wait_channel_username        = State()
    wait_price_value             = State()
    wait_orders_channel_id       = State()
    wait_orders_channel_username = State()
    wait_card_number             = State()
    wait_card_owner              = State()
    wait_exchange_rate           = State()
    wait_default_margin          = State()
    wait_card2_number            = State()
    wait_card2_owner             = State()
    wait_autopay_offset          = State()
    wait_autopay_expiry          = State()
    wait_new_user_channel_id     = State()
    wait_inactive_days           = State()
    wait_incomplete_hours        = State()
    wait_low_balance             = State()

class EmojiState(StatesGroup):
    wait_forward = State()

# ─── MENYULAR ──────────────────────────────────────────────────
def build_main_menu() -> ReplyKeyboardMarkup:
    """Har chaqirilganda joriy bog'langan Premium emoji/style holatiga
    qarab qayta tuziladi — shuning uchun admin /setemoji bilan
    bog'lagach, pastdagi menyu ham darhol yangilanadi."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [kb_button("Nomer olish", "phone"),   kb_button("Buyurtmalarim", "cart")],
            [kb_button("Hisobim", "money"),        kb_button("Hisob to'ldirish", "card", style="primary")],
            [kb_button("Pul ishlash", "cash"),     kb_button("Qo'llanma", "guide")],
            [kb_button("Qo'llab-quvvatlash", "support")],
        ],
        resize_keyboard=True
    )

def phone_request_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📲 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
    resize_keyboard=True
)

CANCEL_WORDS = {"/cancel", "❌ bekor qilish", "bekor qilish", "/bekor"}

def is_cancel_text(text: str | None) -> bool:
    return bool(text) and text.strip().lower() in CANCEL_WORDS

# ─── YORDAMCHI FUNKSIYALAR ─────────────────────────────────────
async def get_setting(key: str, default):
    val = await db.get_setting(key)
    if val is None:
        return default
    try:
        return type(default)(val)
    except Exception:
        return default

async def is_maintenance_mode() -> bool:
    val = await db.get_setting("maintenance_mode")
    return val == "1"

async def notify_new_member(user, referrer_id: int | None):
    """Admin belgilagan kanalga yangi a'zo haqida to'liq ma'lumot yuboradi.
    Kanal sozlanmagan bo'lsa, hech narsa qilmaydi (ixtiyoriy xususiyat)."""
    username_part = f"@{user.username}" if user.username else "— (username yo'q)"
    ref_part = f"\n{E('referral')} Kim taklif qildi: <code>{referrer_id}</code>" if referrer_id else ""
    text = (
        f"{E('sparkle')} <b>Yangi foydalanuvchi botga qo'shildi!</b>\n\n"
        f"{E('profile')} Ism: <b>{user.full_name}</b>\n"
        f"{username_part}\n"
        f"🆔 Telegram ID: <code>{user.id}</code>\n"
        f"🌐 Til: {user.language_code or '—'}"
        f"{ref_part}"
    )
    await log_event(text)

LOW_BALANCE_THRESHOLD_DEFAULT = 3000  # shundan kam qolsa, admin kanaliga ogohlantirish boradi

async def log_event(text: str):
    """Admin belgilagan "hodisalar" kanaliga xabar yuboradi (agar sozlangan
    bo'lsa). Yangi a'zo, xarid, to'lov (qo'lda va avtomatik), kam balans
    kabi HAMMA muhim hodisalar shu bitta funksiya orqali o'tadi — shuning
    uchun kelajakda yangi hodisa turi qo'shish oson va izchil bo'ladi."""
    channel_id = await get_setting("new_user_channel_id", "")
    if not channel_id:
        return
    try:
        await bot.send_message(
            int(channel_id),
            f"{text}\n{E('clock')} {now_tashkent().strftime('%d.%m.%Y %H:%M')}"
        )
    except Exception as e:
        print(f"⚠️ Hodisa kanaliga xabar yuborib bo'lmadi: {e}")

async def maybe_notify_low_balance(user_id: int, new_balance: int):
    """Xariddan keyin foydalanuvchi balansi juda kamayib qolsa, admin
    kanaliga bir marta (spam bo'lmasligi uchun pastki chegaradan o'tganda
    faqat) ogohlantirish yuboradi."""
    threshold = int(await get_setting("low_balance_threshold", LOW_BALANCE_THRESHOLD_DEFAULT))
    if 0 <= new_balance < threshold:
        user = await db.get_user(user_id)
        name = user["fullname"] if user else str(user_id)
        await log_event(
            f"{E('warn')} <b>Foydalanuvchi balansi kamaydi</b>\n\n"
            f"{E('profile')} {name} (<code>{user_id}</code>)\n"
            f"{E('wallet')} Joriy balans: <b>{new_balance:,} so'm</b>"
        )

# ─── NARX HISOBLASH ────────────────────────────────────────────
# Ilgari kurs (12500) va margin (1.3) kodga qattiq yozilgan edi — shuning
# uchun admin foizni o'zgartirganda ba'zi davlatlar bir xil, ba'zilari
# boshqacha "asos narx"dan hisoblanib, natija notekis ko'rinar edi.
# Endi ikkalasi ham sozlamalarda saqlanadi va HAR DOIM shu yerdan olinadi —
# shu bilan barcha davlatlar uchun bir xil formuladan foydalaniladi.
DEFAULT_EXCHANGE_RATE = 12500
DEFAULT_MARGIN        = 1.3
MIN_PRICE_SOM         = 500

async def get_exchange_rate() -> float:
    return await get_setting("exchange_rate", DEFAULT_EXCHANGE_RATE)

async def get_default_margin() -> float:
    return await get_setting("default_margin", DEFAULT_MARGIN)

async def calc_default_price(usd_price: float) -> int:
    """Markup jadvalida narx sozlanmagan davlatlar uchun asos narxni hisoblaydi.
    Kurs va margin sozlamalardan olinadi — shu bilan barcha davlatlar uchun
    bitta izchil formula ishlatiladi."""
    rate   = await get_exchange_rate()
    margin = await get_default_margin()
    price  = int(float(usd_price) * rate * margin)
    return max(price, MIN_PRICE_SOM)

# ─── FAOLLIK ESLATMALARI ────────────────────────────────────────
INACTIVE_DAYS_DEFAULT    = 3   # shuncha kun kirmagan foydalanuvchiga eslatma
INCOMPLETE_HOURS_DEFAULT = 6   # shuncha soat telefon tasdiqlamaganga eslatma
REMINDER_CHECK_INTERVAL_SEC = 6 * 60 * 60  # har 6 soatda bir tekshiradi

async def send_inactivity_reminders():
    """Botga uzoq vaqt kirmagan foydalanuvchilarga eslatma yuboradi."""
    days = int(await get_setting("inactive_reminder_days", INACTIVE_DAYS_DEFAULT))
    users = await db.get_inactive_users(days=days)
    sent = 0
    for u in users:
        try:
            await bot.send_message(
                u["user_id"],
                f"{E('sparkle')} <b>Sizni sog'indik!</b>\n\n"
                f"Bir necha kundan beri botimizga tashrif buyurmadingiz. "
                f"Yangi davlatlar va arzon narxlar sizni kutmoqda {E('fire')}\n\n"
                f"Qaytib kelib, bugungi bonusingizni ham unutmang {E('gift')}"
            )
            sent += 1
        except Exception:
            pass  # bloklagan yoki botni o'chirgan bo'lishi mumkin
        await db.mark_inactive_reminder_sent(u["user_id"])
    if sent:
        print(f"🔔 {sent} ta faolsiz foydalanuvchiga eslatma yuborildi.")

async def send_incomplete_reminders():
    """Ro'yxatdan to'liq o'tmagan (telefon tasdiqlamagan) foydalanuvchilarga
    eslatma yuboradi."""
    hours = int(await get_setting("incomplete_reminder_hours", INCOMPLETE_HOURS_DEFAULT))
    users = await db.get_incomplete_users(hours=hours)
    sent = 0
    for u in users:
        try:
            await bot.send_message(
                u["user_id"],
                f"{E('warn')} <b>Ro'yxatdan o'tishni yakunlamadingiz!</b>\n\n"
                f"Botdan to'liq foydalanish uchun telefon raqamingizni tasdiqlashingiz kerak. "
                f"Bu atigi bir necha soniya vaqt oladi {E('phone')}\n\n"
                f"/start buyrug'ini bosib davom eting."
            )
            sent += 1
        except Exception:
            pass
        await db.mark_incomplete_reminder_sent(u["user_id"])
    if sent:
        print(f"🔔 {sent} ta to'liq ro'yxatdan o'tmagan foydalanuvchiga eslatma yuborildi.")

async def reminder_loop():
    """Fonda doim ishlaydigan sikl — har REMINDER_CHECK_INTERVAL_SEC
    soniyada faolsizlik va to'liqmaslik eslatmalarini tekshirib yuboradi."""
    while True:
        try:
            await send_inactivity_reminders()
            await send_incomplete_reminders()
        except Exception as e:
            print(f"⚠️ Eslatmalarni yuborishda xatolik: {e}")
        await asyncio.sleep(REMINDER_CHECK_INTERVAL_SEC)

# ─── REFERALLARNI OBUNAGA QARAB TEKSHIRISH ──────────────────────
REFERRAL_CHECK_INTERVAL_SEC = 6 * 60 * 60  # har 6 soatda

async def referral_subscription_check_loop():
    """Fonda doim ishlaydigan sikl — hozir 'faol' deb hisoblangan barcha
    referallarning hali ham majburiy kanalga a'zoligini tekshiradi. Agar
    kimdir kanaldan chiqib ketgan bo'lsa, uni taklif qilgan odamning
    referal soni avtomatik -1 bo'ladi (yozuv o'chirilmaydi, faqat
    "nofaol" deb belgilanadi)."""
    while True:
        try:
            active_ids = await db.get_active_referred_user_ids()
            deactivated = 0
            for uid in active_ids:
                try:
                    still_subscribed = await check_subscription(uid)
                except Exception:
                    continue  # vaqtinchalik xato — keyingi tekshiruvda qayta urinamiz
                if not still_subscribed:
                    referrer_id = await db.deactivate_referral(uid)
                    if referrer_id:
                        deactivated += 1
                        await log_event(
                            f"{E('cross')} <b>Referal nofaol bo'ldi</b> (foydalanuvchi kanaldan chiqib ketgan)\n\n"
                            f"{E('profile')} Referrer: <code>{referrer_id}</code>\n"
                            f"{E('profile')} Foydalanuvchi: <code>{uid}</code>"
                        )
            if deactivated:
                print(f"🔻 {deactivated} ta referal kanaldan chiqib ketganligi sababli nofaol qilindi.")
        except Exception as e:
            print(f"⚠️ Referal obunasini tekshirishda xatolik: {e}")
        await asyncio.sleep(REFERRAL_CHECK_INTERVAL_SEC)

# ─── WEBHOOK ORQALI AVTOMATIK TO'LOV (ixtiyoriy, qo'shimcha yo'l) ──
# Ba'zan (masalan @LockAvtoPayBot kabi) tashqi xizmatlar karta SMS
# xabarlarini o'zi kuzatib, aniqlangan to'lovni SIZNING serveringizga
# HTTP so'rov (webhook) orqali yuboradi — sizga o'zingiz Telethon sessiya
# ushlab turishning hojati qolmaydi. Bu yo'l ixtiyoriy va Telethon
# tinglovchisi bilan PARALLEL ishlaydi — biri ishlamay qolsa, ikkinchisi
# baribir ishlayveradi (bitta to'lov ikki marta hisoblanib ketmaydi,
# chunki moslashtirish bazada FOR UPDATE SKIP LOCKED bilan himoyalangan).
#
# So'rov formati (GET yoki POST):
#   https://SIZNING_DOMEN:PORT/webhook/payment?secret_key=...&amount=50000&card_last=2747
#
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))

async def get_webhook_secret() -> str:
    """Webhook uchun maxfiy kalit — birinchi chaqirilganda avtomatik
    tasodifiy generatsiya qilinadi va bazada saqlanadi."""
    key = await db.get_setting("webhook_secret_key")
    if not key:
        key = secrets.token_urlsafe(24)
        await db.set_setting("webhook_secret_key", key)
    return key

async def webhook_payment_handler(request: web.Request) -> web.Response:
    params = request.query if request.method == "GET" else await request.post()
    secret_key = str(params.get("secret_key", "")).strip()
    amount_raw = str(params.get("amount", "")).strip()
    card_last  = str(params.get("card_last", "")).strip()
    method     = str(params.get("method", "webhook")).strip()

    real_key = await get_webhook_secret()
    if not secret_key or secret_key != real_key:
        return web.json_response({"success": False, "error": "Noto'g'ri secret_key"}, status=403)

    amount = parse_amount(amount_raw)
    if not amount or amount <= 0:
        return web.json_response({"success": False, "error": "Miqdor noto'g'ri"}, status=400)

    try:
        matched = await humo_listener.process_deposit_amount(
            db, bot, ADMIN_ID, E, amount=amount, card_last=card_last,
            source=f"webhook:{method}", log_event=log_event
        )
    except Exception as e:
        print(f"⚠️ Webhook to'lovini qayta ishlashda xatolik: {e}")
        return web.json_response({"success": False, "error": "Ichki xatolik"}, status=500)

    if matched:
        return web.json_response({"success": True, "message": "To'lov tasdiqlandi", "amount": amount})
    return web.json_response({"success": False, "error": "Kutilayotgan to'lov topilmadi"})

async def start_webhook_server():
    app = web.Application()
    app.router.add_route("GET", "/webhook/payment", webhook_payment_handler)
    app.router.add_route("POST", "/webhook/payment", webhook_payment_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    print(f"✅ Webhook server {WEBHOOK_PORT}-portda ishga tushdi (/webhook/payment)")

# ─── AVTOMATIK TO'LOV (HUMOcard) ────────────────────────────────
AUTO_PAY_MAX_OFFSET_DEFAULT   = 50    # tasodifiy qo'shimcha (1-50 so'm) uchun urinishlar soni
AUTO_PAY_EXPIRY_MIN_DEFAULT   = 20    # necha daqiqa kutiladi

async def is_auto_pay_enabled() -> bool:
    return (await get_setting("auto_pay_enabled", "0")) == "1"

async def get_auto_pay_max_offset() -> int:
    return int(await get_setting("auto_pay_max_offset", AUTO_PAY_MAX_OFFSET_DEFAULT))

async def get_auto_pay_expiry_min() -> int:
    val = int(await get_setting("auto_pay_expiry_min", AUTO_PAY_EXPIRY_MIN_DEFAULT))
    # Bank/karta bildirishnomalari kechikishi mumkinligi uchun 5 daqiqadan
    # kam bo'lishiga umuman yo'l qo'ymaymiz — hatto bazada eski (xato)
    # qiymat saqlangan bo'lsa ham.
    return max(val, 5)

# ─── HUMOcard SESSIYASINI BOT ICHIDAN ULASH ─────────────────────
# Session generate_session.py orqali qo'lda yaratish o'rniga, endi
# to'g'ridan-to'g'ri botning o'zi orqali (telefon -> kod -> parol)
# ulash mumkin. Bu orqali ulanish HAQIQATDA muvaffaqiyatli bo'lganini
# (qaysi akkaunt ulanganini) aniq ko'rish mumkin.
_humo_setup_ctx: dict = {}       # admin_id -> {"client", "phone", "phone_code_hash", "api_id", "api_hash"}
_humo_listener_task = None       # joriy ishlab turgan tinglovchi vazifasi (qayta ishga tushirish uchun)

async def get_humo_credentials():
    """Bazada (admin panel orqali) yoki .env da sozlangan HUMOcard login
    ma'lumotlarini qaytaradi — baza ustunlik qiladi."""
    api_id  = await get_setting("humo_api_id", os.getenv("HUMO_API_ID", ""))
    api_hash = await get_setting("humo_api_hash", os.getenv("HUMO_API_HASH", ""))
    session  = await get_setting("humo_session_string", os.getenv("HUMO_SESSION_STRING", ""))
    return str(api_id or ""), str(api_hash or ""), str(session or "")

async def is_humo_configured() -> bool:
    api_id, api_hash, session = await get_humo_credentials()
    return bool(api_id and api_hash and session)

async def start_or_restart_humo_listener():
    """Joriy sozlamalar (baza yoki .env) bilan HUMOcard tinglovchisini
    (qayta) ishga tushiradi. Avval eski vazifa bo'lsa, to'xtatadi —
    shu bilan bitta sessiyaga ikkita parallel ulanish bo'lib qolmaydi."""
    global _humo_listener_task
    if _humo_listener_task and not _humo_listener_task.done():
        _humo_listener_task.cancel()
        try:
            await _humo_listener_task
        except Exception:
            pass
    api_id, api_hash, session = await get_humo_credentials()
    if not (api_id and api_hash and session):
        return False
    _humo_listener_task = asyncio.create_task(
        humo_listener.start_humo_listener_with_creds(
            db, bot, ADMIN_ID, E, api_id=int(api_id), api_hash=api_hash,
            session_string=session, log_event=log_event
        )
    )
    return True

# ─── TolovAPI (tolov.run.place) — UCHINCHI mustaqil avtomatik to'lov usuli ─
# Telethon (o'zingiz sessiya ulaysiz) va webhook (tashqi xizmat sizga
# so'rov yuboradi) dan farqli — bu yerda siz TolovAPI serveriga o'zingiz
# davriy so'rov yuborasiz ("shu summa keldimi?"). Bunga sessiya yoki
# ochiq port kerak emas — faqat shop_id va shop_key (TolovAPI'dan olinadi).
TOLOV_API_URL_DEFAULT = tolov_api.TOLOV_API_URL_DEFAULT

async def get_tolov_config() -> dict:
    return {
        "enabled": (await get_setting("tolov_api_enabled", "0")) == "1",
        "api_url": await get_setting("tolov_api_url", TOLOV_API_URL_DEFAULT),
        "shop_id": await get_setting("tolov_shop_id", ""),
        "shop_key": await get_setting("tolov_shop_key", ""),
    }

async def is_tolov_configured() -> bool:
    cfg = await get_tolov_config()
    return bool(cfg["shop_id"] and cfg["shop_key"])

_tolov_polling_task = None

async def start_or_restart_tolov_polling():
    """TolovAPI polling siklini (qayta) ishga tushiradi."""
    global _tolov_polling_task
    if _tolov_polling_task and not _tolov_polling_task.done():
        _tolov_polling_task.cancel()
        try:
            await _tolov_polling_task
        except Exception:
            pass
    if not await is_tolov_configured():
        return False
    _tolov_polling_task = asyncio.create_task(
        tolov_api.tolov_polling_loop(
            db, bot, ADMIN_ID, E,
            get_config=get_tolov_config,
            process_deposit_amount=humo_listener.process_deposit_amount,
            log_event=log_event,
        )
    )
    return True


async def reserve_auto_amount(user_id: int, base_amount: int):
    """
    Berilgan summa uchun noyob "final_amount" band qiladi.

    MUHIM: har doim (faqat to'qnashuvda emas) summaga KICHIK tasodifiy son
    (1 dan 50 so'mgacha) QO'SHIB qo'yamiz (masalan 1000 so'ralsa -> 1000
    dan 50 so'm ko'p, ya'ni 1001-1050 oralig'ida). Bu ikki sabab uchun kerak:
      1) Aynan "dumaloq" summalar (1000, 5000, 50000) haqiqiy hayotda
         ENG KO'P uchraydigan summalar — agar band qilingan summa doim
         dumaloq bo'lsa, botga aloqasi yo'q, tasodifan kelgan boshqa bir
         pul o'tkazmasi ham xuddi shu summada bo'lib, noto'g'ri
         foydalanuvchiga hisoblanib ketish xavfi yuqori.
      2) Tasodifiy qo'shimcha — bir nechta odam bir xil summani xohlasa
         ham, to'qnashish ehtimoli past (50 xil variant), va farq har
         doim KICHIK (atigi 1-50 so'm) bo'lgani uchun foydalanuvchiga
         noqulaylik keltirmaydi.

    Avval 1-karta fazosida joy qidiradi, u to'lib qolsa — 2-kartaga o'tadi.
    Qaytaradi: dict(payment_id, final_amount, offset, card_number,
    card_owner, expires_at) yoki hech joy topilmasa None.
    """
    await db.expire_old_auto_payments()
    max_attempts = await get_auto_pay_max_offset()
    expiry_min   = await get_auto_pay_expiry_min()

    card1_number = await get_setting("card_number", CARD_NUMBER)
    card1_owner  = await get_setting("card_owner", CARD_OWNER)
    card2_number = await get_setting("card2_number", "")
    card2_owner  = await get_setting("card2_owner", "")

    candidates = [(card1_number, card1_owner)]
    if card2_number:
        candidates.append((card2_number, card2_owner))

    MIN_TAIL, MAX_TAIL = 1, 50  # qo'shiladigan tasodifiy summa oralig'i

    for card_number, card_owner in candidates:
        # MUHIM: HUMOcard/banklar xabarida karta faqat OXIRGI 4 raqami
        # bilan ko'rsatiladi (masalan "*2747"). Agar biz bu yerda to'liq
        # karta raqamini (masalan "8600 6067 4557 2747") saqlasak,
        # moslashtirishda hech qachon teng bo'lmaydi va to'lov HECH QACHON
        # avtomatik tasdiqlanmaydi! Shuning uchun faqat oxirgi 4 ta
        # raqamni ajratib olamiz.
        card_last4 = "".join(ch for ch in card_number if ch.isdigit())[-4:]
        reserved = await db.get_reserved_amounts(target_card=card_last4)
        final_amount = None
        for _ in range(max_attempts):
            tail = random.randint(MIN_TAIL, MAX_TAIL)
            cand = base_amount + tail
            if cand not in reserved:
                final_amount = cand
                break
        if final_amount is None:
            continue  # bu kartada (juda kam ehtimol bilan) joy topilmadi, keyingisini sinaymiz
        offset = final_amount - base_amount
        expires_at = now_tashkent() + timedelta(minutes=expiry_min)
        payment_id = await db.add_auto_payment(user_id, base_amount, final_amount, expires_at, target_card=card_last4)
        return {
            "payment_id": payment_id, "final_amount": final_amount, "offset": offset,
            "card_number": card_number, "card_owner": card_owner, "expires_at": expires_at,
            "expiry_min": expiry_min,
        }
    return None  # ikkala karta ham to'lib qolgan — juda kam uchraydigan holat

UZ_CODE = "UZ"

def sort_uz_first(items, key_func, reverse: bool = False):
    """Har qanday saralashda O'zbekiston (+998 / UZ) doim ro'yxat boshida turadi,
    qolgan davlatlar esa berilgan key_func bo'yicha saralanadi."""
    items = list(items)
    uz_items  = [(c, i) for c, i in items if c.upper() == UZ_CODE]
    rest      = [(c, i) for c, i in items if c.upper() != UZ_CODE]
    rest.sort(key=key_func, reverse=reverse)
    return uz_items + rest

def country_flag(code: str) -> str:
    """ISO-2 davlat kodidan bayroq emoji hosil qiladi (masalan UZ -> 🇺🇿)."""
    code = (code or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        return "🌍"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code)

_FLAG_EMOJI_RE = re.compile("[\U0001F1E6-\U0001F1FF]+")

def clean_country_name(name: str) -> str:
    """API'dan kelgan davlat nomida allaqachon bayroq bo'lishi mumkin — shu
    bayroqni olib tashlaydi, shunda o'zimizning country_flag() bilan
    ikkilanib qo'yilmaydi (masalan 'Uzbekistan 🇺🇿' -> 'Uzbekistan')."""
    if not name:
        return name
    return _FLAG_EMOJI_RE.sub("", name).strip()

# ─── PREMIUM (CUSTOM) EMOJI ─────────────────────────────────────
# 2026-yil 9-fevral Bot API yangilanishidan buyon, agar botni
# yaratgan akkaunt (@BotFather orqali) Telegram Premium obunasiga
# ega bo'lsa, bot shaxsiy/guruh/superguruh chatlariga o'zi yuborayotgan
# xabarlarda haqiqiy Premium (custom) emojilardan foydalanishi mumkin.
#
# MUHIM CHEKLOVLAR:
#   1) Faqat XABAR MATNIDA ishlaydi (parse_mode=HTML) — tugma
#      (inline/reply keyboard button) matnida hech qachon ishlamaydi.
#   2) custom_emoji_id — bu real Premium emoji stikerining ID raqami,
#      uni pastdagi /getemojiid (forward orqali) yoki /emojis
#      buyrug'i yordamida olish mumkin.
#   3) ID o'rnatilmagan slug uchun oddiy (fallback) emoji ko'rsatiladi
#      — demak, Premium sozlanmagan bo'lsa ham bot normal ishlayveradi.

PREMIUM_EMOJI_SLUGS = {
    "welcome":   "👋",
    "profile":   "👤",
    "check":     "✅",
    "cross":     "❌",
    "money":     "💰",
    "phone":     "📞",
    "cart":      "🛒",
    "gift":      "🎁",
    "rocket":    "🚀",
    "warn":      "⚠️",
    "card":      "💳",
    "admin":     "🔐",
    "sparkle":   "✨",
    "receipt":   "🧾",
    "camera":    "📸",
    "wallet":    "💼",
    "clock":     "⏰",
    "hourglass": "⏳",
    "fire":      "🔥",
    "chart":     "📊",
    "globe":     "🌐",
    "package":   "📦",
    "key":       "🔑",
    "support":   "🆘",
    "guide":     "📕",
    "referral":  "👥",
    "link":      "🔗",
    "point":     "👉",
    "down":      "👇",
    "shield":    "🛡",
    "party":     "🎉",
    "bell":      "🔔",
    "star":      "⭐",
    "bank":      "🏦",
    "one":       "1️⃣",
    "two":       "2️⃣",
    "cash":      "💸",
    "arrow_l":   "⬅️",
    "arrow_r":   "➡️",
    "gear":      "⚙️",
    "search":    "🔍",
    "report":    "📅",
}

PREMIUM_EMOJI_CACHE: dict[str, str] = {}

async def reload_premium_emoji_cache():
    """settings jadvalidan barcha o'rnatilgan custom_emoji_id larni xotiraga yuklaydi."""
    global PREMIUM_EMOJI_CACHE
    cache = {}
    for slug in PREMIUM_EMOJI_SLUGS:
        val = await db.get_setting(f"premium_emoji_{slug}")
        if val:
            cache[slug] = val
    PREMIUM_EMOJI_CACHE = cache

def E(slug: str) -> str:
    """
    Slug uchun, agar admin haqiqiy custom_emoji_id bog'lagan bo'lsa —
    Premium emojini (<tg-emoji>) qaytaradi, aks holda oddiy fallback
    emojini qaytaradi. FAQAT xabar matni ichida ishlating (tugmalarda
    emas — buning uchun pastdagi kb_button()/ib_button() dan foydalaning).
    """
    fallback = PREMIUM_EMOJI_SLUGS.get(slug, "▫️")
    custom_id = PREMIUM_EMOJI_CACHE.get(slug)
    if custom_id:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return fallback

# ─── TUGMALARDA PREMIUM EMOJI (Bot API 9.4+) ────────────────────
# 2026-yildan boshlab Telegram tugmalarga ham icon_custom_emoji_id
# (matndan OLDIN chiqadigan ikonka) va style ("primary"/"success"/
# "danger") qo'shishga ruxsat berdi. Bog'langan slug bo'lsa — icon
# sifatida shuni qo'yamiz va matndan otiladigan emojini olib
# tashlaymiz (aks holda ikkitasi bir vaqtda chiqib qoladi). Bog'lanmagan
# bo'lsa — oddiy fallback emojini matn boshiga qo'shib qo'yamiz.
def kb_button(label: str, slug: str | None = None, style: str | None = None, **kwargs) -> "KeyboardButton":
    """Pastdagi (reply) menyu tugmasi. Agar slug Premium emojiga bog'langan
    bo'lsa, ikonka matndan OLDIN chiqadi va matndagi fallback emoji olib
    tashlanadi (aks holda ikkalasi birga chiqib, ikki marta ko'rinadi).
    Bog'lanmagan bo'lsa — oddiy fallback emoji matn boshida qoladi.
    DIQQAT: shu sabab matn Premium yoqilgan-yoqilmaganiga qarab o'zgaradi —
    handler'lar F.text == "..." emas, F.text.contains(label) orqali ushlanadi."""
    icon_id  = PREMIUM_EMOJI_CACHE.get(slug) if slug else None
    fallback = PREMIUM_EMOJI_SLUGS.get(slug, "") if slug else ""
    text = label if icon_id else (f"{fallback} {label}".strip() if fallback else label)
    return KeyboardButton(text=text, icon_custom_emoji_id=icon_id, style=style, **kwargs)

def ib_button(label: str, slug: str | None = None, style: str | None = None, **kwargs) -> "InlineKeyboardButton":
    """Xabar ostidagi (inline) tugma. Bular callback_data/url orqali
    aniqlanadi (matn orqali emas), shuning uchun bu yerda Premium
    bog'langanda fallback emojini matndan olib tashlash xavfsiz —
    ikkilanib qolmaydi."""
    icon_id  = PREMIUM_EMOJI_CACHE.get(slug) if slug else None
    fallback = PREMIUM_EMOJI_SLUGS.get(slug, "") if slug else ""
    if icon_id:
        # Ikonka allaqachon ko'rinadi — matnga qaytadan fallback emoji
        # qo'shsak, ikkalasi birga (ikkilanib) chiqib qoladi. Shuning
        # uchun matn bo'sh bo'lsa, faqat bo'sh joy qo'yamiz (Telegram
        # butunlay bo'sh matnli tugmani rad etishi mumkin).
        text = label if label else " "
    else:
        text = f"{fallback} {label}".strip() if fallback else label
        if not text:
            text = "•"
    return InlineKeyboardButton(text=text, icon_custom_emoji_id=icon_id, style=style, **kwargs)

def now_tashkent() -> datetime:
    return datetime.now(TASHKENT_TZ)

def parse_amount(text: str) -> int | None:
    """Foydalanuvchi kiritgan summani ANIQ raqamga aylantiradi. Oddiy
    str.isdigit() dan farqli o'laroq, bo'shliq (1 000), nuqta (1.000),
    vergul yoki mobil klaviaturadan tushib qolishi mumkin bo'lgan
    ko'rinmas belgilarni e'tiborsiz qoldirib, faqat raqamlarni oladi.
    Shu bilan foydalanuvchi qanday formatda yozmasin, to'g'ri o'qiladi."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d]", "", text, flags=re.UNICODE)
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None

def parse_signed_amount(text: str) -> int | None:
    """parse_amount bilan bir xil, lekin manfiy son (masalan balансdan
    ayirish uchun '-5000') ni ham to'g'ri o'qiydi."""
    if not text:
        return None
    negative = text.strip().startswith("-")
    amount = parse_amount(text)
    if amount is None:
        return None
    return -amount if negative else amount

# ─── PREMIUM EMOJI — ADMIN BUYRUQLARI ───────────────────────────
# /emojis        — barcha slug (nom) larning holatini ko'rsatadi
# /getemojiid    — admin Premium emoji yozilgan xabarni yuborsa/forward
#                  qilsa, undagi custom_emoji_id larni chiqarib beradi
# /setemoji slug id — slugni haqiqiy Premium emoji IDga bog'laydi
# /unsetemoji slug  — bog'lanishni bekor qiladi (oddiy emojiga qaytaradi)

@admin_router.message(F.text == "/emojis")
async def emojis_list_cmd(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    lines = [f"{E('sparkle')} <b>Premium emoji sozlamalari</b>\n"]
    for slug, fallback in PREMIUM_EMOJI_SLUGS.items():
        bound = PREMIUM_EMOJI_CACHE.get(slug)
        status = f"✅ bog'langan (<code>{bound}</code>)" if bound else "▫️ oddiy emoji"
        lines.append(f"{fallback} <code>{slug}</code> — {status}")
    lines.append(
        "\nQanday bog'lash kerak:\n"
        "1️⃣ Premium emoji ishtirok etgan xabarni botga forward qiling yoki "
        "<code>/getemojiid</code> yuboring, so'ng shu emojini o'z ichiga olgan xabarni yuboring.\n"
        "2️⃣ Bot sizga o'sha emojining ID sini chiqaradi.\n"
        "3️⃣ <code>/setemoji slug id</code> — masalan: <code>/setemoji money 5368324170671202286</code>\n"
        "4️⃣ Bekor qilish uchun: <code>/unsetemoji slug</code>\n\n"
        f"{E('sparkle')} <b>Endi bog'langan sluglar avtomatik ravishda</b> xabar matnlarida "
        f"HAM, pastdagi menyu va tugmalarda HAM (icon sifatida) qo'llanadi — alohida sozlash shart emas."
    )
    await msg.answer("\n".join(lines))


@admin_router.message(F.text == "/getemojiid")
async def getemojiid_start(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    await msg.answer(
        f"{E('camera')} Endi tarkibida Premium emoji bo'lgan xabarni menga yuboring "
        f"(yozib yoki forward qilib) — men undagi barcha custom emoji ID larini chiqarib beraman.\n\n"
        f"Bekor qilish uchun /cancel yozing."
    )
    await state.set_state(EmojiState.wait_forward)


@admin_router.message(EmojiState.wait_forward)
async def getemojiid_receive(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    if is_cancel_text(msg.text):
        await state.clear()
        await msg.answer("❌ Amal bekor qilindi.")
        return
    entities = (msg.entities or []) + (msg.caption_entities or [])
    custom = [e for e in entities if e.type == "custom_emoji"]
    await state.clear()
    if not custom:
        await msg.answer(
            f"{E('cross')} Bu xabarda Premium (custom) emoji topilmadi.\n"
            f"Diqqat: oddiy emoji (👍😀 kabi) custom_emoji_id ga ega bo'lmaydi — "
            f"faqat Telegram Premium orqali tanlanadigan maxsus emojilar mos keladi."
        )
        return
    lines = [f"{E('check')} Topilgan Premium emoji ID lar:\n"]
    for e in custom:
        lines.append(f"<code>{e.custom_emoji_id}</code>")
    lines.append(
        f"\nBog'lash uchun: <code>/setemoji slug id</code>\n"
        f"Mavjud sluglar ro'yxati uchun: /emojis"
    )
    await msg.answer("\n".join(lines))


@admin_router.message(F.text.regexp(r'^/setemoji\s+\S+\s+\d+$'))
async def setemoji_cmd(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.strip().split()
    slug, emoji_id = parts[1], parts[2]
    if slug not in PREMIUM_EMOJI_SLUGS:
        await msg.answer(
            f"{E('cross')} Bunday slug topilmadi: <code>{slug}</code>\n"
            f"Mavjud sluglarni ko'rish uchun: /emojis"
        )
        return
    await db.set_setting(f"premium_emoji_{slug}", emoji_id)
    await reload_premium_emoji_cache()
    await msg.answer(f"{E('check')} <code>{slug}</code> muvaffaqiyatli bog'landi: {E(slug)}")


@admin_router.message(F.text.regexp(r'^/unsetemoji\s+\S+$'))
async def unsetemoji_cmd(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    slug = msg.text.strip().split()[1]
    if slug not in PREMIUM_EMOJI_SLUGS:
        await msg.answer(f"{E('cross')} Bunday slug topilmadi: <code>{slug}</code>")
        return
    await db.set_setting(f"premium_emoji_{slug}", "")
    await reload_premium_emoji_cache()
    await msg.answer(f"{E('check')} <code>{slug}</code> uchun bog'lanish bekor qilindi, oddiy emojiga qaytdi.")



def today_utc_bounds():
    """Toshkent vaqti bo'yicha 'bugun'ning UTC chegaralarini qaytaradi (naive UTC)."""
    local_now = now_tashkent()
    start_local = datetime.combine(local_now.date(), dt_time.min, tzinfo=TASHKENT_TZ)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc

# ─── TG-LION API ───────────────────────────────────────────────
async def api_get(action: str, extra: dict = None) -> dict:
    params = {"action": action, "apiKey": TGLION_API_KEY, "YourID": TGLION_YOUR_ID}
    if extra:
        params.update(extra)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(TGLION_BASE, params=params, timeout=aiohttp.ClientTimeout(total=30)) as r:
                return await r.json(content_type=None)
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def api_available_countries() -> dict:
    return await api_get("available_countries")

async def api_get_balance_tglion() -> dict:
    return await api_get("get_balance")

async def api_buy_number(country_code: str) -> dict:
    return await api_get("getNumber", {"country_code": country_code})

async def api_get_code(number: str) -> dict:
    return await api_get("getCode", {"number": number})

# ══════════════════════════════════════════════════════════════
#  TA'MIRLASH REJIMI — oddiy foydalanuvchilar botni admin band
#  bo'lganda yoki texnik ishlar paytida ishlata olmasligi uchun
# ══════════════════════════════════════════════════════════════

MAINTENANCE_TEXT = (
    "🔧 <b>Texnik ishlar olib borilmoqda</b>\n\n"
    "Bot hozircha vaqtincha ishlamayapti. Iltimos, birozdan so'ng "
    "qayta urinib ko'ring.\n\n"
    "Qulaysizlik uchun uzr so'raymiz 🙏"
)

class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = getattr(event, "from_user", None)
        if user and user.id != ADMIN_ID and await is_maintenance_mode():
            if isinstance(event, Message):
                await event.answer(MAINTENANCE_TEXT)
            elif isinstance(event, CallbackQuery):
                await event.answer("🔧 Bot texnik ishlar tufayli vaqtincha ishlamayapti.", show_alert=True)
            return
        return await handler(event, data)

router.message.middleware(MaintenanceMiddleware())
router.callback_query.middleware(MaintenanceMiddleware())

class UserActivityMiddleware(BaseMiddleware):
    """Har bir foydalanuvchi harakatida: (1) bloklangan bo'lsa — botga
    umuman ruxsat bermaydi, (2) bloklanmagan bo'lsa — oxirgi faollik
    vaqtini yangilaydi (faolsizlik eslatmalari shu asosida ishlaydi)."""
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = getattr(event, "from_user", None)
        if user and user.id != ADMIN_ID:
            try:
                if await db.is_user_blocked(user.id):
                    if isinstance(event, CallbackQuery):
                        await event.answer("🚫 Siz bloklangansiz.", show_alert=True)
                    return
                await db.touch_last_active(user.id)
            except Exception:
                pass  # bazaga ulanishda vaqtinchalik uzilish botni to'xtatmasin
        return await handler(event, data)

router.message.middleware(UserActivityMiddleware())
router.callback_query.middleware(UserActivityMiddleware())

# ─── BOTNI BLOKLASH/BLOKDAN CHIQARISHNI KUZATISH ────────────────
# Telegram foydalanuvchi botni bloklaganda yoki blokdan chiqarganda
# my_chat_member hodisasini yuboradi. Buni referal hisobini avtomatik
# to'g'irlash uchun ishlatamiz: agar taklif qilingan odam botni
# bloklasa, uni taklif qilgan odamning FAOL referal soni -1 bo'ladi
# (yozuv o'chirilmaydi, faqat "nofaol" deb belgilanadi).
@router.my_chat_member()
async def on_bot_blocked_or_unblocked(update: ChatMemberUpdated):
    if update.chat.type != "private":
        return
    user_id = update.chat.id
    new_status = update.new_chat_member.status
    try:
        if new_status in ("kicked", "left"):
            # Foydalanuvchi botni bloklagan
            referrer_id = await db.deactivate_referral(user_id)
            if referrer_id:
                await log_event(
                    f"{E('cross')} <b>Referal nofaol bo'ldi</b> (foydalanuvchi botni bloklagan)\n\n"
                    f"{E('profile')} Referrer: <code>{referrer_id}</code>\n"
                    f"{E('profile')} Bloklagan: <code>{user_id}</code>"
                )
        elif new_status == "member":
            # Foydalanuvchi botni blokdan chiqargan / qayta ishga tushirgan
            referrer_id = await db.reactivate_referral(user_id)
            if referrer_id:
                await log_event(
                    f"{E('check')} <b>Referal qayta faollashdi</b> (foydalanuvchi botni blokdan chiqargan)\n\n"
                    f"{E('profile')} Referrer: <code>{referrer_id}</code>\n"
                    f"{E('profile')} Foydalanuvchi: <code>{user_id}</code>"
                )
    except Exception as e:
        print(f"⚠️ my_chat_member hodisasini qayta ishlashda xatolik: {e}")

# ══════════════════════════════════════════════════════════════
#  TO'LOVNI TASDIQLASH (admin qo'lda tasdiqlaydi)
# ══════════════════════════════════════════════════════════════

async def confirm_payment(pay_id: str, user_id: int, amount: int, fullname: str):
    """Adminning tasdig'idan so'ng balansga pul qo'shadi, xabar yuboradi va
    bухgalteriya kitobiga (transactions) yozib qo'yadi."""
    await db.update_balance(user_id, amount)
    await db.update_total_deposited(user_id, amount)
    await db.delete_pending_payment(pay_id)
    await db.log_transaction(user_id, "topup", amount, note="admin_tasdiq")
    user_balance = await db.get_balance(user_id)

    try:
        await bot.send_message(
            user_id,
            f"{E('check')} <b>To'lovingiz tasdiqlandi!</b>\n\n"
            f"{E('money')} Hisobingizga <b>{amount:,} so'm</b> qo'shildi.\n"
            f"{E('wallet')} Joriy balans: <b>{user_balance:,} so'm</b>\n\n"
            f"Endi xohlagan xizmatdan foydalanishingiz mumkin {E('party')}"
        )
    except Exception:
        pass

    sav = now_tashkent().strftime("%H:%M:%S | %d.%m.%Y")
    try:
        await bot.send_message(
            ADMIN_ID,
            f"{E('check')} To'lov tasdiqlandi!\n\n"
            f"{E('profile')} {fullname} (<code>{user_id}</code>)\n"
            f"{E('money')} +{amount:,} so'm\n"
            f"{E('wallet')} Yangi balans: {user_balance:,} so'm\n"
            f"{E('clock')} {sav}"
        )
    except Exception:
        pass

    await log_event(
        f"{E('card')} <b>Qo'lda to'lov tasdiqlandi</b>\n\n"
        f"{E('profile')} {fullname} (<code>{user_id}</code>)\n"
        f"{E('money')} +{amount:,} so'm\n"
        f"{E('wallet')} Yangi balans: {user_balance:,} so'm"
    )


# ─── ADMIN BUYRUQ: QO'LDA BALANS QO'SHISH ─────────────────────
@admin_router.message(F.text.regexp(r'^/addbal\s+\d+\s+\d+$'))
async def addbal_cmd(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    parts  = msg.text.strip().split()
    uid    = int(parts[1])
    amount = int(parts[2])
    user = await db.get_user(uid)
    if not user:
        return await msg.answer("❌ Bunday ID li foydalanuvchi topilmadi. ID ni tekshirib qayta yuboring.")
    await db.update_balance(uid, amount)
    await db.update_total_deposited(uid, amount)
    await db.log_transaction(uid, "topup", amount, note="admin_addbal")
    bal = await db.get_balance(uid)
    try:
        await bot.send_message(uid, f"{E('check')} Hisobingizga <b>{amount:,} so'm</b> qo'shildi!\n{E('wallet')} Balans: <b>{bal:,} so'm</b>")
    except Exception:
        pass
    await msg.answer(f"{E('check')} {user['fullname']} ga {amount:,} so'm qo'shildi.\n{E('wallet')} Yangi balans: {bal:,} so'm")

# ══════════════════════════════════════════════════════════════
#  HISOB TO'LDIRISH — foydalanuvchi chek yuboradi, admin tasdiqlaydi
# ══════════════════════════════════════════════════════════════

TOPUP_INTRO = (
    f"{E('card')} <b>Hisob to'ldirish</b>\n\n"
    f"To'lov jarayoni juda oddiy — hammasi qo'lda, admin tomonidan tekshiriladi:\n\n"
    f"{E('one')} Summani kiritasiz\n"
    f"{E('two')} Ko'rsatilgan kartaga pul o'tkazasiz\n"
    f"{E('camera')} Chek (skrinshot) rasmini yuborasiz\n"
    f"{E('check')} Admin tasdiqlaydi — balansingiz to'ldiriladi ✅\n\n"
    f"{E('sparkle')} Endi qancha to'lov qilmoqchi ekaningizni son ko'rinishida yozing (masalan: <code>50000</code>)\n\n"
    f"{E('point')} Minimal: <b>1 000 so'm</b>\n"
    f"{E('point')} Maksimal: <b>10 000 000 so'm</b>"
)

@router.message(F.text.func(is_cancel_text), StateFilter(PayStates.wait_amount, PayStates.wait_check, AutoPayStates.wait_amount))
async def pay_cancel_any(msg: Message, state: FSMContext):
    data   = await state.get_data()
    pay_id = data.get("pay_id")
    if pay_id:
        await db.delete_pending_payment(pay_id)
    auto_id = data.get("auto_payment_id")
    if auto_id:
        await db.cancel_auto_payment(auto_id)
    await state.clear()
    await msg.answer("❌ To'lov bekor qilindi.", reply_markup=build_main_menu())


@router.message(F.text.contains("Hisob to'ldirish"))
async def topup_menu(msg: Message, state: FSMContext):
    if await is_auto_pay_enabled():
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [ib_button("Avtomatik (tezkor)", "rocket", style="success", callback_data="topup_auto")],
            [ib_button("Qo'lda (chek orqali)", "camera", style="primary", callback_data="topup_manual")],
        ])
        await msg.answer(
            f"{E('card')} <b>Hisob to'ldirish</b>\n\n"
            f"{E('rocket')} <b>Avtomatik</b> — kartaga pul o'tkazasiz, bot xabarni o'zi ko'rib, "
            f"balansingizni bir necha soniyada to'ldiradi. Chek yuborish shart emas!\n\n"
            f"{E('camera')} <b>Qo'lda</b> — pul o'tkazib, chekni yuborasiz, admin tasdiqlaydi.\n\n"
            f"{E('point')} Qaysi usulni tanlaysiz?",
            reply_markup=kb
        )
        return
    await topup_manual_start(msg, state)


async def topup_manual_start(msg: Message, state: FSMContext):
    await msg.answer(
        TOPUP_INTRO + f"\n\n{E('warn')} Summani diqqat bilan tekshiring — botga tushgan mablag' qaytarilmaydi.\n"
        "Fikringizdan qaytsangiz, pastdagi «❌ Bekor qilish» tugmasini bosing.",
        reply_markup=cancel_kb
    )
    await state.set_state(PayStates.wait_amount)


@router.callback_query(F.data == "topup_manual")
async def topup_manual_cb(call: CallbackQuery, state: FSMContext):
    await call.message.delete()
    await topup_manual_start(call.message, state)
    await call.answer()


@router.callback_query(F.data == "topup_auto")
async def topup_auto_cb(call: CallbackQuery, state: FSMContext):
    try:
        await call.message.delete()
    except Exception:
        pass
    try:
        await call.message.answer(
            f"{E('rocket')} <b>Avtomatik to'lov</b>\n\n"
            f"Qancha to'lov qilmoqchi ekaningizni son ko'rinishida yozing (masalan: <code>50000</code>).\n\n"
            f"{E('point')} Minimal: <b>1 000 so'm</b>\n"
            f"{E('point')} Maksimal: <b>10 000 000 so'm</b>",
            reply_markup=cancel_kb
        )
        await state.set_state(AutoPayStates.wait_amount)
    except Exception as e:
        import traceback
        print("‼️ topup_auto_cb XATOLIK:")
        traceback.print_exc()
        try:
            await call.message.answer(f"⚠️ Texnik xatolik: <code>{type(e).__name__}: {e}</code>")
        except Exception:
            pass
    await call.answer()


@router.message(AutoPayStates.wait_amount)
async def autopay_amount_received(msg: Message, state: FSMContext):
    try:
        amount = parse_amount(msg.text)
        if amount is None:
            return await msg.answer("❌ Faqat son kiriting, yoki bekor qilish uchun pastdagi tugmani bosing.")
        if not (1000 <= amount <= 10_000_000):
            await msg.answer(
                f"{E('cross')} Kiritilgan summa chegaradan tashqarida.\n"
                f"{E('point')} Minimal: <b>1 000 so'm</b>\n"
                f"{E('point')} Maksimal: <b>10 000 000 so'm</b>"
            )
            return

        reservation = await reserve_auto_amount(msg.from_user.id, amount)
        if not reservation:
            await msg.answer(
                f"{E('warn')} Hozir juda ko'p odam to'lov qilmoqchi — barcha kartalarda joy tugagan.\n"
                f"Birozdan so'ng qayta urinib ko'ring, yoki «✍️ Qo'lda» usulidan foydalaning.",
                reply_markup=build_main_menu()
            )
            await state.clear()
            return

        final_amount = reservation["final_amount"]
        offset       = reservation["offset"]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [ib_button("Bekor qilish", "cross", style="danger", callback_data=f"cancel_auto:{reservation['payment_id']}")],
        ])
        await state.update_data(auto_payment_id=reservation["payment_id"])

        await msg.answer(
            f"{E('receipt')} <b>To'lov ma'lumotlari</b>\n\n"
            f"{E('card')} Karta raqami: <code>{reservation['card_number']}</code>\n"
            f"{E('profile')} Karta egasi: <b>{reservation['card_owner']}</b>\n\n"
            f"<blockquote><b>❗️❗️❗️ FAQAT VA FAQAT quyidagi ANIQ summani yuboring:</b>\n\n"
            f"👉 <b>{final_amount:,} so'm</b> 👈\n\n"
            f"<b>Boshqa summa (masalan {amount:,} so'm yoki boshqa dumaloq son) yuborsangiz — "
            f"bot buni AVTOMATIK ANIQLAY OLMAYDI va pulingiz balansga tushmaydi!</b></blockquote>\n\n"
            f"{E('rocket')} Pul kartaga tushishi bilan, balansingiz {reservation['expiry_min']} daqiqa ichida "
            f"AVTOMATIK to'ldiriladi — chek yuborish shart emas!\n\n"
            f"{E('hourglass')} <i>{reservation['expiry_min']} daqiqa ichida pul tushmasa, bu band bekor bo'ladi.</i>\n\n"
            f"{E('warn')} <b>Yana bir bor: aynan {final_amount:,} so'm, 1 tiyin ham ko'p yoki kam emas!</b>",
            reply_markup=kb
        )
        await state.clear()
        asyncio.create_task(_autopay_timeout(reservation["payment_id"], msg.from_user.id, final_amount, reservation["expiry_min"]))
    except Exception as e:
        import traceback
        print("‼️ autopay_amount_received XATOLIK:")
        traceback.print_exc()
        try:
            await msg.answer(
                f"⚠️ Texnik xatolik yuz berdi: <code>{type(e).__name__}: {e}</code>\n\n"
                f"Iltimos bu xabarni skrinshot qilib adminга/dasturchiga yuboring.",
                reply_markup=build_main_menu()
            )
        except Exception:
            pass
        await state.clear()


async def _autopay_timeout(payment_id: int, user_id: int, final_amount: int, expiry_min: int):
    await asyncio.sleep(expiry_min * 60)
    payment = await db.get_auto_payment(payment_id)
    if payment and payment["status"] == "pending":
        await db.cancel_auto_payment(payment_id)
        try:
            await bot.send_message(
                user_id,
                f"{E('hourglass')} <b>{final_amount:,} so'm</b>lik avtomatik to'lov muddati tugadi, bekor qilindi.\n"
                f"Qayta urinish uchun «{E('card')} Hisob to'ldirish» tugmasini bosing.",
                reply_markup=build_main_menu()
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("cancel_auto:"))
async def cancel_auto_cb(call: CallbackQuery):
    payment_id = int(call.data.split(":")[1])
    payment = await db.get_auto_payment(payment_id)
    if payment and payment["user_id"] == call.from_user.id and payment["status"] == "pending":
        await db.cancel_auto_payment(payment_id)
        await call.message.edit_text(f"{E('cross')} To'lov bekor qilindi.")
    await call.answer()


@router.message(PayStates.wait_amount)
async def pay_amount_received(msg: Message, state: FSMContext):
    amount = parse_amount(msg.text)
    if amount is None:
        await msg.answer("❌ Iltimos, faqat son (raqam) kiriting. Masalan: 50000", reply_markup=cancel_kb)
        return

    if not (1000 <= amount <= 10_000_000):
        await msg.answer(
            f"{E('cross')} Kiritilgan summa chegaradan tashqarida.\n"
            f"{E('point')} Minimal: <b>1 000 so'm</b>\n"
            f"{E('point')} Maksimal: <b>10 000 000 so'm</b>\n\n"
            f"Iltimos, to'g'ri summani qayta kiriting.",
            reply_markup=cancel_kb
        )
        return

    user_id  = msg.from_user.id
    fullname = msg.from_user.full_name

    pay_id = f"pay_{user_id}_{amount}_{msg.message_id}"
    await db.add_pending_payment(pay_id, user_id, amount, fullname)

    card  = await get_setting("card_number", CARD_NUMBER)
    owner = await get_setting("card_owner", CARD_OWNER)

    await state.update_data(pay_id=pay_id, pay_amount=amount)
    await state.set_state(PayStates.wait_check)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ib_button("Bekor qilish", "cross", style="danger", callback_data=f"cancel_pay:{pay_id}")],
    ])

    await msg.answer(
        f"{E('receipt')} <b>To'lov ma'lumotlari</b>\n\n"
        f"{E('money')} Summa: <b>{amount:,} so'm</b>\n"
        f"{E('card')} Karta raqami: <code>{card}</code>\n"
        f"{E('profile')} Karta egasi: <b>{owner}</b>\n\n"
        f"{E('one')} Yuqoridagi kartaga ko'rsatilgan summani o'tkazing.\n"
        f"{E('two')} To'lov chekining (skrinshot) rasmini shu yerga yuboring {E('camera')}\n\n"
        f"{E('check')} Admin chekni ko'rib, tasdiqlashi bilan pul balansingizga tushadi.\n\n"
        f"{E('hourglass')} <i>5 daqiqa ichida chek yuborilmasa, bu to'lov avtomatik bekor bo'ladi.</i>",
        reply_markup=kb
    )

    asyncio.create_task(_pay_timeout(pay_id, user_id, amount))


async def _pay_timeout(pay_id: str, user_id: int, amount: int):
    await asyncio.sleep(300)
    pay = await db.get_pending_payment(pay_id)
    if pay:
        await db.delete_pending_payment(pay_id)
        try:
            await bot.send_message(
                user_id,
                f"{E('hourglass')} <b>{amount:,} so'm</b>lik to'lovingiz uchun berilgan vaqt tugadi, shuning uchun bekor qilindi.\n"
                f"Qayta urinish uchun «{E('card')} Hisob to'ldirish» tugmasini bosing.",
                reply_markup=build_main_menu()
            )
        except Exception:
            pass


@router.message(PayStates.wait_check, F.photo)
async def pay_check_received(msg: Message, state: FSMContext):
    data   = await state.get_data()
    pay_id = data.get("pay_id")
    pay    = await db.get_pending_payment(pay_id) if pay_id else None

    if not pay:
        await msg.answer(
            f"{E('cross')} Bu to'lovning muddati tugagan yoki u bekor qilingan.\n"
            f"Qaytadan «{E('card')} Hisob to'ldirish» tugmasini bosing.",
            reply_markup=build_main_menu()
        )
        await state.clear()
        return

    photo_id = msg.photo[-1].file_id
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        ib_button("Tasdiqlash", "check", style="success", callback_data=f"pay_ok:{pay_id}"),
        ib_button("Rad etish",  "cross", style="danger",  callback_data=f"pay_no:{pay_id}"),
    ]])

    try:
        await bot.send_photo(
            ADMIN_ID,
            photo_id,
            caption=(
                f"{E('receipt')} <b>Yangi to'lov cheki!</b>\n\n"
                f"{E('profile')} {pay['fullname']} (<code>{pay['user_id']}</code>)\n"
                f"{E('money')} Miqdor: <b>{pay['amount']:,} so'm</b>"
            ),
            reply_markup=kb
        )
    except Exception:
        await msg.answer(f"{E('cross')} Chekni yuborishda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.")
        return

    await msg.answer(
        f"{E('check')} Chekingiz qabul qilindi va adminga yuborildi!\n"
        f"{E('hourglass')} Tasdiqlanishini kuting — bu odatda uzoq davom etmaydi {E('sparkle')}",
        reply_markup=build_main_menu()
    )
    await state.clear()


@router.message(PayStates.wait_check)
async def pay_check_wrong_type(msg: Message):
    await msg.answer(f"{E('camera')} Iltimos, to'lov chekining rasmini (skrinshotini) yuboring. Matn yoki fayl qabul qilinmaydi.")


@admin_router.callback_query(F.data.startswith("pay_ok:"))
async def pay_ok_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    pay_id = call.data.split(":", 1)[1]
    pay = await db.get_pending_payment(pay_id)
    if not pay:
        return await call.answer("❌ Bu to'lov allaqachon ko'rib chiqilgan!", show_alert=True)

    await confirm_payment(pay["pay_id"], pay["user_id"], pay["amount"], pay["fullname"])
    try:
        await call.message.edit_caption(caption=(call.message.caption or "") + "\n\n✅ Tasdiqlandi!")
    except TelegramBadRequest:
        pass
    await call.answer("✅ Tasdiqlandi!")


@admin_router.callback_query(F.data.startswith("pay_no:"))
async def pay_no_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    pay_id = call.data.split(":", 1)[1]
    pay = await db.get_pending_payment(pay_id)
    if not pay:
        return await call.answer("❌ Bu to'lov allaqachon ko'rib chiqilgan!", show_alert=True)

    await db.delete_pending_payment(pay_id)
    try:
        await bot.send_message(
            pay["user_id"],
            f"{E('cross')} <b>{pay['amount']:,} so'm</b>lik to'lovingiz rad etildi.\n"
            f"Xato deb hisoblasangiz, «{E('support')} Qo'llab-quvvatlash» orqali admin bilan bog'laning."
        )
    except Exception:
        pass
    try:
        await call.message.edit_caption(caption=(call.message.caption or "") + "\n\n❌ Rad etildi!")
    except TelegramBadRequest:
        pass
    await call.answer("❌ Rad etildi!")


@router.callback_query(F.data.startswith("cancel_pay:"))
async def cancel_pay_cb(call: CallbackQuery, state: FSMContext):
    pay_id = call.data.split(":", 1)[1]
    pay = await db.get_pending_payment(pay_id)
    if pay:
        await db.delete_pending_payment(pay_id)
    await state.clear()
    await call.message.edit_text("❌ To'lov bekor qilindi.")
    try:
        await bot.send_message(call.from_user.id, "Bosh menyuga qaytdingiz.", reply_markup=build_main_menu())
    except Exception:
        pass
    await call.answer()

# ══════════════════════════════════════════════════════════════
#  OBUNA, START, TELEFON
# ══════════════════════════════════════════════════════════════

async def check_subscription(user_id: int) -> bool:
    try:
        ch_id = await get_setting("required_channel_id", CHANNEL_ID)
        m = await bot.get_chat_member(int(ch_id), user_id)
        return m.status in ("creator", "administrator", "member")
    except TelegramBadRequest:
        return False
    except Exception:
        return True

async def get_sub_kb():
    ch_un = await get_setting("required_channel_username", CHANNEL_USERNAME)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Kanalga obuna bo'lish", url=f"https://t.me/{ch_un}")],
        [InlineKeyboardButton(text="✅ Obuna bo'ldim", callback_data="check_sub")]
    ])

def welcome_text(first_name: str) -> str:
    name = (first_name or "").strip()
    hello = f"{E('welcome')} Assalomu alaykum, <b>{name}</b>! {E('sparkle')}" if name else f"{E('welcome')} Assalomu alaykum! {E('sparkle')}"
    return (
        f"{hello}\n\n"
        f"Botimizga xush kelibsiz! Bu yerda siz:\n\n"
        f"{E('phone')} Telegram uchun virtual raqamlar sotib olasiz\n"
        f"{E('card')} Hisobingizni bir necha soniyada to'ldirasiz\n"
        f"{E('referral')} Do'stlaringizni taklif qilib pul ishlaysiz\n"
        f"{E('gift')} Har kuni bepul bonus olasiz\n\n"
        f"Kerakli bo'limni pastdagi menyudan tanlang {E('down')}"
    )

async def send_main(target, first_name: str = ""):
    text = welcome_text(first_name)
    if isinstance(target, Message):
        await target.answer(text, reply_markup=build_main_menu())
    else:
        await target.message.answer(text, reply_markup=build_main_menu())

async def _extract_referrer_id(msg: Message) -> int | None:
    args = msg.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1][4:])
            if referrer_id != msg.from_user.id:
                return referrer_id
        except ValueError:
            pass
    return None

@router.message(CommandStart())
async def start_handler(msg: Message, state: FSMContext):
    await state.clear()
    referrer_id = await _extract_referrer_id(msg)

    if referrer_id:
        await db.add_pending_referrer(msg.from_user.id, referrer_id)

    if not await check_subscription(msg.from_user.id):
        await msg.answer(
            "⚠️ Botdan foydalanish uchun avval quyidagi kanalga obuna bo'ling, "
            "so'ng «✅ Obuna bo'ldim» tugmasini bosing:",
            reply_markup=await get_sub_kb()
        )
        return

    # Foydalanuvchi qatorini DARHOL yaratamiz (telefon hali bo'lmasa ham) —
    # shunda "ro'yxatdan to'liq o'tmaganlar" eslatmasi va yangi a'zo haqida
    # kanalga xabar berish to'g'ri ishlaydi.
    is_new = await db.add_user(msg.from_user.id, msg.from_user.full_name, str(msg.from_user.username or ""), referrer_id)
    if is_new:
        await notify_new_member(msg.from_user, referrer_id)

    user = await db.get_user(msg.from_user.id)
    if not user or not user.get("phone"):
        await msg.answer(
            "📲 Botdan foydalanishni davom ettirish uchun quyidagi "
            "«Telefon raqamni yuborish» tugmasini bosing. Raqamingiz faqat "
            "hisobingizni tasdiqlash uchun ishlatiladi:",
            reply_markup=phone_request_kb()
        )
        await state.set_state(PhoneState.wait_phone)
        return

    await send_main(msg, msg.from_user.first_name)

@router.callback_query(F.data == "check_sub")
async def check_sub_cb(call: CallbackQuery, state: FSMContext):
    if not await check_subscription(call.from_user.id):
        return await call.answer("❌ Siz hali kanalga obuna bo'lmadingiz. Avval obuna bo'lib, keyin qayta urinib ko'ring.", show_alert=True)
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass

    user = await db.get_user(call.from_user.id)
    if not user or not user.get("phone"):
        await bot.send_message(
            call.from_user.id,
            "✅ Obuna tasdiqlandi!\n\n"
            "📲 Endi botdan foydalanish uchun quyidagi "
            "«Telefon raqamni yuborish» tugmasini bosing:",
            reply_markup=phone_request_kb()
        )
        await state.set_state(PhoneState.wait_phone)
        await call.answer()
        return

    await bot.send_message(call.from_user.id, "✅ Obuna tasdiqlandi!", reply_markup=build_main_menu())
    await call.answer()

@router.message(F.content_type == "contact")
async def phone_received(msg: Message, state: FSMContext):
    if msg.contact.user_id and msg.contact.user_id != msg.from_user.id:
        await msg.answer("⚠️ Iltimos, faqat o'zingizga tegishli telefon raqamni yuboring.", reply_markup=phone_request_kb())
        return

    phone = msg.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone

    user_id  = msg.from_user.id
    fullname = msg.from_user.full_name
    username = str(msg.from_user.username or "")

    data = await state.get_data()
    referrer_id = data.get("referrer_id") or await db.get_pending_referrer(user_id)
    await state.clear()

    already_had_phone = bool((await db.get_user(user_id) or {}).get("phone"))

    is_new = await db.add_user(user_id, fullname, username, referrer_id)
    await db.update_phone(user_id, phone)
    await db.delete_pending_referrer(user_id)
    if is_new:
        await notify_new_member(msg.from_user, referrer_id)

    if referrer_id and referrer_id != user_id and not already_had_phone and await check_subscription(user_id):
        if phone.startswith("+998"):
            ref_bonus = int(await get_setting("referral_bonus", 500))
            await db.update_balance(referrer_id, ref_bonus)
            await db.add_referral(referrer_id, user_id)
            await db.log_transaction(referrer_id, "referral_bonus", ref_bonus, note=f"referred:{user_id}")
            try:
                await bot.send_message(
                    referrer_id,
                    f"{E('party')} Siz taklif qilgan foydalanuvchi botga qo'shildi!\n"
                    f"{E('money')} Hisobingizga <b>{ref_bonus:,} so'm</b> qo'shildi!"
                )
            except (TelegramForbiddenError, TelegramBadRequest):
                pass
        else:
            await db.add_referral(referrer_id, user_id)

    await msg.answer(f"{E('check')} Telefon raqamingiz tasdiqlandi!", reply_markup=ReplyKeyboardRemove())
    await send_main(msg, msg.from_user.first_name)

@router.message(PhoneState.wait_phone)
async def phone_wrong(msg: Message):
    await msg.answer("⚠️ Iltimos, pastdagi «📲 Telefon raqamni yuborish» tugmasini bosing — matn kiritish shart emas.", reply_markup=phone_request_kb())

# ─── HISOBIM ───────────────────────────────────────────────────
@router.message(F.text.contains("Hisobim"))
async def show_balance(msg: Message):
    user = await db.get_user(msg.from_user.id)
    if not user:
        is_new = await db.add_user(msg.from_user.id, msg.from_user.full_name, "")
        if is_new:
            await notify_new_member(msg.from_user, None)
        user = await db.get_user(msg.from_user.id)
    purchases = await db.get_purchases(msg.from_user.id)
    text = (
        f"{E('profile')} <b>Shaxsiy kabinetingiz</b>\n\n"
        f"🆔 Tartib raqami: <b>{user['tartib_id']}</b>\n"
        f"🆔 Telegram ID: <code>{user['user_id']}</code>\n"
        f"{E('phone')} Telefon: <code>{user['phone'] or 'Kiritilmagan'}</code>\n\n"
        f"{E('wallet')} Balans: <b>{user['balance']:,.0f} so'm</b>\n"
        f"{E('money')} Jami to'ldirilgan: <b>{user['total_deposited']:,.0f} so'm</b>\n"
        f"{E('cart')} Xaridlar soni: <b>{len(purchases)} ta</b>\n\n"
        f"Hisobingizni to'ldirib, xizmatlardan bemalol foydalanishda davom eting {E('fire')}"
    )
    orders_channel = await get_setting("orders_channel_username", "")
    buttons = [[InlineKeyboardButton(text="💳 Hisob to'ldirish", callback_data="goto_topup")]]
    if orders_channel:
        buttons.append([InlineKeyboardButton(text="📦 Buyurtmalar kanali", url=f"https://t.me/{orders_channel}")])
    await msg.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data == "goto_topup")
async def goto_topup(call: CallbackQuery, state: FSMContext):
    await topup_menu(call.message, state)
    await call.answer()

# ─── NOMER OLISH ───────────────────────────────────────────────
async def build_countries_page(page: int):
    data = await api_available_countries()
    if data.get("status") != "ok" or not data.get("countries"):
        return None, 0
    countries        = data["countries"]
    filtered         = {k: v for k, v in countries.items() if k.upper() not in BLOCKED_COUNTRIES}
    sorted_countries = sort_uz_first(filtered.items(), key_func=lambda x: float(x[1].get("price", 999)))
    markup_prices    = await db.get_all_markup_prices()
    total_pages      = (len(sorted_countries) - 1) // 10 + 1 if sorted_countries else 0
    start = page * 10
    end   = start + 10
    buttons = []
    for code, info in sorted_countries[start:end]:
        qty       = info.get("qty", 0)
        name      = clean_country_name(info.get("name", code))
        usd_price = float(info.get("price", 1))
        uzs_price = markup_prices.get(code.upper()) or await calc_default_price(usd_price)
        flag      = country_flag(code)
        buttons.append([ib_button(
            f"{name} {flag} — {uzs_price:,} so'm -| {qty} dona", "phone",
            callback_data=f"buy:{code}:{uzs_price}"
        )])
    nav = []
    if page > 0:
        nav.append(ib_button("", "arrow_l", style="primary", callback_data=f"countries_page:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if end < len(sorted_countries):
        nav.append(ib_button("", "arrow_r", style="primary", callback_data=f"countries_page:{page+1}"))
    buttons.append(nav)
    buttons.append([
        ib_button("TOP 10 davlatlar", "chart", callback_data="top10_countries"),
        ib_button("Arzon raqamlar",   "fire",  callback_data="cheap_countries"),
    ])
    return buttons, total_pages

@router.message(F.text.contains("Nomer olish"))
async def get_number_menu(msg: Message):
    loading_msg = await msg.answer("⏳ Mavjud davlatlar ro'yxati yuklanmoqda...")
    buttons, total_pages = await build_countries_page(0)
    try:
        await loading_msg.delete()
    except Exception:
        pass
    if not buttons:
        await msg.answer("❌ Hozircha davlatlar ro'yxatini olib bo'lmadi. Iltimos, birozdan so'ng qayta urinib ko'ring.")
        return
    await msg.answer(
        f"{E('globe')} <b>Mavjud davlatlar ro'yxati:</b>\n<i>{page_label(0, total_pages)}</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

def page_label(page: int, total_pages: int) -> str:
    return f"{page+1}/{total_pages}" if total_pages else "0/0"

@router.callback_query(F.data.startswith("countries_page:"))
async def countries_page_cb(call: CallbackQuery):
    page = int(call.data.split(":")[1])
    buttons, total_pages = await build_countries_page(page)
    if not buttons:
        return await call.answer("❌ Ro'yxatni yuklab bo'lmadi, birozdan so'ng qayta urinib ko'ring.", show_alert=True)
    try:
        await call.message.edit_text(
            f"{E('globe')} <b>Mavjud davlatlar ro'yxati:</b>\n<i>{page_label(page, total_pages)}</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
    except Exception:
        pass
    await call.answer()

@router.callback_query(F.data == "top10_countries")
async def top10_countries(call: CallbackQuery):
    data = await api_available_countries()
    if data.get("status") != "ok":
        return await call.answer("❌ Ro'yxatni yuklab bo'lmadi.", show_alert=True)
    countries     = data["countries"]
    filtered      = {k: v for k, v in countries.items() if k.upper() not in BLOCKED_COUNTRIES}
    sorted_c      = sort_uz_first(filtered.items(), key_func=lambda x: int(x[1].get("qty", 0)), reverse=True)[:10]
    markup_prices = await db.get_all_markup_prices()
    buttons = []
    for code, info in sorted_c:
        qty       = info.get("qty", 0)
        name      = clean_country_name(info.get("name", code))
        usd_price = float(info.get("price", 1))
        uzs_price = markup_prices.get(code.upper()) or await calc_default_price(usd_price)
        flag      = country_flag(code)
        buttons.append([ib_button(
            f"{name} {flag} — {uzs_price:,} so'm -| {qty} dona", "phone",
            callback_data=f"buy:{code}:{uzs_price}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="countries_page:0")])
    await call.message.edit_text(f"{E('chart')} <b>TOP 10 davlat (raqamlar soni bo'yicha):</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data == "cheap_countries")
async def cheap_countries(call: CallbackQuery):
    data = await api_available_countries()
    if data.get("status") != "ok":
        return await call.answer("❌ Ro'yxatni yuklab bo'lmadi.", show_alert=True)
    countries     = data["countries"]
    filtered      = {k: v for k, v in countries.items() if k.upper() not in BLOCKED_COUNTRIES}
    sorted_c      = sort_uz_first(filtered.items(), key_func=lambda x: float(x[1].get("price", 999)))[:10]
    markup_prices = await db.get_all_markup_prices()
    buttons = []
    for code, info in sorted_c:
        qty       = info.get("qty", 0)
        name      = clean_country_name(info.get("name", code))
        usd_price = float(info.get("price", 1))
        uzs_price = markup_prices.get(code.upper()) or await calc_default_price(usd_price)
        flag      = country_flag(code)
        buttons.append([ib_button(
            f"{name} {flag} — {uzs_price:,} so'm -| {qty} dona", "phone",
            callback_data=f"buy:{code}:{uzs_price}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="countries_page:0")])
    await call.message.edit_text(f"{E('fire')} <b>Eng arzon raqamlar:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer()

# ─── NOMER SOTIB OLISH (2 bosqichli tasdiqlash) ────────────────
async def _country_name(country_code: str) -> str:
    data = await api_available_countries()
    countries = data.get("countries", {}) if data.get("status") == "ok" else {}
    info = countries.get(country_code, {})
    return clean_country_name(info.get("name", country_code))

@router.callback_query(F.data.startswith("buy:"))
async def buy_number(call: CallbackQuery):
    """1-bosqich: tanlangan raqam haqida ma'lumot va ogohlantirish."""
    parts        = call.data.split(":")
    country_code = parts[1]
    uzs_price    = int(parts[2])
    user_id      = call.from_user.id
    if country_code.upper() in BLOCKED_COUNTRIES:
        return await call.answer("❌ Bu davlat uchun raqamlar mavjud emas.", show_alert=True)
    bal = await db.get_balance(user_id)
    if bal < uzs_price:
        return await call.answer(
            f"❌ Hisobingizda mablag' yetarli emas.\n"
            f"Raqam narxi: {uzs_price:,} so'm\nSizning balansingiz: {bal:,} so'm",
            show_alert=True
        )
    name = await _country_name(country_code)
    flag = country_flag(country_code)

    text = (
        f"{E('receipt')} <b>Siz sotib olmoqchi bo'layotgan raqam haqida ma'lumot:</b>\n\n"
        f"{E('globe')} Davlat: <b>{name} {flag}</b>\n"
        f"{E('money')} Narxi: <b>{uzs_price:,} so'm</b>\n\n"
        f"<blockquote>❗️ Diqqat: Botdan olingan raqamlar uchun hech qanday kafolat berilmaydi va pul qaytarilmaydi!</blockquote>\n\n"
        f"<blockquote>📌 Rasmiy Telegramdan olmang!!!\n"
        f"Agar siz rasmiy Telegram ilovasidan foydalansangiz, pulingiz 100% kuyadi va bu uchun bot va admin javobgar emas!\n"
        f"Faqat 🟢 Telegraph yoki Plus kabi ilovalardan foydalanish tavsiya etiladi!!!</blockquote>\n\n"
        f"{E('point')} Shartlar bilan to'liq tanishib chiqing va \"🛒 Sotib olish\" tugmasini bosing!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ib_button("Sotib olish", "cart", style="success", callback_data=f"terms:{country_code}:{uzs_price}")],
        [ib_button("Bekor qilish", "cross", style="danger", callback_data="countries_page:0")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("terms:"))
async def buy_terms(call: CallbackQuery):
    """2-bosqich: xizmat ko'rsatish shartlari va yakuniy tasdiq."""
    parts        = call.data.split(":")
    country_code = parts[1]
    uzs_price    = int(parts[2])
    user_id      = call.from_user.id
    bal = await db.get_balance(user_id)
    if bal < uzs_price:
        return await call.answer("❌ Balans yetarli emas.", show_alert=True)

    text = (
        "🚀 Bizning bot orqali taqdim etilayotgan akkauntlar — tayyor ochilgan Telegram "
        "akkauntlar bazasidan olinadi va sotib olishdan oldin ma'lumotlarni tekshirish "
        "sizning vazifangizdir:\n\n"
        "<blockquote>⚠️ Kod faqat 🟢 <b>Telegraph</b> ilovasi orqali yuborilishi lozim!\n"
        "Agar Telegramning rasmiy ilovasidan kod yuborilsa, kod yetib bormasligi yoki "
        "xatoliklar bo'lishi mumkin — buning uchun adminlar javobgar emas!</blockquote>\n\n"
        "<blockquote>‼️ Sotib olingan akkauntlar uchun hech qanday kafolat yo'q. Muammo "
        "bo'lsa, pulni qaytarish yoki almashtirish kafolati berilmaydi. ❌</blockquote>\n\n"
        "✅ Raqamni to'g'ri ishlatish, spamdan saqlash va xavfsizlik choralariga rioya "
        "qilish — butunlay foydalanuvchi mas'uliyatidadir 🛡\n\n"
        "👍 Qoidalar bilan tanishib chiqing va \"🟢 Davom etish\" tugmasini bosing!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Davom etish", callback_data=f"confirm_buy:{country_code}:{uzs_price}")],
        [InlineKeyboardButton(text="🚫 Bekor qilish", callback_data="countries_page:0")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

_purchase_locks: dict[int, asyncio.Lock] = {}

def get_purchase_lock(user_id: int) -> asyncio.Lock:
    lock = _purchase_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _purchase_locks[user_id] = lock
    return lock

@router.callback_query(F.data.startswith("confirm_buy:"))
async def confirm_buy(call: CallbackQuery):
    parts        = call.data.split(":")
    country_code = parts[1]
    uzs_price    = int(parts[2])
    user_id      = call.from_user.id

    lock = get_purchase_lock(user_id)
    if lock.locked():
        return await call.answer("⏳ Oldingi buyurtmangiz hali qayta ishlanmoqda, biroz kuting.", show_alert=True)

    async with lock:
        bal = await db.get_balance(user_id)
        if bal < uzs_price:
            return await call.answer("❌ Balans yetarli emas.", show_alert=True)

        await call.message.edit_text("⏳ Raqam sotib olinmoqda... Iltimos, kuting.")
        result = await api_buy_number(country_code)
        if result.get("status") != "ok":
            err = result.get("message", "Noma'lum xato")
            await call.message.edit_text(
                f"{E('cross')} Raqam olishda xatolik yuz berdi: {err}\n\n"
                f"Mablag'ingiz hisobingizdan yechilmadi — istasangiz qayta urinib ko'rishingiz mumkin.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Davlatlar ro'yxatiga qaytish", callback_data="countries_page:0")]
                ])
            )
            await call.answer()
            return

        number   = result.get("Number", "")
        api_name = clean_country_name(result.get("name", country_code))
        await db.update_balance(user_id, -uzs_price)
        await db.log_transaction(user_id, "purchase", -uzs_price, note=f"{country_code}:{number}")
        order_id = await db.log_purchase(user_id, number, country_code, api_name, uzs_price)
        updated_user = await db.get_user(user_id)
        if updated_user:
            await maybe_notify_low_balance(user_id, updated_user["balance"])
        try:
            orders_ch = await get_setting("orders_channel_id", ORDERS_CHANNEL)
            await bot.send_message(
                int(orders_ch),
                f"{E('cart')} <b>Yangi TG Akkaunt buyurtmasi</b>\n\n"
                f"{E('profile')} Foydalanuvchi: <code>{user_id}</code>\n"
                f"{E('globe')} Mamlakat: {api_name}\n"
                f"{E('phone')} Raqam: <code>{number}</code>\n"
                f"{E('money')} Narx: {uzs_price:,} so'm\n"
                f"🆔 Buyurtma #{order_id}"
            )
        except Exception:
            pass
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔑 SMS kodni olish", callback_data=f"getcode:{number}")],
            [InlineKeyboardButton(text="🛒 Buyurtmalarim", callback_data="my_orders_inline")],
        ])
        await call.message.edit_text(
            f"{E('check')} <b>Raqam muvaffaqiyatli olindi!</b>\n\n"
            f"{E('phone')} Raqamingiz: <code>{number}</code>\n"
            f"{E('money')} Narxi: {uzs_price:,} so'm\n\n"
            f"{E('warn')} Faqat norasmiy (Telegraph kabi) ilovalardan foydalaning.\n\n"
            f"{E('sparkle')} Kirish kodini olish uchun pastdagi «🔑 SMS kodni olish» tugmasini bosing.",
            reply_markup=kb
        )
    await call.answer()

@router.callback_query(F.data == "my_orders_inline")
async def my_orders_inline(call: CallbackQuery):
    await my_orders(call.message, override_user_id=call.from_user.id)
    await call.answer()

@router.callback_query(F.data.startswith("getcode:"))
async def get_code(call: CallbackQuery):
    number  = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    purchase = await db.get_purchase_by_phone(user_id, number)
    if not purchase:
        return await call.answer("❌ Bu raqam sizga tegishli emas.", show_alert=True)
    await call.answer("⏳ Kod olinmoqda...")
    result = await api_get_code(number)
    if result.get("status") != "ok":
        err = result.get("message", "Noma'lum xato")
        kb  = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Qayta urinish", callback_data=f"getcode:{number}")]
        ])
        return await call.message.edit_text(f"❌ Kod olishda xatolik: {err}\n\nBiroz kutib, qayta urinib ko'ring.", reply_markup=kb)
    code     = result.get("code", "Topilmadi")
    password = result.get("pass", "")
    text = f"📨 <b>{number}</b> raqami uchun kod:\n\n🔑 Kirish kodi: <code>{code}</code>\n"
    if password:
        text += f"🔐 2-bosqichli parol: <code>{password}</code>\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Yangi kod olish", callback_data=f"getcode:{number}")]
    ])
    await call.message.edit_text(text, reply_markup=kb)

# ─── BUYURTMALARIM ─────────────────────────────────────────────
@router.message(F.text.contains("Buyurtmalarim"))
async def my_orders(msg: Message, override_user_id: int | None = None):
    user_id = override_user_id or msg.from_user.id
    orders = await db.get_purchases(user_id)
    if not orders:
        await msg.answer("🛒 Siz hali raqam sotib olmagansiz.\n📞 Raqam olish uchun «📞 Nomer olish» tugmasini bosing.")
        return
    await msg.answer(f"🛒 <b>Sizning buyurtmalaringiz ({len(orders)} ta):</b>")
    for i, order in enumerate(orders[:20], 1):
        phone       = order['phone']
        country     = clean_country_name(order['country_name'])
        price       = order['price']
        bought_date = order['created_at']
        flag        = country_flag(order.get('country_code', ''))
        try:
            if isinstance(bought_date, str):
                d = datetime.strptime(bought_date[:19], '%Y-%m-%d %H:%M:%S')
            else:
                d = bought_date
            formatted = d.strftime('%d.%m.%Y %H:%M')
        except Exception:
            formatted = str(bought_date)
        text = (
            f"<b>{i}. 🌍 {country} {flag}</b>\n"
            f"📞 <code>{phone}</code>\n"
            f"💰 Narx: {price:,} so'm\n"
            f"📅 Sana: {formatted}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔑 SMS kodni olish", callback_data=f"getcode:{phone}")]
        ])
        await msg.answer(text, reply_markup=kb)

# ─── PUL ISHLASH ───────────────────────────────────────────────
@router.message(F.text.contains("Pul ishlash"))
async def earn_money(msg: Message, override_user_id: int | None = None):
    user_id = override_user_id or msg.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Referal orqali", callback_data=f"referral:{user_id}")],
        [InlineKeyboardButton(text="🎁 Kunlik bonus",   callback_data="daily_bonus")],
    ])
    await msg.answer("💸 <b>Pul ishlash uchun bo'limni tanlang:</b>", reply_markup=kb)

@router.callback_query(F.data.startswith("referral:"))
async def show_referral(call: CallbackQuery):
    user_id   = call.from_user.id
    me        = await bot.get_me()
    ref_link  = f"https://t.me/{me.username}?start=ref_{user_id}"
    ref_count = await db.get_referral_count(user_id)
    earnings  = await db.get_referral_earnings(user_id)
    ref_bonus = int(await get_setting("referral_bonus", 500))
    text = (
        f"{E('referral')} <b>Referal tizimi</b>\n\n"
        f"{E('link')} Sizning shaxsiy referal havolangiz:\n<code>{ref_link}</code>\n\n"
        f"{E('profile')} Taklif qilingan do'stlar: <b>{ref_count} ta</b>\n"
        f"{E('money')} Referaldan daromad: <b>{earnings:,} so'm</b>\n\n"
        f"{E('sparkle')} Har bir taklif qilgan do'stingiz uchun <b>{ref_bonus:,} so'm</b> bonus olasiz!\n"
        f"{E('warn')} Bonus faqat <b>+998</b> (O'zbekiston) raqamli foydalanuvchilar uchun beriladi."
    )
    buttons = []
    if (await get_setting("leaderboard_public", "0")) == "1":
        buttons.append([ib_button("Reytingni ko'rish", "star", style="primary", callback_data="user_leaderboard:all")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_earn")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data.startswith("user_leaderboard:"))
async def show_user_leaderboard(call: CallbackQuery):
    if (await get_setting("leaderboard_public", "0")) != "1":
        await call.answer("Bu bo'lim hozircha yopiq.", show_alert=True)
        return
    period = call.data.split(":")[1]
    period_labels = {"week": "Haftalik", "month": "Oylik", "year": "Yillik", "all": "Umumiy"}
    rows = await db.get_referral_leaderboard(period=period, limit=10)
    my_rank, my_count = await db.get_referral_rank(call.from_user.id, period=period)

    text = f"{E('star')} <b>Eng faol takliflar — {period_labels.get(period, 'Umumiy')}</b>\n\n"
    if not rows:
        text += "<i>Hozircha hech kim odam taklif qilmagan. Birinchi bo'ling!</i>"
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, r in enumerate(rows):
            medal = medals[i] if i < 3 else f"{i+1}."
            name = r["fullname"] or "Foydalanuvchi"
            text += f"{medal} <b>{name}</b> — <b>{r['cnt']}</b> ta\n"
        if my_rank:
            text += f"\n{E('sparkle')} Sizning o'rningiz: <b>{my_rank}-o'rin</b> ({my_count} ta)"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ib_button("Haftalik", callback_data="user_leaderboard:week", style=("primary" if period == "week" else None)),
         ib_button("Oylik", callback_data="user_leaderboard:month", style=("primary" if period == "month" else None))],
        [ib_button("Yillik", callback_data="user_leaderboard:year", style=("primary" if period == "year" else None)),
         ib_button("Umumiy", callback_data="user_leaderboard:all", style=("primary" if period == "all" else None))],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"referral:{call.from_user.id}")],
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@router.callback_query(F.data == "daily_bonus")
async def daily_bonus_cb(call: CallbackQuery):
    user_id     = call.from_user.id
    today       = now_tashkent().date().isoformat()
    last        = await db.get_last_bonus_date(user_id)
    daily_bonus = int(await get_setting("daily_bonus", 200))
    if last == today:
        await call.answer("❌ Bugungi bonusni allaqachon olib bo'lgansiz. Ertaga qayta urinib ko'ring!", show_alert=True)
        return
    await db.update_balance(user_id, daily_bonus)
    await db.set_last_bonus_date(user_id, today)
    await db.log_transaction(user_id, "daily_bonus", daily_bonus, note="kunlik_bonus")
    await call.answer(f"🎁 Kunlik bonus: {daily_bonus:,} so'm hisobingizga qo'shildi!", show_alert=True)

@router.callback_query(F.data == "back_earn")
async def back_earn(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await earn_money(call.message, override_user_id=call.from_user.id)
    await call.answer()

@router.message(F.text.contains("Qo'llanma"))
async def guide_menu(msg: Message):
    text = (
        f"<b>{E('guide')} Botdan foydalanish qo'llanmasi</b>\n\n"
        f"1️⃣ <b>{E('phone')} Nomer olish</b> — Telegram ro'yxatdan o'tish uchun virtual raqam sotib olasiz.\n"
        f"2️⃣ <b>{E('cart')} Buyurtmalarim</b> — sotib olgan raqamlaringiz va ulardan kod olish shu yerda.\n"
        f"3️⃣ <b>{E('money')} Hisobim</b> — joriy balansingiz va shaxsiy ma'lumotlaringiz.\n"
        f"4️⃣ <b>{E('card')} Hisob to'ldirish</b> — karta orqali qo'lda to'lov qilib, chek yuborasiz, admin tasdiqlaydi.\n"
        f"5️⃣ <b>{E('referral')} Pul ishlash</b> — do'stlaringizni taklif qilib yoki kunlik bonus orqali pul ishlaysiz.\n"
        f"6️⃣ <b>{E('support')} Qo'llab-quvvatlash</b> — savol yoki muammo bo'lsa, admin bilan bog'lanasiz.\n\n"
        f"Savollaringiz bo'lsa, «{E('support')} Qo'llab-quvvatlash» bo'limiga murojaat qiling."
    )
    await msg.answer(text)

@router.message(F.text.contains("Qo'llab-quvvatlash"))
async def support_menu(msg: Message):
    await msg.answer(
        f"{E('support')} <b>Qo'llab-quvvatlash</b>\n\n"
        f"Savol, muammo yoki taklifingiz bo'lsa, quyidagi havola orqali "
        f"to'g'ridan-to'g'ri adminga yozing:\n"
        f"<a href='tg://user?id={ADMIN_ID}'>{E('profile')} Admin bilan bog'lanish</a>"
    )

# ══════════════════════════════════════════════════════════════
#  ADMIN PANEL
# ══════════════════════════════════════════════════════════════

async def show_admin_panel(target):
    users_count     = await db.count_users()
    orders_count    = await db.count_orders()
    api_bal         = await api_get_balance_tglion()
    api_balance     = api_bal.get("balance", "N/A")
    daily_bonus_val = await get_setting("daily_bonus", 200)
    ref_bonus_val   = await get_setting("referral_bonus", 500)
    channel_un_val  = await get_setting("required_channel_username", CHANNEL_USERNAME)
    maintenance     = await is_maintenance_mode()
    text = (
        f"🔐 <b>Admin panel</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users_count}</b>\n"
        f"🛒 Jami buyurtmalar: <b>{orders_count}</b>\n"
        f"💰 TG-Lion balansi: <b>{api_balance}</b>\n\n"
        f"⚙️ <b>Joriy sozlamalar:</b>\n"
        f"🎁 Kunlik bonus: <b>{daily_bonus_val} so'm</b>\n"
        f"👥 Referal bonus: <b>{ref_bonus_val} so'm</b>\n"
        f"📢 Kanal: <b>@{channel_un_val}</b>\n"
        f"🔧 Ta'mirlash rejimi: <b>{'🔴 YOQILGAN' if maintenance else '🟢 O`CHIRILGAN'}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ib_button("Statistika", "chart", style="primary", callback_data="adm_stats"),
         ib_button("Foydalanuvchilar", "referral", callback_data="adm_users")],
        [ib_button("Kunlik hisobot", "report", callback_data="adm_daily_report"),
         ib_button("Foydalanuvchi profili", "profile", callback_data="adm_user_profile")],
        [ib_button("Balans o'zgartirish", "money", style="success", callback_data="adm_balance"),
         ib_button("Narx sozlash", "card", style="primary", callback_data="adm_prices")],
        [ib_button("Raqam qidirish", "search", callback_data="adm_search"),
         ib_button("Xabar yuborish", "bell", style="primary", callback_data="adm_broadcast")],
        [ib_button("Referal reytingi", "star", style="primary", callback_data="adm_leaderboard:all"),
         ib_button("Bot sozlamalari", "gear", callback_data="adm_settings")],
        [ib_button("Yangilash", "sparkle", callback_data="adm_refresh")],
        [ib_button(
            ("Ta'mirlashni o'chirish" if maintenance else "Ta'mirlash rejimini yoqish"), "warn",
            style=("success" if maintenance else "danger"),
            callback_data="adm_toggle_maintenance"
        )],
    ])
    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb)
    else:
        try:
            await target.message.edit_text(text, reply_markup=kb)
        except Exception:
            await target.message.answer(text, reply_markup=kb)
        await target.answer()

@admin_router.message(F.text == "/admin")
async def admin_cmd(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    await show_admin_panel(msg)

@admin_router.message(
    F.text.func(is_cancel_text),
    StateFilter(
        BalanceChangeState.wait_user_id, BalanceChangeState.wait_amount,
        AdminSearchState.wait_phone, AdminSearchState.wait_profile_id, BroadcastState.wait_message,
        AdminSettingsState.wait_daily_bonus, AdminSettingsState.wait_referral_bonus,
        AdminSettingsState.wait_bulk_percent, AdminSettingsState.wait_channel_id,
        AdminSettingsState.wait_channel_username, AdminSettingsState.wait_price_value,
        AdminSettingsState.wait_orders_channel_id, AdminSettingsState.wait_orders_channel_username,
        AdminSettingsState.wait_card_number, AdminSettingsState.wait_card_owner,
        AdminSettingsState.wait_exchange_rate, AdminSettingsState.wait_default_margin,
        EmojiState.wait_forward, AutoPayStates.wait_amount,
        AdminSettingsState.wait_card2_number, AdminSettingsState.wait_card2_owner,
        AdminSettingsState.wait_autopay_offset, AdminSettingsState.wait_autopay_expiry,
        AdminSettingsState.wait_new_user_channel_id, AdminSettingsState.wait_inactive_days,
        AdminSettingsState.wait_incomplete_hours, AdminSettingsState.wait_low_balance,
        HumoSetupStates.wait_api_id, HumoSetupStates.wait_api_hash, HumoSetupStates.wait_phone,
        HumoSetupStates.wait_code, HumoSetupStates.wait_password,
        TolovSetupStates.wait_shop_id, TolovSetupStates.wait_shop_key, TolovSetupStates.wait_api_url,
    )
)
async def admin_cancel_any(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    ctx = _humo_setup_ctx.pop(msg.from_user.id, None)
    if ctx and ctx.get("client"):
        try:
            await ctx["client"].disconnect()
        except Exception:
            pass
    await state.clear()
    await msg.answer("❌ Amal bekor qilindi.")
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "adm_refresh")
async def adm_refresh(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    await show_admin_panel(call)

@admin_router.callback_query(F.data == "adm_toggle_maintenance")
async def adm_toggle_maintenance(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    current = await is_maintenance_mode()
    await db.set_setting("maintenance_mode", "0" if current else "1")
    await call.answer("🟢 Ta'mirlash rejimi o'chirildi" if current else "🔴 Ta'mirlash rejimi yoqildi", show_alert=True)
    await show_admin_panel(call)

@admin_router.callback_query(F.data == "adm_stats")
async def adm_stats(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    stats         = await db.get_sales_stats()
    total_orders  = await db.count_orders()
    total_revenue = await db.get_total_revenue()
    text = "📊 <b>Sotuvlar statistikasi:</b>\n\n"
    for country, cnt, rev in stats:
        text += f"🌍 {country}: {cnt} ta — {rev:,} so'm\n"
    text += f"\n📦 Jami buyurtmalar: <b>{total_orders}</b>\n"
    text += f"💰 Jami daromad: <b>{total_revenue:,} so'm</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_refresh")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_leaderboard:"))
async def adm_leaderboard_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    period = call.data.split(":")[1]
    await render_leaderboard(call, period)

async def render_leaderboard(call: CallbackQuery, period: str):
    period_labels = {"week": "Haftalik", "month": "Oylik", "year": "Yillik", "all": "Umumiy"}
    rows = await db.get_referral_leaderboard(period=period, limit=15)

    text = f"{E('star')} <b>Referal reytingi — {period_labels.get(period, 'Umumiy')}</b>\n\n"
    if not rows:
        text += "<i>Hozircha hech kim odam taklif qilmagan.</i>"
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, r in enumerate(rows):
            medal = medals[i] if i < 3 else f"{i+1}."
            name = r["fullname"] or f"ID {r['referrer_id']}"
            uname = f" (@{r['username']})" if r.get("username") else ""
            text += f"{medal} <b>{name}</b>{uname} — <b>{r['cnt']}</b> ta\n"

    public_on = (await get_setting("leaderboard_public", "0")) == "1"
    kb_rows = [[
        ib_button("Haftalik", callback_data="adm_leaderboard:week", style=("primary" if period == "week" else None)),
        ib_button("Oylik", callback_data="adm_leaderboard:month", style=("primary" if period == "month" else None)),
    ], [
        ib_button("Yillik", callback_data="adm_leaderboard:year", style=("primary" if period == "year" else None)),
        ib_button("Umumiy", callback_data="adm_leaderboard:all", style=("primary" if period == "all" else None)),
    ], [
        ib_button(
            ("Foydalanuvchilarga yopish" if public_on else "Foydalanuvchilarga ochish"),
            style=("danger" if public_on else "success"),
            callback_data="toggle_leaderboard_public"
        )
    ], [ib_button("Orqaga", "arrow_l", style="danger", callback_data="adm_refresh")]]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await call.answer()

@admin_router.callback_query(F.data == "toggle_leaderboard_public")
async def toggle_leaderboard_public_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    new_val = "0" if (await get_setting("leaderboard_public", "0")) == "1" else "1"
    await db.set_setting("leaderboard_public", new_val)
    msg = "✅ Endi barcha foydalanuvchilar reytingni ko'ra oladi!" if new_val == "1" else "🚫 Reyting endi faqat admin uchun."
    await call.answer(msg, show_alert=True)
    await render_leaderboard(call, "all")

@admin_router.callback_query(F.data.startswith("adm_users"))
async def adm_users(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    parts = call.data.split(":")
    page = int(parts[1]) if len(parts) > 1 else 0
    per_page = 8
    users, total = await db.get_users_page(page=page, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)

    text = f"{E('referral')} <b>Foydalanuvchilar</b> ({total} ta)\n<i>{page+1}/{total_pages}-sahifa</i>\n\n"
    buttons = []
    for u in users:
        status = "🚫" if u["is_blocked"] else "✅"
        name = u["fullname"] or "Noma'lum"
        buttons.append([ib_button(
            f"{status} {name} — {u['balance']:,} so'm",
            callback_data=f"adm_user_view:{u['user_id']}:{page}"
        )])

    nav = []
    if page > 0:
        nav.append(ib_button("", "arrow_l", style="primary", callback_data=f"adm_users:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if (page + 1) * per_page < total:
        nav.append(ib_button("", "arrow_r", style="primary", callback_data=f"adm_users:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([ib_button("Orqaga", "arrow_l", style="danger", callback_data="adm_refresh")])

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_user_view:"))
async def adm_user_view(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    _, uid_str, page = call.data.split(":")
    await render_user_view(call, int(uid_str), page)

async def render_user_view(call: CallbackQuery, uid: int, page: str):
    u = await db.get_user(uid)
    if not u:
        await call.answer("Foydalanuvchi topilmadi.", show_alert=True)
        return
    purchases = await db.get_purchases(uid)
    status = "🚫 Bloklangan" if u["is_blocked"] else "✅ Faol"
    last_active = u.get("last_active")
    display_name = u['fullname'] or "Noma'lum"
    pending_payments = await db.get_user_pending_auto_payments(uid)
    pending_part = ""
    if pending_payments:
        pending_part = f"\n\n{E('hourglass')} <b>Kutilayotgan to'lov(lar):</b>\n"
        for p in pending_payments:
            pending_part += f"  • {p['final_amount']:,} so'm (ID: {p['id']})\n"
    text = (
        f"{E('profile')} <b>{display_name}</b>\n\n"
        f"🆔 Tartib: {u['tartib_id']} | Telegram ID: <code>{u['user_id']}</code>\n"
        f"👤 Username: @{u['username'] or '—'}\n"
        f"📱 Telefon: <code>{u['phone'] or 'Tasdiqlanmagan'}</code>\n"
        f"{E('wallet')} Balans: <b>{u['balance']:,} so'm</b>\n"
        f"{E('money')} Jami to'ldirgan: {u['total_deposited']:,} so'm\n"
        f"{E('cart')} Xaridlar: {len(purchases)} ta\n"
        f"📌 Holat: <b>{status}</b>\n"
        f"⏰ Oxirgi faollik: {last_active.strftime('%d.%m.%Y %H:%M') if last_active else '—'}\n"
        f"📅 Ro'yxatdan o'tgan: {u['created_at'].strftime('%d.%m.%Y') if u.get('created_at') else '—'}"
        f"{pending_part}"
    )
    block_btn = (
        ib_button("Blokdan chiqarish", "check", style="success", callback_data=f"adm_unblock:{uid}:{page}")
        if u["is_blocked"] else
        ib_button("Bloklash", "cross", style="danger", callback_data=f"adm_block:{uid}:{page}")
    )
    kb_rows = [[block_btn]]
    for p in pending_payments:
        kb_rows.append([ib_button(
            f"Bekor qilish: {p['final_amount']:,} so'm", "cross", style="danger",
            callback_data=f"adm_cancel_pay:{p['id']}:{uid}:{page}"
        )])
    kb_rows.append([ib_button("Orqaga", "arrow_l", callback_data=f"adm_users:{page}")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data.startswith("adm_cancel_pay:"))
async def adm_cancel_pay_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    _, payment_id_str, uid_str, page = call.data.split(":")
    payment_id = int(payment_id_str)
    uid = int(uid_str)
    await db.cancel_auto_payment(payment_id)
    try:
        await bot.send_message(
            uid,
            f"{E('cross')} Kutilayotgan to'lovingiz admin tomonidan bekor qilindi.\n"
            f"Savolingiz bo'lsa, qo'llab-quvvatlash xizmatiga murojaat qiling."
        )
    except Exception:
        pass
    await call.answer(f"{E('check')} To'lov bekor qilindi.", show_alert=True)
    await render_user_view(call, uid, page)

@admin_router.callback_query(F.data.startswith("adm_block:"))
async def adm_block_user(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    _, uid_str, page = call.data.split(":")
    uid = int(uid_str)
    await db.set_user_blocked(uid, True)
    try:
        await bot.send_message(uid, f"{E('cross')} Siz botdan foydalanish huquqidan mahrum qilindingiz.")
    except Exception:
        pass
    await call.answer(f"{E('check')} Bloklandi.", show_alert=True)
    await render_user_view(call, uid, page)

@admin_router.callback_query(F.data.startswith("adm_unblock:"))
async def adm_unblock_user(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    _, uid_str, page = call.data.split(":")
    uid = int(uid_str)
    await db.set_user_blocked(uid, False)
    try:
        await bot.send_message(uid, f"{E('check')} Blok bekor qilindi, botdan qaytadan foydalanishingiz mumkin.")
    except Exception:
        pass
    await call.answer(f"{E('check')} Blokdan chiqarildi.", show_alert=True)
    await render_user_view(call, uid, page)

# ─── KUNLIK HISOBOT (foyda / zarar) ─────────────────────────────
@admin_router.callback_query(F.data == "adm_daily_report")
async def adm_daily_report(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    start_utc, end_utc = today_utc_bounds()
    report    = await db.get_daily_report(start_utc, end_utc)
    new_users = await db.get_new_users_count(start_utc, end_utc)

    topup       = report.get("topup",          {"count": 0, "total": 0})
    purchase    = report.get("purchase",        {"count": 0, "total": 0})   # manfiy summa
    ref_bonus   = report.get("referral_bonus",  {"count": 0, "total": 0})
    daily_bonus = report.get("daily_bonus",     {"count": 0, "total": 0})
    admin_adj   = report.get("admin_adjust",    {"count": 0, "total": 0})

    purchase_revenue = abs(purchase["total"])
    net_profit = purchase_revenue - ref_bonus["total"] - daily_bonus["total"] + admin_adj["total"]

    today_str = now_tashkent().strftime("%d.%m.%Y")
    text = (
        f"📅 <b>Kunlik hisobot — {today_str}</b>\n\n"
        f"🆕 Yangi foydalanuvchilar: <b>{new_users} ta</b>\n\n"
        f"💳 <b>Tasdiqlangan to'lovlar</b>\n"
        f"   ✅ Soni: <b>{topup['count']} ta</b>\n"
        f"   💰 Summasi: <b>{topup['total']:,} so'm</b>\n\n"
        f"🛒 <b>Sotilgan raqamlar</b>\n"
        f"   📦 Soni: <b>{purchase['count']} ta</b>\n"
        f"   💰 Daromad: <b>{purchase_revenue:,} so'm</b>\n\n"
        f"🎁 Kunlik bonuslar berildi: <b>{daily_bonus['count']} ta / {daily_bonus['total']:,} so'm</b>\n"
        f"👥 Referal bonuslar berildi: <b>{ref_bonus['count']} ta / {ref_bonus['total']:,} so'm</b>\n"
        f"➕➖ Admin tomonidan qo'lda o'zgartirilgan: <b>{admin_adj['count']} ta / {admin_adj['total']:+,} so'm</b>\n\n"
        f"📈 <b>Sof foyda (bugungi):</b> <b>{net_profit:,} so'm</b>\n"
        f"<i>(Sotuv daromadi − berilgan bonuslar ± admin tuzatishlari)</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_refresh")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

# ─── FOYDALANUVCHI PROFILI (to'liq ma'lumot) ───────────────────
@admin_router.callback_query(F.data == "adm_user_profile")
async def adm_user_profile_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("🔍 Foydalanuvchining Telegram ID sini kiriting:")
    await state.set_state(AdminSearchState.wait_profile_id)
    await call.answer()

@admin_router.message(AdminSearchState.wait_profile_id)
async def adm_user_profile_show(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    if not msg.text or not msg.text.strip().lstrip("-").isdigit():
        return await msg.answer("❌ Faqat ID (son) kiriting, yoki bekor qilish uchun /cancel yozing.")
    uid = int(msg.text.strip())
    user = await db.get_user(uid)
    if not user:
        await msg.answer(f"❌ <code>{uid}</code> ID li foydalanuvchi topilmadi.")
        await state.clear()
        await show_admin_panel(msg)
        return

    tx_stats     = await db.get_user_tx_stats(uid)
    ref_count    = await db.get_referral_count(uid)
    ref_earnings = await db.get_referral_earnings(uid)
    referrer     = await db.get_referrer_info(uid)
    joined       = user["created_at"]
    try:
        joined_str = joined.strftime("%d.%m.%Y %H:%M") if not isinstance(joined, str) else joined
    except Exception:
        joined_str = str(joined)

    if referrer:
        referred_by_line = f"👤 Kim taklif qilgan: <b>{referrer['ref_fullname']}</b> (<code>{referrer['ref_id']}</code>)"
    else:
        referred_by_line = "👤 Kim taklif qilgan: <i>To'g'ridan-to'g'ri kirgan</i>"

    text = (
        f"🔍 <b>Foydalanuvchi profili</b>\n\n"
        f"🆔 Tartib raqami: <b>{user['tartib_id']}</b>\n"
        f"🆔 Telegram ID: <code>{user['user_id']}</code>\n"
        f"👤 Ism: <b>{user['fullname']}</b>\n"
        f"🔗 Username: @{user['username'] or '—'}\n"
        f"📱 Telefon: <code>{user['phone'] or 'Kiritilmagan'}</code>\n"
        f"📅 Qo'shilgan sana: <b>{joined_str}</b>\n"
        f"{referred_by_line}\n\n"
        f"💰 Joriy balans: <b>{user['balance']:,} so'm</b>\n"
        f"💵 Jami to'ldirilgan: <b>{user['total_deposited']:,} so'm</b>\n\n"
        f"💳 <b>To'lovlar</b>\n"
        f"   ✅ Tasdiqlangan: <b>{tx_stats['topup_count']} marta</b>\n"
        f"   💰 Jami summa: <b>{tx_stats['topup_total']:,} so'm</b>\n\n"
        f"🛒 <b>Xaridlar</b>\n"
        f"   📦 Soni: <b>{tx_stats['purchase_count']} ta</b>\n"
        f"   💰 Jami xarajat: <b>{tx_stats['purchase_total']:,} so'm</b>\n\n"
        f"👥 <b>Referal</b>\n"
        f"   👤 Taklif qilganlar: <b>{ref_count} ta</b>\n"
        f"   💰 Referaldan daromad: <b>{ref_earnings:,} so'm</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Boshqa profil qidirish", callback_data="adm_user_profile")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_refresh")],
    ])
    await state.clear()
    await msg.answer(text, reply_markup=kb)

@admin_router.callback_query(F.data == "adm_balance")
async def adm_balance_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("Foydalanuvchi ID sini kiriting (bekor qilish uchun /cancel yozing):")
    await state.set_state(BalanceChangeState.wait_user_id)
    await call.answer()

@admin_router.message(BalanceChangeState.wait_user_id)
async def adm_balance_uid(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    if not msg.text or not msg.text.isdigit():
        return await msg.answer("❌ Faqat son (ID) kiriting, yoki bekor qilish uchun /cancel yozing.")
    await state.update_data(uid=int(msg.text))
    await msg.answer("Qancha so'm qo'shmoqchisiz? (Ayirish uchun: -5000)")
    await state.set_state(BalanceChangeState.wait_amount)

@admin_router.message(BalanceChangeState.wait_amount)
async def adm_balance_amount(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    amount = parse_signed_amount(msg.text)
    if amount is None:
        return await msg.answer("❌ Faqat son kiriting, yoki bekor qilish uchun /cancel yozing.")
    data = await state.get_data()
    uid  = data['uid']
    await db.update_balance(uid, amount)
    await db.log_transaction(uid, "admin_adjust", amount, note="adm_balance_panel")
    try:
        await bot.send_message(uid, f"ℹ️ Hisobingiz admin tomonidan <b>{amount:+,} so'm</b>ga o'zgartirildi.")
    except Exception:
        pass
    await msg.answer(f"✅ <code>{uid}</code> ID li foydalanuvchi balansi {amount:+,} so'mga o'zgartirildi.")
    await state.clear()
    await show_admin_panel(msg)

# ─── NARX SOZLASH ──────────────────────────────────────────────
@admin_router.callback_query(F.data == "adm_prices")
async def adm_prices(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    rate   = await get_exchange_rate()
    margin = await get_default_margin()
    text = (
        f"💵 <b>Narx sozlash</b>\n\n"
        f"{E('chart')} Joriy kurs: <b>1$ = {rate:,.0f} so'm</b>\n"
        f"{E('sparkle')} Joriy margin (ustama): <b>x{margin:g}</b>\n\n"
        f"<i>Kurs va margin — narxi hali qo'lda sozlanmagan davlatlar uchun "
        f"asos narx shu ikkalasidan hisoblanadi. Bu formula BARCHA davlatlar uchun "
        f"bir xil ishlaydi, shu bilan foiz o'zgartirish hammasiga bir xilda ta'sir qiladi.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ib_button("Bitta davlat narxini o'zgartirish",    "card",    callback_data="price_single")],
        [ib_button("Barcha narxlarni % bilan o'zgartirish", "chart",  style="primary", callback_data="price_bulk")],
        [ib_button("Barcha narxlarni ko'rish",              "report", callback_data="price_list")],
        [ib_button("Kursni o'zgartirish",                   "money",  style="success", callback_data="set_exchange_rate")],
        [ib_button("Marginni o'zgartirish",                 "sparkle", callback_data="set_default_margin")],
        [ib_button("Orqaga", "arrow_l", style="danger", callback_data="adm_refresh")],
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "set_exchange_rate")
async def set_exchange_rate_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    rate = await get_exchange_rate()
    await call.message.edit_text(
        f"💱 Joriy kurs: <b>1$ = {rate:,.0f} so'm</b>\n\n"
        f"Yangi kursni kiriting (masalan: <code>12800</code>):"
    )
    await state.set_state(AdminSettingsState.wait_exchange_rate)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_exchange_rate)
async def set_exchange_rate_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    try:
        rate = float(msg.text.strip())
        if rate <= 0:
            raise ValueError
    except Exception:
        return await msg.answer("❌ Faqat musbat son kiriting, yoki /cancel yozing.")
    await db.set_setting("exchange_rate", str(rate))
    await msg.answer(f"{E('check')} Kurs yangilandi: 1$ = {rate:,.0f} so'm")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_default_margin")
async def set_default_margin_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    margin = await get_default_margin()
    await call.message.edit_text(
        f"✨ Joriy margin: <b>x{margin:g}</b>\n\n"
        f"Yangi marginni kiriting (masalan <code>1.3</code> — 30% ustama demakdir):"
    )
    await state.set_state(AdminSettingsState.wait_default_margin)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_default_margin)
async def set_default_margin_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    try:
        margin = float(msg.text.strip())
        if margin <= 0:
            raise ValueError
    except Exception:
        return await msg.answer("❌ Faqat musbat son kiriting, yoki /cancel yozing.")
    await db.set_setting("default_margin", str(margin))
    await msg.answer(f"{E('check')} Margin yangilandi: x{margin:g}")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "price_single")
async def price_single_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    data          = await api_available_countries()
    countries     = data.get("countries", {})
    filtered      = {k: v for k, v in countries.items() if k.upper() not in BLOCKED_COUNTRIES}
    markup_prices = await db.get_all_markup_prices()
    ordered = sort_uz_first(filtered.items(), key_func=lambda x: float(x[1].get("price", 999)))[:20]
    buttons = []
    for code, info in ordered:
        name  = clean_country_name(info.get("name", code))
        flag  = country_flag(code)
        cur   = markup_prices.get(code.upper(), None)
        label = f"{cur:,} so'm" if cur else "Standart"
        buttons.append([InlineKeyboardButton(text=f"{name} {flag}: {label}", callback_data=f"setprice:{code}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_prices")])
    await call.message.edit_text("✏️ <b>Narx o'zgartirish — davlat tanlang:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@admin_router.callback_query(F.data == "price_list")
async def price_list(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    data          = await api_available_countries()
    countries     = data.get("countries", {})
    filtered      = {k: v for k, v in countries.items() if k.upper() not in BLOCKED_COUNTRIES}
    markup_prices = await db.get_all_markup_prices()
    text = "📋 <b>Barcha davlatlar narxlari:</b>\n\n"
    for code, info in sort_uz_first(filtered.items(), key_func=lambda x: float(x[1].get("price", 999))):
        name      = clean_country_name(info.get("name", code))
        flag      = country_flag(code)
        usd_price = float(info.get("price", 1))
        uzs_price = markup_prices.get(code.upper()) or await calc_default_price(usd_price)
        text += f"🌍 {name} {flag} (<code>{code}</code>): <b>{uzs_price:,} so'm</b>\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_prices")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "price_bulk")
async def price_bulk_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text(
        "📊 <b>Barcha narxlarni foiz bilan o'zgartirish</b>\n\n"
        "Necha foizga oshirmoqchisiz?\n"
        "Misol: <code>20</code> — 20% oshirish\n"
        "Misol: <code>-10</code> — 10% kamaytirish"
    )
    await state.set_state(AdminSettingsState.wait_bulk_percent)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_bulk_percent)
async def price_bulk_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    try:
        percent = float(msg.text)
    except Exception:
        return await msg.answer("❌ Faqat son kiriting, yoki bekor qilish uchun /cancel yozing.")
    if percent <= -100:
        return await msg.answer("❌ Foiz -100 dan katta bo'lishi kerak (masalan -50).")
    data      = await api_available_countries()
    countries = data.get("countries", {})
    if not countries:
        await msg.answer(
            f"{E('warn')} Davlatlar ro'yxatini API dan olib bo'lmadi, shuning uchun "
            f"narxlar o'zgartirilmadi. Birozdan so'ng qayta urinib ko'ring."
        )
        await state.clear()
        return
    filtered      = {k: v for k, v in countries.items() if k.upper() not in BLOCKED_COUNTRIES}
    markup_prices = await db.get_all_markup_prices()
    updated, skipped = 0, 0
    for code, info in filtered.items():
        try:
            usd_price = float(info.get("price", 1))
            cur_price = markup_prices.get(code.upper()) or await calc_default_price(usd_price)
            new_price = max(int(cur_price * (1 + percent / 100)), MIN_PRICE_SOM)
            await db.set_markup_price(code.upper(), new_price)
            updated += 1
        except Exception:
            skipped += 1
            continue
    result_text = f"{E('check')} {updated} ta davlat narxi {percent:+.0f}% ga o'zgartirildi!"
    if skipped:
        result_text += f"\n{E('warn')} {skipped} ta davlat narxli ma'lumoti noto'g'ri bo'lgani uchun o'tkazib yuborildi."
    await msg.answer(result_text)
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data.startswith("setprice:"))
async def setprice_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    code = call.data.split(":")[1]
    await state.update_data(country=code)
    await call.message.edit_text(f"<b>{code}</b> uchun yangi narxni so'mda kiriting:")
    await state.set_state(AdminSettingsState.wait_price_value)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_price_value)
async def setprice_amount(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    price = parse_amount(msg.text)
    if price is None:
        return await msg.answer("❌ Faqat son kiriting, yoki bekor qilish uchun /cancel yozing.")
    if price < MIN_PRICE_SOM:
        return await msg.answer(f"❌ Narx kamida {MIN_PRICE_SOM:,} so'm bo'lishi kerak.")
    data  = await state.get_data()
    code  = data['country']
    await db.set_markup_price(code.upper(), price)
    await msg.answer(f"✅ {code} uchun yangi narx: {price:,} so'm o'rnatildi.")
    await state.clear()
    await show_admin_panel(msg)

# ─── ADMIN SOZLAMALARI ─────────────────────────────────────────
@admin_router.callback_query(F.data == "adm_settings")
async def adm_settings(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    daily_bonus_val = await get_setting("daily_bonus", 200)
    ref_bonus_val   = await get_setting("referral_bonus", 500)
    channel_un      = await get_setting("required_channel_username", CHANNEL_USERNAME)
    orders_ch_un    = await get_setting("orders_channel_username", "")
    card            = await get_setting("card_number", CARD_NUMBER)
    owner           = await get_setting("card_owner", CARD_OWNER)
    bound_count = sum(1 for v in PREMIUM_EMOJI_CACHE.values() if v)
    auto_on = await is_auto_pay_enabled()
    auto_status_text = "✅ Yoqilgan" if auto_on else "▫️ O'chirilgan"
    text = (
        f"⚙️ <b>Bot sozlamalari</b>\n\n"
        f"🎁 Kunlik bonus: <b>{daily_bonus_val} so'm</b>\n"
        f"👥 Referal bonus: <b>{ref_bonus_val} so'm</b>\n"
        f"📢 Majburiy kanal: <b>@{channel_un}</b>\n"
        f"📦 Buyurtmalar kanali: <b>@{orders_ch_un or 'Sozlanmagan'}</b>\n"
        f"💳 Karta raqami: <b>{card}</b>\n"
        f"👤 Karta egasi: <b>{owner}</b>\n"
        f"{E('sparkle')} Premium emoji: <b>{bound_count}/{len(PREMIUM_EMOJI_SLUGS)}</b> bog'langan\n"
        f"{E('rocket')} Avtomatik to'lov (HUMOcard): <b>{auto_status_text}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Kunlik bonusni o'zgartirish",   callback_data="set_daily_bonus")],
        [InlineKeyboardButton(text="👥 Referal bonusni o'zgartirish",  callback_data="set_ref_bonus")],
        [InlineKeyboardButton(text="📢 Majburiy kanalni o'zgartirish", callback_data="set_channel")],
        [InlineKeyboardButton(text="📦 Buyurtmalar kanalini sozlash",  callback_data="set_orders_channel")],
        [InlineKeyboardButton(text="💳 Karta raqamini o'zgartirish",   callback_data="set_card_number")],
        [InlineKeyboardButton(text="👤 Karta egasini o'zgartirish",    callback_data="set_card_owner")],
        [InlineKeyboardButton(text="✨ Premium emoji sozlash",         callback_data="adm_emojis")],
        [InlineKeyboardButton(text="⚡ Avtomatik to'lov sozlamalari",  callback_data="adm_autopay")],
        [InlineKeyboardButton(text="🔔 Yangi a'zo va eslatmalar",      callback_data="adm_notify")],
        [InlineKeyboardButton(text="⬅️ Orqaga",                       callback_data="adm_refresh")],
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "adm_notify")
async def adm_notify_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    channel_id     = await get_setting("new_user_channel_id", "")
    inactive_days  = await get_setting("inactive_reminder_days", INACTIVE_DAYS_DEFAULT)
    incomplete_hrs = await get_setting("incomplete_reminder_hours", INCOMPLETE_HOURS_DEFAULT)
    low_bal        = await get_setting("low_balance_threshold", LOW_BALANCE_THRESHOLD_DEFAULT)
    text = (
        f"🔔 <b>Hodisalar kanali va eslatma sozlamalari</b>\n\n"
        f"{E('sparkle')} Hodisalar kanali: <b>{channel_id or 'Sozlanmagan'}</b>\n"
        f"<i>Shu kanalga quyidagilar avtomatik yuboriladi:</i>\n"
        f"  • Yangi a'zo qo'shilganda\n"
        f"  • Qo'lda to'lov tasdiqlanganda\n"
        f"  • Avtomatik to'lov tasdiqlanganda\n"
        f"  • Foydalanuvchi balansi kam qolganda\n\n"
        f"{E('clock')} Faolsiz foydalanuvchi eslatmasi: <b>{inactive_days} kundan keyin</b>\n"
        f"{E('warn')} To'liq ro'yxatdan o'tmaganlarga eslatma: <b>{incomplete_hrs} soatdan keyin</b>\n"
        f"{E('wallet')} Kam balans chegarasi: <b>{low_bal:,} so'm</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ib_button("Hodisalar kanalini sozlash", "bell", style="primary", callback_data="set_new_user_channel")],
        [ib_button("Faolsizlik muddatini o'zgartirish", "clock", callback_data="set_inactive_days")],
        [ib_button("To'liqmaslik muddatini o'zgartirish", "warn", callback_data="set_incomplete_hours")],
        [ib_button("Kam balans chegarasini o'zgartirish", "wallet", callback_data="set_low_balance")],
        [ib_button("Orqaga", "arrow_l", style="danger", callback_data="adm_settings")],
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "set_low_balance")
async def set_low_balance_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("💼 Qaysi summadan kam qolsa, admin kanaliga xabar borsin? (masalan: 3000)")
    await state.set_state(AdminSettingsState.wait_low_balance)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_low_balance)
async def set_low_balance_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    val = parse_amount(msg.text)
    if val is None or val < 0:
        return await msg.answer("❌ Faqat son kiriting.")
    await db.set_setting("low_balance_threshold", str(val))
    await msg.answer(f"{E('check')} Kam balans chegarasi yangilandi: {val:,} so'm")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_new_user_channel")
async def set_new_user_channel_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text(
        f"{E('bell')} Yangi a'zo xabarlari yuboriladigan kanal ID sini kiriting.\n\n"
        f"<i>Kanal ID sini olish uchun: botni o'sha kanalga admin qilib qo'shing, "
        f"so'ng kanaldagi istalgan xabarni botga forward qiling — men ID sini aytib beraman "
        f"(yoki oddiygina -100 bilan boshlanuvchi ID ni to'g'ridan-to'g'ri kiriting).</i>"
    )
    await state.set_state(AdminSettingsState.wait_new_user_channel_id)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_new_user_channel_id)
async def set_new_user_channel_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    channel_id = msg.text.strip()
    if not (channel_id.lstrip("-").isdigit()):
        return await msg.answer("❌ Faqat kanal ID sini kiriting (masalan: -1001234567890).")
    try:
        test_msg = await bot.send_message(int(channel_id), f"{E('check')} Bu kanal endi yangi a'zolar uchun sozlandi!")
    except Exception as e:
        return await msg.answer(f"❌ Bu kanalga xabar yuborib bo'lmadi: {e}\nBotni o'sha kanalga admin qilib qo'shganingizga ishonch hosil qiling.")
    await db.set_setting("new_user_channel_id", channel_id)
    await msg.answer(f"{E('check')} Yangi a'zo kanali sozlandi!")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_inactive_days")
async def set_inactive_days_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("⏰ Necha kun botga kirmagan foydalanuvchiga eslatma yuborilsin? (masalan: 3)")
    await state.set_state(AdminSettingsState.wait_inactive_days)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_inactive_days)
async def set_inactive_days_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    val = parse_amount(msg.text)
    if val is None or val < 1:
        return await msg.answer("❌ Faqat musbat butun son kiriting.")
    await db.set_setting("inactive_reminder_days", str(val))
    await msg.answer(f"{E('check')} Faolsizlik muddati yangilandi: {val} kun")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_incomplete_hours")
async def set_incomplete_hours_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("⏰ Necha soat telefon tasdiqlamagan foydalanuvchiga eslatma yuborilsin? (masalan: 6)")
    await state.set_state(AdminSettingsState.wait_incomplete_hours)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_incomplete_hours)
async def set_incomplete_hours_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    val = parse_amount(msg.text)
    if val is None or val < 1:
        return await msg.answer("❌ Faqat musbat butun son kiriting.")
    await db.set_setting("incomplete_reminder_hours", str(val))
    await msg.answer(f"{E('check')} To'liqmaslik muddati yangilandi: {val} soat")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "adm_autopay")
async def adm_autopay_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    auto_on      = await is_auto_pay_enabled()
    card2_number = await get_setting("card2_number", "")
    card2_owner  = await get_setting("card2_owner", "")
    max_offset   = await get_auto_pay_max_offset()
    expiry_min   = await get_auto_pay_expiry_min()
    configured   = await is_humo_configured()
    live_status  = humo_listener.get_status()
    if live_status["connected"]:
        listener_status = f"✅ Ulangan: {live_status['account']}"
    elif configured:
        listener_status = "🟡 Sozlangan, lekin hozir ulanmagan (botni qayta ishga tushiring)"
    else:
        listener_status = "❌ Sozlanmagan"
    tolov_cfg = await get_tolov_config()
    if tolov_cfg["enabled"] and tolov_cfg["shop_id"]:
        tolov_status = f"✅ Yoqilgan (shop_id: {tolov_cfg['shop_id']})"
    elif tolov_cfg["shop_id"]:
        tolov_status = "🟡 Sozlangan, lekin o'chirilgan"
    else:
        tolov_status = "❌ Sozlanmagan"
    auto_status_text = "✅ Yoqilgan" if auto_on else "▫️ O'chirilgan"
    text = (
        f"⚡ <b>Avtomatik to'lov sozlamalari</b>\n\n"
        f"🔘 Avtomatik rejim: <b>{auto_status_text}</b>\n"
        f"💳 2-karta: <b>{card2_number or 'Sozlanmagan'}</b>"
        f"{f' ({card2_owner})' if card2_owner else ''}\n"
        f"🔢 Urinishlar soni (tasodifiy summa uchun): <b>{max_offset}</b>\n"
        f"⏰ Kutish muddati: <b>{expiry_min} daqiqa</b>\n\n"
        f"<b>Uchta mustaqil (parallel) usul mavjud:</b>\n"
        f"1️⃣ Telethon tinglovchi: <b>{listener_status}</b>\n"
        f"2️⃣ Webhook server: <b>✅ Har doim tayyor</b>\n"
        f"<i>(tashqi xizmat — masalan karta SMS kuzatuvchi bot — sizga so'rov yuboradi)</i>\n"
        f"3️⃣ TolovAPI (tolov.run.place): <b>{tolov_status}</b>\n"
        f"<i>(siz o'zingiz shop_id/shop_key orqali davriy so'rov yuborasiz)</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ib_button(("O'chirish" if auto_on else "Yoqish"), "rocket",
                    style=("danger" if auto_on else "success"), callback_data="toggle_autopay")],
        [ib_button("Sessiyani bot orqali ulash", "phone", style="primary", callback_data="setup_humo_session")],
        [ib_button("Holatni tekshirish", "search", callback_data="check_humo_status")],
        [ib_button("Webhook manzilini ko'rish", "link", callback_data="show_webhook_info")],
        [ib_button("TolovAPI sozlash", "sparkle", style="primary", callback_data="adm_tolov_api")],
        [ib_button("2-karta raqamini sozlash", "card", callback_data="set_card2_number")],
        [ib_button("2-karta egasini sozlash", "profile", callback_data="set_card2_owner")],
        [ib_button("Urinishlar sonini o'zgartirish", "chart", callback_data="set_autopay_offset")],
        [ib_button("Kutish muddatini o'zgartirish", "clock", callback_data="set_autopay_expiry")],
        [ib_button("Orqaga", "arrow_l", style="danger", callback_data="adm_settings")],
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "adm_tolov_api")
async def adm_tolov_api_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    cfg = await get_tolov_config()
    status_text = "✅ Yoqilgan" if cfg["enabled"] else "▫️ O'chirilgan"
    text = (
        f"{E('sparkle')} <b>TolovAPI sozlamalari</b>\n\n"
        f"<i>tolov.run.place orqali avtomatik to'lov — sessiya yoki webhook "
        f"kerak emas, faqat shop_id/shop_key orqali davriy tekshiriladi.</i>\n\n"
        f"🔘 Holat: <b>{status_text}</b>\n"
        f"🆔 Shop ID: <code>{cfg['shop_id'] or 'Sozlanmagan'}</code>\n"
        f"🔑 Shop Key: <code>{'•' * len(cfg['shop_key']) if cfg['shop_key'] else 'Sozlanmagan'}</code>\n"
        f"🌐 API manzili: <code>{cfg['api_url']}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ib_button(("O'chirish" if cfg["enabled"] else "Yoqish"), "rocket",
                    style=("danger" if cfg["enabled"] else "success"), callback_data="toggle_tolov_api")],
        [ib_button("Shop ID kiritish", "key", style="primary", callback_data="set_tolov_shop_id")],
        [ib_button("Shop Key kiritish", "key", callback_data="set_tolov_shop_key")],
        [ib_button("API manzilini o'zgartirish", "link", callback_data="set_tolov_api_url")],
        [ib_button("Ulanishni tekshirish", "search", callback_data="check_tolov_status")],
        [ib_button("Orqaga", "arrow_l", style="danger", callback_data="adm_autopay")],
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "set_tolov_shop_id")
async def set_tolov_shop_id_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text(f"{E('key')} TolovAPI shop_id ni kiriting (tolov.run.place sayti bergan):")
    await state.set_state(TolovSetupStates.wait_shop_id)
    await call.answer()

@admin_router.message(TolovSetupStates.wait_shop_id)
async def set_tolov_shop_id_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    await db.set_setting("tolov_shop_id", msg.text.strip())
    await msg.answer(f"{E('check')} Shop ID saqlandi.")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_tolov_shop_key")
async def set_tolov_shop_key_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text(f"{E('key')} TolovAPI shop_key ni kiriting:")
    await state.set_state(TolovSetupStates.wait_shop_key)
    await call.answer()

@admin_router.message(TolovSetupStates.wait_shop_key)
async def set_tolov_shop_key_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    await db.set_setting("tolov_shop_key", msg.text.strip())
    await msg.answer(f"{E('check')} Shop Key saqlandi.")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_tolov_api_url")
async def set_tolov_api_url_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text(
        f"{E('link')} TolovAPI manzilini kiriting (standart holda o'zgartirish shart emas):\n\n"
        f"<code>{TOLOV_API_URL_DEFAULT}</code>"
    )
    await state.set_state(TolovSetupStates.wait_api_url)
    await call.answer()

@admin_router.message(TolovSetupStates.wait_api_url)
async def set_tolov_api_url_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    url = msg.text.strip()
    if not url.startswith("http"):
        return await msg.answer("❌ To'g'ri URL kiriting (http:// yoki https:// bilan boshlanishi kerak).")
    await db.set_setting("tolov_api_url", url)
    await msg.answer(f"{E('check')} API manzili saqlandi.")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "toggle_tolov_api")
async def toggle_tolov_api_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    cfg = await get_tolov_config()
    new_val = "0" if cfg["enabled"] else "1"
    if new_val == "1":
        if not (cfg["shop_id"] and cfg["shop_key"]):
            await call.answer("❌ Avval Shop ID va Shop Key kiriting!", show_alert=True)
            return
        status = await tolov_api.check_status(cfg["api_url"], cfg["shop_id"], cfg["shop_key"])
        if not status.get("ok"):
            err_msg = status.get('error', "noma'lum xato")
            await call.answer(f"❌ TolovAPI bilan bog'lanib bo'lmadi: {err_msg}", show_alert=True)
            return
        if not status.get("connected"):
            await call.answer("⚠️ TolovAPI javob berdi, lekin do'kon ulanmagan (sessiya faol emas). Baribir yoqamiz.", show_alert=True)
    await db.set_setting("tolov_api_enabled", new_val)
    if new_val == "1":
        await start_or_restart_tolov_polling()
    await adm_tolov_api_cb(call)

@admin_router.callback_query(F.data == "check_tolov_status")
async def check_tolov_status_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    cfg = await get_tolov_config()
    if not (cfg["shop_id"] and cfg["shop_key"]):
        await call.answer("❌ Avval Shop ID va Shop Key kiriting!", show_alert=True)
        return
    result = await tolov_api.check_status(cfg["api_url"], cfg["shop_id"], cfg["shop_key"])
    if result.get("ok") and result.get("connected"):
        text = f"{E('check')} <b>TolovAPI ulangan va faol!</b>"
    elif result.get("ok"):
        text = f"{E('warn')} <b>TolovAPI javob berdi, lekin do'kon ulanmagan.</b>\nTolovAPI panelida sessiya holatini tekshiring."
    else:
        err_msg = result.get('error', "noma'lum xato")
        text = f"{E('cross')} <b>Bog'lanib bo'lmadi:</b>\n<code>{err_msg}</code>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[ib_button("Orqaga", "arrow_l", callback_data="adm_tolov_api")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "check_humo_status")
async def check_humo_status_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    status = humo_listener.get_status()
    if status["connected"]:
        text = (
            f"{E('check')} <b>Ulangan va ishlayapti!</b>\n\n"
            f"{E('profile')} Akkaunt: <b>{status['account']}</b>\n"
            f"📞 Telefon: <code>{status.get('phone') or '—'}</code>\n\n"
            f"<i>Demak HUMOcard botidan kelayotgan xabarlar shu akkaunt orqali "
            f"real vaqtda kuzatilmoqda.</i>"
        )
    else:
        err = status.get("error")
        configured = await is_humo_configured()
        if err:
            text = f"{E('cross')} <b>Ulanmagan.</b>\n\nXato: <code>{err}</code>"
        elif configured:
            text = (
                f"{E('warn')} <b>Sozlangan, lekin hozir ishlamayapti.</b>\n\n"
                f"Ehtimol bot hali qayta ishga tushirilmagan, yoki tinglovchi "
                f"boshqa sababdan to'xtagan. Botni qayta ishga tushirib ko'ring."
            )
        else:
            text = (
                f"{E('cross')} <b>Sessiya hali sozlanmagan.</b>\n\n"
                f"«📱 Sessiyani bot orqali ulash» tugmasi orqali ulang."
            )
    kb = InlineKeyboardMarkup(inline_keyboard=[[ib_button("Orqaga", "arrow_l", callback_data="adm_autopay")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "setup_humo_session")
async def setup_humo_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    api_id, api_hash, _ = await get_humo_credentials()
    if api_id and api_hash:
        await call.message.edit_text(
            f"{E('phone')} Telefon raqamingizni xalqaro formatda kiriting "
            f"(masalan: <code>+998901234567</code>):\n\n"
            f"<i>Bu — HUMOcard bot xabarlari keladigan akkauntning raqami bo'lishi kerak.</i>"
        )
        await state.set_state(HumoSetupStates.wait_phone)
    else:
        await call.message.edit_text(
            f"{E('key')} my.telegram.org saytidan olingan <b>API_ID</b> ni kiriting:"
        )
        await state.set_state(HumoSetupStates.wait_api_id)
    await call.answer()

@admin_router.message(HumoSetupStates.wait_api_id)
async def setup_humo_api_id(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    if not msg.text.strip().isdigit():
        return await msg.answer("❌ API_ID faqat raqamlardan iborat bo'lishi kerak.")
    await state.update_data(humo_api_id=msg.text.strip())
    await msg.answer(f"{E('key')} Endi <b>API_HASH</b> ni kiriting:")
    await state.set_state(HumoSetupStates.wait_api_hash)

@admin_router.message(HumoSetupStates.wait_api_hash)
async def setup_humo_api_hash(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    await state.update_data(humo_api_hash=msg.text.strip())
    await msg.answer(
        f"{E('phone')} Telefon raqamingizni xalqaro formatda kiriting "
        f"(masalan: <code>+998901234567</code>):"
    )
    await state.set_state(HumoSetupStates.wait_phone)

@admin_router.message(HumoSetupStates.wait_phone)
async def setup_humo_phone(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    data = await state.get_data()
    saved_id, saved_hash, _ = await get_humo_credentials()
    api_id_raw = data.get("humo_api_id") or saved_id
    api_hash   = data.get("humo_api_hash") or saved_hash
    if not (api_id_raw and api_hash):
        await msg.answer("❌ API_ID/API_HASH topilmadi. Qaytadan boshlang.")
        await state.clear()
        return
    phone = msg.text.strip()

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        await msg.answer("❌ Serverda telethon o'rnatilmagan. Terminalda: <code>pip install telethon</code>")
        await state.clear()
        return

    client = TelegramClient(StringSession(), int(api_id_raw), api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
    except Exception as e:
        await client.disconnect()
        await msg.answer(f"❌ Kod yuborishda xatolik: {e}")
        return

    _humo_setup_ctx[msg.from_user.id] = {
        "client": client, "phone": phone, "phone_code_hash": sent.phone_code_hash,
        "api_id": api_id_raw, "api_hash": api_hash,
    }
    await msg.answer(
        f"{E('check')} Kod <b>{phone}</b> raqamiga (Telegram orqali) yuborildi!\n"
        f"Kelgan kodni kiriting:"
    )
    await state.set_state(HumoSetupStates.wait_code)

@admin_router.message(HumoSetupStates.wait_code)
async def setup_humo_code(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    ctx = _humo_setup_ctx.get(msg.from_user.id)
    if not ctx:
        await msg.answer("❌ Sessiya topilmadi, qaytadan boshlang.")
        await state.clear()
        return
    client = ctx["client"]
    try:
        from telethon.errors import SessionPasswordNeededError
        await client.sign_in(phone=ctx["phone"], code=msg.text.strip(), phone_code_hash=ctx["phone_code_hash"])
    except SessionPasswordNeededError:
        await msg.answer(f"{E('key')} Ikki bosqichli (2FA) parolingizni kiriting:")
        await state.set_state(HumoSetupStates.wait_password)
        return
    except Exception as e:
        await msg.answer(f"❌ Kod xato yoki muddati o'tgan: {e}\n\nQaytadan urinib ko'ring yoki /cancel yozing.")
        return
    await _finish_humo_setup(msg, state)

@admin_router.message(HumoSetupStates.wait_password)
async def setup_humo_password(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    ctx = _humo_setup_ctx.get(msg.from_user.id)
    if not ctx:
        await msg.answer("❌ Sessiya topilmadi, qaytadan boshlang.")
        await state.clear()
        return
    try:
        await ctx["client"].sign_in(password=msg.text.strip())
    except Exception as e:
        await msg.answer(f"❌ Parol xato: {e}\n\nQaytadan urinib ko'ring yoki /cancel yozing.")
        return
    await _finish_humo_setup(msg, state)

async def _finish_humo_setup(msg: Message, state: FSMContext):
    ctx = _humo_setup_ctx.pop(msg.from_user.id, None)
    if not ctx:
        await state.clear()
        return
    client = ctx["client"]
    session_string = client.session.save()
    try:
        me = await client.get_me()
        account_name = f"{me.first_name} (@{me.username})" if me.username else (me.first_name or "Noma'lum")
    except Exception:
        account_name = "Noma'lum"
    await client.disconnect()

    await db.set_setting("humo_api_id", str(ctx["api_id"]))
    await db.set_setting("humo_api_hash", ctx["api_hash"])
    await db.set_setting("humo_session_string", session_string)
    await state.clear()

    await msg.answer(
        f"{E('check')} <b>Sessiya muvaffaqiyatli ulandi!</b>\n\n"
        f"{E('profile')} Akkaunt: <b>{account_name}</b>\n\n"
        f"{E('rocket')} Endi tinglovchini ishga tushiryapman..."
    )

    started = await start_or_restart_humo_listener()
    await asyncio.sleep(2)  # ulanish tugashi uchun ozgina kutamiz
    live_status = humo_listener.get_status()
    if live_status["connected"]:
        await msg.answer(
            f"{E('check')} <b>Tasdiqlandi — tinglovchi ishlayapti!</b>\n"
            f"{E('profile')} {live_status['account']}"
        )
    elif started:
        await msg.answer(f"{E('warn')} Ishga tushirildi, lekin holatini hali tasdiqlab bo'lmadi. "
                          f"Bir necha soniyadan so'ng «Holatni tekshirish» tugmasini bosing.")
    else:
        await msg.answer(f"{E('cross')} Ishga tushirishda muammo bo'ldi.")
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "show_webhook_info")
async def show_webhook_info_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    key = await get_webhook_secret()
    text = (
        f"{E('link')} <b>Webhook orqali avtomatik to'lov</b>\n\n"
        f"Agar sizda karta SMS xabarlarini kuzatib, natijani HTTP so'rov "
        f"orqali yuboradigan xizmat (masalan @LockAvtoPayBot kabi) bo'lsa, "
        f"unga quyidagi manzilni bering:\n\n"
        f"<code>http://SIZNING_SERVER_IP:{WEBHOOK_PORT}/webhook/payment</code>\n\n"
        f"{E('key')} Maxfiy kalit (secret_key):\n<code>{key}</code>\n\n"
        f"So'rov parametrlari: <code>secret_key</code>, <code>amount</code>, "
        f"<code>card_last</code> (ixtiyoriy)\n\n"
        f"<i>{E('warn')} Diqqat: agar serveringiz to'g'ridan-to'g'ri internetga "
        f"ochiq bo'lmasa (masalan faqat nginx orqali 443-portda ishlasa), "
        f"bu portni (yoki nginx orqali proksi yo'lini) ochish kerak bo'ladi.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [ib_button("Kalitni yangilash", "cross", style="danger", callback_data="regen_webhook_key")],
        [ib_button("Orqaga", "arrow_l", callback_data="adm_autopay")],
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "regen_webhook_key")
async def regen_webhook_key_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    new_key = secrets.token_urlsafe(24)
    await db.set_setting("webhook_secret_key", new_key)
    await call.answer(f"{E('check')} Yangi kalit yaratildi!", show_alert=True)
    await show_webhook_info_cb(call)

@admin_router.callback_query(F.data == "toggle_autopay")
async def toggle_autopay_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    new_val = "0" if await is_auto_pay_enabled() else "1"
    if new_val == "1" and not await is_humo_configured():
        await call.answer(
            "❌ Avval sessiyani ulang: «📱 Sessiyani bot orqali ulash» yoki .env orqali sozlang!",
            show_alert=True
        )
        return
    await db.set_setting("auto_pay_enabled", new_val)
    await adm_autopay_cb(call)

@admin_router.callback_query(F.data == "set_card2_number")
async def set_card2_number_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("💳 2-karta raqamini kiriting (masalan: 8600123456789012):")
    await state.set_state(AdminSettingsState.wait_card2_number)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_card2_number)
async def set_card2_number_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    await db.set_setting("card2_number", msg.text.strip())
    await msg.answer(f"{E('check')} 2-karta raqami saqlandi.")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_card2_owner")
async def set_card2_owner_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("👤 2-karta egasining ismini kiriting:")
    await state.set_state(AdminSettingsState.wait_card2_owner)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_card2_owner)
async def set_card2_owner_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    await db.set_setting("card2_owner", msg.text.strip())
    await msg.answer(f"{E('check')} 2-karta egasi saqlandi.")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_autopay_offset")
async def set_autopay_offset_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text(
        "🔢 Har bir summaga qo'shiladigan tasodifiy sonni (1-50 so'm oralig'ida) "
        "topishga nechta urinish qilinsin (tavsiya: 50 — bu 1 dan 50 gacha bo'lgan "
        "barcha variantlarni to'liq qamrab oladi):"
    )
    await state.set_state(AdminSettingsState.wait_autopay_offset)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_autopay_offset)
async def set_autopay_offset_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    val = parse_amount(msg.text)
    if val is None or val < 1:
        return await msg.answer("❌ Faqat musbat butun son kiriting.")
    await db.set_setting("auto_pay_max_offset", str(val))
    await msg.answer(f"{E('check')} Urinishlar soni yangilandi: {val}")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_autopay_expiry")
async def set_autopay_expiry_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("⏰ Necha daqiqa kutilishini kiriting (tavsiya: 15-30):")
    await state.set_state(AdminSettingsState.wait_autopay_expiry)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_autopay_expiry)
async def set_autopay_expiry_apply(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID:
        return
    val = parse_amount(msg.text)
    MIN_EXPIRY_MIN = 5
    if val is None or val < MIN_EXPIRY_MIN:
        return await msg.answer(
            f"❌ Kamida {MIN_EXPIRY_MIN} daqiqa bo'lishi kerak.\n\n"
            f"{E('warn')} Diqqat: bank/karta tizimlari real to'lovni aniqlashda "
            f"ba'zan bir necha o'n soniyadan bir necha daqiqagacha kechikishi mumkin. "
            f"Juda qisqa muddat qo'ysangiz, foydalanuvchi TO'G'RI to'lov qilsa ham, "
            f"bildirishnoma kech kelib, band bekor bo'lib qolishi mumkin!"
        )
    await db.set_setting("auto_pay_expiry_min", str(val))
    await msg.answer(f"{E('check')} Kutish muddati yangilandi: {val} daqiqa")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "adm_emojis")
async def adm_emojis_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    lines = [f"{E('sparkle')} <b>Premium emoji sozlamalari</b>\n"]
    for slug, fallback in PREMIUM_EMOJI_SLUGS.items():
        bound = PREMIUM_EMOJI_CACHE.get(slug)
        status = f"✅ bog'langan (<code>{bound}</code>)" if bound else "▫️ oddiy emoji"
        lines.append(f"{fallback} <code>{slug}</code> — {status}")
    lines.append(
        "\n<b>Qanday bog'lash kerak:</b>\n"
        "1️⃣ /getemojiid buyrug'ini yuboring\n"
        "2️⃣ Premium emoji bor xabarni yuboring/forward qiling\n"
        "3️⃣ Bot ID beradi — <code>/setemoji slug id</code> bilan bog'lang\n"
        "4️⃣ Bekor qilish: <code>/unsetemoji slug</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_settings")]])
    await call.message.edit_text("\n".join(lines), reply_markup=kb)
    await call.answer()

@admin_router.callback_query(F.data == "set_daily_bonus")
async def set_daily_bonus_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("🎁 Yangi kunlik bonus miqdorini so'mda kiriting:")
    await state.set_state(AdminSettingsState.wait_daily_bonus)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_daily_bonus)
async def set_daily_bonus_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    val = parse_amount(msg.text)
    if val is None: return await msg.answer("❌ Faqat son kiriting!")
    await db.set_setting("daily_bonus", str(val))
    await msg.answer(f"✅ Kunlik bonus {val:,} so'm ga o'zgartirildi!")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_ref_bonus")
async def set_ref_bonus_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("👥 Yangi referal bonus miqdorini so'mda kiriting:")
    await state.set_state(AdminSettingsState.wait_referral_bonus)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_referral_bonus)
async def set_ref_bonus_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    val = parse_amount(msg.text)
    if val is None: return await msg.answer("❌ Faqat son kiriting!")
    await db.set_setting("referral_bonus", str(val))
    await msg.answer(f"✅ Referal bonus {val:,} so'm ga o'zgartirildi!")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_channel")
async def set_channel_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📢 Yangi majburiy kanal ID sini kiriting:\nMisol: <code>-1001234567890</code>")
    await state.set_state(AdminSettingsState.wait_channel_id)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_channel_id)
async def set_channel_id_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try: int(msg.text)
    except Exception: return await msg.answer("❌ Kanal ID raqam bo'lishi kerak!")
    await db.set_setting("required_channel_id", msg.text)
    await msg.answer("✅ Kanal ID saqlandi! Endi kanal username kiriting (@ belgisiz):")
    await state.set_state(AdminSettingsState.wait_channel_username)

@admin_router.message(AdminSettingsState.wait_channel_username)
async def set_channel_username_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    username = msg.text.strip().lstrip("@")
    await db.set_setting("required_channel_username", username)
    await msg.answer(f"✅ Kanal @{username} ga o'zgartirildi!")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_orders_channel")
async def set_orders_channel_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📦 Buyurtmalar kanal ID sini kiriting:\nMisol: <code>-1001234567890</code>")
    await state.set_state(AdminSettingsState.wait_orders_channel_id)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_orders_channel_id)
async def set_orders_channel_id_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try: int(msg.text)
    except Exception: return await msg.answer("❌ Kanal ID raqam bo'lishi kerak!")
    await db.set_setting("orders_channel_id", msg.text)
    await msg.answer("✅ Buyurtmalar kanal ID saqlandi! Endi username kiriting:")
    await state.set_state(AdminSettingsState.wait_orders_channel_username)

@admin_router.message(AdminSettingsState.wait_orders_channel_username)
async def set_orders_channel_username_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    username = msg.text.strip().lstrip("@")
    await db.set_setting("orders_channel_username", username)
    await msg.answer(f"✅ Buyurtmalar kanali @{username} ga o'zgartirildi!")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_card_number")
async def set_card_number_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("💳 Yangi karta raqamini kiriting:\nMisol: <code>8600 1234 5678 9012</code>")
    await state.set_state(AdminSettingsState.wait_card_number)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_card_number)
async def set_card_number_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await db.set_setting("card_number", msg.text.strip())
    await msg.answer(f"✅ Karta raqami o'zgartirildi: <code>{msg.text.strip()}</code>")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "set_card_owner")
async def set_card_owner_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("👤 Karta egasining ismini kiriting:\nMisol: <code>Qurbonov Q</code>")
    await state.set_state(AdminSettingsState.wait_card_owner)
    await call.answer()

@admin_router.message(AdminSettingsState.wait_card_owner)
async def set_card_owner_save(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    await db.set_setting("card_owner", msg.text.strip())
    await msg.answer(f"✅ Karta egasi o'zgartirildi: <b>{msg.text.strip()}</b>")
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "adm_search")
async def adm_search(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📞 Qidirilayotgan telefon raqamni kiriting:")
    await state.set_state(AdminSearchState.wait_phone)
    await call.answer()

@admin_router.message(AdminSearchState.wait_phone)
async def adm_search_phone(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    phone  = msg.text.strip()
    result = await db.find_purchase_by_phone(phone)
    if not result:
        await msg.answer(f"❌ <code>{phone}</code> raqami bo'yicha xarid topilmadi.")
    else:
        text = (
            f"✅ <b>Raqam topildi!</b>\n\n"
            f"📞 Raqam: <code>{result['phone']}</code>\n"
            f"🌍 Davlat: {result['country_name']}\n"
            f"📅 Sotilgan sana: {result['created_at']}\n\n"
            f"👤 <b>Xaridor:</b>\n"
            f"Ism: {result['fullname']}\n"
            f"ID: <code>{result['user_id']}</code>"
        )
        await msg.answer(text)
    await state.clear()
    await show_admin_panel(msg)

@admin_router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📣 Barcha foydalanuvchilarga yuboriladigan xabarni kiriting:")
    await state.set_state(BroadcastState.wait_message)
    await call.answer()

@admin_router.message(BroadcastState.wait_message)
async def broadcast_send(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    users      = await db.get_all_users()
    sent       = 0
    failed     = 0
    status_msg = await msg.answer(f"📣 Yuborilmoqda... 0/{len(users)}")
    for i, u in enumerate(users):
        try:
            await bot.copy_message(u['user_id'], msg.chat.id, msg.message_id)
            sent += 1
        except Exception:
            failed += 1
        if i % 20 == 0:
            try:
                await status_msg.edit_text(f"📣 Yuborilmoqda... {i+1}/{len(users)}")
            except Exception:
                pass
        await asyncio.sleep(0.05)
    await status_msg.edit_text(f"📣 Xabar yuborildi:\n✅ Muvaffaqiyatli: {sent} ta\n❌ Yetib bormadi: {failed} ta")
    await state.clear()
    await show_admin_panel(msg)

# ─── ISHGA TUSHIRISH ───────────────────────────────────────────
async def main():
    await db.init()
    try:
        await reload_premium_emoji_cache()
    except Exception as e:
        print(f"⚠️ Premium emoji keshini yuklashda xatolik (muhim emas): {e}")
    dp.include_router(admin_router)
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        if await is_humo_configured():
            started = await start_or_restart_humo_listener()
            if started:
                print("✅ HUMOcard avtomatik to'lov tinglovchisi ishga tushirildi ✅")
        else:
            print("ℹ️ HUMOcard sozlanmagan — faqat qo'lda to'lov tizimi ishlaydi "
                  "(admin panel → Avtomatik to'lov → «Sessiyani bot orqali ulash» orqali sozlashingiz mumkin)")
    except Exception as e:
        print(f"⚠️ HUMOcard tinglovchisini ishga tushirishda xatolik (asosiy bot baribir ishlayveradi): {e}")
    try:
        tolov_cfg = await get_tolov_config()
        if tolov_cfg["enabled"] and tolov_cfg["shop_id"] and tolov_cfg["shop_key"]:
            started = await start_or_restart_tolov_polling()
            if started:
                print("✅ TolovAPI polling sikli ishga tushirildi ✅")
        else:
            print("ℹ️ TolovAPI sozlanmagan yoki o'chirilgan — admin panel → Avtomatik to'lov → TolovAPI orqali sozlashingiz mumkin")
    except Exception as e:
        print(f"⚠️ TolovAPI polling siklini ishga tushirishda xatolik (asosiy bot baribir ishlayveradi): {e}")
    try:
        asyncio.create_task(reminder_loop())
        print("✅ Faollik eslatmalari sikli ishga tushirildi ✅")
    except Exception as e:
        print(f"⚠️ Eslatmalar siklini ishga tushirishda xatolik: {e}")
    try:
        asyncio.create_task(referral_subscription_check_loop())
        print("✅ Referal obuna tekshiruvi sikli ishga tushirildi ✅")
    except Exception as e:
        print(f"⚠️ Referal tekshiruvi siklini ishga tushirishda xatolik: {e}")
    try:
        await start_webhook_server()
    except Exception as e:
        print(f"⚠️ Webhook serverni ishga tushirishda xatolik (asosiy bot baribir ishlayveradi): {e}")
    print("✅ Bot ishga tushdi! PostgreSQL ulandi ✅")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
