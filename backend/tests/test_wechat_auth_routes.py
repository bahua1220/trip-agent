import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth


USER = {
    "id": "user-1",
    "username": "Bahua",
    "is_wechat_only": False,
    "has_wechat_identity": True,
}


def _client():
    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    return TestClient(app)


def test_wechat_login_returns_session():
    with (
        patch.object(auth, "login_with_wechat", new=AsyncMock(return_value=USER)) as login,
        patch.object(auth, "token_for", return_value="token"),
        patch.object(auth, "record_user_consent") as consent,
    ):
        response = _client().post(
            "/api/auth/wechat/login",
            json={"code": "wx-code", "consent_version": "2026-08-05"},
        )

    assert response.status_code == 200
    assert response.json()["access_token"] == "token"
    assert response.json()["user"] == USER
    login.assert_awaited_once_with("wx-code")
    consent.assert_called_once_with("user-1", "2026-08-05", "miniapp")


def test_wechat_bind_uses_authenticated_user():
    with (
        patch.object(auth, "user_from_token", return_value={"id": "user-1"}),
        patch.object(auth, "bind_wechat_identity", new=AsyncMock(return_value=USER)) as bind,
        patch.object(auth, "token_for", return_value="token"),
    ):
        response = _client().post(
            "/api/auth/wechat/bind",
            json={"code": "wx-code"},
            headers={"Authorization": "Bearer current-token"},
        )

    assert response.status_code == 200
    bind.assert_awaited_once_with("user-1", "wx-code")


def test_wechat_merge_uses_existing_account_credentials():
    with (
        patch.object(auth, "user_from_token", return_value={"id": "wechat-user"}),
        patch.object(auth, "merge_wechat_user", return_value=USER) as merge,
        patch.object(auth, "token_for", return_value="token"),
    ):
        response = _client().post(
            "/api/auth/wechat/merge",
            json={"username": "Bahua", "password": "password123"},
            headers={"Authorization": "Bearer current-token"},
        )

    assert response.status_code == 200
    merge.assert_called_once_with("wechat-user", "Bahua", "password123")


def test_wechat_code_must_not_be_empty():
    response = _client().post("/api/auth/wechat/login", json={"code": ""})
    assert response.status_code == 422


def test_delete_account_uses_authenticated_user():
    with (
        patch.object(auth, "user_from_token", return_value={"id": "user-1"}),
        patch.object(auth, "delete_user_account") as delete,
    ):
        response = _client().request(
            "DELETE",
            "/api/auth/account",
            json={"confirm": "DELETE"},
            headers={"Authorization": "Bearer current-token"},
        )

    assert response.status_code == 204
    delete.assert_called_once_with("user-1")


def test_delete_account_requires_explicit_confirmation():
    response = _client().request(
        "DELETE",
        "/api/auth/account",
        json={"confirm": "NO"},
        headers={"Authorization": "Bearer current-token"},
    )
    assert response.status_code == 422


class WechatAuthRouteUnittestTests(unittest.TestCase):
    """让项目当前的 unittest discover 也执行微信账号相关用例。"""

    def test_wechat_login_and_consent(self):
        with (
            patch.object(auth, "login_with_wechat", new=AsyncMock(return_value=USER)) as login,
            patch.object(auth, "token_for", return_value="token"),
            patch.object(auth, "record_user_consent") as consent,
        ):
            response = _client().post(
                "/api/auth/wechat/login",
                json={"code": "wx-code", "consent_version": "2026-08-05"},
            )
        self.assertEqual(response.status_code, 200)
        login.assert_awaited_once_with("wx-code")
        consent.assert_called_once_with("user-1", "2026-08-05", "miniapp")

    def test_wechat_binding(self):
        with (
            patch.object(auth, "user_from_token", return_value={"id": "user-1"}),
            patch.object(auth, "bind_wechat_identity", new=AsyncMock(return_value=USER)) as bind,
            patch.object(auth, "token_for", return_value="token"),
        ):
            response = _client().post(
                "/api/auth/wechat/bind",
                json={"code": "wx-code"},
                headers={"Authorization": "Bearer current-token"},
            )
        self.assertEqual(response.status_code, 200)
        bind.assert_awaited_once_with("user-1", "wx-code")

    def test_account_deletion(self):
        with (
            patch.object(auth, "user_from_token", return_value={"id": "user-1"}),
            patch.object(auth, "delete_user_account") as delete,
        ):
            response = _client().request(
                "DELETE",
                "/api/auth/account",
                json={"confirm": "DELETE"},
                headers={"Authorization": "Bearer current-token"},
            )
        self.assertEqual(response.status_code, 204)
        delete.assert_called_once_with("user-1")
