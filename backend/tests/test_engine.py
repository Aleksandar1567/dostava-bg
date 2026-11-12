"""Whole day through the engine with a fake (straight-line) router: no network."""

from pathlib import Path

import pytest

from app.engine import Engine, parse_dt
from app.routing import OsrmRouter

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "addresses.sqlite"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="run tools/build_address_index.py")


class FakeRouter(OsrmRouter):
    def __init__(self):
        self.last_error = ""

    def table(self, points, sources=None, use_cache=True):
        src = list(range(len(points))) if sources is None else list(sources)
        dur, dist = self.estimate(points, src)
        return dur, dist, "procena"

    def route_legs(self, points):
        return [[points[i], points[i + 1]] for i in range(len(points) - 1)]


@pytest.fixture
def engine(tmp_path):
    from app.geocode import Geocoder
    e = Engine(tmp_path, Geocoder(DB), FakeRouter(), ROOT.parent / "test-data")
    e.update_settings({"solver_seconds": 2})
    return e


def test_grouping_and_plan(engine):
    added, errors = engine.load_demo("rejon-nbg-zemun-100")
    assert added == 100 and not errors
    engine.sim_start()
    plan = engine.s["plan"]
    assert plan and not plan["dropped"]
    located = [p for p in engine.s["packages"].values() if p["lat"] is not None]
    # several packages per building -> fewer stops than packages
    assert len(engine.s["stops"]) < len(located)
    # every planned ETA = previous departure + travel (+ waiting for a window)
    order = plan["order"]
    for a, b in zip(order, order[1:]):
        A, B = engine.s["stops"][a], engine.s["stops"][b]
        gap = (parse_dt(B["arrival"]) - parse_dt(A["depart"])).total_seconds() / 60
        assert abs(gap - B["travel_min"]) < 0.2
    # every customer got a promised window
    assert all(p.get("promise") for p in located)


def test_simulated_day_finishes(engine):
    engine.load_demo("ceo-grad-30")
    engine.sim_start(seed=3)
    for _ in range(40):
        engine.sim_step(30)
        if engine.s["courier"]["state"] == "done":
            break
    assert engine.s["courier"]["state"] == "done"
    assert all(st["status"] in ("done", "failed") for st in engine.s["stops"].values())
    kinds = {n["kind"] for n in engine.s["notifications"]}
    assert {"plan", "arriving", "delivered"} <= kinds


def test_failed_customer_is_retried_later(engine):
    engine.load_demo("ceo-grad-30")
    engine.sim_start()
    engine.sim_step(15)
    sid = engine.s["plan"]["order"][0]
    engine.stop_action(sid, "failed")
    st = engine.s["stops"][sid]
    assert st["status"] == "pending" and st["not_before"]
    assert parse_dt(st["eta"]) >= parse_dt(st["not_before"])
