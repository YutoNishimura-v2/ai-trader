# Serial research journal — iterations **103–200**

**Contract:** Each iteration is **one independent thesis** (not a grid point).
**Falsification:** vs `adaptive_dual_pivot_chop_moon_r8_tp9_rollwin.yaml` on
`data/xauusd_m1_2026.csv` via `scripts/iter32_compare_configs.py`.

**Legend:** `cap` = full-sample `cap_violations`; `W` = harness wins / 4;
`WS` = `worst_score`; `minMA` = min(Mar%, Apr%).

---

## Wave A (103–110) — “one alien member” first

| Iter | Config | cap | W | WS | minMA | Mar | Apr | full% | Verdict |
|------|--------|:---:|:--:|-----:|------:|----:|----:|------:|---------|
| 103 | `iter103_rollwin_vwap_sigma_tr.yaml` | 1 | 2 | 0.100 | -0.54 | -0.54 | 2.93 | 231.2 | FALSIFIED |
| 104 | `iter104_rollwin_fib_trend.yaml` | 1 | 1 | 2.236 | -15.51 | -15.51 | -4.45 | -33.3 | FALSIFIED |
| 105 | `iter105_rollwin_friday_first.yaml` | 1 | 2 | 2.329 | -1.53 | -1.53 | 3.68 | 231.7 | FALSIFIED |
| 106 | `iter106_rollwin_donchian_tr.yaml` | 2 | 1 | 16.33 | -1.09 | 13.92 | -1.09 | -11.8 | FALSIFIED |
| 107 | `iter107_rollwin_mtf_bos_tr.yaml` | 1 | 1 | 0.437 | -5.75 | -0.20 | -5.75 | 187.2 | FALSIFIED |
| 108 | `iter108_rollwin_keltner_tr.yaml` | 0 | 1 | 3.920 | -0.36 | -0.36 | 1.93 | 228.4 | FALSIFIED |
| 109 | `iter109_rollwin_ob_tr.yaml` | 1 | 2 | 2.944 | 6.59 | 21.73 | 6.59 | 278.1 | FALSIFIED (cap); strong Mar/Apr |
| 110 | `iter110_rollwin_atr_sq_tr.yaml` | 0 | 0 | -inf | -5.61 | -5.61 | 0.27 | 183.4 | FALSIFIED |

---

## Wave B (111–118) — structural / pivot-period variants

| Iter | Config | cap | W | WS | minMA | Mar | Apr | full% | Verdict |
|------|--------|:---:|:--:|-----:|------:|----:|----:|------:|---------|
| 111 | `iter111_rollwin_vwap_sigma_range.yaml` | 0 | 2 | 0.213 | -6.91 | -6.91 | -0.81 | 179.8 | FALSIFIED |
| 112 | `iter112_rollwin_chop_then_bb.yaml` | 0 | 2 | 1.172 | 5.64 | 25.49 | 5.64 | 349.1 | Mar/Apr ↑; **WS ~1.17** vs ~0.10 — not rollwin-safe |
| 113 | `iter113_rollwin_trend_pullback_scalp.yaml` | 7 | 0 | -inf | -6.21 | 13.18 | -6.21 | 39.3 | FALSIFIED |
| 114 | `iter114_rollwin_momentum_pullback_tr.yaml` | 2 | 0 | -inf | -35.07 | -35.07 | 8.87 | -61.0 | FALSIFIED |
| 115 | `iter115_rollwin_weekly_chop_daily_trend.yaml` | 0 | **4** | 2.046 | **29.36** | 29.36 | **50.82** | 204.4 | **4/4** + cap=0 + huge Mar/Apr; **WS ~2.05** — difficult-month track |
| 116 | `iter116_rollwin_4h_chop_daily_trend.yaml` | 1 | 0 | -inf | -18.74 | -18.74 | 1.73 | 10.2 | FALSIFIED |
| 117 | `iter117_rollwin_chop_overlap_only.yaml` | 1 | 1 | 10.26 | 12.95 | 13.00 | 12.95 | 145.8 | FALSIFIED |
| 118 | `iter118_rollwin_asian_breakout_tr_first.yaml` | 0 | 1 | 1.121 | 0.91 | 5.88 | 0.91 | 230.1 | FALSIFIED (1/4 harness) |

---

## Backlog (119–200)

One YAML per iteration when executed — see prior plan: session sweeps without ADX,
liquidity NY-only, BOS/fib scalpers, router partitions, risk ablations, etc.
Extend this file with new **Executed** tables per wave.
