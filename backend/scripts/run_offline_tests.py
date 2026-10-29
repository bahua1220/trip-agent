"""Run all backend unit tests with DNS and external connections denied."""

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from offline_network import OfflineNetworkGuard


def main():
    # Discovery/imports are guarded too. Caught network exceptions still fail
    # this run: silent production fallbacks must not make it look offline-safe.
    with OfflineNetworkGuard() as network:
        suite = unittest.defaultTestLoader.discover(str(BACKEND / "tests"), pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    report = dict(
        generated_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
        total=result.testsRun, failures=len(result.failures), errors=len(result.errors),
        skipped=len(result.skipped), blocked_network_attempts=network.attempts,
        passed=result.wasSuccessful() and result.testsRun > 0 and not result.skipped and not network.attempts,
    )
    output = BACKEND / "test-results" / "offline-regression.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"离线回归：{result.testsRun}条；拦截外网尝试{len(network.attempts)}次。报告：{output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
