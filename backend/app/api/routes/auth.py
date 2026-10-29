from fastapi import APIRouter, Depends, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ...models.schemas import (
    AuthResponse,
    DeleteAccountRequest,
    RegisterRequest,
    WechatCodeRequest,
)
from ...services.auth_service import (
    authenticate,
    bind_wechat_identity,
    delete_user_account,
    login_with_wechat,
    merge_wechat_user,
    record_user_consent,
    register,
    token_for,
    user_from_token,
)

router = APIRouter(prefix="/auth", tags=["账号"])
security = HTTPBearer()

def current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    return user_from_token(credentials.credentials)

def _response(user):
    return AuthResponse(access_token=token_for(user), user=user)

@router.post("/register", response_model=AuthResponse, status_code=201)
def register_route(data: RegisterRequest):
    user = register(data.username, data.password)
    record_user_consent(user["id"], data.consent_version, "miniapp_or_web")
    return _response(user)

@router.post("/login", response_model=AuthResponse)
def login_route(data: RegisterRequest):
    user = authenticate(data.username, data.password)
    record_user_consent(user["id"], data.consent_version, "miniapp_or_web")
    return _response(user)

@router.post("/wechat/login", response_model=AuthResponse)
async def wechat_login_route(data: WechatCodeRequest):
    user = await login_with_wechat(data.code)
    record_user_consent(user["id"], data.consent_version, "miniapp")
    return _response(user)

@router.post("/wechat/bind", response_model=AuthResponse)
async def wechat_bind_route(data: WechatCodeRequest, user=Depends(current_user)):
    return _response(await bind_wechat_identity(user["id"], data.code))

@router.post("/wechat/merge", response_model=AuthResponse)
def wechat_merge_route(data: RegisterRequest, user=Depends(current_user)):
    return _response(merge_wechat_user(user["id"], data.username, data.password))

@router.get("/me")
def me(user=Depends(current_user)): return user


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account_route(data: DeleteAccountRequest, user=Depends(current_user)):
    delete_user_account(user["id"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
