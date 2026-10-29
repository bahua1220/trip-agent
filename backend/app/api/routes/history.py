from fastapi import APIRouter, Depends, HTTPException, status
from ...models.schemas import HistoryEntryResponse, HistoryUpsertRequest
from ...services.auth_service import delete_history, list_history, save_history
from .auth import current_user

router = APIRouter(prefix="/history", tags=["行程历史"])

@router.get("", response_model=list[HistoryEntryResponse])
def list_route(user=Depends(current_user)): return list_history(user['id'])

@router.put("/{entry_id}", response_model=HistoryEntryResponse)
def save_route(entry_id: str, data: HistoryUpsertRequest, user=Depends(current_user)):
    if entry_id != data.id: raise HTTPException(400, '行程 ID 不一致')
    return save_history(user['id'], entry_id, data.plan.model_dump())

@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_route(entry_id: str, user=Depends(current_user)):
    if not delete_history(user['id'], entry_id): raise HTTPException(404, '历史行程不存在')
