"""目的地动态研究与候选分层的回归测试。"""

import unittest

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import TripRequest


def _request(**overrides) -> TripRequest:
    payload = {
        "city": "济南",
        "start_date": "2026-09-10",
        "end_date": "2026-09-12",
        "travel_days": 3,
        "transportation": "公共交通",
        "accommodation": "舒适型酒店",
        "preferences": ["历史文化", "美食"],
    }
    payload.update(overrides)
    return TripRequest(**payload)


def _poi(name: str, poi_id: str, *, lng: float = 117.0, lat: float = 36.66,
         rating: float = 4.6, poi_type: str = "风景名胜") -> dict:
    return {
        "id": poi_id,
        "name": name,
        "address": "济南市",
        "longitude": lng,
        "latitude": lat,
        "type": poi_type,
        "typecode": "110000",
        "tag": "",
        "rating": rating,
        "importance": "",
        "photos": [],
    }


class DestinationResearchTests(unittest.TestCase):
    def setUp(self):
        self.planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        self.planner._destination_research_cache = {}

    def test_research_names_are_grounded_by_amap_before_becoming_candidates(self):
        self.planner._research_destination = lambda request: {
            "classic": ["趵突泉", "大明湖"],
            "preference": ["山东博物馆"],
            "local": ["曲水亭街"],
        }

        def search(keywords, city):
            catalog = {
                "趵突泉|大明湖": [
                    _poi("趵突泉景区", "BTQ"),
                    _poi("大明湖公园", "DMH"),
                    _poi("名称相似的虚假结果", "BAD"),
                ],
                "山东博物馆": [_poi("山东博物馆", "SDM", poi_type="博物馆")],
                "曲水亭街": [_poi("曲水亭街", "QST", poi_type="历史文化街区")],
            }
            return catalog.get(keywords, [])

        self.planner._call_mcp_search = search
        result = self.planner._search_researched_attractions(_request())

        self.assertEqual(
            {item["id"] for item in result},
            {"BTQ", "DMH", "SDM", "QST"},
        )
        self.assertNotIn("BAD", {item["id"] for item in result})
        tiers = {item["id"]: item["_candidate_tier"] for item in result}
        self.assertEqual(tiers["BTQ"], "classic")
        self.assertEqual(tiers["SDM"], "preference")

    def test_scenic_facility_cannot_verify_a_researched_attraction(self):
        self.planner._research_destination = lambda request: {
            "classic": ["黑虎泉"],
            "preference": [],
            "local": [],
        }
        self.planner._call_mcp_search = lambda keywords, city: [
            _poi("黑虎泉取水点", "WATER"),
            _poi("黑虎泉", "SPRING"),
        ]

        result = self.planner._search_researched_attractions(_request())

        self.assertEqual([item["id"] for item in result], ["SPRING"])

    def test_non_attraction_facilities_are_removed_before_scoring(self):
        pois = [
            {**_poi("黑虎泉取水点", "WATER"), "_candidate_tier": "classic"},
            {**_poi("黑虎泉", "SPRING"), "_candidate_tier": "classic"},
        ]
        self.planner._get_city_center = lambda city: (117.0, 36.66)

        selected = self.planner._score_and_filter(pois, [], 3, "济南")

        self.assertEqual([item["id"] for item in selected], ["SPRING"])

    def test_named_like_landmark_but_generic_leisure_venue_is_filtered(self):
        pois = [
            {
                **_poi(
                    "穿越大唐不夜城",
                    "FAKE-DTYC",
                    rating=0,
                    poi_type="体育休闲服务;体育休闲服务场所;体育休闲服务场所",
                ),
                "typecode": "080000",
                "_candidate_tier": "supplement",
            },
            {**_poi("西安钟楼", "BELL"), "_candidate_tier": "classic"},
        ]
        self.planner._get_city_center = lambda city: (108.948, 34.261)

        selected = self.planner._score_and_filter(pois, [], 3, "西安")

        self.assertEqual([item["id"] for item in selected], ["BELL"])

    def test_classics_and_preferences_win_without_city_hardcoding(self):
        pois = [
            {**_poi("趵突泉景区", "BTQ", rating=4.7), "_candidate_tier": "classic", "_search_rank": 5},
            {**_poi("大明湖公园", "DMH", rating=4.6), "_candidate_tier": "classic", "_search_rank": 6},
            {**_poi("山东博物馆", "SDM", rating=4.8, poi_type="博物馆"), "_candidate_tier": "preference", "_search_rank": 4},
            {**_poi("某某面包博物馆", "BREAD", rating=5.0, poi_type="博物馆"), "_candidate_tier": "supplement", "_search_rank": 0},
        ]
        self.planner._get_city_center = lambda city: (117.0, 36.66)

        selected = self.planner._score_and_filter(
            pois, ["历史文化", "美食"], 3, "济南"
        )
        by_id = {item["id"]: item for item in selected}

        self.assertTrue(by_id["BTQ"]["_is_core"])
        self.assertGreater(by_id["BTQ"]["score"], by_id["BREAD"]["score"])
        self.assertTrue(by_id["BREAD"]["_is_niche"])

    def test_nearby_independent_attractions_are_not_merged(self):
        pois = [
            {**_poi("趵突泉景区", "BTQ", lng=117.015, lat=36.661), "score": 10, "broad_category": "风景名胜"},
            {**_poi("五龙潭公园", "WLT", lng=117.010, lat=36.667), "score": 9, "broad_category": "公园广场"},
            {**_poi("泉城广场", "QCG", lng=117.021, lat=36.661), "score": 8, "broad_category": "公园广场"},
        ]

        groups = self.planner._group_attractions_by_area(pois)

        self.assertEqual(len(groups), 3)
        self.assertEqual(
            {group["display_name"] for group in groups},
            {"趵突泉景区", "五龙潭公园", "泉城广场"},
        )

    def test_amap_parent_id_merges_parent_and_internal_venue(self):
        pois = [
            {**_poi("济南动物园", "ZOO"), "score": 10},
            {
                **_poi("济南动物园-海洋馆", "AQUARIUM"),
                "parent": "ZOO",
                "childtype": "301",
                "score": 9,
            },
        ]

        groups = self.planner._group_attractions_by_area(pois)

        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0]["_strong_hierarchy"])
        self.assertEqual(
            {member["name"] for member in groups[0]["members"]},
            {"济南动物园", "济南动物园-海洋馆"},
        )

    def test_umbrella_is_hidden_but_independent_sibling_scenic_areas_remain(self):
        pois = [
            {**_poi("天下第一泉景区", "SPRINGS"), "score": 10},
            {
                **_poi("趵突泉景区", "BAOTU"),
                "parent": "SPRINGS", "childtype": "601", "score": 9,
            },
            {
                **_poi("大明湖景区", "DAMING"),
                "parent": "SPRINGS", "childtype": "601", "score": 8,
            },
        ]

        groups = self.planner._group_attractions_by_area(pois)

        self.assertEqual(
            {group["display_name"] for group in groups},
            {"趵突泉景区", "大明湖景区"},
        )

    def test_hidden_classic_umbrella_promotes_its_independent_children(self):
        pois = [
            {
                **_poi("夫子庙秦淮风光带", "QINHUAI"),
                "score": 12,
                "_candidate_tier": "classic",
                "_is_core": True,
                "_researched_name": "夫子庙秦淮风光带",
            },
            {
                **_poi("夫子庙", "FUZIMIAO"),
                "parent": "QINHUAI", "childtype": "601", "score": 8,
                "_candidate_tier": "local",
            },
            {
                **_poi("老门东", "LAOMENDONG"),
                "parent": "QINHUAI", "childtype": "601", "score": 7,
                "_candidate_tier": "local",
            },
        ]

        groups = self.planner._group_attractions_by_area(pois)

        self.assertEqual({group["display_name"] for group in groups}, {"夫子庙", "老门东"})
        self.assertTrue(all(group["_candidate_tier"] == "classic" for group in groups))
        self.assertTrue(all(group["_is_core"] for group in groups))
        self.assertTrue(all(group["_researched_name"] == "夫子庙秦淮风光带" for group in groups))


if __name__ == "__main__":
    unittest.main()
