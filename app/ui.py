"""Visual layer for the Streamlit app: theme CSS and small HTML building blocks.

Design: minimal and light. Colour is reserved for problems.
  paper #ffffff · ink #1c1c1c · muted #6b6b6b · line #e6e6e6
  red  #d7263d = station running out of bikes
  blue #1f6feb = station full (no free docks)
  healthy stations stay neutral grey
Type: Instrument Sans only. No shadows, gradients or glows.
Motion: only on the map (a small pulse on stations at high risk, the truck replay),
and it is switched off for people who ask their system for reduced motion.
"""

from __future__ import annotations

import html

PAPER, INK, MUTED, LINE, SOFT = "#ffffff", "#1c1c1c", "#6b6b6b", "#e6e6e6", "#f6f6f5"
RED, BLUE, GREY = "#d7263d", "#1f6feb", "#a3a3a3"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&display=swap');

html, body, .stApp, [data-testid="stSidebar"], button, input, textarea, select,
[data-testid="stMarkdownContainer"] {{
  font-family: 'Instrument Sans', system-ui, sans-serif;
}}
.stApp {{ background: {PAPER}; color: {INK}; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2.4rem; max-width: 1240px; }}
[data-testid="stSidebar"] {{ background: {SOFT}; border-right: 1px solid {LINE}; }}

/* ---- headline ---------------------------------------------------------- */
.v-head {{ display: flex; align-items: baseline; justify-content: space-between;
  gap: 1rem 2rem; flex-wrap: wrap; padding-bottom: 1.1rem; border-bottom: 1px solid {LINE};
  margin-bottom: 1.4rem; }}
.v-title {{ margin: 0; font-size: clamp(1.6rem, 2.6vw, 2.2rem); font-weight: 600;
  letter-spacing: -.02em; line-height: 1.1; color: {INK}; }}
.v-sub {{ margin: .35rem 0 0; color: {MUTED}; font-size: 1rem; }}
.v-live {{ color: {MUTED}; font-size: .92rem; white-space: nowrap; }}
.v-live i {{ display: inline-block; width: .5rem; height: .5rem; border-radius: 50%;
  background: #16a34a; margin-right: .45rem; vertical-align: .05rem; }}
.v-live.stale i {{ background: {GREY}; }}

/* ---- key numbers: a plain row, separated by space, not boxes ------------- */
.v-kpis {{ display: grid; grid-template-columns: repeat(var(--cols, 5), minmax(0, 1fr));
  gap: 1.5rem; margin: 0 0 1.8rem; }}
.v-kpi .num {{ font-size: clamp(1.6rem, 2.4vw, 2.2rem); font-weight: 600; letter-spacing: -.02em;
  font-variant-numeric: tabular-nums; line-height: 1.1; color: {INK}; }}
.v-kpi .lbl {{ margin-top: .25rem; font-size: .9rem; color: {MUTED}; line-height: 1.35; }}
.v-kpi.red .num {{ color: {RED}; }}
.v-kpi.blue .num {{ color: {BLUE}; }}
.v-kpi .sub {{ font-size: .55em; font-weight: 500; color: {MUTED}; margin-left: .2rem; }}

/* ---- tabs ----------------------------------------------------------------- */
.stTabs [data-baseweb="tab-list"] {{ gap: 1.8rem; border-bottom: 1px solid {LINE}; }}
.stTabs [data-baseweb="tab"] {{ padding: .6rem 0; font-weight: 500; color: {MUTED}; }}
.stTabs [aria-selected="true"] {{ color: {INK}; }}
.stTabs [data-baseweb="tab-highlight"] {{ background: {INK}; height: 2px; }}

/* ---- legend + notes ----------------------------------------------------- */
.v-legend {{ display: flex; flex-wrap: wrap; gap: .4rem 1.5rem; color: {MUTED}; font-size: .9rem;
  margin: .1rem 0 .9rem; }}
.v-legend b {{ display: inline-block; width: .65rem; height: .65rem; border-radius: 50%;
  margin-right: .45rem; vertical-align: -.02rem; }}
.v-legend .hollow {{ background: transparent; border: 1.5px solid {MUTED}; }}
.v-note {{ color: {MUTED}; font-size: .9rem; max-width: 72ch; line-height: 1.5; }}
.v-section {{ font-size: 1.15rem; font-weight: 600; margin: 1.8rem 0 .6rem; color: {INK}; }}

/* ---- station header -------------------------------------------------------- */
.v-station {{ margin: .8rem 0 1.4rem; }}
.v-station h3 {{ margin: 0; font-size: 1.5rem; font-weight: 600; letter-spacing: -.01em; color: {INK}; }}
.v-station p {{ margin: .3rem 0 0; color: {MUTED}; }}
.v-risk {{ display: flex; align-items: center; gap: .8rem; margin-top: .8rem; max-width: 420px; }}
.v-risk .track {{ flex: 1; height: 6px; background: {LINE}; border-radius: 3px; overflow: hidden; }}
.v-risk .fill {{ height: 100%; background: var(--c); border-radius: 3px; }}
.v-risk span {{ font-size: .92rem; color: {INK}; white-space: nowrap; }}

/* quieter Streamlit widgets */
[data-testid="stMetric"] {{ padding: 0; }}
.stDownloadButton button, .stButton button {{ border-radius: 8px; border: 1px solid {LINE}; }}

@media (max-width: 760px) {{
  .v-kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
}}
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{ animation: none !important; transition: none !important; }}
}}
</style>
"""


def esc(text) -> str:
    return html.escape(str(text))


def header(title: str, subtitle: str, live_text: str, live: bool) -> str:
    return (
        f'<div class="v-head"><div><h1 class="v-title">{esc(title)}</h1><p class="v-sub">{esc(subtitle)}</p></div>'
        f'<div class="v-live{"" if live else " stale"}"><i></i>{esc(live_text)}</div></div>'
    )


def kpi_strip(items: list[dict]) -> str:
    """items: {value, label, tone: ''|'red'|'blue', sub}"""
    cells = []
    for it in items:
        v = it["value"]
        num = f"{v:,}" if isinstance(v, int) else esc(v)
        sub = f'<span class="sub">{esc(it["sub"])}</span>' if it.get("sub") else ""
        cells.append(
            f'<div class="v-kpi {it.get("tone", "")}"><div class="num">{num}{sub}</div>'
            f'<div class="lbl">{esc(it["label"])}</div></div>'
        )
    return f'<div class="v-kpis" style="--cols:{len(items)}">{"".join(cells)}</div>'


def legend(entries: list[tuple[str, str]]) -> str:
    """entries: (css colour or 'hollow', text)"""
    parts = []
    for colour, text in entries:
        dot = '<b class="hollow"></b>' if colour == "hollow" else f'<b style="background:{colour}"></b>'
        parts.append(f"<span>{dot}{esc(text)}</span>")
    return f'<div class="v-legend">{"".join(parts)}</div>'


def section(title: str) -> str:
    return f'<div class="v-section">{esc(title)}</div>'


def station_header(name: str, meta: str, risk: float, risk_type: str) -> str:
    colour = RED if risk_type == "empty" else BLUE
    if risk < 0.2:
        colour = GREY
    text = "running out of bikes" if risk_type == "empty" else "filling up (no free docks)"
    return (
        f'<div class="v-station"><h3>{esc(name)}</h3><p>{esc(meta)}</p>'
        f'<div class="v-risk"><div class="track"><div class="fill" style="width:{risk * 100:.0f}%;--c:{colour}"></div></div>'
        f"<span><b>{risk:.0%}</b> risk of {text} within 2 h</span></div></div>"
    )
