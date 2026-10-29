"""Web 行程生成入口的登录保护测试。"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import trip
from app.api.routes.auth import current_user
from app.services.trip_task_service import (
    ActiveTripTaskExists,
    TripTaskDailyLimitExceeded,
)


class TripAuthGuardTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(trip.router, prefix="/api")
        self.app = app
        self.client = TestClient(app)

    @staticmethod
    def _payload():
        return {
            "city": "南京",
            "start_date": "2026-08-01",
            "end_date": "2026-08-03",
            "travel_days": 3,
            "transportation": "公共交通",
            "accommodation": "舒适型酒店",
        }

    def test_web_plan_endpoint_requires_login(self):
        response = self.client.post(
            "/api/trip/plan/authenticated",
            json=self._payload(),
        )

        self.assertIn(response.status_code, {401, 403})

    def test_legacy_plan_endpoint_also_requires_login(self):
        response = self.client.post(
            "/api/trip/plan",
            json=self._payload(),
        )
        self.assertIn(response.status_code, {401, 403})

    def test_task_status_endpoint_requires_login(self):
        response = self.client.get("/api/trip/plan/task-id")
        self.assertIn(response.status_code, {401, 403})

    def test_active_task_conflict_returns_409(self):
        self.app.dependency_overrides[current_user] = lambda: {"id": "user-1"}
        service = Mock()
        service.create_task.side_effect = ActiveTripTaskExists()
        with (
            patch.object(trip, "get_trip_task_service", return_value=service),
            patch.object(trip, "check_request_content", new=AsyncMock()),
        ):
            response = self.client.post(
                "/api/trip/plan/authenticated",
                json=self._payload(),
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn("正在生成", response.json()["detail"])

    def test_daily_limit_returns_429(self):
        self.app.dependency_overrides[current_user] = lambda: {"id": "user-1"}
        service = Mock()
        service.create_task.side_effect = TripTaskDailyLimitExceeded()
        with (
            patch.object(trip, "get_trip_task_service", return_value=service),
            patch.object(trip, "check_request_content", new=AsyncMock()),
        ):
            response = self.client.post(
                "/api/trip/plan/authenticated",
                json=self._payload(),
            )
        self.assertEqual(response.status_code, 429)
        self.assertIn("次数", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
