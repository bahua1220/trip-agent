"""旅行规划API路由"""

from fastapi import APIRouter, Depends, HTTPException, status
from ...models.schemas import (
    RouteDraftRequest,
    RouteOption,
    RouteRecommendationRequest,
    TripRequest,
    TripPlan,
    TripPlanReflowRequest,
    TripTaskCreatedResponse,
    TripTaskStatusResponse,
)
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...services.trip_task_service import (
    ActiveTripTaskExists,
    TripTaskDailyLimitExceeded,
    get_trip_task_service,
)
from ...services.route_architect_service import get_route_architect_service
from ...services.wechat_content_security import check_request_content
from .auth import current_user

router = APIRouter(prefix="/trip", tags=["旅行规划"])


@router.post(
    "/route-options",
    response_model=list[RouteOption],
    summary="智能推荐区域跨城路线",
    description="根据地区、天数、偏好和交通方式返回已进行路程校验的候选路线。",
)
async def recommend_route_options(request: RouteRecommendationRequest):
    try:
        await check_request_content(request)
        return get_route_architect_service().recommend(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="路线推荐服务暂时不可用，请稍后重试或改用手动节点。",
        ) from error


@router.post(
    "/route-options/validate",
    response_model=RouteOption,
    summary="重新校验编辑后的跨城路线",
    description="按用户调整后的节点顺序重新计算逐段距离、交通耗时和风险。",
)
async def validate_route_draft(request: RouteDraftRequest):
    try:
        await check_request_content(request)
        return get_route_architect_service().validate_draft(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="路线重新校验失败，请检查节点名称后重试。",
        ) from error


async def _create_plan_task(request: TripRequest, user_id: str) -> TripTaskCreatedResponse:
    try:
        await check_request_content(request, user_id)
        print(f"\n{'='*60}")
        print(f"📥 收到旅行规划请求:")
        print(f"   城市: {request.city}")
        print(f"   日期: {request.start_date} - {request.end_date}")
        print(f"   天数: {request.travel_days}")
        print(f"{'='*60}\n")

        task = get_trip_task_service().create_task(request, user_id)
        return TripTaskCreatedResponse(
            task_id=task.task_id,
            status=task.status,
            progress=task.progress,
            message=task.message,
        )

    except ActiveTripTaskExists as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="你已有一个行程正在生成，请等待完成后再创建新行程。",
        ) from error
    except TripTaskDailyLimitExceeded as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="今天的行程生成次数已达上限，将于明日00:00恢复。",
        ) from error
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ 生成旅行计划失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail="生成旅行计划失败，请稍后重试。"
        )


@router.post(
    "/plan/authenticated",
    response_model=TripTaskCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="登录用户创建旅行规划任务",
    description="Web 端使用此入口；验证登录身份后创建后台生成任务。",
)
async def plan_trip_authenticated(
    request: TripRequest,
    user=Depends(current_user),
):
    return await _create_plan_task(request, user["id"])


@router.post(
    "/plan",
    response_model=TripTaskCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="创建旅行规划任务",
    description="兼容旧客户端的登录保护入口；新客户端使用 authenticated 路径。",
)
async def plan_trip(request: TripRequest, user=Depends(current_user)):
    return await _create_plan_task(request, user["id"])


@router.post(
    "/plan/reflow",
    response_model=TripPlan,
    summary="重新计算编辑后的行程",
    description="使用原始生成条件更新指定日期的逐段交通、时间轴、预算和冲突提示。",
)
async def reflow_trip_plan(
    payload: TripPlanReflowRequest,
    user=Depends(current_user),
):
    try:
        return get_trip_planner_agent().reflow_edited_plan(
            payload.plan,
            payload.day_indices,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="行程重新计算失败，修改已保留，请稍后再试。",
        ) from error


@router.get(
    "/plan/{task_id}",
    response_model=TripTaskStatusResponse,
    summary="查询旅行规划任务",
    description="返回后台任务的实时阶段、进度与最终旅行计划。"
)
async def get_plan_task(task_id: str, user=Depends(current_user)):
    task = get_trip_task_service().get_task(task_id, user["id"])
    if not task:
        raise HTTPException(status_code=404, detail="旅行规划任务不存在或已过期")
    return TripTaskStatusResponse(
        task_id=task.task_id,
        status=task.status,
        progress=task.progress,
        message=task.message,
        data=task.result,
        error=task.error,
    )


@router.get(
    "/health",
    summary="健康检查",
    description="检查旅行规划服务是否正常"
)
async def health_check():
    """健康检查"""
    try:
        # 检查Agent是否可用
        agent = get_trip_planner_agent()
        
        return {
            "status": "healthy",
            "service": "trip-planner",
            "framework": "LangGraph",
            "agents": ["route_architect", "attraction", "weather", "hotel", "planner"]
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"服务不可用: {str(e)}"
        )

