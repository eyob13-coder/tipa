# Tipa Production-Hardening Report

**Date:** 2026-08-22
**Scope:** Security, correctness, and operational readiness audit + fixes. No new features; all existing behavior preserved (170 tests pass before and after).

---

## Verification summary

| Gate | Result |
|---|---|
| `pytest` | 170 passed, 1 skipped (pyzbar optional) |
| `ruff check app tests` | clean |
| Alembic migration chain | single head (`f9a0b1c2d3e4`), verified on scratch DB |
| New regression tests | 23 across Groups A–E |

Commits: `f3276cc` (A), `8256f16` (B), `e1b9250` (C), `f2095df` (D), `3fe24be` (E), `0244fb6` (F), plus this report.

---

## Findings and dispositions

### P1 — fixed

**A. Float money at trust boundaries**
Provider results carried `float` amounts; comparisons/logs converted Decimal→float.
*Fix:* `VerifyResult.amount: Decimal`; all three providers parse with
`Decimal(str(x))`; config money fields (`PLATFORM_FEE_BIRR`,
`PRO_PRICE_BIRR`, `TIPPER_DAILY_BIRR_CAP`, `ACCOUNT_VERIFICATION_AMOUNT_BIRR`)
are now `Decimal`; the daily-cap check does pure Decimal arithmetic;
verification-log signatures accept `Decimal`. Remaining `float()` calls are
display formatting (`:g`) or JSON/CSV serialization only — no arithmetic.

**B. Tipper identity spoofing in `POST /api/tip/initialize`** (`tests/test_group_b_authz.py`)
The body-supplied `tipper_telegram_id` was trusted even when initData was
valid → any authenticated client could bypass the daily cap by rotating ids or
misattribute tips.
*Fix:* identity comes from the initData signature; body id is used only in
dev mode without a bot token. Claim endpoint likewise binds to the verified
user (403 for other users' sessions) and stamps anonymous tips with the
verified claimer id.

**C. Claim TOCTOU races** (`tests/test_group_a_hardening.py`)
Check-then-act on `ref_id` in API tip claim, bot tip claim, Pro-subscription
claim, and account-ownership claim; concurrent losers hit the unique index as
an unhandled `IntegrityError`.
*Fix:* every ref-claim commit is wrapped — API returns 409, bot flows reply
with the friendly duplicate message; nothing partial is persisted (rollback).
Plus a new unique constraint on `creators.account_verification_ref`
(migration `f9a0b1c2d3e4`, batch mode so SQLite works too): one micro-deposit
receipt can only ever verify one creator account.

**D. Creator approval double-fire** (`test_approval_side_effects_fire_exactly_once`)
The status guard was check-then-act; two concurrent callbacks could both pass
and both fire webhooks / VIP invites / overlay alerts / notifications.
*Fix:* the transition is now atomic
(`UPDATE tips SET status='success' WHERE id=? AND status='pending_verification'`);
the rowcount loser exits before side effects. Sequential double-taps still get
the "already processed" alert (pre-guard kept).

**E. Stored XSS in overlay + Mini App** (`tests/test_group_c_xss.py`)
Overlay alerts rendered attacker-controlled note/display-name via innerHTML;
Mini App recent-tips list interpolated the same fields unescaped.
*Fix:* overlay builds DOM via `textContent`; miniapp escapes with a strict
entity replacer. SSE wire format unchanged (single JSON data frame; control
chars escaped by `json.dumps` so no frame forging).

**F. Auth fails open without BOT_TOKEN** (`test_production_without_bot_token_fails_closed`)
If production ever booted without a token, the whole Mini App API served
unauthenticated.
*Fix:* new `APP_ENV` setting; `production` fails closed (503) and logs loudly
at startup (also warns when webhook URL is set without secret). Dev behavior
unchanged.

### P2 — fixed

**G. Webhook SSRF** (`tests/test_group_d_ssrf.py`)
Creator-supplied webhook URLs only needed an `https://` prefix — internal
HTTPS endpoints (cloud metadata etc.) were reachable.
*Fix:* registration resolves the hostname and rejects private / loopback /
link-local / reserved / multicast addresses; delivery re-resolves before each
POST (DNS-rebinding mitigation). Unresolvable hosts are rejected at
registration; a transient DNS failure at delivery degrades to a normal failed
attempt (retry logic), never a silent skip.

**H. Receipt upload validation** (`tests/test_group_e_receipts.py`)
Stored bytes went to disk unvalidated; OCR opened whatever arrived.
*Fix:* size cap (8 MB), real-image verification via PIL, 25 MP
decompression-bomb budget enforced in storage **and** before OCR.

**I. Rate-limit keys behind proxies**
Per-IP limits keyed on the socket peer; behind Render's edge every client
shared one bucket (or an attacker could rotate spoofed XFF).
*Fix:* uvicorn runs with `--proxy-headers --forwarded-allow-ips=*` on
Render/Procfile/Docker CMD (port not publicly reachable except through the
platform router); tip-init limiting no longer depends on the forgeable body id.

**J. No CI** — added `.github/workflows/ci.yml`: ruff + pytest on push/PR.

### P3 — fixed

- Telegram webhook secret compared in constant time (`hmac.compare_digest`).
- SSE hub caps subscribers per creator (50); overflow gets HTTP 503.
- Dockerfile: non-root user, `HEALTHCHECK`, dropped `build-essential`.
- compose: db healthcheck gates web start; credentials env-overridable.
- Cleanup: duplicate logger in `unlock.py`, missing route type annotation.

### Accepted limitations (documented, deliberately not changed)

1. **`GET /metrics` is public.** It exposes aggregate provider/status counts
   (no PII, no amounts). Gate it with a proxy rule if that ever matters.
2. **Creator profile API returns payout details** (account number/name) to any
   *authenticated* viewer — required for tippers to actually pay. This is the
   product model (no custody), not a leak; `is_frozen` exists for abuse.
3. **Overlay/SSE is public by design** (OBS browser sources can't auth);
   payloads are display-only and now XSS-safe.
4. **In-process overlay hub**: multi-worker deployments would need Redis
   pub/sub (noted in module docstring); current target is single instance.
5. **`--forwarded-allow-ips=*`**: safe only while the app port is reachable
   solely through the platform's trusted edge (true on Render). On a raw VPS,
   restrict it to the local reverse-proxy IP instead.
6. **SQLite create_all shows two equivalent UNIQUE constraints** on
   `account_verification_ref` (column flag + batch-migration constraint);
   PostgreSQL gets exactly one named constraint. Harmless duplication.
7. **No request-body size cap globally** — FastAPI/uvicorn defaults apply;
   the high-risk inputs (receipt images, notes, refs, URLs) are individually
   bounded now.
8. **Webhook delivery has one retry, no backoff/dead-letter.** Adequate for
   best-effort notifications; last_status/last_delivered_at expose failures.

---

## Test coverage added

| File | Focus |
|---|---|
| `tests/test_group_a_hardening.py` | Decimal providers, unique backstops, 409 race, IntegrityError paths, approval atomicity |
| `tests/test_group_b_authz.py` | signed identity vs body forgery, non-owner claim 403, fail-closed prod auth |
| `tests/test_group_c_xss.py` | no innerHTML sink, inert JSON frames, subscriber cap |
| `tests/test_group_d_ssrf.py` | IP classification, literal + resolved private rejection, delivery-time skip |
| `tests/test_group_e_receipts.py` | image/size/pixel validation |
