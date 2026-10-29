"""Tests for the quota-conscious Tavily rail fallback."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import intercity_transport_service as service


class IntercityTransportServiceTests(unittest.TestCase):
    def setUp(self):
        service._memory_cache.clear()
        service._tavily_unavailable = False

    def test_duration_extraction_ignores_driving_comparison(self):
        durations, urls = service._duration_candidates([{
            "title": "大理到丽江交通",
            "content": "自驾约2小时；动车最快1小时32分，部分列车约2小时10分。",
            "url": "https://example.test/rail",
        }], 180.5)

        self.assertEqual([92, 130], durations)
        self.assertEqual(["https://example.test/rail"], urls)

    def test_successful_city_pair_is_reused_from_persistent_cache(self):
        payload = {
            "answer": "大理到丽江动车通常约1小时40分钟。",
            "results": [{
                "title": "大理到丽江动车参考",
                "url": "https://example.test/rail",
                "content": "大理站到丽江站动车运行时间约1小时40分钟。",
            }],
        }
        requester = Mock(return_value=payload)
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "rail-cache.json"
            with (
                patch.object(service, "_cache_path", return_value=cache_path),
                patch.object(
                    service,
                    "get_settings",
                    return_value=SimpleNamespace(tavily_api_key="test-key"),
                ),
                patch.object(service, "request_json", requester),
            ):
                first = service.research_intercity_rail_duration(
                    "大理", "丽江", 180.5
                )
                service._memory_cache.clear()
                second = service.research_intercity_rail_duration(
                    "大理", "丽江", 180.5
                )

        self.assertEqual(100, first["duration_minutes"])
        self.assertEqual(first, second)
        self.assertEqual(1, requester.call_count)

    def test_grounded_llm_extracts_chinese_duration_and_explicit_stations(self):
        payload = {
            "answer": "可从景德镇北站乘动车前往婺源站，全程约九十分钟。",
            "results": [],
        }
        requester = Mock(return_value=payload)
        llm = Mock()
        llm.invoke.return_value = SimpleNamespace(content=(
            '{"reliable":true,"duration_min_minutes":90,'
            '"duration_max_minutes":90,"departure_station":"景德镇北站",'
            '"arrival_station":"婺源站"}'
        ))
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(
                    service, "_cache_path",
                    return_value=Path(directory) / "rail-cache.json",
                ),
                patch.object(
                    service,
                    "get_settings",
                    return_value=SimpleNamespace(tavily_api_key="test-key"),
                ),
                patch.object(service, "request_json", requester),
                patch("app.services.llm_service.get_llm", return_value=llm),
            ):
                result = service.research_intercity_rail_duration(
                    "景德镇", "婺源", 72.3
                )

        self.assertEqual(90, result["duration_minutes"])
        self.assertEqual("景德镇北站", result["departure_station"])
        self.assertEqual("婺源站", result["arrival_station"])


if __name__ == "__main__":
    unittest.main()
