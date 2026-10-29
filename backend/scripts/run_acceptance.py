"""Run offline acceptance, isolation or full-pipeline suites; emit JSON/HTML reports."""

import argparse
import html
import json
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from offline_network import OfflineNetworkGuard


class AcceptanceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def startTest(self, test):
        super().startTest(test)
        self.started = time.perf_counter()
        self.status, self.detail = "passed", ""

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.status, self.detail = "failed", self._exc_info_to_string(err, test)

    def addError(self, test, err):
        super().addError(test, err)
        self.status, self.detail = "error", self._exc_info_to_string(err, test)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.status, self.detail = "skipped", reason

    def stopTest(self, test):
        self.cases.append(dict(
            id=test.id().split(".")[-1], title=test.shortDescription() or test.id(),
            suite=test.id().split(".")[0],
            status=self.status, seconds=round(time.perf_counter() - self.started, 3),
            detail=self.detail,
            plans=[p.model_dump(mode="json") for p in getattr(test, "plans", [])],
        ))
        super().stopTest(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=BACKEND / "test-results" / "acceptance")
    parser.add_argument("--suite", choices=("acceptance", "isolation", "pipeline", "all"), default="acceptance")
    args = parser.parse_args()
    from app.services.logger import logger
    logger.remove()  # Keep the report runner readable; preserve unittest failures.
    patterns = {
        "acceptance": "test_trip_acceptance.py",
        "isolation": "test_planner_isolation.py",
        "pipeline": "test_trip_pipeline.py",
    }
    selected = list(patterns) if args.suite == "all" else [args.suite]
    with OfflineNetworkGuard() as network:
        suite = unittest.TestSuite()
        for name in selected:
            group = unittest.defaultTestLoader.discover(str(BACKEND / "tests"), pattern=patterns[name])
            if not group.countTestCases():
                parser.error(f"未找到测试组：{name}")
            if name == "acceptance" and group.countTestCases() != 8:
                parser.error("八条业务验收用例数量不符")
            suite.addTests(group)
        result = unittest.TextTestRunner(verbosity=2, resultclass=AcceptanceResult).run(suite)
    scopes = {
        "acceptance": "固定候选下的八条业务排程与编辑验收",
        "isolation": "规划器会话隔离、并发初始化及失败后复用检查",
        "pipeline": "真实HTTP与后台任务全流程；任务库使用内存替身，历史保存使用记录器",
    }
    report = dict(
        generated_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
        scope=("离线固定数据：" + "；".join(scopes[name] for name in selected)
               + "。外部模型、地图和天气由固定适配器替代。"
               + "不验证真实模型/外部数据质量、MySQL或生产环境性能。"),
        summary=dict(total=result.testsRun, passed=sum(c["status"] == "passed" for c in result.cases),
                     failed=len(result.failures), errors=len(result.errors), skipped=len(result.skipped)),
        cases=result.cases,
        blocked_network_attempts=network.attempts,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    sections = []
    for case in result.cases:
        safe_id = case["suite"] + "-" + case["id"].split("_")[1]
        plan_links = []
        timelines = []
        for index, plan in enumerate(case["plans"], 1):
            name = f"case-{safe_id}-plan-{index}.json"
            (args.output_dir / name).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            plan_links.append(f'<a href="{name}">行程数据 {index}</a>')
            for day in plan["days"]:
                rows = "".join(
                    f'<tr><td>{html.escape(n.get("start_time") or "待定")}</td>'
                    f'<td>{html.escape(n.get("end_time") or "")}</td>'
                    f'<td>{html.escape(n["kind"])}</td><td>{html.escape(n["name"])}</td>'
                    f'<td>{n["duration_minutes"]}</td></tr>' for n in day["timeline"]
                )
                timelines.append(f'<h4>{html.escape(day["date"])}</h4><table><tr><th>开始</th><th>结束</th><th>类型</th><th>内容</th><th>分钟</th></tr>{rows}</table>')
        label = {"passed": "通过", "failed": "未通过", "error": "执行异常", "skipped": "跳过"}[case["status"]]
        details = f'<pre>{html.escape(case["detail"])}</pre>' if case["detail"] else ""
        sections.append(
            f'<section><h2>{html.escape(case["title"])}</h2><p class="{case["status"]}">{label} · {case["seconds"]}秒</p>'
            f'{details}<p>{" · ".join(plan_links)}</p><details><summary>查看固定数据下的时间轴</summary>{"".join(timelines)}</details></section>'
        )
    content = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>旅行规划离线验收</title><style>body{font:16px/1.6 system-ui;max-width:1100px;margin:32px auto;padding:0 20px;color:#223}section{border-top:1px solid #ddd;margin-top:24px}h2{font-size:19px}table{border-collapse:collapse;width:100%;margin-bottom:18px}th,td{border:1px solid #ddd;padding:6px;text-align:left}pre{white-space:pre-wrap;background:#fff3ef;padding:12px}.passed{color:#187448}.failed,.error{color:#b23b2e}summary{cursor:pointer}</style>'
    content += f'<h1>旅行规划离线验收</h1><p>{html.escape(report["generated_at"])}</p><p>{html.escape(report["scope"])}</p><p>共{result.testsRun}条，通过{report["summary"]["passed"]}条，失败{len(result.failures)}条，异常{len(result.errors)}条。外网尝试{len(network.attempts)}次（必须为0）。</p>'
    content += "".join(sections) + "</html>"
    (args.output_dir / "report.html").write_text(content, encoding="utf-8")
    print(f"报告：{args.output_dir.resolve() / 'report.html'}")
    return 0 if result.wasSuccessful() and result.testsRun > 0 and not result.skipped and not network.attempts else 1


if __name__ == "__main__":
    raise SystemExit(main())
