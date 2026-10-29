"""旅行请求输入校验的回归测试。"""

import unittest

from pydantic import ValidationError

from app.models.schemas import TripRequest


def _payload(**overrides):
    data = {
        "city": "北京",
        "start_date": "2026-08-01",
        "end_date": "2026-08-03",
        "travel_days": 3,
        "transportation": "公共交通",
        "accommodation": "舒适型酒店",
    }
    data.update(overrides)
    return data


class TripRequestValidationTests(unittest.TestCase):
    def test_accepts_consistent_dates_and_times(self):
        request = TripRequest(**_payload(
            arrival_time="09:30",
            departure_transport="飞机",
            departure_time="18:05",
        ))
        self.assertEqual(request.travel_days, 3)
        self.assertEqual(request.departure_transport, "飞机")

    def test_travel_pace_is_independent_from_interest_preferences(self):
        request = TripRequest(**_payload(
            travel_pace="轻松慢游",
            preferences=["历史文化", "当地美食"],
        ))
        self.assertEqual("relaxed", request.travel_pace)
        self.assertEqual(["历史文化", "当地美食"], request.preferences)

    def test_legacy_pace_tag_is_migrated_and_removed_from_search_terms(self):
        request = TripRequest(**_payload(
            preferences=["博物馆", "紧凑行程"],
        ))
        self.assertEqual("compact", request.travel_pace)
        self.assertEqual(["博物馆"], request.preferences)

    def test_rejects_unknown_travel_pace(self):
        with self.assertRaises(ValidationError):
            TripRequest(**_payload(travel_pace="越多越好"))

    def test_rejects_invalid_date_format(self):
        with self.assertRaises(ValidationError):
            TripRequest(**_payload(start_date="2026/08/01"))

    def test_rejects_end_date_before_start_date(self):
        with self.assertRaises(ValidationError):
            TripRequest(**_payload(start_date="2026-08-03", end_date="2026-08-01"))

    def test_rejects_days_that_do_not_match_date_range(self):
        with self.assertRaises(ValidationError):
            TripRequest(**_payload(travel_days=2))

    def test_rejects_invalid_time_format(self):
        with self.assertRaises(ValidationError):
            TripRequest(**_payload(arrival_time="9:30"))

    def test_multi_city_requires_at_least_two_route_nodes(self):
        with self.assertRaises(ValidationError):
            TripRequest(**_payload(
                trip_mode="multi_city",
                destinations=["南京"],
            ))

    def test_road_trip_loop_forces_return_and_self_drive(self):
        request = TripRequest(**_payload(
            trip_mode="road_trip_loop",
            destinations=["西宁", "青海湖", "敦煌"],
            intercity_transport="high_speed_rail",
        ))
        self.assertTrue(request.return_to_start)
        self.assertEqual(request.end_location, "西宁")
        self.assertEqual(request.intercity_transport, "self_drive")
        self.assertEqual(request.transportation, "自驾")

    def test_rejects_unknown_intercity_transport(self):
        with self.assertRaises(ValidationError):
            TripRequest(**_payload(intercity_transport="subway"))


if __name__ == "__main__":
    unittest.main()
