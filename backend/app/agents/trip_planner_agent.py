"""多智能体旅行规划系统 — LangGraph 版本

架构:
  - 4 个 ReAct Agent（create_react_agent）: 景点搜索、天气查询、酒店推荐、行程规划
  - LangChain @tool 包装高德 REST API
  - 日志系统基于 loguru

Pipeline:
  步骤1: 多关键词搜索（REST API）→ 算法粗筛 → 区域归并
  步骤2: 天气 + 酒店（并行 LLM Agent）
  步骤3: 第一阶段 LLM → 定景点
  步骤4: 周边搜餐厅
  步骤5: 第二阶段 LLM → 填充餐饮
  步骤6: 门票信息填充（并行）
"""

import json
import os
import re
import math
import time
from itertools import permutations
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Dict, Any, List, Optional, Tuple

# ---- LangChain / LangGraph ----
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

# ---- 内部模块 ----
from ..services.llm_service import get_fallback_llm, get_llm
from ..services.amap_service import get_amap_service
from ..services.http_client import request_json
from ..services.weather_service import get_weather_service
from ..services.ticket_service import query_ticket_info
from ..services.intercity_transport_service import research_intercity_rail_duration
from ..services.logger import logger, log_step_start, log_step_end, log_agent_call, log_agent_result
from ..tools.amap_tools import amap_text_search
from ..models.schemas import TripRequest, TripPlan, DayPlan, Attraction, RemovedAttraction, Meal, WeatherInfo, Location, Hotel, RouteSegment, TimelineItem, TransportAlternative, TicketInfo, Budget, QualityIssue, QualityReport
from ..config import get_settings


NON_DINING_BUSINESS_MARKERS = (
    "搓背", "洗浴", "足浴", "足疗", "按摩", "汗蒸", "水疗", "美容院",
    "美发", "理发", "修脚", "健身房", "宠物医院", "外卖专营", "外卖专送",
    "外卖店", "仅外卖", "无堂食", "不提供堂食", "员工食堂", "职工食堂",
    "内部食堂", "单位食堂", "内部餐厅", "中央厨房",
)


class TripPlanningError(RuntimeError):
    """The requested plan could not be generated safely."""


class TripPlanningInfeasible(TripPlanningError):
    """Hard time conflicts remain; do not publish an unsafe partial plan."""


class TripPlanningDegraded(TripPlanningError):
    """A plan based on real, but incomplete, data is available."""

    def __init__(self, plan: TripPlan, reason: str):
        super().__init__(reason)
        self.plan = plan


# ============ Agent 提示词（LangGraph 版本 — 无 [TOOL_CALL:...] 格式）============

ATTRACTION_AGENT_PROMPT = """你是景点搜索专家。根据用户需求使用 amap_text_search 工具搜索景点。

工具用法:
- amap_text_search(keywords, city): keywords 为搜索关键词（如 "历史文化"、"自然风光"），city 为城市名

搜索后整理并返回景点列表，包含名称、地址、坐标、类型、评分等信息。
"""

WEATHER_AGENT_PROMPT = """你是天气风险分析专家。

输入中会提供由天气服务返回的真实预报数据。不得编造、修改温度、天气或日期；
你的职责是分析天气对行程的影响，给出室内外景点安排、出行时间和携带物品建议。
如果没有预报数据，明确说明天气暂不可用，不要猜测天气。
"""

CRITIC_AGENT_PROMPT = """你是旅行行程质量审查专家。

输入会提供已生成的行程摘要、真实天气和确定性校验发现的问题。请只分析这些数据，
面向普通旅行者输出不超过 3 条可执行、优先级明确的调整建议：每条不超过两句，只说结论和建议。
不要使用 Markdown 标题、加粗、编号或“审查/校验”等内部术语；不要复述输入数据，也不要编造景点、票价、营业时间或天气。
如果没有问题，只用一句话说明“行程基础安排合理，出发前请确认预约和开放时间”。
"""

HOTEL_AGENT_PROMPT = """你是酒店推荐专家。使用 amap_text_search 工具搜索酒店。

酒店档次要求:
优先推荐知名连锁品牌酒店，保证卫生和服务标准:
- 经济型连锁: 如家、汉庭、7天、锦江之星、格林豪泰、尚客优、莫泰、城市便捷
- 中档连锁: 全季、亚朵、桔子、麗枫、和颐、白玉兰、维也纳、美居
- 避免推荐无名小旅馆、招待所、民宿公寓（除非用户明确要求）

搜索关键词用"连锁酒店"或具体品牌名。搜索后整理并返回酒店列表。
"""

PLANNER_AGENT_PROMPT = """你是行程规划专家。你的任务是根据景点信息和天气信息,生成详细的旅行计划。

请严格按照以下JSON格式返回旅行计划:
```json
{
  "city": "城市名称",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "description": "第1天行程概述",
      "transportation": "交通方式",
      "accommodation": "住宿类型",
      "hotel": null,
      "attractions": [
        {
          "name": "景点名称",
          "address": "详细地址",
          "location": {"longitude": 116.397128, "latitude": 39.916527},
          "visit_duration": 120,
          "description": "景点详细描述",
          "category": "景点类别",
          "ticket_price": 60
        }
      ],
      "meals": [
        {"type": "breakfast", "name": "早餐推荐", "description": "早餐描述", "estimated_cost": 30},
        {"type": "lunch", "name": "午餐推荐", "description": "午餐描述", "estimated_cost": 50},
        {"type": "dinner", "name": "晚餐推荐", "description": "晚餐描述", "estimated_cost": 80}
      ]
    }
  ],
  "weather_info": [
    {
      "date": "YYYY-MM-DD",
      "day_weather": "晴",
      "night_weather": "多云",
      "day_temp": 25,
      "night_temp": 15,
      "wind_direction": "南风",
      "wind_power": "1-3级"
    }
  ],
  "overall_suggestions": "总体建议",
  "budget": {
    "total_attractions": 180,
    "total_hotels": 1200,
    "total_meals": 480,
    "total_transportation": 200,
    "total": 2060
  }
}
```

**重要提示:**
1. weather_info 由系统根据真实天气服务统一写入；可以省略该字段，绝不要自行编造天气数据
2. 每天安排2-3个景点
4. 考虑景点之间的距离和游览时间
5. 每天必须包含早中晚三餐
6. 提供实用的旅行建议
7. **hotel 字段全部填 null**，酒店由后续算法根据景点密度自动选址
8. **必须包含预算信息**:
   - 景点门票价格(ticket_price)
   - 餐饮预估费用(estimated_cost)
   - 酒店预估费用(estimated_cost) 填 0（后续算法会填充）
   - 预算汇总(budget)包含各项总费用
"""


SCHEDULE_AGENT_PROMPT = """你是旅行日程编排 Agent。系统会给你带编号的真实景点候选。

你的职责只有三个：选择候选编号、决定每天的游览顺序、给出简短的安排理由。
不要重复输出候选的名称、地址、坐标、门票、酒店、餐饮、天气或预算；这些字段由后端从真实数据组装。
只能使用输入中存在的候选编号，不得编造编号或景点。严格只返回合法 JSON，不要 Markdown。
"""

DESTINATION_RESEARCH_PROMPT = """你是目的地研究 Agent。你的任务不是编造行程，而是为后续地图核验提出地点名称。

请根据目的地和用户偏好，将值得核验的地点分成三层：
- classic：第一次到访通常值得优先考虑、具有目的地代表性的地点
- preference：明显符合用户偏好的地点
- local：有当地特色、适合作为经典行程补充的地点

要求：
1. 只输出广为使用、适合在地图中检索的具体地点名称，不输出餐厅、酒店、商场或泛称
2. classic 4-8个，preference 4-8个，local 2-5个；同一名称不要跨层重复
3. 不提供地址、坐标、评分、票价或开放时间，这些信息由地图服务核验
4. 如果目的地较小或信息不足，宁可少列，不要虚构
5. 只返回 JSON：{"classic":["地点"],"preference":["地点"],"local":["地点"]}
"""


@dataclass(frozen=True)
class _PlannerRuntime:
    """Reusable stateless clients/agent graphs, never per-trip candidate data."""

    llm: Any
    fallback_llm: Any
    weather_service: Any
    attraction_agent: Runnable
    weather_agent: Runnable
    critic_agent: Runnable


class MultiAgentTripPlanner:
    """多智能体旅行规划系统（LangGraph 版本）"""

    PACE_PROFILES: Dict[str, dict] = {
        "relaxed": {
            "label": "轻松慢游",
            "visit_capacity": 600,
            "full_day_target": 2,
            "max_attractions": 2,
            "max_day_span": 720,
            "backfill_gap": 150,
        },
        "balanced": {
            "label": "适中",
            "visit_capacity": 660,
            "full_day_target": 2,
            "max_attractions": 3,
            "max_day_span": 840,
            "backfill_gap": 120,
        },
        "compact": {
            "label": "紧凑",
            "visit_capacity": 750,
            "full_day_target": 3,
            "max_attractions": 3,
            "max_day_span": 900,
            "backfill_gap": 90,
        },
    }

    # 景区型路线节点不是行政城市：路线仍然到达景区本身，但住宿、餐饮和
    # 城市级天气查询应使用附近具备服务能力的落脚地。
    SCENIC_ROUTE_NODE_PROFILES: Dict[str, dict] = {
        "珠峰大本营": {"service_base": "定日"},
        "巴松措": {"service_base": "工布江达"},
        "羊卓雍措": {"service_base": "浪卡子"},
        "纳木措": {"service_base": "当雄"},
        "喀纳斯": {"service_base": "布尔津"},
        "赛里木湖": {"service_base": "博乐"},
        "那拉提": {"service_base": "新源"},
        "巴音布鲁克": {"service_base": "和静"},
        "青海湖": {"service_base": "共和"},
        "茶卡盐湖": {"service_base": "乌兰"},
        "稻城亚丁": {"service_base": "香格里拉镇"},
        "四姑娘山": {"service_base": "小金"},
        "长白山": {"service_base": "安图"},
        "庐山": {
            "service_base": "九江",
            "search_city": "九江",
            "core_queries": ("庐山国家重点风景名胜区", "庐山风景名胜区"),
            "hotel_anchor": "庐山牯岭镇",
            "support_radius_km": 18,
        },
    }

    def __init__(self, runtime: Optional[_PlannerRuntime] = None):
        """A planner is one job/session; only immutable runtime handles are shared."""
        runtime = runtime or _get_planner_runtime()
        self.llm = runtime.llm
        self.fallback_llm = runtime.fallback_llm
        self.weather_service = runtime.weather_service
        self.attraction_agent = runtime.attraction_agent
        self.weather_agent = runtime.weather_agent
        self.critic_agent = runtime.critic_agent
        self._reset_run_state()

    def _reset_run_state(self, request: Optional[TripRequest] = None) -> None:
        """Keep all request-owned caches together; never put these on the runtime."""
        self._transport_hub_cache = {}
        self._intercity_rail_hub_cache = {}
        self._local_dish_catalogs = {}
        self._final_meal_search_cache = {}
        self._rejected_final_meal_ids = {}
        self._destination_research_cache = {}
        self._hotel_expected_region = ""
        self._prefer_local_meals = bool(request and "当地美食" in request.preferences)
        self.CITY_CENTERS = dict(type(self).CITY_CENTERS)

    # ==================== Agent Run Helpers ====================

    def _run_agent(self, agent: Runnable, query: str, agent_name: str) -> str:
        """
        运行 ReAct Agent 并返回最终文本响应。

        Args:
            agent: create_react_agent 返回的 Runnable
            query: 用户查询字符串
            agent_name: Agent 名称（用于日志）

        Returns:
            Agent 最终响应的文本
        """
        log_agent_call(agent_name, query)
        t0 = time.time()

        result = agent.invoke({"messages": [HumanMessage(content=query)]})
        # create_react_agent 返回 {"messages": [...]}，最后一条是最终 AI 响应
        response = result["messages"][-1].content

        elapsed = time.time() - t0
        log_agent_result(agent_name, elapsed, len(response))
        return response

    def _run_planner(
        self,
        system_prompt: str,
        user_query: str,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        运行纯 LLM 调用（规划 Agent，无工具）。

        Args:
            system_prompt: 系统提示词
            user_query: 用户查询

        Returns:
            LLM 响应文本
        """
        log_agent_call("Planner", user_query[:80])
        t0 = time.time()

        bind_options: Dict[str, Any] = {}
        if max_tokens:
            bind_options["max_tokens"] = max_tokens
        if os.getenv("LLM_JSON_MODE_ENABLED", "true").strip().lower() in {
            "1", "true", "yes", "on"
        }:
            # DeepSeek/OpenAI JSON Output：由 API 保证返回 JSON 对象，
            # 不再只依赖提示词约束模型格式。
            bind_options["response_format"] = {"type": "json_object"}

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_query),
        ]
        try:
            max_attempts = max(1, int(os.getenv("LLM_PLANNER_MAX_ATTEMPTS", "2")))
        except ValueError:
            max_attempts = 2
        try:
            base_delay = max(0.0, float(os.getenv("LLM_RETRY_BASE_DELAY", "2")))
        except ValueError:
            base_delay = 2.0

        response = None
        last_error: Optional[Exception] = None
        for attempt in range(max_attempts):
            try:
                planner_llm = self.llm.bind(**bind_options)
                response = planner_llm.invoke(messages)
                break
            except Exception as error:
                last_error = error
                if not self._is_retryable_llm_error(error) or attempt + 1 >= max_attempts:
                    break
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"Planner 服务暂时不可用，{delay:.1f}秒后重试 "
                    f"({attempt + 2}/{max_attempts}): {error}"
                )
                time.sleep(delay)

        if response is None:
            fallback_llm = getattr(self, "fallback_llm", None)
            if fallback_llm is not None and last_error is not None and self._is_retryable_llm_error(last_error):
                logger.warning("主 Planner 多次不可用，切换到备用 LLM")
                response = fallback_llm.bind(**bind_options).invoke(messages)
            elif last_error is not None:
                raise last_error
            else:
                raise TripPlanningError("Planner 未返回结果")

        text = response.content

        elapsed = time.time() - t0
        log_agent_result("Planner", elapsed, len(text))
        logger.debug(f"Planner 原始输出 (前2000字符):\n{text[:2000]}")
        return text

    @staticmethod
    def _is_retryable_llm_error(error: Exception) -> bool:
        """识别限流、服务繁忙和临时网关错误。"""
        message = str(error).lower()
        retry_markers = (
            "429", "500", "502", "503", "504",
            "service unavailable", "service_unavailable",
            "service is too busy", "rate limit", "timeout",
            "temporarily unavailable", "connection reset",
        )
        return any(marker in message for marker in retry_markers)

    @staticmethod
    def _is_route_mode(request: TripRequest) -> bool:
        return request.trip_mode in {"multi_city", "road_trip_loop"}

    @classmethod
    def _route_node_profile(cls, node: str) -> dict:
        normalized = str(node or "").strip()
        for scenic_name, profile in cls.SCENIC_ROUTE_NODE_PROFILES.items():
            if normalized == scenic_name or scenic_name in normalized:
                return {
                    "node_type": "scenic_area",
                    "scenic_name": scenic_name,
                    **profile,
                }
        return {
            "node_type": "city_base",
            "scenic_name": "",
            "service_base": normalized,
        }

    @staticmethod
    def _route_name_key(value: str) -> str:
        """景区名匹配时兼容“措/错”和常见空白、括号差异。"""
        normalized = MultiAgentTripPlanner._normalize_attr_name(str(value or ""))
        return normalized.replace("错", "措")

    @classmethod
    def _matches_scenic_route_node(cls, node: str, attraction_name: str) -> bool:
        node_key = cls._route_name_key(node)
        attraction_key = cls._route_name_key(attraction_name)
        return bool(node_key and attraction_key and node_key in attraction_key)

    def _build_route_nodes(self, request: TripRequest) -> List[str]:
        if not self._is_route_mode(request):
            return []
        start = (request.start_location or request.destinations[0]).strip()
        nodes = [start]
        for location in request.destinations:
            location = location.strip()
            if location and location != nodes[-1]:
                nodes.append(location)
        if request.trip_mode == "multi_city" and request.end_location:
            end = request.end_location.strip()
            if end and end != nodes[-1]:
                nodes.append(end)
        if request.return_to_start and nodes[-1] != start:
            nodes.append(start)
        return nodes

    def _route_segment_summary(
        self,
        origin: str,
        destination: str,
        max_drive_hours: float,
        transport_preference: str = "self_drive",
    ) -> RouteSegment:
        origin_coords = self._get_city_center(origin)
        destination_coords = self._get_city_center(destination)
        straight_distance = 0.0
        if origin_coords and destination_coords:
            straight_distance = self._haversine_distance(
                origin_coords[0], origin_coords[1],
                destination_coords[0], destination_coords[1],
            )

        invalid_regional_distance = False
        if straight_distance > 1200:
            try:
                from ..services.route_architect_service import REGION_CITY_SEEDS
                invalid_regional_distance = any(
                    origin in nodes and destination in nodes
                    for nodes in REGION_CITY_SEEDS.values()
                )
            except Exception:
                invalid_regional_distance = False
        if invalid_regional_distance:
            logger.warning(
                f"详细规划跨城节点疑似误定位: {origin} → {destination} | "
                f"直线距离={straight_distance:.1f}km；已忽略异常坐标"
            )
            origin_coords = None
            destination_coords = None
            straight_distance = 0.0

        selected_mode = transport_preference
        if selected_mode == "auto":
            if invalid_regional_distance:
                selected_mode = "high_speed_rail"
            elif straight_distance > 1200:
                selected_mode = "flight"
            elif straight_distance > 60:
                selected_mode = "high_speed_rail"
            else:
                selected_mode = "train"

        mode_labels = {
            "self_drive": "自驾",
            "high_speed_rail": "高铁/动车",
            "train": "火车/城际交通",
            "flight": "飞机",
        }
        mode_label = mode_labels.get(selected_mode, "城际交通")
        summary = None
        if selected_mode == "self_drive" and origin_coords and destination_coords:
            summary = get_amap_service().get_driving_route_summary(
                f"{origin_coords[0]},{origin_coords[1]}",
                f"{destination_coords[0]},{destination_coords[1]}",
            )
        if summary is None:
            route_factor = 1.25 if selected_mode == "self_drive" else 1.1
            distance_km = round(straight_distance * route_factor, 1)
            if selected_mode == "high_speed_rail":
                # 运行时间 + 往返车站、安检候车和换乘缓冲。
                duration_minutes = round(distance_km / 250 * 60 + 90)
            elif selected_mode == "train":
                duration_minutes = round(distance_km / 100 * 60 + 60)
            elif selected_mode == "flight":
                # 飞行时间 + 往返机场、值机安检和落地缓冲。
                duration_minutes = round(distance_km / 750 * 60 + 180)
            else:
                duration_minutes = round(distance_km / 55 * 60) if distance_km else 0
            summary = {
                "distance_km": distance_km,
                "duration_minutes": duration_minutes,
                "source": "estimated",
            }
        return RouteSegment(
            origin=origin,
            destination=destination,
            transport_mode=mode_label,
            distance_km=summary["distance_km"],
            duration_minutes=summary["duration_minutes"],
            source=summary["source"],
            requires_confirmation=selected_mode != "self_drive",
            exceeds_daily_limit=(
                selected_mode == "self_drive"
                and summary["duration_minutes"] > max_drive_hours * 60
            ),
        )

    def _build_route_itinerary(
        self, request: TripRequest
    ) -> Tuple[List[str], List[dict]]:
        nodes = self._build_route_nodes(request)
        if len(nodes) < 2:
            return nodes, []

        segment_pairs = list(zip(nodes, nodes[1:]))
        segment_count = len(segment_pairs)
        if request.trip_mode == "multi_city":
            # 多城市路线中的第一个节点是“第一站”，不是只用于换乘的出发点。
            # 将换城日放在各城市游玩区间的边界：例如 5 天 3 城会在
            # Day2、Day4 换城，从而得到 1/2/2 天的停留分配，并保证每个
            # 推荐城市至少真正进入一天行程。
            if len(nodes) > request.travel_days:
                raise ValueError("旅行天数不能少于实际路线节点数量")
            travel_day_indexes = [
                max(
                    1,
                    min(
                        request.travel_days - 1,
                        int(node_index * request.travel_days / len(nodes)),
                    ),
                )
                for node_index in range(1, len(nodes))
            ]
        else:
            late_arrival = (
                self._time_to_minutes(request.arrival_time) is not None
                and self._time_to_minutes(request.arrival_time) > 14 * 60
            )
            start_offset = 1 if late_arrival and request.travel_days > segment_count else 0
            usable_days = request.travel_days - start_offset
            travel_day_indexes = [
                min(
                    request.travel_days - 1,
                    start_offset + int(index * usable_days / segment_count),
                )
                for index in range(segment_count)
            ]
        segment_by_day = {
            day_index: self._route_segment_summary(
                origin,
                destination,
                request.max_drive_hours_per_day,
                request.intercity_transport,
            )
            for day_index, (origin, destination) in zip(
                travel_day_indexes, segment_pairs
            )
        }

        itinerary = []
        current_location = nodes[0]
        for day_index in range(request.travel_days):
            segment = segment_by_day.get(day_index)
            start_location = current_location
            if segment:
                current_location = segment.destination
            node_profile = self._route_node_profile(current_location)
            service_base = node_profile["service_base"]
            itinerary.append({
                "day_index": day_index,
                "start_location": start_location,
                "end_location": current_location,
                "activity_location": current_location,
                "overnight_city": service_base,
                "node_type": node_profile["node_type"],
                "route_segment": segment,
                "is_transit_day": bool(
                    segment and (
                        segment.duration_minutes == 0
                        or segment.duration_minutes >= 4 * 60
                    )
                ),
            })
        return nodes, itinerary

    @staticmethod
    def _route_prompt_section(route_itinerary: List[dict]) -> str:
        if not route_itinerary:
            return ""
        lines = ["\n**跨城路线硬约束（每天必须遵循）:**"]
        for item in route_itinerary:
            segment = item.get("route_segment")
            activity_location = (
                item.get("activity_location")
                or item.get("end_location")
                or item.get("overnight_city")
            )
            overnight_city = item.get("overnight_city") or activity_location
            if segment:
                limit_note = "，超过用户每日驾驶上限" if segment.exceeds_daily_limit else ""
                confirm_note = "，需另行确认真实班次" if segment.requires_confirmation else ""
                route_metric = (
                    f"约{segment.distance_km:.0f}km / "
                    f"{segment.duration_minutes // 60}小时{segment.duration_minutes % 60}分钟"
                    if segment.duration_minutes
                    else "距离与耗时待确认"
                )
                lines.append(
                    f"- Day {item['day_index'] + 1}: {segment.origin} → {segment.destination}，"
                    f"建议{segment.transport_mode}，{route_metric}"
                    f"{limit_note}{confirm_note}；游览地为{activity_location}，"
                    f"当晚住{overnight_city}。"
                )
                if item.get("is_transit_day"):
                    lines.append("  该日以赶路为主，最多安排1个沿途或抵达地景点。")
            else:
                lines.append(
                    f"- Day {item.get('day_index', 0) + 1}: 游览{activity_location}，"
                    f"可安排当地景点；当晚住{overnight_city}。"
                )
            if item.get("node_type") == "scenic_area":
                lines.append(
                    f"  {activity_location}是景区型路线节点，当天必须安排该景区本身，"
                    "附近景点不能替代它。"
                )
        lines.append("景点必须与当天路线或住宿地一致，禁止跨到其他节点来回折返。")
        return "\n".join(lines)

    def _apply_route_itinerary(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        route_nodes: List[str],
        route_itinerary: List[dict],
    ) -> TripPlan:
        trip_plan.trip_mode = request.trip_mode
        trip_plan.route_nodes = route_nodes
        for day, item in zip(trip_plan.days, route_itinerary):
            day.start_location = item["start_location"]
            day.end_location = item["end_location"]
            day.overnight_city = item["overnight_city"]
            day.route_segment = item["route_segment"]
            day.is_transit_day = item["is_transit_day"]
            if day.is_transit_day and len(day.attractions) > 1:
                day.attractions = day.attractions[:1]
        return trip_plan

    def _get_weather_for_request(
        self, request: TripRequest, route_itinerary: List[dict]
    ) -> List[WeatherInfo]:
        if not route_itinerary:
            return self.weather_service.get_forecast(
                request.city, request.start_date, request.end_date
            )

        forecasts_by_stop: Dict[str, Dict[str, WeatherInfo]] = {}
        for stop in dict.fromkeys(
            item["overnight_city"] for item in route_itinerary
        ):
            forecasts = self.weather_service.get_forecast(
                stop, request.start_date, request.end_date
            )
            forecasts_by_stop[stop] = {item.date: item for item in forecasts}

        start = date.fromisoformat(request.start_date)
        result = []
        for item in route_itinerary:
            day_date = (start + timedelta(days=item["day_index"])).isoformat()
            stop = item["overnight_city"]
            forecast = forecasts_by_stop.get(stop, {}).get(day_date)
            if forecast:
                result.append(
                    forecast.model_copy(update={"location_name": stop})
                )
        return result

    # ==================== 主流程 ====================

    def plan_trip(
        self,
        request: TripRequest,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> TripPlan:
        """
        多阶段旅行规划：搜索 → 粗筛 → LLM规划 → 餐饮 → 门票

        Args:
            request: 旅行请求

        Returns:
            完整的旅行计划
        """
        candidates: List[dict] = []
        weather_info: List[WeatherInfo] = []
        trip_plan: Optional[TripPlan] = None
        route_nodes: List[str] = []
        route_itinerary: List[dict] = []
        # The factory supplies one session per task. Reset again so a sequential
        # retry on the same session cannot inherit a failed run's candidates.
        self._reset_run_state(request)
        try:
            def report_progress(progress: int, message: str) -> None:
                if progress_callback:
                    progress_callback(progress, message)

            logger.info("=" * 60)
            route_nodes, route_itinerary = self._build_route_itinerary(request)
            logger.info(f"开始规划: {request.city} {request.travel_days}日游")
            logger.info(
                f"偏好: {request.preferences} | 住宿: {request.accommodation} | "
                f"游玩节奏: {self._pace_profile(request)['label']}"
            )
            logger.info(
                "首尾交通: "
                f"抵达={request.arrival_transport or '未填写'} {request.arrival_time or '未填写'} | "
                f"离开={getattr(request, 'departure_transport', None) or '未填写'} "
                f"{request.departure_time or '未填写'}"
            )
            logger.info("=" * 60)

            # ---- 步骤1: 多关键词搜索景点 ----
            report_progress(12, "正在搜索并筛选景点")
            step_id = log_step_start("步骤1: 多关键词搜索景点")
            search_result = self._search_attractions_multi(request)

            if search_result["success"]:
                if self._is_route_mode(request):
                    candidates = []
                    route_stops = list(dict.fromkeys(self._build_route_nodes(request)))
                    days_per_stop = max(1, request.travel_days // len(route_stops))
                    for stop in route_stops:
                        stop_pois = [
                            poi for poi in search_result["attractions"]
                            if poi.get("route_stop") == stop
                        ]
                        candidates.extend(self._rank_and_group_candidates(
                            stop_pois,
                            request.preferences,
                            days_per_stop,
                            stop,
                        ))
                else:
                    candidates = self._rank_and_group_candidates(
                        search_result["attractions"],
                        request.preferences,
                        request.travel_days,
                        request.city,
                    )
                attraction_response = "\n".join(
                    f"- {c['area_name']} | "
                    f"节点:{c.get('members', [{}])[0].get('route_stop', request.city)} | "
                    f"{c.get('address', '')} | "
                    f"坐标:({c['longitude']:.4f},{c['latitude']:.4f})"
                    for c in candidates
                )
            else:
                logger.info("回退到 LLM Agent 搜索...")
                attraction_query = self._build_attraction_query(request)
                attraction_response = self._run_agent(
                    self.attraction_agent, attraction_query, "景点搜索Agent"
                )
                candidates = []
            log_step_end(step_id)

            # ---- 步骤2: 真实预报 + 天气风险分析 ----
            report_progress(28, "正在获取天气并分析出行风险")
            step_id = log_step_start("步骤2: 天气预报和风险分析")
            weather_info = self._get_weather_for_request(
                request, route_itinerary
            )
            weather_data = json.dumps(
                [item.model_dump() for item in weather_info], ensure_ascii=False
            ) if weather_info else "暂无可用预报（可能超出 16 天预报范围）"
            weather_query = (
                f"目的地：{request.city}\n旅行日期：{request.start_date} 至 {request.end_date}\n"
                f"用户偏好：{', '.join(request.preferences) or '无'}\n"
                f"真实预报数据：{weather_data}\n请只输出天气风险与行程调整建议。"
            )
            weather_response = self._run_agent(
                self.weather_agent, weather_query, "天气风险Agent"
            )
            log_step_end(step_id, f"{len(weather_info)} 天真实预报")

            # ---- 步骤3: 第一阶段 LLM — 规划景点（不含酒店） ----
            report_progress(42, "正在生成景点行程")
            step_id = log_step_start("步骤3: 第一阶段LLM — 规划景点")
            compact_schedule = bool(candidates)
            if compact_schedule:
                planner_query = self._build_rich_planner_prompt(
                    request, candidates, weather_response, route_itinerary
                )
                planner_system_prompt = SCHEDULE_AGENT_PROMPT
                planner_max_tokens = min(6000, max(1400, 500 + request.travel_days * 180))
            else:
                planner_query = self._build_planner_query(
                    request, attraction_response, weather_response, route_itinerary
                )
                planner_system_prompt = PLANNER_AGENT_PROMPT
                planner_max_tokens = None
            planner_response = self._run_planner(
                planner_system_prompt, planner_query, max_tokens=planner_max_tokens
            )
            try:
                if compact_schedule:
                    trip_plan = self._parse_compact_schedule(
                        planner_response, request, candidates, route_itinerary
                    )
                else:
                    trip_plan = self._parse_response(planner_response, request)
            except TripPlanningError:
                logger.warning("规划结果格式无效，正在请求模型仅返回一次合法 JSON")
                retry_response = self._run_planner(
                    planner_system_prompt,
                    f"{planner_query}\n\n上一次输出无法解析。请严格只返回完整、合法的 JSON，不要添加任何解释。",
                    max_tokens=planner_max_tokens,
                )
                try:
                    if compact_schedule:
                        trip_plan = self._parse_compact_schedule(
                            retry_response, request, candidates, route_itinerary
                        )
                    else:
                        trip_plan = self._parse_response(retry_response, request)
                except TripPlanningError as retry_error:
                    if not candidates:
                        raise
                    # 模型连续两次输出了不完整/非法 JSON 时，不应把只有景点、
                    # 没有酒店餐饮和时间轴的半成品直接交给用户。候选均已由
                    # 地图服务核验，可以由后端按每天容量与路线节点确定性排日，
                    # 然后继续执行下方完整的酒店、餐饮和时间轴流水线。
                    logger.error(
                        "规划结果连续两次无法解析，改用真实候选确定性排日: "
                        f"{retry_error}"
                    )
                    trip_plan = self._build_deterministic_candidate_schedule(
                        request, candidates, route_itinerary
                    )
            # 原始预报由确定性服务写入，禁止 LLM 覆盖天气数值。
            trip_plan.weather_info = weather_info
            # 用户编辑行程后需要用原始抵达、离开和交通偏好重新计算。
            # 微信内容校验凭证在 TripRequest 中标记为 exclude，不会随结果保存。
            trip_plan.request_context = request
            # 硬去重：LLM 可能跨天重复同一景点
            trip_plan = self._dedup_attractions(
                trip_plan, candidates, route_itinerary, request
            )
            attr_count = sum(len(d.attractions) for d in trip_plan.days)
            log_step_end(step_id, f"{attr_count}个景点")

            # ---- 步骤3.5: 兜底 — 确保天数正确（LLM 有时少输出天） ----
            trip_plan = self._ensure_day_count(
                trip_plan, request, candidates, route_itinerary
            )
            trip_plan = self._ensure_classic_coverage(
                trip_plan, request, candidates
            )
            trip_plan = self._apply_route_itinerary(
                trip_plan, request, route_nodes, route_itinerary
            )
            trip_plan = self._ensure_scenic_route_node_attractions(
                trip_plan, request, candidates, route_itinerary
            )

            # ---- 步骤3.6: 兜底 — 从 description 回填缺失的 attractions ----
            trip_plan = self._backfill_attractions_from_description(trip_plan, candidates)

            # ---- 步骤3.7: 硬约束 — 每天最多1个远郊景点 ----
            trip_plan = self._enforce_remote_one_per_day(trip_plan, candidates)

            # ---- 步骤3.8: 名字还原 — 用 candidates 原始全名替换 LLM 缩写 ----
            trip_plan = self._restore_attraction_names(trip_plan, candidates)

            # ---- 步骤3.9: 跨城节点兜底——景点必须属于当天所在节点 ----
            trip_plan = self._enforce_route_attraction_consistency(
                trip_plan, request, candidates
            )
            # 上述名字还原、远郊重排和跨城校正均可能改变景点位置或名称，
            # 输出前按 POI/候选组再次硬去重，禁止同一核心景区跨天出现。
            trip_plan = self._remove_duplicate_attractions(trip_plan, candidates)
            trip_plan = self._apply_visit_duration_estimates(
                trip_plan, request, candidates
            )
            # 场馆闭馆与夜游约束必须参与第一次换日，而非换完日才发现不合适。
            if request.trip_mode == "single_city":
                trip_plan = self._enrich_ticket_info(trip_plan)
            # 先清理明确放不下的首尾日，再做跨日路线优化。否则优化器会把
            # 完整日的经典景点与离开短日中的无效安排互换，下一步又把经典
            # 景点删除，造成“越优化景点越少”。
            trip_plan = self._enforce_time_constraints(trip_plan, request, candidates)
            # ---- 步骤3.10: 单城日程地理可行性硬约束 ----
            # 模型无法稳定计算经纬度距离；按真实坐标、原始游览时长和
            # 市内交通耗时进行换日与日内重排。
            trip_plan = self._enforce_daily_route_feasibility(
                trip_plan, request, candidates
            )
            # 换日后根据新日期的可用时间重新写入游览时长。
            trip_plan = self._apply_visit_duration_estimates(
                trip_plan, request, candidates
            )
            # ---- 步骤3.11: 首尾日时间硬约束 ----
            # 必须放在所有补位、跨城校正和时长估算之后，否则后续
            # 步骤可能把已删除的超时景点重新加回离开日。
            trip_plan = self._enforce_time_constraints(trip_plan, request, candidates)

            # ---- 步骤3.12: 多城市跨城边界优化 ----
            # 先把换城日的远郊整日景点与下一完整日的市区景点交换，
            # 再决定是否需要提前一晚换城；单城市不会进入这两个方法。
            trip_plan = self._arrange_multi_city_attraction_blocks(
                trip_plan, request
            )
            # 单城市不进入该方法。若下一城的核心景点需要完整白天，
            # 将换城提前到前一天午后，避免“当天抵达后深夜游景区”。
            trip_plan = self._optimize_multi_city_transfer_boundaries(
                trip_plan, request
            )

            # ---- 步骤4: 酒店选址 — 密度锚点 + 周边搜连锁酒店 ----
            report_progress(58, "正在选择酒店位置")
            step_id = log_step_start("步骤4: 酒店选址（密度锚点）")
            try:
                self._select_hotel(trip_plan, request)
            except Exception as hotel_error:
                # 酒店是独立的增强步骤。酒店接口偶发失败时仍应继续搜索餐厅，
                # 不能把已经完成的景点行程直接作为无住宿、无餐饮的半成品返回。
                logger.exception(f"酒店选址失败，将继续生成其余内容: {hotel_error}")
            log_step_end(step_id, f"酒店: {trip_plan.days[0].hotel.name if trip_plan.days[0].hotel else '未选中'}")

            # 酒店确定后，使用高德真实路线耗时复核每天的酒店往返和景点顺序。
            # 早期组合优化仍使用快速直线估算，避免对整个候选池做两两路径请求。
            report_progress(63, "正在按真实路线复核每日行程")
            step_id = log_step_start("步骤4.5: 高德真实路线可行性复核")
            trip_plan = self._enforce_daily_route_feasibility(
                trip_plan,
                request,
                candidates,
                use_amap_routes=True,
            )
            trip_plan = self._apply_visit_duration_estimates(
                trip_plan, request, candidates
            )

            # 开放时间必须在餐厅与最终时间轴之前参与编排。旧流程直到
            # 时间轴生成后才查询门票，导致 17:00 闭园的景点仍排到 19:00。
            report_progress(65, "正在校验景点开放时间")
            trip_plan = self._enrich_ticket_info(trip_plan)
            trip_plan = self._repair_opening_hour_conflicts(trip_plan, request)

            # 真实路线或闭园约束删掉景点后，重新从同片区经典候选补位，
            # 再做一次真实路线门禁。不能让完整日只剩一个短景点。
            trip_plan = self._backfill_underfilled_days(
                trip_plan, request, candidates
            )
            trip_plan = self._remove_duplicate_attractions(trip_plan, candidates)
            trip_plan = self._apply_visit_duration_estimates(
                trip_plan, request, candidates
            )
            trip_plan = self._enforce_daily_route_feasibility(
                trip_plan,
                request,
                candidates,
                use_amap_routes=True,
            )
            trip_plan = self._apply_visit_duration_estimates(
                trip_plan, request, candidates
            )
            # 只会补查刚补入且尚无票务信息的候选。
            trip_plan = self._enrich_ticket_info(trip_plan)
            trip_plan = self._repair_opening_hour_conflicts(trip_plan, request)
            # 短途跨城不必强制从清晨出发。把上一城一个可移动景点延续到
            # 换城日上午，详细时间轴再比较上午、午后和傍晚交通窗口。
            trip_plan = self._rebalance_transfer_day_attractions(
                trip_plan, request
            )
            log_step_end(step_id)

            # ---- 步骤5: 周边搜餐厅 ----
            report_progress(68, "正在搜索餐饮推荐")
            step_id = log_step_start("步骤5: 周边搜索餐厅")
            try:
                day_meals = self._search_meals_for_plan(trip_plan, request)
            except Exception as meal_search_error:
                logger.exception(f"餐厅候选搜索失败: {meal_search_error}")
                day_meals = []
            total_candidates = sum(
                len(dm["meals"].get(meal_type, []))
                for dm in day_meals
                for meal_type in ["breakfast", "lunch", "dinner"]
            )
            log_step_end(step_id, f"{total_candidates}个餐饮候选")

            # ---- 步骤6: 第二阶段 LLM — 填充餐饮 ----
            report_progress(76, "正在补全每日餐饮")
            step_id = log_step_start("步骤6: 第二阶段LLM — 填充餐饮")
            if day_meals:
                try:
                    meal_prompt = self._build_meal_prompt(request, trip_plan, day_meals)
                    meal_response = self._run_planner(PLANNER_AGENT_PROMPT, meal_prompt)
                except Exception as meal_llm_error:
                    # 餐厅候选已经来自高德，模型失败时直接按候选评分确定性选择，
                    # 不应因此丢掉全部餐饮安排。
                    logger.exception(f"餐饮模型选择失败，改用候选评分: {meal_llm_error}")
                    meal_response = "{}"
                trip_plan = self._merge_meal_response(
                    trip_plan, meal_response, request, day_meals
                )
                trip_plan = self._ensure_logistical_meals(
                    trip_plan, request, day_meals
                )
            log_step_end(step_id)

            # ---- 步骤6.5: 餐饮、景点与逐段交通联合成可执行时间轴 ----
            report_progress(81, "正在生成逐时段行程")
            step_id = log_step_start("步骤6.5: 生成日程时间轴")
            trip_plan = self._build_daily_timelines(trip_plan, request)
            trip_plan = self._enforce_final_timeline_constraints(
                trip_plan, request, day_meals, candidates
            )
            log_step_end(step_id)

            # ---- 步骤7: 门票信息已在最终编排前查询 ----
            report_progress(84, "正在查询门票与开放时间")
            step_id = log_step_start("步骤7: 门票和开放时间")
            log_step_end(step_id)

            # ---- 步骤8: 重新计算预算 ----
            report_progress(93, "正在核算旅行预算")
            step_id = log_step_start("步骤8: 重新计算预算")
            self._recalculate_budget(trip_plan, request)
            log_step_end(step_id, f"总预算 ¥{trip_plan.budget.total}")

            # ---- 步骤9: 确定性校验 + Critic Agent 审查 ----
            report_progress(97, "正在进行行程质量校验")
            step_id = log_step_start("步骤9: 行程质量校验")
            trip_plan.quality_report = self._create_quality_report(trip_plan, request)
            log_step_end(
                step_id,
                "通过" if trip_plan.quality_report.passed else f"{len(trip_plan.quality_report.issues)} 个问题",
            )

            self._assert_multi_city_executable(trip_plan, request)
            self._assert_single_city_executable(trip_plan, request)
            logger.info(f"✅ 旅行计划生成完成! {len(trip_plan.days)}天")
            report_progress(100, "旅行计划生成完成")
            return trip_plan

        except Exception as e:
            if isinstance(e, TripPlanningInfeasible):
                raise
            logger.error(f"生成旅行计划失败: {e}")
            import traceback
            traceback.print_exc()
            if trip_plan is not None:
                trip_plan.weather_info = weather_info
                self._best_effort_enrich_stay_and_meals(trip_plan, request)
                trip_plan.quality_report = QualityReport(
                    passed=False,
                    issues=[QualityIssue(
                        code="partial_plan",
                        severity="warning",
                        message="部分自动补全未完成，建议确认餐饮、门票和预算信息。",
                    )],
                    critic_advice="已保留已生成的行程内容；请核对标注信息后再出行。",
                )
                report_progress(100, "部分行程已生成，部分信息待确认")
                raise TripPlanningDegraded(trip_plan, "部分行程已生成，部分信息待确认") from e
            if candidates:
                partial_plan = self._create_partial_plan(request, candidates, weather_info)
                partial_plan = self._apply_route_itinerary(
                    partial_plan, request, route_nodes, route_itinerary
                )
                partial_plan = self._ensure_scenic_route_node_attractions(
                    partial_plan, request, candidates, route_itinerary
                )
                partial_plan = self._enforce_route_attraction_consistency(
                    partial_plan, request, candidates
                )
                partial_plan = self._remove_duplicate_attractions(
                    partial_plan, candidates
                )
                partial_plan = self._apply_visit_duration_estimates(
                    partial_plan, request, candidates
                )
                partial_plan = self._enforce_time_constraints(
                    partial_plan, request, candidates
                )
                # 最后一级降级也要尽量形成可阅读、可执行的行程，避免前端出现
                # 全部 --:-- 且酒店、餐饮完全缺失的“坏结果”。各增强步骤内部
                # 已分别捕获异常，不会遮蔽最初的生成错误。
                self._best_effort_enrich_stay_and_meals(partial_plan, request)
                report_progress(100, "已根据真实景点生成部分行程")
                raise TripPlanningDegraded(partial_plan, "已根据真实景点生成部分行程") from e
            report_progress(100, "行程生成失败，请稍后重试")
            raise TripPlanningError("暂时无法生成可靠的旅行计划，请稍后重试。") from e

    # ==================== 餐饮搜索 ====================

    def _best_effort_enrich_stay_and_meals(
        self, trip_plan: TripPlan, request: TripRequest
    ) -> TripPlan:
        """为降级行程补全酒店和餐饮，各步骤失败时互不连带。"""
        if not any(day.hotel for day in trip_plan.days):
            try:
                self._select_hotel(trip_plan, request)
            except Exception as error:
                logger.exception(f"降级行程酒店补全失败: {error}")

        if not any(day.meals for day in trip_plan.days):
            try:
                day_meals = self._search_meals_for_plan(trip_plan, request)
                if day_meals:
                    # 空响应会触发 _merge_meal_response 的确定性候选兜底。
                    self._merge_meal_response(trip_plan, "{}", request, day_meals)
            except Exception as error:
                logger.exception(f"降级行程餐饮补全失败: {error}")

        try:
            self._build_daily_timelines(trip_plan, request)
        except Exception as error:
            logger.exception(f"降级行程时间轴生成失败: {error}")

        try:
            self._recalculate_budget(trip_plan, request)
        except Exception as error:
            logger.exception(f"降级行程预算重算失败: {error}")
        return trip_plan

    @staticmethod
    def _time_to_minutes(time_str: Optional[str]) -> Optional[int]:
        """将 HH:MM 转为当天分钟数；请求模型已校验格式，这里仍保留容错。"""
        if not time_str:
            return None
        try:
            hour, minute = (int(value) for value in time_str.split(":", 1))
            return hour * 60 + minute
        except (TypeError, ValueError):
            return None

    @classmethod
    def _pace_profile(cls, request: TripRequest) -> dict:
        return cls.PACE_PROFILES.get(
            getattr(request, "travel_pace", "balanced"),
            cls.PACE_PROFILES["balanced"],
        )

    @classmethod
    def _current_day_ready_minutes(cls, request: TripRequest) -> Optional[int]:
        """当天临时生成行程时，不得安排已经过去的时间。"""
        try:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            if date.fromisoformat(request.start_date) != now.date():
                return None
        except (TypeError, ValueError):
            return None
        # 给提交、生成及实际出发留 30 分钟，并向上取整到 5 分钟。
        minutes = now.hour * 60 + now.minute + 30
        return min(23 * 60 + 55, int(math.ceil(minutes / 5) * 5))

    @classmethod
    def _arrival_ready_minutes(cls, request: TripRequest) -> Optional[int]:
        """抵达后真正可以开始游览的时间，包含出站、进城和入住缓冲。"""
        arrival = cls._time_to_minutes(request.arrival_time)
        current_ready = cls._current_day_ready_minutes(request)
        if arrival is None:
            return current_ready
        if request.arrival_transport == "已在此地":
            return max(arrival, current_ready or arrival)
        mode = request.arrival_transport or ""
        if "飞机" in mode:
            buffer_minutes = 120
        elif "火车" in mode or "高铁" in mode:
            buffer_minutes = 60
        elif "自驾" in mode:
            buffer_minutes = 30
        else:
            buffer_minutes = 60
        ready = arrival + buffer_minutes
        return max(ready, current_ready or ready)

    @classmethod
    def _available_meal_types(cls, request: TripRequest, day_index: int) -> Tuple[str, ...]:
        """根据首尾日的真实可用时间，决定当天实际需要推荐哪些餐次。

        正常完整日提供早、中、晚三餐。抵达日和离开日采用保守阈值，避免把
        用户送去机场/车站的时间误当成可用游玩或用餐时间。
        """
        available = {"breakfast", "lunch", "dinner"}
        is_first_day = day_index == 0
        is_last_day = day_index == request.travel_days - 1

        if is_first_day:
            # 显式抵达时间按“到站 + 接驳”计算；当天临时生成但未填时间时，
            # 按当前上海时间计算，禁止再次推荐已经过去的早餐或午餐。
            ready = cls._arrival_ready_minutes(request)
            if ready is not None:
                if ready > 19 * 60:
                    available.clear()
                elif ready > 14 * 60:
                    available &= {"dinner"}
                elif ready > 10 * 60:
                    available &= {"lunch", "dinner"}

        if is_last_day:
            departure = cls._time_to_minutes(request.departure_time)
            if departure is not None:
                if departure < 8 * 60:
                    available.clear()
                elif departure < 13 * 60 + 30:
                    available &= {"breakfast"}
                elif departure < 17 * 60:
                    available &= {"breakfast", "lunch"}
                if request.trip_mode == "single_city" and (request.departure_transport or request.departure_location):
                    # 发车时间不是餐厅离开时间：为市内接驳及候车保留余量。
                    meal_deadline = departure - cls._terminal_buffer_minutes(request.departure_transport or "", arrival=False) - 45
                    if meal_deadline < 18 * 60 + 45:
                        available.discard("dinner")
                    if meal_deadline < 12 * 60 + 30:
                        available.discard("lunch")

        return tuple(meal_type for meal_type in ("breakfast", "lunch", "dinner") if meal_type in available)

    CITY_DISH_CATALOG: Dict[str, Dict[str, Tuple[str, ...]]] = {
        "南京": {
            "breakfast": ("鸭血粉丝汤", "牛肉锅贴", "汤包", "皮肚面", "赤豆元宵", "糖芋苗", "柴火馄饨"),
            "lunch": ("皮肚面", "盐水鸭", "鸭血粉丝汤", "南京小吃", "老卤面", "牛肉锅贴", "金陵烤鸭"),
            "dinner": ("南京菜", "盐水鸭", "炖生敲", "鸭子宴", "金陵烤鸭", "六合猪头肉", "民国菜"),
        },
        "西安": {
            "breakfast": ("肉丸胡辣汤", "油茶麻花", "甑糕", "豆腐脑"),
            "lunch": ("肉夹馍", "凉皮", "羊肉泡馍", "biangbiang面"),
            "dinner": ("羊肉泡馍", "葫芦鸡", "小酥肉", "陕菜"),
        },
        "北京": {
            "breakfast": ("豆汁焦圈", "炒肝", "糖油饼", "豆腐脑"),
            "lunch": ("炸酱面", "卤煮火烧", "爆肚", "北京小吃"),
            "dinner": ("北京烤鸭", "涮羊肉", "京菜", "炙子烤肉"),
        },
        "上海": {
            "breakfast": ("生煎", "小笼包", "粢饭团", "咸豆浆"),
            "lunch": ("本帮面", "排骨年糕", "葱油拌面", "小笼包"),
            "dinner": ("本帮菜", "红烧肉", "八宝鸭", "油爆虾"),
        },
        "成都": {
            "breakfast": ("红油抄手", "担担面", "豆花", "锅盔"),
            "lunch": ("钟水饺", "甜水面", "冒菜", "川菜"),
            "dinner": ("成都火锅", "串串香", "川菜", "烤鱼"),
        },
        "重庆": {
            "breakfast": ("重庆小面", "油茶", "豆花", "抄手"),
            "lunch": ("重庆小面", "酸辣粉", "豆花饭", "江湖菜"),
            "dinner": ("重庆火锅", "江湖菜", "烤鱼", "串串香"),
        },
        "广州": {
            "breakfast": ("广式早茶", "肠粉", "艇仔粥", "云吞面"),
            "lunch": ("烧鹅", "煲仔饭", "云吞面", "粤菜"),
            "dinner": ("粤菜", "白切鸡", "烧鹅", "啫啫煲"),
        },
        "杭州": {
            "breakfast": ("片儿川", "小笼包", "葱包桧", "豆浆油条"),
            "lunch": ("片儿川", "虾爆鳝面", "杭州小吃", "杭帮菜"),
            "dinner": ("杭帮菜", "东坡肉", "西湖醋鱼", "龙井虾仁"),
        },
        "长沙": {
            "breakfast": ("长沙米粉", "糖油粑粑", "葱油粑粑", "馄饨"),
            "lunch": ("长沙米粉", "臭豆腐", "小炒黄牛肉", "湘菜"),
            "dinner": ("湘菜", "剁椒鱼头", "口味虾", "小炒黄牛肉"),
        },
        "武汉": {
            "breakfast": ("热干面", "三鲜豆皮", "面窝", "糊汤粉"),
            "lunch": ("热干面", "武昌鱼", "排骨藕汤", "湖北菜"),
            "dinner": ("湖北菜", "武昌鱼", "排骨藕汤", "油焖大虾"),
        },
        "厦门": {
            "breakfast": ("沙茶面", "面线糊", "花生汤", "烧肉粽"),
            "lunch": ("沙茶面", "海蛎煎", "土笋冻", "闽南小吃"),
            "dinner": ("闽南菜", "姜母鸭", "海鲜", "同安封肉"),
        },
    }

    def _build_local_dish_catalog(self, request: TripRequest) -> Dict[str, List[str]]:
        """生成可用于高德检索的当地菜品池；常见城市走校验过的内置表。"""
        city = request.city.replace("市", "").strip()
        for city_name, catalog in self.CITY_DISH_CATALOG.items():
            if city_name in city or city in city_name:
                result = {meal_type: list(dishes) for meal_type, dishes in catalog.items()}
                logger.info(f"特色菜品池 [{request.city}]: {result}")
                return result

        system_prompt = """你是中国地方饮食知识助手。只返回严格 JSON，不推荐餐厅，只列长期公认、适合检索的地方菜品名称。"""
        user_prompt = f"""请为{request.city}生成当地特色菜品池，每个餐段4项。
早餐必须适合早晨，午餐应方便旅行途中用餐，晚餐可安排代表性地方菜。
不要写店名、品牌名、商圈名或泛泛的“当地美食”。
返回格式：{{"breakfast":["菜品"],"lunch":["菜品"],"dinner":["菜品"]}}"""
        try:
            response = self._run_planner(system_prompt, user_prompt)
            start, end = response.find("{"), response.rfind("}") + 1
            raw = json.loads(response[start:end])
            result: Dict[str, List[str]] = {}
            for meal_type in ("breakfast", "lunch", "dinner"):
                dishes = []
                for value in raw.get(meal_type, []):
                    dish = str(value).strip()
                    if 2 <= len(dish) <= 12 and not any(word in dish for word in ("餐厅", "饭店", "推荐", "附近")):
                        if dish not in dishes:
                            dishes.append(dish)
                if not dishes:
                    raise ValueError(f"{meal_type} 菜品为空")
                result[meal_type] = dishes[:5]
            logger.info(f"特色菜品池 [{request.city}]: {result}")
            return result
        except Exception as exc:
            logger.warning(f"特色菜品池生成失败，使用通用检索词: {exc}")
            return {
                "breakfast": ["本地早餐", "汤包", "面馆", "老字号小吃"],
                "lunch": ["地方小吃", "本地菜", "面馆", "老字号"],
                "dinner": ["地方菜", "本地菜", "老字号", "特色菜"],
            }

    def _build_dish_schedule(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        dish_catalog: Dict[str, List[str]],
    ) -> Dict[int, Dict[str, dict]]:
        """先为整个行程分配菜品；菜品池未耗尽前，任何餐次都不会重复。"""
        schedule: Dict[int, Dict[str, dict]] = {}
        usage: Dict[str, int] = {}
        recent_dishes: List[str] = []

        for day in trip_plan.days:
            schedule[day.day_index] = {}
            for meal_type in self._available_meal_types(request, day.day_index):
                pool = dish_catalog.get(meal_type, [])
                if not pool:
                    continue

                unused = [dish for dish in pool if usage.get(dish, 0) == 0]
                if unused:
                    selected = unused[0]
                    repeat_allowed = False
                else:
                    not_recent = [dish for dish in pool if dish not in recent_dishes[-2:]]
                    choices = not_recent or pool
                    selected = min(choices, key=lambda dish: (usage.get(dish, 0), pool.index(dish)))
                    repeat_allowed = True

                schedule[day.day_index][meal_type] = {
                    "dish": selected,
                    "repeat_allowed": repeat_allowed,
                }
                usage[selected] = usage.get(selected, 0) + 1
                recent_dishes.append(selected)

        logger.info(
            "全程菜品轮换: "
            + "; ".join(
                f"Day{day_idx + 1} "
                + ", ".join(f"{meal_type}={item['dish']}" for meal_type, item in meals.items())
                for day_idx, meals in schedule.items()
            )
        )
        return schedule

    @staticmethod
    def _match_dishes(poi: dict, dishes: List[str]) -> List[str]:
        combined = "".join(str(poi.get(field, "")) for field in ("name", "type", "tag", "address"))
        return [dish for dish in dishes if dish.lower() in combined.lower()]

    @staticmethod
    def _meal_open_status(poi: dict, meal_type: str) -> Optional[bool]:
        """根据高德营业时间判断目标餐次是否营业；无法解析时返回 None。"""
        hours = str(poi.get("opening_hours", "") or "").strip()
        if not hours:
            return None
        if any(marker in hours for marker in ("暂停营业", "歇业", "未营业", "已关闭")):
            return False
        if any(marker in hours for marker in ("24小时", "全天")):
            return True

        target_minutes = {
            "breakfast": 8 * 60,
            "lunch": 12 * 60,
            "dinner": 18 * 60 + 30,
        }.get(meal_type)
        if target_minutes is None:
            return None

        ranges = re.findall(
            r"(\d{1,2}):(\d{2})\s*[-~～—至]\s*(\d{1,2}):(\d{2})",
            hours,
        )
        if not ranges:
            return None
        for start_hour, start_minute, end_hour, end_minute in ranges:
            start = int(start_hour) * 60 + int(start_minute)
            end = int(end_hour) * 60 + int(end_minute)
            if end >= start and start <= target_minutes <= end:
                return True
            if end < start and (target_minutes >= start or target_minutes <= end):
                return True
        return False

    def _is_valid_meal_candidate(
        self,
        poi: dict,
        meal_type: str,
        target_dish: str = "",
        require_dish_evidence: bool = False,
        actual_period: Optional[Tuple[int, int]] = None,
    ) -> bool:
        """跨城市通用校验：搜索相关不等于餐厅确实匹配目标菜品。"""
        name = str(poi.get("name", ""))
        poi_type = str(poi.get("type", ""))
        address = str(poi.get("address", ""))
        tag = str(poi.get("tag", ""))
        combined = name + poi_type + tag + address
        if any(marker in name + poi_type + tag for marker in NON_DINING_BUSINESS_MARKERS):
            return False
        if meal_type in {"lunch", "dinner"} and any(
            marker in name for marker in ("鸭脖", "鸭头", "鸭货", "零食", "卤味", "熟食", "糕点", "甜品")
        ):
            return False
        classified = self._classify_meal_type(name, poi_type + tag, address)
        if not classified:
            return False
        open_status = (
            self._opening_hours_cover_period(
                str(poi.get("opening_hours", "") or ""), *actual_period
            ) if actual_period else self._meal_open_status(poi, meal_type)
        )
        poi["_meal_open_verified"] = open_status is True
        if open_status is False:
            return False

        if require_dish_evidence and target_dish:
            # 高德可能模糊返回无关餐厅，必须在结构化字段中再次找到菜品文本。
            if not self._match_dishes(poi, [target_dish]):
                return False

        if meal_type == "breakfast":
            if any(word in combined for word in self.NOT_BREAKFAST_KW):
                return False
            # 营业时间未知时，只允许名称/分类本身明确是早餐业态的门店。
            # 不能因为搜索词命中了“煎饼卷大葱”之类菜名，就把私房菜、
            # 鲁菜馆等普通正餐店安排到早上八点。
            business_identity = name + poi_type + address
            strong_breakfast_signal = any(
                keyword in business_identity
                for keyword in (
                    "早餐", "早点", "早茶", "包子", "粥", "豆浆", "油条",
                    "煎饼", "锅贴", "馄饨", "汤包", "胡辣汤", "甜沫",
                )
            )
            if open_status is not True and not strong_breakfast_signal:
                return False
            return strong_breakfast_signal or "breakfast" in classified

        # “饭店/菜馆”曾只被名称规则归为晚餐，普通中餐厅因此无法参加午餐
        # 兜底。正餐业态在午晚餐均可用，营业时间仍按实际餐段单独校验。
        regular_restaurant = any(marker in name + poi_type for marker in (
            "中餐厅", "外国餐厅", "快餐厅", "餐厅", "饭店", "菜馆", "食府", "家常菜", "土菜", "私房菜",
        ))
        return meal_type in classified or regular_restaurant or bool(target_dish and self._match_dishes(poi, [target_dish]))

    @classmethod
    def _is_remote_day(cls, day: DayPlan) -> bool:
        """远郊日允许一个大型景点占据全天，并采用单独的用餐策略。"""
        if len(day.attractions) != 1:
            return False
        attraction = day.attractions[0]
        if day.hotel and day.hotel.location:
            distance = cls._haversine_distance(
                day.hotel.location.longitude,
                day.hotel.location.latitude,
                attraction.location.longitude,
                attraction.location.latitude,
            )
            # 有酒店和景点的真实坐标时，距离是唯一判据。过去这里仅在
            # >=12km 时提前返回，随后又用“名称含景区且游览较久”兜底，
            # 导致市中心的大明湖也被误判为远郊全天景点。
            if math.isfinite(distance):
                return distance >= 12
        text = f"{attraction.name} {attraction.category or ''}"
        return attraction.visit_duration >= 180 and any(
            marker in text for marker in ("山", "景区", "森林", "峡谷", "度假区")
        )

    @classmethod
    def _meal_anchor(cls, day: DayPlan, meal_type: str) -> Optional[Tuple[float, float, str]]:
        if meal_type == "breakfast" and day.hotel and day.hotel.location:
            location = day.hotel.location
            if location.longitude and location.latitude:
                return location.longitude, location.latitude, day.hotel.name
        if not day.attractions or (meal_type == "lunch" and day.route_segment):
            if day.hotel and day.hotel.location:
                location = day.hotel.location
                return location.longitude, location.latitude, day.hotel.name
            return None
        # 远郊景区附近往往没有可靠晚餐候选；晚餐应在返城后、酒店附近解决。
        if meal_type == "dinner" and cls._is_remote_day(day) and day.hotel and day.hotel.location:
            location = day.hotel.location
            return location.longitude, location.latitude, day.hotel.name
        index = {"breakfast": 0, "lunch": len(day.attractions) // 2, "dinner": -1}[meal_type]
        attraction = day.attractions[index]
        return attraction.location.longitude, attraction.location.latitude, attraction.name

    @staticmethod
    def _meal_route_neighbors(
        day: DayPlan, meal_type: str
    ) -> Tuple[Optional[Location], Optional[Location]]:
        """返回餐次在时间轴中的前后地点，用于减少餐厅造成的路线绕行。"""
        attractions = day.attractions or []
        hotel_location = day.hotel.location if day.hotel else None
        if meal_type == "breakfast":
            return hotel_location, attractions[0].location if attractions else None
        if meal_type == "dinner":
            return attractions[-1].location if attractions else None, hotel_location
        if day.route_segment:
            return hotel_location, attractions[0].location if attractions else hotel_location
        if not attractions:
            return hotel_location, hotel_location
        split = max(1, math.ceil(len(attractions) / 2))
        before = attractions[split - 1].location
        after = attractions[split].location if split < len(attractions) else hotel_location
        return before, after

    def _meal_route_detour_km(self, day: DayPlan, meal_type: str, poi: dict) -> float:
        """用真实坐标估算候选餐厅相对原路线增加的绕行距离。"""
        try:
            meal_location = Location(
                longitude=float(poi.get("longitude", 0) or 0),
                latitude=float(poi.get("latitude", 0) or 0),
            )
        except (TypeError, ValueError):
            return 99.0
        if not meal_location.longitude or not meal_location.latitude:
            return 99.0
        before, after = self._meal_route_neighbors(day, meal_type)
        legs = []
        if before:
            legs.append(self._haversine_distance(
                before.longitude, before.latitude,
                meal_location.longitude, meal_location.latitude,
            ))
        if after:
            legs.append(self._haversine_distance(
                meal_location.longitude, meal_location.latitude,
                after.longitude, after.latitude,
            ))
        direct = 0.0
        if before and after:
            direct = self._haversine_distance(
                before.longitude, before.latitude,
                after.longitude, after.latitude,
            )
        if not legs or any(not math.isfinite(value) for value in legs):
            return 99.0
        return max(0.0, sum(legs) - (direct if math.isfinite(direct) else 0.0))

    def _search_meals_for_plan(self, trip_plan: TripPlan, request: TripRequest) -> List[dict]:
        """
        按餐次围绕不同锚点搜索真实餐厅：早餐靠近酒店，午餐靠近中间景点，
        晚餐靠近最后一个景点。搜索词来自当地特色菜品池。
        """
        SEARCH_RADIUS = 2500
        MAX_PER_MEAL_TYPE = 15  # 每餐段最多保留候选数
        day_meals = []
        total_pois = 0
        catalogs_by_stop: Dict[str, Dict[str, List[str]]] = {}
        dish_schedule: Dict[int, Dict[str, dict]] = {}
        if self._is_route_mode(request):
            for stop in dict.fromkeys(
                day.overnight_city or day.end_location or request.city
                for day in trip_plan.days
            ):
                local_request = request.model_copy(update={
                    "city": stop,
                    "trip_mode": "single_city",
                    "destinations": [],
                })
                local_catalog = self._build_local_dish_catalog(local_request)
                catalogs_by_stop[stop] = local_catalog
                local_days = [
                    day for day in trip_plan.days
                    if (day.overnight_city or day.end_location or request.city) == stop
                ]
                local_plan = trip_plan.model_copy(update={"days": local_days})
                dish_schedule.update(
                    self._build_dish_schedule(local_plan, request, local_catalog)
                )
        else:
            catalogs_by_stop[request.city] = self._build_local_dish_catalog(request)
            dish_schedule = self._build_dish_schedule(
                trip_plan, request, catalogs_by_stop[request.city]
            )
        self._local_dish_catalogs = catalogs_by_stop
        actual_used_dishes = set()

        for day_position, day in enumerate(trip_plan.days):
            meal_city = day.overnight_city or day.end_location or request.city
            dish_catalog = catalogs_by_stop.get(
                meal_city, catalogs_by_stop.get(request.city, {})
            )
            attrs = day.attractions
            available_meal_types = self._available_meal_types(request, day.day_index)
            day_meal_data = {
                "day": day.day_index + 1,
                "attractions": [{"name": a.name, "lng": a.location.longitude, "lat": a.location.latitude}
                               for a in attrs],
                "meals": {"breakfast": [], "lunch": [], "dinner": []},
                "available_meal_types": available_meal_types,
                "dish_catalog": dish_catalog,
                "target_dishes": {},
                "dish_repeat_allowed": {},
            }

            if not available_meal_types:
                day_meals.append(day_meal_data)
                continue

            for meal_type in available_meal_types:
                search_day = day
                search_city = meal_city
                if request.trip_mode == "multi_city" and day.route_segment and day_position > 0:
                    origin_attrs = [a for a in attrs if self._attraction_route_side(a, day.route_segment) == "origin"]
                    if meal_type == "breakfast" or (meal_type == "lunch" and origin_attrs):
                        search_day = day.model_copy(update={
                            "hotel": trip_plan.days[day_position - 1].hotel,
                            "attractions": origin_attrs,
                            "route_segment": None,
                        })
                        search_city = day.route_segment.origin
                anchor = self._meal_anchor(search_day, meal_type)
                if not anchor:
                    continue
                lng, lat, anchor_name = anchor
                target_info = dish_schedule.get(day.day_index, {}).get(meal_type, {})
                target_dish = target_info.get("dish", "")
                repeat_allowed = bool(target_info.get("repeat_allowed"))

                pool = dish_catalog.get(meal_type, [])
                attempts = []
                for dish in [target_dish, *pool]:
                    if not dish or dish in attempts:
                        continue
                    if dish in actual_used_dishes and not repeat_allowed:
                        continue
                    attempts.append(dish)
                    if len(attempts) >= 3:
                        break

                pois = []
                effective_dish = ""
                for attempt_index, dish in enumerate(attempts):
                    raw_pois = self._call_amap_around_search(
                        lng, lat, radius=SEARCH_RADIUS, keywords=dish
                    )
                    matched_pois = [
                        poi for poi in raw_pois
                        if self._is_valid_meal_candidate(
                            poi, meal_type, dish, require_dish_evidence=True
                        )
                    ]
                    if not matched_pois and attempt_index == 0:
                        raw_pois = self._call_amap_around_search(
                            lng, lat, radius=5000, keywords=dish
                        )
                        matched_pois = [
                            poi for poi in raw_pois
                            if self._is_valid_meal_candidate(
                                poi, meal_type, dish, require_dish_evidence=True
                            )
                        ]
                    if matched_pois:
                        pois = matched_pois
                        effective_dish = dish
                        break

                if not pois:
                    # 仍可推荐真实且餐段合适的餐厅，但不再绑定未经证实的具体菜品。
                    raw_pois = self._call_amap_around_search(lng, lat, radius=SEARCH_RADIUS)
                    pois = [
                        poi for poi in raw_pois
                        if self._is_valid_meal_candidate(poi, meal_type)
                    ]
                if not pois:
                    # 部分地图周边接口在不传关键词时返回商圈设施而非餐厅。
                    # 显式用餐饮业态再搜一次，避免市中心明明餐厅很多却得到
                    # “午餐0候选”。仍然只接受高德返回且类型校验通过的 POI。
                    generic_keyword = "早餐" if meal_type == "breakfast" else "餐厅"
                    raw_pois = self._call_amap_around_search(
                        lng, lat, radius=3500, keywords=generic_keyword
                    )
                    pois = [
                        poi for poi in raw_pois
                        if self._is_valid_meal_candidate(poi, meal_type)
                    ]

                was_used = bool(effective_dish and effective_dish in actual_used_dishes)
                if effective_dish:
                    actual_used_dishes.add(effective_dish)
                day_meal_data["target_dishes"][meal_type] = effective_dish
                day_meal_data["dish_repeat_allowed"][meal_type] = repeat_allowed and was_used
                keyword_grounded = bool(effective_dish)

                seen_ids = set()
                for poi in pois:
                    pid = poi.get("id", "")
                    # 没有高德 POI ID 的结果无法被后续确定性校验，不能作为真实候选。
                    if not pid or pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    dish_options = [effective_dish] if keyword_grounded else []
                    poi["dish_options"] = dish_options
                    poi["dish_matches"] = self._match_dishes(poi, dish_options)
                    poi["keyword_grounded"] = keyword_grounded
                    poi["anchor_name"] = anchor_name
                    poi["candidate_meal_type"] = meal_type
                    score = self._score_nearby_meal(poi, search_city, dish_options)
                    if score < 0:
                        continue
                    # 候选餐厅还必须尽量位于前后行程之间，避免为了吃饭跨区折返。
                    detour_km = self._meal_route_detour_km(search_day, meal_type, poi)
                    poi["route_detour_km"] = round(detour_km, 2)
                    # 餐厅是行程的服务节点，不能为了某家店让主路线跨区折返。
                    # 远郊日午餐略放宽，普通市区日仍以景点路线为主。
                    max_detour = 5.0 if meal_type == "lunch" and self._is_remote_day(day) else 3.0
                    if meal_type == "breakfast":
                        max_detour = 2.0
                    if detour_km > max_detour:
                        logger.debug(
                            f"Day{day.day_index + 1} {meal_type}: 跳过绕行"
                            f"{detour_km:.1f}km的餐厅 '{poi.get('name', '')}'"
                        )
                        continue
                    poi["meal_score"] = score - min(8.0, detour_km * 1.2)
                    day_meal_data["meals"][meal_type].append(poi)

            # 每餐段排序 + 截断
            for meal_type in ["breakfast", "lunch", "dinner"]:
                lst = day_meal_data["meals"][meal_type]
                # 去重（一个POI可能匹配多个餐段，但同一餐段内不应重复）
                seen_in_type = set()
                uniq = []
                for p in lst:
                    pid = p.get("id", "") or p.get("name", "")
                    if pid in seen_in_type:
                        continue
                    seen_in_type.add(pid)
                    uniq.append(p)
                # 早餐尤其依赖开门时间：只要存在高德明确核验为营业的候选，
                # 就不再让营业时间未知的餐厅参与最终选择。
                verified_open = [p for p in uniq if p.get("_meal_open_verified")]
                if meal_type == "breakfast" and verified_open:
                    uniq = verified_open
                uniq.sort(key=lambda x: x.get("meal_score", 0), reverse=True)
                day_meal_data["meals"][meal_type] = uniq[:MAX_PER_MEAL_TYPE]
                total_pois += len(day_meal_data["meals"][meal_type])

            logger.debug(
                f"  Day{day.day_index+1} 候选: "
                f"早餐{len(day_meal_data['meals']['breakfast'])} "
                f"午餐{len(day_meal_data['meals']['lunch'])} "
                f"晚餐{len(day_meal_data['meals']['dinner'])}"
            )
            day_meals.append(day_meal_data)

        logger.info(f"周边搜索总计: {total_pois} 个餐饮候选")
        return day_meals

    def _merge_meal_response(
        self,
        trip_plan: TripPlan,
        meal_response: str,
        request: Optional[TripRequest] = None,
        day_meals: Optional[List[dict]] = None,
    ) -> TripPlan:
        """合并餐饮结果；有真实候选时，模型只能选择候选中的 POI。"""
        days_data = []
        try:
            json_str = meal_response
            if "```json" in meal_response:
                json_start = meal_response.find("```json") + 7
                json_end = meal_response.find("```", json_start)
                json_str = meal_response[json_start:json_end].strip()
            elif "```" in meal_response:
                json_start = meal_response.find("```") + 3
                json_end = meal_response.find("```", json_start)
                json_str = meal_response[json_start:json_end].strip()
            elif "{" in meal_response:
                json_start = meal_response.find("{")
                json_end = meal_response.rfind("}") + 1
                json_str = meal_response[json_start:json_end]

            meal_data = json.loads(json_str)
            days_data = meal_data.get("days", [])
        except Exception as e:
            logger.warning(f"餐饮响应解析失败: {e}，将按真实候选分数确定性选择")

        response_by_day = {
            item.get("day_index", 0): item.get("meals", [])
            for item in days_data if isinstance(item, dict)
        }

        if day_meals is not None:
            candidate_days = {item.get("day", 0) - 1: item for item in day_meals}
            used_poi_ids = set()
            used_dishes = set()
            default_cost = {"breakfast": 20, "lunch": 50, "dinner": 80}

            for day in trip_plan.days:
                allowed_types = self._available_meal_types(request, day.day_index) if request else ("breakfast", "lunch", "dinner")
                candidate_day = candidate_days.get(day.day_index, {})
                response_meals = response_by_day.get(day.day_index, [])
                response_by_type = {
                    item.get("type"): item for item in response_meals if isinstance(item, dict)
                }
                selected_meals = []

                for meal_type in allowed_types:
                    candidates = candidate_day.get("meals", {}).get(meal_type, [])
                    if not candidates:
                        logger.warning(f"Day{day.day_index + 1} {meal_type}: 无真实餐厅候选，跳过而不编造")
                        continue

                    model_choice = response_by_type.get(meal_type, {})
                    requested_poi_id = str(model_choice.get("poi_id", "")).strip()
                    requested_name = str(model_choice.get("name", "")).strip()
                    selected = next(
                        (candidate for candidate in candidates if requested_poi_id and candidate.get("id") == requested_poi_id),
                        None,
                    )
                    if selected is None:
                        selected = next(
                            (candidate for candidate in candidates if requested_name and candidate.get("name") == requested_name),
                            None,
                        )
                    if selected is not None and selected.get("id") in used_poi_ids:
                        selected = None
                    # 模型选择若比路线最优候选明显更绕，使用路线更合理的真实餐厅。
                    available_candidates = [
                        candidate for candidate in candidates
                        if candidate.get("id") and candidate.get("id") not in used_poi_ids
                    ]
                    route_best = available_candidates[0] if available_candidates else None
                    if (
                        selected is not None
                        and route_best is not None
                        and float(selected.get("meal_score", 0) or 0)
                        < float(route_best.get("meal_score", 0) or 0) - 2.5
                    ):
                        selected = route_best
                    if selected is None:
                        selected = next(
                            (candidate for candidate in candidates if candidate.get("id") and candidate.get("id") not in used_poi_ids),
                            None,
                        )
                    if selected is None:
                        logger.warning(f"Day{day.day_index + 1} {meal_type}: 真实候选均已使用，跳过重复餐厅")
                        continue

                    poi_id = selected.get("id") or None
                    if poi_id:
                        used_poi_ids.add(poi_id)

                    dish_options = selected.get("dish_options", [])
                    recommended_dish = str(model_choice.get("recommended_dish", "")).strip()
                    if recommended_dish not in dish_options:
                        matches = selected.get("dish_matches", [])
                        recommended_dish = matches[0] if matches else (dish_options[0] if dish_options else "")
                    repeat_allowed = candidate_day.get("dish_repeat_allowed", {}).get(meal_type, False)
                    if recommended_dish in used_dishes and not repeat_allowed:
                        logger.warning(
                            f"Day{day.day_index + 1} {meal_type}: 拒绝重复菜品 {recommended_dish}"
                        )
                        recommended_dish = ""
                    if recommended_dish:
                        used_dishes.add(recommended_dish)

                    api_cost = selected.get("estimated_cost", 0) or 0
                    model_cost = model_choice.get("estimated_cost", 0) or 0
                    try:
                        estimated_cost = int(float(api_cost or model_cost))
                    except (TypeError, ValueError):
                        estimated_cost = 0
                    if not 1 <= estimated_cost <= 2000:
                        estimated_cost = default_cost[meal_type]

                    rating = selected.get("rating") if selected.get("rating_available") else None
                    distance_m = selected.get("distance_m", 0) or 0
                    reason_parts = []
                    if selected.get("anchor_name"):
                        reason_parts.append(f"距{selected['anchor_name']}约{distance_m / 1000:.1f}公里")
                    reason_parts.append(f"高德评分{rating:.1f}" if rating else "高德暂无评分")

                    lng, lat = selected.get("longitude", 0), selected.get("latitude", 0)
                    location = Location(longitude=lng, latitude=lat) if lng and lat else None
                    selected_meals.append(Meal(
                        type=meal_type,
                        name=selected.get("name", ""),
                        address=selected.get("address") or None,
                        location=location,
                        description="；".join(reason_parts),
                        estimated_cost=estimated_cost,
                        poi_id=poi_id,
                        recommended_dish=recommended_dish or None,
                        rating=rating,
                        data_source="amap",
                        opening_hours=str(selected.get("opening_hours", "") or ""),
                        opening_hours_verified=bool(selected.get("_meal_open_verified")),
                        route_anchor_name=selected.get("anchor_name"),
                        route_anchor_poi_id=next((a.poi_id for a in day.attractions if a.name == selected.get("anchor_name")), None),
                    ))

                day.meals = selected_meals
        else:
            # 兼容不带候选数据的内部调用，但仍执行首尾日餐次过滤。
            for day_meal in days_data:
                day_idx = day_meal.get("day_index", 0)
                if day_idx >= len(trip_plan.days):
                    continue
                allowed_types = set(self._available_meal_types(request, day_idx)) if request else {"breakfast", "lunch", "dinner", "snack"}
                new_meals = []
                for item in day_meal.get("meals", []):
                    meal_type = item.get("type", "lunch")
                    if meal_type not in allowed_types:
                        continue
                    new_meals.append(Meal(
                        type=meal_type,
                        name=item.get("name") or "当地特色美食",
                        address=item.get("address"),
                        estimated_cost=item.get("estimated_cost", 0),
                    ))
                trip_plan.days[day_idx].meals = new_meals

            if request:
                for day in trip_plan.days:
                    if not self._available_meal_types(request, day.day_index):
                        day.meals = []

        logger.info(f"餐饮合并: {sum(len(d.meals) for d in trip_plan.days)} 餐")

        return trip_plan

    def _ensure_logistical_meals(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        day_meals: Optional[List[dict]] = None,
    ) -> TripPlan:
        """补齐必要餐次；远郊无实体候选时明确给出自备餐建议。"""
        candidate_days = {
            int(item.get("day", 0)) - 1: item
            for item in (day_meals or []) if isinstance(item, dict)
        }
        default_cost = {"breakfast": 20, "lunch": 40, "dinner": 70}

        for day in trip_plan.days:
            expected = self._available_meal_types(request, day.day_index)
            if request.trip_mode == "single_city" and day.day_index == 0:
                arrival_hotel = next((n for n in day.timeline if n.kind == "hotel" and n.end_time), None)
                ready = self._time_to_minutes(arrival_hotel.end_time) if arrival_hotel else None
                if ready is not None and ready > 840:
                    expected = tuple(kind for kind in expected if kind != "lunch")
            existing = {meal.type for meal in day.meals}
            candidate_day = candidate_days.get(day.day_index, {})
            for meal_type in expected:
                if meal_type in existing:
                    continue
                candidates = candidate_day.get("meals", {}).get(meal_type, [])
                selected = candidates[0] if candidates else None
                if selected:
                    lng = float(selected.get("longitude", 0) or 0)
                    lat = float(selected.get("latitude", 0) or 0)
                    raw_cost = selected.get("estimated_cost", 0) or default_cost[meal_type]
                    try:
                        estimated_cost = int(float(raw_cost))
                    except (TypeError, ValueError):
                        estimated_cost = default_cost[meal_type]
                    day.meals.append(Meal(
                        type=meal_type,
                        name=str(selected.get("name") or ""),
                        address=selected.get("address") or None,
                        location=(Location(longitude=lng, latitude=lat) if lng and lat else None),
                        description="高德地图核验的餐饮候选。",
                        estimated_cost=estimated_cost,
                        poi_id=selected.get("id") or None,
                        rating=(selected.get("rating") if selected.get("rating_available") else None),
                        data_source="amap",
                        opening_hours=str(selected.get("opening_hours", "") or ""),
                        opening_hours_verified=bool(selected.get("_meal_open_verified")),
                        route_anchor_name=selected.get("anchor_name"),
                        route_anchor_poi_id=next((a.poi_id for a in day.attractions if a.name == selected.get("anchor_name")), None),
                    ))
                    existing.add(meal_type)
                    continue

                if self._is_remote_day(day) and meal_type == "lunch":
                    attraction = day.attractions[0]
                    day.meals.append(Meal(
                        type="lunch",
                        name="景区内用餐或自备简餐",
                        address=attraction.address or None,
                        location=attraction.location,
                        description=(
                            f"{attraction.name}周边未检索到可靠午餐商户，"
                            "请优先确认景区餐饮，或提前准备便携食品和饮水。"
                        ),
                        estimated_cost=default_cost["lunch"],
                        data_source="logistics_advice",
                        opening_hours_verified=True,
                        route_anchor_name=attraction.name,
                        route_anchor_poi_id=attraction.poi_id,
                    ))
                    existing.add(meal_type)
                    logger.warning(
                        f"远郊用餐兜底 Day{day.day_index + 1}: 添加景区午餐/自备提示"
                    )
                elif self._is_remote_day(day) and meal_type in {"breakfast", "dinner"}:
                    hotel_location = day.hotel.location if day.hotel else None
                    label = (
                        "酒店早餐或附近早餐店"
                        if meal_type == "breakfast"
                        else "返城后在酒店附近用餐"
                    )
                    day.meals.append(Meal(
                        type=meal_type,
                        name=label,
                        address=day.hotel.address if day.hotel else None,
                        location=hotel_location,
                        description=(
                            "未检索到营业时间可靠的实体餐厅，请优先使用酒店早餐或现场确认附近早食。"
                            if meal_type == "breakfast"
                            else "远郊返程时间可能波动，未绑定具体餐厅；返城后请在酒店附近就餐。"
                        ),
                        estimated_cost=default_cost[meal_type],
                        data_source="logistics_advice",
                        opening_hours_verified=True,
                        route_anchor_name=day.hotel.name if day.hotel else None,
                    ))
                    existing.add(meal_type)
                    logger.warning(
                        f"远郊用餐兜底 Day{day.day_index + 1}: 添加{label}提示"
                    )
                else:
                    # 非远郊日也必须为正餐保留时间。没有可靠实体候选时给出
                    # 明确的现场选择节点，而不是直接删掉午餐、让景点占满全天。
                    anchor = self._meal_anchor(day, meal_type)
                    anchor_location = (
                        Location(longitude=anchor[0], latitude=anchor[1])
                        if anchor else (day.hotel.location if day.hotel else None)
                    )
                    labels = {
                        "breakfast": "酒店早餐或附近早餐店",
                        "lunch": "行程附近午餐（现场选择）",
                        "dinner": "行程附近晚餐（现场选择）",
                    }
                    day.meals.append(Meal(
                        type=meal_type,
                        name=labels[meal_type],
                        address=None,
                        location=anchor_location,
                        description=(
                            "当前未检索到营业时间和类型均可靠的实体餐厅；"
                            "时间轴已保留用餐时间，请在相邻景点附近现场选择。"
                        ),
                        estimated_cost=default_cost[meal_type],
                        data_source="logistics_advice",
                        opening_hours_verified=True,
                        route_anchor_name=anchor[2] if anchor else (day.hotel.name if day.hotel else None),
                        route_anchor_poi_id=next((a.poi_id for a in day.attractions if anchor and a.name == anchor[2]), None),
                    ))
                    existing.add(meal_type)
                    logger.warning(
                        f"市区用餐兜底 Day{day.day_index + 1}: "
                        f"为{meal_type}保留现场选择时间"
                    )

            order = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}
            day.meals.sort(key=lambda meal: order.get(meal.type, 9))
        return trip_plan

    def _build_attraction_query(self, request: TripRequest) -> str:
        """构建景点搜索查询（LangGraph 版本 — 自然语言，不用 [TOOL_CALL:...]）"""
        keywords = request.preferences[0] if request.preferences else "热门景点"
        return f"请使用 amap_text_search 工具搜索{request.city}的{keywords}相关景点，keywords=\"{keywords}\", city=\"{request.city}\""

    # ==================== 景点筛选（以下方法不变，纯 Python 逻辑）====================

    @staticmethod
    def _clean_research_names(values: Any) -> List[str]:
        """清洗研究 Agent 输出；这里只接受名称，绝不信任模型生成的 POI 字段。"""
        if not isinstance(values, list):
            return []
        result: List[str] = []
        seen: set[str] = set()
        for value in values:
            if isinstance(value, dict):
                value = value.get("name", "")
            name = re.sub(r"^[\s\d.、-]+", "", str(value or "")).strip()
            name = name.strip("，。；;：:|/\\")
            normalized = MultiAgentTripPlanner._normalize_attr_name(name)
            if not normalized or len(name) < 2 or len(name) > 40 or normalized in seen:
                continue
            seen.add(normalized)
            result.append(name)
        return result

    def _research_destination(self, request: TripRequest) -> Dict[str, List[str]]:
        """动态发现目的地代表地点；返回的名称必须再经高德真实 POI 核验。"""
        cache_key = json.dumps(
            {"city": request.city, "preferences": sorted(request.preferences)},
            ensure_ascii=False,
            sort_keys=True,
        )
        cache = self.__dict__.setdefault("_destination_research_cache", {})
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        query = (
            f"目的地：{request.city}\n"
            f"旅行天数：{request.travel_days}\n"
            f"用户偏好：{', '.join(request.preferences) if request.preferences else '未指定'}\n"
            "请给出需要由地图服务核验的分层地点名称。"
        )
        empty = {"classic": [], "preference": [], "local": []}
        try:
            response = self._run_planner(
                DESTINATION_RESEARCH_PROMPT,
                query,
                max_tokens=900,
            )
            text = response.strip()
            if "```json" in text:
                text = text.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in text:
                text = text.split("```", 1)[1].split("```", 1)[0].strip()
            elif "{" in text and "}" in text:
                text = text[text.find("{"):text.rfind("}") + 1]
            data = json.loads(text)
            result = {
                tier: self._clean_research_names(data.get(tier, []))
                for tier in empty
            }
            # 跨层去重，优先级 classic > preference > local。
            seen: set[str] = set()
            for tier in ("classic", "preference", "local"):
                unique = []
                for name in result[tier]:
                    normalized = self._normalize_attr_name(name)
                    if normalized in seen:
                        continue
                    seen.add(normalized)
                    unique.append(name)
                result[tier] = unique
            cache[cache_key] = result
            logger.info(
                "目的地研究: "
                + ", ".join(f"{tier}={len(names)}" for tier, names in result.items())
            )
            return result
        except Exception as error:
            # 研究 Agent 失败时仍可使用原有高德泛搜索，不让整次规划失败。
            logger.warning(f"目的地研究失败，继续使用地图泛搜索: {error}")
            return empty

    @classmethod
    def _research_name_matches(cls, poi_name: str, researched_name: str) -> bool:
        """判断高德 POI 是否能核验研究 Agent 给出的名称。"""
        poi_key = cls._normalize_attr_name(poi_name)
        research_key = cls._normalize_attr_name(researched_name)
        if not poi_key or not research_key:
            return False
        if poi_key == research_key:
            return True
        shorter, longer = sorted((poi_key, research_key), key=len)
        return len(shorter) >= 2 and shorter in longer

    def _search_researched_attractions(
        self, request: TripRequest
    ) -> List[dict]:
        """批量核验动态研究名称，并给真实 POI 写入候选层级。"""
        profile = self._research_destination(request)
        results: List[dict] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        tier_priority = {"classic": 3, "preference": 2, "local": 1}

        for tier in ("classic", "preference", "local"):
            names = profile.get(tier, [])
            # 高德支持“|”分隔的多关键词；每组不宜过大，以免相关结果被截断。
            for start in range(0, len(names), 4):
                chunk = names[start:start + 4]
                if not chunk:
                    continue
                pois = self._call_mcp_search("|".join(chunk), request.city)
                for poi in pois:
                    if self._is_non_attraction_poi(poi):
                        logger.debug(
                            f"  ↳ 目的地研究排除附属设施: {poi.get('name', '')}"
                        )
                        continue
                    matched_name = next(
                        (
                            name for name in chunk
                            if self._research_name_matches(poi.get("name", ""), name)
                        ),
                        None,
                    )
                    if not matched_name:
                        continue
                    identity = poi.get("id") or self._normalize_attr_name(poi.get("name", ""))
                    if not identity:
                        continue
                    # 同一 POI 若被多个层级命中，只保留优先级最高的一层。
                    if identity in seen_ids:
                        for existing in results:
                            existing_identity = existing.get("id") or self._normalize_attr_name(existing.get("name", ""))
                            if existing_identity == identity and tier_priority[tier] > tier_priority.get(existing.get("_candidate_tier", ""), 0):
                                existing["_candidate_tier"] = tier
                                existing["_researched_name"] = matched_name
                        continue
                    normalized = self._normalize_attr_name(poi.get("name", ""))
                    if normalized in seen_names:
                        continue
                    poi["_candidate_tier"] = tier
                    poi["_researched_name"] = matched_name
                    poi["search_keyword"] = f"目的地研究:{tier}"
                    poi["_search_rank"] = len(results)
                    poi["is_food"] = False
                    seen_ids.add(identity)
                    seen_names.add(normalized)
                    results.append(poi)

        logger.info(f"目的地研究 POI 核验: {sum(len(v) for v in profile.values())}个名称 → {len(results)}个真实地点")
        return results

    @classmethod
    def _is_non_attraction_poi(cls, poi: dict) -> bool:
        """判断 POI 是否为景区附属设施或其他非景点。

        该门禁由动态研究、泛搜索和最终打分共用，避免某条候选链
        绕过过滤。不仅看高德分类，也使用名称兜底，因为附属设施的
        typecode 并不总是稳定。
        """
        name = str(poi.get("name", "") or "")
        poi_type = str(poi.get("type", "") or "")
        typecode = str(poi.get("typecode", "") or "")
        venue_name = cls._normalize_attr_name(name)
        # A restaurant or ordinary shop does not become a landmark because its
        # name/address contains 老门东/夫子庙. Preserve actual cultural shops and
        # food streets, rather than excluding every 05/06 POI indiscriminately.
        food_street = any(venue_name.endswith(word) for word in ("美食街", "小吃街", "夜市"))
        cultural_retail = any(word in venue_name for word in (
            "书店", "书局", "书屋", "博物馆", "美术馆", "展览馆", "陈列馆",
            "步行街", "历史文化街区", "文创园", "文创街区", "古玩市场",
        ))
        if (typecode.startswith("05") or poi_type.startswith("餐饮服务")) and not food_street:
            return True
        if (typecode.startswith("06") or poi_type.startswith("购物服务")) and not (cultural_retail or food_street):
            return True
        if any(keyword in name for keyword in cls._NON_ATTRACTION_NAME_KW):
            return True
        if typecode and typecode[:2] in (
            "01", "02", "03", "04", "07", "10", "15", "16", "20"
        ):
            return True
        facility_types = (
            "公共厕所", "公共设施", "生活服务", "道路附属设施",
            "停车场", "售票处", "服务中心",
            # 高德会把部分室内娱乐商家命名成近似知名景点的名称；例如
            # “穿越大唐不夜城”实际是楼内的普通体育休闲场所，而不是
            # 大雁塔附近的大唐不夜城景区。通用体育休闲场所不能仅凭
            # 名称进入景点池。
            "体育休闲服务场所",
        )
        return any(keyword in poi_type for keyword in facility_types)

    def _call_mcp_search(self, keywords: str, city: str) -> List[dict]:
        """
        通过高德 REST API 搜索 POI（结构化数据）。

        Returns:
            [{"name", "address", "longitude", "latitude", "type", "rating", ...}]
        """
        try:
            from urllib.parse import urlencode

            settings = get_settings()
            api_key = settings.amap_api_key

            params = urlencode({
                "key": api_key,
                "keywords": keywords,
                "city": city,
                "extensions": "all",
                "offset": "15",
                "output": "json",
            }).replace('%7C', '|')
            url = f"https://restapi.amap.com/v3/place/text?{params}"
            data = request_json(url)
            pois = data.get("pois", [])

            results = []
            for poi in pois:
                name = poi.get("name", "")
                if not name:
                    continue

                location_str = poi.get("location", "")
                lng, lat = 0.0, 0.0
                if "," in location_str:
                    parts = location_str.split(",")
                    try:
                        lng, lat = float(parts[0]), float(parts[1])
                    except ValueError:
                        pass

                biz_ext = poi.get("biz_ext", {})
                rating = 0.0
                if isinstance(biz_ext, dict):
                    try:
                        rating = float(biz_ext.get("rating", 0))
                    except (ValueError, TypeError):
                        pass

                photos = []
                raw_photos = poi.get("photos", [])
                if isinstance(raw_photos, list):
                    for ph in raw_photos:
                        if isinstance(ph, dict) and ph.get("url"):
                            photos.append(ph["url"])

                results.append({
                    "id": poi.get("id", ""),
                    "parent": poi.get("parent", ""),
                    "childtype": poi.get("childtype", ""),
                    "name": name,
                    "address": poi.get("address", ""),
                    "longitude": lng,
                    "latitude": lat,
                    "type": poi.get("type", ""),
                    "typecode": poi.get("typecode", ""),
                    "tag": poi.get("tag", ""),
                    "rating": rating,
                    "importance": poi.get("importance", ""),
                    "photos": photos,
                    "keyword_matched": keywords,
                })

            return results
        except Exception as e:
            logger.warning(f"Amap搜索失败 [{keywords}]: {e}")
            return []

    def _call_mcp_search_no_city(self, keywords: str) -> List[dict]:
        """
        高德文本搜索（无 city 约束）。

        与 _call_mcp_search 的区别：不传 city 参数，让高德在全国范围
        按关键词文本匹配。用于解决下辖县景点被 city 参数过滤的问题，
        如"咸阳 景点"能搜到礼泉县的袁家村、乾县的乾陵、兴平的茂陵。

        Returns:
            [{"name", "address", "longitude", "latitude", "type", "rating", ...}]
        """
        try:
            from urllib.parse import urlencode

            settings = get_settings()
            api_key = settings.amap_api_key

            params = urlencode({
                "key": api_key,
                "keywords": keywords,
                "extensions": "all",
                "offset": "15",
                "output": "json",
            }).replace('%7C', '|')
            url = f"https://restapi.amap.com/v3/place/text?{params}"
            logger.debug(f"县区兜底搜索: {keywords}")
            data = request_json(url)
            pois = data.get("pois", [])

            results = []
            for poi in pois:
                name = poi.get("name", "")
                if not name:
                    continue

                location_str = poi.get("location", "")
                lng, lat = 0.0, 0.0
                if "," in location_str:
                    parts = location_str.split(",")
                    try:
                        lng, lat = float(parts[0]), float(parts[1])
                    except ValueError:
                        pass

                biz_ext = poi.get("biz_ext", {})
                rating = 0.0
                if isinstance(biz_ext, dict):
                    try:
                        rating = float(biz_ext.get("rating", 0))
                    except (ValueError, TypeError):
                        pass

                photos = []
                raw_photos = poi.get("photos", [])
                if isinstance(raw_photos, list):
                    for ph in raw_photos:
                        if isinstance(ph, dict) and ph.get("url"):
                            photos.append(ph["url"])

                results.append({
                    "id": poi.get("id", ""),
                    "parent": poi.get("parent", ""),
                    "childtype": poi.get("childtype", ""),
                    "name": name,
                    "address": poi.get("address", ""),
                    "longitude": lng,
                    "latitude": lat,
                    "type": poi.get("type", ""),
                    "typecode": poi.get("typecode", ""),
                    "rating": rating,
                    "importance": poi.get("importance", ""),
                    "photos": photos,
                    "keyword_matched": keywords,
                })

            logger.debug(f"  县区兜底返回 {len(results)} 个结果")
            return results
        except Exception as e:
            logger.warning(f"县区兜底搜索失败 [{keywords}]: {e}")
            return []

    def _search_attractions_multi(self, request: TripRequest) -> dict:
        """多关键词搜索 + 合并去重 + 美食分离"""
        if self._is_route_mode(request):
            return self._search_route_attractions(request)

        keywords_list = request.preferences if request.preferences else ["热门景点", "必游景点"]
        city = request.city

        # 用户偏好 → 高德搜索词映射：部分偏好名称太宽泛（如"自然风光"返回所有公园），
        # 映射到更精准的关键词
        _PREFERENCE_KW_MAP = {
            "自然风光": "风景名胜",
            "休闲": "温泉|度假村",
        }
        preference_search_terms = {
            _PREFERENCE_KW_MAP.get(pref, pref) for pref in request.preferences
        }

        FALLBACK_KEYWORDS = ["热门景点", "必游景点", "风景名胜", "5A景区"]
        all_keywords = list(keywords_list)
        # 将宽泛的偏好词替换为精确关键词
        for i, kw in enumerate(all_keywords):
            if kw in _PREFERENCE_KW_MAP:
                all_keywords[i] = _PREFERENCE_KW_MAP[kw]
        for fbk in FALLBACK_KEYWORDS:
            if fbk not in all_keywords:
                all_keywords.append(fbk)

        # 先由目的地研究 Agent 提出“经典/偏好/特色”名称，再用高德核验。
        # 后面的泛关键词搜索只补漏，不再承担判断城市代表性的职责。
        all_pois: List[dict] = self._search_researched_attractions(request)
        seen_names: set = {
            self._normalize_attr_name(poi.get("name", ""))
            for poi in all_pois if poi.get("name")
        }
        food_keywords = {"美食", "小吃", "餐饮", "餐厅", "夜市", "火锅", "烧烤", "海鲜"}

        logger.info(f"多关键词搜索({len(all_keywords)}个): {all_keywords}")

        kw_new_counts: dict = {}
        for kw in all_keywords:
            pois = self._call_mcp_search(kw, city)
            before = len(seen_names)
            logger.debug(f"  '{kw}' → {len(pois)} 个结果")

            for idx, poi in enumerate(pois[:15]):
                name = poi["name"]
                normalized = self._normalize_attr_name(name)

                if normalized in seen_names:
                    continue

                # 排除明显不是景点的 POI：住宿类(10xxxx)、汽车服务(01-04)
                tc = poi.get("typecode", "")
                if tc and str(tc)[:2] in ("10", "01", "02", "03", "04", "07", "15", "16"):
                    logger.debug(f"  ↳ 排除非景点POI: {name} (typecode={tc})")
                    continue
                # 名称黑名单兜底：民宿、养发、美发等 — typecode 可能缺失或错乱
                if any(kw in name for kw in self._NON_ATTRACTION_NAME_KW):
                    logger.debug(f"  ↳ 排除非景点名称: {name}")
                    continue

                seen_names.add(normalized)

                # 名称中的餐饮关键词（夜市/小吃街等 Amap type 不含"餐饮"但实际是吃的）
                _food_name_kw = ["夜市", "小吃街", "美食街", "大排档", "美食城", "小吃城",
                                 "美食广场", "小吃广场", "烧烤街", "火锅街"]
                name_is_food = any(fk in poi.get("name", "") for fk in _food_name_kw)
                is_food = (
                    kw in food_keywords
                    or "餐饮" in poi.get("type", "")
                    or "美食" in poi.get("type", "")
                    or "餐厅" in poi.get("type", "")
                    or name_is_food
                )
                poi["is_food"] = is_food
                poi["search_keyword"] = kw
                if kw in preference_search_terms:
                    poi["_candidate_tier"] = "preference"
                else:
                    poi["_candidate_tier"] = "supplement"
                poi["_search_rank"] = idx  # 高德返回顺序 = 热度排名
                all_pois.append(poi)

            kw_new = len(seen_names) - before
            kw_new_counts[kw] = kw_new
            tag = " [兜底]" if kw in FALLBACK_KEYWORDS else ""
            logger.debug(f"    → 新增{kw_new}个{tag}")

        # 小吃街景点化
        FOOD_STREET_KEYWORDS = ["小吃街", "美食街", "美食街区"]
        HAS_FOOD_PREF = any(p in {"美食", "小吃", "夜市", "美食街"} for p in keywords_list)
        if HAS_FOOD_PREF:
            logger.info("美食偏好检测到，搜索小吃街...")
            for fkw in FOOD_STREET_KEYWORDS:
                street_pois = self._call_mcp_search(fkw, city)
                added = 0
                for idx, poi in enumerate(street_pois[:10]):
                    name = poi["name"]
                    if not self._is_branded_food_street(name, city):
                        continue
                    # 名称黑名单 — 民宿/养发等不应被当成"小吃街"
                    if any(kw in name for kw in self._NON_ATTRACTION_NAME_KW):
                        logger.debug(f"  ↳ 排除非景点名称(小吃街): {name}")
                        continue
                    normalized = re.sub(r'[（(].*?[)）]', '', name)
                    normalized = normalized.split('-')[0].split('—')[0].strip()
                    if normalized in seen_names:
                        continue
                    seen_names.add(normalized)
                    poi["is_food"] = True
                    poi["is_food_street"] = True
                    poi["_candidate_tier"] = "preference"
                    poi["search_keyword"] = fkw
                    poi["_search_rank"] = idx + 10  # 小吃街排名次要
                    all_pois.append(poi)
                    added += 1
                logger.debug(f"    '{fkw}' → 品牌小吃街 {added} 个")

        # ==== 远郊兜底搜索：城市中心 50km 半径扫风景名胜+文物古迹 ====
        # 文本搜索对小城市/远郊景点（如 乾陵距咸阳50km）覆盖不足，
        # 用 place/around 从城市中心大范围扫描补漏。
        # 始终运行 — 去重逻辑会处理大城市重复项，额外成本仅一次 API 调用
        center = self._get_city_center(city)
        wide_added = 0
        if center:
            lng, lat = center
            logger.info(f"远郊兜底: 从城市中心({lng},{lat}) 50km半径扫描...")
            wide_pois = self._call_amap_attraction_around(lng, lat, radius=50000)
            for idx, poi in enumerate(wide_pois):
                name = poi["name"]
                normalized = re.sub(r'[（(].*?[)）]', '', name)
                normalized = normalized.split('-')[0].split('—')[0].split('－')[0]
                normalized = normalized.strip()
                if normalized in seen_names:
                    continue
                tc = poi.get("typecode", "")
                if tc and str(tc)[:2] in ("10", "01", "02", "03", "04", "07", "15", "16"):
                    continue
                # 名称黑名单兜底：民宿、养发等 — typecode 可能缺失或错乱
                if any(kw in name for kw in self._NON_ATTRACTION_NAME_KW):
                    logger.debug(f"  ↳ 排除非景点名称(远郊): {name}")
                    continue
                seen_names.add(normalized)
                poi["is_food"] = False
                poi["_candidate_tier"] = "supplement"
                poi["search_keyword"] = "远郊兜底"
                poi["_search_rank"] = idx + 5  # around搜索结果排序不如text精准，给个基础偏移
                all_pois.append(poi)
                wide_added += 1
            if wide_added:
                logger.info(f"  远郊兜底新增 {wide_added} 个远郊景点")

        # ==== 县区兜底搜索：去掉 city 参数全文搜索"城市名 景点" ====
        # place/text 带 city 参数会限定搜索范围到市区，导致下辖县景点
        # 被过滤（如袁家村在礼泉县、乾陵在乾县、茂陵在兴平）。
        # 去掉 city，用城市名作为关键词全文搜索，覆盖全域下辖县。
        wide2_added = 0
        # 给城市名加"市"后缀，避免重名冲突（如"吉林市"vs吉林省、"朝阳"vs北京朝阳区）
        city_qualified = city if city.endswith("市") else f"{city}市"
        wide_text_keywords = f"{city_qualified} 景点"
        logger.info(f"县区兜底: 无city约束文本搜索 '{wide_text_keywords}' ...")
        wide_text_pois = self._call_mcp_search_no_city(wide_text_keywords)
        for idx, poi in enumerate(wide_text_pois[:15]):
            name = poi["name"]
            normalized = re.sub(r'[（(].*?[)）]', '', name)
            normalized = normalized.split('-')[0].split('—')[0].split('－')[0]
            normalized = normalized.strip()
            if normalized in seen_names:
                logger.debug(f"  ↳ 县区去重: {name}")
                continue
            tc = poi.get("typecode", "")
            if tc and str(tc)[:2] in ("10", "01", "02", "03", "04", "07", "15", "16"):
                logger.debug(f"  ↳ 排除非景点POI(县区): {name} (typecode={tc})")
                continue
            if any(kw in name for kw in self._NON_ATTRACTION_NAME_KW):
                logger.debug(f"  ↳ 排除非景点名称(县区): {name}")
                continue
            seen_names.add(normalized)
            poi["is_food"] = False
            poi["_candidate_tier"] = "supplement"
            poi["search_keyword"] = "县区兜底"
            poi["_search_rank"] = idx  # 高德返回顺序 = 热度排名
            all_pois.append(poi)
            logger.debug(f"  ↳ 县区新增: {name} (typecode={tc})")
            wide2_added += 1
        if wide2_added:
            logger.info(f"  县区兜底新增 {wide2_added} 个景点")
        else:
            logger.info(f"  县区兜底无新增（已都被其他搜索覆盖）")

        # 小吃街/夜市/美食街 → 景点（可在当天下午/傍晚游览）
        # 普通餐饮POI → food_pois（留给餐饮填充步骤）
        attractions = [p for p in all_pois
                       if not p["is_food"] or p.get("is_food_street")]
        food_pois = [p for p in all_pois
                     if p["is_food"] and not p.get("is_food_street")]

        min_needed = request.travel_days * 2
        success = len(attractions) >= min_needed

        logger.info(f"合并去重: {len(attractions)} 景点 + {len(food_pois)} 餐饮"
                    f" {'✓' if success else '⚠ 不足，将回退LLM'}")

        return {
            "attractions": attractions,
            "food_pois": food_pois,
            "success": success,
        }

    def _search_route_attractions(self, request: TripRequest) -> dict:
        """分别围绕每个路线节点搜索，避免把整个省区当成一个城市中心。"""
        all_attractions = []
        all_food_pois = []
        seen = set()
        route_stops = list(dict.fromkeys(self._build_route_nodes(request)))
        per_stop_days = max(1, request.travel_days // len(route_stops))

        for stop in route_stops:
            node_profile = self._route_node_profile(stop)
            is_scenic_node = node_profile["node_type"] == "scenic_area"
            search_city = (
                node_profile.get("search_city")
                or (node_profile.get("service_base") if is_scenic_node else stop)
                or stop
            )
            local_request = request.model_copy(update={
                "city": search_city,
                "trip_mode": "single_city",
                "destinations": [],
                "travel_days": per_stop_days,
            })
            result = self._search_attractions_multi(local_request)
            if is_scenic_node:
                core_pois: List[dict] = []
                queries = node_profile.get("core_queries") or (
                    f"{stop}风景区", stop
                )
                for query in queries:
                    for poi in self._call_mcp_search(query, search_city):
                        if (
                            self._matches_scenic_route_node(stop, poi.get("name", ""))
                            and not self._is_non_attraction_poi(poi)
                        ):
                            poi["_candidate_tier"] = "classic"
                            poi["_route_node_core"] = stop
                            poi["search_keyword"] = f"景区核心:{query}"
                            poi["_search_rank"] = 0
                            poi["is_food"] = False
                            core_pois.append(poi)
                core_location = next((
                    (
                        float(poi.get("longitude", 0) or 0),
                        float(poi.get("latitude", 0) or 0),
                    )
                    for poi in core_pois
                    if poi.get("longitude") and poi.get("latitude")
                ), None)
                if core_location:
                    radius = float(node_profile.get("support_radius_km", 20) or 20)
                    result["attractions"] = [
                        poi for poi in result.get("attractions", [])
                        if self._matches_scenic_route_node(stop, poi.get("name", ""))
                        or (
                            poi.get("longitude")
                            and poi.get("latitude")
                            and self._haversine_distance(
                                float(poi["longitude"]), float(poi["latitude"]),
                                core_location[0], core_location[1],
                            ) <= radius
                        )
                    ]
                result["attractions"] = [*core_pois, *result.get("attractions", [])]
            for key, target in (
                ("attractions", all_attractions),
                ("food_pois", all_food_pois),
            ):
                for poi in result.get(key, []):
                    identity = poi.get("id") or (
                        poi.get("name", ""), round(poi.get("longitude", 0), 3),
                        round(poi.get("latitude", 0), 3)
                    )
                    if identity in seen:
                        continue
                    seen.add(identity)
                    poi["route_stop"] = stop
                    if (
                        is_scenic_node
                        and self._matches_scenic_route_node(stop, poi.get("name", ""))
                    ):
                        poi["_route_node_core"] = stop
                    target.append(poi)

        success = len(all_attractions) >= max(4, len(route_stops))
        logger.info(
            f"跨城节点搜索完成: {len(route_stops)}个节点 → "
            f"{len(all_attractions)}个景点"
        )
        return {
            "attractions": all_attractions,
            "food_pois": all_food_pois,
            "success": success,
        }

    # ==================== 餐饮过滤 + 打分 ====================

    def _filter_food_pois(self, food_pois: List[dict], city: str) -> List[dict]:
        """过滤餐饮 POI：去连锁，优先本地特色"""
        if not food_pois:
            return []

        LOCAL_SIGNALS = [
            city, "老字号", "老店", "百年", "传统", "地道",
            "小吃", "本地", "特色", "正宗", "私房", "家宴",
            "土菜", "农家", "卤", "酱", "烤鸭", "烧腊", "熏",
            "巷", "胡同", "里", "弄",
        ]
        city_short = city[0] if len(city) <= 2 else ""

        scored = []
        for poi in food_pois:
            name = poi.get("name", "")
            addr = poi.get("address", "")

            is_chain = False
            for chain in self.FOOD_CHAIN_BLACKLIST:
                if chain in name:
                    is_chain = True
                    break
            if is_chain:
                continue

            score = 0
            combined = name + addr
            for signal in LOCAL_SIGNALS:
                if signal in combined:
                    score += 1
            if city_short and city_short in name:
                score += 2
            if "街" in name or "夜市" in name or "巷" in name:
                score += 1

            rating = poi.get("rating", 0)
            if isinstance(rating, (int, float)) and rating > 3.5:
                score += rating / 5.0

            poi["food_score"] = round(score, 1)
            scored.append(poi)

        scored.sort(key=lambda x: x.get("food_score", 0), reverse=True)

        seen_name = set()
        filtered = []
        for poi in scored:
            name = poi.get("name", "")
            normalized = re.sub(r'[（(].*?[)）]', '', name).strip()
            if normalized in seen_name:
                continue
            seen_name.add(normalized)
            filtered.append(poi)

        result = filtered[:12]
        logger.info(f"餐饮过滤: {len(food_pois)} → {len(scored)} (去连锁) → {len(result)} (top12)")
        return result

    # ==================== 小吃街品牌识别 ====================

    def _is_branded_food_street(self, name: str, city: str) -> bool:
        """判断小吃街是否具有地名品牌价值"""
        GENERIC_TERMS = {"美食广场", "购物中心", "商场", "地下", "超市", "大排档", "食堂",
                         "美食城", "小吃城", "美食汇", "小吃广场"}
        for t in GENERIC_TERMS:
            if t in name:
                return False

        BRAND_SIGNALS = ["历史文化街区", "步行街", "老街", "古街", "夜市"]
        for s in BRAND_SIGNALS:
            if s in name:
                return True

        if len(city) >= 1:
            city_short = city[0]
            if city_short in name and ("街" in name or "巷" in name):
                return True

        return False

    # ==================== 餐段分类 + 周边搜索 ====================

    BREAKFAST_KW = {
        "汤包", "锅贴", "馄饨", "豆浆", "油条", "煎饼", "小笼",
        "包子", "鸭血粉丝", "豆腐脑", "烧饼", "生煎", "粥", "面馆",
        "早茶", "粢饭", "麻团", "蒸饺", "煎包", "鸡蛋饼",
        "豆花", "糖芋苗", "赤豆元宵", "酒酿", "糕团",
        "牛肉锅贴", "蒸饭", "饭团", "大肉包", "灌汤包",
    }
    # 名称含这些关键词 → 排除出早餐候选（夜市摊、烧烤店不适合当早餐）
    NOT_BREAKFAST_KW = {
        "夜市", "夜宵", "烧烤", "大排档", "酒吧", "酒馆",
        "烤鱼", "烤羊", "龙虾", "火锅", "串串", "麻辣烫",
        "冒菜", "烤肉", "涮肉", "羊蝎子", "酸菜鱼",
    }
    LUNCH_KW = {
        "盖浇饭", "砂锅", "快餐", "简餐", "拌面", "拉面", "米线",
        "小吃", "锅贴", "鸭血粉丝", "汤包", "生煎", "凉皮",
        "麻辣烫", "冒菜", "黄焖鸡", "排骨米饭", "牛肉面", "炒饭",
        "螺蛳粉", "酸辣粉", "热干面", "炸酱面", "水饺", "馄饨",
        "铁板", "石锅", "煲仔饭", "卤肉饭", "烧腊",
    }
    DINNER_KW = {
        "私房菜", "烤鸭", "火锅", "烧烤", "酒楼", "大排档", "鱼",
        "龙虾", "土菜", "本帮菜", "农家", "海鲜", "烤肉", "涮肉",
        "羊蝎子", "酸菜鱼", "水煮鱼", "剁椒鱼头", "红烧", "清蒸",
        "干锅", "铁板", "石锅", "煲", "炖", "焖", "卤", "私房",
        "馆子", "酒家", "餐厅", "饭店", "食府", "家宴", "会馆",
    }
    NON_MEAL_KW = {
        "咖啡", "拿铁", "美式", "摩卡", "星巴克", "瑞幸",
        "奶茶", "茶饮", "甜品", "冰淇淋", "冰激凌", "蛋糕",
        "酒吧", "酒馆", "饮品", "果汁", "酸奶", "雪花冰", "糖水",
        "烘焙", "面包", "甜点", "冰室", "茶馆", "茶社", "茶室",
        "书局", "书店", "文具", "文具店", "服装", "鞋", "帽",
        "眼镜", "药店", "药房", "诊所", "口腔", "牙科",
        "理发", "美发", "美容", "美甲", "按摩", "足浴", "足疗",
        "KTV", "台球", "棋牌", "网吧", "网咖", "电玩",
        "手机", "数码", "电器", "维修", "洗车", "加油站",
        "中介", "房产", "地产", "售楼", "链家", "贝壳",
        "银行", "ATM", "快递", "物流", "邮政", "移动", "联通", "电信",
        "摄影", "照相", "冲印", "洗衣", "干洗",
        "花店", "鲜花", "宠物", "兽医", "黄金", "珠宝",
        "烟酒", "茶叶", "零食", "水果店", "便利店", "超市",
        "服装店", "鞋店", "饰品", "箱包", "皮具",
        # 母婴/儿童用品 — 不是餐饮
        "孩子王", "母婴", "孕婴", "婴儿", "童装", "玩具",
        "儿童乐园", "亲子乐园", "早教", "儿童摄影",
        # 其他常见非餐饮
        "健身", "瑜伽", "游泳", "酒店", "宾馆", "旅馆",
        "大酒店", "度假村", "温泉酒店",
        "驾校", "培训", "教育", "琴行",
        # 酒店品牌 — 酒店内的餐厅不适合当日常正餐推荐
        "希尔顿", "万豪", "香格里拉", "洲际", "凯悦", "威斯汀",
        "喜来登", "皇冠假日", "凯宾斯基", "悦榕庄", "文华东方",
        "丽思卡尔顿", "瑞吉", "四季酒店", "半岛酒店", "华尔道夫",
    }

    _FOOD_STREET_SIGNALS = {
        "夜市", "小吃街", "美食街", "大排档", "美食城", "小吃城",
        "美食广场", "小吃广场", "烧烤街", "火锅街",
    }

    def _classify_meal_type(self, name: str, poi_type: str = "", address: str = "") -> set:
        """根据餐厅名称和地址判断适合的餐段"""
        combined = name + poi_type + address

        for nk in self.NON_MEAL_KW:
            if nk in combined:
                return set()

        result = set()
        for kw in self.BREAKFAST_KW:
            if kw in combined:
                result.add("breakfast")
                break
        for kw in self.LUNCH_KW:
            if kw in combined:
                result.add("lunch")
                break
        for kw in self.DINNER_KW:
            if kw in combined:
                result.add("dinner")
                break

        # 如果名称含"夜市/烧烤/火锅"等 → 不允许作为早餐
        if "breakfast" in result:
            for nk in self.NOT_BREAKFAST_KW:
                if nk in combined:
                    result.discard("breakfast")
                    break

        if not result:
            result = {"lunch", "dinner"}
        return result

    def _call_amap_around_search(
        self, lng: float, lat: float, radius: int = 3000, keywords: str = ""
    ) -> List[dict]:
        """高德周边搜索：搜索指定坐标附近的餐饮 POI"""
        try:
            from urllib.parse import urlencode

            settings = get_settings()
            api_key = settings.amap_api_key

            query_params = {
                "key": api_key,
                "location": f"{lng},{lat}",
                "radius": str(radius),
                "types": "050000|060000",
                "offset": "25",
                "extensions": "all",
                "output": "json",
            }
            if keywords:
                query_params["keywords"] = keywords
            params = urlencode(query_params).replace('%7C', '|')
            url = f"https://restapi.amap.com/v3/place/around?{params}"
            data = request_json(url)
            pois = data.get("pois", [])

            results = []
            for poi in pois:
                name = poi.get("name", "")
                if not name:
                    continue

                location_str = poi.get("location", "")
                plng, plat = 0.0, 0.0
                if "," in location_str:
                    try:
                        parts = location_str.split(",")
                        plng, plat = float(parts[0]), float(parts[1])
                    except ValueError:
                        pass

                distance_m = int(poi.get("distance", 9999))

                biz_ext = poi.get("biz_ext", {})
                rating = 0.0
                cost = 0
                opening_hours = ""
                if isinstance(biz_ext, dict):
                    try:
                        rating = float(biz_ext.get("rating", 0))
                    except (ValueError, TypeError):
                        pass
                    try:
                        cost = int(float(biz_ext.get("cost", 0)))
                    except (ValueError, TypeError):
                        pass
                    raw_hours = biz_ext.get("opentime2", "") or biz_ext.get("opentime", "")
                    if isinstance(raw_hours, str):
                        opening_hours = raw_hours.strip()

                raw_addr = poi.get("address", "")
                if isinstance(raw_addr, list):
                    raw_addr = "".join(str(a) for a in raw_addr)
                elif not isinstance(raw_addr, str):
                    raw_addr = str(raw_addr)

                results.append({
                    "id": poi.get("id", ""),
                    "parent": poi.get("parent", ""),
                    "childtype": poi.get("childtype", ""),
                    "name": name,
                    "address": raw_addr,
                    "longitude": plng,
                    "latitude": plat,
                    "type": poi.get("type", ""),
                    "typecode": poi.get("typecode", ""),
                    "tag": poi.get("tag", ""),
                    "rating": rating,
                    "rating_available": rating > 0,
                    "estimated_cost": cost,
                    "opening_hours": opening_hours,
                    "distance_m": distance_m,
                })

            # 过滤：购物类(06)只放小吃街/夜市/美食街，其余排除
            # 餐饮(05)下的饮品/甜品/咖啡/糕饼子类 → 当正餐不合适，排除
            _NON_MEAL_TYPECODES = {"050500", "050600", "050700", "050800", "050900"}
            # 正餐子类：中餐、外国餐厅、快餐厅、休闲餐饮
            _MEAL_TYPECODE_PREFIXES = ("0501", "0502", "0503", "0504")
            filtered = []
            for r in results:
                tc = r.get("typecode", "")
                name = r.get("name", "")
                if any(marker in name + str(r.get("type", "")) for marker in NON_DINING_BUSINESS_MARKERS):
                    logger.debug(f"  ↳ 排除非堂食业态: {name} (typecode={tc})")
                    continue
                # 购物类(06xxxx) → 只放小吃街/夜市/美食街
                if tc.startswith("06"):
                    is_food_street = any(s in name for s in self._FOOD_STREET_SIGNALS)
                    if not is_food_street:
                        logger.debug(f"  ↳ 排除非餐饮POI: {name} (typecode={tc})")
                        continue
                # 饮品/甜品/咖啡/糕饼/茶室 → 排除
                if any(tc.startswith(code[:4]) for code in _NON_MEAL_TYPECODES):
                    logger.debug(f"  ↳ 排除饮品/甜品POI: {name} (typecode={tc})")
                    continue
                # typecode 为空 或 既不是05也不是06（可能是错误分类的POI）→ 排除
                if not tc or (not tc.startswith("05") and not tc.startswith("06")):
                    logger.debug(f"  ↳ 排除异常分类POI: {name} (typecode={tc or '空'})")
                    continue
                filtered.append(r)

            return filtered
        except Exception as e:
            logger.warning(f"周边搜索失败 [{lng},{lat}]: {e}")
            return []

    def _call_amap_attraction_around(self, lng: float, lat: float,
                                     radius: int = 50000) -> List[dict]:
        """高德周边搜索（大范围）：从城市中心搜远郊景点，补足文本搜索覆盖不到的景区。

        Amap place/around 最大半径 50000m，可覆盖如 咸阳→乾陵(~50km) 的远郊景点。
        用 keywords（文本匹配）而非 types（严格分类过滤），避免民俗村/古镇等
        非标准分类的景点被漏掉（如袁家村可能不被归为 110200 风景名胜）。
        """
        try:
            from urllib.parse import urlencode

            settings = get_settings()
            api_key = settings.amap_api_key

            params = urlencode({
                "key": api_key,
                "location": f"{lng},{lat}",
                "radius": str(radius),
                "keywords": "风景名胜|文物古迹|博物馆|陵墓|古镇|民俗村|寺庙|遗址|故居|古建筑",
                "offset": "25",
                "output": "json",
            }).replace('%7C', '|')
            url = f"https://restapi.amap.com/v3/place/around?{params}"
            data = request_json(url)
            pois = data.get("pois", [])

            results = []
            for poi in pois:
                name = poi.get("name", "")
                if not name:
                    continue
                location_str = poi.get("location", "")
                plng, plat = 0.0, 0.0
                if "," in location_str:
                    try:
                        plng, plat = float(location_str.split(",")[0]), float(location_str.split(",")[1])
                    except ValueError:
                        pass

                biz_ext = poi.get("biz_ext", {})
                rating = 0.0
                if isinstance(biz_ext, dict):
                    try:
                        rating = float(biz_ext.get("rating", 0))
                    except (ValueError, TypeError):
                        pass

                results.append({
                    "id": poi.get("id", ""),
                    "parent": poi.get("parent", ""),
                    "childtype": poi.get("childtype", ""),
                    "name": name,
                    "address": poi.get("address", ""),
                    "longitude": plng,
                    "latitude": plat,
                    "type": poi.get("type", ""),
                    "typecode": poi.get("typecode", ""),
                    "rating": rating,
                    "importance": poi.get("importance", ""),
                    "photos": [],
                })

            return results
        except Exception as e:
            logger.warning(f"远郊景点搜索失败 [{lng},{lat}]: {e}")
            return []

    def _score_nearby_meal(
        self, poi: dict, city: str = "", dishes: Optional[List[str]] = None
    ) -> float:
        """周边餐厅打分"""
        name = poi.get("name", "")
        addr = poi.get("address", "")
        if isinstance(addr, list):
            addr = "".join(str(a) for a in addr)
        elif not isinstance(addr, str):
            addr = str(addr)
        combined = name + addr
        distance_m = poi.get("distance_m", 9999)

        if distance_m <= 300:
            dist_score = 10.0
        elif distance_m <= 500:
            dist_score = 8.0
        elif distance_m <= 1000:
            dist_score = 6.0
        elif distance_m <= 1500:
            dist_score = 4.0
        else:
            dist_score = 2.0

        # 地址含城市/街巷、门店自称“正宗”，都不是地方菜品证据。
        LOCAL_SIGNALS = ["老字号", "百年", "本地", "土菜", "农家"]
        local_hits = sum(1 for s in LOCAL_SIGNALS if s in name + str(poi.get("tag", "")))
        if city and city in name:
            local_hits += 2
        local_score = min(local_hits * 2, 10.0)

        rating = poi.get("rating", 0)
        if isinstance(rating, (int, float)) and rating > 0:
            rating_score = min(rating / 5.0 * 10, 10.0)
        else:
            # 缺失评分不再伪装成中等分；保留候选，但排序时自然靠后。
            rating_score = 0.0

        dish_matches = self._match_dishes(poi, dishes or [])
        if dish_matches:
            dish_score = 10.0
        elif poi.get("keyword_grounded") and dishes:
            dish_score = 6.0
        else:
            dish_score = 0.0

        for chain in self.FOOD_CHAIN_BLACKLIST:
            if chain in name:
                return -99.0

        score = dist_score * 0.25 + local_score * 0.2 + rating_score * 0.35 + dish_score * 0.2
        if any(marker in name for marker in ("兰州", "沙县", "黄焖鸡", "擂饭", "金牌水饺", "新疆烧烤")) and not dish_matches:
            score -= 2.0
        if poi.get("_meal_open_verified"):
            score += 1.0
        return round(score, 1)

    # ==================== 景点区域归并 ====================

    @staticmethod
    def _haversine_distance(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
        """计算两点间的 Haversine 距离（km）；参数顺序固定为经度、纬度。"""
        if not all([lng1, lat1, lng2, lat2]):
            return float("inf")
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lng2 - lng1)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
             * math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    GENERIC_CLASSIFIERS: tuple = (
        "博物馆", "展览馆", "陈列馆", "科技馆", "纪念馆",
        "公园", "景区", "风景区", "名胜区", "广场",
        "遗址", "故居", "书院", "寺庙", "道观", "教堂",
        "步行街", "老街", "历史文化街区", "街区",
        "植物园", "动物园", "海洋馆",
    )

    @staticmethod
    def _strip_classifiers(s: str) -> str:
        """剥离名称末尾的通用分类词（只剥离最长匹配的一个）"""
        classifiers = MultiAgentTripPlanner.GENERIC_CLASSIFIERS
        classifiers_sorted = sorted(classifiers, key=len, reverse=True)
        for c in classifiers_sorted:
            if s.endswith(c) and len(s) > len(c):
                return s[:-len(c)]
        return s

    @staticmethod
    def _extract_prefix(name: str) -> str:
        """从 POI 名称提取父级前缀"""
        for sep in ["-", "—", "－", "·", "（", "("]:
            idx = name.find(sep)
            if idx > 0:
                return name[:idx].strip()
        return name

    @classmethod
    def _names_have_explicit_hierarchy(cls, name1: str, name2: str) -> bool:
        """判断名称是否明确表达“父景区-内部场馆”关系。"""
        norm1 = cls._normalize_attr_name(name1)
        norm2 = cls._normalize_attr_name(name2)
        if not norm1 or not norm2 or norm1 == norm2:
            return False
        for parent, child in ((norm1, norm2), (norm2, norm1)):
            for sep in ("-", "—", "－", "·"):
                if child.startswith(f"{parent}{sep}"):
                    return True
        return False

    @staticmethod
    def _poi_parent_id(poi: dict) -> str:
        """安全读取地图 POI 的父级 ID。"""
        value = poi.get("parent", "") or poi.get("parent_id", "")
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _prefixes_related(p1: str, p2: str) -> bool:
        """判断两个前缀是否关联"""
        if not p1 or not p2:
            return False
        if p1 == p2:
            return True
        c1 = MultiAgentTripPlanner._strip_classifiers(p1)
        c2 = MultiAgentTripPlanner._strip_classifiers(p2)
        if len(c1) < 2 or len(c2) < 2:
            return False
        if c1 == c2:
            return True
        if len(c1) >= 3 and c1 in c2:
            return True
        if len(c2) >= 3 and c2 in c1:
            return True
        # An arbitrary shared three-character substring (often the city name)
        # is not evidence that two independent venues are the same entity.
        return False

    def _group_attractions_by_area(
        self, pois: List[dict], distance_km: float = 1.5
    ) -> List[dict]:
        """景点实体归并：只合并名称/父景区相关项，邻近地点仍各自保留。

        distance_km 参数为兼容旧调用保留；距离仅在编排行程时使用。
        """
        if not pois:
            return []

        pois = [dict(poi) for poi in pois if not self._is_non_attraction_poi(poi)]
        by_id = {str(poi.get("id")): poi for poi in pois if poi.get("id")}
        umbrella_ids = {
            self._poi_parent_id(poi) for poi in pois
            if str(poi.get("childtype", "")).startswith("6")
            and self._poi_parent_id(poi) in by_id
        }
        tier_priority = {"supplement": 0, "local": 1, "preference": 2, "classic": 3}
        for child in pois:
            if not str(child.get("childtype", "")).startswith("6"):
                continue
            umbrella = by_id.get(self._poi_parent_id(child))
            if not umbrella:
                continue
            if tier_priority.get(umbrella.get("_candidate_tier"), 0) > tier_priority.get(child.get("_candidate_tier"), 0):
                child["_candidate_tier"] = umbrella["_candidate_tier"]
            child["score"] = max(child.get("score", 0) or 0, umbrella.get("score", 0) or 0)
            if umbrella.get("_is_core"):
                child["_is_core"] = True
                if not child.get("_researched_name"):
                    child["_researched_name"] = umbrella.get("_researched_name") or umbrella.get("name", "")
        # Remove ONLY the umbrella POI, before aliases can bridge it to a child.
        # The old whole-group deletion erased independent sibling attractions.
        if umbrella_ids:
            logger.debug(f"景区归并: 仅隐藏总景区POI，保留独立子景区: {sorted(umbrella_ids)}")
            pois = [poi for poi in pois if str(poi.get("id", "")) not in umbrella_ids]
        if not pois:
            return []

        n = len(pois)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            px, py = find(x), find(y)
            if px != py:
                merged_landmarks = (
                    root_landmarks.get(px, set()) | root_landmarks.get(py, set())
                )
                zone_x = root_zone.get(px, "")
                zone_y = root_zone.get(py, "")
                parent[px] = py
                root_independent[py] = root_independent.get(px, set()) | root_independent.get(py, set())
                if merged_landmarks:
                    root_landmarks[py] = merged_landmarks
                zone_order = {"市区": 0, "近郊": 1, "远郊": 2, "超远郊": 3}
                merged_zone = max(
                    (zone for zone in (zone_x, zone_y) if zone),
                    key=lambda zone: zone_order.get(zone, 0),
                    default="",
                )
                if merged_zone:
                    root_zone[py] = merged_zone

        # 每个 group 的动态经典地点集合（用于防止别名合并时把两个不同
        # 代表景点连锁合并到一起）。
        root_landmarks: dict = {}
        root_zone: dict = {}  # 每个 group 的主要距离分区
        root_independent: dict = {}
        for i, poi in enumerate(pois):
            if str(poi.get("childtype", "")).startswith("6") and poi.get("id"):
                root_independent[i] = {str(poi["id"])}
            lm = poi.get("_researched_name") if poi.get("_is_core") else None
            # 兼容旧测试/历史候选，不再由城市白名单生成此字段。
            lm = lm or poi.get("_landmark_match")
            if lm:
                root_landmarks[i] = {lm}
            zone = poi.get("_dist_zone", "")
            if zone:
                root_zone[i] = zone

        def _merge_landmarks(i: int, j: int) -> None:
            """合并 i 和 j 所在 root 的地标集合到新 root"""
            ri, rj = find(i), find(j)
            lms_i = root_landmarks.get(ri, set())
            lms_j = root_landmarks.get(rj, set())
            if lms_i or lms_j:
                root_landmarks[find(i)] = lms_i | lms_j
            # 合并 zone：远郊优先（取"更远"的 zone）
            zone_i = root_zone.get(ri, "")
            zone_j = root_zone.get(rj, "")
            merged_zone = zone_i
            if zone_j:
                zone_order = {"市区": 0, "近郊": 1, "远郊": 2, "超远郊": 3}
                if zone_order.get(zone_j, 0) > zone_order.get(zone_i, 0):
                    merged_zone = zone_j
            if merged_zone:
                root_zone[find(i)] = merged_zone

        # 地图返回的 parent 是最可靠的景区层级证据。只要父 POI 也在
        # 候选池中，就先把父项与内部场馆归入同一实体组。
        id_to_index = {
            str(poi.get("id", "")): index
            for index, poi in enumerate(pois)
            if poi.get("id")
        }
        for child_index, poi in enumerate(pois):
            parent_index = id_to_index.get(self._poi_parent_id(poi))
            if parent_index is None or parent_index == child_index:
                continue
            # 高德 childtype=6xx 表示可独立游览的下级景区。它与总景区
            # 互斥，但不能和兄弟景区合成一个候选（如趵突泉和大明湖）。
            if str(poi.get("childtype", "")).startswith("6"):
                continue
            child_stop = poi.get("route_stop")
            parent_stop = pois[parent_index].get("route_stop")
            if child_stop and parent_stop and child_stop != parent_stop:
                continue
            if find(child_index) != find(parent_index):
                union(child_index, parent_index)
                _merge_landmarks(child_index, parent_index)

        prefixes = [self._extract_prefix(p.get("name", "")) for p in pois]
        name_merges = 0
        for i in range(n):
            for j in range(i + 1, n):
                # childtype=6xx 是可独立售票/游览的下级景区。即便名称包含
                # 总景区名称，也不能在后续“名称相似”阶段重新并回父项，
                # 否则父项被隐藏时会连同这个真实景点一起丢掉。
                direct_independent_pair = (
                    (
                        self._poi_parent_id(pois[i]) == str(pois[j].get("id", ""))
                        and str(pois[i].get("childtype", "")).startswith("6")
                    )
                    or (
                        self._poi_parent_id(pois[j]) == str(pois[i].get("id", ""))
                        and str(pois[j].get("childtype", "")).startswith("6")
                    )
                )
                if direct_independent_pair:
                    continue
                stop_i = pois[i].get("route_stop")
                stop_j = pois[j].get("route_stop")
                if stop_i and stop_j and stop_i != stop_j:
                    continue
                if self._prefixes_related(prefixes[i], prefixes[j]):
                    if find(i) != find(j):
                        # 地标冲突检查：两个已有不同地标的 group 禁止名字合并
                        ri, rj = find(i), find(j)
                        if root_independent.get(ri) and root_independent.get(rj) and root_independent[ri] != root_independent[rj]:
                            continue  # Also block transitive alias bridges between siblings.
                        lms_i = root_landmarks.get(ri, set())
                        lms_j = root_landmarks.get(rj, set())
                        if lms_i and lms_j and lms_i != lms_j:
                            continue
                        # 跨区防护：有地标的远郊组禁止与市区/近郊组名字合并
                        # 防止"城"等常用字桥接把兵马俑拉进城区组
                        zone_i = root_zone.get(ri, "")
                        zone_j = root_zone.get(rj, "")
                        if (lms_i or lms_j) and zone_i and zone_j and zone_i != zone_j:
                            if ("远郊" in (zone_i, zone_j) or "超远郊" in (zone_i, zone_j)) and \
                               ("市区" in (zone_i, zone_j) or "近郊" in (zone_i, zone_j)):
                                continue  # 远郊地标不与城区名混
                        union(i, j)
                        name_merges += 1
                        _merge_landmarks(i, j)

        # 位置接近只用于后续把景点安排在同一天，不能用于景点去重。
        # 例如趵突泉、五龙潭和泉城广场彼此很近，但都是独立景点。
        # 因此这里仅保留名称/父景区关系合并，不再按 1.5km 距离合并。

        groups: dict = {}
        for i, poi in enumerate(pois):
            root = find(i)
            groups.setdefault(root, []).append(poi)

        result: List[dict] = []
        for root, members in groups.items():
            # 景区型路线节点的同名 POI 必须成为聚合组代表，不能被附近分数
            # 略高的山峰、观景台等替代（如纳木措被念青唐古拉山覆盖）。
            members.sort(
                key=lambda p: (
                    bool(p.get("_route_node_core")),
                    p.get("_candidate_tier") == "classic",
                    p.get("score", 0),
                ),
                reverse=True,
            )
            best = members[0]
            member_ids = {
                str(member.get("id", "")) for member in members if member.get("id")
            }
            has_parent_link = any(
                self._poi_parent_id(member) in member_ids
                for member in members
                if self._poi_parent_id(member)
            )
            has_explicit_name_link = any(
                self._names_have_explicit_hierarchy(
                    left.get("name", ""), right.get("name", "")
                )
                for index, left in enumerate(members)
                for right in members[index + 1:]
            )

            if len(members) == 1:
                area_name = best.get("name", "")
            else:
                member_prefixes = [self._extract_prefix(m.get("name", "")) for m in members]
                from collections import Counter
                prefix_counts = Counter(member_prefixes)
                top_prefix, top_count = prefix_counts.most_common(1)[0]
                if top_count >= len(members) * 0.5 and len(top_prefix) >= 2:
                    area_name = self._strip_classifiers(top_prefix)
                    if len(area_name) < 2:
                        area_name = top_prefix
                else:
                    area_name = self._strip_classifiers(
                        self._extract_prefix(best.get("name", ""))
                    )
                    if len(area_name) < 2:
                        area_name = best.get("name", "")

            result.append({
                "area_name": area_name,
                "display_name": best.get("name", ""),  # 原始全名，LLM 选用后展示用
                "score": best.get("score", 0),
                "category": best.get("broad_category", ""),
                "longitude": best.get("longitude", 0),
                "latitude": best.get("latitude", 0),
                "address": best.get("address", ""),
                "members": members,
                "is_group": len(members) > 1,
                "_dist_from_center": best.get("_dist_from_center"),
                "_dist_zone": best.get("_dist_zone", ""),
                "_is_remote": best.get("_is_remote", False),
                "_is_core": any(member.get("_is_core") for member in members),
                "_candidate_tier": best.get("_candidate_tier", "supplement"),
                "_researched_name": best.get("_researched_name", ""),
                "_landmark_match": best.get("_landmark_match"),
                "_strong_hierarchy": has_parent_link or has_explicit_name_link,
            })

        result.sort(key=lambda g: g["score"], reverse=True)

        group_count = sum(1 for g in result if g["is_group"])
        single_count = sum(1 for g in result if not g["is_group"])
        logger.info(f"景点归并: {len(pois)} POI → {len(result)} 组 (多景点组:{group_count}, 单景点:{single_count})")
        for g in result:
            if g["is_group"]:
                member_names = [m["name"] for m in g["members"]]
                logger.debug(f"  📦 {g['area_name']}: {', '.join(member_names)}")

        return result

    # ==================== 黑名单 + 配置 ====================

    FOOD_CHAIN_BLACKLIST: set = {
        "肯德基", "麦当劳", "必胜客", "汉堡王", "赛百味", "达美乐",
        "星巴克", "瑞幸", "Tims", "Costa", "Manner", "库迪",
        "海底捞", "呷哺呷哺", "小龙坎", "大龙燚", "蜀大侠", "谭鸭血",
        "巴奴", "捞王", "左庭右院", "德庄",
        "半天妖", "探鱼", "炉鱼",
        "汉拿山", "九田家", "韩宫宴",
        "萨莉亚", "绿茶餐厅", "外婆家", "西贝", "太二",
        "喜茶", "奈雪", "蜜雪冰城", "茶颜悦色",
        "茶百道", "茶话弄", "霸王茶姬", "书亦烧仙草", "一点点", "CoCo",
        "杨国福", "张亮麻辣烫", "袁记云饺",
        "沙县小吃", "黄焖鸡米饭", "杨铭宇",
        "真功夫", "永和大王", "永和豆浆",
        "吉野家", "食其家",
        "德克士", "华莱士", "正新鸡排",
        "全家", "罗森", "711", "7-Eleven",
        # 包子/早餐连锁
        "和善园", "青露", "巴比馒头", "甘其食", "早阳",
        # 螺蛳粉连锁
        "螺鼎记", "螺公堂", "爱民螺蛳粉", "王味螺", "螺状元",
        "螺满地", "好欢螺", "螺霸王", "卷味螺蛳粉",
        # 其他连锁快餐/简餐
        "大米先生", "乡村基", "老娘舅", "和府捞面", "遇见小面",
        "五谷渔粉", "三顾冒菜", "芙蓉冒菜",
        # 中式汉堡连锁
        "塔斯汀", "塔斯汀中国汉堡",
    }

    PREFERENCE_KEYWORDS: dict = {
        "历史文化": ["博物", "故宫", "古迹", "遗址", "古城", "寺庙", "道观", "故居",
                     "陵", "城墙", "皇", "宫", "殿", "坛", "祠", "胡同",
                     "老街", "古巷", "四合院", "纪念"],
        "自然风光": ["山", "湖", "海", "公园", "森林", "峡谷", "瀑布", "湿地",
                     "植物园", "风景", "岛", "泉", "洞", "峰", "岭"],
        "美食": ["小吃", "美食", "餐厅", "夜市", "火锅", "烤鸭", "涮肉", "炸酱面",
                 "街", "大栅栏", "簋街", "牛街", "护国寺"],
        "购物": ["商场", "购物", "步行街", "市场", "王府井", "西单", "三里屯", "国贸"],
        "亲子": ["乐园", "动物园", "海洋馆", "游乐", "科技馆", "天文馆", "水族馆"],
        "文艺": ["艺术", "美术馆", "画廊", "798", "创意", "文化宫", "剧院", "音乐厅"],
        "休闲": ["温泉", "度假", "spa", "茶馆", "咖啡馆"],
        "网红打卡": ["网红", "打卡", "拍照", "ins", "小红书"],
    }

    CITY_CENTERS: dict = {
        "南京": (118.784, 32.042),
        "北京": (116.397, 39.909),
        "上海": (121.474, 31.230),
        "杭州": (120.146, 30.247),
        "西安": (108.948, 34.261),
        "成都": (104.066, 30.658),
        "广州": (113.264, 23.129),
        "深圳": (114.058, 22.547),
        "苏州": (120.632, 31.299),
        "重庆": (106.577, 29.561),
        "武汉": (114.302, 30.547),
        "长沙": (112.977, 28.201),
        "咸阳": (108.71, 34.33),
        "济南": (117.120, 36.651),
        # 高频跨城节点保留稳定行政中心，避免同名酒店/商户污染整条路线。
        "昆明": (102.833, 24.881),
        "大理": (100.267, 25.606),
        "丽江": (100.233, 26.872),
        "香格里拉": (99.701, 27.830),
    }

    def _get_city_center(self, city: str) -> Tuple[float, float] | None:
        """获取城市中心坐标。先查白名单，未命中则用高德 API 动态查询并缓存。"""
        # Also cover lightweight __new__ instances used by offline unit tests.
        # Dynamic geocoding must never write back to the class-level seed map.
        centers = self.__dict__.setdefault("CITY_CENTERS", dict(type(self).CITY_CENTERS))
        city_lower = city.lower()
        # 1. 白名单
        for ck, coords in centers.items():
            if ck in city_lower or city_lower in ck:
                return coords
        # 2. 高德行政区接口兜底。不能直接取 place/text 第一条，
        # “香格里拉”等名称很容易命中外省同名酒店。
        try:
            coords = get_amap_service().geocode_administrative_location(city)
            if isinstance(coords, (tuple, list)) and len(coords) == 2:
                coords = (float(coords[0]), float(coords[1]))
                if not (math.isfinite(coords[0]) and math.isfinite(coords[1])
                        and -180 <= coords[0] <= 180 and -90 <= coords[1] <= 90):
                    return None
                centers[city] = coords
                logger.info(
                    f"  高德行政区: {city} 中心坐标 "
                    f"({coords[0]},{coords[1]})"
                )
                return coords
        except Exception as e:
            logger.warning(f"  城市中心兜底查询失败 ({city}): {e}")
        return None

    def _rank_and_group_candidates(
        self, pois: List[dict], preferences: List[str], travel_days: int, city: str = "",
    ) -> List[dict]:
        """Budget unique venue groups, not duplicate raw POI rows; no extra API calls."""
        ranked = self._score_and_filter(pois, preferences, travel_days, city, candidate_limit=len(pois))
        groups = self._group_attractions_by_area(ranked)
        groups.sort(key=lambda group: (
            bool(group.get("_is_remote") and group.get("_candidate_tier") not in {"classic", "preference"}),
            -float(group.get("score", 0) or 0),
        ))
        selected = groups[:max(12, travel_days * 5)]
        logger.info(f"独立景点候选预算: {len(pois)}条POI → {len(groups)}个实体组 → 保留{len(selected)}组 ({city})")
        return selected

    def _score_and_filter(
        self, pois: List[dict], preferences: List[str], travel_days: int, city: str = "",
        candidate_limit: Optional[int] = None,
    ) -> List[dict]:
        """多维度打分 + 类型多样性保证"""
        if not pois:
            return []

        raw_count = len(pois)
        unavailable_markers = (
            "不对外开放", "暂停营业", "停止营业", "永久关闭", "已关闭",
            "暂停开放", "暂不开放", "停止开放", "维修关闭",
        )
        pois = [
            poi for poi in pois
            if not self._is_non_attraction_poi(poi)
            and not any(
                marker in " ".join(str(poi.get(key, "") or "") for key in (
                    "name", "address", "type", "business_status", "opening_hours"
                ))
                for marker in unavailable_markers
            )
        ]
        if len(pois) != raw_count:
            logger.info(f"非景点设施过滤: {raw_count} → {len(pois)}")
        if not pois:
            return []

        feature_words: set = set()
        for pref in preferences:
            mapped = self.PREFERENCE_KEYWORDS.get(pref, [pref])
            feature_words.update(mapped)

        def _broad_category(poi_type: str, typecode: str = "") -> str:
            combined = f"{poi_type}|{typecode}"
            # 动植物园/海洋馆 — 在 typecode 大类判断之前，避免被归入"风景名胜"
            if any(w in combined for w in ["动物园", "植物园", "海洋馆", "水族馆",
                                              "野生动物园", "鸟园", "花园"]):
                return "动植物园"
            if typecode:
                tc_prefix = typecode[:2] if len(typecode) >= 2 else ""
                if tc_prefix == "14":
                    return "博物馆展览"
                if tc_prefix in ("11", "10"):
                    return "风景名胜"
                if tc_prefix == "12":
                    return "科教文化"
                if tc_prefix == "06":
                    return "商业街区"
                if tc_prefix == "05":
                    return "餐饮"
            if any(w in combined for w in ["博物", "展览", "陈列"]):
                return "博物馆展览"
            if any(w in combined for w in ["风景名胜", "景区", "国家级景点", "世界遗产"]):
                return "风景名胜"
            if any(w in combined for w in ["公园", "广场", "绿地"]):
                return "公园广场"
            if any(w in combined for w in ["寺庙", "道观", "宗教", "教堂", "清真寺"]):
                return "寺庙宗教"
            if any(w in combined for w in ["故居", "纪念馆"]):
                return "名人故居"
            if any(w in combined for w in ["商业", "步行街", "购物", "特色商业街"]):
                return "商业街区"
            if any(w in combined for w in ["湖", "山", "自然", "海滩", "岛屿"]):
                return "自然景观"
            if any(w in combined for w in ["大学", "校园", "学府", "学院"]):
                return "高校学府"
            if any(w in combined for w in ["剧院", "演出", "音乐厅", "影院"]):
                return "演出场所"
            return "其他"

        for poi in pois:
            score = 0.0
            name = poi.get("name", "")
            addr = poi.get("address", "")
            ptype = poi.get("type", "")
            combined = f"{name} {addr} {ptype}"

            matches = sum(1 for w in feature_words if w in combined)
            score += min(matches, 10) * 0.5

            tier = poi.get("_candidate_tier", "supplement")
            tier_bonus = {
                "classic": 5.0,
                "preference": 2.5,
                "local": 1.0,
                "supplement": 0.0,
            }.get(tier, 0.0)
            score += tier_bonus
            poi["_is_core"] = tier == "classic"

            # 品牌展馆、企业体验馆等不是一律删除：用户明确偏好时仍可作为
            # 小众补充；默认行程中则不能凭一个高评分挤掉目的地代表景点。
            niche_signals = (
                "面包博物馆", "企业博物馆", "品牌博物馆", "工业旅游",
                "企业展厅", "品牌展厅", "展示中心", "体验中心",
                "文化体验馆", "科普基地",
            )
            is_niche = any(signal in combined for signal in niche_signals)
            poi["_is_niche"] = is_niche
            if is_niche and tier != "classic":
                score -= 1.0 if matches else 3.5

            rating = poi.get("rating", 0)
            if rating > 0:
                score += min(rating / 5.0 * 3, 3.0)

            importance = poi.get("importance", "")
            if importance:
                try:
                    imp_val = float(importance)
                    if imp_val > 0:
                        score += min(2.0, imp_val / 5.0)
                except (ValueError, TypeError):
                    pass

            # 搜索排名加成：高德返回顺序 = 热度排名，越靠前越知名
            # rank 0 (第1条) → +3.0, rank 10 → +1.0, rank 14 → +0.2
            search_rank = poi.get("_search_rank")
            if search_rank is not None and search_rank < 25:
                rank_bonus = max(0, (15 - search_rank) / 15 * 3.0)
                score += rank_bonus

            if len(name) <= 2:
                score -= 1.0
            elif len(name) > 10:
                score -= 0.3

            dist_km = None
            center_coords = self._get_city_center(city)
            if center_coords:
                poi_lng = poi.get("longitude")
                poi_lat = poi.get("latitude")
                if poi_lng and poi_lat:
                    try:
                        dist_km = self._haversine_distance(
                            float(poi_lng), float(poi_lat),
                            center_coords[0], center_coords[1]
                        )
                        poi["_dist_from_center"] = round(dist_km, 1)
                    except (ValueError, TypeError):
                        pass

            if dist_km is not None:
                # 距离罚分 + 区位标记（>15km 标远郊全天，planner 强制单独一天）
                if dist_km <= 5:
                    poi["_dist_zone"] = "市区"
                elif dist_km <= 15:
                    score -= 0.5
                    poi["_dist_zone"] = "近郊"
                elif dist_km <= 30:
                    score -= 1.5
                    poi["_is_remote"] = True
                    poi["_dist_zone"] = "远郊"
                elif dist_km <= 60:
                    score -= 2.5
                    poi["_is_remote"] = True
                    poi["_dist_zone"] = "远郊"
                else:
                    score -= 3.5
                    poi["_is_remote"] = True
                    poi["_dist_zone"] = "超远郊"

            poi["score"] = round(score, 2)
            poi["broad_category"] = _broad_category(ptype, poi.get("typecode", ""))

        sorted_pois = sorted(pois, key=lambda p: p["score"], reverse=True)

        target_count = max(12, travel_days * 5) if candidate_limit is None else max(1, candidate_limit)
        selected: List[dict] = []

        def _take(pool: List[dict], limit: int) -> None:
            for poi in pool:
                if len([item for item in selected if item in pool]) >= limit:
                    break
                if poi not in selected:
                    selected.append(poi)

        # 先保证目的地代表性，再体现用户偏好，最后才由总分补足。
        # 不再为了“类型齐全”强塞每个类别的第一名。
        classic_pool = [p for p in sorted_pois if p.get("_candidate_tier") == "classic"]
        preference_pool = [p for p in sorted_pois if p.get("_candidate_tier") == "preference"]
        classic_target = min(len(classic_pool), max(2, min(6, travel_days + 1)))
        preference_target = min(len(preference_pool), max(3, travel_days * 2))
        _take(classic_pool, classic_target)
        _take(preference_pool, preference_target)
        # 普通远郊仍保留为候选，但只有市区/近郊候选不足时才补入；
        # 经典地标和明确命中用户偏好的远郊不受此限制。
        ordinary_remote = [
            poi for poi in sorted_pois
            if poi.get("_is_remote")
            and poi.get("_candidate_tier") not in {"classic", "preference"}
        ]
        city_first_pool = [poi for poi in sorted_pois if poi not in ordinary_remote]
        for poi in city_first_pool:
            if len(selected) >= target_count:
                break
            if poi not in selected:
                selected.append(poi)
        for poi in ordinary_remote:
            if len(selected) >= target_count:
                break
            if poi not in selected:
                selected.append(poi)

        classic_hits = [p for p in selected if p.get("_candidate_tier") == "classic"]
        logger.info(f"粗筛: {len(pois)} → {len(selected)} 候选 (目标{target_count})")
        if classic_hits:
            logger.info("动态经典候选: " + ", ".join(p["name"] for p in classic_hits))
        for i, p in enumerate(selected[:5]):
            tier_label = f" [{p.get('_candidate_tier', 'supplement')}]"
            logger.debug(f"  {i+1}. {p['name']}{tier_label} (得分:{p['score']:.1f}, 类:{p['broad_category']})")

        return selected

    # ==================== 时间约束辅助 ====================

    @staticmethod
    def _classify_arrival_time(time_str: str) -> str:
        """分类抵达时间: early (<12:00) / afternoon (12-15) / late (>15:00)"""
        if not time_str:
            return 'early'
        try:
            hour = int(time_str.split(':')[0])
        except (ValueError, IndexError):
            return 'early'
        if hour < 12:
            return 'early'
        elif hour < 15:
            return 'afternoon'
        else:
            return 'late'

    @staticmethod
    def _classify_departure_time(time_str: str) -> str:
        """分类离开时间: early (<13:00) / afternoon (13-17) / late (>17:00)"""
        if not time_str:
            return 'late'
        try:
            hour = int(time_str.split(':')[0])
        except (ValueError, IndexError):
            return 'late'
        if hour < 13:
            return 'early'
        elif hour < 17:
            return 'afternoon'
        else:
            return 'late'

    def _build_time_constraints_section(self, request: TripRequest) -> str:
        """构建时间感知约束文本，注入 LLM prompt。无时间信息时返回空字符串。"""
        parts = []
        travel_days = request.travel_days

        # ---- 抵达约束 ----
        has_arrival = (
            request.arrival_transport
            and request.arrival_transport != "已在此地"
            and request.arrival_time
        )
        if has_arrival:
            classification = self._classify_arrival_time(request.arrival_time)
            if classification == 'late':
                parts.append(
                    f"**Day 1（{request.start_date}）是抵达日，傍晚/晚上抵达：**\n"
                    f"  - 扣除出站、进城和入住时间后不足60分钟时，**不安排景点**\n"
                    f"  - 仍有时间时最多安排 **1个明确支持晚间游览** 的开放式街区、夜市或广场\n"
                    f"  - ⛔ 禁止安排博物馆、博物院、纪念馆、美术馆、科技馆等日间场馆\n"
                    f"  - ⛔ 禁止在Day 1安排[远郊·全天]景点"
                )
            elif classification == 'afternoon':
                parts.append(
                    f"**Day 1（{request.start_date}）是抵达日，下午抵达，仅有半天时间：**\n"
                    f"  - 安排 **1-2个** 景点\n"
                    f"  - 优先选择距市中心≤5km的景点（[市区]或[近郊]标签）\n"
                    f"  - ⛔ 禁止在Day 1安排[远郊·全天]景点"
                )
            else:  # early
                parts.append(
                    f"**Day 1（{request.start_date}）是抵达日，上午/中午抵达：**\n"
                    f"  - 按正常全天安排 2-3 个景点"
                )
        elif request.arrival_transport == "已在此地":
            parts.append(
                f"**Day 1（{request.start_date}）：** 您已在此地，按正常全天安排 2-3 个景点。"
            )

        # ---- 离开约束 ----
        if request.departure_time:
            classification = self._classify_departure_time(request.departure_time)
            last_day_label = (
                f"Day {travel_days}（{request.end_date}）是离开日"
                if travel_days > 1
                else "同一天也是离开日"
            )
            if classification == 'early':
                parts.append(
                    f"**{last_day_label}，中午前后离开，时间非常有限：**\n"
                    f"  - 当天 items 必须是空数组，**不安排任何固定景点**\n"
                    f"  - 只安排早餐、退房、取行李和前往机场/车站\n"
                    f"  - ⛔ 禁止安排乐园、动物园、大型景区及[远郊·全天]景点"
                )
            elif classification == 'afternoon':
                parts.append(
                    f"**{last_day_label}，下午离开，仅有上午时间：**\n"
                    f"  - 最多安排 **1个短时市区景点**\n"
                    f"  - 优先选择距市中心≤8km的景点\n"
                    f"  - ⛔ 禁止安排乐园、动物园、大型景区及[远郊·全天]景点"
                )
            else:  # late
                parts.append(
                    f"**{last_day_label}，傍晚/晚上离开：**\n"
                    f"  - 可按正常全天安排 2-3 个景点，但建议最后1个景点靠近市中心方便取行李"
                )

        if not parts:
            return ""

        header = "\n**时间感知约束（请严格遵循以下抵达/离开时间限制）:**\n"
        return header + "\n\n".join(parts)

    # ==================== Prompt 构建 ====================

    def _build_rich_planner_prompt(
        self,
        request: TripRequest,
        candidates: List[dict],
        weather: str,
        route_itinerary: Optional[List[dict]] = None,
    ) -> str:
        """构建紧凑景点编排提示词；完整 POI 字段由后端确定性组装。"""
        attr_lines = []
        food_street_lines = []
        schedule_profiles = []
        for i, g in enumerate(candidates):
            score = g.get("score", 0)
            cat = g.get("category", "")
            area_name = g["area_name"]
            display_name = g.get("display_name", area_name)
            members = g["members"]
            is_food_street = members[0].get("is_food_street", False) if members else False

            m = members[0]
            lng = g.get("longitude", 0) or 0
            lat = g.get("latitude", 0) or 0
            dist_km = g.get("_dist_from_center")
            dist_zone = m.get("_dist_zone", "")
            is_remote = m.get("_is_remote", False)
            route_stop = m.get("route_stop", "")
            tier = g.get("_candidate_tier") or m.get("_candidate_tier", "supplement")
            tier_label = {
                "classic": "经典必游",
                "preference": "偏好匹配",
                "local": "当地特色",
                "supplement": "补充候选",
            }.get(tier, "补充候选")
            # 距离标签：市区/近郊/远郊/超远郊，LLM一眼看出区位
            if dist_km is not None:
                dist_str = f" | [{dist_zone} {dist_km:.1f}km]"
            else:
                dist_str = ""
            tag = " [小吃街]" if is_food_street else ""
            tag += " [远郊·全天]" if is_remote else ""
            tag += f" [{tier_label}]"
            if m.get("_is_niche"):
                tag += " [小众专题]"
            if route_stop:
                tag += f" [路线节点:{route_stop}]"
            estimated_duration = self._estimate_visit_duration(
                display_name, cat, request.preferences, g
            )
            line = (
                f"  C{i+1} | {display_name}{tag} | "
                f"坐标({lng:.3f},{lat:.3f}){dist_str} | 类型:{cat} | "
                f"预计游览:{estimated_duration}分钟 | 推荐度:{score:.1f}"
            )
            attr_lines.append(line)
            schedule_profiles.append({
                "id": f"C{i + 1}",
                "name": display_name,
                "category": cat,
                "duration": estimated_duration,
                "lng": float(lng),
                "lat": float(lat),
            })
            if is_food_street:
                food_street_lines.append(f"  - {area_name}")
        candidates_text = "\n".join(attr_lines)

        incompatible_pairs = []
        for left_index, left in enumerate(schedule_profiles):
            for right in schedule_profiles[left_index + 1:]:
                distance = self._haversine_distance(
                    left["lng"], left["lat"], right["lng"], right["lat"]
                )
                if not math.isfinite(distance):
                    continue
                both_high_effort = (
                    self._is_high_effort_attraction(
                        left["name"], left["category"], left["duration"]
                    )
                    and self._is_high_effort_attraction(
                        right["name"], right["category"], right["duration"]
                    )
                )
                if distance > 18 or (both_high_effort and distance > 6):
                    incompatible_pairs.append((distance, left["id"], right["id"]))
        incompatible_pairs.sort(reverse=True)
        pair_hint = ""
        if incompatible_pairs:
            pair_lines = [
                f"  - {left_id} 与 {right_id}（直线约{distance:.1f}km）"
                for distance, left_id, right_id in incompatible_pairs[:24]
            ]
            pair_hint = (
                "\n**不可同日组合（由真实坐标、游览强度动态计算）:**\n"
                + "\n".join(pair_lines)
                + "\n以上任一组合必须拆到不同日期。"
            )

        food_street_hint = ""
        if food_street_lines:
            food_street_hint = (
                "\n**[小吃街] 小吃街（也是景点，可安排在当天下午/傍晚）:**\n"
                + "\n".join(food_street_lines)
                + "\n小吃街适合放在当天最后一个游览位置。"
            )

        remote_hint = ""
        remote_count = sum(1 for g in candidates if g.get("_is_remote"))
        if remote_count:
            remote_hint = (
                "\n**[远郊·全天] 标注[远郊·全天]的景点距市中心 >15km，"
                "往返 + 游玩至少需要大半天。安排当天时：\n"
                "  1. ⚠️ 该天最多只放 1 个远郊景点，该景点当天独占全天\n"
                "  2. 如果当天安排了远郊景点，不要再搭配任何其他景点（市区顺路的小景点也不行）\n"
                "  3. 远郊景点必须是当天 items 中唯一的候选编号\n"
                "  4. 优先安排在天气好的日期（雨天爬山/户外观赏体验差）\n"
                "  5. 经典必游远郊可保留；偏好匹配远郊在天数充足时安排；"
                "普通补充远郊只有在市区经典已经覆盖后才可安排**\n"
            )

        route_candidate_hint = ""
        if route_itinerary:
            daily_lines = []
            for position, item in enumerate(route_itinerary):
                day_index = item.get("day_index", position)
                expected_stop = (
                    item.get("activity_location")
                    or item.get("end_location")
                    or item.get("overnight_city")
                )
                if not expected_stop:
                    continue
                allowed_ids = [
                    f"C{candidate_index + 1}"
                    for candidate_index, candidate in enumerate(candidates)
                    if self._candidate_route_stop(candidate) == expected_stop
                ]
                target_note = (
                    "选1个核心候选"
                    if item.get("is_transit_day")
                    or item.get("node_type") == "scenic_area"
                    else "选1-2个候选"
                )
                allowed_text = ", ".join(allowed_ids) if allowed_ids else "无可靠候选"
                daily_lines.append(
                    f"- Day {day_index + 1}（{expected_stop}）仅可选择: "
                    f"{allowed_text}；{target_note}。"
                )
            if daily_lines:
                route_candidate_hint = (
                    "\n**每日候选编号白名单（不得跨日、跨节点选择）:**\n"
                    + "\n".join(daily_lines)
                )

        pace = self._pace_profile(request)
        pace_instruction = {
            "relaxed": "完整日以1-2个景点为宜；一个大型景区可独占一天，不得为凑数量补景点",
            "balanced": "完整日通常安排2个景点；只有短时且顺路时才可安排第3个",
            "compact": "完整日安排2-3个景点，但仍须满足开放时间、用餐和交通约束",
        }.get(request.travel_pace, "完整日通常安排2个景点")
        prompt = f"""请为{request.city}的{request.travel_days}天行程编排景点候选编号。

**基本信息:**
- 城市: {request.city}
- 日期: {request.start_date} 至 {request.end_date}
- 天数: {request.travel_days}天
- 交通方式: {request.transportation}
- 住宿: {request.accommodation}
- 偏好: {', '.join(request.preferences) if request.preferences else '无'}
- 游玩节奏: {pace['label']}（{pace_instruction}）

**真实景点候选（只能选择 C 编号）:**
{candidates_text}{pair_hint}{food_street_hint}{remote_hint}{route_candidate_hint}

**天气信息:**
{weather}

**编排要求:**
1. {pace_instruction}；长距离换城日最多1个；抵达或离开时间不足时可以少排
2. 先覆盖[经典必游]，再选择[偏好匹配]与[当地特色]；经典与偏好同时命中的地点优先
3. 默认首次到访，整段行程至少选择{max(1, min(request.travel_days, 3))}个[经典必游]（候选不足时除外）
4. [小众专题]只能作为补充，除非用户明确提出相关偏好，否则不得替代[经典必游]
5. 优先选择推荐度高且相互顺路的候选，同一天景点之间不宜超过20km
6. 每个 C 编号全程最多使用一次，严禁跨天重复
7. [远郊·全天]候选当天独占，不能搭配其他候选
8. 多城市行程必须严格匹配每天标注的路线节点，不得把其他城市候选放进当天
9. reason 只写选择和排序理由，不要补充输入中没有的历史、票价或开放时间事实

**只返回以下紧凑 JSON:**
{{"days":[{{"day_index":0,"items":[{{"id":"C1","reason":"适合上午游览"}}],"summary":"当天安排概述"}}],"overall_suggestions":"不超过60字的总体建议"}}
"""
        time_section = self._build_time_constraints_section(request)
        if time_section:
            prompt += time_section
        prompt += self._route_prompt_section(route_itinerary or [])
        if request.free_text_input:
            prompt += f"\n**额外要求:** {request.free_text_input}"

        return prompt

    @staticmethod
    def _compact_candidate_pick(candidate: dict, reason: str = "") -> dict:
        """把编排 Agent 选中的候选恢复为真实高德 POI 字段。"""
        members = candidate.get("members") or []
        primary = members[0] if members else candidate
        category = (
            candidate.get("broad_category")
            or candidate.get("category")
            or primary.get("broad_category")
            or primary.get("category")
            or "景点"
        )
        name = (
            candidate.get("display_name")
            or candidate.get("area_name")
            or primary.get("name")
            or "景点"
        )
        description = reason.strip() or f"{name}是本日安排的{category}类景点，建议预留约2小时游览。"
        raw_rating = primary.get("rating")
        try:
            rating = float(raw_rating) if raw_rating not in (None, "", [], {}) else None
        except (TypeError, ValueError):
            rating = None
        return {
            "name": name,
            "address": candidate.get("address") or primary.get("address", ""),
            "lng": float(candidate.get("longitude") or primary.get("longitude") or 0),
            "lat": float(candidate.get("latitude") or primary.get("latitude") or 0),
            "category": category,
            "description": description[:120],
            "rating": rating,
            "score": candidate.get("score", primary.get("score", 0)),
            "poi_id": primary.get("id", ""),
        }

    @staticmethod
    def _repair_json_structure(text: str) -> str:
        """修复模型 JSON 中字符串外多余或缺失的花/方括号。"""
        pairs = {"}": "{", "]": "["}
        closers = {"{": "}", "[": "]"}
        stack: List[str] = []
        repaired: List[str] = []
        in_string = False
        escaped = False

        for char in text:
            if in_string:
                repaired.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                repaired.append(char)
            elif char in closers:
                stack.append(char)
                repaired.append(char)
            elif char in pairs:
                if stack and stack[-1] == pairs[char]:
                    stack.pop()
                    repaired.append(char)
                else:
                    # 与栈顶不匹配的关闭符是模型多输出的括号。
                    continue
            else:
                repaired.append(char)

        while stack:
            repaired.append(closers[stack.pop()])
        return "".join(repaired)

    def _parse_compact_schedule(
        self,
        response: str,
        request: TripRequest,
        candidates: List[dict],
        route_itinerary: Optional[List[dict]] = None,
    ) -> TripPlan:
        """解析候选编号日程，并用后端持有的真实 POI 数据组装 TripPlan。"""
        try:
            text = response.strip()
            if "```json" in text:
                text = text.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in text:
                text = text.split("```", 1)[1].split("```", 1)[0].strip()
            elif "{" in text and "}" in text:
                text = text[text.find("{"):text.rfind("}") + 1]
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                repaired = self._repair_json_structure(text)
                if repaired == text:
                    raise
                data = json.loads(repaired)
                logger.warning("紧凑日程 JSON 括号结构已在本地修复，无需再次请求模型")
            raw_days = data.get("days", [])
            if not isinstance(raw_days, list):
                raise ValueError("days 必须是数组")
        except Exception as error:
            logger.warning(f"紧凑日程解析失败: {error}")
            raise TripPlanningError("紧凑规划结果格式无效") from error

        candidate_by_id = {f"C{index + 1}": candidate for index, candidate in enumerate(candidates)}
        raw_by_day = {
            item.get("day_index"): item
            for item in raw_days
            if isinstance(item, dict) and isinstance(item.get("day_index"), int)
        }
        used_ids: set[str] = set()
        days: List[DayPlan] = []
        selected_count = 0
        start = date.fromisoformat(request.start_date)

        for day_index in range(request.travel_days):
            raw_day = raw_by_day.get(day_index, {})
            raw_items = raw_day.get("items", raw_day.get("candidate_ids", []))
            if not isinstance(raw_items, list):
                raw_items = []
            expected_stop = None
            if route_itinerary and day_index < len(route_itinerary):
                expected_stop = (
                    route_itinerary[day_index].get("activity_location")
                    or route_itinerary[day_index].get("end_location")
                    or route_itinerary[day_index].get("overnight_city")
                )

            attractions: List[Attraction] = []
            for raw_item in raw_items:
                if isinstance(raw_item, dict):
                    candidate_id = str(raw_item.get("id", "")).upper().strip()
                    reason = str(raw_item.get("reason", "")).strip()
                else:
                    candidate_id = str(raw_item).upper().strip()
                    reason = ""
                if candidate_id.isdigit():
                    candidate_id = f"C{candidate_id}"
                candidate = candidate_by_id.get(candidate_id)
                if not candidate or candidate_id in used_ids:
                    continue
                members = candidate.get("members") or []
                candidate_stop = members[0].get("route_stop") if members else None
                if expected_stop and candidate_stop and candidate_stop != expected_stop:
                    logger.warning(
                        f"紧凑日程忽略跨节点候选: Day{day_index + 1} "
                        f"{candidate_id}({candidate_stop}) != {expected_stop}"
                    )
                    continue
                try:
                    pick = self._compact_candidate_pick(candidate, reason)
                    attraction = Attraction(
                        name=pick["name"],
                        address=pick["address"],
                        location=Location(longitude=pick["lng"], latitude=pick["lat"]),
                        visit_duration=120,
                        description=pick["description"],
                        category=pick["category"],
                        rating=pick["rating"],
                        poi_id=pick["poi_id"],
                        ticket_price=0,
                    )
                except (ValueError, TypeError, KeyError) as error:
                    # 单个地图候选损坏不应丢弃模型已成功编排的其他景点。
                    logger.warning(
                        f"紧凑日程跳过异常候选: Day{day_index + 1} "
                        f"{candidate_id}，原因={error}"
                    )
                    continue
                attractions.append(attraction)
                used_ids.add(candidate_id)
                selected_count += 1
                if candidate.get("_is_remote") or len(attractions) >= 3:
                    break

            names = "、".join(item.name for item in attractions)
            summary = str(raw_day.get("summary", "")).strip()
            if not summary:
                summary = f"第{day_index + 1}天：{names}" if names else f"第{day_index + 1}天行程待补充"
            days.append(DayPlan(
                date=(start + timedelta(days=day_index)).isoformat(),
                day_index=day_index,
                description=summary[:180],
                transportation=request.transportation,
                accommodation=request.accommodation,
                attractions=attractions,
                meals=[],
            ))

        if selected_count == 0:
            raise TripPlanningError("紧凑规划未选择任何有效景点候选")

        overall = str(data.get("overall_suggestions", "")).strip()
        if not overall:
            overall = "行程已按景点距离、天气和每日可用时间安排，出发前请确认开放时间与预约要求。"
        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=[],
            overall_suggestions=overall[:200],
            budget=Budget(),
        )

    def _build_deterministic_candidate_schedule(
        self,
        request: TripRequest,
        candidates: List[dict],
        route_itinerary: Optional[List[dict]] = None,
    ) -> TripPlan:
        """模型日程不可解析时，按真实候选和路线节点生成可继续加工的底稿。"""
        plan = TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=[],
            weather_info=[],
            overall_suggestions=(
                "已按真实景点候选、每日可用时间和路线节点完成基础排日，"
                "出发前请确认开放时间与预约要求。"
            ),
            budget=Budget(),
        )
        plan = self._ensure_day_count(
            plan, request, candidates, route_itinerary
        )
        selected_count = sum(len(day.attractions) for day in plan.days)
        if selected_count == 0:
            raise TripPlanningError("真实候选无法匹配到任何行程日期")
        logger.info(
            f"确定性排日完成: {len(plan.days)}天, {selected_count}个景点"
        )
        return plan

    def _build_meal_prompt(
        self,
        request: TripRequest,
        trip_plan: TripPlan,
        day_meals: List[dict],
    ) -> str:
        """构建餐饮填充提示词（第二阶段 LLM）"""
        day_blocks = []
        for dm in day_meals:
            day_num = dm["day"]
            attrs = dm["attractions"]
            attr_names = " → ".join(a["name"] for a in attrs)
            available_meal_types = set(dm.get("available_meal_types", ("breakfast", "lunch", "dinner")))

            block = f"### Day {day_num} ({attr_names})\n"
            if not available_meal_types:
                block += "当天处于抵达/离开交通时间，不安排餐饮；请返回 meals: []。\n"
                day_blocks.append(block)
                continue

            labels = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
            unavailable = [labels[item] for item in ("breakfast", "lunch", "dinner") if item not in available_meal_types]
            if unavailable:
                block += f"当天仅安排{'、'.join(labels[item] for item in available_meal_types)}；禁止输出{'、'.join(unavailable)}。\n"

            for meal_type in ["breakfast", "lunch", "dinner"]:
                if meal_type not in available_meal_types:
                    continue
                emoji = {"breakfast": "🌅", "lunch": "🌞", "dinner": "🌙"}
                meal_label = labels
                options = dm["meals"].get(meal_type, [])
                target_dish = dm.get("target_dishes", {}).get(meal_type, "")
                if target_dish:
                    repeat_note = "（菜品池已耗尽，允许间隔后重复）" if dm.get("dish_repeat_allowed", {}).get(meal_type) else ""
                    block += f"\n本餐目标菜品：{target_dish}{repeat_note}\n"

                if not options:
                    block += f"\n{emoji[meal_type]} **{meal_label[meal_type]}**: 无真实高德候选，返回时不要填写这一餐。\n"
                    continue

                block += f"\n{emoji[meal_type]} **{meal_label[meal_type]}** (周边搜索,按得分排序):\n"
                for j, m in enumerate(options[:8]):
                    dist_km = m.get("distance_m", 0) / 1000
                    addr = m.get("address", "")
                    score = m.get("meal_score", 0)
                    rating_text = f"{m['rating']:.1f}" if m.get("rating_available") else "暂无"
                    matched = m.get("dish_matches", [])
                    dish_text = "、".join(matched or m.get("dish_options", [])[:3])
                    block += (
                        f"  {j+1}. POI_ID={m.get('id','')} | {m['name']} | {addr} | "
                        f"距{m.get('anchor_name','行程点')}{dist_km:.1f}km | 高德评分{rating_text} | "
                        f"建议菜品:{dish_text} | 综合得分{score:.1f}\n"
                    )

            day_blocks.append(block)

        meals_text = "\n".join(day_blocks)

        day_summary_lines = []
        for dm in day_meals:
            day_num = dm["day"]
            attrs = dm["attractions"]
            attr_names = " → ".join(a["name"] for a in attrs)
            day_summary_lines.append(f"Day {day_num}: {attr_names}")
        day_summary = "\n".join(day_summary_lines)

        prompt = f"""请为以下{request.city}{request.travel_days}天旅行计划，从真实高德候选中选择适合的餐饮。

**行程概要:**
{day_summary}

**每天景点周边餐饮候选:**
{meals_text}

**填充规则:**
1. 🌅 早餐: **清淡为主**, 从候选中选择汤包/馄饨/豆浆/锅贴/鸭血粉丝/面馆等, **禁止**螺蛳粉/烤鱼/火锅/烧烤/咖啡
2. 🌞 午餐: 简餐/小吃/面馆/盖浇饭等, **必须是有主食的正餐**, **绝对禁止**咖啡馆/奶茶店/甜品店/面包店/酒吧
3. 🌙 晚餐: 可以丰盛些, 私房菜/烤鸭/火锅/烧烤/本地菜等, 从候选中选择, **禁止**咖啡馆/甜品店
4. **只能从上方真实候选中选择，poi_id、name 必须原样复制，绝对禁止编造或改写店名**
5. **优先选得分高且有真实评分的**, 但同一天三餐应选不同店；recommended_dish 必须使用当天目标菜品
6. **不同天不得重复同一个 POI_ID，也不得自行重复或替换目标菜品**；候选为空时省略该餐，不能用模型知识补一家店
7. 如果当天行程中有小吃街景点, 对应的午餐或晚餐可用小吃街替代, 餐厅名填"xx小吃街(逛吃)"
8. **禁止全国连锁**: 肯德基/麦当劳/海底捞/萨莉亚/星巴克/塔斯汀/华莱士/德克士等, **禁止任何咖啡品牌**(星巴克/瑞幸/M Stand/Costa/Tims/Manner等)
9. recommended_dish 必须从该餐列出的“优先菜品/建议菜品”中选择；这是建议菜品，不表示已核验餐厅实时菜单
10. 只填写每一天明确允许且存在真实候选的餐次；无可用餐次的日期必须返回空数组

**返回JSON格式:**
```json
{{
  "days": [
    {{
      "day_index": 0,
      "meals": [
        {{"type": "breakfast", "poi_id": "候选POI_ID", "name": "候选原始店名", "recommended_dish": "建议菜品", "estimated_cost": 20}},
        {{"type": "lunch", "poi_id": "候选POI_ID", "name": "候选原始店名", "recommended_dish": "建议菜品", "estimated_cost": 50}},
        {{"type": "dinner", "poi_id": "候选POI_ID", "name": "候选原始店名", "recommended_dish": "建议菜品", "estimated_cost": 80}}
      ]
    }}
  ]
}}
```

只返回JSON，不要其他文字。
"""
        return prompt

    def _build_planner_query(
        self,
        request: TripRequest,
        attractions: str,
        weather: str,
        route_itinerary: Optional[List[dict]] = None,
    ) -> str:
        """构建行程规划查询（Fallback 版本）"""
        query = f"""请根据以下信息生成{request.city}的{request.travel_days}天旅行计划:

**基本信息:**
- 城市: {request.city}
- 日期: {request.start_date} 至 {request.end_date}
- 天数: {request.travel_days}天
- 交通方式: {request.transportation}
- 住宿: {request.accommodation}
- 偏好: {', '.join(request.preferences) if request.preferences else '无'}

**景点信息:**
{attractions}

**天气信息:**
{weather}

**要求:**
1. 每天安排2-3个景点
2. ⛔ **严禁跨天重复！每个景点名称只能出现一次**
3. 每天必须包含早中晚三餐
4. **酒店由后续算法统一分配，hotel 字段填 null 即可**
5. 考虑景点之间的距离和交通方式
6. 返回完整的JSON格式数据
7. 景点的经纬度坐标要真实准确
"""
        if request.free_text_input:
            query += f"\n**额外要求:** {request.free_text_input}"
        query += self._route_prompt_section(route_itinerary or [])

        return query

    # ==================== 响应解析 + 后处理 ====================

    def _parse_response(self, response: str, request: TripRequest) -> TripPlan:
        """解析 LLM 响应为 TripPlan"""
        try:
            if "```json" in response:
                json_start = response.find("```json") + 7
                json_end = response.find("```", json_start)
                json_str = response[json_start:json_end].strip()
            elif "```" in response:
                json_start = response.find("```") + 3
                json_end = response.find("```", json_start)
                json_str = response[json_start:json_end].strip()
            elif "{" in response and "}" in response:
                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                json_str = response[json_start:json_end]
            else:
                raise ValueError("响应中未找到JSON数据")

            # 修正 LLM 常见拼写错误
            json_str = json_str.replace('"longongitude"', '"longitude"')
            json_str = json_str.replace('"longtitude"', '"longitude"')
            json_str = json_str.replace('"langitude"', '"longitude"')
            json_str = json_str.replace('"latitute"', '"latitude"')

            # 修正畸形 location: {"longitude": 118.7852, "32.0210": 32.021}
            # LLM 有时把纬度数值写成 key 名
            json_str = re.sub(
                r'"longitude"\s*:\s*([\d.]+)\s*,\s*"([\d.]+)"\s*:\s*([\d.]+)',
                r'"longitude": \1, "latitude": \2',
                json_str,
            )

            data = json.loads(json_str)
            trip_plan = TripPlan(**data)
            return trip_plan

        except Exception as e:
            logger.warning(f"解析响应失败: {e}")
            raise TripPlanningError("规划结果格式无效") from e

    @staticmethod
    def _normalize_attr_name(name: str) -> str:
        """归一化景点名：去括号、去空格"""
        n = re.sub(r'[（(].*?[)）]', '', name)
        return n.replace(' ', '').replace('　', '').strip()

    @classmethod
    def _scenic_entity_name(cls, name: str) -> str:
        """Conservative scenic aliases, not arbitrary shared-parent deduplication.

        明孝陵景区-明孝陵 and 明孝陵景区 describe one visit; 钟山景区-中山陵
        and 钟山景区-明孝陵 remain distinct independently visitable subareas.
        """
        normalized = cls._normalize_attr_name(name)

        def core(value: str) -> str:
            stripped = re.sub(r"(?:风景名胜区|风景区|景区|旅游区)$", "", value)
            return stripped if len(stripped) >= 2 else value

        parts = re.split(r"[-—－]", normalized)
        if len(parts) > 1:
            if all(core(part) == core(parts[0]) for part in parts[1:]):
                return core(parts[0])
            return normalized
        return core(normalized)

    @classmethod
    def _record_removed_attraction(
        cls,
        trip_plan: TripPlan,
        attraction: Attraction,
        day_index: int,
        reason: str,
    ) -> None:
        """保留系统调整项，供编辑页解释并一键恢复。"""
        key = attraction.poi_id or cls._normalize_attr_name(attraction.name)
        for item in trip_plan.removed_attractions:
            existing = item.attraction.poi_id or cls._normalize_attr_name(
                item.attraction.name
            )
            if existing == key:
                return
        trip_plan.removed_attractions.append(RemovedAttraction(
            attraction=attraction.model_copy(deep=True),
            original_day_index=max(0, day_index),
            reason=reason,
        ))

    @classmethod
    def _prune_readded_removed_attractions(cls, trip_plan: TripPlan) -> None:
        """重新排入正式行程的景点不能继续出现在“已删除推荐”中。"""
        active_ids = {
            attraction.poi_id
            for day in trip_plan.days
            for attraction in day.attractions
            if attraction.poi_id
        }
        active_names = {
            cls._normalize_attr_name(attraction.name)
            for day in trip_plan.days
            for attraction in day.attractions
        }
        before = len(trip_plan.removed_attractions)
        trip_plan.removed_attractions = [
            item for item in trip_plan.removed_attractions
            if not (
                (item.attraction.poi_id and item.attraction.poi_id in active_ids)
                or cls._normalize_attr_name(item.attraction.name) in active_names
            )
        ]
        if len(trip_plan.removed_attractions) != before:
            logger.info(
                "备选同步: 清理了 "
                f"{before - len(trip_plan.removed_attractions)} 个已重新排入的景点"
            )

    @classmethod
    def _sync_single_city_day_descriptions(cls, trip_plan: TripPlan) -> None:
        """按最终时间轴顺序刷新文案，避免景点列表顺序与页面展示相反。"""
        for day in trip_plan.days:
            ordered_names: List[str] = []
            for item in day.timeline:
                if item.kind != "attraction" or item.reference_index is None:
                    continue
                index = int(item.reference_index)
                if 0 <= index < len(day.attractions):
                    name = day.attractions[index].name
                    if name not in ordered_names:
                        ordered_names.append(name)
            for attraction in day.attractions:
                if attraction.name not in ordered_names:
                    ordered_names.append(attraction.name)
            day.description = (
                "本日依次安排" + "、".join(ordered_names)
                + "；三餐与交通按实际游览时段衔接。"
                if ordered_names
                else "本日以抵达、返程或酒店附近休整为主。"
            )

    def _enforce_pace_timeline_limits(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        candidates: Optional[List[dict]] = None,
    ) -> TripPlan:
        """在真实逐段交通生成后，按用户节奏裁掉低优先级过量项目。"""
        profile = self._pace_profile(request)
        tier_rank = {"supplement": 0, "local": 1, "preference": 2, "classic": 3}
        metadata_by_id: Dict[str, dict] = {}
        metadata_by_name: Dict[str, dict] = {}
        for group in candidates or []:
            group_meta = dict(group)
            for member in group.get("members") or [group]:
                merged = {**group_meta, **member}
                if member.get("id"):
                    metadata_by_id[str(member["id"])] = merged
                if member.get("name"):
                    metadata_by_name[self._normalize_attr_name(member["name"])] = merged

        def priority(attraction: Attraction) -> tuple:
            metadata = (
                metadata_by_id.get(str(attraction.poi_id or ""))
                or metadata_by_name.get(self._normalize_attr_name(attraction.name))
                or {}
            )
            return (
                tier_rank.get(metadata.get("_candidate_tier", "supplement"), 0),
                float(metadata.get("score", attraction.rating or 0) or 0),
                float(attraction.rating or 0),
                attraction.visit_duration,
            )

        def day_span(day: DayPlan) -> int:
            starts = [
                self._time_to_minutes(item.start_time)
                for item in day.timeline if item.start_time
            ]
            ends = [
                self._time_to_minutes(item.end_time or item.start_time)
                for item in day.timeline if item.start_time
            ]
            valid_starts = [value for value in starts if value is not None]
            valid_ends = [value for value in ends if value is not None]
            return (
                max(valid_ends) - min(valid_starts)
                if valid_starts and valid_ends else 0
            )

        def allowed_attractions(day: DayPlan, position: int, span: int) -> int:
            target = self._route_day_target_count(None, position, request)
            if day.route_segment or day.is_transit_day:
                # A short multi-city transfer is not automatically a whole day
                # of travel. Keep two already feasible stops rather than trim
                # one here and then report the same day as underfilled.
                if (
                    request.trip_mode == "multi_city"
                    and not day.is_transit_day
                    and day.route_segment is not None
                    and 0 < day.route_segment.duration_minutes < 4 * 60
                    and span <= profile["max_day_span"]
                    and self._timeline_hard_cost(day, request, position) == 0
                ):
                    return min(profile["max_attractions"], 2)
                return min(profile["max_attractions"], max(1, min(target, 1)))
            # “适中”不是机械地每天只能两个景点。两个主景点之外，若第三项
            # 本身不超过两小时、且含真实交通的全天跨度仍在上限内，就保留。
            if (
                request.travel_pace == "balanced"
                and len(day.attractions) >= 3
                and any(item.visit_duration <= 120 for item in day.attractions)
                and span <= profile["max_day_span"]
            ):
                target = max(target, 3)
            return min(profile["max_attractions"], max(1, target))

        def try_move_night_visit(source_position: int, attraction: Attraction) -> bool:
            """超量夜游点优先换到空闲日期；没有可执行日期时才删除。"""
            if not self._is_night_friendly_attraction(attraction):
                return False
            source = trip_plan.days[source_position]
            targets = sorted(
                (position for position in range(len(trip_plan.days)) if position != source_position),
                key=lambda position: len(trip_plan.days[position].attractions),
            )
            for target_position in targets:
                source = trip_plan.days[source_position]
                current_attraction = next((
                    item for item in source.attractions
                    if (item.poi_id and attraction.poi_id and item.poi_id == attraction.poi_id)
                    or self._normalize_attr_name(item.name) == self._normalize_attr_name(attraction.name)
                ), None)
                if current_attraction is None:
                    return False
                target = trip_plan.days[target_position]
                if target.route_segment or target.is_transit_day or any(a.user_locked for a in target.attractions):
                    continue
                if any(self._normalize_attr_name(a.name) == self._normalize_attr_name(attraction.name) for a in target.attractions):
                    continue
                source_backup = source.model_copy(deep=True)
                target_backup = target.model_copy(deep=True)
                rejected_backup = {
                    key: set(values)
                    for key, values in getattr(self, "_rejected_final_meal_ids", {}).items()
                }
                source.attractions.remove(current_attraction)
                target.attractions.append(current_attraction)
                self._build_daily_timelines(trip_plan, request, {source_position, target_position})
                source_span = day_span(source)
                target_span = day_span(target)
                valid = (
                    self._timeline_hard_cost(source, request, source_position) == 0
                    and self._timeline_hard_cost(target, request, target_position) == 0
                    and len(source.attractions) <= allowed_attractions(source, source_position, source_span)
                    and len(target.attractions) <= allowed_attractions(target, target_position, target_span)
                    and source_span <= profile["max_day_span"]
                    and target_span <= profile["max_day_span"]
                )
                if valid:
                    logger.info(
                        f"节奏换日 Day{source_position + 1}→Day{target_position + 1}: "
                        f"保留夜游项目 '{attraction.name}'"
                    )
                    return True
                trip_plan.days[source_position] = source_backup
                trip_plan.days[target_position] = target_backup
                self._rejected_final_meal_ids = rejected_backup
                source = trip_plan.days[source_position]
            return False

        for position, day in enumerate(trip_plan.days):
            self._build_daily_timelines(trip_plan, request, {position})
            while len(day.attractions) > 1:
                span = day_span(day)
                max_attractions = allowed_attractions(day, position, span)
                if (
                    len(day.attractions) <= max_attractions
                    and span <= profile["max_day_span"]
                ):
                    break
                removable = [
                    attraction for attraction in day.attractions
                    if not attraction.user_locked
                ]
                if not removable:
                    break
                removed = min(removable, key=priority)
                night_options = sorted(
                    (item for item in removable if self._is_night_friendly_attraction(item)),
                    key=priority,
                )
                moved = next(
                    (item for item in night_options if try_move_night_visit(position, item)),
                    None,
                )
                if moved is not None:
                    day = trip_plan.days[position]
                    continue
                # 失败的换日尝试会从深拷贝恢复日期对象，重新取得当前对象，
                # 避免继续修改已经脱离 trip_plan 的旧引用。
                day = trip_plan.days[position]
                removable = [
                    attraction for attraction in day.attractions
                    if not attraction.user_locked
                ]
                if not removable:
                    break
                removed = min(removable, key=priority)
                day.attractions.remove(removed)
                reason = (
                    f"不符合{profile['label']}的单日强度上限，已转为备选"
                )
                self._record_removed_attraction(
                    trip_plan, removed, day.day_index, reason
                )
                logger.info(
                    f"节奏修复 Day{position + 1}: 移出 '{removed.name}' | "
                    f"pace={request.travel_pace} span={span}分钟"
                )
                self._build_daily_timelines(trip_plan, request, {position})
        return trip_plan

    @classmethod
    def _is_dup_name(cls, name: str, seen: set) -> bool:
        """检查 name 是否与 seen 中任一名称重复（含模糊匹配）。

        精确匹配 + 双向包含（短名 ≥ 6 字时才算，避免"中山陵"误杀"中山陵音乐台"）。
        """
        if name in seen:
            return True
        if len(name) >= 6:
            for s in seen:
                if len(s) >= 6:
                    if name in s or s in name:
                        return True
        return False

    @staticmethod
    def _candidate_route_stop(candidate: dict) -> str:
        """返回候选组可靠的路线节点。"""
        route_stop = candidate.get("route_stop", "")
        if route_stop:
            return route_stop
        members = candidate.get("members") or []
        return members[0].get("route_stop", "") if members else ""

    @classmethod
    def _candidate_seen_aliases(cls, candidate: dict) -> set:
        """返回选中候选组后应一并标记为已使用的名称。

        display_name 与 area_name 永远代表同一个组级候选，必须原子使用；
        小组（不超过5个成员）通常是同一景区的多个名称/入口，成员名也一并
        标记。地图父级 ID 或明确“父景区-子场馆”名称证明为强层级关系时，
        不受成员数量限制；其他大型区域聚簇仍允许成员独立参与选择。
        """
        aliases = {
            cls._normalize_attr_name(candidate.get(key, ""))
            for key in ("display_name", "area_name")
            if candidate.get(key)
        }
        members = candidate.get("members") or []
        if len(members) <= 5 or candidate.get("_strong_hierarchy"):
            aliases.update(
                cls._normalize_attr_name(member.get("name", ""))
                for member in members
                if member.get("name")
            )
        return {alias for alias in aliases if alias}

    @classmethod
    def _route_day_candidates(
        cls,
        candidates: List[dict],
        route_itinerary: Optional[List[dict]],
        day_index: int,
    ) -> List[dict]:
        """跨城/环线补位时，只返回当天游览节点的候选。"""
        if not route_itinerary:
            return candidates
        route_item = next(
            (
                item for position, item in enumerate(route_itinerary)
                if item.get("day_index", position) == day_index
            ),
            None,
        )
        if route_item is None and day_index < len(route_itinerary):
            route_item = route_itinerary[day_index]
        if not route_item:
            return candidates
        expected_stop = (
            route_item.get("activity_location")
            or route_item.get("end_location")
            or route_item.get("overnight_city")
        )
        if not expected_stop:
            return candidates
        return [
            candidate for candidate in candidates
            if cls._candidate_route_stop(candidate) == expected_stop
        ]

    @classmethod
    def _route_day_target_count(
        cls,
        route_itinerary: Optional[List[dict]],
        day_index: int,
        request: Optional[TripRequest] = None,
    ) -> int:
        """赶路日和景区型节点安排1个核心景点，普通完整游览日安排2个。"""
        if request:
            if day_index == 0:
                ready = cls._arrival_ready_minutes(request)
                if ready is not None:
                    if 20 * 60 - ready < 60:
                        return 0
                    if ready >= 14 * 60:
                        return 1
            if day_index == request.travel_days - 1 and request.departure_time:
                departure = cls._time_to_minutes(request.departure_time)
                if departure is not None:
                    # 需要在离开前完成退房、取行李并前往机场/车站。
                    # 11:00 前离开时，从常规 9:00 开始计算已不足以安排
                    # 一个最短 60 分钟的景点，空出最后一天更符合实际。
                    if departure < 13 * 60:
                        return 0
                    if departure < 17 * 60:
                        return 1
        if not route_itinerary:
            return cls._pace_profile(request)["full_day_target"] if request else 2
        route_item = next(
            (
                item for position, item in enumerate(route_itinerary)
                if item.get("day_index", position) == day_index
            ),
            None,
        )
        if route_item and (
            route_item.get("is_transit_day")
            or route_item.get("node_type") == "scenic_area"
        ):
            return 1
        return cls._pace_profile(request)["full_day_target"] if request else 2

    @classmethod
    def _pick_next_attr(
        cls, candidates: List[dict], seen_normalized: set
    ) -> Optional[dict]:
        """从候选池中选下一个未使用的景点。先组级，组级耗尽后下沉到成员级。

        返回 dict with keys: name, address, lng, lat, category, score, poi_id
        返回 None 表示候选池彻底耗尽。
        """
        # ---- 第1轮：组级候选（area_name / display_name） ----
        best_group = None
        for cand in candidates:
            output_name = cand.get("display_name", "") or cand.get("area_name", "")
            if not output_name:
                continue
            output_norm = cls._normalize_attr_name(output_name)
            # 必须检查最终实际返回的名称，不能因为另一个未使用别名而再次
            # 返回已经用过的 display_name。
            if cls._is_dup_name(output_norm, seen_normalized):
                continue
            aliases = cls._candidate_seen_aliases(cand)
            if any(cls._is_dup_name(alias, seen_normalized) for alias in aliases):
                continue
            if best_group is None or cand.get("score", 0) > best_group.get("score", 0):
                best_group = cand
        if best_group is not None:
            name = best_group.get("display_name", "") or best_group.get("area_name", "")
            return {
                "name": name,
                "address": best_group.get("address", ""),
                "lng": float(best_group.get("longitude", 0) or 0),
                "lat": float(best_group.get("latitude", 0) or 0),
                "category": best_group.get("broad_category", best_group.get("category", "景点")),
                "score": best_group.get("score", 0),
                "poi_id": best_group.get("id", ""),
                "_seen_aliases": cls._candidate_seen_aliases(best_group),
                "_is_remote": bool(
                    best_group.get("_is_remote")
                    or any(member.get("_is_remote") for member in best_group.get("members", []))
                ),
            }

        # ---- 第2轮：成员级候选（从多 POI 组的未使用成员中选） ----
        best_member = None
        for cand in candidates:
            members = cand.get("members", [])
            if len(members) <= 1:
                continue
            for m in members:
                m_name = m.get("name", "")
                if not m_name:
                    continue
                m_norm = cls._normalize_attr_name(m_name)
                if cls._is_dup_name(m_norm, seen_normalized):
                    continue
                m_score = m.get("score", 0)
                if best_member is None or m_score > best_member.get("score", 0):
                    best_member = {
                        "name": m_name,
                        "address": m.get("address", ""),
                        "lng": float(m.get("longitude", 0) or 0),
                        "lat": float(m.get("latitude", 0) or 0),
                        "category": m.get("broad_category", m.get("category", "景点")),
                        "score": m_score,
                        "poi_id": m.get("id", ""),
                        "_seen_aliases": {m_norm},
                        "_is_remote": bool(m.get("_is_remote") or cand.get("_is_remote")),
                    }
        return best_member

    @staticmethod
    def _make_attraction(pick: dict) -> Attraction:
        """从 _pick_next_attr 返回的 dict 创建 Attraction 对象"""
        return Attraction(
            name=pick["name"],
            address=pick.get("address", ""),
            location=Location(longitude=pick["lng"], latitude=pick["lat"]),
            visit_duration=120,
            description=pick.get("category", "") or "景点",
            category=pick.get("category", "景点"),
            rating=None,
            poi_id=pick.get("poi_id", ""),
            ticket_price=0,
        )

    def _ensure_classic_coverage(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        candidates: Optional[List[dict]] = None,
    ) -> TripPlan:
        """保证首次到访行程不会被补充景点挤掉主要经典景点。"""
        if self._is_route_mode(request) or not candidates:
            return trip_plan
        classics = sorted(
            [
                candidate for candidate in candidates
                if candidate.get("_candidate_tier") == "classic"
            ],
            key=lambda candidate: float(candidate.get("score", 0) or 0),
            reverse=True,
        )
        required = min(len(classics), max(2, min(4, request.travel_days)))
        if required == 0:
            return trip_plan

        selected_names = {
            self._normalize_attr_name(attr.name)
            for day in trip_plan.days for attr in day.attractions
        }

        def candidate_is_selected(candidate: dict) -> bool:
            return any(
                self._normalize_attr_name(alias) in selected_names
                for alias in (
                    candidate.get("display_name", ""),
                    candidate.get("area_name", ""),
                    *(member.get("name", "") for member in candidate.get("members", [])),
                )
                if alias
            )

        selected_classic_count = sum(candidate_is_selected(item) for item in classics)
        missing = [item for item in classics if not candidate_is_selected(item)]
        affected: set[int] = set()

        # 记录现有景点的候选层级，必要替换时优先移除补充/当地特色，而不是经典。
        name_to_tier: Dict[str, str] = {}
        for candidate in candidates:
            tier = candidate.get("_candidate_tier", "supplement")
            for alias in self._candidate_seen_aliases(candidate):
                name_to_tier.setdefault(alias, tier)
        tier_rank = {"classic": 3, "preference": 2, "local": 1, "supplement": 0}

        for candidate in missing:
            if selected_classic_count >= required:
                break
            pick = self._compact_candidate_pick(candidate)
            attraction = Attraction(
                name=pick["name"], address=pick["address"],
                location=Location(longitude=pick["lng"], latitude=pick["lat"]),
                visit_duration=120, description=pick["description"],
                category=pick["category"], rating=pick["rating"],
                poi_id=pick["poi_id"], ticket_price=0,
            )
            placed = False
            is_remote = bool(candidate.get("_is_remote"))
            day_positions = sorted(
                range(len(trip_plan.days)),
                key=lambda position: len(trip_plan.days[position].attractions),
            )
            for position in day_positions:
                day = trip_plan.days[position]
                target_count = self._route_day_target_count(None, position, request)
                if len(day.attractions) >= target_count:
                    continue
                if is_remote and day.attractions:
                    continue
                day.attractions.append(attraction)
                affected.add(position)
                placed = True
                break

            if not placed:
                replaceable = []
                for position, day in enumerate(trip_plan.days):
                    for attr_index, current in enumerate(day.attractions):
                        current_tier = name_to_tier.get(
                            self._normalize_attr_name(current.name), "supplement"
                        )
                        if current_tier == "classic":
                            continue
                        replaceable.append((
                            tier_rank.get(current_tier, 0), position, attr_index, current
                        ))
                if replaceable:
                    _, position, attr_index, removed = min(replaceable, key=lambda item: item[0])
                    trip_plan.days[position].attractions[attr_index] = attraction
                    self._record_removed_attraction(
                        trip_plan, removed, trip_plan.days[position].day_index,
                        f"优先补入城市经典景点“{attraction.name}”",
                    )
                    selected_names.discard(self._normalize_attr_name(removed.name))
                    affected.add(position)
                    placed = True
                    logger.info(
                        f"经典覆盖: 用 '{attraction.name}' 替换 '{removed.name}'"
                    )

            if placed:
                selected_names.add(self._normalize_attr_name(attraction.name))
                selected_classic_count += 1
                logger.info(f"经典覆盖: 补入 '{attraction.name}'")

        for position in affected:
            day = trip_plan.days[position]
            names = "、".join(attr.name for attr in day.attractions)
            day.description = f"本日安排{names}，优先覆盖目的地经典景点。"
        return trip_plan

    def _dedup_attractions(
        self,
        trip_plan: TripPlan,
        candidates: List[dict] = None,
        route_itinerary: Optional[List[dict]] = None,
        request: Optional[TripRequest] = None,
    ) -> TripPlan:
        """
        硬去重 + 补位：移除跨天重复的景点，不足时从落选候选中补位。
        LLM 有时不遵守"严禁跨天重复"的指令，这里做兜底。
        同名或名称高度相似的景点只保留第一次出现。
        """
        seen_normalized: set = set()
        removed = 0
        affected_day_positions: set[int] = set()

        # 小团或地图已确认的强父子组做原子扩展；普通大型区域聚簇中的
        # 成员仍是独立景点，不应互斥。
        atomic_group_name_sets: List[set] = []
        for g in (candidates or []):
            names = self._candidate_seen_aliases(g)
            if len(g.get("members", [])) <= 5 or g.get("_strong_hierarchy"):
                atomic_group_name_sets.append(names)

        def _expand_seen(name: str) -> None:
            """将 name 所在小候选组的所有名称加入 seen_normalized"""
            norm = self._normalize_attr_name(name)
            seen_normalized.add(norm)
            for gs in atomic_group_name_sets:
                if norm in gs:
                    for n in gs:
                        seen_normalized.add(n)
                    break

        for day_position, day in enumerate(trip_plan.days):
            unique_attrs = []
            for attr in day.attractions:
                normalized = self._normalize_attr_name(attr.name)
                if self._is_dup_name(normalized, seen_normalized):
                    logger.warning(f"去重: 移除重复景点 '{attr.name}' (Day{day.day_index+1})")
                    removed += 1
                    affected_day_positions.add(day_position)
                    continue
                _expand_seen(attr.name)
                unique_attrs.append(attr)
            day.attractions = unique_attrs
        if removed > 0:
            logger.info(f"景点去重: 移除了 {removed} 个跨天重复项")

        # ---- 补位：去重后某天不足 MIN_PER_DAY 个景点，从落选候选中补 ----
        if candidates:
            # 预计算：哪些 attraction name 是远郊地标（全天独占）
            remote_landmark_names: set = set()
            for g in candidates:
                is_remote = g.get("_is_remote", False)
                is_core = bool(g.get("_is_core") or g.get("_landmark_match"))
                if is_remote and is_core:
                    for m in g.get("members", []):
                        remote_landmark_names.add(self._normalize_attr_name(m.get("name", "")))
                    remote_landmark_names.add(self._normalize_attr_name(g.get("area_name", "")))
                    remote_landmark_names.add(self._normalize_attr_name(g.get("display_name", "")))

            def _day_has_remote_landmark(day) -> bool:
                """检查当天是否已有远郊地标——如果是，当天应独占，不再补位"""
                for attr in day.attractions:
                    if self._normalize_attr_name(attr.name) in remote_landmark_names:
                        return True
                return False

            backfilled = 0
            for day in trip_plan.days:
                # LLM 已经为当天选出至少一个合法景点时，尊重它对天气、返程
                # 和节奏的判断。补位只负责修复“整天空白”，不能为了凑数把
                # 牛首山等远郊全天景点塞进已有安排的日期。
                if day.attractions:
                    continue
                if _day_has_remote_landmark(day):
                    logger.debug(
                        f"补位跳过: Day{day.day_index+1} 已有远郊地标（{day.attractions[0].name}），"
                        f"作为独立全天节点不补位"
                    )
                    continue
                day_candidates = self._route_day_candidates(
                    candidates, route_itinerary, day.day_index
                )
                target_count = self._route_day_target_count(
                    route_itinerary, day.day_index, request
                )
                if route_itinerary and not day_candidates:
                    logger.warning(
                        f"补位跳过: Day{day.day_index+1} 的路线节点没有可靠景点候选"
                    )
                    continue
                while len(day.attractions) < target_count:
                    pick = self._pick_next_attr(day_candidates, seen_normalized)
                    if pick is None:
                        break  # 候选池彻底耗尽（组级+成员级都用完）
                    seen_normalized.update(
                        pick.get("_seen_aliases")
                        or {self._normalize_attr_name(pick["name"])}
                    )
                    new_attr = self._make_attraction(pick)
                    day.attractions.append(new_attr)
                    affected_day_positions.add(day.day_index)
                    backfilled += 1
                    logger.info(
                        f"补位: Day{day.day_index+1} 新增 '{pick['name']}' "
                        f"(score={pick['score']:.1f})"
                    )
                    if pick.get("_is_remote"):
                        break
            if backfilled > 0:
                logger.info(f"景点补位: 共补入 {backfilled} 个景点")
        for position in affected_day_positions:
            if position < 0 or position >= len(trip_plan.days):
                continue
            day = trip_plan.days[position]
            names = "、".join(attr.name for attr in day.attractions)
            day.description = (
                f"本日安排{names}，已根据景点去重与行程可行性更新。"
                if names else "本日以交通衔接和休整为主，未安排固定景点。"
            )
        return trip_plan

    def _remove_duplicate_attractions(
        self, trip_plan: TripPlan, candidates: Optional[List[dict]] = None
    ) -> TripPlan:
        """最终输出保护：按 POI、规范化名称和小候选组跨天去重。"""
        alias_to_group: Dict[str, str] = {}
        for index, candidate in enumerate(candidates or []):
            group_key = f"group:{index}"
            for alias in self._candidate_seen_aliases(candidate):
                alias_to_group.setdefault(alias, group_key)

        seen_identities: set = set()
        removed = 0
        affected_day_positions: set[int] = set()
        for day_position, day in enumerate(trip_plan.days):
            unique = []
            for attraction in day.attractions:
                if self._is_non_attraction_poi({
                    "name": attraction.name,
                    "type": attraction.category or "",
                }):
                    logger.warning(
                        f"最终景点门禁: 移除非景点设施 '{attraction.name}'"
                    )
                    removed += 1
                    continue
                normalized = self._normalize_attr_name(attraction.name)
                identities = {f"name:{normalized}"}
                identities.add(f"scenic:{self._scenic_entity_name(attraction.name)}")
                if attraction.poi_id:
                    identities.add(f"poi:{attraction.poi_id}")
                group_key = alias_to_group.get(normalized)
                if group_key:
                    identities.add(group_key)
                if identities & seen_identities:
                    logger.warning(
                        f"最终去重: 移除 Day{day.day_index+1} 重复景点 '{attraction.name}'"
                    )
                    removed += 1
                    affected_day_positions.add(day_position)
                    continue
                seen_identities.update(identities)
                unique.append(attraction)
            day.attractions = unique
        if removed:
            logger.info(f"最终景点去重: 共移除 {removed} 个重复项")
        for position in affected_day_positions:
            day = trip_plan.days[position]
            names = "、".join(attr.name for attr in day.attractions)
            day.description = (
                f"本日安排{names}，已根据景区层级与跨日去重更新。"
                if names else "本日以交通衔接和休整为主，未安排固定景点。"
            )
        return trip_plan

    @classmethod
    def _estimate_visit_duration(
        cls,
        name: str,
        category: str = "",
        preferences: Optional[List[str]] = None,
        metadata: Optional[dict] = None,
        scenic_core: bool = False,
    ) -> int:
        """根据景点类型、路线属性和用户偏好估算计划游览分钟数。"""
        metadata = metadata or {}
        text = f"{name} {category} {metadata.get('category', '')} {metadata.get('broad_category', '')}"

        leaf_name = re.split(r"[-—]", name)[-1]
        small_feature = bool(re.search(r"亭$|观景台$|观景点$|瞭望台$|牌坊$", leaf_name))
        if small_feature or any(keyword in text for keyword in ("观景台", "观景点", "瞭望台", "广场", "牌坊")):
            duration = 60
        elif any(keyword in text for keyword in ("寺", "庙", "陵", "故居", "纪念堂", "宗祠")):
            duration = 90
        elif any(keyword in text for keyword in ("博物馆", "美术馆", "科技馆", "纪念馆", "展览馆")):
            duration = 150
        elif any(keyword in text for keyword in ("主题乐园", "游乐园", "乐园", "动物园", "海洋馆", "欢乐谷")):
            duration = 240
        elif (
            "公园" in text
            and not any(keyword in text for keyword in (
                "国家公园", "森林公园", "湿地公园", "地质公园", "风景区", "景区",
            ))
        ):
            # 玄武湖公园、人民公园等城市公园不能仅因名称含“湖/山”就按
            # 大型自然景区估算。
            duration = 120
        elif any(keyword in text for keyword in (
            "国家公园", "自然保护区", "大峡谷", "草原", "雪山", "冰川",
            "湖", "措", "沙漠", "无人区",
        )):
            duration = 240
        elif any(keyword in text for keyword in ("风景区", "景区", "森林公园", "湿地公园")):
            duration = 180
        elif any(keyword in text for keyword in (
            "古城", "古镇", "古村", "园林", "公园", "街", "胡同", "市场",
        )):
            duration = 120
        else:
            duration = 120

        is_remote = bool(metadata.get("_is_remote"))
        if scenic_core:
            duration = max(duration, 240)
        elif is_remote:
            duration = max(duration, 180)

        preference_text = " ".join(preferences or [])
        preference_matches = (
            ("自然" in preference_text and any(keyword in text for keyword in (
                "公园", "风景", "湖", "措", "山", "峡谷", "草原", "湿地", "森林", "冰川",
            )))
            or ("历史" in preference_text and any(keyword in text for keyword in (
                "博物馆", "寺", "庙", "陵", "故居", "古城", "古镇", "纪念",
            )))
            or ("亲子" in preference_text and any(keyword in text for keyword in (
                "动物园", "海洋馆", "科技馆", "乐园",
            )))
            or ("美食" in preference_text and any(keyword in text for keyword in (
                "小吃街", "美食街", "市场", "夜市",
            )))
        )
        if preference_matches:
            duration += 30

        # 用户偏好影响“选不选”，不应让所有市区同类景点无限拉长。
        # 高德已确认位于市区的景点采用半日上限，给第二个景点、午餐和
        # 逐段交通留下空间；远郊/景区节点仍保留原来的全天时长。
        dist_zone = str(metadata.get("_dist_zone", "") or "")
        if not is_remote and not scenic_core:
            if dist_zone == "市区":
                duration = min(duration, 180)
            elif dist_zone == "近郊":
                duration = min(duration, 210)

        duration = max(60, min(360, duration))
        if small_feature:
            # 名称带上级景区不代表游览整个景区，亭/观景台不套用整日下限。
            duration = min(duration, 60)
        return int(round(duration / 30) * 30)

    @classmethod
    def _day_visit_capacity(
        cls, request: TripRequest, day: DayPlan, day_index: int
    ) -> int:
        """估算当天可用于景点内部游览的总分钟数，不含城际交通。"""
        pace = cls._pace_profile(request)
        day_start = {
            "relaxed": 9 * 60 + 30,
            "balanced": 9 * 60,
            "compact": 8 * 60 + 30,
        }.get(getattr(request, "travel_pace", "balanced"), 9 * 60)
        day_end = {
            "relaxed": 19 * 60 + 30,
            "balanced": 20 * 60,
            "compact": 21 * 60,
        }.get(getattr(request, "travel_pace", "balanced"), 20 * 60)

        if day_index == 0:
            ready = cls._arrival_ready_minutes(request)
            if ready is not None:
                day_start = max(day_start, ready)

        if day_index == request.travel_days - 1 and request.departure_time:
            departure = cls._time_to_minutes(request.departure_time)
            if departure is not None:
                # 离开交通方式与抵达方式可能不同；旧客户端未传
                # departure_transport 时才兼容性回退到抵达方式。
                mode = (
                    getattr(request, "departure_transport", None)
                    or request.arrival_transport
                    or ""
                )
                if "飞机" in mode:
                    departure_buffer = 180
                elif "火车" in mode or "高铁" in mode:
                    departure_buffer = 90
                elif "自驾" in mode:
                    departure_buffer = 30
                else:
                    departure_buffer = 90
                day_end = min(day_end, departure - departure_buffer)

        capacity = min(pace["visit_capacity"], max(0, day_end - day_start))
        if day.route_segment:
            capacity = max(0, capacity - max(0, day.route_segment.duration_minutes))
        return capacity

    @classmethod
    def _pace_adjusted_visit_duration(
        cls, request: TripRequest, duration: int
    ) -> int:
        """慢游增加真实停留余量；紧凑模式也不压低景点合理下限。"""
        if request.travel_pace == "relaxed" and duration < 360:
            return min(360, duration + 30)
        return duration

    def _apply_visit_duration_estimates(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        candidates: Optional[List[dict]] = None,
    ) -> TripPlan:
        """给所有景点写入类型估算，并在可行时压缩到当天时间窗口内。"""
        poi_metadata: Dict[str, dict] = {}
        alias_metadata: Dict[str, dict] = {}
        for group in candidates or []:
            group_metadata = dict(group)
            for key in ("display_name", "area_name"):
                value = group.get(key, "")
                if value:
                    alias_metadata.setdefault(
                        self._normalize_attr_name(value), group_metadata
                    )
            for member in group.get("members") or []:
                merged = {**group_metadata, **member}
                if member.get("id"):
                    poi_metadata[member["id"]] = merged
                if member.get("name"):
                    alias_metadata.setdefault(
                        self._normalize_attr_name(member["name"]), merged
                    )

        for day_position, day in enumerate(trip_plan.days):
            estimates = []
            minimums = []
            for attraction in day.attractions:
                metadata = (
                    poi_metadata.get(attraction.poi_id or "")
                    or alias_metadata.get(self._normalize_attr_name(attraction.name))
                    or {}
                )
                scenic_core = bool(
                    day.end_location
                    and self._route_node_profile(day.end_location)["node_type"] == "scenic_area"
                    and self._matches_scenic_route_node(day.end_location, attraction.name)
                )
                estimate = self._estimate_visit_duration(
                    attraction.name,
                    attraction.category or "",
                    request.preferences,
                    metadata,
                    scenic_core,
                )
                estimate = self._pace_adjusted_visit_duration(request, estimate)
                estimates.append(estimate)
                minimums.append(120 if scenic_core or estimate >= 240 else (90 if estimate >= 150 else 60))

            capacity = self._day_visit_capacity(request, day, day_position)
            if (
                estimates
                and sum(estimates) > capacity
                and capacity >= sum(minimums)
            ):
                scale = capacity / sum(estimates)
                adjusted = [
                    max(minimum, int((estimate * scale) // 30) * 30)
                    for estimate, minimum in zip(estimates, minimums)
                ]
                while sum(adjusted) > capacity:
                    reducible = [
                        index for index, value in enumerate(adjusted)
                        if value > minimums[index]
                    ]
                    if not reducible:
                        break
                    index = max(reducible, key=lambda item: adjusted[item] - minimums[item])
                    adjusted[index] -= 30
                while sum(adjusted) + 30 <= capacity:
                    expandable = [
                        index for index, value in enumerate(adjusted)
                        if value < estimates[index]
                    ]
                    if not expandable:
                        break
                    index = max(expandable, key=lambda item: estimates[item] - adjusted[item])
                    adjusted[index] += 30
                estimates = adjusted

            for attraction, estimate in zip(day.attractions, estimates):
                attraction.visit_duration = estimate
            if estimates:
                logger.info(
                    f"游览时长估算 Day{day.day_index+1}: "
                    + ", ".join(
                        f"{attraction.name}={attraction.visit_duration}分钟"
                        for attraction in day.attractions
                    )
                    + f" | 当天可用约{capacity}分钟"
                )
        return trip_plan

    @staticmethod
    def _is_high_effort_attraction(name: str, category: str, duration: int) -> bool:
        """识别不宜与另一个跨片区大型景点拼在同一天的高投入景点。"""
        text = f"{name} {category}"
        signals = (
            "山", "风景区", "景区", "国家公园", "森林公园", "湿地公园",
            "动物园", "海洋馆", "水族馆", "主题乐园", "游乐园", "欢乐谷",
            "古城", "大型博物馆",
        )
        return duration >= 180 and any(signal in text for signal in signals)

    @staticmethod
    def _intra_city_route_mode(transportation: str, distance_km: float) -> str:
        """把用户交通偏好映射为高德路径规划方式。"""
        if transportation == "步行":
            return "walking"
        if "自驾" in transportation or "打车" in transportation:
            return "driving"
        # 公交出行中，1.2km 内通常直接步行比等待公交更符合真实行程。
        if distance_km <= 1.2:
            return "walking"
        return "transit"

    def _route_choice_between_locations(
        self,
        first_location: Location,
        second_location: Location,
        transportation: str,
        city: str = "",
        include_alternatives: bool = False,
    ) -> dict:
        """按用户选择，为一段市内移动挑选真实的门到门交通方案。"""
        distance = self._haversine_distance(
            first_location.longitude,
            first_location.latitude,
            second_location.longitude,
            second_location.latitude,
        )
        if not math.isfinite(distance):
            return {
                "mode": "交通方式待确认",
                "route_mode": "unknown",
                "duration_minutes": 0,
                "distance_km": None,
                "source": "estimated",
            }
        if distance <= 0.05:
            return {
                "mode": "同一地点",
                "route_mode": "none",
                "duration_minutes": 0,
                "distance_km": 0.0,
                "source": "estimated",
                "alternatives": [],
            }

        preference = (transportation or "公共交通").strip()
        if "自驾" in preference:
            preferred_modes = ["driving"]
        elif preference in {"智能混合", "混合"}:
            preferred_modes = (
                (["walking"] if distance <= 5 else [])
                + (["bicycling"] if distance <= 25 else [])
                + ["transit", "driving"]
            )
        elif "步行" in preference and "公交" in preference:
            preferred_modes = ["walking", "transit"] if distance <= 5 else ["transit"]
        elif preference == "步行":
            preferred_modes = ["walking"]
        else:
            # 公共交通优先；1.2km 内直接步行比等待公交更符合实际。
            preferred_modes = ["walking"] if distance <= 1.2 else ["transit"]

        query_modes = list(preferred_modes)
        if include_alternatives:
            # 全程偏好只是默认值。时间轴同时返回该段可行的其他方式，供
            # 用户局部修改；不把超长步行/骑行作为噪声选项。
            available_modes = (
                (["walking"] if distance <= 5 else [])
                + (["bicycling"] if distance <= 25 else [])
                + (["transit"] if city else [])
                + ["driving"]
            )
            query_modes = list(dict.fromkeys([*preferred_modes, *available_modes]))

        origin = f"{first_location.longitude:.6f},{first_location.latitude:.6f}"
        destination = f"{second_location.longitude:.6f},{second_location.latitude:.6f}"
        choices = []
        for route_mode in query_modes:
            summary = get_amap_service().get_route_summary(
                origin, destination, route_mode, city
            )
            if not summary or not summary.get("duration_minutes"):
                continue
            raw_minutes = float(summary["duration_minutes"])
            if route_mode == "walking":
                buffered = raw_minutes
                label = "步行"
            elif route_mode == "bicycling":
                buffered = raw_minutes * 1.05
                label = "骑行"
            elif route_mode == "transit":
                # 高德公交方案包含接驳步行，再补少量候车/进站缓冲。
                buffered = raw_minutes * 1.05 + 5
                label = "公共交通"
            else:
                if "自驾" in preference:
                    buffered = raw_minutes * 1.10 + 10
                    label = "自驾"
                else:
                    buffered = raw_minutes * 1.10 + 5
                    label = "打车"
            choices.append({
                "mode": label,
                "route_mode": route_mode,
                "duration_minutes": max(5, int(math.ceil(buffered / 5) * 5)),
                "distance_km": summary.get("distance_km"),
                "source": "amap",
            })

        preferred_choices = [
            item for item in choices if item["route_mode"] in preferred_modes
        ]
        if preferred_choices:
            selected = min(preferred_choices, key=lambda item: item["duration_minutes"])
            selected = dict(selected)
            selected["alternatives"] = sorted(
                choices, key=lambda item: item["duration_minutes"]
            ) if include_alternatives else []
            return selected

        # 单个高德能力临时不可用时仍保持用户偏好，不偷偷切换到另一种方式。
        fallback_mode = preferred_modes[0]
        if fallback_mode == "walking":
            speed, fixed, label = 4.5, 5, "步行"
        elif fallback_mode == "bicycling":
            speed, fixed, label = 15.0, 5, "骑行"
        elif fallback_mode == "driving":
            speed, fixed = 28.0, 15
            label = "自驾" if "自驾" in preference else "打车"
        else:
            speed, fixed, label = 18.0, 20, "公共交通"
        minutes = int(math.ceil((distance / speed * 60 + fixed) / 5) * 5)
        selected = {
            "mode": label,
            "route_mode": fallback_mode,
            "duration_minutes": max(5, minutes),
            "distance_km": round(distance, 1),
            "source": "estimated",
        }
        if include_alternatives:
            choices.append(dict(selected))
            selected["alternatives"] = sorted(
                choices, key=lambda item: item["duration_minutes"]
            )
        else:
            selected["alternatives"] = []
        return selected

    def _estimated_location_transfer_minutes(
        self,
        first_location: Location,
        second_location: Location,
        transportation: str,
        city: str = "",
        use_amap: bool = False,
    ) -> int:
        """优先使用高德真实路线耗时，失败时退回直线距离估算。"""
        distance = self._haversine_distance(
            first_location.longitude,
            first_location.latitude,
            second_location.longitude,
            second_location.latitude,
        )
        if not math.isfinite(distance):
            return 0

        if use_amap:
            return self._route_choice_between_locations(
                first_location, second_location, transportation, city
            )["duration_minutes"]

        fallback_mode = self._intra_city_route_mode(transportation, distance)
        if fallback_mode == "walking":
            speed, fixed = 4.5, 5
        elif fallback_mode == "driving":
            speed, fixed = 28.0, 15
        else:
            speed, fixed = 18.0, 20
        return int(math.ceil((distance / speed * 60 + fixed) / 5) * 5)

    @staticmethod
    def _clock_text(minutes: int) -> str:
        minutes = max(0, min(23 * 60 + 59, int(minutes)))
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    @staticmethod
    def _terminal_buffer_minutes(transport: str, arrival: bool = False) -> int:
        mode = str(transport or "")
        if "飞机" in mode:
            return 45 if arrival else 120
        if "火车" in mode or "高铁" in mode:
            return 20 if arrival else 60
        if "自驾" in mode:
            return 0
        return 20 if arrival else 60

    def _resolve_transport_hub(
        self, request: TripRequest, arrival: bool
    ) -> Optional[dict]:
        """把用户填写的具体车站/机场解析为真实高德 POI。"""
        name = (
            request.arrival_location if arrival else request.departure_location
        )
        transport = (
            request.arrival_transport if arrival else request.departure_transport
        )
        if not name or "自驾" in str(transport or "") or transport == "已在此地":
            return None
        longitude = getattr(
            request,
            "arrival_location_longitude" if arrival else "departure_location_longitude",
            None,
        )
        latitude = getattr(
            request,
            "arrival_location_latitude" if arrival else "departure_location_latitude",
            None,
        )
        poi_id = getattr(
            request,
            "arrival_location_poi_id" if arrival else "departure_location_poi_id",
            None,
        )
        if longitude is not None and latitude is not None:
            return {
                "name": str(name),
                "poi_id": poi_id,
                "location": Location(
                    longitude=float(longitude), latitude=float(latitude)
                ),
            }

        hub_city = request.city
        if self._is_route_mode(request):
            route_nodes = self._build_route_nodes(request)
            if route_nodes:
                hub_city = route_nodes[0] if arrival else route_nodes[-1]

        cache = getattr(self, "_transport_hub_cache", {})
        cache_key = (str(name), str(hub_city), str(transport), bool(arrival))
        if cache_key in cache:
            return cache[cache_key]
        pois = get_amap_service().search_poi(name, hub_city)
        if not pois:
            logger.warning(f"交通枢纽无法解析: {name}")
            return None
        query = self._normalize_attr_name(name)

        def rank(poi: dict) -> tuple:
            poi_name = self._normalize_attr_name(str(poi.get("name", "")))
            poi_type = str(poi.get("type", ""))
            exact = poi_name == query
            contains = query in poi_name or poi_name in query
            if "飞机" in str(transport or ""):
                type_match = "机场" in poi_name or "飞机场" in poi_type
            else:
                type_match = "火车站" in poi_type or poi_name.endswith("站")
            return exact, contains, type_match

        poi = max(pois, key=rank)
        lng, lat = float(poi.get("lng", 0) or 0), float(poi.get("lat", 0) or 0)
        if not lng or not lat:
            return None
        result = {
            "name": str(poi.get("name") or name),
            "poi_id": poi.get("id") or None,
            "location": Location(longitude=lng, latitude=lat),
        }
        cache[cache_key] = result
        self._transport_hub_cache = cache
        return result

    def _attraction_route_side(
        self,
        attraction: Attraction,
        segment: RouteSegment,
    ) -> str:
        """判断跨城日景点更靠近出发城还是到达城。"""
        origin = self._get_city_center(segment.origin)
        destination = self._get_city_center(segment.destination)
        if not origin or not destination or not attraction.location:
            return "destination"
        origin_distance = self._haversine_distance(
            attraction.location.longitude,
            attraction.location.latitude,
            origin[0],
            origin[1],
        )
        destination_distance = self._haversine_distance(
            attraction.location.longitude,
            attraction.location.latitude,
            destination[0],
            destination[1],
        )
        return "origin" if origin_distance < destination_distance else "destination"

    @staticmethod
    def _multi_city_full_day_candidate(attraction: Attraction) -> bool:
        """多城市中不应与跨城交通硬挤在同一白天的大型景点。"""
        text = f"{attraction.name} {attraction.category or ''}"
        return attraction.visit_duration >= 180 and any(
            marker in text
            for marker in (
                "山", "景区", "风景区", "森林", "峡谷", "古镇",
                "度假区", "乐园", "湿地", "自然保护区",
            )
        )

    @classmethod
    def _multi_city_full_day_attraction(cls, day: DayPlan) -> bool:
        """多城市到达日是否只有一个需要完整白天的大型景点。"""
        return (
            len(day.attractions) == 1
            and cls._multi_city_full_day_candidate(day.attractions[0])
        )

    def _arrange_multi_city_attraction_blocks(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
    ) -> TripPlan:
        """仅多城市：先把完整景区日与换城日拆开，再选择跨城边界。"""
        if request.trip_mode != "multi_city":
            return trip_plan

        last_position = len(trip_plan.days) - 1
        for position in range(1, len(trip_plan.days)):
            transfer_day = trip_plan.days[position]
            segment = transfer_day.route_segment
            if not segment or not self._multi_city_full_day_attraction(transfer_day):
                continue

            full_day_attraction = transfer_day.attractions[0]
            replacement_position = None
            for later_position in range(position + 1, len(trip_plan.days)):
                later_day = trip_plan.days[later_position]
                # 下一次换城已经开始，不能跨过城市边界寻找空位。
                if later_day.route_segment:
                    break
                later_stop = later_day.overnight_city or later_day.end_location
                if later_stop and later_stop != segment.destination:
                    break
                if (
                    later_position == last_position
                    and request.departure_time
                    and (
                        self._route_day_target_count(None, later_position, request) == 0
                        or (self._time_to_minutes(request.departure_time) or 0) < 17 * 60
                    )
                ):
                    continue
                if not later_day.attractions:
                    replacement_position = later_position
                    break
                if any(item.user_locked for item in later_day.attractions):
                    continue
                if any(
                    self._multi_city_full_day_candidate(item)
                    for item in later_day.attractions
                ):
                    continue
                replacement_position = later_position
                break

            if replacement_position is None:
                continue

            full_day = trip_plan.days[replacement_position]
            city_attractions = list(full_day.attractions)
            # 换城日最多先保留两个市区景点；真实路线复核仍会继续裁剪。
            transfer_day.attractions = city_attractions[:2]
            for attraction in city_attractions[2:]:
                self._record_removed_attraction(
                    trip_plan,
                    attraction,
                    full_day.day_index,
                    f"为{full_day_attraction.name}预留完整游览日而移出",
                )
            full_day.attractions = [full_day_attraction]
            transfer_names = "、".join(
                attraction.name for attraction in transfer_day.attractions
            )
            transfer_day.description = (
                f"从{segment.origin}前往{segment.destination}，"
                + (
                    f"抵达后游览{transfer_names}。"
                    if transfer_names else "抵达后休整。"
                )
            )
            full_day.description = (
                f"本日为{full_day_attraction.name}预留完整游览和往返时间。"
            )
            logger.info(
                f"多城市景点分块: {full_day_attraction.name} "
                f"由 Day{position + 1} 调整至 Day{replacement_position + 1}，"
                "换城日改排目的地市区景点"
            )
        return trip_plan

    @classmethod
    def _multi_city_transfer_keep_score(cls, attraction: Attraction) -> float:
        """提前换城时，优先保留需白天入场且价值较高的景点。"""
        text = f"{attraction.name} {attraction.category or ''}"
        daytime = any(
            keyword in text
            for keyword in (
                "博物馆", "博物院", "纪念馆", "美术馆", "遗址",
                "公园", "故居", "风景区", "景区",
            )
        )
        return (
            (10000 if attraction.user_locked else 0)
            + (500 if daytime else 0)
            + float(attraction.rating or 0) * 10
            + max(0, attraction.visit_duration)
        )

    def _optimize_multi_city_transfer_boundaries(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
    ) -> TripPlan:
        """仅多城市：必要时把换城放到整日景点的前一天。

        路线初稿依旧保证每城有停留日；该步只调整相邻两天的
        过夜边界，不会进入单城市或自驾环线的编排。
        """
        if request.trip_mode != "multi_city":
            return trip_plan

        for position in range(2, len(trip_plan.days)):
            destination_day = trip_plan.days[position]
            segment = destination_day.route_segment
            if not segment or not self._multi_city_full_day_attraction(destination_day):
                continue
            previous_day = trip_plan.days[position - 1]
            if previous_day.route_segment:
                continue
            previous_stop = previous_day.overnight_city or previous_day.end_location
            if previous_stop and previous_stop != segment.origin:
                continue
            # 超长路段不宜在没有真实班次的情况下强行改到前一天。
            if segment.duration_minutes > 6 * 60:
                continue

            # 两个相邻的大型景区都需要完整白天时，天数已经无法同时容纳。
            # 保留用户锁定项；否则优先保住下一城市的代表景点，避免路线
            # 到达了下一城却没有任何核心游览。被移出的景点进入编辑页推荐。
            if self._multi_city_full_day_attraction(previous_day):
                previous_attraction = previous_day.attractions[0]
                destination_attraction = destination_day.attractions[0]
                previous_score = self._multi_city_transfer_keep_score(
                    previous_attraction
                )
                destination_score = self._multi_city_transfer_keep_score(
                    destination_attraction
                )
                keep_previous = (
                    previous_attraction.user_locked
                    and not destination_attraction.user_locked
                ) or (
                    previous_attraction.user_locked
                    == destination_attraction.user_locked
                    and previous_score > destination_score
                )
                if keep_previous:
                    destination_day.attractions = []
                    self._record_removed_attraction(
                        trip_plan,
                        destination_attraction,
                        destination_day.day_index,
                        "相邻两天均为大型景区且跨城时间不足，保留前一日景点",
                    )
                    logger.warning(
                        f"多城市整日景点冲突: 保留 {previous_attraction.name}，"
                        f"移出 {destination_attraction.name}"
                    )
                    continue
                previous_day.attractions = []
                self._record_removed_attraction(
                    trip_plan,
                    previous_attraction,
                    previous_day.day_index,
                    f"为前往{segment.destination}并保留"
                    f"{destination_attraction.name}完整游览日而移出",
                )
                logger.warning(
                    f"多城市整日景点冲突: 移出 {previous_attraction.name}，"
                    f"保留下一城 {destination_attraction.name}"
                )

            if len(previous_day.attractions) > 1:
                if sum(
                    1 for attraction in previous_day.attractions
                    if attraction.user_locked
                ) > 1:
                    # 用户明确锁定的多个景点不能为了自动提前换城而删除。
                    continue
                keep = max(
                    previous_day.attractions,
                    key=self._multi_city_transfer_keep_score,
                )
                removed = [
                    attraction for attraction in previous_day.attractions
                    if attraction is not keep
                ]
                previous_day.attractions = [keep]
                for attraction in removed:
                    self._record_removed_attraction(
                        trip_plan,
                        attraction,
                        previous_day.day_index,
                        f"为提前前往{segment.destination}、保证次日整日景点可执行而移出",
                    )

            destination_stop = (
                destination_day.overnight_city
                or destination_day.end_location
                or segment.destination
            )
            previous_day.route_segment = segment
            previous_day.end_location = segment.destination
            previous_day.overnight_city = destination_stop
            previous_day.is_transit_day = True

            destination_day.route_segment = None
            destination_day.start_location = segment.destination
            destination_day.end_location = segment.destination
            destination_day.overnight_city = destination_stop
            destination_day.is_transit_day = False

            kept_names = "、".join(
                attraction.name for attraction in previous_day.attractions
            )
            previous_day.description = (
                (f"上午游览{kept_names}，" if kept_names else "")
                + f"下午从{segment.origin}前往{segment.destination}，"
                f"当晚住{destination_stop}。"
            )
            destination_day.description = (
                f"已提前抵达{segment.destination}，本日预留完整时间游览"
                f"{'、'.join(item.name for item in destination_day.attractions)}。"
            )
            logger.info(
                f"多城市边界优化: {segment.origin}→{segment.destination} "
                f"由 Day{position + 1} 提前至 Day{position}，"
                "为目的地整日景点留出白天"
            )
        return trip_plan

    def _refresh_multi_city_day_descriptions(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
    ) -> TripPlan:
        """仅多城市：按最终景点和跨城段重写每日摘要。"""
        if request.trip_mode != "multi_city":
            return trip_plan
        last_position = len(trip_plan.days) - 1
        for position, day in enumerate(trip_plan.days):
            segment = day.route_segment
            if segment:
                origin_names = [
                    attraction.name for attraction in day.attractions
                    if self._attraction_route_side(attraction, segment) == "origin"
                ]
                destination_names = [
                    attraction.name for attraction in day.attractions
                    if self._attraction_route_side(attraction, segment) == "destination"
                ]
                pieces = []
                if origin_names:
                    pieces.append(f"先游览{'、'.join(origin_names)}")
                pieces.append(f"从{segment.origin}前往{segment.destination}")
                if destination_names:
                    pieces.append(f"抵达后游览{'、'.join(destination_names)}")
                pieces.append(f"当晚住{day.overnight_city or segment.destination}")
                day.description = "，".join(pieces) + "。"
                continue

            names = "、".join(attraction.name for attraction in day.attractions)
            if position == last_position and request.departure_time:
                day.description = (
                    (f"离开前游览{names}，" if names else "")
                    + "预留早餐、退房、取行李及前往交通枢纽的时间。"
                )
            elif position == 0 and request.arrival_time:
                day.description = (
                    f"抵达{day.overnight_city or day.end_location or request.city}并办理入住后"
                    + (f"，游览{names}。" if names else "，以用餐和休整为主。")
                )
            elif names and self._multi_city_full_day_attraction(day):
                day.description = (
                    f"本日安排{names}，预留往返交通和完整游览时间。"
                )
            elif names:
                day.description = (
                    f"本日游览{names}，当晚住"
                    f"{day.overnight_city or day.end_location or request.city}。"
                )
            else:
                day.description = "本日以跨城衔接或休整为主，未安排固定景点。"
        return trip_plan

    def _rebalance_transfer_day_attractions(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
    ) -> TripPlan:
        """在短途换城日保留上一城半天，避免每座城市都被切成整天边界。"""
        if request.trip_mode != "multi_city":
            return trip_plan
        for position in range(1, len(trip_plan.days)):
            day = trip_plan.days[position]
            segment = day.route_segment
            previous_day = trip_plan.days[position - 1]
            if not segment or not (0 < segment.duration_minutes <= 210):
                continue
            # 目的地大型景区需要完整白天，不能为了“多排一个”再把上一城
            # 景点塞到上午，最终把景区推到傍晚甚至深夜。
            if self._multi_city_full_day_attraction(day):
                continue
            # 已经有出发城活动时说明规划器完成了半日衔接，不重复移动。
            if any(
                self._attraction_route_side(attraction, segment) == "origin"
                for attraction in day.attractions
            ):
                continue
            # 下一城已有两个及以上景点时优先保证到达后的可执行性；否则从
            # 上一天末尾延续一个非远郊、非用户锁定景点到换城日上午。
            if len(day.attractions) > 1 or len(previous_day.attractions) < 2:
                continue
            if self._is_remote_day(previous_day):
                continue
            movable_index = next((
                index for index in range(len(previous_day.attractions) - 1, -1, -1)
                if not previous_day.attractions[index].user_locked
            ), None)
            if movable_index is None:
                continue
            attraction = previous_day.attractions.pop(movable_index)
            day.attractions.insert(0, attraction)
            previous_names = "、".join(item.name for item in previous_day.attractions)
            previous_day.description = (
                f"本日游览{previous_names}，返回{segment.origin}住宿。"
                if previous_names else f"本日抵达并住在{segment.origin}。"
            )
            destination_names = "、".join(
                item.name for item in day.attractions[1:]
            )
            day.description = (
                f"上午继续游览{segment.origin}的{attraction.name}，"
                f"之后前往{segment.destination}"
                + (f"并游览{destination_names}。" if destination_names else "。")
            )
            logger.info(
                f"跨城半日衔接 Day{position + 1}: "
                f"{attraction.name} 留在 {segment.origin} 上午游览"
            )
        return trip_plan

    @staticmethod
    def _expects_rail_transfer(segment: RouteSegment) -> bool:
        mode = str(segment.transport_mode or "")
        return any(word in mode for word in ("高铁", "动车", "火车", "铁路", "列车"))

    def _resolve_intercity_rail_hub(
        self, city: str, preferred_name: Optional[str] = None
    ) -> Optional[dict]:
        """Resolve a practical main railway station for an intermediate city."""
        city_name = str(city or "").strip()
        city_root = re.sub(r"(?:市|州|地区)$", "", city_name)
        cache = getattr(self, "_intercity_rail_hub_cache", {})
        cache_key = (city_root, str(preferred_name or "").strip())
        if cache_key in cache:
            return cache[cache_key]

        amap = get_amap_service()
        candidates = amap.search_transport_hubs(city_name, "高铁")
        if not isinstance(candidates, (list, tuple)) or not candidates:
            candidates = amap.search_poi(
                str(preferred_name or f"{city_root}站"), city_name
            )
        if not isinstance(candidates, (list, tuple)):
            candidates = []

        normalized = []
        for item in candidates or []:
            try:
                longitude = float(item.get("longitude", item.get("lng", 0)) or 0)
                latitude = float(item.get("latitude", item.get("lat", 0)) or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            name = str(item.get("name", "") or "").strip()
            if not name or not longitude or not latitude:
                continue
            if any(word in name for word in ("地铁站", "公交站", "客运站", "收费站")):
                continue
            normalized.append({
                "name": name,
                "longitude": longitude,
                "latitude": latitude,
                "poi_id": item.get("id") or None,
            })
        if not normalized:
            cache[cache_key] = None
            self._intercity_rail_hub_cache = cache
            return None

        requested_name = str(preferred_name or "").replace(" ", "").strip()
        preferred_names = [
            f"{city_root}站", f"{city_root}南站", f"{city_root}北站",
            f"{city_root}东站", f"{city_root}西站",
        ]

        def rank(item: dict) -> tuple:
            name = item["name"].replace(" ", "")
            requested_match = (
                0 if requested_name and (
                    name == requested_name
                    or requested_name in name
                    or name in requested_name
                ) else 1
            )
            try:
                preferred_index = preferred_names.index(name)
            except ValueError:
                preferred_index = len(preferred_names)
            return (
                requested_match,
                preferred_index,
                0 if city_root and city_root in name else 1,
                len(name),
                name,
            )

        result = min(normalized, key=rank)
        cache[cache_key] = result
        self._intercity_rail_hub_cache = cache
        return result

    @staticmethod
    def _estimated_intercity_rail_minutes(distance_km: float) -> int:
        """Conservative station-to-station fallback, never a claimed timetable."""
        distance = max(0.0, float(distance_km or 0))
        minutes = max(60, distance / 110 * 60 + 15)
        return min(600, int(math.ceil(minutes / 5) * 5))

    @staticmethod
    def _usable_railway_summary(railway: Optional[dict], distance_km: float) -> bool:
        """Require splittable station data and reject physically suspicious legs."""
        if not isinstance(railway, dict):
            return False
        try:
            duration = int(railway.get("duration_minutes", 0) or 0)
            departure = railway.get("departure_hub") or {}
            arrival = railway.get("arrival_hub") or {}
            coordinates = (
                float(departure.get("longitude", 0) or 0),
                float(departure.get("latitude", 0) or 0),
                float(arrival.get("longitude", 0) or 0),
                float(arrival.get("latitude", 0) or 0),
            )
        except (TypeError, ValueError, AttributeError):
            return False
        if (
            duration <= 0
            or not departure.get("name")
            or not arrival.get("name")
            or not all(coordinates)
        ):
            return False
        distance = max(
            0.0,
            float(railway.get("distance_km", 0) or distance_km or 0),
        )
        plausible_maximum = max(180, int(distance / 35 * 60 + 90))
        return duration <= plausible_maximum

    def _rail_fallback_summary(
        self,
        day: DayPlan,
        segment: RouteSegment,
        previous_hotel: Optional[Hotel],
        destination_hotel: Optional[Hotel],
        research: Optional[dict],
    ) -> dict:
        """Build a transparent hotel→station→rail→station→hotel estimate."""
        rail_minutes = int(
            (research or {}).get("duration_minutes")
            or self._estimated_intercity_rail_minutes(segment.distance_km)
        )
        source = "tavily" if research else "estimated"
        departure_hub = self._resolve_intercity_rail_hub(
            segment.origin, (research or {}).get("departure_station")
        )
        arrival_hub = self._resolve_intercity_rail_hub(
            segment.destination, (research or {}).get("arrival_station")
        )
        if (not departure_hub or not arrival_hub) and not research:
            return {
                "duration_minutes": max(0, segment.duration_minutes),
                "distance_km": segment.distance_km or None,
                "source": "estimated",
                "railway": None,
            }
        railway = None
        access_minutes = 45
        egress_minutes = 45
        if departure_hub and arrival_hub:
            departure_location = Location(
                longitude=departure_hub["longitude"],
                latitude=departure_hub["latitude"],
            )
            arrival_location = Location(
                longitude=arrival_hub["longitude"],
                latitude=arrival_hub["latitude"],
            )
            if previous_hotel and previous_hotel.location:
                access_minutes = self._route_choice_between_locations(
                    previous_hotel.location,
                    departure_location,
                    day.transportation,
                    segment.origin,
                )["duration_minutes"]
            if destination_hotel and destination_hotel.location:
                egress_minutes = self._route_choice_between_locations(
                    arrival_location,
                    destination_hotel.location,
                    day.transportation,
                    segment.destination,
                )["duration_minutes"]
            railway = {
                "departure_hub": departure_hub,
                "arrival_hub": arrival_hub,
                "duration_minutes": rail_minutes,
                "distance_km": segment.distance_km or None,
                "source": source,
                "requires_confirmation": True,
                "source_urls": (research or {}).get("source_urls", []),
            }

        # 45 minutes before a reference train and 20 minutes to leave the
        # arrival station are deliberately visible in the resulting timeline.
        total = access_minutes + 45 + rail_minutes + 20 + egress_minutes
        return {
            "duration_minutes": total,
            "distance_km": segment.distance_km or None,
            "source": source,
            "railway": railway,
        }

    def _normalize_intercity_rail_summary(
        self,
        day: DayPlan,
        segment: RouteSegment,
        previous_hotel: Optional[Hotel],
        destination_hotel: Optional[Hotel],
        summary: Optional[dict],
        fallback_holder: dict,
    ) -> Optional[dict]:
        """Reject aggregate AMap transit when it cannot prove a railway leg."""
        if not self._expects_rail_transfer(segment):
            return summary
        railway = (summary or {}).get("railway")
        if self._usable_railway_summary(railway, segment.distance_km):
            return summary

        if "summary" not in fallback_holder:
            logger.warning(
                f"高德跨城结果缺少铁路段，触发异常兜底: "
                f"{segment.origin}→{segment.destination} "
                f"amap_minutes={(summary or {}).get('duration_minutes')}"
            )
            research = research_intercity_rail_duration(
                segment.origin,
                segment.destination,
                segment.distance_km,
            )
            if research:
                try:
                    researched_minutes = int(
                        research.get("duration_minutes", 0) or 0
                    )
                    amap_minutes = int(
                        (summary or {}).get("duration_minutes", 0) or 0
                    )
                    estimated_minutes = self._estimated_intercity_rail_minutes(
                        segment.distance_km
                    )
                    research_cap = min(
                        720,
                        max(
                            estimated_minutes + 90,
                            int(estimated_minutes * 1.75),
                        ),
                    )
                    conflicts_with_amap = (
                        amap_minutes > 0
                        and researched_minutes
                        > max(amap_minutes + 45, int(amap_minutes * 1.35))
                    )
                    if (
                        researched_minutes <= 0
                        or researched_minutes > research_cap
                        or conflicts_with_amap
                    ):
                        logger.warning(
                            f"铁路网页耗时异常，改用距离估算: "
                            f"{segment.origin}→{segment.destination} "
                            f"tavily={researched_minutes}分钟 "
                            f"amap={amap_minutes or '无'}分钟 "
                            f"cap={research_cap}分钟"
                        )
                        research = None
                except (TypeError, ValueError, AttributeError):
                    research = None
            fallback_holder["summary"] = self._rail_fallback_summary(
                day, segment, previous_hotel, destination_hotel, research
            )
            fallback_holder["research"] = research
        return fallback_holder["summary"]

    def _choose_intercity_transfer_window(
        self,
        day: DayPlan,
        segment: RouteSegment,
        previous_hotel: Optional[Hotel],
        destination_hotel: Optional[Hotel],
        origin_attractions: List[Tuple[int, Attraction]],
        destination_attractions: List[Tuple[int, Attraction]],
    ) -> dict:
        """比较上午、午后和傍晚出发，选择当天损失最小的跨城窗口。"""
        candidate_departures = (
            [13 * 60 + 30, 17 * 60 + 30]
            if origin_attractions
            else [8 * 60 + 45, 13 * 60 + 30, 17 * 60 + 30]
        )
        fallback_duration = max(0, segment.duration_minutes)
        origin_value = sum(max(60, item.visit_duration) for _, item in origin_attractions)
        destination_value = sum(
            max(60, item.visit_duration) for _, item in destination_attractions
        )
        can_query_transit = (
            previous_hotel
            and previous_hotel.location
            and destination_hotel
            and destination_hotel.location
            and "自驾" not in segment.transport_mode
            and "飞机" not in segment.transport_mode
        )
        amap = get_amap_service()
        choices = []
        rail_fallback: dict = {}
        for departure in candidate_departures:
            summary = None
            if can_query_transit:
                summary = amap.get_intercity_transit_summary(
                    (
                        f"{previous_hotel.location.longitude},"
                        f"{previous_hotel.location.latitude}"
                    ),
                    (
                        f"{destination_hotel.location.longitude},"
                        f"{destination_hotel.location.latitude}"
                    ),
                    segment.origin,
                    segment.destination,
                    day.date,
                    self._clock_text(departure),
                )
            summary = self._normalize_intercity_rail_summary(
                day,
                segment,
                previous_hotel,
                destination_hotel,
                summary,
                rail_fallback,
            )
            duration = (
                int(summary["duration_minutes"])
                if summary and summary.get("duration_minutes") else fallback_duration
            )
            arrival = departure + duration
            pre_available = max(0, departure - 8 * 60)
            post_available = max(0, 21 * 60 + 30 - arrival)
            origin_need = (
                45
                + origin_value
                + len(origin_attractions) * 25
                + 20
                + (60 if departure >= 13 * 60 else 0)
            )
            destination_need = (
                20
                + destination_value
                + len(destination_attractions) * 25
                + 75
                + (60 if arrival <= 13 * 60 + 30 else 0)
            )
            deficit = max(0, origin_need - pre_available) + max(
                0, destination_need - post_available
            )
            if origin_attractions:
                slot_bonus = 90 if departure == 13 * 60 + 30 else (
                    20 if departure > 13 * 60 + 30 else -120
                )
            else:
                slot_bonus = 70 if departure < 12 * 60 else (
                    10 if departure < 17 * 60 else -70
                )
            score = (
                slot_bonus
                - deficit * 2
                - max(0, arrival - 20 * 60) * 2
                + (20 if summary else 0)
            )
            if arrival > 22 * 60:
                score -= 2000
            choices.append({
                "departure_minutes": departure,
                "arrival_minutes": arrival,
                "duration_minutes": duration,
                "distance_km": (
                    summary.get("distance_km") if summary else segment.distance_km
                ),
                "source": summary.get("source", "estimated") if summary else segment.source,
                "railway": summary.get("railway") if summary else None,
                "score": score,
            })

        selected = max(choices, key=lambda item: item["score"])
        segment.duration_minutes = selected["duration_minutes"]
        if selected.get("distance_km"):
            segment.distance_km = selected["distance_km"]
        segment.source = selected["source"]
        segment.timing_source = selected["source"]
        if selected["source"] in {"tavily", "estimated"}:
            segment.requires_confirmation = True
        segment.suggested_departure_time = self._clock_text(
            selected["departure_minutes"]
        )
        segment.suggested_arrival_time = self._clock_text(
            selected["arrival_minutes"]
        )
        logger.info(
            f"跨城窗口 Day{day.day_index + 1}: {segment.origin}→"
            f"{segment.destination} {segment.suggested_departure_time}-"
            f"{segment.suggested_arrival_time} source={segment.timing_source}"
        )
        return selected

    @staticmethod
    def _railway_timeline_label(railway: dict, fallback: str) -> str:
        """Use the returned train type instead of calling every rail leg high-speed."""
        trip = str(railway.get("trip", "") or "").strip()
        rail_type = str(railway.get("type", "") or "").strip()
        type_labels = {
            "2010": "普速列车",
            "2011": "高铁",
            "2012": "动车",
            "2013": "城际列车",
            "2014": "直达列车",
            "2015": "特快列车",
            "2016": "快速列车",
            "2017": "临时列车",
            "2018": "市郊列车",
        }
        label = type_labels.get(rail_type)
        if not label and trip:
            label = {
                "G": "高铁", "D": "动车", "C": "城际列车",
                "Z": "直达列车", "T": "特快列车", "K": "快速列车",
            }.get(trip[:1].upper(), "铁路")
        if not label:
            label = fallback if "火车" in fallback or "高铁" in fallback else "铁路"
        return f"{trip + ' ' if trip else ''}{label}（参考）"

    @staticmethod
    def _railway_hub_location(hub: dict) -> Optional[Location]:
        try:
            longitude = float(hub.get("longitude", 0) or 0)
            latitude = float(hub.get("latitude", 0) or 0)
        except (TypeError, ValueError, AttributeError):
            return None
        if not longitude or not latitude:
            return None
        return Location(longitude=longitude, latitude=latitude)

    @staticmethod
    def _is_night_visit(attraction: Attraction) -> bool:
        text = attraction.name + " " + (attraction.description or "")
        if any(marker in text for marker in ("不适合夜游", "不建议夜游", "不安排夜游")):
            return False
        return any(marker in text for marker in ("夜游", "夜间", "夜景", "夜逛", "夜市"))

    @classmethod
    def _is_subordinate_backfill_attraction(cls, attraction: Attraction) -> bool:
        """最终补位不能把景区入口、方向广场等附属节点当成独立景点。"""
        name = cls._normalize_attr_name(attraction.name)
        return bool(
            re.search(r"(?:东|西|南|北|入口|出口|游客|集散|服务)广场$", name)
            or re.search(r"(?:入口|出口|牌坊|游客中心|服务中心|售票处)$", name)
        )

    @classmethod
    def _is_night_friendly_attraction(cls, attraction: Attraction) -> bool:
        """没有明确夜游文案时，仅放行常见的开放式夜间游览地点。"""
        if cls._is_subordinate_backfill_attraction(attraction):
            return False
        if cls._is_night_visit(attraction):
            return True
        text = " ".join((
            attraction.name,
            attraction.description or "",
            attraction.category or "",
        ))
        if any(marker in text for marker in (
            "步行街", "商业街", "夜市", "古街", "街区", "秦淮河",
            "夫子庙", "老门东", "外滩", "广场",
        )):
            return True
        hours = attraction.ticket_info.opening_hours if attraction.ticket_info else ""
        return any(start <= 18 * 60 and end >= 20 * 60 for start, end in cls._opening_intervals(hours))

    @classmethod
    def _single_city_visit_fit_start(cls, attraction: Attraction, desired: int, duration: int) -> Optional[int]:
        if cls._is_night_visit(attraction):
            desired = max(desired, 18 * 60)
        return cls._opening_fit_start(attraction, desired, duration)

    @staticmethod
    def _timeline_stop_route_name(stop: dict) -> str:
        return stop.get("route_name") or stop.get("entity_name") or stop["name"]

    @staticmethod
    def _saved_transport_choices(day: DayPlan) -> Dict[Tuple[str, str], TimelineItem]:
        """Capture user choices before rebuilding; fresh timeline edits win."""
        choices = {}
        for item in [*day.transport_overrides, *day.timeline]:
            if (
                item.kind == "transport" and item.customized and not item.is_intercity
                and item.from_name and item.to_name and item.transport_mode
                and item.duration_minutes > 0
            ):
                choices[(item.from_name, item.to_name)] = item.model_copy(deep=True)
        return choices

    @staticmethod
    def _apply_saved_transport_choice(route: dict, choice: Optional[TimelineItem]) -> dict:
        if choice is None:
            return route
        return {
            **route,
            "mode": choice.transport_mode,
            "duration_minutes": choice.duration_minutes,
            "distance_km": choice.distance_km if choice.distance_km is not None else route.get("distance_km"),
            "source": choice.source or route.get("source", "estimated"),
            "customized": True,
        }

    def _arrange_single_city_timeline_stops(
        self, stops: List[dict], day: DayPlan, request: TripRequest, starting: int,
        transport_choices: Optional[Dict[Tuple[str, str], TimelineItem]] = None,
    ) -> List[dict]:
        """仅在当天已选景点内比较三餐插入位置，不改变多城市跨城排程。"""
        attr_stops = [s for s in stops if s["kind"] == "attraction"]
        if not attr_stops or any(a.user_locked for a in day.attractions):
            return stops
        first = next(i for i, s in enumerate(stops) if s["kind"] == "attraction" or s.get("meal_type") in {"lunch", "dinner"})
        last = max(i for i, s in enumerate(stops) if s["kind"] == "attraction" or s.get("meal_type") in {"lunch", "dinner"})
        middle = stops[first:last + 1]
        if any(s["kind"] not in {"attraction", "meal"} for s in middle):
            return stops
        meals = {s.get("meal_type"): s for s in middle if s["kind"] == "meal"}
        if any(key not in {"lunch", "dinner"} for key in meals):
            return stops
        prefix, suffix = stops[:first], stops[last + 1:]
        targets = {"breakfast": 480, "lunch": 690, "dinner": 1050}
        route_cache = {}

        def minutes_between(left, right):
            if not left or not left.get("location") or not right.get("location"):
                return 0
            a, b = left["location"], right["location"]
            names = (self._timeline_stop_route_name(left), self._timeline_stop_route_name(right))
            key = (a.longitude, a.latitude, b.longitude, b.latitude, *names)
            if key not in route_cache:
                route = self._route_choice_between_locations(
                    a, b, request.transportation, request.city, include_alternatives=False,
                )
                route_cache[key] = self._apply_saved_transport_choice(
                    route, (transport_choices or {}).get(names),
                )["duration_minutes"]
            return route_cache[key]

        def score(sequence):
            current, previous, hard, wait, travel = starting, None, 0, 0, 0
            visited, idle_before_visiting = False, 0
            for stop in sequence:
                leg = minutes_between(previous, stop)
                current += leg
                travel += leg
                arrival = current
                meal_type = stop.get("meal_type")
                if meal_type in targets:
                    current = max(current, targets[meal_type])
                    if not visited and meal_type in {"lunch", "dinner"}:
                        # Waiting for lunch before the first visit is not the
                        # same as resting after sightseeing. Keep hard opening/
                        # meal constraints first, but do not sacrifice a whole
                        # usable morning just to save a small amount of travel.
                        idle_before_visiting += max(0, current - arrival - 30)
                    if meal_type == "lunch":
                        hard += max(0, current - 840)
                    elif meal_type == "dinner":
                        hard += max(0, current - 1230)
                    meal = day.meals[int(stop["reference_index"])]
                    if self._opening_hours_cover_period(meal.opening_hours, current, current + stop["duration"]) is False:
                        hard += 180
                if stop["kind"] == "attraction":
                    attraction = day.attractions[int(stop["reference_index"])]
                    fitted = self._single_city_visit_fit_start(attraction, current, stop["duration"])
                    if fitted is None:
                        hard += 240
                    else:
                        current = fitted
                    visited = True
                wait += max(0, current - arrival)
                if stop.get("is_departure_hub") and request.departure_time:
                    latest = self._time_to_minutes(request.departure_time) - self._terminal_buffer_minutes(request.departure_transport or "", arrival=False)
                    hard += max(0, current - latest) * 3
                current += stop["duration"]
                if stop["kind"] == "hotel":
                    hard += max(0, current - 1320)
                if stop.get("location"):
                    previous = stop
            hard += max(0, current - 1439)
            # 硬约束先于路线长度；正常饭点与更少空档优于机械平分景点。
            return hard, travel + wait * 0.5 + idle_before_visiting, current

        orders = [tuple(attr_stops)]
        if len(attr_stops) <= 4:
            orders = list(permutations(attr_stops))
        else:
            orders.append(tuple(sorted(attr_stops, key=lambda s: (
                self._is_night_visit(day.attractions[int(s["reference_index"])]),
                min((end for _, end in self._effective_attraction_opening_intervals(day.attractions[int(s["reference_index"])])), default=1440),
            ))))
        best, best_score = stops, score(stops)
        for order in orders:
            n = len(order)
            for lunch_cut in range(n + 1) if "lunch" in meals else [0]:
                for dinner_cut in range(lunch_cut, n + 1) if "dinner" in meals else [n]:
                    sequence = list(prefix)
                    for index in range(n + 1):
                        if "lunch" in meals and index == lunch_cut:
                            sequence.append(meals["lunch"])
                        if "dinner" in meals and index == dinner_cut:
                            sequence.append(meals["dinner"])
                        if index < n:
                            sequence.append(order[index])
                    sequence.extend(suffix)
                    value = score(sequence)
                    if value < best_score:
                        best, best_score = sequence, value
        return best

    def _build_daily_timelines(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        day_indices: Optional[set[int]] = None,
    ) -> TripPlan:
        """把分离的酒店、餐饮和景点转换为可执行的逐段时间轴。"""
        self._sync_multi_city_meal_anchors(trip_plan, request)
        if request.trip_mode == "single_city":
            self._sync_single_city_meal_anchors(trip_plan)
        meal_targets = {"breakfast": 8 * 60, "lunch": 11 * 60 + 30, "dinner": 17 * 60 + 30}
        meal_durations = {"breakfast": 45, "lunch": 60, "dinner": 75, "snack": 30}

        for day_position, day in enumerate(trip_plan.days):
            if day_indices is not None and day_position not in day_indices:
                continue
            transport_choices = self._saved_transport_choices(day)
            day.transport_overrides = list(transport_choices.values())
            intercity_segment = (
                day.route_segment
                if self._is_route_mode(request) and day_position > 0
                else None
            )
            previous_day_hotel = (
                trip_plan.days[day_position - 1].hotel
                if intercity_segment and day_position > 0
                else None
            )
            meal_indices = {meal.type: index for index, meal in enumerate(day.meals)}
            all_attractions = list(enumerate(day.attractions))
            origin_attractions: List[Tuple[int, Attraction]] = []
            attractions = all_attractions
            transfer_choice = None
            if intercity_segment:
                origin_attractions = [
                    item for item in all_attractions
                    if self._attraction_route_side(item[1], intercity_segment) == "origin"
                ]
                origin_indexes = {index for index, _ in origin_attractions}
                attractions = [
                    item for item in all_attractions if item[0] not in origin_indexes
                ]
                transfer_choice = self._choose_intercity_transfer_window(
                    day,
                    intercity_segment,
                    previous_day_hotel,
                    day.hotel,
                    origin_attractions,
                    attractions,
                )
            lunch_before_transfer = bool(
                intercity_segment
                and transfer_choice
                and (
                    origin_attractions
                    or transfer_choice["departure_minutes"] >= 13 * 60
                )
            )
            lunch_at_arrival_station = bool(
                intercity_segment
                and transfer_choice
                and transfer_choice.get("railway")
                and not origin_attractions
                and "lunch" in meal_indices
                and transfer_choice["arrival_minutes"] + 20 >= 14 * 60
            )
            morning_count = max(1, math.ceil(len(attractions) / 2)) if attractions else 0
            morning = attractions[:morning_count]
            afternoon = attractions[morning_count:]
            stops = []

            # A selected station/airport is still useful trip metadata when the
            # traveller leaves the boundary clock blank. Resolve it whenever a
            # concrete hub was selected, but only let it participate in route
            # timing when the corresponding clock is known.
            arrival_hub = (
                self._resolve_transport_hub(request, arrival=True)
                if (
                    day_position == 0
                    and (request.arrival_location or request.arrival_time)
                )
                else None
            )
            departure_hub = (
                self._resolve_transport_hub(request, arrival=False)
                if (
                    day_position == len(trip_plan.days) - 1
                    and (request.departure_location or request.departure_time)
                )
                else None
            )
            timed_arrival_hub = arrival_hub if request.arrival_time else None
            timed_departure_hub = departure_hub if request.departure_time else None

            if timed_arrival_hub:
                stops.append({
                    "kind": "hub", "name": timed_arrival_hub["name"],
                    "location": timed_arrival_hub["location"],
                    "duration": self._terminal_buffer_minutes(
                        request.arrival_transport or "", arrival=True
                    ),
                    "reference_type": None, "reference_index": None,
                    "hub_role": "trip_arrival",
                })

            if not intercity_segment and day.hotel and day.hotel.location:
                stops.append({
                    "kind": "hotel",
                    "name": "抵达酒店办理入住" if timed_arrival_hub else "从酒店出发",
                    "entity_name": day.hotel.name,
                    "location": day.hotel.location,
                    "duration": 20 if timed_arrival_hub else 0,
                    "reference_type": "hotel", "reference_index": None,
                })

            def add_meal(meal_type: str) -> None:
                index = meal_indices.get(meal_type)
                if index is None:
                    return
                meal = day.meals[index]
                stops.append({
                    "kind": "note" if meal.data_source == "travel_packed" else "meal", "name": meal.name, "location": meal.location,
                    "duration": 10 if meal.data_source == "travel_packed" else meal_durations.get(meal_type, 60),
                    "reference_type": "meal", "reference_index": index,
                    "meal_type": None if meal.data_source == "travel_packed" else meal_type,
                    "route_name": meal.route_anchor_name if meal.data_source == "logistics_advice" else None,
                })

            def add_attractions(items: list) -> None:
                for index, attraction in items:
                    stops.append({
                        "kind": "attraction", "name": attraction.name,
                        "location": attraction.location,
                        "duration": attraction.visit_duration,
                        "reference_type": "attraction", "reference_index": index,
                    })

            ready = self._arrival_ready_minutes(request) if day_position == 0 else None
            arrival_lunch_first = bool(
                ready is not None
                and ready >= 11 * 60
                and "lunch" in meal_indices
            )
            # 早上跨城且中午才抵达时，到达城市后应先吃午餐。
            # 过去只把该规则用在整个行程的 Day1，导致跨城日
            # 先塞入景点，再为了不让午餐过晚而误删景点。
            transfer_arrival_lunch_first = bool(
                intercity_segment
                and transfer_choice
                and not origin_attractions
                and transfer_choice["arrival_minutes"] >= 11 * 60
                and "lunch" in meal_indices
            )
            arrival_lunch_first = (
                arrival_lunch_first or transfer_arrival_lunch_first
            )
            if not intercity_segment:
                add_meal("breakfast")
            split_long_visit = bool(
                request.trip_mode == "multi_city" and not intercity_segment
                and not timed_arrival_hub and len(attractions) == 1
                and attractions[0][1].visit_duration >= 180
                and "lunch" in meal_indices
                and not self._is_remote_day(day)
            )
            if split_long_visit:
                index, attraction = attractions[0]
                first_part = min(120, attraction.visit_duration // 2)
                add_attractions(attractions)
                stops[-1]["duration"] = first_part
                add_meal("lunch")
                add_attractions(attractions)
                stops[-1]["duration"] = attraction.visit_duration - first_part
                stops[-1]["entity_name"] = f"继续游览{attraction.name}"
            elif self._is_remote_day(day) and "lunch" in meal_indices:
                # 远郊单景点通常需要较长进山时间。午餐节点放在抵达景区后、
                # 正式游览前，避免完整游览结束到下午三四点才吃午饭。
                if not lunch_before_transfer and not lunch_at_arrival_station:
                    add_meal("lunch")
                add_attractions(attractions)
            elif arrival_lunch_first:
                if not lunch_before_transfer and not lunch_at_arrival_station:
                    add_meal("lunch")
                add_attractions(attractions)
            else:
                add_attractions(morning)
                if not lunch_before_transfer:
                    add_meal("lunch")
                add_attractions(afternoon)
            add_meal("dinner")

            minimum_stops_before_return = 0 if intercity_segment else 1
            if (
                day.hotel
                and day.hotel.location
                and len(stops) > minimum_stops_before_return
            ):
                stops.append({
                    "kind": "hotel", "name": "返回酒店", "entity_name": day.hotel.name,
                    "location": day.hotel.location,
                    "duration": 20 if timed_departure_hub else 0,
                    "reference_type": "hotel", "reference_index": None,
                })
            elif timed_departure_hub and len(stops) == 1 and stops[0]["kind"] == "hotel":
                # An early departure can have no meals or visits at all. There
                # is no later "return to hotel" stop to carry checkout time,
                # so reserve it at the sole starting hotel instead.
                stops[0]["duration"] = 20

            if timed_departure_hub:
                stops.append({
                    "kind": "hub", "name": timed_departure_hub["name"],
                    "location": timed_departure_hub["location"], "duration": 0,
                    "reference_type": None, "reference_index": None,
                    "is_departure_hub": True,
                    "hub_role": "trip_departure",
                })

            current = 8 * 60 if "breakfast" in meal_indices else 9 * 60
            if timed_arrival_hub:
                arrival_clock = self._time_to_minutes(request.arrival_time)
                if arrival_clock is not None:
                    current = arrival_clock
            elif day_position == 0:
                ready = self._arrival_ready_minutes(request)
                if ready is not None:
                    current = max(current, ready)

            if request.trip_mode == "single_city":
                stops = self._arrange_single_city_timeline_stops(
                    stops, day, request, current, transport_choices,
                )

            timeline: List[TimelineItem] = []
            previous = None

            def append_timeline_stop(stop: dict) -> None:
                nonlocal current, previous
                location = stop.get("location")
                if previous and previous.get("location") and location:
                    route = self._route_choice_between_locations(
                        previous["location"], location,
                        request.transportation,
                        stop.get("route_city")
                        or day.overnight_city
                        or day.end_location
                        or request.city,
                        include_alternatives=True,
                    )
                    route = self._apply_saved_transport_choice(route, transport_choices.get((
                        self._timeline_stop_route_name(previous),
                        self._timeline_stop_route_name(stop),
                    )))
                    if route["duration_minutes"] > 0:
                        if request.trip_mode == "single_city":
                            desired = current + route["duration_minutes"]
                            target = max(desired, meal_targets.get(stop.get("meal_type"), desired))
                            if stop["kind"] == "attraction":
                                fitted = self._single_city_visit_fit_start(day.attractions[int(stop["reference_index"])], desired, int(stop["duration"]))
                                if fitted is not None:
                                    target = max(target, fitted)
                            pause = target - desired
                            if pause >= 20:
                                timeline.append(TimelineItem(
                                    kind="note", name=f"自由活动或休息（{previous.get('entity_name') or previous['name']}附近）",
                                    start_time=self._clock_text(current), end_time=self._clock_text(current + pause),
                                    duration_minutes=pause, source="planned_break",
                                ))
                                current += pause
                        route_start = current
                        current += route["duration_minutes"]
                        previous_route_name = (
                            previous.get("route_name")
                            or previous.get("entity_name")
                            or previous["name"]
                        )
                        stop_route_name = (
                            stop.get("route_name")
                            or stop.get("entity_name")
                            or stop["name"]
                        )
                        timeline.append(TimelineItem(
                            kind="transport",
                            name=f"{previous_route_name} → {stop_route_name}",
                            start_time=self._clock_text(route_start),
                            end_time=self._clock_text(current),
                            duration_minutes=route["duration_minutes"],
                            from_name=previous_route_name,
                            to_name=stop_route_name,
                            transport_mode=route["mode"],
                            distance_km=route.get("distance_km"),
                            source=route["source"],
                            customized=bool(route.get("customized")),
                            alternatives=[
                                TransportAlternative(**option)
                                for option in route.get("alternatives", [])
                            ],
                        ))

                meal_type = stop.get("meal_type")
                if meal_type in meal_targets:
                    if request.trip_mode == "single_city" and meal_targets[meal_type] - current >= 20:
                        timeline.append(TimelineItem(kind="note", name="自由活动或休息，再按时用餐", start_time=self._clock_text(current), end_time=self._clock_text(meal_targets[meal_type]), duration_minutes=meal_targets[meal_type]-current, source="planned_break"))
                    current = max(current, meal_targets[meal_type])
                if request.trip_mode in {"multi_city", "single_city"} and stop["kind"] == "attraction":
                    attraction = day.attractions[int(stop["reference_index"])]
                    fitted = (self._single_city_visit_fit_start if request.trip_mode == "single_city" else self._opening_fit_start)(attraction, current, int(stop["duration"]))
                    if fitted is not None:
                        if request.trip_mode == "single_city" and fitted - current >= 20:
                            timeline.append(TimelineItem(kind="note", name="自由活动或休息，等待适合的游览时段", start_time=self._clock_text(current), end_time=self._clock_text(fitted), duration_minutes=fitted-current, source="planned_break"))
                        current = max(current, fitted)
                start = current
                current += int(stop["duration"] or 0)
                timeline.append(TimelineItem(
                    kind=stop["kind"],
                    name=stop.get("entity_name") or stop["name"],
                    start_time=self._clock_text(start),
                    end_time=self._clock_text(current) if stop["duration"] else None,
                    duration_minutes=int(stop["duration"] or 0),
                    reference_type=stop.get("reference_type"),
                    reference_index=stop.get("reference_index"),
                    meal_type=meal_type,
                    hub_role=stop.get("hub_role"),
                ))
                # 极少数兼容数据可能缺餐厅坐标；此时保留最近一个有坐标的
                # 地点作为下一段起点，不生成虚假的零坐标路线。
                if location:
                    previous = stop

            if intercity_segment and transfer_choice:
                origin_name = (
                    previous_day_hotel.name
                    if previous_day_hotel else f"{intercity_segment.origin}住宿地"
                )
                origin_location = (
                    previous_day_hotel.location if previous_day_hotel else None
                )
                append_timeline_stop({
                    "kind": "hotel",
                    "name": origin_name,
                    "entity_name": origin_name,
                    "location": origin_location,
                    "duration": 0,
                    "reference_type": None,
                    "reference_index": None,
                    "route_city": intercity_segment.origin,
                })

                breakfast_index = meal_indices.get("breakfast")
                if breakfast_index is not None:
                    existing_breakfast = day.meals[breakfast_index]
                    keep_breakfast = bool(
                        request.trip_mode == "multi_city" and existing_breakfast.poi_id
                        and existing_breakfast.location and origin_location
                        and self._haversine_distance(
                            existing_breakfast.location.longitude, existing_breakfast.location.latitude,
                            origin_location.longitude, origin_location.latitude,
                        ) <= 3.5
                    )
                    breakfast = existing_breakfast if keep_breakfast else existing_breakfast.model_copy(update={
                        "name": f"{intercity_segment.origin}酒店早餐或附近早餐",
                        "address": (
                            previous_day_hotel.address if previous_day_hotel else None
                        ),
                        "location": origin_location,
                        "description": (
                            "跨城移动日应先在上一座城市解决早餐，再继续游览或前往下一站。"
                        ),
                        "estimated_cost": max(
                            15, day.meals[breakfast_index].estimated_cost or 0
                        ),
                        "poi_id": None,
                        "recommended_dish": None,
                        "rating": None,
                        "data_source": "logistics_advice",
                        "opening_hours": "",
                        "opening_hours_verified": True,
                        "route_anchor_name": origin_name,
                        "route_anchor_poi_id": None,
                    })
                    day.meals[breakfast_index] = breakfast
                    append_timeline_stop({
                        "kind": "meal",
                        "name": breakfast.name,
                        "location": breakfast.location,
                        "duration": meal_durations["breakfast"],
                        "reference_type": "meal",
                        "reference_index": breakfast_index,
                        "meal_type": "breakfast",
                        "route_city": intercity_segment.origin,
                        # The synthetic breakfast is physically at the hotel;
                        # subsequent transport should say hotel → station.
                        "route_name": origin_name if breakfast.data_source == "logistics_advice" else None,
                    })

                for attraction_index, attraction in origin_attractions:
                    append_timeline_stop({
                        "kind": "attraction",
                        "name": attraction.name,
                        "location": attraction.location,
                        "duration": attraction.visit_duration,
                        "reference_type": "attraction",
                        "reference_index": attraction_index,
                        "route_city": intercity_segment.origin,
                    })

                if lunch_before_transfer and "lunch" in meal_indices:
                    lunch_index = meal_indices["lunch"]
                    lunch_location = (
                        origin_attractions[-1][1].location
                        if origin_attractions else origin_location
                    )
                    existing_lunch = day.meals[lunch_index]
                    keep_lunch = bool(
                        request.trip_mode == "multi_city" and existing_lunch.poi_id
                        and existing_lunch.location and lunch_location
                        and self._haversine_distance(
                            existing_lunch.location.longitude, existing_lunch.location.latitude,
                            lunch_location.longitude, lunch_location.latitude,
                        ) <= 3.5
                    )
                    lunch = existing_lunch if keep_lunch else existing_lunch.model_copy(update={
                        "name": f"{intercity_segment.origin}行程附近午餐（现场选择）",
                        "address": None,
                        "location": lunch_location,
                        "description": (
                            "午后跨城前预留午餐时间，请在上午景点或出发酒店附近选择。"
                        ),
                        "estimated_cost": max(
                            30, day.meals[lunch_index].estimated_cost or 0
                        ),
                        "poi_id": None,
                        "recommended_dish": None,
                        "rating": None,
                        "data_source": "logistics_advice",
                        "opening_hours": "",
                        "opening_hours_verified": True,
                        "route_anchor_name": origin_attractions[-1][1].name if origin_attractions else origin_name,
                        "route_anchor_poi_id": origin_attractions[-1][1].poi_id if origin_attractions else None,
                    })
                    day.meals[lunch_index] = lunch
                    append_timeline_stop({
                        "kind": "meal",
                        "name": lunch.name,
                        "location": lunch.location,
                        "duration": meal_durations["lunch"],
                        "reference_type": "meal",
                        "reference_index": lunch_index,
                        "meal_type": "lunch",
                        "route_city": intercity_segment.origin,
                    })

                if origin_attractions or lunch_before_transfer:
                    append_timeline_stop({
                        "kind": "hotel",
                        "name": f"返回{origin_name}取行李并退房",
                        "entity_name": f"返回{origin_name}取行李并退房",
                        "route_name": origin_name,
                        "location": origin_location,
                        "duration": 20,
                        "reference_type": None,
                        "reference_index": None,
                        "route_city": intercity_segment.origin,
                    })

                transfer_start = max(
                    current, transfer_choice["departure_minutes"]
                )
                transfer_duration = max(0, intercity_segment.duration_minutes)
                # 前面的景点、午餐和取行李可能使真实出发时间晚于
                # 候选窗口。多城市模式下必须用真实出发时间再查一次，
                # 不能沿用 13:30 的耗时去声称 14:40 出发。
                if (
                    request.trip_mode == "multi_city"
                    and abs(
                        transfer_start
                        - transfer_choice["departure_minutes"]
                    ) >= 20
                    and previous_day_hotel
                    and previous_day_hotel.location
                    and day.hotel
                    and day.hotel.location
                    and "自驾" not in intercity_segment.transport_mode
                    and "飞机" not in intercity_segment.transport_mode
                ):
                    refreshed = get_amap_service().get_intercity_transit_summary(
                        (
                            f"{previous_day_hotel.location.longitude},"
                            f"{previous_day_hotel.location.latitude}"
                        ),
                        (
                            f"{day.hotel.location.longitude},"
                            f"{day.hotel.location.latitude}"
                        ),
                        intercity_segment.origin,
                        intercity_segment.destination,
                        day.date,
                        self._clock_text(transfer_start),
                    )
                    refreshed_is_usable = bool(
                        refreshed
                        and refreshed.get("duration_minutes")
                        and (
                            not self._expects_rail_transfer(intercity_segment)
                            or self._usable_railway_summary(
                                refreshed.get("railway"),
                                intercity_segment.distance_km,
                            )
                        )
                    )
                    if refreshed_is_usable:
                        transfer_duration = int(refreshed["duration_minutes"])
                        transfer_choice["railway"] = refreshed.get("railway")
                        intercity_segment.duration_minutes = transfer_duration
                        if refreshed.get("distance_km"):
                            intercity_segment.distance_km = refreshed["distance_km"]
                        intercity_segment.source = refreshed.get("source", "amap")
                        intercity_segment.timing_source = intercity_segment.source
                    elif refreshed and self._expects_rail_transfer(intercity_segment):
                        logger.warning(
                            f"忽略缺少铁路段的高德刷新结果: "
                            f"{intercity_segment.origin}→{intercity_segment.destination} "
                            f"amap_minutes={refreshed.get('duration_minutes')}"
                        )
                railway = transfer_choice.get("railway") or {}
                departure_rail_hub = railway.get("departure_hub") or {}
                arrival_rail_hub = railway.get("arrival_hub") or {}
                departure_rail_location = self._railway_hub_location(
                    departure_rail_hub
                )
                arrival_rail_location = self._railway_hub_location(
                    arrival_rail_hub
                )
                railway_duration = int(railway.get("duration_minutes", 0) or 0)
                can_split_railway = bool(
                    railway_duration > 0
                    and departure_rail_hub.get("name")
                    and arrival_rail_hub.get("name")
                    and departure_rail_location
                    and arrival_rail_location
                    and (
                        "高铁" in intercity_segment.transport_mode
                        or "火车" in intercity_segment.transport_mode
                        or "动车" in intercity_segment.transport_mode
                        or "铁路" in intercity_segment.transport_mode
                    )
                )

                if can_split_railway:
                    # AMap's integrated duration is door-to-door. Build the
                    # understandable legs explicitly instead of labelling that
                    # entire total as a high-speed train journey.
                    current = transfer_start
                    journey_start = current
                    departure_hub_name = str(departure_rail_hub["name"])
                    arrival_hub_name = str(arrival_rail_hub["name"])
                    append_timeline_stop({
                        "kind": "hub",
                        "name": departure_hub_name,
                        "entity_name": departure_hub_name,
                        "location": departure_rail_location,
                        "duration": 0,
                        "reference_type": None,
                        "reference_index": None,
                        "route_city": intercity_segment.origin,
                        "hub_role": "intercity_departure",
                    })

                    station_arrival = current
                    station_access_minutes = max(0, station_arrival - journey_start)
                    scheduled_rail_departure = self._time_to_minutes(
                        railway.get("departure_time")
                    )
                    if (
                        scheduled_rail_departure is not None
                        and scheduled_rail_departure >= station_arrival + 10
                    ):
                        rail_start = scheduled_rail_departure
                    else:
                        rail_start = station_arrival + 45
                    wait_minutes = max(10, rail_start - station_arrival)
                    departure_hub_item = timeline[-1]
                    departure_hub_item.start_time = self._clock_text(station_arrival)
                    departure_hub_item.end_time = self._clock_text(rail_start)
                    departure_hub_item.duration_minutes = wait_minutes
                    current = rail_start

                    rail_end = current + railway_duration
                    timeline.append(TimelineItem(
                        kind="transport",
                        name=f"{departure_hub_name} → {arrival_hub_name}",
                        start_time=self._clock_text(current),
                        end_time=self._clock_text(rail_end),
                        duration_minutes=railway_duration,
                        from_name=departure_hub_name,
                        to_name=arrival_hub_name,
                        transport_mode=self._railway_timeline_label(
                            railway, intercity_segment.transport_mode
                        ),
                        distance_km=railway.get("distance_km"),
                        source=railway.get("source") or transfer_choice.get("source"),
                        is_intercity=True,
                    ))
                    current = rail_end

                    exit_minutes = self._terminal_buffer_minutes(
                        "高铁", arrival=True
                    )
                    timeline.append(TimelineItem(
                        kind="hub",
                        name=arrival_hub_name,
                        start_time=self._clock_text(current),
                        end_time=self._clock_text(current + exit_minutes),
                        duration_minutes=exit_minutes,
                        hub_role="intercity_arrival",
                    ))
                    current += exit_minutes
                    destination_departure = current
                    previous = {
                        "kind": "hub",
                        "name": arrival_hub_name,
                        "entity_name": arrival_hub_name,
                        "location": arrival_rail_location,
                        "route_city": intercity_segment.destination,
                    }

                    transfer_meal_minutes = 0
                    if lunch_at_arrival_station:
                        lunch_index = meal_indices["lunch"]
                        existing_lunch = day.meals[lunch_index]
                        keep_station_lunch = bool(
                            request.trip_mode == "multi_city" and existing_lunch.poi_id
                            and existing_lunch.location and arrival_rail_location
                            and self._haversine_distance(
                                existing_lunch.location.longitude, existing_lunch.location.latitude,
                                arrival_rail_location.longitude, arrival_rail_location.latitude,
                            ) <= 1.5
                        )
                        lunch = existing_lunch if keep_station_lunch else existing_lunch.model_copy(update={
                            "name": f"{arrival_hub_name}内或附近简餐",
                            "address": None,
                            "location": arrival_rail_location,
                            "description": (
                                "跨城抵达酒店会超过正常午餐时段，"
                                "因此在到达车站后先安排简餐。"
                            ),
                            "estimated_cost": max(
                                30, day.meals[lunch_index].estimated_cost or 0
                            ),
                            "poi_id": None,
                            "recommended_dish": None,
                            "rating": None,
                            "data_source": "logistics_advice",
                            "opening_hours": "",
                            "opening_hours_verified": True,
                            "route_anchor_name": arrival_hub_name,
                            "route_anchor_poi_id": None,
                        })
                        day.meals[lunch_index] = lunch
                        append_timeline_stop({
                            "kind": "meal",
                            "name": lunch.name,
                            "location": lunch.location,
                            "duration": meal_durations["lunch"],
                            "reference_type": "meal",
                            "reference_index": lunch_index,
                            "meal_type": "lunch",
                            "route_city": intercity_segment.destination,
                        })
                        transfer_meal_minutes = meal_durations["lunch"]

                    hotel_arrival = current
                    if day.hotel:
                        append_timeline_stop({
                            "kind": "hotel",
                            "name": day.hotel.name,
                            "entity_name": day.hotel.name,
                            "location": day.hotel.location,
                            "duration": 20,
                            "reference_type": "hotel",
                            "reference_index": None,
                            "route_city": intercity_segment.destination,
                        })
                        hotel_arrival_item = timeline[-1]
                        parsed_hotel_arrival = self._time_to_minutes(
                            hotel_arrival_item.start_time
                        )
                        if parsed_hotel_arrival is not None:
                            hotel_arrival = parsed_hotel_arrival
                    door_to_door_duration = max(
                        0,
                        hotel_arrival - journey_start - transfer_meal_minutes,
                    )
                    station_egress_minutes = max(
                        0,
                        hotel_arrival - destination_departure - transfer_meal_minutes,
                    )
                    intercity_segment.duration_minutes = door_to_door_duration
                    intercity_segment.suggested_departure_time = self._clock_text(
                        journey_start
                    )
                    intercity_segment.suggested_arrival_time = self._clock_text(
                        hotel_arrival
                    )
                    rail_source = str(
                        railway.get("source")
                        or transfer_choice.get("source")
                        or "estimated"
                    )
                    intercity_segment.source = rail_source
                    intercity_segment.timing_source = rail_source
                    logger.info(
                        f"跨城分段 Day{day.day_index + 1}: "
                        f"{origin_name}→{departure_hub_name} "
                        f"{station_access_minutes}分钟 / "
                        f"候车{wait_minutes}分钟 / "
                        f"{departure_hub_name}→{arrival_hub_name} "
                        f"{railway_duration}分钟 / "
                        f"出站{exit_minutes}分钟 / "
                        f"{arrival_hub_name}→{day.hotel.name if day.hotel else intercity_segment.destination} "
                        f"{station_egress_minutes}分钟; "
                        f"门到门{door_to_door_duration}分钟"
                    )
                else:
                    current = transfer_start + transfer_duration
                    intercity_segment.suggested_departure_time = self._clock_text(
                        transfer_start
                    )
                    intercity_segment.suggested_arrival_time = self._clock_text(current)
                    display_mode = intercity_segment.transport_mode
                    if (
                        intercity_segment.source == "amap"
                        and "自驾" not in display_mode
                        and "飞机" not in display_mode
                    ):
                        display_mode = "跨城公共交通"
                    timeline.append(TimelineItem(
                        kind="transport",
                        name=(
                            f"{intercity_segment.origin} → "
                            f"{intercity_segment.destination}"
                        ),
                        start_time=intercity_segment.suggested_departure_time,
                        end_time=intercity_segment.suggested_arrival_time,
                        duration_minutes=transfer_duration,
                        from_name=intercity_segment.origin,
                        to_name=intercity_segment.destination,
                        transport_mode=display_mode,
                        distance_km=intercity_segment.distance_km or None,
                        source=intercity_segment.source,
                        is_intercity=True,
                    ))
                    previous = None

                    if day.hotel:
                        append_timeline_stop({
                            "kind": "hotel",
                            "name": day.hotel.name,
                            "entity_name": day.hotel.name,
                            "location": day.hotel.location,
                            "duration": 20,
                            "reference_type": "hotel",
                            "reference_index": None,
                        })

            for stop in stops:
                append_timeline_stop(stop)

            # 离开时间是列车/航班发车时刻，不是从酒店出发时刻。整条离开日
            # 时间轴应当从“最晚到站时间”向前平移，而不是在最后一段用 max
            # 把已经超时的 current 继续向后推。
            if timed_departure_hub and request.departure_time:
                departure_clock = self._time_to_minutes(request.departure_time)
                hub_item = next(
                    (item for item in reversed(timeline) if item.kind == "hub"), None
                )
                hub_start = self._time_to_minutes(hub_item.start_time) if hub_item else None
                if departure_clock is not None and hub_start is not None:
                    latest_hub_arrival = departure_clock - self._terminal_buffer_minutes(
                        request.departure_transport or "", arrival=False
                    )
                    lateness = max(0, hub_start - latest_hub_arrival)
                    timed = [
                        self._time_to_minutes(item.start_time)
                        for item in timeline if item.start_time
                    ]
                    earliest = min((value for value in timed if value is not None), default=8 * 60)
                    # An early departure may legitimately require a pre-05:00
                    # hotel start. Bound by the calendar day and explicit arrival
                    # / current-time constraints, not an arbitrary morning clock.
                    earliest_allowed = 6 * 60 if request.trip_mode == "multi_city" else 0
                    if day_position == 0:
                        first_day_boundary = (
                            self._time_to_minutes(request.arrival_time)
                            if timed_arrival_hub else self._arrival_ready_minutes(request)
                        )
                        if first_day_boundary is not None:
                            earliest_allowed = max(earliest_allowed, first_day_boundary)
                    shift = min(lateness, max(0, earliest - earliest_allowed))
                    if request.trip_mode == "single_city":
                        # 不能为了到站而把固定饭点或夜游整体平移到清晨。
                        for node in timeline:
                            floor = {"lunch": 11 * 60, "dinner": 17 * 60}.get(node.meal_type)
                            if node.kind == "attraction" and node.reference_index is not None:
                                attr = day.attractions[int(node.reference_index)]
                                if self._is_night_visit(attr):
                                    floor = 18 * 60
                            if floor is not None:
                                shift = min(shift, max(0, (self._time_to_minutes(node.start_time) or 0) - floor))
                    if shift:
                        for item in timeline:
                            start_minutes = self._time_to_minutes(item.start_time)
                            end_minutes = self._time_to_minutes(item.end_time)
                            if start_minutes is not None:
                                item.start_time = self._clock_text(start_minutes - shift)
                            if end_minutes is not None:
                                item.end_time = self._clock_text(end_minutes - shift)

            # Do not silently discard a user-selected endpoint just because no
            # boundary time was supplied. These untimed cards deliberately have
            # no location-driven transport leg: calculating one here would imply
            # a fabricated arrival/departure clock and distort the day schedule.
            if arrival_hub and not request.arrival_time:
                timeline.insert(0, TimelineItem(
                    kind="hub",
                    name=arrival_hub["name"],
                    hub_role="trip_arrival",
                    source="user_selected_untimed",
                ))
            if departure_hub and not request.departure_time:
                timeline.append(TimelineItem(
                    kind="hub",
                    name=departure_hub["name"],
                    hub_role="trip_departure",
                    source="user_selected_untimed",
                ))

            day.timeline = timeline
            logger.info(
                f"时间轴 Day{day.day_index + 1}: {len(timeline)}个节点，"
                f"市内交通偏好={request.transportation}"
            )
        return trip_plan

    @classmethod
    def _opening_hours_cover_period(
        cls, opening_hours: str, start_minutes: int, end_minutes: int
    ) -> Optional[bool]:
        """营业时间是否完整覆盖实际到店时段。

        None 表示地图未提供可解析的时间；False 表示已知不营业。
        """
        intervals = cls._opening_intervals(opening_hours)
        if not intervals:
            return None
        if intervals == [(-1, -1)]:
            return False
        for start, end in intervals:
            adjusted_start = start_minutes
            adjusted_end = end_minutes
            if end > 24 * 60 and adjusted_start < start:
                adjusted_start += 24 * 60
                adjusted_end += 24 * 60
            if start <= adjusted_start and adjusted_end <= end:
                return True
        return False

    @staticmethod
    def _meal_from_candidate(candidate: dict, meal_type: str) -> Meal:
        """将已核验的高德候选转换为餐饮节点。"""
        lng = float(candidate.get("longitude", 0) or 0)
        lat = float(candidate.get("latitude", 0) or 0)
        location = Location(longitude=lng, latitude=lat) if lng and lat else None
        raw_cost = candidate.get("estimated_cost", 0) or 0
        try:
            cost = int(float(raw_cost))
        except (TypeError, ValueError):
            cost = 0
        if not 1 <= cost <= 2000:
            cost = {"breakfast": 20, "lunch": 50, "dinner": 80}.get(meal_type, 40)
        raw_rating = candidate.get("rating")
        try:
            rating = float(raw_rating) if raw_rating not in (None, "", [], {}) else None
        except (TypeError, ValueError):
            rating = None
        dish_options = candidate.get("dish_matches") or candidate.get("dish_options") or []
        return Meal(
            type=meal_type,
            name=str(candidate.get("name", "") or "附近餐厅"),
            address=candidate.get("address") or None,
            location=location,
            description="已按实际到店时段核验营业时间",
            estimated_cost=cost,
            poi_id=candidate.get("id") or None,
            recommended_dish=dish_options[0] if dish_options else None,
            rating=rating,
            data_source="amap",
            opening_hours=str(candidate.get("opening_hours", "") or ""),
            opening_hours_verified=True,
        )

    def _replace_closed_meal(
        self,
        day: DayPlan,
        meal_index: int,
        meal_type: str,
        start_minutes: int,
        end_minutes: int,
        candidate_day: Optional[dict],
    ) -> bool:
        """优先用同餐次中实际时段营业的真实 POI 替换。"""
        current_poi_id = day.meals[meal_index].poi_id
        candidates = (candidate_day or {}).get("meals", {}).get(meal_type, [])
        for candidate in candidates:
            if candidate.get("id") == current_poi_id:
                continue
            if not self._is_valid_meal_candidate(candidate, meal_type, actual_period=(start_minutes, end_minutes)):
                continue
            if self._opening_hours_cover_period(
                str(candidate.get("opening_hours", "") or ""),
                start_minutes,
                end_minutes,
            ) is not True:
                continue
            day.meals[meal_index] = self._meal_from_candidate(candidate, meal_type)
            logger.warning(
                f"营业时间修复 Day{day.day_index + 1}: "
                f"已替换 {meal_type} 为 '{day.meals[meal_index].name}'"
            )
            return True
        removed = day.meals.pop(meal_index)
        logger.warning(
            f"营业时间修复 Day{day.day_index + 1}: "
            f"移除已知未营业的 '{removed.name}'"
        )
        return True

    def _nearby_final_meal(
        self, day: DayPlan, meal: Meal, city: str,
        start: int, end: int, candidate_day: Optional[dict] = None,
    ) -> Optional[Meal]:
        """围绕最终用餐区域补搜；每个区域最多两次通用搜索，不再次调用模型。"""
        if not meal.location:
            return None
        catalog = getattr(self, "_local_dish_catalogs", {}).get(city, (candidate_day or {}).get("dish_catalog", {}))
        dishes = catalog.get(meal.type, []) if isinstance(catalog, dict) else []
        cache = getattr(self, "_final_meal_search_cache", {})
        self._final_meal_search_cache = cache
        key = (city, meal.type, round(meal.location.longitude, 4), round(meal.location.latitude, 4))
        if key not in cache:
            keyword = "早餐" if meal.type == "breakfast" else "餐厅"
            results = []
            for radius in (1500, 3500):
                try:
                    results.extend(self._call_amap_around_search(
                        meal.location.longitude, meal.location.latitude,
                        radius=radius, keywords=keyword,
                    ))
                except Exception as error:
                    logger.warning(f"最终餐饮补搜失败 {city} {meal.type}: {error}")
                if any(self._is_valid_meal_candidate(p, meal.type, actual_period=(start, end)) for p in results):
                    break
            cache[key] = results
        pool = [*(candidate_day or {}).get("meals", {}).get(meal.type, []), *cache[key]]
        used = {m.poi_id for m in day.meals if m is not meal and m.poi_id}
        rejected = getattr(self, "_rejected_final_meal_ids", {}).get((day.day_index, meal.type), set())
        ranked = []
        seen = set()
        for poi in pool:
            pid = poi.get("id")
            if not pid or pid in seen or pid in used or pid in rejected:
                continue
            seen.add(pid)
            if not self._is_valid_meal_candidate(poi, meal.type, actual_period=(start, end)):
                continue
            try:
                distance = self._haversine_distance(
                    meal.location.longitude, meal.location.latitude,
                    float(poi.get("longitude", 0)), float(poi.get("latitude", 0)),
                )
            except (TypeError, ValueError):
                continue
            if not math.isfinite(distance) or distance > 3.5:
                continue
            matches = self._match_dishes(poi, dishes)
            score = self._score_nearby_meal(poi, city, dishes)
            if score < 0:
                continue
            selected = {**poi, "dish_matches": matches, "dish_options": matches}
            local_tier = 2 if matches else (0 if any(word in poi.get("name", "") for word in ("兰州", "沙县", "擂饭", "新疆烧烤")) else 1)
            ranked.append((bool(poi.get("_meal_open_verified")), local_tier if getattr(self, "_prefer_local_meals", False) else 0, score - distance * 2, selected))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[:3], reverse=True)
        replacement = self._meal_from_candidate(ranked[0][3], meal.type)
        replacement.route_anchor_name = meal.route_anchor_name
        replacement.route_anchor_poi_id = meal.route_anchor_poi_id
        replacement.description = f"在{meal.route_anchor_name or city}附近重新检索的真实餐厅；出发前请确认营业。"
        return replacement

    def _sync_single_city_meal_anchors(self, trip_plan: TripPlan) -> None:
        """只修复明确失去服务景点的餐饮；不挪动用户指定门店。"""
        for day in trip_plan.days:
            valid_ids = {a.poi_id for a in day.attractions if a.poi_id}
            valid_names = {a.name for a in day.attractions}
            for index, meal in enumerate(day.meals):
                if meal.data_source == "user" or meal.type == "breakfast":
                    continue
                stale = bool(meal.route_anchor_poi_id and meal.route_anchor_poi_id not in valid_ids)
                stale = stale or bool(meal.route_anchor_name and meal.route_anchor_name not in valid_names and any(r.attraction.name == meal.route_anchor_name for r in trip_plan.removed_attractions))
                if not stale:
                    continue
                anchor = self._meal_anchor(day, meal.type)
                if meal.type == "dinner" and day.hotel and day.hotel.location:
                    anchor = (day.hotel.location.longitude, day.hotel.location.latitude, day.hotel.name)
                if not anchor:
                    continue
                location = Location(longitude=anchor[0], latitude=anchor[1])
                distance = self._haversine_distance(location.longitude, location.latitude, meal.location.longitude, meal.location.latitude) if meal.location else float("inf")
                if meal.data_source == "logistics_advice" or distance > 2.5:
                    self._set_area_meal(day, index, location, anchor[2], "原关联景点已移出或换日，按剩余路线重新选择餐厅。")
                else:
                    meal.route_anchor_name = anchor[2]
                    meal.route_anchor_poi_id = next((a.poi_id for a in day.attractions if a.name == anchor[2]), None)

    def _sync_multi_city_meal_anchors(self, trip_plan: TripPlan, request: TripRequest) -> None:
        """景点移除/换日后，清理失去服务对象的餐饮坐标，兼容旧行程。"""
        if request.trip_mode != "multi_city":
            return
        for position, day in enumerate(trip_plan.days):
            valid_names = {a.name for a in day.attractions}
            valid_ids = {a.poi_id for a in day.attractions if a.poi_id}
            removed = [r.attraction for r in trip_plan.removed_attractions if r.original_day_index == day.day_index]
            for meal in day.meals:
                if meal.data_source == "user":
                    continue
                stale = bool(meal.route_anchor_poi_id and meal.route_anchor_poi_id not in valid_ids)
                if not stale and meal.data_source == "logistics_advice" and meal.location:
                    stale = any(
                        a.name not in valid_names and (
                            a.name == meal.route_anchor_name
                            or a.name in (meal.description or "")
                            or self._haversine_distance(
                                meal.location.longitude, meal.location.latitude,
                                a.location.longitude, a.location.latitude,
                            ) < 0.1
                        ) for a in removed
                    )
                # 跨城早餐服务的是上一城住宿，不能因为本日酒店在下一城而重定位。
                origin_hotel = trip_plan.days[position - 1].hotel if position > 0 and day.route_segment else None
                if meal.type == "breakfast" and origin_hotel:
                    anchor = (origin_hotel.location.longitude, origin_hotel.location.latitude, origin_hotel.name) if origin_hotel.location else None
                elif meal.type == "dinner" and day.hotel and day.hotel.location:
                    anchor = (day.hotel.location.longitude, day.hotel.location.latitude, day.hotel.name)
                else:
                    anchor = self._meal_anchor(day, meal.type)
                if stale:
                    if anchor:
                        location = Location(longitude=anchor[0], latitude=anchor[1])
                        # 邻近的真实餐厅可继续服务新路线；远处商户必须重新选址。
                        near = bool(meal.location and self._haversine_distance(
                            location.longitude, location.latitude,
                            meal.location.longitude, meal.location.latitude,
                        ) <= 2.5)
                    else:
                        location, near = None, False
                    if meal.data_source == "logistics_advice" or not near:
                        label = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}.get(meal.type, "用餐")
                        meal.name = f"{anchor[2] if anchor else (day.overnight_city or request.city)}附近{label}（现场选择）"
                        meal.location = location
                        meal.poi_id = None
                        meal.address = None
                        meal.rating = None
                        meal.recommended_dish = None
                        meal.data_source = "logistics_advice"
                        meal.opening_hours = ""
                        meal.opening_hours_verified = True
                        meal.description = "原关联景点已移出，已按剩余行程重新定位用餐区域，不再前往原景区。"
                    meal.route_anchor_name = anchor[2] if anchor else None
                    meal.route_anchor_poi_id = next((a.poi_id for a in day.attractions if anchor and a.name == anchor[2]), None)
                    if meal.data_source == "amap" and anchor:
                        meal.description = f"原关联景点已移出；餐厅仍在{anchor[2]}附近，沿用邻近推荐。"
                elif meal.data_source == "logistics_advice" and not meal.route_anchor_name and anchor:
                    meal.route_anchor_name = anchor[2]
                    meal.route_anchor_poi_id = next((a.poi_id for a in day.attractions if a.name == anchor[2]), None)
                if meal.data_source == "logistics_advice" and meal.route_anchor_name:
                    if meal.name in {"景区内用餐或自备简餐", "行程附近午餐（现场选择）", "行程附近晚餐（现场选择）", "酒店早餐或附近早餐店", "返回酒店后在附近用餐"}:
                        label = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}.get(meal.type, "用餐")
                        meal.name = f"{meal.route_anchor_name}附近{label}（现场选择）"
                    if meal.route_anchor_name not in (meal.description or ""):
                        meal.description = f"用餐区域：{meal.route_anchor_name}附近。{meal.description or ''}"

    def _resolve_flexible_multi_city_meals(
        self, trip_plan: TripPlan, request: TripRequest, candidate_days: dict,
        day_position: Optional[int] = None,
    ) -> bool:
        if request.trip_mode not in {"multi_city", "single_city"}:
            return False
        changed = False
        for position, day in enumerate(trip_plan.days):
            if day_position is not None and position != day_position:
                continue
            for item in day.timeline:
                if item.kind != "meal" or item.reference_index is None:
                    continue
                meal = day.meals[int(item.reference_index)]
                if meal.data_source != "logistics_advice":
                    continue
                start = self._time_to_minutes(item.start_time)
                end = self._time_to_minutes(item.end_time)
                if start is None or end is None:
                    continue
                # 已经错过正常餐段时，先修路线而不是继续遍历营业中的商户。
                if (meal.type == "lunch" and start > 14 * 60) or (meal.type == "dinner" and start > 20 * 60 + 30):
                    continue
                if len(getattr(self, "_rejected_final_meal_ids", {}).get((day.day_index, meal.type), set())) >= 2:
                    continue
                city = day.overnight_city or day.end_location or request.city
                if day.route_segment and (meal.type == "breakfast" or any(
                    a.name == meal.route_anchor_name and self._attraction_route_side(a, day.route_segment) == "origin"
                    for a in day.attractions
                )):
                    city = day.route_segment.origin
                replacement = self._nearby_final_meal(day, meal, city, start, end, candidate_days.get(day.day_index))
                if replacement:
                    before_cost = self._timeline_hard_cost(day, request, position) if request.trip_mode == "single_city" else 0
                    before_timeline = list(day.timeline)
                    day.meals[int(item.reference_index)] = replacement
                    if request.trip_mode == "single_city":
                        self._build_daily_timelines(trip_plan, request, {position})
                        before_travel = sum(n.duration_minutes for n in before_timeline if n.kind == "transport")
                        after_travel = sum(n.duration_minutes for n in day.timeline if n.kind == "transport")
                        if self._timeline_hard_cost(day, request, position) > before_cost or after_travel - before_travel > 45:
                            day.meals[int(item.reference_index)] = meal
                            day.timeline = before_timeline
                            if replacement.poi_id:
                                self._rejected_final_meal_ids.setdefault((day.day_index, meal.type), set()).add(replacement.poi_id)
                            changed = True
                            continue
                    logger.info(f"最终餐饮补位 Day{day.day_index + 1}: {meal.name} → {replacement.name}")
                    changed = True
        return changed

    def _resolve_post_trim_meals(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        candidates_by_day: dict,
    ) -> None:
        """景点最终换日/裁剪后，再把失效的餐饮锚点落成真实门店。"""
        self._build_daily_timelines(trip_plan, request)
        self._repair_meal_recommendation_quality(trip_plan, request)
        self._build_daily_timelines(trip_plan, request)
        for position in range(len(trip_plan.days)):
            # 一家候选可能因营业时间或新增绕行被拒绝，允许继续尝试下一家，
            # 但保持小的确定性上限，避免外部 POI 数据异常造成循环。
            for _ in range(4):
                if not self._resolve_flexible_multi_city_meals(
                    trip_plan, request, candidates_by_day, position,
                ):
                    break
        self._build_daily_timelines(trip_plan, request)

    def _restore_missing_multi_city_meals(
        self, trip_plan: TripPlan, request: TripRequest
    ) -> None:
        """Keep logistics meals after late/opening-hour repairs remove a POI."""
        if request.trip_mode not in {"multi_city", "single_city"}:
            return
        defaults = {
            "breakfast": ("酒店早餐或附近早餐店", 20),
            "lunch": ("行程附近午餐（现场选择）", 40),
            "dinner": ("返回酒店后在附近用餐", 70),
        }
        for day in trip_plan.days:
            expected = self._available_meal_types(request, day.day_index)
            if request.trip_mode == "single_city" and day.day_index == 0:
                arrival_hotel = next((n for n in day.timeline if n.kind == "hotel" and n.end_time), None)
                ready = self._time_to_minutes(arrival_hotel.end_time) if arrival_hotel else None
                if ready is not None and ready > 840:
                    expected = tuple(kind for kind in expected if kind != "lunch")
            existing = {meal.type for meal in day.meals}
            for meal_type in expected:
                if meal_type in existing:
                    continue
                label, cost = defaults[meal_type]
                if meal_type == "breakfast" and day is trip_plan.days[-1] and request.departure_time and (self._time_to_minutes(request.departure_time) or 1440) <= 810:
                    label = "酒店早餐或提前打包早餐"
                anchor = self._meal_anchor(day, meal_type)
                if meal_type in {"breakfast", "dinner"} and day.hotel:
                    location = day.hotel.location
                else:
                    location = (
                        Location(longitude=anchor[0], latitude=anchor[1])
                        if anchor else (day.hotel.location if day.hotel else None)
                    )
                # Missing meals at this stage normally mean that the original
                # restaurant conflicted with the final route or opening hours.
                # Restore only a logistics slot; never rebind the failed POI.
                day.meals.append(Meal(
                    type=meal_type,
                    name=label,
                    address=day.hotel.address if day.hotel else None,
                    location=location,
                    description=(
                        "原餐厅与最终交通或营业时间冲突，"
                        "时间轴保留用餐时段，请在相邻地点现场选择。"
                    ),
                    estimated_cost=cost,
                    data_source="logistics_advice",
                    opening_hours="",
                    opening_hours_verified=True,
                ))
                logger.warning(
                    f"餐次兜底 Day{day.day_index + 1}: 补回{meal_type}时间"
                )
            order = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}
            day.meals.sort(key=lambda meal: order.get(meal.type, 9))

    def _relocate_arrival_day_conflict(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        attraction_index: int,
    ) -> str:
        """移动落地日的系统景点；无合适日期时将其保留为编辑页备选。"""
        if not trip_plan.days or not 0 <= attraction_index < len(
            trip_plan.days[0].attractions
        ):
            return "unchanged"

        source_day = trip_plan.days[0]
        attraction = source_day.attractions[attraction_index]
        if attraction.user_locked:
            return "locked"

        source_city = str(
            source_day.overnight_city
            or source_day.end_location
            or request.city
            or ""
        ).strip()
        candidates = []
        for position, target_day in enumerate(trip_plan.days[1:], start=1):
            if target_day.is_transit_day or target_day.route_segment:
                continue
            if request.trip_mode == "multi_city":
                target_city = str(
                    target_day.overnight_city
                    or target_day.end_location
                    or ""
                ).strip()
                if not source_city or not target_city or (
                    source_city not in target_city and target_city not in source_city
                ):
                    continue
            if any(
                self._normalize_attr_name(item.name)
                == self._normalize_attr_name(attraction.name)
                for item in target_day.attractions
            ):
                continue

            capacity = self._day_visit_capacity(request, target_day, position)
            current_visit = sum(
                max(60, int(item.visit_duration or 0))
                for item in target_day.attractions
            )
            # 新增景点至少会增加一段接驳；已有景点越多，路线余量应越保守。
            transfer_allowance = 45 * (len(target_day.attractions) + 1)
            required = (
                current_visit
                + max(60, int(attraction.visit_duration or 0))
                + transfer_allowance
            )
            remaining = capacity - required
            if remaining >= 0:
                candidates.append((position, remaining))

        moved = source_day.attractions.pop(attraction_index)
        if source_day.attractions:
            remaining_names = "、".join(
                item.name for item in source_day.attractions
            )
            source_day.description = (
                f"抵达{source_city or request.city}并办理入住后，"
                f"游览{remaining_names}。"
            )
        else:
            source_day.description = (
                f"抵达{source_city or request.city}并办理入住后，在酒店附近休整。"
            )
        if candidates:
            # 优先最近的可用日期；容量只作为能否放入的硬门槛。
            target_position = min(candidates, key=lambda item: item[0])[0]
            target_day = trip_plan.days[target_position]
            target_day.attractions.append(moved)
            target_names = "、".join(item.name for item in target_day.attractions)
            target_day.description = (
                f"本日安排{target_names}，已为落地日减少长距离奔波。"
            )
            logger.warning(
                f"落地日长途修复: 将 '{moved.name}' 从 Day1 调整到 "
                f"Day{target_day.day_index + 1}"
            )
            return "moved"

        self._record_removed_attraction(
            trip_plan,
            moved,
            source_day.day_index,
            "落地日往返交通过长，后续同城日期也无足够时间，已转为备选",
        )
        logger.warning(
            f"落地日长途修复: '{moved.name}' 无可用日期，已移入备选"
        )
        return "removed"

    def _enforce_final_timeline_constraints(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        day_meals: Optional[List[dict]] = None,
        attraction_candidates: Optional[List[dict]] = None,
        _day_position: Optional[int] = None,
    ) -> TripPlan:
        """用真实逐段交通时间做最后一轮可执行性修复。"""
        candidates_by_day = {
            int(item.get("day", 0)) - 1: item
            for item in (day_meals or []) if isinstance(item, dict)
        }

        if request.trip_mode == "single_city" and _day_position is None:
            self._final_meal_search_cache = {}
            self._rejected_final_meal_ids = {}
            self._prefer_local_meals = "当地美食" in request.preferences
            self._restore_missing_multi_city_meals(trip_plan, request)
            self._repair_meal_recommendation_quality(trip_plan, request)
            self._build_daily_timelines(trip_plan, request)
            if request.arrival_time and trip_plan.days:
                first_day = trip_plan.days[0]
                endpoints = {self._normalize_attr_name(name or "") for n in first_day.timeline if n.kind == "transport" and n.duration_minutes > 90 for name in (n.from_name, n.to_name)}
                index = next((i for i, a in enumerate(first_day.attractions) if self._normalize_attr_name(a.name) in endpoints and not a.user_locked), None)
                if index is not None:
                    self._relocate_arrival_day_conflict(trip_plan, request, index)
            for position in range(len(trip_plan.days)):
                self._enforce_final_timeline_constraints(trip_plan, request, day_meals, None, _day_position=position)
            self._rebalance_single_city_full_days(trip_plan, request)
            self._backfill_final_route_gaps(trip_plan, request, attraction_candidates or [], day_meals)
            self._enforce_pace_timeline_limits(
                trip_plan, request, attraction_candidates or []
            )
            self._resolve_post_trim_meals(
                trip_plan, request, candidates_by_day,
            )
            self._prune_readded_removed_attractions(trip_plan)
            self._sync_single_city_day_descriptions(trip_plan)
            return trip_plan

        if request.trip_mode == "multi_city" and _day_position is None:
            self._final_meal_search_cache = {}
            self._rejected_final_meal_ids = {}
            self._prefer_local_meals = "当地美食" in request.preferences
            self._repair_meal_recommendation_quality(trip_plan, request)
            self._move_partial_day_scenic_visits(trip_plan, request)
            # 每天独立处理，前一天的商户问题不能耗尽后面日期的修复预算。
            for position in range(len(trip_plan.days)):
                self._enforce_final_timeline_constraints(
                    trip_plan, request, day_meals, None, _day_position=position,
                )
            self._backfill_final_route_gaps(trip_plan, request, attraction_candidates or [], day_meals)
            self._enforce_pace_timeline_limits(
                trip_plan, request, attraction_candidates or []
            )
            self._resolve_post_trim_meals(
                trip_plan, request, candidates_by_day,
            )
            self._prune_readded_removed_attractions(trip_plan)
            self._refresh_multi_city_day_descriptions(trip_plan, request)
            return trip_plan

        # 每日独立的修复预算，避免前一天的商户问题耗尽后面日期的机会。
        repair_passes = 24 if request.trip_mode in {"multi_city", "single_city"} else 8
        if _day_position is None:
            self._final_meal_search_cache = {}
            self._rejected_final_meal_ids = {}
        seen_repair_states = set()
        for _ in range(repair_passes):
            if self._backfill_final_empty_multi_city_days(trip_plan, request, attraction_candidates or []):
                self._enrich_ticket_info(trip_plan)
            # 上一轮可能因营业时间移除了餐厅。多城市模式先补成不绑定
            # 具体 POI 的用餐时段，再让本轮时间轴继续校验，避免循环结束
            # 后才补餐、把已经修好的行程重新推迟到深夜。
            self._restore_missing_multi_city_meals(trip_plan, request)
            self._sync_multi_city_meal_anchors(trip_plan, request)
            self._build_daily_timelines(trip_plan, request, {_day_position} if _day_position is not None else None)
            if _day_position is not None:
                active_day = trip_plan.days[_day_position]
                state = json.dumps({"attractions": [a.model_dump() for a in active_day.attractions], "meals": [m.model_dump() for m in active_day.meals], "rejected": {kind: sorted(self._rejected_final_meal_ids.get((active_day.day_index, kind), set())) for kind in ("breakfast", "lunch", "dinner")}}, sort_keys=True, ensure_ascii=False)
                if state in seen_repair_states:
                    logger.warning(f"时间修复停止 Day{_day_position + 1}: 状态重复，交由最终硬约束检查")
                    break
                seen_repair_states.add(state)
            if self._resolve_flexible_multi_city_meals(trip_plan, request, candidates_by_day, _day_position):
                continue
            changed = False

            # 先处理被 _clock_text 截到 23:59 的硬溢出。过去修复次数由整条
            # 行程共享，长行程可能先在前几天反复修餐厅，导致后几天已经跨
            # 午夜的景点来不及处理。这里一轮可同时清理每个日期的一个溢出
            # 景点，且适用于单城市和多城市。
            for day in trip_plan.days:
                if _day_position is not None and day is not trip_plan.days[_day_position]:
                    continue
                clipped_positions = [
                    index for index, item in enumerate(day.timeline)
                    if self._timeline_item_is_clipped_at_midnight(item)
                ]
                if not clipped_positions:
                    continue
                first_clipped = clipped_positions[0]
                removable_index = next((
                    int(item.reference_index)
                    for item in reversed(day.timeline[:first_clipped + 1])
                    if item.kind == "attraction"
                    and item.reference_index is not None
                    and 0 <= int(item.reference_index) < len(day.attractions)
                    and not day.attractions[int(item.reference_index)].user_locked
                ), None)
                if removable_index is None:
                    removable_index = next((
                        index for index in range(len(day.attractions) - 1, -1, -1)
                        if not day.attractions[index].user_locked
                    ), None)
                if removable_index is None:
                    logger.error(
                        f"午夜溢出 Day{day.day_index + 1}: "
                        "所有关联景点均为用户锁定，无法自动移出"
                    )
                    continue
                removed = day.attractions.pop(removable_index)
                self._record_removed_attraction(
                    trip_plan, removed, day.day_index,
                    "当天累计交通和游览耗时超过午夜，已转为备选",
                )
                logger.warning(
                    f"午夜溢出修复 Day{day.day_index + 1}: "
                    f"移出 '{removed.name}'"
                )
                changed = True
            if changed:
                continue

            for position, day in enumerate(trip_plan.days):
                if _day_position is not None and position != _day_position:
                    continue
                meal_items = [
                    item for item in day.timeline
                    if item.kind == "meal" and item.reference_index is not None
                ]
                for item in reversed(meal_items):
                    index = int(item.reference_index)
                    if not 0 <= index < len(day.meals):
                        continue
                    start = self._time_to_minutes(item.start_time)
                    end = self._time_to_minutes(item.end_time)
                    if start is None or end is None:
                        continue
                    meal = day.meals[index]
                    if meal.data_source == "user":
                        continue
                    if self._opening_hours_cover_period(meal.opening_hours, start, end) is False:
                        if meal.poi_id:
                            self._rejected_final_meal_ids.setdefault((day.day_index, meal.type), set()).add(meal.poi_id)
                        self._replace_closed_meal(
                            day, index, meal.type, start, end,
                            None if request.trip_mode == "multi_city" else candidates_by_day.get(day.day_index),
                        )
                        changed = True
                        break
                if changed:
                    break

                # 真实路线已经证明落地日某个系统景点会产生超过 90 分钟的
                # 单段接驳时，不再把硬冲突留给用户“确认”。优先移动到后续
                # 同城且有容量的日期，否则转为编辑页备选。用户锁定项不动。
                if position == 0 and request.arrival_time and day.attractions:
                    long_legs = sorted(
                        (
                            item for item in day.timeline
                            if item.kind == "transport"
                            and (item.duration_minutes or 0) > 90
                        ),
                        key=lambda item: item.duration_minutes or 0,
                        reverse=True,
                    )
                    for leg in long_legs:
                        endpoint_names = {
                            self._normalize_attr_name(leg.from_name or ""),
                            self._normalize_attr_name(leg.to_name or ""),
                        }
                        conflicting_index = next((
                            index for index, attraction in enumerate(day.attractions)
                            if self._normalize_attr_name(attraction.name)
                            in endpoint_names
                        ), None)
                        if conflicting_index is None:
                            continue
                        action = self._relocate_arrival_day_conflict(
                            trip_plan, request, conflicting_index
                        )
                        if action in {"moved", "removed"}:
                            changed = True
                        # 锁定景点保留给用户决定，不能转而删除另一个无关景点。
                        break
                    if changed:
                        break

                lunch = next(
                    (item for item in day.timeline if item.kind == "meal" and item.meal_type == "lunch"),
                    None,
                )
                if (
                    request.trip_mode == "single_city"
                    and lunch is not None
                    and lunch.reference_index is not None
                ):
                    meal_index = int(lunch.reference_index)
                    meal = day.meals[meal_index]
                    detour = self._single_city_meal_detour(day, lunch, request)
                    if (
                        meal.data_source not in {"user", "logistics_advice"}
                        and detour is not None
                        and detour[0] > 45
                    ):
                        if meal.poi_id:
                            self._rejected_final_meal_ids.setdefault(
                                (day.day_index, "lunch"), set()
                            ).add(meal.poi_id)
                        self._set_area_meal(
                            day, meal_index, detour[1], detour[2],
                            f"原午餐会额外增加约{detour[0]}分钟交通，按当前游览区域重新选择餐厅。",
                        )
                        logger.warning(
                            f"午餐绕行修复 Day{day.day_index + 1}: "
                            f"'{meal.name}' 额外绕行约{detour[0]}分钟"
                        )
                        changed = True
                        break
                lunch_start = self._time_to_minutes(lunch.start_time) if lunch else None
                if lunch_start is not None and lunch_start > 14 * 60:
                    if request.trip_mode == "single_city" and lunch.reference_index is not None:
                        meal_index = int(lunch.reference_index)
                        meal = day.meals[meal_index]
                        previous = next((n for n in reversed(day.timeline[:day.timeline.index(lunch)]) if n.kind in {"hotel", "attraction", "meal"}), None)
                        anchor_entity = day.hotel
                        if previous and previous.kind == "attraction" and previous.reference_index is not None:
                            anchor_entity = day.attractions[int(previous.reference_index)]
                        elif previous and previous.kind == "meal" and previous.reference_index is not None:
                            anchor_entity = day.meals[int(previous.reference_index)]
                        if meal.data_source != "user" and anchor_entity and anchor_entity.location and meal.location:
                            distance = self._haversine_distance(meal.location.longitude, meal.location.latitude, anchor_entity.location.longitude, anchor_entity.location.latitude)
                            if distance > 1.5 and len(self._rejected_final_meal_ids.get((day.day_index, "lunch"), set())) < 2:
                                self._set_area_meal(day, meal_index, anchor_entity.location, anchor_entity.name, "原午餐产生绕路，先重新选择当前游览区域的餐厅。")
                                changed = True
                                break
                        previous_attr_index = next((int(n.reference_index) for n in reversed(day.timeline[:day.timeline.index(lunch)]) if n.kind == "attraction" and n.reference_index is not None), None)
                        if previous_attr_index is not None and self._relocate_single_city_time_conflict(trip_plan, request, position, previous_attr_index):
                            changed = True
                            break
                    if request.trip_mode == "multi_city" and day.route_segment and day.hotel and day.hotel.location:
                        lunch_index = int(lunch.reference_index) if lunch.reference_index is not None else None
                        if lunch_index is not None:
                            meal = day.meals[lunch_index]
                            distance = self._haversine_distance(
                                day.hotel.location.longitude, day.hotel.location.latitude,
                                meal.location.longitude, meal.location.latitude,
                            ) if meal.location else 0
                            origin_visit = any(self._attraction_route_side(a, day.route_segment) == "origin" for a in day.attractions)
                            hotel_before_lunch = any(
                                n.kind == "hotel" and n.reference_type == "hotel"
                                and (self._time_to_minutes(n.start_time) or 0) <= lunch_start
                                for n in day.timeline
                            )
                            if distance > 2.5 and not origin_visit and hotel_before_lunch:
                                if meal.poi_id:
                                    self._rejected_final_meal_ids.setdefault((day.day_index, "lunch"), set()).add(meal.poi_id)
                                day.meals[lunch_index] = Meal(
                                    type="lunch", name=f"{day.hotel.name}附近午餐（现场选择）",
                                    location=day.hotel.location, address=day.hotel.address,
                                    description="跨城抵达后先在酒店附近午餐，再前往景点。",
                                    estimated_cost=meal.estimated_cost or 40,
                                    data_source="logistics_advice", opening_hours_verified=True,
                                    route_anchor_name=day.hotel.name,
                                )
                                changed = True
                                break
                    previous_attractions = [
                        item for item in day.timeline
                        if item.kind == "attraction"
                        and item.reference_index is not None
                        and (self._time_to_minutes(item.end_time) or 0) <= lunch_start
                    ]
                    if previous_attractions:
                        item = previous_attractions[-1]
                        attr = day.attractions[int(item.reference_index)]
                        excess = lunch_start - (13 * 60 + 30)
                        reducible = max(0, attr.visit_duration - 90)
                        if reducible:
                            attr.visit_duration -= min(reducible, excess)
                        elif request.trip_mode == "multi_city" and not attr.user_locked:
                            removed_attr = day.attractions.pop(int(item.reference_index))
                            self._record_removed_attraction(
                                trip_plan, removed_attr, day.day_index,
                                "为避免午餐晚于14:00而移出",
                            )
                        elif request.trip_mode == "multi_city":
                            continue
                        else:
                            if attr.user_locked:
                                continue
                            removed_attr = day.attractions.pop(int(item.reference_index))
                            self._record_removed_attraction(trip_plan, removed_attr, day.day_index, "真实交通下无法衔接正常午餐，换日也不可行，转为备选")
                        logger.warning(
                            f"午餐时间修复 Day{day.day_index + 1}: "
                            f"调整午餐前景点 '{attr.name}'"
                        )
                        changed = True
                        break
                    if position == 0:
                        lunch_index = next(
                            (index for index, meal in enumerate(day.meals) if meal.type == "lunch"),
                            None,
                        )
                        if lunch_index is not None:
                            removed = day.meals.pop(lunch_index)
                            logger.warning(
                                f"抵达日午餐修复 Day{day.day_index + 1}: "
                                f"实际到店时间过晚，移除 '{removed.name}'"
                            )
                            changed = True
                            break

                dinner = next(
                    (item for item in day.timeline if item.kind == "meal" and item.meal_type == "dinner"),
                    None,
                )
                dinner_start = self._time_to_minutes(dinner.start_time) if dinner else None
                if dinner_start is not None and dinner_start > 20 * 60 + 30:
                    dinner_index = (
                        int(dinner.reference_index)
                        if dinner and dinner.reference_index is not None else None
                    )
                    if (
                        dinner_index is not None
                        and 0 <= dinner_index < len(day.meals)
                        and day.hotel
                        and day.hotel.location
                        and day.meals[dinner_index].data_source != "logistics_advice"
                        and not (request.trip_mode == "multi_city" and len(self._rejected_final_meal_ids.get((day.day_index, "dinner"), set())) >= 1)
                    ):
                        # 餐厅只是推荐项，不能比核心景点拥有更高优先级。
                        # 晚餐过晚时先改成返店附近用餐，再考虑压缩景点。
                        original_dinner = day.meals[dinner_index]
                        if original_dinner.poi_id:
                            self._rejected_final_meal_ids.setdefault((day.day_index, "dinner"), set()).add(original_dinner.poi_id)
                        day.meals[dinner_index] = Meal(
                            type="dinner",
                            name="返回酒店后在附近用餐",
                            address=day.hotel.address,
                            location=day.hotel.location,
                            description=(
                                f"原推荐餐厅“{original_dinner.name}”会使晚餐过晚，"
                                "已改为酒店附近灵活选择。"
                            ),
                            estimated_cost=original_dinner.estimated_cost or 70,
                            data_source="logistics_advice",
                            opening_hours_verified=True,
                            route_anchor_name=day.hotel.name,
                        )
                        logger.warning(
                            f"晚餐优先级修复 Day{day.day_index + 1}: "
                            f"将 '{original_dinner.name}' 改为酒店附近用餐"
                        )
                        changed = True
                        break
                    previous_attractions = [
                        item for item in day.timeline
                        if item.kind == "attraction"
                        and item.reference_index is not None
                        and (self._time_to_minutes(item.end_time) or 0) <= dinner_start
                    ]
                    if previous_attractions:
                        item = previous_attractions[-1]
                        attr = day.attractions[int(item.reference_index)]
                        excess = dinner_start - 20 * 60
                        # 首尾半日允许把最后一个景点压缩到 60 分钟，但不能仅
                        # 因晚餐迟几分钟就把当天唯一景点整项删除。
                        partial_day = position in {0, len(trip_plan.days) - 1}
                        minimum_visit = 60 if partial_day else 90
                        reducible = max(0, attr.visit_duration - minimum_visit)
                        if reducible:
                            attr.visit_duration -= min(reducible, excess)
                        elif not attr.user_locked:
                            # 景点压到合理下限后仍会导致深夜用餐，说明整项已经
                            # 放不下。保留晚餐并移出最后一个景点，供编辑页作为
                            # 备选恢复；不能删除晚餐后又在末尾兜底补回。
                            removed_attr = day.attractions.pop(int(item.reference_index))
                            self._record_removed_attraction(
                                trip_plan, removed_attr, day.day_index,
                                "为保证晚餐不晚于20:30而移出",
                            )
                        else:
                            continue
                        logger.warning(
                            f"晚餐时间修复 Day{day.day_index + 1}: "
                            f"调整晚餐前景点 '{attr.name}'"
                        )
                        changed = True
                        break

                for item in day.timeline:
                    if item.kind != "attraction" or item.reference_index is None:
                        continue
                    index = int(item.reference_index)
                    if not 0 <= index < len(day.attractions):
                        continue
                    start = self._time_to_minutes(item.start_time)
                    end = self._time_to_minutes(item.end_time)
                    if start is None or end is None:
                        continue
                    attraction = day.attractions[index]
                    fitted = self._opening_fit_start(attraction, start, max(1, end - start))
                    if fitted is None or fitted != start:
                        if attraction.user_locked:
                            continue
                        if request.trip_mode == "single_city" and self._relocate_single_city_time_conflict(trip_plan, request, position, index):
                            changed = True
                            break
                        if request.trip_mode == "multi_city":
                            intervals = self._effective_attraction_opening_intervals(attraction)
                            available = max((closing - start for opening, closing in intervals if opening <= start < closing), default=0)
                            minimum = min(attraction.visit_duration, 120 if attraction.visit_duration >= 240 else (90 if attraction.visit_duration >= 150 else 60))
                            if available >= minimum and available < attraction.visit_duration:
                                attraction.visit_duration = available
                                logger.info(f"最终闭园时长修复 Day{day.day_index + 1}: {attraction.name} → {available}分钟")
                                changed = True
                                break
                        removed = day.attractions.pop(index)
                        self._record_removed_attraction(
                            trip_plan, removed, day.day_index,
                            "开放时间与最终时间轴冲突",
                        )
                        logger.warning(
                            f"最终开放时间修复 Day{day.day_index + 1}: "
                            f"移除 '{removed.name}'"
                        )
                        changed = True
                        break
                if changed:
                    break

                hotel_returns = [
                    item for item in day.timeline
                    if item.kind == "hotel" and item.start_time
                ]
                latest_return = max(
                    (
                        self._time_to_minutes(item.start_time) or 0
                        for item in hotel_returns
                    ),
                    default=0,
                )
                if (
                    request.trip_mode == "multi_city"
                    and latest_return > 22 * 60
                    and day.attractions
                ):
                    removable_index = next(
                        (
                            index for index in range(len(day.attractions) - 1, -1, -1)
                            if not day.attractions[index].user_locked
                        ),
                        None,
                    )
                    if removable_index is not None:
                        removed_attr = day.attractions.pop(removable_index)
                        self._record_removed_attraction(
                            trip_plan, removed_attr, day.day_index,
                            "为保证22:00前返回酒店而移出",
                        )
                        logger.warning(
                            f"返店时间修复 Day{day.day_index + 1}: "
                            f"移出 '{removed_attr.name}'"
                        )
                        changed = True
                        break

                if position == len(trip_plan.days) - 1 and request.departure_time:
                    hub = next(
                        (item for item in reversed(day.timeline) if item.kind == "hub"),
                        None,
                    )
                    hub_start = self._time_to_minutes(hub.start_time) if hub else None
                    departure = self._time_to_minutes(request.departure_time)
                    if hub_start is not None and departure is not None:
                        latest = departure - self._terminal_buffer_minutes(
                            request.departure_transport or "", arrival=False
                        )
                        if hub_start > latest:
                            lateness = hub_start - latest
                            if day.attractions:
                                attr = day.attractions[-1]
                                reducible = max(0, attr.visit_duration - 90)
                                if reducible:
                                    attr.visit_duration -= min(reducible, lateness)
                                elif not attr.user_locked:
                                    removed_attr = day.attractions.pop()
                                    self._record_removed_attraction(
                                        trip_plan, removed_attr, day.day_index,
                                        "为确保按时抵达返程交通枢纽而移出",
                                    )
                                else:
                                    continue
                                logger.warning(
                                    f"离站倒排 Day{day.day_index + 1}: "
                                    f"调整最后景点 '{attr.name}'"
                                )
                            else:
                                lunch_index = next((i for i, m in enumerate(day.meals) if m.type == "lunch" and m.data_source not in {"user", "travel_packed"}), None)
                                if request.trip_mode == "multi_city" and lunch_index is not None:
                                    day.meals[lunch_index] = Meal(
                                        type="lunch", name="准备途中午餐（乘车期间食用）",
                                        location=day.hotel.location if day.hotel else None,
                                        description="返程接驳较长，出发前准备可携带简餐；不是在清晨吃午饭。",
                                        estimated_cost=30, data_source="travel_packed",
                                        route_anchor_name=day.hotel.name if day.hotel else None,
                                    )
                                    changed = True
                                    break
                                removable = [
                                    index for index, meal in enumerate(day.meals)
                                    if meal.type != "breakfast" and meal.data_source not in {"user", "travel_packed"}
                                ]
                                if removable:
                                    day.meals.pop(removable[-1])
                                else:
                                    logger.error(
                                        f"离站倒排 Day{day.day_index + 1}: "
                                        "无可裁剪活动，仍无法按时到站"
                                    )
                                    continue
                            changed = True
                            break

            if not changed:
                break

        self._restore_missing_multi_city_meals(trip_plan, request)

        # 早班离开倒排后，原早餐店可能尚未营业并被移除。此时不能重新
        # 绑定同一个无效候选，改为明确的酒店早餐/提前打包建议。
        if trip_plan.days and request.departure_time:
            last_day = trip_plan.days[-1]
            departure = self._time_to_minutes(request.departure_time)
            needs_breakfast = "breakfast" in self._available_meal_types(
                request, last_day.day_index
            )
            has_breakfast = any(meal.type == "breakfast" for meal in last_day.meals)
            if (
                needs_breakfast
                and not has_breakfast
                and departure is not None
                and departure <= 13 * 60 + 30
            ):
                last_day.meals.append(Meal(
                    type="breakfast",
                    name="酒店早餐或提前打包早餐",
                    address=last_day.hotel.address if last_day.hotel else None,
                    location=last_day.hotel.location if last_day.hotel else None,
                    description=(
                        "返程时间较早，附近实体早餐店营业时间无法可靠覆盖；"
                        "建议提前向酒店确认早餐，或前一晚准备可携带食品。"
                    ),
                    estimated_cost=20,
                    data_source="logistics_advice",
                    opening_hours_verified=True,
                ))
                logger.warning(
                    f"早班离开兜底 Day{last_day.day_index + 1}: 添加打包早餐提示"
                )

        self._sync_multi_city_meal_anchors(trip_plan, request)
        self._build_daily_timelines(trip_plan, request)
        self._refresh_multi_city_day_descriptions(trip_plan, request)
        return trip_plan

    @staticmethod
    def _assert_multi_city_executable(trip_plan: TripPlan, request: TripRequest) -> None:
        if request.trip_mode != "multi_city" or not trip_plan.quality_report:
            return
        hard_codes = {
            "departure_hub_late", "meal_opening_conflict", "attraction_opening_conflict",
            "dinner_too_late", "hotel_return_too_late", "lunch_too_late", "timeline_midnight_overflow",
        }
        conflicts = [i.message for i in trip_plan.quality_report.issues if i.code in hard_codes and i.severity == "error"]
        if any(MultiAgentTripPlanner._timeline_item_is_clipped_at_midnight(item) for day in trip_plan.days for item in day.timeline):
            conflicts.append("当天累计安排超过午夜")
        if conflicts:
            raise TripPlanningInfeasible(
                "当前路线无法形成可执行行程：" + "；".join(conflicts[:3])
                + "。请增加停留时间、减少路线节点或调整抵达/离开时间后重试。"
            )

    @staticmethod
    def _assert_single_city_executable(trip_plan: TripPlan, request: TripRequest) -> None:
        if request.trip_mode != "single_city" or not trip_plan.quality_report:
            return
        hard_codes = {
            "missing_expected_meal", "invalid_meal_business", "lunch_too_late", "dinner_too_late",
            "attraction_opening_conflict", "meal_opening_conflict", "night_visit_too_early",
            "departure_hub_late", "departure_hub_unresolved", "arrival_hub_unresolved",
            "timeline_midnight_overflow", "hotel_return_too_late", "empty_day", "underfilled_full_day",
        }
        errors = [i for i in trip_plan.quality_report.issues if i.severity == "error" and i.code in hard_codes]
        conflicts = [i.message for i in errors]
        if conflicts:
            suggestion = (
                "。系统尚未完成景点补排，请重试或反馈此任务编号。"
                if all(i.code in {"underfilled_full_day", "empty_day"} for i in errors)
                else "。请核对相关时间约束，或反馈此任务编号。"
            )
            raise TripPlanningInfeasible("当前单城市行程自动修复后仍有冲突：" + "；".join(conflicts[:3]) + suggestion)

    def _rebalance_single_city_full_days(self, trip_plan: TripPlan, request: TripRequest) -> bool:
        """Repair a 3/1 distribution using existing verified places, never a new search.

        Trials preserve all places and user locks, protect partial/remote days,
        and check real-route meal/opening constraints on both affected days.
        """
        if request.trip_mode != "single_city" or request.travel_pace == "relaxed":
            return False
        changed = False
        minimum = self._pace_profile(request)["full_day_target"]
        weather = {w.date: f"{w.day_weather}{w.night_weather}" for w in trip_plan.weather_info}
        for position in range(len(trip_plan.days)):
            day = trip_plan.days[position]
            if ((position == 0 and request.arrival_time)
                    or (position == len(trip_plan.days)-1 and request.departure_time)
                    or day.route_segment or self._is_remote_day(day)):
                continue
            while len(trip_plan.days[position].attractions) < minimum:
                day = trip_plan.days[position]
                if sum(a.visit_duration for a in day.attractions) >= 300 or self._timeline_hard_cost(day, request, position):
                    break
                if self._day_visit_capacity(request, day, position) - sum(n.duration_minutes for n in day.timeline if n.kind == "transport") < 300:
                    break
                choices = []
                rainy = any(word in weather.get(day.date, "") for word in ("雨", "雪", "雷"))
                for donor_position, donor in enumerate(trip_plan.days):
                    if donor_position == position or len(donor.attractions) <= max(2, minimum) or donor.route_segment or self._is_remote_day(donor):
                        continue
                    if self._timeline_hard_cost(donor, request, donor_position):
                        continue
                    for attraction_index, attraction in enumerate(donor.attractions):
                        if attraction.user_locked or any(
                            r.original_day_index == day.day_index and (
                                (attraction.poi_id and r.attraction.poi_id == attraction.poi_id)
                                or self._normalize_attr_name(r.attraction.name) == self._normalize_attr_name(attraction.name)
                            ) for r in trip_plan.removed_attractions
                        ):
                            continue
                        trial = trip_plan.model_copy(deep=True)
                        moved = trial.days[donor_position].attractions.pop(attraction_index)
                        trial.days[position].attractions.append(moved)
                        self._build_daily_timelines(trial, request, {position, donor_position})
                        if any(self._timeline_hard_cost(trial.days[p], request, p) for p in (position, donor_position)):
                            continue
                        travel = sum(n.duration_minutes for p in (position, donor_position) for n in trial.days[p].timeline if n.kind == "transport")
                        choices.append((int(rainy and not self._is_indoor_venue(moved.name, moved.category or "")), travel, donor_position, moved.name, trial))
                if not choices:
                    break
                _, _, donor_position, name, best = min(choices, key=lambda item: item[:2])
                trip_plan.days = best.days
                changed = True
                logger.info(f"单城市完整日均衡: {name} Day{donor_position + 1} → Day{position + 1}，两天饭点及开放时间均可行")
        return changed

    def _relocate_single_city_time_conflict(self, trip_plan: TripPlan, request: TripRequest, position: int, index: int) -> bool:
        """先尝试保留景点换日，只有两天真实三餐/开放时间都改善才提交。"""
        source = trip_plan.days[position]
        attraction = source.attractions[index]
        if attraction.user_locked:
            return False
        before_cost = self._timeline_hard_cost(source, request, position)
        for target_position in range(len(trip_plan.days)):
            target = trip_plan.days[target_position]
            if target_position == position or len(target.attractions) >= 3 or any(a.user_locked for a in target.attractions):
                continue
            if any(r.original_day_index == target.day_index and r.attraction.name == attraction.name for r in trip_plan.removed_attractions):
                continue
            saved = [d.model_copy(deep=True) for d in trip_plan.days]
            source.attractions.pop(index)
            target.attractions.append(attraction)
            self._build_daily_timelines(trip_plan, request, {position, target_position})
            if self._timeline_hard_cost(source, request, position) < before_cost and self._timeline_hard_cost(target, request, target_position) == 0:
                logger.info(f"单城市真实时段换日: {attraction.name} Day{position + 1}→Day{target_position + 1}")
                return True
            for current, backup in zip(trip_plan.days, saved):
                for field in type(current).model_fields:
                    setattr(current, field, getattr(backup, field))
        return False

    def _set_area_meal(self, day: DayPlan, index: int, location: Optional[Location], anchor: str, reason: str) -> None:
        original = day.meals[index]
        if original.data_source == "user":
            return
        if not hasattr(self, "_rejected_final_meal_ids"):
            self._rejected_final_meal_ids = {}
        if original.poi_id:
            self._rejected_final_meal_ids.setdefault((day.day_index, original.type), set()).add(original.poi_id)
        label = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}.get(original.type, "用餐")
        day.meals[index] = Meal(
            type=original.type, name=f"{anchor}附近{label}（现场选择）", location=location,
            description=f"用餐区域：{anchor}附近。{reason}", estimated_cost=original.estimated_cost or 30,
            data_source="logistics_advice", route_anchor_name=anchor,
        )

    def _single_city_meal_detour(
        self, day: DayPlan, meal_item: TimelineItem, request: TripRequest,
    ) -> Optional[Tuple[int, Location, str]]:
        """返回餐厅相对前后主路线增加的分钟数，以及重新搜索用的前序锚点。"""
        if meal_item.reference_index is None:
            return None
        meal_index = int(meal_item.reference_index)
        if not 0 <= meal_index < len(day.meals):
            return None
        meal = day.meals[meal_index]
        if not meal.location:
            return None
        try:
            timeline_index = day.timeline.index(meal_item)
        except ValueError:
            return None

        def entity(item: TimelineItem):
            if item.kind == "attraction" and item.reference_index is not None:
                index = int(item.reference_index)
                return day.attractions[index] if 0 <= index < len(day.attractions) else None
            if item.kind == "hotel":
                return day.hotel
            return None

        previous = next((
            value for value in (
                entity(item) for item in reversed(day.timeline[:timeline_index])
            ) if value is not None and value.location is not None
        ), None)
        following = next((
            value for value in (
                entity(item) for item in day.timeline[timeline_index + 1:]
            ) if value is not None and value.location is not None
        ), None)
        if previous is None or following is None:
            return None

        def travel(first: Location, second: Location) -> int:
            if first == second:
                return 0
            result = self._route_choice_between_locations(
                first, second, request.transportation, request.city,
            )
            return int(result.get("duration_minutes", 0) or 0)

        detour = (
            travel(previous.location, meal.location)
            + travel(meal.location, following.location)
            - travel(previous.location, following.location)
        )
        return max(0, detour), previous.location, previous.name

    def _repair_meal_recommendation_quality(self, trip_plan: TripPlan, request: TripRequest) -> None:
        for position, day in enumerate(trip_plan.days):
            for index, meal in enumerate(list(day.meals)):
                if meal.data_source == "user":
                    continue
                invalid = any(word in meal.name for word in NON_DINING_BUSINESS_MARKERS) or (meal.type in {"lunch", "dinner"} and any(word in meal.name for word in ("鸭脖", "鸭头", "卤味", "零食", "甜品")))
                generic = "当地美食" in request.preferences and any(word in meal.name for word in ("兰州牛肉", "沙县", "擂饭", "新疆烧烤"))
                # 灵活晚餐默认服务返店动线，不能为了建议坐标再回到已游览景点。
                reanchor = meal.type == "dinner" and meal.data_source == "logistics_advice"
                detour = False
                if request.trip_mode == "single_city" and meal.type == "dinner" and meal.location and day.hotel and day.hotel.location:
                    origin = day.attractions[-1].location if day.attractions else day.hotel.location
                    def travel(first, second):
                        return 0 if first == second else self._route_choice_between_locations(first, second, request.transportation, request.city)["duration_minutes"]
                    detour = travel(origin, meal.location) + travel(meal.location, day.hotel.location) - travel(origin, day.hotel.location) > 45
                if not (invalid or generic or reanchor or detour):
                    continue
                if meal.type == "breakfast" and day.route_segment and position > 0:
                    origin_hotel = trip_plan.days[position - 1].hotel
                    anchor = (origin_hotel.location.longitude, origin_hotel.location.latitude, origin_hotel.name) if origin_hotel and origin_hotel.location else None
                elif meal.type == "dinner" and day.hotel and day.hotel.location:
                    anchor = (day.hotel.location.longitude, day.hotel.location.latitude, day.hotel.name)
                else:
                    anchor = self._meal_anchor(day, meal.type)
                if anchor:
                    self._set_area_meal(day, index, Location(longitude=anchor[0], latitude=anchor[1]), anchor[2],
                                        "原推荐不适合作为本次用餐安排，优先重新选择沿线堂食餐厅。")

    def _timeline_hard_cost(self, day: DayPlan, request: TripRequest, position: int) -> int:
        cost = 0
        for item in day.timeline:
            start = self._time_to_minutes(item.start_time)
            end = self._time_to_minutes(item.end_time)
            if start is None:
                continue
            if self._timeline_item_is_clipped_at_midnight(item):
                cost += 1000
            if item.meal_type == "lunch":
                cost += max(0, start - 14 * 60)
            if item.meal_type == "dinner":
                cost += max(0, start - (20 * 60 + 30))
            if item.kind == "hotel":
                cost += max(0, start - 22 * 60)
            if item.kind == "meal" and item.reference_index is not None and end is not None:
                meal = day.meals[int(item.reference_index)]
                if self._opening_hours_cover_period(meal.opening_hours, start, end) is False:
                    cost += 120
            if item.kind == "attraction" and item.reference_index is not None and end is not None:
                attraction = day.attractions[int(item.reference_index)]
                fit = self._single_city_visit_fit_start if request.trip_mode == "single_city" else self._opening_fit_start
                if fit(attraction, start, end - start) != start:
                    cost += 120
        if request.departure_time and any(n.hub_role == "trip_departure" for n in day.timeline):
            latest = self._time_to_minutes(request.departure_time) - self._terminal_buffer_minutes(request.departure_transport or "", arrival=False)
            for item in day.timeline:
                if item.hub_role == "trip_departure":
                    cost += max(0, (self._time_to_minutes(item.start_time) or 0) - latest) * 3
        return cost

    def _move_partial_day_scenic_visits(self, trip_plan: TripPlan, request: TripRequest) -> None:
        self._build_daily_timelines(trip_plan, request)
        for position in range(len(trip_plan.days)):
            day = trip_plan.days[position]
            if not day.attractions or not (day.route_segment or position == len(trip_plan.days) - 1):
                continue
            if not self._timeline_hard_cost(day, request, position):
                continue
            stop = day.end_location or day.overnight_city
            movable = next((a for a in day.attractions if not a.user_locked and self._multi_city_full_day_candidate(a)), None)
            if movable is None:
                continue
            for target_position, target in enumerate(trip_plan.days):
                if target_position == position or target.route_segment or (target.end_location or target.overnight_city) != stop:
                    continue
                if target_position == len(trip_plan.days) - 1 and request.departure_time:
                    continue
                if target_position == 0 and request.arrival_time:
                    continue
                if any(a.user_locked for a in target.attractions):
                    continue
                saved_days = [d.model_copy(deep=True) for d in trip_plan.days]
                before = self._timeline_hard_cost(day, request, position) + self._timeline_hard_cost(target, request, target_position)
                day.attractions = [a for a in day.attractions if a.name != movable.name] + list(target.attractions)
                target.attractions = [movable]
                self._repair_meal_recommendation_quality(trip_plan, request)
                self._sync_multi_city_meal_anchors(trip_plan, request)
                self._build_daily_timelines(trip_plan, request, {position, target_position})
                after = self._timeline_hard_cost(day, request, position) + self._timeline_hard_cost(target, request, target_position)
                if after < before and self._timeline_hard_cost(target, request, target_position) == 0:
                    logger.info(f"远郊换日: {movable.name} Day{position + 1}→Day{target_position + 1}，真实路线冲突成本 {before}→{after}")
                    break
                trip_plan.days = saved_days
                day = trip_plan.days[position]

    def _backfill_final_route_gaps(self, trip_plan: TripPlan, request: TripRequest, candidates: List[dict], day_meals: Optional[List[dict]]) -> None:
        if not candidates:
            return
        used = {self._normalize_attr_name(a.name) for day in trip_plan.days for a in day.attractions}
        if request.trip_mode != "single_city":
            used.update(self._normalize_attr_name(r.attraction.name) for r in trip_plan.removed_attractions)
        # Earlier stages mark atomic scenic aliases together. This late member-
        # level backfill must obey the same rule, including across different days.
        # Large non-hierarchical area clusters retain independent member choices.
        used_entities = {self._scenic_entity_name(name) for name in used}
        for candidate in candidates:
            if not (candidate.get("display_name") or candidate.get("area_name") or candidate.get("_strong_hierarchy")):
                continue  # Raw city pools may contain unrelated independent POIs.
            aliases = self._candidate_seen_aliases(candidate)
            if aliases & used:
                used.update(aliases)
        positions = list(range(len(trip_plan.days)))
        if request.trip_mode == "single_city":
            # 稀缺候选先服务真正缺景点的日期，不能被前面已安排两处的日子抢走。
            positions.sort(key=lambda p: (len(trip_plan.days[p].attractions), -sum(n.duration_minutes for n in trip_plan.days[p].timeline if n.source == "planned_break")))
        for position in positions:
            day = trip_plan.days[position]
            gaps = sum(n.duration_minutes for n in day.timeline if n.source == "planned_break")
            pace = self._pace_profile(request)
            limit = min(
                pace["max_attractions"],
                3
                if request.trip_mode == "single_city"
                and gaps >= pace["backfill_gap"]
                else 2,
            )
            if request.travel_pace == "relaxed" and sum(
                attraction.visit_duration for attraction in day.attractions
            ) >= 240:
                limit = min(limit, 1)
            if not day.hotel or not day.hotel.location or len(day.attractions) >= limit:
                continue
            if self._timeline_hard_cost(day, request, position):
                continue
            anchor = day.attractions[-1].location if day.attractions else day.hotel.location
            stop = day.end_location or day.overnight_city
            pool = []
            forbidden = {self._normalize_attr_name(r.attraction.name) for r in trip_plan.removed_attractions if r.original_day_index == day.day_index}
            for group in candidates:
                if request.trip_mode != "single_city" and self._candidate_route_stop(group) != stop:
                    continue
                for member in group.get("members") or [group]:
                    name = self._normalize_attr_name(member.get("name", ""))
                    if not name or self._is_dup_name(name, used) or self._scenic_entity_name(name) in used_entities or (request.trip_mode == "single_city" and self._is_dup_name(name, forbidden)):
                        continue
                    if self._is_non_attraction_poi(member) or member.get("_is_remote") or group.get("_is_remote"):
                        continue
                    try:
                        distance = self._haversine_distance(anchor.longitude, anchor.latitude, float(member.get("longitude", 0)), float(member.get("latitude", 0)))
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(distance) or distance > 6:
                        continue
                    pool.append((float(member.get("score", group.get("score", 0)) or 0) - distance, group, member))
            for _, group, member in sorted(pool, key=lambda p: p[0], reverse=True)[:4]:
                try:
                    pick = self._compact_candidate_pick({**group, **member, "members": [], "display_name": member.get("name"), "area_name": member.get("name")})
                    extra = self._make_attraction(pick)
                    extra.visit_duration = self._pace_adjusted_visit_duration(
                        request,
                        self._estimate_visit_duration(
                            extra.name, extra.category, request.preferences, member
                        ),
                    )
                    if member.get("opening_hours"):
                        extra.ticket_info = TicketInfo(opening_hours=member["opening_hours"], source="amap")
                    else:
                        self._enrich_ticket_info(trip_plan.model_copy(update={"days": [day.model_copy(update={"attractions": [extra]})]}))
                except (ValueError, TypeError, KeyError):
                    continue
                if self._is_subordinate_backfill_attraction(extra):
                    logger.debug(f"最终路线空档补位跳过附属节点: {extra.name}")
                    continue
                before = day.model_copy(deep=True)
                before_visit = sum(a.visit_duration for a in day.attractions)
                day.attractions.append(extra)
                self._build_daily_timelines(trip_plan, request, {position})
                # 实际逐段时间校验；不为了补数量牺牲已有核心景点或正常用餐。
                after_gaps = sum(n.duration_minutes for n in day.timeline if n.source == "planned_break")
                extra_index = len(day.attractions) - 1
                extra_node = next((
                    node for node in day.timeline
                    if node.kind == "attraction" and node.reference_index == extra_index
                ), None)
                extra_start = self._time_to_minutes(extra_node.start_time) if extra_node else None
                night_ok = not (
                    extra_start is not None
                    and extra_start >= 18 * 60
                    and not self._is_night_friendly_attraction(extra)
                )
                if night_ok and self._timeline_hard_cost(day, request, position) == 0 and sum(a.visit_duration for a in day.attractions) >= before_visit + 60 and (request.trip_mode != "single_city" or after_gaps < gaps or len(before.attractions) < 2):
                    used.add(self._normalize_attr_name(extra.name))
                    used_entities.add(self._scenic_entity_name(extra.name))
                    aliases = self._candidate_seen_aliases(group)
                    if (group.get("display_name") or group.get("area_name") or group.get("_strong_hierarchy")) and self._normalize_attr_name(extra.name) in aliases:
                        used.update(aliases)
                    logger.info(f"最终路线空档补位 Day{position + 1}: {extra.name}")
                    self._enforce_final_timeline_constraints(trip_plan, request, day_meals, None, _day_position=position)
                    break
                trip_plan.days[position] = before
                day = before

    def _backfill_final_empty_multi_city_days(
        self, trip_plan: TripPlan, request: TripRequest, candidates: List[dict],
    ) -> bool:
        """最终修复后的空日只从本节点补入一个可执行候选；不重排单城市。"""
        if request.trip_mode != "multi_city" or not candidates:
            return False
        changed = False
        unavailable = {self._normalize_attr_name(a.name) for d in trip_plan.days for a in d.attractions}
        unavailable.update(self._normalize_attr_name(r.attraction.name) for r in trip_plan.removed_attractions)
        unavailable_ids = {a.poi_id for d in trip_plan.days for a in d.attractions if a.poi_id}
        unavailable_ids.update(r.attraction.poi_id for r in trip_plan.removed_attractions if r.attraction.poi_id)
        for position, day in enumerate(trip_plan.days):
            if day.attractions or day.route_segment or not day.hotel or not day.hotel.location:
                continue
            if position == len(trip_plan.days) - 1 and request.departure_time:
                continue
            if position == 0 and request.arrival_time:
                continue
            stop = day.end_location or day.overnight_city
            pool = []
            for group in candidates:
                if self._candidate_route_stop(group) != stop:
                    continue
                for member in group.get("members") or [group]:
                    if self._normalize_attr_name(member.get("name", "")) in unavailable or (member.get("id") and member["id"] in unavailable_ids):
                        continue
                    if self._is_non_attraction_poi(member):
                        continue
                    pool.append(({**group, **member, "members": [], "display_name": member.get("name"), "area_name": member.get("name")}, member))
            pool.sort(key=lambda pair: float(pair[1].get("score", pair[0].get("score", 0)) or 0), reverse=True)
            for source, member in pool[:8]:
                try:
                    pick = self._compact_candidate_pick(source, "最终空日按同目的地可执行景点补位。")
                    duration = self._pace_adjusted_visit_duration(
                        request,
                        self._estimate_visit_duration(
                            pick["name"], pick["category"], request.preferences, member
                        ),
                    )
                    attraction = Attraction(
                        name=pick["name"], address=pick["address"],
                        location=Location(longitude=pick["lng"], latitude=pick["lat"]),
                        visit_duration=duration, description=pick["description"],
                        category=pick["category"], rating=pick["rating"], poi_id=pick["poi_id"],
                        ticket_info=TicketInfo(opening_hours=str(member.get("opening_hours", "") or ""), source="amap", confidence="medium") if member.get("opening_hours") else None,
                    )
                    travel = self._estimated_location_transfer_minutes(day.hotel.location, attraction.location, request.transportation, day.overnight_city or stop or "", True)
                except (TypeError, ValueError, KeyError):
                    continue
                start = self._opening_fit_start(attraction, 9 * 60 + travel, duration)
                # 保留午餐、返程和晚餐；短备选也不能让空日重新变成深夜赶路。
                if start is None or start + duration + 60 + travel > 20 * 60:
                    continue
                day.attractions.append(attraction)
                unavailable.add(self._normalize_attr_name(attraction.name))
                if attraction.poi_id:
                    unavailable_ids.add(attraction.poi_id)
                changed = True
                logger.info(f"最终空日补位 Day{day.day_index + 1}: {attraction.name}")
                break
        return changed

    def _estimated_intra_city_transfer_minutes(
        self,
        first: Attraction,
        second: Attraction,
        transportation: str,
        city: str = "",
        use_amap: bool = False,
    ) -> int:
        """估算两个景点之间的市内门到门交通时间。"""
        return self._estimated_location_transfer_minutes(
            first.location,
            second.location,
            transportation,
            city,
            use_amap,
        )

    @classmethod
    def _pair_requires_separate_days(
        cls,
        first: Attraction,
        second: Attraction,
        first_duration: int,
        second_duration: int,
    ) -> bool:
        """两个景点是否属于需要拆日的跨片区/双高投入组合。"""
        distance = cls._haversine_distance(
            first.location.longitude, first.location.latitude,
            second.location.longitude, second.location.latitude,
        )
        if not math.isfinite(distance):
            return False
        if distance > 18:
            return True
        return (
            distance > 6
            and cls._is_high_effort_attraction(
                first.name, first.category or "", first_duration
            )
            and cls._is_high_effort_attraction(
                second.name, second.category or "", second_duration
            )
        )

    def _enforce_daily_route_feasibility(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        candidates: Optional[List[dict]] = None,
        use_amap_routes: bool = False,
    ) -> TripPlan:
        """按游览时长、交通耗时和景点强度修复单城行程的跨片区拼接。

        模型只负责给出初始分天；这里使用真实坐标做确定性换日和日内排序。
        多城市/环线已有路线节点硬约束，不在此处跨节点交换景点。
        """
        if self._is_route_mode(request) or len(trip_plan.days) < 2:
            return trip_plan

        city_center = self._get_city_center(request.city)

        alias_metadata: Dict[str, dict] = {}
        poi_metadata: Dict[str, dict] = {}
        for group in candidates or []:
            group_metadata = dict(group)
            for key in ("display_name", "area_name"):
                value = group.get(key, "")
                if value:
                    alias_metadata.setdefault(
                        self._normalize_attr_name(value), group_metadata
                    )
            for member in group.get("members") or []:
                merged = {**group_metadata, **member}
                if member.get("id"):
                    poi_metadata[member["id"]] = merged
                if member.get("name"):
                    alias_metadata.setdefault(
                        self._normalize_attr_name(member["name"]), merged
                    )

        def original_duration(attraction: Attraction) -> int:
            metadata = (
                poi_metadata.get(attraction.poi_id or "")
                or alias_metadata.get(self._normalize_attr_name(attraction.name))
                or {}
            )
            return self._pace_adjusted_visit_duration(
                request,
                self._estimate_visit_duration(
                    attraction.name,
                    attraction.category or "",
                    request.preferences,
                    metadata,
                ),
            )

        def best_order_and_penalty(day: DayPlan, position: int, attrs: List[Attraction]):
            if not attrs:
                # 抵达/离开短日为空是合法结果，不能用巨额空日惩罚诱导
                # 优化器把其他日期的景点交换到一个根本没有游览窗口的日期。
                capacity = self._day_visit_capacity(request, day, position)
                return attrs, 0.0 if capacity < 60 else 1_000_000.0
            durations = {id(attr): original_duration(attr) for attr in attrs}
            hard_pairs = 0
            for left_index, left in enumerate(attrs):
                for right in attrs[left_index + 1:]:
                    if self._pair_requires_separate_days(
                        left, right, durations[id(left)], durations[id(right)]
                    ):
                        hard_pairs += 1

            best_order = list(attrs)
            best_transfer = float("inf")
            best_constraints = float("inf")
            orders = permutations(attrs) if len(attrs) <= 6 else [tuple(attrs)]
            for order in orders:
                transfer = sum(
                    self._estimated_intra_city_transfer_minutes(
                        previous,
                        current,
                        request.transportation,
                        request.city,
                        use_amap_routes,
                    )
                    for previous, current in zip(order, order[1:])
                )
                # 酒店确定后，每一天都计算酒店出发和返回；首次粗排时酒店
                # 尚未产生，仅对首尾日使用城市中心作为保守锚点。
                anchor = None
                if day.hotel and day.hotel.location:
                    anchor = day.hotel.location
                elif city_center and (
                    position == 0 or position == len(trip_plan.days) - 1
                ):
                    anchor = Location(
                        longitude=city_center[0], latitude=city_center[1]
                    )
                if anchor and order:
                    transfer += self._estimated_location_transfer_minutes(
                        anchor,
                        order[0].location,
                        request.transportation,
                        request.city,
                        use_amap_routes,
                    )
                    transfer += self._estimated_location_transfer_minutes(
                        order[-1].location,
                        anchor,
                        request.transportation,
                        request.city,
                        use_amap_routes,
                    )
                current = max(9 * 60, self._arrival_ready_minutes(request) or 0) if position == 0 else 9 * 60
                constraints, previous_location, had_lunch = 0, anchor, False
                for attr in order:
                    if previous_location:
                        current += self._estimated_location_transfer_minutes(previous_location, attr.location, request.transportation, request.city, use_amap_routes)
                    if not had_lunch and current + durations[id(attr)] > 13 * 60 + 30:
                        current = max(current, 11 * 60 + 30) + 60
                        had_lunch = True
                    fitted = self._single_city_visit_fit_start(attr, current, durations[id(attr)])
                    if fitted is None:
                        constraints += 100_000
                    else:
                        current = fitted
                    current += durations[id(attr)]
                    previous_location = attr.location
                if (constraints, transfer) < (best_constraints, best_transfer):
                    best_constraints = constraints
                    best_transfer = transfer
                    best_order = list(order)

            visit_minutes = sum(durations.values())
            required = visit_minutes + int(best_transfer)
            capacity = self._day_visit_capacity(request, day, position)
            overflow = max(0, required - capacity)
            # 强冲突优先于普通路线长度；空日也禁止作为“优化”结果。
            penalty = hard_pairs * 100_000 + overflow * 1_000 + best_transfer + best_constraints
            return best_order, penalty

        def total_penalty() -> float:
            return sum(
                best_order_and_penalty(day, position, day.attractions)[1]
                for position, day in enumerate(trip_plan.days)
            )

        affected: set[int] = set()
        for _ in range(12):
            baseline = total_penalty()
            best_change = None
            best_improvement = 0.0

            # 优先尝试把景点移动到仍有容量的其他日期。
            for source_pos, source in enumerate(trip_plan.days):
                if len(source.attractions) <= 1:
                    continue
                source_penalty = best_order_and_penalty(
                    source, source_pos, source.attractions
                )[1]
                if source_penalty < 1_000:
                    continue
                for attr_pos, attraction in enumerate(source.attractions):
                    for target_pos, target in enumerate(trip_plan.days):
                        if target_pos == source_pos or len(target.attractions) >= 3:
                            continue
                        source_new = source.attractions[:attr_pos] + source.attractions[attr_pos + 1:]
                        target_new = target.attractions + [attraction]
                        # 不能把景点“优化”到根本放不下的抵达/离开半日。
                        # 这正是此前完整日经典景点被挪到离开日后再删除的根因。
                        if best_order_and_penalty(target, target_pos, target_new)[1] >= 1_000:
                            continue
                        old_local = (
                            best_order_and_penalty(source, source_pos, source.attractions)[1]
                            + best_order_and_penalty(target, target_pos, target.attractions)[1]
                        )
                        new_local = (
                            best_order_and_penalty(source, source_pos, source_new)[1]
                            + best_order_and_penalty(target, target_pos, target_new)[1]
                        )
                        improvement = old_local - new_local
                        if improvement > best_improvement:
                            best_improvement = improvement
                            best_change = ("move", source_pos, attr_pos, target_pos, None)

            # 日期都已排满时，尝试跨日交换一个景点。
            for left_pos, left_day in enumerate(trip_plan.days):
                for right_pos in range(left_pos + 1, len(trip_plan.days)):
                    right_day = trip_plan.days[right_pos]
                    if (
                        best_order_and_penalty(
                            left_day, left_pos, left_day.attractions
                        )[1] < 1_000
                        and best_order_and_penalty(
                            right_day, right_pos, right_day.attractions
                        )[1] < 1_000
                    ):
                        continue
                    for left_index in range(len(left_day.attractions)):
                        for right_index in range(len(right_day.attractions)):
                            left_new = list(left_day.attractions)
                            right_new = list(right_day.attractions)
                            left_new[left_index], right_new[right_index] = (
                                right_new[right_index], left_new[left_index]
                            )
                            left_new_penalty = best_order_and_penalty(
                                left_day, left_pos, left_new
                            )[1]
                            right_new_penalty = best_order_and_penalty(
                                right_day, right_pos, right_new
                            )[1]
                            # 首尾日受到抵达/离开时间约束时，交换后的安排必须
                            # 真正可执行。完整日仍允许先接受“更优但尚未完美”的
                            # 中间状态，后续迭代可继续拆分远郊组合。
                            left_constrained = (
                                (left_pos == 0 and bool(request.arrival_time))
                                or (left_pos == len(trip_plan.days) - 1 and bool(request.departure_time))
                            )
                            right_constrained = (
                                (right_pos == 0 and bool(request.arrival_time))
                                or (right_pos == len(trip_plan.days) - 1 and bool(request.departure_time))
                            )
                            if (
                                (left_constrained and left_new_penalty >= 1_000)
                                or (right_constrained and right_new_penalty >= 1_000)
                            ):
                                continue
                            old_local = (
                                best_order_and_penalty(left_day, left_pos, left_day.attractions)[1]
                                + best_order_and_penalty(right_day, right_pos, right_day.attractions)[1]
                            )
                            new_local = left_new_penalty + right_new_penalty
                            improvement = old_local - new_local
                            if improvement > best_improvement:
                                best_improvement = improvement
                                best_change = (
                                    "swap", left_pos, left_index, right_pos, right_index
                                )

            if best_change is None or best_improvement <= 0:
                break
            kind, source_pos, source_index, target_pos, target_index = best_change
            source_day = trip_plan.days[source_pos]
            target_day = trip_plan.days[target_pos]
            if kind == "move":
                attraction = source_day.attractions.pop(source_index)
                target_day.attractions.append(attraction)
                logger.info(
                    f"跨片区修复: '{attraction.name}' Day{source_pos + 1} → Day{target_pos + 1}"
                )
            else:
                source_attr = source_day.attractions[source_index]
                target_attr = target_day.attractions[target_index]
                source_day.attractions[source_index], target_day.attractions[target_index] = (
                    target_attr, source_attr
                )
                logger.info(
                    f"跨片区修复: Day{source_pos + 1} '{source_attr.name}' 与 "
                    f"Day{target_pos + 1} '{target_attr.name}' 换日"
                )
            affected.update((source_pos, target_pos))
            if total_penalty() >= baseline:
                logger.warning("跨片区修复未降低整体代价，停止继续调整")
                break

        # 如果所有日期都已塞满，移动/交换仍可能无法消除冲突。此时用
        # 尚未入选的真实候选替换造成冲突的项目，优先经典和偏好候选。
        used_names = {
            self._normalize_attr_name(attr.name)
            for day in trip_plan.days for attr in day.attractions
        }
        tier_rank = {"classic": 3, "preference": 2, "local": 1, "supplement": 0}
        replacement_pool = []
        # 未调用路线 API 的快速粗排可以遍历候选池尝试替换。真实路线
        # 复核只处理已选景点，避免为大量未入选 POI 产生路径请求。
        if not use_amap_routes:
            for candidate in candidates or []:
                candidate_name = (
                    candidate.get("display_name") or candidate.get("area_name") or ""
                )
                if not candidate_name or self._normalize_attr_name(candidate_name) in used_names:
                    continue
                pick = self._compact_candidate_pick(candidate)
                replacement = Attraction(
                    name=pick["name"],
                    address=pick["address"],
                    location=Location(longitude=pick["lng"], latitude=pick["lat"]),
                    visit_duration=120,
                    description=pick["description"],
                    category=pick["category"],
                    rating=pick["rating"],
                    poi_id=pick["poi_id"],
                    ticket_price=0,
                )
                replacement_pool.append((
                    tier_rank.get(candidate.get("_candidate_tier", "supplement"), 0),
                    float(candidate.get("score", 0) or 0),
                    replacement,
                ))
        replacement_pool.sort(key=lambda item: (item[0], item[1]), reverse=True)

        for position, day in enumerate(trip_plan.days):
            old_penalty = best_order_and_penalty(day, position, day.attractions)[1]
            if old_penalty < 1_000:
                continue
            best_replacement = None
            best_key = None
            for attr_index in range(len(day.attractions)):
                for candidate_tier, candidate_score, replacement in replacement_pool:
                    if self._normalize_attr_name(replacement.name) in used_names:
                        continue
                    removed = day.attractions[attr_index]
                    removed_metadata = (
                        poi_metadata.get(removed.poi_id or "")
                        or alias_metadata.get(self._normalize_attr_name(removed.name))
                        or {}
                    )
                    removed_tier = tier_rank.get(
                        removed_metadata.get("_candidate_tier", "supplement"), 0
                    )
                    # 路线变短不能以景点质量降级为代价。替换只允许同类且
                    # 候选层级不低于原景点；核心经典景点因此不会被普通公园替掉。
                    if candidate_tier < removed_tier:
                        continue
                    if (
                        removed.category
                        and replacement.category
                        and removed.category != replacement.category
                    ):
                        continue
                    proposed = list(day.attractions)
                    proposed[attr_index] = replacement
                    _, new_penalty = best_order_and_penalty(day, position, proposed)
                    if new_penalty >= old_penalty:
                        continue
                    key = (new_penalty < 1_000, candidate_tier, candidate_score, -new_penalty)
                    if best_key is None or key > best_key:
                        best_key = key
                        best_replacement = (attr_index, replacement, proposed)
            if best_replacement:
                attr_index, replacement, proposed = best_replacement
                removed = day.attractions[attr_index]
                day.attractions = proposed
                self._record_removed_attraction(
                    trip_plan, removed, day.day_index,
                    f"路线不顺，系统改用同级候选“{replacement.name}”",
                )
                used_names.discard(self._normalize_attr_name(removed.name))
                used_names.add(self._normalize_attr_name(replacement.name))
                affected.add(position)
                logger.info(
                    f"跨片区修复: Day{position + 1} 用 '{replacement.name}' "
                    f"替换不合理的 '{removed.name}'"
                )

        # 真实路线复核是最终硬门禁。若换日、换序、替换仍无法让当天放进
        # 时间窗口，逐个移除最低价值且最能降低超时的项目，绝不返回已知
        # 不可完成的行程。首次快速粗排不执行删除，避免接口未调用时过度裁剪。
        if use_amap_routes:
            tier_rank = {
                "classic": 3,
                "preference": 2,
                "local": 1,
                "supplement": 0,
            }

            def attraction_priority(attraction: Attraction) -> tuple[int, float]:
                metadata = (
                    poi_metadata.get(attraction.poi_id or "")
                    or alias_metadata.get(self._normalize_attr_name(attraction.name))
                    or {}
                )
                return (
                    tier_rank.get(metadata.get("_candidate_tier", "supplement"), 0),
                    float(metadata.get("score", 0) or 0),
                )

            for position, day in enumerate(trip_plan.days):
                # 首尾半日即使只剩一个景点，也必须通过“游览 + 酒店往返”的
                # 真实路线门禁。过去 len==1 会绕过下面的裁剪，导致抵达日
                # 保留公共交通单程近两小时的景点。
                if (
                    len(day.attractions) == 1
                    and position in {0, len(trip_plan.days) - 1}
                    and best_order_and_penalty(day, position, day.attractions)[1] >= 1_000
                    and not day.attractions[0].user_locked
                ):
                    removed = day.attractions.pop()
                    self._record_removed_attraction(
                        trip_plan, removed, day.day_index,
                        "真实交通与游览耗时超出抵达或离开日窗口",
                    )
                    affected.add(position)
                    logger.warning(
                        f"高德路线超时修复: Day{position + 1} 移出首尾日景点 "
                        f"'{removed.name}'"
                    )
                while len(day.attractions) > 1:
                    _, current_penalty = best_order_and_penalty(
                        day, position, day.attractions
                    )
                    if current_penalty < 1_000:
                        break

                    options = []
                    for attr_index, attraction in enumerate(day.attractions):
                        proposed = (
                            day.attractions[:attr_index]
                            + day.attractions[attr_index + 1:]
                        )
                        proposed_order, proposed_penalty = best_order_and_penalty(
                            day, position, proposed
                        )
                        priority = attraction_priority(attraction)
                        options.append((
                            proposed_penalty < 1_000,
                            current_penalty - proposed_penalty,
                            -priority[0],
                            -priority[1],
                            attr_index,
                            attraction,
                            proposed_order,
                        ))
                    if not options:
                        break
                    best = max(options, key=lambda item: item[:4])
                    if best[1] <= 0:
                        break
                    _, _, _, _, _, removed, proposed_order = best
                    day.attractions = proposed_order
                    self._record_removed_attraction(
                        trip_plan, removed, day.day_index,
                        "真实交通耗时超出当天可用时间",
                    )
                    affected.add(position)
                    logger.warning(
                        f"高德路线超时修复: Day{position + 1} 移除低优先级景点 "
                        f"'{removed.name}'"
                    )

        # 每天按最短日内连线排序，并同步刷新发生换日的摘要。
        for position, day in enumerate(trip_plan.days):
            day.attractions = best_order_and_penalty(
                day, position, day.attractions
            )[0]
            if position in affected:
                names = "、".join(attr.name for attr in day.attractions)
                day.description = (
                    f"本日安排{names}，已综合游览时长和景点间交通调整顺序。"
                )
        return trip_plan

    # 不需要查门票的景点名称关键词
    _FREE_ENTRY_KW = {"夜市", "小吃街", "美食街", "步行街", "商业街", "广场",
                      "街区", "大排档", "美食城", "小吃城", "购物中心", "商场"}

    def _enforce_remote_one_per_day(
        self, trip_plan: TripPlan, candidates: List[dict]
    ) -> TripPlan:
        """硬约束：远郊地标 = 独立全天节点。

        1. 每天最多 1 个远郊景点
        2. 远郊地标当天应为唯一景点——移除当天的非远郊景点
        3. 被移除的非远郊景点尝试重新分配到有空位的天
        """
        if not candidates:
            return trip_plan

        # 从 candidates 收集所有远郊景点名称
        remote_names: set = set()
        for g in candidates:
            for m in g.get("members", []):
                if m.get("_is_remote"):
                    remote_names.add(m.get("name", ""))
                    remote_names.add(g.get("area_name", ""))

        if not remote_names:
            return trip_plan

        def _is_remote_attr(attr) -> bool:
            name = attr.name
            if name in remote_names:
                return True
            norm = re.sub(r'[（(].*?[)）]', '', name).replace(' ', '').strip()
            for rn in remote_names:
                rn_norm = re.sub(r'[（(].*?[)）]', '', rn).replace(' ', '').strip()
                if norm == rn_norm:
                    return True
                if len(norm) >= 6 and len(rn_norm) >= 6:
                    if norm in rn_norm or rn_norm in norm:
                        return True
            return False

        # Step 1: 远郊地标当天应为唯一景点，移除非远郊景点
        orphaned: list = []  # 被移除的非远郊景点
        affected_day_positions: set = set()
        for source_position, day in enumerate(trip_plan.days):
            remotes = [a for a in day.attractions if _is_remote_attr(a)]
            if remotes:
                non_remotes = [a for a in day.attractions if not _is_remote_attr(a)]
                if non_remotes:
                    orphaned.extend(non_remotes)
                    day.attractions = remotes  # 只保留远郊地标
                    affected_day_positions.add(source_position)
                    orphan_names = [a.name for a in non_remotes]
                    logger.info(
                        f"远郊独占 Day{day.day_index+1}: '{remotes[0].name}' 是远郊地标，"
                        f"移出同天的非远郊景点 {orphan_names}"
                    )

        # Step 2: 每天最多 1 个远郊（兜底：LLM 可能给同天放多个远郊）
        day_remotes: List[List] = []
        for day in trip_plan.days:
            day_remotes.append([a for a in day.attractions if _is_remote_attr(a)])

        overflow: List = []
        for i, (day, remotes) in enumerate(zip(trip_plan.days, day_remotes)):
            if len(remotes) > 1:
                keep_name = remotes[0].name
                day.attractions = [
                    a for a in day.attractions
                    if not _is_remote_attr(a) or a.name == keep_name
                ]
                overflow.extend(remotes[1:])
                affected_day_positions.add(i)
                moved_names = [a.name for a in remotes[1:]]
                logger.info(
                    f"远郊约束 Day{i+1}: 保留 '{keep_name}'，"
                    f"移出 {moved_names}"
                )

        if not overflow and not orphaned:
            return trip_plan

        # Step 3: 把溢出的远郊景点分配到没有远郊、且不满 3 个景点的天
        for attr in overflow:
            placed = False
            for i, (day, remotes) in enumerate(zip(trip_plan.days, day_remotes)):
                if len(remotes) == 0 and len(day.attractions) < 3:
                    attr.description = (
                        f"{attr.name}已根据远郊景点的全天游览约束调整至本日；"
                        "请结合当天交通和开放时间游览。"
                    )
                    day.attractions.append(attr)
                    day_remotes[i].append(attr)
                    affected_day_positions.add(i)
                    logger.info(f"远郊约束: '{attr.name}' → Day{i+1}")
                    placed = True
                    break
            if not placed:
                # 所有天都已有远郊或已满 → 追加到最后一天
                last_day = trip_plan.days[-1]
                if len(last_day.attractions) < 3:
                    attr.description = (
                        f"{attr.name}已调整至本日，具体游览顺序以当天交通条件为准。"
                    )
                    last_day.attractions.append(attr)
                    affected_day_positions.add(len(trip_plan.days) - 1)
                    logger.warning(
                        f"远郊约束: '{attr.name}' 无处可放，追加到最后一 day"
                    )

        # Step 4: 把从远郊天移除的非远郊景点分配到无远郊且有空位的天
        if orphaned:
            for attr in orphaned:
                placed = False
                for i, (day, remotes) in enumerate(zip(trip_plan.days, day_remotes)):
                    if len(remotes) == 0 and len(day.attractions) < 3:
                        attr.description = (
                            f"{attr.name}已根据远郊景点的全天游览约束调整至本日；"
                            "请结合当天交通和开放时间游览。"
                        )
                        day.attractions.append(attr)
                        affected_day_positions.add(i)
                        logger.info(f"远郊约束: 移回 '{attr.name}' → Day{i+1}")
                        placed = True
                        break
                if not placed:
                    logger.debug(f"远郊约束: '{attr.name}' 无合适空位，丢弃")

        # 景点发生跨日移动后，LLM 原始 summary/reason 已不再对应当前日期，
        # 必须用最终景点列表同步刷新受影响日期的文案。
        for position in affected_day_positions:
            day = trip_plan.days[position]
            names = "、".join(attraction.name for attraction in day.attractions)
            if not names:
                day.description = "本日以交通衔接和休整为主，未安排固定景点。"
            elif any(_is_remote_attr(attraction) for attraction in day.attractions):
                day.description = f"本日游览{names}，远郊景点按全天安排并预留往返时间。"
            else:
                day.description = f"本日安排{names}，行程已根据前后日期的游览强度重新调整。"

        return trip_plan

    @staticmethod
    def _opening_intervals(opening_hours: str) -> List[Tuple[int, int]]:
        """从高德开放时间文本中提取分钟区间；无法解析时返回空列表。"""
        text = str(opening_hours or "").strip()
        if not text:
            return []
        if any(marker in text for marker in ("暂停营业", "歇业", "永久关闭", "已关闭")):
            return [(-1, -1)]
        if any(marker in text for marker in ("24小时", "全天开放", "00:00-24:00")):
            return [(0, 24 * 60)]
        intervals: List[Tuple[int, int]] = []
        for start_hour, start_minute, end_hour, end_minute in re.findall(
            r"(\d{1,2}):(\d{2})\s*[-~～—至]\s*(\d{1,2}):(\d{2})",
            text,
        ):
            start = min(24 * 60, int(start_hour) * 60 + int(start_minute))
            end = min(24 * 60, int(end_hour) * 60 + int(end_minute))
            if end < start:
                end += 24 * 60
            intervals.append((start, end))
        return intervals

    @classmethod
    def _effective_attraction_opening_intervals(
        cls, attraction: Attraction
    ) -> List[Tuple[int, int]]:
        """返回真实开放时段；日间场馆缺数据时提供保守默认值。"""
        hours = attraction.ticket_info.opening_hours if attraction.ticket_info else ""
        intervals = cls._opening_intervals(hours)
        if not intervals:
            # 地图未给营业时间不等于全天开放。博物馆等日间场馆若被排到
            # 傍晚甚至午夜，应按安全默认时段处理；若后续拿到明确的夜场
            # 开放时间，上面的真实 intervals 会覆盖这里的默认值。
            venue_text = f"{attraction.name} {attraction.category or ''}"
            if any(marker in venue_text for marker in (
                "博物馆", "博物院", "纪念馆", "美术馆", "科技馆",
                "展览馆", "陈列馆", "图书馆",
            )):
                return [(9 * 60, 17 * 60)]
        return intervals

    @classmethod
    def _opening_fit_start(
        cls, attraction: Attraction, desired_start: int, duration: int
    ) -> Optional[int]:
        """返回能够完整游览的最早开始时间；日间场馆未知时使用保守时段。"""
        intervals = cls._effective_attraction_opening_intervals(attraction)
        if not intervals:
            return desired_start
        if intervals == [(-1, -1)]:
            return None
        for start, end in intervals:
            candidate_start = max(desired_start, start)
            if candidate_start + duration <= end:
                return candidate_start
        return None

    @classmethod
    def _timeline_item_is_clipped_at_midnight(cls, item: TimelineItem) -> bool:
        """判断时间轴节点是否因超过当天而被 _clock_text 截成 23:59。"""
        duration = max(0, int(item.duration_minutes or 0))
        if duration <= 0:
            return False
        start = cls._time_to_minutes(item.start_time)
        end = cls._time_to_minutes(item.end_time)
        if start is None:
            return False
        expected_end = start + duration
        return (
            start >= 23 * 60 + 59
            or expected_end > 23 * 60 + 59
            or (
                end is not None
                and end == 23 * 60 + 59
                and expected_end != end
            )
        )

    def _repair_opening_hour_conflicts(
        self, trip_plan: TripPlan, request: TripRequest
    ) -> TripPlan:
        """在餐厅搜索前移除当天已知无法完整游览的景点。"""
        for position, day in enumerate(trip_plan.days):
            # 闭馆较早的场馆优先放在白天前段；夜市、街区和开放时间未知的
            # 普通户外景点靠后。这样先尝试换序，而不是发现博物馆排太晚后
            # 直接删除。
            day.attractions.sort(key=lambda attraction: min(
                (
                    end for start, end
                    in self._effective_attraction_opening_intervals(attraction)
                    if start >= 0
                ),
                default=10 ** 9,
            ))
            current = 9 * 60
            available_meals = set(self._available_meal_types(request, day.day_index))
            if position == 0:
                ready = self._arrival_ready_minutes(request)
                if ready is not None:
                    current = max(current, ready)
                    if ready >= 11 * 60 and "lunch" in available_meals:
                        current = max(current, 11 * 60 + 30) + 90
            elif "breakfast" in available_meals:
                # 酒店→早餐、用餐、早餐→首景点的保守合计。
                current = 9 * 60 + 15
            previous_location = day.hotel.location if day.hotel else None
            kept: List[Attraction] = []
            removed: List[Attraction] = []
            lunch_split = max(1, math.ceil(len(day.attractions) / 2))
            for attraction_index, attraction in enumerate(day.attractions):
                if (
                    attraction_index == lunch_split
                    and "lunch" in available_meals
                    and not (position == 0 and self._arrival_ready_minutes(request) is not None
                             and self._arrival_ready_minutes(request) >= 11 * 60)
                ):
                    current = max(current, 11 * 60 + 30) + 90
                if previous_location:
                    current += self._estimated_location_transfer_minutes(
                        previous_location,
                        attraction.location,
                        request.transportation,
                        day.overnight_city or day.end_location or request.city,
                        use_amap=False,
                    )
                fitted_start = (self._single_city_visit_fit_start if request.trip_mode == "single_city" else self._opening_fit_start)(
                    attraction, current, attraction.visit_duration
                )
                if fitted_start is None:
                    removed.append(attraction)
                    continue
                current = fitted_start + attraction.visit_duration
                previous_location = attraction.location
                kept.append(attraction)
            if removed:
                day.attractions = kept
                for attraction in removed:
                    self._record_removed_attraction(
                        trip_plan, attraction, day.day_index,
                        "开放时间与当天可用时段冲突",
                    )
                names = "、".join(item.name for item in kept)
                day.description = (
                    f"本日安排{names}，已移除无法在闭园前完成游览的景点。"
                    if names else "本日暂不安排固定景点，原候选无法在闭园前完成游览。"
                )
                logger.warning(
                    f"开放时间修复 Day{position + 1}: "
                    f"移除 {[item.name for item in removed]}"
                )
        return trip_plan

    def _backfill_underfilled_days(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        candidates: Optional[List[dict]] = None,
    ) -> TripPlan:
        """为被去重、超时裁剪后仍明显过空的日期补入同片区优质候选。"""
        # 自驾环线仍由节点日规则独占处理；多城市则必须允许在真实路线、
        # 开放时间裁剪后，从当天所在城市的候选池补位。过去所有路线模式
        # 都直接跳过，导致多城市越校验景点越少。
        if request.trip_mode == "road_trip_loop" or not candidates:
            return trip_plan

        used = {
            self._normalize_attr_name(attraction.name)
            for day in trip_plan.days for attraction in day.attractions
        }
        if request.trip_mode == "multi_city":
            used.update(self._normalize_attr_name(r.attraction.name) for r in trip_plan.removed_attractions)
        tier_rank = {"classic": 4, "preference": 3, "local": 2, "supplement": 1}
        weather_by_date = {w.date: f"{w.day_weather}{w.night_weather}" for w in trip_plan.weather_info}

        for position, day in enumerate(trip_plan.days):
            capacity = self._day_visit_capacity(request, day, position)
            rainy = any(word in weather_by_date.get(day.date, "") for word in ("雨", "雪", "雷"))
            target = self._route_day_target_count(None, position, request)
            current_visit = sum(item.visit_duration for item in day.attractions)
            if request.travel_pace == "relaxed" and (
                current_visit >= 240 or self._is_remote_day(day)
            ):
                target = min(target, 1)
            forbidden = {self._normalize_attr_name(r.attraction.name) for r in trip_plan.removed_attractions if r.original_day_index == day.day_index} if request.trip_mode == "single_city" else set()
            day_candidates = candidates
            if request.trip_mode == "multi_city":
                expected_stop = day.end_location or day.overnight_city
                day_candidates = [
                    candidate for candidate in candidates
                    if self._candidate_route_stop(candidate) == expected_stop
                ]
                if self._multi_city_full_day_attraction(day):
                    continue
                # 换城日首先保证可执行，真实可用时间充足时才补第二个。
                if day.route_segment:
                    target = 2 if capacity >= 300 else (1 if capacity >= 90 else 0)
            if capacity < 180:
                target = min(target, 1)
            if capacity >= 300:
                target = max(target, 2)
            if len(day.attractions) >= target:
                continue

            while len(day.attractions) < target:
                anchor = (
                    day.attractions[-1].location
                    if day.attractions
                    else (day.hotel.location if day.hotel else None)
                )
                ranked = []
                for candidate in day_candidates:
                    options = [(
                        self._compact_candidate_pick(candidate),
                        self._candidate_seen_aliases(candidate),
                        bool(candidate.get("_is_remote") or any(
                            member.get("_is_remote")
                            for member in candidate.get("members", [])
                        )),
                    )]
                    # 普通大型地理聚簇不是一个原子景区；其内的真实
                    # POI 仍可独立补入下午空档。强父子景区仍保持原子性。
                    members = candidate.get("members", [])
                    if len(members) > 5 and not candidate.get("_strong_hierarchy"):
                        for member in members:
                            name = str(member.get("name", "") or "").strip()
                            if not name:
                                continue
                            options.append(({
                                "name": name,
                                "address": member.get("address", ""),
                                "lng": float(member.get("longitude", 0) or 0),
                                "lat": float(member.get("latitude", 0) or 0),
                                "category": member.get(
                                    "broad_category", member.get("category", "景点")
                                ),
                                "score": member.get("score", candidate.get("score", 0)),
                                "poi_id": member.get("id", ""),
                            }, {self._normalize_attr_name(name)}, bool(
                                member.get("_is_remote") or candidate.get("_is_remote")
                            )))

                    for pick, aliases, is_remote in options:
                        if request.trip_mode == "single_city" and any(self._is_dup_name(alias, forbidden) for alias in aliases | {self._normalize_attr_name(pick["name"])}):
                            continue
                        if request.trip_mode == "multi_city" and self._is_dup_name(self._normalize_attr_name(pick["name"]), used):
                            continue
                        if any(self._is_dup_name(alias, used) for alias in aliases):
                            continue
                        if not pick["lng"] or not pick["lat"]:
                            continue
                        # 抵达/离开日及已有景点的日期不再塞入远郊全天项目。
                        if is_remote and (day.attractions or position in {0, len(trip_plan.days) - 1}):
                            continue
                        # 多城市远郊项目只能来自前面的整日分块决策，不能在
                        # 路线校验删掉后又被普通补位重新塞回来。
                        if request.trip_mode == "multi_city" and is_remote:
                            continue
                        distance = 0.0
                        if anchor:
                            distance = self._haversine_distance(
                                anchor.longitude, anchor.latitude, pick["lng"], pick["lat"]
                            )
                            max_distance = 6.0 if capacity < 300 else 10.0
                            if not math.isfinite(distance) or distance > max_distance:
                                continue
                        duration = self._estimate_visit_duration(
                            pick["name"], pick["category"], request.preferences, candidate
                        )
                        current_visit = sum(item.visit_duration for item in day.attractions)
                        leg_buffer = {
                            "relaxed": 45,
                            "balanced": 30,
                            "compact": 20,
                        }.get(request.travel_pace, 30)
                        transfer_allowance = leg_buffer * (len(day.attractions) + 1)
                        if current_visit + duration + transfer_allowance > capacity:
                            continue
                        score = (
                            tier_rank.get(candidate.get("_candidate_tier", "supplement"), 0) * 100
                            + float(pick.get("score", candidate.get("score", 0)) or 0)
                            - distance * 4
                        )
                        ranked.append((int(rainy and not self._is_indoor_venue(pick["name"], pick["category"])), score, candidate, pick, duration, aliases))
                if not ranked:
                    break
                _, _, candidate, pick, duration, aliases = min(ranked, key=lambda item: (item[0], -item[1]))
                attraction = self._make_attraction(pick)
                attraction.visit_duration = duration
                if request.trip_mode == "single_city":
                    source = next((m for m in candidate.get("members", []) if m.get("id") == attraction.poi_id), candidate)
                    if source.get("opening_hours"):
                        attraction.ticket_info = TicketInfo(opening_hours=source["opening_hours"], source="amap")
                    trial_day = day.model_copy(deep=True)
                    trial_day.attractions.append(attraction)
                    trial = trip_plan.model_copy(deep=True)
                    trial.days[position] = trial_day
                    self._repair_opening_hour_conflicts(trial, request)
                    if {a.name for a in trial.days[position].attractions} != {a.name for a in [*day.attractions, attraction]}:
                        forbidden.update(aliases or {self._normalize_attr_name(attraction.name)})
                        continue
                attraction.description = (
                    f"{attraction.name}与本日已有安排位于相近区域，"
                    "用于补足被开放时间或路线约束移除后的游览空档。"
                )
                day.attractions.append(attraction)
                used.update(aliases or {self._normalize_attr_name(attraction.name)})
                logger.info(
                    f"空档补位 Day{position + 1}: 新增 '{attraction.name}'"
                )

            if day.attractions:
                day.description = (
                    f"本日安排{'、'.join(item.name for item in day.attractions)}，"
                    "已按经典程度、同片区距离和可用时间补足行程。"
                )
        return trip_plan

    def _enforce_time_constraints(
        self, trip_plan: TripPlan, request: TripRequest, candidates: List[dict]
    ) -> TripPlan:
        """后处理：时间约束兜底——Day1/最后一天强制限制景点数量和类型。

        必须在 _ensure_day_count 之后运行（它可能回填过多景点）。
        复用与 _enforce_remote_one_per_day 相同的远郊名称匹配逻辑。
        """
        if not request.arrival_time and not request.departure_time:
            return trip_plan
        original_day_names = [
            [attraction.name for attraction in day.attractions]
            for day in trip_plan.days
        ]
        original_day_attractions = [list(day.attractions) for day in trip_plan.days]

        def record_removed_by_time() -> None:
            remaining = {
                attraction.poi_id or self._normalize_attr_name(attraction.name)
                for current_day in trip_plan.days
                for attraction in current_day.attractions
            }
            for original_day, attractions in zip(
                trip_plan.days, original_day_attractions
            ):
                for attraction in attractions:
                    key = attraction.poi_id or self._normalize_attr_name(
                        attraction.name
                    )
                    if key not in remaining:
                        self._record_removed_attraction(
                            trip_plan, attraction, original_day.day_index,
                            "抵达或离开时间不足，系统暂未排入",
                        )
        city_center = self._get_city_center(request.city)

        # ---- 构建远郊名称集合（复用相同匹配逻辑） ----
        remote_names: set = set()
        for g in (candidates or []):
            for m in g.get("members", []):
                if m.get("_is_remote"):
                    remote_names.add(m.get("name", ""))
                    remote_names.add(g.get("area_name", ""))

        def _is_remote_attr(attr) -> bool:
            name = attr.name
            if name in remote_names:
                return True
            norm = re.sub(r'[（(].*?[)）]', '', name).replace(' ', '').strip()
            for rn in remote_names:
                rn_norm = re.sub(r'[（(].*?[)）]', '', rn).replace(' ', '').strip()
                if norm == rn_norm:
                    return True
                if len(norm) >= 6 and len(rn_norm) >= 6:
                    if norm in rn_norm or rn_norm in norm:
                        return True
            return False

        def _is_geographically_remote(attr) -> bool:
            if not city_center or not attr.location:
                return False
            distance = self._haversine_distance(
                attr.location.longitude,
                attr.location.latitude,
                city_center[0],
                city_center[1],
            )
            return math.isfinite(distance) and distance > 12

        def _requires_daytime_admission(attr) -> bool:
            text = f"{attr.name} {attr.category or ''} {attr.description or ''}"
            return any(keyword in text for keyword in (
                "博物馆", "博物院", "纪念馆", "美术馆", "科技馆", "展览馆",
                "陈列馆", "文化馆", "图书馆", "故居", "动物园", "海洋馆",
                "主题乐园", "游乐园",
            ))

        # ---- Day 1: 抵达约束 ----
        if (request.arrival_time
                and request.arrival_transport
                and request.arrival_transport != "已在此地"):
            classification = self._classify_arrival_time(request.arrival_time)
            day = trip_plan.days[0]
            available_minutes = self._day_visit_capacity(request, day, 0)

            if available_minutes < 60:
                removed_names = [a.name for a in day.attractions]
                day.attractions = []
                day.description = (
                    "傍晚抵达，不安排景点，预留出站、进城、办理入住和晚餐时间。"
                )
                if removed_names:
                    logger.info(
                        "时间约束 Day1: 可游玩时间不足60分钟，"
                        f"移出 {removed_names}"
                    )
            else:
                ready = self._arrival_ready_minutes(request)
                if ready is not None and ready >= 16 * 60:
                    daytime_names = [
                        a.name for a in day.attractions
                        if _requires_daytime_admission(a)
                    ]
                    day.attractions = [
                        a for a in day.attractions
                        if not _requires_daytime_admission(a)
                    ]
                    if daytime_names:
                        logger.info(
                            "时间约束 Day1: 傍晚开始游览，移出日间场馆 "
                            f"{daytime_names}"
                        )
                    if not day.attractions:
                        day.description = (
                            "傍晚抵达，优先办理入住和用餐；未安排可能已经停止入场的日间场馆。"
                        )

            if classification in ('afternoon', 'late'):
                max_attrs = 1 if classification == 'late' else 2

                before = len(day.attractions)
                day.attractions = [a for a in day.attractions if not _is_remote_attr(a)]
                removed = before - len(day.attractions)
                if removed:
                    logger.info(
                        f"时间约束 Day1: 移除{removed}个远郊景点（{classification}抵达）"
                    )

                if len(day.attractions) > max_attrs:
                    overflow_names = [a.name for a in day.attractions[max_attrs:]]
                    day.attractions = day.attractions[:max_attrs]
                    logger.info(
                        f"时间约束 Day1: 截断至{max_attrs}个，移出 {overflow_names}"
                    )

        # ---- 最后一天: 离开约束（多日游） ----
        if request.departure_time:
            classification = self._classify_departure_time(request.departure_time)
            last_day = trip_plan.days[-1]
            available_minutes = self._day_visit_capacity(
                request, last_day, len(trip_plan.days) - 1
            )

            # 先以真实时间窗口为准。即使模型输出了景点，只要扣除退房、
            # 取行李和前往交通枢纽的缓冲后不足 60 分钟，就必须清空，
            # 不能仅凭“早上离开可安排 0-1 个”保留一个 2-3 小时景点。
            if available_minutes < 60:
                removed_names = [a.name for a in last_day.attractions]
                last_day.attractions = []
                last_day.description = (
                    "早上离开，不安排景点，预留退房、取行李和前往机场或车站的时间。"
                )
                if removed_names:
                    logger.info(
                        "时间约束 最后一天: 可游玩时间不足60分钟，"
                        f"移出 {removed_names}"
                    )
                record_removed_by_time()
                return trip_plan

            departure = self._time_to_minutes(request.departure_time) or 0
            if departure < 18 * 60:
                remote_names_removed = [
                    attraction.name
                    for attraction in last_day.attractions
                    if _is_remote_attr(attraction) or _is_geographically_remote(attraction)
                ]
                last_day.attractions = [
                    attraction
                    for attraction in last_day.attractions
                    if not (
                        _is_remote_attr(attraction)
                        or _is_geographically_remote(attraction)
                    )
                ]
                if remote_names_removed:
                    logger.info(
                        "时间约束 最后一天: 按最终坐标移除返程前远郊景点 "
                        f"{remote_names_removed}"
                    )

            if classification in ('early', 'afternoon'):
                max_attrs = 0 if classification == 'early' else 1

                before = len(last_day.attractions)
                last_day.attractions = [
                    a for a in last_day.attractions
                    if not (_is_remote_attr(a) or _is_geographically_remote(a))
                ]
                removed = before - len(last_day.attractions)
                if removed:
                    logger.info(
                        f"时间约束 最后一天: 移除{removed}个远郊景点（{classification}离开）"
                    )

                high_commitment_keywords = (
                    "主题乐园", "游乐园", "乐园", "欢乐谷", "动物园",
                    "海洋馆", "水族馆", "国家公园", "大峡谷", "风景区",
                )
                if departure < 18 * 60:
                    removed_names = [
                        attraction.name for attraction in last_day.attractions
                        if any(keyword in f"{attraction.name} {attraction.category or ''}" for keyword in high_commitment_keywords)
                    ]
                    last_day.attractions = [
                        attraction for attraction in last_day.attractions
                        if not any(keyword in f"{attraction.name} {attraction.category or ''}" for keyword in high_commitment_keywords)
                    ]
                    if removed_names:
                        logger.info(
                            f"时间约束 最后一天: 移除高时间投入景点 {removed_names}"
                        )

                if len(last_day.attractions) > max_attrs:
                    overflow_names = [a.name for a in last_day.attractions[max_attrs:]]
                    last_day.attractions = last_day.attractions[:max_attrs]
                    logger.info(
                        f"时间约束 最后一天: 截断至{max_attrs}个，移出 {overflow_names}"
                    )

            # 不只限制“几个景点”，还要确保实际时间放得下。离开日
            # 每个候选均计入确定性游览时长，并为酒店→景点、景点
            # →酒店预留各 30 分钟。多景点时中间路程同样计时。
            fitted = []
            fitted_minutes = 0
            removed_for_budget = []
            for attraction in last_day.attractions:
                estimate = self._estimate_visit_duration(
                    attraction.name,
                    attraction.category or "",
                    request.preferences,
                )
                tentative_count = len(fitted) + 1
                required = fitted_minutes + estimate + 30 * (tentative_count + 1)
                if required <= available_minutes:
                    attraction.visit_duration = estimate
                    fitted.append(attraction)
                    fitted_minutes += estimate
                else:
                    removed_for_budget.append(attraction.name)
            last_day.attractions = fitted
            if removed_for_budget:
                logger.info(
                    "时间约束 最后一天: 游览+市内往返时间超出窗口，"
                    f"移出 {removed_for_budget}"
                )
            if not last_day.attractions:
                last_day.description = (
                    "离开日不安排固定景点，预留早餐、退房、取行李"
                    "以及前往机场或车站的时间。"
                )

        for position, day in enumerate(trip_plan.days):
            current_names = [attraction.name for attraction in day.attractions]
            if current_names and current_names != original_day_names[position]:
                day.description = (
                    f"本日安排{'、'.join(current_names)}，"
                    "已根据抵达或返程时间重新校正。"
                )

        record_removed_by_time()
        return trip_plan

    def _ensure_scenic_route_node_attractions(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        candidates: List[dict],
        route_itinerary: List[dict],
    ) -> TripPlan:
        """景区型路线节点必须真正进入当天景点，而不只是路线/住宿文字。"""
        if not self._is_route_mode(request) or not candidates or not route_itinerary:
            return trip_plan

        excluded_service_words = ("停车场", "售票处", "游客中心", "集散中心", "服务区")
        processed_nodes: set[str] = set()
        for item in route_itinerary:
            if item.get("node_type") != "scenic_area":
                continue
            scenic_node = item.get("activity_location") or item.get("end_location")
            if not scenic_node or scenic_node in processed_nodes:
                continue
            processed_nodes.add(scenic_node)
            related_items = [
                candidate_item for candidate_item in route_itinerary
                if candidate_item.get("node_type") == "scenic_area"
                and (
                    candidate_item.get("activity_location")
                    or candidate_item.get("end_location")
                ) == scenic_node
            ]

            def target_rank(candidate_item: dict) -> tuple:
                candidate_index = candidate_item.get("day_index")
                attraction_count = (
                    len(trip_plan.days[candidate_index].attractions)
                    if isinstance(candidate_index, int)
                    and 0 <= candidate_index < len(trip_plan.days)
                    else 0
                )
                available_minutes = (
                    self._day_visit_capacity(
                        request,
                        trip_plan.days[candidate_index],
                        candidate_index,
                    )
                    if isinstance(candidate_index, int)
                    and 0 <= candidate_index < len(trip_plan.days)
                    else 0
                )
                return (
                    1 if available_minutes >= 240 else 0,
                    available_minutes,
                    1 if not candidate_item.get("route_segment") else 0,
                    1 if not candidate_item.get("is_transit_day") else 0,
                    -attraction_count,
                    -(candidate_index or 0),
                )

            item = max(related_items, key=target_rank)
            day_index = item.get("day_index")
            if not scenic_node or not isinstance(day_index, int) or day_index >= len(trip_plan.days):
                continue

            matches = []
            for group in candidates:
                group_stop = group.get("route_stop")
                members = group.get("members") or []
                if not group_stop and members:
                    group_stop = members[0].get("route_stop")
                if group_stop != scenic_node:
                    continue
                for member in members:
                    name = member.get("name", "")
                    if (
                        self._matches_scenic_route_node(scenic_node, name)
                        and not any(word in name for word in excluded_service_words)
                    ):
                        matches.append((group, member))

            if not matches:
                logger.warning(f"景区节点未找到同名核心 POI: {scenic_node}")
                continue

            def core_rank(pair: tuple) -> tuple:
                _group, member = pair
                name = member.get("name", "")
                normalized = self._route_name_key(name)
                node_key = self._route_name_key(scenic_node)
                if "国家风景区" in name:
                    official_bonus = 4
                elif "景区" in name:
                    official_bonus = 3
                elif "国家公园" in name:
                    official_bonus = 2
                elif "自然保护区" in name:
                    official_bonus = 1
                else:
                    official_bonus = 0
                lake_bonus = 1 if name.endswith(("湖", "措", "错")) else 0
                exact_bonus = 3 if normalized == node_key else 0
                return (
                    exact_bonus + official_bonus + lake_bonus,
                    float(member.get("score", 0) or 0),
                    -len(name),
                )

            group, member = max(matches, key=core_rank)
            raw_rating = member.get("rating")
            try:
                rating = float(raw_rating) if raw_rating not in (None, "", [], {}) else None
            except (TypeError, ValueError):
                rating = None
            core_attraction = Attraction(
                name=member.get("name") or group.get("display_name") or scenic_node,
                address=member.get("address") or group.get("address", ""),
                location=Location(
                    longitude=float(member.get("longitude") or group.get("longitude") or 0),
                    latitude=float(member.get("latitude") or group.get("latitude") or 0),
                ),
                visit_duration=240,
                description=(
                    f"{scenic_node}是本日路线的核心景区，抵达后应优先完成游览；"
                    "具体开放范围和游览线路以景区当天公告为准。"
                ),
                category=member.get("broad_category") or member.get("category") or "风景名胜",
                rating=rating,
                poi_id=member.get("id", ""),
                ticket_price=0,
            )

            day = trip_plan.days[day_index]
            for related_item in related_items:
                other_index = related_item.get("day_index")
                if (
                    not isinstance(other_index, int)
                    or other_index == day_index
                    or not 0 <= other_index < len(trip_plan.days)
                ):
                    continue
                trip_plan.days[other_index].attractions = [
                    attraction
                    for attraction in trip_plan.days[other_index].attractions
                    if not self._matches_scenic_route_node(
                        scenic_node, attraction.name
                    )
                ]
            remaining = [
                attraction for attraction in day.attractions
                if not self._matches_scenic_route_node(scenic_node, attraction.name)
            ]
            if day.is_transit_day or group.get("_is_remote") or member.get("_is_remote"):
                day.attractions = [core_attraction]
            else:
                day.attractions = [core_attraction, *remaining[:2]]
            logger.info(
                f"景区节点强制落位: Day{day_index + 1} {scenic_node} → "
                f"{core_attraction.name}，住宿服务地={item.get('overnight_city')}"
            )
        return trip_plan

    def _enforce_route_attraction_consistency(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        candidates: List[dict],
    ) -> TripPlan:
        """把已核验景点限制在所属路线节点，修正 LLM 的跨城错排。

        只处理能够从候选池可靠识别所属节点的景点；未知名称保留给后续质量
        校验，避免仅凭字符串猜城市。
        """
        if not self._is_route_mode(request) or not candidates:
            return trip_plan

        alias_to_stop: Dict[str, str] = {}
        for group in candidates:
            members = group.get("members", [])
            route_stop = group.get("route_stop")
            if not route_stop and members:
                route_stop = members[0].get("route_stop")
            if not route_stop:
                continue
            aliases = [
                group.get("area_name", ""),
                group.get("display_name", ""),
                *(member.get("name", "") for member in members),
            ]
            for alias in aliases:
                normalized = self._normalize_attr_name(alias)
                if normalized:
                    alias_to_stop.setdefault(normalized, route_stop)

        def resolve_stop(name: str) -> Optional[str]:
            normalized = self._normalize_attr_name(name)
            direct = alias_to_stop.get(normalized)
            if direct:
                return direct
            for alias, stop in alias_to_stop.items():
                if len(normalized) >= 6 and len(alias) >= 6:
                    if normalized in alias or alias in normalized:
                        return stop
            return None

        def allowed_stops(day: DayPlan) -> set:
            return {
                value for value in (
                    day.start_location,
                    day.end_location,
                    day.overnight_city,
                ) if value
            }

        misplaced = []
        for day_index, day in enumerate(trip_plan.days):
            kept = []
            allowed = allowed_stops(day)
            for attraction in day.attractions:
                stop = resolve_stop(attraction.name)
                if stop and stop not in allowed:
                    misplaced.append((attraction, stop, day_index))
                else:
                    kept.append(attraction)
            day.attractions = kept

        moved = 0
        dropped = 0
        used_names = {
            self._normalize_attr_name(attraction.name)
            for day in trip_plan.days
            for attraction in day.attractions
        }
        for attraction, stop, original_day_index in misplaced:
            normalized = self._normalize_attr_name(attraction.name)
            if normalized in used_names:
                continue
            compatible = [
                day for day in trip_plan.days
                if stop in allowed_stops(day)
                and len(day.attractions) < (1 if day.is_transit_day else 3)
            ]
            compatible.sort(key=lambda day: (
                day.is_transit_day,
                abs(day.day_index - original_day_index),
            ))
            if compatible:
                compatible[0].attractions.append(attraction)
                used_names.add(normalized)
                moved += 1
            else:
                dropped += 1

        # 回填和重新分配都可能发生在路线元数据写入之后，最终再次限制赶路日。
        for day in trip_plan.days:
            if day.is_transit_day and len(day.attractions) > 1:
                day.attractions = day.attractions[:1]

        if moved or dropped:
            logger.info(f"跨城景点校正: 移动{moved}个，移除{dropped}个无法落位的景点")
        return trip_plan

    def _restore_attraction_names(
        self, trip_plan: TripPlan, candidates: List[dict]
    ) -> TripPlan:
        """将模型景点重新落到真实 POI；名称、坐标、地址和 ID 必须同步。"""
        if not candidates:
            return trip_plan

        def clean(value: str) -> str:
            return re.sub(r'[（(].*?[)）]', '', value or '').replace(' ', '').strip()

        alias_to_poi: Dict[str, dict] = {}
        poi_by_id: Dict[str, dict] = {}
        for group in candidates:
            members = group.get("members") or [group]
            display_name = group.get("display_name") or group.get("area_name") or ""
            primary = next(
                (member for member in members if member.get("name") == display_name),
                members[0],
            )
            for member in members:
                member_id = str(member.get("id", "") or "")
                if member_id:
                    poi_by_id[member_id] = member
                member_norm = clean(member.get("name", ""))
                if member_norm:
                    alias_to_poi.setdefault(member_norm, member)
                    stripped = self._strip_classifiers(member_norm)
                    if len(stripped) >= 2:
                        alias_to_poi.setdefault(stripped, member)
            for alias in (display_name, group.get("area_name", "")):
                alias_norm = clean(alias)
                if alias_norm:
                    alias_to_poi.setdefault(alias_norm, primary)
                    stripped = self._strip_classifiers(alias_norm)
                    if len(stripped) >= 2:
                        alias_to_poi.setdefault(stripped, primary)

        restored = 0
        grounded = 0
        for day in trip_plan.days:
            for attr in day.attractions:
                attr_norm = clean(attr.name)
                matched = poi_by_id.get(str(attr.poi_id or "")) or alias_to_poi.get(attr_norm)
                if matched is None:
                    fuzzy_matches = {
                        id(poi): poi
                        for alias, poi in alias_to_poi.items()
                        if len(attr_norm) >= 6 and len(alias) >= 6
                        and (attr_norm in alias or alias in attr_norm)
                    }
                    if len(fuzzy_matches) == 1:
                        matched = next(iter(fuzzy_matches.values()))
                if matched is None:
                    continue

                real_name = str(matched.get("name", "") or attr.name)
                if real_name != attr.name:
                    logger.debug(f"名字还原: '{attr.name}' → '{real_name}'")
                    attr.name = real_name
                    restored += 1
                longitude = float(matched.get("longitude", 0) or 0)
                latitude = float(matched.get("latitude", 0) or 0)
                if longitude and latitude:
                    attr.location = Location(longitude=longitude, latitude=latitude)
                if matched.get("address"):
                    attr.address = matched["address"]
                if matched.get("id"):
                    attr.poi_id = str(matched["id"])
                category = matched.get("broad_category") or matched.get("category")
                if category:
                    attr.category = category
                raw_rating = matched.get("rating")
                try:
                    if raw_rating not in (None, "", [], {}):
                        attr.rating = float(raw_rating)
                except (TypeError, ValueError):
                    pass
                grounded += 1

        if restored:
            logger.info(f"名字还原: 修复了 {restored} 个景点名")
        logger.info(f"景点POI落位: {grounded} 个景点已同步真实坐标和地址")
        return trip_plan

    def _ensure_day_count(
        self,
        trip_plan: TripPlan,
        request: TripRequest,
        candidates: List[dict],
        route_itinerary: Optional[List[dict]] = None,
    ) -> TripPlan:
        """确保行程天数 == request.travel_days。

        LLM 有时输出的天数比请求的少（候选池不够时倾向少输出而不是复用），
        这里补齐缺失的天，并从落选候选中自动填充景点。
        """
        from datetime import datetime, timedelta

        existing_count = len(trip_plan.days)

        if existing_count >= request.travel_days:
            return trip_plan

        logger.warning(
            f"天数不足: LLM输出{existing_count}天, 需要{request.travel_days}天, 补齐中..."
        )

        # 收集已使用的候选名。小团和明确父子组整体互斥；普通大型
        # 区域聚簇的成员仍可独立使用。
        seen_normalized: set = set()
        atomic_group_name_sets: List[set] = []
        for g in (candidates or []):
            names = self._candidate_seen_aliases(g)
            if len(g.get("members", [])) <= 5 or g.get("_strong_hierarchy"):
                atomic_group_name_sets.append(names)

        def _expand_seen(name: str) -> None:
            norm = self._normalize_attr_name(name)
            seen_normalized.add(norm)
            for gs in atomic_group_name_sets:
                if norm in gs:
                    for n in gs:
                        seen_normalized.add(n)
                    break

        for day in trip_plan.days:
            for attr in day.attractions:
                _expand_seen(attr.name)

        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")

        # 创建缺失的天
        for i in range(existing_count, request.travel_days):
            current_date = start_date + timedelta(days=i)
            new_day = DayPlan(
                date=current_date.strftime("%Y-%m-%d"),
                day_index=i,
                description=f"第{i+1}天：探索{request.city}更多精彩",
                transportation=request.transportation,
                accommodation=request.accommodation,
                attractions=[],
                meals=[],
            )
            trip_plan.days.append(new_day)
            logger.info(f"天数补齐: 创建 Day{i+1}")

        # 从落选候选中为不足 MIN_PER_DAY 的天（含新创建的）补位
        if candidates:
            # 预计算远郊地标名称——远郊地标全天独占，不强制 MIN_PER_DAY
            remote_landmark_names: set = set()
            for g in candidates:
                is_remote = g.get("_is_remote", False)
                is_core = bool(g.get("_is_core") or g.get("_landmark_match"))
                if is_remote and is_core:
                    for m in g.get("members", []):
                        remote_landmark_names.add(self._normalize_attr_name(m.get("name", "")))
                    remote_landmark_names.add(self._normalize_attr_name(g.get("area_name", "")))
                    remote_landmark_names.add(self._normalize_attr_name(g.get("display_name", "")))

            def _day_has_remote_landmark(day) -> bool:
                for attr in day.attractions:
                    if self._normalize_attr_name(attr.name) in remote_landmark_names:
                        return True
                return False

            backfilled = 0
            for day in trip_plan.days:
                if day.attractions:
                    continue
                if _day_has_remote_landmark(day):
                    continue  # 远郊地标 = 独立全天，不补位
                day_candidates = self._route_day_candidates(
                    candidates, route_itinerary, day.day_index
                )
                target_count = self._route_day_target_count(
                    route_itinerary, day.day_index, request
                )
                if route_itinerary and not day_candidates:
                    logger.warning(
                        f"天数补齐-补位跳过: Day{day.day_index+1} 的路线节点没有可靠景点候选"
                    )
                    continue
                while len(day.attractions) < target_count:
                    pick = self._pick_next_attr(day_candidates, seen_normalized)
                    if pick is None:
                        break  # 候选池彻底耗尽（组级+成员级都用完）
                    seen_normalized.update(
                        pick.get("_seen_aliases")
                        or {self._normalize_attr_name(pick["name"])}
                    )
                    new_attr = self._make_attraction(pick)
                    day.attractions.append(new_attr)
                    backfilled += 1
                    logger.info(
                        f"天数补齐-补位: Day{day.day_index+1} 新增 '{pick['name']}' "
                        f"(score={pick['score']:.1f})"
                    )
                    if pick.get("_is_remote"):
                        break
            if backfilled > 0:
                logger.info(f"天数补齐: 共补入 {backfilled} 个景点")

        return trip_plan

    def _backfill_attractions_from_description(
        self, trip_plan: TripPlan, candidates: List[dict]
    ) -> TripPlan:
        """
        兜底：如果某天 description 有内容但 attractions 数组为空，
        从 description 文本中匹配候选景点名，回填到 attractions 数组。
        解决 LLM 有时只在 description 里写景点但漏填结构化数组的问题。
        """
        if not candidates:
            logger.debug("回填跳过：候选景点列表为空")
            return trip_plan

        # 按长度降序排列候选名，优先匹配长名（避免"茂陵"匹配到"茂陵博物馆"之前）
        candidate_names = sorted(
            [(c.get("name", ""), c) for c in candidates],
            key=lambda x: len(x[0]), reverse=True,
        )

        filled_count = 0
        for day in trip_plan.days:
            if day.attractions:
                continue  # 已有 attractions，跳过
            if not day.description:
                continue

            desc = day.description
            matched: List[tuple] = []  # (position, candidate_dict)

            for name, cand in candidate_names:
                if not name or len(name) < 2:
                    continue
                pos = desc.find(name)
                if pos >= 0:
                    # 避免同一候选被重复匹配
                    if name not in {c.get("name") for _, c in matched}:
                        matched.append((pos, cand))
                        logger.debug(f"回填匹配: Day{day.day_index} '{name}' @ pos {pos}")

            if not matched:
                continue

            # 按在 description 中出现位置排序
            matched.sort(key=lambda x: x[0])

            for _, cand in matched:
                try:
                    lng = float(cand.get("longitude", 0))
                    lat = float(cand.get("latitude", 0))
                except (ValueError, TypeError):
                    lng, lat = 0.0, 0.0

                attr = Attraction(
                    name=cand.get("name", ""),
                    address=cand.get("address", ""),
                    location=Location(longitude=lng, latitude=lat),
                    visit_duration=120,
                    description=cand.get("type", "") or "景点",
                    category=cand.get("broad_category", "景点"),
                    rating=cand.get("rating"),
                    poi_id=cand.get("id", ""),
                    ticket_price=0,
                )
                day.attractions.append(attr)
                filled_count += 1

        if filled_count > 0:
            logger.info(f"回填兜底: 从 description 恢复了 {filled_count} 个景点到 attractions 数组")
        return trip_plan

    def _enrich_ticket_info(self, trip_plan: TripPlan) -> TripPlan:
        """后处理：并行查询门票和开放时间"""
        try:
            city = trip_plan.city
            all_attrs: List[Tuple] = []
            for day in trip_plan.days:
                for attr in day.attractions:
                    all_attrs.append((day, attr))

            if not all_attrs:
                return trip_plan

            # 过滤掉不需要查门票的（夜市、小吃街、步行街等）
            to_query = []
            for day, attr in all_attrs:
                existing_source = (
                    str(attr.ticket_info.source or "") if attr.ticket_info else ""
                )
                if attr.ticket_info is not None and any(
                    source in existing_source for source in ("amap", "tavily", "classified")
                ):
                    continue
                if any(kw in attr.name for kw in self._FREE_ENTRY_KW):
                    attr.ticket_price = 0
                    attr.ticket_info = TicketInfo(
                        price=0, opening_hours="",
                        purchase_method="免费区域，无需购票", source="classified",
                        confidence="medium",
                    )
                    logger.debug(f"跳过门票查询: {attr.name} (非收费场所)")
                else:
                    to_query.append((day, attr))

            if not to_query:
                return trip_plan

            logger.info(f"并行查询 {len(to_query)} 个景点门票...")

            def _query_single(name: str) -> TicketInfo:
                amap_service = get_amap_service()
                opening_hours = amap_service.get_opening_hours(name, city)
                ticket_info = query_ticket_info(name, city)
                if opening_hours:
                    ticket_info.opening_hours = opening_hours
                    ticket_info.source = f"amap+{ticket_info.source}"
                return ticket_info

            names = [attr.name for _, attr in to_query]
            with ThreadPoolExecutor(max_workers=min(5, len(names))) as executor:
                futures = {executor.submit(_query_single, name): (day, attr)
                          for name, (day, attr) in zip(names, to_query)}
                for future in as_completed(futures):
                    day, attr = futures[future]
                    try:
                        ticket_info = future.result()
                        attr.ticket_price = ticket_info.price
                        attr.ticket_info = ticket_info
                    except Exception as e:
                        logger.warning(f"{attr.name} 门票查询失败: {e}")

            return trip_plan
        except Exception as e:
            logger.warning(f"门票信息填充失败: {e}")
            import traceback
            traceback.print_exc()
            return trip_plan

    # ---- 酒店选址（密度锚点 + 周边搜） ----

    # 住宿档次 → 高德 POI typecode（精筛）
    # key 同时覆盖内部名称和前端 UI 实际传值，避免 mismatch
    _HOTEL_TIER_TYPECODES: dict = {
        "经济型": "100105",
        "经济型酒店": "100105",
        "舒适型": "100103|100104",
        "舒适型酒店": "100103|100104",
        "豪华型": "100101|100102",
        "豪华酒店": "100101|100102",
        "民宿": "100200|100201",              # 民宿 → 旅馆/青旅类别
    }
    # 兜底：同档不够时扩大到所有酒店 typecode
    _ALL_HOTEL_TYPECODES = "100101|100102|100103|100104|100105"

    # 非酒店名称关键词 — 即使高德返回也一律排除（兜底）
    _NON_HOTEL_KW: set = {
        "特步", "安踏", "李宁", "耐克", "阿迪达斯", "乔丹", "361°", "匹克",
        "鸿星尔克", "森马", "海澜之家", "优衣库",
        "便利店", "超市", "烟酒", "五金", "建材", "汽修", "洗车",
        "服装", "专卖店", "手机", "数码", "眼镜", "药房", "诊所",
        "美发", "美甲", "足浴", "按摩", "KTV", "网吧", "台球",
        "中介", "地产", "快递", "银行", "移动", "联通", "电信",
        "SALOMON", "NIKE", "ADIDAS", "PUMA",
        "驿站", "服务区", "停车区", "养护工区", "收费站",
        "洗浴", "桑拿", "会所", "歌舞厅", "夜总会",
        "烘焙", "烘培", "蛋糕", "餐厅", "小吃店",
        # 非标准住宿 — 民宿/青旅/公寓等不是酒店
        "民宿", "客栈", "青年旅舍", "青旅", "公寓", "日租", "短租",
        "家庭旅馆", "招待所",
    }
    _PROVINCE_REGION_NAMES: set = {
        "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
        "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
        "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
        "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆", "香港", "澳门",
    }

    # 非景点名称关键词 — 民宿、养发、美发等明显不是景点的 POI 一律排除
    _NON_ATTRACTION_NAME_KW: set = {
        "民宿", "客栈", "青年旅舍", "公寓", "日租", "短租",
        "养发", "美发", "理发", "烫染", "造型",
        "美甲", "美睫", "纹眉", "足浴", "按摩", "推拿", "采耳",
        "KTV", "网吧", "台球", "棋牌", "麻将",
        "便利店", "超市", "烟酒", "五金", "药房", "诊所", "牙科",
        "中介", "地产", "快递", "银行", "移动", "联通", "电信",
        "服装", "内衣", "鞋店", "眼镜", "手机", "数码", "家电",
        "文具", "琴行", "古玩", "佛具",
        # 景区附属设施：名称可能包含著名景点，但本身不是可游览 POI。
        "取水点", "饮水点", "饮水处", "直饮水", "供水点",
        "卫生间", "公共厕所", "母婴室", "医务室",
        "停车场", "停车区", "售票处", "售票点", "检票口",
        "游客中心", "游客服务中心", "客服中心", "管理处",
        "入口", "出口", "出入口", "换乘点", "集散点",
    }

    def _hotel_density_anchor(self, trip_plan: TripPlan) -> Tuple[float, float]:
        """找到景点密度最高的锚点坐标。

        对每个景点，数周围 5km 内有多少其他景点（跨天累计），
        返回密度最高的那个景点的坐标。平局时取距离所有景点质心最近的那个。
        """
        # 收集所有景点坐标
        points: List[Tuple[float, float, str]] = []  # (lng, lat, name)
        for day in trip_plan.days:
            for attr in day.attractions:
                if attr.location and attr.location.longitude and attr.location.latitude:
                    points.append((attr.location.longitude, attr.location.latitude, attr.name))

        if not points:
            logger.warning("没有景点坐标，无法计算锚点")
            return (0.0, 0.0)
        if len(points) == 1:
            logger.info(f"仅1个景点，锚点: {points[0][2]} ({points[0][0]:.4f},{points[0][1]:.4f})")
            return (points[0][0], points[0][1])

        # 算每个点的密度（5km 内的邻居数）
        NEIGHBOR_RADIUS_KM = 5.0
        densities: List[Tuple[int, float, float, float, str]] = []  # (count, lng, lat, dist_to_centroid, name)
        for lng1, lat1, name1 in points:
            count = sum(
                1 for lng2, lat2, _name2 in points
                if (lng1, lat1) != (lng2, lat2)
                and self._haversine_distance(lng1, lat1, lng2, lat2) <= NEIGHBOR_RADIUS_KM
            )
            densities.append((count, lng1, lat1, 0.0, name1))

        # 按密度降序
        densities.sort(key=lambda x: x[0], reverse=True)
        top_density = densities[0][0]

        if top_density == 0:
            # 所有点都孤立：直接用质心
            avg_lng = sum(p[0] for p in points) / len(points)
            avg_lat = sum(p[1] for p in points) / len(points)
            logger.info(f"景点全孤立，用质心: ({avg_lng:.4f},{avg_lat:.4f})")
            return (avg_lng, avg_lat)

        # 密度并列第一的几个，取离质心最近的
        top_candidates = [d for d in densities if d[0] == top_density]
        avg_lng = sum(p[0] for p in points) / len(points)
        avg_lat = sum(p[1] for p in points) / len(points)

        best = min(
            top_candidates,
            key=lambda d: self._haversine_distance(d[1], d[2], avg_lng, avg_lat)
        )

        logger.info(
            f"密度锚点: {best[4]} ({best[1]:.4f},{best[2]:.4f}) "
            f"密度={best[0]}/{len(points)-1}邻居, 景点总数={len(points)}"
        )
        return (best[1], best[2])

    # typecode 排名：针对每个档次，定义 typecode 的匹配优先级（越小越好）
    _TIER_RANK: dict = {
        "经济型": {"100105": 0,                             # 精准匹配（经济连锁）
                   "100100": 1,                             # 通用宾馆
                   "100104": 2, "100103": 3,                # 三星/四星
                   "100102": 4, "100101": 5,                # 五星/奢华
                   "100200": 99, "100201": 99},             # 招待所/青旅 → 排除
        "舒适型": {"100103": 0, "100104": 0,                # 精准匹配
                   "100100": 1,                             # 通用宾馆
                   "100105": 2,                             # 经济连锁
                   "100102": 3, "100101": 4,                # 五星/奢华
                   "100200": 99, "100201": 99},             # 招待所/青旅 → 排除
        "豪华型": {"100101": 0, "100102": 0,                # 精准匹配
                   "100100": 1,                             # 通用宾馆
                   "100103": 2, "100104": 3,                # 三星/四星
                   "100105": 4,                             # 经济连锁
                   "100200": 99, "100201": 99},             # 招待所/青旅 → 排除
    }

    def _search_hotels_nearby(self, lng: float, lat: float, city: str,
                               accommodation: str) -> List[dict]:
        """在锚点周边搜索酒店，用 typecode 匹配住宿档次。

        策略：同档 typecode 精筛，不够时扩半径（不降档次）。
        同档确实搜不到 → 才用全量兜底。
        排序用 tier_rank（0=精准匹配, 值越小越优先），而非简单的布尔 is_same_tier。
        """
        tier_types = self._HOTEL_TIER_TYPECODES.get(
            accommodation, self._HOTEL_TIER_TYPECODES["经济型"]
        )
        # 常规化档次 key（兼容前端传 "豪华酒店" 等）
        norm_accommodation = accommodation
        for label in ["豪华", "舒适", "经济"]:
            if label in accommodation:
                norm_accommodation = label + "型"
                break
        tier_rank_map = self._TIER_RANK.get(norm_accommodation, self._TIER_RANK["经济型"])

        all_hotels = []
        # 该档 typecode 搜：5km → 10km → 20km
        for radius in [5000, 10000, 20000]:
            new_hotels = self._do_hotel_search(lng, lat, tier_types, radius, tier_types)
            # 去重合并
            for h in new_hotels:
                if h["name"] not in {x["name"] for x in all_hotels}:
                    all_hotels.append(h)
            if len(all_hotels) >= 5:
                break

        # 兜底：同档确实不够（<2家），扩展到全量（但仍优先同档）
        if len(all_hotels) < 2:
            logger.info(f"同档酒店严重不足({len(all_hotels)}家)，扩展到全量酒店")
            for radius in [5000, 10000, 20000]:
                new_hotels = self._do_hotel_search(
                    lng, lat, self._ALL_HOTEL_TYPECODES, radius, tier_types
                )
                for h in new_hotels:
                    if h["name"] not in {x["name"] for x in all_hotels}:
                        all_hotels.append(h)
                if len(all_hotels) >= 5:
                    break

        if not all_hotels:
            logger.warning(f"锚点 ({lng:.4f},{lat:.4f}) 周边无酒店结果")
            return []

        # 为每家酒店计算 tier_rank（越小越匹配）
        for h in all_hotels:
            tc = h.get("typecode", "")
            h["tier_rank"] = tier_rank_map.get(tc, 99)

        # 排序：tier_rank（优先） → 距离（次优）
        all_hotels.sort(key=lambda h: (h["tier_rank"], h["distance_m"]))

        same_tier = sum(1 for h in all_hotels if h.get("is_same_tier"))
        top3 = [(h["name"][:12], h.get("typecode",""), h["tier_rank"]) for h in all_hotels[:3]]
        logger.info(
            f"搜到 {len(all_hotels)} 家酒店（同档{same_tier}家），"
            f"档次={accommodation}, 锚点 ({lng:.4f},{lat:.4f}), top3={top3}"
        )
        return all_hotels

    def _do_hotel_search(self, lng: float, lat: float, types: str,
                         radius: int, tier_types: str) -> List[dict]:
        """单次高德酒店周边搜索"""
        results = []
        try:
            from urllib.parse import urlencode

            settings = get_settings()
            params = urlencode({
                "key": settings.amap_api_key,
                "location": f"{lng},{lat}",
                "radius": str(radius),
                "types": types,
                "offset": "20",
                "output": "json",
            })
            # urlencode 会把 | 编码成 %7C，高德 API 不认，导致 types 过滤失效
            params = params.replace('%7C', '|')
            url = f"https://restapi.amap.com/v3/place/around?{params}"
            data = request_json(url)
            pois = data.get("pois", [])

            for poi in pois:
                name = poi.get("name", "")
                if not name:
                    continue

                expected_region = re.sub(
                    r"(?:省|市|自治区|特别行政区)$",
                    "",
                    str(getattr(self, "_hotel_expected_region", "") or "").strip(),
                )
                location_scope = " ".join(str(poi.get(key, "") or "") for key in (
                    "pname", "cityname", "adname", "address"
                ))
                if (
                    expected_region in self._PROVINCE_REGION_NAMES
                    and location_scope
                    and expected_region not in location_scope
                ):
                    logger.debug(
                        f"酒店地域排除: 期望{expected_region}，"
                        f"候选{name}位于{location_scope}"
                    )
                    continue

                tc = str(poi.get("typecode", ""))
                # 必须是住宿类
                if tc and not tc.startswith("10"):
                    continue

                # 名称黑名单
                if any(kw in name for kw in self._NON_HOTEL_KW):
                    continue

                loc_str = poi.get("location", "")
                plng, plat = 0.0, 0.0
                if "," in loc_str:
                    try:
                        parts = loc_str.split(",")
                        plng, plat = float(parts[0]), float(parts[1])
                    except ValueError:
                        pass

                dist_m = int(poi.get("distance", 99999))
                dist_km = dist_m / 1000.0

                # 是否匹配用户选择的档次 typecode
                is_same_tier = tc in tier_types.split("|") if tc else False

                biz_ext = poi.get("biz_ext", {})
                rating_str = ""
                if isinstance(biz_ext, dict):
                    raw = biz_ext.get("rating", "")
                    if raw and not isinstance(raw, (list, dict)):
                        rating_str = str(raw)

                results.append({
                    "name": name,
                    "address": poi.get("address", ""),
                    "lng": plng,
                    "lat": plat,
                    "distance_km": dist_km,
                    "distance_m": dist_m,
                    "type": poi.get("type", ""),
                    "typecode": tc,
                    "rating": str(rating_str),
                    "is_same_tier": is_same_tier,
                    "_is_hotel": True,
                })

        except Exception as e:
            logger.warning(f"酒店搜索失败 (radius={radius}m, types={types}): {e}")

        return results

    def _select_hotel(self, trip_plan: TripPlan, request: TripRequest) -> None:
        """酒店选址主方法：密度锚点 → 周边搜连锁 → 挑最佳 → 填到所有天。

        1. 收集所有景点坐标，密度锚点算法找最密集点
        2. 在锚点周边 5-10km 搜连锁酒店
        3. 取排名第一的（链品牌优先 + 距离最近）
        4. 同一家酒店填到每一天
        """
        if self._is_route_mode(request):
            self._hotel_expected_region = request.city
            self._select_route_hotels(trip_plan, request)
            return

        self._hotel_expected_region = ""

        # 1. 找密度锚点
        anchor_lng, anchor_lat = self._hotel_density_anchor(trip_plan)
        if anchor_lng == 0.0 and anchor_lat == 0.0:
            logger.warning("锚点无效，跳过酒店选址")
            return

        # 2. 搜周边酒店
        candidates = self._search_hotels_nearby(
            anchor_lng, anchor_lat, request.city, request.accommodation
        )

        if not candidates:
            logger.warning("未找到酒店，保持 plan 中的 null hotel")
            return

        # 3. 选最佳：取第一个（已按同档typecode+距离排序）
        best = candidates[0]

        # 4. LLM 估算价格（轻量单次调用，不经过 Agent）
        estimated_cost = self._estimate_hotel_price(
            best["name"], best.get("type", ""), request
        )

        # 5. 填到所有天
        hotel = Hotel(
            name=best["name"],
            address=best.get("address", ""),
            location=Location(longitude=best["lng"], latitude=best["lat"]),
            price_range=f"¥{estimated_cost}/晚" if estimated_cost else "",
            rating=best.get("rating", ""),
            distance=f"距锚点{best['distance_km']:.1f}km" if best['distance_km'] >= 0.1
                    else f"距锚点{best['distance_m']}m",
            type=best.get("type", ""),
            estimated_cost=estimated_cost,
        )

        for day in trip_plan.days:
            day.hotel = hotel

        logger.info(
            f"选中酒店: {best['name']} | "
            f"tier_rank={best.get('tier_rank', '?')} | "
            f"typecode={best.get('typecode', '?')} | "
            f"距锚点{best['distance_km']:.1f}km | "
            f"估价¥{estimated_cost}/晚"
        )

    def _select_route_hotels(
        self, trip_plan: TripPlan, request: TripRequest
    ) -> None:
        """跨城模式按当晚停留地选择酒店，同一节点连续住宿时复用。"""
        hotel_cache: Dict[str, Optional[Hotel]] = {}
        final_route_stop = None
        departure_hub = None
        departure_minutes = self._time_to_minutes(request.departure_time)
        if request.trip_mode == "multi_city":
            route_nodes = self._build_route_nodes(request)
            if route_nodes:
                final_route_stop = self._route_node_profile(
                    route_nodes[-1]
                )["service_base"]
            if (
                final_route_stop
                and departure_minutes is not None
            ):
                departure_hub = self._resolve_transport_hub(
                    request, arrival=False
                )

        for day in trip_plan.days:
            stop = day.overnight_city or day.end_location
            if not stop:
                continue
            if stop in hotel_cache:
                day.hotel = hotel_cache[stop]
                continue

            activity_stop = day.end_location or stop
            node_profile = self._route_node_profile(activity_stop)
            full_visit_days = [
                stay for stay in trip_plan.days
                if stay.day_index < request.travel_days - 1
                and not stay.route_segment and stay.attractions
                and (stay.overnight_city or stay.end_location) == stop
            ]
            balance_scenic_departure = bool(
                request.trip_mode == "multi_city" and stop == final_route_stop
                and departure_hub and departure_hub.get("location")
                and node_profile["node_type"] == "scenic_area" and full_visit_days
            )
            outgoing_rail_hub = None
            if request.trip_mode == "multi_city":
                outgoing_segment = next((
                    candidate_day.route_segment
                    for candidate_day in trip_plan.days
                    if candidate_day.route_segment
                    and candidate_day.route_segment.origin == stop
                    and self._expects_rail_transfer(candidate_day.route_segment)
                ), None)
                if outgoing_segment:
                    outgoing_rail_hub = self._resolve_intercity_rail_hub(stop)
            if (
                request.trip_mode == "multi_city"
                and stop == final_route_stop
                and departure_hub
                and departure_hub.get("location")
                and not balance_scenic_departure
            ):
                # 多城市最后一站若次日上午离开，酒店优先围绕用户选定
                # 的车站/机场搜索。远郊景点作为完整日往返，不能为了靠近
                # 景区而让离开日出现数小时、且不稳定的乡村公交接驳。
                anchor_lng = departure_hub["location"].longitude
                anchor_lat = departure_hub["location"].latitude
                logger.info(
                    f"多城市末站酒店锚点: {stop} → "
                    f"{departure_hub['name']} ({anchor_lng:.4f},{anchor_lat:.4f})"
                )
            elif (
                node_profile["node_type"] == "scenic_area"
                and stop != activity_stop
            ):
                # 景区当天的景点坐标通常在湖边/山中；住宿应围绕服务地县城搜索，
                # 不能继续拿景区坐标找酒店。
                hotel_anchor = node_profile.get("hotel_anchor")
                service_center = (
                    get_amap_service().geocode_location(hotel_anchor)
                    if hotel_anchor else self._get_city_center(stop)
                )
                anchor_lng, anchor_lat = service_center or (0.0, 0.0)
                logger.info(
                    f"景区住宿锚点: {activity_stop} 游览 → {stop}住宿 "
                    f"{f'({hotel_anchor}) ' if hotel_anchor else ''}"
                    f"({anchor_lng:.4f},{anchor_lat:.4f})"
                )
            elif request.trip_mode == "multi_city":
                # 动态提前换城后，首次在目的城市过夜的日期可能只含
                # 上一城上午景点。酒店锚点应综合该城市全部住宿日中的
                # 目的地景点，不能被上一城景点坐标带偏。
                anchor_days = []
                for stay_day in trip_plan.days:
                    stay_stop = stay_day.overnight_city or stay_day.end_location
                    if stay_stop != stop:
                        continue
                    attractions = list(stay_day.attractions)
                    if stay_day.route_segment:
                        attractions = [
                            attraction for attraction in attractions
                            if self._attraction_route_side(
                                attraction, stay_day.route_segment
                            ) == "destination"
                        ]
                    anchor_days.append(
                        stay_day.model_copy(update={"attractions": attractions})
                    )
                anchor_plan = trip_plan.model_copy(
                    update={"days": anchor_days}
                )
                anchor_lng, anchor_lat = self._hotel_density_anchor(anchor_plan)
            else:
                day_plan = trip_plan.model_copy(update={"days": [day]})
                anchor_lng, anchor_lat = self._hotel_density_anchor(day_plan)
            if anchor_lng == 0.0 and anchor_lat == 0.0:
                center = self._get_city_center(stop)
                if center:
                    anchor_lng, anchor_lat = center
            if anchor_lng == 0.0 and anchor_lat == 0.0:
                hotel_cache[stop] = None
                continue

            candidates = self._search_hotels_nearby(
                anchor_lng, anchor_lat, stop, request.accommodation
            )
            if balance_scenic_departure:
                hub_location = departure_hub["location"]
                hub_candidates = self._search_hotels_nearby(
                    hub_location.longitude, hub_location.latitude, stop, request.accommodation
                )
                balanced = []
                seen_hotels = set()
                # 每侧只复核前三家，避免对所有酒店逐一调用路径 API。
                for candidate in [*candidates[:3], *hub_candidates[:3]]:
                    if candidate["name"] in seen_hotels:
                        continue
                    seen_hotels.add(candidate["name"])
                    hotel_location = Location(longitude=candidate["lng"], latitude=candidate["lat"])
                    hub_minutes = self._estimated_location_transfer_minutes(
                        hotel_location, hub_location, request.transportation, stop, True
                    )
                    # 返程日按最早06:00开始，预留早餐、退房和进站缓冲。
                    departure_ok = hub_minutes + 45 + 20 + self._terminal_buffer_minutes(
                        request.departure_transport or "高铁", arrival=False
                    ) <= (departure_minutes or 0) - 6 * 60
                    visit_minutes = sum(
                        2 * self._estimated_location_transfer_minutes(
                            hotel_location, stay.attractions[0].location,
                            request.transportation, stop, True,
                        ) for stay in full_visit_days
                    )
                    # 全部候选都无法满足06:00下限时，优先最短赶车接驳，
                    # 不能再为了游览距离选择更不可能赶上的景区酒店。
                    balanced.append((not departure_ok, visit_minutes + hub_minutes if departure_ok else hub_minutes, candidate))
                balanced.sort(key=lambda item: item[:2])
                candidates = [item[2] for item in balanced]
                if balanced:
                    logger.info(
                        f"多城市景区住宿平衡: {activity_stop}，完整游览{len(full_visit_days)}天，"
                        f"兼顾{departure_hub['name']}返程，选择{candidates[0]['name']}"
                    )
            if (
                outgoing_rail_hub
                and outgoing_rail_hub.get("longitude")
                and outgoing_rail_hub.get("latitude")
                and self._haversine_distance(
                    anchor_lng,
                    anchor_lat,
                    float(outgoing_rail_hub["longitude"]),
                    float(outgoing_rail_hub["latitude"]),
                ) > 1
            ):
                station_candidates = self._search_hotels_nearby(
                    float(outgoing_rail_hub["longitude"]),
                    float(outgoing_rail_hub["latitude"]),
                    stop,
                    request.accommodation,
                )
                merged = {
                    candidate["name"]: candidate
                    for candidate in [*candidates, *station_candidates]
                }
                for candidate in merged.values():
                    attraction_distance = self._haversine_distance(
                        float(candidate.get("lng", 0) or 0),
                        float(candidate.get("lat", 0) or 0),
                        anchor_lng,
                        anchor_lat,
                    )
                    station_distance = self._haversine_distance(
                        float(candidate.get("lng", 0) or 0),
                        float(candidate.get("lat", 0) or 0),
                        float(outgoing_rail_hub["longitude"]),
                        float(outgoing_rail_hub["latitude"]),
                    )
                    candidate["_balanced_distance"] = (
                        attraction_distance + station_distance * 1.25
                    )
                    candidate["distance_km"] = attraction_distance
                    candidate["distance_m"] = int(attraction_distance * 1000)
                candidates = sorted(
                    merged.values(),
                    key=lambda candidate: (
                        candidate.get("tier_rank", 99),
                        candidate.get("_balanced_distance", 99999),
                    ),
                )
                logger.info(
                    f"跨城住宿平衡: {stop} 同时考虑行程锚点与"
                    f"{outgoing_rail_hub['name']}，候选{len(candidates)}家"
                )
            if not candidates:
                hotel_cache[stop] = None
                continue
            best = candidates[0]
            local_request = request.model_copy(update={"city": stop})
            estimated_cost = self._estimate_hotel_price(
                best["name"], best.get("type", ""), local_request
            )
            hotel = Hotel(
                name=best["name"],
                address=best.get("address", ""),
                location=Location(
                    longitude=best["lng"], latitude=best["lat"]
                ),
                price_range=f"¥{estimated_cost}/晚" if estimated_cost else "",
                rating=best.get("rating", ""),
                distance=(
                    "综合景区游览与返程接驳选择" if balance_scenic_departure else
                    f"距当日行程锚点{best['distance_km']:.1f}km"
                    if best["distance_km"] >= 0.1
                    else f"距当日行程锚点{best['distance_m']}m"
                ),
                type=best.get("type", ""),
                estimated_cost=estimated_cost,
            )
            hotel_cache[stop] = hotel
            day.hotel = hotel
            logger.info(f"环线酒店 [{stop}]: {hotel.name}")

    def _estimate_hotel_price(self, hotel_name: str, hotel_type: str,
                               request: TripRequest) -> int:
        """用 LLM 估算酒店每晚价格（单次轻量调用）。

        仅用一次 LLM 调用，给定酒店名、类型、城市、住宿档次，
        返回估计的每晚价格（整数）。
        """
        accommodation = request.accommodation or "经济型"
        prompt = (
            f"请估算以下酒店在{request.city}的每晚价格（人民币整数）:\n"
            f"- 酒店名称: {hotel_name}\n"
            f"- 高德分类: {hotel_type}\n"
            f"- 用户住宿要求: {accommodation}\n\n"
            f"只返回一个整数数字（如 350），不要其他文字。"
        )

        try:
            from langchain_core.messages import HumanMessage
            response = self.llm.invoke([HumanMessage(content=prompt)])
            content = response.content if hasattr(response, "content") else str(response)
            content = content.strip()
            # 提取数字
            match = re.search(r"\d+", content)
            if match:
                price = int(match.group())
                logger.debug(f"LLM 酒店估价: {hotel_name} → ¥{price}/晚")
                return price
        except Exception as e:
            logger.warning(f"LLM 酒店估价失败: {e}")

        # 默认价格 fallback
        tier_map = {
            "经济型": 200, "经济型酒店": 200,
            "舒适型": 350, "舒适型酒店": 350, "民宿": 350,
            "豪华型": 600, "豪华酒店": 600,
            "高档型": 500,
        }
        fallback = tier_map.get(accommodation, 300)
        logger.debug(f"酒店估价 fallback: {hotel_name} → ¥{fallback}/晚")
        return fallback

    # ---- 城市交通日花费 ----
    _CITY_TRANSPORT_COST: Dict[str, int] = {
        "北京": 80, "上海": 80, "广州": 80, "深圳": 80,
        "成都": 60, "杭州": 60, "南京": 60, "武汉": 60, "重庆": 60,
        "西安": 60, "长沙": 60, "天津": 60, "苏州": 60, "郑州": 60,
        "青岛": 60, "厦门": 60, "宁波": 60, "无锡": 60, "东莞": 60,
        "佛山": 60, "合肥": 60, "福州": 60, "济南": 60, "大连": 60,
        "昆明": 60, "南宁": 60, "贵阳": 60, "哈尔滨": 60, "长春": 60,
        "沈阳": 60, "石家庄": 60, "太原": 60,
    }

    def _recalculate_budget(self, trip_plan: TripPlan, request: TripRequest) -> None:
        """基于实际数据重新计算预算。

        门票:   Σ ticket_price (已被 _enrich_ticket_info 更新为真实票价)
        酒店:   Σ hotel.estimated_cost (LLM 估计的每晚价格)
        餐饮:   Σ meal.estimated_cost (LLM 估计的人均价)
        交通:   城市日花费 × 旅行天数
        """
        total_attractions = 0
        total_hotels = 0
        total_meals = 0

        overnight_positions = set(range(max(0, len(trip_plan.days) - 1)))
        for position, day in enumerate(trip_plan.days):
            for attr in day.attractions:
                total_attractions += attr.ticket_price or 0
            # 行程天数包含离店日；4天行程通常只有3晚，不能按每天一晚累加。
            if (
                position in overnight_positions
                and day.hotel
                and day.hotel.estimated_cost
            ):
                total_hotels += day.hotel.estimated_cost
            for meal in day.meals:
                total_meals += meal.estimated_cost or 0

        if self._is_route_mode(request):
            total_transportation = request.travel_days * 30
            for day in trip_plan.days:
                segment = day.route_segment
                if not segment:
                    continue
                if segment.transport_mode == "自驾":
                    segment_cost = segment.distance_km * 0.8
                elif segment.transport_mode == "高铁/动车":
                    segment_cost = segment.distance_km * 0.45
                elif segment.transport_mode == "飞机":
                    segment_cost = max(400, segment.distance_km * 0.7)
                else:
                    segment_cost = segment.distance_km * 0.25
                total_transportation += round(segment_cost)
        else:
            daily_transport = self._CITY_TRANSPORT_COST.get(request.city, 40)
            total_transportation = daily_transport * request.travel_days

        trip_plan.budget = Budget(
            total_attractions=total_attractions,
            total_hotels=total_hotels,
            total_meals=total_meals,
            total_transportation=total_transportation,
            total=total_attractions + total_hotels + total_meals + total_transportation,
        )

        logger.info(
            f"预算重算: 门票¥{total_attractions} + 酒店¥{total_hotels} + "
            f"餐饮¥{total_meals} + 交通¥{total_transportation} = "
            f"总计¥{trip_plan.budget.total}"
        )

    @classmethod
    def _is_indoor_venue(cls, name: str, category: str = "") -> bool:
        text = f"{cls._normalize_attr_name(name)} {category}"
        return not any(word in text for word in ("露天", "户外")) and any(word in text for word in (
            "书店", "图书馆", "博物馆", "博物院", "美术馆", "科技馆", "展览馆",
            "纪念馆", "陈列馆", "艺术馆", "海洋馆", "室内",
        ))

    @classmethod
    def _is_outdoor_attraction(cls, attraction: Attraction) -> bool:
        """Use venue identity/type before geographic words in a branch address."""
        name = cls._normalize_attr_name(attraction.name)
        text = f"{name} {attraction.category or ''}"
        if any(marker in text for marker in ("露天", "户外", "遗址公园", "植物园", "动物园")):
            return True
        if cls._is_indoor_venue(name, attraction.category or ""):
            return False
        return any(marker in text for marker in (
            "山", "湖", "海", "岛", "公园", "森林", "长城", "景区", "古镇",
            "自然风光", "商业街区", "步行街",
        ))

    @classmethod
    def _validate_trip_plan(cls, trip_plan: TripPlan, request: TripRequest) -> List[QualityIssue]:
        """用确定性规则检查 LLM 无法可靠保证的行程约束。"""
        from datetime import datetime, timedelta

        issues: List[QualityIssue] = []
        if len(trip_plan.days) != request.travel_days:
            issues.append(QualityIssue(
                code="day_count_mismatch", severity="error",
                message=f"行程包含 {len(trip_plan.days)} 天，与请求的 {request.travel_days} 天不一致。",
            ))

        try:
            start = datetime.strptime(request.start_date, "%Y-%m-%d")
            for index, day in enumerate(trip_plan.days):
                expected_date = (start + timedelta(days=index)).strftime("%Y-%m-%d")
                if day.date != expected_date:
                    issues.append(QualityIssue(
                        code="date_mismatch", severity="error",
                        day_index=index,
                        message=f"第 {index + 1} 天日期为 {day.date}，应为 {expected_date}。",
                    ))
        except ValueError:
            issues.append(QualityIssue(
                code="invalid_request_date", severity="error",
                message="请求日期格式无效，无法校验每日日期。",
            ))

        seen_attractions: dict[str, int] = {}
        seen_scenic_entities: dict[str, str] = {}
        bad_weather_keywords = ("雨", "雪", "雷", "雾")
        weather_by_date = {
            weather.date: f"{weather.day_weather}{weather.night_weather}"
            for weather in trip_plan.weather_info
        }
        for index, day in enumerate(trip_plan.days):
            if (
                day.route_segment
                and (
                    day.route_segment.distance_km <= 0
                    or day.route_segment.duration_minutes <= 0
                )
            ):
                issues.append(QualityIssue(
                    code="route_segment_unresolved",
                    severity="warning",
                    day_index=index,
                    message=(
                        f"第 {index + 1} 天 {day.route_segment.origin} → "
                        f"{day.route_segment.destination} 的路线距离或耗时未能获取，"
                        "请确认地点名称并补充实际交通信息。"
                    ),
                ))
            if day.route_segment and day.route_segment.exceeds_daily_limit:
                issues.append(QualityIssue(
                    code="daily_drive_limit_exceeded",
                    severity="warning",
                    day_index=index,
                    message=(
                        f"第 {index + 1} 天 {day.route_segment.origin} → "
                        f"{day.route_segment.destination} 预计驾驶 "
                        f"{day.route_segment.duration_minutes / 60:.1f} 小时，"
                        f"超过设置的每日 {request.max_drive_hours_per_day:.1f} 小时上限。"
                    ),
                ))
            expected_meals = set(cls._available_meal_types(request, day.day_index))
            # 精确车站/机场路线可能比首轮通用抵达缓冲更长。
            # 已证明放好行李时超过 14:00 时，不再强制要求午餐。
            if index == 0 and "lunch" in expected_meals:
                arrival_hotel = next((
                    item for item in day.timeline
                    if item.kind == "hotel" and item.end_time
                ), None)
                hotel_ready = cls._time_to_minutes(
                    arrival_hotel.end_time if arrival_hotel else None
                )
                if hotel_ready is not None and hotel_ready > 14 * 60:
                    expected_meals.discard("lunch")
            actual_meals = {meal.type for meal in day.meals}
            for meal in day.meals:
                if meal.data_source != "user" and any(marker in meal.name for marker in NON_DINING_BUSINESS_MARKERS):
                    issues.append(QualityIssue(code="invalid_meal_business", severity="error", day_index=index, message=f"第 {index + 1} 天餐饮候选“{meal.name}”不是适合的堂食门店。"))
                if (
                    request.trip_mode == "single_city"
                    and meal.type in expected_meals
                    and meal.data_source == "logistics_advice"
                    and not cls._is_remote_day(day)
                ):
                    meal_label = {
                        "breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐",
                    }.get(meal.type, "用餐")
                    issues.append(QualityIssue(
                        code="unresolved_meal_recommendation",
                        severity="error",
                        day_index=index,
                        message=(
                            f"第 {index + 1} 天{meal_label}"
                            f"仍是“{meal.name}”，尚未匹配到可核验的真实堂食门店。"
                        ),
                    ))
            missing_meals = expected_meals - actual_meals
            if missing_meals:
                labels = {
                    "breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"
                }
                issues.append(QualityIssue(
                    code="missing_expected_meal",
                    severity="error",
                    day_index=index,
                    message=(
                        f"第 {index + 1} 天缺少"
                        + "、".join(labels[item] for item in (
                            "breakfast", "lunch", "dinner"
                        ) if item in missing_meals)
                        + "安排。"
                    ),
                ))

            for item in day.timeline:
                if item.kind != "meal" or item.reference_index is None:
                    continue
                if not 0 <= item.reference_index < len(day.meals):
                    continue
                meal = day.meals[item.reference_index]
                start_minutes = cls._time_to_minutes(item.start_time)
                end_minutes = cls._time_to_minutes(item.end_time)
                if start_minutes is None or end_minutes is None:
                    continue
                if meal.type == "lunch" and start_minutes > 14 * 60:
                    issues.append(QualityIssue(
                        code="lunch_too_late", severity="error", day_index=index,
                        message=f"第 {index + 1} 天午餐 {item.start_time} 才开始，时间过晚。",
                    ))
                if meal.type == "dinner" and start_minutes > 20 * 60 + 30:
                    issues.append(QualityIssue(
                        code="dinner_too_late", severity="error", day_index=index,
                        message=f"第 {index + 1} 天晚餐 {item.start_time} 才开始，时间过晚。",
                    ))
                if cls._opening_hours_cover_period(
                    meal.opening_hours, start_minutes, end_minutes
                ) is False:
                    issues.append(QualityIssue(
                        code="meal_opening_conflict", severity="error", day_index=index,
                        message=(
                            f"第 {index + 1} 天“{meal.name}”计划于 "
                            f"{item.start_time}-{item.end_time} 用餐，"
                            f"与营业时间“{meal.opening_hours}”冲突。"
                        ),
                    ))

            # 时间轴已经包含高德逐段结果，质量校验不能只检查景点间的
            # 直线距离。首日若单段公共交通过长，或任何一天很晚才回酒店，
            # 都应阻止结果被标记为“通过”。
            transport_items = [item for item in day.timeline if item.kind == "transport"]
            for leg in transport_items:
                departure_minute = cls._time_to_minutes(leg.start_time)
                if (not leg.is_intercity and departure_minute is not None
                        and (departure_minute < 6 * 60 or departure_minute >= 23 * 60)
                        and any(mode in (leg.transport_mode or "") for mode in ("公共交通", "公交", "地铁"))):
                    issues.append(QualityIssue(
                        code="transit_service_hours_unverified", severity="warning", day_index=index,
                        message=(f"第 {index + 1} 天 {leg.start_time} 出发的公共交通仅有参考耗时，"
                                 "尚未核验首末班。请确认运营时段，必要时将该段改为打车。"),
                    ))
            if index == 0 and request.arrival_time:
                attraction_names = {
                    cls._normalize_attr_name(attraction.name)
                    for attraction in day.attractions
                }
                long_arrival_legs = [
                    item for item in transport_items
                    if (item.duration_minutes or 0) > 90
                    and (
                        cls._normalize_attr_name(item.from_name or "")
                        in attraction_names
                        or cls._normalize_attr_name(item.to_name or "")
                        in attraction_names
                    )
                ]
                if long_arrival_legs:
                    longest = max(long_arrival_legs, key=lambda item: item.duration_minutes or 0)
                    issues.append(QualityIssue(
                        code="arrival_day_transfer_too_long",
                        severity="error",
                        day_index=index,
                        message=(
                            f"第 1 天抵达后“{longest.from_name}”到“{longest.to_name}”"
                            f"仍需约 {longest.duration_minutes} 分钟，不适合作为落地日安排。"
                        ),
                    ))
            hotel_returns = [
                item for item in day.timeline
                if item.kind == "hotel" and item.start_time
            ]
            if hotel_returns:
                latest_return = max(
                    (cls._time_to_minutes(item.start_time) or 0 for item in hotel_returns),
                    default=0,
                )
                if latest_return > 22 * 60 + 30:
                    issues.append(QualityIssue(
                        code="hotel_return_too_late",
                        severity="error",
                        day_index=index,
                        message=(
                            f"第 {index + 1} 天预计 {latest_return // 60:02d}:"
                            f"{latest_return % 60:02d} 才返回酒店，行程结束过晚。"
                        ),
                    ))

            if not day.attractions:
                if day.is_transit_day:
                    continue
                arrival_ready = (
                    cls._arrival_ready_minutes(request) if index == 0 else None
                )
                departure = (
                    cls._time_to_minutes(request.departure_time)
                    if index == len(trip_plan.days) - 1 else None
                )
                # 晚到或早离的首尾日，本来就可能只有入住、早餐和交通，
                # 不能套用完整日“至少一个景点”的规则。
                if (
                    (arrival_ready is not None and arrival_ready >= 16 * 60)
                    or (departure is not None and departure <= 13 * 60 + 30)
                ):
                    continue
                issues.append(QualityIssue(
                    code="empty_day", severity="error", day_index=index,
                    message=f"第 {index + 1} 天没有安排景点。",
                ))
                continue
            pace = cls._pace_profile(request)
            if len(day.attractions) > pace["max_attractions"]:
                issues.append(QualityIssue(
                    code="too_many_attractions", severity="error", day_index=index,
                    message=(
                        f"第 {index + 1} 天安排了 {len(day.attractions)} 个景点，"
                        f"超过“{pace['label']}”的单日上限。"
                    ),
                ))

            timed_starts = [
                cls._time_to_minutes(item.start_time)
                for item in day.timeline if item.start_time
            ]
            timed_ends = [
                cls._time_to_minutes(item.end_time or item.start_time)
                for item in day.timeline if item.start_time
            ]
            valid_starts = [value for value in timed_starts if value is not None]
            valid_ends = [value for value in timed_ends if value is not None]
            if valid_starts and valid_ends:
                span = max(valid_ends) - min(valid_starts)
                if span > pace["max_day_span"]:
                    issues.append(QualityIssue(
                        code="pace_day_too_long", severity="error", day_index=index,
                        message=(
                            f"第 {index + 1} 天连续安排约 {span // 60}小时"
                            f"{span % 60:02d}分钟，超过“{pace['label']}”的强度上限。"
                        ),
                    ))

            capacity = cls._day_visit_capacity(request, day, index)
            # “可游玩时间”必须扣掉时间轴里已经证实的市内交通。
            # 否则远郊往返三四小时的一天仍会被误判为“景点不足”，
            # 页面便会把实际已经排满的行程显示成待处理异常。
            local_transport_minutes = sum(
                int(item.duration_minutes or 0)
                for item in day.timeline
                if item.kind == "transport" and not item.is_intercity
            )
            effective_visit_capacity = max(
                0, capacity - local_transport_minutes
            )
            visit_minutes = sum(item.visit_duration for item in day.attractions)
            partial_first_day = bool(
                index == 0
                and request.arrival_time
                and request.arrival_transport != "已在此地"
            )
            partial_last_day = bool(
                index == len(trip_plan.days) - 1 and request.departure_time
            )
            if (
                effective_visit_capacity >= 300
                and len(day.attractions) < 2
                and visit_minutes < 300
                and request.travel_pace != "relaxed"
                and not cls._is_remote_day(day)
                and not day.is_transit_day
                and not partial_first_day
                and not partial_last_day
            ):
                issues.append(QualityIssue(
                    code="underfilled_full_day", severity="error", day_index=index,
                    message=(
                        f"第 {index + 1} 天扣除交通后仍有约 "
                        f"{effective_visit_capacity} 分钟可用于游览，"
                        f"但只安排了 {len(day.attractions)} 个景点、约 {visit_minutes} 分钟，"
                        "仍存在明显空档。"
                    ),
                ))

            for item in day.timeline:
                if item.kind != "attraction" or item.reference_index is None:
                    continue
                if not (0 <= item.reference_index < len(day.attractions)):
                    continue
                attraction = day.attractions[item.reference_index]
                start = cls._time_to_minutes(item.start_time)
                end = cls._time_to_minutes(item.end_time)
                if start is None or end is None:
                    continue
                if request.trip_mode == "single_city" and cls._is_night_visit(attraction) and start < 18 * 60:
                    issues.append(QualityIssue(code="night_visit_too_early", severity="error", day_index=index, message=f"第 {index + 1} 天夜游项目“{attraction.name}”被排在 {item.start_time}，未保留晚间时段。"))
                fitted = cls._opening_fit_start(
                    attraction, start, max(1, end - start)
                )
                if fitted is None or fitted != start:
                    hours = (
                        attraction.ticket_info.opening_hours
                        if attraction.ticket_info else "未获取"
                    )
                    issues.append(QualityIssue(
                        code="attraction_opening_conflict",
                        severity="error",
                        day_index=index,
                        message=(
                            f"第 {index + 1} 天“{attraction.name}”计划于 "
                            f"{item.start_time}-{item.end_time} 游览，与开放时间“{hours}”冲突。"
                        ),
                    ))

            for meal in day.meals:
                if meal.type == "breakfast" and not meal.opening_hours_verified:
                    issues.append(QualityIssue(
                        code="breakfast_hours_unverified",
                        severity="warning",
                        day_index=index,
                        message=(
                            f"第 {index + 1} 天早餐“{meal.name}”的早间营业时间"
                            "未由地图数据明确确认，出发前请核实。"
                        ),
                    ))

            for attraction in day.attractions:
                normalized_name = cls._normalize_attr_name(attraction.name)
                entity = cls._scenic_entity_name(attraction.name)
                previous_name = seen_scenic_entities.get(entity)
                if previous_name is not None and previous_name != normalized_name:
                    issues.append(QualityIssue(
                        code="duplicate_scenic_entity", severity="error", day_index=index,
                        message=f"“{attraction.name}”与“{previous_name}”为同一景区的重复名称，不能分别计为两次游览。",
                    ))
                seen_scenic_entities[entity] = normalized_name
                if normalized_name in seen_attractions:
                    previous_day = seen_attractions[normalized_name] + 1
                    issues.append(QualityIssue(
                        code="duplicate_attraction", severity="warning", day_index=index,
                        message=f"“{attraction.name}”与第 {previous_day} 天重复安排。",
                    ))
                else:
                    seen_attractions[normalized_name] = index

            for previous, current in zip(day.attractions, day.attractions[1:]):
                distance = cls._haversine_distance(
                    previous.location.longitude, previous.location.latitude,
                    current.location.longitude, current.location.latitude,
                )
                if distance > 20:
                    issues.append(QualityIssue(
                        code="long_intra_day_distance", severity="warning", day_index=index,
                        message=(f"第 {index + 1} 天 “{previous.name}” 到 “{current.name}” "
                                 f"直线距离约 {distance:.1f} km，建议调整顺序或拆分日期。"),
                    ))

            weather_text = weather_by_date.get(day.date, "")
            if any(keyword in weather_text for keyword in bad_weather_keywords):
                outdoor = [item.name for item in day.attractions if cls._is_outdoor_attraction(item)]
                if outdoor:
                    issues.append(QualityIssue(
                        code="weather_outdoor_risk", severity="warning", day_index=index,
                        message=f"第 {index + 1} 天预报“{weather_text}”，户外景点 {', '.join(outdoor)} 可能受影响。",
                    ))

        if trip_plan.budget:
            budget = trip_plan.budget
            component_total = (
                budget.total_attractions + budget.total_hotels
                + budget.total_meals + budget.total_transportation
            )
            if budget.total != component_total:
                issues.append(QualityIssue(
                    code="budget_mismatch", severity="error",
                    message=f"预算总计 ¥{budget.total} 与各项合计 ¥{component_total} 不一致。",
                ))

        if (
            request.arrival_time
            and request.arrival_transport
            and request.arrival_transport not in {"已在此地", "自驾"}
            and not request.arrival_location
        ):
            issues.append(QualityIssue(
                code="arrival_hub_missing", severity="warning",
                message="未填写具体抵达车站或机场，无法计算交通枢纽到酒店的真实用时。",
            ))
        if (
            request.departure_time
            and request.departure_transport
            and request.departure_transport != "自驾"
            and not request.departure_location
        ):
            issues.append(QualityIssue(
                code="departure_hub_missing", severity="warning",
                message="未填写具体离开车站或机场，无法计算酒店到交通枢纽的真实用时。",
            ))

        # 只有提供了边界时间，时间轴才会生成抵达/离开枢纽节点。
        # 用户仅预选车站、但把时间留空时，不能反过来误报“地图无法确认”。
        if request.arrival_location and request.arrival_time:
            first_timeline = trip_plan.days[0].timeline if trip_plan.days else []
            if not any(item.kind == "hub" for item in first_timeline):
                issues.append(QualityIssue(
                    code="arrival_hub_unresolved", severity="error",
                    message=f"无法在地图中确认抵达枢纽“{request.arrival_location}”。",
                ))
        if request.departure_location and request.departure_time and trip_plan.days:
            hub_items = [
                item for item in trip_plan.days[-1].timeline if item.kind == "hub"
            ]
            if not hub_items:
                issues.append(QualityIssue(
                    code="departure_hub_unresolved", severity="error",
                    message=f"无法在地图中确认离开枢纽“{request.departure_location}”。",
                ))
            else:
                departure = cls._time_to_minutes(request.departure_time)
                hub_arrival = cls._time_to_minutes(hub_items[-1].start_time)
                buffer_minutes = cls._terminal_buffer_minutes(
                    request.departure_transport or "", arrival=False
                )
                if (
                    departure is not None
                    and hub_arrival is not None
                    and hub_arrival > departure - buffer_minutes
                ):
                    issues.append(QualityIssue(
                        code="departure_hub_late", severity="error",
                        day_index=len(trip_plan.days) - 1,
                        message=(
                            f"最后一天预计 {hub_items[-1].start_time} 才抵达"
                            f"{hub_items[-1].name}，无法满足 {request.departure_time} "
                            "的离开时间和必要候车/值机缓冲。"
                        ),
                    ))

        return issues

    def _create_quality_report(self, trip_plan: TripPlan, request: TripRequest) -> QualityReport:
        """生成确定性校验结果，并由 Critic Agent 给出可读的改进建议。"""
        issues = self._validate_trip_plan(trip_plan, request)
        summary = {
            "city": trip_plan.city,
            "dates": f"{trip_plan.start_date} 至 {trip_plan.end_date}",
            "travel_pace": self._pace_profile(request)["label"],
            "days": [
                {
                    "date": day.date,
                    "attractions": [attraction.name for attraction in day.attractions],
                    "meals": [
                        {
                            "type": meal.type,
                            "name": meal.name,
                            "opening_hours": meal.opening_hours,
                        }
                        for meal in day.meals
                    ],
                    "timeline": [
                        {
                            "kind": item.kind,
                            "name": item.name,
                            "start_time": item.start_time,
                            "end_time": item.end_time,
                            "transport_mode": item.transport_mode,
                            "duration_minutes": item.duration_minutes,
                        }
                        for item in day.timeline
                    ],
                    "transportation": day.transportation,
                }
                for day in trip_plan.days
            ],
            "weather": [item.model_dump() for item in trip_plan.weather_info],
            "validation_issues": [item.model_dump() for item in issues],
        }
        try:
            advice = self._run_agent(
                self.critic_agent,
                f"请审查以下行程数据：\n{json.dumps(summary, ensure_ascii=False)}",
                "行程CriticAgent",
            )
        except Exception as error:
            logger.warning(f"Critic Agent 调用失败，仅返回确定性校验结果: {error}")
            advice = "已完成规则校验；出发前请再次确认景点开放时间、预约要求和天气预报。"

        return QualityReport(
            passed=not any(issue.severity == "error" for issue in issues),
            issues=issues,
            critic_advice=advice,
        )

    def reflow_edited_plan(
        self,
        trip_plan: TripPlan,
        day_indices: Optional[List[int]] = None,
    ) -> TripPlan:
        """按原始旅行条件重算用户编辑过的日期、预算和确定性校验。"""
        request = trip_plan.request_context
        if request is None:
            raise ValueError("这个历史行程没有保存原始生成条件，请重新生成后再使用自动重排。")

        selected = {
            index for index in (day_indices or range(len(trip_plan.days)))
            if 0 <= index < len(trip_plan.days)
        }
        if not selected:
            selected = set(range(len(trip_plan.days)))

        self._reset_run_state(request)
        self._build_daily_timelines(trip_plan, request, selected)
        self._recalculate_budget(trip_plan, request)
        issues = self._validate_trip_plan(trip_plan, request)
        trip_plan.quality_report = QualityReport(
            passed=not any(issue.severity == "error" for issue in issues),
            issues=issues,
            critic_advice=(
                "已按修改后的地点顺序重新计算交通与时间；请处理标出的时间冲突。"
                if issues else "已按修改后的地点顺序重新计算交通、时间和预算。"
            ),
        )
        return trip_plan

    def _create_partial_plan(
        self,
        request: TripRequest,
        candidates: List[dict],
        weather_info: List[WeatherInfo],
    ) -> TripPlan:
        """Use only verified POI candidates when LLM planning is unavailable."""
        from datetime import datetime, timedelta

        start_date = datetime.strptime(request.start_date, "%Y-%m-%d")
        attractions: List[Attraction] = []
        seen_names: set[str] = set()
        for candidate in candidates:
            name = (candidate.get("display_name") or candidate.get("area_name") or "").strip()
            longitude = candidate.get("longitude")
            latitude = candidate.get("latitude")
            if not name or name in seen_names or longitude is None or latitude is None:
                continue
            try:
                attractions.append(Attraction(
                    name=name,
                    address=candidate.get("address", ""),
                    location=Location(longitude=float(longitude), latitude=float(latitude)),
                    visit_duration=120,
                    description="来自高德地图的真实景点信息，建议出发前确认开放时间。",
                    category=candidate.get("broad_category", candidate.get("category", "景点")),
                    poi_id=candidate.get("id", ""),
                    ticket_price=0,
                ))
                seen_names.add(name)
            except (TypeError, ValueError):
                continue

        if not attractions:
            raise TripPlanningError("未获得可用的真实景点数据")

        day_attractions: List[List[Attraction]] = [[] for _ in range(request.travel_days)]
        for index, attraction in enumerate(attractions):
            day_attractions[index % request.travel_days].append(attraction)

        days: List[DayPlan] = []
        for i in range(request.travel_days):
            current_date = start_date + timedelta(days=i)
            days.append(DayPlan(
                date=current_date.strftime("%Y-%m-%d"),
                day_index=i,
                description="已根据真实景点信息整理，餐饮和住宿信息待补全。",
                transportation=request.transportation,
                accommodation=request.accommodation,
                attractions=day_attractions[i],
                meals=[],
            ))

        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=weather_info,
            overall_suggestions="本计划仅包含已获取的真实景点；餐饮、住宿、门票和预算信息需要补充确认。",
            budget=Budget(),
            quality_report=QualityReport(
                passed=False,
                issues=[QualityIssue(
                    code="partial_plan",
                    severity="warning",
                    message="行程生成降级：仅保留已获取的真实景点，其他信息待补全。",
                )],
                critic_advice="请检查景点分配，并补充餐饮、住宿和门票信息后再出行。",
            ),
            request_context=request,
        )


# ==================== Shared clients, isolated planning sessions ====================

_planner_runtime: Optional[_PlannerRuntime] = None
_planner_runtime_lock = Lock()


def _get_planner_runtime() -> _PlannerRuntime:
    """Initialize reusable clients once, including simultaneous first requests."""
    global _planner_runtime
    with _planner_runtime_lock:
        if _planner_runtime is None:
            llm = get_llm()
            runtime = _PlannerRuntime(
                llm=llm,
                fallback_llm=get_fallback_llm(),
                weather_service=get_weather_service(),
                attraction_agent=create_react_agent(
                    model=llm, tools=[amap_text_search], prompt=ATTRACTION_AGENT_PROMPT,
                ),
                weather_agent=create_react_agent(model=llm, tools=[], prompt=WEATHER_AGENT_PROMPT),
                critic_agent=create_react_agent(model=llm, tools=[], prompt=CRITIC_AGENT_PROMPT),
            )
            _planner_runtime = runtime
            logger.info("规划模型与工具客户端初始化完成；每个任务使用独立规划会话")
    return _planner_runtime


def get_trip_planner_agent() -> MultiAgentTripPlanner:
    """Create a request-owned session for generation or edit/reflow."""
    return MultiAgentTripPlanner()
