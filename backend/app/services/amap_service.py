"""高德地图服务封装 — 基于 REST API（LangGraph 版本）"""

from functools import lru_cache
import re
import time
from typing import Optional, Dict, Any
from urllib.parse import urlencode

from ..config import get_settings
from ..services.logger import logger
from .http_client import request_json


class AmapService:
    """高德地图 REST API 服务"""

    @staticmethod
    def _rail_clock(value: Any) -> Optional[str]:
        """Normalize AMap railway stop clocks such as ``0935`` or ``09:35``."""
        text = str(value or "").strip()
        if not text:
            return None
        if ":" in text:
            try:
                hour, minute = (int(part) for part in text.split(":", 1))
            except (TypeError, ValueError):
                return None
        else:
            digits = "".join(character for character in text if character.isdigit())
            if not digits or len(digits) > 4:
                return None
            digits = digits.zfill(4)
            hour, minute = int(digits[:-2]), int(digits[-2:])
        if hour < 0 or minute < 0 or minute >= 60:
            return None
        return f"{hour:02d}:{minute:02d}"

    @staticmethod
    def _clean_opening_hours(value: Any) -> str:
        """清理高德偶发的重复/嵌套时段，避免把停止售票时间显示成开放时段。"""
        text = str(value or "").strip()
        matches = [
            (int(hour) * 60 + int(minute), int(end_hour) * 60 + int(end_minute))
            for hour, minute, end_hour, end_minute in re.findall(
                r"(\d{1,2}):(\d{2})\s*[-~～—至]\s*(\d{1,2}):(\d{2})",
                text,
            )
            if int(hour) <= 24 and int(end_hour) <= 24
        ]
        nested = any(
            outer_start <= inner_start and inner_end <= outer_end
            and (outer_start, outer_end) != (inner_start, inner_end)
            for outer_start, outer_end in matches
            for inner_start, inner_end in matches
        )
        suspicious_nested = nested and any(
            marker in text for marker in ("停止售票", "停止入场", "停止检票")
        )
        if not (text.startswith("初周") or suspicious_nested):
            return text
        widest = [
            interval for interval in matches
            if not any(
                other[0] <= interval[0] and interval[1] <= other[1]
                and interval != other
                for other in matches
            )
        ]
        unique = []
        for interval in widest:
            if interval not in unique:
                unique.append(interval)
        return ",".join(
            f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"
            for start, end in unique
        ) or text

    @staticmethod
    def _rail_location(value: Any) -> Optional[dict]:
        text = str(value or "").strip()
        if "," not in text:
            return None
        try:
            longitude, latitude = (
                float(part) for part in text.split(",", 1)
            )
        except (TypeError, ValueError):
            return None
        if not longitude or not latitude:
            return None
        return {"longitude": longitude, "latitude": latitude}

    @classmethod
    def _extract_railway_summary(cls, transit: dict) -> Optional[dict]:
        """Extract the actual railway portion from an integrated transit result."""
        railways = []
        for segment in transit.get("segments", []) or []:
            railway = segment.get("railway") if isinstance(segment, dict) else None
            if isinstance(railway, dict) and railway:
                railways.append(railway)
            elif isinstance(railway, list):
                railways.extend(item for item in railway if isinstance(item, dict) and item)
        if not railways:
            return None

        first, last = railways[0], railways[-1]
        departure = first.get("departure_stop") or {}
        arrival = last.get("arrival_stop") or {}
        if not isinstance(departure, dict) or not isinstance(arrival, dict):
            return None
        departure_name = str(departure.get("name", "") or "").strip()
        arrival_name = str(arrival.get("name", "") or "").strip()
        departure_location = cls._rail_location(departure.get("location"))
        arrival_location = cls._rail_location(arrival.get("location"))
        if not departure_name or not arrival_name or not departure_location or not arrival_location:
            return None

        departure_time = cls._rail_clock(departure.get("time"))
        arrival_time = cls._rail_clock(arrival.get("time"))
        duration_minutes = 0
        if departure_time and arrival_time:
            departure_minutes = (
                int(departure_time.split(":", 1)[0]) * 60
                + int(departure_time.split(":", 1)[1])
            )
            arrival_minutes = (
                int(arrival_time.split(":", 1)[0]) * 60
                + int(arrival_time.split(":", 1)[1])
            )
            if arrival_minutes < departure_minutes:
                arrival_minutes += 24 * 60
            duration_minutes = arrival_minutes - departure_minutes
        if duration_minutes <= 0:
            for railway in railways:
                try:
                    # AMap documents railway.time as the railway leg duration.
                    duration_minutes += max(
                        0, round(float(railway.get("time", 0) or 0) / 60)
                    )
                except (TypeError, ValueError):
                    continue
        if duration_minutes <= 0:
            return None

        trips = [
            str(item.get("trip", "") or "").strip()
            for item in railways
            if str(item.get("trip", "") or "").strip()
        ]
        types = [
            str(item.get("type", "") or "").strip()
            for item in railways
            if str(item.get("type", "") or "").strip()
        ]
        distance_m = 0.0
        for railway in railways:
            try:
                distance_m += float(railway.get("distance", 0) or 0)
            except (TypeError, ValueError):
                continue
        return {
            "departure_hub": {
                "name": departure_name,
                **departure_location,
            },
            "arrival_hub": {
                "name": arrival_name,
                **arrival_location,
            },
            "departure_time": departure_time,
            "arrival_time": arrival_time,
            "duration_minutes": duration_minutes,
            "distance_km": round(distance_m / 1000, 1) if distance_m > 0 else None,
            "trip": "/".join(dict.fromkeys(trips)),
            "type": types[0] if len(set(types)) == 1 and types else "",
            "source": "amap",
        }

    def _get(self, endpoint: str, params: dict, version: str = "v3") -> dict:
        """通用 GET 请求"""
        settings = get_settings()
        params["key"] = settings.amap_api_key
        params["output"] = "json"
        url = f"https://restapi.amap.com/{version}/{endpoint}?{urlencode(params)}"
        return request_json(url)

    def search_poi(self, keywords: str, city: str = "北京") -> list:
        """
        高德 POI 关键字搜索（文本搜索）

        Args:
            keywords: 搜索关键词
            city: 城市名称

        Returns:
            POI 列表，每项含 name, address, location(lng/lat), typecode
        """
        try:
            data = self._get("place/text", {
                "keywords": keywords,
                "city": city,
                "offset": "10",
            })
            pois = data.get("pois", [])
            result = []
            for poi in pois:
                loc_str = poi.get("location", "")
                lng, lat = 0.0, 0.0
                if "," in loc_str:
                    try:
                        parts = loc_str.split(",")
                        lng, lat = float(parts[0]), float(parts[1])
                    except ValueError:
                        pass
                result.append({
                    "id": poi.get("id", ""),
                    "parent": poi.get("parent", ""),
                    "childtype": poi.get("childtype", ""),
                    "name": poi.get("name", ""),
                    "address": poi.get("address", ""),
                    "typecode": poi.get("typecode", ""),
                    "type": poi.get("type", ""),
                    "lng": lng,
                    "lat": lat,
                })
            logger.info(f"POI搜索: '{keywords}' @ {city} → {len(result)} 条")
            return result
        except Exception as e:
            logger.warning(f"POI搜索失败 ({keywords}): {e}")
            return []

    @lru_cache(maxsize=128)
    def search_transport_hubs(self, city: str, transport: str) -> list:
        """返回城市内可供用户选择的真实车站或机场。"""
        mode = str(transport or "").strip()
        is_airport = "飞机" in mode
        keywords = "机场" if is_airport else "火车站"
        try:
            data = self._get("place/text", {
                "keywords": keywords,
                "city": city,
                "citylimit": "true",
                "offset": "20",
                "extensions": "base",
            })
            result = []
            seen = set()
            for poi in data.get("pois", []):
                name = str(poi.get("name", "") or "").strip()
                poi_type = str(poi.get("type", "") or "")
                if not name or name in seen:
                    continue
                if is_airport:
                    if "机场" not in name and "飞机场" not in poi_type:
                        continue
                else:
                    if any(word in name for word in ("地铁站", "公交站", "收费站")):
                        continue
                    if "火车站" not in poi_type and not name.endswith(("站", "站房")):
                        continue
                location = str(poi.get("location", "") or "")
                if "," not in location:
                    continue
                try:
                    longitude, latitude = (float(value) for value in location.split(",", 1))
                except (TypeError, ValueError):
                    continue
                seen.add(name)
                result.append({
                    "id": str(poi.get("id", "") or ""),
                    "name": name,
                    "address": poi.get("address", "") or "",
                    "type": poi_type,
                    "longitude": longitude,
                    "latitude": latitude,
                })
            result.sort(key=lambda item: (
                0 if item["name"].startswith(city.rstrip("市")) else 1,
                len(item["name"]),
                item["name"],
            ))
            logger.info(f"交通枢纽候选: {city} {mode} → {len(result)} 条")
            return result
        except Exception as error:
            logger.warning(f"交通枢纽搜索失败 ({city} {mode}): {error}")
            return []

    @lru_cache(maxsize=256)
    def geocode_administrative_location(
        self, name: str
    ) -> Optional[tuple[float, float]]:
        """优先按行政区解析城市名，避免命中同名酒店、商场等 POI。"""
        keyword = str(name or "").strip()
        if not keyword:
            return None
        try:
            data = self._get("config/district", {
                "keywords": keyword,
                "subdistrict": "0",
                "extensions": "base",
            })
            districts = list(data.get("districts", []) or [])
            # 少数关键词会返回上级行政区并把真正命中项放在 children 中。
            pending = list(districts)
            while pending:
                district = pending.pop(0)
                pending.extend(
                    item for item in (district.get("districts", []) or [])
                    if isinstance(item, dict)
                )
                district_name = str(district.get("name") or "").strip()
                # 行政区接口存在模糊匹配；只有返回名称确实包含原关键词时才采用。
                if keyword not in district_name and district_name not in keyword:
                    continue
                center = str(district.get("center") or "")
                if "," not in center:
                    continue
                longitude, latitude = center.split(",", 1)
                return float(longitude), float(latitude)

            # district 对部分县级市可能不给结果，继续使用地理编码接口，
            # 但只接受行政区层级且格式化地址明确包含原关键词的候选。
            address = keyword if keyword.endswith(("省", "市", "州", "县", "区", "盟")) else f"{keyword}市"
            geocode_data = self._get("geocode/geo", {"address": address})
            for geocode in geocode_data.get("geocodes", []) or []:
                formatted = str(geocode.get("formatted_address") or "")
                level = str(geocode.get("level") or "")
                location = str(geocode.get("location") or "")
                if (
                    keyword not in formatted
                    or level not in {"省", "市", "区县", "乡镇"}
                    or "," not in location
                ):
                    continue
                longitude, latitude = location.split(",", 1)
                return float(longitude), float(latitude)
            return None
        except (TypeError, ValueError) as error:
            logger.warning(f"行政区坐标解析失败 ({keyword}): {error}")
            return None
        except Exception as error:
            logger.warning(f"行政区坐标查询失败 ({keyword}): {error}")
            return None

    @lru_cache(maxsize=256)
    def geocode_location(self, name: str) -> Optional[tuple[float, float]]:
        """将城市、景区或住宿节点转换为经纬度，并缓存结果。"""
        try:
            data = self._get("place/text", {
                "keywords": name,
                "offset": "1",
            })
            pois = data.get("pois", [])
            if not pois:
                return None
            location = pois[0].get("location", "")
            if "," not in location:
                return None
            longitude, latitude = location.split(",", 1)
            return float(longitude), float(latitude)
        except (TypeError, ValueError) as error:
            logger.warning(f"地点坐标解析失败 ({name}): {error}")
            return None
        except Exception as error:
            logger.warning(f"地点坐标查询失败 ({name}): {error}")
            return None

    def search_poi_detail(self, keywords: str, city: str = "北京") -> Optional[dict]:
        """
        高德 POI 详情搜索（extensions=all），用于编辑模式下查找酒店/餐厅的评分、人均等

        Args:
            keywords: 搜索关键词
            city: 城市名称

        Returns:
            单条 POI 详情 dict，含 name/address/lng/lat/typecode/type/rating/cost/opentime
            未找到返回 None
        """
        try:
            data = self._get("place/text", {
                "keywords": keywords,
                "city": city,
                "extensions": "all",
                "offset": "1",
            })
            pois = data.get("pois", [])
            if not pois:
                return None

            poi = pois[0]
            loc_str = poi.get("location", "")
            lng, lat = 0.0, 0.0
            if "," in loc_str:
                try:
                    parts = loc_str.split(",")
                    lng, lat = float(parts[0]), float(parts[1])
                except ValueError:
                    pass

            biz_ext = poi.get("biz_ext", {})
            if not isinstance(biz_ext, dict):
                biz_ext = {}

            # 安全提取字符串值（高德 biz_ext 可能返回空列表 []）
            def _safe_str(v):
                if isinstance(v, str):
                    return v
                return ""

            result = {
                "id": poi.get("id", ""),
                "parent": poi.get("parent", ""),
                "childtype": poi.get("childtype", ""),
                "name": poi.get("name", ""),
                "address": poi.get("address", ""),
                "typecode": poi.get("typecode", ""),
                "type": poi.get("type", ""),
                "lng": lng,
                "lat": lat,
                "rating": _safe_str(biz_ext.get("rating", "")),
                "cost": _safe_str(biz_ext.get("cost", "")),
                "opentime": _safe_str(biz_ext.get("opentime2", "")),
            }
            logger.info(f"POI详情: '{keywords}' @ {city} → {result['name']} rating={result['rating']} cost={result['cost']}")
            return result
        except Exception as e:
            logger.warning(f"POI详情搜索失败 ({keywords}): {e}")
            return None

    def get_driving_route(self, origin: str, destination: str) -> Optional[list]:
        """
        获取驾车导航路径（polyline坐标数组）

        Args:
            origin: 起点坐标 "lng,lat"
            destination: 终点坐标 "lng,lat"

        Returns:
            [[lng, lat], ...] 导航路径坐标数组，失败返回 None
        """
        try:
            data = self._get("direction/driving", {
                "origin": origin,
                "destination": destination,
                "extensions": "all",
                "strategy": "0",  # 速度优先
            })
            if data.get("status") != "1":
                logger.warning(f"驾车导航失败: {origin} → {destination} status={data.get('status')}")
                return None

            route = data.get("route", {})
            paths = route.get("paths", [])
            if not paths:
                return None

            # 取第一条路径，拼接所有 step 的 polyline
            all_coords: list = []
            for step in paths[0].get("steps", []):
                polyline = step.get("polyline", "")
                if not polyline:
                    continue
                # polyline 格式: "lng,lat;lng,lat;..."
                for pt in polyline.split(";"):
                    parts = pt.split(",")
                    if len(parts) == 2:
                        try:
                            all_coords.append([float(parts[0]), float(parts[1])])
                        except ValueError:
                            continue

            dist = paths[0].get("distance", "?")
            dura = paths[0].get("duration", "?")
            logger.info(
                f"驾车导航: {origin} → {destination} | "
                f"距离={dist}m 时间={dura}s | 坐标点={len(all_coords)}"
            )
            return all_coords if all_coords else None

        except Exception as e:
            logger.warning(f"驾车导航异常 ({origin}→{destination}): {e}")
            return None

    @lru_cache(maxsize=512)
    def get_driving_route_summary(
        self, origin: str, destination: str
    ) -> Optional[dict]:
        """返回跨城路线的距离和预计驾驶时间，不下载完整轨迹。"""
        try:
            data = self._get("direction/driving", {
                "origin": origin,
                "destination": destination,
                "extensions": "base",
                "strategy": "0",
            })
            if data.get("status") != "1":
                return None
            paths = data.get("route", {}).get("paths", [])
            if not paths:
                return None
            path = paths[0]
            distance_m = float(path.get("distance", 0) or 0)
            duration_seconds = float(path.get("duration", 0) or 0)
            if distance_m <= 0 or duration_seconds <= 0:
                return None
            return {
                "distance_km": round(distance_m / 1000, 1),
                "duration_minutes": max(1, round(duration_seconds / 60)),
                "source": "amap",
            }
        except Exception as e:
            logger.warning(f"驾车路线摘要异常 ({origin}→{destination}): {e}")
            return None

    @lru_cache(maxsize=512)
    def get_intercity_transit_summary(
        self,
        origin: str,
        destination: str,
        origin_city: str,
        destination_city: str,
        departure_date: str = "",
        departure_time: str = "",
    ) -> Optional[dict]:
        """返回跨城市公共交通门到门参考耗时。

        高德跨城公交会综合铁路、公交和地铁。这里仅将其用于行程时间占位，
        不把结果当作 12306 车次、余票或购票信息。
        """
        if not origin_city or not destination_city:
            return None
        params = {
            "origin": origin,
            "destination": destination,
            "city": origin_city,
            "cityd": destination_city,
            "strategy": "0",
            "nightflag": "0",
            # Railway station, train and stop-time fields are required to split
            # the previous door-to-door total into understandable timeline legs.
            "extensions": "all",
        }
        if departure_date:
            params["date"] = departure_date
        if departure_time:
            params["time"] = departure_time
        try:
            data = self._get("direction/transit/integrated", params)
            if data.get("status") != "1":
                logger.warning(
                    f"跨城公交查询失败: {origin_city}→{destination_city} "
                    f"{departure_date} {departure_time} | {data.get('info', '')}"
                )
                return None
            transits = data.get("route", {}).get("transits", [])
            if not transits:
                return None
            usable = []
            for transit in transits:
                try:
                    duration_seconds = float(transit.get("duration", 0) or 0)
                    distance_m = float(transit.get("distance", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if duration_seconds > 0:
                    usable.append((duration_seconds, distance_m, transit))
            if not usable:
                return None
            duration_seconds, distance_m, selected_transit = min(
                usable, key=lambda item: item[0]
            )
            summary = {
                "duration_minutes": max(1, round(duration_seconds / 60)),
                "distance_km": round(distance_m / 1000, 1) if distance_m > 0 else None,
                "source": "amap",
                "railway": self._extract_railway_summary(selected_transit),
            }
            logger.info(
                f"跨城公交: {origin_city}→{destination_city} "
                f"{departure_date} {departure_time} | "
                f"约{summary['duration_minutes']}分钟"
            )
            return summary
        except (TypeError, ValueError) as error:
            logger.warning(
                f"跨城公交数据解析失败 ({origin_city}→{destination_city}): {error}"
            )
            return None
        except Exception as error:
            logger.warning(
                f"跨城公交查询异常 ({origin_city}→{destination_city}): {error}"
            )
            return None

    @lru_cache(maxsize=2048)
    def get_route_summary(
        self,
        origin: str,
        destination: str,
        mode: str = "driving",
        city: str = "",
    ) -> Optional[dict]:
        """返回市内两点间的真实规划距离和用时。

        mode 支持 driving、walking、bicycling、transit。调用结果按坐标、方式和城市
        缓存在进程内，避免日程换位计算重复消耗高德额度。
        """
        normalized_mode = mode if mode in {"driving", "walking", "bicycling", "transit"} else "driving"
        try:
            params = {
                "origin": origin,
                "destination": destination,
            }
            if normalized_mode == "driving":
                endpoint = "direction/driving"
                params.update({"extensions": "base", "strategy": "0"})
            elif normalized_mode == "walking":
                endpoint = "direction/walking"
            elif normalized_mode == "bicycling":
                endpoint = "direction/bicycling"
            else:
                if not city:
                    return None
                endpoint = "direction/transit/integrated"
                params.update({
                    "city": city,
                    "cityd": city,
                    "strategy": "0",
                    "nightflag": "0",
                })

            api_version = "v4" if normalized_mode == "bicycling" else "v3"
            data = (
                self._get(endpoint, params, api_version)
                if normalized_mode == "bicycling"
                else self._get(endpoint, params)
            )
            # 路线组合评估可能在短时间触发高德 QPS 门槛。只对明确的
            # 瞬时限流做一次短退避重试，避免把这次临时失败缓存成 None。
            info = str(data.get("info", "") or "")
            if "CUQPS_HAS_EXCEEDED_THE_LIMIT" in info:
                time.sleep(0.35)
                data = (
                    self._get(endpoint, params, api_version)
                    if normalized_mode == "bicycling"
                    else self._get(endpoint, params)
                )
            succeeded = (
                str(data.get("errcode")) == "0"
                if normalized_mode == "bicycling"
                else data.get("status") == "1"
            )
            if not succeeded:
                logger.warning(
                    f"市内路线查询失败: mode={normalized_mode} "
                    f"{origin} → {destination} | {data.get('info', '')}"
                )
                return None

            route = data.get("data", {}) if normalized_mode == "bicycling" else data.get("route", {})
            if normalized_mode == "transit":
                choices = route.get("transits", [])
            else:
                choices = route.get("paths", [])
            if not choices:
                return None

            choice = choices[0]
            duration_seconds = float(choice.get("duration", 0) or 0)
            distance_m = float(
                choice.get("distance", 0)
                or route.get("distance", 0)
                or 0
            )
            if duration_seconds <= 0:
                return None

            summary = {
                "distance_km": round(distance_m / 1000, 1) if distance_m > 0 else None,
                "duration_minutes": max(1, round(duration_seconds / 60)),
                "mode": normalized_mode,
                "source": "amap",
            }
            logger.info(
                f"市内路线: mode={normalized_mode} {origin} → {destination} | "
                f"距离={summary['distance_km']}km 时间={summary['duration_minutes']}分钟"
            )
            return summary
        except (TypeError, ValueError) as error:
            logger.warning(f"市内路线数据解析失败 ({origin}→{destination}): {error}")
            return None
        except Exception as error:
            logger.warning(f"市内路线查询异常 ({origin}→{destination}): {error}")
            return None

    def get_opening_hours(self, attraction_name: str, city: str = "") -> str:
        """
        通过高德 REST API 查询景点开放时间（biz_ext.opentime2）

        Args:
            attraction_name: 景点名称
            city: 城市名称

        Returns:
            开放时间字符串，如 "08:30-17:00(周一闭馆)"
        """
        try:
            data = self._get("place/text", {
                "keywords": attraction_name,
                "city": city,
                "extensions": "all",
            })
            pois = data.get("pois", [])

            if pois:
                biz_ext = pois[0].get("biz_ext", {})
                if isinstance(biz_ext, dict):
                    opentime = biz_ext.get("opentime2", "")
                    if opentime:
                        return self._clean_opening_hours(opentime)
            return ""
        except Exception as e:
            logger.warning(f"获取开放时间失败 ({attraction_name}): {e}")
            return ""


# 全局实例
_amap_service: Optional[AmapService] = None


def get_amap_service() -> AmapService:
    """获取高德地图服务实例（单例）"""
    global _amap_service
    if _amap_service is None:
        _amap_service = AmapService()
        logger.debug("AmapService 初始化完成")
    return _amap_service
