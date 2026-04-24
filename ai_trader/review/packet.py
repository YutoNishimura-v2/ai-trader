"""Review-packet generator.

Given a trigger + a snapshot of the bot's state, write:

- ``artifacts/reviews/<ts>/review.md``: human-readable summary.
- ``artifacts/reviews/<ts>/review.json``: machine-readable dump
  (trade log, day ledger, positions, trigger details).

Plan v3: every trigger emits a packet; the daily-EOD packet is
mandatory, including quiet days (where we write "nothing unusual"
so silence is still logged).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .triggers import ReviewTrigger


@dataclass
class ReviewContext:
    """Snapshot passed to the packet generator."""

    strategy_name: str
    account_currency: str
    balance: float
    withdrawn_total: float
    day: str | None
    day_starting_equity: float
    day_realized_pnl: float
    consecutive_sl: int
    kill_switch: bool
    kill_reason: str
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    closed_trades_today: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewPacket:
    out_dir: Path
    md_path: Path
    json_path: Path


def write_review_packet(
    trigger: ReviewTrigger,
    ctx: ReviewContext,
    *,
    artifacts_root: Path | str = "artifacts/reviews",
) -> ReviewPacket:
    ts = trigger.when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(artifacts_root) / f"{ts}-{trigger.kind.value}"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "review.json"
    json_path.write_text(
        json.dumps(
            {
                "trigger": {
                    "kind": trigger.kind.value,
                    "when": trigger.when.isoformat(),
                    "detail": trigger.detail,
                },
                "context": asdict(ctx),
            },
            indent=2,
            default=str,
        )
    )

    md_path = out_dir / "review.md"
    md_path.write_text(_render_markdown(trigger, ctx))
    return ReviewPacket(out_dir=out_dir, md_path=md_path, json_path=json_path)


def _render_markdown(trigger: ReviewTrigger, ctx: ReviewContext) -> str:
    lines: list[str] = []
    lines.append(f"# Review — {trigger.kind.value} ({trigger.when.isoformat()})")
    lines.append("")
    lines.append(f"**Trigger:** `{trigger.kind.value}` — {trigger.detail}")
    lines.append(f"**Strategy:** `{ctx.strategy_name}`")
    lines.append("")

    lines.append("## Account snapshot")
    lines.append("")
    lines.append(f"- Currency: `{ctx.account_currency}`")
    lines.append(f"- Balance: `{ctx.balance:,.2f}`")
    lines.append(f"- Withdrawn to date: `{ctx.withdrawn_total:,.2f}`")
    lines.append("")

    lines.append("## Today")
    lines.append("")
    lines.append(f"- UTC day: `{ctx.day}`")
    lines.append(f"- Starting equity: `{ctx.day_starting_equity:,.2f}`")
    pnl_pct = (
        100.0 * ctx.day_realized_pnl / ctx.day_starting_equity
        if ctx.day_starting_equity > 0
        else 0.0
    )
    lines.append(f"- Realized P&L: `{ctx.day_realized_pnl:+,.2f}` ({pnl_pct:+.2f}%)")
    lines.append(f"- Closed trades today: `{len(ctx.closed_trades_today)}`")
    lines.append(f"- Open positions: `{len(ctx.open_positions)}`")
    lines.append(f"- Consecutive-SL streak: `{ctx.consecutive_sl}`")
    lines.append(f"- Kill-switch: `{ctx.kill_switch}` {ctx.kill_reason}")
    lines.append("")

    if not ctx.closed_trades_today and not ctx.open_positions:
        lines.append("## Notes")
        lines.append("")
        lines.append(
            "Quiet day — no trades, no triggers. Hypothesis held. "
            "Logging per plan v3 §A.10 (silence is data)."
        )
        lines.append("")
    else:
        if ctx.closed_trades_today:
            lines.append("## Closed trades today")
            lines.append("")
            lines.append("| time | side | lots | entry | exit | pnl | reason |")
            lines.append("|---|---|---|---|---|---|---|")
            for t in ctx.closed_trades_today:
                lines.append(
                    "| {time} | {side} | {lots} | {entry} | {exit} | {pnl:+.2f} | {reason} |".format(**t)
                )
            lines.append("")
        if ctx.open_positions:
            lines.append("## Open positions")
            lines.append("")
            lines.append("| id | side | lots | entry | sl | tp |")
            lines.append("|---|---|---|---|---|---|")
            for p in ctx.open_positions:
                lines.append(
                    "| {id} | {side} | {lots} | {entry} | {sl} | {tp} |".format(**p)
                )
            lines.append("")

    lines.append("## Proposed next action")
    lines.append("")
    if trigger.kind.value == "consecutive_sl":
        lines.append(
            "Pause in effect. Plan v3 §A.10 triggered on two consecutive SL-"
            "hit trades. Review the context above before resuming. Consider: "
            "regime shift, widened volatility, news event near entries."
        )
    elif trigger.kind.value == "kill_switch":
        lines.append(
            "Daily envelope hit. Pause in effect until next UTC day. No "
            "adjustment required if the envelope trigger was a profit "
            "(lucky day). Investigate if it was a loss."
        )
    elif trigger.kind.value == "eod":
        lines.append(
            "Routine end-of-day review. No action required unless something "
            "in the snapshot above looks unusual."
        )
    elif trigger.kind.value == "weekly":
        lines.append("Weekly wrap. Good moment to reassess hypotheses and tune the plan.")
    else:
        lines.append("Investigate the trigger above.")
    lines.append("")
    return "\n".join(lines)
