"""Research-oriented utilities (Iter30+).

This package hosts modules that exist purely to produce research
artifacts (not to run live trading). They MUST never be imported
from `ai_trader/live/` or `ai_trader/broker/`. The split is
deliberate: live code is the production path; `research/` is where
we build the harnesses that decide which configs deserve to be
promoted to live.
"""
