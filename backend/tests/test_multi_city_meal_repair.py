"""最终餐饮选址、景点关联清理及景区住宿平衡的离线回归。"""

import unittest
from unittest.mock import Mock, patch

from app.agents.trip_planner_agent import MultiAgentTripPlanner, TripPlanningInfeasible
from app.models.schemas import Attraction, DayPlan, Hotel, Location, Meal, QualityIssue, QualityReport, RemovedAttraction, RouteSegment, TicketInfo, TripPlan, TripRequest


def request(**changes):
    values = dict(city="江西", start_date="2026-09-15", end_date="2026-09-17",
                  travel_days=3, transportation="公共交通", accommodation="经济型酒店",
                  trip_mode="multi_city", destinations=["南昌", "庐山"])
    values.update(changes)
    return TripRequest(**values)


def hotel(name="车站酒店", lng=115.8829, lat=29.5993):
    return Hotel(name=name, address="测试地址", location=Location(longitude=lng, latitude=lat))


def attraction(name="庐山三叠泉景区", lng=116.044, lat=29.5553, duration=270):
    return Attraction(name=name, address="景区地址", poi_id=name,
                      location=Location(longitude=lng, latitude=lat), visit_duration=duration,
                      description="测试", category="风景名胜")


def plan_for(req, **day_changes):
    days = [DayPlan(date=f"2026-09-{15 + i:02d}", day_index=i, description="测试",
                    transportation=req.transportation, accommodation=req.accommodation,
                    end_location="庐山", overnight_city="九江", hotel=hotel()) for i in range(3)]
    for key, value in day_changes.items():
        setattr(days[1], key, value)
    return TripPlan(city=req.city, start_date=req.start_date, end_date=req.end_date,
                    days=days, overall_suggestions="测试")


def poi(name="附近饭店", **changes):
    values = dict(id=name, name=name, address="测试街", type="餐饮服务;中餐厅",
                  longitude=115.883, latitude=29.5993, distance_m=100,
                  rating=4.5, rating_available=True, opening_hours="11:00-21:00")
    values.update(changes)
    return values


class MultiCityMealRepairTests(unittest.TestCase):
    def setUp(self):
        self.planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        self.planner._call_amap_around_search = Mock(return_value=[])
        self.planner._resolve_transport_hub = Mock(return_value=None)
        self.planner._get_city_center = Mock(side_effect=lambda city: (115.89, 28.68) if city == "南昌" else (116.04, 29.55))
        self.planner._route_choice_between_locations = Mock(side_effect=lambda first, second, *a, **k: {
            "mode": "公共交通", "route_mode": "transit", "source": "amap", "alternatives": [],
            "distance_km": 0 if first == second else 2,
            "duration_minutes": 0 if first == second else 10,
        })

    def test_removed_scenic_lunch_cannot_generate_orphan_bus_round_trip(self):
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                req = request()
                core = attraction()
                meal = Meal(type="lunch", name="景区内用餐或自备简餐", location=core.location,
                            data_source="logistics_advice", description=f"{core.name}周边未检索到餐厅",
                            route_anchor_name=None if legacy else core.name,
                            route_anchor_poi_id=None if legacy else core.poi_id)
                plan = plan_for(req, meals=[meal])
                plan.removed_attractions = [RemovedAttraction(attraction=core, original_day_index=1, reason="闭园冲突")]
                self.planner._sync_multi_city_meal_anchors(plan, req)
                self.planner._build_daily_timelines(plan, req)
                self.assertEqual(plan.days[1].hotel.location, meal.location)
                self.assertIn("车站酒店", meal.name)
                self.assertIsNone(meal.route_anchor_poi_id)
                self.assertFalse(any(n.kind == "transport" for n in plan.days[1].timeline))

    def test_user_selected_restaurant_is_not_removed_as_orphan(self):
        req = request()
        core = attraction()
        manual = Meal(type="lunch", name="用户指定餐厅", location=core.location, data_source="user")
        plan = plan_for(req, meals=[manual])
        plan.removed_attractions = [RemovedAttraction(attraction=core, original_day_index=1)]
        self.planner._sync_multi_city_meal_anchors(plan, req)
        self.assertEqual(core.location, manual.location)
        self.assertEqual("用户指定餐厅", manual.name)

    def test_generic_restaurant_research_uses_actual_period_and_cache(self):
        day = plan_for(request()).days[1]
        meal = Meal(type="lunch", name="灵活午餐", location=day.hotel.location,
                    data_source="logistics_advice", route_anchor_name=day.hotel.name)
        self.planner._call_amap_around_search.side_effect = [[], [
            poi("关门店", opening_hours="11:00-13:00"),
            poi("午后营业饭店", opening_hours="13:00-20:00"),
            poi("远处饭店", longitude=117.5),
        ]]
        replacement = self.planner._nearby_final_meal(day, meal, "九江", 13 * 60 + 35, 14 * 60 + 35)
        self.assertEqual("午后营业饭店", replacement.name)
        self.assertEqual("amap", replacement.data_source)
        self.assertTrue(replacement.opening_hours_verified)
        self.planner._nearby_final_meal(day, meal, "九江", 13 * 60 + 35, 14 * 60 + 35)
        self.assertEqual(2, self.planner._call_amap_around_search.call_count)
        self.assertEqual([1500, 3500], [c.kwargs["radius"] for c in self.planner._call_amap_around_search.call_args_list])
        self.assertTrue(all(c.kwargs["keywords"] == "餐厅" for c in self.planner._call_amap_around_search.call_args_list))

    def test_unknown_hours_regular_lunch_allowed_but_closed_and_snack_rejected(self):
        self.assertTrue(self.planner._is_valid_meal_candidate(poi(opening_hours=""), "lunch"))
        self.assertFalse(self.planner._is_valid_meal_candidate(poi(opening_hours="17:00-21:00"), "lunch"))
        self.assertFalse(self.planner._is_valid_meal_candidate(poi("绝味鸭脖"), "dinner"))
        self.assertFalse(self.planner._is_valid_meal_candidate(poi("私房菜", opening_hours=""), "breakfast"))

    def test_noon_transfer_lunch_anchor_is_hotel_not_distant_scenic_area(self):
        req = request()
        plan = plan_for(req, attractions=[attraction()], route_segment=RouteSegment(
            origin="南昌", destination="庐山", transport_mode="高铁", duration_minutes=150))
        anchor = self.planner._meal_anchor(plan.days[1], "lunch")
        self.assertEqual("车站酒店", anchor[2])
        self.assertAlmostEqual(115.8829, anchor[0])

    def test_real_origin_breakfast_survives_repeated_timeline_builds(self):
        req = request()
        origin = hotel("南昌酒店", lng=115.89, lat=28.68)
        meal = Meal(type="breakfast", name="南昌真实早餐店", poi_id="breakfast-poi",
                    location=Location(longitude=115.891, latitude=28.68), data_source="amap")
        plan = plan_for(req, meals=[meal], route_segment=RouteSegment(
            origin="南昌", destination="庐山", transport_mode="高铁", duration_minutes=120))
        plan.days[0].hotel = origin
        self.planner._choose_intercity_transfer_window = Mock(return_value=dict(
            departure_minutes=8 * 60 + 45, arrival_minutes=10 * 60 + 45, source="estimated", railway=None))
        for _ in range(2):
            self.planner._build_daily_timelines(plan, req)
        self.assertEqual("breakfast-poi", plan.days[1].meals[0].poi_id)
        self.assertEqual("南昌真实早餐店", plan.days[1].meals[0].name)

    def test_late_arrival_lunch_moves_before_uphill_trip(self):
        req = request()
        core = attraction("庐山如琴湖-忆琴亭", duration=60)
        plan = plan_for(req, attractions=[core], meals=[
            Meal(type="breakfast", name="早餐", location=hotel().location, data_source="logistics_advice"),
            Meal(type="lunch", name="山上餐厅", poi_id="uphill-lunch", location=core.location, data_source="amap"),
        ], route_segment=RouteSegment(origin="南昌", destination="庐山", transport_mode="高铁", duration_minutes=256))
        plan.days[0].hotel = hotel("南昌酒店", lng=115.89, lat=28.68)
        self.planner._choose_intercity_transfer_window = Mock(return_value=dict(
            departure_minutes=8 * 60 + 45, arrival_minutes=13 * 60 + 1, source="estimated", railway=None))
        self.planner._route_choice_between_locations.side_effect = lambda first, second, *a, **k: dict(
            mode="公共交通", route_mode="transit", source="amap", alternatives=[],
            duration_minutes=0 if first == second else 55)
        self.planner._enforce_final_timeline_constraints(plan, req)
        lunch = next(m for m in plan.days[1].meals if m.type == "lunch")
        self.assertEqual(plan.days[1].hotel.location, lunch.location)
        lunch_item = next(n for n in plan.days[1].timeline if n.meal_type == "lunch")
        self.assertEqual("13:21", lunch_item.start_time)
        self.assertEqual([core.name], [a.name for a in plan.days[1].attractions])

    def test_closing_conflict_trims_five_minutes_instead_of_deleting_core(self):
        req = request()
        core = attraction()
        core.ticket_info = TicketInfo(opening_hours="06:30-16:30", source="amap")
        h = hotel()
        plan = plan_for(req, attractions=[core], meals=[
            Meal(type="breakfast", name="早餐", location=h.location, data_source="logistics_advice"),
            Meal(type="lunch", name="景区内用餐或自备简餐", location=core.location,
                 data_source="logistics_advice", route_anchor_name=core.name, route_anchor_poi_id=core.poi_id),
            Meal(type="dinner", name="附近晚餐", location=h.location, data_source="logistics_advice"),
        ])
        def route(first, second, *a, **k):
            duration = 0 if first == second else (190 if second == core.location else 205)
            return dict(mode="公共交通", route_mode="transit", source="amap", alternatives=[], duration_minutes=duration)
        self.planner._route_choice_between_locations.side_effect = route
        self.planner._enforce_final_timeline_constraints(plan, req)
        self.assertEqual([core.name], [a.name for a in plan.days[1].attractions])
        visit = next(n for n in plan.days[1].timeline if n.kind == "attraction")
        self.assertEqual("16:30", visit.end_time)
        self.assertEqual(215, core.visit_duration)
        self.assertFalse(any(r.attraction.name == core.name for r in plan.removed_attractions))

    def test_final_empty_day_backfill_uses_same_stop_not_removed_core(self):
        req = request()
        core = attraction()
        plan = plan_for(req)
        plan.days[0].end_location = "南昌"
        plan.days[0].overnight_city = "南昌"
        plan.days[0].attractions = [attraction("南昌地标", lng=115.89, lat=28.68)]
        plan.removed_attractions = [RemovedAttraction(attraction=core, original_day_index=1)]
        candidates = [dict(route_stop="南昌", members=[dict(id="other", name="南昌景点", longitude=115.89, latitude=28.68)]),
                      dict(route_stop="庐山", members=[dict(id=core.poi_id, name=core.name, longitude=116.044, latitude=29.5553),
                          dict(id="backup", name="庐山博物馆", longitude=115.884, latitude=29.5993,
                               address=[], score=8, opening_hours="09:00-17:00")])]
        changed = self.planner._backfill_final_empty_multi_city_days(plan, req, candidates)
        self.assertTrue(changed)
        self.assertEqual(["庐山博物馆"], [a.name for a in plan.days[1].attractions])
        self.assertNotIn(core.name, [a.name for a in plan.days[1].attractions])

    def test_pavilion_does_not_inherit_whole_mountain_duration(self):
        duration = self.planner._estimate_visit_duration("庐山风景名胜区如琴湖-忆琴亭", "风景名胜", ["自然风景"], {"_is_remote": True}, True)
        self.assertEqual(60, duration)

    def test_single_city_sync_leaves_legacy_meals_unchanged(self):
        req = request(trip_mode="single_city", destinations=[])
        meal = Meal(type="lunch", name="原午餐", location=attraction().location, data_source="logistics_advice")
        plan = plan_for(req, meals=[meal])
        before = meal.model_dump()
        self.planner._sync_multi_city_meal_anchors(plan, req)
        self.assertEqual(before, meal.model_dump())

    def test_scenic_hotel_balances_full_days_and_noon_departure(self):
        req = request(departure_time="12:01", departure_transport="高铁")
        plan = plan_for(req, attractions=[attraction()])
        req.start_location = "南昌"
        req.end_location = "庐山"
        plan.days[0].overnight_city = "南昌"
        plan.days[0].end_location = "南昌"
        hub = hotel().location
        self.planner._resolve_transport_hub.return_value = dict(name="庐山站", location=hub)
        scenic = dict(name="景区酒店", lng=116.04, lat=29.5553, distance_km=1, distance_m=1000)
        station = dict(name="车站酒店", lng=115.8829, lat=29.5993, distance_km=1, distance_m=1000)
        self.planner._search_hotels_nearby = Mock(side_effect=lambda lng, lat, *a: [] if lng < 115.8 else ([station] if abs(lng - hub.longitude) < .001 else [scenic]))
        self.planner._estimate_hotel_price = Mock(return_value=180)
        self.planner._estimated_location_transfer_minutes = Mock(side_effect=lambda first, second, *a: (
            10 if abs(first.longitude - second.longitude) < .01 else 120))
        service = Mock()
        service.geocode_location.return_value = (116.04, 29.5553)
        with patch("app.agents.trip_planner_agent.get_amap_service", return_value=service):
            self.planner._select_route_hotels(plan, req)
        self.assertEqual("景区酒店", plan.days[1].hotel.name)
        self.assertEqual(plan.days[1].hotel, plan.days[2].hotel)
        req.departure_time = "08:00"
        with patch("app.agents.trip_planner_agent.get_amap_service", return_value=service):
            self.planner._select_route_hotels(plan, req)
        self.assertEqual("车站酒店", plan.days[1].hotel.name)

    def test_takeaway_internal_canteen_and_duck_head_are_not_full_meals(self):
        for name in ("员工食堂", "婺女洲员工食堂", "外卖专营厨房", "好吃饭店(无堂食)", "衢州鸭头"):
            with self.subTest(name=name):
                self.assertFalse(self.planner._is_valid_meal_candidate(poi(name), "dinner"))
        self.assertTrue(self.planner._is_valid_meal_candidate(poi("江湾农家饭店"), "lunch"))

    def test_street_address_and_authentic_label_are_not_local_food_evidence(self):
        ordinary = poi("普通餐厅", address="路1号")
        misleading = poi("正宗普通餐厅", address="九江老街巷弄里1号")
        self.assertEqual(self.planner._score_nearby_meal(ordinary, "九江"), self.planner._score_nearby_meal(misleading, "九江"))

    def test_research_prefers_grounded_local_dish_over_generic_chain(self):
        self.planner._prefer_local_meals = True
        self.planner._local_dish_catalogs = {"景德镇": {"breakfast": ["冷粉"]}}
        self.planner._call_amap_around_search.return_value = [
            poi("正宗兰州牛肉拉面", opening_hours="07:00-21:00", rating=5),
            poi("本地冷粉早餐店", opening_hours="07:00-21:00", rating=4),
        ]
        day = plan_for(request()).days[1]
        meal = Meal(type="breakfast", name="区域早餐", location=day.hotel.location)
        replacement = self.planner._nearby_final_meal(day, meal, "景德镇", 8 * 60, 8 * 60 + 45)
        self.assertEqual("本地冷粉早餐店", replacement.name)
        self.assertEqual("冷粉", replacement.recommended_dish)

    def test_late_logistics_dinner_does_not_research_ten_open_restaurants(self):
        req = request()
        plan = plan_for(req)
        day = plan.days[1]
        day.meals = [Meal(type="dinner", name="附近晚餐", location=day.hotel.location, data_source="logistics_advice")]
        self.planner._build_daily_timelines(plan, req)
        item = next(n for n in day.timeline if n.kind == "meal")
        item.start_time, item.end_time = "22:13", "23:28"
        self.planner._call_amap_around_search.return_value = [poi(f"餐厅{i}") for i in range(20)]
        self.assertFalse(self.planner._resolve_flexible_multi_city_meals(plan, req, {}, 1))
        self.planner._call_amap_around_search.assert_not_called()

    def test_long_nearby_scenic_visit_is_split_around_lunch(self):
        req = request()
        core = attraction(duration=270)
        plan = plan_for(req, hotel=hotel("景区酒店", 116.044, 29.5553), attractions=[core], meals=[
            Meal(type="breakfast", name="早餐", location=core.location, data_source="logistics_advice"),
            Meal(type="lunch", name="景区简餐", location=core.location, data_source="logistics_advice"),
            Meal(type="dinner", name="晚餐", location=core.location, data_source="logistics_advice"),
        ])
        self.planner._build_daily_timelines(plan, req)
        visits = [n for n in plan.days[1].timeline if n.kind == "attraction"]
        lunch = next(n for n in plan.days[1].timeline if n.meal_type == "lunch")
        self.assertEqual(2, len(visits))
        self.assertEqual(270, sum(n.duration_minutes for n in visits))
        self.assertLessEqual(visits[0].end_time, lunch.start_time)
        self.assertLessEqual(lunch.end_time, visits[1].start_time)

    def test_partial_day_remote_visit_swaps_to_feasible_full_day(self):
        req = request()
        core = attraction(duration=210)
        plan = plan_for(req, attractions=[core], route_segment=RouteSegment(origin="南昌", destination="庐山", transport_mode="高铁", duration_minutes=256))
        plan.days[0].hotel = hotel("南昌酒店", 115.89, 28.68)
        plan.days[0].end_location = plan.days[0].overnight_city = "南昌"
        plan.days[2].attractions = [attraction("市区小景点", 115.883, 29.5993, 60)]
        for day in plan.days:
            day.meals = [Meal(type=t, name=t, location=day.hotel.location, data_source="logistics_advice") for t in ("breakfast", "lunch", "dinner")]
        self.planner._choose_intercity_transfer_window = Mock(return_value=dict(departure_minutes=8 * 60 + 45, arrival_minutes=13 * 60 + 1, source="estimated", railway=None))
        self.planner._route_choice_between_locations.side_effect = lambda first, second, *a, **k: dict(
            mode="公共交通", route_mode="transit", source="amap", alternatives=[], duration_minutes=(145 if first == core.location or second == core.location else 10) if first != second else 0)
        self.planner._rejected_final_meal_ids = {}
        self.planner._move_partial_day_scenic_visits(plan, req)
        self.assertEqual(["市区小景点"], [a.name for a in plan.days[1].attractions])
        self.assertEqual([core.name], [a.name for a in plan.days[2].attractions])
        self.assertEqual(0, self.planner._timeline_hard_cost(plan.days[2], req, 2))

    def test_closed_removed_attraction_cannot_be_added_by_early_backfill(self):
        req = request()
        core = attraction("古窑民俗博览区", 115.884, 29.5993, 150)
        plan = plan_for(req)
        plan.removed_attractions = [RemovedAttraction(attraction=core, original_day_index=1, reason="闭园冲突")]
        candidates = [dict(route_stop="庐山", id=core.poi_id, name=core.name, longitude=115.884, latitude=29.5993, category="风景名胜", score=9)]
        self.planner._backfill_underfilled_days(plan, req, candidates)
        self.assertFalse(any(a.name == core.name for d in plan.days for a in d.attractions))

    def test_hard_conflict_blocks_delivery_but_single_city_and_warnings_do_not(self):
        req = request()
        plan = plan_for(req)
        plan.quality_report = QualityReport(passed=False, issues=[QualityIssue(code="departure_hub_late", severity="error", message="16:10到站，14:00发车")])
        with self.assertRaisesRegex(TripPlanningInfeasible, "14:00"):
            self.planner._assert_multi_city_executable(plan, req)
        self.planner._assert_multi_city_executable(plan, request(trip_mode="single_city"))
        plan.quality_report.issues[0].severity = "warning"
        self.planner._assert_multi_city_executable(plan, req)

    def test_late_previous_day_cannot_starve_last_day_opening_and_hub_repairs(self):
        req = request(departure_time="14:00", departure_transport="高铁")
        plan = plan_for(req, attractions=[attraction(duration=210)])
        plan.days[0].end_location = plan.days[0].overnight_city = "南昌"
        plan.days[2].end_location = plan.days[2].overnight_city = "上饶"
        hub = Location(longitude=118.0068, latitude=28.4905)
        self.planner._resolve_transport_hub.side_effect = lambda r, arrival: None if arrival else dict(name="上饶站", location=hub)
        plan.days[2].meals = [
            Meal(type="breakfast", name="06:30开门早餐店", poi_id="closed-breakfast", location=plan.days[2].hotel.location, opening_hours="06:30-21:00", data_source="amap"),
            Meal(type="lunch", name="午餐", location=plan.days[2].hotel.location, data_source="logistics_advice"),
        ]
        def route(first, second, *args, **kwargs):
            duration = 255 if second == hub else (145 if first == plan.days[1].attractions[0].location or second == plan.days[1].attractions[0].location else 0) if plan.days[1].attractions else 0
            return dict(mode="公共交通", route_mode="transit", source="amap", alternatives=[], duration_minutes=duration if first != second else 0)
        self.planner._route_choice_between_locations.side_effect = route
        self.planner._enforce_final_timeline_constraints(plan, req)
        last = plan.days[2]
        station = next(n for n in last.timeline if n.hub_role == "trip_departure")
        self.assertLessEqual(station.start_time, "13:00")
        self.assertTrue(any(n.kind == "note" and "途中午餐" in n.name for n in last.timeline))
        self.assertEqual(0, self.planner._timeline_hard_cost(last, req, 2))

    def test_final_gap_can_fill_arrival_afternoon_but_not_reintroduce_closed_poi(self):
        req = request(arrival_time="09:00", arrival_transport="高铁")
        plan = plan_for(req)
        existing = attraction("市区博物馆", 115.883, 29.5993, 90)
        existing.ticket_info = TicketInfo(opening_hours="09:00-17:00", source="amap")
        plan.days[0].attractions = [existing]
        blocked = attraction("古窑民俗博览区", 115.884, 29.5993, 150)
        plan.removed_attractions = [RemovedAttraction(attraction=blocked, original_day_index=0, reason="闭园冲突")]
        candidates = [dict(route_stop="庐山", members=[
            dict(id=blocked.poi_id, name=blocked.name, longitude=115.884, latitude=29.5993, opening_hours="09:00-17:00", score=10),
            dict(id="local-walk", name="市区历史街区", longitude=115.884, latitude=29.5993, category="商业街区", opening_hours="09:00-22:00", score=8),
        ])]
        self.planner._restore_missing_multi_city_meals(plan, req)
        self.planner._build_daily_timelines(plan, req)
        self.planner._rejected_final_meal_ids = {}
        self.planner._backfill_final_route_gaps(plan, req, candidates, None)
        self.assertEqual([existing.name, "市区历史街区"], [a.name for a in plan.days[0].attractions])
        self.assertEqual(0, self.planner._timeline_hard_cost(plan.days[0], req, 0))


if __name__ == "__main__":
    unittest.main()
