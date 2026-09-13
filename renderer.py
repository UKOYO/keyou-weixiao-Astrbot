"""Blender 工作区风格渲染器 — 星渊账本卡片

深灰视口 + 橙色高亮 + 倒角立体面板，标题两端点缀四叶草。
"""

from __future__ import annotations

import io
import os
import time
from typing import Any, Dict, List

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = "/home/astrbot/.meme_generator/resources/fonts"
FONT_PATH = f"{FONT_DIR}/NotoSansSC-Bold.ttf"
REG_FONT_PATH = f"{FONT_DIR}/NotoSansSC-Regular.ttf"
EMOJI_FONT_PATH = f"{FONT_DIR}/NotoColorEmoji.ttf"

if not os.path.exists(REG_FONT_PATH):
    REG_FONT_PATH = FONT_PATH

# ------------------------------------------------------------------ 调色板

# 双主题调色板：dark = 原有 Blender 深灰视口；light = 冷白灰（最亮基准 #e1e1e1）
# 最暗基准 #333333 在白昼版里转为正文字色，保证对比度。
THEMES: Dict[str, Dict[str, tuple]] = {
    "dark": {
        "BG": (38, 38, 38), "BG_GRID": (45, 45, 45),
        "HEADER_TOP": (58, 58, 58), "HEADER_BOT": (44, 44, 44),
        "PANEL": (53, 53, 53), "PANEL_TOP": (60, 60, 60),
        "PANEL_LINE": (20, 20, 20), "PANEL_HI": (74, 74, 74), "SHADOW": (26, 26, 26),
        "INK": (232, 232, 232), "INK_MUTED": (154, 154, 154), "INK_FAINT": (108, 108, 108),
        "TITLE_INK": (242, 242, 242), "VALUE_INK": (244, 244, 244),
        "CHIP_BG": (31, 31, 31), "CHIP_INK": (198, 198, 198),
        "FLOW_C1": (150, 150, 150), "FLOW_C2": (70, 70, 70),
        "ORANGE": (232, 125, 13), "ORANGE_LT": (255, 159, 67),
        "BLUE": (71, 114, 179), "BLUE_LT": (107, 150, 216),
        "GREEN": (124, 184, 95), "GREEN_LT": (150, 208, 130),
        "RED": (217, 83, 79), "RED_LT": (232, 118, 111),
        "YELLOW": (224, 169, 46), "YELLOW_LT": (242, 193, 78),
        "CORAL": (226, 114, 102),
    },
    "light": {
        "BG": (225, 225, 225), "BG_GRID": (214, 214, 214),
        "HEADER_TOP": (245, 245, 245), "HEADER_BOT": (233, 233, 233),
        "PANEL": (242, 242, 242), "PANEL_TOP": (250, 250, 250),
        "PANEL_LINE": (198, 198, 198), "PANEL_HI": (255, 255, 255), "SHADOW": (204, 204, 204),
        "INK": (51, 51, 51), "INK_MUTED": (108, 108, 108), "INK_FAINT": (146, 146, 146),
        "TITLE_INK": (44, 44, 44), "VALUE_INK": (34, 34, 34),
        "CHIP_BG": (230, 230, 230), "CHIP_INK": (72, 72, 72),
        "FLOW_C1": (152, 152, 152), "FLOW_C2": (108, 108, 108),
        "ORANGE": (206, 104, 8), "ORANGE_LT": (224, 128, 34),
        "BLUE": (54, 92, 152), "BLUE_LT": (78, 118, 178),
        "GREEN": (84, 142, 62), "GREEN_LT": (96, 158, 72),
        "RED": (190, 62, 58), "RED_LT": (182, 58, 54),
        "YELLOW": (170, 124, 20), "YELLOW_LT": (168, 120, 18),
        "CORAL": (196, 88, 76),
    },
}

#: 当前生效主题，apply_theme() 会把它同步进模块全局
CURRENT_THEME = "dark"

# 全局色名占位，import 后由 apply_theme() 填充
BG = BG_GRID = HEADER_TOP = HEADER_BOT = PANEL = PANEL_TOP = PANEL_LINE = PANEL_HI = SHADOW = (0, 0, 0)
INK = INK_MUTED = INK_FAINT = TITLE_INK = VALUE_INK = CHIP_BG = CHIP_INK = (0, 0, 0)
FLOW_C1 = FLOW_C2 = ORANGE = ORANGE_LT = BLUE = BLUE_LT = (0, 0, 0)
GREEN = GREEN_LT = RED = RED_LT = YELLOW = YELLOW_LT = CORAL = (0, 0, 0)


def normalize_theme(name: Any) -> str:
    """把配置里各种写法归一成 dark / light。"""
    text = str(name or "").strip().lower()
    if text in ("light", "day", "white", "bright", "白昼", "白色", "亮色", "浅色", "1", "true"):
        return "light"
    return "dark"


def apply_theme(name: Any) -> str:
    """切换调色板。返回实际生效的主题名。"""
    global CURRENT_THEME
    key = normalize_theme(name)
    g = globals()
    for k, v in THEMES[key].items():
        g[k] = tuple(v)
    g["CURRENT_THEME"] = key
    return key


apply_theme("dark")

_EMOJI_CACHE: Dict[tuple, Image.Image] = {}


# ------------------------------------------------------------------ 工具函数

def _emoji(ch: str, size: int) -> Image.Image:
    """把彩色 emoji 渲染成目标尺寸的 RGBA 图（NotoColorEmoji 仅支持 109px 位图）。"""
    key = (ch, size)
    hit = _EMOJI_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        font = ImageFont.truetype(EMOJI_FONT_PATH, 109)
        tmp = Image.new("RGBA", (150, 150), (0, 0, 0, 0))
        ImageDraw.Draw(tmp).text((12, 12), ch, font=font, embedded_color=True)
        box = tmp.getbbox()
        if box:
            tmp = tmp.crop(box)
        tmp = tmp.resize((size, size), Image.LANCZOS)
    except Exception:
        tmp = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    _EMOJI_CACHE[key] = tmp
    return tmp


def _get_font(size: int, bold: bool = False):
    path = FONT_PATH if bold else REG_FONT_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        try:
            return ImageFont.truetype(FONT_PATH, size)
        except Exception:
            return ImageFont.load_default()


def _fit(draw, text: str, font, max_w: int) -> str:
    """按像素宽度裁剪文本，超出部分用省略号收尾。"""
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return ""
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _tail(draw, text: str, font, min_x: int, right_x: int, tail_w: int = 0):
    """把右对齐文字压进 [min_x, right_x]，返回 (文本, 起始 x)。

    tail_w > 0 时额外预留固定宽度（用于右侧还有图标/印章的场景）。
    """
    text = (text or "").replace("\n", " ").strip()
    limit = max(40, right_x - min_x - tail_w)
    if draw.textlength(text, font=font) > limit:
        text = _fit(draw, text, font, limit)
    return text, right_x - draw.textlength(text, font=font)


def _left_fit(draw, text: str, font, x_left: int, right_x: int, gap: int = 16, min_w: int = 80) -> str:
    """左侧文字按「到 right_x 为止」的剩余宽度裁剪，绝不压到右侧内容。"""
    return _fit(draw, text, font, max(min_w, right_x - gap - x_left))


def _bevel_panel(draw, box, radius: int = 6, fill=None, top=None, line=None, hi=None):
    fill = PANEL if fill is None else fill
    top = PANEL_TOP if top is None else top
    line = PANEL_LINE if line is None else line
    hi = PANEL_HI if hi is None else hi
    """画一块带倒角高光的立体面板。"""
    x0, y0, x1, y1 = box
    # 落影
    draw.rounded_rectangle([(x0 + 1, y0 + 2), (x1 + 1, y1 + 2)], radius=radius, fill=SHADOW)
    # 主体（上亮下暗，模拟受光）
    draw.rounded_rectangle(box, radius=radius, fill=fill)
    draw.rectangle([(x0 + radius, y0 + 1), (x1 - radius, y0 + max(3, int((y1 - y0) * 0.42)))], fill=top)
    # 描边 + 顶部倒角高光
    draw.rounded_rectangle(box, radius=radius, outline=line, width=1)
    draw.line([(x0 + radius, y0 + 1), (x1 - radius, y0 + 1)], fill=hi, width=1)


def _accent_bar(draw, x: int, y0: int, y1: int, c1, c2, w: int = 5, radius: int = 3):
    """左侧强调竖条（渐变色带）。"""
    h = max(1, y1 - y0)
    for i in range(h):
        t = i / h
        col = tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3))
        draw.line([(x, y0 + i), (x + w - 1, y0 + i)], fill=col)


# ------------------------------------------------------------------ 主渲染

def render_journal_card(data: Dict[str, Any], theme: Any = None) -> bytes:
    # 主题优先取入参，没传就沿用当前生效主题（由 main.py 按插件配置设定）
    apply_theme(theme if theme is not None else CURRENT_THEME)
    width = 900
    tool_stats = data.get("tool_stats", {}) or {}
    model_stats = data.get("model_stats", {}) or {}
    recent_logs = data.get("recent_logs", []) or []
    all_logs = list(data.get("all_logs") or []) or list(recent_logs)
    try:
        raw_rows = data.get("flow_rows")
        flow_rows = 5 if raw_rows is None else max(0, int(raw_rows))
    except Exception:
        flow_rows = 5
    shown_logs = all_logs[:flow_rows] if flow_rows > 0 else []
    flow_total = len(all_logs) or len(recent_logs)
    flow_overflow = bool(shown_logs) and flow_total > len(shown_logs)
    error_items = (data.get("error_stats", {}) or {}).get("items", []) or []

    err_lines = min(3, len(error_items)) if error_items else 1
    tool_h = 58
    height = (
        128                                   # 标题栏
        + 92                                  # 四张统计卡
        + 44 + err_lines * 36 + 18            # 报错速览
        + 40 + max(1, len(tool_stats)) * tool_h   # 工具明细
        + 40 + max(1, len(model_stats)) * 38      # 模型分布
        + ((40 + max(1, len(shown_logs)) * 46 + (24 if flow_overflow else 0)) if flow_rows > 0 else 0)
        + 58                                  # 页脚
    )

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    # ---- 视口网格背景
    for gx in range(0, width, 26):
        draw.line([(gx, 0), (gx, height)], fill=BG_GRID, width=1)
    for gy in range(0, height, 26):
        draw.line([(0, gy), (width, gy)], fill=BG_GRID, width=1)
    # 外框
    draw.rectangle([(0, 0), (width - 1, height - 1)], outline=PANEL_LINE, width=2)

    f_title = _get_font(25, bold=True)
    f_sub = _get_font(13)
    f_body = _get_font(14)
    f_bold = _get_font(15, bold=True)
    f_small = _get_font(12)
    f_tiny = _get_font(11)
    f_mono = _get_font(11)

    # ---- 标题栏
    draw.rectangle([(2, 2), (width - 3, 92)], fill=HEADER_BOT)
    draw.rectangle([(2, 2), (width - 3, 52)], fill=HEADER_TOP)
    draw.rectangle([(2, 2), (6, 92)], fill=ORANGE)
    draw.line([(2, 92), (width - 3, 92)], fill=PANEL_LINE, width=1)

    title_text = "大魔王的 API 工具与消费手账"
    leaf = 26
    tw = draw.textlength(title_text, font=f_title)
    gap = 14
    total = tw + (leaf + gap) * 2
    tx = (width - total) / 2
    ty = 22
    img.paste(_emoji("🍀", leaf), (int(tx), int(ty + 1)), _emoji("🍀", leaf))
    draw.text((tx + leaf + gap, ty), title_text, fill=TITLE_INK, font=f_title)
    img.paste(_emoji("🍀", leaf), (int(tx + leaf + gap + tw + gap), int(ty + 1)), _emoji("🍀", leaf))

    period = data.get("period") or "实时近况"
    sub = f"账单周期: {period}   |   专属恋人: keyou   |   更新: {time.strftime('%H:%M:%S')}"
    draw.text((24, 66), sub, fill=INK_MUTED, font=f_sub)
    # 视口右上角装饰点
    for i, c in enumerate([ORANGE, YELLOW, GREEN]):
        draw.ellipse([(width - 30 - i * 16, 70), (width - 22 - i * 16, 78)], fill=c)

    # ---- 四张统计卡
    total_quota = (data.get("total_quota", 0) or 0) / 500000.0
    avg_latency = data.get("avg_latency", 0.0) or 0.0
    total_calls = data.get("total_calls", 0) or 0

    boxes = [
        ("中转总调用", f"{total_calls} 次", ORANGE_LT, ORANGE),
        ("总费用估算", f"${total_quota:.4f}", YELLOW_LT, YELLOW),
        ("中转平均延迟", f"{avg_latency:.2f} 秒", GREEN_LT, GREEN),
        ("报错条数", f"{len(error_items)} 条", RED_LT, RED),
    ]
    card_w = (width - 48 - 48) // 4
    y_top = 110
    for i, (label, val, c1, c2) in enumerate(boxes):
        bx = 24 + i * (card_w + 16)
        box = (bx, y_top, bx + card_w, y_top + 70)
        _bevel_panel(draw, box, radius=6)
        _accent_bar(draw, bx, y_top, y_top + 70, c1, c2, w=5)
        draw.text((bx + 16, y_top + 12), label, fill=INK_MUTED, font=f_small)
        draw.text((bx + 16, y_top + 34), val, fill=VALUE_INK, font=f_bold)

    cur_y = y_top + 70 + 22

    # ---- 报错速览
    draw.text((24, cur_y), "最近报错速览 (Recent Errors)", fill=RED_LT, font=f_bold)
    draw.line([(24, cur_y + 25), (width - 24, cur_y + 25)], fill=PANEL_LINE, width=1)
    cur_y += 36

    if not error_items:
        box = (24, cur_y, width - 24, cur_y + 32)
        _bevel_panel(draw, box, radius=6)
        _accent_bar(draw, 24, cur_y, cur_y + 32, GREEN, GREEN, w=5)
        draw.text((38, cur_y + 8), "最近的日志里一条报错都没有，中转站很乖", fill=GREEN_LT, font=f_small)
        cur_y += 32 + 18
    else:
        for item in error_items[:3]:
            e_ts = time.strftime('%H:%M:%S', time.localtime(item.get("created_at") or time.time()))
            left = f"{e_ts} · {item.get('token', '-')} · {item.get('model', '-')} · {item.get('kind', '')}"
            box = (24, cur_y, width - 24, cur_y + 30)
            _bevel_panel(draw, box, radius=6)
            _accent_bar(draw, 24, cur_y, cur_y + 30, RED_LT, RED, w=5)
            det, det_x = _tail(draw, item.get("detail", ""), f_tiny, 38 + 140, width - 36)
            draw.text((38, cur_y + 7), _left_fit(draw, left, f_small, 38, det_x), fill=INK, font=f_small)
            draw.text((det_x, cur_y + 9), det, fill=INK_FAINT, font=f_tiny)
            cur_y += 36
        cur_y += 18

    # ---- 工具明细
    draw.text((24, cur_y), "各插件与功能调用花费明细", fill=INK, font=f_bold)
    draw.line([(24, cur_y + 25), (width - 24, cur_y + 25)], fill=PANEL_LINE, width=1)
    cur_y += 36

    if not tool_stats:
        box = (24, cur_y, width - 24, cur_y + 34)
        _bevel_panel(draw, box, radius=6)
        _accent_bar(draw, 24, cur_y, cur_y + 34, BLUE, BLUE, w=5)
        draw.text((38, cur_y + 9), "暂无记录到工具调用（普通对话消耗已计入模型汇总）", fill=INK_MUTED, font=f_small)
        cur_y += 34 + 18
    else:
        for name, st in sorted(tool_stats.items(), key=lambda x: x[1].get("calls", 0), reverse=True):
            calls = st.get("calls", 0)
            cost = st.get("cost", 0.0)
            toks = st.get("tokens", 0)
            box = (24, cur_y, width - 24, cur_y + 50)
            _bevel_panel(draw, box, radius=6)
            _accent_bar(draw, 24, cur_y, cur_y + 50, BLUE_LT, BLUE, w=5)
            right, right_x = _tail(
                draw, f"触发 {calls} 次  |  Token {toks}  |  ${cost:.4f}", f_small, 340, width - 36
            )
            draw.text(
                (38, cur_y + 7),
                _left_fit(draw, f"{name}", f_body, 38, right_x),
                fill=INK,
                font=f_body,
            )
            subs = []
            if st.get("raw_tool"):
                subs.append(f"id: {st['raw_tool']}")
            if st.get("llm"):
                subs.append(f"LLM: {st['llm']}")
            if subs:
                draw.text(
                    (54, cur_y + 29),
                    _fit(draw, "  |  ".join(subs), f_mono, width - 36 - 54),
                    fill=INK_FAINT,
                    font=f_mono,
                )
            draw.text((right_x, cur_y + 17), right, fill=INK_MUTED, font=f_small)
            cur_y += 58
        cur_y += 12

    # ---- 模型分布
    draw.text((24, cur_y), "模型调用与延迟分布", fill=INK, font=f_bold)
    draw.line([(24, cur_y + 25), (width - 24, cur_y + 25)], fill=PANEL_LINE, width=1)
    cur_y += 36

    for name, st in model_stats.items():
        m_calls = st.get("calls", 0)
        m_cost = (st.get("quota", 0) or 0) / 500000.0
        m_lat = st.get("latency", 0.0) or 0.0
        box = (24, cur_y, width - 24, cur_y + 32)
        _bevel_panel(draw, box, radius=6)
        _accent_bar(draw, 24, cur_y, cur_y + 32, YELLOW_LT, YELLOW, w=5)
        right, right_x = _tail(
            draw, f"调用 {m_calls} 次  |  均延 {m_lat:.1f}s  |  总额 ${m_cost:.4f}", f_small, 320, width - 36
        )
        draw.text((38, cur_y + 8), _left_fit(draw, f"{name}", f_body, 38, right_x), fill=INK, font=f_body)
        draw.text((right_x, cur_y + 9), right, fill=INK_MUTED, font=f_small)
        cur_y += 38
    cur_y += 12

    if flow_rows > 0:
        # ---- 流水明细
        draw.text((24, cur_y), f"API 交互流水明细（共 {flow_total} 条）", fill=INK, font=f_bold)
        draw.line([(24, cur_y + 25), (width - 24, cur_y + 25)], fill=PANEL_LINE, width=1)
        cur_y += 36

        for log in shown_logs:
            t_str = time.strftime('%H:%M:%S', time.localtime(log.get("created_at", time.time())))
            m_name = log.get("model_name", "unknown")
            use_time = log.get("use_time", 0)
            cost = (log.get("quota", 0) or 0) / 500000.0
            in_tok = log.get("prompt_tokens", 0)
            out_tok = log.get("completion_tokens", 0)
            box = (24, cur_y, width - 24, cur_y + 38)
            _bevel_panel(draw, box, radius=6)
            _accent_bar(draw, 24, cur_y, cur_y + 38, FLOW_C1, FLOW_C2, w=5)
            draw.rounded_rectangle([(38, cur_y + 8), (110, cur_y + 30)], radius=4, fill=CHIP_BG, outline=PANEL_LINE)
            draw.text((46, cur_y + 11), t_str, fill=CHIP_INK, font=f_mono)
            det, det_x = _tail(
                draw, f"耗时 {use_time}s  |  Token {in_tok}+{out_tok}  |  ${cost:.5f}", f_small, 300, width - 36
            )
            draw.text((122, cur_y + 10), _left_fit(draw, m_name, f_body, 122, det_x), fill=INK, font=f_body)
            draw.text((det_x, cur_y + 11), det, fill=INK_MUTED, font=f_small)
            cur_y += 46

        if flow_overflow:
            more = flow_total - len(shown_logs)
            draw.text(
                (38, cur_y + 6),
                f"卡片只画最近 {len(shown_logs)} 条，还有 {more} 条可以到 WebUI 账本翻~",
                fill=INK_FAINT,
                font=f_tiny,
            )
            cur_y += 24

    # ---- 页脚
    draw.line([(24, height - 46), (width - 24, height - 46)], fill=PANEL_LINE, width=1)
    foot = "唯笑的私人数据手账 · Syuan API Monitor · 每天 06:00 自动汇报"
    fw = draw.textlength(foot, font=f_small)
    img.paste(_emoji("🍀", 15), (int((width - fw) / 2) - 22, height - 34), _emoji("🍀", 15))
    draw.text(((width - fw) / 2, height - 32), foot, fill=INK_FAINT, font=f_small)
    draw.rectangle([(2, height - 3), (width - 3, height - 3)], fill=ORANGE)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
