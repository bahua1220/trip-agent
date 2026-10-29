"""南京单城市问题快照及联合排程回归；外部服务全部替身，不消耗额度。"""

import math
import unittest
from unittest.mock import Mock, patch

from app.agents.trip_planner_agent import MultiAgentTripPlanner, TripPlanningInfeasible
from app.models.schemas import Attraction, DayPlan, Hotel, Location, Meal, QualityReport, RemovedAttraction, TicketInfo, TimelineItem, TripPlan, TripRequest
from app.services.amap_service import AmapService


def point(lng=118.790392, lat=32.01904):
    return Location(longitude=lng, latitude=lat)


def visit(name, lng=118.79, lat=32.02, duration=90, hours="", description="测试游览"):
    return Attraction(name=name, address="测试地址", poi_id=name, location=point(lng, lat),
                      visit_duration=duration, description=description,
                      ticket_info=TicketInfo(opening_hours=hours, source="amap") if hours else None)


def meal(kind, name, lng=118.79, lat=32.02, hours="07:00-22:00"):
    return Meal(type=kind, name=name, location=point(lng, lat), poi_id=name, data_source="amap", opening_hours=hours)


class SingleCityScheduleRepairTests(unittest.TestCase):
    def setUp(self):
        self.planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        self.planner._call_amap_around_search = Mock(return_value=[])
        self.planner._resolve_transport_hub = Mock(return_value=None)
        self.planner._enrich_ticket_info = Mock(side_effect=lambda p: p)
        self.planner._route_choice_between_locations = Mock(side_effect=lambda a, b, *args, **kwargs: {
            "mode": "公共交通", "source": "amap", "alternatives": [],
            "duration_minutes": 0 if a == b else 10,
        })
        self.request = TripRequest(city="南京", start_date="2026-09-15", end_date="2026-09-18", travel_days=4,
                                   transportation="公共交通", accommodation="经济型酒店", trip_mode="single_city")

    def plan(self, attrs, meals, count=1):
        days = [DayPlan(date=f"2026-09-{15+i:02d}", day_index=i, description="测试",
                        transportation="公共交通", accommodation="经济型酒店",
                        hotel=Hotel(name="南京大富豪宾馆(夫子庙店)", address="夫子庙大石坝街69号", location=point()),
                        attractions=list(attrs), meals=[m.model_copy(deep=True) for m in meals]) for i in range(count)]
        return TripPlan(city="南京", start_date=self.request.start_date, end_date=self.request.end_date, days=days, overall_suggestions="测试")

    def test_late_lunch_does_not_delete_dinner_at_90_minute_visit_floor(self):
        core = visit("中山陵景区", 118.854097, 32.054508, hours="08:30-17:00")
        p = self.plan([core, visit("南京秦淮河画舫", duration=120, description="夜间体验最佳")],
                      [meal("breakfast", "回味鸭血粉丝汤"), meal("lunch", "皮肚面"), meal("dinner", "南京菜馆")])
        self.planner._route_choice_between_locations.side_effect = lambda a, b, *args, **kwargs: {
            "mode": "公共交通", "source": "amap", "alternatives": [],
            "duration_minutes": 0 if a == b else (90 if core.location in (a, b) else 10),
        }
        self.planner._enforce_final_timeline_constraints(p, self.request)
        self.assertEqual({"breakfast", "lunch", "dinner"}, {m.type for m in p.days[0].meals})
        self.assertEqual(2, len(p.days[0].attractions))
        lunch = next(n for n in p.days[0].timeline if n.meal_type == "lunch")
        self.assertLessEqual(self.planner._time_to_minutes(lunch.start_time), 840)
        boat = next(n for n in p.days[0].timeline if n.name == "南京秦淮河画舫" and n.kind == "attraction")
        self.assertGreaterEqual(self.planner._time_to_minutes(boat.start_time), 1080)

    def test_bathhouse_rejected_even_with_incorrect_restaurant_type(self):
        poi = dict(name="扬州搓背馆(建邺店)", type="餐饮服务;中餐厅", opening_hours="10:00-24:00")
        self.assertFalse(self.planner._is_valid_meal_candidate(poi, "dinner"))
        p = self.plan([visit("纪念馆")], [meal("dinner", poi["name"])])
        self.planner._enforce_final_timeline_constraints(p, self.request)
        self.assertFalse(any("搓背" in m.name for m in p.days[0].meals))

    def test_closed_replacement_cannot_introduce_non_restaurant(self):
        p = self.plan([], [meal("lunch", "关门饭店", hours="17:00-22:00")])
        self.planner._replace_closed_meal(p.days[0], 0, "lunch", 720, 780, {"meals": {"lunch": [
            dict(id="bath", name="扬州搓背馆", type="餐饮服务;中餐厅", opening_hours="10:00-24:00"),
        ]}})
        self.assertFalse(p.days[0].meals)

    def test_rejected_museum_is_not_blindly_backfilled_on_same_day(self):
        p = self.plan([visit("纪念馆", duration=150)], [])
        museum = visit("南京博物院", duration=150, hours="09:00-17:00")
        p.removed_attractions = [RemovedAttraction(attraction=museum, original_day_index=0, reason="开放时间冲突")]
        candidate = dict(display_name="南京博物院", area_name="南京博物院", members=[dict(
            id=museum.poi_id, name=museum.name, longitude=118.79, latitude=32.02, score=5, category="博物馆")])
        self.planner._backfill_underfilled_days(p, self.request, [candidate])
        self.assertEqual(["纪念馆"], [a.name for a in p.days[0].attractions])

    def test_new_backfill_is_checked_against_closing_time(self):
        p = self.plan([visit("上午景点", duration=180)], [])
        candidate = dict(display_name="午前闭馆博物馆", members=[dict(
            id="early", name="午前闭馆博物馆", longitude=118.79, latitude=32.02,
            score=5, category="博物馆", opening_hours="09:00-09:30")])
        self.planner._backfill_underfilled_days(p, self.request, [candidate])
        self.assertNotIn("午前闭馆博物馆", [a.name for a in p.days[0].attractions])

    def test_wait_is_explicit_and_transport_departs_after_break(self):
        p = self.plan([visit("纪念馆", duration=150)],
                      [meal("breakfast", "早餐"), meal("lunch", "午餐"), meal("dinner", "晚餐", 118.792)])
        self.planner._build_daily_timelines(p, self.request)
        timeline = p.days[0].timeline
        self.assertTrue(any(n.source == "planned_break" and n.duration_minutes >= 90 for n in timeline))
        dinner_index = next(i for i, n in enumerate(timeline) if n.meal_type == "dinner")
        self.assertEqual("transport", timeline[dinner_index - 1].kind)
        self.assertEqual("17:30", timeline[dinner_index - 1].end_time)
        self.assertEqual("planned_break", timeline[dinner_index - 2].source)

    def test_failed_relocation_rolls_back_day_objects_and_data(self):
        core = visit("超时博物馆", duration=600, hours="09:00-17:00")
        p = self.plan([core], [meal("breakfast", "早餐"), meal("lunch", "午餐"), meal("dinner", "晚餐")], count=2)
        p.days[1].attractions = [visit("目标景点", duration=600, hours="09:00-17:00")]
        self.planner._build_daily_timelines(p, self.request)
        original_objects = list(p.days)
        before = p.model_dump()
        self.assertFalse(self.planner._relocate_single_city_time_conflict(p, self.request, 0, 0))
        self.assertEqual(before, p.model_dump())
        self.assertTrue(all(a is b for a, b in zip(original_objects, p.days)))

    def test_final_gate_blocks_known_single_city_errors(self):
        p = self.plan([visit("南京博物院")], [meal("dinner", "扬州搓背馆")])
        p.quality_report = QualityReport(passed=False, issues=self.planner._validate_trip_plan(p, self.request))
        self.assertIn("invalid_meal_business", {i.code for i in p.quality_report.issues})
        with self.assertRaises(TripPlanningInfeasible):
            self.planner._assert_single_city_executable(p, self.request)

    def test_removed_attraction_meal_cannot_keep_orphan_remote_coordinates(self):
        removed = visit("已删远郊景点", 119.1, 32.2)
        food = meal("lunch", "远郊饭店", 119.1, 32.2)
        food.route_anchor_poi_id = removed.poi_id
        food.route_anchor_name = removed.name
        p = self.plan([visit("当前景点")], [food])
        p.removed_attractions = [RemovedAttraction(attraction=removed, original_day_index=0, reason="交通超时")]
        self.planner._build_daily_timelines(p, self.request)
        self.assertNotEqual(removed.location, p.days[0].meals[0].location)
        self.assertFalse(any("远郊饭店" in n.name for n in p.days[0].timeline))

    def test_restaurant_replacement_must_pass_joint_route_constraints(self):
        core = visit("上午景点", duration=150)
        advice = Meal(type="lunch", name="附近午餐", location=core.location, data_source="logistics_advice")
        p = self.plan([core], [meal("breakfast", "早餐"), advice, meal("dinner", "晚餐")])
        self.planner._build_daily_timelines(p, self.request)
        self.planner._rejected_final_meal_ids = {}
        replacement = meal("lunch", "绕路餐厅", 119.1, 32.2)
        self.planner._nearby_final_meal = Mock(return_value=replacement)
        self.planner._route_choice_between_locations.side_effect = lambda a, b, *args, **kwargs: dict(
            mode="公共交通", source="amap", alternatives=[], duration_minutes=180 if replacement.location in (a, b) else 10)
        self.planner._resolve_flexible_multi_city_meals(p, self.request, {})
        self.assertEqual("附近午餐", next(m.name for m in p.days[0].meals if m.type == "lunch"))
        self.assertIn(replacement.poi_id, self.planner._rejected_final_meal_ids[(0, "lunch")])

    def test_user_selected_restaurant_and_locked_visit_are_preserved(self):
        core = visit("用户锁定景点")
        core.user_locked = True
        manual = meal("dinner", "用户指定餐厅")
        manual.data_source = "user"
        p = self.plan([core], [manual])
        self.planner._enforce_final_timeline_constraints(p, self.request)
        self.assertEqual([core.name], [a.name for a in p.days[0].attractions])
        self.assertEqual("user", next(m.data_source for m in p.days[0].meals if m.type == "dinner"))

    def test_early_rail_departure_does_not_require_city_dinner(self):
        req = self.request.model_copy(update={"travel_days": 1, "departure_transport": "高铁", "departure_time": "17:00"})
        self.assertEqual(("breakfast", "lunch"), self.planner._available_meal_types(req, 0))
        # 本次首尾餐次策略只调整单城市，多城市仍使用现有规则。
        multi = req.model_copy(update={"trip_mode": "multi_city"})
        self.assertEqual(("breakfast", "lunch", "dinner"), self.planner._available_meal_types(multi, 0))

    def test_no_boundary_time_preserves_selected_hubs_without_fabricating_routes(self):
        req = self.request.model_copy(update={
            "arrival_location": "南京南站",
            "departure_location": "南京站",
            "arrival_time": None,
            "departure_time": None,
        })
        p = self.plan([visit("总统府")], [meal("lunch", "午餐"), meal("dinner", "晚餐")])
        self.planner._resolve_transport_hub.side_effect = lambda _request, arrival: {
            "name": "南京南站" if arrival else "南京站",
            "poi_id": "arrival" if arrival else "departure",
            "location": point(118.80, 32.08 if arrival else 32.05),
        }
        self.planner._build_daily_timelines(p, req)
        hubs = [item for item in p.days[0].timeline if item.kind == "hub"]
        self.assertEqual(
            [("南京南站", "trip_arrival"), ("南京站", "trip_departure")],
            [(item.name, item.hub_role) for item in hubs],
        )
        self.assertTrue(all(item.start_time is None for item in hubs))
        self.assertTrue(all(item.source == "user_selected_untimed" for item in hubs))
        hotel_start = next(item for item in p.days[0].timeline if item.kind == "hotel")
        self.assertEqual(0, hotel_start.duration_minutes)
        self.assertFalse(any(
            item.kind == "transport"
            and (item.from_name in {"南京南站", "南京站"}
                 or item.to_name in {"南京南站", "南京站"})
            for item in p.days[0].timeline
        ))
        issue_codes = {
            issue.code for issue in self.planner._validate_trip_plan(p, req)
        }
        self.assertNotIn("arrival_hub_unresolved", issue_codes)
        self.assertNotIn("departure_hub_unresolved", issue_codes)

    @patch.object(MultiAgentTripPlanner, "_current_day_ready_minutes", return_value=15 * 60)
    def test_same_day_explicit_arrival_cannot_schedule_before_now(self, _mock_ready):
        req = self.request.model_copy(update={
            "arrival_transport": "高铁",
            "arrival_time": "08:00",
        })
        self.assertEqual(15 * 60, self.planner._arrival_ready_minutes(req))
        self.assertEqual(("dinner",), self.planner._available_meal_types(req, 0))

    def test_final_metadata_matches_active_timeline(self):
        first = visit("总统府")
        second = visit("朝天宫")
        p = self.plan([first, second], [])
        p.days[0].timeline = [
            TimelineItem(kind="attraction", name=second.name, reference_index=1),
            TimelineItem(kind="attraction", name=first.name, reference_index=0),
        ]
        p.removed_attractions = [
            RemovedAttraction(attraction=second.model_copy(deep=True), original_day_index=0, reason="曾被调整")
        ]
        self.planner._sync_single_city_day_descriptions(p)
        self.planner._prune_readded_removed_attractions(p)
        self.assertLess(p.days[0].description.index(second.name), p.days[0].description.index(first.name))
        self.assertEqual([], p.removed_attractions)

    def test_relaxed_pace_caps_full_day_at_two_attractions(self):
        req = self.request.model_copy(update={"travel_pace": "relaxed"})
        p = self.plan(
            [visit("总统府"), visit("朝天宫"), visit("南京博物院")],
            [meal("breakfast", "早餐"), meal("lunch", "午餐"), meal("dinner", "晚餐")],
        )
        self.planner._enforce_final_timeline_constraints(p, req)
        self.assertLessEqual(len(p.days[0].attractions), 2)

    def test_balanced_pace_keeps_short_third_stop_when_day_span_fits(self):
        p = self.plan(
            [visit("总统府", duration=120), visit("中山陵", duration=90), visit("明孝陵", duration=90)],
            [meal("breakfast", "早餐"), meal("lunch", "午餐"), meal("dinner", "晚餐")],
        )
        self.planner._build_daily_timelines(p, self.request)
        self.planner._enforce_pace_timeline_limits(p, self.request)
        self.assertEqual(3, len(p.days[0].attractions))

    def test_pace_overflow_moves_night_stop_to_empty_day_before_removing_it(self):
        night = visit("老门东-步行街", duration=120, description="适合夜间街区漫步")
        p = self.plan([
            visit("大型景点甲", duration=300),
            visit("大型景点乙", duration=300),
            night,
        ], [], count=2)
        p.days[1].attractions = []
        self.planner._route_choice_between_locations.side_effect = lambda a, b, *args, **kwargs: {
            "mode": "公共交通", "source": "amap", "alternatives": [],
            "duration_minutes": 0 if a == b else 70,
        }
        self.planner._build_daily_timelines(p, self.request)
        self.planner._enforce_pace_timeline_limits(p, self.request)
        self.assertNotIn(night.name, [item.attraction.name for item in p.removed_attractions])
        self.assertIn(night.name, [item.name for item in p.days[1].attractions])

    def test_final_backfill_rejects_scenic_area_directional_plaza(self):
        p = self.plan([], [meal("dinner", "晚餐")])
        self.planner._build_daily_timelines(p, self.request)
        candidate = dict(display_name="中山陵南广场", area_name="中山陵南广场", score=10, members=[dict(
            id="south-plaza", name="中山陵南广场", longitude=118.852, latitude=32.054,
            score=10, category="风景名胜",
        )])
        self.planner._backfill_final_route_gaps(p, self.request, [candidate], None)
        self.assertEqual([], p.days[0].attractions)

    def test_lunch_route_detour_is_reanchored_before_it_delays_the_day(self):
        first = visit("明孝陵", 118.84, 32.05, duration=90)
        second = visit("中山陵", 118.85, 32.05, duration=90)
        remote_lunch = meal("lunch", "远处午餐", 118.95, 32.15)
        p = self.plan(
            [first, second],
            [meal("breakfast", "早餐"), remote_lunch, meal("dinner", "晚餐")],
        )
        self.planner._route_choice_between_locations.side_effect = lambda a, b, *args, **kwargs: {
            "mode": "公共交通", "source": "amap", "alternatives": [],
            "duration_minutes": 70 if remote_lunch.location in (a, b) else (0 if a == b else 10),
        }
        self.planner._enforce_final_timeline_constraints(p, self.request)
        lunch = next(item for item in p.days[0].meals if item.type == "lunch")
        self.assertEqual("logistics_advice", lunch.data_source)
        self.assertNotEqual(remote_lunch.name, lunch.name)

    def test_post_trim_meal_anchor_is_resolved_to_real_restaurant(self):
        kept = visit("玄武湖")
        removed = visit("老门东", 118.74, 32.06)
        stale = meal("dinner", "老门东南京菜", 118.74, 32.06)
        stale.route_anchor_name = removed.name
        stale.route_anchor_poi_id = removed.poi_id
        p = self.plan([kept], [meal("breakfast", "早餐"), meal("lunch", "午餐"), stale])
        p.removed_attractions = [RemovedAttraction(
            attraction=removed, original_day_index=0, reason="节奏裁剪",
        )]
        replacement = meal("dinner", "酒店旁南京菜", 118.7905, 32.0191)
        self.planner._nearby_final_meal = Mock(return_value=replacement)
        self.planner._rejected_final_meal_ids = {}
        self.planner._final_meal_search_cache = {}
        self.planner._resolve_post_trim_meals(p, self.request, {})
        dinner = next(item for item in p.days[0].meals if item.type == "dinner")
        self.assertEqual("amap", dinner.data_source)
        self.assertEqual(replacement.name, dinner.name)

    def test_unresolved_normal_city_meal_fails_quality_instead_of_silent_pass(self):
        advice = Meal(
            type="dinner", name="酒店附近晚餐（现场选择）",
            location=point(), data_source="logistics_advice",
        )
        p = self.plan([visit("总统府")], [
            meal("breakfast", "早餐"), meal("lunch", "午餐"), advice,
        ])
        self.planner._build_daily_timelines(p, self.request)
        codes = {issue.code for issue in self.planner._validate_trip_plan(p, self.request)}
        self.assertIn("unresolved_meal_recommendation", codes)

    def test_amap_opening_hours_removes_nested_schedule_noise(self):
        self.assertEqual(
            "07:30-17:30",
            AmapService._clean_opening_hours("初周一、15:05-17:30,17:15停止售票； 07:30-17:30"),
        )
        self.assertEqual(
            "10:30-14:00,16:30-21:00",
            AmapService._clean_opening_hours("10:30-14:00,16:30-21:00"),
        )
        self.assertEqual(
            "周一09:00-17:00；周二10:00-16:00",
            AmapService._clean_opening_hours("周一09:00-17:00；周二10:00-16:00"),
        )

    def test_opening_constraints_participate_in_coarse_route_order(self):
        museum = visit("南京博物院", duration=150, hours="09:00-12:00")
        park = visit("白天公园", duration=180)
        p = self.plan([park, museum], [], count=2)
        p.days[1].attractions = [visit("另一公园", duration=240)]
        self.planner._get_city_center = Mock(return_value=(118.79, 32.02))
        self.planner._estimate_visit_duration = Mock(side_effect=lambda name, *a: {museum.name:150, park.name:180, "另一公园":240}[name])
        self.planner._estimated_location_transfer_minutes = Mock(return_value=10)
        self.planner._enforce_daily_route_feasibility(p, self.request)
        self.assertEqual(museum.name, p.days[0].attractions[0].name)

    def test_daytime_description_rejecting_night_visit_is_not_night_constraint(self):
        self.assertFalse(self.planner._is_night_visit(visit("南京博物院", description="白天参观，不适合夜游")))

    def test_nanjing_four_day_snapshot_joint_repair(self):
        """复现用户快照的景点/餐厅坐标；缺失路段用明确的离线估算替身。"""
        p = self.plan([], [], count=4)
        attractions = [
            [visit("中山陵景区",118.854097,32.054508,90,"08:30-17:00"), visit("南京秦淮河画舫",118.789902,32.020086,120,"09:00-22:00","夜间秦淮河画舫体验最佳")],
            [visit("总统府",118.797398,32.044228,120,"08:30-18:00"), visit("老门东-步行街",118.787877,32.012641,120,description="傍晚逛街，夜间氛围好")],
            [visit("南京城墙博物馆",118.783016,32.012551,150,"09:00-17:00"), visit("南京雨花台风景区",118.781727,31.997896,180,"06:00-20:00")],
            [visit("侵华日军南京大屠杀遇难同胞纪念馆",118.742372,32.035217,150)],
        ]
        meals = [
            [meal("breakfast","回味鸭血粉丝汤",118.788353,32.018996), meal("lunch","袁福记天天大碗皮肚面",118.79474,32.023484)],
            [meal("breakfast","老新隆牛肉锅贴",118.791555,32.022343), meal("lunch","应天大明王朝南京菜",118.785701,32.013122,"11:00-22:00"), meal("dinner","堂屋·老南京菜馆",118.786519,32.01434,"11:00-15:30,17:00-21:30")],
            [meal("breakfast","鸡鸣汤包",118.789474,32.020927), meal("lunch","南京老卤面汤包鸭血粉丝汤",118.78077,32.010313,"10:00-21:00"), meal("dinner","南京大牌档",118.786385,32.011197,"11:00-14:30,16:30-22:00")],
            [meal("breakfast","叶新鸭血粉丝汤",118.78759,32.019529), meal("lunch","艺味深尝",118.743818,32.033271,"11:00-13:30,17:00-20:30"), meal("dinner","扬州搓背馆(建邺店)",118.744678,32.034125,"10:00-24:00")],
        ]
        for day, attrs, food in zip(p.days, attractions, meals):
            day.attractions, day.meals = attrs, food
        old_names = {a.name for d in p.days for a in d.attractions}
        def route(a, b, *args, **kwargs):
            distance = self.planner._haversine_distance(a.longitude, a.latitude, b.longitude, b.latitude)
            return dict(mode="公共交通", source="estimated", alternatives=[],
                        duration_minutes=0 if a == b else max(5, math.ceil((distance * 8 + (20 if distance > 1.5 else 0))/5)*5))
        self.planner._route_choice_between_locations.side_effect = route
        extra = dict(name="古鸡鸣寺", display_name="古鸡鸣寺", area_name="古鸡鸣寺", score=4.8,
                     members=[dict(id="temple", name="古鸡鸣寺", longitude=118.795246, latitude=32.061061, category="寺庙", score=4.8, opening_hours="07:00-17:30")])
        self.planner._enforce_final_timeline_constraints(p, self.request, attraction_candidates=[extra])
        issues = self.planner._validate_trip_plan(p, self.request)
        defects = {"missing_expected_meal", "invalid_meal_business", "lunch_too_late", "dinner_too_late", "attraction_opening_conflict", "night_visit_too_early", "underfilled_full_day"}
        self.assertFalse([(i.code, i.message) for i in issues if i.code in defects])
        self.assertTrue(old_names.issubset({a.name for d in p.days for a in d.attractions}))
        self.assertEqual(8, sum(len(d.attractions) for d in p.days))


if __name__ == "__main__":
    unittest.main()
