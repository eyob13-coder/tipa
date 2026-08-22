"""Live tip overlay (#7) for OBS / Streamlabs browser sources.

An in-process pub/sub hub: verified tips publish an event, connected
Server-Sent-Events streams fan it out to any open overlay pages.

Note: the hub lives inside one process. With multiple API workers behind a
load balancer each worker only sees its own subscribers — fine for the
single-instance deployments Tipa targets; swap in Redis pub/sub if that
changes.
"""
import asyncio
import html
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SUBSCRIBERS: dict[str, set[asyncio.Queue]] = {}
_KEEPALIVE_SECONDS = 15.0
# Memory guard: one OBS page plus a few previews is plenty per creator.
_MAX_SUBSCRIBERS_PER_CREATOR = 50


def subscribe(creator_id: str) -> asyncio.Queue | None:
    """Register a listener queue; None when the creator's fan-out is full."""
    creator_key = str(creator_id)
    bucket = _SUBSCRIBERS.setdefault(creator_key, set())
    if len(bucket) >= _MAX_SUBSCRIBERS_PER_CREATOR:
        logger.warning("Overlay subscriber cap reached for creator %s", creator_id)
        return None
    queue: asyncio.Queue = asyncio.Queue(maxsize=32)
    bucket.add(queue)
    return queue


def unsubscribe(creator_id: str, queue: asyncio.Queue) -> None:
    bucket = _SUBSCRIBERS.get(str(creator_id))
    if not bucket:
        return
    bucket.discard(queue)
    if not bucket:
        _SUBSCRIBERS.pop(str(creator_id), None)


def subscriber_count(creator_id: str) -> int:
    return len(_SUBSCRIBERS.get(str(creator_id), ()))


def publish_tip(creator_id: str, payload: dict[str, Any]) -> None:
    """Fan a verified-tip event out to all overlay viewers of this creator."""
    creator_key = str(creator_id)
    event = {
        "event": "tip",
        "amount": float(payload.get("amount", 0)),
        "tipper": payload.get("tipper") or "A follower",
        "note": payload.get("note"),
    }
    for queue in list(_SUBSCRIBERS.get(creator_key, ())):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Overlay queue full for creator %s; dropping event", creator_id)


def format_sse(data: dict[str, Any], event_name: str = "tip") -> str:
    return f"event: {event_name}\ndata: {json.dumps(data)}\n\n"


OVERLAY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Tipa Live Tips</title>
<style>
  :root { color-scheme: dark; }
  body {
    margin: 0; background: transparent; overflow: hidden;
    font-family: 'Segoe UI', system-ui, sans-serif;
  }
  #alerts { position: fixed; top: 4vh; left: 50%; transform: translateX(-50%); }
  .alert {
    display: flex; align-items: center; gap: 18px;
    background: linear-gradient(135deg, #16a34aee, #065f46ee);
    border: 2px solid #4ade80; border-radius: 18px;
    padding: 20px 34px; margin-bottom: 12px;
    color: #fff; box-shadow: 0 10px 40px #000a;
    animation: pop .45s cubic-bezier(.2,1.6,.4,1), fadeout .6s ease-in 5s forwards;
  }
  .alert .emoji { font-size: 44px; }
  .alert .who { font-size: 26px; font-weight: 700; }
  .alert .what { font-size: 19px; opacity: .92; }
  @keyframes pop { from { transform: scale(.4); opacity: 0; } to { transform: scale(1); opacity: 1; } }
  @keyframes fadeout { to { transform: translateY(-24px); opacity: 0; } }
</style>
</head>
<body>
<div id="alerts"></div>
<script>
const CREATOR_ID = __CREATOR_ID__;
function showAlert(tip) {
  const el = document.createElement('div');
  el.className = 'alert';

  // Build with textContent — tipper names and notes are user-supplied, so
  // raw HTML interpolation here would be a stored-XSS sink.
  const emoji = document.createElement('div');
  emoji.className = 'emoji';
  emoji.textContent = '🎁';

  const body = document.createElement('div');
  const who = document.createElement('div');
  who.className = 'who';
  who.textContent = `${String(tip.tipper)} tipped ${tip.amount} ETB!`;
  body.appendChild(who);
  if (tip.note) {
    const what = document.createElement('div');
    what.className = 'what';
    what.textContent = `"${String(tip.note).slice(0, 80)}"`;
    body.appendChild(what);
  }

  el.appendChild(emoji);
  el.appendChild(body);
  document.getElementById('alerts').appendChild(el);
  setTimeout(() => el.remove(), 6000);
}
function connect() {
  const es = new EventSource(`/overlay/${CREATOR_ID}/stream`);
  es.addEventListener('tip', (e) => showAlert(JSON.parse(e.data)));
  es.onerror = () => setTimeout(connect, 3000);
}
connect();
</script>
</body>
</html>"""


def render_overlay_page(creator_id: str) -> str:
    """The OBS browser-source page for one creator (id is JSON-escaped)."""
    safe_id = json.dumps(html.escape(str(creator_id)))
    return OVERLAY_HTML.replace("__CREATOR_ID__", safe_id)
