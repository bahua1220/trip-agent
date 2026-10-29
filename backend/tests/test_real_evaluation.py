"""The real-provider harness itself is tested offline: no billable requests."""

import io
import json
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import real_eval_support as support
import run_real_evaluation as runner


def sample():
    request = support.load_cases("2030-11-04", ["single-default"])[0]["request"]
    request.update(travel_days=1, end_date=request["start_date"])
    attractions = [dict(name=name, poi_id=name, ticket_info={"opening_hours": "09:00-18:00"})
                   for name in ("总统府", "南京博物院")]
    meals = [dict(name=kind, type=kind, poi_id=kind, data_source="amap", opening_hours="06:00-22:00")
             for kind in ("breakfast", "lunch", "dinner")]
    timeline = []
    for kind, name, ref, start, end in (
        ("meal", "breakfast", 0, "08:00", "08:45"),
        ("attraction", "总统府", 0, "09:00", "11:00"),
        ("meal", "lunch", 1, "12:00", "13:00"),
        ("attraction", "南京博物院", 1, "14:00", "16:00"),
        ("meal", "dinner", 2, "18:00", "19:00"),
    ):
        timeline.append(dict(kind=kind, name=name, reference_index=ref,
                             start_time=start, end_time=end,
                             duration_minutes=support.minutes(end)-support.minutes(start)))
    plan = dict(city=request["city"], start_date=request["start_date"], end_date=request["end_date"],
                trip_mode="single_city", days=[dict(date=request["start_date"], day_index=0,
                    attractions=attractions, meals=meals, timeline=timeline)],
                quality_report=dict(passed=True, issues=[]), budget=dict(total=100, total_meals=100))
    return plan, request


class RealEvaluationAuditTests(unittest.TestCase):
    def check_codes(self, plan, request):
        return {item["code"] for item in support.audit_plan(plan, request)["findings"]}

    def test_three_cases_are_valid_and_date_adjustable(self):
        cases = support.load_cases("2030-11-04")
        self.assertEqual(3, len(cases))
        self.assertEqual([], cases[0]["request"]["preferences"])
        self.assertIsNone(cases[0]["request"]["arrival_time"])
        self.assertEqual("relaxed", cases[1]["request"]["travel_pace"])
        self.assertEqual("2030-11-08", cases[2]["request"]["end_date"])
        self.assertEqual(["南昌", "景德镇"], cases[2]["request"]["destinations"])
        self.assertEqual("景德镇北站", cases[2]["request"]["departure_location"])
        with self.assertRaisesRegex(ValueError, "未知用例"):
            support.load_cases("2030-11-04", ["no-such-case"])

    def test_valid_result_is_automatically_passed_not_human_approved(self):
        plan, request = sample()
        checks = support.audit_plan(plan, request)
        self.assertTrue(checks["passed"], checks)
        self.assertEqual("pending", checks["human_review"])
        self.assertEqual(["总统府", "南京博物院"], checks["days"][0]["attractions"])

    def test_four_day_regression_is_opt_in_and_does_not_expand_default_paid_batch(self):
        defaults = support.load_cases("2030-11-04")
        self.assertEqual(3, len(defaults))
        self.assertNotIn("single-four-day", {case["id"] for case in defaults})
        selected = support.load_cases("2030-11-04", ["single-four-day"])
        self.assertEqual(1, len(selected))
        self.assertEqual(4, selected[0]["request"]["travel_days"])
        self.assertEqual("07:01", selected[0]["request"]["departure_time"])

    def test_instantaneous_hotel_marker_can_omit_end_time(self):
        plan, request = sample()
        plan["days"][0]["timeline"].insert(0, dict(kind="hotel", name="酒店出发",
            start_time="08:00", end_time=None, duration_minutes=0))
        self.assertTrue(support.audit_plan(plan, request)["passed"])

    def test_late_lunch_closed_restaurant_and_overlap_detected_independently(self):
        plan, request = sample()
        plan["days"][0]["timeline"][2].update(start_time="15:00", end_time="16:00")
        plan["days"][0]["meals"][1]["opening_hours"] = "10:00-14:00,17:00-21:00"
        codes = self.check_codes(plan, request)
        self.assertTrue({"meal_window", "opening_conflict", "overlap"} <= codes)

    def test_missing_meals_unreferenced_attractions_and_stale_name(self):
        plan, request = sample()
        day = plan["days"][0]
        day["meals"].pop()
        day["timeline"].pop()
        day["attractions"].append(deepcopy(day["attractions"][0]))
        day["timeline"][1]["name"] = "旧名称"
        self.assertTrue({"missing_meals", "duplicate_attraction", "attraction_unscheduled", "stale_timeline_name"}
                        <= self.check_codes(plan, request))

    def test_clock_overflow_and_broken_reference(self):
        plan, request = sample()
        plan["days"][0]["timeline"][0].update(reference_index=99, end_time="23:59")
        plan["days"][0]["timeline"][1]["start_time"] = "25:00"
        self.assertTrue({"midnight_clamp", "duration_mismatch", "broken_reference", "untimed_event"}
                        <= self.check_codes(plan, request))

    def test_hours_unknown_not_passed_as_verified_and_overnight_handled(self):
        self.assertIsNone(support.hours_cover("周一闭馆，周二至周日09:00-17:00", 600, 660))
        self.assertIsNone(support.hours_cover("", 600, 660))
        self.assertTrue(support.hours_cover("18:00-02:00", 30, 90))
        self.assertFalse(support.hours_cover("10:30-14:00,16:30-21:00", 890, 950))
        self.assertTrue(support.hours_cover("24小时营业", 0, 1439))

    def test_advice_meal_and_takeaway_not_silently_accepted(self):
        plan, request = sample()
        plan["days"][0]["meals"][0].update(name="早餐外卖专营", data_source="logistics_advice", poi_id=None)
        self.assertTrue({"meal_not_restaurant", "takeaway_only"} <= self.check_codes(plan, request))

    def test_cross_city_requires_named_stations_and_access_legs(self):
        plan, request = sample()
        day = plan["days"][0]
        day["route_segment"] = dict(origin="南京", destination="苏州")
        self.assertIn("missing_intercity", self.check_codes(plan, request))
        day["timeline"].append(dict(kind="transport", name="高铁参考", from_name="南京南站", to_name="苏州站",
                                    start_time="19:00", end_time="20:00", duration_minutes=60,
                                    is_intercity=True, transport_mode="高铁"))
        self.assertTrue({"unsplit_intercity", "missing_station_access", "rail_reference"}
                        <= self.check_codes(plan, request))

    def test_selected_departure_station_and_buffer_checked(self):
        plan, request = sample()
        request.update(departure_time="20:00", departure_location="南京南站", departure_transport="高铁")
        plan["days"][0]["timeline"].append(dict(kind="hub", hub_role="trip_departure", name="南京站",
            start_time="19:45", end_time="19:45", duration_minutes=0))
        self.assertTrue({"wrong_trip_hub", "departure_buffer", "missing_hub_access"}
                        <= self.check_codes(plan, request))

    def test_independent_audit_catches_missing_checkout_even_when_production_passed(self):
        plan, request = sample()
        request.update(departure_time="20:00", departure_transport="高铁", departure_location="南京南站")
        day = plan["days"][0]
        day["hotel"] = {"name": "测试酒店"}
        day["timeline"].append(dict(kind="hotel", name="测试酒店", start_time="19:00", duration_minutes=0))
        self.assertIn("missing_checkout_buffer", self.check_codes(plan, request))
        day["timeline"][-1].update(duration_minutes=20, end_time="19:20")
        self.assertNotIn("missing_checkout_buffer", self.check_codes(plan, request))

    def test_returned_production_errors_remain_visible(self):
        plan, request = sample()
        plan["quality_report"] = dict(passed=False, issues=[dict(code="custom", severity="error", message="规则失败")])
        checks = support.audit_plan(plan, request)
        self.assertFalse(checks["passed"])
        self.assertTrue(any(item["origin"] == "production_validator" for item in checks["findings"]))

    def test_redaction_before_log_and_json_storage(self):
        redact = support.Redactor({"AMAP_API_KEY": "map-secret-123", "LLM_API_KEY": "llm-secret-456"})
        value = redact("https://example.test/?key=map-secret-123&token=other-secret Authorization: Bearer abc.xyz llm-secret-456 https://user:pass@example.test")
        for secret in ("map-secret-123", "other-secret", "abc.xyz", "llm-secret-456", "user:pass"):
            self.assertNotIn(secret, value)
        stream = io.StringIO()
        writer = runner.SafeLogWriter(stream, redact)
        writer.write("map-secret-")
        writer.write("123\n")
        self.assertNotIn("map-secret-123", stream.getvalue())
        self.assertEqual("[REDACTED]", redact.data({"value": ["llm-secret-456"]})["value"][0])


class RealEvaluationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.network = self.enterContext(patch.object(socket.socket, "connect", side_effect=AssertionError("禁止联网")))
        self.enterContext(patch.object(runner, "load_environment"))

    def tearDown(self):
        self.assertEqual(0, self.network.call_count, "工具测试不得访问真实接口")

    def test_preview_writes_three_requests_no_worker_no_generation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "run_worker") as worker, redirect_stdout(io.StringIO()):
            self.assertEqual(0, runner.main(["--start-date", "2030-11-04", "--output-dir", directory]))
            worker.assert_not_called()
            output = next(Path(directory).iterdir())
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual({"not_run": 3}, report["summary"])
            self.assertEqual("preview-no-network", report["mode"])
            self.assertFalse(list(output.glob("*/tripPlan.json")))
            self.assertEqual(3, len(list(output.glob("*/request.json"))))

    def test_paid_execution_requires_both_flags_including_hidden_worker(self):
        with redirect_stderr(io.StringIO()), patch.object(runner, "execute_worker") as worker:
            for args in (["--execute"], ["--confirm-paid"], ["--worker-dir", "unused"]):
                with self.assertRaises(SystemExit) as error:
                    runner.main(args)
                self.assertEqual(2, error.exception.code)
            worker.assert_not_called()

    def test_missing_keys_fail_before_worker_start(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(runner, "run_worker") as worker, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                runner.main(["--execute", "--confirm-paid", "--start-date", "2030-11-04"])
            self.assertEqual(2, error.exception.code)
            worker.assert_not_called()

    def test_failure_stops_later_spending_and_remains_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "preflight", return_value={"llm_key_configured": True, "amap_key_configured": True}), patch.object(runner, "run_worker", return_value=dict(status="error", error="接口配额不足")) as worker, redirect_stdout(io.StringIO()):
            result = runner.main(["--execute", "--confirm-paid", "--start-date", "2030-11-04", "--output-dir", directory])
            self.assertEqual(1, result)
            self.assertEqual(1, worker.call_count)
            report = json.loads(next(Path(directory).glob("*/report.json")).read_text(encoding="utf-8"))
            self.assertEqual(["error", "not_run", "not_run"], [case["status"] for case in report["cases"]])

    def test_worker_timeout_kills_process_and_does_not_retry(self):
        process = Mock()
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory, patch.object(runner.subprocess, "Popen", return_value=process) as start, patch.object(runner.time, "monotonic", side_effect=[0, 2, 3]):
            outcome = runner.run_worker(Path(directory), 1)
            self.assertEqual("timeout", outcome["status"])
            start.assert_called_once()
            self.assertGreaterEqual(process.kill.call_count, 1)
            self.assertTrue((Path(directory) / "result.json").is_file())

    def test_report_escapes_external_names_and_keeps_plan_links(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            case = support.load_cases("2030-11-04", ["single-default"])[0]
            folder = output / case["id"]
            folder.mkdir()
            plan, request = sample()
            plan["days"][0]["timeline"][0]["name"] = "<script>alert(1)</script>"
            support.write_json(folder / "tripPlan.json", plan)
            case.update(status="passed", checks=support.audit_plan(plan, request))
            support.render_report(output, dict(generated_at=support.now(), scope=support.SCOPE, mode="test", cases=[case]))
            content = (output / "report.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>", content)
            self.assertIn("&lt;script&gt;", content)
            self.assertIn("single-default/tripPlan.json", content)
            self.assertIn("待完成", content)

    def test_worker_success_saves_plan_checks_progress_and_redacted_log(self):
        plan, request = sample()
        result_plan = Mock()
        result_plan.model_dump.return_value = plan
        planner = Mock()
        planner.plan_trip.return_value = result_plan
        with tempfile.TemporaryDirectory() as directory, patch("app.agents.trip_planner_agent.get_trip_planner_agent", return_value=planner), patch.dict("os.environ", {"AMAP_API_KEY": "dummy-secret-123"}), patch("app.services.logger.logger.remove"), patch("app.services.logger.logger.add"):
            folder = Path(directory)
            support.write_json(folder / "request.json", request)
            def dump(**kwargs):
                print("dummy-secret-123")
                return plan
            result_plan.model_dump.side_effect = dump
            self.assertEqual(0, runner.execute_worker(folder))
            self.assertTrue(json.loads((folder / "checks.json").read_text(encoding="utf-8"))["passed"])
            self.assertEqual("passed", json.loads((folder / "result.json").read_text(encoding="utf-8"))["status"])
            self.assertEqual(plan, json.loads((folder / "tripPlan.json").read_text(encoding="utf-8")))
            self.assertNotIn("dummy-secret-123", (folder / "run.log").read_text(encoding="utf-8"))
            self.assertEqual(2, len((folder / "progress.jsonl").read_text(encoding="utf-8").splitlines()))

    def test_worker_degraded_preserves_plan_not_a_success(self):
        from app.agents.trip_planner_agent import TripPlanningDegraded

        plan, request = sample()
        result_plan = Mock()
        result_plan.model_dump.return_value = plan
        planner = Mock()
        planner.plan_trip.side_effect = TripPlanningDegraded(result_plan, "数据不完整")
        with tempfile.TemporaryDirectory() as directory, patch("app.agents.trip_planner_agent.get_trip_planner_agent", return_value=planner), patch("app.services.logger.logger.remove"), patch("app.services.logger.logger.add"):
            folder = Path(directory)
            support.write_json(folder / "request.json", request)
            self.assertEqual(1, runner.execute_worker(folder))
            self.assertTrue((folder / "tripPlan.json").is_file())
            self.assertEqual("degraded", json.loads((folder / "result.json").read_text(encoding="utf-8"))["status"])

    def test_worker_failure_redacts_error_and_keeps_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory, patch("app.agents.trip_planner_agent.get_trip_planner_agent", side_effect=RuntimeError("token dummy-secret-456 invalid")), patch.dict("os.environ", {"LLM_API_KEY": "dummy-secret-456"}), patch("app.services.logger.logger.remove"), patch("app.services.logger.logger.add"):
            folder = Path(directory)
            support.write_json(folder / "request.json", sample()[1])
            self.assertEqual(1, runner.execute_worker(folder))
            self.assertEqual("error", json.loads((folder / "result.json").read_text(encoding="utf-8"))["status"])
            self.assertFalse((folder / "tripPlan.json").exists())
            for name in ("result.json", "run.log"):
                self.assertNotIn("dummy-secret-456", (folder / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
