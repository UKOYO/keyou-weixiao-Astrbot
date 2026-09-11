"""Syuan API Monitor Plugin for AstrBot

星渊账本：消费/延迟/工具统计 + 实时报错流水。
数据来源（按优先级自动降级）：
  1) 账号访问令牌 -> /api/log/   （账号全量，可看到所有令牌的报错）
  2) 账号访问令牌 -> /api/log/self
  3) 星渊 API Key  -> /api/log/token（仅能看到该令牌自己的流水）
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image as Img, Plain
from astrbot.api.star import Context, Star, register
from astrbot.api.web import error_response, json_response
from astrbot.api.web import request as plugin_request

try:
    from .renderer import render_journal_card
except ImportError:
    from renderer import render_journal_card

try:
    from .account_api import SyuanAccount
except ImportError:
    from account_api import SyuanAccount

PLUGIN_NAME = "astrbot_plugin_syuan_monitor"
API_PREFIX = f"/{PLUGIN_NAME}/api"
LOG_TAG = "[SyuanMonitor]"

CMD_CONFIG = "/home/astrbot/data/data/cmd_config.json"
LOCAL_LOG = "/home/astrbot/data/data/logs/astrbot.log"

#: New API 日志 type：1 充值 / 2 消费 / 3 管理 / 4 系统 / 5 错误 / 6 退款 / 7 登录
TYPE_TOPUP = 1
TYPE_CONSUME = 2
TYPE_MANAGE = 3
TYPE_SYSTEM = 4
TYPE_ERROR = 5
TYPE_REFUND = 6
TYPE_LOGIN = 7

#: 与站点「使用日志」下拉一致的顺序与中文名
LOG_TYPE_NAMES = {
    0: "所有类型",
    1: "充值",
    2: "消耗",
    3: "管理",
    4: "系统",
    5: "错误",
    6: "退款",
    7: "登录",
}
LOG_TYPE_OPTIONS = [{"value": k, "label": v} for k, v in LOG_TYPE_NAMES.items()]

#: 站点「使用日志」的快捷时间范围
RANGE_PRESETS = [
    {"value": "today", "label": "今天"},
    {"value": "7d", "label": "7天"},
    {"value": "week", "label": "本周"},
    {"value": "30d", "label": "30天"},
    {"value": "month", "label": "本月获得"},
]

#: 花费换算：New API quota -> 站点显示的额度（默认 500000 quota = ¥1）
QUOTA_PER_USD = 500000.0

#: 单页拉取上限（上游把 page_size 夹到 100，多出来的靠翻页补齐）
UPSTREAM_PAGE_CAP = 100

CODE_PAT = re.compile(r"status_code=(\d{3})")

#: 工具/插件中文归属映射表 (含主要关联驱动/决策 LLM)
TOOL_PLUGIN_MAP = {
    "astrbot_execute_shell": ("系统执行 (Shell终端)", "AstrBot核心", "gemini-3.8-flash"),
    "astrbot_execute_python": ("Python代码执行", "AstrBot核心", "gemini-3.8-flash"),
    "astrbot_shell_session": ("后台终端会话管理", "AstrBot核心", "gemini-3.8-flash"),
    "astrbot_file_read_tool": ("文件读取查看", "文件与工作区", "gemini-3.8-flash"),
    "astrbot_file_write_tool": ("文件写入创建", "文件与工作区", "gemini-3.8-flash"),
    "astrbot_file_edit_tool": ("代码与文本编辑", "文件与工作区", "gemini-3.8-flash"),
    "astrbot_grep_tool": ("代码文件正则检索", "文件与工作区", "gemini-3.8-flash"),
    "NAI_Generate_Image": ("NovelAI 图像生成", "NovelAI画图", "gemini-3.8-flash / NAI 4.5"),
    "anysearch_search": ("联网实时搜索", "AnySearch聚合搜索", "gemini-3.8-flash"),
    "anysearch_batch_search": ("批量联网检索", "AnySearch聚合搜索", "gemini-3.8-flash"),
    "anysearch_extract": ("网页正文深度提取", "AnySearch聚合搜索", "gemini-3.8-flash"),
    "web_search_tavily": ("Tavily 网页搜索", "Tavily深度搜索", "gemini-3.8-flash"),
    "tavily_extract_web_page": ("Tavily 页面提取", "Tavily深度搜索", "gemini-3.8-flash"),
    "luoqi_mobile_lookup": ("洛奇Mobile游戏资料库", "洛奇助手", "gemini-3.8-flash"),
    "memory_companion_remember": ("长期记忆写入", "记忆伴侣", "gemini-3.8-flash / bge-m3"),
    "memory_companion_recall": ("长期记忆主动回忆", "记忆伴侣", "gemini-3.8-flash / bge-m3"),
    "memory_companion_core_memory": ("核心记忆库管理", "记忆伴侣", "gemini-3.8-flash"),
    "memory_companion_note_create": ("陪伴备忘笔记", "记忆伴侣", "gemini-3.8-flash"),
    "find_music": ("音乐检索定位", "听歌点歌助手", "gemini-3.8-flash"),
    "deliver_music": ("音乐语音直发", "听歌点歌助手", "gemini-3.8-flash"),
    "search_music": ("音乐交互点歌", "听歌点歌助手", "gemini-3.8-flash"),
    "future_task": ("定时与未来任务流", "未来任务计划", "gemini-3.8-flash"),
    "send_message_to_user": ("富媒体主动投递", "AstrBot核心", "gemini-3.8-flash"),
    "llm_box_user": ("QQ名片资料查询", "名片盒Box", "gemini-3.8-flash"),
    "search_meme": ("表情包检索", "MemeLite表情", "gemini-3.8-flash"),
    "send_meme": ("表情包发送", "MemeLite表情", "gemini-3.8-flash"),
    "steal_meme": ("表情自动入库偷图", "表情包小偷", "gemini-3.7-flash (VLM)"),
}

#: 状态码 -> 中文归因
CODE_KIND = {
    400: "请求内容不合法",
    401: "鉴权失败",
    403: "无权限 / 被风控",
    404: "模型或端点不存在",
    413: "请求体过大",
    422: "参数校验失败",
    429: "限流 / 额度耗尽",
    499: "客户端主动断开",
    500: "上游内部错误",
    502: "上游网关异常",
    503: "上游无可用渠道",
    504: "上游超时",
}

#: 关键词 -> 归因（优先于状态码通用归因）
KEYWORD_KIND = (
    ("all accounts", "渠道下所有账号不可用"),
    ("no available", "渠道下无可用账号"),
    ("insufficient", "额度不足"),
    ("quota", "额度不足"),
    ("rate limit", "触发限流"),
    ("timeout", "上游超时"),
    ("timed out", "上游超时"),
    ("context length", "上下文超长"),
    ("maximum context", "上下文超长"),
    ("unsupported", "请求内容不被支持"),
    ("connection", "连接上游失败"),
    ("overload", "上游过载"),
)


def _error_kind(code: int, content: str) -> str:
    low = (content or "").lower()
    for kw, kind in KEYWORD_KIND:
        if kw in low:
            return kind
    if code in CODE_KIND:
        return CODE_KIND[code]
    if code:
        return f"HTTP {code}"
    return "未知错误"


def _extract_list(payload: Any) -> Optional[List[dict]]:
    """兼容 New API 各种 data 包装形态。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "records", "logs", "list", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
    return None


def _cut(text: str, limit: int = 220) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


@register(
    "astrbot_plugin_syuan_monitor",
    "唯笑",
    "监控星渊 API 消费额度、调用分布、耗时与实时报错流水，附工具手账卡片",
    "1.2.0",
)
class SyuanMonitorPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.api_base = str(self.config.get("api_base") or "https://api.syuan.org").rstrip("/")
        self.target_user = "2157195950"
        self.log_file = LOCAL_LOG
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._alert_seen: set = set()
        self._last_alert_at = 0.0
        self._tasks: List[asyncio.Task] = []
        self.account = SyuanAccount(
            api_base=self.api_base,
            token=str(self.config.get("access_token") or "").strip(),
            uid=str(self.config.get("user_id") or "").strip(),
            key=str(self.config.get("api_key") or "").strip(),
        )
        try:
            loop = asyncio.get_running_loop()
            self._tasks.append(loop.create_task(self._start_daily_cron()))
            self._tasks.append(loop.create_task(self._watch_errors()))
            self._tasks.append(loop.create_task(self._start_auto_checkin()))
        except RuntimeError:
            pass

    # ------------------------------------------------------------ 生命周期

    async def initialize(self):
        """注册 WebUI 所需的 Web API 路由"""
        routes = [
            ("/overview", self._api_overview, "Syuan 账本：总览统计"),
            ("/models", self._api_models, "Syuan 账本：模型维度统计"),
            ("/tools", self._api_tools, "Syuan 账本：插件与工具调用统计"),
            ("/logs", self._api_logs, "Syuan 账本：最近调用流水"),
            ("/errors", self._api_errors, "Syuan 账本：报错流水（支持筛选）"),
            ("/errors/summary", self._api_errors_summary, "Syuan 账本：报错聚合概览"),
            ("/live", self._api_live, "Syuan 账本：实时刷新轻量接口"),
            ("/card", self._api_card, "Syuan 账本：手账卡片渲染"),
            ("/wallet", self._api_wallet, "Syuan 账本：钱包、账号与签到概览"),
        ]
        for path, handler, desc in routes:
            try:
                self.context.register_web_api(f"{API_PREFIX}{path}", handler, ["GET"], desc)
            except Exception as exc:  # pragma: no cover
                logger.warning(f"{LOG_TAG} [init] 注册 {path} 失败: {exc!r}")
        key, token, uid = self._resolve_credentials()
        self.account.update_credentials(
            api_base=self.api_base, token=token, uid=uid, key=key
        )
        scope = "账号全量" if token else ("令牌级" if key else "未配置")
        logger.info(f"{LOG_TAG} [init] WebUI 就绪 | 数据范围={scope} | prefix={API_PREFIX}")

    async def terminate(self):
        for task in self._tasks:
            if task and not task.done():
                task.cancel()
        logger.info(f"{LOG_TAG} [terminate] 监控插件已卸载")

    # ------------------------------------------------------------ 凭据与请求

    def _cmd_config(self) -> dict:
        try:
            with open(CMD_CONFIG, encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning(f"{LOG_TAG} 读取 cmd_config.json 失败: {exc!r}")
            return {}

    def _resolve_credentials(self) -> Tuple[str, str, str]:
        """返回 (sk_key, access_token, user_id)"""
        key = str(self.config.get("api_key") or "").strip()
        token = str(self.config.get("access_token") or "").strip()
        uid = str(self.config.get("user_id") or "").strip()
        if not key:
            for src in self._cmd_config().get("provider_sources", []):
                if "syuan" in str(src.get("api_base", "")).lower():
                    kv = src.get("key")
                    if isinstance(kv, list) and kv:
                        key = str(kv[0])
                        break
                    if isinstance(kv, str) and kv:
                        key = kv
                        break
        return key, token, uid

    def _get_api_key(self) -> str:
        return self._resolve_credentials()[0]

    @staticmethod
    def _http_json(url: str, headers: Dict[str, str], timeout: int = 15) -> Any:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _fetch_logs_raw(
        self,
        limit: int,
        start_ts: float = 0.0,
        end_ts: float = 0.0,
    ) -> Tuple[List[dict], str, str]:
        """按可用凭据拉取日志，返回 (logs, scope, error_message)

        传入 start_ts / end_ts（秒级时间戳）时只取该闭开区间 [start, end) 内的记录。
        上游 New API 的日志接口原生支持 start_timestamp / end_timestamp，
        这里另外在本地按 created_at 兜底过滤一次，防止上游忽略参数。
        """
        key, token, uid = self._resolve_credentials()
        base_headers = {"User-Agent": "Mozilla/5.0 (AstrBot SyuanMonitor)"}
        last_err = ""

        window = ""
        if start_ts:
            window += f"&start_timestamp={int(start_ts)}"
        if end_ts:
            window += f"&end_timestamp={int(end_ts)}"

        want = max(1, int(limit or UPSTREAM_PAGE_CAP))
        page_size = min(want, UPSTREAM_PAGE_CAP)
        max_pages = max(1, min(80, (want + page_size - 1) // page_size + 2))

        def _paged(base_url: str, headers: Dict[str, str]):
            """逐页补齐：上游 p 为 1 起的页码，单页最多 UPSTREAM_PAGE_CAP 条，靠翻页凑够 want 条。

            只读中转站、不落盘。返回 None 表示这一路凭据不可用。
            """
            collected: List[dict] = []
            seen = set()
            for page in range(1, max_pages + 1):
                payload = self._http_json(
                    f"{base_url}?p={page}&page_size={page_size}{window}", headers
                )
                batch = _extract_list(payload)
                if batch is None:
                    return None
                if not batch:
                    break
                fresh = 0
                for item in batch:
                    if not isinstance(item, dict):
                        continue
                    sig = item.get("id") or (
                        item.get("created_at"),
                        item.get("model_name"),
                        item.get("quota"),
                    )
                    if sig in seen:
                        continue
                    seen.add(sig)
                    collected.append(item)
                    fresh += 1
                if fresh == 0:
                    break
                if len(batch) < page_size:
                    break
                if start_ts:
                    oldest = min(
                        (b.get("created_at") or 0) for b in batch if isinstance(b, dict)
                    )
                    if oldest and oldest < int(start_ts):
                        break
                if len(collected) >= want:
                    break
            return collected[:want]

        def _clip(logs: List[dict]) -> List[dict]:
            if not start_ts and not end_ts:
                return logs
            out: List[dict] = []
            for item in logs:
                ts = item.get("created_at") or 0
                if start_ts and ts < start_ts:
                    continue
                if end_ts and ts >= end_ts:
                    continue
                out.append(item)
            return out

        if token:
            headers = dict(base_headers)
            headers["Authorization"] = f"Bearer {token}"
            headers["New-Api-User"] = uid or "1"
            for path in ("/api/log/", "/api/log/self"):
                try:
                    logs = _paged(f"{self.api_base}{path}", headers)
                    if logs is not None:
                        return _clip(logs), "账号全量", ""
                except Exception as exc:
                    last_err = f"{path} -> {exc!r}"

        if key:
            headers = dict(base_headers)
            headers["Authorization"] = f"Bearer {key}"
            try:
                logs = _paged(f"{self.api_base}/api/log/token", headers)
                if logs is not None:
                    return _clip(logs), "令牌级", ""
            except Exception as exc:
                last_err = f"/api/log/token -> {exc!r}"

        return [], "无", last_err or "未配置可用的 API Key / 访问令牌"

    # ------------------------------------------------------------ 分析

    def _parse_error(self, log: dict) -> Dict[str, Any]:
        content = str(log.get("content") or "")
        match = CODE_PAT.search(content)
        code = int(match.group(1)) if match else 0
        return {
            "id": log.get("id"),
            "created_at": log.get("created_at", 0),
            "model": log.get("model_name") or "unknown",
            "token": log.get("token_name") or "-",
            "channel": log.get("channel"),
            "channel_name": log.get("channel_name") or "",
            "group": log.get("group") or "",
            "code": code,
            "kind": _error_kind(code, content),
            "detail": _cut(content, 400),
            "use_time": log.get("use_time", 0),
            "is_stream": bool(log.get("is_stream")),
            "request_id": log.get("request_id") or "",
        }

    @staticmethod
    def _rank(counter: Dict[str, int], extra: Optional[Dict[str, dict]] = None) -> List[dict]:
        items = []
        for name, count in counter.items():
            row = {"name": name, "count": count}
            if extra and name in extra:
                row.update(extra[name])
            items.append(row)
        items.sort(key=lambda x: x["count"], reverse=True)
        return items

    def _analyze(
        self, logs: List[dict], scope: str, err: str, period: str = ""
    ) -> Dict[str, Any]:
        consumed = [l for l in logs if l.get("type") == TYPE_CONSUME]
        errors = [l for l in logs if l.get("type") == TYPE_ERROR]

        total_calls = len(consumed) + len(errors)
        total_quota = sum(l.get("quota", 0) or 0 for l in consumed)
        lat_sum = sum(l.get("use_time", 0) or 0 for l in consumed)
        avg_latency = (lat_sum / len(consumed)) if consumed else 0.0

        model_stats: Dict[str, Dict[str, Any]] = {}

        def slot(name: str) -> Dict[str, Any]:
            if name not in model_stats:
                model_stats[name] = {
                    "calls": 0,
                    "quota": 0,
                    "latency_sum": 0,
                    "latency": 0.0,
                    "errors": 0,
                }
            return model_stats[name]

        for log in consumed:
            stat = slot(log.get("model_name") or "unknown")
            stat["calls"] += 1
            stat["quota"] += log.get("quota", 0) or 0
            stat["latency_sum"] += log.get("use_time", 0) or 0

        error_items = [self._parse_error(l) for l in errors]
        error_items.sort(key=lambda x: x.get("created_at", 0), reverse=True)

        for item in error_items:
            slot(item["model"])["errors"] += 1

        for stat in model_stats.values():
            calls = stat["calls"] or 0
            stat["latency"] = (stat["latency_sum"] / calls) if calls else 0.0

        # ---- 报错聚合
        now = time.time()
        by_model: Dict[str, int] = {}
        by_token: Dict[str, int] = {}
        by_channel: Dict[str, int] = {}
        by_code: Dict[str, int] = {}
        hourly = [0] * 24
        last_hour = 0
        last_10min = 0
        for item in error_items:
            by_model[item["model"]] = by_model.get(item["model"], 0) + 1
            by_token[item["token"]] = by_token.get(item["token"], 0) + 1
            ch = item["channel_name"] or (f"渠道 {item['channel']}" if item["channel"] else "未知渠道")
            by_channel[ch] = by_channel.get(ch, 0) + 1
            code_key = f"HTTP {item['code']}" if item["code"] else "无状态码"
            by_code[code_key] = by_code.get(code_key, 0) + 1
            age = now - (item["created_at"] or 0)
            if 0 <= age < 3600:
                last_hour += 1
                hourly[time.localtime(item["created_at"]).tm_hour] += 1
            if 0 <= age < 600:
                last_10min += 1

        top_messages: Dict[str, int] = {}
        for item in error_items:
            key = _cut(re.sub(r"\d{2}:\d{2}:\d{2}", "", item["detail"]), 120)
            top_messages[key] = top_messages.get(key, 0) + 1

        recent_error = error_items[0] if error_items else None

        summary = {
            "total": len(error_items),
            "last_hour": last_hour,
            "last_10min": last_10min,
            "error_rate": round((len(error_items) / total_calls * 100), 2) if total_calls else 0.0,
            "latest": recent_error,
        }

        return {
            "scope": scope,
            "period": period or "实时近况",
            "fetch_error": err,
            "fetched_at": now,
            "total_calls": total_calls,
            "success_calls": len(consumed),
            "error_calls": len(error_items),
            "total_quota": total_quota,
            "avg_latency": avg_latency,
            "model_stats": model_stats,
            "tool_stats": self._parse_local_tool_stats(),
            "recent_logs": logs[:10],
            "all_logs": logs,
            "error_stats": {
                "summary": summary,
                "items": error_items,
                "by_model": self._rank(by_model),
                "by_token": self._rank(by_token),
                "by_channel": self._rank(by_channel),
                "by_code": self._rank(by_code),
                "hourly": hourly,
                "top_messages": self._rank(top_messages)[:6],
            },
        }

    def _collect(
        self,
        force: bool = False,
        start_ts: float = 0.0,
        end_ts: float = 0.0,
        period: str = "",
        limit: int = 0,
    ) -> Dict[str, Any]:
        """带短 TTL 缓存的报告，供轮询式实时刷新使用。

        不传时间窗时取最近 page_size 条流水（实时面板用）；
        传入 [start_ts, end_ts) 时只统计该区间，日报用它锁定“昨天全天”。
        """
        page = int(limit or self.config.get("log_page_size") or 1000)
        ttl = float(self.config.get("refresh_seconds") or 6)
        cache_key = (
            "report"
            if not start_ts and not end_ts
            else f"report:{int(start_ts)}:{int(end_ts)}"
        )
        hit = self._cache.get(cache_key)
        if not force and hit and (time.time() - hit[0]) < ttl:
            return hit[1]
        logs, scope, err = self._fetch_logs_raw(page, start_ts, end_ts)
        if not logs and hit:
            # 拉取失败时短暂回退到上一次快照，避免页面整片空白
            stale = dict(hit[1])
            stale["fetch_error"] = err
            self._cache[cache_key] = (time.time(), stale)
            return stale
        report = self._analyze(logs, scope, err, period)
        self._cache[cache_key] = (time.time(), report)
        return report

    @staticmethod
    def _day_window(offset_days: int = 1) -> Tuple[float, float, str]:
        """返回 (start_ts, end_ts, 中文标签) 的整日区间，默认“昨天”。

        offset_days=1 -> 昨天 00:00:00 到 今天 00:00:00（左闭右开）。
        标签形如「昨日 09-10 全天」。
        """
        lt = time.localtime()
        today_zero = time.mktime(
            (lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)
        )
        start = today_zero - offset_days * 86400
        end = start + 86400
        st = time.localtime(start)
        return start, end, f"昨日 {st.tm_mon:02d}-{st.tm_mday:02d} 全天"

    def _fetch_data(self) -> Dict[str, Any]:
        return self._collect()

    # ------------------------------------------------------------ 本地工具日志

    def _parse_local_tool_stats(self) -> Dict[str, Dict[str, Any]]:
        """从 AstrBot 本地日志分析各工具被调用的频次与 Token/花费估算"""
        stats: Dict[str, Dict[str, Any]] = {}
        if not os.path.exists(self.log_file):
            return stats

        try:
            with open(self.log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            recent_lines = lines[-5000:]
            tool_call_pat = re.compile(r"Agent 使用工具:\s*\[(.*?)\]")

            for line in recent_lines:
                match = tool_call_pat.search(line)
                if not match:
                    continue
                tools = re.findall(r"['\"]([a-zA-Z0-9_\-]+)['\"]", match.group(1))
                for tool in tools:
                    c_name, p_name, llm_name = TOOL_PLUGIN_MAP.get(
                        tool, (tool, "扩展插件", "gemini-3.8-flash")
                    )
                    key_name = f"{p_name} · {c_name}"
                    if key_name not in stats:
                        stats[key_name] = {
                            "calls": 0,
                            "tokens": 0,
                            "cost": 0.0,
                            "raw_tool": tool,
                            "plugin": p_name,
                            "llm": llm_name,
                        }
                    stats[key_name]["calls"] += 1
                    stats[key_name]["tokens"] += 2800
                    stats[key_name]["cost"] += (2800 / 1000000.0) * 0.15
        except Exception as e:
            logger.warning(f"{LOG_TAG} 解析本地工具日志失败: {e}")
        return stats

    # ------------------------------------------------------------ Web API

    @staticmethod
    def _q(name: str, default: str = "") -> str:
        try:
            return str(plugin_request.query.get(name, default) or default)
        except Exception:
            return default

    @staticmethod
    def _q_int(name: str, default: int) -> int:
        try:
            return int(float(SyuanMonitorPlugin._q(name, str(default))))
        except Exception:
            return default

    def _window_from_query(self) -> Tuple[float, float, str, int]:
        """解析 WebUI 的 range 参数，返回 (start_ts, end_ts, period, page_size)

        live（默认）: 最近 log_page_size 条实时流水，不锁时间；
        yesterday  : 昨天 00:00:00 ~ 今天 00:00:00（与 06:00 日报同源）；
        today      : 今天 00:00:00 ~ 此刻。
        """
        rng = (self._q("range", "live") or "live").strip().lower()
        base_page = int(self.config.get("log_page_size") or 1000)
        daily_page = int(self.config.get("daily_page_size") or 3000)

        if rng in ("yesterday", "yest", "prev", "昨天"):
            start, end, label = self._day_window(1)
            return start, end, label, daily_page

        if rng in ("today", "今天"):
            lt = time.localtime()
            start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
            st = time.localtime(start)
            return start, time.time(), f"今日 {st.tm_mon:02d}-{st.tm_mday:02d} 全天", daily_page

        return 0.0, 0.0, "实时近况", base_page

    async def _report_or_error(self, tag: str):
        try:
            start, end, period, page = self._window_from_query()
            return (
                await asyncio.to_thread(self._collect, False, start, end, period, page),
                None,
            )
        except Exception as exc:
            logger.warning(f"{LOG_TAG} [{tag}] 失败: {exc!r}")
            return None, error_response("拉取中转站数据失败", status_code=502)

    async def _api_wallet(self) -> Any:
        """钱包：余额 / 已用 / 请求次数 / 邀请奖励 + 签到状态（都带折算金额）"""
        info = await asyncio.to_thread(self.account.user_self, False)
        if not isinstance(info, dict) or info.get("error"):
            return error_response(
                str((info or {}).get("error") or "读取账号信息失败"), status_code=502
            )

        status = await asyncio.to_thread(self.account.site_status)
        if not isinstance(status, dict) or status.get("error"):
            status = {}
        per_unit = float(status.get("quota_per_unit") or QUOTA_PER_USD) or QUOTA_PER_USD
        symbol = str(status.get("currency_symbol") or "¥")

        def money(quota: Any) -> float:
            return round(int(quota or 0) / per_unit, 4)

        checkin = await asyncio.to_thread(self.account.checkin_status, False)
        if not isinstance(checkin, dict):
            checkin = {}

        records = [
            {
                "date": str(item.get("date") or ""),
                "quota": int(item.get("quota") or 0),
                "money": money(item.get("quota")),
            }
            for item in (checkin.get("records") or [])[:7]
        ]

        ledger = await asyncio.to_thread(self.account.ledger, False)
        if not isinstance(ledger, dict):
            ledger = {"items": [], "summary": {}}

        return json_response(
            {
                "status": "ok",
                "site_name": status.get("system_name") or "星渊",
                "username": info.get("username") or "",
                "display_name": info.get("display_name") or "",
                "group": info.get("group") or "",
                "role": info.get("role"),
                "quota": int(info.get("quota") or 0),
                "quota_money": money(info.get("quota")),
                "used_quota": int(info.get("used_quota") or 0),
                "used_money": money(info.get("used_quota")),
                "request_count": int(info.get("request_count") or 0),
                "aff_count": int(info.get("aff_count") or 0),
                "aff_quota": int(info.get("aff_quota") or 0),
                "aff_money": money(info.get("aff_quota")),
                "aff_history_quota": int(info.get("aff_history_quota") or 0),
                "currency_symbol": symbol,
                "quota_per_unit": per_unit,
                "display_in_currency": bool(status.get("display_in_currency")),
                "checkin_enabled": bool(checkin.get("enabled")),
                "checked_in_today": bool(checkin.get("checked_in_today")),
                "checkin_count": int(checkin.get("checkin_count") or 0),
                "total_checkins": int(checkin.get("total_checkins") or 0),
                "checkin_total_quota": int(checkin.get("total_quota") or 0),
                "checkin_total_money": money(checkin.get("total_quota")),
                "checkin_records": records,
                "ledger": ledger.get("items") or [],
                "ledger_summary": ledger.get("summary") or {},
                "ledger_error": ledger.get("error") or "",
                "stale_error": info.get("stale_error") or "",
                "fetched_at": int(info.get("fetched_at") or time.time()),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    async def _api_overview(self) -> Any:
        """总览：总调用、花费、平均延迟、报错数与数据范围"""
        data, err = await self._report_or_error("overview")
        if err:
            return err
        err_sum = data["error_stats"]["summary"]
        return json_response(
            {
                "status": "ok",
                "scope": data.get("scope"),
                "period": data.get("period"),
                "fetch_error": data.get("fetch_error") or "",
                "total_calls": data.get("total_calls", 0),
                "success_calls": data.get("success_calls", 0),
                "error_calls": data.get("error_calls", 0),
                "error_rate": err_sum.get("error_rate", 0.0),
                "error_last_hour": err_sum.get("last_hour", 0),
                "total_cost": round(data.get("total_quota", 0) / QUOTA_PER_USD, 6),
                "avg_latency": round(data.get("avg_latency", 0.0), 3),
                "model_count": len(data.get("model_stats", {})),
                "tool_count": len(data.get("tool_stats", {})),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    async def _api_models(self) -> Any:
        """各模型的调用次数、花费、平均延迟与报错数"""
        data, err = await self._report_or_error("models")
        if err:
            return err
        items = []
        for name, stat in data.get("model_stats", {}).items():
            items.append(
                {
                    "model": name,
                    "calls": stat.get("calls", 0),
                    "errors": stat.get("errors", 0),
                    "cost": round(stat.get("quota", 0) / QUOTA_PER_USD, 6),
                    "avg_latency": round(stat.get("latency", 0.0), 3),
                }
            )
        items.sort(key=lambda x: x["calls"] + x["errors"], reverse=True)
        return json_response(
            {
                "status": "ok",
                "scope": data.get("scope"),
                "period": data.get("period"),
                "items": items,
            }
        )

    async def _api_tools(self) -> Any:
        """插件与工具的调用次数、Token 估算、花费与驱动 LLM"""
        try:
            stats = await asyncio.to_thread(self._parse_local_tool_stats)
        except Exception as exc:
            logger.warning(f"{LOG_TAG} [tools] 失败: {exc!r}")
            return error_response("解析本地日志失败", status_code=500)

        items = []
        for key, stat in stats.items():
            items.append(
                {
                    "name": key,
                    "plugin": stat.get("plugin", ""),
                    "tool": stat.get("raw_tool", ""),
                    "llm": stat.get("llm", ""),
                    "calls": stat.get("calls", 0),
                    "tokens": stat.get("tokens", 0),
                    "cost": round(stat.get("cost", 0.0), 6),
                }
            )
        items.sort(key=lambda x: x["calls"], reverse=True)
        return json_response({"status": "ok", "items": items})

    async def _api_logs(self) -> Any:
        """最近的调用流水明细（含成功/失败标记）"""
        data, err = await self._report_or_error("logs")
        if err:
            return err
        limit = max(1, min(500, self._q_int("limit", 80)))
        items = []
        for log in data.get("all_logs", [])[:limit]:
            is_error = log.get("type") == TYPE_ERROR
            content = str(log.get("content") or "")
            match = CODE_PAT.search(content)
            items.append(
                {
                    "model": log.get("model_name", "unknown"),
                    "token": log.get("token_name", "-"),
                    "channel": log.get("channel"),
                    "group": log.get("group") or "",
                    "type": log.get("type"),
                    "status": "error" if is_error else "ok",
                    "code": int(match.group(1)) if match else 0,
                    "detail": _cut(content, 200) if is_error else "",
                    "created_at": log.get("created_at", 0),
                    "use_time": log.get("use_time", 0),
                    "is_stream": bool(log.get("is_stream")),
                    "cost": round((log.get("quota", 0) or 0) / QUOTA_PER_USD, 6),
                    "prompt_tokens": log.get("prompt_tokens", 0),
                    "completion_tokens": log.get("completion_tokens", 0),
                }
            )
        return json_response(
            {
                "status": "ok",
                "scope": data.get("scope"),
                "period": data.get("period"),
                "items": items,
            }
        )

    async def _api_errors(self) -> Any:
        """报错流水：支持 model / token / code / keyword / hours / limit 筛选"""
        data, err = await self._report_or_error("errors")
        if err:
            return err

        stats = data["error_stats"]
        items = list(stats["items"])

        model = self._q("model")
        token = self._q("token")
        code = self._q("code")
        keyword = self._q("keyword").lower()
        hours = self._q_int("hours", 0)
        limit = max(1, min(1000, self._q_int("limit", 300)))

        if model:
            items = [i for i in items if i["model"] == model]
        if token:
            items = [i for i in items if i["token"] == token]
        if code:
            items = [i for i in items if str(i["code"]) == code]
        if keyword:
            items = [
                i
                for i in items
                if keyword in (i["detail"] + " " + i["kind"] + " " + i["model"]).lower()
            ]
        if hours > 0:
            floor = time.time() - hours * 3600
            items = [i for i in items if (i["created_at"] or 0) >= floor]

        return json_response(
            {
                "status": "ok",
                "scope": data.get("scope"),
                "period": data.get("period"),
                "fetch_error": data.get("fetch_error") or "",
                "total": len(items),
                "total_all": len(stats["items"]),
                "summary": stats["summary"],
                "by_model": stats["by_model"],
                "by_token": stats["by_token"],
                "by_channel": stats["by_channel"],
                "by_code": stats["by_code"],
                "hourly": stats["hourly"],
                "top_messages": stats["top_messages"],
                "items": items[:limit],
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    async def _api_errors_summary(self) -> Any:
        """仅返回报错聚合结果，供角标与告警使用"""
        data, err = await self._report_or_error("errors/summary")
        if err:
            return err
        stats = data["error_stats"]
        return json_response(
            {
                "status": "ok",
                "scope": data.get("scope"),
                "period": data.get("period"),
                "summary": stats["summary"],
                "by_model": stats["by_model"],
                "by_code": stats["by_code"],
                "hourly": stats["hourly"],
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    async def _api_live(self) -> Any:
        """实时刷新用轻量接口：总览 + 报错 + 最近流水"""
        data, err = await self._report_or_error("live")
        if err:
            return err
        stats = data["error_stats"]
        recent = []
        for log in data.get("all_logs", [])[:25]:
            is_error = log.get("type") == TYPE_ERROR
            content = str(log.get("content") or "")
            match = CODE_PAT.search(content)
            recent.append(
                {
                    "created_at": log.get("created_at", 0),
                    "model": log.get("model_name", "unknown"),
                    "token": log.get("token_name", "-"),
                    "status": "error" if is_error else "ok",
                    "code": int(match.group(1)) if match else 0,
                    "detail": _cut(content, 160) if is_error else "",
                    "use_time": log.get("use_time", 0),
                    "cost": round((log.get("quota", 0) or 0) / QUOTA_PER_USD, 6),
                }
            )
        return json_response(
            {
                "status": "ok",
                "scope": data.get("scope"),
                "period": data.get("period"),
                "fetch_error": data.get("fetch_error") or "",
                "total_calls": data.get("total_calls", 0),
                "success_calls": data.get("success_calls", 0),
                "error_calls": data.get("error_calls", 0),
                "error_rate": stats["summary"].get("error_rate", 0.0),
                "error_last_hour": stats["summary"].get("last_hour", 0),
                "total_cost": round(data.get("total_quota", 0) / QUOTA_PER_USD, 6),
                "avg_latency": round(data.get("avg_latency", 0.0), 3),
                "latest_errors": stats["items"][:20],
                "recent": recent,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    async def _api_card(self) -> Any:
        """返回手账卡片 PNG 的 base64 data URL，供 WebUI 直接展示"""
        try:
            start, end, period, page = self._window_from_query()
            data = await asyncio.to_thread(self._collect, False, start, end, period, page)
            card_bytes = await asyncio.to_thread(render_journal_card, data)
        except Exception as exc:
            logger.warning(f"{LOG_TAG} [card] 渲染失败: {exc!r}")
            return error_response("渲染手账卡片失败", status_code=500)

        b64 = base64.b64encode(card_bytes).decode("ascii")
        return json_response({"status": "ok", "data_url": f"data:image/png;base64,{b64}"})

    # ------------------------------------------------------------ 指令

    def _error_text_report(self, data: Dict[str, Any], top: int = 6) -> str:
        stats = data.get("error_stats", {})
        items = stats.get("items", [])
        summary = stats.get("summary", {})
        if not items:
            return (
                "Oha~ 大魔王把流水翻了一遍，最近这批日志里干干净净的，一条报错都没有哦。\n"
                f"（数据范围：{data.get('scope')}）"
            )

        lines = [
            f"【星渊报错速报】数据范围：{data.get('scope')}",
            f"近 {data.get('total_calls', 0)} 条调用里报错 {len(items)} 条"
            f"（占比 {summary.get('error_rate', 0)}%，近一小时 {summary.get('last_hour', 0)} 条）",
        ]
        codes = "、".join(f"{i['name']} ×{i['count']}" for i in stats.get("by_code", [])[:4])
        if codes:
            lines.append(f"状态码：{codes}")
        models = "、".join(f"{i['name']} ×{i['count']}" for i in stats.get("by_model", [])[:4])
        if models:
            lines.append(f"受影响模型：{models}")
        tokens = "、".join(f"{i['name']} ×{i['count']}" for i in stats.get("by_token", [])[:4])
        if tokens:
            lines.append(f"涉及令牌：{tokens}")
        lines.append("— 最新几条 —")
        for item in items[:top]:
            ts = time.strftime("%m-%d %H:%M:%S", time.localtime(item.get("created_at") or time.time()))
            lines.append(
                f"· {ts} | {item['token']} | {item['model']} | {item['kind']}\n  {item['detail'][:110]}"
            )
        return "\n".join(lines)

    @filter.command("api账本", "api统计", "api监控")
    async def cmd_api_monitor(self, event: AstrMessageEvent):
        """查看最近的 API 调用消费、报错与工具统计手账卡片"""
        data = await asyncio.to_thread(self._collect, True)
        if not data or not data.get("all_logs"):
            yield event.plain_result(
                "唔……大魔王去翻账本的时候没能连上中转站，稍后再试一下好不好？"
            )
            return
        card_bytes = await asyncio.to_thread(render_journal_card, data)
        yield event.chain_result([Img.fromBytes(card_bytes)])

    @filter.command("api报错", "api错误", "api故障")
    async def cmd_api_errors(self, event: AstrMessageEvent):
        """单独查看中转站报错流水（含状态码归因）"""
        data = await asyncio.to_thread(self._collect, True)
        if not data:
            yield event.plain_result("唔……报错日志这次没能拉下来，等一下再问问大魔王好不好？")
            return
        yield event.plain_result(self._error_text_report(data))

    # ------------------------------------------------------------ 签到与兑换

    def _checkin_text(self, info: Dict[str, Any]) -> str:
        if info.get("error"):
            return (
                "唔……大魔王敲中转站的门没敲开，签到状态没读到呢。" + chr(10)
                + f"（原因：{info['error']}）" + chr(10)
                + "看看账号访问令牌和用户 ID 有没有填对呀~"
            )
        if not info.get("enabled"):
            return "星渊站点那边的签到开关现在是关着的哦，先等等看它什么时候开~"
        lines = []
        if info.get("checked_in_today"):
            lines.append("Oha~ 今天已经签过啦，大魔王替你盯着呢。")
        else:
            lines.append("今天还是没签的状态哦，说一声「签到」大魔王立刻帮你补上！")
        lines.append(
            f"连签 {info.get('checkin_count', 0)} 天 | 累计 {info.get('total_checkins', 0)} 次"
            f" | 累计到手 {int(info.get('total_quota') or 0):,} 额度"
        )
        records = info.get("records") or []
        if records:
            recent = "、".join(
                f"{str(r.get('date') or '')[5:]} +{int(r.get('quota') or 0):,}" for r in records[:5]
            )
            lines.append(f"最近记录：{recent}")
        return chr(10).join(lines)

    @filter.command("签到状态", "查询签到")
    async def cmd_checkin_status(self, event: AstrMessageEvent):
        """查看星渊中转站的每日签到状态"""
        info = await asyncio.to_thread(self.account.checkin_status, True)
        yield event.plain_result(self._checkin_text(info))

    @filter.command("签到", "星渊签到")
    async def cmd_checkin(self, event: AstrMessageEvent):
        """手动执行一次星渊签到"""
        res = await asyncio.to_thread(self.account.do_checkin)
        if res.get("ok"):
            quota = int(res.get("quota_awarded") or 0)
            extra = await self._amount_extra(quota)
            yield event.plain_result(
                f"Okie-dokie！签到成功啦，到手 {quota:,} 额度{extra}~"
                f"（{res.get('checkin_date') or '今天'}）往后的日子也交给大魔王盯着 (๑˃ᴗ˂๑)"
            )
            return
        if res.get("already"):
            yield event.plain_result(f"今天已经签过啦，别贪心嘛~ {res.get('message') or ''}")
            return
        yield event.plain_result(f"唔……这次没签上：{res.get('message') or '原因不明'}")

    @staticmethod
    def _pick_redeem_amount(res) -> float:
        """尽量从兑换响应里抠出到账额度数字。"""
        payload = res.get("data")
        if isinstance(payload, dict):
            candidates = [
                payload.get(k) for k in ("quota", "amount", "add", "added", "value", "topup")
            ]
        else:
            candidates = [payload]
        candidates.append(res.get("message"))
        for item in candidates:
            if item is None or isinstance(item, bool):
                continue
            if isinstance(item, (int, float)):
                if item > 0:
                    return float(item)
                continue
            matched = re.search(r"\d[\d,]*(?:\.\d+)?", str(item))
            if matched:
                value = float(matched.group().replace(",", ""))
                if value > 0:
                    return value
        return 0.0

    async def _amount_extra(self, amount) -> str:
        """把额度换算成「，折合大概 ¥x」，读不到站点换算系数就返回空串。"""
        try:
            amount = float(amount or 0)
        except (TypeError, ValueError):
            return ""
        if amount <= 0:
            return ""
        symbol = "¥"
        per_unit = QUOTA_PER_USD
        try:
            status = await asyncio.to_thread(self.account.site_status)
            if isinstance(status, dict) and not status.get("error"):
                per_unit = float(status.get("quota_per_unit") or QUOTA_PER_USD) or QUOTA_PER_USD
                symbol = str(status.get("currency_symbol") or "¥")
        except Exception:
            pass
        return f"，折合大概 {symbol}{amount / per_unit:.2f}"

    async def _redeem_amount_text(self, res) -> str:
        """把到账额度换算成「额度 + 大概多少钱」，抠不出来就返回空串。"""
        amount = self._pick_redeem_amount(res)
        if not amount:
            return ""
        extra = await self._amount_extra(amount)
        return f"到手 {int(amount):,} 额度{extra}"

    @filter.command("兑换码", "兑换")
    async def cmd_redeem(self, event: AstrMessageEvent):
        """兑换码充值：兑换码 <码>；不带码时列出公告里捡到的码"""
        raw = (event.message_str or "").strip()
        parts = re.split(r"\s+", raw, maxsplit=1)
        code = parts[1].strip() if len(parts) > 1 else ""
        if not code:
            codes = await asyncio.to_thread(self.account.announcement_codes, True, 8)
            if not codes:
                yield event.plain_result(
                    "这条指令要带码用哦：兑换码 <码>" + chr(10) + "公告里目前也没捡到新码呢~"
                )
                return
            lines = ["公告里捡到这些码，大魔王直接帮你兑掉也行："]
            for item in codes:
                flag = "（已兑过）" if item.get("redeemed") else ""
                lines.append(f"· {str(item.get('code'))[:14]}… {flag}")
            yield event.plain_result(chr(10).join(lines))
            return
        res = await asyncio.to_thread(self.account.redeem, code)
        if res.get("ok"):
            detail = await self._redeem_amount_text(res)
            line = "兑换成功啦！"
            if detail:
                line += detail + "，已经进账 "
            else:
                line += f"{res.get('message') or ''} 额度已经进账 "
            yield event.plain_result(line + "(๑˃̵ᴗ˂̵)و")
            return
        if res.get("used"):
            yield event.plain_result("这个码大魔王账本上有记录，之前已经兑过啦~")
            return
        yield event.plain_result(f"唔……没兑上：{res.get('message') or '码可能过期或已被用过'}")

    @filter.command("一键兑换", "捡公告码")
    async def cmd_redeem_announcement(self, event: AstrMessageEvent):
        """把站点公告里捡到的兑换码逐个试兑，已兑过的自动跳过"""
        codes = await asyncio.to_thread(self.account.announcement_codes, True, 10)
        pending = [c for c in codes if not c.get("redeemed")]
        if not pending:
            yield event.plain_result("公告里没有新码啦，都是兑过的、或者还没发出来~")
            return
        ok_list: List[str] = []
        fail_list: List[str] = []
        for item in pending:
            res = await asyncio.to_thread(self.account.redeem, str(item.get("code") or ""))
            if res.get("ok"):
                ok_list.append(str(item.get("code"))[:12] + "…")
            else:
                fail_list.append(f"{str(item.get('code'))[:12]}…（{res.get('message') or '未通过'}）")
        lines = [f"大魔王把公告翻了一遍，试了 {len(pending)} 个码："]
        if ok_list:
            lines.append("· 到手：" + "、".join(ok_list))
        if fail_list:
            lines.append("· 没通过：" + "、".join(fail_list[:5]))
        yield event.plain_result(chr(10).join(lines))

    # ------------------------------------------------------------ 后台任务

    def _auto_checkin_enabled(self) -> bool:
        raw = self.config.get("auto_checkin_enabled", True)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() not in ("false", "0", "no", "off")

    def _auto_checkin_minutes(self) -> int:
        """自动签到时刻（当日分钟数），默认 00:05"""
        raw = str(self.config.get("auto_checkin_time") or "00:05").strip()
        match = re.match(r"^(\d{1,2})\s*[:：]\s*(\d{1,2})$", raw)
        if not match:
            return 5
        hour = min(23, max(0, int(match.group(1))))
        minute = min(59, max(0, int(match.group(2))))
        return hour * 60 + minute

    async def _start_auto_checkin(self):
        """每天到点替 keyou 自动签到，成功才私聊播报一句"""
        if not self._auto_checkin_enabled():
            logger.info(f"{LOG_TAG} 自动签到未开启（auto_checkin_enabled=false）")
            return
        target_minutes = self._auto_checkin_minutes()
        logger.info(
            f"{LOG_TAG} 自动签到已武装：每天 {target_minutes // 60:02d}:{target_minutes % 60:02d}"
        )
        while True:
            try:
                now = time.localtime()
                cur_sec = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
                target_sec = target_minutes * 60
                if cur_sec < target_sec:
                    sleep_sec = target_sec - cur_sec
                else:
                    sleep_sec = (24 * 3600) - cur_sec + target_sec
                await asyncio.sleep(max(30, sleep_sec))

                if not self.account.available:
                    logger.info(f"{LOG_TAG} 自动签到跳过：账号令牌尚未配置")
                    continue

                res = await asyncio.to_thread(self.account.do_checkin)
                if res.get("ok"):
                    quota = int(res.get("quota_awarded") or 0)
                    extra = await self._amount_extra(quota)
                    text = (
                        f"Oha~！自动签到完成啦，{quota:,} 额度已经到手{extra}"
                        f"（{res.get('checkin_date') or '今天'}）。大魔王会一直帮你盯着 (๑˃ᴗ˂๑)"
                    )
                    session_id = f"aiocqhttp:FriendMessage:{self.target_user}"
                    await self.context.send_message(session_id, [Plain(text)])
                    logger.info(f"{LOG_TAG} 自动签到成功：+{res.get('quota_awarded')}")
                elif res.get("already"):
                    logger.info(f"{LOG_TAG} 自动签到：今天站点那边已经签过了")
                else:
                    logger.info(f"{LOG_TAG} 自动签到未成功：{res.get('message')}")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"{LOG_TAG} 自动签到任务异常: {exc!r}")
                await asyncio.sleep(600)

    async def _wallet_line(self) -> str:
        """日报里的一行余额：折算金额 + 原始额度。"""
        try:
            info = await asyncio.to_thread(self.account.user_self, True)
        except Exception as exc:
            logger.warning(f"{LOG_TAG} [daily] 读取余额失败: {exc!r}")
            return ""
        if not isinstance(info, dict) or info.get("error"):
            return ""
        try:
            status = await asyncio.to_thread(self.account.site_status)
        except Exception:
            status = {}
        if not isinstance(status, dict):
            status = {}
        per_unit = float(status.get("quota_per_unit") or QUOTA_PER_USD) or QUOTA_PER_USD
        symbol = str(status.get("currency_symbol") or "¥")
        quota = int(info.get("quota") or 0)
        return f"当前余额：{symbol}{quota / per_unit:.2f}（{quota:,} 额度）"

    async def _start_daily_cron(self):
        """每天早上 06:00 定时推送给 keyou"""
        while True:
            try:
                now = time.localtime()
                cur_seconds = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
                target_seconds = 6 * 3600
                if cur_seconds < target_seconds:
                    sleep_sec = target_seconds - cur_seconds
                else:
                    sleep_sec = (24 * 3600) - cur_seconds + target_seconds

                logger.info(f"{LOG_TAG} 下次 06:00 日报将在 {sleep_sec} 秒后触发")
                await asyncio.sleep(sleep_sec)

                start_ts, end_ts, label = self._day_window(1)
                data = await asyncio.to_thread(
                    self._collect,
                    True,
                    start_ts,
                    end_ts,
                    label,
                    int(self.config.get("daily_page_size") or 3000),
                )
                balance_line = await self._wallet_line()
                if data and data.get("all_logs"):
                    data["flow_rows"] = int(self.config.get("daily_flow_rows") or 0)
                    card_bytes = await asyncio.to_thread(render_journal_card, data)
                    session_id = f"aiocqhttp:FriendMessage:{self.target_user}"
                    await self.context.send_message(
                        session_id,
                        [
                            Plain(
                                f"Oha~！优可，{label}的 API 与工具手账整理好啦，快来看看~ (๑˃̵ᴗ˂̵)و\n"
                                + balance_line
                            ),
                            Img.fromBytes(card_bytes),
                        ],
                    )
                elif balance_line:
                    session_id = f"aiocqhttp:FriendMessage:{self.target_user}"
                    await self.context.send_message(
                        session_id,
                        [
                            Plain(
                                f"Oha~！优可，{label}没有新的流水，大魔王只报一下余额~\n"
                                + balance_line
                            )
                        ],
                    )
                else:
                    logger.info(f"{LOG_TAG} {label} 窗口内没有流水，跳过本次推送")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"{LOG_TAG} 日报定时任务异常: {e}")
                await asyncio.sleep(60)

    async def _watch_errors(self):
        """报错尖峰巡检：默认每 5 分钟看一次，短时间内报错过多才提醒（30 分钟冷却）"""
        if self.config.get("alert_enabled") is True or str(
            self.config.get("alert_enabled", "false")
        ).lower() == "true":
            pass
        else:
            logger.info(f"{LOG_TAG} 报错尖峰提醒未开启（alert_enabled=false）")
            return

        interval = max(60, int(self.config.get("alert_interval_seconds") or 300))
        threshold = max(1, int(self.config.get("alert_threshold") or 10))
        cooldown = max(300, int(self.config.get("alert_cooldown_seconds") or 1800))

        while True:
            try:
                await asyncio.sleep(interval)
                data = await asyncio.to_thread(self._collect, True)
                stats = (data or {}).get("error_stats", {})
                burst = stats.get("summary", {}).get("last_10min", 0)
                if burst < threshold:
                    continue
                if time.time() - self._last_alert_at < cooldown:
                    continue
                self._last_alert_at = time.time()
                session_id = f"aiocqhttp:FriendMessage:{self.target_user}"
                await self.context.send_message(
                    session_id,
                    [
                        Plain(
                            f"优可……星渊那边在冒烟了！近十分钟里有 {burst} 条报错，"
                            f"大魔王帮你抓了最新几条，要不要看一眼？（.api报错）\n"
                        ),
                        Plain(self._error_text_report(data, top=3)),
                    ],
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"{LOG_TAG} 报错巡检异常: {e}")
                await asyncio.sleep(60)
