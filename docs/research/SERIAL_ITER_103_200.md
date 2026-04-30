# Serial research journal — iterations **103–200**

**Contract:** Each iteration is **one independent thesis** (not a grid point).
**Falsification:** vs `adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml` on
`data/xauusd_m1_2026.csv` via `scripts/iter32_compare_configs.py`.

**Legend:** `cap` = full-sample `cap_violations`; `W` = harness wins / 4;
`WS` = `worst_score`; `minMA` = min(Mar%, Apr%).

---

## Wave A (103–110)

| Iter | Config | cap | W | WS | minMA | Mar | Apr | full% | Verdict |
|------|--------|:---:|:--:|-----:|------:|----:|----:|------:|---------|
| 103 | `iter103_rollwin_vwap_sigma_tr.yaml` | 1 | 2 | 0.100 | -0.54 | -0.54 | 2.93 | 231.2 | FALSIFIED |
| 104 | `iter104_rollwin_fib_trend.yaml` | 1 | 1 | 2.236 | -15.51 | -15.51 | -4.45 | -33.3 | FALSIFIED |
| 105 | `iter105_rollwin_friday_first.yaml` | 1 | 2 | 2.329 | -1.53 | -1.53 | 3.68 | 231.7 | FALSIFIED |
| 106 | `iter106_rollwin_donchian_tr.yaml` | 2 | 1 | 16.33 | -1.09 | 13.92 | -1.09 | -11.8 | FALSIFIED |
| 107 | `iter107_rollwin_mtf_bos_tr.yaml` | 1 | 1 | 0.437 | -5.75 | -0.20 | -5.75 | 187.2 | FALSIFIED |
| 108 | `iter108_rollwin_keltner_tr.yaml` | 0 | 1 | 3.920 | -0.36 | -0.36 | 1.93 | 228.4 | FALSIFIED |
| 109 | `iter109_rollwin_ob_tr.yaml` | 1 | 2 | 2.944 | 6.59 | 21.73 | 6.59 | 278.1 | FALSIFIED (cap) |
| 110 | `iter110_rollwin_atr_sq_tr.yaml` | 0 | 0 | -inf | -5.61 | -5.61 | 0.27 | 183.4 | FALSIFIED |

---

## Wave B (111–118)

| Iter | Config | cap | W | WS | minMA | Mar | Apr | full% | Verdict |
|------|--------|:---:|:--:|-----:|------:|----:|----:|------:|---------|
| 111 | `iter111_rollwin_vwap_sigma_range.yaml` | 0 | 2 | 0.213 | -6.91 | -6.91 | -0.81 | 179.8 | FALSIFIED |
| 112 | `iter112_rollwin_chop_then_bb.yaml` | 0 | 2 | 1.172 | 5.64 | 25.49 | 5.64 | 349.1 | Trade-off (WS vs rollwin) |
| 113 | `iter113_rollwin_trend_pullback_scalp.yaml` | 7 | 0 | -inf | -6.21 | 13.18 | -6.21 | 39.3 | FALSIFIED |
| 114 | `iter114_rollwin_momentum_pullback_tr.yaml` | 2 | 0 | -inf | -35.07 | -35.07 | 8.87 | -61.0 | FALSIFIED |
| 115 | `iter115_rollwin_weekly_chop_daily_trend.yaml` | 0 | **4** | 2.046 | **29.36** | 29.36 | **50.82** | 204.4 | **4/4** difficult-month track |
| 116 | `iter116_rollwin_4h_chop_daily_trend.yaml` | 1 | 0 | -inf | -18.74 | -18.74 | 1.73 | 10.2 | FALSIFIED |
| 117 | `iter117_rollwin_chop_overlap_only.yaml` | 1 | 1 | 10.26 | 12.95 | 13.00 | 12.95 | 145.8 | FALSIFIED |
| 118 | `iter118_rollwin_asian_breakout_tr_first.yaml` | 0 | 1 | 1.121 | 0.91 | 5.88 | 0.91 | 230.1 | FALSIFIED |

---

## Wave C (119–126)

| Iter | Config | cap | W | WS | minMA | Mar | Apr | full% | Verdict |
|------|--------|:---:|:--:|-----:|------:|----:|----:|------:|---------|
| 119 | `iter119_rollwin_sweep_no_adx.yaml` | 0 | 1 | 2.521 | 0.43 | 7.43 | 0.43 | 251.5 | FALSIFIED |
| 120 | `iter120_rollwin_liq_range_lonny.yaml` | 0 | 1 | 2.051 | -9.70 | -9.70 | 3.79 | 89.3 | FALSIFIED |
| 121 | `iter121_rollwin_bos_tr_first.yaml` | 1 | 1 | 2.774 | -12.76 | -11.96 | -12.76 | 128.6 | FALSIFIED |
| 122 | `iter122_rollwin_fib_scalp_tr_first.yaml` | 0 | 0 | -inf | -14.85 | -12.27 | -14.85 | 66.9 | FALSIFIED |
| 123 | `iter123_rollwin_donchian_tr_only_first.yaml` | 2 | 0 | -inf | -22.00 | -22.00 | -17.58 | 188.0 | FALSIFIED |
| 124 | `iter124_rollwin_squeeze_transition_first.yaml` | 0 | 0 | -inf | -16.29 | -16.29 | 6.45 | 239.0 | FALSIFIED |
| 125 | `iter125_rollwin_atr_sq_transition_first.yaml` | 0 | 0 | -inf | -16.14 | -16.14 | -9.74 | 72.6 | FALSIFIED |
| 126 | `iter126_rollwin_chop_weekdays_incl_fri.yaml` | 0 | 1 | 6.569 | -5.99 | -5.99 | 4.69 | 237.0 | FALSIFIED |

---

## Wave D (127–130)

| Iter | Config | cap | W | WS | minMA | Mar | Apr | full% | Verdict |
|------|--------|:---:|:--:|-----:|------:|----:|----:|------:|---------|
| 127 | `iter127_rollwin_trend_max1.yaml` | 1 | 0 | -inf | 1.78 | -11.09 | 1.78 | 189.5 | FALSIFIED |
| 128 | `iter128_rollwin_chop_cool60.yaml` | 0 | 2 | 0.101 | 1.28 | 0.83 | 1.28 | 232.7 | **NO-OP** (bit-identical vs rollwin) |
| 129 | `iter129_rollwin_chop_touch008.yaml` | 0 | 1 | 3.367 | -2.60 | 0.83 | -2.60 | 220.0 | FALSIFIED |
| 130 | `iter130_rollwin_trend_tp145.yaml` | 0 | 1 | 3.266 | -1.58 | -1.58 | 0.70 | 227.2 | FALSIFIED |

---

## Wave E (131–134)

| Iter | Config | cap | W | WS | minMA | Mar | Apr | full% | Verdict |
|------|--------|:---:|:--:|-----:|------:|----:|----:|------:|---------|
| 131 | `iter131_rollwin_router_adx1826.yaml` | 0 | 0 | -inf | 0.42 | -6.53 | 0.42 | 214.6 | FALSIFIED |
| 132 | `iter132_rollwin_router_adx2228.yaml` | 0 | 2 | 2.015 | 7.96 | 7.96 | 10.73 | 290.7 | Trade-off (Mar/Apr↑, WS vs rollwin) |
| 133 | `iter133_rollwin_risk75.yaml` | 0 | 2 | 1.134 | -0.25 | -0.25 | 3.97 | 234.0 | FALSIFIED |
| 134 | `iter134_rollwin_chop_block131415.yaml` | 0 | 1 | 4.161 | 3.38 | 3.38 | 4.42 | 152.8 | FALSIFIED |

---

## Backlog (135–200)

Continue with one YAML + one harness per iteration.
