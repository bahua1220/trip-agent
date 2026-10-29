"""Low-frequency web fallback for suspicious intercity rail estimates."""

import json
import math
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..config import get_settings
from .http_client import ExternalRequestError, request_json
from .logger import logger


_cache_lock = threading.Lock()
_memory_cache: dict[str, dict] = {}
_tavily_unavailable = False


def _cache_path() -> Path:
    configured = os.getenv("INTERCITY_RESEARCH_CACHE_PATH", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "data" / "intercity_rail_cache.json"


def _cache_key(origin_city: str, destination_city: str) -> str:
    normalize = lambda value: re.sub(r"[\s市州地区]+$", "", str(value or "").strip())
    # Version the key whenever extraction rules change so an old one-day
    # negative cache cannot hide the improved parser after deployment.
    return f"rail-v3:{normalize(origin_city)}->{normalize(destination_city)}"


def _read_cache_entry(key: str) -> Optional[dict]:
    now = datetime.now(timezone.utc)
    with _cache_lock:
        entry = _memory_cache.get(key)
        if entry is None:
            path = _cache_path()
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    _memory_cache.update(payload)
                    entry = _memory_cache.get(key)
            except (FileNotFoundError, OSError, ValueError, TypeError):
                entry = None
        if not isinstance(entry, dict):
            return None
        try:
            expires_at = datetime.fromisoformat(str(entry["expires_at"]))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            return None
        if expires_at <= now:
            return None
        result = entry.get("result")
        return result if isinstance(result, dict) else {"unavailable": True}


def _write_cache_entry(key: str, result: Optional[dict], ttl_days: int) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(days=max(1, ttl_days))
    entry = {
        "expires_at": expires_at.isoformat(),
        "result": result,
    }
    with _cache_lock:
        _memory_cache[key] = entry
        path = _cache_path()
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(_memory_cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError as error:
            logger.warning(f"跨城铁路查询缓存写入失败: {error}")
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _duration_limits(distance_km: float) -> tuple[int, int]:
    minimum = max(25, int(math.floor(max(0.0, distance_km) / 350 * 60)))
    # 铁路异常查询不能接受接近公路慢车速度的结果，否则网页摘要中的
    # 中转、全天班次跨度会被误当成站到站运行时间。
    maximum = min(720, int(math.ceil(max(80.0, distance_km) / 60 * 60 + 90)))
    return minimum, maximum


def _duration_candidates(results: list[dict], distance_km: float) -> tuple[list[int], list[str]]:
    """Extract only durations that appear in rail-related result snippets."""
    values: list[int] = []
    urls: list[str] = []
    minimum, maximum = _duration_limits(distance_km)
    rail_words = re.compile(r"高铁|动车|火车|铁路|列车|城际|车次")
    road_words = re.compile(r"自驾|驾车|汽车|大巴|客车")
    duration_pattern = re.compile(
        r"(?<!\d)(\d{1,2})\s*(?:小时|时)\s*(?:(\d{1,2})\s*(?:分钟|分))?"
        r"|(?<!\d)(\d{2,3})\s*(?:分钟|分)(?!钟?钟)"
    )
    for item in results:
        text = f"{item.get('title', '')} {item.get('content', '')}"
        if not rail_words.search(text):
            continue
        accepted = False
        for match in duration_pattern.finditer(text):
            clause_start = max(
                text.rfind(separator, 0, match.start())
                for separator in ("，", "。", "；", ";", "\n")
            ) + 1
            following_boundaries = [
                position for separator in ("，", "。", "；", ";", "\n")
                if (position := text.find(separator, match.end())) >= 0
            ]
            clause_end = min(following_boundaries) if following_boundaries else len(text)
            context = text[clause_start:clause_end]
            # A snippet may compare driving and rail. Require local rail evidence,
            # and reject a duration locally labelled as a road journey.
            if not rail_words.search(context):
                continue
            if road_words.search(context):
                continue
            if match.group(3):
                minutes = int(match.group(3))
            else:
                minutes = int(match.group(1)) * 60 + int(match.group(2) or 0)
            if minimum <= minutes <= maximum:
                values.append(minutes)
                accepted = True
        url = str(item.get("url", "") or "").strip()
        if accepted and url and url not in urls:
            urls.append(url)
    return values, urls


def _station_candidates(
    results: list[dict], origin_city: str, destination_city: str
) -> tuple[Optional[str], Optional[str]]:
    """Extract explicit city station names without guessing a connection."""
    origin_root = re.sub(r"[\s市州地区]+$", "", str(origin_city or "").strip())
    destination_root = re.sub(r"[\s市州地区]+$", "", str(destination_city or "").strip())
    source_texts = [
        f"{item.get('title', '')} {item.get('content', '')}" for item in results
    ]

    def station_for_city(city_root: str) -> Optional[str]:
        if not city_root:
            return None
        pattern = re.compile(
            rf"{re.escape(city_root)}(?:市)?"
            rf"(?:东|西|南|北|中心|北广场|南广场)?站"
        )
        matches = [
            match for text in source_texts for match in pattern.findall(text)
        ]
        # Prefer the shortest explicit match. This keeps “大理站到丽江站”
        # from being consumed as one greedy station name.
        return min(matches, key=len) if matches else None

    origin = station_for_city(origin_root)
    destination = station_for_city(destination_root)
    if origin and destination:
        return origin, destination

    station_pattern = re.compile(r"[\u4e00-\u9fffA-Za-z0-9·]{2,14}站")
    names = []
    for text in source_texts:
        for name in station_pattern.findall(text):
            # Trim common prose that regex may attach to the front.
            trimmed = re.split(r"[到从至乘坐抵达前往可在，。；：:\s]", name)[-1]
            if trimmed and trimmed not in names:
                names.append(trimmed)
    origin = origin or next(
        (name for name in names if origin_root and origin_root in name), None
    )
    destination = destination or next(
        (name for name in names if destination_root and destination_root in name), None
    )
    return origin, destination


def _extract_with_grounded_llm(
    results: list[dict],
    origin_city: str,
    destination_city: str,
    distance_km: float,
) -> Optional[dict]:
    """Let the existing LLM parse Tavily snippets, never supply outside facts."""
    if not results:
        return None
    context = "\n".join(
        f"【{item.get('title', '')}】{str(item.get('content', ''))[:700]}"
        for item in results[:4]
    )
    prompt = f"""只根据下方搜索摘要，提取{origin_city}到{destination_city}的铁路信息。
不得使用训练记忆，不得补写摘要中没有的数据。若摘要只讲驾车或没有铁路耗时，reliable=false。

搜索摘要：
{context}

严格返回JSON，不要解释：
{{"reliable":true,"duration_min_minutes":90,"duration_max_minutes":120,
"departure_station":"出发站名或空字符串","arrival_station":"到达站名或空字符串"}}

规则：
1. 只提取高铁、动车、火车、铁路或列车的站到站运行时间，不含酒店接驳和候车。
2. “1小时30分”换算成90；范围保留最小和最大分钟数。
3. 车站名必须在摘要中明确出现并以“站”结尾，不确定则留空。
4. 没有明确铁路耗时必须返回reliable=false。"""
    try:
        from langchain_core.messages import HumanMessage
        from .llm_service import get_llm

        response = get_llm().invoke([HumanMessage(content=prompt)])
        text = response.content if hasattr(response, "content") else str(response)
        json_text = str(text).strip()
        if "```json" in json_text:
            json_text = json_text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in json_text:
            json_text = json_text.split("```", 1)[1].split("```", 1)[0].strip()
        elif "{" in json_text and "}" in json_text:
            json_text = json_text[json_text.find("{"):json_text.rfind("}") + 1]
        data = json.loads(json_text)
        if data.get("reliable") is not True:
            return None
        minimum = int(data.get("duration_min_minutes", 0) or 0)
        maximum = int(data.get("duration_max_minutes", 0) or minimum)
        lower_limit, upper_limit = _duration_limits(distance_km)
        if minimum <= 0 or maximum < minimum:
            return None
        if minimum < lower_limit or maximum > upper_limit:
            return None
        source_text = " ".join(
            f"{item.get('title', '')} {item.get('content', '')}" for item in results
        )

        def verified_station(value: object) -> Optional[str]:
            name = str(value or "").strip()
            return name if name.endswith("站") and name in source_text else None

        return {
            "duration_min_minutes": minimum,
            "duration_max_minutes": maximum,
            "departure_station": verified_station(data.get("departure_station")),
            "arrival_station": verified_station(data.get("arrival_station")),
        }
    except Exception as error:
        logger.warning(f"铁路搜索摘要结构化失败: {error}")
        return None


def research_intercity_rail_duration(
    origin_city: str,
    destination_city: str,
    distance_km: float = 0,
) -> Optional[dict]:
    """Use Tavily only after the planner has identified an abnormal rail result.

    Successful city-pair references are cached for 60 days. An unavailable result
    is cached for one day so a burst of plan generations cannot consume credits.
    """
    global _tavily_unavailable

    key = _cache_key(origin_city, destination_city)
    cached = _read_cache_entry(key)
    if cached:
        return None if cached.get("unavailable") else cached

    settings = get_settings()
    api_key = str(getattr(settings, "tavily_api_key", "") or "").strip()
    if not api_key or _tavily_unavailable:
        return None

    query = (
        f"{origin_city} 到 {destination_city} 高铁 动车 火车 "
        "出发站 到达站 站到站运行时间 最快多久"
    )
    try:
        payload = request_json(
            "https://api.tavily.com/search",
            method="POST",
            payload={
                "query": query,
                "search_depth": "basic",
                "max_results": 3,
                "include_answer": True,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if payload.get("error") or payload.get("detail"):
            message = str(payload.get("error") or payload.get("detail"))
            if any(word in message.lower() for word in (
                "quota", "credit", "exceeded", "limit", "usage"
            )):
                _tavily_unavailable = True
            logger.warning(f"Tavily 跨城铁路查询失败: {message}")
            _write_cache_entry(key, None, 1)
            return None

        results = []
        if payload.get("answer"):
            results.append({
                "title": "Tavily 摘要",
                "url": "",
                "content": str(payload["answer"]),
            })
        results.extend(
            {
                "title": str(item.get("title", "") or ""),
                "url": str(item.get("url", "") or ""),
                "content": str(item.get("content", "") or ""),
            }
            for item in payload.get("results", [])
            if isinstance(item, dict)
        )
        durations, urls = _duration_candidates(results, distance_km)
        departure_station, arrival_station = _station_candidates(
            results, origin_city, destination_city
        )
        grounded = None
        if not durations or not departure_station or not arrival_station:
            grounded = _extract_with_grounded_llm(
                results, origin_city, destination_city, distance_km
            )
        if not durations and grounded:
            durations = [
                grounded["duration_min_minutes"],
                grounded["duration_max_minutes"],
            ]
        if grounded:
            departure_station = departure_station or grounded.get("departure_station")
            arrival_station = arrival_station or grounded.get("arrival_station")
        if not durations:
            logger.warning(f"Tavily 未找到可验证的铁路耗时: {origin_city}→{destination_city}")
            _write_cache_entry(key, None, 1)
            return None

        ordered = sorted(durations)
        reference = ordered[len(ordered) // 2]
        result = {
            "duration_minutes": reference,
            "duration_min_minutes": ordered[0],
            "duration_max_minutes": ordered[-1],
            "source": "tavily",
            "source_urls": urls[:3],
            "departure_station": departure_station,
            "arrival_station": arrival_station,
            "requires_confirmation": True,
        }
        _write_cache_entry(key, result, 60)
        logger.info(
            f"Tavily 铁路异常兜底: {origin_city}→{destination_city} "
            f"参考{reference}分钟 range={ordered[0]}-{ordered[-1]}"
        )
        return result
    except ExternalRequestError as error:
        if error.status_code in (402, 403, 429, 432):
            _tavily_unavailable = True
        logger.warning(f"Tavily 跨城铁路请求失败: {error}")
    except Exception as error:
        logger.warning(f"Tavily 跨城铁路查询异常: {error}")

    _write_cache_entry(key, None, 1)
    return None
