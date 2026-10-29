"""数据模型定义"""

from typing import List, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import date, datetime


# ============ 请求模型 ============

class TripRequest(BaseModel):
    """旅行规划请求"""
    city: str = Field(..., min_length=1, max_length=100, description="目的地城市", example="北京")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD", example="2025-06-01")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD", example="2025-06-03")
    travel_days: int = Field(..., description="旅行天数", ge=1, le=30, example=3)
    transportation: str = Field(..., description="交通方式", example="公共交通")
    accommodation: str = Field(..., description="住宿偏好", example="经济型酒店")
    preferences: List[str] = Field(default=[], description="旅行偏好标签", example=["历史文化", "美食"])
    travel_pace: str = Field(
        default="balanced",
        description="游玩节奏: relaxed / balanced / compact",
    )
    free_text_input: Optional[str] = Field(
        default="",
        max_length=500,
        description="额外要求",
        example="希望多安排一些博物馆",
    )
    wechat_content_check_code: Optional[str] = Field(
        default=None,
        max_length=256,
        exclude=True,
        description="小程序端用于内容安全校验的一次性微信登录凭证",
    )
    arrival_transport: Optional[str] = Field(default=None, description="抵达方式: 飞机/火车/自驾/已在此地")
    arrival_time: Optional[str] = Field(default=None, description="抵达时间 HH:MM", example="14:30")
    arrival_location: Optional[str] = Field(
        default=None,
        max_length=100,
        description="实际抵达交通枢纽，如济南西站或济南遥墙国际机场",
    )
    arrival_location_poi_id: Optional[str] = Field(
        default=None, max_length=100, description="用户选中的抵达枢纽高德 POI ID"
    )
    arrival_location_longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    arrival_location_latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    departure_transport: Optional[str] = Field(default=None, description="离开方式: 飞机/高铁/火车/自驾")
    departure_time: Optional[str] = Field(default=None, description="离开时间 HH:MM", example="16:00")
    departure_location: Optional[str] = Field(
        default=None,
        max_length=100,
        description="实际离开交通枢纽，如济南西站或济南遥墙国际机场",
    )
    departure_location_poi_id: Optional[str] = Field(
        default=None, max_length=100, description="用户选中的离开枢纽高德 POI ID"
    )
    departure_location_longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    departure_location_latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    trip_mode: str = Field(
        default="single_city",
        description="旅行模式: single_city / multi_city / road_trip_loop",
    )
    destinations: List[str] = Field(
        default_factory=list,
        description="跨城路线节点，按旅行顺序填写",
    )
    start_location: Optional[str] = Field(default=None, description="跨城路线出发地")
    end_location: Optional[str] = Field(default=None, description="跨城路线最终目的地")
    return_to_start: bool = Field(default=False, description="是否返回出发地")
    intercity_transport: str = Field(
        default="auto",
        description="跨城交通偏好: auto / high_speed_rail / train / flight / self_drive",
    )
    max_drive_hours_per_day: float = Field(
        default=6.0,
        ge=1.0,
        le=12.0,
        description="每天最多可接受的驾驶小时数",
    )

    @field_validator("trip_mode")
    @classmethod
    def validate_trip_mode(cls, value: str) -> str:
        allowed = {"single_city", "multi_city", "road_trip_loop"}
        if value not in allowed:
            raise ValueError("旅行模式必须是 single_city、multi_city 或 road_trip_loop")
        return value

    @field_validator("travel_pace")
    @classmethod
    def validate_travel_pace(cls, value: str) -> str:
        aliases = {
            "轻松慢游": "relaxed",
            "慢节奏": "relaxed",
            "适中": "balanced",
            "适中节奏": "balanced",
            "紧凑": "compact",
            "紧凑行程": "compact",
        }
        normalized = aliases.get(str(value).strip(), str(value).strip())
        if normalized not in {"relaxed", "balanced", "compact"}:
            raise ValueError("游玩节奏必须是 relaxed、balanced 或 compact")
        return normalized

    @field_validator("intercity_transport")
    @classmethod
    def validate_intercity_transport(cls, value: str) -> str:
        allowed = {"auto", "high_speed_rail", "train", "flight", "self_drive"}
        if value not in allowed:
            raise ValueError(
                "跨城交通必须是 auto、high_speed_rail、train、flight 或 self_drive"
            )
        return value

    @field_validator("destinations")
    @classmethod
    def normalize_destinations(cls, values: List[str]) -> List[str]:
        result = []
        for value in values:
            location = str(value).strip()
            # 环线可能合理地再次经过同一住宿枢纽，只去掉连续重复节点。
            if location and (not result or location != result[-1]):
                result.append(location)
        if len(result) > 12:
            raise ValueError("跨城路线节点最多填写 12 个")
        return result

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date_format(cls, value: str) -> str:
        """仅接受严格的 YYYY-MM-DD 日期，防止无效请求进入 Agent 流程。"""
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise ValueError("日期必须为 YYYY-MM-DD 格式") from error
        if parsed.isoformat() != value:
            raise ValueError("日期必须为 YYYY-MM-DD 格式")
        return value

    @field_validator("arrival_time", "departure_time")
    @classmethod
    def validate_time_format(cls, value: Optional[str]) -> Optional[str]:
        """抵达和离开时间仅接受 24 小时制 HH:MM。"""
        if value is None:
            return value
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except (TypeError, ValueError) as error:
            raise ValueError("时间必须为 HH:MM 格式") from error
        if parsed.strftime("%H:%M") != value:
            raise ValueError("时间必须为 HH:MM 格式")
        return value

    @model_validator(mode="after")
    def validate_trip_date_range(self):
        """确保结束日期与旅行天数和日期跨度严格一致。"""
        # 兼容旧小程序：过去把节奏混在普通兴趣标签中。节奏只控制每天
        # 安排强度，不能再作为高德景点搜索关键词。
        legacy_pace_tags = {
            "慢节奏": "relaxed",
            "适中节奏": "balanced",
            "紧凑行程": "compact",
        }
        for tag, pace in legacy_pace_tags.items():
            if tag in self.preferences:
                self.travel_pace = pace
        self.preferences = [
            item for item in self.preferences if item not in legacy_pace_tags
        ]
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        if end < start:
            raise ValueError("结束日期不能早于开始日期")
        actual_days = (end - start).days + 1
        if self.travel_days != actual_days:
            raise ValueError(f"旅行天数应为 {actual_days} 天，与所选日期不一致")
        if self.trip_mode != "single_city":
            if len(self.destinations) < 2:
                raise ValueError("多城市或环线模式至少需要填写 2 个路线节点")
            if self.travel_days < len(self.destinations):
                raise ValueError("旅行天数不能少于路线节点数量")
            if not self.start_location:
                self.start_location = self.destinations[0]
            if self.trip_mode == "road_trip_loop":
                self.return_to_start = True
                self.end_location = self.start_location
                self.intercity_transport = "self_drive"
                self.transportation = "自驾"
        return self

    class Config:
        json_schema_extra = {
            "example": {
                "city": "北京",
                "start_date": "2025-06-01",
                "end_date": "2025-06-03",
                "travel_days": 3,
                "transportation": "公共交通",
                "accommodation": "经济型酒店",
                "preferences": ["历史文化", "美食"],
                "free_text_input": "希望多安排一些博物馆"
            }
        }


# ============ 响应模型 ============

class Location(BaseModel):
    """地理位置"""
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")


class TicketInfo(BaseModel):
    """门票信息"""
    price: int = Field(default=0, description="门票价格(元)")
    opening_hours: str = Field(default="", description="开放时间")
    purchase_method: str = Field(default="", description="购票/预约方式")
    source: str = Field(default="", description="数据来源: amap / tavily / llm")
    source_url: str = Field(default="", description="可追溯的网页来源链接")
    confidence: str = Field(default="low", description="可信度: medium / low")


class Attraction(BaseModel):
    """景点信息"""
    name: str = Field(..., description="景点名称")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    visit_duration: int = Field(..., description="建议游览时间(分钟)")
    description: str = Field(..., description="景点描述")
    category: Optional[str] = Field(default="景点", description="景点类别")
    rating: Optional[float] = Field(default=None, description="评分")
    photos: Optional[List[str]] = Field(default_factory=list, description="景点图片URL列表")
    poi_id: Optional[str] = Field(default="", description="POI ID")
    image_url: Optional[str] = Field(default=None, description="图片URL")
    ticket_price: int = Field(default=0, description="门票价格(元, 兼容旧字段)")
    ticket_info: Optional[TicketInfo] = Field(default=None, description="门票详细信息")
    user_locked: bool = Field(default=False, description="是否由用户手动加入并锁定")

    @field_validator("address", mode="before")
    @classmethod
    def normalize_address(cls, value):
        """兼容地图 POI 用 null 或空数组表示缺失地址的情况。"""
        if value is None:
            return ""
        if isinstance(value, list) and all(isinstance(part, str) for part in value):
            return " ".join(part.strip() for part in value if part.strip())
        return value


class RemovedAttraction(BaseModel):
    """系统调整掉的景点，供用户编辑行程时恢复。"""
    attraction: Attraction
    original_day_index: int = Field(ge=0, description="原安排日期，从0开始")
    reason: str = Field(default="行程可执行性调整", description="移出原因")


class Meal(BaseModel):
    """餐饮信息"""
    type: str = Field(..., description="餐饮类型: breakfast/lunch/dinner/snack")
    name: str = Field(..., description="餐饮名称")
    address: Optional[str] = Field(default=None, description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标")
    description: Optional[str] = Field(default=None, description="描述")
    estimated_cost: int = Field(default=0, description="预估费用(元)")
    poi_id: Optional[str] = Field(default=None, description="高德 POI ID")
    recommended_dish: Optional[str] = Field(default=None, description="建议品尝的当地菜品")
    rating: Optional[float] = Field(default=None, description="高德评分；缺失时为 null")
    data_source: Optional[str] = Field(default=None, description="餐厅数据来源")
    opening_hours: str = Field(default="", description="高德返回的营业时间")
    opening_hours_verified: bool = Field(default=False, description="是否确认目标餐次营业")
    route_anchor_name: Optional[str] = Field(default=None, description="该餐次服务的景点或住宿地")
    route_anchor_poi_id: Optional[str] = Field(default=None, description="关联景点 POI；用于景点移除后的重新选址")


class Hotel(BaseModel):
    """酒店信息"""
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="酒店地址")
    location: Optional[Location] = Field(default=None, description="酒店位置")
    price_range: str = Field(default="", description="价格范围")
    rating: str = Field(default="", description="评分")
    distance: str = Field(default="", description="距离景点距离")
    type: str = Field(default="", description="酒店类型")
    estimated_cost: int = Field(default=0, description="预估费用(元/晚)")


class RouteSegment(BaseModel):
    """跨城行程中的单日移动路段。"""
    origin: str = Field(..., description="当天出发地")
    destination: str = Field(..., description="当天目的地")
    transport_mode: str = Field(default="自驾", description="该路段建议的城际交通方式")
    distance_km: float = Field(default=0, description="路段参考距离，公里")
    duration_minutes: int = Field(default=0, description="含必要换乘缓冲的预计耗时，分钟")
    source: str = Field(default="estimated", description="amap 或 estimated")
    requires_confirmation: bool = Field(
        default=False,
        description="是否需要用户另行确认真实班次或线路",
    )
    exceeds_daily_limit: bool = Field(default=False, description="是否超过每日驾驶上限")
    suggested_departure_time: Optional[str] = Field(
        default=None, description="详细编排阶段建议的跨城出发时间 HH:MM"
    )
    suggested_arrival_time: Optional[str] = Field(
        default=None, description="详细编排阶段建议的跨城到达时间 HH:MM"
    )
    timing_source: str = Field(
        default="estimated", description="跨城时刻来源: amap 或 estimated"
    )


class RouteRecommendationRequest(BaseModel):
    """先生成候选路线骨架，确认后再进入完整行程规划。"""
    region: str = Field(min_length=2, max_length=30, description="目标区域，如西藏、新疆")
    travel_days: int = Field(ge=3, le=30, description="可用旅行天数")
    start_location: Optional[str] = Field(default=None, max_length=30)
    preferences: List[str] = Field(default_factory=list)
    max_drive_hours_per_day: float = Field(default=6.0, ge=1.0, le=12.0)
    trip_mode: str = Field(
        default="road_trip_loop",
        description="路线类型: multi_city / road_trip_loop",
    )
    intercity_transport: str = Field(
        default="self_drive",
        description="城际交通: auto / high_speed_rail / train / flight / self_drive",
    )
    wechat_content_check_code: Optional[str] = Field(
        default=None,
        max_length=256,
        exclude=True,
        description="小程序端用于内容安全校验的一次性微信登录凭证",
    )

    @field_validator("trip_mode")
    @classmethod
    def validate_route_trip_mode(cls, value: str) -> str:
        if value not in {"multi_city", "road_trip_loop"}:
            raise ValueError("路线推荐仅支持 multi_city 或 road_trip_loop")
        return value

    @field_validator("intercity_transport")
    @classmethod
    def validate_route_transport(cls, value: str) -> str:
        allowed = {"auto", "high_speed_rail", "train", "flight", "self_drive"}
        if value not in allowed:
            raise ValueError("不支持的城际交通方式")
        return value


class RouteDraftRequest(BaseModel):
    """用户编辑后的环线草稿，用于重新计算与校验。"""
    region: str = Field(min_length=2, max_length=30)
    nodes: List[str] = Field(min_length=2, max_length=12)
    travel_days: int = Field(ge=3, le=30)
    max_drive_hours_per_day: float = Field(default=6.0, ge=1.0, le=12.0)
    trip_mode: str = Field(default="road_trip_loop")
    intercity_transport: str = Field(default="self_drive")
    wechat_content_check_code: Optional[str] = Field(
        default=None,
        max_length=256,
        exclude=True,
        description="小程序端用于内容安全校验的一次性微信登录凭证",
    )

    @field_validator("trip_mode")
    @classmethod
    def validate_route_trip_mode(cls, value: str) -> str:
        if value not in {"multi_city", "road_trip_loop"}:
            raise ValueError("路线校验仅支持 multi_city 或 road_trip_loop")
        return value

    @field_validator("intercity_transport")
    @classmethod
    def validate_route_transport(cls, value: str) -> str:
        allowed = {"auto", "high_speed_rail", "train", "flight", "self_drive"}
        if value not in allowed:
            raise ValueError("不支持的城际交通方式")
        return value

    @field_validator("nodes")
    @classmethod
    def normalize_nodes(cls, values: List[str]) -> List[str]:
        result = []
        for value in values:
            node = str(value).strip()
            if node and (not result or node != result[-1]):
                result.append(node)
        if len(result) < 2:
            raise ValueError("编辑后的路线至少需要 2 个不同节点")
        return result


class RouteOption(BaseModel):
    """路线规划 Agent 返回的一套可选路线。"""
    id: str
    name: str
    summary: str
    nodes: List[str]
    segments: List[RouteSegment]
    total_distance_km: float = 0
    total_drive_minutes: int = 0
    max_segment_minutes: int = 0
    fit_score: float = 0
    tags: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class TransportAlternative(BaseModel):
    """时间轴中某一交通段的可切换方案。"""
    mode: str = Field(..., description="步行 / 骑行 / 公共交通 / 打车 / 自驾")
    route_mode: str = Field(..., description="walking / bicycling / transit / driving")
    duration_minutes: int = Field(..., ge=1, description="门到门预计耗时")
    distance_km: Optional[float] = Field(default=None, description="路线距离")
    source: str = Field(default="amap", description="amap 或 estimated")


class TimelineItem(BaseModel):
    """按实际发生顺序展示的单日时间轴节点。"""
    kind: str = Field(
        ...,
        description="节点类型: hotel / attraction / meal / transport / note",
    )
    name: str = Field(default="", description="地点名称或交通段标题")
    start_time: Optional[str] = Field(default=None, description="开始时间 HH:MM")
    end_time: Optional[str] = Field(default=None, description="结束时间 HH:MM")
    duration_minutes: int = Field(default=0, description="游览、用餐或交通耗时")
    reference_type: Optional[str] = Field(
        default=None,
        description="关联原始对象类型: attraction / meal / hotel",
    )
    reference_index: Optional[int] = Field(default=None, description="原始列表下标")
    meal_type: Optional[str] = Field(default=None, description="餐次类型")
    from_name: Optional[str] = Field(default=None, description="交通起点")
    to_name: Optional[str] = Field(default=None, description="交通终点")
    transport_mode: Optional[str] = Field(default=None, description="该段推荐交通方式")
    distance_km: Optional[float] = Field(default=None, description="该段路线距离")
    source: Optional[str] = Field(default=None, description="amap 或 estimated")
    alternatives: List[TransportAlternative] = Field(
        default_factory=list,
        description="用户可以为这一段单独选择的其他交通方案",
    )
    customized: bool = Field(default=False, description="是否由用户手动更改")
    is_intercity: bool = Field(default=False, description="是否为城市之间的交通段")
    hub_role: Optional[str] = Field(
        default=None,
        description=(
            "交通枢纽节点角色: trip_arrival / trip_departure / "
            "intercity_departure / intercity_arrival"
        ),
    )


class DayPlan(BaseModel):
    """单日行程"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_index: int = Field(..., description="第几天(从0开始)")
    description: str = Field(..., description="当日行程描述")
    transportation: str = Field(..., description="交通方式")
    accommodation: str = Field(..., description="住宿")
    hotel: Optional[Hotel] = Field(default=None, description="推荐酒店")
    attractions: List[Attraction] = Field(default=[], description="景点列表")
    meals: List[Meal] = Field(default=[], description="餐饮列表")
    start_location: Optional[str] = Field(default=None, description="当天出发地")
    end_location: Optional[str] = Field(default=None, description="当天结束地")
    overnight_city: Optional[str] = Field(default=None, description="当晚住宿城市/区域")
    route_segment: Optional[RouteSegment] = Field(default=None, description="当天跨城移动路段")
    is_transit_day: bool = Field(default=False, description="是否以跨城移动为主")
    timeline: List[TimelineItem] = Field(
        default_factory=list,
        description="酒店、餐饮、景点和逐段交通组成的有序日程时间轴",
    )
    transport_overrides: List[TimelineItem] = Field(
        default_factory=list,
        description="用户指定的市内逐段交通，独立于可重建的时间轴保存；仅同起终点时生效",
    )


class WeatherInfo(BaseModel):
    """天气信息"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    day_temp: Union[int, str] = Field(default=0, description="白天温度")
    night_temp: Union[int, str] = Field(default=0, description="夜间温度")
    wind_direction: str = Field(default="", description="风向")
    wind_power: str = Field(default="", description="风力")
    location_name: Optional[str] = Field(default=None, description="该日天气对应地点")

    @field_validator('day_temp', 'night_temp', mode='before')
    @classmethod
    def parse_temperature(cls, v):
        """解析温度,移除°C等单位"""
        if isinstance(v, str):
            # 移除°C, ℃等单位符号
            v = v.replace('°C', '').replace('℃', '').replace('°', '').strip()
            try:
                return int(v)
            except ValueError:
                return 0
        return v


class Budget(BaseModel):
    """预算信息"""
    total_attractions: int = Field(default=0, description="景点门票总费用")
    total_hotels: int = Field(default=0, description="酒店总费用")
    total_meals: int = Field(default=0, description="餐饮总费用")
    total_transportation: int = Field(default=0, description="交通总费用")
    total: int = Field(default=0, description="总费用")


class QualityIssue(BaseModel):
    """行程校验发现的问题。"""
    code: str = Field(..., description="稳定的问题代码")
    severity: str = Field(..., description="error 或 warning")
    message: str = Field(..., description="面向用户的问题说明")
    day_index: Optional[int] = Field(default=None, description="相关行程日，从 0 开始")


class QualityReport(BaseModel):
    """确定性校验与 Critic Agent 的综合报告。"""
    passed: bool = Field(..., description="是否通过所有阻断性校验")
    issues: List[QualityIssue] = Field(default_factory=list, description="发现的问题")
    critic_advice: str = Field(default="", description="Critic Agent 的行程调整建议")


class TripPlan(BaseModel):
    """旅行计划"""
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(..., description="每日行程")
    weather_info: List[WeatherInfo] = Field(default=[], description="天气信息")
    overall_suggestions: str = Field(..., description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息")
    quality_report: Optional[QualityReport] = Field(default=None, description="行程质量校验报告")
    trip_mode: str = Field(default="single_city", description="旅行模式")
    route_nodes: List[str] = Field(default_factory=list, description="跨城路线节点")
    removed_attractions: List[RemovedAttraction] = Field(
        default_factory=list,
        description="生成过程中因时间、路线或营业约束移出的景点",
    )
    request_context: Optional[TripRequest] = Field(
        default=None,
        description="生成行程时使用的条件，供用户编辑后重新计算时间轴",
    )


class TripPlanReflowRequest(BaseModel):
    """用户编辑行程后重新计算指定日期。"""
    plan: TripPlan
    day_indices: List[int] = Field(default_factory=list)

    @field_validator("day_indices")
    @classmethod
    def normalize_day_indices(cls, values: List[int]) -> List[int]:
        return sorted({int(value) for value in values if int(value) >= 0})


class TripPlanResponse(BaseModel):
    """旅行计划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[TripPlan] = Field(default=None, description="旅行计划数据")


class TripTaskCreatedResponse(BaseModel):
    """创建后台旅行规划任务后的即时响应。"""
    task_id: str
    status: str
    progress: int = Field(ge=0, le=100)
    message: str


class TripTaskStatusResponse(TripTaskCreatedResponse):
    """后台旅行规划任务的状态与最终结果。"""
    data: Optional[TripPlan] = None
    error: Optional[str] = None


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=8, max_length=128)
    consent_version: Optional[str] = Field(default=None, max_length=32)


class WechatCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256)
    consent_version: Optional[str] = Field(default=None, max_length=32)


class DeleteAccountRequest(BaseModel):
    confirm: str = Field(pattern=r"^DELETE$")


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class HistoryUpsertRequest(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    plan: TripPlan


class HistoryEntryResponse(HistoryUpsertRequest):
    createdAt: str
    updatedAt: str


class WeatherResponse(BaseModel):
    """天气查询响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[WeatherInfo] = Field(default=[], description="天气信息")


# ============ 错误响应 ============

class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = Field(default=None, description="错误代码")

