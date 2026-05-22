#!/usr/bin/env python3
from pathlib import Path

for cap, name in [(0.75, "iter271_wavez_cap075.yaml"), (0.80, "iter272_wavez_cap080.yaml")]:
    src = Path("config/research_aspiration_200/iter268_wavez_cap070.yaml").read_text()
    text = src.replace("active_risk_multiplier_cap: 0.70", f"active_risk_multiplier_cap: {cap}")
    text = text.replace(
        "# Iter268 — cap 0.70",
        f"# Iter{271 if cap == 0.75 else 272} — cap {cap}",
        1,
    )
    text = text.replace("iter268_wavez_cap070.yaml", name.replace(".yaml", ""), 1)
    p = Path("config/research_aspiration_200") / name
    p.write_text(text.replace("extends: iter244", f"# cap peel\nextends: iter244"))
    # fix extends line
    lines = p.read_text().splitlines()
    if lines[1].startswith("# cap"):
        lines[1] = "extends: iter244_handoff_overlap_lunchblock.yaml"
    lines[0] = f"# Iter{271 if cap == 0.75 else 272} — Wave Z cap **{cap}** peel."
    p.write_text("\n".join(lines) + "\n")
    print(p)
