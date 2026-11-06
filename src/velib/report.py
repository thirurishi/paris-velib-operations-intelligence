"""Write the latest real numbers into README.md and reports/cv_bullets.md.

The README contains blocks like:
    <!-- AUTO:STATUS -->  ...  <!-- /AUTO:STATUS -->
Everything between the markers is regenerated from reports/*.json and
models/meta.json, so the README never shows stale or made-up numbers.

Run (from the repo root):
    python -m velib.report
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
REPORTS = ROOT / "reports"


def _load(path: Path) -> dict | list | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _replace(text: str, block: str, content: str) -> str:
    pattern = re.compile(rf"(<!-- AUTO:{block} -->)(.*?)(<!-- /AUTO:{block} -->)", re.S)
    return pattern.sub(lambda m: f"{m.group(1)}\n{content.strip()}\n{m.group(3)}", text)


def status_block(ins: dict | None) -> str:
    if not ins:
        return "_Collection running. Numbers appear after the first nightly analytics run._"
    rows = ins["snapshots"] * ins["stations"]
    volume = f"{rows / 1e6:.1f} million" if rows >= 1e6 else f"{rows:,}"
    return (
        f"**{ins['days_collected']} days** collected ({ins['first_day'][:10]} → {ins['last_day'][:10]}) · "
        f"**{ins['snapshots']:,} snapshots** · ~**{volume}** station records · "
        f"average coverage **{ins['avg_coverage']:.0%}** of 15-minute slots · {ins['stations']:,} stations. "
        f"_Last refreshed {ins['generated_at'][:16].replace('T', ' ')} UTC._"
    )


def insights_block(ins: dict | None) -> str:
    if not ins:
        return "_Insights are generated automatically from the collected data (see `reports/insights.md`)._"
    md = (REPORTS / "insights.md").read_text(encoding="utf-8")
    bullets = [line for line in md.splitlines() if re.match(r"^\d\. ", line) or line.startswith(("> ⚠️", "> ⏳"))]
    return "\n".join(bullets) + "\n\nFull list of the worst stations: [`reports/insights.md`](reports/insights.md)."


def model_block(res: dict | None, meta: dict | None) -> str:
    if not res or not meta:
        return "_The model trains automatically once a few days of data are collected (weekly retraining)._"
    reg = {(r["horizon"], r["method"]): r for r in res["regression"]}
    lines = [
        f"Test period: {meta['test_start'][:10]} → {meta['test_end'][:10]} (time-based split).",
        "",
        "| Forecast | Persistence MAE | Seasonal MAE | **LightGBM MAE** | Improvement vs persistence |",
        "|---|---|---|---|---|",
    ]
    for h in ("1h", "2h"):
        p, s, m = (reg[(h, k)] for k in ("Persistence (same as now)", "Seasonal (same time last week)", "LightGBM"))
        lines.append(
            f"| Bikes in +{h} | {p['mae_bikes']:.2f} | {s['mae_bikes']:.2f} | **{m['mae_bikes']:.2f}** | {-m['vs_persistence']:.0%} |"
        )
    lines += [
        "",
        "| 2-hour alert | Rule (≤ 2 now) precision / recall | **LightGBM precision / recall** |",
        "|---|---|---|",
    ]
    al = {(a["event"], a["method"].split(":")[0]): a for a in res["alerts"]}
    for ev, label in (("empty_2h", "Station runs empty"), ("full_2h", "Station gets full")):
        r, m = al[(ev, "Rule")], al[(ev, "LightGBM")]
        lines.append(
            f"| {label} | {r['precision']:.2f} / {r['recall']:.2f} | **{m['precision']:.2f} / {m['recall']:.2f}** |"
        )
    charts = [c for c in meta.get("shap_charts", []) if (ROOT / c).exists()]
    if charts:
        lines += ["", "**What drives the predictions (SHAP):**", ""]
        lines += [f'<img src="{c}" width="48%">' for c in charts]
    return "\n".join(lines) + "\n\nDetails: [`reports/model_results.md`](reports/model_results.md)."


def cv_bullets(ins: dict | None, res: dict | None) -> str:
    out = ["# CV bullets (auto-filled with real numbers)", ""]
    if ins:
        rows = ins["snapshots"] * ins["stations"]
        out.append(
            f"- Built an automated pipeline collecting live data from {ins['stations']:,} Vélib' stations every "
            f"15 minutes ({rows / 1e6:.1f} million records over {ins['days_collected']} days) with Python, "
            "GitHub Actions, Parquet and DuckDB."
        )
        if ins.get("top20_share_of_empty_hours"):
            out.append(
                f"- Found that 20 stations cause {ins['top20_share_of_empty_hours']:.0%} of the network's "
                f"{ins['empty_station_hours_per_day']:,.0f} daily empty-station hours (SQL/DuckDB, Power BI)."
            )
    if res:
        m2 = next(r for r in res["regression"] if r["horizon"] == "2h" and r["method"] == "LightGBM")
        out.append(
            f"- Forecast station availability 2 hours ahead with LightGBM, reducing error by "
            f"{-m2['vs_persistence']:.0%} vs. a persistence baseline; explained drivers with SHAP."
        )
    out.append(
        "- Designed an OR-Tools truck rebalancing planner (vehicle routing with capacity and time limits), "
        "delivered as a live Streamlit app and a Power BI dashboard."
    )
    return "\n".join(out) + "\n"


def run() -> None:
    ins = _load(REPORTS / "insights.json")
    res = _load(REPORTS / "model_results.json")
    meta = _load(ROOT / "models" / "meta.json")
    text = README.read_text(encoding="utf-8")
    text = _replace(text, "STATUS", status_block(ins))
    text = _replace(text, "INSIGHTS", insights_block(ins))
    text = _replace(text, "MODEL", model_block(res, meta))
    README.write_text(text, encoding="utf-8")
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "cv_bullets.md").write_text(cv_bullets(ins, res), encoding="utf-8")
    print("README.md and reports/cv_bullets.md updated")


if __name__ == "__main__":
    run()
    sys.exit(0)
