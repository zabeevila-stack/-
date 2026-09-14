import asyncio
import csv
import io
import os
import sqlite3
from datetime import datetime
from html import escape
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv


# ============================================================
# DELIX REFERAL V5
# Полностью переработанная версия на базе загруженного bot.py
#
# Главное:
# - /start -> обязательный выбор возраста
# - предложения зависят от возраста
# - Т-Банк / Альфа-Банк
# - дебетовые и кредитные карты, самозанятость, бизнес
# - SQLite: пользователи, нажатия, заявки
# - статистика пользователя
# - админка и CSV
# - старые сообщения бота удаляются при навигации
# - банковские ссылки хранятся в .env
#
# ВАЖНО:
# Бот не получает данные о фактической заявке из банка.
# "Переход" ниже означает нажатие кнопки внутри бота.
# Подтвержденные заявки/выплаты добавляются администратором.
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
TBANK_REFERRAL_URL = os.getenv("TBANK_REFERRAL_URL")
ALFA_REFERRAL_URL = os.getenv("ALFA_REFERRAL_URL")
SUPPORT_URL = os.getenv("SUPPORT_URL")

TBANK_CREDIT_URL = os.getenv(
    "TBANK_CREDIT_URL",
    "https://www.tbank.ru/cards/credit-cards/tinkoff-platinum/",
)
ALFA_CREDIT_URL = os.getenv(
    "ALFA_CREDIT_URL",
    "https://alfabank.ru/get-money/credit-cards/100-days/",
)

TBANK_BUSINESS_URL = os.getenv(
    "TBANK_BUSINESS_URL",
    "https://www.tbank.ru/business/account/ip/",
)
ALFA_BUSINESS_URL = os.getenv(
    "ALFA_BUSINESS_URL",
    "https://alfabank.ru/sme/start/",
)
ALFA_SELFEMPLOYED_URL = os.getenv(
    "ALFA_SELFEMPLOYED_URL",
    "https://alfabank.ru/selfemployed/",
)

# Администраторы. Можно указать один ID через ADMIN_ID
# или несколько через ADMIN_IDS=123,456,789
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
_single_admin_id = os.getenv("ADMIN_ID", "").strip()
if _single_admin_id.isdigit():
    ADMIN_IDS.add(int(_single_admin_id))

DB_PATH = os.getenv("DB_PATH", "referral.db")


def require_env(name: str, value: Optional[str]):
    if not value:
        raise ValueError(f"❌ Не найден {name} в файле .env")
    if not value.startswith(("http://", "https://")):
        raise ValueError(
            f"❌ {name} должна начинаться с http:// или https://"
        )


if not BOT_TOKEN:
    raise ValueError("❌ Не найден BOT_TOKEN в файле .env")

require_env("TBANK_REFERRAL_URL", TBANK_REFERRAL_URL)
require_env("ALFA_REFERRAL_URL", ALFA_REFERRAL_URL)
require_env("SUPPORT_URL", SUPPORT_URL)
require_env("TBANK_CREDIT_URL", TBANK_CREDIT_URL)
require_env("ALFA_CREDIT_URL", ALFA_CREDIT_URL)
require_env("TBANK_BUSINESS_URL", TBANK_BUSINESS_URL)
require_env("ALFA_BUSINESS_URL", ALFA_BUSINESS_URL)
require_env("ALFA_SELFEMPLOYED_URL", ALFA_SELFEMPLOYED_URL)


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

db.executescript(
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        age INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS clicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        bank TEXT NOT NULL,
        offer TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        bank TEXT NOT NULL,
        offer TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        amount REAL NOT NULL DEFAULT 0,
        note TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    """
)
db.commit()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def upsert_user(user, age: Optional[int] = None):
    existing = db.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user.id,),
    ).fetchone()

    timestamp = now()

    if existing:
        if age is None:
            db.execute(
                """
                UPDATE users
                SET username = ?, first_name = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (
                    user.username,
                    user.first_name,
                    timestamp,
                    user.id,
                ),
            )
        else:
            db.execute(
                """
                UPDATE users
                SET username = ?, first_name = ?, age = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (
                    user.username,
                    user.first_name,
                    age,
                    timestamp,
                    user.id,
                ),
            )
    else:
        db.execute(
            """
            INSERT INTO users
            (user_id, username, first_name, age, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user.id,
                user.username,
                user.first_name,
                age,
                timestamp,
                timestamp,
            ),
        )

    db.commit()


def get_user(user_id: int):
    return db.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()


def get_age(user_id: int) -> Optional[int]:
    row = get_user(user_id)
    return int(row["age"]) if row and row["age"] is not None else None


def add_click(user_id: int, bank: str, offer: str):
    db.execute(
        """
        INSERT INTO clicks (user_id, bank, offer, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, bank, offer, now()),
    )
    db.commit()


def user_clicks(user_id: int) -> int:
    row = db.execute(
        "SELECT COUNT(*) AS n FROM clicks WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return int(row["n"])


def user_applications(user_id: int) -> int:
    row = db.execute(
        "SELECT COUNT(*) AS n FROM applications WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return int(row["n"])


def user_paid(user_id: int) -> float:
    row = db.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM applications
        WHERE user_id = ? AND status = 'paid'
        """,
        (user_id,),
    ).fetchone()
    return float(row["total"] or 0)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def admin_configured() -> bool:
    return bool(ADMIN_IDS)


# ============================================================
# OFFERS
# ============================================================

OFFERS = {
    "tbank_debit": {
        "bank": "Т-Банк",
        "title": "Дебетовая карта Т-Банка",
        "reward": "до 3 000 ₽",
        "min_age": 14,
        "url": TBANK_REFERRAL_URL,
    },
    "alfa_debit": {
        "bank": "Альфа-Банк",
        "title": "Альфа-Карта",
        "reward": "1 500 ₽",
        "min_age": 14,
        "url": ALFA_REFERRAL_URL,
    },
    "tbank_credit": {
        "bank": "Т-Банк",
        "title": "Кредитная карта «Платинум»",
        "reward": "по условиям программы",
        "min_age": 18,
        "url": TBANK_CREDIT_URL,
    },
    "alfa_credit": {
        "bank": "Альфа-Банк",
        "title": "Кредитная карта",
        "reward": "по условиям программы",
        "min_age": 18,
        "url": ALFA_CREDIT_URL,
    },
    "alfa_self": {
        "bank": "Альфа-Банк",
        "title": "Самозанятость + Альфа-Карта",
        "reward": "по условиям банка",
        "min_age": 14,
        "url": ALFA_SELFEMPLOYED_URL,
    },
    "tbank_business": {
        "bank": "Т-Банк",
        "title": "Бизнес-счёт / ИП",
        "reward": "по условиям бизнес-программы",
        "min_age": 18,
        "url": TBANK_BUSINESS_URL,
    },
    "alfa_business": {
        "bank": "Альфа-Банк",
        "title": "Бизнес-счёт / ИП",
        "reward": "по условиям бизнес-программы",
        "min_age": 18,
        "url": ALFA_BUSINESS_URL,
    },
}


def available_offers(age: int) -> list:
    return [
        key for key, offer in OFFERS.items()
        if age >= offer["min_age"]
    ]


# ============================================================
# UI HELPERS
# ============================================================

async def safe_delete(message: Optional[Message]):
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        # Telegram может не дать удалить сообщение:
        # например, если оно слишком старое или нет прав.
        pass


async def replace_screen(
    callback: CallbackQuery,
    text: str,
    keyboard: Optional[InlineKeyboardMarkup] = None,
):
    try:
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        # Если сообщение нельзя отредактировать, удаляем его
        # и создаём новое.
        await safe_delete(callback.message)
        await callback.message.answer(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def back_button(target: str = "menu"):
    return InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=target,
    )


def age_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="14", callback_data="age:14"),
            InlineKeyboardButton(text="15", callback_data="age:15"),
            InlineKeyboardButton(text="16", callback_data="age:16"),
        ],
        [
            InlineKeyboardButton(text="17", callback_data="age:17"),
            InlineKeyboardButton(text="18", callback_data="age:18"),
            InlineKeyboardButton(text="19", callback_data="age:19"),
        ],
        [
            InlineKeyboardButton(text="20", callback_data="age:20"),
            InlineKeyboardButton(text="21", callback_data="age:21"),
            InlineKeyboardButton(text="22", callback_data="age:22"),
        ],
        [
            InlineKeyboardButton(text="23+", callback_data="age:23"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Т-Банк",
                    callback_data="bank:tbank",
                ),
                InlineKeyboardButton(
                    text="🔴 Альфа-Банк",
                    callback_data="bank:alfa",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎁 Все предложения",
                    callback_data="offers",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💼 Бизнес / ИП",
                    callback_data="business",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Моя статистика",
                    callback_data="stats",
                ),
                InlineKeyboardButton(
                    text="🎂 Мой возраст",
                    callback_data="change_age",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="ℹ️ Как это работает",
                    callback_data="how",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👨‍💼 Поддержка",
                    callback_data="support",
                )
            ],
        ]
    )


def single_back(target: str = "menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[back_button(target)]]
    )


def bank_keyboard(bank: str, age: int) -> InlineKeyboardMarkup:
    rows = []

    if bank == "tbank":
        rows.append(
            [
                InlineKeyboardButton(
                    text="💳 Дебетовая карта",
                    callback_data="offer:tbank_debit",
                )
            ]
        )
        if age >= 18:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="💰 Кредитная карта «Платинум»",
                        callback_data="offer:tbank_credit",
                    )
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="💼 Бизнес-счёт / ИП",
                        callback_data="offer:tbank_business",
                    )
                ]
            )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="💳 Альфа-Карта",
                    callback_data="offer:alfa_debit",
                )
            ]
        )
        if age >= 18:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="💰 Кредитная карта",
                        callback_data="offer:alfa_credit",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🧾 Самозанятость",
                    callback_data="offer:alfa_self",
                )
            ]
        )
        if age >= 18:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="💼 Бизнес-счёт / ИП",
                        callback_data="offer:alfa_business",
                    )
                ]
            )

    rows.append([back_button("menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def offer_keyboard(offer_key: str) -> InlineKeyboardMarkup:
    offer = OFFERS[offer_key]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Получить предложение",
                    callback_data=f"click:{offer_key}",
                )
            ],
            [
                back_button(
                    "bank:tbank"
                    if offer["bank"] == "Т-Банк"
                    else "bank:alfa"
                )
            ],
        ]
    )


def external_url_keyboard(
    offer_key: str,
    back_target: str = "menu",
) -> InlineKeyboardMarkup:
    offer = OFFERS[offer_key]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Открыть сайт банка",
                    url=offer["url"],
                )
            ],
            [back_button(back_target)],
        ]
    )


# ============================================================
# TEXT
# ============================================================

def start_text(age: int) -> str:
    return (
        "🏦 <b>DELIX REFERAL</b>\n\n"
        f"🎂 Возраст в профиле: <b>{age if age < 23 else '23+'}</b>\n\n"
        "Каталог банковских продуктов и реферальных предложений.\n\n"
        "Доступные категории:\n"
        "• 💳 дебетовые карты\n"
        "• 💰 кредитные карты — с 18 лет\n"
        "• 🧾 решения для самозанятых\n"
        "• 🏢 продукты для бизнеса и ИП\n"
        "• 📊 личная статистика\n\n"
        "⚠️ Условия, возрастные требования, ставки, лимиты и "
        "вознаграждения определяются банком. Перед оформлением "
        "всегда проверяйте актуальные условия на сайте банка.\n\n"
        "👇 Выберите категорию:"
    )

def age_question() -> str:
    return (
        "👋 <b>Добро пожаловать в DELIX REFERAL!</b>\n\n"
        "Перед началом укажи свой возраст.\n\n"
        "🎯 Это нужно, чтобы показывать только те "
        "банковские предложения, которые доступны "
        "тебе по возрасту.\n\n"
        "👇 Выбери возраст:"
    )


def offer_text(key: str, age: int) -> str:
    if key == "tbank_debit":
        extra = (
            "👨‍👩‍👦 Если тебе 14–17 лет, для оформления "
            "нужно согласие родителей."
            if age < 18
            else
            "✅ Для совершеннолетних дополнительных возрастных "
            "ограничений по этой карте нет."
        )

        return (
            "💳 <b>Т-БАНК — ДЕБЕТОВАЯ КАРТА</b>\n\n"
            "🎁 <b>Вознаграждение: до 3 000 ₽</b>\n\n"
            "✨ Что есть:\n"
            "• оформление онлайн\n"
            "• бесплатная доставка\n"
            "• управление в приложении\n"
            "• кэшбэк и другие преимущества карты\n\n"
            f"{extra}\n\n"
            "📌 Точная сумма бонуса и условия выплаты "
            "определяются действующей программой банка.\n\n"
            "👇 Нажми ниже — сначала зафиксируем переход "
            "в статистике бота:"
        )

    if key == "alfa_debit":
        return (
            "🔴 <b>АЛЬФА-БАНК — АЛЬФА-КАРТА</b>\n\n"
            "🎁 <b>Вознаграждение: 1 500 ₽</b>\n\n"
            "✨ Основные преимущества:\n"
            "• бесплатное обслуживание\n"
            "• кэшбэк до 30% в выбранных категориях\n"
            "• суперкэшбэк до 100% в случайной категории\n"
            "• удобные переводы и платежи\n\n"
            "🎂 Доступна с <b>14 лет</b>.\n\n"
            "📌 Для получения реферального бонуса нужно "
            "выполнить условия программы банка.\n\n"
            "👇 Нажми ниже — сначала зафиксируем переход "
            "в статистике бота:"
        )

    if key == "tbank_credit":
        return (
            "💰 <b>Т-БАНК — КРЕДИТНАЯ КАРТА «ПЛАТИНУМ»</b>\n\n"
            "Кредитная карта для совершеннолетних клиентов.\n\n"
            "Основные параметры текущего продукта:\n"
            "• кредитный лимит — до 1 000 000 ₽\n"
            "• до 55 дней без процентов на покупки\n"
            "• до 120 дней на погашение кредитов других банков\n"
            "• оформление онлайн\n\n"
            "🎂 <b>Возраст: от 18 лет</b>.\n\n"
            "⚠️ Лимит, ставка, полная стоимость кредита и итоговые "
            "условия определяются банком индивидуально.\n\n"
            "👇 Открыть страницу продукта:"
        )

    if key == "alfa_credit":
        return (
            "💰 <b>АЛЬФА-БАНК — КРЕДИТНЫЕ КАРТЫ</b>\n\n"
            "В каталоге Альфа-Банка представлены несколько кредитных "
            "продуктов с различными условиями.\n\n"
            "Например, в зависимости от продукта доступны:\n"
            "• льготный период без процентов\n"
            "• кредитный лимит до 1 000 000 ₽ по отдельным продуктам\n"
            "• льготные условия на покупки и другие операции "
            "в соответствии с тарифом\n\n"
            "🎂 <b>Возраст: от 18 лет</b>.\n\n"
            "⚠️ Одобрение, лимит, ставка и условия определяются "
            "банком индивидуально.\n\n"
            "👇 Открыть каталог кредитных карт:"
        )

    if key == "alfa_self":
        return (
            "🧾 <b>АЛЬФА-БАНК — САМОЗАНЯТОСТЬ</b>\n\n"
            "🎁 <b>Реферальное вознаграждение — "
            "по условиям действующей акции</b>\n\n"
            "👤 Подходит тем, кто хочет официально "
            "работать как самозанятый.\n\n"
            "🎂 В отдельных случаях оформить самозанятость "
            "можно с 14 лет при предусмотренных законом "
            "условиях и согласии родителей.\n\n"
            "⚠️ Возможность оформления и бонус зависят "
            "от актуальных условий банка и законодательства.\n\n"
            "👇 Открыть предложение:"
        )

    if key == "tbank_business":
        return (
            "💼 <b>Т-БАНК — БИЗНЕС / ИП</b>\n\n"
            "🏢 Направление для предпринимателей и ИП.\n\n"
            "Что можно получить:\n"
            "• расчётный счёт для бизнеса\n"
            "• онлайн-управление\n"
            "• сервисы для предпринимателей\n"
            "• оформление части услуг дистанционно\n\n"
            "🎂 Стандартный сценарий оформления — "
            "<b>с 18 лет</b>.\n\n"
            "🎁 Вознаграждение зависит от действующей "
            "бизнес-реферальной программы и не гарантируется "
            "самим ботом.\n\n"
            "👇 Открыть сайт Т-Банка:"
        )

    if key == "alfa_business":
        return (
            "💼 <b>АЛЬФА-БАНК — БИЗНЕС / ИП</b>\n\n"
            "🏢 Предложение для предпринимателей:\n"
            "• бизнес-счёт\n"
            "• решения для ИП и компаний\n"
            "• банковские сервисы для бизнеса\n\n"
            "🎂 Стандартное оформление бизнес-продуктов "
            "рассчитано на совершеннолетних.\n\n"
            "🎁 Размер бонуса зависит от действующей "
            "бизнес-программы банка.\n\n"
            "👇 Открыть предложение:"
        )

    return "Предложение не найдено."


# ============================================================
# /START + AGE
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message):
    upsert_user(message.from_user)

    age = get_age(message.from_user.id)

    if age is None:
        await message.answer(
            age_question(),
            reply_markup=age_keyboard(),
            parse_mode="HTML",
        )
        return

    await message.answer(
        start_text(age),
        reply_markup=main_menu(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data.startswith("age:"))
async def age_handler(callback: CallbackQuery):
    raw_age = callback.data.split(":", 1)[1]

    try:
        age = int(raw_age)
    except ValueError:
        await callback.answer("Ошибка возраста", show_alert=True)
        return

    if age < 14:
        await callback.answer(
            "Минимальный возраст в этом боте — 14 лет.",
            show_alert=True,
        )
        return

    upsert_user(callback.from_user, age)

    await replace_screen(
        callback,
        start_text(age),
        main_menu(),
    )
    await callback.answer("Возраст сохранён ✅")


@dp.callback_query(F.data == "change_age")
async def change_age_handler(callback: CallbackQuery):
    await replace_screen(
        callback,
        age_question(),
        age_keyboard(),
    )
    await callback.answer()


# ============================================================
# BANKS
# ============================================================

@dp.callback_query(F.data.startswith("bank:"))
async def bank_handler(callback: CallbackQuery):
    age = get_age(callback.from_user.id)

    if age is None:
        await replace_screen(
            callback,
            age_question(),
            age_keyboard(),
        )
        await callback.answer()
        return

    bank = callback.data.split(":", 1)[1]

    if bank == "tbank":
        text = (
            "💳 <b>ПРЕДЛОЖЕНИЯ Т-БАНКА</b>\n\n"
            f"🎂 Твой возраст: <b>{age if age < 23 else '23+'}</b>\n\n"
            "Дебетовая карта доступна с 14 лет; "
            "до 18 лет требуется согласие родителей.\n\n"
            "Для совершеннолетних дополнительно доступно "
            "направление бизнеса / ИП.\n\n"
            "👇 Выбирай:"
        )
    else:
        text = (
            "🔴 <b>ПРЕДЛОЖЕНИЯ АЛЬФА-БАНКА</b>\n\n"
            f"🎂 Твой возраст: <b>{age if age < 23 else '23+'}</b>\n\n"
            "💳 Альфа-Карта — с 14 лет\n"
            "🧾 Самозанятость — при выполнении "
            "необходимых условий\n"
            + (
                "💼 Бизнес / ИП — для 18+\n"
                if age >= 18
                else ""
            )
            + "\n👇 Выбирай:"
        )

    await replace_screen(
        callback,
        text,
        bank_keyboard(bank, age),
    )
    await callback.answer()


# ============================================================
# OFFERS
# ============================================================

@dp.callback_query(F.data == "offers")
async def offers_handler(callback: CallbackQuery):
    age = get_age(callback.from_user.id)

    if age is None:
        await replace_screen(
            callback,
            age_question(),
            age_keyboard(),
        )
        await callback.answer()
        return

    keys = available_offers(age)

    lines = [
        "🎁 <b>ПОДХОДЯЩИЕ ПРЕДЛОЖЕНИЯ</b>",
        "",
        f"🎂 Возраст: <b>{age if age < 23 else '23+'}</b>",
        "",
    ]

    rows = []

    for key in keys:
        offer = OFFERS[key]
        lines.append(
            f"• {offer['bank']} — {offer['title']} "
            f"— <b>{offer['reward']}</b>"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{offer['bank']} — {offer['title']}",
                    callback_data=f"offer:{key}",
                )
            ]
        )

    lines += [
        "",
        "⚠️ Бонусы и условия могут меняться.",
        "Окончательные условия определяет банк.",
    ]

    rows.append([back_button("menu")])

    await replace_screen(
        callback,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("offer:"))
async def offer_handler(callback: CallbackQuery):
    key = callback.data.split(":", 1)[1]

    if key not in OFFERS:
        await callback.answer("Предложение не найдено.", show_alert=True)
        return

    age = get_age(callback.from_user.id)

    if age is None:
        await replace_screen(
            callback,
            age_question(),
            age_keyboard(),
        )
        await callback.answer()
        return

    offer = OFFERS[key]

    if age < offer["min_age"]:
        await callback.answer(
            f"Это предложение доступно с {offer['min_age']} лет.",
            show_alert=True,
        )
        return

    await replace_screen(
        callback,
        offer_text(key, age),
        offer_keyboard(key),
    )
    await callback.answer()


# ============================================================
# CLICK TRACKING
# ============================================================

@dp.callback_query(F.data.startswith("click:"))
async def click_handler(callback: CallbackQuery):
    key = callback.data.split(":", 1)[1]

    if key not in OFFERS:
        await callback.answer("Предложение не найдено.", show_alert=True)
        return

    age = get_age(callback.from_user.id)

    if age is None:
        await replace_screen(
            callback,
            age_question(),
            age_keyboard(),
        )
        await callback.answer()
        return

    offer = OFFERS[key]

    if age < offer["min_age"]:
        await callback.answer(
            f"Доступно с {offer['min_age']} лет.",
            show_alert=True,
        )
        return

    add_click(
        callback.from_user.id,
        offer["bank"],
        offer["title"],
    )

    # ВАЖНО: callback фиксирует нажатие кнопки.
    # Реальный переход по URL происходит после следующего клика.
    await replace_screen(
        callback,
        (
            f"✅ <b>Переход зафиксирован</b>\n\n"
            f"🏦 {escape(offer['bank'])}\n"
            f"📦 {escape(offer['title'])}\n\n"
            "Теперь нажми кнопку ниже, чтобы открыть "
            "сайт банка.\n\n"
            "⚠️ Статистика бота фиксирует именно нажатие "
            "кнопки. Факт оформления и выплаты банк "
            "не передаёт боту автоматически."
        ),
        external_url_keyboard(key),
    )
    await callback.answer("Переход записан 📊")


# ============================================================
# BUSINESS
# ============================================================

@dp.callback_query(F.data == "business")
async def business_handler(callback: CallbackQuery):
    age = get_age(callback.from_user.id)

    if age is None:
        await replace_screen(
            callback,
            age_question(),
            age_keyboard(),
        )
        await callback.answer()
        return

    if age < 18:
        text = (
            "💼 <b>БИЗНЕС / ИП</b>\n\n"
            "Стандартные бизнес-предложения в этом разделе "
            "рассчитаны на пользователей 18+.\n\n"
            "Но тебе уже доступны:\n"
            "• 💳 дебетовые карты\n"
            "• 🧾 направление самозанятости — при выполнении "
            "условий\n\n"
            "👇 Посмотреть доступные предложения:"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎁 Мои предложения",
                        callback_data="offers",
                    )
                ],
                [back_button("menu")],
            ]
        )
    else:
        text = (
            "💼 <b>БИЗНЕС / ИП</b>\n\n"
            "Ты совершеннолетний, поэтому доступны "
            "бизнес-направления:\n\n"
            "🏦 Т-Банк — бизнес / ИП\n"
            "🔴 Альфа-Банк — бизнес / ИП\n\n"
            "👇 Выбирай:"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🏦 Т-Банк — бизнес",
                        callback_data="offer:tbank_business",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔴 Альфа — бизнес",
                        callback_data="offer:alfa_business",
                    )
                ],
                [back_button("menu")],
            ]
        )

    await replace_screen(callback, text, keyboard)
    await callback.answer()


# ============================================================
# HOW
# ============================================================

@dp.callback_query(F.data == "how")
async def how_handler(callback: CallbackQuery):
    text = (
        "ℹ️ <b>КАК ЭТО РАБОТАЕТ</b>\n\n"
        "1️⃣ Указываешь возраст.\n\n"
        "2️⃣ Бот показывает подходящие предложения.\n\n"
        "3️⃣ Выбираешь банк и продукт.\n\n"
        "4️⃣ Нажимаешь «Получить предложение» — "
        "бот фиксирует нажатие в статистике.\n\n"
        "5️⃣ Открываешь официальный сайт банка.\n\n"
        "6️⃣ Если выполняешь условия банковской программы, "
        "банк может начислить реферальное вознаграждение.\n\n"
        "⚠️ Важно: бот не гарантирует одобрение заявки, "
        "размер выплаты или факт оформления. Окончательное "
        "решение принимает банк."
    )

    await replace_screen(
        callback,
        text,
        single_back("menu"),
    )
    await callback.answer()


# ============================================================
# STATS
# ============================================================

@dp.callback_query(F.data == "stats")
async def stats_handler(callback: CallbackQuery):
    age = get_age(callback.from_user.id)

    if age is None:
        await replace_screen(
            callback,
            age_question(),
            age_keyboard(),
        )
        await callback.answer()
        return

    clicks = user_clicks(callback.from_user.id)
    apps = user_applications(callback.from_user.id)
    paid = user_paid(callback.from_user.id)

    text = (
        "📊 <b>МОЯ СТАТИСТИКА</b>\n\n"
        f"🎂 Возраст: <b>{age if age < 23 else '23+'}</b>\n\n"
        f"👆 Нажатий «Получить предложение»: <b>{clicks}</b>\n"
        f"📝 Подтверждённых заявок: <b>{apps}</b>\n"
        f"💰 Выплачено: <b>{paid:,.2f} ₽</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "ℹ️ Нажатия считаются автоматически.\n"
        "Подтверждённые заявки и выплаты появляются "
        "после внесения данных администратором."
    )

    await replace_screen(
        callback,
        text,
        single_back("menu"),
    )
    await callback.answer()


# ============================================================
# SUPPORT
# ============================================================

@dp.callback_query(F.data == "support")
async def support_handler(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Написать менеджеру",
                    url=SUPPORT_URL,
                )
            ],
            [back_button("menu")],
        ]
    )

    text = (
        "👨‍💼 <b>ПОДДЕРЖКА</b>\n\n"
        "Если возник вопрос по работе бота или "
        "банковским предложениям — напиши менеджеру.\n\n"
        "⚠️ По условиям конкретного банковского продукта "
        "ориентируйся на официальный сайт банка."
    )

    await replace_screen(callback, text, keyboard)
    await callback.answer()


# ============================================================
# MENU / BACK
# ============================================================

@dp.callback_query(F.data == "menu")
@dp.callback_query(F.data == "back")
async def menu_handler(callback: CallbackQuery):
    age = get_age(callback.from_user.id)

    if age is None:
        await replace_screen(
            callback,
            age_question(),
            age_keyboard(),
        )
    else:
        await replace_screen(
            callback,
            start_text(age),
            main_menu(),
        )

    await callback.answer()


# ============================================================
# ADMIN
# ============================================================

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👥 Пользователи",
                    callback_data="admin:users",
                ),
                InlineKeyboardButton(
                    text="📊 Клики",
                    callback_data="admin:clicks",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📝 Заявки",
                    callback_data="admin:apps",
                ),
                InlineKeyboardButton(
                    text="💰 Выплаты",
                    callback_data="admin:paid",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📥 CSV",
                    callback_data="admin:csv",
                )
            ],
            [back_button("menu")],
        ]
    )


def admin_summary() -> str:
    users = db.execute(
        "SELECT COUNT(*) AS n FROM users"
    ).fetchone()["n"]
    clicks = db.execute(
        "SELECT COUNT(*) AS n FROM clicks"
    ).fetchone()["n"]
    apps = db.execute(
        "SELECT COUNT(*) AS n FROM applications"
    ).fetchone()["n"]
    paid = db.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM applications
        WHERE status = 'paid'
        """
    ).fetchone()["total"]

    return (
        "🛠 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        f"👥 Пользователей: <b>{users}</b>\n"
        f"👆 Клики: <b>{clicks}</b>\n"
        f"📝 Заявки: <b>{apps}</b>\n"
        f"💰 Выплачено: <b>{float(paid or 0):,.2f} ₽</b>\n\n"
        "Для добавления заявки используй:\n"
        "<code>/app USER_ID BANK OFFER STATUS AMOUNT NOTE</code>\n\n"
        "Пример:\n"
        "<code>/app 123456789 Т-Банк Дебетовая_карта paid 3000</code>"
    )


@dp.message(Command("app"))
async def admin_add_application(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    parts = message.text.split(maxsplit=5)

    if len(parts) < 5:
        await message.answer(
            "❌ Формат:\n"
            "<code>/app USER_ID BANK OFFER STATUS AMOUNT [NOTE]</code>\n\n"
            "STATUS: pending / confirmed / paid",
            parse_mode="HTML",
        )
        return

    try:
        user_id = int(parts[1])
        bank = parts[2]
        offer = parts[3]
        status = parts[4].lower()
        amount = float(parts[5].split()[0]) if len(parts) >= 6 else 0
        note = (
            " ".join(parts[5].split()[1:])
            if len(parts) >= 6 and len(parts[5].split()) > 1
            else ""
        )
    except (ValueError, IndexError):
        await message.answer("❌ Не удалось разобрать команду.")
        return

    if status not in {"pending", "confirmed", "paid"}:
        await message.answer(
            "❌ STATUS должен быть pending, confirmed или paid."
        )
        return

    db.execute(
        """
        INSERT INTO applications
        (user_id, bank, offer, status, amount, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, bank, offer, status, amount, note, now()),
    )
    db.commit()

    await message.answer(
        "✅ Заявка добавлена.\n"
        f"ID пользователя: <code>{user_id}</code>\n"
        f"Статус: <b>{escape(status)}</b>\n"
        f"Сумма: <b>{amount:,.2f} ₽</b>",
        parse_mode="HTML",
    )


@dp.message(Command("myid"))
async def my_id_handler(message: Message):
    await message.answer(
        "🆔 Твой Telegram ID:\n"
        f"<code>{message.from_user.id}</code>\n\n"
        "Добавь этот ID в .env как ADMIN_ID=... и перезапусти бота.",
        parse_mode="HTML",
    )


@dp.message(Command("admin"))
@dp.message(Command("appadmin"))
async def admin_handler(message: Message):
    if not admin_configured():
        await message.answer(
            "⚠️ Администратор ещё не настроен.\n\n"
            "1. Напиши /myid\n"
            "2. Скопируй свой ID\n"
            "3. В .env добавь строку ADMIN_ID=ТВОЙ_ID\n"
            "4. Перезапусти бота"
        )
        return

    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    await message.answer(
        admin_summary(),
        reply_markup=admin_keyboard(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "admin:users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    rows = db.execute(
        """
        SELECT user_id, username, first_name, age, created_at
        FROM users
        ORDER BY created_at DESC
        LIMIT 30
        """
    ).fetchall()

    lines = ["👥 <b>ПОСЛЕДНИЕ ПОЛЬЗОВАТЕЛИ</b>", ""]

    if not rows:
        lines.append("Пока пользователей нет.")
    else:
        for row in rows:
            name = escape(row["first_name"] or "Без имени")
            username = (
                f"@{escape(row['username'])}"
                if row["username"]
                else "без username"
            )
            age = row["age"] if row["age"] is not None else "—"
            lines.append(
                f"• <code>{row['user_id']}</code> — "
                f"{name} ({username}), возраст: {age}"
            )

    lines.append("")
    lines.append("Показаны последние 30.")

    await replace_screen(
        callback,
        "\n".join(lines),
        single_back("admin"),
    )
    await callback.answer()


@dp.callback_query(F.data == "admin:clicks")
async def admin_clicks(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    rows = db.execute(
        """
        SELECT bank, offer, COUNT(*) AS n
        FROM clicks
        GROUP BY bank, offer
        ORDER BY n DESC
        """
    ).fetchall()

    lines = ["📊 <b>КЛИКИ ПО ПРЕДЛОЖЕНИЯМ</b>", ""]

    if not rows:
        lines.append("Кликов пока нет.")
    else:
        for row in rows:
            lines.append(
                f"• {escape(row['bank'])} — "
                f"{escape(row['offer'])}: <b>{row['n']}</b>"
            )

    await replace_screen(
        callback,
        "\n".join(lines),
        single_back("admin"),
    )
    await callback.answer()


@dp.callback_query(F.data == "admin:apps")
async def admin_apps(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    rows = db.execute(
        """
        SELECT id, user_id, bank, offer, status, amount, created_at
        FROM applications
        ORDER BY id DESC
        LIMIT 30
        """
    ).fetchall()

    lines = ["📝 <b>ЗАЯВКИ</b>", ""]

    if not rows:
        lines.append("Заявок пока нет.")
    else:
        for row in rows:
            lines.append(
                f"#{row['id']} | {row['user_id']} | "
                f"{escape(row['bank'])} | "
                f"{escape(row['offer'])} | "
                f"{escape(row['status'])} | "
                f"{float(row['amount']):,.2f} ₽"
            )

    await replace_screen(
        callback,
        "\n".join(lines),
        single_back("admin"),
    )
    await callback.answer()


@dp.callback_query(F.data == "admin:paid")
async def admin_paid(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    total = db.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM applications
        WHERE status = 'paid'
        """
    ).fetchone()["total"]

    count = db.execute(
        """
        SELECT COUNT(*) AS n
        FROM applications
        WHERE status = 'paid'
        """
    ).fetchone()["n"]

    text = (
        "💰 <b>ВЫПЛАТЫ</b>\n\n"
        f"Количество оплаченных заявок: <b>{count}</b>\n"
        f"Общая сумма: <b>{float(total or 0):,.2f} ₽</b>"
    )

    await replace_screen(
        callback,
        text,
        single_back("admin"),
    )
    await callback.answer()


@dp.callback_query(F.data == "admin:csv")
async def admin_csv(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "user_id",
            "bank",
            "offer",
            "status",
            "amount",
            "note",
            "created_at",
        ]
    )

    rows = db.execute(
        """
        SELECT id, user_id, bank, offer, status, amount, note, created_at
        FROM applications
        ORDER BY id DESC
        """
    ).fetchall()

    for row in rows:
        writer.writerow(
            [
                row["id"],
                row["user_id"],
                row["bank"],
                row["offer"],
                row["status"],
                row["amount"],
                row["note"],
                row["created_at"],
            ]
        )

    data = output.getvalue().encode("utf-8-sig")
    document = BufferedInputFile(
        data,
        filename="delix_applications.csv",
    )

    await callback.message.answer_document(document)
    await callback.answer("CSV готов ✅")


@dp.callback_query(F.data == "admin")
async def admin_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    await replace_screen(
        callback,
        admin_summary(),
        admin_keyboard(),
    )
    await callback.answer()


# ============================================================
# ERROR-SAFE CLOSE
# ============================================================

async def shutdown():
    db.commit()
    db.close()


# ============================================================
# RUN
# ============================================================

async def main():
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("🔥 DELIX REFERAL V5")
    print("✅ Новый код загружен")
    print("✅ SQLite включён")
    print("✅ Возрастная фильтрация включена")
    print("✅ Статистика кликов включена")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await dp.start_polling(bot)
    finally:
        await shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен.")
