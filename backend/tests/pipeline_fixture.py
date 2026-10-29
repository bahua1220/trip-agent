"""Deterministic adapters for HTTP→worker→real planner pipeline tests.

Only external acquisition boundaries are replaced: verified attraction search,
hotel/restaurant POI acquisition, map paths, weather and model responses.
Production filtering, parsing, day allocation, meals, reflow, validation,
task state and history serialization all execute normally.
"""

import json
import math
import re
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from app.agents import trip_planner_agent as planner_module
from app.models.schemas import TicketInfo, WeatherInfo


class PipelineFixture:
    def __init__(self, synchronized=False):
        self.cities = json.loads((Path(__file__).parent / "fixtures" / "pipeline_cities.json").read_text(encoding="utf-8"))
        self.schedule = {
            "南京": self.cities["南京"]["schedule"],
            "江西": [["滕王阁", "八一广场"], ["陶溪川文创街区", "人民公园"], ["御窑博物馆", "陶阳里历史文化街区"]],
        }
        self.calls = []
        self.lock = Lock()
        self.gate = Barrier(2, timeout=10) if synchronized else None
        self.synchronized_sessions = set()

    def record(self, kind, city):
        with self.lock:
            self.calls.append((kind, city))

    def pois(self, city):
        result = []
        stops = ["南昌", "景德镇"] if city == "江西" else [city]
        for stop in stops:
            for index, item in enumerate(self.cities[stop]["attractions"]):
                result.append(dict(
                    id=f"{stop}-attraction-{index}", name=item["name"], address=stop + "离线地址",
                    longitude=item["lng"], latitude=item["lat"], category=item["category"],
                    broad_category=item["category"], type="风景名胜;" + item["category"],
                    typecode="110000", rating=4.8, is_food=False, _candidate_tier="classic",
                    opening_hours=item["hours"], route_stop=stop if city == "江西" else "",
                    _search_rank=index,
                ))
        return result

    def acquire_attractions(self, planner, request):
        self.record("attractions", request.city)
        if request.free_text_input == "模拟景点接口故障":
            planner._transport_hub_cache["failed-job-only"] = "不能进入后续任务"
            raise RuntimeError("离线模拟景点接口故障")
        return {"success": True, "attractions": self.pois(request.city)}

    def nearest_city(self, lng, lat):
        return min(self.cities, key=lambda c: math.hypot(lng-self.cities[c]["center"][0], lat-self.cities[c]["center"][1]))

    def hotels(self, planner, lng, lat, city, accommodation):
        self.record("hotel", city)
        center = self.cities[city]["center"]
        return [dict(
            name="汉庭酒店(" + city + "离线店)", address=city + "离线酒店地址",
            lng=center[0], lat=center[1], distance_km=0.1, distance_m=100,
            type="宾馆酒店", typecode="100105", rating="4.6", tier_rank=0,
        )]

    def restaurants(self, planner, lng, lat, radius=2500, keywords=""):
        city = self.nearest_city(lng, lat)
        self.record("restaurant", city)
        # Force both workers past writing _local_dish_catalogs before either
        # can continue. Timing doesn't depend on arbitrary sleeps.
        if self.gate:
            wait = False
            with self.lock:
                if id(planner) not in self.synchronized_sessions:
                    self.synchronized_sessions.add(id(planner))
                    wait = True
            if wait:
                self.gate.wait()
        center = self.cities[city]["center"]
        return [dict(
            id=f"{city}-meal-{i}",
            name=(city + "汤包米粉早餐店" + str(i)) if i < 5 else self.cities[city]["restaurant"] + str(i),
            address=city + "离线餐厅地址", longitude=center[0]+i*0.0001,
            latitude=center[1], type="餐饮服务;中餐厅;小吃快餐店", typecode="050100",
            tag=" ".join(dish for values in self.cities[city]["dishes"].values() for dish in values),
            opening_hours="06:00-23:00", rating=4.6, rating_available=True,
            distance_m=100+i, estimated_cost=30,
        ) for i in range(15)]

    def get_route_summary(self, origin, destination, mode, city=""):
        a = tuple(map(float, origin.split(",")))
        b = tuple(map(float, destination.split(",")))
        distance = math.hypot((a[0]-b[0])*95, (a[1]-b[1])*111)
        return dict(duration_minutes=max(2, math.ceil(distance / {"walking": 4.5, "bicycling": 15, "transit": 25, "driving": 35}[mode] * 60)), distance_km=round(distance, 2), source="amap")

    def geocode_administrative_location(self, city):
        return tuple(self.cities[city]["center"]) if city in self.cities else None

    geocode_location = geocode_administrative_location

    def search_poi(self, keywords, city="", **kwargs):
        self.record("poi", city)
        if city in self.cities and ("站" in keywords or "火车" in keywords):
            lng, lat = self.cities[city]["center"]
            return [dict(id=city+"-station", name=city+"站", lng=lng+0.005, lat=lat, type="交通设施服务;火车站")]
        return []

    def search_transport_hubs(self, city, transport):
        lng, lat = self.cities[city]["center"]
        return [dict(id=city+"-station", poi_id=city+"-station", name=city+"站",
                     longitude=lng+0.005, latitude=lat, address=city+"离线车站地址")]

    def get_opening_hours(self, name, city):
        for values in self.cities.values():
            for item in values["attractions"]:
                if item["name"] == name:
                    return item["hours"]
        raise AssertionError("Unexpected attraction: " + name)

    def ticket(self, name, city):
        self.record("ticket", city)
        return TicketInfo(price=20, opening_hours=self.get_opening_hours(name, city), source="amap", confidence="high")

    def get_intercity_transit_summary(self, origin, destination, origin_city, destination_city, day, departure):
        self.record("rail", origin_city + "→" + destination_city)
        a, b = self.cities[origin_city]["center"], self.cities[destination_city]["center"]
        return dict(duration_minutes=170, distance_km=220, source="amap", railway=dict(
            departure_hub=dict(name=origin_city+"站", longitude=a[0]+0.005, latitude=a[1]),
            arrival_hub=dict(name=destination_city+"站", longitude=b[0]+0.005, latitude=b[1]),
            duration_minutes=90, distance_km=220, type="2012", trip="D离线测试", source="amap",
        ))

    def get_forecast(self, city, start_date, end_date):
        self.record("weather", city)
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        return [WeatherInfo(date=(start+timedelta(days=i)).isoformat(), location_name=city,
                            day_weather="晴", night_weather="晴", day_temp=25, night_temp=18)
                for i in range((end-start).days+1)]

    def invoke(self, messages):
        text = messages[-1].content
        system = messages[0].content if len(messages) > 1 else ""
        if "请估算以下酒店" in text:
            return AIMessage(content="220")
        if system == planner_module.SCHEDULE_AGENT_PROMPT:
            city = re.search(r"请为(.+?)的\d+天行程", text).group(1)
            self.record("model-schedule", city)
            ids = {match.group(2).split(" [", 1)[0].strip(): match.group(1)
                   for match in re.finditer(r"^\s+(C\d+) \| (.+?) \|", text, re.M)}
            data = dict(days=[dict(day_index=i, items=[ids[name] for name in names])
                              for i, names in enumerate(self.schedule[city])])
            return AIMessage(content=json.dumps(data, ensure_ascii=False))
        if "地方饮食知识助手" in system:
            city = re.search(r"请为(.+?)生成当地", text).group(1)
            return AIMessage(content=json.dumps(self.cities[city]["dishes"], ensure_ascii=False))
        if "从真实高德候选中选择适合的餐饮" in text:
            city = re.search(r"请为以下(.+?)\d+天旅行计划", text).group(1)
            self.record("model-meals", city)
            # Fixed empty model selection exercises production candidate fallback.
            return AIMessage(content='{"days":[]}')
        raise AssertionError("Unexpected model prompt: " + str(text)[:120])

    def bind(self, **kwargs):
        return self

    def agent_invoke(self, payload):
        return {"messages": [AIMessage(content="离线数据下按开放时间及交通条件安排，出发前核验预约。") ]}

    def runtime(self):
        agent = SimpleNamespace(invoke=self.agent_invoke)
        return planner_module._PlannerRuntime(
            llm=self, fallback_llm=None, weather_service=self,
            attraction_agent=agent, weather_agent=agent, critic_agent=agent,
        )
