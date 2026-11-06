"""Animated deck.gl maps rendered as a Streamlit HTML component.

Two maps:
  network_map   every station; stations at risk are coloured (red = running out of
                bikes, blue = full) and the high-risk ones pulse softly. Hover for details.
  dispatch_map  the truck plan replayed: trucks drive their routes with light trails,
                stops light up when served. Play / pause and speed controls.

Libraries load from unpkg at pinned versions; basemap = CARTO Positron (light, no API key).
"""

from __future__ import annotations

import json

DECK_JS = "https://unpkg.com/deck.gl@9.0.38/dist.min.js"
MAPLIBRE_JS = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"
MAPLIBRE_CSS = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"
BASEMAP = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
VIEW = {"longitude": 2.345, "latitude": 48.862, "zoom": 11.4, "pitch": 0, "bearing": 0}

TRUCK_COLOURS = [[28, 28, 28], [15, 118, 110], [180, 83, 9], [100, 116, 139], [126, 34, 206]]


def _json(obj) -> str:
    return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")


_SHELL = """<!doctype html><html><head><meta charset="utf-8">
<link href="__MLCSS__" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  html,body{margin:0;height:100%;background:#fff;font-family:'Instrument Sans',system-ui,sans-serif;color:#1c1c1c}
  #map{position:absolute;inset:0;border-radius:10px;overflow:hidden;border:1px solid #e6e6e6}
  .hud{position:absolute;left:14px;bottom:14px;z-index:2;display:flex;gap:10px;align-items:center;
       background:#fff;border:1px solid #e6e6e6;border-radius:8px;padding:8px 12px;font-size:14px}
  .hud button{all:unset;cursor:pointer;width:32px;height:32px;border-radius:8px;display:grid;place-items:center;
       background:#1c1c1c;color:#fff;font-weight:700}
  .hud button:focus-visible{outline:2px solid #1f6feb;outline-offset:2px}
  .hud select{background:#fff;color:#1c1c1c;border:1px solid #e6e6e6;border-radius:6px;padding:4px;font:inherit}
  .clock{font-weight:600;font-variant-numeric:tabular-nums;min-width:118px}
  .event{color:#6b6b6b;max-width:46ch;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .bar{position:absolute;left:1px;right:1px;bottom:1px;height:2px;background:#e6e6e6;z-index:2;border-radius:0 0 10px 10px;overflow:hidden}
  .bar i{display:block;height:100%;width:0;background:#1c1c1c}
  .err{position:absolute;inset:0;display:none;place-items:center;color:#6b6b6b;font-size:15px}
</style>
<script src="__MLJS__"></script>
<script src="__DECKJS__"></script>
</head><body><div id="map"></div>__HUD__<div class="err" id="err">Map libraries could not load. Check your connection and reload.</div>
<script>
const REDUCE = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
if (typeof deck === 'undefined') { document.getElementById('err').style.display = 'grid'; }
const D = __DATA__;
__BODY__
</script></body></html>"""


def _render(data: dict, body: str, hud: str = "") -> str:
    return (
        _SHELL.replace("__MLCSS__", MAPLIBRE_CSS)
        .replace("__MLJS__", MAPLIBRE_JS)
        .replace("__DECKJS__", DECK_JS)
        .replace("__HUD__", hud)
        .replace("__DATA__", _json(data))
        .replace("__BODY__", body)
    )


_TOOLTIP_STYLE = (
    "{backgroundColor:'#fff',color:'#1c1c1c',border:'1px solid #e6e6e6',borderRadius:'8px',"
    "padding:'10px 12px',fontFamily:'Instrument Sans, sans-serif',fontSize:'13px',lineHeight:'1.45',maxWidth:'260px'}"
)

_NETWORK_JS = (
    """
const C = {empty:[215,38,61], full:[31,111,235], ok:[163,163,163]};
const ok = D.stations.filter(s => s.lvl === 'green');
const risky = D.stations.filter(s => s.lvl !== 'green');
const high = D.stations.filter(s => s.lvl === 'red');
const col = s => C[s.t] || C.ok;
const tip = ({object: s}) => s && {html:
  `<b>${s.name}</b><br><span style="color:#6b6b6b">${s.area} · ${s.code}</span><br>` +
  `${s.b} bikes · ${s.d} free docks<br>In 2 h: <b>${s.f2}</b> bikes<br>` +
  (s.lvl === 'green' ? 'Low risk' : `<span style="color:rgb(${col(s)})">${s.r}% risk of ${s.t === 'empty' ? 'running empty' : 'filling up'}</span>`),
  style: TOOLTIP};
function layers(t) {
  return [
    new deck.ScatterplotLayer({id:'ok', data: ok, getPosition: s => s.p, getFillColor: [...C.ok, 120],
      getRadius: 24, radiusMinPixels: 1.6, pickable: true}),
    new deck.ScatterplotLayer({id:'pulse', data: high, getPosition: s => s.p, filled: false, stroked: true,
      getLineColor: s => [...col(s), Math.round(150 * (1 - ((t / 2200 + s.o) % 1)))],
      getRadius: s => 45 + 180 * ((t / 2200 + s.o) % 1), lineWidthMinPixels: 1.2,
      updateTriggers: {getLineColor: t, getRadius: t}}),
    new deck.ScatterplotLayer({id:'risk', data: risky, getPosition: s => s.p,
      getFillColor: s => [...col(s), s.lvl === 'red' ? 235 : 150], getRadius: s => 32 + 0.6 * s.r,
      radiusMinPixels: 3, stroked: true, getLineColor: [255,255,255], lineWidthMinPixels: 1, pickable: true}),
  ];
}
if (typeof deck !== 'undefined') {
  const map = new deck.DeckGL({container: 'map', mapStyle: BASEMAP, initialViewState: VIEW,
    controller: true, getTooltip: tip, layers: layers(0)});
  if (!REDUCE) { const loop = t => { map.setProps({layers: layers(t)}); requestAnimationFrame(loop); };
    requestAnimationFrame(loop); }
}
""".replace("TOOLTIP", _TOOLTIP_STYLE)
    .replace("BASEMAP", json.dumps(BASEMAP))
    .replace("VIEW", json.dumps(VIEW))
)


def network_map(forecast) -> str:
    """forecast: DataFrame from predict.predict_latest."""
    f = forecast.dropna(subset=["lat", "lon"])
    stations = [
        {
            "p": [round(r.lon, 5), round(r.lat, 5)],
            "lvl": r.risk_level,
            "t": r.risk_type if r.risk_level != "green" else "ok",
            "r": int(round(100 * r.risk)),
            "name": str(r.name),
            "code": str(r.station_code),
            "area": str(r.area),
            "b": int(r.bikes_now),
            "d": int(r.docks_now),
            "f2": round(float(r.pred_bikes_2h), 1),
            "o": round((i * 0.618) % 1, 3),  # golden-ratio offsets so pulses don't sync
        }
        for i, r in enumerate(f.itertuples(index=False))
    ]
    return _render({"stations": stations}, _NETWORK_JS)


_DISPATCH_JS = (
    """
const C = {drop:[215,38,61], pick:[31,111,235]};
const DUR = D.duration, SPEEDS = {'1':4, '2':8, '4':16};   // plan minutes per real second
let speed = 8, playing = !REDUCE, tm = REDUCE ? DUR : 0, last = null;
function pos(trip, t) {            // where is this truck at minute t?
  const ts = trip.ts, p = trip.path;
  if (t <= ts[0]) return p[0];
  for (let i = 1; i < ts.length; i++) if (t <= ts[i]) {
    const k = (t - ts[i-1]) / Math.max(ts[i] - ts[i-1], 1e-6);
    return [p[i-1][0] + k * (p[i][0] - p[i-1][0]), p[i-1][1] + k * (p[i][1] - p[i-1][1])];
  }
  return p[p.length - 1];
}
const tip = ({object: s}) => s && s.name && {html:
  `<b>${s.label} · ${s.name}</b><br>${s.action} ${s.bikes} bikes at +${s.arrive} min<br>` +
  `<span style="color:#6b6b6b">${s.reason} · load after ${s.load}</span>`, style: TOOLTIP};
function layers() {
  const done = s => tm >= s.arrive;
  return [
    new deck.ScatterplotLayer({id:'bg', data: D.bg, getPosition: p => p, getFillColor: [163,163,163,70],
      getRadius: 20, radiusMinPixels: 1.2}),
    new deck.PathLayer({id:'plan', data: D.trips, getPath: d => d.path, getColor: d => [...d.color, 45],
      widthMinPixels: 2, jointRounded: true}),
    new deck.TripsLayer({id:'trails', data: D.trips, getPath: d => d.path, getTimestamps: d => d.ts,
      getColor: d => d.color, widthMinPixels: 4, trailLength: 22, currentTime: tm, fadeTrail: true,
      capRounded: true, jointRounded: true}),
    new deck.ScatterplotLayer({id:'stops', data: D.stops, getPosition: s => s.p, pickable: true,
      getFillColor: s => done(s) ? [...C[s.kind], 240] : [255,255,255,255], getRadius: 70,
      radiusMinPixels: 5, stroked: true, getLineColor: s => C[s.kind],
      lineWidthMinPixels: 1.6, updateTriggers: {getFillColor: tm, getRadius: tm, getLineColor: tm}}),
    new deck.TextLayer({id:'labels', data: D.stops, getPosition: s => s.p, getText: s => s.label,
      getSize: 12, getColor: [28,28,28], getPixelOffset: [0, -16], fontFamily: 'Instrument Sans, sans-serif',
      fontWeight: 600, outlineWidth: 4, outlineColor: [255,255,255], fontSettings: {sdf: true}}),
    new deck.ScatterplotLayer({id:'depot', data: [D.depot], getPosition: p => p, getFillColor: [255,255,255],
      getRadius: 110, radiusMinPixels: 7, stroked: true, getLineColor: [28,28,28], lineWidthMinPixels: 3}),
    new deck.ScatterplotLayer({id:'trucks', data: D.trips, getPosition: d => pos(d, tm),
      getFillColor: d => d.color, getRadius: 95, radiusMinPixels: 7, stroked: true,
      getLineColor: [255,255,255], lineWidthMinPixels: 2.5, updateTriggers: {getPosition: tm}}),
  ];
}
const clock = document.getElementById('clock'), ev = document.getElementById('event'),
      bar = document.getElementById('bar'), btn = document.getElementById('play');
function hud() {
  const m = Math.min(tm, DUR);
  clock.textContent = `+${Math.floor(m)} min of ${DUR}`;
  const latest = D.stops.filter(s => s.arrive <= m).sort((a, b) => b.arrive - a.arrive)[0];
  ev.textContent = latest ? `Truck ${latest.truck}: ${latest.action} ${latest.bikes} bikes at ${latest.name}`
                          : 'Trucks leave the depot';
  bar.style.width = (100 * m / DUR) + '%';
  btn.textContent = playing ? '❚❚' : '▶';
  btn.setAttribute('aria-label', playing ? 'Pause replay' : 'Play replay');
}
if (typeof deck !== 'undefined') {
  const map = new deck.DeckGL({container: 'map', mapStyle: BASEMAP, initialViewState: VIEW,
    controller: true, getTooltip: tip, layers: layers()});
  btn.onclick = () => { if (!playing && tm >= DUR) tm = 0; playing = !playing; last = null; hud(); };
  document.getElementById('speed').onchange = e => speed = SPEEDS[e.target.value];
  const loop = now => {
    if (playing) {
      if (last !== null) tm += (now - last) / 1000 * speed;
      last = now;
      if (tm > DUR + 6) tm = 0;                 // short pause on the final state, then loop
      map.setProps({layers: layers()});
      hud();
    } else { last = null; }
    requestAnimationFrame(loop);
  };
  hud(); map.setProps({layers: layers()});
  requestAnimationFrame(loop);
}
""".replace("TOOLTIP", _TOOLTIP_STYLE)
    .replace("BASEMAP", json.dumps(BASEMAP))
    .replace("VIEW", json.dumps(VIEW))
)

_DISPATCH_HUD = """<div class="hud"><button id="play" aria-label="Play replay">▶</button>
<span class="clock" id="clock">+0 min</span>
<label>Speed <select id="speed" aria-label="Replay speed"><option value="1">1×</option>
<option value="2" selected>2×</option><option value="4">4×</option></select></label>
<span class="event" id="event"></span></div><div class="bar"><i id="bar"></i></div>"""


def dispatch_map(plan, forecast, cfg) -> str:
    """plan: DataFrame from optimise.plan; cfg: optimise.Config."""
    depot = [cfg.depot_lon, cfg.depot_lat]
    trips, stops = [], []
    for truck, g in plan.groupby("truck"):
        path, ts = [depot], [0.0]
        for r in g.itertuples(index=False):
            service = cfg.stop_minutes + cfg.minutes_per_bike * r.bikes
            path += [[r.lon, r.lat], [r.lon, r.lat]]  # arrive, then wait while loading
            ts += [float(r.arrive_min), float(r.arrive_min) + service]
            stops.append(
                {
                    "p": [r.lon, r.lat],
                    "truck": int(truck),
                    "label": f"{truck}.{r.stop}",
                    "arrive": int(r.arrive_min),
                    "kind": "drop" if r.action == "drop off" else "pick",
                    "action": "Drop" if r.action == "drop off" else "Pick up",
                    "bikes": int(r.bikes),
                    "load": int(r.load_after),
                    "name": str(r.name),
                    "reason": str(r.reason),
                }
            )
        trips.append({"path": path, "ts": ts, "color": TRUCK_COLOURS[(int(truck) - 1) % len(TRUCK_COLOURS)]})
    bg = forecast.dropna(subset=["lat", "lon"])[["lon", "lat"]].round(5).values.tolist()
    data = {"trips": trips, "stops": stops, "depot": depot, "bg": bg, "duration": cfg.shift_minutes}
    return _render(data, _DISPATCH_JS, _DISPATCH_HUD)
