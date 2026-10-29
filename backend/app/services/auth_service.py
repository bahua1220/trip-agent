import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import jwt
import pymysql
from fastapi import HTTPException

from ..config import get_settings


WECHAT_PROVIDER = "wechat_miniapp"


def _conn(include_database: bool = True):
    settings = get_settings()
    options = {
        "host": settings.mysql_host,
        "port": settings.mysql_port,
        "user": settings.mysql_user,
        "password": settings.mysql_password,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
    }
    if include_database:
        options["database"] = settings.mysql_database
    return pymysql.connect(**options)


def init_database():
    settings = get_settings()
    with _conn(include_database=False) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE DATABASE IF NOT EXISTS `{}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci".format(
                    settings.mysql_database.replace("`", "``")
                )
            )
        connection.commit()

    with _conn() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id CHAR(36) PRIMARY KEY,
                    username VARCHAR(100) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    created_at VARCHAR(40) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trip_history (
                    id CHAR(36) PRIMARY KEY,
                    user_id CHAR(36) NOT NULL,
                    plan_json JSON NOT NULL,
                    created_at VARCHAR(40) NOT NULL,
                    updated_at VARCHAR(40) NOT NULL,
                    INDEX idx_history_user (user_id, updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_identities (
                    id CHAR(36) PRIMARY KEY,
                    user_id CHAR(36) NOT NULL,
                    provider VARCHAR(32) NOT NULL,
                    provider_app_id VARCHAR(80) NOT NULL,
                    provider_subject VARCHAR(160) NOT NULL,
                    union_id VARCHAR(160) NULL,
                    created_at VARCHAR(40) NOT NULL,
                    UNIQUE KEY uk_provider_subject (
                        provider, provider_app_id, provider_subject
                    ),
                    UNIQUE KEY uk_user_provider (
                        user_id, provider, provider_app_id
                    ),
                    INDEX idx_identity_user (user_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trip_tasks (
                    id CHAR(36) PRIMARY KEY,
                    user_id CHAR(36) NOT NULL,
                    request_json JSON NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    progress SMALLINT NOT NULL DEFAULT 0,
                    message VARCHAR(255) NOT NULL,
                    result_json JSON NULL,
                    error VARCHAR(500) NULL,
                    created_at VARCHAR(40) NOT NULL,
                    updated_at VARCHAR(40) NOT NULL,
                    INDEX idx_tasks_user_status (user_id, status),
                    INDEX idx_tasks_user_created (user_id, created_at),
                    INDEX idx_tasks_updated (updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_consents (
                    user_id CHAR(36) NOT NULL,
                    document_type VARCHAR(32) NOT NULL,
                    version VARCHAR(32) NOT NULL,
                    accepted_at VARCHAR(40) NOT NULL,
                    source VARCHAR(32) NOT NULL,
                    PRIMARY KEY (user_id, document_type, version),
                    INDEX idx_consents_user (user_id, accepted_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        connection.commit()


def _public_user(cursor, user_id):
    cursor.execute("SELECT id, username FROM users WHERE id=%s", (user_id,))
    row = cursor.fetchone()
    if not row:
        return None
    cursor.execute(
        """
        SELECT 1 FROM user_identities
        WHERE user_id=%s AND provider=%s
        LIMIT 1
        """,
        (user_id, WECHAT_PROVIDER),
    )
    has_wechat_identity = cursor.fetchone() is not None
    return {
        "id": row["id"],
        "username": row["username"],
        "is_wechat_only": row["username"].startswith("wx_") and has_wechat_identity,
        "has_wechat_identity": has_wechat_identity,
    }


def _hash(password, salt=None):
    salt = salt or os.urandom(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return "scrypt${}${}".format(base64.b64encode(salt).decode(), base64.b64encode(value).decode())


def register(username, password):
    init_database()
    if username.lower().startswith("wx_"):
        raise HTTPException(400, "用户名不能以 wx_ 开头")
    user = {
        "id": str(uuid4()),
        "username": username,
        "is_wechat_only": False,
        "has_wechat_identity": False,
    }
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _conn() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (id, username, password_hash, created_at) VALUES (%s, %s, %s, %s)",
                    (user["id"], username, _hash(password), now),
                )
            connection.commit()
    except pymysql.err.IntegrityError as exc:
        raise HTTPException(409, "用户名已被使用") from exc
    return user


def authenticate(username, password):
    init_database()
    with _conn() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
            row = cursor.fetchone()
    try:
        _, salt, _ = row["password_hash"].split("$")
    except (TypeError, ValueError):
        raise HTTPException(401, "用户名或密码错误")
    if not hmac.compare_digest(_hash(password, base64.b64decode(salt)), row["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    with _conn() as connection:
        with connection.cursor() as cursor:
            return _public_user(cursor, row["id"])


def token_for(user):
    settings = get_settings()
    return jwt.encode(
        {"sub": user["id"], "exp": datetime.now(timezone.utc) + timedelta(days=settings.auth_token_expire_days)},
        settings.auth_secret_key,
        algorithm="HS256",
    )


def user_from_token(token):
    try:
        user_id = jwt.decode(token, get_settings().auth_secret_key, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "登录已失效，请重新登录") from exc
    with _conn() as connection:
        with connection.cursor() as cursor:
            user = _public_user(cursor, user_id)
    if not user:
        raise HTTPException(401, "用户不存在")
    return user


def record_user_consent(user_id, version, source="miniapp"):
    """记录用户主动同意的协议版本，便于后续审计和协议更新提醒。"""
    if not version:
        return
    init_database()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as connection:
        with connection.cursor() as cursor:
            for document_type in ("terms", "privacy"):
                cursor.execute(
                    """
                    INSERT INTO user_consents (
                        user_id, document_type, version, accepted_at, source
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        accepted_at=VALUES(accepted_at), source=VALUES(source)
                    """,
                    (user_id, document_type, version, now, source),
                )
        connection.commit()


def get_wechat_identity_for_user(user_id):
    """返回内容安全接口所需的 AppID 与 OpenID，不向客户端暴露。"""
    init_database()
    with _conn() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT provider_app_id, provider_subject
                FROM user_identities
                WHERE user_id=%s AND provider=%s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, WECHAT_PROVIDER),
            )
            row = cursor.fetchone()
    if not row:
        return None
    return {
        "app_id": row["provider_app_id"],
        "open_id": row["provider_subject"],
    }


def _merge_user_consents(cursor, source_user_id, target_user_id):
    cursor.execute(
        """
        INSERT INTO user_consents (
            user_id, document_type, version, accepted_at, source
        )
        SELECT %s, document_type, version, accepted_at, source
        FROM user_consents
        WHERE user_id=%s
        ON DUPLICATE KEY UPDATE
            accepted_at=VALUES(accepted_at), source=VALUES(source)
        """,
        (target_user_id, source_user_id),
    )
    cursor.execute("DELETE FROM user_consents WHERE user_id=%s", (source_user_id,))


def delete_user_account(user_id):
    """注销账号并删除身份、任务和历史数据；运行中任务完成前禁止注销。"""
    init_database()
    with _conn() as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS active_count
                    FROM trip_tasks
                    WHERE user_id=%s AND status IN ('queued', 'running')
                    """,
                    (user_id,),
                )
                if int(cursor.fetchone()["active_count"] or 0) > 0:
                    raise HTTPException(409, "当前有行程正在生成，请等待完成后再注销账号")

                cursor.execute("DELETE FROM trip_history WHERE user_id=%s", (user_id,))
                cursor.execute("DELETE FROM trip_tasks WHERE user_id=%s", (user_id,))
                cursor.execute("DELETE FROM user_consents WHERE user_id=%s", (user_id,))
                cursor.execute("DELETE FROM user_identities WHERE user_id=%s", (user_id,))
                deleted = cursor.execute("DELETE FROM users WHERE id=%s", (user_id,))
                if not deleted:
                    raise HTTPException(404, "用户不存在或已经注销")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return True


def _wechat_config():
    settings = get_settings()
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise HTTPException(503, "微信登录尚未配置，请联系管理员")
    return settings.wechat_app_id, settings.wechat_app_secret


async def exchange_wechat_code(code):
    app_id, app_secret = _wechat_config()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": app_id,
                    "secret": app_secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "微信登录服务暂时不可用") from exc

    try:
        error_code = int(payload.get("errcode") or 0)
    except (TypeError, ValueError):
        error_code = -1
    if error_code:
        status_code = 401 if error_code in {40029, 40163} else 502
        raise HTTPException(status_code, "微信登录凭证无效或已过期")

    open_id = payload.get("openid")
    if not open_id:
        raise HTTPException(502, "微信登录响应缺少用户标识")
    return {
        "app_id": app_id,
        "open_id": open_id,
        "union_id": payload.get("unionid"),
    }


def _find_wechat_identity(cursor, app_id, open_id):
    cursor.execute(
        """
        SELECT * FROM user_identities
        WHERE provider=%s AND provider_app_id=%s AND provider_subject=%s
        """,
        (WECHAT_PROVIDER, app_id, open_id),
    )
    return cursor.fetchone()


def _insert_wechat_identity(cursor, user_id, identity_data, now):
    cursor.execute(
        """
        INSERT INTO user_identities (
            id, user_id, provider, provider_app_id,
            provider_subject, union_id, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            str(uuid4()),
            user_id,
            WECHAT_PROVIDER,
            identity_data["app_id"],
            identity_data["open_id"],
            identity_data["union_id"],
            now,
        ),
    )


async def login_with_wechat(code):
    identity_data = await exchange_wechat_code(code)
    init_database()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as connection:
        try:
            with connection.cursor() as cursor:
                identity = _find_wechat_identity(
                    cursor, identity_data["app_id"], identity_data["open_id"]
                )
                if identity:
                    return _public_user(cursor, identity["user_id"])

                user_id = str(uuid4())
                username_hash = hashlib.sha256(
                    f'{identity_data["app_id"]}:{identity_data["open_id"]}'.encode()
                ).hexdigest()[:20]
                username = f"wx_{username_hash}"
                cursor.execute(
                    """
                    INSERT INTO users (id, username, password_hash, created_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (user_id, username, _hash(secrets.token_urlsafe(32)), now),
                )
                _insert_wechat_identity(cursor, user_id, identity_data, now)
                user = _public_user(cursor, user_id)
            connection.commit()
            return user
        except pymysql.err.IntegrityError as exc:
            connection.rollback()
            with connection.cursor() as cursor:
                identity = _find_wechat_identity(
                    cursor, identity_data["app_id"], identity_data["open_id"]
                )
                if identity:
                    return _public_user(cursor, identity["user_id"])
            raise HTTPException(409, "微信身份创建冲突，请重新登录") from exc


async def bind_wechat_identity(user_id, code):
    identity_data = await exchange_wechat_code(code)
    init_database()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as connection:
        try:
            with connection.cursor() as cursor:
                target_user = _public_user(cursor, user_id)
                if not target_user:
                    raise HTTPException(401, "用户不存在")
                if target_user["is_wechat_only"]:
                    raise HTTPException(400, "微信临时账号请绑定已有 Web 账号")

                cursor.execute(
                    """
                    SELECT * FROM user_identities
                    WHERE user_id=%s AND provider=%s AND provider_app_id=%s
                    """,
                    (user_id, WECHAT_PROVIDER, identity_data["app_id"]),
                )
                target_identity = cursor.fetchone()
                identity = _find_wechat_identity(
                    cursor, identity_data["app_id"], identity_data["open_id"]
                )
                if identity and identity["user_id"] == user_id:
                    return target_user
                if target_identity:
                    raise HTTPException(409, "当前账号已经绑定了另一个微信身份")

                if identity:
                    source_user = _public_user(cursor, identity["user_id"])
                    if not source_user or not source_user["is_wechat_only"]:
                        raise HTTPException(409, "该微信已经绑定其他账号")
                    cursor.execute(
                        "UPDATE trip_history SET user_id=%s WHERE user_id=%s",
                        (user_id, source_user["id"]),
                    )
                    cursor.execute(
                        "UPDATE trip_tasks SET user_id=%s WHERE user_id=%s",
                        (user_id, source_user["id"]),
                    )
                    cursor.execute(
                        "UPDATE user_identities SET user_id=%s WHERE id=%s",
                        (user_id, identity["id"]),
                    )
                    _merge_user_consents(cursor, source_user["id"], user_id)
                    cursor.execute("DELETE FROM users WHERE id=%s", (source_user["id"],))
                else:
                    _insert_wechat_identity(cursor, user_id, identity_data, now)
            connection.commit()
        except pymysql.err.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(409, "微信身份绑定冲突，请重新登录后再试") from exc

        with connection.cursor() as cursor:
            return _public_user(cursor, user_id)


def merge_wechat_user(current_user_id, username, password):
    try:
        target_user = authenticate(username, password)
    except HTTPException as exc:
        if exc.status_code == 401:
            raise HTTPException(400, "Web 用户名或密码错误") from exc
        raise
    if target_user["id"] == current_user_id:
        return target_user
    init_database()
    with _conn() as connection:
        try:
            with connection.cursor() as cursor:
                source_user = _public_user(cursor, current_user_id)
                if not source_user or not source_user["is_wechat_only"]:
                    raise HTTPException(400, "当前账号不是待绑定的微信临时账号")
                if not source_user["has_wechat_identity"]:
                    raise HTTPException(400, "当前账号没有可合并的微信身份")
                if target_user["has_wechat_identity"]:
                    raise HTTPException(409, "目标账号已经绑定了微信")

                cursor.execute(
                    "UPDATE trip_history SET user_id=%s WHERE user_id=%s",
                    (target_user["id"], current_user_id),
                )
                cursor.execute(
                    "UPDATE trip_tasks SET user_id=%s WHERE user_id=%s",
                    (target_user["id"], current_user_id),
                )
                cursor.execute(
                    "UPDATE user_identities SET user_id=%s WHERE user_id=%s",
                    (target_user["id"], current_user_id),
                )
                _merge_user_consents(cursor, current_user_id, target_user["id"])
                cursor.execute("DELETE FROM users WHERE id=%s", (current_user_id,))
            connection.commit()
        except pymysql.err.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(409, "账号合并冲突，请重新登录后再试") from exc

        with connection.cursor() as cursor:
            return _public_user(cursor, target_user["id"])


def save_history(user_id, entry_id, plan):
    init_database()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT created_at FROM trip_history WHERE id=%s AND user_id=%s", (entry_id, user_id))
            old = cursor.fetchone()
            created = old["created_at"] if old else now
            cursor.execute(
                """
                INSERT INTO trip_history (id, user_id, plan_json, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    user_id=VALUES(user_id), plan_json=VALUES(plan_json), updated_at=VALUES(updated_at)
                """,
                (entry_id, user_id, json.dumps(plan, ensure_ascii=False), created, now),
            )
        connection.commit()
    return {"id": entry_id, "plan": plan, "createdAt": created, "updatedAt": now}


def list_history(user_id):
    init_database()
    with _conn() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM trip_history WHERE user_id=%s ORDER BY updated_at DESC", (user_id,))
            rows = cursor.fetchall()
    return [
        {"id": row["id"], "plan": json.loads(row["plan_json"]), "createdAt": row["created_at"], "updatedAt": row["updated_at"]}
        for row in rows
    ]


def delete_history(user_id, entry_id):
    with _conn() as connection:
        with connection.cursor() as cursor:
            count = cursor.execute("DELETE FROM trip_history WHERE id=%s AND user_id=%s", (entry_id, user_id))
        connection.commit()
    return count > 0
