"""距离计算的回归测试。"""

import unittest

from app.agents.trip_planner_agent import MultiAgentTripPlanner


class HaversineDistanceTests(unittest.TestCase):
    def test_nearby_beijing_landmarks_are_about_one_kilometre_apart(self):
        """故宫到天安门的直线距离应接近 1 km。"""
        distance = MultiAgentTripPlanner._haversine_distance(
            116.397128, 39.916527,
            116.397477, 39.908722,
        )
        self.assertGreater(distance, 0.5)
        self.assertLess(distance, 1.5)

    def test_beijing_to_shanghai_is_about_one_thousand_kilometres(self):
        """防止经纬度顺序被意外调换。"""
        distance = MultiAgentTripPlanner._haversine_distance(
            116.4074, 39.9042,
            121.4737, 31.2304,
        )
        self.assertGreater(distance, 1_000)
        self.assertLess(distance, 1_150)


if __name__ == "__main__":
    unittest.main()
