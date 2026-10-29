"""用户确认的八条验收场景：固定候选/路线，无模型、地图或搜索调用。

覆盖真实的候选解析、最终联合排程、预算、校验及编辑重排。
不以这些离线结果代替线上模型的景点质量评测。
运行：python -m unittest discover -s tests -p test_trip_acceptance.py -v
"""

import json
import unittest
from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import Mock, patch

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import (
    Attraction, DayPlan, Hotel, Location, Meal, RouteSegment, TicketInfo,
    TransportAlternative, TripPlan, TripRequest,
)


def clock(value):
    if value is None:
        raise AssertionError("需要明确的计划时刻，实际为空")
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def point(index=0):
    return Location(longitude=118.78 + index * 0.004, latitude=32.02)


def attraction(name, index, duration=120, hours="09:00-18:00", category="景点"):
    return Attraction(
        name=name, address="离线样本地址", location=point(index), poi_id=name,
        visit_duration=duration, description="固定候选", category=category,
        ticket_info=TicketInfo(opening_hours=hours, source="amap", confidence="high"),
    )


def food(kind, name, index=0, hours="07:00-22:00"):
    return Meal(
        type=kind, name=name, location=point(index), poi_id=name,
        data_source="amap", opening_hours=hours, opening_hours_verified=True,
        estimated_cost=30,
    )


def request(days=2, **changes):
    start = date(2026, 11, 10)
    values = dict(
        city="南京", start_date=start.isoformat(),
        end_date=(start + timedelta(days=days - 1)).isoformat(), travel_days=days,
        transportation="公共交通", accommodation="经济型酒店",
        preferences=[], travel_pace="balanced", trip_mode="single_city",
    )
    values.update(changes)
    return TripRequest(**values)


def candidate(visit, tier="classic"):
    return dict(
        display_name=visit.name, area_name=visit.name, score=9,
        longitude=visit.location.longitude, latitude=visit.location.latitude,
        _candidate_tier=tier, _is_remote=False,
        members=[dict(
            id=visit.poi_id, name=visit.name, address=visit.address,
            longitude=visit.location.longitude, latitude=visit.location.latitude,
            score=9, rating=4.8, category=visit.category,
            broad_category=visit.category, _candidate_tier=tier,
            opening_hours=visit.ticket_info.opening_hours,
        )],
    )


class TripAcceptanceTests(unittest.TestCase):
    def setUp(self):
        # Any accidental outbound request fails the case, including code that
        # catches service errors and falls back. Check call_count in tearDown.
        self.network = self.enterContext(patch(
            "app.services.http_client.request_json",
            side_effect=AssertionError("离线验收禁止访问外部网络"),
        ))
        self.socket_connect = self.enterContext(patch(
            "socket.socket.connect", side_effect=AssertionError("离线验收禁止联网"),
        ))
        self.amap = self.enterContext(patch(
            "app.agents.trip_planner_agent.get_amap_service",
        )).return_value
        self.amap.get_route_summary.return_value = None
        self.amap.get_opening_hours.return_value = "09:00-18:00"
        self.amap.search_poi.return_value = []
        self.enterContext(patch(
            "app.agents.trip_planner_agent.query_ticket_info",
            return_value=TicketInfo(opening_hours="09:00-18:00", source="amap"),
        ))
        self.enterContext(patch.object(
            MultiAgentTripPlanner, "_current_day_ready_minutes", return_value=None,
        ))
        self.planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        self.planner._call_amap_around_search = Mock(return_value=[])
        self.planner._get_city_center = Mock(return_value=(118.78, 32.02))
        self.planner._route_choice_between_locations = Mock(side_effect=self.route)
        self.routes = {}
        self.plans = []

    def tearDown(self):
        self.assertEqual(0, self.network.call_count, "检测到外部请求，不能算离线通过")
        self.assertEqual(0, self.socket_connect.call_count, "检测到套接字连接")

    def route(self, first, second, *_args, **_kwargs):
        key = (round(first.longitude, 6), round(second.longitude, 6))
        minutes = self.routes.get(key)
        if minutes is None:
            # Non-uniform, direction-aware fixture; not every leg is 10 minutes.
            minutes = 0 if first == second else 5 + round(abs(first.longitude - second.longitude) * 500 / 5) * 5
        return dict(
            mode="公共交通", route_mode="transit", duration_minutes=minutes,
            distance_km=round(abs(first.longitude - second.longitude) * 95, 2),
            source="amap", alternatives=[
                dict(mode="步行", route_mode="walking", duration_minutes=15,
                     distance_km=0.8, source="amap"),
            ],
        )

    def route_time(self, first, second, minutes):
        self.routes[(round(first.longitude, 6), round(second.longitude, 6))] = minutes

    def plan(self, req, visits_by_day, meals_by_day=None):
        days = []
        for index in range(req.travel_days):
            meals = meals_by_day[index] if meals_by_day else [
                food(kind, f"第{index + 1}天{kind}店", 0)
                for kind in self.planner._available_meal_types(req, index)
            ]
            days.append(DayPlan(
                date=(date.fromisoformat(req.start_date) + timedelta(days=index)).isoformat(),
                day_index=index, description="离线验收", transportation=req.transportation,
                accommodation=req.accommodation,
                hotel=Hotel(name="测试酒店", address="离线地址", location=point(), estimated_cost=200),
                attractions=deepcopy(visits_by_day[index]), meals=deepcopy(meals),
            ))
        plan = TripPlan(
            city=req.city, start_date=req.start_date, end_date=req.end_date,
            trip_mode=req.trip_mode, days=days, overall_suggestions="离线验收",
            request_context=req,
        )
        self.plans.append(plan)
        return plan

    def finish(self, plan, req, candidates=None):
        self.planner._enforce_final_timeline_constraints(
            plan, req, attraction_candidates=candidates or [],
        )
        self.planner._recalculate_budget(plan, req)
        self.assert_schedule_valid(plan, req)
        return plan

    def assert_schedule_valid(self, plan, req):
        # Independent assertions, not only the production validator.
        seen = set()
        for day in plan.days:
            previous_end = None
            for node in day.timeline:
                if node.start_time is None:
                    continue
                start = clock(node.start_time)
                end = clock(node.end_time) if node.end_time else start
                self.assertGreaterEqual(end, start, node.name)
                self.assertEqual(end - start, node.duration_minutes, node.name)
                if previous_end is not None:
                    self.assertGreaterEqual(start, previous_end, node.name)
                previous_end = end
                if node.kind == "meal":
                    meal = day.meals[node.reference_index]
                    if meal.type == "lunch":
                        self.assertLessEqual(start, 840, meal.name)
                    if meal.type == "dinner":
                        self.assertLessEqual(start, 1230, meal.name)
                    self.assertIsNot(False, self.planner._opening_hours_cover_period(meal.opening_hours, start, end), meal.name)
                if node.kind == "attraction":
                    visit = day.attractions[node.reference_index]
                    self.assertIsNot(False, self.planner._opening_hours_cover_period(visit.ticket_info.opening_hours, start, end), visit.name)
            for visit in day.attractions:
                self.assertNotIn(visit.poi_id, seen)
                seen.add(visit.poi_id)
        errors = [i.model_dump() for i in self.planner._validate_trip_plan(plan, req) if i.severity == "error"]
        self.assertEqual([], errors)

    def test_01_defaults_keep_classics_meals_and_full_days(self):
        """01 空白抵离及兴趣：四天代表性景点、三餐、无重复无冲突。"""
        req = request(days=4)
        visits = [
            attraction("总统府", 1), attraction("南京博物院", 3),
            attraction("中山陵", 6), attraction("明孝陵", 7),
            attraction("朝天宫", -3), attraction("南京城墙博物馆", -1),
            attraction("玄武湖公园", 4), attraction("古鸡鸣寺", 5),
        ]
        candidates = [candidate(v) for v in visits]
        # Model response is frozen; actual compact parsing and all downstream
        # processing remain real. Selection quality of a live model is separate.
        raw = json.dumps({"days": [dict(day_index=i, items=[f"C{2*i+1}", f"C{2*i+2}"]) for i in range(4)]})
        plan = self.planner._parse_compact_schedule(raw, req, candidates)
        plan.request_context = req
        self.plans.append(plan)
        for index, day in enumerate(plan.days):
            day.hotel = Hotel(name="测试酒店", location=point())
            day.meals = [food(k, f"第{index+1}天{k}店") for k in ("breakfast", "lunch", "dinner")]
            for visit in day.attractions:
                visit.ticket_info = TicketInfo(opening_hours="09:00-18:00", source="amap")
        self.finish(plan, req, candidates)
        self.assertEqual({v.name for v in visits}, {a.name for d in plan.days for a in d.attractions})
        for day in plan.days:
            self.assertGreaterEqual(len(day.attractions), 2)
            self.assertEqual({"breakfast", "lunch", "dinner"}, {m.type for m in day.meals})

    def test_02_selected_hubs_without_times_are_untimed(self):
        """02 已选枢纽未填时刻：保留两端、不虚构接驳、不占用日程。"""
        req = request(days=3, arrival_transport="高铁", arrival_location="南京站",
                      arrival_location_longitude=point(15).longitude, arrival_location_latitude=32.02,
                      departure_transport="高铁", departure_location="南京南站",
                      departure_location_longitude=point(-15).longitude, departure_location_latitude=32.02)
        plan = self.plan(req, [[attraction(f"景点{i}甲", i+1), attraction(f"景点{i}乙", i+2)] for i in range(3)])
        no_hubs = plan.model_copy(deep=True)
        plain = req.model_copy(update={"arrival_location": None, "departure_location": None})
        self.planner._build_daily_timelines(no_hubs, plain)
        self.planner._build_daily_timelines(plan, req)
        for day, baseline in zip(plan.days, no_hubs.days):
            self.assertEqual([n.model_dump() for n in baseline.timeline],
                             [n.model_dump() for n in day.timeline if n.kind != "hub"])
        self.assertEqual("南京站", plan.days[0].timeline[0].name)
        self.assertEqual("南京南站", plan.days[-1].timeline[-1].name)
        for node in [plan.days[0].timeline[0], plan.days[-1].timeline[-1]]:
            self.assertIsNone(node.start_time)
            self.assertEqual(0, node.duration_minutes)
        self.assert_schedule_valid(plan, req)

    def test_03_noon_arrival_includes_access_then_lunch_then_visit(self):
        """03 12点到站：出站20+交通30+入住20，午餐先于景点。"""
        req = request(arrival_transport="高铁", arrival_time="12:00", arrival_location="南京南站",
                      arrival_location_longitude=point(20).longitude, arrival_location_latitude=32.02)
        self.route_time(point(20), point(), 30)
        plan = self.plan(req, [[attraction("下午展馆", 2)], [attraction("总统府", 3), attraction("朝天宫", 4)]])
        self.finish(plan, req)
        day = plan.days[0]
        nodes = [n for n in day.timeline if n.kind not in {"transport", "note"}]
        self.assertEqual(["hub", "hotel", "meal", "attraction", "meal", "hotel"], [n.kind for n in nodes])
        self.assertEqual("12:00", nodes[0].start_time)
        self.assertEqual("13:10", nodes[1].end_time)
        self.assertEqual("lunch", nodes[2].meal_type)
        self.assertTrue(any(n.kind == "transport" and n.from_name == "南京南站" and n.to_name == "测试酒店" and n.duration_minutes == 30 for n in day.timeline))
        self.assertEqual(1, len(day.attractions))

    def test_04_early_departure_keeps_breakfast_checkout_and_buffer(self):
        """04 10点高铁：早餐45+退房20+接驳35，最迟09点到站。"""
        req = request(departure_transport="高铁", departure_time="10:00", departure_location="南京南站",
                      departure_location_longitude=point(20).longitude, departure_location_latitude=32.02)
        self.route_time(point(), point(20), 35)
        plan = self.plan(req, [[attraction("总统府", 2), attraction("朝天宫", 3)], []])
        self.finish(plan, req)
        day = plan.days[-1]
        breakfast = next(n for n in day.timeline if n.meal_type == "breakfast")
        self.assertGreaterEqual(clock(breakfast.start_time), 420)
        self.assertEqual(45, breakfast.duration_minutes)
        station = next(n for n in day.timeline if n.hub_role == "trip_departure")
        self.assertLessEqual(clock(station.start_time), 540)
        checkout = [n for n in day.timeline if n.kind == "hotel" and clock(n.start_time) >= clock(breakfast.end_time)]
        self.assertTrue(any(n.duration_minutes >= 20 for n in checkout), "缺少整理行李及退房20分钟")
        self.assertEqual({"breakfast"}, {m.type for m in day.meals})
        self.assertEqual([], day.attractions)

    def test_05_late_museum_moves_or_becomes_backup_without_shortening(self):
        """05 15:30开始可用、17点闭馆、需120分钟：换日或备选。"""
        req = request(arrival_transport="已在此地", arrival_time="15:30")
        museum = attraction("固定闭馆博物馆", 2, hours="09:00-17:00", category="博物馆")
        street = attraction("晚间开放街区", 3, duration=90, hours="09:00-23:00")
        self.route_time(point(), museum.location, 20)
        plan = self.plan(req, [[museum, street], [attraction("次日景点", 4)]])
        self.finish(plan, req)
        self.assertNotIn(museum.name, [a.name for a in plan.days[0].attractions])
        retained = [a for d in plan.days for a in d.attractions if a.poi_id == museum.poi_id]
        backup = [r.attraction for r in plan.removed_attractions if r.attraction.poi_id == museum.poi_id]
        self.assertTrue(retained or backup)
        self.assertTrue(all(a.visit_duration >= 120 for a in retained + backup))
        self.assertIn(street.name, [a.name for d in plan.days for a in d.attractions])

    def test_06_replace_detour_restaurant_before_deleting_visits(self):
        """06 绕路饭店A与沿线饭店B：更换真实门店，保留两个景点。"""
        req = request(days=1)
        first, second = attraction("上午景点", 3), attraction("下午景点", 4)
        remote = food("lunch", "绕路饭店A", 30, "11:00-21:00")
        nearby = food("lunch", "沿线饭店B", 3, "11:00-21:00")
        for entity in [point(), first.location, second.location]:
            self.route_time(entity, remote.location, 45)
            self.route_time(remote.location, entity, 45)
        self.planner._call_amap_around_search.return_value = [dict(
            id=nearby.poi_id, name=nearby.name, type="餐饮服务;中餐厅", typecode="050100",
            longitude=nearby.location.longitude, latitude=nearby.location.latitude,
            opening_hours=nearby.opening_hours, rating=4.6, estimated_cost=30,
        )]
        plan = self.plan(req, [[first, second]], [[food("breakfast", "早餐店"), remote, food("dinner", "晚餐店")]])
        self.finish(plan, req)
        lunch = next(m for m in plan.days[0].meals if m.type == "lunch")
        self.assertEqual(nearby.poi_id, lunch.poi_id)
        self.assertEqual(nearby.location, lunch.location)
        self.assertEqual({first.name, second.name}, {a.name for a in plan.days[0].attractions})
        self.assertFalse(any(remote.name in n.name for n in plan.days[0].timeline))

    def test_07_afternoon_intercity_keeps_origin_activity_and_split_rail(self):
        """07 上午南昌、午后换城：三段交通，参考班次不当作已确认。"""
        req = request(days=2, city="江西", trip_mode="multi_city", destinations=["南昌", "景德镇"],
                      intercity_transport="high_speed_rail")
        origin = attraction("南昌上午景点", 2, duration=90)
        origin.location = Location(longitude=115.89, latitude=28.68)
        destination = attraction("景德镇晚间街区", 3, duration=60, hours="09:00-23:00")
        destination.location = Location(longitude=117.20, latitude=29.30)
        self.planner._get_city_center.side_effect = lambda city: (115.89, 28.68) if city == "南昌" else (117.20, 29.30)
        plan = self.plan(req, [[attraction("首日景点", 1)], [origin, destination]])
        for index, city in enumerate(["南昌", "景德镇"]):
            day = plan.days[index]
            day.overnight_city = day.end_location = city
            location = Location(longitude=115.89, latitude=28.68) if index == 0 else destination.location
            day.hotel = Hotel(name=city + "酒店", location=location)
            for meal in day.meals:
                meal.location = location
        plan.days[1].route_segment = RouteSegment(origin="南昌", destination="景德镇", transport_mode="高铁/动车", distance_km=200, duration_minutes=185)
        rail = dict(
            departure_hub=dict(name="南昌站", longitude=115.90, latitude=28.67),
            arrival_hub=dict(name="景德镇北站", longitude=117.21, latitude=29.31),
            duration_minutes=90, distance_km=200, type="2012", trip="D测试",
            source="amap", requires_confirmation=True,
        )
        self.amap.get_intercity_transit_summary.return_value = dict(duration_minutes=185, distance_km=200, source="amap", railway=rail)
        # Short city access/egress; railway duration is independently supplied.
        self.planner._route_choice_between_locations.side_effect = lambda a, b, *args, **kw: dict(
            mode="公共交通", route_mode="transit", duration_minutes=0 if a == b else 15,
            distance_km=0 if a == b else 2, source="amap", alternatives=[],
        )
        self.planner._build_daily_timelines(plan, req)
        day = plan.days[1]
        first_visit = next(n for n in day.timeline if n.kind == "attraction" and n.name == origin.name)
        rail_node = next(n for n in day.timeline if n.is_intercity)
        self.assertLessEqual(clock(first_visit.end_time), clock(rail_node.start_time))
        self.assertGreaterEqual(clock(rail_node.start_time), 13 * 60)
        self.assertEqual(("南昌站", "景德镇北站", 90), (rail_node.from_name, rail_node.to_name, rail_node.duration_minutes))
        self.assertIn("参考", rail_node.transport_mode)
        pairs = {(n.from_name, n.to_name) for n in day.timeline if n.kind == "transport"}
        self.assertIn(("南昌酒店", "南昌站"), pairs)
        self.assertIn(("景德镇北站", "景德镇酒店"), pairs)
        hotel = next(n for n in day.timeline if n.kind == "hotel" and n.name == "景德镇酒店")
        visit = next(n for n in day.timeline if n.name == destination.name and n.kind == "attraction")
        self.assertGreaterEqual(clock(hotel.start_time), clock(rail_node.end_time) + 20)
        self.assertGreaterEqual(clock(visit.start_time), clock(hotel.end_time))

    def test_08_reflow_preserves_manual_leg_and_reports_new_conflicts(self):
        """08 用户改步行再添加景点：保留该段方式，重算并检查闭馆。"""
        req = request(days=1)
        first = attraction("第一景点", 2, duration=90)
        first.user_locked = True  # Keep this case's unchanged-endpoint precondition.
        second = attraction("第二景点", 3, duration=90)
        plan = self.plan(req, [[first, second]])
        self.planner._build_daily_timelines(plan, req)
        leg = next(n for n in plan.days[0].timeline if n.kind == "transport" and n.to_name == first.name)
        original_pair = (leg.from_name, leg.to_name)
        leg.transport_mode, leg.duration_minutes, leg.customized = "步行", 15, True
        leg.alternatives = [TransportAlternative(mode="步行", route_mode="walking", duration_minutes=15, source="amap")]
        added = attraction("新增景点", 4, duration=60)
        added.user_locked = True
        plan.days[0].attractions.append(added)
        self.planner.reflow_edited_plan(plan, [0])
        kept = next(n for n in plan.days[0].timeline if (n.from_name, n.to_name) == original_pair)
        self.assertEqual("步行", kept.transport_mode)
        self.assertTrue(kept.customized)
        self.assertEqual(15, kept.duration_minutes)
        self.assert_schedule_valid(plan, req)
        # The miniapp clears derived timeline data while editing. Persisted
        # choices must survive an actual JSON/schema round trip as well.
        plan.days[0].timeline = []
        plan = TripPlan.model_validate_json(plan.model_dump_json())
        self.plans.append(plan)
        self.planner.reflow_edited_plan(plan, [0])
        kept = next(n for n in plan.days[0].timeline if (n.from_name, n.to_name) == original_pair)
        self.assertEqual(("步行", 15, True), (kept.transport_mode, kept.duration_minutes, kept.customized))
        # Keep the same endpoints but choose a long, otherwise representable
        # duration. A genuine new opening conflict must not disappear silently.
        kept.duration_minutes = 240
        first.ticket_info.opening_hours = "09:00-10:30"
        plan.days[0].attractions[0].ticket_info.opening_hours = "09:00-10:30"
        self.planner.reflow_edited_plan(plan, [0])
        kept = next(n for n in plan.days[0].timeline if (n.from_name, n.to_name) == original_pair)
        self.assertEqual(("步行", 240, True), (kept.transport_mode, kept.duration_minutes, kept.customized))
        self.assertFalse(plan.quality_report.passed)
        self.assertTrue(any(i.code == "attraction_opening_conflict" for i in plan.quality_report.issues))
        self.assertIn(added.name, [a.name for a in plan.days[0].attractions])


if __name__ == "__main__":
    unittest.main()
