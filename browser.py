from __future__ import annotations

import base64
import io
import json
import os
import re
import threading
import time
from typing import Any
from urllib.parse import urlparse
from pathlib import Path

import qrcode
import websocket





CDP_URL = "ws://127.0.0.1:9222/"
RECORD_URL = "https://live.pinduoduo.com/n-creator/live/live-record"
COOKIE_FILE = Path("/var/lib/lightpanda-goods-api/cookies.json")



class LoginRequired(Exception):
    pass


class Browser:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.ws: Any = None
        self.session_id: str | None = None
        self.next_id = 0
        self.last_shows: list[dict] = []
        self.cookie_snapshot: str | None = None

    def connect(self) -> None:
        if self.ws is not None:
            try:
                self.ws.ping()
                return
            except (OSError, websocket.WebSocketConnectionClosedException):
                self.close()
        self.ws = websocket.create_connection(CDP_URL, timeout=30, suppress_origin=True)
        try:
            target = self.call("Target.createTarget", {"url": "about:blank"}, browser=True)
            attached = self.call(
                "Target.attachToTarget",
                {"targetId": target["targetId"], "flatten": True},
                browser=True,
            )
            self.session_id = attached["sessionId"]
            self.call("Page.enable")
            self.call("Runtime.enable")
            self.call("Network.enable")
            self._restore_cookies()
            self.call("Page.navigate", {"url": RECORD_URL})
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None
        self.session_id = None

    def call(self, method: str, params: dict | None = None, *, browser: bool = False) -> dict:
        self.next_id += 1
        request: dict[str, Any] = {"id": self.next_id, "method": method, "params": params or {}}
        if not browser and self.session_id:
            request["sessionId"] = self.session_id
        try:
            self.ws.send(json.dumps(request))
        except (OSError, websocket.WebSocketConnectionClosedException) as exc:
            self.close()
            raise ConnectionError("Lightpanda 连接已断开") from exc
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                message = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            except (OSError, websocket.WebSocketConnectionClosedException) as exc:
                self.close()
                raise ConnectionError("Lightpanda 连接已断开") from exc
            if message.get("id") != request["id"]:
                continue
            if "error" in message:
                raise RuntimeError(f"CDP {method}: {message['error']}")
            return message.get("result", {})
        self.close()
        raise TimeoutError(f"CDP {method} timed out")

    def _restore_cookies(self) -> None:
        if not COOKIE_FILE.exists():
            return
        try:
            cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            fields = ("name", "value", "domain", "path", "secure", "httpOnly")
            prepared = [{key: cookie[key] for key in fields if key in cookie} for cookie in cookies]
            self.call("Network.setCookies", {"cookies": prepared})
        except (OSError, ValueError, RuntimeError):
            pass

    def _save_cookies(self) -> None:
        cookies = self.call("Network.getAllCookies").get("cookies", [])
        content = json.dumps(cookies, ensure_ascii=False)
        if not cookies or content == self.cookie_snapshot:
            return
        COOKIE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = COOKIE_FILE.with_suffix(".tmp")
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(content)
            os.replace(temp, COOKIE_FILE)
        finally:
            if temp.exists():
                temp.unlink()
        self.cookie_snapshot = content

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True}
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(f"JavaScript: {expression[:220]} :: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def wait_for(self, expression: str, timeout: float = 15) -> Any:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                value = self.evaluate(expression)
                if value:
                    return value
            except RuntimeError:
                pass
            time.sleep(0.3)
        raise TimeoutError("页面响应超时，请重试")

    def url(self) -> str:
        return self.evaluate("location.href") or ""

    def _qr_value(self) -> str | None:
        return self.evaluate(
            """(()=>{let c=document.querySelector('canvas');if(!c)return null;
            let k=Object.keys(c).find(x=>x.startsWith('__react'));
            let f=k&&c[k];for(let i=0;f&&i<12;i++,f=f.return){
            let v=f.memoizedProps?.value;if(typeof v==='string'&&v.startsWith('http'))return v}
            return null})()"""
        )

    def status(self) -> dict:
        with self.lock:
            try:
                return self._status()
            except (ConnectionError, TimeoutError):
                self.close()
                return self._status()

    def _status(self) -> dict:
        with self.lock:
            self.connect()
            for _ in range(3):
                try:
                    current = self.url()
                except RuntimeError:
                    time.sleep(0.5)
                    continue
                path = urlparse(current).path
                if path == "/n-creator/live/live-record":
                    ready = self.evaluate(
                        "document.readyState==='complete' && document.body.innerText.includes('退出登录')"
                    )
                    if not ready:
                        time.sleep(0.5)
                        continue
                    if not self.last_shows:
                        try:
                            self.wait_for(
                                "document.querySelectorAll('tr[data-testid=\"beast-core-table-body-tr\"]').length>0",
                                timeout=10,
                            )
                        except TimeoutError:
                            pass
                    shows = self.evaluate(
                        """[...document.querySelectorAll('tr[data-testid="beast-core-table-body-tr"]')]
                        .filter(r=>r.querySelector('[class*="goodsNumWrap"]')?.innerText.includes('编辑商品'))
                        .map(r=>({showId:r.querySelector('[data-show]')?.getAttribute('data-show'),
                        title:r.querySelector('[class*="title__"]')?.innerText,
                        time:r.querySelector('[class*="time__"]')?.innerText}))"""
                    )
                    if shows:
                        self.last_shows = shows
                    elif self.last_shows:
                        shows = self.last_shows
                    self._save_cookies()
                    return {"authenticated": True, "shows": shows or []}
                if path == "/login":
                    self.evaluate(
                        """[...document.querySelectorAll('li')]
                        .find(x=>x.textContent.trim()==='我是商家')?.click()"""
                    )
                    time.sleep(0.7)
                    continue
                if current.startswith("https://mms.pinduoduo.com/login/"):
                    qr_value = self._qr_value()
                    return {
                        "authenticated": False,
                        "qrImage": qr_png_data_url(qr_value) if qr_value else None,
                    }
                time.sleep(0.7)
            return {"authenticated": False, "qrImage": None}

    def query(self, goods_ids: list[int], show_id: str | None) -> dict:
        with self.lock:
            self.connect()
            self.call("Page.navigate", {"url": RECORD_URL})
            self.wait_for(
                "(location.pathname==='/n-creator/live/live-record' && "
                "document.readyState==='complete') || "
                "location.pathname==='/login' || location.hostname==='mms.pinduoduo.com'",
                timeout=20,
            )
            if urlparse(self.url()).path != "/n-creator/live/live-record":
                self.status()
                raise LoginRequired()
            self.wait_for("document.querySelectorAll('tr[data-testid=\"beast-core-table-body-tr\"]').length>0")
            shows = self.status()["shows"]
            if not shows:
                raise ValueError("没有可编辑商品的直播场次")
            selected = show_id or shows[0]["showId"]
            if selected not in {s["showId"] for s in shows}:
                raise ValueError("所选直播场次当前不可编辑商品")

            click_edit = """(()=>{let r=[...document.querySelectorAll('tr[data-testid="beast-core-table-body-tr"]')]
                .find(x=>x.querySelector('[data-show]')?.getAttribute('data-show')===__SHOW__);
                r?.querySelector('[class*="goodsNumWrap"] [class*="edit__"]')?.click()})()"""
            self.evaluate(click_edit.replace("__SHOW__", json.dumps(selected)))
            self.wait_for("[...document.querySelectorAll('button')].some(x=>x.textContent.trim()==='继续添加')")
            self.evaluate("[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='继续添加').click()")
            self.wait_for("[...document.querySelectorAll('[data-testid=\"beast-core-tab-itemLabel\"]')].some(x=>x.textContent.trim()==='商品ID')")
            self.evaluate("[...document.querySelectorAll('[data-testid=\"beast-core-tab-itemLabel\"]')].find(x=>x.textContent.trim()==='商品ID').click()")
            self.wait_for("!!document.querySelector('textarea[data-testid=\"beast-core-textArea-htmlInput\"]')")

            failures: dict[int, str] = {}
            remaining = goods_ids[:]
            for _ in range(3):
                self._fill_ids(remaining)
                self.evaluate("[...document.querySelectorAll('button')].find(x=>x.textContent.includes('下一步'))?.click()")
                remaining_json = json.dumps([str(gid) for gid in remaining])
                state_expression = """(()=>{let n=document.querySelectorAll('span[class*="confirm-goods-modal_id"]').length;
                    let t=document.body.innerText,e=t.slice(t.lastIndexOf('添加商品异常'));
                    return n?{confirmed:n}:t.includes('添加商品异常')&&__IDS__.some(id=>e.includes(id))?{error:e}:null})()"""
                state = self.wait_for(state_expression.replace("__IDS__", remaining_json), timeout=25)
                if "confirmed" in state:
                    goods = self._extract_goods()
                    break
                found = parse_failures(state["error"], remaining)
                if not found:
                    raise RuntimeError("页面返回商品异常，但未能解析失败 ID：" + state["error"][:300])
                failures.update(found)
                remaining = [gid for gid in remaining if gid not in failures]
                if not remaining:
                    goods = []
                    break
            else:
                raise RuntimeError("多次查询后仍有未解析的商品异常")

            returned = {g["goodsId"] for g in goods}
            for gid in goods_ids:
                if gid not in returned and gid not in failures:
                    failures[gid] = "接口未返回该商品"
            result = {
                "showId": selected,
                "requestedCount": len(goods_ids),
                "successCount": len(goods),
                "failureCount": len(failures),
                "goodsList": goods,
                "failures": [{"goodsId": gid, "reason": failures[gid]} for gid in goods_ids if gid in failures],
                "source": "browser",
            }
            self._save_cookies()
            try:
                self.call("Page.navigate", {"url": RECORD_URL})
            except (RuntimeError, TimeoutError):
                pass
            return result

    def _fill_ids(self, goods_ids: list[int]) -> None:
        value = ",".join(map(str, goods_ids))
        fill_expression = """(()=>{let e=document.querySelector('textarea[data-testid="beast-core-textArea-htmlInput"]');
            Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,__VALUE__);
            e.dispatchEvent(new Event('input',{bubbles:true}));return e.value})()"""
        self.evaluate(fill_expression.replace("__VALUE__", json.dumps(value)))
        self.wait_for(
            "document.body.innerText.includes('" + str(len(goods_ids)) + "/1000')"
        )

    def _extract_goods(self) -> list[dict]:
        return self.evaluate(
            """(()=>{let out=[];for(let s of document.querySelectorAll('span[class*="confirm-goods-modal_id"]')){
            let k=Object.keys(s).find(k=>k.startsWith('__reactFiber')),f=k&&s[k];
            while(f&&!f.memoizedProps?.record?.goodsId)f=f.return;
            if(f)out.push(f.memoizedProps.record)}return out})()"""
        ) or []

    def logout(self) -> dict:
        with self.lock:
            self.connect()
            try:
                self.evaluate("localStorage.clear();sessionStorage.clear();true")
            except RuntimeError:
                pass
            self.call("Network.clearBrowserCookies")
            self.last_shows = []
            self.cookie_snapshot = None
            COOKIE_FILE.unlink(missing_ok=True)
            self.call("Page.navigate", {"url": RECORD_URL})
            self.wait_for(
                "location.pathname==='/login' || location.hostname==='mms.pinduoduo.com'",
                timeout=12,
            )
            return {"loggedOut": True}

    def start_login(self) -> dict:
        with self.lock:
            self.connect()
            if urlparse(self.url()).path == "/n-creator/live/live-record":
                return self.status()
            self.call("Page.navigate", {"url": RECORD_URL})
            return self.status()


def parse_failures(text: str, requested: list[int]) -> dict[int, str]:
    requested_set = set(requested)
    result: dict[int, str] = {}
    for line in text.splitlines():
        if ":" not in line and "：" not in line:
            continue
        reason, values = re.split(r"[:：]", line, maxsplit=1)
        for token in re.findall(r"\b\d{10,20}\b", values):
            gid = int(token)
            if gid in requested_set:
                result[gid] = reason.strip()
    return result


def qr_png_data_url(value: str) -> str:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=9, border=4)
    qr.add_data(value)
    qr.make(fit=True)
    output = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def parse_ids(raw: str) -> list[int]:
    tokens = [part for part in re.split(r"[\s,;，；]+", raw.strip()) if part]
    if not tokens:
        raise ValueError("请至少输入 1 个商品 ID")
    if any(not re.fullmatch(r"\d{10,20}", token) for token in tokens):
        raise ValueError("商品 ID 只能包含 10 至 20 位数字，多个 ID 用逗号、空格或换行分隔")
    return list(dict.fromkeys(int(token) for token in tokens))
