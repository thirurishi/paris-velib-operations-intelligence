"""Paris Vélib' Operations Intelligence: live risk map, forecasts and truck plan.

Run locally (from the repo root):
    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(APP))

import altair as alt  # noqa: E402
import deckmap  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402
import ui  # noqa: E402

from velib import analytics, collect, features, geo, optimise, predict, storage  # noqa: E402

st.set_page_config(page_title="Vélib' Operations", page_icon="🚲", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)

DEPOTS = {
    "Châtelet (central)": (48.8584, 2.3470),
    "Bercy (east)": (48.8396, 2.3826),
    "Montparnasse (south)": (48.8421, 2.3219),
    "Porte Maillot (west)": (48.8781, 2.2830),
}


# ---------------------------------------------------------------------------
# Data (cached)
# ---------------------------------------------------------------------------
@st.cache_resource
def get_models():
    return predict.load_models()


@st.cache_data(ttl=600, show_spinner=False)
def get_history() -> pd.DataFrame:
    return storage.read_recent_snapshots(days=8)


@st.cache_data(ttl=3600, show_spinner=False)
def get_stations_saved() -> pd.DataFrame:
    return analytics.latest_stations()


@st.cache_data(ttl=300, show_spinner=False)
def get_live() -> tuple[pd.DataFrame, pd.DataFrame] | None:
    try:
        snapshot, info, _ = collect.fetch_snapshot()
    except Exception as exc:  # noqa: BLE001 - shown as a short note instead
        st.session_state["live_error"] = str(exc)
        return None
    return snapshot, geo.add_area(info)


@st.cache_data(ttl=300, show_spinner=False)
def get_forecast(use_live: bool) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    history, stations, live = get_history(), get_stations_saved(), False
    if use_live and (fetched := get_live()) is not None:
        snapshot, stations = fetched
        history, live = pd.concat([history, snapshot], ignore_index=True), True
    return predict.predict_latest(history, stations, get_models()), history, live


@st.cache_data(ttl=300, show_spinner=False)
def get_plan(_forecast: pd.DataFrame, key: str, cfg_items: tuple) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    return optimise.plan(_forecast, optimise.Config(**dict(cfg_items)))


def paris_time(ts) -> str:
    return pd.Timestamp(ts).tz_convert("Europe/Paris").strftime("%H:%M")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Settings")
    use_live = st.toggle("Live data", value=True, help="Off: use the latest snapshot stored in the repository")
    if st.button("Refresh now", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.markdown("### Truck fleet")
    n_trucks = st.slider("Trucks", 1, 5, 3)
    capacity = st.slider("Bikes per truck", 10, 40, 20, step=2)
    start_load = st.slider("Bikes on board at start", 0, capacity, capacity // 2)
    shift = st.slider("Planning window (minutes)", 60, 180, 120, step=15)
    depot_name = st.selectbox("Depot", list(DEPOTS))
    st.caption(
        "Data: Vélib' Métropole open data (© Smovengo / Syndicat Autolib' Vélib' Métropole, ODbL). "
        "Weather: Open-Meteo.com. Map: © OpenStreetMap contributors, © CARTO. "
        "[Source code](https://github.com/thirurishi/paris-velib-operations-intelligence)"
    )

cfg_items = (
    ("n_trucks", n_trucks),
    ("truck_capacity", capacity),
    ("start_load", start_load),
    ("shift_minutes", shift),
    ("depot_lat", DEPOTS[depot_name][0]),
    ("depot_lon", DEPOTS[depot_name][1]),
    ("solver_seconds", 5),
)
cfg = optimise.Config(**dict(cfg_items))

# ---------------------------------------------------------------------------
# Header + key numbers
# ---------------------------------------------------------------------------
try:
    with st.spinner("Loading stations and forecasts"):
        forecast, history, live = get_forecast(use_live)
except FileNotFoundError:
    st.error("No collected data yet. Run `python -m velib.collect` once, then reload.")
    st.stop()

method = forecast["method"].iloc[0]
updated = paris_time(forecast["slot_ts"].max())
live_text = f"Live, updated {updated}" if live else f"Stored snapshot from {updated}"
st.markdown(
    ui.header(
        "Vélib' operations",
        "Which stations will run out of bikes or docks in the next two hours, and where the trucks should go.",
        live_text,
        live,
    ),
    unsafe_allow_html=True,
)
if use_live and not live:
    st.caption("The live feed did not answer, so the latest stored snapshot is shown.")

n_red_empty = int(((forecast["risk_level"] == "red") & (forecast["risk_type"] == "empty")).sum())
n_red_full = int(((forecast["risk_level"] == "red") & (forecast["risk_type"] == "full")).sum())
st.markdown(
    ui.kpi_strip(
        [
            {"value": len(forecast), "label": "stations reporting"},
            {"value": int((forecast["bikes_now"] == 0).sum()), "label": "empty right now"},
            {"value": int((forecast["docks_now"] == 0).sum()), "label": "full right now"},
            {"value": n_red_empty, "label": "likely to run out of bikes within 2 h", "tone": "red"},
            {"value": n_red_full, "label": "likely to fill up within 2 h", "tone": "blue"},
        ]
    ),
    unsafe_allow_html=True,
)

tab_map, tab_station, tab_plan, tab_insights, tab_about = st.tabs(
    ["Network", "Station", "Truck plan", "Insights", "About"]
)

# ---------------------------------------------------------------------------
# Network map
# ---------------------------------------------------------------------------
with tab_map:
    st.markdown(
        ui.legend(
            [
                (ui.RED, "Likely to run out of bikes"),
                (ui.BLUE, "Likely to fill up"),
                (ui.GREY, "Low risk"),
            ]
        ),
        unsafe_allow_html=True,
    )
    components.html(deckmap.network_map(forecast), height=600)
    note = (
        "Forecasts come from the LightGBM model trained on the collected history."
        if method == "lightgbm"
        else "The forecasting model trains after a few days of data. Until then, a simple rule flags stations "
        "with 0–2 bikes or docks left."
    )
    st.markdown(
        f'<p class="v-note">{ui.esc(note)} Stations with at least 50% risk pulse softly.</p>', unsafe_allow_html=True
    )

    st.markdown(ui.section("Stations to watch"), unsafe_allow_html=True)
    top = forecast.head(15).assign(
        problem=lambda d: d["risk_type"].map({"empty": "Running out of bikes", "full": "Filling up"})
    )
    st.dataframe(
        top[["name", "area", "bikes_now", "docks_now", "pred_bikes_2h", "problem", "risk"]],
        hide_index=True,
        width="stretch",
        column_config={
            "name": "Station",
            "area": "Area",
            "bikes_now": st.column_config.NumberColumn("Bikes", format="%d"),
            "docks_now": st.column_config.NumberColumn("Free docks", format="%d"),
            "pred_bikes_2h": st.column_config.NumberColumn("Bikes in 2 h", format="%.1f"),
            "problem": "Problem",
            "risk": st.column_config.ProgressColumn("Risk", min_value=0, max_value=1, format="percent"),
        },
    )

# ---------------------------------------------------------------------------
# One station
# ---------------------------------------------------------------------------
with tab_station:
    labels = forecast["name"].fillna("?") + " · " + forecast["area"].fillna("")
    choice = st.selectbox("Station, sorted by risk", labels.tolist(), label_visibility="collapsed")
    row = forecast.iloc[labels.tolist().index(choice)]
    st.markdown(
        ui.station_header(
            str(row["name"]),
            f"{row['area']}, station {row['station_code']}, {int(row['usable_capacity'])} usable docks",
            float(row["risk"]),
            str(row["risk_type"]),
        ),
        unsafe_allow_html=True,
    )
    st.markdown(
        ui.kpi_strip(
            [
                {"value": int(row["bikes_now"]), "label": f"bikes now ({int(row['ebikes_now'])} electric)"},
                {"value": f"{row['pred_bikes_1h']:.0f}", "label": "bikes in 1 hour"},
                {"value": f"{row['pred_bikes_2h']:.0f}", "label": "bikes in 2 hours"},
                {"value": int(row["docks_now"]), "label": "free docks now"},
            ]
        ),
        unsafe_allow_html=True,
    )

    now = forecast["slot_ts"].max()
    h = features.prepare(history)
    h = h[h["station_id"] == row["station_id"]]
    past = h[h["slot_ts"] >= now - pd.Timedelta(hours=24)].assign(series="Observed")
    week = h[(h["slot_ts"] >= now - pd.Timedelta(days=8)) & (h["slot_ts"] <= now - pd.Timedelta(days=7, hours=-2))]
    week = week.assign(slot_ts=week["slot_ts"] + pd.Timedelta(days=7), series="Same time last week")
    fc = pd.DataFrame(
        {
            "slot_ts": [now, now + pd.Timedelta(hours=1), now + pd.Timedelta(hours=2)],
            "bikes_total": [row["bikes_now"], row["pred_bikes_1h"], row["pred_bikes_2h"]],
            "series": "Forecast",
        }
    )
    df = pd.concat([past, week, fc])[["slot_ts", "bikes_total", "series"]]
    df["time"] = df["slot_ts"].dt.tz_convert("Europe/Paris").dt.tz_localize(None)
    order = ["Observed", "Forecast", "Same time last week"]
    base = alt.Chart(df).encode(
        x=alt.X("time:T", title=None, axis=alt.Axis(format="%a %H:%M", labelColor=ui.MUTED, grid=False)),
        y=alt.Y(
            "bikes_total:Q",
            title="Bikes",
            scale=alt.Scale(domainMin=0),
            axis=alt.Axis(labelColor=ui.MUTED, gridColor=ui.LINE),
        ),
        color=alt.Color(
            "series:N",
            scale=alt.Scale(domain=order, range=[ui.INK, ui.RED, ui.GREY]),
            legend=alt.Legend(orient="top", title=None),
        ),
        strokeDash=alt.StrokeDash(
            "series:N", scale=alt.Scale(domain=order, range=[[1, 0], [5, 3], [2, 3]]), legend=None
        ),
        tooltip=[
            "series",
            alt.Tooltip("time:T", format="%a %H:%M"),
            alt.Tooltip("bikes_total:Q", title="Bikes", format=".1f"),
        ],
    )
    capacity_rule = (
        alt.Chart(pd.DataFrame({"y": [row["usable_capacity"]]})).mark_rule(color=ui.LINE, strokeWidth=2).encode(y="y:Q")
    )
    chart = (
        (capacity_rule + base.mark_line(strokeWidth=2))
        .properties(height=320)
        .configure_view(stroke=None)
        .configure(font="Instrument Sans, Helvetica, Arial, sans-serif")
    )
    st.altair_chart(chart, width="stretch")
    st.markdown(
        '<p class="v-note">The light horizontal line is the station\'s usable capacity. '
        "History comes from the snapshots collected every 15 minutes.</p>",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Truck plan
# ---------------------------------------------------------------------------
with tab_plan:
    with st.spinner("Planning truck routes"):
        plan_df, imp, tasks = get_plan(forecast, str(forecast["slot_ts"].max()), cfg_items)
    st.markdown(
        ui.kpi_strip(
            [
                {"value": imp["at_risk_stations"], "label": "stations at risk"},
                {"value": imp["stations_served"], "label": "visited by the plan"},
                {
                    "value": f"{imp['problem_hours_prevented']:.1f}",
                    "label": "problem-hours avoided (expected)",
                    "sub": "h",
                },
                {"value": imp["bikes_moved"], "label": "bikes moved"},
            ]
        ),
        unsafe_allow_html=True,
    )
    if plan_df.empty:
        st.markdown('<p class="v-note">No station needs a visit right now.</p>', unsafe_allow_html=True)
    else:
        st.markdown(
            ui.legend(
                [
                    (ui.RED, "Drop bikes (station running out)"),
                    (ui.BLUE, "Pick up bikes (station full or spare bikes)"),
                    ("hollow", "Not visited yet"),
                ]
            ),
            unsafe_allow_html=True,
        )
        components.html(deckmap.dispatch_map(plan_df, forecast, cfg), height=560)
        st.markdown(ui.section("Stops"), unsafe_allow_html=True)
        st.dataframe(
            plan_df.drop(columns=["lat", "lon", "risk"]),
            hide_index=True,
            width="stretch",
            column_config={
                "truck": "Truck",
                "stop": "Stop",
                "arrive_min": st.column_config.NumberColumn("Arrives after", format="%d min"),
                "action": "Action",
                "bikes": "Bikes",
                "load_after": "On board after",
                "station_code": "Code",
                "name": "Station",
                "area": "Area",
                "reason": "Why",
            },
        )
        st.download_button("Download plan as CSV", plan_df.to_csv(index=False), "truck_plan.csv", "text/csv")

    with st.expander("Compare with simpler ways of dispatching"):
        if st.button("Run comparison (about 20 seconds)"):
            with st.spinner("Comparing"):
                st.dataframe(optimise.compare_strategies(tasks, cfg), hide_index=True, width="stretch")
        st.markdown(
            '<p class="v-note">Value of a stop = probability the station goes empty or full within the window × 1 hour. '
            "Random order visits the same risky stations without planning a route.</p>",
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------
with tab_insights:
    ins_path, res_path = ROOT / "reports" / "insights.md", ROOT / "reports" / "model_results.md"
    if ins_path.exists():
        st.markdown(ins_path.read_text(encoding="utf-8"))
    else:
        st.markdown(
            '<p class="v-note">Insights are written every night from the collected data. '
            "The first ones appear after one full day of collection.</p>",
            unsafe_allow_html=True,
        )
    if res_path.exists():
        st.divider()
        st.markdown(res_path.read_text(encoding="utf-8").split("## What drives the predictions")[0])
        images = sorted((ROOT / "docs" / "img").glob("shap_*.png"))
        for col, img in zip(st.columns(max(len(images), 1)), images, strict=False):
            col.image(str(img))

# ---------------------------------------------------------------------------
# About
# ---------------------------------------------------------------------------
with tab_about:
    st.markdown(
        """
#### The problem
At rush hour, Vélib' stations in residential areas run out of bikes while stations in business districts
fill up. Trucks move bikes between them, but there are only a few trucks.

#### How this works
1. Station data is collected every 15 minutes since November 2026 and checked for errors.
2. A LightGBM model forecasts bikes per station in 1 and 2 hours, and the risk of running empty or full.
3. Google OR-Tools plans the truck routes: which stations to visit, in which order, and how many bikes to move.

#### Limits
Vélib' publishes station counts, not trips, so demand is estimated from changes in counts. Truck travel uses
straight-line distance × 1.35 at 15 km/h, and the depot location is an assumption.

Built by **Thirumurugan Kumar** · [GitHub](https://github.com/thirurishi/paris-velib-operations-intelligence)
"""
    )
