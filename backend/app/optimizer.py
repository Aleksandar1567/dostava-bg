"""Single-courier route optimization with time windows (VRPTW) on OR-Tools.

Model
  node 0            where the courier starts (depot, GPS position or the stop
                    it is currently driving to)
  nodes 1..n        stops (one per building / window group)
  end node          depot when the courier returns, otherwise a dummy node
                    with zero travel cost (open route)

  transit(i, j)     service(i) + travel(i, j) * traffic_factor(departure from i)
  window earliest   hard: courier waits outside if early
  window latest     soft: allowed but expensive, reported as "late"
  shift end         soft: overtime allowed but expensive
  every stop        optional with a huge penalty: dropped only if nothing fits
                    before the hard horizon

Travel time depends on the time of day. OR-Tools wants a static matrix, so we
solve twice: pass 1 uses the factor at the start time, pass 2 uses the factor
at each node's departure time from pass 1, warm-started from pass 1.
"""

import time
from dataclasses import dataclass, field
from typing import Callable

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

LATE_PENALTY = 30          # cost per second a customer window (or our promise) is missed
OVERTIME_PENALTY = 4       # cost per second after the shift end
DROP_PENALTY = 50_000_000  # cost of not visiting a stop at all
# Insertion copes with optional nodes + time windows far better than PATH_CHEAPEST_ARC,
# which tends to build one long greedy path and leave stops out.
FIRST_SOLUTION = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION


@dataclass
class Problem:
    dur: list                     # free-flow seconds, dur[i][j]
    service: list                 # seconds spent at node i
    earliest: list                # seconds from t0 or None
    latest: list                  # seconds from t0 or None
    factor_at: Callable[[float], float]   # seconds from t0 -> slowdown factor
    shift_end: int                # seconds from t0
    horizon: int                  # hard limit, seconds from t0 (keep it generous:
                                  # a tight one makes the solver drop stops)
    end_node: int | None = None   # index of the return depot, None = open route
    time_limit_s: float = 5.0


@dataclass
class Solution:
    order: list                   # stop nodes in visiting order
    dropped: list = field(default_factory=list)
    objective: int = 0
    solve_ms: int = 0


def timeline(order, dur, service, earliest, factor_at, start_node=0):
    """Arrival/departure for each node of `order`, seconds from t0."""
    out, t, prev = [], 0.0, start_node
    for node in order:
        f = factor_at(t)
        travel = dur[prev][node] * f
        arrival = t + travel
        begin = max(arrival, earliest[node] or 0)
        depart = begin + service[node]
        out.append({"node": node, "travel": travel, "arrival": arrival, "begin": begin,
                    "depart": depart, "factor": f})
        t, prev = depart, node
    return out


def _solve_once(p: Problem, factors: list, initial: list | None, time_limit: float):
    n = len(p.dur)
    stop_nodes = [i for i in range(1, n) if i != p.end_node]
    if p.end_node is None:
        end = n                      # dummy node
        size = n + 1
    else:
        end = p.end_node
        size = n

    def travel(i, j):
        if i >= n or j >= n:       # dummy end
            return 0
        return int(round(p.dur[i][j] * factors[i]))

    manager = pywrapcp.RoutingIndexManager(size, 1, [0], [end])
    routing = pywrapcp.RoutingModel(manager)

    def travel_cb(a, b):
        return travel(manager.IndexToNode(a), manager.IndexToNode(b))

    def transit_cb(a, b):
        i, j = manager.IndexToNode(a), manager.IndexToNode(b)
        s = p.service[i] if i < n else 0
        return s + travel(i, j)

    t_idx = routing.RegisterTransitCallback(travel_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(t_idx)
    tr_idx = routing.RegisterTransitCallback(transit_cb)
    routing.AddDimension(tr_idx, p.horizon, p.horizon, True, "Time")
    td = routing.GetDimensionOrDie("Time")
    td.SetSpanCostCoefficientForAllVehicles(1)

    for node in stop_nodes:
        idx = manager.NodeToIndex(node)
        e, l = p.earliest[node], p.latest[node]
        if e is not None and e > 0:
            td.CumulVar(idx).SetMin(min(int(e), p.horizon))
        if l is not None:
            td.SetCumulVarSoftUpperBound(idx, max(0, int(l)), LATE_PENALTY)
        routing.AddDisjunction([idx], DROP_PENALTY)
    td.SetCumulVarSoftUpperBound(routing.End(0), max(0, int(p.shift_end)), OVERTIME_PENALTY)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = FIRST_SOLUTION
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    params.time_limit.FromMilliseconds(int(time_limit * 1000))

    if initial:
        routing.CloseModelWithParameters(params)
        init = routing.ReadAssignmentFromRoutes(
            [[manager.NodeToIndex(x) for x in initial]], True)
        sol = routing.SolveFromAssignmentWithParameters(init, params) if init else None
        if sol is None:
            sol = routing.SolveWithParameters(params)
    else:
        sol = routing.SolveWithParameters(params)
    if sol is None:
        return None

    order, idx = [], routing.Start(0)
    idx = sol.Value(routing.NextVar(idx))
    while not routing.IsEnd(idx):
        order.append(manager.IndexToNode(idx))
        idx = sol.Value(routing.NextVar(idx))
    dropped = [x for x in stop_nodes if x not in set(order)]
    return Solution(order, dropped, sol.ObjectiveValue())


def solve(p: Problem) -> Solution:
    started = time.monotonic()
    n = len(p.dur)
    stop_nodes = [i for i in range(1, n) if i != p.end_node]
    if len(stop_nodes) <= 1:
        return Solution(stop_nodes, [], 0, 0)

    budget = p.time_limit_s if len(stop_nodes) > 12 else min(1.0, p.time_limit_s)
    f0 = p.factor_at(0)
    first = _solve_once(p, [f0] * n, None, budget * 0.4)
    if first is None:
        return Solution([], stop_nodes, 0, int((time.monotonic() - started) * 1000))

    # pass 2: factor at each node's planned departure time
    factors = [f0] * n
    for row in timeline(first.order, p.dur, p.service, p.earliest, p.factor_at):
        factors[row["node"]] = p.factor_at(row["depart"])
    second = _solve_once(p, factors, first.order, budget * 0.6)
    best = second or first
    best.solve_ms = int((time.monotonic() - started) * 1000)
    return best
