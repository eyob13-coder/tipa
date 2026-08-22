"""Group C regression tests: overlay XSS sink removal + subscriber cap."""
from app.overlay import (
    _MAX_SUBSCRIBERS_PER_CREATOR,
    OVERLAY_HTML,
    format_sse,
    publish_tip,
    subscribe,
    unsubscribe,
)


def test_overlay_html_has_no_innerhtml_sink():
    assert "innerHTML" not in OVERLAY_HTML
    assert "textContent" in OVERLAY_HTML


def test_hostile_event_data_stays_inert_json():
    import json

    queue = subscribe("c1")
    assert queue is not None
    publish_tip(
        "c1",
        {
            "amount": 5,
            "tipper": "<script>alert(1)</script>",
            "note": '"</script><img src=x onerror=alert(3)>',
        },
    )
    wire = format_sse(queue.get_nowait())

    # Exactly one JSON data frame; newlines inside the payload cannot forge
    # extra SSE frames because json.dumps escapes control characters.
    data_lines = [ln for ln in wire.splitlines() if ln.startswith("data: ")]
    assert len(data_lines) == 1
    parsed = json.loads(data_lines[0][len("data: ") :])
    # The hostile strings arrive verbatim as *data* — inert until the client
    # renders them via textContent.
    assert parsed["tipper"] == "<script>alert(1)</script>"
    assert "</script>" in parsed["note"]
    unsubscribe("c1", queue)


def test_subscriber_cap_per_creator():
    queues = []
    for _ in range(_MAX_SUBSCRIBERS_PER_CREATOR):
        q = subscribe("cap-creator")
        assert q is not None
        queues.append(q)
    # Next subscriber is refused instead of growing memory unboundedly.
    assert subscribe("cap-creator") is None
    for q in queues:
        unsubscribe("cap-creator", q)
    assert subscribe("cap-creator") is not None
