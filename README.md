# Tipa — Telegram Tipping for Ethiopian Creators

**Tipa** is a Telegram bot system that enables followers to tip channel creators directly in Ethiopian Birr (ETB). Money flows straight from the tipper to the creator's own bank account via **Chapa**'s NBE-licensed subaccount split payments. Tipa never custodies funds, and there are no wallets, balances, or virtual assets.

---

## 🌟 Key Features

- **Direct Bank Payouts**: Creators link their Ethiopian bank account during `/register`.
- **Chapa Subaccount Split Payments**: Auto-splits settlement: creator receives their tip minus a flat platform fee (e.g. 2 ETB).
- **Telegram Deep Link**: Each creator gets a personal link (`t.me/TipaBot?start=tip_<creator_id>`) to pin in their Telegram channel.
- **Instant Tip Flow**: Quick-amount buttons (10 / 25 / 50 / 100 Birr) + Custom Amount option.
- **Authoritative Webhook Verification**: FastAPI webhook handler verifies every payment directly against Chapa's `GET /transaction/verify/{tx_ref}` API.
- **Creator Dashboard (`/mytips`)**: Real-time summary of total ETB earned, tip count, and recent tip breakdown.

---

## 🛠 Tech Stack

- **Backend**: FastAPI (Python 3.10+)
- **Bot Framework**: `python-telegram-bot` (v21+, async)
- **Database**: PostgreSQL / SQLite + SQLAlchemy (async) + Alembic migrations
- **Payment Gateway**: Chapa API (Subaccounts, Split Payments, Webhook verification)
- **HTTP Client**: `httpx` async

---

## 📁 Project Structure

```
tipa/
├── app/
│   ├── config.py             # Environment & settings configuration
│   ├── main.py               # FastAPI application & bot lifespan manager
│   ├── db/
│   │   ├── base.py           # SQLAlchemy Base
│   │   ├── models.py         # Creator and Tip database models
│   │   └── session.py        # Async engine & session management
│   ├── chapa/
│   │   └── client.py         # Chapa v1 API client (subaccounts, transactions, banks)
│   ├── bot/
│   │   ├── bot.py            # Telegram Application setup & handler registration
│   │   ├── handlers.py       # /start, /register, /tip, /mytips handlers
│   │   └── keyboards.py      # Inline keyboard builders (banks, presets, checkout)
│   └── webhooks/
│       └── chapa_webhook.py  # Chapa webhook endpoint & verification logic
├── alembic/                  # Database migration scripts
├── tests/                    # Async unit and integration tests
├── requirements.txt
├── .env.example
└── README.md
```

---

## 🚀 Environment Variables

Copy `.env.example` to `.env` and adjust the variables:

```ini
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyZ
CHAPA_SECRET_KEY=CHASECK_TEST-xxxxxx
DATABASE_URL=sqlite+aiosqlite:///./tipa.db
WEBHOOK_BASE_URL=https://your-domain.com
PLATFORM_FEE_BIRR=2.0
```

---

## 🧪 Running Tests

Run the full pytest suite:

```bash
pytest
```

---

## 🖥 Running Locally

1. Run database migrations:
   ```bash
   alembic upgrade head
   ```

2. Start the FastAPI application & Telegram bot:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

---

## 📜 Regulatory & Architectural Guarantee

Tipa never holds, exchanges, or custodies any virtual asset or Birr balance. Every tip is Birr-in from the tipper, split instantly by Chapa (an NBE-licensed gateway), and Birr-out directly to the creator's own bank account.
