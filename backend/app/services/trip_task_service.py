"""可持久化、可恢复的旅行规划后台任务管理器。"""

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional
from uuid import uuid4

from ..agents.trip_planner_agent import TripPlanningDegraded, TripPlanningInfeasible, get_trip_planner_agent
from ..config import get_settings
from ..models.schemas import TripPlan, TripRequest
from .auth_service import save_history
from .logger import logger
from .trip_task_store import (
    ActiveTripTaskExists,
    MemoryTripTaskStore,
    MySQLTripTaskStore,
    StoredTripTask,
    TripTaskDailyLimitExceeded,
)


TripTask = StoredTripTask


class TripTaskService:
    """将耗时 Agent 调用移出 HTTP 线程，并把任务状态保存到持久层。"""

    def __init__(
        self,
        planner_factory: Callable = get_trip_planner_agent,
        max_workers: int = 2,
        store=None,
        daily_limit: Optional[int] = None,
        history_saver: Optional[Callable] = save_history,
        recover_pending: bool = True,
    ):
        settings = get_settings()
        self._planner_factory = planner_factory
        self._store = store or MySQLTripTaskStore(
            retention_hours=settings.trip_task_retention_hours
        )
        self._daily_limit = max(1, daily_limit or settings.trip_daily_generation_limit)
        self._history_saver = history_saver
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="trip-plan",
        )
        if recover_pending:
            self._recover_pending_tasks()

    def _recover_pending_tasks(self) -> None:
        tasks = self._store.recover_pending()
        if tasks:
            logger.warning(f"服务启动恢复 {len(tasks)} 个未完成旅行规划任务")
        for task in tasks:
            self._executor.submit(self._run_task, task.task_id)

    def create_task(self, request: TripRequest, user_id: str) -> TripTask:
        task = self._store.create(
            str(uuid4()),
            user_id,
            request,
            self._daily_limit,
        )
        self._executor.submit(self._run_task, task.task_id)
        return task

    def get_task(self, task_id: str, user_id: str) -> Optional[TripTask]:
        return self._store.get(task_id, user_id)

    def _save_result_to_history(self, task: TripTask, plan: TripPlan) -> None:
        if not self._history_saver:
            return
        try:
            self._history_saver(
                task.user_id,
                task.task_id,
                plan.model_dump(mode="json"),
            )
        except Exception as error:
            logger.warning(f"任务结果写入历史记录失败: {task.task_id} | {error}")

    def _run_task(self, task_id: str) -> None:
        if not self._store.claim(task_id):
            return
        task = self._store.get(task_id)
        if not task:
            return
        try:
            planner = self._planner_factory()
            plan = planner.plan_trip(
                task.request,
                progress_callback=lambda progress, message: self._store.update_progress(
                    task_id,
                    progress,
                    message,
                ),
            )
            quality_failed = bool(
                plan.quality_report and not plan.quality_report.passed
            )
            task_status = "degraded" if quality_failed else "succeeded"
            task_message = (
                "行程已生成，部分安排需要确认"
                if quality_failed else "旅行计划生成完成"
            )
            self._store.finish(
                task_id,
                task_status,
                plan,
                task_message,
                task_message if quality_failed else None,
            )
            self._save_result_to_history(task, plan)
        except TripPlanningInfeasible as error:
            logger.warning(f"旅行路线不可执行: {task_id} | {error}")
            self._store.finish(task_id, "failed", None, "当前路线无法满足时间约束", str(error))
        except TripPlanningDegraded as error:
            logger.warning(f"旅行规划任务降级完成: {task_id} | {error}")
            self._store.finish(
                task_id,
                "degraded",
                error.plan,
                str(error),
                str(error),
            )
            self._save_result_to_history(task, error.plan)
        except Exception:
            logger.exception(f"旅行规划任务失败: {task_id}")
            self._store.finish(
                task_id,
                "failed",
                None,
                "旅行计划生成失败",
                "生成服务暂时不可用，请稍后重试。",
            )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


_trip_task_service: Optional[TripTaskService] = None


def get_trip_task_service() -> TripTaskService:
    global _trip_task_service
    if _trip_task_service is None:
        _trip_task_service = TripTaskService()
    return _trip_task_service
