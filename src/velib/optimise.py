"""Phase 4: plan rebalancing truck routes from the 2-hour shortage forecast.

Idea
----
* Stations likely to run EMPTY need bikes dropped; stations likely to get FULL
  need bikes picked up. Healthy stations with lots of spare bikes can donate.
* Each stop has a value = expected problem-hours prevented in the next 2 hours
  (risk probability x 1 hour, a deliberately simple and transparent estimate).
* Google OR-Tools solves a vehicle routing problem: each truck has a capacity
  (bikes on board), a shift length, travel times between stations and a handling
  time per stop. Stops are optional: skipping one costs its value, so the solver
  serves the most valuable stops it can reach in time.
* The plan is compared with "no action", a greedy heuristic and visiting the
  same risky stations in a random order.

Run (from the repo root):
    python -m velib.optimise          # uses the latest forecast
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

from velib import geo

log = logging.getLogger("velib.optimise")


@dataclass
class Config:
    n_trucks: int = 3
    truck_capacity: int = 20  # bikes per truck
    start_load: int = 10  # bikes on board when leaving the depot
    shift_minutes: int = 120  # plan the next 2 hours
    speed_kmh: float = 15.0  # average urban truck speed
    road_factor: float = 1.35  # straight-line -> street distance
    stop_minutes: float = 4.0  # park + walk per stop
    minutes_per_bike: float = 0.5  # load / unload one bike
    risk_threshold: float = 0.3  # stations above this get a pickup/drop
    max_task_stops: int = 60  # keep the problem small enough to solve in seconds
    max_donor_stops: int = 25
    depot_lat: float = 48.8584  # default depot: Châtelet (central Paris)
    depot_lon: float = 2.3470
    solver_seconds: int = 10


# ---------------------------------------------------------------------------
# 1. Which stations need a visit, and how many bikes?
# ---------------------------------------------------------------------------
def build_tasks(forecast: pd.DataFrame, cfg: Config | None = None) -> pd.DataFrame:
    """Return one row per candidate stop: qty > 0 = pick up, qty < 0 = drop off."""
    cfg = cfg or Config()
    f = forecast.dropna(subset=["lat", "lon"]).copy()
    cap = f["usable_capacity"].clip(lower=1)
    pred = f["pred_bikes_2h"]

    drops = f[f["p_empty_2h"] >= cfg.risk_threshold].copy()
    want = np.ceil(0.4 * cap[drops.index] - pred[drops.index]).clip(lower=3)
    drops["qty"] = -np.minimum.reduce([want, drops["docks_now"], np.full(len(drops), cfg.truck_capacity)])
    drops["kind"] = "drop (empty risk)"
    drops["value"] = drops["p_empty_2h"]

    picks = f[f["p_full_2h"] >= cfg.risk_threshold].copy()
    want = np.ceil(pred[picks.index] - 0.6 * cap[picks.index]).clip(lower=3)
    picks["qty"] = np.minimum.reduce([want, picks["bikes_now"], np.full(len(picks), cfg.truck_capacity)])
    picks["kind"] = "pickup (full risk)"
    picks["value"] = picks["p_full_2h"]

    tasks = pd.concat([drops, picks]).query("qty != 0")
    tasks = tasks.sort_values("value", ascending=False).head(cfg.max_task_stops)

    # Donors: low-risk stations with many spare bikes, close to the drop-offs.
    spare = (f["bikes_now"] - np.ceil(0.6 * cap)).clip(lower=0)
    donors = f[(spare >= 5) & (f["risk"] < 0.1) & ~f["station_id"].isin(tasks["station_id"])].copy()
    if not donors.empty and (tasks["qty"] < 0).any():
        d = tasks[tasks["qty"] < 0]
        dist = geo.haversine_km(
            donors["lat"].to_numpy()[:, None],
            donors["lon"].to_numpy()[:, None],
            d["lat"].to_numpy()[None],
            d["lon"].to_numpy()[None],
        ).min(axis=1)
        donors = donors.assign(_dist=dist).nsmallest(cfg.max_donor_stops, "_dist").drop(columns="_dist")
        donors["qty"] = np.minimum(spare[donors.index], cfg.truck_capacity).astype(int)
        donors["kind"] = "pickup (donor)"
        donors["value"] = 0.02  # small: only worth it to feed drop-offs
        tasks = pd.concat([tasks, donors])
    tasks["qty"] = tasks["qty"].astype(int)
    return tasks.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 2. Travel times
# ---------------------------------------------------------------------------
def travel_minutes(lat: np.ndarray, lon: np.ndarray, cfg: Config) -> np.ndarray:
    km = geo.haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :]) * cfg.road_factor
    return km / cfg.speed_kmh * 60


def _nodes(tasks: pd.DataFrame, cfg: Config) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lat = np.concatenate([[cfg.depot_lat], tasks["lat"].to_numpy()])
    lon = np.concatenate([[cfg.depot_lon], tasks["lon"].to_numpy()])
    service = np.concatenate([[0], cfg.stop_minutes + cfg.minutes_per_bike * tasks["qty"].abs().to_numpy()])
    return travel_minutes(lat, lon, cfg), service, lat


# ---------------------------------------------------------------------------
# 3a. OR-Tools solver
# ---------------------------------------------------------------------------
def solve_ortools(tasks: pd.DataFrame, cfg: Config | None = None) -> list[list[int]]:
    """Return one list of task indices per truck, in visiting order."""
    cfg = cfg or Config()
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    if tasks.empty:
        return [[] for _ in range(cfg.n_trucks)]
    travel, service, _ = _nodes(tasks, cfg)
    scale = 10  # OR-Tools needs integers: work in tenths of a minute
    cost = np.round((travel + service[:, None]) * scale).astype(int)  # leaving i includes service at i
    demand = np.concatenate([[0], tasks["qty"].to_numpy()]).astype(int)

    manager = pywrapcp.RoutingIndexManager(len(cost), cfg.n_trucks, 0)
    routing = pywrapcp.RoutingModel(manager)

    def time_cb(i, j):
        return int(cost[manager.IndexToNode(i), manager.IndexToNode(j)])

    t_idx = routing.RegisterTransitCallback(time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(t_idx)
    routing.AddDimension(t_idx, 0, cfg.shift_minutes * scale, True, "time")

    # Load on board: pickups add bikes, drops remove them; always between 0 and capacity.
    d_idx = routing.RegisterUnaryTransitCallback(lambda i: int(demand[manager.IndexToNode(i)]))
    routing.AddDimension(d_idx, 0, cfg.truck_capacity, False, "load")
    load = routing.GetDimensionOrDie("load")
    for v in range(cfg.n_trucks):
        load.CumulVar(routing.Start(v)).SetValue(cfg.start_load)

    # Every stop is optional; skipping it costs its value (in the same unit as time).
    penalty = np.round(tasks["value"].to_numpy() * 60 * scale * 3).astype(int)  # 1 problem-hour ~ 3h of driving
    for k in range(len(tasks)):
        routing.AddDisjunction([manager.NodeToIndex(k + 1)], int(max(penalty[k], 1)))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = cfg.solver_seconds
    solution = routing.SolveWithParameters(params)
    if solution is None:
        log.warning("OR-Tools found no solution, falling back to greedy")
        return solve_greedy(tasks, cfg)

    routes = []
    for v in range(cfg.n_trucks):
        idx, route = routing.Start(v), []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node:
                route.append(node - 1)
            idx = solution.Value(routing.NextVar(idx))
        routes.append(route)
    return routes


# ---------------------------------------------------------------------------
# 3b. Simple baselines
# ---------------------------------------------------------------------------
def _simulate(order: list[int], tasks: pd.DataFrame, cfg: Config, travel, service, used: set) -> list[int]:
    """Follow a fixed order, skipping stops that break capacity or the shift length."""
    route, pos, t, load = [], 0, 0.0, cfg.start_load
    for k in order:
        if k in used:
            continue
        node, q = k + 1, int(tasks.at[k, "qty"])
        if not 0 <= load + q <= cfg.truck_capacity:
            continue
        arrive = t + travel[pos, node]
        if arrive + service[node] > cfg.shift_minutes:
            continue
        route.append(k)
        used.add(k)
        t, pos, load = arrive + service[node], node, load + q
    return route


def solve_greedy(tasks: pd.DataFrame, cfg: Config | None = None) -> list[list[int]]:
    """Each truck repeatedly goes to the feasible stop with the best value per minute."""
    cfg = cfg or Config()
    travel, service, _ = _nodes(tasks, cfg)
    used: set = set()
    routes = []
    for _ in range(cfg.n_trucks):
        route, pos, t, load = [], 0, 0.0, cfg.start_load
        while True:
            best, best_score = None, 0.0
            for k in range(len(tasks)):
                if k in used:
                    continue
                q, node = int(tasks.at[k, "qty"]), k + 1
                if not 0 <= load + q <= cfg.truck_capacity:
                    continue
                dt = travel[pos, node] + service[node]
                if t + dt > cfg.shift_minutes:
                    continue
                score = tasks.at[k, "value"] / max(dt, 1)
                if score > best_score:
                    best, best_score = k, score
            if best is None:
                break
            node = best + 1
            t += travel[pos, node] + service[node]
            pos, load = node, load + int(tasks.at[best, "qty"])
            route.append(best)
            used.add(best)
        routes.append(route)
    return routes


def solve_random(tasks: pd.DataFrame, cfg: Config | None = None, seed: int = 0) -> list[list[int]]:
    """Visit the risky stations (not donors) in a random order: the naive dispatcher."""
    cfg = cfg or Config()
    travel, service, _ = _nodes(tasks, cfg)
    risky = [k for k in range(len(tasks)) if tasks.at[k, "kind"] != "pickup (donor)"]
    rng = np.random.default_rng(seed)
    used: set = set()
    return [_simulate(list(rng.permutation(risky)), tasks, cfg, travel, service, used) for _ in range(cfg.n_trucks)]


# ---------------------------------------------------------------------------
# 4. Plan table + impact
# ---------------------------------------------------------------------------
def routes_to_plan(routes: list[list[int]], tasks: pd.DataFrame, cfg: Config | None = None) -> pd.DataFrame:
    cfg = cfg or Config()
    travel, service, _ = _nodes(tasks, cfg)
    rows = []
    for v, route in enumerate(routes, start=1):
        pos, t, load = 0, 0.0, cfg.start_load
        for stop, k in enumerate(route, start=1):
            node = k + 1
            t += travel[pos, node]
            q = int(tasks.at[k, "qty"])
            load += q
            rows.append(
                {
                    "truck": v,
                    "stop": stop,
                    "arrive_min": round(t),
                    "action": "pick up" if q > 0 else "drop off",
                    "bikes": abs(q),
                    "load_after": load,
                    "station_code": tasks.at[k, "station_code"],
                    "name": tasks.at[k, "name"],
                    "area": tasks.at[k, "area"] if "area" in tasks else None,
                    "reason": tasks.at[k, "kind"],
                    "risk": round(float(tasks.at[k, "value"]), 2),
                    "lat": tasks.at[k, "lat"],
                    "lon": tasks.at[k, "lon"],
                }
            )
            t += service[node]
            pos = node
    return pd.DataFrame(rows)


def impact(routes: list[list[int]], tasks: pd.DataFrame) -> dict:
    """Expected problem-hours prevented in the next 2h (risk x 1 hour per served at-risk stop)."""
    served = tasks.iloc[[k for r in routes for k in r]]
    risky = tasks[tasks["kind"] != "pickup (donor)"]
    risky_served = served[served["kind"] != "pickup (donor)"]
    return {
        "at_risk_stations": len(risky),
        "expected_problem_hours": round(float(risky["value"].sum()), 1),
        "stations_served": len(risky_served),
        "problem_hours_prevented": round(float(risky_served["value"].sum()), 1),
        "bikes_moved": int(served["qty"].abs().sum()),
    }


def compare_strategies(tasks: pd.DataFrame, cfg: Config | None = None) -> pd.DataFrame:
    cfg = cfg or Config()
    rows = [{"strategy": "No action", **impact([[]], tasks)}]
    rows.append(
        {
            "strategy": "Random order (avg of 20)",
            **_avg([impact(solve_random(tasks, cfg, s), tasks) for s in range(20)]),
        }
    )
    rows.append({"strategy": "Greedy (best value per minute)", **impact(solve_greedy(tasks, cfg), tasks)})
    rows.append({"strategy": "OR-Tools routing", **impact(solve_ortools(tasks, cfg), tasks)})
    return pd.DataFrame(rows)


def _avg(items: list[dict]) -> dict:
    return {k: round(float(np.mean([i[k] for i in items])), 1) for k in items[0]}


def plan(forecast: pd.DataFrame, cfg: Config | None = None) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Full pipeline: forecast -> tasks -> OR-Tools routes -> (plan table, impact, tasks)."""
    cfg = cfg or Config()
    tasks = build_tasks(forecast, cfg)
    routes = solve_ortools(tasks, cfg)
    return routes_to_plan(routes, tasks, cfg), impact(routes, tasks), tasks


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from velib import analytics, predict, storage

    history = storage.read_recent_snapshots(days=8)
    forecast = predict.predict_latest(history, analytics.latest_stations(), predict.load_models())
    cfg = Config()
    tasks = build_tasks(forecast, cfg)
    print(f"{len(tasks)} candidate stops ({(tasks['qty'] < 0).sum()} drops, {(tasks['qty'] > 0).sum()} pickups)\n")
    print(compare_strategies(tasks, cfg).to_string(index=False), "\n")
    p, imp, _ = plan(forecast, cfg)
    pd.set_option("display.width", 160)
    print(p.drop(columns=["lat", "lon"]).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
