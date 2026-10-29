import unittest

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import DayPlan, Hotel, Location, TripPlan, TripRequest


class MealGroundingTests(unittest.TestCase):
    def setUp(self):
        self.planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)

    @staticmethod
    def _request(**overrides):
        values = {
            "city": "南京",
            "start_date": "2026-08-01",
            "end_date": "2026-08-01",
            "travel_days": 1,
            "transportation": "公共交通",
            "accommodation": "经济型酒店",
            "arrival_transport": "已在此地",
            "departure_time": "20:00",
        }
        values.update(overrides)
        return TripRequest(**values)

    def test_six_am_departure_has_no_meals(self):
        request = self._request(departure_time="06:00")
        self.assertEqual((), self.planner._available_meal_types(request, 0))

    def test_noon_departure_does_not_schedule_lunch(self):
        request = self._request(departure_time="12:00")
        self.assertEqual(
            ("breakfast",),
            self.planner._available_meal_types(request, 0),
        )

    def test_five_day_nanjing_breakfasts_are_all_different(self):
        request = self._request(
            start_date="2026-08-01",
            end_date="2026-08-05",
            travel_days=5,
            departure_time="20:00",
        )
        days = [
            DayPlan(
                date=f"2026-08-0{index + 1}",
                day_index=index,
                description="",
                transportation="公共交通",
                accommodation="经济型酒店",
            )
            for index in range(5)
        ]
        plan = TripPlan(
            city="南京",
            start_date="2026-08-01",
            end_date="2026-08-05",
            days=days,
            overall_suggestions="",
        )
        catalog = self.planner._build_local_dish_catalog(request)
        schedule = self.planner._build_dish_schedule(plan, request, catalog)
        breakfasts = [schedule[index]["breakfast"]["dish"] for index in range(5)]

        self.assertEqual(5, len(set(breakfasts)))
        self.assertEqual(1, breakfasts.count("鸭血粉丝汤"))

    def test_fake_llm_restaurant_is_replaced_by_real_amap_candidate(self):
        request = self._request()
        day = DayPlan(
            date="2026-08-01",
            day_index=0,
            description="",
            transportation="公共交通",
            accommodation="经济型酒店",
        )
        plan = TripPlan(
            city="南京",
            start_date="2026-08-01",
            end_date="2026-08-01",
            days=[day],
            overall_suggestions="",
        )
        candidate = {
            "id": "AMAP-REAL-1",
            "name": "真实高德餐厅",
            "address": "真实地址",
            "longitude": 118.8,
            "latitude": 32.0,
            "distance_m": 400,
            "anchor_name": "南京博物院",
            "dish_options": ["鸭血粉丝汤"],
            "dish_matches": ["鸭血粉丝汤"],
            "rating_available": True,
            "rating": 4.7,
            "estimated_cost": 28,
            "meal_score": 9.0,
        }
        day_meals = [{
            "day": 1,
            "meals": {
                "breakfast": [candidate],
                "lunch": [],
                "dinner": [],
            },
        }]
        response = """{"days":[{"day_index":0,"meals":[
            {"type":"breakfast","poi_id":"FAKE","name":"模型编造餐厅","recommended_dish":"虚构菜"}
        ]}]}"""

        result = self.planner._merge_meal_response(plan, response, request, day_meals)

        self.assertEqual("真实高德餐厅", result.days[0].meals[0].name)
        self.assertEqual("AMAP-REAL-1", result.days[0].meals[0].poi_id)
        self.assertEqual("鸭血粉丝汤", result.days[0].meals[0].recommended_dish)
        self.assertEqual("amap", result.days[0].meals[0].data_source)

    def test_missing_rating_does_not_receive_neutral_bonus(self):
        base = {
            "name": "本地特色餐厅",
            "address": "南京市",
            "distance_m": 500,
            "keyword_grounded": True,
        }
        rated = {**base, "rating": 4.7, "rating_available": True}
        missing = {**base, "rating": 0, "rating_available": False}
        dishes = ["盐水鸭"]

        self.assertGreater(
            self.planner._score_nearby_meal(rated, "南京", dishes),
            self.planner._score_nearby_meal(missing, "南京", dishes),
        )

    def test_keyword_search_cannot_bind_unrelated_breakfast_dish(self):
        unrelated = {
            "name": "小长安·品质川湘菜",
            "type": "中餐厅;川湘菜",
            "tag": "川湘菜",
            "address": "商业楼",
        }
        matched = {
            "name": "老城豆腐脑早餐店",
            "type": "中餐厅;特色小吃",
            "tag": "豆腐脑",
            "address": "老城街",
        }

        self.assertFalse(
            self.planner._is_valid_meal_candidate(
                unrelated, "breakfast", "豆腐脑", require_dish_evidence=True
            )
        )
        self.assertTrue(
            self.planner._is_valid_meal_candidate(
                matched, "breakfast", "豆腐脑", require_dish_evidence=True
            )
        )

    def test_unknown_hours_private_cuisine_cannot_be_breakfast(self):
        private_cuisine = {
            "name": "湖畔人家私房菜",
            "type": "中餐厅;鲁菜",
            "tag": "煎饼卷大葱",
            "address": "县前街32号",
            "opening_hours": "",
        }

        self.assertFalse(
            self.planner._is_valid_meal_candidate(
                private_cuisine,
                "breakfast",
                "煎饼卷大葱",
                require_dish_evidence=True,
            )
        )
    def test_breakfast_candidate_is_rejected_when_opening_hours_start_at_lunch(self):
        candidate = {
            "name": "老城豆腐脑",
            "type": "中餐厅;特色小吃",
            "tag": "豆腐脑",
            "address": "老城街",
            "opening_hours": "周一至周日 11:00-21:30",
        }

        self.assertFalse(
            self.planner._is_valid_meal_candidate(
                candidate, "breakfast", "豆腐脑", require_dish_evidence=True
            )
        )

    def test_breakfast_candidate_accepts_morning_opening_hours(self):
        candidate = {
            "name": "老城豆腐脑早餐店",
            "type": "中餐厅;特色小吃",
            "tag": "豆腐脑",
            "address": "老城街",
            "opening_hours": "06:30-10:30; 17:00-20:00",
        }

        self.assertTrue(
            self.planner._is_valid_meal_candidate(
                candidate, "breakfast", "豆腐脑", require_dish_evidence=True
            )
        )
        self.assertTrue(candidate["_meal_open_verified"])

    def test_search_switches_to_next_dish_when_target_has_only_fuzzy_results(self):
        request = self._request(departure_time="10:00")
        hotel = Hotel(
            name="测试酒店",
            address="测试地址",
            location=Location(longitude=118.8, latitude=32.0),
        )
        day = DayPlan(
            date="2026-08-01",
            day_index=0,
            description="",
            transportation="公共交通",
            accommodation="经济型酒店",
            hotel=hotel,
        )
        plan = TripPlan(
            city="南京",
            start_date="2026-08-01",
            end_date="2026-08-01",
            days=[day],
            overall_suggestions="",
        )

        def fake_search(_lng, _lat, radius=3000, keywords=""):
            if keywords == "牛肉锅贴":
                return [{
                    "id": "AMAP-POTSTICKER",
                    "name": "七家湾牛肉锅贴",
                    "type": "中餐厅;特色小吃",
                    "tag": "牛肉锅贴",
                    "address": "老城街",
                    "longitude": 118.8,
                    "latitude": 32.0,
                    "distance_m": 300,
                    "rating": 4.6,
                    "rating_available": True,
                    "estimated_cost": 22,
                }]
            if keywords:
                return [{
                    "id": "AMAP-UNRELATED",
                    "name": "小长安·品质川湘菜",
                    "type": "中餐厅;川湘菜",
                    "tag": "川湘菜",
                    "address": "商业楼",
                    "longitude": 118.8,
                    "latitude": 32.0,
                    "distance_m": 200,
                    "rating": 4.8,
                    "rating_available": True,
                    "estimated_cost": 54,
                }]
            return []

        self.planner._call_amap_around_search = fake_search
        result = self.planner._search_meals_for_plan(plan, request)

        self.assertEqual("牛肉锅贴", result[0]["target_dishes"]["breakfast"])
        self.assertEqual("七家湾牛肉锅贴", result[0]["meals"]["breakfast"][0]["name"])

    def test_degraded_plan_still_adds_meals_when_hotel_step_fails(self):
        request = self._request()
        day = DayPlan(
            date="2026-08-01",
            day_index=0,
            description="测试行程",
            transportation="公共交通",
            accommodation="经济型酒店",
        )
        plan = TripPlan(
            city="南京",
            start_date="2026-08-01",
            end_date="2026-08-01",
            days=[day],
            overall_suggestions="",
        )
        candidate = {
            "id": "AMAP-MEAL-1",
            "name": "真实高德餐厅",
            "address": "测试地址",
            "longitude": 118.8,
            "latitude": 32.0,
            "distance_m": 300,
            "rating": 4.6,
            "rating_available": True,
            "estimated_cost": 40,
            "dish_options": ["盐水鸭"],
            "dish_matches": ["盐水鸭"],
            "anchor_name": "测试景点",
        }
        meal_day = {
            "day": 1,
            "meals": {
                "breakfast": [candidate],
                "lunch": [],
                "dinner": [],
            },
            "dish_repeat_allowed": {},
        }

        def fail_hotel(_plan, _request):
            raise RuntimeError("hotel service unavailable")

        self.planner._select_hotel = fail_hotel
        self.planner._search_meals_for_plan = lambda _plan, _request: [meal_day]
        self.planner._recalculate_budget = lambda _plan, _request: None

        result = self.planner._best_effort_enrich_stay_and_meals(plan, request)

        self.assertIsNone(result.days[0].hotel)
        self.assertEqual(["真实高德餐厅"], [meal.name for meal in result.days[0].meals])

    def test_city_day_without_verified_restaurant_still_reserves_meal_times(self):
        request = self._request(departure_time="20:00")
        hotel = Hotel(
            name="测试酒店",
            location=Location(longitude=118.8, latitude=32.0),
        )
        day = DayPlan(
            date=request.start_date, day_index=0, description="市区行程",
            transportation="公共交通", accommodation="经济型酒店",
            hotel=hotel, attractions=[], meals=[],
        )
        plan = TripPlan(
            city=request.city, start_date=request.start_date,
            end_date=request.end_date, days=[day], overall_suggestions="",
        )

        self.planner._ensure_logistical_meals(plan, request, [{
            "day": 1,
            "meals": {"breakfast": [], "lunch": [], "dinner": []},
        }])

        self.assertEqual(
            {"breakfast", "lunch", "dinner"},
            {meal.type for meal in day.meals},
        )
        self.assertTrue(all(meal.data_source == "logistics_advice" for meal in day.meals))


if __name__ == "__main__":
    unittest.main()
