"""Thin 8765 web frontend for the authenticated 8766 goods API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib import error, request

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel


API_BASE = os.environ.get("GOODS_API_URL", "http://127.0.0.1:8766").rstrip("/")
INDEX = Path(__file__).with_name("index.html")
app = FastAPI(title="多多直播商品查询 Web", docs_url=None, redoc_url=None)


class QueryBody(BaseModel):
    goodsIds: list[int] | str
    showId: str | None = None


def api_call(path: str, key: str | None, payload: dict | None = None) -> dict:
    if not key:
        raise HTTPException(status_code=401, detail={"code": "INVALID_API_KEY", "message": "请填写 API Key"})
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"X-API-Key": key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    upstream = request.Request(API_BASE + path, data=data, headers=headers)
    try:
        with request.urlopen(upstream, timeout=90) as response:
            return json.load(response)
    except error.HTTPError as exc:
        try:
            detail = json.load(exc).get("detail", "上游接口请求失败")
        except (ValueError, OSError):
            detail = "上游接口请求失败"
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=503, detail="商品 API 暂不可用，请稍后重试") from exc


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(INDEX, headers={"Cache-Control": "no-store"})


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/status", include_in_schema=False)
def status(x_api_key: str | None = Header(default=None)) -> dict:
    try:
        data = api_call("/v1/shows", x_api_key)
        return {"authenticated": True, "shows": data.get("shows", [])}
    except HTTPException as exc:
        if exc.status_code == 409:
            return {"authenticated": False, "shows": [], "loginRequired": True}
        raise


@app.post("/api/query", include_in_schema=False)
def query(body: QueryBody, x_api_key: str | None = Header(default=None)) -> dict:
    return api_call("/v1/goods/query", x_api_key, body.model_dump(exclude_none=True))
