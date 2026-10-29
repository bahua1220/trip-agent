"""外部 HTTP JSON 请求的统一入口。"""

import json
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from .logger import logger


class ExternalRequestError(RuntimeError):
    """第三方服务请求失败，保留状态码以便调用方做降级处理。"""

    def __init__(self, message: str, *, status_code: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: Optional[dict[str, Any]] = None,
    headers: Optional[Mapping[str, str]] = None,
    timeout: int = 10,
    retries: int = 2,
) -> dict[str, Any]:
    """请求 JSON，使用系统 HTTPS 证书校验，并对临时网络错误有限重试。"""
    request_headers = {"Accept": "application/json", "User-Agent": "trip-planner/1.0"}
    if headers:
        request_headers.update(headers)
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    host = urlsplit(url).netloc
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                error_body = error.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                error_body = ""
            retryable = error.code == 429 or error.code >= 500
            if retryable and attempt < retries:
                logger.warning(f"外部请求 HTTP {error.code}，重试 {attempt + 1}/{retries}: {host}")
                time.sleep(0.4 * (2 ** attempt))
                continue
            raise ExternalRequestError(
                f"HTTP {error.code} from {host}", status_code=error.code, body=error_body
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            if attempt < retries:
                logger.warning(f"外部请求失败，重试 {attempt + 1}/{retries}: {host} ({error})")
                time.sleep(0.4 * (2 ** attempt))
                continue
            raise ExternalRequestError(f"请求 {host} 失败: {error}") from error

    raise ExternalRequestError(f"请求 {host} 失败")
