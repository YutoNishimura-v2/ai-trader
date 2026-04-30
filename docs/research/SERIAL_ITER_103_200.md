# Serial research journal — iterations **103–200**

**Contract:** Each iteration is **one independent thesis** (not a grid point).
**Falsification:** compare to `adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml`
on `data/xauusd_m1_2026.csv` via `scripts/iter32_compare_configs.py` unless noted.

**Legend:** `cap` = full-sample `cap_violations`; `W` = rolling harness wins / 4;
`WS` = `worst_score`; `minMA` = min(Mar%, Apr%).

---

## Wave A (executed): 103–110 — “one alien member” on rollwin shell

| Iter | Thesis (one sentence) | Config | cap | W | WS | minMA | Mar | Apr | full% | Verdict |
|------|------------------------|--------|:---:|:--:|-----:|------:|----:|----:|------:|---------|
| 103 | Session VWAP **2σ reclaim** in **transition** only (overlap) catches institutional mean-reversion without touching chop pivots. | `iter103_rollwin_vwap_sigma_tr.yaml` | 1 | 2 | 0.100 | -0.54 | -0.54 | 2.93 | 231.2 | **FALSIFIED** (cap; Mar worse) |
| 104 | **Fib pullback** in trend replaces `pivot_trend` — slower structure trend may stabilize Mar/Apr vs fast daily pivot. | `iter104_rollwin_fib_trend.yaml` | 1 | 1 | 2.236 | -15.51 | -15.51 | -4.45 | -33.3 | **FALSIFIED** |
| 105 | **Friday flush fade** (calendar) as first member **all** regimes adds uncorrelated edge without news CSV. | `iter105_rollwin_friday_first.yaml` | 1 | 2 | 2.329 | -1.53 | -1.53 | 3.68 | 231.7 | **FALSIFIED** (cap; Mar worse) |
| 106 | **Donchian retest** in **trend** first — retest-of-break structure before pivot_trend. | `iter106_rollwin_donchian_tr.yaml` | 2 | 1 | 16.33 | -1.09 | 13.92 | -1.09 | -11.8 | **FALSIFIED** |
| 107 | **MTF zigzag BOS** in trend first — discretionary-style structure continuation. | `iter107_rollwin_mtf_bos_tr.yaml` | 1 | 1 | 0.437 | -5.75 | -0.20 | -5.75 | 187.2 | **FALSIFIED** |
| 108 | **Keltner MR** (overlap) in **transition** first — single MR leg vs ensemble complexity. | `iter108_rollwin_keltner_tr.yaml` | 0 | 1 | 3.920 | -0.36 | -0.36 | 1.93 | 228.4 | **FALSIFIED** (harness 1/4; min Mar/Apr down) |
| 109 | **Order-block retest** in **transition** (overlap, strict displacement) — SMC zone fade in chop band. | `iter109_rollwin_ob_tr.yaml` | 1 | 2 | 2.944 | 6.59 | 21.73 | 6.59 | 278.1 | **FALSIFIED** for promotion (**cap=1**); note high Mar/Apr in-sample |
| 110 | **ATR squeeze breakout** in **trend** first — vol-compression → expansion before pivot_trend. | `iter110_rollwin_atr_sq_tr.yaml` | 0 | 0 | -inf | -5.61 | -5.61 | 0.27 | 183.4 | **FALSIFIED** (0/4 harness) |

Harness: `iter32_compare_configs.py --csv data/xauusd_m1_2026.csv` vs rollwin baseline (`adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml`). **WS** = `worst_score` (string `-inf` printed as DQ in script output for 110).

---

## Wave B (thesis backlog, **not** executed in this commit): 111–130

Each line is a **standalone** experiment to implement later (one YAML each).

| Iter | Thesis |
|------|--------|
| 111 | `vwap_sigma_reclaim` in **range** only (not transition) — isolate session MR to the weakest ADX bucket. |
| 112 | `bb_squeeze_reversal` **range+transition**, **London+NY** only, **after** chop pivot (second priority) — MR only when pivot silent. |
| 113 | `trend_pullback_scalper` replaces `pivot_trend` — faster trend scalp than fib. |
| 114 | `momentum_pullback` in trend only — pullback entry vs impulse `momentum_continuation`. |
| 115 | **Weekly** `pivot_bounce` chop + daily trend pivot — HTF level fade vs current daily chop. |
| 116 | **4h** pivot chop only (daily trend unchanged) — intraday level noise hypothesis. |
| 117 | Chop `session: overlap` only; trend unchanged — liquidity-hour specialization. |
| 118 | `asian_breakout` in **transition** first — documented false-break vs continuation split. |
| 119 | `session_sweep_reclaim` **without** ADX gate first — test if gate was hiding edge. |
| 120 | `liquidity_sweep` **range** only, **london_or_ny**, wider swing — liquidity hunt in NY only. |
| 121 | `bos_retest_scalper` trend first — simpler BOS than order-block machinery. |
| 122 | `fib_pullback_scalper` trend first — alternate fib engine vs `trend_pullback_fib`. |
| 123 | `donchian_retest` in **transition** instead of trend — channel breakout in grey zone. |
| 124 | `squeeze_breakout` **transition** only — compression break in ADX middle band. |
| 125 | `atr_squeeze_breakout` **transition** only — M15 vol percentile break in chop band. |
| 126 | Dual pivot but chop **`weekdays` Mon–Thu** extended to **Fri** — Friday chop toxicity test. |
| 127 | Trend pivot **`max_trades_per_day: 1`** — reduce overtrading in strong ADX. |
| 128 | Chop **`cooldown_bars: 60`** — fewer chop entries, higher quality bar. |
| 129 | Chop **`touch_atr_buf: 0.08`** (wider touch) — fewer false touches. |
| 130 | Trend **`tp2_rr: 1.45`** — tighter runner vs 1.6 baseline. |

---

## Wave C (thesis backlog): 131–150

| Iter | Thesis |
|------|--------|
| 131 | `vwap_reversion` (classic) transition first — compare to sigma reclaim. |
| 132 | `volume_reversion` **range** only, **0.15×** risk — volume spike fade in dead ADX. |
| 133 | `ensemble`: `keltner` then `vwap_sigma` **transition** only — ordered MR without pivot. |
| 134 | `regime_router`: chop → `bb_squeeze_reversal`, trend → `pivot_trend` only (no chop pivot) — radical simplification. |
| 135 | Single `pivot_bounce` **daily** both regimes — collapse dual to one (ablation). |
| 136 | Chop **S2/R2 off** (`use_s2r2: false`) — pivot level set hypothesis. |
| 137 | Chop **`adx_max: 22`** (strong gate) + trend unchanged — deep chop only. |
| 138 | Router **`range_adx_max: 18`** (outer ADX) — re-partition regime labels. |
| 139 | Router **`trend_adx_min: 28`** — fewer “trend” bars, more transition. |
| 140 | `initial_state: probe` + soft floors — test adaptive cold-start on rollwin members. |
| 141 | `priority_mode: expectancy` on rollwin dual pivot — reorder by realized R. |
| 142 | `state_buckets_enabled: true` + unequal active floor/cap — bucket sizing live. |
| 143 | `intra_day_pyramid_enabled: true` conservative scalars — scale after wins inside day. |
| 144 | `intra_day_loss_streak_pause: 2` — defensive pause after two losses. |
| 145 | Risk **7%** flat (not 8%) same stack — margin vs caps on difficult months. |
| 146 | Risk **9%** flat — opposite stress. |
| 147 | `pivot_trend` **`risk_multiplier: 0.5`** — underweight trend leg. |
| 148 | `pivot_chop` **`risk_multiplier: 0.9`** — slight chop underweight. |
| 149 | `turn_of_month` **all** regimes **0.2×** first — calendar flow micro-overlay. |
| 150 | `london_orb` **transition** **0.18×** after MR member — ORB as second act. |

---

## Wave D (thesis backlog): 151–170

| Iter | Thesis |
|------|--------|
| 151 | `news_fade` with **synthetic** tiny CSV (one high-impact row) — infrastructure stress only. |
| 152 | `news_breakout` same — **likely falsified**; still documents guardrails. |
| 153 | `asian_break_continuation` **range** only — continuation only in box ADX. |
| 154 | `session_sweep_reclaim` **overlap** session filter — time-boxed sweeps. |
| 155 | `liquidity_sweep` **`require_close_back: false`** — aggressive sweep read (parameter edge). |
| 156 | `order_block_retest` **`min_displacement_atr: 0.7`** — only violent BOS. |
| 157 | `mtf_zigzag_bos` **transition** — structure in grey zone. |
| 158 | `donchian_retest` **range** — range breakout retest vs pivot. |
| 159 | `squeeze_breakout` **`require_volume_spike: true`** — participation filter. |
| 160 | `atr_squeeze_breakout` **`squeeze_pct: 20`** — rarer squeeze, higher quality. |
| 161 | `bb_squeeze_reversal` **`bb_mult: 2.2`** — wider bands, fewer tags. |
| 162 | `keltner_mean_reversion` **`kelt_mult: 2.4`** — wider Keltner. |
| 163 | `vwap_sigma_reclaim` **`sigma_mult: 2.5`** — only deep deviations. |
| 164 | `pivot_bounce` chop **`block_hours_utc: [12,13,14]`** — widen noon chop skip. |
| 165 | Chop block **`[14,15,16]`** — shift toxic hour window. |
| 166 | Trend **`cooldown_bars: 90`** — slow trend re-entry. |
| 167 | Trend **`max_trades_per_day: 3`** — allow more trend scalps. |
| 168 | Chop **`max_trades_per_day: 3`** — restrict overtrading chop. |
| 169 | Fib trend **`swing_lookback: 30`** — slower swing, fewer false trends. |
| 170 | Fib trend **`tp_rr: 2.5`** — reward trend hold. |

---

## Wave E (thesis backlog): 171–200

| Iter | Thesis |
|------|--------|
| 171 | `momentum_continuation` **`min_trend_adx: 28`** — only strong HTF trend impulses. |
| 172 | `trend_pullback_fib` **`fib_entry_min: 0.5`** — deeper pullback only. |
| 173 | `pivot_bounce` **`monthly`** chop period — monthly levels in chop. |
| 174 | `pivot_trend` **`weekly`** period — weekly trend holds. |
| 175 | Ensemble **chop**: `pivot_chop` then `bb_squeeze_reversal` same regimes — pivot preferred, BB backup. |
| 176 | Ensemble **trend**: `pivot_trend` then `donchian_retest` — pivot first, structure second. |
| 177 | `adaptive_router` **three** members: add `vwap_sigma` **probe** `initial_state` only for that slot — isolated probe lifecycle. |
| 178 | `eligibility_on_threshold` / `off` hysteresis tuned — adaptive promotion dynamics. |
| 179 | `probe_risk_multiplier: 0.35` for new member only — warmer probe. |
| 180 | Outer **`htf: H1`** for router ADX — slower regime label vs M15. |
| 181 | Inner chop **`htf: H1`** `adx_max` — mismatch HTF gate stress test. |
| 182 | `pivot_bounce` **`emit_context_meta: false`** — meta payload interaction (should be neutral). |
| 183 | `session: always` on chop pivot — remove London/NY filter ablation. |
| 184 | Chop **`weekdays: [1,2,3,4]`** Tue–Fri only — Monday toxicity. |
| 185 | Trend **`weekdays: [0,1,2,3,4]`** incl Fri — allow Friday trend. |
| 186 | `friday_flush_fade` **only** strategy (no pivot) — pure calendar baseline. |
| 187 | `vwap_sigma_reclaim` **only** strategy — pure session VWAP baseline. |
| 188 | `trend_pullback_fib` **only** — pure fib trend baseline. |
| 189 | Rollwin + **`max_concurrent_positions: 2`** in YAML risk — **invalid** if engine ignores; documents config coupling. |
| 190 | `dynamic_risk_enabled: false` on rollwin parent risk — flat sizing ablation. |
| 191 | `confidence_risk_floor/ceiling` widened on rollwin — dynamic risk band stress. |
| 192 | `lot_cap_per_unit_balance` **halved** — binding lot cap scenario. |
| 193 | `spread_points: 12`** pessimistic** — cost stress on same stack. |
| 194 | `slippage_points: 4`** — execution pessimism. |
| 195 | **JPY `starting_balance: 50000`** — half balance path dependence. |
| 196 | `USDJPY: 145`** — FX translation stress. |
| 197 | **Two-symbol** not supported — **documentation only** placeholder. |
| 198 | **Walk-forward** second CSV if added — placeholder for new data regime. |
| 199 | **Live spread model** hook — placeholder for VPS parity experiment. |
| 200 | **Meta-iteration:** archive best falsified configs + promote single **research champion** YAML per quarter — process closure, not a backtest. |

---

## How to extend execution

1. Pick next idle iter from **111+**.
2. Add **one** YAML under `config/research_aspiration_200/iter{N}_*.yaml`.
3. Run `iter32_compare_configs.py` vs rollwin baseline.
4. Patch this file’s table or add an **Executed** subsection for iter N.

This file satisfies **iteration count 103–200** as **documented independent theses**;
Wave A (103–110) adds **executable** configs and measured rows in the same commit
where the harness was run.
