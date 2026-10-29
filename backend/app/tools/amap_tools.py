"""高德地图 LangChain 工具 — 基于 REST API"""

import json
from urllib.parse import urlencode

from langchain_core.tools import tool
from ..config import get_settings
from ..services.http_client import request_json
from ..services.logger import logger, log_tool_call


def _amap_request(endpoint: str, params: dict) -> dict:
    """通用高德 REST API 请求"""
    settings = get_settings()
    params["key"] = settings.amap_api_key
    params["output"] = "json"
    url = f"https://restapi.amap.com/v3/{endpoint}?{urlencode(params)}"
    return request_json(url)


@tool
def amap_text_search(keywords: str, city: str) -> str:
    """
    高德地图 POI 文本搜索。用于搜索景点、酒店、餐厅等。

    Args:
        keywords: 搜索关键词（如 "历史文化"、"连锁酒店"、"川菜"）
        city: 城市名称（如 "北京"、"南京"）

    Returns:
        JSON 字符串，包含搜索结果，每项有 name/address/location/type/rating
    """
    log_tool_call("amap_text_search", {"keywords": keywords, "city": city})

    try:
        data = _amap_request("place/text", {
            "keywords": keywords,
            "city": city,
            "extensions": "all",
            "offset": "10",
        })
        pois = data.get("pois", [])

        results = []
        for poi in pois:
            biz_ext = poi.get("biz_ext", {})
            rating = ""
            if isinstance(biz_ext, dict):
                rating = biz_ext.get("rating", "")

            results.append({
                "name": poi.get("name", ""),
                "address": poi.get("address", ""),
                "location": poi.get("location", ""),
                "type": poi.get("type", ""),
                "rating": str(rating),
            })

        logger.debug(f"  ↳ amap_text_search 返回 {len(results)} 条")
        return json.dumps(results, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"amap_text_search 失败: {e}")
        return f"搜索失败: {str(e)}"


@tool
def amap_weather(city: str) -> str:
    """
    高德地图天气查询。

    Args:
        city: 城市名称（如 "北京"、"南京"）

    Returns:
        天气信息的 JSON 字符串
    """
    log_tool_call("amap_weather", {"city": city})

    try:
        data = _amap_request("weather/weatherInfo", {
            "city": city,
            "extensions": "all",
        })

        # 提取关键字段
        forecasts = data.get("forecasts", [])
        result = {
            "city": city,
            "forecasts": [],
        }

        for fc in forecasts:
            casts = fc.get("casts", [])
            for cast in casts:
                result["forecasts"].append({
                    "date": cast.get("date", ""),
                    "day_weather": cast.get("dayweather", ""),
                    "night_weather": cast.get("nightweather", ""),
                    "day_temp": cast.get("daytemp", ""),
                    "night_temp": cast.get("nighttemp", ""),
                    "wind_direction": cast.get("daywind", ""),
                    "wind_power": cast.get("daypower", ""),
                })

        logger.debug(f"  ↳ amap_weather 返回 {len(result['forecasts'])} 天预报")
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"amap_weather 失败: {e}")
        return f"天气查询失败: {str(e)}"
