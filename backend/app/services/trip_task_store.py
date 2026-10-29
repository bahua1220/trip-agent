"""旅行规划任务持久化存储。"""

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..models.schemas import TripPlan, TripRequest
from .auth_service import _conn, init_database


ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"succeeded", "degraded", "failed"}
BEIJING_TIMEZONE = timezone(timedelta(hours=8))


class ActiveTripTaskExists(Exception):
    """用户已经存在生成中的任务。"""


class TripTaskDailyLimitExceeded(Exception):
    """用户超过北京时间自然日生成上限。"""


@dataclass
class StoredTripTask:
    task_id: str
    user_id: str
    request: TripRequest
    status: str = "queued"
    progress: int = 0
    message: str = "任务已创建，等待开始"
    result: Optional[TripPlan] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _beijing_day_bounds_utc(now: datetime) -> tuple[datetime, datetime]:
    """返回当前北京时间自然日对应的 UTC 起止时刻。"""
    local_now = now.astimezone(BEIJING_TIMEZONE)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        local_start.astimezone(timezone.utc),
        (local_start + timedelta(days=1)).astimezone(timezone.utc),
    )


def _parse_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return _utc_now()


def _decode_json(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    return json.loads(value)


class MySQLTripTaskStore:
    """以 MySQL 为准的任务存储，支持单进程工作器重启恢复。"""

    def __init__(self, retention_hours: int = 24):
        init_database()
        self.retention_hours = max(2, retention_hours)

    @staticmethod
    def _from_row(row) -> StoredTripTask:
        request = TripRequest.model_validate(_decode_json(row["request_json"]))
        result_payload = _decode_json(row.get("result_json"))
        result = TripPlan.model_validate(result_payload) if result_payload else None
        return StoredTripTask(
            task_id=row["id"],
            user_id=row["user_id"],
            request=request,
            status=row["status"],
            progress=int(row["progress"] or 0),
            message=row["message"],
            result=result,
            error=row.get("error"),
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )

    def create(self, task_id: str, user_id: str, request: TripRequest,
               daily_limit: int) -> StoredTripTask:
        now = _utc_now()
        day_start, day_end = _beijing_day_bounds_utc(now)
        with _conn() as connection:
            with connection.cursor() as cursor:
                # 锁住用户行，使同一用户的并发创建请求串行化。
                cursor.execute("SELECT id FROM users WHERE id=%s FOR UPDATE", (user_id,))
                if not cursor.fetchone():
                    raise ValueError("用户不存在")
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count FROM trip_tasks
                    WHERE user_id=%s AND status IN ('queued', 'running')
                    """,
                    (user_id,),
                )
                if int(cursor.fetchone()["count"]) >= 1:
                    raise ActiveTripTaskExists()
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count FROM trip_tasks
                    WHERE user_id=%s AND created_at>=%s AND created_at<%s
                        AND status<>'failed'
                    """,
                    (user_id, day_start.isoformat(), day_end.isoformat()),
                )
                if int(cursor.fetchone()["count"]) >= daily_limit:
                    raise TripTaskDailyLimitExceeded()
                cursor.execute(
                    """
                    INSERT INTO trip_tasks (
                        id, user_id, request_json, status, progress, message,
                        result_json, error, created_at, updated_at
                    ) VALUES (%s, %s, %s, 'queued', 0, %s, NULL, NULL, %s, %s)
                    """,
                    (
                        task_id,
                        user_id,
                        json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
                        "任务已创建，等待开始",
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
            connection.commit()
        return self.get(task_id, user_id)

    def get(self, task_id: str, user_id: Optional[str] = None) -> Optional[StoredTripTask]:
        sql = "SELECT * FROM trip_tasks WHERE id=%s"
        params = [task_id]
        if user_id is not None:
            sql += " AND user_id=%s"
            params.append(user_id)
        with _conn() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, tuple(params))
                row = cursor.fetchone()
        return self._from_row(row) if row else None

    def claim(self, task_id: str) -> bool:
        now = _utc_now().isoformat()
        with _conn() as connection:
            with connection.cursor() as cursor:
                count = cursor.execute(
                    """
                    UPDATE trip_tasks
                    SET status='running', progress=GREATEST(progress, 5),
                        message=%s, updated_at=%s
                    WHERE id=%s AND status='queued'
                    """,
                    ("正在初始化旅行规划 Agent", now, task_id),
                )
            connection.commit()
        return count == 1

    def update_progress(self, task_id: str, progress: int, message: str) -> None:
        with _conn() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE trip_tasks
                    SET progress=GREATEST(progress, %s), message=%s, updated_at=%s
                    WHERE id=%s AND status='running'
                    """,
                    (min(100, progress), message, _utc_now().isoformat(), task_id),
                )
            connection.commit()

    def finish(self, task_id: str, status: str, result: Optional[TripPlan],
               message: str, error: Optional[str] = None) -> None:
        payload = (
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
            if result else None
        )
        with _conn() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE trip_tasks
                    SET status=%s, progress=100, message=%s, result_json=%s,
                        error=%s, updated_at=%s
                    WHERE id=%s
                    """,
                    (status, message, payload, error, _utc_now().isoformat(), task_id),
                )
            connection.commit()

    def recover_pending(self) -> list[StoredTripTask]:
        cutoff = _utc_now() - timedelta(hours=self.retention_hours)
        now = _utc_now().isoformat()
        with _conn() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM trip_tasks WHERE updated_at<%s",
                    (cutoff.isoformat(),),
                )
                cursor.execute(
                    """
                    UPDATE trip_tasks
                    SET status='queued', message=%s, updated_at=%s
                    WHERE status IN ('queued', 'running')
                    """,
                    ("服务已恢复，正在重新提交生成任务", now),
                )
                cursor.execute(
                    "SELECT * FROM trip_tasks WHERE status='queued' ORDER BY created_at"
                )
                rows = cursor.fetchall()
            connection.commit()
        return [self._from_row(row) for row in rows]


class MemoryTripTaskStore:
    """仅用于单元测试的线程安全存储。"""

    def __init__(self):
        self.tasks: dict[str, StoredTripTask] = {}
        self._lock = threading.Lock()

    def create(self, task_id: str, user_id: str, request: TripRequest,
               daily_limit: int) -> StoredTripTask:
        with self._lock:
            now = _utc_now()
            day_start, day_end = _beijing_day_bounds_utc(now)
            user_tasks = [task for task in self.tasks.values() if task.user_id == user_id]
            if any(task.status in ACTIVE_STATUSES for task in user_tasks):
                raise ActiveTripTaskExists()
            if sum(
                day_start <= task.created_at < day_end
                and task.status != "failed"
                for task in user_tasks
            ) >= daily_limit:
                raise TripTaskDailyLimitExceeded()
            task = StoredTripTask(
                task_id=task_id,
                user_id=user_id,
                request=request,
                created_at=now,
                updated_at=now,
            )
            self.tasks[task_id] = task
            return task

    def get(self, task_id: str, user_id: Optional[str] = None) -> Optional[StoredTripTask]:
        with self._lock:
            task = self.tasks.get(task_id)
            if not task or (user_id is not None and task.user_id != user_id):
                return None
            return StoredTripTask(**task.__dict__)

    def claim(self, task_id: str) -> bool:
        with self._lock:
            task = self.tasks.get(task_id)
            if not task or task.status != "queued":
                return False
            task.status = "running"
            task.progress = max(task.progress, 5)
            task.updated_at = _utc_now()
            return True

    def update_progress(self, task_id: str, progress: int, message: str) -> None:
        with self._lock:
            task = self.tasks.get(task_id)
            if task and task.status == "running":
                task.progress = max(task.progress, min(100, progress))
                task.message = message
                task.updated_at = _utc_now()

    def finish(self, task_id: str, status: str, result: Optional[TripPlan],
               message: str, error: Optional[str] = None) -> None:
        with self._lock:
            task = self.tasks.get(task_id)
            if task:
                task.status = status
                task.progress = 100
                task.message = message
                task.result = result
                task.error = error
                task.updated_at = _utc_now()

    def recover_pending(self) -> list[StoredTripTask]:
        with self._lock:
            recovered = []
            for task in self.tasks.values():
                if task.status in ACTIVE_STATUSES:
                    task.status = "queued"
                    task.message = "服务已恢复，正在重新提交生成任务"
                    recovered.append(StoredTripTask(**task.__dict__))
            return recovered
