"""Offline HTTP→background worker→real full planning pipeline→history tests."""

import socket
import time
import unittest
from copy import deepcopy
from threading import Event, Lock
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from app.agents import trip_planner_agent as planner_module
from app.api.routes import trip
from app.api.routes.auth import current_user
from app.models.schemas import TripPlan
from app.services.trip_task_service import MemoryTripTaskStore, TripTaskService
from pipeline_fixture import PipelineFixture


def payload(city="南京", **changes):
    data = dict(city=city, start_date="2026-11-10", end_date="2026-11-11", travel_days=2,
                transportation="公共交通", accommodation="经济型酒店", preferences=["当地美食"])
    if city == "江西":
        data.update(end_date="2026-11-12", travel_days=3, trip_mode="multi_city",
                    destinations=["南昌", "景德镇"], intercity_transport="high_speed_rail", preferences=["历史文化"])
    data.update(changes)
    return data


class TripPipelineTests(unittest.TestCase):
    def setUp(self):
        self.external_attempts = []
        original_connect = socket.socket.connect

        def connect(sock, address):
            # Windows asyncio can use a local socket pair. Only loopback is
            # allowed; all model/map/search egress fails and is counted.
            if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                return original_connect(sock, address)
            self.external_attempts.append(address)
            raise AssertionError("离线流水线测试禁止外部网络")

        self.enterContext(patch("socket.socket.connect", new=connect))
        self.fixture = PipelineFixture()
        self.enterContext(patch.object(planner_module, "_planner_runtime", self.fixture.runtime()))
        self.enterContext(patch.object(planner_module, "get_amap_service", return_value=self.fixture))
        self.enterContext(patch.object(planner_module, "query_ticket_info", side_effect=self.fixture.ticket))
        self.enterContext(patch.object(planner_module.MultiAgentTripPlanner, "_current_day_ready_minutes", return_value=None))
        self.enterContext(patch.object(planner_module.MultiAgentTripPlanner, "_search_attractions_multi", autospec=True, side_effect=self.fixture.acquire_attractions))
        self.enterContext(patch.object(planner_module.MultiAgentTripPlanner, "_search_hotels_nearby", autospec=True, side_effect=self.fixture.hotels))
        self.enterContext(patch.object(planner_module.MultiAgentTripPlanner, "_call_amap_around_search", autospec=True, side_effect=self.fixture.restaurants))
        self.sessions, self.history = [], []
        self.history_events = {}
        self.lock = Lock()
        self.plans = []  # Read by the report runner.

        def factory():
            session = planner_module.get_trip_planner_agent()
            with self.lock:
                self.sessions.append(session)
            return session

        def save_history(user, task_id, data):
            with self.lock:
                self.history.append((user, task_id, deepcopy(data)))
                self.history_events.setdefault(task_id, Event()).set()

        self.service = TripTaskService(planner_factory=factory, store=MemoryTripTaskStore(),
                                       max_workers=2, history_saver=save_history, recover_pending=False)
        self.enterContext(patch.object(trip, "get_trip_task_service", return_value=self.service))
        self.enterContext(patch.object(trip, "get_trip_planner_agent", side_effect=factory))
        self.enterContext(patch.object(trip, "check_request_content", new=AsyncMock()))
        app = FastAPI()
        app.include_router(trip.router, prefix="/api")

        def user(x_test_user: str = Header(default="user-nanjing")):
            return {"id": x_test_user}

        app.dependency_overrides[current_user] = user
        self.client = self.enterContext(TestClient(app))
        # Join workers before fake external adapters are removed in cleanup.
        self.addCleanup(self.service._executor.shutdown, wait=True, cancel_futures=True)

    def tearDown(self):
        self.assertEqual([], self.external_attempts)

    def submit(self, data, user="user-nanjing"):
        response = self.client.post("/api/trip/plan/authenticated", json=data, headers={"X-Test-User": user})
        self.assertEqual(202, response.status_code, response.text)
        return response.json()["task_id"]

    def wait_result(self, task_id, user="user-nanjing", expected="succeeded"):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/trip/plan/{task_id}", headers={"X-Test-User": user})
            self.assertEqual(200, response.status_code, response.text)
            value = response.json()
            if value["status"] in {"succeeded", "degraded", "failed"}:
                if value.get("data"):
                    self.plans.append(TripPlan.model_validate(value["data"]))
                self.assertEqual(expected, value["status"], {
                    "status": value["status"], "error": value.get("error"),
                    "quality": (value.get("data") or {}).get("quality_report"),
                })
                if expected == "succeeded":
                    # finish() precedes history save in the real worker. Wait
                    # explicitly rather than racing the history assertion.
                    with self.lock:
                        saved = self.history_events.setdefault(task_id, Event())
                    self.assertTrue(saved.wait(5), "任务完成后未保存历史记录")
                return value
            time.sleep(0.01)
        self.fail("后台任务未在离线测试时限内完成")

    def assert_plan(self, result, data):
        plan = TripPlan.model_validate(result["data"])
        self.assertEqual(data["city"], plan.city)
        self.assertEqual(data["start_date"], plan.start_date)
        self.assertEqual(data["end_date"], plan.end_date)
        self.assertEqual(data["travel_days"], len(plan.days))
        self.assertEqual(data["preferences"], plan.request_context.preferences)
        self.assertTrue(plan.quality_report.passed, plan.quality_report.model_dump())
        expected_pois = {p["id"] for p in self.fixture.pois(data["city"])}
        allowed_cities = data.get("destinations") or [data["city"]]
        actual = [a.poi_id for d in plan.days for a in d.attractions]
        self.assertTrue(actual)
        self.assertTrue(set(actual) <= expected_pois)
        self.assertEqual(len(actual), len(set(actual)))
        for day in plan.days:
            self.assertIsNotNone(day.hotel)
            self.assertTrue(any(city in day.hotel.name for city in allowed_cities), day.hotel.name)
            self.assertEqual({"breakfast", "lunch", "dinner"}, {m.type for m in day.meals})
            self.assertTrue(all(m.poi_id and m.data_source == "amap" for m in day.meals))
            self.assertTrue(all(any(m.poi_id.startswith(city + "-meal-") for city in allowed_cities)
                                for m in day.meals), "餐厅混入了其他任务的城市")
            previous_end = None
            for item in day.timeline:
                if item.start_time is None:
                    continue
                start = self.minutes(item.start_time)
                end = self.minutes(item.end_time or item.start_time)
                self.assertGreaterEqual(end, start, item.name)
                self.assertEqual(item.duration_minutes, end-start, item.name)
                if previous_end is not None:
                    self.assertGreaterEqual(start, previous_end, item.name)
                previous_end = end
                if item.meal_type == "lunch":
                    self.assertLessEqual(start, 840)
                if item.meal_type == "dinner":
                    self.assertLessEqual(start, 1230)
        self.assertIsNotNone(plan.budget)
        budget = plan.budget
        self.assertEqual(budget.total, budget.total_attractions+budget.total_hotels+budget.total_meals+budget.total_transportation)
        self.assertGreater(budget.total_hotels, 0)
        return plan

    @staticmethod
    def minutes(value):
        hour, minute = map(int, value.split(":"))
        return hour*60+minute

    def test_01_single_city_http_to_history(self):
        """南京：真实HTTP入口、后台生成全流程、质量报告与历史保存。"""
        data = payload()
        task_id = self.submit(data)
        result = self.wait_result(task_id)
        plan = self.assert_plan(result, data)
        self.assertEqual({p["id"] for p in self.fixture.pois("南京")}, {a.poi_id for d in plan.days for a in d.attractions})
        self.assertEqual(100, result["progress"])
        self.assertEqual("user-nanjing", self.history[0][0])
        self.assertEqual(task_id, self.history[0][1])
        for stage in ("attractions", "model-schedule", "hotel", "restaurant", "model-meals", "weather"):
            self.assertTrue(any(kind == stage for kind, city in self.fixture.calls), stage)

    def test_02_multi_city_http_to_split_rail(self):
        """江西：从生成入口一直到带车站接驳的跨城行程。"""
        data = payload("江西")
        task_id = self.submit(data, "user-jiangxi")
        plan = self.assert_plan(self.wait_result(task_id, "user-jiangxi"), data)
        self.assertEqual(["南昌", "景德镇"], plan.route_nodes)
        rail = [n for d in plan.days for n in d.timeline if n.kind == "transport" and n.is_intercity]
        self.assertEqual(1, len(rail))
        self.assertEqual(("南昌站", "景德镇站", 90), (rail[0].from_name, rail[0].to_name, rail[0].duration_minutes))
        self.assertIn("参考", rail[0].transport_mode)

    def test_03_two_users_parallel_pipelines_are_isolated(self):
        """并发南京/江西，在餐饮阶段同步交错，结果、缓存、历史均隔离。"""
        from threading import Barrier
        self.fixture.gate = Barrier(2, timeout=10)
        single = payload()
        multi = payload("江西", start_date="2026-11-14", end_date="2026-11-16")
        first = self.submit(single, "user-nanjing")
        second = self.submit(multi, "user-jiangxi")
        self.assertNotEqual(first, second)
        self.assert_plan(self.wait_result(first, "user-nanjing"), single)
        self.assert_plan(self.wait_result(second, "user-jiangxi"), multi)
        self.assertEqual(2, len(self.sessions))
        self.assertIsNot(self.sessions[0], self.sessions[1])
        self.assertIs(self.sessions[0].llm, self.sessions[1].llm)
        self.assertEqual({("南京",), ("南昌", "景德镇")}, {tuple(sorted(s._local_dish_catalogs)) for s in self.sessions})
        self.assertEqual({False, True}, {s._prefer_local_meals for s in self.sessions})
        self.assertEqual({"", "江西"}, {s._hotel_expected_region for s in self.sessions})
        self.assertEqual({("user-nanjing", first, "南京"), ("user-jiangxi", second, "江西")},
                         {(user, task, plan["city"]) for user, task, plan in self.history})
        self.assertEqual(404, self.client.get(f"/api/trip/plan/{first}", headers={"X-Test-User": "user-jiangxi"}).status_code)

    def test_04_edit_uses_new_session_and_keeps_original_request(self):
        """生成后通过HTTP重排：编辑会话独立，原始条件和三餐保留。"""
        task_id = self.submit(payload())
        value = self.wait_result(task_id)
        response = self.client.post("/api/trip/plan/reflow", json={"plan": value["data"], "day_indices": [0]})
        self.assertEqual(200, response.status_code, response.text)
        self.plans.append(TripPlan.model_validate(response.json()))
        self.assertEqual(value["data"]["request_context"], response.json()["request_context"])
        self.assertEqual(2, len(self.sessions))
        self.assertIsNot(self.sessions[0], self.sessions[1])
        self.assertEqual({}, self.sessions[1]._local_dish_catalogs)
        self.assertIsNot(self.sessions[0]._transport_hub_cache, self.sessions[1]._transport_hub_cache)

    def test_05_failed_job_does_not_poison_next_job(self):
        """景点接口失败后创建新任务：失败无历史，后续真实流水线可完成。"""
        with patch("traceback.print_exc"):
            failed = self.submit(payload(free_text_input="模拟景点接口故障"))
            result = self.wait_result(failed, expected="failed")
        self.assertIsNone(result["data"])
        self.assertEqual([], self.history)
        successful = self.submit(payload())
        self.assert_plan(self.wait_result(successful), payload())
        self.assertEqual(2, len(self.sessions))
        self.assertNotIn("failed-job-only", self.sessions[-1]._transport_hub_cache)
        self.assertEqual([successful], [task for user, task, plan in self.history])


if __name__ == "__main__":
    unittest.main()
