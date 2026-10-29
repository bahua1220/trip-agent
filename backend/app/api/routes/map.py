"""地图服务API路由"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import date, timedelta

from ...models.schemas import WeatherResponse

from ...services.amap_service import get_amap_service
from ...services.weather_service import get_weather_service

router = APIRouter(prefix="/map", tags=["地图服务"])


@router.get(
    "/weather",
    response_model=WeatherResponse,
    summary="查询天气",
    description="查询指定城市的天气信息"
)
async def get_weather(
    city: str = Query(..., description="城市名称", examples=["北京"]),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
):
    """
    查询天气
    
    Args:
        city: 城市名称
        
    Returns:
        天气信息
    """
    try:
        today = date.today()
        start_date = start_date or today.isoformat()
        end_date = end_date or (today + timedelta(days=6)).isoformat()
        weather_info = get_weather_service().get_forecast(city, start_date, end_date)
        
        return WeatherResponse(
            success=True,
            message="天气查询成功" if weather_info else "暂无可用预报",
            data=weather_info
        )
        
    except Exception as e:
        print(f"❌ 天气查询失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"天气查询失败: {str(e)}"
        )


@router.get(
    "/driving-route",
    summary="获取驾车导航路径",
    description="根据起终点坐标获取驾车导航路径的polyline坐标数组"
)
async def get_driving_route(
    origin: str = Query(..., description="起点坐标 lng,lat"),
    destination: str = Query(..., description="终点坐标 lng,lat"),
):
    """获取驾车导航路径"""
    try:
        service = get_amap_service()
        path = service.get_driving_route(origin, destination)

        if path is None:
            return {
                "success": False,
                "message": "导航路径获取失败",
                "data": None
            }

        return {
            "success": True,
            "message": "获取成功",
            "data": {
                "path": path,
                "point_count": len(path),
            }
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"获取导航路径失败: {str(e)}"
        )


@router.get(
    "/health",
    summary="健康检查",
    description="检查地图服务是否正常"
)
async def health_check():
    """服务存活检查；不调用第三方地图服务，也不消耗配额。"""
    return {
        "status": "healthy",
        "service": "map-service",
        "capabilities": ["weather", "driving-route"],
    }

