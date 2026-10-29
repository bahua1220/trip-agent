"""区域路线推荐服务的回归测试。"""

import unittest
from unittest.mock import Mock, patch

from app.models.schemas import (
    RouteDraftRequest,
    RouteRecommendationRequest,
    RouteSegment,
    TripRequest,
)
from app.services.route_architect_service import RouteArchitectService


class RouteArchitectServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = RouteArchitectService()
        self.service._build_segment = (
            lambda origin, destination, max_hours, transport="self_drive": RouteSegment(
                origin=origin,
                destination=destination,
                transport_mode="自驾" if transport == "self_drive" else "高铁/动车",
                distance_km=180,
                duration_minutes=180,
                source="amap",
                requires_confirmation=transport != "self_drive",
                exceeds_daily_limit=180 > max_hours * 60,
            )
        )

    def test_recommends_closed_tibet_routes_without_user_nodes(self):
        options = self.service.recommend(RouteRecommendationRequest(
            region="西藏",
            travel_days=8,
            preferences=["自然风光"],
            max_drive_hours_per_day=6,
        ))

        self.assertGreaterEqual(len(options), 2)
        self.assertLessEqual(len(options), 3)
        self.assertEqual(options[0].nodes[0], options[0].nodes[-1])
        self.assertEqual(
            len(options[0].segments),
            len(options[0].nodes) - 1,
        )
        self.assertGreater(options[0].fit_score, 0)

    def test_custom_start_is_added_when_not_in_template(self):
        options = self.service.recommend(RouteRecommendationRequest(
            region="川西",
            travel_days=7,
            start_location="重庆",
        ))

        self.assertEqual(options[0].nodes[0], "重庆")
        self.assertTrue(any("接驳节点" in warning for warning in options[0].warnings))

    def test_rejects_unsupported_region(self):
        with self.assertRaises(ValueError):
            self.service.recommend(RouteRecommendationRequest(
                region="海南",
                travel_days=7,
            ))

    def test_recommends_open_jiangxi_multi_city_route_without_user_nodes(self):
        options = self.service.recommend(RouteRecommendationRequest(
            region="江西省",
            travel_days=5,
            trip_mode="multi_city",
            intercity_transport="auto",
            preferences=["人文历史", "特色美食"],
        ))

        self.assertGreaterEqual(len(options), 2)
        self.assertEqual(options[0].nodes[0], "南昌")
        self.assertNotEqual(options[0].nodes[0], options[0].nodes[-1])
        self.assertIn("景德镇", options[0].nodes)
        self.assertEqual(len(options[0].segments), len(options[0].nodes) - 1)
        self.assertEqual(options[0].segments[0].transport_mode, "高铁/动车")

    def test_validates_multi_city_route_without_closing_loop(self):
        result = self.service.validate_draft(RouteDraftRequest(
            region="江西",
            nodes=["南昌", "景德镇", "婺源"],
            travel_days=5,
            trip_mode="multi_city",
            intercity_transport="high_speed_rail",
        ))

        self.assertEqual(result.nodes, ["南昌", "景德镇", "婺源"])
        self.assertEqual(len(result.segments), 2)

    def test_validates_edited_nodes_and_closes_loop(self):
        result = self.service.validate_draft(RouteDraftRequest(
            region="西藏",
            nodes=["拉萨", "林芝", "山南"],
            travel_days=5,
            max_drive_hours_per_day=6,
        ))

        self.assertEqual(result.nodes, ["拉萨", "林芝", "山南", "拉萨"])
        self.assertEqual(len(result.segments), 3)
        self.assertIn("用户编辑", result.tags)
        self.assertEqual(result.warnings, [])

    def test_rejects_more_overnight_nodes_than_days(self):
        with self.assertRaises(ValueError):
            self.service.validate_draft(RouteDraftRequest(
                region="西藏",
                nodes=["拉萨", "林芝", "山南", "日喀则", "班戈"],
                travel_days=4,
            ))

    def test_trip_request_keeps_non_consecutive_hub_revisits(self):
        request = TripRequest(
            city="西藏",
            start_date="2026-08-01",
            end_date="2026-08-08",
            travel_days=8,
            transportation="自驾",
            accommodation="舒适型酒店",
            trip_mode="road_trip_loop",
            destinations=["拉萨", "林芝", "拉萨", "日喀则"],
        )

        self.assertEqual(
            request.destinations,
            ["拉萨", "林芝", "拉萨", "日喀则"],
        )

    def test_auto_route_uses_administrative_centers_for_ambiguous_city_name(self):
        service = RouteArchitectService()
        amap = Mock()
        amap.geocode_administrative_location.side_effect = {
            "丽江": (100.2278, 26.8550),
            "香格里拉": (99.7000, 27.8297),
        }.get
        amap.geocode_location.return_value = (121.4737, 31.2304)

        with patch(
            "app.services.route_architect_service.get_amap_service",
            return_value=amap,
        ):
            segment = service._build_segment(
                "丽江", "香格里拉", 6, "auto"
            )

        self.assertEqual(segment.transport_mode, "高铁/动车")
        self.assertLess(segment.distance_km, 300)
        amap.geocode_location.assert_not_called()

    def test_auto_route_rejects_impossible_same_region_flight_distance(self):
        service = RouteArchitectService()
        amap = Mock()
        amap.geocode_administrative_location.return_value = None
        amap.geocode_location.side_effect = [
            (100.2278, 26.8550),
            (121.4737, 31.2304),
        ]

        with patch(
            "app.services.route_architect_service.get_amap_service",
            return_value=amap,
        ):
            segment = service._build_segment(
                "丽江", "香格里拉", 6, "auto"
            )

        self.assertEqual(segment.transport_mode, "高铁/动车")
        self.assertEqual(segment.duration_minutes, 0)
        self.assertTrue(segment.requires_confirmation)


if __name__ == "__main__":
    unittest.main()
