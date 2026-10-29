import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import wechat_content_security as security


def test_request_text_collects_user_editable_fields():
    request = SimpleNamespace(
        city="江西",
        region=None,
        start_location="南昌",
        end_location="景德镇",
        free_text_input="想看古村和陶瓷",
        preferences=["人文", "美食"],
        destinations=["南昌", "婺源", "景德镇"],
        nodes=None,
    )

    content = security._request_text(request)

    assert "江西" in content
    assert "想看古村和陶瓷" in content
    assert "婺源" in content


def test_wechat_code_is_exchanged_and_checked():
    request = SimpleNamespace(
        city="南京",
        region=None,
        start_location=None,
        end_location=None,
        free_text_input="想去博物馆",
        preferences=[],
        destinations=[],
        nodes=[],
        wechat_content_check_code="fresh-code",
    )
    settings = SimpleNamespace(
        wechat_content_security_enabled=True,
        wechat_app_id="app-id",
    )

    with (
        patch.object(security, "get_settings", return_value=settings),
        patch.object(
            security,
            "exchange_wechat_code",
            new=AsyncMock(return_value={"app_id": "app-id", "open_id": "open-id"}),
        ) as exchange,
        patch.object(security, "_wechat_check", new=AsyncMock()) as check,
    ):
        asyncio.run(security.check_request_content(request, "user-1"))

    exchange.assert_awaited_once_with("fresh-code")
    checked_content, checked_app_id, checked_open_id = check.await_args.args
    assert "南京" in checked_content
    assert checked_app_id == "app-id"
    assert checked_open_id == "open-id"


class WechatContentSecurityUnittestTests(unittest.TestCase):
    def test_user_editable_fields_are_collected(self):
        request = SimpleNamespace(
            city="江西",
            region=None,
            start_location="南昌",
            end_location="景德镇",
            free_text_input="想看古村和陶瓷",
            preferences=["人文", "美食"],
            destinations=["南昌", "婺源", "景德镇"],
            nodes=None,
        )
        content = security._request_text(request)
        self.assertIn("江西", content)
        self.assertIn("想看古村和陶瓷", content)
        self.assertIn("婺源", content)

    def test_fresh_wechat_code_is_used_for_content_check(self):
        request = SimpleNamespace(
            city="南京",
            region=None,
            start_location=None,
            end_location=None,
            free_text_input="想去博物馆",
            preferences=[],
            destinations=[],
            nodes=[],
            wechat_content_check_code="fresh-code",
        )
        settings = SimpleNamespace(
            wechat_content_security_enabled=True,
            wechat_app_id="app-id",
        )
        with (
            patch.object(security, "get_settings", return_value=settings),
            patch.object(
                security,
                "exchange_wechat_code",
                new=AsyncMock(return_value={"app_id": "app-id", "open_id": "open-id"}),
            ) as exchange,
            patch.object(security, "_wechat_check", new=AsyncMock()) as check,
        ):
            asyncio.run(security.check_request_content(request, "user-1"))

        exchange.assert_awaited_once_with("fresh-code")
        checked_content, checked_app_id, checked_open_id = check.await_args.args
        self.assertIn("南京", checked_content)
        self.assertEqual(checked_app_id, "app-id")
        self.assertEqual(checked_open_id, "open-id")
