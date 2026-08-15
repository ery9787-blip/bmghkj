"""
humo_listener.py — Adminning shaxsiy Telegram akkaunti (session) orqali
@HUMOcardbot dan kelayotgan "To'ldirish" xabarlarini o'qib, botdagi
kutilayotgan avtomatik to'lovlar (auto_payments) bilan solishtiradi.
Mos summa topilsa — foydalanuvchi balansi avtomatik to'ldiriladi.

DIQQAT — bu ODDIY BOT EMAS, USERBOT (Telethon orqali shaxsiy akkaunt
sifatida ishlaydi). Shuning uchun:
  1) Faqat bitta joyda (bitta serverda) ishga tushirilishi kerak — aks
     holda Telegram akkauntni "bir nechta joyda anomal faollik" deb
     bloklashi mumkin.
  2) Session stringni hech kimga bermang, uni .env faylida saqlang va
     serverga faqat SSH orqali kirish huquqini cheklang.

O'rnatish:
    pip install telethon

Bir martalik sozlash — session string olish:
    python3 generate_session.py
    (telefon raqam + SMS kod so'raladi, oxirida uzun SESSION STRING chiqadi)

.env ga qo'shing:
    HUMO_API_ID=1234567
    HUMO_API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    HUMO_SESSION_STRING=1BVtsO...  (generate_session.py dan olingan)
    HUMO_BOT_USERNAME=HUMOcardbot
"""

import os
import re
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger("humo_listener")

HUMO_API_ID          = os.getenv("HUMO_API_ID", "")
HUMO_API_HASH         = os.getenv("HUMO_API_HASH", "")
HUMO_SESSION_STRING  = os.getenv("HUMO_SESSION_STRING", "")
HUMO_BOT_USERNAME    = os.getenv("HUMO_BOT_USERNAME", "HUMOcardbot")

HUMO_ENABLED = bool(HUMO_API_ID and HUMO_API_HASH and HUMO_SESSION_STRING)


def parse_humocard_message(text: str) -> dict | None:
    """
    HUMOcard xabarini tahlil qiladi. Namuna:

        🎉 To'ldirish
        ➕ 100.000,00 UZS
        📍 Alif uz h2h 075>Tosh
        🏦 HUMOCARD *2747
        🕐 20:16 27.07.2026
        💰 100.341,16 UZS

    DIQQAT: summa oldida ODDIY "+" emas, "➕" (U+2795, Heavy Plus Sign)
    emojisi keladi — bular butunlay boshqa belgilar! Xavfsizlik uchun
    ikkalasini ham (va manfiy tomon uchun "➖"/"-" ni ham) qabul qilamiz.

    Qaytaradi: {"type": "deposit"|"withdraw", "amount": int, "card_last": str}
    yoki mos kelmasa None.
    """
    if not text:
        return None
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if not lines:
        return None

    header = lines[0].lower()
    # "To'ldirish" — HUMOcard uchun, "Kartaga o'tkazma" — ba'zi UzCard/boshqa
    # kartalar uchun ishlatiladigan muqobil sarlavha — ikkalasi ham pul KIRIM.
    is_deposit = any(k in header for k in ["to'ldirish", "to‘ldirish", "kartaga o'tkazma", "kartaga o‘tkazma"])
    is_withdraw = any(k in header for k in ["to'lov", "to‘lov"]) and not is_deposit
    if not (is_deposit or is_withdraw):
        return None

    PLUS_CHARS  = ("+", "➕")
    MINUS_CHARS = ("-", "➖")
    amount_line = next(
        (l for l in lines if "uzs" in l.lower() and l.strip().startswith(PLUS_CHARS + MINUS_CHARS)),
        None
    )
    if not amount_line:
        return None

    m = re.search(r"[+➕\-➖]\s*([\d\s.,]+)\s*UZS", amount_line, re.IGNORECASE)
    if not m:
        return None

    raw_num = m.group(1).strip()
    # "100.000,00" -> butun qismi "100.000" -> "100000"
    integer_part = raw_num.split(",")[0].replace(".", "").replace(" ", "")
    try:
        amount = int(integer_part)
    except ValueError:
        return None

    card_line = next((l for l in lines if "*" in l), "")
    card_m = re.search(r"\*\s*(\d+)", card_line)
    card_last = card_m.group(1) if card_m else ""

    return {
        "type": "deposit" if is_deposit else "withdraw",
        "amount": amount,
        "card_last": card_last,
    }


async def start_humo_listener(db, bot, admin_id: int, E):
    """Botning main() ichidan asyncio.create_task() bilan chaqiriladi."""
    if not HUMO_ENABLED:
        logger.warning(
            "HUMO_API_ID / HUMO_API_HASH / HUMO_SESSION_STRING sozlanmagan — "
            "avtomatik to'lov tinglovchisi ishga tushmadi (qo'lda chek tizimi ishlayveradi)."
        )
        return

    try:
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession
    except ImportError:
        logger.error("telethon o'rnatilmagan — 'pip install telethon' qiling.")
        return

    client = TelegramClient(StringSession(HUMO_SESSION_STRING), int(HUMO_API_ID), HUMO_API_HASH)

    @client.on(events.NewMessage(from_users=HUMO_BOT_USERNAME))
    async def _on_humo_message(event):
        try:
            parsed = parse_humocard_message(event.raw_text)
            if not parsed or parsed["type"] != "deposit":
                return

            amount = parsed["amount"]
            card_last = parsed.get("card_last", "")
            payment = await db.find_matching_auto_payment(amount, card_last)
            if not payment:
                return  # bu summani (yoki shu kartada) kutayotgan hech kim yo'q

            await db.complete_auto_payment(payment["id"], card_used=parsed.get("card_last", ""))
            await db.update_balance(payment["user_id"], payment["final_amount"])
            await db.log_transaction(
                payment["user_id"], "topup", payment["final_amount"],
                note=f"auto:humocard:{parsed.get('card_last','')}"
            )
            user = await db.get_user(payment["user_id"])
            new_balance = user["balance"] if user else payment["final_amount"]

            try:
                await bot.send_message(
                    payment["user_id"],
                    f"{E('check')} <b>To'lovingiz avtomatik tasdiqlandi!</b>\n\n"
                    f"{E('money')} Hisobingizga <b>{payment['final_amount']:,} so'm</b> qo'shildi.\n"
                    f"{E('wallet')} Joriy balans: <b>{new_balance:,} so'm</b>\n\n"
                    f"Endi xohlagan xizmatdan foydalanishingiz mumkin {E('party')}"
                )
            except Exception:
                pass

            try:
                await bot.send_message(
                    admin_id,
                    f"{E('check')} Avtomatik to'lov (HUMOcard) tasdiqlandi!\n\n"
                    f"{E('profile')} Foydalanuvchi: <code>{payment['user_id']}</code>\n"
                    f"{E('money')} Summa: {payment['final_amount']:,} so'm\n"
                    f"💳 Karta: *{parsed.get('card_last','?')}"
                )
            except Exception:
                pass

        except Exception as e:
            logger.exception("HUMOcard xabarini qayta ishlashda xato: %s", e)

    await client.start()
    logger.info("✅ HUMOcard avtomatik to'lov tinglovchisi ishga tushdi.")
    async with client:
        await client.run_until_disconnected()
