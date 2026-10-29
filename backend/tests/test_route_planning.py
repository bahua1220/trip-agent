"""跨城与自驾环线确定性逻辑的回归测试。"""

import unittest
from unittest.mock import Mock, patch

from app.agents.trip_planner_agent import MultiAgentTripPlanner, TripPlanningError
from app.models.schemas import (
    Attraction,
    DayPlan,
    Hotel,
    Location,
    Meal,
    RouteSegment,
    TripPlan,
    TripRequest,
)


def _request(**overrides) -> TripRequest:
    payload = {
        "city": "长三角",
        "start_date": "2026-08-01",
        "end_date": "2026-08-03",
        "travel_days": 3,
        "transportation": "公共交通",
        "accommodation": "舒适型酒店",
        "trip_mode": "multi_city",
        "destinations": ["南京", "苏州", "上海"],
    }
    payload.update(overrides)
    return TripRequest(**payload)


def _attraction(name: str) -> Attraction:
    return Attraction(
        name=name,
        address="测试地址",
        location=Location(longitude=120, latitude=31),
        visit_duration=90,
        description="测试景点",
    )


def _candidate(name: str, stop: str, index: int) -> dict:
    return {
        "area_name": name,
        "display_name": name,
        "score": 10 - index,
        "category": "风景名胜",
        "longitude": 118 + index,
        "latitude": 31 + index,
        "address": f"{stop}测试地址{index}",
        "_is_remote": False,
        "members": [{
            "id": f"POI-{index}",
            "name": name,
            "route_stop": stop,
            "longitude": 118 + index,
            "latitude": 31 + index,
            "address": f"{stop}测试地址{index}",
            "broad_category": "风景名胜",
            "rating": "4.7",
        }],
    }


class RoutePlanningTests(unittest.TestCase):
    def setUp(self):
        self.planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)

    def test_auto_intercity_uses_rail_and_requires_confirmation(self):
        centers = {
            "南京": (118.7969, 32.0603),
            "上海": (121.4737, 31.2304),
        }
        self.planner._get_city_center = lambda city: centers.get(city)

        segment = self.planner._route_segment_summary(
            "南京", "上海", 6, "auto"
        )

        self.assertEqual(segment.transport_mode, "高铁/动车")
        self.assertTrue(segment.requires_confirmation)
        self.assertFalse(segment.exceeds_daily_limit)
        self.assertGreater(segment.duration_minutes, 0)

    def test_compact_schedule_repairs_extra_closing_brace_locally(self):
        request = _request(
            city="南京",
            start_date="2026-08-01",
            end_date="2026-08-01",
            travel_days=1,
            trip_mode="single_city",
            destinations=[],
        )
        candidates = [_candidate("南京夫子庙", "南京", 1)]
        malformed = (
            '{"days":[{"day_index":0,"items":[{"id":"C1",'
            '"reason":"市区核心景点"}],"summary":"轻松游览"}}],'
            '"overall_suggestions":"注意开放时间"}'
        )

        result = self.planner._parse_compact_schedule(
            malformed, request, candidates
        )

        self.assertEqual("南京夫子庙", result.days[0].attractions[0].name)
        self.assertEqual("轻松游览", result.days[0].description)

    def test_deterministic_schedule_keeps_full_plan_when_model_json_is_invalid(self):
        request = _request()
        candidates = [
            _candidate("中山陵", "南京", 1),
            _candidate("苏州博物馆", "苏州", 2),
            _candidate("外滩", "上海", 3),
        ]
        itinerary = [
            {"day_index": 0, "activity_location": "南京"},
            {"day_index": 1, "activity_location": "苏州"},
            {"day_index": 2, "activity_location": "上海"},
        ]

        result = self.planner._build_deterministic_candidate_schedule(
            request, candidates, itinerary
        )

        self.assertEqual(3, len(result.days))
        self.assertEqual(
            [["中山陵"], ["苏州博物馆"], ["外滩"]],
            [[item.name for item in day.attractions] for day in result.days],
        )
        self.assertNotIn("待补全", result.overall_suggestions)

    def test_attraction_normalizes_missing_and_list_addresses(self):
        for raw, expected in [
            ([], ""), (None, ""), ("正常地址", "正常地址"),
            ([" 江西省 ", "婺源县", ""], "江西省 婺源县"),
        ]:
            with self.subTest(address=raw):
                payload = _attraction("篁岭").model_dump()
                payload["address"] = raw
                self.assertEqual(expected, Attraction(**payload).address)

    def test_compact_schedule_preserves_model_choices_with_empty_amap_address(self):
        candidates = [
            _candidate("中山陵", "南京", 1),
            _candidate("苏州博物馆", "苏州", 2),
            _candidate("外滩", "上海", 3),
        ]
        candidates[1]["address"] = []
        candidates[1]["members"][0]["address"] = []
        response = '{"days":[{"day_index":0,"items":["C1"]},{"day_index":1,"items":["C2"]},{"day_index":2,"items":["C3"]}]}'

        result = self.planner._parse_compact_schedule(response, _request(), candidates)

        self.assertEqual(
            [["中山陵"], ["苏州博物馆"], ["外滩"]],
            [[item.name for item in day.attractions] for day in result.days],
        )
        self.assertEqual("", result.days[1].attractions[0].address)

    def test_compact_schedule_skips_only_corrupt_candidate(self):
        candidates = [
            _candidate("中山陵", "南京", 1),
            _candidate("损坏的地图景点", "南京", 2),
            _candidate("夫子庙", "南京", 3),
        ]
        response = '{"days":[{"day_index":0,"items":["C1","C2","C3"],"summary":"模型原有安排"}]}'
        for field, value in [("longitude", "无效坐标"), ("address", {"unexpected": "address"})]:
            with self.subTest(field=field):
                corrupt = dict(candidates[1], **{field: value})
                result = self.planner._parse_compact_schedule(
                    response, _request(), [candidates[0], corrupt, candidates[2]]
                )
                self.assertEqual(
                    ["中山陵", "夫子庙"],
                    [item.name for item in result.days[0].attractions],
                )
                self.assertEqual("模型原有安排", result.days[0].description)

    def test_planner_uses_json_mode_and_retries_503_with_backoff(self):
        class Response:
            content = '{"days":[]}'

        class FlakyLLM:
            def __init__(self):
                self.calls = 0
                self.bind_options = []

            def bind(self, **options):
                self.bind_options.append(options)
                return self

            def invoke(self, _messages):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("503 Service is too busy")
                return Response()

        flaky = FlakyLLM()
        self.planner.llm = flaky
        self.planner.fallback_llm = None

        with patch("app.agents.trip_planner_agent.time.sleep") as sleep:
            result = self.planner._run_planner("只返回JSON", "生成JSON")

        self.assertEqual('{"days":[]}', result)
        self.assertEqual(2, flaky.calls)
        self.assertEqual(
            {"type": "json_object"},
            flaky.bind_options[0]["response_format"],
        )
        sleep.assert_called_once_with(2.0)

    def test_loop_route_returns_to_start(self):
        request = _request(
            city="青甘大环线",
            trip_mode="road_trip_loop",
            destinations=["西宁", "青海湖", "敦煌"],
        )
        self.assertEqual(
            self.planner._build_route_nodes(request),
            ["西宁", "青海湖", "敦煌", "西宁"],
        )

    def test_multi_city_itinerary_treats_first_node_as_first_stop(self):
        request = _request()
        self.planner._route_segment_summary = (
            lambda origin, destination, max_hours, preference: RouteSegment(
                origin=origin,
                destination=destination,
                transport_mode="高铁/动车",
                distance_km=200,
                duration_minutes=150,
                requires_confirmation=True,
            )
        )

        nodes, itinerary = self.planner._build_route_itinerary(request)

        self.assertEqual(nodes, ["南京", "苏州", "上海"])
        self.assertEqual(len(itinerary), 3)
        self.assertEqual(
            [item["overnight_city"] for item in itinerary],
            ["南京", "苏州", "上海"],
        )
        self.assertIsNone(itinerary[0]["route_segment"])

    def test_five_day_three_city_route_keeps_all_cities_in_the_plan(self):
        request = _request(
            city="江苏",
            start_date="2026-08-03",
            end_date="2026-08-07",
            travel_days=5,
            destinations=["扬州", "无锡", "南京"],
        )
        self.planner._route_segment_summary = (
            lambda origin, destination, max_hours, preference: RouteSegment(
                origin=origin,
                destination=destination,
                transport_mode="高铁/动车",
                distance_km=100,
                duration_minutes=90,
                requires_confirmation=True,
            )
        )

        nodes, itinerary = self.planner._build_route_itinerary(request)

        self.assertEqual(nodes, ["扬州", "无锡", "南京"])
        self.assertEqual(
            [item["overnight_city"] for item in itinerary],
            ["扬州", "无锡", "无锡", "南京", "南京"],
        )
        self.assertEqual(
            [
                (item["route_segment"].origin, item["route_segment"].destination)
                for item in itinerary
                if item["route_segment"]
            ],
            [("扬州", "无锡"), ("无锡", "南京")],
        )

    def test_multi_city_moves_transfer_before_destination_full_day(self):
        request = _request(
            city="江西",
            start_date="2026-09-07",
            end_date="2026-09-11",
            travel_days=5,
            destinations=["南昌", "景德镇", "婺源"],
            start_location="南昌",
            end_location="婺源",
        )
        segment = RouteSegment(
            origin="景德镇",
            destination="婺源",
            transport_mode="高铁/动车",
            distance_km=90,
            duration_minutes=150,
        )
        days = [
            DayPlan(
                date=f"2026-09-{day + 7:02d}",
                day_index=day,
                description="原描述",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
                start_location="景德镇" if day >= 1 else "南昌",
                end_location="婺源" if day >= 3 else (
                    "景德镇" if day >= 1 else "南昌"
                ),
                overnight_city="婺源" if day >= 3 else (
                    "景德镇" if day >= 1 else "南昌"
                ),
                route_segment=segment if day == 3 else None,
            )
            for day in range(5)
        ]
        days[2].attractions = [
            Attraction(
                name="御窑厂国家考古遗址公园",
                address="景德镇",
                location=Location(longitude=117.20, latitude=29.29),
                visit_duration=150,
                description="日间景点",
                category="遗址公园",
                rating=4.8,
            ),
            Attraction(
                name="陶溪川文创街区",
                address="景德镇",
                location=Location(longitude=117.23, latitude=29.29),
                visit_duration=150,
                description="文创街区",
                category="文化街区",
                rating=4.7,
            ),
        ]
        days[3].attractions = [
            Attraction(
                name="婺源篁岭景区",
                address="婺源",
                location=Location(longitude=118.12, latitude=29.32),
                visit_duration=210,
                description="远郊整日景点",
                category="风景名胜区",
            )
        ]
        plan = TripPlan(
            city="江西",
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            overall_suggestions="测试",
        )

        self.planner._optimize_multi_city_transfer_boundaries(plan, request)

        self.assertIs(plan.days[2].route_segment, segment)
        self.assertIsNone(plan.days[3].route_segment)
        self.assertEqual("婺源", plan.days[2].overnight_city)
        self.assertEqual(["御窑厂国家考古遗址公园"], [
            item.name for item in plan.days[2].attractions
        ])
        self.assertEqual(
            ["陶溪川文创街区"],
            [item.attraction.name for item in plan.removed_attractions],
        )

    def test_multi_city_moves_remote_arrival_attraction_to_next_full_day(self):
        request = _request(
            city="江西",
            start_date="2026-09-09",
            end_date="2026-09-12",
            travel_days=4,
            destinations=["南昌", "景德镇", "婺源"],
        )
        segment = RouteSegment(
            origin="南昌", destination="景德镇",
            transport_mode="高铁/动车", duration_minutes=150,
        )
        yaoli = Attraction(
            name="瑶里古镇风景区", address="景德镇",
            location=Location(longitude=117.55, latitude=29.54),
            visit_duration=210, description="远郊整日景点", category="风景区",
        )
        museum = Attraction(
            name="景德镇中国陶瓷博物馆", address="景德镇",
            location=Location(longitude=117.17, latitude=29.29),
            visit_duration=150, description="市区场馆", category="博物馆",
        )
        kiln = Attraction(
            name="古窑民俗博览区", address="景德镇",
            location=Location(longitude=117.18, latitude=29.27),
            visit_duration=150, description="市区景点", category="文化景区",
        )
        days = [
            DayPlan(
                date=f"2026-09-{9 + index:02d}", day_index=index,
                description="原描述", transportation="公共交通",
                accommodation="经济型酒店",
                start_location="南昌" if index < 1 else "景德镇",
                end_location="南昌" if index < 1 else "景德镇",
                overnight_city="南昌" if index < 1 else "景德镇",
                route_segment=segment if index == 1 else None,
                attractions=[yaoli] if index == 1 else (
                    [museum, kiln] if index == 2 else []
                ),
            )
            for index in range(4)
        ]
        plan = TripPlan(
            city="江西", start_date=request.start_date, end_date=request.end_date,
            days=days, overall_suggestions="测试",
        )

        self.planner._arrange_multi_city_attraction_blocks(plan, request)

        self.assertEqual(
            ["景德镇中国陶瓷博物馆", "古窑民俗博览区"],
            [item.name for item in plan.days[1].attractions],
        )
        self.assertEqual(
            ["瑶里古镇风景区"],
            [item.name for item in plan.days[2].attractions],
        )

    def test_multi_city_rebalance_does_not_delay_full_day_destination(self):
        request = _request(
            city="江西", travel_days=2,
            start_date="2026-09-09", end_date="2026-09-10",
            destinations=["南昌", "景德镇"],
        )
        remote = Attraction(
            name="瑶里古镇风景区", address="景德镇",
            location=Location(longitude=117.55, latitude=29.54),
            visit_duration=210, description="远郊整日景点", category="风景区",
        )
        first = _attraction("八一起义纪念馆")
        second = _attraction("滕王阁")
        segment = RouteSegment(
            origin="南昌", destination="景德镇",
            transport_mode="高铁/动车", duration_minutes=150,
        )
        plan = TripPlan(
            city="江西", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date=request.start_date, day_index=0, description="南昌",
                    transportation="公共交通", accommodation="经济型酒店",
                    attractions=[first, second], overnight_city="南昌",
                ),
                DayPlan(
                    date=request.end_date, day_index=1, description="景德镇",
                    transportation="公共交通", accommodation="经济型酒店",
                    attractions=[remote], route_segment=segment,
                    start_location="南昌", end_location="景德镇",
                    overnight_city="景德镇",
                ),
            ],
        )

        self.planner._rebalance_transfer_day_attractions(plan, request)

        self.assertEqual(
            ["八一起义纪念馆", "滕王阁"],
            [item.name for item in plan.days[0].attractions],
        )
        self.assertEqual(["瑶里古镇风景区"], [
            item.name for item in plan.days[1].attractions
        ])

    def test_multi_city_adjacent_full_day_conflict_keeps_next_city_landmark(self):
        request = _request(
            city="江西", travel_days=4,
            start_date="2026-09-09", end_date="2026-09-12",
            destinations=["南昌", "景德镇", "婺源"],
        )
        yaoli = Attraction(
            name="瑶里古镇风景区", address="景德镇",
            location=Location(longitude=117.55, latitude=29.54),
            visit_duration=210, description="远郊整日景点", category="风景区",
            rating=4.5,
        )
        huangling = Attraction(
            name="婺源篁岭景区", address="婺源",
            location=Location(longitude=118.12, latitude=29.32),
            visit_duration=210, description="远郊整日景点", category="风景区",
            rating=4.8,
        )
        segment = RouteSegment(
            origin="景德镇", destination="婺源",
            transport_mode="高铁/动车", duration_minutes=150,
        )
        days = [
            DayPlan(
                date=f"2026-09-{9 + index:02d}", day_index=index,
                description="原描述", transportation="公共交通",
                accommodation="经济型酒店",
                start_location="景德镇", end_location=(
                    "婺源" if index >= 3 else "景德镇"
                ),
                overnight_city="婺源" if index >= 3 else "景德镇",
                route_segment=segment if index == 3 else None,
                attractions=[yaoli] if index == 2 else (
                    [huangling] if index == 3 else []
                ),
            )
            for index in range(4)
        ]
        plan = TripPlan(
            city="江西", start_date=request.start_date, end_date=request.end_date,
            days=days, overall_suggestions="测试",
        )

        self.planner._optimize_multi_city_transfer_boundaries(plan, request)

        self.assertIs(plan.days[2].route_segment, segment)
        self.assertIsNone(plan.days[3].route_segment)
        self.assertEqual([], plan.days[2].attractions)
        self.assertEqual(["婺源篁岭景区"], [
            item.name for item in plan.days[3].attractions
        ])
        self.assertIn(
            "瑶里古镇风景区",
            [item.attraction.name for item in plan.removed_attractions],
        )

    def test_multi_city_backfill_only_uses_current_route_stop(self):
        request = _request(
            city="江西", travel_days=2,
            start_date="2026-09-09", end_date="2026-09-10",
            destinations=["南昌", "景德镇"],
        )
        segment = RouteSegment(
            origin="南昌", destination="景德镇",
            transport_mode="高铁/动车", duration_minutes=120,
        )
        plan = TripPlan(
            city="江西", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date=request.start_date, day_index=0, description="南昌",
                    transportation="公共交通", accommodation="经济型酒店",
                    start_location="南昌", end_location="南昌",
                    overnight_city="南昌",
                ),
                DayPlan(
                    date=request.end_date, day_index=1, description="景德镇",
                    transportation="公共交通", accommodation="经济型酒店",
                    start_location="南昌", end_location="景德镇",
                    overnight_city="景德镇", route_segment=segment,
                ),
            ],
        )
        candidates = [
            _candidate("滕王阁", "南昌", 1),
            _candidate("陶瓷博物馆", "景德镇", 2),
            _candidate("御窑厂遗址", "景德镇", 3),
        ]

        self.planner._backfill_underfilled_days(plan, request, candidates)

        self.assertEqual(["滕王阁"], [
            item.name for item in plan.days[0].attractions
        ])
        self.assertTrue(plan.days[1].attractions)
        self.assertTrue(
            {item.name for item in plan.days[1].attractions}
            <= {"陶瓷博物馆", "御窑厂遗址"}
        )

    def test_single_city_attraction_block_arrangement_is_noop(self):
        request = _request(
            city="济南", trip_mode="single_city", destinations=[],
            travel_days=2, start_date="2026-09-09", end_date="2026-09-10",
        )
        original = _attraction("趵突泉")
        plan = TripPlan(
            city="济南", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date=request.start_date, day_index=0, description="济南",
                    transportation="公共交通", accommodation="经济型酒店",
                    attractions=[original],
                ),
                DayPlan(
                    date=request.end_date, day_index=1, description="济南",
                    transportation="公共交通", accommodation="经济型酒店",
                ),
            ],
        )

        self.planner._arrange_multi_city_attraction_blocks(plan, request)

        self.assertEqual(["趵突泉"], [
            item.name for item in plan.days[0].attractions
        ])

    def test_single_city_never_moves_transfer_boundaries(self):
        request = _request(
            city="济南",
            start_date="2026-09-07",
            end_date="2026-09-09",
            travel_days=3,
            trip_mode="single_city",
            destinations=[],
        )
        segment = RouteSegment(
            origin="甲",
            destination="乙",
            duration_minutes=120,
        )
        days = [
            DayPlan(
                date=f"2026-09-{day + 7:02d}",
                day_index=day,
                description="原描述",
                transportation="公共交通",
                accommodation="经济型酒店",
                attractions=[],
                route_segment=segment if day == 2 else None,
            )
            for day in range(3)
        ]
        days[2].attractions = [
            Attraction(
                name="测试山景区",
                address="测试",
                location=Location(longitude=117, latitude=36),
                visit_duration=210,
                description="测试",
                category="景区",
            )
        ]
        plan = TripPlan(
            city="济南",
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            overall_suggestions="测试",
        )

        self.planner._optimize_multi_city_transfer_boundaries(plan, request)

        self.assertIsNone(plan.days[1].route_segment)
        self.assertIs(plan.days[2].route_segment, segment)

    def test_scenic_route_node_keeps_scenic_destination_but_uses_service_base(self):
        request = _request(
            city="西藏",
            start_date="2026-08-03",
            end_date="2026-08-07",
            travel_days=5,
            trip_mode="road_trip_loop",
            destinations=["拉萨", "羊卓雍措", "日喀则", "班戈", "纳木措"],
        )
        self.planner._route_segment_summary = (
            lambda origin, destination, max_hours, preference: RouteSegment(
                origin=origin,
                destination=destination,
                transport_mode="自驾",
                distance_km=150,
                duration_minutes=180,
            )
        )

        _, itinerary = self.planner._build_route_itinerary(request)

        namtso_day = itinerary[3]
        self.assertEqual(namtso_day["activity_location"], "纳木措")
        self.assertEqual(namtso_day["end_location"], "纳木措")
        self.assertEqual(namtso_day["overnight_city"], "当雄")
        self.assertEqual(namtso_day["node_type"], "scenic_area")

    def test_scenic_route_node_forces_matching_core_attraction(self):
        request = _request(
            city="西藏",
            start_date="2026-08-03",
            end_date="2026-08-07",
            travel_days=5,
            trip_mode="road_trip_loop",
            destinations=["拉萨", "羊卓雍措", "日喀则", "班戈", "纳木措"],
        )
        days = [
            DayPlan(
                date=f"2026-08-0{index + 3}",
                day_index=index,
                description="测试",
                transportation="自驾",
                accommodation="经济型酒店",
                attractions=[_attraction("念青唐古拉山")] if index == 3 else [],
                end_location="纳木措" if index == 3 else "拉萨",
                overnight_city="当雄" if index == 3 else "拉萨",
                is_transit_day=index == 3,
            )
            for index in range(5)
        ]
        plan = TripPlan(
            city="西藏",
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            overall_suggestions="测试",
        )
        candidates = [{
            "area_name": "念青唐古拉山",
            "display_name": "念青唐古拉山",
            "route_stop": "纳木措",
            "_is_remote": True,
            "members": [
                {
                    "id": "MOUNTAIN",
                    "name": "念青唐古拉山",
                    "route_stop": "纳木措",
                    "longitude": 91.13,
                    "latitude": 30.54,
                    "score": 5.2,
                },
                {
                    "id": "NAMTSO",
                    "name": "纳木措国家风景区",
                    "route_stop": "纳木措",
                    "longitude": 90.75,
                    "latitude": 30.72,
                    "address": "当雄县纳木措景区",
                    "broad_category": "风景名胜",
                    "rating": "4.8",
                    "score": 5.1,
                    "_is_remote": True,
                },
            ],
        }]
        route_itinerary = [{
            "day_index": 3,
            "activity_location": "纳木措",
            "end_location": "纳木措",
            "overnight_city": "当雄",
            "node_type": "scenic_area",
        }]

        result = self.planner._ensure_scenic_route_node_attractions(
            plan, request, candidates, route_itinerary
        )

        self.assertEqual([item.name for item in result.days[3].attractions], ["纳木措国家风景区"])
        self.assertEqual(result.days[3].attractions[0].poi_id, "NAMTSO")
        self.assertEqual(result.days[3].attractions[0].rating, 4.8)

    def test_scenic_route_hotel_search_uses_service_base_center(self):
        request = _request()
        plan = TripPlan(
            city="西藏",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[DayPlan(
                date="2026-08-01",
                day_index=0,
                description="纳木措",
                transportation="自驾",
                accommodation="经济型酒店",
                end_location="纳木措",
                overnight_city="当雄",
                attractions=[Attraction(
                    name="纳木措国家风景区",
                    address="纳木措",
                    location=Location(longitude=90.75, latitude=30.72),
                    visit_duration=240,
                    description="核心景区",
                )],
            )],
        )
        calls = []
        self.planner._get_city_center = lambda stop: (91.10, 30.48) if stop == "当雄" else None
        self.planner._search_hotels_nearby = (
            lambda lng, lat, city, accommodation: calls.append((lng, lat, city)) or []
        )

        self.planner._select_route_hotels(plan, request)

        self.assertEqual(calls, [(91.10, 30.48, "当雄")])

    def test_scenic_core_is_put_on_clear_day_not_arrival_day(self):
        request = _request(
            city="江西", travel_days=2, start_date="2026-09-10",
            end_date="2026-09-11", destinations=["婺源", "庐山"],
            start_location="婺源", end_location="庐山",
        )
        plan = TripPlan(
            city="江西", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试", trip_mode="multi_city",
            days=[
                DayPlan(
                    date="2026-09-10", day_index=0, description="抵达庐山",
                    transportation="公共交通", accommodation="酒店",
                    end_location="庐山", overnight_city="九江",
                    route_segment=RouteSegment(
                        origin="婺源", destination="庐山",
                        transport_mode="高铁/动车", duration_minutes=240,
                    ),
                ),
                DayPlan(
                    date="2026-09-11", day_index=1, description="游览庐山",
                    transportation="公共交通", accommodation="酒店",
                    end_location="庐山", overnight_city="九江",
                ),
            ],
        )
        candidates = [_candidate("庐山国家重点风景名胜区", "庐山", 1)]
        itinerary = [
            {
                "day_index": 0, "activity_location": "庐山",
                "end_location": "庐山", "overnight_city": "九江",
                "node_type": "scenic_area", "is_transit_day": True,
                "route_segment": plan.days[0].route_segment,
            },
            {
                "day_index": 1, "activity_location": "庐山",
                "end_location": "庐山", "overnight_city": "九江",
                "node_type": "scenic_area", "is_transit_day": False,
                "route_segment": None,
            },
        ]

        self.planner._ensure_scenic_route_node_attractions(
            plan, request, candidates, itinerary
        )

        self.assertEqual([], plan.days[0].attractions)
        self.assertEqual(
            ["庐山国家重点风景名胜区"],
            [item.name for item in plan.days[1].attractions],
        )

    def test_scenic_core_avoids_early_departure_day(self):
        request = _request(
            city="江西", travel_days=2, end_date="2026-08-02",
            destinations=["婺源", "庐山"], start_location="婺源",
            end_location="庐山", departure_transport="高铁",
            departure_time="09:30",
        )
        plan = TripPlan(
            city="江西", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试", trip_mode="multi_city",
            days=[
                DayPlan(
                    date=request.start_date, day_index=0, description="抵达庐山",
                    transportation="公共交通", accommodation="酒店",
                    end_location="庐山", overnight_city="九江", is_transit_day=True,
                    route_segment=RouteSegment(
                        origin="婺源", destination="庐山",
                        transport_mode="高铁/动车", duration_minutes=120,
                    ),
                ),
                DayPlan(
                    date=request.end_date, day_index=1, description="离开庐山",
                    transportation="公共交通", accommodation="酒店",
                    end_location="庐山", overnight_city="九江",
                ),
            ],
        )
        itinerary = [
            {
                "day_index": 0, "activity_location": "庐山",
                "end_location": "庐山", "overnight_city": "九江",
                "node_type": "scenic_area", "is_transit_day": True,
                "route_segment": plan.days[0].route_segment,
            },
            {
                "day_index": 1, "activity_location": "庐山",
                "end_location": "庐山", "overnight_city": "九江",
                "node_type": "scenic_area", "is_transit_day": False,
                "route_segment": None,
            },
        ]

        self.planner._ensure_scenic_route_node_attractions(
            plan, request, [_candidate("庐山国家重点风景名胜区", "庐山", 1)],
            itinerary,
        )

        self.assertEqual(
            ["庐山国家重点风景名胜区"],
            [item.name for item in plan.days[0].attractions],
        )
        self.assertEqual([], plan.days[1].attractions)

    def test_scenic_route_search_injects_core_and_filters_distant_city_poi(self):
        request = _request(
            city="江西", travel_days=2, end_date="2026-08-02",
            destinations=["九江", "庐山"],
            start_location="九江", end_location="庐山",
        )
        self.planner._search_attractions_multi = Mock(return_value={
            "attractions": [{
                "id": "CITY", "name": "九江市区公园", "longitude": 116.30,
                "latitude": 29.80, "is_food": False,
            }],
            "food_pois": [], "success": True,
        })
        self.planner._call_mcp_search = Mock(return_value=[{
            "id": "LUSHAN", "name": "庐山国家重点风景名胜区",
            "longitude": 115.98, "latitude": 29.57,
            "type": "风景名胜", "typecode": "110000",
        }])

        result = self.planner._search_route_attractions(request)
        lushan = [
            item for item in result["attractions"]
            if item.get("route_stop") == "庐山"
        ]

        self.assertTrue(any(item["name"] == "庐山国家重点风景名胜区" for item in lushan))
        self.assertFalse(any(item["name"] == "九江市区公园" for item in lushan))

    def test_route_hotel_balances_attraction_anchor_and_next_station(self):
        # Hotel scoring needs both city centers; never query the real map here.
        self.planner._get_city_center = Mock(side_effect=lambda city: {
            "婺源": (117.86, 29.25), "景德镇": (117.20, 29.30),
        }.get(city))
        request = _request(
            city="江西", travel_days=2, start_date="2026-09-10",
            end_date="2026-09-11", destinations=["婺源", "景德镇"],
            start_location="婺源", end_location="景德镇",
        )
        plan = TripPlan(
            city="江西", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试", trip_mode="multi_city",
            days=[DayPlan(
                date=request.start_date, day_index=0, description="婺源",
                transportation="公共交通", accommodation="舒适型酒店",
                end_location="婺源", overnight_city="婺源",
                route_segment=RouteSegment(
                    origin="婺源", destination="景德镇",
                    transport_mode="高铁/动车", duration_minutes=120,
                ),
                attractions=[Attraction(
                    name="李坑景区", address="李坑",
                    location=Location(longitude=118.00, latitude=29.30),
                    visit_duration=120, description="测试",
                )],
            )],
        )
        self.planner._hotel_density_anchor = Mock(return_value=(118.00, 29.30))
        self.planner._resolve_intercity_rail_hub = Mock(return_value={
            "name": "婺源站", "longitude": 117.90, "latitude": 29.30,
        })

        def hotels(lng, _lat, _city, _accommodation):
            name = "景区酒店" if lng > 117.95 else "车站城区酒店"
            return [{
                "name": name, "address": "测试", "lng": lng, "lat": 29.30,
                "distance_km": 0.0, "distance_m": 0, "type": "宾馆酒店",
                "typecode": "100103", "rating": "4.6", "tier_rank": 0,
            }]

        self.planner._search_hotels_nearby = Mock(side_effect=hotels)
        self.planner._estimate_hotel_price = Mock(return_value=300)
        self.planner._select_route_hotels(plan, request)

        self.assertEqual("车站城区酒店", plan.days[0].hotel.name)

    def test_missing_meal_fallback_supports_single_and_multi_city(self):
        multi_request = _request()
        hotel = Hotel(
            name="测试酒店",
            address="测试地址",
            location=Location(longitude=118.0, latitude=29.3),
        )
        multi_plan = TripPlan(
            city="长三角", start_date=multi_request.start_date,
            end_date=multi_request.end_date, overall_suggestions="测试",
            trip_mode="multi_city",
            days=[DayPlan(
                date=multi_request.start_date, day_index=0, description="南京",
                transportation="公共交通", accommodation="舒适型酒店",
                hotel=hotel, meals=[Meal(type="breakfast", name="原早餐")],
            )],
        )

        self.planner._restore_missing_multi_city_meals(multi_plan, multi_request)

        self.assertEqual(
            ["breakfast", "lunch", "dinner"],
            [meal.type for meal in multi_plan.days[0].meals],
        )
        self.assertTrue(all(
            meal.data_source == "logistics_advice"
            for meal in multi_plan.days[0].meals if meal.type != "breakfast"
        ))

        single_request = _request(
            city="南京", trip_mode="single_city", destinations=[],
            start_location=None, end_location=None,
        )
        single_plan = multi_plan.model_copy(deep=True)
        single_plan.days[0].meals = []
        self.planner._restore_missing_multi_city_meals(single_plan, single_request)
        self.assertEqual(["breakfast", "lunch", "dinner"], [m.type for m in single_plan.days[0].meals])

    def test_multi_city_early_departure_hotel_anchors_to_selected_hub(self):
        request = _request(
            city="江西",
            start_date="2026-09-07",
            end_date="2026-09-09",
            travel_days=3,
            destinations=["南昌", "景德镇", "婺源"],
            start_location="南昌",
            end_location="婺源",
            departure_transport="高铁",
            departure_time="11:01",
            departure_location="婺源站",
            departure_location_longitude=117.877,
            departure_location_latitude=29.239,
        )
        plan = TripPlan(
            city="江西",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[DayPlan(
                date="2026-09-09",
                day_index=2,
                description="篁岭",
                transportation="公共交通",
                accommodation="经济型酒店",
                end_location="婺源",
                overnight_city="婺源",
                attractions=[Attraction(
                    name="婺源篁岭景区",
                    address="婺源",
                    location=Location(longitude=118.12, latitude=29.32),
                    visit_duration=210,
                    description="整日景点",
                    category="风景名胜区",
                )],
            )],
        )
        calls = []
        self.planner._search_hotels_nearby = (
            lambda lng, lat, city, accommodation:
            calls.append((lng, lat, city)) or []
        )

        self.planner._select_route_hotels(plan, request)

        self.assertEqual(calls, [(117.877, 29.239, "婺源")])

    def test_compact_schedule_restores_verified_candidate_fields(self):
        request = _request()
        candidates = [
            _candidate("中山陵", "南京", 1),
            _candidate("拙政园", "苏州", 2),
            _candidate("外滩", "上海", 3),
        ]
        route_itinerary = [
            {"overnight_city": "南京"},
            {"overnight_city": "苏州"},
            {"overnight_city": "上海"},
        ]
        response = """{
          "days": [
            {"day_index": 0, "items": [{"id": "C1", "reason": "上午游览"}], "summary": "南京历史文化"},
            {"day_index": 1, "items": [{"id": "C2", "reason": "抵达后游览"}]},
            {"day_index": 2, "items": [{"id": "C3", "reason": "返程前游览"}]}
          ],
          "overall_suggestions": "按城际路线依次游览。"
        }"""

        plan = self.planner._parse_compact_schedule(
            response, request, candidates, route_itinerary
        )

        self.assertEqual([day.attractions[0].name for day in plan.days], ["中山陵", "拙政园", "外滩"])
        self.assertEqual(plan.days[0].attractions[0].poi_id, "POI-1")
        self.assertEqual(plan.days[0].attractions[0].address, "南京测试地址1")
        self.assertEqual(plan.days[0].attractions[0].location.longitude, 119)
        self.assertEqual(plan.days[0].attractions[0].rating, 4.7)
        self.assertEqual(plan.days[0].attractions[0].description, "上午游览")
        self.assertEqual(plan.days[0].meals, [])
        self.assertEqual(plan.days[0].date, "2026-08-01")

    def test_compact_schedule_rejects_candidates_from_wrong_route_stop(self):
        request = _request()
        candidates = [
            _candidate("拙政园", "苏州", 1),
            _candidate("外滩", "上海", 2),
        ]
        response = '{"days":[{"day_index":0,"items":[{"id":"C1"}]}]}'

        with self.assertRaises(TripPlanningError):
            self.planner._parse_compact_schedule(
                response,
                request,
                candidates,
                [{"overnight_city": "南京"}, {"overnight_city": "苏州"}, {"overnight_city": "上海"}],
            )

    def test_compact_prompt_requests_ids_instead_of_full_trip_objects(self):
        request = _request()
        prompt = self.planner._build_rich_planner_prompt(
            request,
            [_candidate("中山陵", "南京", 1)],
            "天气晴",
            [],
        )

        self.assertIn("C1 | 中山陵", prompt)
        self.assertIn('"items"', prompt)
        self.assertNotIn("ticket_price", prompt)
        self.assertNotIn("hotel 字段", prompt)

    def test_compact_prompt_lists_daily_route_candidate_whitelist(self):
        request = _request()
        candidates = [
            _candidate("中山陵", "南京", 1),
            _candidate("拙政园", "苏州", 2),
            _candidate("外滩", "上海", 3),
        ]
        route_itinerary = [
            {"day_index": 0, "activity_location": "南京"},
            {"day_index": 1, "activity_location": "苏州", "is_transit_day": True},
            {"day_index": 2, "activity_location": "上海"},
        ]

        prompt = self.planner._build_rich_planner_prompt(
            request, candidates, "天气晴", route_itinerary
        )

        self.assertIn("Day 1（南京）仅可选择: C1", prompt)
        self.assertIn("Day 2（苏州）仅可选择: C2；选1个核心候选", prompt)
        self.assertIn("Day 3（上海）仅可选择: C3", prompt)

    def test_misplaced_attractions_are_moved_to_matching_stop(self):
        request = _request(travel_days=3)
        plan = TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            trip_mode="multi_city",
            route_nodes=["南京", "苏州", "上海"],
            days=[
                DayPlan(
                    date="2026-08-01", day_index=0, description="南京",
                    transportation="公共交通", accommodation="酒店",
                    start_location="南京", end_location="南京", overnight_city="南京",
                    attractions=[_attraction("外滩")],
                ),
                DayPlan(
                    date="2026-08-02", day_index=1, description="苏州",
                    transportation="公共交通", accommodation="酒店",
                    start_location="苏州", end_location="苏州", overnight_city="苏州",
                ),
                DayPlan(
                    date="2026-08-03", day_index=2, description="上海",
                    transportation="公共交通", accommodation="酒店",
                    start_location="上海", end_location="上海", overnight_city="上海",
                    attractions=[_attraction("中山陵")],
                ),
            ],
        )
        candidates = [
            {
                "area_name": "中山陵",
                "display_name": "中山陵",
                "members": [{"name": "中山陵", "route_stop": "南京"}],
            },
            {
                "area_name": "外滩",
                "display_name": "外滩",
                "members": [{"name": "外滩", "route_stop": "上海"}],
            },
        ]

        result = self.planner._enforce_route_attraction_consistency(
            plan, request, candidates
        )

        self.assertEqual([a.name for a in result.days[0].attractions], ["中山陵"])
        self.assertEqual([a.name for a in result.days[2].attractions], ["外滩"])

    def test_route_backfill_only_uses_candidates_for_daily_activity_stop(self):
        request = _request()
        plan = TripPlan(
            city="西藏",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date=f"2026-08-0{index + 1}",
                    day_index=index,
                    description="测试",
                    transportation="自驾",
                    accommodation="酒店",
                )
                for index in range(3)
            ],
        )
        candidates = [
            _candidate("羊卓雍措景区", "羊卓雍措", 1),
            _candidate("扎什伦布寺", "日喀则", 2),
            _candidate("班戈江龙玛曲国家湿地公园", "班戈", 3),
        ]
        route_itinerary = [
            {
                "day_index": index,
                "activity_location": stop,
                "overnight_city": stop,
                "is_transit_day": True,
            }
            for index, stop in enumerate(["羊卓雍措", "日喀则", "班戈"])
        ]

        result = self.planner._dedup_attractions(
            plan, candidates, route_itinerary
        )

        self.assertEqual(
            [[item.name for item in day.attractions] for day in result.days],
            [["羊卓雍措景区"], ["扎什伦布寺"], ["班戈江龙玛曲国家湿地公园"]],
        )

    def test_backfill_marks_scenic_group_aliases_atomically(self):
        request = _request()
        plan = TripPlan(
            city="西藏",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date=f"2026-08-0{index + 1}",
                    day_index=index,
                    description="测试",
                    transportation="自驾",
                    accommodation="酒店",
                )
                for index in range(3)
            ],
        )
        scenic_group = {
            "area_name": "羊卓雍措",
            "display_name": "羊卓雍措景区",
            "score": 6.4,
            "longitude": 90.61,
            "latitude": 29.19,
            "members": [
                {
                    "id": f"YAMDROK-{index}",
                    "name": name,
                    "route_stop": "羊卓雍措",
                    "longitude": 90.61,
                    "latitude": 29.19,
                    "score": 6.4 - index / 10,
                }
                for index, name in enumerate([
                    "羊卓雍措景区",
                    "羊卓雍措",
                    "羊卓雍错(羊湖)4号观景台",
                    "羊卓雍错(羊湖)2号朗布琪观景台",
                    "羊卓雍措乌巴拉观景台",
                ])
            ],
        }

        result = self.planner._dedup_attractions(plan, [scenic_group])

        all_names = [
            item.name for day in result.days for item in day.attractions
        ]
        self.assertEqual(all_names, ["羊卓雍措景区"])

    def test_final_dedup_treats_scenic_aliases_as_same_attraction(self):
        request = _request()
        plan = TripPlan(
            city="西藏",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-08-01",
                    day_index=0,
                    description="羊湖",
                    transportation="自驾",
                    accommodation="酒店",
                    attractions=[_attraction("羊卓雍措景区")],
                ),
                DayPlan(
                    date="2026-08-02",
                    day_index=1,
                    description="重复羊湖",
                    transportation="自驾",
                    accommodation="酒店",
                    attractions=[_attraction("羊卓雍措")],
                ),
            ],
        )
        scenic_group = {
            "area_name": "羊卓雍措",
            "display_name": "羊卓雍措景区",
            "members": [
                {"name": "羊卓雍措景区", "route_stop": "羊卓雍措"},
                {"name": "羊卓雍措", "route_stop": "羊卓雍措"},
            ],
        }

        result = self.planner._remove_duplicate_attractions(
            plan, [scenic_group]
        )

        self.assertEqual([a.name for a in result.days[0].attractions], ["羊卓雍措景区"])
        self.assertEqual(result.days[1].attractions, [])

    def test_large_explicit_parent_child_group_is_still_atomic(self):
        plan = TripPlan(
            city="济南",
            start_date="2026-08-01",
            end_date="2026-08-02",
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-08-01",
                    day_index=0,
                    description="游览动物园",
                    transportation="公共交通",
                    accommodation="酒店",
                    attractions=[_attraction("济南动物园")],
                ),
                DayPlan(
                    date="2026-08-02",
                    day_index=1,
                    description="重复内部场馆",
                    transportation="公共交通",
                    accommodation="酒店",
                    attractions=[_attraction("济南动物园-海洋馆")],
                ),
            ],
        )
        members = [{"name": "济南动物园"}, {"name": "济南动物园-海洋馆"}]
        members.extend({"name": f"济南动物园-{name}"} for name in (
            "熊猫馆", "狮虎山", "灵长类馆", "鸟类馆", "儿童乐园"
        ))
        candidate = {
            "area_name": "济南动物园",
            "display_name": "济南动物园",
            "members": members,
            "_strong_hierarchy": True,
        }

        result = self.planner._remove_duplicate_attractions(plan, [candidate])

        self.assertEqual([a.name for a in result.days[0].attractions], ["济南动物园"])
        self.assertEqual(result.days[1].attractions, [])

    def test_final_dedup_refreshes_stale_day_summary(self):
        plan = TripPlan(
            city="济南", start_date="2026-08-01", end_date="2026-08-02",
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-08-01", day_index=0, description="游览趵突泉",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[_attraction("趵突泉景区")],
                ),
                DayPlan(
                    date="2026-08-02", day_index=1,
                    description="上午趵突泉，下午千佛山",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[_attraction("趵突泉景区"), _attraction("千佛山风景区")],
                ),
            ],
        )

        result = self.planner._remove_duplicate_attractions(plan, [])

        self.assertEqual(
            [item.name for item in result.days[1].attractions], ["千佛山风景区"]
        )
        self.assertNotIn("趵突泉", result.days[1].description)
        self.assertIn("千佛山", result.days[1].description)

    def test_explicit_parent_child_names_create_strong_group(self):
        pois = [
            {"id": "ZOO", "name": "济南动物园", "score": 10},
            {"id": "AQUARIUM", "name": "济南动物园-海洋馆", "score": 9},
            *[
                {"id": f"CHILD-{index}", "name": f"济南动物园-{name}", "score": 8 - index}
                for index, name in enumerate((
                    "熊猫馆", "狮虎山", "灵长类馆", "鸟类馆", "儿童乐园"
                ))
            ],
        ]

        groups = self.planner._group_attractions_by_area(pois)

        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0]["_strong_hierarchy"])
        self.assertEqual(len(groups[0]["members"]), 7)

    def test_visit_duration_estimation_uses_type_and_preferences(self):
        self.assertEqual(
            self.planner._estimate_visit_duration("羊湖观景台"),
            60,
        )
        self.assertEqual(
            self.planner._estimate_visit_duration(
                "南京博物院", "博物馆展览", ["历史文化"]
            ),
            180,
        )
        self.assertEqual(
            self.planner._estimate_visit_duration(
                "大昭寺", "宗教场所", ["历史文化"]
            ),
            120,
        )
        self.assertEqual(
            self.planner._estimate_visit_duration(
                "纳木措国家风景区", "风景名胜", ["自然风光"], scenic_core=True
            ),
            270,
        )
        self.assertEqual(
            self.planner._estimate_visit_duration("济南融创乐园"),
            240,
        )
        self.assertEqual(
            self.planner._estimate_visit_duration(
                "大明湖景区", "风景名胜", ["自然风景"],
                {"_dist_zone": "市区", "_is_remote": False},
            ),
            180,
        )

    def test_daily_route_feasibility_separates_distant_high_effort_scenic_spots(self):
        self.planner._get_city_center = lambda city: (117.021, 36.661)
        request = _request(
            city="济南",
            start_date="2026-09-01",
            end_date="2026-09-03",
            travel_days=3,
            trip_mode="single_city",
            destinations=[],
        )

        def scenic(name: str, lng: float, lat: float) -> Attraction:
            item = _attraction(name)
            item.location = Location(longitude=lng, latitude=lat)
            item.category = "风景区"
            return item

        plan = TripPlan(
            city="济南",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-09-01", day_index=0, description="双山",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[
                        scenic("华山风景区", 117.111, 36.718),
                        scenic("千佛山风景区", 117.027, 36.637),
                    ],
                ),
                DayPlan(
                    date="2026-09-02", day_index=1, description="湖区",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[scenic("大明湖公园", 117.025, 36.675)],
                ),
                DayPlan(
                    date="2026-09-03", day_index=2, description="市中心",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[scenic("泉城广场", 117.021, 36.661)],
                ),
            ],
        )

        result = self.planner._enforce_daily_route_feasibility(
            plan, request, []
        )

        for day in result.days:
            names = {item.name for item in day.attractions}
            self.assertFalse({"华山风景区", "千佛山风景区"} <= names)
        self.assertEqual(
            sum(
                item.name in {"华山风景区", "千佛山风景区"}
                for day in result.days for item in day.attractions
            ),
            2,
        )

    def test_daily_route_feasibility_keeps_nearby_high_effort_spots_together(self):
        self.planner._get_city_center = lambda city: (117.000, 36.000)
        request = _request(
            city="测试市", end_date="2026-08-02", travel_days=2,
            trip_mode="single_city", destinations=[]
        )
        first = _attraction("甲山风景区")
        first.category = "风景区"
        first.location = Location(longitude=117.000, latitude=36.000)
        second = _attraction("乙山风景区")
        second.category = "风景区"
        second.location = Location(longitude=117.005, latitude=36.005)
        plan = TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date=request.start_date, day_index=0, description="邻近景区",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[first, second],
                ),
                DayPlan(
                    date=request.end_date, day_index=1, description="其他",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[_attraction("测试广场")],
                ),
            ],
        )

        result = self.planner._enforce_daily_route_feasibility(plan, request, [])

        self.assertEqual(
            {item.name for item in result.days[0].attractions},
            {"甲山风景区", "乙山风景区"},
        )

    def test_route_swap_never_moves_classic_attraction_to_short_departure_day(self):
        self.planner._get_city_center = lambda _city: (117.02, 36.66)
        request = _request(
            city="济南", start_date="2026-09-05", end_date="2026-09-08",
            travel_days=4, trip_mode="single_city", destinations=[],
            arrival_transport="高铁", arrival_time="13:00",
            departure_transport="高铁", departure_time="11:00",
        )

        def scenic(name: str, lng: float, lat: float) -> Attraction:
            item = _attraction(name)
            item.location = Location(longitude=lng, latitude=lat)
            item.category = "风景区"
            return item

        plan = TripPlan(
            city="济南", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-09-05", day_index=0, description="抵达日",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[
                        scenic("趵突泉景区", 117.015, 36.661),
                        scenic("黑虎泉", 117.033, 36.662),
                    ],
                ),
                DayPlan(
                    date="2026-09-06", day_index=1, description="完整日",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[scenic("大明湖景区", 117.017, 36.673)],
                ),
                DayPlan(
                    date="2026-09-07", day_index=2, description="完整日",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[scenic("千佛山风景名胜区", 117.027, 36.637)],
                ),
                DayPlan(
                    date="2026-09-08", day_index=3, description="离开日",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[scenic("华山风景区", 117.073, 36.729)],
                ),
            ],
        )

        result = self.planner._enforce_daily_route_feasibility(plan, request, [])

        self.assertEqual(
            ["华山风景区"],
            [item.name for item in result.days[-1].attractions],
        )
        self.assertIn(
            "千佛山风景名胜区",
            {item.name for item in result.days[2].attractions},
        )

    def test_real_route_gate_removes_unworkable_single_attraction_from_arrival_day(self):
        self.planner._get_city_center = lambda _city: (117.02, 36.66)
        self.planner._estimated_location_transfer_minutes = (
            lambda *_args, **_kwargs: 110
        )
        request = _request(
            city="济南", start_date="2026-09-05", end_date="2026-09-06",
            travel_days=2, trip_mode="single_city", destinations=[],
            arrival_transport="高铁", arrival_time="13:00",
        )
        attraction = _attraction("华山风景区")
        attraction.category = "风景区"
        attraction.location = Location(longitude=117.073, latitude=36.729)
        hotel = Location(longitude=117.014, latitude=36.672)
        plan = TripPlan(
            city="济南", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-09-05", day_index=0, description="抵达日",
                    transportation="公共交通", accommodation="酒店",
                    hotel={"name": "测试酒店", "location": hotel},
                    attractions=[attraction],
                ),
                DayPlan(
                    date="2026-09-06", day_index=1, description="完整日",
                    transportation="公共交通", accommodation="酒店",
                    hotel={"name": "测试酒店", "location": hotel},
                    attractions=[_attraction("测试广场")],
                ),
            ],
        )

        result = self.planner._enforce_daily_route_feasibility(
            plan, request, [], use_amap_routes=True
        )

        self.assertNotIn(
            "华山风景区", [item.name for item in result.days[0].attractions]
        )
        self.assertIn(
            "华山风景区", [item.name for item in result.days[1].attractions]
        )

    def test_departure_day_accounts_for_return_from_remote_scenic_spot(self):
        self.planner._get_city_center = lambda city: (117.020, 36.660)
        request = _request(
            city="济南", end_date="2026-08-02", travel_days=2,
            trip_mode="single_city", destinations=[], departure_time="17:30",
            departure_transport="高铁",
        )
        plaza = _attraction("泉城广场")
        plaza.location = Location(longitude=117.021, latitude=36.661)
        canyon = _attraction("青铜山大峡谷")
        canyon.category = "风景区"
        canyon.location = Location(longitude=117.430, latitude=36.500)
        plan = TripPlan(
            city="济南", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-08-01", day_index=0, description="市区",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[plaza],
                ),
                DayPlan(
                    date="2026-08-02", day_index=1, description="返程日前往峡谷",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[canyon],
                ),
            ],
        )

        result = self.planner._enforce_daily_route_feasibility(plan, request, [])

        self.assertEqual(result.days[-1].attractions[0].name, "泉城广场")

    def test_departure_day_rechecks_remote_distance_from_final_coordinates(self):
        self.planner._get_city_center = lambda city: (117.020, 36.660)
        request = _request(
            city="济南", end_date="2026-08-02", travel_days=2,
            trip_mode="single_city", destinations=[], departure_time="17:30",
            departure_transport="高铁",
        )
        remote = _attraction("未标记远郊的山水画廊")
        remote.location = Location(longitude=117.400, latitude=36.500)
        plan = TripPlan(
            city="济南", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-08-01", day_index=0, description="市区",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[_attraction("泉城广场")],
                ),
                DayPlan(
                    date="2026-08-02", day_index=1, description="返程日远郊",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[remote],
                ),
            ],
        )

        result = self.planner._enforce_time_constraints(plan, request, [])

        self.assertEqual([], result.days[-1].attractions)

    def test_restored_attraction_name_also_syncs_real_poi_coordinates(self):
        attraction = _attraction("大明湖")
        attraction.location = Location(longitude=0.1, latitude=0.2)
        plan = TripPlan(
            city="济南", start_date="2026-08-01", end_date="2026-08-01",
            overall_suggestions="测试",
            days=[DayPlan(
                date="2026-08-01", day_index=0, description="测试",
                transportation="公共交通", accommodation="酒店",
                attractions=[attraction],
            )],
        )
        candidate = _candidate("大明湖景区", "济南", 1)
        candidate["area_name"] = "大明湖"
        candidate["display_name"] = "大明湖景区"
        candidate["longitude"] = 117.032
        candidate["latitude"] = 36.675
        candidate["address"] = "大明湖路271号"
        candidate["members"][0].update({
            "longitude": 117.032,
            "latitude": 36.675,
            "address": "大明湖路271号",
        })

        result = self.planner._restore_attraction_names(plan, [candidate])
        restored = result.days[0].attractions[0]

        self.assertEqual("大明湖景区", restored.name)
        self.assertAlmostEqual(117.032, restored.location.longitude)
        self.assertAlmostEqual(36.675, restored.location.latitude)
        self.assertEqual("大明湖路271号", restored.address)

    def test_classic_coverage_replaces_supplement_candidates(self):
        request = _request(
            city="济南", trip_mode="single_city", destinations=[]
        )
        plan = TripPlan(
            city="济南", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date=f"2026-08-0{index + 1}", day_index=index,
                    description="补充景点", transportation="公共交通",
                    accommodation="酒店", attractions=[_attraction(f"补充景点{index + 1}")],
                )
                for index in range(3)
            ],
        )
        candidates = [
            {**_candidate("趵突泉景区", "济南", 1), "_candidate_tier": "classic"},
            {**_candidate("大明湖景区", "济南", 2), "_candidate_tier": "classic"},
            {**_candidate("千佛山风景区", "济南", 3), "_candidate_tier": "classic"},
        ]

        result = self.planner._ensure_classic_coverage(plan, request, candidates)
        names = {item.name for day in result.days for item in day.attractions}

        self.assertTrue({"趵突泉景区", "大明湖景区", "千佛山风景区"} <= names)

    def test_visit_duration_is_adjusted_to_arrival_day_window(self):
        request = _request(
            city="南京",
            start_date="2026-08-01",
            end_date="2026-08-01",
            travel_days=1,
            trip_mode="single_city",
            destinations=[],
            arrival_transport="火车",
            arrival_time="16:00",
        )
        plan = TripPlan(
            city="南京",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[DayPlan(
                date="2026-08-01",
                day_index=0,
                description="抵达后游览",
                transportation="公共交通",
                accommodation="酒店",
                attractions=[_attraction("玄武湖公园"), _attraction("老门东街")],
            )],
        )

        result = self.planner._apply_visit_duration_estimates(
            plan, request, []
        )

        self.assertEqual(
            [item.visit_duration for item in result.days[0].attractions],
            [90, 90],
        )

    def test_nine_am_departure_has_no_attractions(self):
        request = _request(
            city="南京",
            start_date="2026-08-03",
            end_date="2026-08-07",
            travel_days=5,
            trip_mode="single_city",
            destinations=[],
            arrival_transport="火车",
            departure_time="09:00",
        )
        plan = TripPlan(
            city="南京",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date=f"2026-08-0{index + 3}",
                    day_index=index,
                    description="早上返程前游览",
                    transportation="公共交通",
                    accommodation="酒店",
                    attractions=[_attraction("南京夫子庙")],
                )
                for index in range(5)
            ],
        )

        result = self.planner._enforce_time_constraints(plan, request, [])

        self.assertEqual([], result.days[-1].attractions)
        self.assertIn("不安排景点", result.days[-1].description)
        self.assertEqual(
            0,
            self.planner._route_day_target_count(None, 4, request),
        )

    def test_noon_departure_removes_amusement_park_and_followup_attraction(self):
        request = _request(
            city="济南",
            start_date="2026-09-03",
            end_date="2026-09-07",
            travel_days=5,
            trip_mode="single_city",
            destinations=[],
            arrival_transport="高铁",
            departure_time="12:00",
        )
        plan = TripPlan(
            city="济南",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date=f"2026-09-0{index + 3}",
                    day_index=index,
                    description="游览后返程",
                    transportation="公共交通",
                    accommodation="酒店",
                    attractions=(
                        [_attraction("济南融创乐园"), _attraction("泉城广场")]
                        if index == 4 else [_attraction("趵突泉")]
                    ),
                )
                for index in range(5)
            ],
        )

        result = self.planner._enforce_time_constraints(plan, request, [])

        self.assertEqual([], result.days[-1].attractions)
        self.assertIn("不安排固定景点", result.days[-1].description)
        self.assertEqual(
            0,
            self.planner._route_day_target_count(None, 4, request),
        )

    def test_departure_buffer_uses_departure_transport_not_arrival_transport(self):
        request = _request(
            city="济南",
            start_date="2026-09-03",
            end_date="2026-09-03",
            travel_days=1,
            trip_mode="single_city",
            destinations=[],
            arrival_transport="高铁",
            departure_transport="飞机",
            departure_time="12:00",
        )
        day = DayPlan(
            date="2026-09-03",
            day_index=0,
            description="返程",
            transportation="公共交通",
            accommodation="酒店",
        )

        self.assertEqual(0, self.planner._day_visit_capacity(request, day, 0))

    def test_evening_arrival_under_one_hour_has_no_attractions(self):
        request = _request(
            city="济南",
            start_date="2026-08-04",
            end_date="2026-08-07",
            travel_days=4,
            trip_mode="single_city",
            destinations=[],
            arrival_transport="高铁",
            arrival_time="18:25",
        )
        plan = TripPlan(
            city="济南",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date=f"2026-08-0{index + 4}",
                    day_index=index,
                    description="抵达后游览",
                    transportation="公共交通",
                    accommodation="酒店",
                    attractions=[_attraction("山东博物馆")],
                )
                for index in range(4)
            ],
        )

        result = self.planner._enforce_time_constraints(plan, request, [])

        self.assertEqual([], result.days[0].attractions)
        self.assertIn("不安排景点", result.days[0].description)
        self.assertEqual(
            0,
            self.planner._route_day_target_count(None, 0, request),
        )

    def test_late_arrival_filters_daytime_venue(self):
        request = _request(
            city="南京",
            start_date="2026-08-03",
            end_date="2026-08-05",
            travel_days=3,
            trip_mode="single_city",
            destinations=[],
            arrival_transport="自驾",
            arrival_time="15:30",
        )
        plan = TripPlan(
            city="南京",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-08-03",
                    day_index=0,
                    description="抵达后游览",
                    transportation="自驾",
                    accommodation="酒店",
                    attractions=[_attraction("南京博物院"), _attraction("老门东街区")],
                ),
                DayPlan(
                    date="2026-08-04",
                    day_index=1,
                    description="正常游览",
                    transportation="自驾",
                    accommodation="酒店",
                    attractions=[_attraction("玄武湖公园")],
                ),
                DayPlan(
                    date="2026-08-05",
                    day_index=2,
                    description="正常游览",
                    transportation="自驾",
                    accommodation="酒店",
                    attractions=[_attraction("中山陵")],
                ),
            ],
        )

        result = self.planner._enforce_time_constraints(plan, request, [])

        self.assertEqual(["老门东街区"], [a.name for a in result.days[0].attractions])

    def test_backfill_does_not_override_intentional_single_attraction_day(self):
        request = _request(
            city="南京",
            start_date="2026-08-03",
            end_date="2026-08-07",
            travel_days=5,
            trip_mode="single_city",
            destinations=[],
        )
        day_names = ["古鸡鸣寺", "中山陵", "南京博物院", "红山森林动物园", "侵华日军南京大屠杀遇难同胞纪念馆"]
        plan = TripPlan(
            city="南京",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date=f"2026-08-0{index + 3}",
                    day_index=index,
                    description="测试",
                    transportation="公共交通",
                    accommodation="酒店",
                    attractions=[_attraction(name)],
                )
                for index, name in enumerate(day_names)
            ],
        )
        candidates = [
            {
                **_candidate("南京牛首山文化旅游区", "南京", 1),
                "_is_remote": True,
                "_landmark_match": "牛首山",
            }
        ]
        candidates[0]["members"][0]["_is_remote"] = True

        result = self.planner._dedup_attractions(
            plan, candidates, request=request
        )

        self.assertEqual(
            [item.name for item in result.days[-1].attractions],
            ["侵华日军南京大屠杀遇难同胞纪念馆"],
        )
        self.assertFalse(any(
            item.name == "南京牛首山文化旅游区"
            for day in result.days for item in day.attractions
        ))

    def test_remote_rebalance_refreshes_moved_attraction_text(self):
        request = _request(
            city="南京",
            start_date="2026-08-03",
            end_date="2026-08-07",
            travel_days=5,
            trip_mode="single_city",
            destinations=[],
        )
        memorial = _attraction("侵华日军南京大屠杀遇难同胞纪念馆")
        memorial.description = "返程日仅安排室内场馆"
        days = [
            DayPlan(
                date=f"2026-08-0{index + 3}",
                day_index=index,
                description="单一室内纪念馆，预留充足返程时间" if index == 4 else "测试",
                transportation="公共交通",
                accommodation="酒店",
                attractions=(
                    [_attraction("古鸡鸣寺")]
                    if index == 0
                    else ([_attraction("南京牛首山文化旅游区"), memorial] if index == 4 else [_attraction(f"测试景点{index}")])
                ),
            )
            for index in range(5)
        ]
        plan = TripPlan(
            city="南京",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=days,
        )
        remote_candidate = {
            "area_name": "南京牛首山文化旅游区",
            "display_name": "南京牛首山文化旅游区",
            "members": [{
                "name": "南京牛首山文化旅游区",
                "route_stop": "南京",
                "_is_remote": True,
            }],
        }

        result = self.planner._enforce_remote_one_per_day(
            plan, [remote_candidate]
        )

        self.assertEqual(
            [item.name for item in result.days[-1].attractions],
            ["南京牛首山文化旅游区"],
        )
        self.assertIn("牛首山", result.days[-1].description)
        self.assertNotIn("室内纪念馆", result.days[-1].description)
        moved = next(
            item for item in result.days[0].attractions
            if item.name == "侵华日军南京大屠杀遇难同胞纪念馆"
        )
        self.assertNotIn("返程日", moved.description)
        self.assertIn("纪念馆", result.days[0].description)


if __name__ == "__main__":
    unittest.main()
