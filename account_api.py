"""星渊账号侧接口：钱包额度、每日签到、兑换码与站点公告。

只负责跟 New API 的「账号/用户」接口打交道，日志流水仍旧交给 main.py。
所有请求都带 30 秒左右的短缓存，避免 WebUI 轮询把上游打爆。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger

LOG_TAG = "[SyuanMonitor]"

DATA_DIR = "/home/astrbot/data/data/plugin_data/astrbot_plugin_syuan_monitor"
STATE_FILE = os.path.join(DATA_DIR, "account_state.json")

#: 站点公告里的「神秘兑换码」形态：32 位十六进制
CODE_PAT = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])")
CODE_HINT = ("兑换码", "神秘", "口令", "code")


class SyuanAccount:
    """账号级接口客户端（钱包 / 签到 / 兑换）。"""

    def __init__(
        self,
        api_base: str = "",
        token: str = "",
        uid: str = "",
        key: str = "",
        ttl: float = 30.0,
        timeout: int = 15,
    ) -> None:
        self.api_base = (api_base or "").rstrip("/")
        self.token = (token or "").strip()
        self.uid = (uid or "").strip()
        self.key = (key or "").strip()
        self.ttl = max(3.0, float(ttl or 30.0))
        self.timeout = max(5, int(timeout or 15))
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._state: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------ 凭据更新

    def update_credentials(self, api_base: str = "", token: str = "", uid: str = "", key: str = "") -> None:
        """凭据变化时刷新，同时清掉旧缓存。"""
        self.api_base = (api_base or self.api_base).rstrip("/")
        self.token = (token or self.token).strip()
        self.uid = (uid or self.uid).strip()
        self.key = (key or self.key).strip()
        self._cache.clear()

    @property
    def available(self) -> bool:
        return bool(self.token or self.key)

    # ------------------------------------------------------------ 本地状态

    def _load_state(self) -> Dict[str, Any]:
        if self._state is not None:
            return self._state
        data: Dict[str, Any] = {"redeemed": {}, "checkin": {}}
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    data.update(raw)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"{LOG_TAG} [state] 读取状态文件失败: {exc!r}")
        data.setdefault("redeemed", {})
        data.setdefault("checkin", {})
        self._state = data
        return data

    def _save_state(self) -> None:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state or {}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, STATE_FILE)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"{LOG_TAG} [state] 写入状态文件失败: {exc!r}")

    def mark_redeemed(self, code: str, message: str = "") -> None:
        state = self._load_state()
        state["redeemed"][code] = {
            "at": int(time.time()),
            "message": message[:120],
        }
        self._save_state()

    def redeemed_map(self) -> Dict[str, Any]:
        return dict(self._load_state().get("redeemed") or {})

    def remember_checkin(self, quota_awarded: int, day: str = "") -> None:
        state = self._load_state()
        state["checkin"] = {
            "day": day or time.strftime("%Y-%m-%d"),
            "quota_awarded": int(quota_awarded or 0),
            "at": int(time.time()),
        }
        self._save_state()

    # ------------------------------------------------------------ 缓存

    def _cache_get(self, key: str, ttl: float) -> Any:
        hit = self._cache.get(key)
        if hit and (time.time() - hit[0]) < ttl:
            return hit[1]
        return None

    def _cache_put(self, key: str, value: Any) -> Any:
        self._cache[key] = (time.time(), value)
        return value

    def bust(self, *keys: str) -> None:
        if not keys:
            self._cache.clear()
            return
        for k in keys:
            self._cache.pop(k, None)

    # ------------------------------------------------------------ 底层请求

    def _request(
        self,
        path: str,
        method: str = "GET",
        payload: Optional[dict] = None,
        need_token: bool = True,
    ) -> Tuple[Optional[dict], str]:
        """返回 (响应体, 错误说明)。"""
        if not self.api_base:
            return None, "还没配置中转站地址"
        if need_token and not self.token:
            return None, "缺少账号访问令牌（access_token），钱包与签到接口需要它"

        headers = {
            "User-Agent": "Mozilla/5.0 (AstrBot SyuanMonitor)",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["New-Api-User"] = self.uid or "1"
        elif self.key:
            headers["Authorization"] = f"Bearer {self.key}"

        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.api_base}{path}", data=body, headers=headers, method=method.upper()
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "ignore")
            parsed = self._maybe_json(raw)
            msg = ""
            if isinstance(parsed, dict):
                msg = str(parsed.get("message") or "")
            return None, msg or f"HTTP {exc.code}"
        except Exception as exc:
            return None, repr(exc)[:200]

        parsed = self._maybe_json(raw)
        if not isinstance(parsed, dict):
            return None, "上游返回了无法解析的内容"
        return parsed, ""

    @staticmethod
    def _maybe_json(raw: str) -> Any:
        try:
            return json.loads(raw)
        except Exception:
            return None

    @staticmethod
    def _site_message(body: Optional[dict], default: str = "") -> str:
        if isinstance(body, dict):
            for key in ("message", "msg", "error"):
                value = body.get(key)
                if value:
                    return str(value)
        return default

    # ------------------------------------------------------------ 账号信息

    def user_self(self, force: bool = False) -> Dict[str, Any]:
        """账号概览：余额、已用、请求次数、邀请奖励等。"""
        if not force:
            hit = self._cache_get("self", self.ttl)
            if hit is not None:
                return hit

        body, err = self._request("/api/user/self")
        if err:
            stale = self._cache.get("self")
            if stale:
                return dict(stale[1], stale_error=err)
            return {"error": err}

        data = (body or {}).get("data") or {}
        if not isinstance(data, dict) or not data:
            return {"error": self._site_message(body, "账号信息为空")}

        info = {
            "id": data.get("id"),
            "username": data.get("username") or data.get("display_name") or "",
            "display_name": data.get("display_name") or "",
            "email": data.get("email") or "",
            "group": data.get("group") or "",
            "role": data.get("role"),
            "quota": int(data.get("quota") or 0),
            "used_quota": int(data.get("used_quota") or 0),
            "request_count": int(data.get("request_count") or 0),
            "aff_code": data.get("aff_code") or "",
            "aff_count": int(data.get("aff_count") or 0),
            "aff_quota": int(data.get("aff_quota") or 0),
            "aff_history_quota": int(data.get("aff_history_quota") or 0),
            "fetched_at": int(time.time()),
        }
        return self._cache_put("self", info)

    # ------------------------------------------------------------ 签到

    def checkin_status(self, force: bool = False) -> Dict[str, Any]:
        """签到状态：今天签没签、连签次数、最近记录。"""
        if not force:
            hit = self._cache_get("checkin", min(self.ttl, 20.0))
            if hit is not None:
                return hit

        body, err = self._request("/api/user/checkin")
        if err:
            stale = self._cache.get("checkin")
            if stale:
                return dict(stale[1], stale_error=err)
            return {"error": err, "enabled": False, "checked_in_today": False}

        data = (body or {}).get("data") or {}
        stats = data.get("stats") or {}
        records = []
        for item in stats.get("records") or []:
            records.append(
                {
                    "date": item.get("checkin_date") or "",
                    "quota": int(item.get("quota_awarded") or 0),
                }
            )
        info = {
            "enabled": bool(data.get("enabled")),
            "min_quota": int(data.get("min_quota") or 0),
            "max_quota": int(data.get("max_quota") or 0),
            "checked_in_today": bool(stats.get("checked_in_today")),
            "checkin_count": int(stats.get("checkin_count") or 0),
            "total_checkins": int(stats.get("total_checkins") or 0),
            "total_quota": int(stats.get("total_quota") or 0),
            "records": records,
            "fetched_at": int(time.time()),
        }
        return self._cache_put("checkin", info)

    def do_checkin(self) -> Dict[str, Any]:
        """执行签到。"""
        body, err = self._request("/api/user/checkin", method="POST", payload={})
        if err:
            return {"ok": False, "message": err}

        ok = bool((body or {}).get("success"))
        message = self._site_message(body, "签到成功" if ok else "签到失败")
        data = (body or {}).get("data") or {}
        quota = int((data or {}).get("quota_awarded") or 0)

        if not ok:
            status = self.checkin_status(force=True)
            if status.get("checked_in_today"):
                return {"ok": False, "already": True, "message": message or "今天已经签过啦"}
            return {"ok": False, "message": message}

        self.bust("self", "checkin")
        self.remember_checkin(quota, str((data or {}).get("checkin_date") or ""))
        return {
            "ok": True,
            "message": message or "签到成功",
            "quota_awarded": quota,
            "checkin_date": (data or {}).get("checkin_date") or "",
        }

    # ------------------------------------------------------------ 兑换码

    def redeem(self, code: str) -> Dict[str, Any]:
        """兑换码充值。"""
        key = (code or "").strip()
        if not key:
            return {"ok": False, "message": "先把兑换码填进来呀"}
        if key in self.redeemed_map():
            return {"ok": False, "used": True, "message": "这个码之前已经兑换过了"}

        body, err = self._request("/api/user/topup", method="POST", payload={"key": key})
        if err:
            return {"ok": False, "message": err, "code": key}

        ok = bool((body or {}).get("success"))
        message = self._site_message(body, "兑换成功" if ok else "兑换失败")
        if ok:
            self.bust("self")
            self.mark_redeemed(key, message)
            return {"ok": True, "message": message, "code": key, "data": (body or {}).get("data")}
        return {"ok": False, "message": message, "code": key}

    # ------------------------------------------------------------ 站点公告

    def site_status(self, force: bool = False) -> Dict[str, Any]:
        """站点公开状态：币种、额度换算、公告、签到开关。"""
        if not force:
            hit = self._cache_get("status", 300.0)
            if hit is not None:
                return hit

        body, err = self._request("/api/status", need_token=False)
        if err or not isinstance(body, dict):
            stale = self._cache.get("status")
            if stale:
                return dict(stale[1], stale_error=err)
            return {"error": err or "站点状态读取失败"}

        data = body.get("data") or {}
        if not isinstance(data, dict):
            data = {}

        announcements = []
        for item in data.get("announcements") or []:
            content = str(item.get("content") or "")
            announcements.append(
                {
                    "id": item.get("id"),
                    "type": item.get("type") or "",
                    "publish_time": item.get("publishDate") or "",
                    "content": content,
                    "codes": self._extract_codes(content),
                }
            )

        info = {
            "system_name": data.get("system_name") or "中转站",
            "logo": data.get("logo") or "",
            "checkin_enabled": bool(data.get("checkin_enabled")),
            "quota_per_unit": float(data.get("quota_per_unit") or 500000) or 500000.0,
            "currency_symbol": data.get("custom_currency_symbol") or "¥",
            "display_in_currency": bool(data.get("display_in_currency")),
            "usd_exchange_rate": float(data.get("usd_exchange_rate") or 1) or 1.0,
            "version": data.get("version") or "",
            "announcements_enabled": bool(data.get("announcements_enabled")),
            "announcements": announcements,
            "fetched_at": int(time.time()),
        }
        return self._cache_put("status", info)

    def _extract_codes(self, text: str) -> List[Dict[str, str]]:
        """从公告正文里挑出疑似兑换码。"""
        found: List[Dict[str, str]] = []
        if not text:
            return found
        redeemed = self.redeemed_map()
        lines = re.split(r"[\n\r]+", text)
        for line in lines:
            for match in CODE_PAT.findall(line):
                plain = line.strip().strip("*#` ")
                found.append(
                    {
                        "code": match,
                        "label": plain[:60] or "公告里的兑换码",
                        "redeemed": bool(match in redeemed),
                    }
                )
        # 兜底：整段文本里再兜一次（公告可能没有换行）
        if not found:
            for match in CODE_PAT.findall(text):
                found.append(
                    {
                        "code": match,
                        "label": "公告里的兑换码",
                        "redeemed": bool(match in redeemed),
                    }
                )
        seen = set()
        unique = []
        for item in found:
            if item["code"] in seen:
                continue
            seen.add(item["code"])
            unique.append(item)
        return unique

    def announcement_codes(self, force: bool = False, limit: int = 8) -> List[Dict[str, Any]]:
        """公告里抓到的兑换码（附公告时间），默认只给最近几条。"""
        status = self.site_status(force=force)
        if status.get("error"):
            return []
        items: List[Dict[str, Any]] = []
        for ann in status.get("announcements") or []:
            for code in ann.get("codes") or []:
                items.append(
                    {
                        "code": code.get("code"),
                        "label": code.get("label") or "",
                        "redeemed": bool(code.get("redeemed")),
                        "publish_time": ann.get("publish_time") or "",
                        "announcement_id": ann.get("id"),
                    }
                )
        return items[: max(1, int(limit or 8))]

    # ------------------------------------------------------------ 入账明细

    def online_topups(self, force: bool = False) -> Dict[str, Any]:
        """站内自助充值的订单（自己扫码付款那部分）。"""
        if not force:
            hit = self._cache_get("topups", self.ttl)
            if hit is not None:
                return hit

        body, err = self._request("/api/user/topup/self?p=0&page_size=100")
        if err:
            stale = self._cache.get("topups")
            if stale:
                return dict(stale[1], stale_error=err)
            return {"error": err, "items": []}

        data = (body or {}).get("data") or {}
        if not isinstance(data, dict):
            data = {}
        items: List[Dict[str, Any]] = []
        for raw in data.get("items") or []:
            if not isinstance(raw, dict):
                continue
            items.append(
                {
                    "id": raw.get("id"),
                    "amount": float(raw.get("amount") or 0),
                    "money": float(raw.get("money") or 0),
                    "trade_no": raw.get("trade_no") or "",
                    "payment_method": raw.get("payment_method") or "",
                    "payment_provider": raw.get("payment_provider") or "",
                    "status": raw.get("status") or "",
                    "create_time": int(raw.get("create_time") or 0),
                    "complete_time": int(raw.get("complete_time") or 0),
                }
            )
        info = {
            "items": items,
            "total": int(data.get("total") or len(items)),
            "fetched_at": int(time.time()),
        }
        return self._cache_put("topups", info)

    def self_logs(self, log_type: int, force: bool = False) -> Dict[str, Any]:
        """账号自己的日志：type=1 充值，type=4 系统（签到、邀请）。"""
        ck = f"logs:{int(log_type)}"
        if not force:
            hit = self._cache_get(ck, self.ttl)
            if hit is not None:
                return hit

        body, err = self._request(f"/api/log/self?type={int(log_type)}&p=0&page_size=100")
        if err:
            stale = self._cache.get(ck)
            if stale:
                return dict(stale[1], stale_error=err)
            return {"error": err, "items": []}

        data = (body or {}).get("data") or {}
        if not isinstance(data, dict):
            data = {}
        items: List[Dict[str, Any]] = []
        for raw in data.get("items") or []:
            if not isinstance(raw, dict):
                continue
            items.append(
                {
                    "id": raw.get("id"),
                    "type": int(raw.get("type") or log_type),
                    "content": str(raw.get("content") or ""),
                    "created_at": int(raw.get("created_at") or 0),
                }
            )
        info = {
            "items": items,
            "total": int(data.get("total") or len(items)),
            "fetched_at": int(time.time()),
        }
        return self._cache_put(ck, info)

    def log_stat(
        self, start_ts: float = 0.0, end_ts: float = 0.0, force: bool = False
    ) -> Dict[str, Any]:
        """上游用量汇总（/api/log/self/stat）：区间内的额度消耗与速率。

        返回 {quota, cache_rate, rpm, tpm}。传 [start_ts, end_ts) 秒级时间戳即可
        拿到该区间汇总；这是站点侧直接聚合好的结果，比本地扫流水快得多。
        """
        ck = f"stat:{int(start_ts)}:{int(end_ts)}"
        # 汇总口径不必高频刷新，缓存给到 3 倍基础 TTL，压低上游压力
        ttl = max(self.ttl, self.ttl * 3)
        if not force:
            hit = self._cache_get(ck, ttl)
            if hit is not None:
                return hit

        query = ""
        if start_ts:
            query += f"&start_timestamp={int(start_ts)}"
        if end_ts:
            query += f"&end_timestamp={int(end_ts)}"
        body, err = self._request(f"/api/log/self/stat?p=1&page_size=1{query}")
        if err:
            stale = self._cache.get(ck)
            if stale:
                return dict(stale[1], stale_error=err)
            return {"error": err, "quota": None}

        data = (body or {}).get("data") or {}
        if not isinstance(data, dict):
            data = {}
        info = {
            "quota": int(data.get("quota") or 0),
            "cache_rate": float(data.get("cache_rate") or 0.0),
            "rpm": int(data.get("rpm") or 0),
            "tpm": int(data.get("tpm") or 0),
            "fetched_at": int(time.time()),
        }
        return self._cache_put(ck, info)

    #: 入账来源 → 展示名
    SOURCE_LABEL = {
        "self": "自己充值",
        "code": "兑换码",
        "checkin": "每日签到",
        "aff": "邀请奖励",
        "other": "其它入账",
    }

    @staticmethod
    def _classify(content: str, log_type: int) -> str:
        text = content or ""
        if log_type == 1:
            return "code" if "兑换码" in text else "self"
        if log_type == 4:
            if "签到" in text:
                return "checkin"
            if "邀请" in text:
                return "aff"
        return "other"

    @staticmethod
    def _money_in_text(text: str) -> float:
        """从「... ¥12.5 额度...」里抠出站点货币金额。"""
        match = re.search(r"[¥￥]\s*([0-9]+(?:\.[0-9]+)?)", text or "")
        if not match:
            return 0.0
        try:
            return float(match.group(1))
        except ValueError:
            return 0.0

    @staticmethod
    def _method_label(method: str) -> str:
        table = {
            "wxpay": "微信支付",
            "alipay": "支付宝",
            "stripe": "Stripe",
            "creem": "Creem",
            "epay": "易支付",
            "usdt": "USDT",
        }
        key = (method or "").strip().lower()
        return table.get(key, method or "")

    def _local_codes(self) -> List[Tuple[int, str]]:
        """本地记下的兑换码（时间戳, 码），用来给上游日志补上明文。"""
        pairs = []
        for code, meta in (self.redeemed_map() or {}).items():
            if not isinstance(meta, dict):
                continue
            pairs.append((int(meta.get("at") or 0), str(code)))
        pairs.sort()
        return pairs

    def _match_local_code(self, at: int, used: set) -> str:
        """按时间就近匹配一枚本地兑换码，匹配不到就留空。"""
        best, best_gap = "", 1e18
        for ts, code in self._local_codes():
            if not ts or code in used:
                continue
            gap = abs(ts - at)
            if gap < best_gap:
                best, best_gap = code, gap
        if best and best_gap <= 600:
            used.add(best)
            return best
        return ""

    def ledger(self, force: bool = False) -> Dict[str, Any]:
        """把中转入账拆成「自己充值 / 兑换码 / 每日签到 / 邀请奖励」四路。"""
        status = self.site_status(force=force)
        if not isinstance(status, dict) or status.get("error"):
            status = {}
        per_unit = float(status.get("quota_per_unit") or 500000) or 500000.0

        rows: List[Dict[str, Any]] = []
        sources: Dict[str, Dict[str, Any]] = {
            key: {"label": label, "count": 0, "quota": 0, "money": 0.0, "paid": 0.0}
            for key, label in self.SOURCE_LABEL.items()
        }

        def add(row: Dict[str, Any]) -> None:
            rows.append(row)
            bucket = sources.setdefault(
                row["source"],
                {"label": row["source_label"], "count": 0, "quota": 0, "money": 0.0, "paid": 0.0},
            )
            bucket["count"] += 1
            bucket["quota"] += int(row.get("quota") or 0)
            bucket["money"] = round(float(bucket["money"]) + float(row.get("money") or 0), 6)
            bucket["paid"] = round(float(bucket["paid"]) + float(row.get("paid") or 0), 6)

        # ---- 1) 自己充值：站内自助下单（有实付金额与支付方式）
        topups = self.online_topups(force=force)
        for item in topups.get("items") or []:
            amount = float(item.get("amount") or 0)
            paid = float(item.get("money") or 0)
            at = int(item.get("complete_time") or item.get("create_time") or 0)
            parts = []
            method = self._method_label(item.get("payment_method") or "")
            if method:
                parts.append(method)
            if item.get("trade_no"):
                parts.append("订单 " + str(item["trade_no"])[:28])
            if item.get("status") and item["status"] != "success":
                parts.append(str(item["status"]))
            add(
                {
                    "source": "self",
                    "source_label": self.SOURCE_LABEL["self"],
                    "at": at,
                    "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else "--",
                    "quota": int(amount * per_unit),
                    "money": round(amount, 4),
                    "paid": round(paid, 4),
                    "code": "",
                    "detail": " · ".join(parts) or "站内在线充值",
                }
            )

        # ---- 2) 兑换码 / 自己充值：账号日志 type=1
        used_codes: set = set()
        code_logs = self.self_logs(1, force=force)
        for item in code_logs.get("items") or []:
            content = str(item.get("content") or "")
            kind = self._classify(content, 1)
            at = int(item.get("created_at") or 0)
            yuan = self._money_in_text(content)
            code = self._match_local_code(at, used_codes) if kind == "code" else ""
            label = self.SOURCE_LABEL.get(kind, self.SOURCE_LABEL["other"])
            if kind == "code":
                match_id = re.search(r"兑换码\s*ID\s*([0-9A-Za-z]+)", content)
                ident = match_id.group(1) if match_id else ""
                if code:
                    shown = code[:8] + "…" + code[-4:] if len(code) > 12 else code
                    detail = "兑换码 " + shown + (" · ID " + ident if ident else "")
                elif ident:
                    detail = "公告兑换码 · ID " + ident
                else:
                    detail = content.strip()[:60] or label
            else:
                detail = content.strip() or label
            add(
                {
                    "source": kind,
                    "source_label": label,
                    "at": at,
                    "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else "--",
                    "quota": int(round(yuan * per_unit)),
                    "money": round(yuan, 4),
                    "paid": 0.0,
                    "code": code,
                    "detail": detail,
                }
            )

        # ---- 3) 签到：站点签到接口更完整，日志只作为补充
        checkin = self.checkin_status(force=force)
        if not isinstance(checkin, dict):
            checkin = {}
        seen_days = set()
        for rec in checkin.get("records") or []:
            day = str(rec.get("date") or "")
            quota = int(rec.get("quota") or 0)
            if not day:
                continue
            seen_days.add(day)
            stamp = 0
            try:
                stamp = int(time.mktime(time.strptime(day, "%Y-%m-%d")))
            except Exception:
                stamp = 0
            add(
                {
                    "source": "checkin",
                    "source_label": self.SOURCE_LABEL["checkin"],
                    "at": stamp,
                    "time": day,
                    "quota": quota,
                    "money": round(quota / per_unit, 4),
                    "paid": 0.0,
                    "code": "",
                    "detail": "每日签到奖励",
                }
            )

        # ---- 4) 系统日志 type=4：签到（补齐）、邀请奖励
        for item in (self.self_logs(4, force=force).get("items") or []):
            content = str(item.get("content") or "")
            kind = self._classify(content, 4)
            at = int(item.get("created_at") or 0)
            if kind == "checkin":
                day = time.strftime("%Y-%m-%d", time.localtime(at)) if at else ""
                if day and day in seen_days:
                    continue
                seen_days.add(day)
            yuan = self._money_in_text(content)
            label = self.SOURCE_LABEL.get(kind, self.SOURCE_LABEL["other"])
            add(
                {
                    "source": kind,
                    "source_label": label,
                    "at": at,
                    "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else "--",
                    "quota": int(round(yuan * per_unit)),
                    "money": round(yuan, 4),
                    "paid": 0.0,
                    "code": "",
                    "detail": content.strip() or label,
                }
            )

        rows.sort(key=lambda r: (r.get("at") or 0), reverse=True)
        summary = {
            "count": len(rows),
            "quota": sum(int(r.get("quota") or 0) for r in rows),
            "money": round(sum(float(r.get("money") or 0) for r in rows), 6),
            "paid": round(sum(float(r.get("paid") or 0) for r in rows), 6),
            "by_source": sources,
            "currency_symbol": str(status.get("currency_symbol") or "¥"),
            "quota_per_unit": per_unit,
            "fetched_at": int(time.time()),
        }
        return {"items": rows, "summary": summary, "error": (topups.get("stale_error") or "")}
