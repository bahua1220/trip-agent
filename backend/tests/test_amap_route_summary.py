"""Tests for AMap intra-city route duration parsing."""

import unittest
from unittest.mock import Mock, patch

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import Attraction, DayPlan, Hotel, Location, Meal, RouteSegment, TicketInfo, TimelineItem, TripPlan, TripRequest
from app.services.amap_service import AmapService


class AmapRouteSummaryTests(unittest.TestCase):
    def setUp(self):
        # These are fixed-route unit tests, not live Tavily/cache integration.
        # Tests of research normalization override this default locally.
        self.enterContext(patch(
            "app.agents.trip_planner_agent.research_intercity_rail_duration",
            return_value=None,
        ))
        # 最终餐饮修复现在也支持单城市；本组路线测试不应请求真实餐厅 API。
        self.meal_search_patch = patch.object(MultiAgentTripPlanner, "_call_amap_around_search", return_value=[])
        self.meal_search_patch.start()
        self.addCleanup(self.meal_search_patch.stop)

    def test_unknown_museum_hours_do_not_allow_night_visit(self):
        attraction = Attraction(
            name="江西省博物馆",
            address="南昌市",
            location=Location(longitude=115.9, latitude=28.7),
            visit_duration=120,
            description="博物馆",
            category="博物馆",
        )

        self.assertEqual(
            10 * 60,
            MultiAgentTripPlanner._opening_fit_start(
                attraction, 10 * 60, 120
            ),
        )
        self.assertIsNone(
            MultiAgentTripPlanner._opening_fit_start(
                attraction, 20 * 60, 120
            )
        )

    def test_midnight_clipped_timeline_item_is_detected(self):
        item = TimelineItem(
            kind="attraction",
            name="江西省博物馆",
            start_time="23:59",
            end_time="23:59",
            duration_minutes=120,
            reference_type="attraction",
            reference_index=0,
        )

        self.assertTrue(
            MultiAgentTripPlanner._timeline_item_is_clipped_at_midnight(item)
        )

    def test_administrative_geocoder_falls_back_to_structured_geocode(self):
        service = AmapService()
        service._get = Mock(side_effect=[
            {"districts": []},
            {"geocodes": [{
                "formatted_address": "云南省迪庆藏族自治州香格里拉市",
                "level": "区县",
                "location": "99.701000,27.830000",
            }]},
        ])

        result = service.geocode_administrative_location("香格里拉")

        self.assertEqual((99.701, 27.83), result)
        self.assertEqual("geocode/geo", service._get.call_args_list[1].args[0])

    def test_detailed_route_uses_stable_shangrila_city_center(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)

        segment = planner._route_segment_summary(
            "丽江", "香格里拉", 6, "auto"
        )

        self.assertEqual("高铁/动车", segment.transport_mode)
        self.assertLess(segment.distance_km, 300)

    def test_long_city_scenic_name_is_not_remote_when_hotel_is_nearby(self):
        attraction = Attraction(
            name="大明湖景区", address="市中心",
            location=Location(longitude=117.026, latitude=36.675),
            visit_duration=270, description="城市经典", category="风景名胜",
        )
        day = DayPlan(
            date="2026-09-05", day_index=0, description="",
            transportation="公共交通", accommodation="经济型酒店",
            hotel=Hotel(
                name="市中心酒店",
                location=Location(longitude=117.020, latitude=36.670),
            ),
            attractions=[attraction],
        )

        self.assertFalse(MultiAgentTripPlanner._is_remote_day(day))

    def test_removed_attraction_is_kept_as_edit_recommendation(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        attraction = Attraction(
            name="千佛山风景区", address="经十一路",
            location=Location(longitude=117.025, latitude=36.636),
            visit_duration=180, description="城市经典",
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-05", end_date="2026-09-05",
            days=[], overall_suggestions="",
        )

        planner._record_removed_attraction(plan, attraction, 0, "时间不足")
        planner._record_removed_attraction(plan, attraction, 0, "重复记录")

        self.assertEqual(1, len(plan.removed_attractions))
        self.assertEqual("时间不足", plan.removed_attractions[0].reason)

    def test_transport_hub_options_filter_out_metro_stations(self):
        service = AmapService()
        service._get = Mock(return_value={"pois": [
            {
                "id": "RAIL-1", "name": "济南西站", "address": "齐州路",
                "location": "116.899,36.669", "type": "交通设施服务;火车站",
            },
            {
                "id": "METRO-1", "name": "济南西站地铁站", "address": "齐州路",
                "location": "116.900,36.670", "type": "交通设施服务;地铁站",
            },
        ]})

        result = service.search_transport_hubs("济南", "高铁")

        self.assertEqual(["济南西站"], [item["name"] for item in result])
        self.assertEqual("RAIL-1", result[0]["id"])

    def test_selected_hub_coordinates_skip_name_search(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04", travel_days=1,
            transportation="公共交通", accommodation="经济型酒店",
            arrival_transport="高铁", arrival_location="济南西站",
            arrival_location_poi_id="RAIL-1",
            arrival_location_longitude=116.899,
            arrival_location_latitude=36.669,
        )

        hub = planner._resolve_transport_hub(request, arrival=True)

        self.assertEqual("济南西站", hub["name"])
        self.assertEqual("RAIL-1", hub["poi_id"])
        self.assertEqual(116.899, hub["location"].longitude)

    def test_multi_city_hubs_fall_back_to_first_and_last_route_cities(self):
        from app.agents import trip_planner_agent as planner_module

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._transport_hub_cache = {}
        request = TripRequest(
            city="云南", start_date="2026-09-04", end_date="2026-09-06", travel_days=3,
            transportation="公共交通", accommodation="经济型酒店",
            trip_mode="multi_city", destinations=["昆明", "大理", "丽江"],
            start_location="昆明", end_location="丽江",
            arrival_transport="高铁", arrival_location="昆明南站",
            departure_transport="飞机", departure_location="丽江三义机场",
        )
        route_service = Mock()
        route_service.search_poi.side_effect = [
            [{"id": "KM", "name": "昆明南站", "type": "火车站", "lng": 102.86, "lat": 24.87}],
            [{"id": "LJ", "name": "丽江三义机场", "type": "飞机场", "lng": 100.25, "lat": 26.68}],
        ]
        original = planner_module.get_amap_service
        planner_module.get_amap_service = lambda: route_service
        try:
            planner._resolve_transport_hub(request, arrival=True)
            planner._resolve_transport_hub(request, arrival=False)
        finally:
            planner_module.get_amap_service = original

        self.assertEqual(
            [("昆明南站", "昆明"), ("丽江三义机场", "丽江")],
            [call.args for call in route_service.search_poi.call_args_list],
        )

    def test_multi_city_transfer_occupies_time_between_city_hotels(self):
        from app.agents import trip_planner_agent as planner_module

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._route_choice_between_locations = Mock(side_effect=(
            lambda first, second, *_args, **_kwargs: {
                "mode": "同一地点" if first == second else "步行",
                "route_mode": "none" if first == second else "walking",
                "duration_minutes": 0 if first == second else 10,
                "distance_km": 0 if first == second else 0.8,
                "source": "estimated" if first == second else "amap",
                "alternatives": [],
            }
        ))
        request = TripRequest(
            city="江西", start_date="2026-09-04", end_date="2026-09-05", travel_days=2,
            transportation="公共交通", accommodation="经济型酒店",
            trip_mode="multi_city", destinations=["南昌", "景德镇"],
            start_location="南昌", end_location="景德镇",
        )
        nanchang_hotel = Hotel(
            name="南昌酒店", address="南昌市中心",
            location=Location(longitude=115.89, latitude=28.68),
        )
        jingdezhen_hotel = Hotel(
            name="景德镇酒店", address="景德镇市中心",
            location=Location(longitude=117.18, latitude=29.27),
        )
        plan = TripPlan(
            city="江西", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-09-04", day_index=0, description="南昌",
                    transportation="公共交通", accommodation="经济型酒店",
                    hotel=nanchang_hotel,
                ),
                DayPlan(
                    date="2026-09-05", day_index=1, description="前往景德镇",
                    transportation="公共交通", accommodation="经济型酒店",
                    hotel=jingdezhen_hotel,
                    meals=[Meal(type="breakfast", name="原早餐店")],
                    start_location="南昌", end_location="景德镇",
                    overnight_city="景德镇",
                    route_segment=RouteSegment(
                        origin="南昌", destination="景德镇",
                        transport_mode="高铁/动车", distance_km=180,
                        duration_minutes=120, source="estimated",
                        requires_confirmation=True,
                    ),
                ),
            ],
        )

        route_service = Mock()
        route_service.get_intercity_transit_summary.return_value = None
        original = planner_module.get_amap_service
        planner_module.get_amap_service = lambda: route_service
        try:
            planner._build_daily_timelines(plan, request)
        finally:
            planner_module.get_amap_service = original

        timeline = plan.days[1].timeline
        self.assertEqual("南昌酒店", timeline[0].name)
        self.assertEqual("breakfast", timeline[1].meal_type)
        self.assertEqual("南昌酒店早餐或附近早餐", plan.days[1].meals[0].name)
        self.assertEqual("transport", timeline[2].kind)
        self.assertEqual(("08:45", "10:45"), (timeline[2].start_time, timeline[2].end_time))
        self.assertEqual(("南昌", "景德镇"), (timeline[2].from_name, timeline[2].to_name))
        self.assertEqual("景德镇酒店", timeline[3].name)
        self.assertEqual(("10:45", "11:05"), (timeline[3].start_time, timeline[3].end_time))

    def test_short_transfer_with_origin_activity_uses_afternoon_window(self):
        from app.agents import trip_planner_agent as planner_module

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._route_choice_between_locations = Mock(side_effect=(
            lambda first, second, *_args, **_kwargs: {
                "mode": "同一地点" if first == second else "步行",
                "route_mode": "none" if first == second else "walking",
                "duration_minutes": 0 if first == second else 10,
                "distance_km": 0 if first == second else 0.8,
                "source": "estimated" if first == second else "amap",
                "alternatives": [],
            }
        ))
        planner._get_city_center = lambda city: {
            "南昌": (115.89, 28.68), "景德镇": (117.18, 29.27),
        }.get(city)
        request = TripRequest(
            city="江西", start_date="2026-09-04", end_date="2026-09-05", travel_days=2,
            transportation="公共交通", accommodation="经济型酒店",
            trip_mode="multi_city", destinations=["南昌", "景德镇"],
            start_location="南昌", end_location="景德镇",
        )
        first = Attraction(
            name="滕王阁", address="南昌",
            location=Location(longitude=115.88, latitude=28.68),
            visit_duration=120, description="南昌景点",
        )
        continued = Attraction(
            name="八一广场", address="南昌",
            location=Location(longitude=115.90, latitude=28.67),
            visit_duration=120, description="南昌景点",
        )
        destination = Attraction(
            name="陶阳里", address="景德镇",
            location=Location(longitude=117.19, latitude=29.27),
            visit_duration=120, description="景德镇景点",
        )
        nanchang_hotel = Hotel(
            name="南昌酒店", address="南昌市中心",
            location=Location(longitude=115.89, latitude=28.68),
        )
        jingdezhen_hotel = Hotel(
            name="景德镇酒店", address="景德镇市中心",
            location=Location(longitude=117.18, latitude=29.27),
        )
        plan = TripPlan(
            city="江西", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-09-04", day_index=0, description="南昌",
                    transportation="公共交通", accommodation="经济型酒店",
                    hotel=nanchang_hotel, attractions=[first, continued],
                ),
                DayPlan(
                    date="2026-09-05", day_index=1, description="前往景德镇",
                    transportation="公共交通", accommodation="经济型酒店",
                    hotel=jingdezhen_hotel, attractions=[destination],
                    meals=[
                        Meal(type="breakfast", name="原早餐"),
                        Meal(type="lunch", name="原午餐"),
                        Meal(type="dinner", name="晚餐", location=jingdezhen_hotel.location),
                    ],
                    start_location="南昌", end_location="景德镇",
                    overnight_city="景德镇",
                    route_segment=RouteSegment(
                        origin="南昌", destination="景德镇",
                        transport_mode="高铁/动车", distance_km=180,
                        duration_minutes=120, source="estimated",
                        requires_confirmation=True,
                    ),
                ),
            ],
        )
        route_service = Mock()
        route_service.get_intercity_transit_summary.return_value = None
        original = planner_module.get_amap_service
        planner_module.get_amap_service = lambda: route_service
        try:
            planner._rebalance_transfer_day_attractions(plan, request)
            planner._build_daily_timelines(plan, request)
        finally:
            planner_module.get_amap_service = original

        self.assertEqual(["滕王阁"], [item.name for item in plan.days[0].attractions])
        self.assertEqual("八一广场", plan.days[1].attractions[0].name)
        transfer = next(
            item for item in plan.days[1].timeline
            if item.kind == "transport" and item.from_name == "南昌" and item.to_name == "景德镇"
        )
        self.assertEqual(("13:30", "15:30"), (transfer.start_time, transfer.end_time))
        self.assertEqual("南昌行程附近午餐（现场选择）", plan.days[1].meals[1].name)

    def test_noon_intercity_arrival_schedules_lunch_before_attraction(self):
        from app.agents import trip_planner_agent as planner_module

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._route_choice_between_locations = Mock(return_value={
            "mode": "步行",
            "route_mode": "walking",
            "duration_minutes": 10,
            "distance_km": 0.8,
            "source": "amap",
            "alternatives": [],
        })
        request = TripRequest(
            city="江西",
            start_date="2026-09-07",
            end_date="2026-09-08",
            travel_days=2,
            transportation="公共交通",
            accommodation="经济型酒店",
            trip_mode="multi_city",
            destinations=["南昌", "景德镇"],
            start_location="南昌",
            end_location="景德镇",
        )
        first_hotel = Hotel(
            name="南昌酒店",
            location=Location(longitude=115.89, latitude=28.68),
        )
        second_hotel = Hotel(
            name="景德镇酒店",
            location=Location(longitude=117.18, latitude=29.27),
        )
        attraction = Attraction(
            name="景德镇中国陶瓷博物馆",
            address="景德镇",
            location=Location(longitude=117.17, latitude=29.29),
            visit_duration=120,
            description="测试",
        )
        lunch = Meal(
            type="lunch",
            name="景德镇午餐",
            location=Location(longitude=117.18, latitude=29.28),
        )
        plan = TripPlan(
            city="江西",
            start_date=request.start_date,
            end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-09-07",
                    day_index=0,
                    description="南昌",
                    transportation="公共交通",
                    accommodation="经济型酒店",
                    hotel=first_hotel,
                ),
                DayPlan(
                    date="2026-09-08",
                    day_index=1,
                    description="景德镇",
                    transportation="公共交通",
                    accommodation="经济型酒店",
                    hotel=second_hotel,
                    attractions=[attraction],
                    meals=[lunch],
                    start_location="南昌",
                    end_location="景德镇",
                    overnight_city="景德镇",
                    route_segment=RouteSegment(
                        origin="南昌",
                        destination="景德镇",
                        transport_mode="高铁/动车",
                        distance_km=180,
                        duration_minutes=195,
                    ),
                ),
            ],
        )
        route_service = Mock()
        route_service.get_intercity_transit_summary.return_value = None
        original = planner_module.get_amap_service
        planner_module.get_amap_service = lambda: route_service
        try:
            planner._build_daily_timelines(plan, request)
        finally:
            planner_module.get_amap_service = original

        lunch_position = next(
            index for index, item in enumerate(plan.days[1].timeline)
            if item.kind == "meal" and item.meal_type == "lunch"
        )
        attraction_position = next(
            index for index, item in enumerate(plan.days[1].timeline)
            if item.kind == "attraction"
        )
        self.assertLess(lunch_position, attraction_position)
        self.assertLessEqual(
            plan.days[1].timeline[lunch_position].start_time,
            "14:00",
        )

    def test_intercity_transit_summary_uses_both_cities_and_time(self):
        service = AmapService()
        service._get = Mock(return_value={
            "status": "1",
            "route": {"transits": [
                {"duration": "10800", "distance": "190000"},
                {"duration": "9000", "distance": "180000"},
            ]},
        })

        result = service.get_intercity_transit_summary(
            "115.89,28.68", "117.18,29.27",
            "南昌", "景德镇", "2026-09-05", "13:30",
        )

        self.assertEqual(150, result["duration_minutes"])
        self.assertEqual(180.0, result["distance_km"])
        params = service._get.call_args.args[1]
        self.assertEqual("南昌", params["city"])
        self.assertEqual("景德镇", params["cityd"])
        self.assertEqual("2026-09-05", params["date"])
        self.assertEqual("13:30", params["time"])
        self.assertEqual("all", params["extensions"])

    def test_intercity_transit_summary_extracts_railway_leg(self):
        service = AmapService()
        service._get = Mock(return_value={
            "status": "1",
            "route": {"transits": [{
                "duration": "15000",
                "distance": "315700",
                "segments": [{
                    "railway": {
                        "trip": "D777",
                        "type": "2012",
                        "time": "8400",
                        "distance": "285000",
                        "departure_stop": {
                            "name": "昆明南站",
                            "location": "102.861,24.870",
                            "time": "0950",
                        },
                        "arrival_stop": {
                            "name": "大理站",
                            "location": "100.250,25.590",
                            "time": "1210",
                        },
                    },
                }],
            }]},
        })

        result = service.get_intercity_transit_summary(
            "102.71,25.04", "100.23,25.60",
            "昆明", "大理", "2026-09-05", "08:45",
        )

        railway = result["railway"]
        self.assertEqual("昆明南站", railway["departure_hub"]["name"])
        self.assertEqual("大理站", railway["arrival_hub"]["name"])
        self.assertEqual(140, railway["duration_minutes"])
        self.assertEqual("D777", railway["trip"])
        self.assertEqual("09:50", railway["departure_time"])
        self.assertEqual("12:10", railway["arrival_time"])

    def test_intercity_railway_is_split_into_access_rail_and_egress(self):
        from app.agents import trip_planner_agent as planner_module

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        def local_route(first, second, *_args, **_kwargs):
            same_place = (
                first.longitude == second.longitude
                and first.latitude == second.latitude
            )
            return {
                "mode": "同一地点" if same_place else "公共交通",
                "route_mode": "none" if same_place else "transit",
                "duration_minutes": 0 if same_place else 30,
                "distance_km": 0 if same_place else 12.0,
                "source": "estimated" if same_place else "amap",
                "alternatives": [],
            }

        planner._route_choice_between_locations = Mock(side_effect=local_route)
        request = TripRequest(
            city="云南", start_date="2026-09-05", end_date="2026-09-06",
            travel_days=2, transportation="公共交通", accommodation="经济型酒店",
            trip_mode="multi_city", destinations=["昆明", "大理"],
            start_location="昆明", end_location="大理",
            intercity_transport="high_speed_rail",
        )
        kunming_hotel = Hotel(
            name="昆明酒店",
            location=Location(longitude=102.71, latitude=25.04),
        )
        dali_hotel = Hotel(
            name="大理酒店",
            location=Location(longitude=100.23, latitude=25.60),
        )
        plan = TripPlan(
            city="云南", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试",
            days=[
                DayPlan(
                    date="2026-09-05", day_index=0, description="昆明",
                    transportation="公共交通", accommodation="经济型酒店",
                    hotel=kunming_hotel,
                ),
                DayPlan(
                    date="2026-09-06", day_index=1, description="前往大理",
                    transportation="公共交通", accommodation="经济型酒店",
                    hotel=dali_hotel,
                    meals=[Meal(type="breakfast", name="原早餐")],
                    start_location="昆明", end_location="大理", overnight_city="大理",
                    route_segment=RouteSegment(
                        origin="昆明", destination="大理",
                        transport_mode="高铁/动车", distance_km=315.7,
                        duration_minutes=250, source="amap",
                        requires_confirmation=True,
                    ),
                ),
            ],
        )
        route_service = Mock()
        route_service.get_intercity_transit_summary.return_value = {
            "duration_minutes": 250,
            "distance_km": 315.7,
            "source": "amap",
            "railway": {
                "departure_hub": {
                    "name": "昆明南站", "longitude": 102.861, "latitude": 24.870,
                },
                "arrival_hub": {
                    "name": "大理站", "longitude": 100.250, "latitude": 25.590,
                },
                "departure_time": "09:50", "arrival_time": "12:10",
                "duration_minutes": 140, "distance_km": 285.0,
                "trip": "D777", "type": "2012", "source": "amap",
            },
        }
        original = planner_module.get_amap_service
        planner_module.get_amap_service = lambda: route_service
        try:
            planner._build_daily_timelines(plan, request)
        finally:
            planner_module.get_amap_service = original

        timeline = plan.days[1].timeline
        transport_legs = [item for item in timeline if item.kind == "transport"]
        self.assertEqual(
            [
                ("昆明酒店", "昆明南站"),
                ("昆明南站", "大理站"),
                ("大理站", "大理酒店"),
            ],
            [(item.from_name, item.to_name) for item in transport_legs],
        )
        self.assertEqual("D777 动车（参考）", transport_legs[1].transport_mode)
        self.assertEqual(140, transport_legs[1].duration_minutes)
        hubs = [item for item in timeline if item.kind == "hub"]
        self.assertEqual(
            ["intercity_departure", "intercity_arrival"],
            [item.hub_role for item in hubs],
        )
        self.assertEqual(255, plan.days[1].route_segment.duration_minutes)
        self.assertFalse(any(
            item.from_name == "昆明" and item.to_name == "大理"
            for item in transport_legs
        ))

    def test_late_rail_arrival_puts_lunch_at_arrival_station_before_hotel(self):
        from app.agents import trip_planner_agent as planner_module

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        def local_route(first, second, *_args, **_kwargs):
            same_place = (
                first.longitude == second.longitude
                and first.latitude == second.latitude
            )
            return {
                "mode": "同一地点" if same_place else "公共交通",
                "route_mode": "none" if same_place else "transit",
                "duration_minutes": 0 if same_place else 30,
                "distance_km": 0 if same_place else 10.0,
                "source": "estimated" if same_place else "amap",
                "alternatives": [],
            }

        planner._route_choice_between_locations = Mock(side_effect=local_route)
        request = TripRequest(
            city="江西", start_date="2026-09-10", end_date="2026-09-11",
            travel_days=2, transportation="公共交通", accommodation="经济型酒店",
            trip_mode="multi_city", destinations=["婺源", "庐山"],
            start_location="婺源", end_location="庐山",
            intercity_transport="high_speed_rail",
        )
        origin_hotel = Hotel(
            name="婺源酒店",
            location=Location(longitude=117.86, latitude=29.25),
        )
        destination_hotel = Hotel(
            name="庐山酒店",
            location=Location(longitude=115.97, latitude=29.57),
        )
        plan = TripPlan(
            city="江西", start_date=request.start_date, end_date=request.end_date,
            overall_suggestions="测试", trip_mode="multi_city",
            days=[
                DayPlan(
                    date="2026-09-10", day_index=0, description="婺源",
                    transportation="公共交通", accommodation="经济型酒店",
                    hotel=origin_hotel,
                ),
                DayPlan(
                    date="2026-09-11", day_index=1, description="前往庐山",
                    transportation="公共交通", accommodation="经济型酒店",
                    hotel=destination_hotel,
                    meals=[Meal(type="lunch", name="原午餐")],
                    start_location="婺源", end_location="庐山", overnight_city="九江",
                    route_segment=RouteSegment(
                        origin="婺源", destination="庐山",
                        transport_mode="高铁/动车", distance_km=180,
                        duration_minutes=375, source="amap",
                    ),
                ),
            ],
        )
        amap = Mock()
        amap.get_intercity_transit_summary.return_value = {
            "duration_minutes": 375, "distance_km": 180, "source": "amap",
            "railway": {
                "departure_hub": {
                    "name": "婺源站", "longitude": 117.89, "latitude": 29.25,
                },
                "arrival_hub": {
                    "name": "庐山站", "longitude": 115.88, "latitude": 29.61,
                },
                "departure_time": "10:30", "arrival_time": "12:40",
                "duration_minutes": 130, "distance_km": 160,
                "trip": "D100", "type": "2012", "source": "amap",
            },
        }
        original = planner_module.get_amap_service
        planner_module.get_amap_service = lambda: amap
        try:
            planner._build_daily_timelines(plan, request)
        finally:
            planner_module.get_amap_service = original

        timeline = plan.days[1].timeline
        lunch_index = next(
            index for index, item in enumerate(timeline)
            if item.kind == "meal" and item.meal_type == "lunch"
        )
        arrival_index = next(
            index for index, item in enumerate(timeline)
            if item.kind == "hub" and item.hub_role == "intercity_arrival"
        )
        hotel_leg_index = next(
            index for index, item in enumerate(timeline)
            if item.kind == "transport"
            and item.to_name == "庐山酒店"
        )
        self.assertEqual("庐山站内或附近简餐", plan.days[1].meals[0].name)
        self.assertLess(arrival_index, lunch_index)
        self.assertLess(lunch_index, hotel_leg_index)
        self.assertEqual("logistics_advice", plan.days[1].meals[0].data_source)

    def test_intercity_aggregate_without_railway_uses_one_tavily_fallback(self):
        from app.agents import trip_planner_agent as planner_module

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        request_day = DayPlan(
            date="2026-09-13", day_index=4, description="前往丽江",
            transportation="公共交通", accommodation="经济型酒店",
            start_location="大理", end_location="丽江", overnight_city="丽江",
        )
        segment = RouteSegment(
            origin="大理", destination="丽江", transport_mode="高铁/动车",
            distance_km=180.5, duration_minutes=150, source="estimated",
        )
        origin_hotel = Hotel(
            name="大理酒店", location=Location(longitude=100.21, latitude=25.65),
        )
        destination_hotel = Hotel(
            name="丽江酒店", location=Location(longitude=100.20, latitude=26.92),
        )
        amap = Mock()
        amap.get_intercity_transit_summary.return_value = {
            "duration_minutes": 485,
            "distance_km": 180.5,
            "source": "amap",
            "railway": None,
        }
        amap.search_transport_hubs.side_effect = lambda city, _mode: [{
            "id": city,
            "name": f"{city}站",
            "longitude": 100.25 if city == "大理" else 100.23,
            "latitude": 25.59 if city == "大理" else 26.85,
        }]
        amap.get_route_summary.return_value = {
            "duration_minutes": 30,
            "distance_km": 10.0,
        }
        research = Mock(return_value={
            "duration_minutes": 100,
            "source": "tavily",
            "source_urls": ["https://example.test/reference"],
            "departure_station": "大理东站",
            "arrival_station": "丽江站",
        })
        amap.search_transport_hubs.side_effect = lambda city, _mode: ([
            {
                "id": "main", "name": "大理站",
                "longitude": 100.25, "latitude": 25.59,
            },
            {
                "id": "east", "name": "大理东站",
                "longitude": 100.31, "latitude": 25.61,
            },
        ] if city == "大理" else [{
            "id": city, "name": "丽江站",
            "longitude": 100.23, "latitude": 26.85,
        }])
        original_amap = planner_module.get_amap_service
        original_research = planner_module.research_intercity_rail_duration
        planner_module.get_amap_service = lambda: amap
        planner_module.research_intercity_rail_duration = research
        try:
            selected = planner._choose_intercity_transfer_window(
                request_day,
                segment,
                origin_hotel,
                destination_hotel,
                [],
                [],
            )
        finally:
            planner_module.get_amap_service = original_amap
            planner_module.research_intercity_rail_duration = original_research

        # Each station access leg is buffered from 30 to 40 minutes, then the
        # plan exposes 45 minutes waiting, 100 rail and 20 minutes station exit.
        self.assertEqual(245, selected["duration_minutes"])
        self.assertEqual(100, selected["railway"]["duration_minutes"])
        self.assertEqual("tavily", selected["source"])
        self.assertEqual("大理东站", selected["railway"]["departure_hub"]["name"])
        self.assertEqual("丽江站", selected["railway"]["arrival_hub"]["name"])
        self.assertEqual(1, research.call_count)
        self.assertEqual(3, amap.get_intercity_transit_summary.call_count)
        self.assertTrue(segment.requires_confirmation)

    def test_intercity_amap_railway_does_not_call_tavily(self):
        from app.agents import trip_planner_agent as planner_module

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        day = DayPlan(
            date="2026-09-06", day_index=1, description="前往大理",
            transportation="公共交通", accommodation="经济型酒店",
        )
        segment = RouteSegment(
            origin="昆明", destination="大理", transport_mode="高铁/动车",
            distance_km=315.0, duration_minutes=250,
        )
        summary = {
            "duration_minutes": 250,
            "distance_km": 315.0,
            "source": "amap",
            "railway": {
                "duration_minutes": 140,
                "distance_km": 285.0,
                "departure_hub": {
                    "name": "昆明南站", "longitude": 102.86, "latitude": 24.87,
                },
                "arrival_hub": {
                    "name": "大理站", "longitude": 100.25, "latitude": 25.59,
                },
            },
        }
        research = Mock()
        original_research = planner_module.research_intercity_rail_duration
        planner_module.research_intercity_rail_duration = research
        try:
            result = planner._normalize_intercity_rail_summary(
                day, segment, None, None, summary, {}
            )
        finally:
            planner_module.research_intercity_rail_duration = original_research

        self.assertIs(summary, result)
        research.assert_not_called()

    def test_intercity_rejects_tavily_duration_longer_than_amap_total(self):
        from app.agents import trip_planner_agent as planner_module

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._resolve_intercity_rail_hub = Mock(return_value=None)
        day = DayPlan(
            date="2026-09-12", day_index=1, description="前往大理",
            transportation="公共交通", accommodation="经济型酒店",
        )
        segment = RouteSegment(
            origin="昆明", destination="大理", transport_mode="高铁/动车",
            distance_km=296.5, duration_minutes=180,
        )
        summary = {
            "duration_minutes": 186, "distance_km": 296.5,
            "source": "amap", "railway": None,
        }
        research = Mock(return_value={
            "duration_minutes": 476, "source": "tavily",
            "departure_station": "昆明站", "arrival_station": "大理站",
        })
        original_research = planner_module.research_intercity_rail_duration
        planner_module.research_intercity_rail_duration = research
        try:
            result = planner._normalize_intercity_rail_summary(
                day, segment, None, None, summary, {}
            )
        finally:
            planner_module.research_intercity_rail_duration = original_research

        self.assertEqual("estimated", result["source"])
        self.assertEqual(180, result["duration_minutes"])
        research.assert_called_once()

    def test_driving_summary_returns_minutes_and_distance(self):
        service = AmapService()
        service._get = Mock(return_value={
            "status": "1",
            "route": {"paths": [{"distance": "10500", "duration": "900"}]},
        })

        result = service.get_route_summary(
            "117.000000,36.600000",
            "117.100000,36.700000",
            "driving",
            "济南",
        )

        self.assertEqual(15, result["duration_minutes"])
        self.assertEqual(10.5, result["distance_km"])
        self.assertEqual("direction/driving", service._get.call_args.args[0])

    def test_transit_summary_reads_transit_duration(self):
        service = AmapService()
        service._get = Mock(return_value={
            "status": "1",
            "route": {"transits": [{"duration": "1800"}]},
        })

        result = service.get_route_summary(
            "117.000000,36.600000",
            "117.100000,36.700000",
            "transit",
            "济南",
        )

        self.assertEqual(30, result["duration_minutes"])
        self.assertEqual("direction/transit/integrated", service._get.call_args.args[0])
        self.assertEqual("济南", service._get.call_args.args[1]["city"])

    def test_bicycling_summary_reads_v4_response(self):
        service = AmapService()
        service._get = Mock(return_value={
            "errcode": 0,
            "data": {"paths": [{"distance": 3200, "duration": 720}]},
        })

        result = service.get_route_summary(
            "117.000000,36.600000",
            "117.030000,36.620000",
            "bicycling",
            "济南",
        )

        self.assertEqual(12, result["duration_minutes"])
        self.assertEqual(3.2, result["distance_km"])
        self.assertEqual("direction/bicycling", service._get.call_args.args[0])
        self.assertEqual("v4", service._get.call_args.args[2])

    def test_planner_adds_route_buffer(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        route_service = Mock()
        route_service.get_route_summary.return_value = {
            "duration_minutes": 20,
            "distance_km": 5.0,
            "source": "amap",
        }
        first = Location(longitude=117.0, latitude=36.6)
        second = Location(longitude=117.1, latitude=36.7)

        from app.agents import trip_planner_agent as planner_module
        original = planner_module.get_amap_service
        planner_module.get_amap_service = lambda: route_service
        try:
            minutes = planner._estimated_location_transfer_minutes(
                first,
                second,
                "公共交通",
                "济南",
                use_amap=True,
            )
        finally:
            planner_module.get_amap_service = original

        self.assertEqual(30, minutes)
        route_service.get_route_summary.assert_called_once()

    def test_smart_mix_compares_transit_and_taxi_per_leg(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        route_service = Mock()

        def route_result(_origin, _destination, mode, _city):
            durations = {"walking": 50, "bicycling": 25, "transit": 24, "driving": 12}
            return {
                "duration_minutes": durations[mode],
                "distance_km": 5.0,
                "source": "amap",
            }

        route_service.get_route_summary.side_effect = route_result
        first = Location(longitude=117.0, latitude=36.6)
        second = Location(longitude=117.04, latitude=36.6)

        from app.agents import trip_planner_agent as planner_module
        original = planner_module.get_amap_service
        planner_module.get_amap_service = lambda: route_service
        try:
            choice = planner._route_choice_between_locations(
                first, second, "智能混合", "济南"
            )
        finally:
            planner_module.get_amap_service = original

        self.assertEqual("打车", choice["mode"])
        self.assertEqual("driving", choice["route_mode"])
        called_modes = {call.args[2] for call in route_service.get_route_summary.call_args_list}
        self.assertEqual({"walking", "bicycling", "transit", "driving"}, called_modes)

    def test_timeline_transport_contains_switchable_alternatives(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        route_service = Mock()

        def route_result(_origin, _destination, mode, _city):
            durations = {"walking": 20, "bicycling": 8, "transit": 15, "driving": 6}
            return {
                "duration_minutes": durations[mode],
                "distance_km": 1.5,
                "source": "amap",
            }

        route_service.get_route_summary.side_effect = route_result
        first = Location(longitude=117.0, latitude=36.6)
        second = Location(longitude=117.015, latitude=36.6)

        from app.agents import trip_planner_agent as planner_module
        original = planner_module.get_amap_service
        planner_module.get_amap_service = lambda: route_service
        try:
            choice = planner._route_choice_between_locations(
                first, second, "公共交通", "济南", include_alternatives=True
            )
        finally:
            planner_module.get_amap_service = original

        self.assertEqual("公共交通", choice["mode"])
        self.assertEqual(
            {"步行", "骑行", "公共交通", "打车"},
            {option["mode"] for option in choice["alternatives"]},
        )

    def test_public_transport_does_not_query_driving(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        route_service = Mock()
        route_service.get_route_summary.return_value = {
            "duration_minutes": 30,
            "distance_km": 8.0,
            "source": "amap",
        }
        first = Location(longitude=117.0, latitude=36.6)
        second = Location(longitude=117.08, latitude=36.6)

        from app.agents import trip_planner_agent as planner_module
        original = planner_module.get_amap_service
        planner_module.get_amap_service = lambda: route_service
        try:
            choice = planner._route_choice_between_locations(
                first, second, "公共交通", "济南"
            )
        finally:
            planner_module.get_amap_service = original

        self.assertEqual("公共交通", choice["mode"])
        self.assertEqual("transit", route_service.get_route_summary.call_args.args[2])
        route_service.get_route_summary.assert_called_once()

    def test_self_drive_uses_driving_route(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        route_service = Mock()
        route_service.get_route_summary.return_value = {
            "duration_minutes": 18,
            "distance_km": 6.0,
            "source": "amap",
        }
        first = Location(longitude=117.0, latitude=36.6)
        second = Location(longitude=117.06, latitude=36.6)

        from app.agents import trip_planner_agent as planner_module
        original = planner_module.get_amap_service
        planner_module.get_amap_service = lambda: route_service
        try:
            choice = planner._route_choice_between_locations(
                first, second, "自驾", "济南"
            )
        finally:
            planner_module.get_amap_service = original

        self.assertEqual("自驾", choice["mode"])
        self.assertEqual("driving", route_service.get_route_summary.call_args.args[2])
        route_service.get_route_summary.assert_called_once()

    def test_timeline_places_lunch_between_morning_and_afternoon(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._route_choice_between_locations = Mock(return_value={
            "mode": "公共交通",
            "route_mode": "transit",
            "duration_minutes": 20,
            "distance_km": 3.0,
            "source": "amap",
        })
        hotel = Hotel(
            name="测试酒店",
            location=Location(longitude=117.0, latitude=36.6),
        )
        attractions = [
            Attraction(
                name="上午景点", address="A", location=Location(longitude=117.01, latitude=36.61),
                visit_duration=120, description="",
            ),
            Attraction(
                name="下午景点", address="B", location=Location(longitude=117.02, latitude=36.62),
                visit_duration=120, description="",
            ),
        ]
        meals = [
            Meal(type="breakfast", name="早餐店", location=Location(longitude=117.001, latitude=36.601)),
            Meal(type="lunch", name="午餐店", location=Location(longitude=117.015, latitude=36.615)),
            Meal(type="dinner", name="晚餐店", location=Location(longitude=117.018, latitude=36.618)),
        ]
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店", hotel=hotel, attractions=attractions, meals=meals,
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            days=[day], budget={"total": 0}, overall_suggestions="",
        )
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04", travel_days=1,
            transportation="公共交通", accommodation="经济型酒店",
        )

        planner._build_daily_timelines(plan, request)

        stop_names = [item.name for item in day.timeline if item.kind not in {"transport", "note"}]
        self.assertEqual(
            ["测试酒店", "早餐店", "上午景点", "午餐店", "下午景点", "晚餐店", "测试酒店"],
            stop_names,
        )
        self.assertEqual(6, sum(item.kind == "transport" for item in day.timeline))

    def test_noon_arrival_places_lunch_before_first_attraction(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._route_choice_between_locations = Mock(return_value={
            "mode": "公共交通", "route_mode": "transit",
            "duration_minutes": 15, "distance_km": 2.0, "source": "amap",
        })
        hotel = Hotel(name="测试酒店", location=Location(longitude=117.0, latitude=36.6))
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店", hotel=hotel,
            attractions=[Attraction(
                name="下午景点", address="", location=Location(longitude=117.01, latitude=36.61),
                visit_duration=120, description="",
            )],
            meals=[
                Meal(type="lunch", name="午餐店", location=Location(longitude=117.005, latitude=36.605)),
                Meal(type="dinner", name="晚餐店", location=Location(longitude=117.006, latitude=36.606)),
            ],
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            days=[day], overall_suggestions="",
        )
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04", travel_days=1,
            transportation="公共交通", accommodation="经济型酒店",
            arrival_transport="高铁", arrival_time="12:00",
        )

        planner._build_daily_timelines(plan, request)

        stops = [item.name for item in day.timeline if item.kind != "transport"]
        self.assertLess(stops.index("午餐店"), stops.index("下午景点"))

    def test_exact_hubs_appear_before_hotel_and_after_return(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        route_service = Mock()
        route_service.search_poi.side_effect = [
            [{"name": "济南西站", "lng": 116.90, "lat": 36.67, "type": "交通设施服务;火车站"}],
            [{"name": "济南东站", "lng": 117.20, "lat": 36.72, "type": "交通设施服务;火车站"}],
        ]
        route_service.get_route_summary.return_value = {
            "duration_minutes": 20, "distance_km": 10.0, "source": "amap",
        }
        hotel = Hotel(name="测试酒店", location=Location(longitude=117.0, latitude=36.6))
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店", hotel=hotel,
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            days=[day], overall_suggestions="",
        )
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04", travel_days=1,
            transportation="公共交通", accommodation="经济型酒店",
            arrival_transport="高铁", arrival_time="12:00", arrival_location="济南西站",
            departure_transport="高铁", departure_time="20:00", departure_location="济南东站",
        )
        from app.agents import trip_planner_agent as planner_module
        original = planner_module.get_amap_service
        planner_module.get_amap_service = lambda: route_service
        try:
            planner._build_daily_timelines(plan, request)
        finally:
            planner_module.get_amap_service = original

        hubs = [item.name for item in day.timeline if item.kind == "hub"]
        self.assertEqual(["济南西站", "济南东站"], hubs)
        legs = [(item.from_name, item.to_name) for item in day.timeline if item.kind == "transport"]
        self.assertIn(("济南西站", "测试酒店"), legs)
        self.assertIn(("测试酒店", "济南东站"), legs)

    def test_final_constraints_pull_late_lunch_back_into_window(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._route_choice_between_locations = Mock(return_value={
            "mode": "公共交通", "route_mode": "transit",
            "duration_minutes": 20, "distance_km": 2.0, "source": "amap",
        })
        hotel = Hotel(name="测试酒店", location=Location(longitude=117.0, latitude=36.6))
        attraction = Attraction(
            name="大型景点", address="", location=Location(longitude=117.01, latitude=36.61),
            visit_duration=270, description="",
        )
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店", hotel=hotel, attractions=[attraction],
            meals=[Meal(
                type="lunch", name="午餐店",
                location=Location(longitude=117.02, latitude=36.62),
            )],
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            days=[day], overall_suggestions="",
        )
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04", travel_days=1,
            transportation="公共交通", accommodation="经济型酒店",
        )

        planner._enforce_final_timeline_constraints(plan, request)

        lunch = next(item for item in day.timeline if item.meal_type == "lunch")
        self.assertLessEqual(planner._time_to_minutes(lunch.start_time), 14 * 60)
        # 可以换用餐顺序解决时，保留完整游览，不强制压缩景点。
        self.assertEqual(270, attraction.visit_duration)

    def test_final_constraints_remove_overflow_attraction_instead_of_dinner(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)

        def route(first, second, *_args, **_kwargs):
            station_access = first.longitude == 115.91 and second.longitude == 115.92
            return {
                "mode": "步行" if station_access else "公共交通",
                "route_mode": "walking" if station_access else "transit",
                "duration_minutes": 15 if station_access else 40,
                "distance_km": 1.0 if station_access else 4.0,
                "source": "amap", "alternatives": [],
            }

        planner._route_choice_between_locations = Mock(side_effect=route)
        planner._resolve_transport_hub = Mock(side_effect=lambda _request, arrival: (
            {
                "name": "南昌站",
                "location": Location(longitude=115.91, latitude=28.66),
            }
            if arrival else None
        ))
        hotel = Hotel(
            name="南昌酒店",
            location=Location(longitude=115.92, latitude=28.67),
        )
        attractions = [
            Attraction(
                name="滕王阁", address="", description="",
                location=Location(longitude=115.88, latitude=28.68),
                visit_duration=150,
            ),
            Attraction(
                name="万寿宫历史文化街区", address="", description="",
                location=Location(longitude=115.89, latitude=28.67),
                visit_duration=120,
            ),
            Attraction(
                name="南昌八一起义纪念馆", address="", description="",
                location=Location(longitude=115.90, latitude=28.67),
                visit_duration=180,
            ),
        ]
        day = DayPlan(
            date="2026-09-11", day_index=0, description="",
            transportation="公共交通", accommodation="经济型酒店",
            hotel=hotel, attractions=attractions,
            meals=[
                Meal(
                    type="lunch", name="午餐",
                    location=Location(longitude=115.90, latitude=28.67),
                ),
                Meal(type="dinner", name="晚餐", location=hotel.location),
            ],
        )
        plan = TripPlan(
            city="江西", start_date="2026-09-11", end_date="2026-09-12",
            trip_mode="multi_city", days=[day], overall_suggestions="",
        )
        request = TripRequest(
            city="江西", start_date="2026-09-11", end_date="2026-09-12",
            travel_days=2, transportation="公共交通", accommodation="经济型酒店",
            trip_mode="multi_city", destinations=["南昌", "景德镇"],
            start_location="南昌", end_location="景德镇",
            arrival_transport="高铁", arrival_time="11:00",
            arrival_location="南昌站",
        )

        planner._enforce_final_timeline_constraints(plan, request)

        self.assertEqual(2, len(day.attractions))
        self.assertNotIn(
            "南昌八一起义纪念馆",
            [attraction.name for attraction in day.attractions],
        )
        self.assertTrue(any(meal.type == "dinner" for meal in day.meals))
        dinner = next(item for item in day.timeline if item.meal_type == "dinner")
        self.assertLessEqual(planner._time_to_minutes(dinner.start_time), 20 * 60 + 30)
        self.assertTrue(any(
            item.attraction.name == "南昌八一起义纪念馆"
            for item in plan.removed_attractions
        ))

    def test_final_constraints_drop_arrival_lunch_when_hotel_is_reached_too_late(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)

        def route(first, _second, _preference, _city, include_alternatives=True):
            return {
                "mode": "公共交通", "route_mode": "transit",
                "duration_minutes": 90 if first.longitude == 116.9 else 20,
                "distance_km": 10.0, "source": "amap", "alternatives": [],
            }

        planner._route_choice_between_locations = route
        planner._resolve_transport_hub = Mock(side_effect=lambda _request, arrival: (
            {"name": "济南西站", "location": Location(longitude=116.9, latitude=36.67)}
            if arrival else None
        ))
        hotel = Hotel(name="测试酒店", location=Location(longitude=117.0, latitude=36.6))
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店", hotel=hotel,
            meals=[Meal(
                type="lunch", name="午餐店",
                location=Location(longitude=117.01, latitude=36.61),
            )],
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            days=[day], overall_suggestions="",
        )
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04", travel_days=1,
            transportation="公共交通", accommodation="经济型酒店",
            arrival_transport="高铁", arrival_time="12:00", arrival_location="济南西站",
        )

        planner._enforce_final_timeline_constraints(plan, request)

        self.assertFalse(any(meal.type == "lunch" for meal in day.meals))

    def test_final_constraints_move_long_arrival_day_attraction_to_later_day(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)

        def route(first, second, *_args, **_kwargs):
            touches_remote = 117.30 in {first.longitude, second.longitude}
            return {
                "mode": "公共交通", "route_mode": "transit",
                "duration_minutes": 0 if first == second else (140 if touches_remote else 20),
                "distance_km": 25.0 if touches_remote else 2.0,
                "source": "amap", "alternatives": [],
            }

        planner._route_choice_between_locations = Mock(side_effect=route)
        planner._resolve_transport_hub = Mock(return_value=None)
        hotel = Hotel(
            name="昆明酒店", location=Location(longitude=102.70, latitude=25.04)
        )
        attraction = Attraction(
            name="滇池", address="滇池路", description="",
            location=Location(longitude=117.30, latitude=25.00),
            visit_duration=120,
        )
        first_day = DayPlan(
            date="2026-09-11", day_index=0, description="抵达后游览滇池",
            transportation="公共交通", accommodation="经济型酒店",
            hotel=hotel, attractions=[attraction],
            meals=[Meal(type="dinner", name="酒店附近晚餐", location=hotel.location)],
        )
        second_day = DayPlan(
            date="2026-09-12", day_index=1, description="市区游览",
            transportation="公共交通", accommodation="经济型酒店",
            hotel=hotel,
        )
        plan = TripPlan(
            city="昆明", start_date="2026-09-11", end_date="2026-09-12",
            days=[first_day, second_day], overall_suggestions="",
        )
        request = TripRequest(
            city="昆明", start_date="2026-09-11", end_date="2026-09-12",
            travel_days=2, transportation="公共交通", accommodation="经济型酒店",
            arrival_transport="高铁", arrival_time="12:00",
        )

        planner._enforce_final_timeline_constraints(plan, request)

        self.assertEqual([], first_day.attractions)
        self.assertEqual(["滇池"], [item.name for item in second_day.attractions])
        self.assertEqual([], plan.removed_attractions)
        self.assertIn("酒店附近休整", first_day.description)

    def test_final_constraints_keep_user_locked_long_arrival_attraction(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._route_choice_between_locations = Mock(return_value={
            "mode": "公共交通", "route_mode": "transit",
            "duration_minutes": 140, "distance_km": 25.0,
            "source": "amap", "alternatives": [],
        })
        planner._resolve_transport_hub = Mock(return_value=None)
        hotel = Hotel(
            name="昆明酒店", location=Location(longitude=102.70, latitude=25.04)
        )
        attraction = Attraction(
            name="滇池", address="滇池路", description="",
            location=Location(longitude=102.65, latitude=24.90),
            visit_duration=120, user_locked=True,
        )
        day = DayPlan(
            date="2026-09-11", day_index=0, description="抵达后游览滇池",
            transportation="公共交通", accommodation="经济型酒店",
            hotel=hotel, attractions=[attraction],
        )
        plan = TripPlan(
            city="昆明", start_date="2026-09-11", end_date="2026-09-11",
            days=[day], overall_suggestions="",
        )
        request = TripRequest(
            city="昆明", start_date="2026-09-11", end_date="2026-09-11",
            travel_days=1, transportation="公共交通", accommodation="经济型酒店",
            arrival_transport="高铁", arrival_time="12:00",
        )

        planner._enforce_final_timeline_constraints(plan, request)

        self.assertEqual(["滇池"], [item.name for item in day.attractions])
        issues = planner._validate_trip_plan(plan, request)
        self.assertTrue(any(
            item.code == "arrival_day_transfer_too_long" for item in issues
        ))

    def test_late_dinner_changes_restaurant_before_removing_attractions(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)

        def route(_first, second, *_args, **_kwargs):
            restaurant_trip = second.longitude == 118.0
            return {
                "mode": "公共交通", "route_mode": "transit",
                "duration_minutes": 240 if restaurant_trip else 20,
                "distance_km": 30.0 if restaurant_trip else 2.0,
                "source": "amap", "alternatives": [],
            }

        planner._route_choice_between_locations = Mock(side_effect=route)
        planner._resolve_transport_hub = Mock(return_value=None)
        hotel = Hotel(
            name="测试酒店", address="市中心",
            location=Location(longitude=117.0, latitude=36.6),
        )
        attractions = [
            Attraction(
                name=f"核心景点{index}", address="", description="",
                location=Location(longitude=117.01 + index * 0.01, latitude=36.61),
                visit_duration=240,
            )
            for index in range(2)
        ]
        day = DayPlan(
            date="2026-09-12", day_index=0, description="",
            transportation="公共交通", accommodation="经济型酒店",
            hotel=hotel, attractions=attractions,
            meals=[Meal(
                type="dinner", name="远处餐厅",
                location=Location(longitude=118.0, latitude=36.6),
                estimated_cost=80, data_source="amap",
            )],
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-12", end_date="2026-09-12",
            days=[day], overall_suggestions="",
        )
        request = TripRequest(
            city="济南", start_date="2026-09-12", end_date="2026-09-12",
            travel_days=1, transportation="公共交通", accommodation="经济型酒店",
        )

        planner._enforce_final_timeline_constraints(plan, request)

        self.assertEqual(2, len(day.attractions))
        dinner = next(m for m in day.meals if m.type == "dinner")
        self.assertEqual("logistics_advice", dinner.data_source)
        self.assertEqual(hotel.location, dinner.location)
        self.assertFalse(any(m.name == "远处餐厅" for m in day.meals))

    def test_hotel_search_rejects_candidate_from_another_province(self):
        from app.agents import trip_planner_agent as planner_module

        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._hotel_expected_region = "云南"
        payload = {"pois": [
            {
                "name": "北京如家酒店", "typecode": "100105",
                "location": "116.31,39.94", "distance": "100",
                "pname": "北京市", "cityname": "北京市", "adname": "海淀区",
            },
            {
                "name": "香格里拉如家酒店", "typecode": "100105",
                "location": "99.70,27.83", "distance": "500",
                "pname": "云南省", "cityname": "迪庆藏族自治州",
                "adname": "香格里拉市",
            },
        ]}
        original_request_json = planner_module.request_json
        planner_module.request_json = Mock(return_value=payload)
        try:
            hotels = planner._do_hotel_search(
                99.70, 27.83, "100105", 5000, "100105"
            )
        finally:
            planner_module.request_json = original_request_json

        self.assertEqual(["香格里拉如家酒店"], [item["name"] for item in hotels])

    def test_final_constraints_replace_breakfast_closed_at_actual_arrival(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._route_choice_between_locations = Mock(return_value={
            "mode": "步行", "route_mode": "walking",
            "duration_minutes": 20, "distance_km": 1.0, "source": "amap",
        })
        hotel = Hotel(name="测试酒店", location=Location(longitude=117.0, latitude=36.6))
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店", hotel=hotel,
            meals=[Meal(
                type="breakfast", name="九点才开门的私房菜",
                location=Location(longitude=117.01, latitude=36.61),
                opening_hours="09:00-14:00,16:30-21:00",
            )],
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            days=[day], overall_suggestions="",
        )
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04", travel_days=1,
            transportation="公共交通", accommodation="经济型酒店",
            arrival_transport="已在此地", departure_time="10:00",
        )
        replacement = {
            "id": "OPEN-BREAKFAST", "name": "早餐铺", "address": "测试路",
            "longitude": 117.005, "latitude": 36.605,
            "opening_hours": "06:30-10:30", "estimated_cost": 15,
            "rating": 4.6, "dish_options": ["包子"],
        }

        planner._enforce_final_timeline_constraints(plan, request, [{
            "day": 1, "meals": {"breakfast": [replacement], "lunch": [], "dinner": []},
        }])

        self.assertEqual("早餐铺", day.meals[0].name)
        self.assertTrue(day.meals[0].opening_hours_verified)

    def test_final_constraints_back_schedule_departure_hub(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)

        def route(_first, _second, _preference, _city, include_alternatives=True):
            return {
                "mode": "公共交通", "route_mode": "transit",
                "duration_minutes": 70 if _second.longitude == 117.2 else 20,
                "distance_km": 8.0, "source": "amap", "alternatives": [],
            }

        planner._route_choice_between_locations = route
        planner._resolve_transport_hub = Mock(side_effect=lambda _request, arrival: (
            None if arrival else {
                "name": "济南站", "location": Location(longitude=117.2, latitude=36.7),
            }
        ))
        hotel = Hotel(name="测试酒店", location=Location(longitude=117.0, latitude=36.6))
        attraction = Attraction(
            name="超时景点", address="", location=Location(longitude=117.01, latitude=36.61),
            visit_duration=420, description="",
        )
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店", hotel=hotel, attractions=[attraction],
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            days=[day], overall_suggestions="",
        )
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04", travel_days=1,
            transportation="公共交通", accommodation="经济型酒店",
            departure_transport="高铁", departure_time="17:00", departure_location="济南站",
        )

        planner._enforce_final_timeline_constraints(plan, request)

        hub = next(item for item in day.timeline if item.kind == "hub")
        self.assertLessEqual(planner._time_to_minutes(hub.start_time), 16 * 60)
        self.assertTrue(any(m.type == "breakfast" for m in day.meals))
        self.assertTrue(any(m.type == "lunch" for m in day.meals))
        self.assertFalse(any(m.type == "dinner" for m in day.meals))
        self.assertTrue(day.attractions)

    def test_early_departure_shifts_whole_timeline_to_reach_station_before_buffer(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._route_choice_between_locations = Mock(return_value={
            "mode": "公共交通", "route_mode": "transit",
            "duration_minutes": 60, "distance_km": 12.0,
            "source": "amap", "alternatives": [],
        })
        planner._resolve_transport_hub = Mock(side_effect=lambda _request, arrival: (
            None if arrival else {
                "name": "济南站", "location": Location(longitude=117.2, latitude=36.7),
            }
        ))
        hotel = Hotel(name="测试酒店", location=Location(longitude=117.0, latitude=36.6))
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店", hotel=hotel,
            meals=[Meal(
                type="breakfast", name="早餐店",
                location=Location(longitude=117.01, latitude=36.61),
            )],
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            days=[day], overall_suggestions="",
        )
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04", travel_days=1,
            transportation="公共交通", accommodation="经济型酒店",
            departure_transport="高铁", departure_time="10:00", departure_location="济南站",
        )

        planner._build_daily_timelines(plan, request)

        hub = next(item for item in day.timeline if item.kind == "hub")
        self.assertLessEqual(planner._time_to_minutes(hub.start_time), 9 * 60)
        first = next(item for item in day.timeline if item.start_time)
        self.assertLess(planner._time_to_minutes(first.start_time), 8 * 60)

    def test_remote_day_adds_explicit_meal_advice_without_fabricating_restaurants(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        hotel = Hotel(
            name="测试酒店", address="市区",
            location=Location(longitude=117.0, latitude=36.6),
        )
        attraction = Attraction(
            name="九如山休闲旅游度假区", address="景区",
            location=Location(longitude=117.3, latitude=36.48),
            visit_duration=210, description="", category="风景名胜",
        )
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店", hotel=hotel, attractions=[attraction], meals=[],
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            days=[day], overall_suggestions="",
        )
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04", travel_days=1,
            transportation="公共交通", accommodation="经济型酒店",
        )

        planner._ensure_logistical_meals(plan, request, [{
            "day": 1, "meals": {"breakfast": [], "lunch": [], "dinner": []},
        }])

        self.assertEqual({"breakfast", "lunch", "dinner"}, {meal.type for meal in day.meals})
        self.assertTrue(all(meal.data_source == "logistics_advice" for meal in day.meals))
        lunch = next(meal for meal in day.meals if meal.type == "lunch")
        self.assertIn("自备简餐", lunch.name)

    def test_quality_report_allows_remote_single_attraction_and_early_departure_empty_day(self):
        hotel = Hotel(name="测试酒店", location=Location(longitude=117.0, latitude=36.6))
        remote = Attraction(
            name="九如山休闲旅游度假区", address="景区",
            location=Location(longitude=117.3, latitude=36.48),
            visit_duration=210, description="", category="风景名胜",
        )
        days = [
            DayPlan(
                date="2026-09-04", day_index=0, description="", transportation="公共交通",
                accommodation="经济型酒店", hotel=hotel, attractions=[remote],
                meals=[
                    Meal(type="breakfast", name="早餐", data_source="logistics_advice", opening_hours_verified=True),
                    Meal(type="lunch", name="午餐", data_source="logistics_advice", opening_hours_verified=True),
                    Meal(type="dinner", name="晚餐", data_source="logistics_advice", opening_hours_verified=True),
                ],
            ),
            DayPlan(
                date="2026-09-05", day_index=1, description="", transportation="公共交通",
                accommodation="经济型酒店", hotel=hotel, attractions=[],
                meals=[Meal(type="breakfast", name="早餐", opening_hours_verified=True)],
            ),
        ]
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-05",
            days=days, overall_suggestions="",
        )
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-05", travel_days=2,
            transportation="公共交通", accommodation="经济型酒店",
            departure_transport="高铁", departure_time="10:00",
        )

        issues = MultiAgentTripPlanner._validate_trip_plan(plan, request)
        codes = {issue.code for issue in issues}
        self.assertNotIn("underfilled_full_day", codes)
        self.assertNotIn("empty_day", codes)

    def test_closed_attraction_is_removed_before_scoring(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._get_city_center = Mock(return_value=None)
        pois = [
            {
                "id": "CLOSED", "name": "济南黄河森林公园(不对外开放)",
                "address": "黄河北", "type": "风景名胜;公园", "typecode": "110100",
                "longitude": 117.0, "latitude": 36.8, "rating": 4.8,
                "_candidate_tier": "supplement", "_search_rank": 0,
            },
            {
                "id": "OPEN", "name": "大明湖景区", "address": "市区",
                "type": "风景名胜;公园", "typecode": "110100",
                "longitude": 117.02, "latitude": 36.68, "rating": 4.8,
                "_candidate_tier": "classic", "_search_rank": 1,
            },
        ]

        result = planner._score_and_filter(pois, [], 2, "济南")

        self.assertEqual(["大明湖景区"], [item["name"] for item in result])

    def test_closed_early_breakfast_falls_back_to_packaged_breakfast_advice(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        planner._route_choice_between_locations = Mock(return_value={
            "mode": "步行", "route_mode": "walking",
            "duration_minutes": 10, "distance_km": 0.5,
            "source": "amap", "alternatives": [],
        })
        planner._resolve_transport_hub = Mock(return_value=None)
        hotel = Hotel(
            name="测试酒店", address="测试路",
            location=Location(longitude=117.0, latitude=36.6),
        )
        day = DayPlan(
            date="2026-09-04", day_index=0, description="", transportation="公共交通",
            accommodation="经济型酒店", hotel=hotel,
            meals=[Meal(
                type="breakfast", name="九点才营业的餐厅",
                location=Location(longitude=117.001, latitude=36.601),
                opening_hours="09:00-14:00",
            )],
        )
        plan = TripPlan(
            city="济南", start_date="2026-09-04", end_date="2026-09-04",
            days=[day], overall_suggestions="",
        )
        request = TripRequest(
            city="济南", start_date="2026-09-04", end_date="2026-09-04", travel_days=1,
            transportation="公共交通", accommodation="经济型酒店",
            departure_transport="高铁", departure_time="10:00",
        )

        planner._enforce_final_timeline_constraints(plan, request, [{
            "day": 1, "meals": {"breakfast": [], "lunch": [], "dinner": []},
        }])

        breakfast = next(meal for meal in day.meals if meal.type == "breakfast")
        self.assertEqual("logistics_advice", breakfast.data_source)
        self.assertIn("打包早餐", breakfast.name)


if __name__ == "__main__":
    unittest.main()
