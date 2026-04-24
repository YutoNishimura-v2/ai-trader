"""Review-trigger engine + packet generator (plan v3 §A.10)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_trader.review.packet import ReviewContext, write_review_packet
from ai_trader.review.triggers import ReviewTriggerKind, TriggerEngine


def _now(d: int, h: int = 12) -> datetime:
    return datetime(2026, 4, d, h, tzinfo=timezone.utc)


def test_day_rollover_emits_eod():
    te = TriggerEngine()
    got = te.tick(_now(24, 0), consecutive_sl=0, kill_switch=False, day_rollover=True)
    kinds = {t.kind for t in got}
    assert ReviewTriggerKind.EOD in kinds


def test_consecutive_sl_trigger_is_emitted_once_per_day():
    te = TriggerEngine(consecutive_sl_threshold=2)
    # First time threshold is met: fire.
    t1 = te.tick(_now(24, 10), consecutive_sl=2, kill_switch=False, day_rollover=False)
    assert any(t.kind == ReviewTriggerKind.CONSECUTIVE_SL for t in t1)
    # Second tick, still same day: do not fire again.
    t2 = te.tick(_now(24, 11), consecutive_sl=3, kill_switch=False, day_rollover=False)
    assert not any(t.kind == ReviewTriggerKind.CONSECUTIVE_SL for t in t2)


def test_kill_switch_trigger_is_emitted_once_per_day():
    te = TriggerEngine()
    t1 = te.tick(_now(24, 10), consecutive_sl=0, kill_switch=True, day_rollover=False)
    assert any(t.kind == ReviewTriggerKind.KILL_SWITCH for t in t1)
    t2 = te.tick(_now(24, 11), consecutive_sl=0, kill_switch=True, day_rollover=False)
    assert not any(t.kind == ReviewTriggerKind.KILL_SWITCH for t in t2)


def test_new_day_allows_trigger_again():
    te = TriggerEngine()
    te.tick(_now(24, 10), consecutive_sl=2, kill_switch=False, day_rollover=False)
    # New UTC day: roll over and re-arm.
    got = te.tick(_now(25, 0), consecutive_sl=2, kill_switch=False, day_rollover=True)
    kinds = [t.kind for t in got]
    assert ReviewTriggerKind.EOD in kinds
    assert ReviewTriggerKind.CONSECUTIVE_SL in kinds


def test_weekly_wrap_fires_on_configured_dow_rollover():
    # 2026-04-26 is a Sunday (weekday=6). The rollover INTO Monday
    # 2026-04-27 should produce a weekly trigger because the *previous*
    # day was Sunday.
    te = TriggerEngine(weekly_dow=6)
    got = te.tick(_now(27, 0), consecutive_sl=0, kill_switch=False, day_rollover=True)
    kinds = {t.kind for t in got}
    assert ReviewTriggerKind.WEEKLY in kinds


def test_packet_writes_markdown_and_json(tmp_path: Path):
    from ai_trader.review.triggers import ReviewTrigger

    trigger = ReviewTrigger(
        kind=ReviewTriggerKind.EOD, when=_now(24, 23), detail="end-of-day wrap"
    )
    ctx = ReviewContext(
        strategy_name="trend_pullback_fib",
        account_currency="JPY",
        balance=105_000.0,
        withdrawn_total=2_000.0,
        day="2026-04-24",
        day_starting_equity=100_000.0,
        day_realized_pnl=5_000.0,
        consecutive_sl=0,
        kill_switch=False,
        kill_reason="",
    )
    pkt = write_review_packet(trigger, ctx, artifacts_root=tmp_path)
    assert pkt.md_path.exists()
    assert pkt.json_path.exists()
    md = pkt.md_path.read_text()
    assert "Review" in md
    assert "JPY" in md
    assert "trend_pullback_fib" in md
    data = json.loads(pkt.json_path.read_text())
    assert data["trigger"]["kind"] == "eod"
    assert data["context"]["day_realized_pnl"] == 5_000.0


def test_quiet_day_packet_logs_silence(tmp_path: Path):
    from ai_trader.review.triggers import ReviewTrigger

    trigger = ReviewTrigger(
        kind=ReviewTriggerKind.EOD, when=_now(24, 23), detail="end-of-day wrap"
    )
    ctx = ReviewContext(
        strategy_name="trend_pullback_fib",
        account_currency="JPY",
        balance=100_000.0,
        withdrawn_total=0.0,
        day="2026-04-24",
        day_starting_equity=100_000.0,
        day_realized_pnl=0.0,
        consecutive_sl=0,
        kill_switch=False,
        kill_reason="",
    )
    pkt = write_review_packet(trigger, ctx, artifacts_root=tmp_path)
    md = pkt.md_path.read_text()
    assert "Quiet day" in md
    assert "silence is data" in md.lower() or "silence is data" in md
