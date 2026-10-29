"""Prepare a small real-provider batch; network execution requires two explicit flags."""

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import RLock
from uuid import uuid4


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from real_eval_support import (
    Redactor, SHANGHAI, SCOPE, audit_plan, load_cases, now, render_report, write_json,
)


def load_environment():
    from dotenv import load_dotenv

    # Resolve explicitly so running from the repository root works as well.
    load_dotenv(BACKEND / ".env", override=False)


def preflight():
    """Only inspect presence, never instantiate a client or print a credential."""
    return {
        "llm_key_configured": bool(os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")),
        "amap_key_configured": bool(os.getenv("AMAP_API_KEY")),
        "tavily_key_configured_optional": bool(os.getenv("TAVILY_API_KEY")),
        "fallback_model_configured_optional": bool(os.getenv("LLM_FALLBACK_MODEL_ID")),
    }


class SafeLogWriter(io.TextIOBase):
    """Buffer fragmented prints, redact whole lines, then persist them."""

    def __init__(self, file, redactor):
        self.file, self.redactor, self.pending = file, redactor, ""
        self.lock = RLock()

    def write(self, text):
        with self.lock:
            self.pending += text
            while "\n" in self.pending:
                line, self.pending = self.pending.split("\n", 1)
                self.file.write(self.redactor(line) + "\n")
                self.file.flush()
        return len(text)

    def flush(self):
        with self.lock:
            if self.file.closed:
                return
            if self.pending:
                self.file.write(self.redactor(self.pending))
                self.pending = ""
            self.file.flush()


def execute_worker(folder):
    """One process per case: timeouts can stop all of its planner worker threads."""
    load_environment()
    redactor = Redactor(os.environ)
    # Import before stdout redirection: the existing logger wraps stdout.buffer
    # on Windows. Remove its normal daily/console sinks before loading clients.
    from app.services.logger import logger

    logger.remove()
    started = time.monotonic()
    outcome = dict(status="error", started_at=now(), seconds=0)
    with (folder / "run.log").open("a", encoding="utf-8", buffering=1) as log_file:
        writer = SafeLogWriter(log_file, redactor)
        sink = logger.add(lambda message: log_file.write(redactor(str(message))),
                          format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} | {message}",
                          level="DEBUG", colorize=False, backtrace=False, diagnose=False)
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            try:
                from app.agents.trip_planner_agent import TripPlanningDegraded, get_trip_planner_agent
                from app.models.schemas import TripRequest

                request_data = json.loads((folder / "request.json").read_text(encoding="utf-8"))
                request = TripRequest.model_validate(request_data)

                def progress(percent, message):
                    event = redactor.data(dict(at=now(), progress=percent, message=message))
                    with (folder / "progress.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(event, ensure_ascii=False) + "\n")

                progress(0, "开始真实接口规划；无测试脚本级自动重试")
                planner = get_trip_planner_agent()
                try:
                    plan = planner.plan_trip(request, progress_callback=progress)
                    outcome["status"] = "passed"
                except TripPlanningDegraded as error:
                    plan = error.plan
                    outcome.update(status="degraded", error=redactor(str(error)))
                # Save data before auditing: a checker bug must not lose the plan.
                payload = plan.model_dump(mode="json")
                write_json(folder / "tripPlan.json", redactor.data(payload))
                checks = audit_plan(payload, request_data)
                write_json(folder / "checks.json", redactor.data(checks))
                if not checks["passed"] and outcome["status"] == "passed":
                    outcome["status"] = "failed"
                progress(100, "生成与自动检查结束，推荐质量仍待人工审阅")
            except Exception as error:
                outcome.update(status="error", error=redactor(f"{type(error).__name__}: {error}"))
                log_file.write(redactor(traceback.format_exc()))
            finally:
                writer.flush()
                outcome.update(finished_at=now(), seconds=round(time.monotonic()-started, 3))
                write_json(folder / "result.json", redactor.data(outcome))
                logger.remove(sink)
    return 0 if outcome["status"] == "passed" else 1


def run_worker(folder, timeout_seconds):
    command = [sys.executable, str(Path(__file__).resolve()), "--execute", "--confirm-paid",
               "--worker-dir", str(folder)]
    started = time.monotonic()
    try:
        process = subprocess.Popen(command, cwd=BACKEND, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as error:
        outcome = dict(status="error", seconds=0, error=f"无法启动工作进程：{type(error).__name__}")
        write_json(folder / "result.json", outcome)
        return outcome
    last_message, last_notice = None, started
    try:
        while process.poll() is None:
            elapsed = time.monotonic() - started
            if elapsed >= timeout_seconds:
                process.kill()
                process.wait(timeout=10)
                outcome = dict(status="timeout", seconds=round(elapsed, 3),
                               error=f"超过{timeout_seconds}秒，已终止本条进程；已发出的接口调用仍可能计费，不自动重试")
                write_json(folder / "result.json", outcome)
                return outcome
            message = None
            progress = folder / "progress.jsonl"
            if progress.is_file():
                lines = progress.read_text(encoding="utf-8").splitlines()
                if lines:
                    try:
                        latest = json.loads(lines[-1])
                        message = f"{latest['progress']}% {latest['message']}"
                    except (ValueError, KeyError):
                        pass  # A partially written JSONL line is not a generation failure.
            if message != last_message or time.monotonic() - last_notice >= 30:
                print(f"  {int(elapsed)}秒 · {message or '初始化中'}", flush=True)
                last_message, last_notice = message, time.monotonic()
            time.sleep(1)
    finally:
        # Ctrl+C must not leave paid requests running in an orphan worker.
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    result_file = folder / "result.json"
    if result_file.is_file():
        outcome = json.loads(result_file.read_text(encoding="utf-8"))
        if process.returncode != 0 and outcome["status"] == "passed":
            outcome.update(status="error", error=f"工作进程异常退出：{process.returncode}")
            write_json(result_file, outcome)
        return outcome
    outcome = dict(status="error", seconds=round(time.monotonic()-started, 3),
                   error=f"工作进程退出且未写结果，退出码{process.returncode}；请检查环境及run.log")
    write_json(result_file, outcome)
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="调用真实外部服务；默认仅准备请求与预览报告")
    parser.add_argument("--confirm-paid", action="store_true", help="确认一次行程会调用多次模型/地图等服务且可能收费")
    parser.add_argument("--case", action="append", default=[], help="指定用例ID，可重复；默认三条")
    parser.add_argument("--start-date", default=(datetime.now(SHANGHAI).date()+timedelta(days=7)).isoformat())
    parser.add_argument("--output-dir", type=Path, default=BACKEND / "test-results" / "real-evaluation")
    parser.add_argument("--timeout-seconds", type=int, default=900, help="单条进程时限，默认900秒；不是费用上限")
    parser.add_argument("--worker-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.execute != args.confirm_paid:
        parser.error("真实调用必须同时提供 --execute --confirm-paid；不带这两个参数只生成预览")
    if not 1 <= args.timeout_seconds <= 3600:
        parser.error("单条时限必须为1至3600秒")
    if args.worker_dir:
        if not args.execute:
            parser.error("工作进程同样需要真实调用的两项明确开关")
        return execute_worker(args.worker_dir.resolve())
    load_environment()
    try:
        cases = load_cases(args.start_date, args.case)
        if date.fromisoformat(args.start_date) < datetime.now(SHANGHAI).date():
            parser.error("请使用今天或将来的旅行日期")
    except ValueError as error:
        parser.error(str(error))
    config = preflight()
    if args.execute and not (config["llm_key_configured"] and config["amap_key_configured"]):
        parser.error("真实执行需要配置 LLM_API_KEY/OPENAI_API_KEY 和 AMAP_API_KEY（backend/.env或环境变量）")
    run_id = datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    output = args.output_dir.resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    report = dict(generated_at=now(), mode="real" if args.execute else "preview-no-network",
                  scope=SCOPE, preflight=config, timeout_seconds=args.timeout_seconds,
                  cost_note="至多3条、串行、无脚本级重试；生产SDK内部重试保持原配置；不保证固定调用次数或费用上限",
                  cases=[{**case, "status": "not_run"} for case in cases])
    for case in cases:
        folder = output / case["id"]
        folder.mkdir()
        write_json(folder / "request.json", case["request"])
    render_report(output, report)
    print(f"报告：{output / 'report.html'}", flush=True)
    if not args.execute:
        print(f"已准备{len(cases)}条请求，未调用真实接口。人工审阅清单与请求均已保存。")
        return 0
    print(f"真实执行{len(cases)}条；会产生接口用量，每条最多等待{args.timeout_seconds}秒。", flush=True)
    try:
        for case in report["cases"]:
            print(f"开始：{case['id']} · {case['title']}", flush=True)
            folder = output / case["id"]
            case["status"] = "running"
            render_report(output, report)
            case.update(run_worker(folder, args.timeout_seconds))
            check_file = folder / "checks.json"
            if check_file.is_file():
                case["checks"] = json.loads(check_file.read_text(encoding="utf-8"))
            render_report(output, report)
            print(f"结束：{case['id']} · {case['status']}", flush=True)
            # Key/quota/network errors or timeouts can affect all cases; stop spending.
            if case["status"] in {"error", "timeout"}:
                print("发生执行异常或超时，停止本批后续请求，避免继续消耗接口额度。", flush=True)
                break
    except KeyboardInterrupt:
        case.update(status="error", error="用户中断；工作进程已终止，已发出的请求仍可能计费")
        write_json(output / case["id"] / "result.json", {key: case[key] for key in ("status", "error")})
        render_report(output, report)
        print(f"已中断，现有产物保留：{output}")
        return 130
    print(f"报告：{output / 'report.html'}", flush=True)
    return 0 if all(case["status"] == "passed" for case in report["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
