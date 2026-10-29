"""Production planner factory isolation; no model or map calls."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock, patch

from app.agents import trip_planner_agent as module


class PlannerIsolationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(module, "_multi_agent_planner", None, create=True))
        self.enterContext(patch.object(module, "_planner_runtime", None, create=True))
        self.llm = self.enterContext(patch.object(module, "get_llm", return_value=object()))
        self.enterContext(patch.object(module, "get_fallback_llm", return_value=None))
        self.enterContext(patch.object(module, "get_weather_service", return_value=object()))
        self.agents = self.enterContext(patch.object(module, "create_react_agent", side_effect=lambda **kw: Mock()))
        self.enterContext(patch.dict(module.MultiAgentTripPlanner.CITY_CENTERS, clear=False))
        if hasattr(module.MultiAgentTripPlanner, "_destination_research_cache"):
            self.enterContext(patch.dict(module.MultiAgentTripPlanner._destination_research_cache, clear=True))

    def test_factory_returns_distinct_sessions_but_reuses_model_runtime(self):
        first, second = module.get_trip_planner_agent(), module.get_trip_planner_agent()
        self.assertIsNot(first, second)
        self.assertIs(first.llm, second.llm)
        self.assertIs(first.critic_agent, second.critic_agent)
        self.assertEqual(3, self.agents.call_count)
        self.assertEqual(1, self.llm.call_count)

    def test_concurrent_sessions_cannot_overwrite_city_food_or_preferences(self):
        gate = Barrier(2, timeout=5)
        # Deliberately create sessions before interleaving all writes and reads.
        sessions = [module.get_trip_planner_agent(), module.get_trip_planner_agent()]

        def work(index):
            planner = sessions[index]
            city = ["南京", "江西"][index]
            planner._local_dish_catalogs = {city: {"lunch": [city + "菜"]}}
            planner._rejected_final_meal_ids = {(0, "lunch"): {city + "店"}}
            planner._hotel_expected_region = city
            planner._prefer_local_meals = index == 0
            planner._transport_hub_cache = {"hub": city}
            gate.wait()
            return (
                planner._local_dish_catalogs,
                planner._rejected_final_meal_ids,
                planner._hotel_expected_region, planner._prefer_local_meals,
                planner._transport_hub_cache,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(work, range(2)))
        for index, result in enumerate(results):
            city = ["南京", "江西"][index]
            self.assertEqual(({city: {"lunch": [city + "菜"]}}, {(0, "lunch"): {city + "店"}},
                              city, index == 0, {"hub": city}), result)

    def test_reused_worker_gets_clean_state_after_previous_failure(self):
        def failed():
            planner = module.get_trip_planner_agent()
            planner._intercity_rail_hub_cache = {"old": "旧车站"}
            planner._final_meal_search_cache = {"old": ["旧餐厅"]}
            planner._destination_research_cache = {"old": {"classic": ["旧景点"]}}
            raise RuntimeError("模拟接口失败")

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(RuntimeError):
                pool.submit(failed).result(timeout=5)
            fresh = pool.submit(module.get_trip_planner_agent).result(timeout=5)
        for name in ("_intercity_rail_hub_cache", "_final_meal_search_cache", "_destination_research_cache"):
            self.assertEqual({}, getattr(fresh, name))

    def test_dynamic_city_coordinates_never_mutate_class_defaults(self):
        first = module.get_trip_planner_agent()
        with patch.object(module, "get_amap_service") as amap:
            amap.return_value.geocode_administrative_location.return_value = (115.86, 28.68)
            self.assertEqual((115.86, 28.68), first._get_city_center("测试隔离县"))
        self.assertNotIn("测试隔离县", module.MultiAgentTripPlanner.CITY_CENTERS)
        self.assertNotIn("测试隔离县", module.get_trip_planner_agent().CITY_CENTERS)

    def test_request_caches_are_never_shared_by_reference(self):
        first, second = module.get_trip_planner_agent(), module.get_trip_planner_agent()
        for name in ("_transport_hub_cache", "_local_dish_catalogs", "_intercity_rail_hub_cache",
                     "_final_meal_search_cache", "_rejected_final_meal_ids", "_destination_research_cache"):
            self.assertIsNot(getattr(first, name, None), getattr(second, name, None), name)

    def test_simultaneous_first_requests_initialize_runtime_only_once(self):
        start = Barrier(4, timeout=5)

        def create(_):
            start.wait()
            return module.get_trip_planner_agent()

        with ThreadPoolExecutor(max_workers=4) as pool:
            sessions = list(pool.map(create, range(4)))
        self.assertEqual(4, len({id(session) for session in sessions}))
        self.assertTrue(all(session.llm is sessions[0].llm for session in sessions))
        self.assertEqual(1, self.llm.call_count)
        self.assertEqual(3, self.agents.call_count)

    def test_runtime_initialization_failure_does_not_publish_partial_clients(self):
        self.agents.side_effect = RuntimeError("初始化失败")
        with self.assertRaises(RuntimeError):
            module.get_trip_planner_agent()
        self.assertIsNone(module._planner_runtime)
        self.agents.side_effect = lambda **kw: Mock()
        session = module.get_trip_planner_agent()
        self.assertIs(session.llm, module._planner_runtime.llm)


if __name__ == "__main__":
    unittest.main()
