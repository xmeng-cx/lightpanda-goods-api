from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from browser import Browser, LoginRequired, parse_ids
from security import (
    SESSION_SECONDS,
    create_admin_session,
    create_api_key,
    init_state,
    list_api_keys,
    revoke_admin_session,
    revoke_api_key,
    valid_admin_session,
    verify_admin_password,
    verify_api_key,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_state()
    yield
    browser.close()


app = FastAPI(title="多多直播商品 API", version="1.0.0", lifespan=lifespan)
browser = Browser()
key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
INDEX = Path(__file__).with_name("index.html")


class QueryBody(BaseModel):
    goodsIds: list[int] | str
    showId: str | None = None


class PasswordBody(BaseModel):
    password: str


class KeyBody(BaseModel):
    label: str = Field(min_length=1, max_length=64)


def api_key_required(key: Annotated[str | None, Depends(key_header)]) -> None:
    if not verify_api_key(key):
        raise HTTPException(status_code=401, detail={"code": "INVALID_API_KEY", "message": "API Key 无效或已撤销"})


def admin_required(request: Request) -> None:
    token = request.cookies.get("lp_admin")
    if not valid_admin_session(token):
        raise HTTPException(status_code=401, detail="请先登录管理后台")
    if request.method != "GET":
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(status_code=403, detail="请求来源不匹配")


def normalize_ids(raw: list[int] | str) -> list[int]:
    if isinstance(raw, str):
        return parse_ids(raw)
    if not raw or len(raw) > 1000:
        raise ValueError("请输入 1 至 1000 个商品 ID")
    if any(not isinstance(value, int) or value < 10**9 or value >= 10**20 for value in raw):
        raise ValueError("商品 ID 必须是 10 至 20 位正整数")
    return list(dict.fromkeys(raw))


def query_goods(raw: list[int] | str, show_id: str | None) -> dict:
    try:
        ids = normalize_ids(raw)
        result = browser.query(ids, show_id)
        for goods in result["goodsList"]:
            if isinstance(goods.get("price"), (int, float)):
                goods["priceYuan"] = round(goods["price"] / 100, 2)
        return result
    except LoginRequired as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "LOGIN_REQUIRED", "message": "商家登录已失效，请在管理后台扫码", "adminUrl": "/admin"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/admin", status_code=302)


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(INDEX, headers={"Cache-Control": "no-store"})


@app.post("/admin/api/session", include_in_schema=False)
def admin_login(body: PasswordBody, request: Request):
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="请求来源不匹配")
    if not verify_admin_password(body.password):
        raise HTTPException(status_code=401, detail="管理密码错误")
    token = create_admin_session()
    from fastapi.responses import JSONResponse

    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        "lp_admin", token, max_age=SESSION_SECONDS, httponly=True,
        samesite="strict", secure=False, path="/admin",
    )
    return response


@app.get("/admin/api/session", dependencies=[Depends(admin_required)], include_in_schema=False)
def admin_session() -> dict:
    return {"authenticated": True}


@app.post("/admin/api/session/logout", dependencies=[Depends(admin_required)], include_in_schema=False)
def admin_logout(request: Request):
    revoke_admin_session(request.cookies.get("lp_admin"))
    from fastapi.responses import JSONResponse

    response = JSONResponse({"loggedOut": True})
    response.delete_cookie("lp_admin", path="/admin")
    return response


@app.get("/admin/api/keys", dependencies=[Depends(admin_required)], include_in_schema=False)
def admin_keys() -> dict:
    return {"keys": list_api_keys()}


@app.post("/admin/api/keys", dependencies=[Depends(admin_required)], include_in_schema=False)
def admin_create_key(body: KeyBody) -> dict:
    label = body.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="请输入 Key 名称")
    info, token = create_api_key(label)
    return {"key": info, "token": token}


@app.delete("/admin/api/keys/{key_id}", dependencies=[Depends(admin_required)], include_in_schema=False)
def admin_revoke_key(key_id: int) -> dict:
    if not revoke_api_key(key_id):
        raise HTTPException(status_code=404, detail="API Key 不存在或已撤销")
    return {"revoked": True}


@app.get("/admin/api/merchant", dependencies=[Depends(admin_required)], include_in_schema=False)
def admin_merchant_status() -> dict:
    try:
        return browser.status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/admin/api/merchant/login", dependencies=[Depends(admin_required)], include_in_schema=False)
def admin_merchant_login() -> dict:
    try:
        return browser.start_login()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/admin/api/merchant/logout", dependencies=[Depends(admin_required)], include_in_schema=False)
def admin_merchant_logout() -> dict:
    try:
        return browser.logout()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/admin/api/goods/query", dependencies=[Depends(admin_required)], include_in_schema=False)
def admin_query(body: QueryBody) -> dict:
    return query_goods(body.goodsIds, body.showId)


@app.get("/v1/shows", dependencies=[Depends(api_key_required)])
def api_shows() -> dict:
    try:
        state = browser.status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not state.get("authenticated"):
        raise HTTPException(status_code=409, detail={"code": "LOGIN_REQUIRED", "adminUrl": "/admin"})
    return {"shows": state.get("shows", [])}


@app.get("/v1/goods", dependencies=[Depends(api_key_required)])
def api_goods_get(ids: str, showId: str | None = None) -> dict:
    return query_goods(ids, showId)


@app.post("/v1/goods/query", dependencies=[Depends(api_key_required)])
def api_goods_post(body: QueryBody) -> dict:
    return query_goods(body.goodsIds, body.showId)
