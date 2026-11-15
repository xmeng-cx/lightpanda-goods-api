"""Thin 8765 web frontend for the authenticated 8766 goods API."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from io import BytesIO
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib import error, request
from urllib.parse import quote, urlsplit
from decimal import Decimal, InvalidOperation
from unicodedata import east_asian_width

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from pydantic import BaseModel, Field


API_BASE = os.environ.get("GOODS_API_URL", "http://127.0.0.1:8766").rstrip("/")
INDEX = Path(__file__).with_name("index.html")
DATA_DIR = Path(os.environ.get("GOODS_WEB_DATA_DIR") or os.environ.get("STATE_DIRECTORY") or INDEX.parent / "data")
DB_PATH = DATA_DIR / "batches.sqlite3"
app = FastAPI(title="多多直播商品查询 Web", docs_url=None, redoc_url=None)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


API_OPENER = request.build_opener(NoRedirect)


def api_base_url(value: str | None) -> str:
    if not value or not value.strip():
        return API_BASE
    value = value.strip()
    if len(value) > 256:
        raise HTTPException(status_code=400, detail="API 地址过长")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="API 地址格式不正确") from exc
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or
            parsed.username or parsed.password or parsed.path not in ("", "/") or
            parsed.query or parsed.fragment or port == 0):
        raise HTTPException(status_code=400, detail="API 地址须为 http(s)://主机[:端口]，不能包含路径、参数或账号")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}{f':{port}' if port is not None else ''}"


class QueryBody(BaseModel):
    goodsIds: list[int] | str
    showId: str | None = None
    batchName: str = Field(default="", max_length=80)
    taskType: Literal["filter", "sales"] = "filter"
    salesThreshold: int = Field(default=10, ge=0)


class SalesThresholdBody(BaseModel):
    salesThreshold: int = Field(ge=0)


@contextmanager
def database():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            name TEXT NOT NULL,
            task_type TEXT NOT NULL,
            api_base TEXT NOT NULL,
            show_id TEXT,
            total INTEGER NOT NULL,
            sold INTEGER NOT NULL,
            unsold INTEGER NOT NULL,
            sold_out INTEGER NOT NULL,
            unknown INTEGER NOT NULL,
            sales_threshold INTEGER NOT NULL DEFAULT 10,
            metric_version INTEGER NOT NULL DEFAULT 2,
            result_json TEXT NOT NULL
        )""")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(batches)")}
        if "sales_threshold" not in columns:
            connection.execute("ALTER TABLE batches ADD COLUMN sales_threshold INTEGER NOT NULL DEFAULT 10")
        if "metric_version" not in columns:
            connection.execute("ALTER TABLE batches ADD COLUMN metric_version INTEGER NOT NULL DEFAULT 1")
        if "api_base" not in columns:
            connection.execute("ALTER TABLE batches ADD COLUMN api_base TEXT NOT NULL DEFAULT ''")
            connection.execute("UPDATE batches SET api_base = ? WHERE api_base = ''", (API_BASE,))
        for row in connection.execute("SELECT id, result_json, sales_threshold FROM batches WHERE metric_version < 2"):
            result = json.loads(row["result_json"])
            totals = counts(result, row["sales_threshold"])
            connection.execute("""UPDATE batches SET total=?, sold=?, unsold=?, sold_out=?, unknown=?, metric_version=2
                WHERE id=?""", (totals["total"], totals["sold"], totals["unsold"], totals["sold_out"], totals["unknown"], row["id"]))
        yield connection
        connection.commit()
    finally:
        connection.close()


def displayed_sales(goods: dict) -> int | None:
    """Read the visible 已拼/已抢 count; never substitute soldQuantity."""
    tip = str(goods.get("soldQuantityTip") or "").strip()
    if not tip:
        return None
    compact = re.sub(r"\s+", "", tip).replace(",", "")
    match = re.search(r"(?:已拼|已抢|已售)(\d+(?:\.\d+)?)(万|亿)?", compact)
    if match is None:
        raise ValueError(f"无法解析已拼显示：{tip}")
    try:
        multiplier = {None: 1, "万": 10000, "亿": 100000000}[match.group(2)]
        return int(Decimal(match.group(1)) * multiplier)
    except InvalidOperation as exc:
        raise ValueError(f"无法解析已拼显示：{tip}") from exc


def counts(result: dict, sales_threshold: int = 10) -> dict:
    goods = {str(item.get("goodsId")): item for item in result.get("goodsList", [])}
    failures = {str(item.get("goodsId")) for item in result.get("failures", [])}
    requested = result.get("requestedGoodsIds") or list(goods) + list(failures)
    totals = {"total": len(requested), "sold": 0, "unsold": 0, "sold_out": 0, "unknown": 0}
    for goods_id in requested:
        key = str(goods_id)
        item = goods.get(key)
        if key in failures or item is None:
            totals["unknown"] += 1
        elif isinstance(item.get("stock"), (int, float)) and item["stock"] <= 0:
            totals["sold_out"] += 1
        else:
            try:
                sales = displayed_sales(item)
            except ValueError:
                totals["unknown"] += 1
                continue
            if sales is not None and sales > sales_threshold:
                totals["sold"] += 1
            else:
                totals["unsold"] += 1
    return totals


def batch_row(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in ("id", "created_at", "name", "task_type", "show_id", "total", "sold", "unsold", "sold_out", "unknown", "sales_threshold")}


def api_call(path: str, key: str | None, payload: dict | None = None,
             api_base: str | None = None) -> dict:
    if not key:
        raise HTTPException(status_code=401, detail={"code": "INVALID_API_KEY", "message": "请填写 API Key"})
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"X-API-Key": key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    upstream = request.Request(api_base_url(api_base) + path, data=data, headers=headers)
    try:
        goods_ids = payload.get("goodsIds", []) if payload and path == "/v1/goods/query" else []
        batch_count = (len(goods_ids) + 999) // 1000 if isinstance(goods_ids, list) else 1
        with API_OPENER.open(upstream, timeout=max(300, batch_count * 300) if batch_count else 30) as response:
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


@app.get("/style.css", include_in_schema=False)
def style() -> FileResponse:
    return FileResponse(INDEX.with_name("style.css"), media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/app.js", include_in_schema=False)
def script() -> FileResponse:
    return FileResponse(INDEX.with_name("app.js"), media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/status", include_in_schema=False)
def status(x_api_key: str | None = Header(default=None),
           x_api_base_url: Annotated[str | None, Header()] = None) -> dict:
    return api_call("/v1/merchant/status", x_api_key, api_base=x_api_base_url)


@app.post("/api/login", include_in_schema=False)
def login(x_api_key: str | None = Header(default=None),
          x_api_base_url: Annotated[str | None, Header()] = None) -> dict:
    return api_call("/v1/merchant/login", x_api_key, {}, x_api_base_url)


@app.post("/api/logout", include_in_schema=False)
def logout(x_api_key: str | None = Header(default=None),
           x_api_base_url: Annotated[str | None, Header()] = None) -> dict:
    return api_call("/v1/merchant/logout", x_api_key, {}, x_api_base_url)


@app.post("/api/query", include_in_schema=False)
def query(body: QueryBody, x_api_key: str | None = Header(default=None),
          x_api_base_url: Annotated[str | None, Header()] = None) -> dict:
    payload = body.model_dump(include={"goodsIds", "showId"}, exclude_none=True)
    result = api_call("/v1/goods/query", x_api_key, payload, x_api_base_url)
    for item in result.get("goodsList", []):
        item["goodsId"] = str(item["goodsId"])
    for item in result.get("failures", []):
        item["goodsId"] = str(item["goodsId"])
    # The upstream response omits the submitted IDs. Retain their order so failures
    # and any omitted products still appear in the batch.
    if isinstance(body.goodsIds, str):
        import re
        ids = list(dict.fromkeys(re.split(r"[\s,;，；]+", body.goodsIds.strip())))
        ids = [item for item in ids if item]
    else:
        ids = list(dict.fromkeys(str(item) for item in body.goodsIds))
    result["requestedGoodsIds"] = ids
    totals = counts(result, body.salesThreshold if body.taskType == "sales" else 10)
    created_at = datetime.now(timezone.utc).isoformat()
    name = body.batchName.strip() or datetime.now().strftime("查询批次 %Y-%m-%d %H:%M")
    with database() as db:
        cursor = db.execute(
            """INSERT INTO batches (created_at, name, task_type, api_base, show_id, total, sold, unsold, sold_out, unknown, sales_threshold, result_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (created_at, name, body.taskType, api_base_url(x_api_base_url), result.get("showId"), totals["total"], totals["sold"],
             totals["unsold"], totals["sold_out"], totals["unknown"], body.salesThreshold if body.taskType == "sales" else 10,
             json.dumps(result, ensure_ascii=False)),
        )
        result["batchId"] = cursor.lastrowid
    return result


@app.get("/api/batches", include_in_schema=False)
def batches(taskType: Literal["filter", "sales"], x_api_key: str | None = Header(default=None),
            x_api_base_url: Annotated[str | None, Header()] = None) -> dict:
    api_call("/v1/merchant/status", x_api_key, api_base=x_api_base_url)
    with database() as db:
        rows = db.execute("""SELECT id, created_at, name, task_type, show_id, total, sold, unsold,
            sold_out, unknown, sales_threshold FROM batches WHERE task_type = ? AND api_base = ? ORDER BY id DESC LIMIT 200""",
            (taskType, api_base_url(x_api_base_url))).fetchall()
    return {"batches": [batch_row(row) for row in rows]}


@app.post("/api/sales-threshold", include_in_schema=False)
def update_sales_threshold(body: SalesThresholdBody, x_api_key: str | None = Header(default=None),
                           x_api_base_url: Annotated[str | None, Header()] = None) -> dict:
    api_call("/v1/merchant/status", x_api_key, api_base=x_api_base_url)
    updated = 0
    with database() as db:
        rows = db.execute("SELECT id, result_json, sales_threshold FROM batches WHERE task_type = 'sales' AND api_base = ?",
                          (api_base_url(x_api_base_url),)).fetchall()
        for row in rows:
            if row["sales_threshold"] == body.salesThreshold:
                continue
            totals = counts(json.loads(row["result_json"]), body.salesThreshold)
            db.execute("""UPDATE batches SET total=?, sold=?, unsold=?, sold_out=?, unknown=?, sales_threshold=?
                WHERE id=?""", (totals["total"], totals["sold"], totals["unsold"], totals["sold_out"],
                               totals["unknown"], body.salesThreshold, row["id"]))
            updated += 1
    return {"updated": updated, "salesThreshold": body.salesThreshold}


@app.get("/api/batches/{batch_id}", include_in_schema=False)
def batch_detail(batch_id: int, x_api_key: str | None = Header(default=None),
                 x_api_base_url: Annotated[str | None, Header()] = None) -> dict:
    api_call("/v1/merchant/status", x_api_key, api_base=x_api_base_url)
    with database() as db:
        row = db.execute("SELECT * FROM batches WHERE id = ? AND api_base = ?",
                         (batch_id, api_base_url(x_api_base_url))).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    return {"batch": batch_row(row), "result": json.loads(row["result_json"])}


@app.delete("/api/batches/{batch_id}", include_in_schema=False)
def delete_batch(batch_id: int, x_api_key: str | None = Header(default=None),
                 x_api_base_url: Annotated[str | None, Header()] = None) -> dict:
    api_call("/v1/merchant/status", x_api_key, api_base=x_api_base_url)
    with database() as db:
        deleted = db.execute("DELETE FROM batches WHERE id = ? AND api_base = ?",
                             (batch_id, api_base_url(x_api_base_url))).rowcount
    if not deleted:
        raise HTTPException(status_code=404, detail="批次不存在")
    return {"deleted": deleted}


@app.delete("/api/batches", include_in_schema=False)
def clear_batches(x_api_key: str | None = Header(default=None),
                  x_api_base_url: Annotated[str | None, Header()] = None) -> dict:
    api_call("/v1/merchant/status", x_api_key, api_base=x_api_base_url)
    with database() as db:
        deleted = db.execute("DELETE FROM batches WHERE api_base = ?", (api_base_url(x_api_base_url),)).rowcount
    return {"deleted": deleted}


def export_status(item: dict, threshold: int) -> str:
    goods = item["goods"]
    if item["failure"] or not goods:
        return "未知"
    if isinstance(goods.get("stock"), (int, float)) and goods["stock"] <= 0:
        return "售罄"
    try:
        sales = displayed_sales(goods)
    except ValueError:
        return "未知"
    return "已出" if sales is not None and sales > threshold else "未出"


def workbook_width(value: object) -> int:
    return sum(2 if east_asian_width(char) in "FW" else 1 for char in str(value or ""))


@app.get("/api/batches/{batch_id}/export", include_in_schema=False)
def export_batch(
    batch_id: int,
    view: Literal["eligible", "overSales", "overPrice", "unknown"] = "eligible",
    filterSales: int = 20,
    filterPrice: float = 500,
    x_api_key: str | None = Header(default=None),
    x_api_base_url: Annotated[str | None, Header()] = None,
) -> Response:
    if filterSales < 0 or filterPrice < 0:
        raise HTTPException(status_code=422, detail="过滤规则不能为负数")
    data = batch_detail(batch_id, x_api_key, x_api_base_url)
    batch, result = data["batch"], data["result"]
    goods = {str(item.get("goodsId")): item for item in result.get("goodsList", [])}
    failures = {str(item.get("goodsId")): item for item in result.get("failures", [])}
    ids = result.get("requestedGoodsIds") or list(goods) + list(failures)
    items = [{"index": index, "id": str(goods_id), "goods": goods.get(str(goods_id)),
              "failure": failures.get(str(goods_id))} for index, goods_id in enumerate(ids, 1)]
    if batch["task_type"] == "filter":
        selected = []
        for item in items:
            product = item["goods"]
            if not product or item["failure"]:
                group = "unknown"
                over_sales = over_price = False
            else:
                try:
                    sales = displayed_sales(product)
                    sales_known = True
                except ValueError:
                    sales = None
                    sales_known = False
                price_known = isinstance(product.get("price"), (int, float))
                over_sales = sales_known and sales is not None and sales > filterSales
                over_price = price_known and product["price"] / 100 > filterPrice
                group = "eligible" if not over_sales and not over_price and sales_known and price_known else "unknown"
            if (view == "overSales" and over_sales) or (view == "overPrice" and over_price) or group == view:
                selected.append(item)
    else:
        selected = items

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "商品销量查询明细" if batch["task_type"] == "sales" else {"eligible": "符合条件", "overSales": "超销预览", "overPrice": "超价预览", "unknown": "待核实"}[view]
    if batch["task_type"] == "sales":
        threshold = batch["sales_threshold"]
        sheet.append(["总商品数量", f"已出 (销量>{threshold})", f"未出 (销量≤{threshold}/空)", "售罄", "未知", "出销率"])
        sheet.append([batch["total"], batch["sold"], batch["unsold"], batch["sold_out"], batch["unknown"],
                      f"{batch['sold'] / batch['total'] * 100:.2f}%" if batch["total"] else "0.00%"])
        sheet.append([])
        header_row = 4
        sheet.append(["序号", "商品ID", "商品名称", "销量", "价格", "处理状态", "商品链接"])
    else:
        header_row = 1
        sheet.append(["序号", "商品ID", "商品名称", "销量（已拼显示）", "价格(元)", "库存", "处理状态", "商品链接", "失败原因"])

    for item in selected:
        product = item["goods"] or {}
        price = product.get("price")
        link = f"https://mobile.yangkeduo.com/goods.html?goods_id={item['id']}" if re.fullmatch(r"\d{10,20}", item["id"]) else str(product.get("goodsLink") or "")
        reason = str((item["failure"] or {}).get("reason") or product.get("reason") or "")
        if batch["task_type"] == "sales":
            try:
                sales = displayed_sales(product)
            except ValueError:
                sales = None
            sales_value = sales if sales is not None else ""
        else:
            sales_value = str(product.get("soldQuantityTip") or "")
        common = [item["index"], item["id"], str(product.get("title") or ""), sales_value,
                  price / 100 if isinstance(price, (int, float)) else ""]
        if batch["task_type"] == "sales":
            values = common + [export_status(item, batch["sales_threshold"]), link]
        else:
            status = "查询失败" if item["failure"] or not item["goods"] else {"eligible": "符合条件", "overSales": "超销", "overPrice": "超价", "unknown": "待核实"}[view]
            values = common + [product.get("stock") if product.get("stock") is not None else "", status, link, reason]
        sheet.append(values)
        id_cell = sheet.cell(sheet.max_row, 2)
        id_cell.number_format = "@"
        id_cell.data_type = "s"
        sheet.cell(sheet.max_row, 5).number_format = "¥#,##0.00" if batch["task_type"] == "sales" else "0.00"
        link_cell = sheet.cell(sheet.max_row, 7 if batch["task_type"] == "sales" else 8)
        if link.startswith(("https://", "http://")):
            link_cell.hyperlink = link
            link_cell.font = Font(color="3659DC", underline="single")

    if batch["task_type"] == "sales":
        border = Border(left=Side(style="thin", color="C9D8C3"), right=Side(style="thin", color="C9D8C3"),
                        top=Side(style="thin", color="C9D8C3"), bottom=Side(style="thin", color="C9D8C3"))
        for row in sheet:
            for cell in row:
                if cell.row == 3 or (cell.row < 4 and cell.column == 7):
                    continue
                cell.border = border
                cell.font = Font(name="Microsoft YaHei", size=10, bold=cell.row in (1, 4) or cell.coordinate == "F2" or cell.column == 6 and cell.row > 4,
                                 color="0000FF" if cell.column == 7 and cell.row > 4 and cell.hyperlink else "000000",
                                 underline="single" if cell.column == 7 and cell.row > 4 and cell.hyperlink else None)
                cell.alignment = Alignment(horizontal="right" if cell.column == 5 and cell.row > 4 else "left" if cell.column in (3, 7) and cell.row > 4 else "center",
                                           vertical="center", wrap_text=True)
                if cell.row in (1, 4):
                    cell.fill = PatternFill("solid", fgColor="D9EAD3" if cell.row == 1 else "E2EFDA")
        for letter, width in {"A": 8, "B": 20, "C": 45, "D": 12, "E": 14, "F": 14, "G": 48}.items():
            sheet.column_dimensions[letter].width = width
        sheet.row_dimensions[1].height = 24
        sheet.row_dimensions[2].height = 22
        sheet.row_dimensions[3].height = 10
        sheet.row_dimensions[4].height = 24
    else:
        for row in sheet:
            for cell in row:
                if cell.row == header_row:
                    cell.fill = PatternFill("solid", fgColor="EAF0FF")
                    cell.font = Font(bold=True, color="203455")
                cell.alignment = Alignment(vertical="center", wrap_text=True)
        for column in sheet.columns:
            letter = column[0].column_letter
            maximum = max(workbook_width(cell.value) for cell in column)
            sheet.column_dimensions[letter].width = min(max(maximum + 3, 12), 72)
        sheet.column_dimensions["B"].width = max(sheet.column_dimensions["B"].width, 23)
        sheet.column_dimensions["C"].width = min(max(sheet.column_dimensions["C"].width, 32), 72)
    for row in sheet.iter_rows(min_row=header_row + 1):
        lines = max((workbook_width(cell.value) + max(1, int(sheet.column_dimensions[cell.column_letter].width)) - 1)
                    // max(1, int(sheet.column_dimensions[cell.column_letter].width)) for cell in row)
        sheet.row_dimensions[row[0].row].height = min(409, max(28, lines * 19))
    output = BytesIO()
    workbook.save(output)
    date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    if batch["task_type"] == "sales":
        rate = f"{batch['sold'] / batch['total'] * 100:.2f}".rstrip("0").rstrip(".") if batch["total"] else "0"
        filename = f"{date}-出销率{rate}%-成功{batch['sold']}.xlsx"
    else:
        filename = f"商品过滤-{date}-{sheet.title}.xlsx"
    return Response(output.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}", "Cache-Control": "no-store"})
