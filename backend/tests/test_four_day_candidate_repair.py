"""Noisy POIs + umbrella suppression + a sparse rainy full day; offline only."""

import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock

from app.agents.trip_planner_agent import MultiAgentTripPlanner, TripPlanningInfeasible
from app.models.schemas import Attraction, DayPlan, Hotel, Location, Meal, QualityIssue, QualityReport, TicketInfo, TripPlan, TripRequest, WeatherInfo


FIXTURE = json.loads((Path(__file__).parent / "fixtures/nanjing_four_day_failure.json").read_text(encoding="utf-8"))


def poi(name, identity, **changes):
    return dict(id=identity, name=name, longitude=118.79, latitude=32.04,
                address="南京测试地址", type="风景名胜", typecode="110000",
                rating=4.6, score=8, **changes)


class FourDayCandidateRepairTests(unittest.TestCase):
    def setUp(self):
        self.planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        self.planner._get_city_center = Mock(return_value=(118.79, 32.04))
        self.planner._route_choice_between_locations = Mock(side_effect=lambda a, b, *args, **kwargs: {
            "mode": "公共交通", "route_mode": "transit", "source": "amap", "alternatives": [],
            "duration_minutes": 0 if a == b else 10,
        })
        self.planner._resolve_transport_hub = Mock(return_value={
            "name": "南京南站", "location": Location(longitude=118.80, latitude=32.02),
        })
        self.planner._enrich_ticket_info = Mock(side_effect=lambda plan: plan)
        self.planner._call_amap_around_search = Mock(return_value=[])
        self.request = TripRequest(**FIXTURE["request"], start_date="2030-11-05", end_date="2030-11-08")

    def plan(self):
        days = []
        for i, names in enumerate(FIXTURE["after_route_repair"]):
            location = Location(longitude=118.79 + i*0.001, latitude=32.04)
            attrs = [Attraction(name=name, poi_id=name, address="南京", location=location,
                category="博物馆" if "博物" in name else "风景名胜", description="离线缩减样本",
                visit_duration=duration, ticket_info=TicketInfo(opening_hours="08:00-18:00", source="amap")) for name, duration in names]
            meals = [Meal(type=kind, name=f"餐厅-{i}-{kind}", poi_id=f"food-{i}-{kind}", data_source="amap",
                          location=location, opening_hours="06:00-23:00")
                     for kind in self.planner._available_meal_types(self.request, i)]
            days.append(DayPlan(date=(date(2030, 11, 5)+timedelta(days=i)).isoformat(), day_index=i,
                description="测试", transportation="公共交通", accommodation="经济型酒店",
                attractions=attrs, meals=meals, hotel=Hotel(name="测试酒店", location=location, estimated_cost=200)))
        return TripPlan(city="南京", start_date=self.request.start_date, end_date=self.request.end_date,
                        days=days, overall_suggestions="测试",
                        weather_info=[WeatherInfo(date=days[2].date, day_weather="毛毛雨")])

    def test_plain_restaurant_and_retail_cannot_enter_attraction_pool(self):
        for item in FIXTURE["mixed_hierarchy"][-2:]:
            self.assertTrue(self.planner._is_non_attraction_poi(item), item["name"])

    def test_cultural_bookstore_and_real_food_street_remain_eligible(self):
        for name, code, category in (("先锋书店(五台山总店)", "060400", "购物服务;书店"),
                                     ("夫子庙美食街", "050000", "餐饮服务;美食街")):
            self.assertFalse(self.planner._is_non_attraction_poi(dict(name=name, typecode=code, type=category)))

    def test_hidden_umbrella_does_not_delete_independent_children_via_alias_bridge(self):
        data = []
        for record in FIXTURE["mixed_hierarchy"]:
            item = poi(record["name"], record["id"])
            item.update(record)
            data.append(item)
        groups = self.planner._group_attractions_by_area(data)
        memberships = [{m["id"] for m in g["members"]} for g in groups]
        self.assertTrue(any("LAOMENDONG" in ids for ids in memberships))
        self.assertTrue(any("FUZIMIAO" in ids for ids in memberships))
        self.assertFalse(any({"LAOMENDONG", "FUZIMIAO"} <= ids for ids in memberships))
        self.assertFalse(any({"QINHUAI", "RESTAURANT", "SHOP"} & ids for ids in memberships))
        self.assertTrue(all(g["_candidate_tier"] == "classic" for g in groups))

    def test_common_three_character_city_prefix_is_not_identity_evidence(self):
        groups = self.planner._group_attractions_by_area([
            poi("南京老门东", "east"), poi("南京老门西", "west")])
        self.assertEqual(2, len(groups))

    def test_unique_candidate_budget_is_applied_after_alias_grouping(self):
        data = [poi(f"甲山景区-{suffix}", f"alias-{i}", _candidate_tier="classic")
                for i, suffix in enumerate([f"内部展馆{n}" for n in range(24)])]
        data.extend(poi(name, f"independent-{i}", _candidate_tier="local") for i, name in enumerate(
            ("乙地博物馆", "丙地美术馆", "丁地科技馆", "戊地古街", "己地公园", "庚地遗址", "辛地书院", "壬地故居")))
        groups = self.planner._rank_and_group_candidates(data, [], 4, "南京")
        self.assertGreaterEqual(len(groups), 8)
        self.assertIn("丙地美术馆", {g["display_name"] for g in groups})

    def test_full_day_rebalanced_from_three_to_one_without_losing_any_place(self):
        plan = self.plan()
        before = sorted(a.poi_id for day in plan.days for a in day.attractions)
        self.planner._build_daily_timelines(plan, self.request)
        self.planner._rebalance_single_city_full_days(plan, self.request)
        self.assertEqual([1, 2, 2, 0], [len(d.attractions) for d in plan.days])
        self.assertEqual(before, sorted(a.poi_id for day in plan.days for a in day.attractions))
        self.assertEqual(0, self.planner._timeline_hard_cost(plan.days[2], self.request, 2))

    def test_locked_donor_attractions_are_not_moved(self):
        plan = self.plan()
        for attraction in plan.days[1].attractions:
            attraction.user_locked = True
        self.planner._build_daily_timelines(plan, self.request)
        self.planner._rebalance_single_city_full_days(plan, self.request)
        self.assertEqual([1, 3, 1, 0], [len(d.attractions) for d in plan.days])

    def test_multi_city_is_not_rebalanced_by_single_city_rule(self):
        plan = self.plan()
        request = self.request.model_copy(update={"trip_mode": "multi_city", "destinations": ["南京", "苏州"]})
        before = plan.model_dump()
        self.planner._rebalance_single_city_full_days(plan, request)
        self.assertEqual(before, plan.model_dump())

    def test_rebalance_cannot_move_into_a_known_closed_window(self):
        plan = self.plan()
        for attraction in plan.days[1].attractions:
            attraction.ticket_info.opening_hours = "08:00-08:30"
        before = [[a.name for a in d.attractions] for d in plan.days]
        self.planner._build_daily_timelines(plan, self.request)
        self.planner._rebalance_single_city_full_days(plan, self.request)
        self.assertEqual(before, [[a.name for a in d.attractions] for d in plan.days])

    def test_rainy_underfilled_day_prefers_feasible_indoor_candidate(self):
        plan = self.plan()
        candidates = []
        for name, category, tier in (("室外甲公园", "风景名胜", "classic"), ("城市美术馆", "美术馆", "local")):
            record = poi(name, name, _candidate_tier=tier, category=category, broad_category=category, opening_hours="08:00-18:00")
            candidates.append(dict(display_name=name, area_name=name, score=10, _candidate_tier=tier,
                longitude=118.79, latitude=32.04, category=category, members=[record]))
        self.planner._backfill_underfilled_days(plan, self.request, candidates)
        self.assertIn("城市美术馆", [a.name for a in plan.days[2].attractions])

    def test_lack_of_candidates_still_fails_quality_but_does_not_blame_trip_dates(self):
        plan = self.plan()
        plan.quality_report = QualityReport(passed=False, issues=[QualityIssue(
            code="underfilled_full_day", severity="error", message="第三天景点不足")])
        with self.assertRaises(TripPlanningInfeasible) as error:
            self.planner._assert_single_city_executable(plan, self.request)
        self.assertNotIn("请调整停留天数", str(error.exception))

    def test_full_final_pipeline_repairs_the_logged_four_day_distribution(self):
        plan = self.plan()
        self.planner._enforce_final_timeline_constraints(plan, self.request)
        self.planner._recalculate_budget(plan, self.request)
        issues = self.planner._validate_trip_plan(plan, self.request)
        self.assertEqual([1, 2, 2, 0], [len(d.attractions) for d in plan.days])
        self.assertFalse([i.model_dump() for i in issues if i.severity == "error"])

    def test_0701_departure_without_meals_still_includes_twenty_minute_checkout(self):
        plan = self.plan()
        self.planner._route_choice_between_locations.return_value = None
        self.planner._route_choice_between_locations.side_effect = lambda a, b, *args, **kwargs: {
            "mode": "公共交通", "route_mode": "transit", "source": "amap", "alternatives": [],
            "duration_minutes": 0 if a == b else 45,
        }
        self.planner._build_daily_timelines(plan, self.request, {3})
        nodes = plan.days[3].timeline
        checkout = next(n for n in nodes if n.kind == "hotel")
        transfer = next(n for n in nodes if n.kind == "transport")
        hub = next(n for n in nodes if n.hub_role == "trip_departure")
        self.assertEqual(20, checkout.duration_minutes)
        self.assertEqual(("04:56", "05:16"), (checkout.start_time, checkout.end_time))
        self.assertEqual(("05:16", "06:01"), (transfer.start_time, transfer.end_time))
        self.assertEqual("06:01", hub.start_time)

    def test_breakfast_departure_has_only_one_twenty_minute_checkout(self):
        plan = self.plan()
        request = self.request.model_copy(update={"departure_time": "10:00"})
        location = plan.days[3].hotel.location
        plan.days[3].meals = [Meal(type="breakfast", name="早餐店", poi_id="breakfast",
                                   location=location, opening_hours="06:00-22:00", data_source="amap")]
        self.planner._build_daily_timelines(plan, request, {3})
        checkouts = [n for n in plan.days[3].timeline if n.kind == "hotel" and n.duration_minutes >= 20]
        self.assertEqual(1, len(checkouts))
        self.assertEqual(20, checkouts[0].duration_minutes)

    def test_early_transit_is_not_presented_as_verified_operating_service(self):
        plan = self.plan()
        self.planner._build_daily_timelines(plan, self.request, {3})
        issues = self.planner._validate_trip_plan(plan, self.request)
        warning = next(i for i in issues if i.code == "transit_service_hours_unverified")
        self.assertEqual("warning", warning.severity)
        self.assertIn("首末班", warning.message)

    def test_daytime_public_transport_does_not_get_early_service_warning(self):
        plan = self.plan()
        request = self.request.model_copy(update={"departure_time": "17:00"})
        self.planner._build_daily_timelines(plan, request, {3})
        issues = self.planner._validate_trip_plan(plan, request)
        self.assertFalse(any(i.code == "transit_service_hours_unverified" for i in issues))


if __name__ == "__main__":
    unittest.main()
