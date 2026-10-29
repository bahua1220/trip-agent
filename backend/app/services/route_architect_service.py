"""区域环线推荐：路线知识骨架 + 高德逐段校验。"""

from __future__ import annotations

import json
import math
import re
from typing import List, Optional

from ..models.schemas import (
    RouteDraftRequest,
    RouteOption,
    RouteRecommendationRequest,
    RouteSegment,
)
from .amap_service import get_amap_service
from .llm_service import get_llm
from .logger import logger


ROUTE_TEMPLATES: List[dict] = [
    {
        "id": "tibet_linzhi_shigatse",
        "regions": ["西藏", "藏区"],
        "name": "林芝·日喀则经典全景线",
        "summary": "兼顾藏东自然风光与日喀则方向的人文、雪山景观，适合第一次进藏。",
        "nodes": ["拉萨", "林芝", "波密", "林芝", "拉萨", "日喀则", "珠峰大本营", "日喀则"],
        "min_days": 8,
        "ideal_days": 10,
        "max_days": 13,
        "tags": ["经典", "自然风光", "历史文化", "雪山"],
    },
    {
        "id": "tibet_linzhi_shannan",
        "regions": ["西藏", "藏区"],
        "name": "林芝·山南舒缓环线",
        "summary": "以拉萨、林芝和山南为核心，路线相对集中，适合不想连续长途赶路的人。",
        "nodes": ["拉萨", "巴松措", "林芝", "加查", "山南"],
        "min_days": 6,
        "ideal_days": 8,
        "max_days": 10,
        "tags": ["舒缓", "自然风光", "人文", "初次进藏"],
    },
    {
        "id": "tibet_shigatse_namtso",
        "regions": ["西藏", "藏区"],
        "name": "日喀则·纳木措经典环线",
        "summary": "从拉萨串联羊湖、日喀则和藏北湖泊，重点体验圣湖与高原人文。",
        "nodes": ["拉萨", "羊卓雍措", "日喀则", "班戈", "纳木措"],
        "min_days": 5,
        "ideal_days": 7,
        "max_days": 9,
        "tags": ["经典", "湖泊", "自然风光", "历史文化"],
    },
    {
        "id": "xinjiang_north",
        "regions": ["新疆", "北疆"],
        "name": "北疆喀纳斯·伊犁大环线",
        "summary": "覆盖阿勒泰、喀纳斯与伊犁草原，风景类型丰富，但整体里程较长。",
        "nodes": ["乌鲁木齐", "富蕴", "布尔津", "喀纳斯", "克拉玛依", "赛里木湖", "伊宁", "那拉提", "巴音布鲁克"],
        "min_days": 10,
        "ideal_days": 13,
        "max_days": 16,
        "tags": ["自然风光", "草原", "湖泊", "深度"],
    },
    {
        "id": "xinjiang_duku",
        "regions": ["新疆", "南疆", "北疆"],
        "name": "独库公路南北疆环线",
        "summary": "从北疆穿越天山到南疆，再经库尔勒返回，适合偏好公路景观的自驾者。",
        "nodes": ["乌鲁木齐", "奎屯", "那拉提", "巴音布鲁克", "库车", "库尔勒"],
        "min_days": 7,
        "ideal_days": 9,
        "max_days": 12,
        "tags": ["自驾", "公路", "自然风光", "峡谷"],
    },
    {
        "id": "xinjiang_ili",
        "regions": ["新疆", "北疆", "伊犁"],
        "name": "伊犁草原轻环线",
        "summary": "聚焦赛里木湖与伊犁河谷，节点更少，适合时间有限的草原湖泊主题旅行。",
        "nodes": ["乌鲁木齐", "奎屯", "赛里木湖", "伊宁", "那拉提"],
        "min_days": 6,
        "ideal_days": 8,
        "max_days": 10,
        "tags": ["草原", "湖泊", "轻松", "自然风光"],
    },
    {
        "id": "qinggan_classic",
        "regions": ["青甘", "青海", "甘肃"],
        "name": "青甘经典大环线",
        "summary": "串联青海湖、柴达木、敦煌、嘉峪关、张掖与祁连，景观类型最完整。",
        "nodes": ["西宁", "青海湖", "茶卡盐湖", "大柴旦", "敦煌", "嘉峪关", "张掖", "祁连"],
        "min_days": 7,
        "ideal_days": 9,
        "max_days": 11,
        "tags": ["经典", "自然风光", "历史文化", "沙漠"],
    },
    {
        "id": "qinghai_lakes",
        "regions": ["青甘", "青海"],
        "name": "青海湖·祁连轻环线",
        "summary": "以青海湖、茶卡和祁连为主，减少河西走廊长距离移动。",
        "nodes": ["西宁", "青海湖", "茶卡盐湖", "祁连"],
        "min_days": 4,
        "ideal_days": 6,
        "max_days": 8,
        "tags": ["湖泊", "草原", "轻松", "自然风光"],
    },
    {
        "id": "west_sichuan_daocheng",
        "regions": ["川西", "四川西部"],
        "name": "川西稻城亚丁经典环线",
        "summary": "从成都经康定、新都桥前往稻城亚丁，并通过丹巴方向形成环线。",
        "nodes": ["成都", "康定", "新都桥", "理塘", "稻城亚丁", "理塘", "丹巴"],
        "min_days": 8,
        "ideal_days": 10,
        "max_days": 13,
        "tags": ["雪山", "自然风光", "摄影", "经典"],
    },
    {
        "id": "west_sichuan_small_loop",
        "regions": ["川西", "四川西部"],
        "name": "川西小环线",
        "summary": "成都出发串联康定、新都桥、丹巴与四姑娘山，适合中短假期。",
        "nodes": ["成都", "康定", "新都桥", "丹巴", "四姑娘山"],
        "min_days": 5,
        "ideal_days": 7,
        "max_days": 9,
        "tags": ["轻环线", "雪山", "摄影", "自然风光"],
    },
]


# 省级目的地的候选城市种子。用户只输入省份时，由路线 Agent 根据天数截取、
# 组合并用真实地理距离校验；这些种子也作为 LLM 不可用时的稳定兜底。
REGION_CITY_SEEDS = {
    "河北": ["石家庄", "保定", "承德", "秦皇岛"],
    "山西": ["太原", "平遥", "临汾", "大同"],
    "辽宁": ["沈阳", "本溪", "丹东", "大连"],
    "吉林": ["长春", "吉林市", "延边", "长白山"],
    "黑龙江": ["哈尔滨", "牡丹江", "齐齐哈尔", "漠河"],
    "江苏": ["南京", "扬州", "无锡", "苏州"],
    "浙江": ["杭州", "绍兴", "宁波", "温州"],
    "安徽": ["合肥", "池州", "黄山", "宣城"],
    "福建": ["福州", "泉州", "厦门", "武夷山"],
    "江西": ["南昌", "景德镇", "婺源", "庐山", "上饶"],
    "山东": ["济南", "曲阜", "青岛", "烟台", "威海"],
    "河南": ["郑州", "开封", "洛阳", "安阳"],
    "湖北": ["武汉", "宜昌", "恩施", "十堰"],
    "湖南": ["长沙", "衡阳", "张家界", "湘西"],
    "广东": ["广州", "佛山", "潮州", "汕头"],
    "海南": ["海口", "文昌", "琼海", "三亚"],
    "四川": ["成都", "乐山", "宜宾", "广元"],
    "贵州": ["贵阳", "安顺", "黔东南", "荔波"],
    "云南": ["昆明", "大理", "丽江", "香格里拉"],
    "陕西": ["西安", "宝鸡", "汉中", "延安"],
    "甘肃": ["兰州", "张掖", "嘉峪关", "敦煌"],
    "青海": ["西宁", "青海湖", "茶卡盐湖", "德令哈"],
    "内蒙古": ["呼和浩特", "包头", "鄂尔多斯", "呼伦贝尔"],
    "广西": ["南宁", "桂林", "柳州", "北海"],
    "西藏": ["拉萨", "林芝", "山南", "日喀则"],
    "宁夏": ["银川", "中卫", "固原"],
    "新疆": ["乌鲁木齐", "吐鲁番", "伊宁", "喀什"],
    "青甘": ["西宁", "青海湖", "张掖", "嘉峪关", "敦煌"],
}


class RouteArchitectService:
    """根据少量用户条件自主生成、排序并校验跨城路线或自驾环线。"""

    @staticmethod
    def _normalize_region(region: str) -> str:
        normalized = str(region or "").strip()
        for suffix in (
            "维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "特别行政区", "省"
        ):
            normalized = normalized.replace(suffix, "")
        return normalized

    @staticmethod
    def _haversine(
        origin: tuple[float, float], destination: tuple[float, float]
    ) -> float:
        lon1, lat1, lon2, lat2 = map(
            math.radians, (*origin, *destination)
        )
        delta_lon, delta_lat = lon2 - lon1, lat2 - lat1
        value = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
        return 6371 * 2 * math.asin(math.sqrt(value))

    @staticmethod
    def _matches_region(template: dict, region: str) -> bool:
        normalized = RouteArchitectService._normalize_region(region)
        return any(
            alias in normalized or normalized in alias
            for alias in template["regions"]
        )

    def _seed_nodes(self, region: str) -> List[str]:
        normalized = self._normalize_region(region)
        for alias, nodes in REGION_CITY_SEEDS.items():
            if alias in normalized or normalized in alias:
                return list(nodes)
        return []

    def _build_seed_templates(
        self, request: RouteRecommendationRequest
    ) -> List[dict]:
        seeds = self._seed_nodes(request.region)
        if len(seeds) < 2:
            return []

        # 通常给每个住宿节点留出至少 1 天，5 天行程优先推荐 3 个城市。
        node_limit = min(len(seeds), max(2, min(5, (request.travel_days + 1) // 2)))
        variants = [
            ("经典初游线", seeds[:node_limit], ["经典", "人文历史", "特色美食"]),
            (
                "精华主题线",
                seeds[1:node_limit + 1] if len(seeds) > node_limit else seeds[-node_limit:],
                ["自然风光", "小众探索", "慢节奏"],
            ),
        ]
        if len(seeds) >= 4 and node_limit >= 3:
            variants.append(
                ("舒缓组合线", [seeds[0], seeds[2], seeds[3]], ["慢节奏", "经典"])
            )

        templates = []
        normalized = self._normalize_region(request.region)
        for index, (name, nodes, tags) in enumerate(variants):
            unique_nodes = list(dict.fromkeys(nodes))[:request.travel_days]
            if len(unique_nodes) < 2:
                continue
            templates.append({
                "id": f"region_{normalized}_{index}",
                "regions": [normalized],
                "name": name,
                "summary": (
                    f"从{unique_nodes[0]}出发，串联"
                    f"{'、'.join(unique_nodes[1:])}；已按 {request.travel_days} 天控制跨城数量。"
                ),
                "nodes": unique_nodes,
                "min_days": len(unique_nodes),
                "ideal_days": max(len(unique_nodes) + 1, request.travel_days),
                "max_days": max(len(unique_nodes) + 4, request.travel_days),
                "tags": tags,
            })
        return templates

    def _generate_llm_templates(
        self, request: RouteRecommendationRequest
    ) -> List[dict]:
        """为未收录地区生成候选城市；失败时由调用方给出可编辑的稳定提示。"""
        prompt = f"""
你是中国旅行路线架构师。为“{request.region}”规划 {request.travel_days} 天省内或区域跨城旅行。
用户偏好：{', '.join(request.preferences) or '未指定'}。
只返回 JSON 数组，提供 2 到 3 套路线。每项字段必须为：
id、name、summary、nodes、tags、min_days、ideal_days、max_days。
nodes 必须是 2 到 {min(5, request.travel_days)} 个真实城市或适合作为住宿基地的地区，
按合理移动顺序排列；不要加入餐厅或单个小景点，不要自动闭环。
""".strip()
        try:
            response = get_llm().invoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            if isinstance(content, list):
                content = "".join(
                    item.get("text", "") if isinstance(item, dict) else str(item)
                    for item in content
                )
            match = re.search(r"\[.*\]", str(content), re.S)
            payload = json.loads(match.group(0) if match else str(content))
        except Exception as error:
            logger.warning(f"区域路线 LLM 生成失败 [{request.region}]: {error}")
            return []

        templates = []
        for index, item in enumerate(payload if isinstance(payload, list) else []):
            nodes = list(dict.fromkeys(
                str(node).strip() for node in item.get("nodes", []) if str(node).strip()
            ))[:min(5, request.travel_days)]
            if len(nodes) < 2:
                continue
            templates.append({
                "id": str(item.get("id") or f"ai_region_{index}"),
                "regions": [self._normalize_region(request.region)],
                "name": str(item.get("name") or f"候选路线 {index + 1}"),
                "summary": str(item.get("summary") or "AI 推荐的区域跨城路线。"),
                "nodes": nodes,
                "min_days": max(2, int(item.get("min_days") or len(nodes))),
                "ideal_days": max(len(nodes), int(item.get("ideal_days") or request.travel_days)),
                "max_days": max(request.travel_days, int(item.get("max_days") or request.travel_days + 3)),
                "tags": [str(tag) for tag in item.get("tags", [])][:6],
            })
        return templates

    @staticmethod
    def _rotate_to_start(nodes: List[str], start: Optional[str]) -> tuple[List[str], bool]:
        if not start:
            return list(nodes), False
        for index, node in enumerate(nodes):
            if start in node or node in start:
                return nodes[index:] + nodes[:index], False
        return [start, *nodes], True

    @staticmethod
    def _known_same_region_pair(origin: str, destination: str) -> bool:
        """判断两个节点是否属于同一组省内城市种子。"""
        return any(
            origin in nodes and destination in nodes
            for nodes in REGION_CITY_SEEDS.values()
        )

    def _build_segment(
        self,
        origin: str,
        destination: str,
        max_drive_hours: float,
        transport_preference: str = "self_drive",
    ) -> RouteSegment:
        amap = get_amap_service()
        administrative_geocoder = getattr(
            amap, "geocode_administrative_location", None
        )
        origin_coords = (
            administrative_geocoder(origin)
            if callable(administrative_geocoder) else None
        ) or amap.geocode_location(origin)
        destination_coords = (
            administrative_geocoder(destination)
            if callable(administrative_geocoder) else None
        ) or amap.geocode_location(destination)
        straight_distance = 0.0
        if origin_coords and destination_coords:
            straight_distance = self._haversine(origin_coords, destination_coords)

        invalid_regional_distance = (
            straight_distance > 1200
            and self._known_same_region_pair(origin, destination)
        )
        if invalid_regional_distance:
            logger.warning(
                f"跨城节点疑似同名地点误定位: {origin} → {destination} | "
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
            summary = amap.get_driving_route_summary(
                f"{origin_coords[0]},{origin_coords[1]}",
                f"{destination_coords[0]},{destination_coords[1]}",
            )
        if summary is None:
            route_factor = 1.25 if selected_mode == "self_drive" else 1.1
            distance = round(straight_distance * route_factor, 1)
            if selected_mode == "high_speed_rail":
                duration_minutes = round(distance / 250 * 60 + 90) if distance else 0
            elif selected_mode == "train":
                duration_minutes = round(distance / 100 * 60 + 60) if distance else 0
            elif selected_mode == "flight":
                duration_minutes = round(distance / 750 * 60 + 180) if distance else 0
            else:
                duration_minutes = round(distance / 55 * 60) if distance else 0
            summary = {
                "distance_km": distance,
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

    def recommend(self, request: RouteRecommendationRequest) -> List[RouteOption]:
        is_loop = request.trip_mode == "road_trip_loop"
        if is_loop:
            templates = [
                template for template in ROUTE_TEMPLATES
                if self._matches_region(template, request.region)
            ]
            transport_preference = "self_drive"
        else:
            templates = self._build_seed_templates(request)
            if not templates:
                templates = self._generate_llm_templates(request)
            transport_preference = request.intercity_transport

        if not templates:
            if is_loop:
                supported = "西藏、新疆、青甘、青海、甘肃、川西"
                raise ValueError(f"当前自驾环线模板暂支持 {supported}")
            raise ValueError(
                "暂时无法可靠识别该区域的跨城节点，请输入省份名称或手动添加至少两个城市。"
            )

        preferences = set(request.preferences)
        ranked = sorted(
            templates,
            key=lambda item: (
                0 if item["min_days"] <= request.travel_days <= item["max_days"] else 1,
                abs(request.travel_days - item["ideal_days"]),
                -len(preferences.intersection(item["tags"])),
            ),
        )[:3]

        options = []
        for template in ranked:
            route_nodes, custom_start_added = self._rotate_to_start(
                template["nodes"], request.start_location
            )
            # 当前详细规划器每天最多推进一个跨城路段；节点多于天数时不可提交。
            if len(route_nodes) > request.travel_days:
                continue
            option_nodes = route_nodes + [route_nodes[0]] if is_loop else route_nodes
            segments = [
                self._build_segment(
                    origin,
                    destination,
                    request.max_drive_hours_per_day,
                    transport_preference,
                )
                for origin, destination in zip(option_nodes, option_nodes[1:])
            ]
            warnings = []
            if custom_start_added:
                warnings.append(
                    f"自定义起点“{request.start_location}”不在模板节点中，已作为接驳节点加入。"
                )
            if request.travel_days < template["min_days"]:
                warnings.append(
                    f"该路线建议至少 {template['min_days']} 天，当前 {request.travel_days} 天偏紧。"
                )
            if request.travel_days > template["max_days"]:
                warnings.append(
                    f"当前天数较充裕，可在重点节点增加停留时间。"
                )
            for segment in segments:
                if segment.duration_minutes <= 0:
                    warnings.append(
                        f"{segment.origin} → {segment.destination} 的路程暂未获取，请确认节点。"
                    )
                elif segment.exceeds_daily_limit:
                    warnings.append(
                        f"{segment.origin} → {segment.destination} 约 "
                        f"{segment.duration_minutes / 60:.1f} 小时，超过每日驾驶上限。"
                    )
                elif segment.requires_confirmation:
                    warnings.append(
                        f"{segment.origin} → {segment.destination} 建议{segment.transport_mode}，"
                        "当前为估算耗时，出发前请确认真实班次。"
                    )

            preference_matches = len(preferences.intersection(template["tags"]))
            day_penalty = abs(request.travel_days - template["ideal_days"]) * 4
            drive_penalty = sum(8 for segment in segments if segment.exceeds_daily_limit)
            fit_score = max(
                1,
                min(99, 88 + preference_matches * 3 - day_penalty - drive_penalty),
            )
            total_distance = round(sum(item.distance_km for item in segments), 1)
            total_minutes = sum(item.duration_minutes for item in segments)
            options.append(RouteOption(
                id=template["id"],
                name=template["name"],
                summary=template["summary"],
                nodes=option_nodes,
                segments=segments,
                total_distance_km=total_distance,
                total_drive_minutes=total_minutes,
                max_segment_minutes=max(
                    (item.duration_minutes for item in segments), default=0
                ),
                fit_score=fit_score,
                tags=template["tags"],
                warnings=list(dict.fromkeys(warnings)),
            ))

        if not options:
            raise ValueError(
                "当前旅行天数不足以覆盖候选节点，请增加天数或减少城市数量。"
            )
        options.sort(key=lambda option: option.fit_score, reverse=True)
        logger.info(
            f"智能路线推荐 [{request.region}]: "
            + ", ".join(f"{item.name}({item.fit_score})" for item in options)
        )
        return options

    def validate_draft(self, request: RouteDraftRequest) -> RouteOption:
        """重新计算用户编辑后的跨城路线，不依赖原模板顺序。"""
        nodes = list(request.nodes)
        if nodes[-1] == nodes[0]:
            nodes.pop()
        if len(nodes) < 2:
            raise ValueError("跨城路线至少需要 2 个节点")
        if len(nodes) > request.travel_days:
            raise ValueError(
                f"当前 {request.travel_days} 天无法覆盖 {len(nodes)} 个住宿节点，"
                "请删除节点或增加旅行天数。"
            )

        is_loop = request.trip_mode == "road_trip_loop"
        option_nodes = [*nodes, nodes[0]] if is_loop else nodes
        transport_preference = (
            "self_drive" if is_loop else request.intercity_transport
        )
        segments = [
            self._build_segment(
                origin,
                destination,
                request.max_drive_hours_per_day,
                transport_preference,
            )
            for origin, destination in zip(option_nodes, option_nodes[1:])
        ]
        warnings = []
        for segment in segments:
            if segment.duration_minutes <= 0:
                warnings.append(
                    f"{segment.origin} → {segment.destination} 暂未获取到可靠路程，请检查地点名称。"
                )
            elif segment.exceeds_daily_limit:
                warnings.append(
                    f"{segment.origin} → {segment.destination} 约 "
                    f"{segment.duration_minutes / 60:.1f} 小时，超过每日驾驶上限。"
                )
            elif segment.requires_confirmation:
                warnings.append(
                    f"{segment.origin} → {segment.destination} 建议{segment.transport_mode}，"
                    "请在出发前确认实际班次。"
                )
        spare_days = request.travel_days - len(nodes)
        if spare_days == 0:
            warnings.append("每天都需要更换住宿点，整体节奏较紧凑。")

        warning_penalty = sum(
            10 for segment in segments
            if segment.exceeds_daily_limit or segment.duration_minutes <= 0
        )
        total_minutes = sum(item.duration_minutes for item in segments)
        return RouteOption(
            id="custom_route",
            name="已编辑路线",
            summary=(
                f"已按你的节点顺序重新计算；"
                f"{spare_days} 天可用于在重点节点增加停留。"
            ),
            nodes=option_nodes,
            segments=segments,
            total_distance_km=round(
                sum(item.distance_km for item in segments), 1
            ),
            total_drive_minutes=total_minutes,
            max_segment_minutes=max(
                (item.duration_minutes for item in segments), default=0
            ),
            fit_score=max(1, 92 - warning_penalty),
            tags=["用户编辑", "已重新校验"],
            warnings=list(dict.fromkeys(warnings)),
        )


_route_architect_service: Optional[RouteArchitectService] = None


def get_route_architect_service() -> RouteArchitectService:
    global _route_architect_service
    if _route_architect_service is None:
        _route_architect_service = RouteArchitectService()
    return _route_architect_service
