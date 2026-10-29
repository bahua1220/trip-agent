"""Tests for the in-memory background trip task service."""

import time
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from app.models.schemas import QualityIssue, QualityReport, TripPlan, TripRequest
from app.agents.trip_planner_agent import MultiAgentTripPlanner, TripPlanningDegraded, TripPlanningInfeasible
from app.services.trip_task_service import (
    ActiveTripTaskExists,
    MemoryTripTaskStore,
    TripTaskDailyLimitExceeded,
    TripTaskService,
)


class _FakePlanner:
    def plan_trip(self, request, progress_callback=None):
        if progress_callback:
            progress_callback(35, "Searching attractions")
            progress_callback(80, "Checking tickets")
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=[],
            overall_suggestions="Test plan",
        )


class _DegradedPlanner:
    def plan_trip(self, request, progress_callback=None):
        plan = TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=[],
            overall_suggestions="Partial plan",
        )
        raise TripPlanningDegraded(plan, "Partial data available")


class _QualityFailedPlanner:
    def plan_trip(self, request, progress_callback=None):
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=[],
            overall_suggestions="Test plan",
            quality_report=QualityReport(
                passed=False,
                issues=[QualityIssue(
                    code="test_conflict",
                    severity="error",
                    message="Test conflict",
                )],
            ),
        )


class TripTaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryTripTaskStore()
        self.saved_history = []
        self.service = TripTaskService(
            planner_factory=_FakePlanner,
            max_workers=1,
            store=self.store,
            history_saver=lambda user_id, entry_id, plan: self.saved_history.append(
                (user_id, entry_id, plan)
            ),
        )
        self.user_id = "user-1"
        self.request = TripRequest(
            city="Hangzhou",
            start_date="2026-08-01",
            end_date="2026-08-01",
            travel_days=1,
            transportation="public transport",
            accommodation="budget hotel",
        )

    def tearDown(self):
        self.service.shutdown()

    def test_task_returns_progress_and_result(self):
        task = self.service.create_task(self.request, self.user_id)
        for _ in range(50):
            status = self.service.get_task(task.task_id, self.user_id)
            if status and status.status in {"succeeded", "failed"}:
                break
            time.sleep(0.01)

        self.assertIsNotNone(status)
        self.assertEqual(status.status, "succeeded")
        self.assertEqual(status.progress, 100)
        self.assertEqual(status.result.city, "Hangzhou")
        self.assertEqual(self.saved_history[0][0], self.user_id)
        self.assertEqual(self.saved_history[0][1], task.task_id)

    def test_infeasible_route_is_failed_without_unsafe_result_or_history(self):
        fake = Mock()
        fake.plan_trip.side_effect = TripPlanningInfeasible("14:00发车，但预计16:10到站，请调整路线")
        self.service._planner_factory = lambda: fake
        task = self.service.create_task(self.request, self.user_id)
        for _ in range(50):
            status = self.service.get_task(task.task_id, self.user_id)
            if status.status == "failed":
                break
            time.sleep(.01)
        self.assertEqual("failed", status.status)
        self.assertIsNone(status.result)
        self.assertIn("14:00", status.error)
        self.assertEqual([], self.saved_history)

    def test_unknown_task_returns_none(self):
        self.assertIsNone(self.service.get_task("missing", self.user_id))

    def test_degraded_plan_is_not_reported_as_success(self):
        service = TripTaskService(
            planner_factory=_DegradedPlanner,
            max_workers=1,
            store=MemoryTripTaskStore(),
            history_saver=None,
        )
        try:
            task = service.create_task(self.request, self.user_id)
            for _ in range(50):
                status = service.get_task(task.task_id, self.user_id)
                if status and status.status in {"succeeded", "degraded", "failed"}:
                    break
                time.sleep(0.01)

            self.assertIsNotNone(status)
            self.assertEqual(status.status, "degraded")
            self.assertEqual(status.result.city, "Hangzhou")
        finally:
            service.shutdown()

    def test_quality_failed_plan_is_reported_as_needing_confirmation(self):
        service = TripTaskService(
            planner_factory=_QualityFailedPlanner,
            max_workers=1,
            store=MemoryTripTaskStore(),
            history_saver=None,
        )
        try:
            task = service.create_task(self.request, self.user_id)
            for _ in range(50):
                status = service.get_task(task.task_id, self.user_id)
                if status and status.status in {"succeeded", "degraded", "failed"}:
                    break
                time.sleep(0.01)

            self.assertEqual(status.status, "degraded")
            self.assertIn("需要确认", status.message)
            self.assertIsNotNone(status.result)
        finally:
            service.shutdown()

    def test_task_is_visible_only_to_owner(self):
        task = self.service.create_task(self.request, self.user_id)
        self.assertIsNone(self.service.get_task(task.task_id, "another-user"))

    def test_only_one_active_task_per_user(self):
        store = MemoryTripTaskStore()
        store.create("task-1", self.user_id, self.request, daily_limit=10)
        with self.assertRaises(ActiveTripTaskExists):
            store.create("task-2", self.user_id, self.request, daily_limit=10)

    def test_daily_limit_counts_terminal_tasks(self):
        store = MemoryTripTaskStore()
        store.create("task-1", self.user_id, self.request, daily_limit=2)
        store.finish("task-1", "succeeded", None, "done")
        store.create("task-2", self.user_id, self.request, daily_limit=2)
        store.finish("task-2", "degraded", None, "partial")
        with self.assertRaises(TripTaskDailyLimitExceeded):
            store.create("task-3", self.user_id, self.request, daily_limit=2)

    def test_failed_tasks_do_not_consume_daily_limit(self):
        store = MemoryTripTaskStore()
        store.create("task-1", self.user_id, self.request, daily_limit=1)
        store.finish("task-1", "failed", None, "failed")

        replacement = store.create(
            "task-2", self.user_id, self.request, daily_limit=1
        )

        self.assertEqual("queued", replacement.status)

    def test_daily_limit_resets_at_beijing_midnight(self):
        store = MemoryTripTaskStore()
        before_midnight = datetime(2026, 9, 3, 15, 59, tzinfo=timezone.utc)
        after_midnight = datetime(2026, 9, 3, 16, 1, tzinfo=timezone.utc)

        with patch(
            "app.services.trip_task_store._utc_now",
            return_value=before_midnight,
        ):
            store.create("task-before-midnight", self.user_id, self.request, daily_limit=1)
            store.finish("task-before-midnight", "succeeded", None, "done")

        with patch(
            "app.services.trip_task_store._utc_now",
            return_value=after_midnight,
        ):
            replacement = store.create(
                "task-after-midnight", self.user_id, self.request, daily_limit=1
            )

        self.assertEqual("queued", replacement.status)

    def test_queued_task_is_recovered_by_new_service(self):
        store = MemoryTripTaskStore()
        task = store.create("recover-me", self.user_id, self.request, daily_limit=10)
        service = TripTaskService(
            planner_factory=_FakePlanner,
            max_workers=1,
            store=store,
            history_saver=None,
            recover_pending=True,
        )
        try:
            for _ in range(50):
                status = service.get_task(task.task_id, self.user_id)
                if status and status.status == "succeeded":
                    break
                time.sleep(0.01)
            self.assertEqual(status.status, "succeeded")
        finally:
            service.shutdown()

    def test_partial_plan_uses_real_candidate_coordinates(self):
        planner = MultiAgentTripPlanner.__new__(MultiAgentTripPlanner)
        plan = planner._create_partial_plan(
            self.request,
            [{
                "display_name": "West Lake",
                "address": "Hangzhou",
                "longitude": 120.155,
                "latitude": 30.274,
                "category": "nature",
                "id": "real-poi-1",
            }],
            [],
        )

        attraction = plan.days[0].attractions[0]
        self.assertEqual(attraction.name, "West Lake")
        self.assertEqual(attraction.location.longitude, 120.155)
        self.assertEqual(attraction.location.latitude, 30.274)
        self.assertTrue(any(issue.code == "partial_plan" for issue in plan.quality_report.issues))


if __name__ == "__main__":
    unittest.main()
