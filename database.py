"""
database.py — PostgreSQL asosida ishlaydi.
Ma'lumotlar deploy qayta qilinganda o'CHMAYDI.

O'rnatish:
    pip install asyncpg

.env ga qo'shing:
    DATABASE_URL=postgresql://user:password@host:5432/dbname
"""

import os
import asyncpg
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/botdb")


class Database:
    def __init__(self):
        self.pool = None

    async def init(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        await self._create_tables()

    async def _create_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    tartib_id     SERIAL PRIMARY KEY,
                    user_id       BIGINT UNIQUE NOT NULL,
                    fullname      TEXT DEFAULT '',
                    username      TEXT DEFAULT '',
                    phone         TEXT DEFAULT '',
                    balance       BIGINT DEFAULT 0,
                    total_deposited BIGINT DEFAULT 0,
                    referrer_id   BIGINT DEFAULT NULL,
                    last_bonus_date TEXT DEFAULT '',
                    created_at    TIMESTAMP DEFAULT NOW()
                )
            """)
            # Foydalanuvchilarni boshqarish uchun qo'shimcha ustunlar — xavfsiz
            # migratsiya (eski bazalarda ham, yangilarida ham ishlaydi).
            for col_sql in [
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN DEFAULT FALSE",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active TIMESTAMPTZ DEFAULT NOW()",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS inactive_reminder_sent TIMESTAMPTZ",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS incomplete_reminder_sent TIMESTAMPTZ",
            ]:
                try:
                    await conn.execute(col_sql)
                except Exception as e:
                    print(f"⚠️ users jadvalini yangilashda xatolik ({col_sql}): {e}")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id          SERIAL PRIMARY KEY,
                    referrer_id BIGINT NOT NULL,
                    referred_id BIGINT NOT NULL,
                    created_at  TIMESTAMP DEFAULT NOW(),
                    UNIQUE(referrer_id, referred_id)
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS purchases (
                    id           SERIAL PRIMARY KEY,
                    user_id      BIGINT NOT NULL,
                    phone        TEXT NOT NULL,
                    country_code TEXT DEFAULT '',
                    country_name TEXT DEFAULT '',
                    price        BIGINT DEFAULT 0,
                    created_at   TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_payments (
                    pay_id    TEXT PRIMARY KEY,
                    user_id   BIGINT NOT NULL,
                    amount    BIGINT NOT NULL,
                    fullname  TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT DEFAULT ''
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS markup_prices (
                    country_code TEXT PRIMARY KEY,
                    price        BIGINT NOT NULL
                )
            """)
            # Har bir balans o'zgarishini yozib boradigan umumiy "bухgalteriya"
            # jadvali — kunlik hisobot va foydalanuvchi tarixi shu yerdan olinadi.
            # type: topup | purchase | referral_bonus | daily_bonus | admin_adjust
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id         SERIAL PRIMARY KEY,
                    user_id    BIGINT NOT NULL,
                    type       TEXT NOT NULL,
                    amount     BIGINT NOT NULL,
                    note       TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions (created_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions (user_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_purchases_created ON purchases (created_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_created ON users (created_at)")
            # Referal ID lar bot qayta ishga tushganda yo'qolib qolmasligi
            # uchun vaqtinchalik saqlanadigan jadval (start_handler'da yoziladi,
            # telefon tasdiqlanganda o'chiriladi).
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_referrers (
                    user_id     BIGINT PRIMARY KEY,
                    referrer_id BIGINT NOT NULL,
                    created_at  TIMESTAMP DEFAULT NOW()
                )
            """)
            # Avtomatik (HUMOcard orqali) to'lovlar — BUTUNLAY IXTIYORIY xususiyat.
            # Agar bu qismda biror sabab bilan xatolik chiqsa (masalan eski
            # jadval formati bilan mos kelmasa), butun botning ishga tushishiga
            # xalaqit bermasligi uchun alohida try/except ichiga olingan —
            # asosiy bot (raqam sotib olish, qo'lda to'lov va h.k.) bunga
            # bog'liq bo'lmasdan har doim ishlayveradi.
            try:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS auto_payments (
                        id           SERIAL PRIMARY KEY,
                        user_id      BIGINT NOT NULL,
                        base_amount  BIGINT NOT NULL,
                        final_amount BIGINT NOT NULL,
                        target_card  TEXT DEFAULT '',
                        status       TEXT DEFAULT 'pending',
                        card_used    TEXT DEFAULT '',
                        created_at   TIMESTAMPTZ DEFAULT NOW(),
                        expires_at   TIMESTAMPTZ NOT NULL,
                        completed_at TIMESTAMPTZ
                    )
                """)
                # Eski (birinchi versiyada yaratilgan) jadvallarda expires_at
                # TIMESTAMPTZ emas, oddiy TIMESTAMP bo'lishi mumkin — shuni
                # avtomatik tuzatib qo'yamiz.
                try:
                    await conn.execute("""
                        ALTER TABLE auto_payments
                            ALTER COLUMN expires_at TYPE TIMESTAMPTZ USING expires_at AT TIME ZONE 'Asia/Tashkent'
                    """)
                except Exception:
                    pass
                try:
                    await conn.execute("""
                        ALTER TABLE auto_payments
                            ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'Asia/Tashkent'
                    """)
                except Exception:
                    pass
                try:
                    await conn.execute("""
                        ALTER TABLE auto_payments
                            ALTER COLUMN completed_at TYPE TIMESTAMPTZ USING completed_at AT TIME ZONE 'Asia/Tashkent'
                    """)
                except Exception:
                    pass
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_pay_status ON auto_payments (status, final_amount)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_pay_user ON auto_payments (user_id)")
            except Exception as e:
                print(f"⚠️ auto_payments jadvalini tayyorlashda xatolik (avtomatik to'lov ishlamasligi mumkin, "
                      f"lekin asosiy bot ishlayveradi): {e}")

    # ─── USERS ─────────────────────────────────────────────────
    async def add_user(self, user_id: int, fullname: str, username: str, referrer_id=None) -> bool:
        """Foydalanuvchini qo'shadi (yoki yangilaydi). Agar bu HAQIQIY YANGI
        foydalanuvchi bo'lsa (birinchi marta /start bosgan) — True qaytaradi,
        aks holda (allaqachon bor edi) — False. Bot shu orqali "yangi a'zo"
        bildirishnomasini faqat chindan yangilarga yuboradi."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO users (user_id, fullname, username, referrer_id)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id) DO UPDATE
                SET fullname = EXCLUDED.fullname,
                    username = EXCLUDED.username
                RETURNING (xmax = 0) AS inserted
            """, user_id, fullname, username, referrer_id)
            return bool(row["inserted"]) if row else False

    async def get_user(self, user_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            return dict(row) if row else None

    async def update_phone(self, user_id: int, phone: str):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET phone = $1 WHERE user_id = $2", phone, user_id)

    async def touch_last_active(self, user_id: int):
        """Foydalanuvchi botga har safar biror amal qilganda chaqiriladi —
        "faol emaslar" eslatmasi kimga kerakligini aniqlash uchun."""
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET last_active = NOW() WHERE user_id = $1", user_id)

    async def is_user_blocked(self, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("SELECT is_blocked FROM users WHERE user_id = $1", user_id)
            return bool(val)

    async def set_user_blocked(self, user_id: int, blocked: bool):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET is_blocked = $1 WHERE user_id = $2", blocked, user_id)

    async def get_users_page(self, page: int = 0, per_page: int = 10, only_blocked: bool = False):
        """Foydalanuvchilar ro'yxatini sahifalab qaytaradi (admin panel uchun)."""
        async with self.pool.acquire() as conn:
            where = "WHERE is_blocked = TRUE" if only_blocked else ""
            rows = await conn.fetch(
                f"SELECT * FROM users {where} ORDER BY tartib_id DESC LIMIT $1 OFFSET $2",
                per_page, page * per_page
            )
            total = await conn.fetchval(f"SELECT COUNT(*) FROM users {where}")
            return [dict(r) for r in rows], total

    async def get_inactive_users(self, days: int, remind_cooldown_days: int = 7):
        """Necha kundan beri botga kirmagan (last_active dan), va oxirgi
        eslatma yuborilganiga kamida remind_cooldown_days kun bo'lgan
        (yoki hech qachon yuborilmagan) foydalanuvchilarni qaytaradi."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM users
                WHERE is_blocked = FALSE
                  AND last_active < NOW() - ($1 || ' days')::interval
                  AND (inactive_reminder_sent IS NULL
                       OR inactive_reminder_sent < NOW() - ($2 || ' days')::interval)
            """, str(days), str(remind_cooldown_days))
            return [dict(r) for r in rows]

    async def mark_inactive_reminder_sent(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET inactive_reminder_sent = NOW() WHERE user_id = $1", user_id)

    async def get_incomplete_users(self, hours: int, remind_cooldown_hours: int = 24):
        """Ro'yxatdan to'liq o'tmagan (telefon tasdiqlamagan) va kamida
        `hours` soatdan beri shunday turgan foydalanuvchilarni qaytaradi."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM users
                WHERE is_blocked = FALSE
                  AND (phone IS NULL OR phone = '')
                  AND created_at < NOW() - ($1 || ' hours')::interval
                  AND (incomplete_reminder_sent IS NULL
                       OR incomplete_reminder_sent < NOW() - ($2 || ' hours')::interval)
            """, str(hours), str(remind_cooldown_hours))
            return [dict(r) for r in rows]

    async def mark_incomplete_reminder_sent(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET incomplete_reminder_sent = NOW() WHERE user_id = $1", user_id)

    async def update_balance(self, user_id: int, amount: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                amount, user_id
            )

    async def update_total_deposited(self, user_id: int, amount: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET total_deposited = total_deposited + $1 WHERE user_id = $2",
                amount, user_id
            )

    async def get_balance(self, user_id: int) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT balance FROM users WHERE user_id = $1", user_id)
            return row["balance"] if row else 0

    async def get_all_users(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM users ORDER BY tartib_id")
            return [dict(r) for r in rows]

    async def count_users(self) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM users")

    async def get_last_bonus_date(self, user_id: int) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT last_bonus_date FROM users WHERE user_id = $1", user_id)
            return row["last_bonus_date"] if row else ""

    async def set_last_bonus_date(self, user_id: int, date_str: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET last_bonus_date = $1 WHERE user_id = $2",
                date_str, user_id
            )

    # ─── PENDING REFERRERS (deploydan omon qoladigan referal xotira) ──
    async def add_pending_referrer(self, user_id: int, referrer_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO pending_referrers (user_id, referrer_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET referrer_id = EXCLUDED.referrer_id
            """, user_id, referrer_id)

    async def get_pending_referrer(self, user_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT referrer_id FROM pending_referrers WHERE user_id = $1", user_id)
            return row["referrer_id"] if row else None

    async def delete_pending_referrer(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM pending_referrers WHERE user_id = $1", user_id)

    # ─── REFERRALS ─────────────────────────────────────────────
    async def add_referral(self, referrer_id: int, referred_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO referrals (referrer_id, referred_id)
                VALUES ($1, $2) ON CONFLICT DO NOTHING
            """, referrer_id, referred_id)

    async def get_referral_count(self, user_id: int) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM referrals WHERE referrer_id = $1", user_id
            )

    async def get_referral_earnings(self, user_id: int) -> int:
        """Referal orqali haqiqatda yozib borilgan bonuslar yig'indisi (transactions dan)."""
        async with self.pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE user_id = $1 AND type = 'referral_bonus'",
                user_id
            )
            return int(val)

    async def get_referral_leaderboard(self, period: str = "all", limit: int = 10):
        """Kim eng ko'p odam taklif qilganini ko'rsatadigan reyting.
        period: 'week' | 'month' | 'year' | 'all'."""
        interval_map = {"week": "7 days", "month": "30 days", "year": "365 days"}
        async with self.pool.acquire() as conn:
            if period in interval_map:
                rows = await conn.fetch(f"""
                    SELECT r.referrer_id, COUNT(*) AS cnt, u.fullname, u.username
                    FROM referrals r
                    LEFT JOIN users u ON u.user_id = r.referrer_id
                    WHERE r.created_at > NOW() - INTERVAL '{interval_map[period]}'
                    GROUP BY r.referrer_id, u.fullname, u.username
                    ORDER BY cnt DESC
                    LIMIT $1
                """, limit)
            else:
                rows = await conn.fetch("""
                    SELECT r.referrer_id, COUNT(*) AS cnt, u.fullname, u.username
                    FROM referrals r
                    LEFT JOIN users u ON u.user_id = r.referrer_id
                    GROUP BY r.referrer_id, u.fullname, u.username
                    ORDER BY cnt DESC
                    LIMIT $1
                """, limit)
            return [dict(r) for r in rows]

    async def get_referral_rank(self, user_id: int, period: str = "all") -> tuple:
        """Berilgan foydalanuvchining reytingdagi o'rnini va shu davrdagi
        taklif sonini qaytaradi: (o'rin, soni). Agar hech kimni taklif
        qilmagan bo'lsa (0, 0) qaytaradi."""
        interval_map = {"week": "7 days", "month": "30 days", "year": "365 days"}
        where_clause = f"WHERE created_at > NOW() - INTERVAL '{interval_map[period]}'" if period in interval_map else ""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT referrer_id, COUNT(*) AS cnt
                FROM referrals
                {where_clause}
                GROUP BY referrer_id
                ORDER BY cnt DESC
            """)
            for idx, row in enumerate(rows, start=1):
                if row["referrer_id"] == user_id:
                    return idx, row["cnt"]
            return 0, 0

    async def get_referrer_info(self, user_id: int):
        """Foydalanuvchini kim taklif qilganini (referrer) topib beradi."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT ref.user_id AS ref_id, ref.fullname AS ref_fullname
                FROM users u
                JOIN users ref ON ref.user_id = u.referrer_id
                WHERE u.user_id = $1
            """, user_id)
            return dict(row) if row else None

    # ─── PURCHASES ─────────────────────────────────────────────
    async def log_purchase(self, user_id: int, phone: str, country_code: str, country_name: str, price: int) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO purchases (user_id, phone, country_code, country_name, price)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
            """, user_id, phone, country_code, country_name, price)
            return row["id"]

    async def get_purchases(self, user_id: int):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM purchases WHERE user_id = $1 ORDER BY created_at DESC",
                user_id
            )
            return [dict(r) for r in rows]

    async def get_purchase_by_phone(self, user_id: int, phone: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM purchases WHERE user_id = $1 AND phone = $2",
                user_id, phone
            )
            return dict(row) if row else None

    async def find_purchase_by_phone(self, phone: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT p.*, u.fullname, u.user_id
                FROM purchases p
                JOIN users u ON u.user_id = p.user_id
                WHERE p.phone = $1
                ORDER BY p.created_at DESC
                LIMIT 1
            """, phone)
            return dict(row) if row else None

    async def count_orders(self) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM purchases")

    async def get_total_revenue(self) -> int:
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("SELECT COALESCE(SUM(price), 0) FROM purchases")
            return int(val)

    async def get_sales_stats(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT country_name, COUNT(*) as cnt, SUM(price) as rev
                FROM purchases
                GROUP BY country_name
                ORDER BY cnt DESC
                LIMIT 10
            """)
            return [(r["country_name"], r["cnt"], r["rev"]) for r in rows]

    # ─── PENDING PAYMENTS ──────────────────────────────────────
    async def add_pending_payment(self, pay_id: str, user_id: int, amount: int, fullname: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO pending_payments (pay_id, user_id, amount, fullname)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (pay_id) DO NOTHING
            """, pay_id, user_id, amount, fullname)

    async def get_pending_payment(self, pay_id: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pending_payments WHERE pay_id = $1", pay_id
            )
            return dict(row) if row else None

    async def find_pending_by_amount(self, amount: int):
        """HUMO SMS uchun — shu summaga mos pending to'lovlar."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM pending_payments WHERE amount = $1 ORDER BY created_at ASC",
                amount
            )
            return [dict(r) for r in rows]

    async def delete_pending_payment(self, pay_id: str):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM pending_payments WHERE pay_id = $1", pay_id)

    async def get_all_pending(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM pending_payments ORDER BY created_at")
            return [dict(r) for r in rows]

    # ─── SETTINGS ──────────────────────────────────────────────
    async def get_setting(self, key: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM settings WHERE key = $1", key)
            return row["value"] if row else None

    async def set_setting(self, key: str, value: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO settings (key, value) VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, key, value)

    # ─── MARKUP PRICES ─────────────────────────────────────────
    async def get_all_markup_prices(self) -> dict:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM markup_prices")
            return {r["country_code"]: r["price"] for r in rows}

    async def set_markup_price(self, country_code: str, price: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO markup_prices (country_code, price) VALUES ($1, $2)
                ON CONFLICT (country_code) DO UPDATE SET price = EXCLUDED.price
            """, country_code, price)

    # ─── TRANSACTIONS (bухgalteriya kitobi) ────────────────────
    async def log_transaction(self, user_id: int, type_: str, amount: int, note: str = ""):
        """
        Har qanday balans o'zgarishini yozib boradi.
        type_: 'topup' | 'purchase' | 'referral_bonus' | 'daily_bonus' | 'admin_adjust'
        amount: musbat yoki manfiy bo'lishi mumkin (masalan purchase => manfiy)
        """
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO transactions (user_id, type, amount, note)
                VALUES ($1, $2, $3, $4)
            """, user_id, type_, amount, note)

    async def get_daily_report(self, start_utc: datetime, end_utc: datetime) -> dict:
        """Berilgan vaqt oralig'ida (UTC) turlar bo'yicha guruhlangan hisobot."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT type, COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total
                FROM transactions
                WHERE created_at >= $1 AND created_at < $2
                GROUP BY type
            """, start_utc, end_utc)
            return {r["type"]: {"count": r["cnt"], "total": int(r["total"])} for r in rows}

    async def get_new_users_count(self, start_utc: datetime, end_utc: datetime) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE created_at >= $1 AND created_at < $2",
                start_utc, end_utc
            )

    async def get_user_tx_stats(self, user_id: int) -> dict:
        """Foydalanuvchining to'lov/xarid tarixi bo'yicha yig'indi statistikasi."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE type = 'topup') AS topup_count,
                    COALESCE(SUM(amount) FILTER (WHERE type = 'topup'), 0) AS topup_total,
                    COUNT(*) FILTER (WHERE type = 'purchase') AS purchase_count,
                    COALESCE(SUM(-amount) FILTER (WHERE type = 'purchase'), 0) AS purchase_total
                FROM transactions
                WHERE user_id = $1
            """, user_id)
            return dict(row) if row else {
                "topup_count": 0, "topup_total": 0, "purchase_count": 0, "purchase_total": 0
            }

    # ─── AUTO PAYMENTS (HUMOcard) ───────────────────────────────
    async def get_reserved_amounts(self, target_card: str = "") -> set:
        """Berilgan karta uchun hozir 'pending' va muddati o'tmagan barcha
        band qilingan summalar (kartalar orasida summa fazosi mustaqil)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT final_amount FROM auto_payments "
                "WHERE status = 'pending' AND expires_at > NOW() AND target_card = $1",
                target_card
            )
            return {int(r["final_amount"]) for r in rows}

    async def get_pending_auto_payments(self):
        """Hozir 'pending' va muddati o'tmagan BARCHA avtomatik to'lovlarni
        qaytaradi — TolovAPI kabi tashqi tekshiruvchi (polling) manbalar
        buni bittalab tashqi API orqali tekshirib chiqadi."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM auto_payments WHERE status = 'pending' AND expires_at > NOW() "
                "ORDER BY created_at ASC"
            )
            return [dict(r) for r in rows]

    async def add_auto_payment(self, user_id: int, base_amount: int, final_amount: int,
                                expires_at: datetime, target_card: str = "") -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO auto_payments (user_id, base_amount, final_amount, expires_at, target_card)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
            """, user_id, base_amount, final_amount, expires_at, target_card)

    async def get_auto_payment(self, payment_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM auto_payments WHERE id = $1", payment_id)
            return dict(row) if row else None

    async def find_matching_auto_payment(self, amount: int, card_last: str = ""):
        """HUMOcard'dan kelgan summa (va agar bilinsa karta oxiri) ga mos,
        hali 'pending' bo'lgan so'rovni topadi va band qilib qaytaradi.
        Karta mos kelishi ixtiyoriy: agar so'rovda karta belgilanmagan
        (target_card bo'sh) BO'LSA, YOKI kiruvchi tomonda karta noma'lum
        bo'lsa (masalan TolovAPI/webhook qaysi kartani bilmaydi, card_last
        bo'sh keladi) — istalgan kartadan tushgan pul mos deb hisoblanadi."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE auto_payments SET status = 'matching'
                WHERE id = (
                    SELECT id FROM auto_payments
                    WHERE final_amount = $1 AND status = 'pending' AND expires_at > NOW()
                      AND (target_card = '' OR $2 = '' OR target_card = $2)
                    ORDER BY created_at ASC LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
            """, amount, card_last)
            return dict(row) if row else None

    async def complete_auto_payment(self, payment_id: int, card_used: str = ""):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE auto_payments SET status = 'completed', completed_at = NOW(), card_used = $2
                WHERE id = $1
            """, payment_id, card_used)

    async def cancel_auto_payment(self, payment_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE auto_payments SET status = 'expired' WHERE id = $1 AND status IN ('pending', 'matching')", payment_id)

    async def expire_old_auto_payments(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE auto_payments SET status = 'expired'
                WHERE status = 'pending' AND expires_at <= NOW()
            """)
