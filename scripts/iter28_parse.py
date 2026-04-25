"""Parse quick_eval output into a clean per-window summary table."""
from __future__ import annotations
import argparse, re, subprocess, sys
from pathlib import Path

KEYS = ["return_pct", "profit_factor", "max_drawdown_pct", "trades", "cap_violations"]

def extract(block: str) -> dict:
    out = {}
    # april_return_pct etc would conflict; we want the standalone metric
    # listed before "april_return_pct". Use word-boundary anchors.
    pats = {
        "trades":           r'(?:^|\s)trades=(-?\d+)',
        "profit_factor":    r'(?:^|\s)profit_factor=(-?\d+\.?\d*)',
        "return_pct":       r'(?:^|\s)return_pct=(-?\d+\.?\d*)',
        "max_drawdown_pct": r'(?:^|\s)max_drawdown_pct=(-?\d+\.?\d*)',
        "cap_violations":   r'(?:^|\s)cap_violations=(\d+)',
    }
    for k, p in pats.items():
        m = re.search(p, block)
        out[k] = float(m.group(1)) if m else None
    return out

def parse_quick_eval(text: str) -> dict:
    sections = {}
    headers = re.split(r'^== (.+?) ==\s*$', text, flags=re.M)
    # headers = ['', 'FULL', '<block>', 'RESEARCH (...)', '<block>', ...]
    for i in range(1, len(headers), 2):
        name = headers[i].split()[0]  # FULL / RESEARCH / VALIDATION / TOURNAMENT
        block = headers[i + 1] if i + 1 < len(headers) else ""
        sections[name] = extract(block)
    return sections

def fmt(d):
    if d is None: return "  --"
    if d.get("return_pct") is None: return "  --"
    return f"{d['return_pct']:+7.2f}%/PF{d['profit_factor']:4.2f}/DD{d['max_drawdown_pct']:+7.2f}%/n{int(d['trades'] or 0):3d}/cap{int(d['cap_violations'] or 0)}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("configs", nargs="+")
    ap.add_argument("--csv", default="data/xauusd_m1_2026.csv")
    args = ap.parse_args()
    print(f"{'config':<50} | {'FULL':<40} | {'VALIDATION':<40} | {'TOURNAMENT 14d':<40}")
    print("-" * 175)
    for cfg in args.configs:
        try:
            res = subprocess.run(
                ["python3", "scripts/quick_eval.py", "--config", cfg, "--csv", args.csv],
                capture_output=True, text=True, timeout=120,
            )
            secs = parse_quick_eval(res.stdout)
            label = Path(cfg).stem
            print(f"{label:<50} | {fmt(secs.get('FULL')):<40} | {fmt(secs.get('VALIDATION')):<40} | {fmt(secs.get('TOURNAMENT')):<40}")
        except Exception as e:
            print(f"{cfg}: ERROR {e}")

if __name__ == "__main__":
    main()
