# Tipa — Telegram Tipping for Ethiopian Creators

**Tipa** is a Telegram bot system that enables followers to tip channel creators directly in Ethiopian Birr (ETB). Money flows straight from the tipper to the creator's own account via **direct transfers to their mobile money wallet or bank account** (Telebirr, CBE, CBE Birr, M-Pesa, HelloCash, Amole, and more) — Tipa never custodies funds, and there are no wallets, balances, or virtual assets. **Tipa does not use Chapa.**

---

## 🌟 Key Features

- **Direct Mobile Money & Bank Transfers**: Creators link a phone number or bank account during `/register` from a growing list of Ethiopian payment methods (Telebirr, CBE, CBE Birr, Dashen, Awash, BoA, M-Pesa, Zemen, Siinqee, HelloCash, Amole, Coop). Supporters send money straight to that account — no payment gateway in the middle.
- **Server-Side Payment Verification with Provider Failover**: Claims are automatically verified against **three independent providers** — [verify.et](https://verify.et), [Check.et](https://check.et), and [JustVerify](https://justverify.et) — tried in priority order. If one provider is unreachable or its rail is down, the registry fails over to the next; a definitive "transaction not found" stops the chain so nothing is overridden blindly. Duplicate receipt references are rejected. If nothing auto-verifies, the creator gets a one-tap **Approve Tip** button as a fallback.
- **Telegram Deep Link**: Each creator gets a personal link (`t.me/TipaPayBot?start=tip_<creator_id>`) and a QR code to pin in their Telegram channel.
- **Instant Tip Flow**: Quick-amount buttons (10 / 25 / 50 / 100 Birr) + Custom Amount option, in both the bot and the Telegram Mini App.
- **Creator Dashboard (`/mytips` / Mini App)**: Real-time summary of total ETB earned, tip count, and recent tip breakdown.
- **Reconciliation CSV Export**: Creators can download their verified tip history as CSV from the Mini App API (`/api/creator/{id}/export`).
- **Auto-Cancel & Reminders**: Unconfirmed tips expire automatically; creators are reminded to approve pending claims.
- **Security**: Every Mini App API call is authenticated with a Telegram WebApp `initData` HMAC signature; the claim endpoint is rate-limited; the audit log records every verification attempt.
- **Audit Trail**: An append-only `verification_logs` table records every verification attempt (provider, status, amount) plus manual creator approvals.

---

## 🛠 Tech Stack

- **Backend**: FastAPI (Python 3.10+)
- **Bot Framework**: `python-telegram-bot` (v21+, async)
- **Database**: PostgreSQL / SQLite + SQLAlchemy (async) + Alembic migrations
- **Verification**: verify.et, Check.et, and JustVerify APIs (CBE & Telebirr bank/wallet transfer verification) with automatic provider failover
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
│   ├── verify/
│   │   ├── base.py          # VerifyResult + provider interface
│   │   ├── registry.py      # Provider registry with failover
│   │   ├── service.py       # auto_verify_tip() shared by bot & Mini App API
│   │   └── providers/       # verify.et, Check.et, JustVerify adapters
│   ├── api/
│   │   └── routes.py        # Mini App API: profile, tip init, claim, CSV export
│   ├── bot/
│   │   ├── bot.py            # Telegram Application setup & handler registration
│   │   ├── handlers.py       # /start, /register, /tip, /mytips handlers
│   │   ├── notifications.py  # Tip verified/received notifications
│   │   └── keyboards.py      # Inline keyboard builders
│   └── static/
│       └── index.html        # Telegram Mini App (tipper & creator views)
├── docs/
│   └── ARCHITECTURE.md      # Design doc: tip lifecycle, failover, security
├── .github/workflows/ci.yml # ruff + pytest on every push/PR
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
# Optional: verification provider API keys. Tried in priority order with
# automatic failover. Leave all empty to rely on creator approval only.
VERIFY_ET_API_KEY=
VERIFY_ET_BASE_URL=https://verify.et
CHECK_ET_API_KEY=
CHECK_ET_BASE_URL=https://api.check.et/api/v1
JUSTVERIFY_API_KEY=
JUSTVERIFY_BASE_URL=https://justverify.et
DATABASE_URL=sqlite+aiosqlite:///./tipa.db
PLATFORM_FEE_BIRR=0.0
```

---

## 🧪 Running Tests

Run the full pytest suite:

```bash
python -m pytest
```

CI (`.github/workflows/ci.yml`) runs ruff + pytest on every push/PR.

---

## 🖥 Running Locally

1. Run database migrations:
   ```bash
   python -m alembic upgrade head
   ```

2. Start the FastAPI application & Telegram bot:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

---

## 📜 Regulatory & Architectural Guarantee

Tipa never holds, exchanges, or custodies any virtual asset or Birr balance. Every tip is Birr-in from the tipper via a direct mobile money/bank transfer and Birr-out to the creator's own account. The platform only verifies that a transfer really happened (via the verification providers or creator confirmation) and records it.
