# Tipa — Architecture

Tipa is a Telegram tipping system for Ethiopian creators. Supporters tip in Birr via
direct **Telebirr** or **CBE** transfers to the creator's own account; Tipa orchestrates
the tip session, verifies the transfer happened, and notifies both sides. **Tipa never
custodies funds** — no wallets, no balances, no virtual assets.

## Components

```
┌─────────────┐      t.me deep link / QR     ┌──────────────────────────┐
│  Telegram   │ ───────────────────────────▶ │  Telegram Mini App        │
│  (tipper)   │                               │  app/static/index.html   │
└─────────────┘                               └────────────┬─────────────┘
        │                                                   │ initData (signed)
        │ /start /tip /mytips /register                     ▼
        │                          ┌────────────────────────────────────┐
        │                          │        FastAPI (app/main.py)       │
        │                          │  ┌──────────────────────────────┐  │
        │                          │  │  app/api/routes.py           │  │
        └─────────────────────────▶│  │  · initData HMAC validation  │  │
                                   │  │  · claim rate limiting       │  │
                                   │  │  · creator CSV export        │  │
                                   │  └──────────────┬───────────────┘  │
                                   └─────────────────┼──────────────────┘
                                                     ▼
              ┌──────────────────────────────────────────────────────┐
              │             app/verify (verification layer)          │
              │  registry.py ── failover across providers            │
              │    │  ┌──────────────┬────────────────┐               │
              │    │  ▼              ▼                ▼               │
              │    │  verify_et.py  check_et.py    justverify.py     │
              │    └── providers (each implements VerificationProvider)│
              │  service.py ── auto_verify_tip() + audit log         │
              └──────────────────────────────────────────────────────┘
                                   ▲
                                   │ (notify, reminders, approvals)
                                   ▼
              ┌──────────────────────────────────────────────────────┐
              │     python-telegram-bot (app/bot)                    │
              │  handlers.py · keyboards.py · notifications.py       │
              │  reminders.py (claim expiry + approval reminders)    │
              └──────────────────────────────────────────────────────┘
                                   ▼
              ┌──────────────────────────────────────────────────────┐
              │   PostgreSQL / SQLite · SQLAlchemy async · Alembic    │
              │   tables: creators, tips, verification_logs           │
              └──────────────────────────────────────────────────────┘
```

## Tip lifecycle

1. **Register** — creator runs `/register` in the bot, links a Telebirr phone or CBE
   account (`app/bot/handlers.py`).
2. **Tip session** — a follower opens the creator's deep link, chooses an amount, and the
   Mini App calls `POST /api/tip/initialize`, which creates a `pending` `Tip` row and
   returns the creator's transfer details (account, USSD code).
3. **Transfer** — the tipper sends Birr directly to the creator's account via Telebirr /
   CBE. Tipa never touches the money.
4. **Claim** — the tipper submits the SMS receipt/reference code via the Mini App
   (`POST /api/tip/claim`) or the bot. The code is checked for duplicates (app-level
   check + DB unique index on `tips.ref_id`), then passed to `auto_verify_tip`.
5. **Auto-verify** — `app/verify/service.py` asks the provider registry
   (`app/verify/registry.py`) to confirm the transfer by reference.
6. **Fallback** — if no provider can conclusively confirm, the tip goes to
   `pending_verification` and the creator gets a one-tap **Approve Tip** inline button.
7. **Confirmed** — on verification or approval the tip is marked `success`; the tipper
   and creator are notified; a `verification_logs` row records the audit trail.
8. **Expiry** — unconfirmed claims are reminded and auto-cancelled by
   `app/bot/reminders.py`.

## Verification & failover

Each provider is an adapter implementing `VerificationProvider` in
`app/verify/base.py`, returning a normalized `VerifyResult` with a `conclusive` flag:

- **success** — transfer confirmed. Conclusive: verification stops, tip is confirmed if
  the verified amount matches the expected amount (±0.01 ETB).
- **failed / not_found** — definitive negative. Conclusive: the chain stops so a second
  provider never overrides a definitive "not found".
- **pending / unknown / error** — non-conclusive. The registry tries the next provider.

`BANK_PRIORITY` in `app/verify/registry.py` orders providers per bank. verify.et's
Telebirr rail is known to be unreliable, so Check.et leads for Telebirr while verify.et
leads for CBE. A provider that raises `VerificationError` (unreachable, HTTP error) is
skipped in favor of the next.

Every attempt — provider name, result status, verified flag, amount, message — is
written to `verification_logs` (`app/verify/service.py:log_verification_attempt`), giving
a full audit trail that also records manual creator approvals.

## API security

- All `/api/*` endpoints require a valid Telegram WebApp `initData` signature
  (`X-Telegram-Init-Data` header, HMAC-SHA256 over the sorted `key=value` fields using the
  bot token as the secret). Validation is skipped only when `BOT_TOKEN` is unset (local
  dev / CI).
- `POST /api/tip/claim` is rate-limited per client IP (10 claims / 60s sliding window).
- The creator CSV export endpoint verifies the caller is the creator themselves.

## Data model (`app/db/models.py`)

- **Creator** — Telegram identity, payment method (`cbe` | `telebirr`), account number,
  optional linked channel.
- **Tip** — the tip session: creator, amount, platform fee, `tx_ref` (unique), claim
  reference `ref_id` (unique, indexed), status, verification metadata
  (`verification_method`, `verified_at`, `verified_amount`), reminder/expiry timestamps.
- **VerificationLog** — append-only audit of every verification attempt per tip
  (provider, status, verified, amount, message, timestamp).

## Configuration

Environment-driven via `app/config.py` (pydantic-settings, `.env`):

- `BOT_TOKEN` — Telegram bot token (also the initData validation secret).
- `VERIFY_ET_API_KEY` / `CHECK_ET_API_KEY` / `JUSTVERIFY_API_KEY` + base URLs — the three
  verification providers. All empty ⇒ creator-approval-only mode.
- `DATABASE_URL` — async SQLAlchemy URL (PostgreSQL via asyncpg, or SQLite via aiosqlite).
- `PLATFORM_FEE_BIRR`, `BOT_USERNAME`, `TIP_REMINDER_HOURS`, `TIP_EXPIRY_HOURS`,
  `TIP_REMINDER_LOOP_MINUTES`.

## Migrations

Alembic, revisions in `alembic/versions/`. `alembic upgrade head` is safe to re-run on
fresh databases; migrations are defensive (check columns/indexes before altering).
`app/db/session.py:init_db` also runs `create_all` as a startup safety net.

## Testing

`python -m pytest -q` — 40+ tests covering providers, registry failover semantics,
auto-verify success/mismatch/error paths, duplicate-reference rejection, API auth
(401 on missing/tampered initData, 403 on cross-user export), DB models, keyboards, and
reminder/expiry behavior. CI (`.github/workflows/ci.yml`) runs ruff + pytest on every
push/PR.
