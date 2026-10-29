"""2026-10-29 real Nanjing findings, reduced to offline regressions."""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from real_eval_support import audit_plan
from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import (
    Attraction, DayPlan, Hotel, Location, Meal, TicketInfo, TripPlan, TripRequest, WeatherInfo,
)


def visit(name, poi_id, category="风景名胜"):
    return Attraction(name=name, poi_id=poi_id, address="南京",
        location=Location(longitude=118.839826, latitude=32.058692),
        visit_duration=90, description="真实问题缩减样本", category=category,
        ticket_info=TicketInfo(opening_hours="08:00-18:00", source="amap"))


def group(names, strong=True):
    return dict(area_name="明孝陵", display_name=names[0], _strong_hierarchy=strong,
        members=[dict(name=name, id=f"poi-{i}", score=10-i, category="风景名胜",
                      longitude=118.840463, latitude=32.051926, opening_hours="08:00-18:00")
                 for i, name in enumerate(names)], score=10)


class RealNanjingRegressionTests(unittest.TestCase):
    def setUp(self):
        self.planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        self.planner._route_choice_between_locations = Mock(side_effect=lambda a, b, *args, **kwargs: {
            "mode": "公共交通", "route_mode": "transit", "source": "amap", "alternatives": [],
            "duration_minutes": 0 if a == b else 10,
        })
        self.planner._resolve_transport_hub = Mock(return_value=None)
        self.planner._enrich_ticket_info = Mock(side_effect=lambda plan: plan)
        self.planner._timeline_hard_cost = Mock(return_value=0)
        self.planner._enforce_final_timeline_constraints = Mock()
        self.request = TripRequest(city="南京", start_date="2026-11-05", end_date="2026-11-05",
            travel_days=1, transportation="公共交通", accommodation="经济型酒店")

    def plan(self, attractions):
        location = Location(longitude=118.839826, latitude=32.058692)
        return TripPlan(city="南京", start_date=self.request.start_date, end_date=self.request.end_date,
            overall_suggestions="测试", days=[DayPlan(date=self.request.start_date, day_index=0,
                description="测试", transportation="公共交通", accommodation="经济型酒店",
                hotel=Hotel(name="测试酒店", location=location), attractions=attractions,
                meals=[Meal(type=kind, name=kind, poi_id=kind, data_source="amap", location=location,
                            opening_hours="07:00-22:00") for kind in ("breakfast", "lunch", "dinner")])])

    def test_final_backfill_cannot_reintroduce_other_poi_of_selected_scenic_group(self):
        plan = self.plan([visit("中山陵景区", "B0FFHVEECG"), visit("明孝陵景区-明孝陵", "B0FFHE3I6U")])
        self.planner._build_daily_timelines(plan, self.request)
        candidates = [group(["明孝陵景区-明孝陵", "明孝陵景区", "明孝陵博物馆"])]
        self.planner._backfill_final_route_gaps(plan, self.request, candidates, None)
        self.assertEqual(["中山陵景区", "明孝陵景区-明孝陵"], [a.name for a in plan.days[0].attractions])

    def test_final_backfill_keeps_unrelated_nearby_attraction_available(self):
        plan = self.plan([visit("中山陵景区", "a"), visit("明孝陵景区-明孝陵", "b")])
        self.planner._build_daily_timelines(plan, self.request)
        candidates = [group(["明孝陵景区-明孝陵", "明孝陵景区"]),
                      group(["灵谷景区"])]
        candidates[1]["area_name"] = "灵谷景区"
        self.planner._backfill_final_route_gaps(plan, self.request, candidates, None)
        self.assertIn("灵谷景区", [a.name for a in plan.days[0].attractions])
        self.assertNotIn("明孝陵景区", [a.name for a in plan.days[0].attractions])

    def test_production_validator_blocks_repeated_scenic_entity_with_different_ids(self):
        plan = self.plan([visit("明孝陵景区-明孝陵", "B0FFHE3I6U"), visit("明孝陵景区", "B0FFFFKG55")])
        issues = self.planner._validate_trip_plan(plan, self.request)
        self.assertTrue(any(i.code == "duplicate_scenic_entity" and i.severity == "error" for i in issues))

    def test_independent_audit_also_detects_repeated_scenic_entity(self):
        plan = self.plan([visit("明孝陵景区-明孝陵", "B0FFHE3I6U"), visit("明孝陵景区", "B0FFFFKG55")])
        checks = audit_plan(plan.model_dump(mode="json"), self.request.model_dump(mode="json"))
        self.assertTrue(any(i["code"] == "duplicate_scenic_entity" for i in checks["findings"]))

    def test_independent_scenic_subareas_are_not_name_aliases(self):
        plan = self.plan([visit("钟山风景区-中山陵", "a"), visit("钟山风景区-明孝陵", "b")])
        issues = self.planner._validate_trip_plan(plan, self.request)
        self.assertFalse(any(i.code == "duplicate_scenic_entity" for i in issues))

    def test_rain_warning_does_not_classify_bookstore_branch_name_as_mountain(self):
        plan = self.plan([visit("先锋书店(五台山总店)", "bookstore", "商业街区"),
                          visit("海南省博物馆", "museum", "博物馆")])
        plan.weather_info = [WeatherInfo(date=self.request.start_date, day_weather="小雨")]
        issues = self.planner._validate_trip_plan(plan, self.request)
        self.assertFalse(any(i.code == "weather_outdoor_risk" for i in issues))

    def test_rain_warning_still_covers_actual_outdoor_stops(self):
        plan = self.plan([visit("玄武湖公园", "park", "公园"), visit("五台山风景区", "mountain")])
        plan.weather_info = [WeatherInfo(date=self.request.start_date, day_weather="小雨")]
        warning = next(i for i in self.planner._validate_trip_plan(plan, self.request) if i.code == "weather_outdoor_risk")
        self.assertIn("玄武湖公园", warning.message)
        self.assertIn("五台山风景区", warning.message)

    def morning_gap_plan(self):
        plan = self.plan([visit("中山陵景区", "first"), visit("明孝陵景区-明孝陵", "second")])
        day = plan.days[0]
        # Reduced route matrix reproduces the real score tradeoff: the lunch-
        # first option saves 45 travel minutes but leaves 135 idle morning minutes.
        day.hotel.location = Location(longitude=118.79, latitude=32.05)
        day.attractions[0].location = Location(longitude=118.81, latitude=32.05)
        day.attractions[1].location = Location(longitude=118.82, latitude=32.05)
        day.attractions[1].ticket_info.opening_hours = "13:00-18:00"
        for meal, lng in zip(day.meals, (118.79, 118.80, 118.83)):
            meal.location = Location(longitude=lng, latitude=32.05)
        routes = {
            (118.79, 118.80): 30, (118.79, 118.81): 65, (118.79, 118.82): 80,
            (118.81, 118.80): 55, (118.82, 118.80): 50,
            (118.80, 118.81): 45, (118.80, 118.82): 45,
            (118.81, 118.82): 45, (118.82, 118.81): 45,
            (118.81, 118.83): 60, (118.82, 118.83): 55, (118.83, 118.79): 35,
        }
        self.planner._route_choice_between_locations.side_effect = lambda a, b, *args, **kwargs: {
            "mode": "公共交通", "source": "amap", "alternatives": [],
            "duration_minutes": 0 if a == b else routes.get((a.longitude, b.longitude), 60),
        }
        return plan

    def test_route_score_does_not_trade_entire_morning_for_slightly_less_travel(self):
        plan = self.morning_gap_plan()
        self.planner._build_daily_timelines(plan, self.request)
        nodes = plan.days[0].timeline
        first_visit = next(i for i, node in enumerate(nodes) if node.kind == "attraction")
        lunch = next(i for i, node in enumerate(nodes) if node.meal_type == "lunch")
        self.assertLess(first_visit, lunch)
        self.assertFalse(any(node.source == "planned_break" and node.duration_minutes >= 90 for node in nodes[:first_visit]))

    def test_route_score_keeps_lunch_first_when_all_venues_open_in_afternoon(self):
        plan = self.morning_gap_plan()
        plan.days[0].attractions[0].ticket_info.opening_hours = "13:00-18:00"
        self.planner._build_daily_timelines(plan, self.request)
        nodes = plan.days[0].timeline
        first_visit = next(i for i, node in enumerate(nodes) if node.kind == "attraction")
        lunch = next(i for i, node in enumerate(nodes) if node.meal_type == "lunch")
        self.assertLess(lunch, first_visit)
        self.assertLessEqual(self.planner._time_to_minutes(nodes[lunch].start_time), 840)


if __name__ == "__main__":
    unittest.main()
