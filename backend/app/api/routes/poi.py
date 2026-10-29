"""POI相关API路由"""

from fastapi import APIRouter, HTTPException
from ...services.amap_service import get_amap_service
from ...services.unsplash_service import get_unsplash_service

router = APIRouter(prefix="/poi", tags=["POI"])


_ATTRACTION_TYPE_PREFIXES = ("11", "14")
_ATTRACTION_EXCLUDED_WORDS = (
    "停车场", "售票处", "游客中心", "出入口", "入口", "出口",
    "公交站", "地铁站", "服务区", "卫生间", "便利店", "餐厅", "酒店",
)


def _is_attraction_result(item: dict) -> bool:
    name = str(item.get("name", "") or "")
    typecode = str(item.get("typecode", "") or "")
    return (
        bool(name)
        and bool(item.get("lng"))
        and bool(item.get("lat"))
        and typecode.startswith(_ATTRACTION_TYPE_PREFIXES)
        and not any(word in name for word in _ATTRACTION_EXCLUDED_WORDS)
    )


@router.get(
    "/attraction-suggestions",
    summary="获取编辑行程时的景点候选",
    description="返回真实高德景点，过滤停车场、入口、餐厅等非游览 POI",
)
async def attraction_suggestions(city: str, keywords: str = ""):
    city = city.strip()
    if not city:
        raise HTTPException(status_code=422, detail="请先选择目的地城市")
    queries = [keywords.strip()] if keywords.strip() else ["热门景点", "风景名胜", "博物馆"]
    amap_service = get_amap_service()
    results = []
    seen = set()
    for query in queries:
        for item in amap_service.search_poi(query, city):
            if not _is_attraction_result(item):
                continue
            key = item.get("id") or item.get("name")
            if key in seen:
                continue
            seen.add(key)
            normalized = dict(item)
            normalized["recommendation_reason"] = (
                "与你搜索的名称匹配" if keywords.strip() else "城市内可加入的真实景点"
            )
            results.append(normalized)
            if len(results) >= 12:
                break
        if len(results) >= 12:
            break
    return {
        "success": True,
        "message": "搜索成功" if results else "暂未找到可加入的景点",
        "data": results,
    }


@router.get(
    "/transport-hubs",
    summary="获取城市交通枢纽选项",
    description="根据城市和飞机/火车方式返回真实高德 POI",
)
async def transport_hubs(city: str, transport: str):
    city = city.strip()
    transport = transport.strip()
    if not city:
        raise HTTPException(status_code=422, detail="请先选择目的地城市")
    if transport not in {"高铁", "火车", "飞机"}:
        raise HTTPException(status_code=422, detail="当前交通方式无需选择车站或机场")
    result = get_amap_service().search_transport_hubs(city, transport)
    return {
        "success": True,
        "message": "搜索成功" if result else "未找到可选交通枢纽",
        "data": result,
    }


@router.get(
    "/search",
    summary="搜索POI",
    description="根据关键词搜索POI"
)
async def search_poi(keywords: str, city: str = "北京"):
    """
    搜索POI

    Args:
        keywords: 搜索关键词
        city: 城市名称

    Returns:
        搜索结果
    """
    try:
        amap_service = get_amap_service()
        result = amap_service.search_poi(keywords, city)

        return {
            "success": True,
            "message": "搜索成功",
            "data": result
        }

    except Exception as e:
        print(f"❌ 搜索POI失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"搜索POI失败: {str(e)}"
        )


@router.get(
    "/photo",
    summary="获取景点图片",
    description="根据景点名称从Unsplash获取图片"
)
async def get_attraction_photo(name: str):
    """
    获取景点图片

    Args:
        name: 景点名称

    Returns:
        图片URL
    """
    try:
        unsplash_service = get_unsplash_service()

        # 搜索景点图片
        photo_url = unsplash_service.get_photo_url(f"{name} China landmark")

        if not photo_url:
            # 如果没找到,尝试只用景点名称搜索
            photo_url = unsplash_service.get_photo_url(name)

        return {
            "success": True,
            "message": "获取图片成功",
            "data": {
                "name": name,
                "photo_url": photo_url
            }
        }

    except Exception as e:
        print(f"❌ 获取景点图片失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取景点图片失败: {str(e)}"
        )


@router.get(
    "/search-detail",
    summary="搜索POI详情（含评分、人均）",
    description="根据名称搜索酒店/餐厅的详细信息，用于编辑模式自动补全"
)
async def search_poi_detail(keywords: str, city: str = "北京"):
    """搜索POI详情（extensions=all，包含评分、人均消费等）"""
    try:
        amap_service = get_amap_service()
        result = amap_service.search_poi_detail(keywords, city)

        if result is None:
            return {
                "success": False,
                "message": "未找到匹配的POI",
                "data": None
            }

        return {
            "success": True,
            "message": "搜索成功",
            "data": result
        }

    except Exception as e:
        print(f"❌ 搜索POI详情失败: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"搜索POI详情失败: {str(e)}"
        )


@router.get(
    "/ticket-info",
    summary="查询景点门票信息（LLM估价）",
    description="根据景点名称和城市查询门票价格、购票方式"
)
async def get_ticket_info(name: str, city: str = ""):
    """查询景点门票信息（Tavily不可用时走LLM兜底估价）"""
    try:
        from ...services.ticket_service import query_ticket_info
        ticket = query_ticket_info(name, city)
        return {
            "success": True,
            "message": "查询成功",
            "data": {
                "price": ticket.price,
                "opening_hours": ticket.opening_hours,
                "purchase_method": ticket.purchase_method,
                "source": ticket.source,
                "source_url": ticket.source_url,
                "confidence": ticket.confidence,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询门票信息失败: {str(e)}")

