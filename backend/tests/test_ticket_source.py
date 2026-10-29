"""门票来源可追溯性的回归测试。"""

import unittest

from app.services.ticket_service import _select_source_url


class TicketSourceTests(unittest.TestCase):
    def test_rejects_source_for_a_different_similarly_named_place(self):
        url = _select_source_url([{
            "title": "西安大唐不夜城游览攻略",
            "content": "大雁塔附近的步行街",
            "url": "https://example.com/datang",
        }], "穿越大唐不夜城")

        self.assertEqual(url, "")

    def test_prefers_official_like_url_and_rejects_unsafe_protocol(self):
        url = _select_source_url([
            {"url": "javascript:alert(1)"},
            {"url": "https://travel.example.com/ticket"},
            {"url": "https://tourism.beijing.gov.cn/ticket"},
        ])
        self.assertEqual(url, "https://tourism.beijing.gov.cn/ticket")

    def test_returns_empty_string_when_no_web_source_exists(self):
        self.assertEqual(_select_source_url([{"url": ""}]), "")


if __name__ == "__main__":
    unittest.main()
