"""Paris Vélib' Operations Intelligence: live risk map, forecasts and truck plan.

Run locally (from the repo root):
    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import altair as alt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pydeck as pdk  # noqa: E402
import streamlit as st  # noqa: E402

from velib import analytics, collect, features, geo, optimise, predict, storage  # noqa: E402

st.set_page_config(page_title="Vélib' Operations Intelligence", page_icon="🚲", layout="wide")

COLORS = {"green": [46, 158, 91], "orange": [242, 142, 43], "red": [214, 39, 40]}
TRUCK_COLORS = [[31, 119, 180], [148, 103, 189], [23, 190, 207], [227, 119, 194], [140, 86, 75]]
DEPOTS = {
    "Châtelet (central)": (48.8584, 2.3470),
    "Bercy (east)": (48.8396, 2.3826),
    "Montparnasse (south)": (48.8421, 2.3219),
    "Porte Maillot (west)": (48.8781, 2.2830),
}


# ---------------------------------------------------------------------------
# Data loading (cached)
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
    except Exception as exc:  # noqa: BLE001 - show a friendly message instead
        st.session_state["live_error"] = str(exc)
        return None
    return snapshot, geo.add_area(info)


@st.cache_data(ttl=300, show_spinner=False)
def get_forecast(use_live: bool) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    history = get_history()
    stations = get_stations_saved()
    source = "latest collected snapshot"
    if use_live:
        live = get_live()
        if live is not None:
            snapshot, stations = live
            history = pd.concat([history, snapshot], ignore_index=True)
            source = "live Vélib' feed"
    forecast = predict.predict_latest(history, stations, get_models())
    return forecast, history, stations, source


def as_local(ts) -> str:
    return pd.Timestamp(ts).tz_convert("Europe/Paris").strftime("%a %d %b, %H:%M")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("🚲 Vélib' Ops")
use_live = st.sidebar.toggle("Use live data", value=True, help="Off = latest snapshot stored in the repo")
st.sidebar.subheader("Rebalancing settings")
cfg = optimise.Config(
    n_trucks=st.sidebar.slider("Trucks", 1, 5, 3),
    truck_capacity=st.sidebar.slider("Bikes per truck", 10, 40, 20, step=2),
    shift_minutes=st.sidebar.slider("Planning window (min)", 60, 180, 120, step=15),
    solver_seconds=5,
)
cfg.start_load = st.sidebar.slider("Bikes on board at start", 0, cfg.truck_capacity, cfg.truck_capacity // 2)
cfg.depot_lat, cfg.depot_lon = DEPOTS[st.sidebar.selectbox("Depot", list(DEPOTS))]
st.sidebar.caption(
    "Data: Vélib' Métropole open data (© Smovengo / Syndicat Autolib' Vélib' Métropole, ODbL) · "
    "Weather: Open-Meteo.com · [Source code](https://github.com/thirurishi/paris-velib-operations-intelligence)"
)

# ---------------------------------------------------------------------------
# Header + KPIs
# ---------------------------------------------------------------------------
try:
    with st.spinner("Loading stations and forecasts..."):
        forecast, history, stations, source = get_forecast(use_live)
except FileNotFoundError:
    st.error("No collected data found yet. Run `python -m velib.collect` first.")
    st.stop()

if use_live and source != "live Vélib' feed":
    st.warning("The live Vélib' feed is unreachable right now, so the latest stored snapshot is shown.")
    with st.expander("Technical details"):
        st.code(st.session_state.get("live_error", "unknown error"))

method = forecast["method"].iloc[0]
st.title("Paris Vélib' Operations Intelligence")
st.caption(
    f"Data: {source}, {as_local(forecast['slot_ts'].max())} (Paris time) · Forecast method: "
    + (
        "**LightGBM model**"
        if method == "lightgbm"
        else "**simple rule** (model not trained yet: needs a few days of data)"
    )
)

levels = forecast["risk_level"].value_counts()
c = st.columns(5)
c[0].metric("Stations", f"{len(forecast):,}")
c[1].metric("Empty now", int((forecast["bikes_now"] == 0).sum()))
c[2].metric("Full now", int((forecast["docks_now"] == 0).sum()))
c[3].metric("🔴 High risk (2h)", int(levels.get("red", 0)))
c[4].metric("🟠 Watch (2h)", int(levels.get("orange", 0)))

tab_map, tab_station, tab_plan, tab_insights, tab_about = st.tabs(
    ["🗺️ Risk map", "📈 Station forecast", "🚚 Rebalancing plan", "💡 Insights", "ℹ️ About"]
)

VIEW = pdk.ViewState(latitude=48.86, longitude=2.34, zoom=11.3, pitch=0)

# ---------------------------------------------------------------------------
# Tab 1: risk map
# ---------------------------------------------------------------------------
with tab_map:
    left, right = st.columns([3, 1])
    with right:
        show = st.multiselect("Show", ["red", "orange", "green"], default=["red", "orange", "green"])
        kind = st.radio("Risk type", ["all", "empty", "full"], horizontal=True)
        st.markdown(
            "🔴 ≥ 50% risk · 🟠 20–50% · 🟢 < 20%  \nRisk = chance of 0 bikes (empty) or 0 docks (full) within 2 hours."
        )
    m = forecast[forecast["risk_level"].isin(show)].copy()
    if kind != "all":
        m = m[(m["risk_type"] == kind) | (m["risk_level"] == "green")]
    m["color"] = m["risk_level"].map(COLORS)
    m["radius"] = np.where(m["risk_level"] == "green", 35, 60)
    m["risk_pct"] = (100 * m["risk"]).round(0).astype(int)
    m["pred2"] = m["pred_bikes_2h"].round(1)
    layer = pdk.Layer(
        "ScatterplotLayer",
        m,
        get_position=["lon", "lat"],
        get_fill_color="color",
        get_radius="radius",
        radius_min_pixels=2,
        radius_max_pixels=9,
        pickable=True,
        opacity=0.85,
    )
    tooltip = {
        "html": "<b>{name}</b> ({station_code})<br/>{area}<br/>Bikes now: {bikes_now} · Docks: {docks_now}<br/>"
        "Forecast in 2h: {pred2} bikes<br/>Risk: {risk_pct}% ({risk_type})",
        "style": {"fontSize": "12px"},
    }
    with left:
        st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=VIEW, tooltip=tooltip), height=560)
    st.subheader("Highest-risk stations")
    top = forecast.head(20)[
        [
            "station_code",
            "name",
            "area",
            "bikes_now",
            "docks_now",
            "pred_bikes_1h",
            "pred_bikes_2h",
            "risk_type",
            "risk",
        ]
    ]
    st.dataframe(
        top,
        hide_index=True,
        width="stretch",
        column_config={
            "risk": st.column_config.ProgressColumn("Risk (2h)", min_value=0, max_value=1, format="%.0f%%"),
            "pred_bikes_1h": st.column_config.NumberColumn("Bikes +1h", format="%.1f"),
            "pred_bikes_2h": st.column_config.NumberColumn("Bikes +2h", format="%.1f"),
        },
    )

# ---------------------------------------------------------------------------
# Tab 2: one station
# ---------------------------------------------------------------------------
with tab_station:
    labels = (
        forecast["name"].fillna("?")
        + " ("
        + forecast["station_code"].astype(str)
        + ", "
        + forecast["area"].fillna("")
        + ")"
    )
    choice = st.selectbox("Station (sorted by risk)", labels.tolist())
    row = forecast.iloc[labels.tolist().index(choice)]
    k = st.columns(4)
    k[0].metric("Bikes now", int(row["bikes_now"]), help=f"{int(row['ebikes_now'])} e-bikes")
    k[1].metric("Forecast +1h", f"{row['pred_bikes_1h']:.1f}", f"{row['pred_bikes_1h'] - row['bikes_now']:+.1f}")
    k[2].metric("Forecast +2h", f"{row['pred_bikes_2h']:.1f}", f"{row['pred_bikes_2h'] - row['bikes_now']:+.1f}")
    k[3].metric("Risk within 2h", f"{row['risk']:.0%}", row["risk_type"], delta_color="off")

    now = forecast["slot_ts"].max()
    h = features.prepare(history)
    h = h[h["station_id"] == row["station_id"]]
    past = h[h["slot_ts"] >= now - pd.Timedelta(hours=24)].assign(series="Observed")
    lastweek = h[
        (h["slot_ts"] >= now - pd.Timedelta(days=7, hours=24))
        & (h["slot_ts"] <= now - pd.Timedelta(days=7) + pd.Timedelta(hours=2))
    ]
    lastweek = lastweek.assign(slot_ts=lastweek["slot_ts"] + pd.Timedelta(days=7), series="Same time last week")
    fc = pd.DataFrame(
        {
            "slot_ts": [now, now + pd.Timedelta(hours=1), now + pd.Timedelta(hours=2)],
            "bikes_total": [row["bikes_now"], row["pred_bikes_1h"], row["pred_bikes_2h"]],
            "series": "Forecast",
        }
    )
    chart_df = pd.concat([past, lastweek, fc])[["slot_ts", "bikes_total", "series"]]
    chart_df["time"] = chart_df["slot_ts"].dt.tz_convert("Europe/Paris").dt.tz_localize(None)
    chart = (
        alt.Chart(chart_df)
        .mark_line(point=alt.OverlayMarkDef(size=18))
        .encode(
            x=alt.X("time:T", title="Paris time"),
            y=alt.Y("bikes_total:Q", title="Bikes available", scale=alt.Scale(domainMin=0)),
            color=alt.Color(
                "series:N",
                scale=alt.Scale(
                    domain=["Observed", "Forecast", "Same time last week"],
                    range=["#2a6f97", "#d62728", "#adb5bd"],
                ),
                legend=alt.Legend(orient="top", title=None),
            ),
            strokeDash=alt.condition(alt.datum.series == "Same time last week", alt.value([4, 3]), alt.value([0])),
            tooltip=[
                "series",
                alt.Tooltip("time:T", format="%a %H:%M"),
                alt.Tooltip("bikes_total:Q", format=".1f"),
            ],
        )
        .properties(height=360)
    )
    cap_rule = (
        alt.Chart(pd.DataFrame({"y": [row["usable_capacity"]]}))
        .mark_rule(color="#888", strokeDash=[2, 2])
        .encode(y="y:Q")
    )
    st.altair_chart(chart + cap_rule, width="stretch")
    st.caption(
        "Dotted grey line = usable capacity (bikes + free docks). Observed history comes from the 15-minute collector."
    )

# ---------------------------------------------------------------------------
# Tab 3: truck plan
# ---------------------------------------------------------------------------
with tab_plan:
    with st.spinner("Optimising truck routes with OR-Tools..."):
        plan_df, imp, tasks = optimise.plan(forecast, cfg)
    k = st.columns(4)
    k[0].metric("Stations at risk", imp["at_risk_stations"])
    k[1].metric("Served by the plan", imp["stations_served"])
    k[2].metric(
        "Problem-hours prevented",
        imp["problem_hours_prevented"],
        help="Expected hours of empty/full stations avoided in the next 2h",
    )
    k[3].metric("Bikes moved", imp["bikes_moved"])

    if plan_df.empty:
        st.info("No station needs a visit right now. 🎉")
    else:
        depot = pd.DataFrame({"lon": [cfg.depot_lon], "lat": [cfg.depot_lat]})
        paths = []
        for truck, g in plan_df.groupby("truck"):
            coords = [[cfg.depot_lon, cfg.depot_lat]] + g[["lon", "lat"]].values.tolist()
            paths.append(
                {
                    "truck": f"Truck {truck}",
                    "path": coords,
                    "color": TRUCK_COLORS[(truck - 1) % len(TRUCK_COLORS)],
                }
            )
        stops = plan_df.copy()
        stops["color"] = np.where(stops["action"] == "drop off", 1, 0)
        stops["color"] = stops["color"].map({1: COLORS["red"], 0: [31, 119, 180]})
        stops["label"] = stops["truck"].astype(str) + "." + stops["stop"].astype(str)
        layers = [
            pdk.Layer(
                "ScatterplotLayer",
                forecast,
                get_position=["lon", "lat"],
                get_fill_color=[180, 180, 180],
                get_radius=25,
                opacity=0.3,
            ),
            pdk.Layer("PathLayer", paths, get_path="path", get_color="color", width_min_pixels=3, pickable=True),
            pdk.Layer(
                "ScatterplotLayer",
                stops,
                get_position=["lon", "lat"],
                get_fill_color="color",
                get_radius=70,
                radius_min_pixels=5,
                pickable=True,
            ),
            pdk.Layer(
                "TextLayer",
                stops,
                get_position=["lon", "lat"],
                get_text="label",
                get_size=13,
                get_pixel_offset=[0, -16],
                get_color=[30, 30, 30],
            ),
            pdk.Layer(
                "ScatterplotLayer",
                depot,
                get_position=["lon", "lat"],
                get_fill_color=[0, 0, 0],
                get_radius=110,
                radius_min_pixels=7,
            ),
        ]
        tip = {
            "html": "<b>{truck}</b><br/>{name}<br/>{action} {bikes} bikes · arrive +{arrive_min} min",
            "style": {"fontSize": "12px"},
        }
        st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=VIEW, tooltip=tip), height=520)
        st.caption(
            "⚫ depot · 🔴 drop bikes (station about to run empty) · 🔵 pick up bikes (full or spare) · label = truck.stop"
        )
        st.dataframe(
            plan_df.drop(columns=["lat", "lon"]),
            hide_index=True,
            width="stretch",
            column_config={
                "arrive_min": st.column_config.NumberColumn("Arrive (+min)"),
                "load_after": "Load after",
            },
        )
        st.download_button("Download plan (CSV)", plan_df.to_csv(index=False), "rebalancing_plan.csv", "text/csv")

    with st.expander("Compare with simpler strategies"):
        if st.button("Run comparison (≈20 s)"):
            with st.spinner("Comparing..."):
                st.dataframe(optimise.compare_strategies(tasks, cfg), hide_index=True, width="stretch")
        st.caption(
            "Value of a stop = probability the station goes empty/full within 2h × 1 hour. "
            "'Random order' visits the same risky stations as a dispatcher without a plan would."
        )

# ---------------------------------------------------------------------------
# Tab 4: insights
# ---------------------------------------------------------------------------
with tab_insights:
    ins_path, res_path = ROOT / "reports" / "insights.md", ROOT / "reports" / "model_results.md"
    if ins_path.exists():
        st.markdown(ins_path.read_text(encoding="utf-8"))
    else:
        st.info("Insights appear after the first nightly analytics run.")
    if res_path.exists():
        st.divider()
        text = res_path.read_text(encoding="utf-8").split("## What drives the predictions")[0]
        st.markdown(text)
        imgs = sorted((ROOT / "docs" / "img").glob("shap_*.png"))
        cols = st.columns(max(len(imgs), 1))
        for col, img in zip(cols, imgs, strict=False):
            col.image(str(img))

# ---------------------------------------------------------------------------
# Tab 5: about
# ---------------------------------------------------------------------------
with tab_about:
    st.markdown(
        """
**The problem.** Vélib' stations run out of bikes in residential areas and fill up in business districts
at rush hour. Trucks move bikes between them, but there are only a few trucks.

**What this app does.**
1. Reads live station data (or the latest of the snapshots collected every 15 minutes since November 2026).
2. Forecasts bikes per station in 1 and 2 hours, and the risk of becoming empty or full (LightGBM).
3. Plans truck routes with Google OR-Tools: which stations to visit, in what order, and how many bikes to move.

**Limits.** Only station counts are public (no trip data), so demand is inferred from changes in counts.
Truck travel uses straight-line distance × 1.35 at 15 km/h; the depot location is an assumption.

Built by **Thirumurugan Kumar** · [GitHub](https://github.com/thirurishi/paris-velib-operations-intelligence)
"""
    )
