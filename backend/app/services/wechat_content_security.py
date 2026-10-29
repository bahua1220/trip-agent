"""微信小程序用户文本内容安全检查。

AppSecret 与 access_token 只存在于后端。小程序为每次提交附带一次性
wx.login code，后端换取 OpenID 后调用微信 msgSecCheck v2。
"""

import asyncio
import time
from typing import Optional

import httpx
from fastapi import HTTPException

from ..config import get_settings
from .auth_service import exchange_wechat_code, get_wechat_identity_for_user
from .logger import logger


_token_cache = {}
_token_lock = asyncio.Lock()


def _request_text(request) -> str:
    parts = []
    field_labels = (
        ("city", "目的地"),
        ("region", "旅行区域"),
        ("start_location", "出发地"),
        ("end_location", "结束地"),
        ("arrival_location", "抵达交通枢纽"),
        ("departure_location", "离开交通枢纽"),
        ("free_text_input", "其他要求"),
    )
    for field, label in field_labels:
        value = getattr(request, field, None)
        if value:
            parts.append(f"{label}：{value}")

    for field, label in (("preferences", "偏好"), ("destinations", "路线节点"), ("nodes", "路线节点")):
        values = getattr(request, field, None) or []
        if values:
            parts.append(f"{label}：{'、'.join(str(value) for value in values)}")

    content = "\n".join(parts).strip()
    if len(content) > 2500:
        raise HTTPException(400, "填写的旅行要求过长，请精简后重试")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in content):
        raise HTTPException(400, "填写内容包含无法识别的控制字符")
    return content


async def _access_token(app_id: str, force_refresh: bool = False) -> str:
    settings = get_settings()
    async with _token_lock:
        cached = _token_cache.get(app_id)
        if not force_refresh and cached and cached[1] > time.monotonic():
            return cached[0]

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.weixin.qq.com/cgi-bin/token",
                    params={
                        "grant_type": "client_credential",
                        "appid": app_id,
                        "secret": settings.wechat_app_secret,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise HTTPException(503, "内容安全服务暂时不可用，请稍后重试") from error

        token = payload.get("access_token")
        if not token:
            logger.warning(
                f"获取微信内容安全 access_token 失败: errcode={payload.get('errcode')}"
            )
            raise HTTPException(503, "内容安全服务暂时不可用，请稍后重试")

        expires_in = max(300, int(payload.get("expires_in") or 7200))
        _token_cache[app_id] = (token, time.monotonic() + expires_in - 300)
        return token


async def _wechat_check(content: str, app_id: str, open_id: str, retry: bool = True) -> None:
    token = await _access_token(app_id)
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                "https://api.weixin.qq.com/wxa/msg_sec_check",
                params={"access_token": token},
                json={
                    "content": content,
                    "version": 2,
                    "scene": 2,
                    "openid": open_id,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(503, "内容安全服务暂时不可用，请稍后重试") from error

    error_code = int(payload.get("errcode") or 0)
    if error_code in {40014, 42001} and retry:
        await _access_token(app_id, force_refresh=True)
        return await _wechat_check(content, app_id, open_id, retry=False)
    if error_code:
        logger.warning(
            f"微信文本内容安全检查失败: errcode={error_code} "
            f"trace_id={payload.get('trace_id')}"
        )
        raise HTTPException(503, "内容安全服务暂时不可用，请稍后重试")

    result = payload.get("result") or {}
    suggestion = result.get("suggest") or payload.get("suggest")
    if suggestion in {"risky", "review"}:
        logger.info(
            f"微信文本内容安全检查未通过: suggest={suggestion} "
            f"label={result.get('label')} trace_id={payload.get('trace_id')}"
        )
        raise HTTPException(400, "填写内容可能不符合平台规范，请修改后重试")
    if suggestion != "pass":
        raise HTTPException(503, "内容安全服务暂时不可用，请稍后重试")


async def check_request_content(request, user_id: Optional[str] = None) -> None:
    """检查路线/行程请求中的全部用户可编辑文本。"""
    content = _request_text(request)
    if not content:
        return

    settings = get_settings()
    if not settings.wechat_content_security_enabled:
        return

    identity = None
    code = getattr(request, "wechat_content_check_code", None)
    if code:
        identity = await exchange_wechat_code(code)
    elif user_id:
        identity = get_wechat_identity_for_user(user_id)

    # Web 账号可能尚未绑定微信。小程序每次提交都会携带一次性 code，
    # 因此提审和正式小程序路径不会走到这里。
    if not identity:
        logger.info("当前请求没有微信身份，跳过微信专用文本安全接口")
        return

    if identity["app_id"] != settings.wechat_app_id:
        raise HTTPException(400, "微信身份与当前小程序不匹配")
    await _wechat_check(content, identity["app_id"], identity["open_id"])
