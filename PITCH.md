# Job Pitch — Ethiopian Payments & Fintech

Hi [Name / Hiring Team],

I built **Tipa**, a Telegram tipping system that lets Ethiopian followers tip channel
creators directly in Birr. Money flows straight from the tipper's mobile money wallet or
bank account (Telebirr, CBE, CBE Birr, M-Pesa, and more) to the creator's own account —
the platform never custodies funds: no wallets, no balances, no virtual assets.

I'm sharing it because it demonstrates the exact skills a payments company like
[Company Name] needs — and I want to do this work for real.

## What the project proves

- **Money-movement design**: Birr tipper → creator's bank account. Tipa only
  orchestrates and verifies — it never holds money. That "no custody" decision is the
  kind of thing you have to think about every day.
- **Payment verification with real failover**: I integrated **three independent
  verification providers** (verify.et, Check.et, JustVerify) behind a single provider
  interface. If one provider is unreachable or its rail is down — Telebirr verification
  has real upstream outages — the registry automatically fails over to the next one.
  A definitive "transaction not found" still stops the chain so nothing is overridden
  blindly. This is provider redundancy, not a mockup.
- **Fraud-aware claim handling**: every SMS receipt code is checked for duplicates
  before it can be claimed again (application check + DB unique index), and the exact
  verified amount is compared to the expected tip before a tip is confirmed.
- **Idempotency & queued-result handling**: verify.et is called with an idempotency
  key; async-queued verifications are polled until terminal, with timeout fallback.
- **End-to-end ownership**: async FastAPI backend, `python-telegram-bot`, a Telegram
  Mini App, SQLAlchemy + Alembic migrations, PostgreSQL/SQLite, background
  reminder/expiry jobs, env-based config, structured logging, and a 27-test pytest
  suite covering success paths, provider failover, duplicate references, and errors.
- **Production-grade engineering**: typed settings, defensive parsing of third-party
  responses, and code organized the way a payments team would expect
  (`app/verify/` = providers + registry + service).

## Stack

FastAPI · python-telegram-bot · SQLAlchemy (async) + Alembic · verify.et / Check.et /
JustVerify APIs · httpx · pytest · Docker · Render

## Where I want to go

I want to work on payment systems professionally — integrations, verification,
reconciliation, provider failover, webhook reliability, payout flows. I'm comfortable
taking a small feature or an internal tool and owning it end-to-end.

I'd love 20 minutes to walk through Tipa live — how a tip is verified across three
providers, how a duplicate reference is caught, and how a payout lands in a creator's
account.

---

# Target companies & the angle to lead with

## Tier 1 — Verification providers (best fit, your project is literally their product)

These companies build exactly what Tipa's verification layer is. Lead with the
multi-provider registry + failover design.

| Company | Why Tipa fits | Where to apply |
| --- | --- | --- |
| **Check.et** | Your CBE/Telebirr verification adapter targets their API | check.et (Developers → Create account) |
| **JustVerify** | Same — their `POST /v1/verify` is one of your three providers | justverify.et (register) |
| **qbirr** | Ethiopian payment verification API, same use case | qbirr.com/docs |
| **TrustPay ET** (fava technologies) | "Verify before you pay" — your exact flow | trustpay.favatechnologies.com |
| **verify.et / Suba Software** | The original provider you built around | verify.et/docs |
| **ShegerPay** | Verification + rails, hiring developers | shegerpay.com/docs |

## Tier 2 — Payment gateways & wallets (actively hiring, most jobs)

| Company | Why Tipa fits | Where to apply |
| --- | --- | --- |
| **Chapa Financial Technologies** | Leading gateway, **actively hiring backend devs (Go/Rust) + React**; the project shows you know their world | chapa.co/careers |
| **Safaricom Ethiopia (M-PESA)** | 10M+ customers, **Top Employer Award 2 years running**, runs recurring vacancy waves (July 2026) | Oracle STEP careers portal: egjd.fa.us6.oraclecloud.com/.../STEP |
| **Ethio Telecom (telebirr)** | 45M+ telebirr users; Tipa is a Telebirr-native product | ethiotelecom.et/job-openings |
| **ArifPay** | Top-10 hiring fintech for junior devs; payments infra | arifpay.net / LinkedIn |
| **Kifiya Financial Technology** | Veteran fintech, payments + data, large engineering org | kifiya.com / LinkedIn |
| **YenePay / Santimpay / HelloCash** | Local gateways, smaller teams = faster decisions | LinkedIn + direct email |

## Tier 3 — National rails & banks (longer process, huge credibility)

| Company | Why Tipa fits | Where to apply |
| --- | --- | --- |
| **EthSwitch S.C.** | National payments switch, **open vacancies (2026)** | ethswitch.com / etcareers |
| **CBE, Awash, Dashen, Co-op, BOA** | All running digital-banking units; Tipa's CBE integration is direct | bank career portals |
| **UNDP / WFP (digital payments)** | Government digital-payments analyst roles were recently posted | etcareers.com |

## Tier 4 — Talent ecosystem / dev shops (easiest foot in the door)

iceaddis · Gebeya · iCog Labs · Horan Technologies · Apposit — these train and place
developers; a strong repo like this accelerates placement.

---

# How to apply (what actually works in 2026)

1. **Send the pitch to a person, not a portal.** Find the founder/CTO/eng lead on
   LinkedIn (Chapa founders, qbirr/Check.et founders are on Telegram — you can message
   them directly). A cold DM with a 3-line version of this pitch + a link to the repo
   outperforms an application form.
2. **For portal applications**, attach the repo + a 60-second Loom demo of the tip →
   verify → payout flow. Few candidates attach working demos.
3. **Sequence it:** apply to Tier 1 first (fastest feedback, most on-point), then Tier 2
   (most volume), keep Tier 3 running in parallel (slowest).
4. **Tailor the opening line per company** — see the angle column above. Never send the
   same body twice.
