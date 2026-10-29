"""确定性的天气数据服务：Open-Meteo 为主，高德为备用。"""

from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlencode

from ..config import get_settings
from ..models.schemas import WeatherInfo
from .http_client import ExternalRequestError, request_json
from .logger import logger


class WeatherService:
    """获取真实预报数据；不让 LLM 参与数值天气数据的生成。"""

    _WEATHER_TEXT = {
        0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
        45: "雾", 48: "雾凇", 51: "毛毛雨", 53: "毛毛雨",
        55: "强毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨",
        71: "小雪", 73: "中雪", 75: "大雪", 80: "阵雨",
        81: "强阵雨", 82: "暴雨", 95: "雷暴", 96: "雷暴伴冰雹",
        99: "强雷暴伴冰雹",
    }
    _DIRECTIONS = ("北风", "东北风", "东风", "东南风", "南风", "西南风", "西风", "西北风")

    @staticmethod
    def _get_json(url: str, timeout: int = 10) -> dict:
        return request_json(url, timeout=timeout)

    @classmethod
    def _weather_text(cls, code: object) -> str:
        try:
            return cls._WEATHER_TEXT.get(int(code), "未知")
        except (TypeError, ValueError):
            return "未知"

    @classmethod
    def _wind_direction(cls, degrees: object) -> str:
        try:
            return cls._DIRECTIONS[int((float(degrees) + 22.5) % 360 // 45)]
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _wind_power(speed: object) -> str:
        try:
            return f"{round(float(speed))} km/h"
        except (TypeError, ValueError):
            return ""

    def _geocode_open_meteo(self, city: str) -> Optional[tuple[float, float]]:
        params = urlencode({"name": city, "count": 1, "language": "zh", "format": "json"})
        data = self._get_json(f"https://geocoding-api.open-meteo.com/v1/search?{params}")
        results = data.get("results") or []
        if not results:
            return None
        first = results[0]
        return float(first["latitude"]), float(first["longitude"])

    def _fetch_open_meteo(self, city: str, start_date: str, end_date: str) -> list[WeatherInfo]:
        coordinates = self._geocode_open_meteo(city)
        if not coordinates:
            raise ValueError(f"Open-Meteo 未找到城市: {city}")

        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        today = date.today()
        if end < today or start > today + timedelta(days=15):
            logger.info("行程日期不在 Open-Meteo 16 天预报范围内")
            return []

        forecast_days = min(16, max(1, (end - today).days + 1))
        latitude, longitude = coordinates
        params = urlencode({
            "latitude": latitude,
            "longitude": longitude,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,wind_speed_10m_max,wind_direction_10m_dominant",
            "timezone": "Asia/Shanghai",
            "forecast_days": forecast_days,
        })
        data = self._get_json(f"https://api.open-meteo.com/v1/forecast?{params}")
        daily = data.get("daily") or {}
        values = zip(
            daily.get("time", []), daily.get("weather_code", []),
            daily.get("temperature_2m_max", []), daily.get("temperature_2m_min", []),
            daily.get("wind_direction_10m_dominant", []), daily.get("wind_speed_10m_max", []),
        )
        forecasts = []
        for forecast_date, code, max_temp, min_temp, direction, speed in values:
            if start_date <= forecast_date <= end_date:
                weather = self._weather_text(code)
                forecasts.append(WeatherInfo(
                    date=forecast_date,
                    day_weather=weather,
                    night_weather=weather,
                    day_temp=round(float(max_temp)),
                    night_temp=round(float(min_temp)),
                    wind_direction=self._wind_direction(direction),
                    wind_power=self._wind_power(speed),
                ))
        return forecasts

    def _fetch_amap(self, city: str, start_date: str, end_date: str) -> list[WeatherInfo]:
        """Open-Meteo 不可用时才消耗高德天气配额。"""
        settings = get_settings()
        if not settings.amap_api_key:
            return []
        params = urlencode({"key": settings.amap_api_key, "city": city, "extensions": "all", "output": "json"})
        data = self._get_json(f"https://restapi.amap.com/v3/weather/weatherInfo?{params}")
        forecasts = []
        for item in (data.get("forecasts") or []):
            for cast in item.get("casts") or []:
                forecast_date = cast.get("date", "")
                if start_date <= forecast_date <= end_date:
                    forecasts.append(WeatherInfo(
                        date=forecast_date,
                        day_weather=cast.get("dayweather", ""),
                        night_weather=cast.get("nightweather", ""),
                        day_temp=cast.get("daytemp", 0),
                        night_temp=cast.get("nighttemp", 0),
                        wind_direction=cast.get("daywind", ""),
                        wind_power=cast.get("daypower", ""),
                    ))
        return forecasts

    def get_forecast(self, city: str, start_date: str, end_date: str) -> list[WeatherInfo]:
        """优先使用免费 Open-Meteo，失败时自动回退到高德。"""
        try:
            forecasts = self._fetch_open_meteo(city, start_date, end_date)
            if forecasts:
                logger.info(f"天气查询: Open-Meteo {city} → {len(forecasts)} 天")
                return forecasts
        except (ExternalRequestError, ValueError, KeyError, TypeError) as error:
            logger.warning(f"Open-Meteo 天气查询失败 ({city}): {error}")

        try:
            forecasts = self._fetch_amap(city, start_date, end_date)
            logger.info(f"天气查询: 高德备用 {city} → {len(forecasts)} 天")
            return forecasts
        except (ExternalRequestError, ValueError, KeyError, TypeError) as error:
            logger.warning(f"高德备用天气查询失败 ({city}): {error}")
            return []


_weather_service: Optional[WeatherService] = None


def get_weather_service() -> WeatherService:
    global _weather_service
    if _weather_service is None:
        _weather_service = WeatherService()
    return _weather_service
