# Wave Y — calendar simulation policy

**Date:** 2026-05-22  
**Data:** `data/xauusd_m1_2026_combined.csv` (Jan 1 – May 22, 2026)

## Monthly returns (%)

| Config | Jan | Feb | Mar | Apr | **May (full)** |
|--------|----:|----:|----:|----:|---------------:|
| iter244 | +64.8 | +36.9 | +5.6 | −5.1 | **−2.2** |
| iter268 | +42.9 | +17.8 | +10.7 | −4.6 | −0.6 |
| iter274 | +42.0 | +24.5 | +7.4 | −7.0 | −1.2 |
| **iter280** | +48.8 | +17.4 | +12.8 | −3.9 | **+1.7** |

**iter280** is the only headline config **positive on full May 2026** (through May 22).

Reproduce::

    python3 scripts/build_combined_m1_csv.py
    python3 scripts/wave_y_monthly_compare.py

## Resolver (paper / demo helper)

```bash
# Jan–Apr 2026 → iter244; May 2026+ → iter280
python3 scripts/wave_y_resolve_config.py --at 2026-04-15T12:00:00Z
python3 scripts/wave_y_resolve_config.py --at 2026-05-15T12:00:00Z --print-path
```

| Policy | May config |
|--------|------------|
| `positive_may` (default) | `wave_y_iter280_may_positive.yaml` |
| `least_loss_may` | `wave_y_iter268_may_sleeve.yaml` |

## Live caution

Calendar switching is **research-only** until:
1. More OOS months confirm May edge.
2. Demo run on Windows MT5 with `run_demo.py` + resolved config path.

Do **not** auto-switch live without explicit user approval.
