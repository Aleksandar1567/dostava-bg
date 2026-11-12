import math
import random

from app.optimizer import Problem, solve, timeline


def _grid(n, seed=1):
    rnd = random.Random(seed)
    pts = [(rnd.random() * 6000, rnd.random() * 6000) for _ in range(n)]
    return [[math.dist(a, b) / 8 for b in pts] for a in pts]   # ~29 km/h


def test_windows_are_met_and_nothing_dropped():
    n = 41
    dur = _grid(n)
    service = [0] + [300] * (n - 1)
    earliest = [None] * n
    latest = [None] * n
    for i in range(1, n, 5):
        earliest[i], latest[i] = 3600 * (i % 4), 3600 * (i % 4) + 7200
    p = Problem(dur, service, earliest, latest, lambda t: 1.2, shift_end=10 * 3600,
                horizon=22 * 3600, time_limit_s=2)
    s = solve(p)
    assert sorted(s.order) == list(range(1, n)) and not s.dropped
    for r in timeline(s.order, dur, service, earliest, p.factor_at):
        node = r["node"]
        if earliest[node] is not None:
            assert r["begin"] >= earliest[node] - 1
            assert r["begin"] <= latest[node] + 1


def test_timeline_adds_service_and_travel():
    dur = [[0, 600, 900], [600, 0, 300], [900, 300, 0]]
    rows = timeline([1, 2], dur, [0, 300, 300], [None, None, None], lambda t: 1.5)
    assert rows[0]["arrival"] == 900               # 600 s * 1.5
    assert rows[1]["arrival"] == 900 + 300 + 450   # service + 300 s * 1.5
