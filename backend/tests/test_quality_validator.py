"""行程质量校验器的回归测试。"""

import unittest

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import (
    Attraction, DayPlan, Hotel, Location, TicketInfo, TimelineItem,
    TripPlan, TripRequest,
)


def _attraction(name: str, longitude: float, latitude: float) -> Attraction:
    return Attraction(
        name=name,
        address="测试地址",
        location=Location(longitude=longitude, latitude=latitude),
        visit_duration=90,
        description="测试景点",
    )


class QualityValidatorTests(unittest.TestCase):
    def setUp(self):
        self.request = TripRequest(
            city="北京", start_date="2026-08-01", end_date="2026-08-02",
            travel_days=2, transportation="公共交通", accommodation="舒适型酒店",
        )

    def test_detects_duplicate_attraction_and_long_distance(self):
        plan = TripPlan(
            city="北京", start_date="2026-08-01", end_date="2026-08-02",
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-08-01", day_index=0, description="测试",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[
                        _attraction("故宫", 116.397128, 39.916527),
                        _attraction("上海外滩", 121.490317, 31.241701),
                    ],
                ),
                DayPlan(
                    date="2026-08-02", day_index=1, description="测试",
                    transportation="公共交通", accommodation="酒店",
                    attractions=[_attraction("故宫", 116.397128, 39.916527)],
                ),
            ],
        )

        codes = {issue.code for issue in MultiAgentTripPlanner._validate_trip_plan(plan, self.request)}

        self.assertIn("duplicate_attraction", codes)
        self.assertIn("long_intra_day_distance", codes)

    def test_full_day_with_one_short_attraction_is_blocking_issue(self):
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            travel_days=1, transportation="公共交通", accommodation="经济型酒店",
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            overall_suggestions="测试",
            days=[DayPlan(
                date="2026-09-04", day_index=0, description="测试",
                transportation="公共交通", accommodation="经济型酒店",
                attractions=[_attraction("单一短景点", 117.0, 36.6)],
            )],
        )

        issues = MultiAgentTripPlanner._validate_trip_plan(plan, request)

        issue = next(item for item in issues if item.code == "underfilled_full_day")
        self.assertEqual("error", issue.severity)

    def test_long_local_transport_is_not_reported_as_underfilled(self):
        request = TripRequest(
            city="婺源", start_date="2026-09-04", end_date="2026-09-04",
            travel_days=1, transportation="公共交通", accommodation="经济型酒店",
        )
        attraction = _attraction("思溪延村景区", 117.88, 29.42)
        attraction.visit_duration = 210
        plan = TripPlan(
            city="婺源", start_date="2026-09-04", end_date="2026-09-04",
            overall_suggestions="测试",
            days=[DayPlan(
                date="2026-09-04", day_index=0, description="测试",
                transportation="公共交通", accommodation="经济型酒店",
                attractions=[attraction],
                timeline=[
                    TimelineItem(
                        kind="transport", name="酒店 → 景区",
                        start_time="08:50", end_time="09:50",
                        duration_minutes=60, transport_mode="公共交通",
                    ),
                    TimelineItem(
                        kind="attraction", name=attraction.name,
                        start_time="09:50", end_time="13:20",
                        duration_minutes=210, reference_type="attraction",
                        reference_index=0,
                    ),
                    TimelineItem(
                        kind="transport", name="景区 → 酒店",
                        start_time="18:45", end_time="21:50",
                        duration_minutes=185, transport_mode="公共交通",
                    ),
                ],
            )],
        )

        codes = {
            issue.code for issue in MultiAgentTripPlanner._validate_trip_plan(
                plan, request
            )
        }

        self.assertNotIn("underfilled_full_day", codes)

    def test_timeline_after_closing_time_is_blocking_issue(self):
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            travel_days=1, transportation="公共交通", accommodation="经济型酒店",
        )
        attraction = _attraction("测试景区", 117.0, 36.6)
        attraction.visit_duration = 210
        attraction.ticket_info = TicketInfo(opening_hours="09:00-17:00")
        day = DayPlan(
            date="2026-09-04", day_index=0, description="测试",
            transportation="公共交通", accommodation="经济型酒店",
            attractions=[attraction],
            timeline=[TimelineItem(
                kind="attraction", name="测试景区", start_time="15:30", end_time="19:00",
                duration_minutes=210, reference_type="attraction", reference_index=0,
            )],
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            overall_suggestions="测试", days=[day],
        )

        codes = {item.code for item in MultiAgentTripPlanner._validate_trip_plan(plan, request)}

        self.assertIn("attraction_opening_conflict", codes)

    def test_four_day_trip_charges_three_hotel_nights(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-07",
            travel_days=4, transportation="公共交通", accommodation="经济型酒店",
        )
        days = [
            DayPlan(
                date=f"2026-09-0{4 + index}", day_index=index, description="",
                transportation="公共交通", accommodation="经济型酒店",
                hotel=Hotel(name="测试酒店", estimated_cost=180),
            )
            for index in range(4)
        ]
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-07",
            overall_suggestions="", days=days,
        )

        planner._recalculate_budget(plan, request)

        self.assertEqual(540, plan.budget.total_hotels)

    def test_arrival_attraction_that_cannot_finish_before_close_is_removed(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            travel_days=1, transportation="公共交通", accommodation="经济型酒店",
            arrival_transport="高铁", arrival_time="14:00",
        )
        attraction = _attraction("黄金谷景区", 117.07, 36.62)
        attraction.visit_duration = 210
        attraction.ticket_info = TicketInfo(opening_hours="09:00-17:00")
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店", attractions=[attraction],
            hotel=Hotel(
                name="测试酒店", location=Location(longitude=117.03, latitude=36.64)
            ),
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            overall_suggestions="", days=[day],
        )

        planner._repair_opening_hour_conflicts(plan, request)

        self.assertEqual([], day.attractions)

    def test_underfilled_day_is_backfilled_with_nearby_classic_candidate(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            travel_days=1, transportation="公共交通", accommodation="经济型酒店",
        )
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店",
            attractions=[_attraction("大明湖景区", 117.0167, 36.6726)],
            hotel=Hotel(
                name="测试酒店", location=Location(longitude=117.03, latitude=36.65)
            ),
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            overall_suggestions="", days=[day],
        )
        candidates = [{
            "display_name": "五龙潭公园", "area_name": "五龙潭公园",
            "longitude": 117.0105, "latitude": 36.6654,
            "address": "筐市街18号", "broad_category": "公园",
            "score": 9.2, "_candidate_tier": "classic", "members": [{
                "id": "WLTP", "name": "五龙潭公园", "longitude": 117.0105,
                "latitude": 36.6654, "address": "筐市街18号", "category": "公园",
            }],
        }]

        planner._backfill_underfilled_days(plan, request, candidates)

        self.assertEqual(["大明湖景区", "五龙潭公园"], [item.name for item in day.attractions])

    def test_underfilled_day_can_use_member_of_large_geo_cluster(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            travel_days=1, transportation="公共交通", accommodation="经济型酒店",
        )
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店",
            attractions=[_attraction("大明湖片区", 117.0167, 36.6726)],
            hotel=Hotel(
                name="测试酒店", location=Location(longitude=117.03, latitude=36.65)
            ),
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            overall_suggestions="", days=[day],
        )
        members = [
            {
                "id": f"POI-{index}", "name": name,
                "longitude": 117.011 + index * 0.0001,
                "latitude": 36.666 + index * 0.0001,
                "address": "测试地址", "category": "景点", "score": 9 - index * 0.1,
            }
            for index, name in enumerate((
                "五龙潭公园", "曲水亭街", "珍珠泉", "百花洲", "芙蓉街", "泉城广场"
            ))
        ]
        candidates = [{
            "display_name": "大明湖片区", "area_name": "大明湖片区",
            "longitude": 117.0167, "latitude": 36.6726,
            "score": 9.5, "_candidate_tier": "classic", "members": members,
        }]

        planner._backfill_underfilled_days(plan, request, candidates)

        self.assertEqual(2, len(day.attractions))
        self.assertEqual("五龙潭公园", day.attractions[1].name)


if __name__ == "__main__":
    unittest.main()
