#!/usr/bin/env python3
from __future__ import annotations

import math
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BASE_DIR = Path(__file__).resolve().parent
OUT = BASE_DIR / "skill_graph_before_after_b3_example.png"

W, H = 1800, 1040

TEXT = "#111827"
MUTED = "#4B5563"
LINE = "#CBD5E1"
BLUE = "#2563EB"
BLUE_FILL = "#EFF6FF"
PURPLE = "#7C3AED"
PURPLE_FILL = "#F5F3FF"
ORANGE = "#F97316"
ORANGE_FILL = "#FFF7ED"
GREEN = "#16A34A"
GREEN_FILL = "#F0FDF4"
RED = "#DC2626"
GRAY_FILL = "#F8FAFC"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "NotoSansCJK-Bold.ttc" if bold else "NotoSansCJK-Regular.ttc"
    return ImageFont.truetype(name, size=size)


F_TITLE = font(40, True)
F_H1 = font(29, True)
F_H2 = font(23, True)
F_BODY = font(18)
F_SMALL = font(15)
F_TINY = font(13)


def text_width(draw: ImageDraw.ImageDraw, value: str, fnt: ImageFont.FreeTypeFont) -> int:
    if hasattr(draw, "textbbox"):
        return draw.textbbox((0, 0), value, font=fnt)[2]
    return draw.textsize(value, font=fnt)[0]


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, width: int) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    for part in text.split("\n"):
        if all(ord(ch) < 128 for ch in part):
            lines.extend(textwrap.wrap(part, width=max(8, width // max(9, fnt.size // 2))) or [""])
            continue
        current = ""
        for ch in part:
            trial = current + ch
            if text_width(draw, trial, fnt) <= width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = ch
        if current:
            lines.append(current)
    return lines


def text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: str,
    fnt: ImageFont.FreeTypeFont,
    fill: str = TEXT,
    max_width: int | None = None,
    line_gap: int = 4,
) -> int:
    x, y = xy
    lines = wrap(draw, value, fnt, max_width) if max_width else value.split("\n")
    for line in lines:
        draw.text((x, y), line, font=fnt, fill=fill)
        y += fnt.size + line_gap
    return y


def rounded(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    fill: str,
    outline: str = LINE,
    width: int = 2,
    radius: int = 10,
) -> None:
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    else:
        draw.rectangle(box, fill=fill, outline=outline, width=width)


def node(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    title: str,
    body: str,
    *,
    fill: str,
    outline: str,
    w: int = 190,
    h: int = 86,
) -> tuple[int, int, int, int]:
    x, y = center
    box = (x - w // 2, y - h // 2, x + w // 2, y + h // 2)
    rounded(draw, box, fill, outline, 3, 8)
    text(draw, (box[0] + 16, box[1] + 12), title, F_H2, TEXT, w - 32)
    text(draw, (box[0] + 16, box[1] + 46), body, F_SMALL, MUTED, w - 32, line_gap=1)
    return box


def boundary_point(box: tuple[int, int, int, int], toward: tuple[int, int]) -> tuple[int, int]:
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    dx = toward[0] - cx
    dy = toward[1] - cy
    if dx == 0 and dy == 0:
        return int(cx), int(cy)
    sx = (box[2] - box[0]) / 2 / abs(dx) if dx else 10**9
    sy = (box[3] - box[1]) / 2 / abs(dy) if dy else 10**9
    s = min(sx, sy)
    return int(cx + dx * s), int(cy + dy * s)


def draw_arrow(
    draw: ImageDraw.ImageDraw,
    boxes: dict[str, tuple[int, int, int, int]],
    start: str,
    end: str,
    *,
    color: str = BLUE,
    width: int = 3,
    label: str | None = None,
    label_offset: tuple[int, int] = (0, 0),
    dashed: bool = False,
    curve: int = 0,
) -> None:
    sb = boxes[start]
    eb = boxes[end]
    sc = ((sb[0] + sb[2]) // 2, (sb[1] + sb[3]) // 2)
    ec = ((eb[0] + eb[2]) // 2, (eb[1] + eb[3]) // 2)
    p1 = boundary_point(sb, ec)
    p2 = boundary_point(eb, sc)
    if curve:
        mx = (p1[0] + p2[0]) // 2
        my = (p1[1] + p2[1]) // 2 + curve
        points = []
        for i in range(26):
            t = i / 25
            x = (1 - t) ** 2 * p1[0] + 2 * (1 - t) * t * mx + t**2 * p2[0]
            y = (1 - t) ** 2 * p1[1] + 2 * (1 - t) * t * my + t**2 * p2[1]
            points.append((int(x), int(y)))
    else:
        points = [p1, p2]
    if dashed:
        draw_dashed_line(draw, points, fill=color, width=width)
    else:
        draw.line(points, fill=color, width=width, joint="curve")
    draw_head(draw, points[-2], points[-1], color, width)
    if label:
        lx = (p1[0] + p2[0]) // 2 + label_offset[0]
        ly = (p1[1] + p2[1]) // 2 + label_offset[1]
        tw = text_width(draw, label, F_TINY)
        rounded(draw, (lx - 8, ly - 4, lx + tw + 8, ly + 22), "#FFFFFF", color, 1, 7)
        draw.text((lx, ly), label, font=F_TINY, fill=color)


def draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[int, int]],
    *,
    fill: str,
    width: int,
    dash: int = 12,
    gap: int = 8,
) -> None:
    segs = list(zip(points, points[1:]))
    for p1, p2 in segs:
        x1, y1 = p1
        x2, y2 = p2
        length = math.hypot(x2 - x1, y2 - y1)
        if length == 0:
            continue
        vx = (x2 - x1) / length
        vy = (y2 - y1) / length
        pos = 0
        while pos < length:
            end = min(length, pos + dash)
            draw.line(
                [(x1 + vx * pos, y1 + vy * pos), (x1 + vx * end, y1 + vy * end)],
                fill=fill,
                width=width,
            )
            pos += dash + gap


def draw_head(
    draw: ImageDraw.ImageDraw,
    p1: tuple[int, int],
    p2: tuple[int, int],
    color: str,
    width: int,
) -> None:
    angle = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    length = 13 + width
    spread = math.radians(26)
    pts = [
        p2,
        (int(p2[0] - length * math.cos(angle - spread)), int(p2[1] - length * math.sin(angle - spread))),
        (int(p2[0] - length * math.cos(angle + spread)), int(p2[1] - length * math.sin(angle + spread))),
    ]
    draw.polygon(pts, fill=color)


def draw_panel(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y0: int,
    title_text: str,
    mode: str,
) -> None:
    rounded(draw, (x0, y0, x0 + 740, y0 + 690), "#FFFFFF", LINE, 2, 16)
    text(draw, (x0 + 26, y0 + 22), title_text, F_H1, TEXT)
    boxes = {
        "INIT": node(draw, (x0 + 370, y0 + 125), "INIT", "route + contract", fill=BLUE_FILL, outline=BLUE),
        "LOCAL": node(draw, (x0 + 170, y0 + 285), "LOCAL_READ", "files / attachments", fill=BLUE_FILL, outline=BLUE),
        "WEB": node(draw, (x0 + 570, y0 + 285), "WEB_LOOKUP", "search / fetch", fill=BLUE_FILL, outline=BLUE),
        "COMPUTE": node(draw, (x0 + 300, y0 + 445), "COMPUTE", "filter / math", fill=PURPLE_FILL, outline=PURPLE),
        "VERIFY": node(draw, (x0 + 300, y0 + 590), "VERIFY", "exact final check", fill=PURPLE_FILL, outline=PURPLE),
        "REPAIR": node(draw, (x0 + 570, y0 + 590), "REPAIR", "blocker recovery", fill=ORANGE_FILL, outline=ORANGE),
    }

    if mode == "before":
        draw_arrow(draw, boxes, "INIT", "WEB", color=ORANGE, width=5, label="broad web-first", label_offset=(-20, -18))
        draw_arrow(draw, boxes, "INIT", "LOCAL", color=BLUE, width=3)
        draw_arrow(draw, boxes, "INIT", "COMPUTE", color=BLUE, width=3, curve=12)
        draw_arrow(draw, boxes, "LOCAL", "WEB", color=ORANGE, width=4, label="easy pivot", label_offset=(-38, -30))
        draw_arrow(draw, boxes, "LOCAL", "COMPUTE", color=BLUE, width=3)
        draw_arrow(draw, boxes, "WEB", "COMPUTE", color=BLUE, width=3)
        draw_arrow(draw, boxes, "WEB", "VERIFY", color=BLUE, width=3, curve=18)
        draw_arrow(draw, boxes, "WEB", "REPAIR", color=ORANGE, width=4, label="low-confidence repair", label_offset=(-60, -8))
        draw_arrow(draw, boxes, "COMPUTE", "VERIFY", color=ORANGE, width=4, label="default verify", label_offset=(-68, -14))
        draw_arrow(draw, boxes, "VERIFY", "REPAIR", color=ORANGE, width=3, dashed=True)
    else:
        draw_arrow(draw, boxes, "INIT", "COMPUTE", color=GREEN, width=5, label="protect", label_offset=(-24, -24))
        draw_arrow(draw, boxes, "LOCAL", "COMPUTE", color=GREEN, width=5, label="protect", label_offset=(-10, -8))
        draw_arrow(draw, boxes, "INIT", "LOCAL", color=BLUE, width=3)
        draw_arrow(draw, boxes, "INIT", "WEB", color=ORANGE, width=4, dashed=True, label="external anchor only", label_offset=(-54, -18))
        draw_arrow(draw, boxes, "LOCAL", "WEB", color=ORANGE, width=4, dashed=True, label="after source anchor", label_offset=(-50, -30))
        draw_arrow(draw, boxes, "WEB", "COMPUTE", color=GREEN, width=4, label="facts -> compute", label_offset=(-45, -26))
        draw_arrow(draw, boxes, "WEB", "VERIFY", color=GREEN, width=4, curve=18, label="supported candidate", label_offset=(-62, -10))
        draw_arrow(draw, boxes, "WEB", "REPAIR", color=ORANGE, width=4, dashed=True, label="diagnosed blocker", label_offset=(-54, -8))
        draw_arrow(draw, boxes, "COMPUTE", "VERIFY", color=ORANGE, width=4, dashed=True, label="only if risk", label_offset=(-52, -16))
        draw_arrow(draw, boxes, "REPAIR", "WEB", color=ORANGE, width=3, dashed=True, curve=-40, label="one changed method", label_offset=(-68, -40))
        answer_box = node(draw, (x0 + 112, y0 + 590), "ANSWER", "exact string", fill=GREEN_FILL, outline=GREEN, w=150, h=74)
        boxes["ANSWER"] = answer_box
        draw_arrow(draw, boxes, "COMPUTE", "ANSWER", color=GREEN, width=4, dashed=True, label="direct if exact", label_offset=(-58, -18))
        draw_arrow(draw, boxes, "VERIFY", "ANSWER", color=GREEN, width=4)


def legend(draw: ImageDraw.ImageDraw) -> None:
    x, y = 118, 900
    items = [
        (GREEN, "保护/强化的正收益通路"),
        (ORANGE, "B3 收紧的低收益边"),
        (BLUE, "保留的常规路由"),
    ]
    for color, label in items:
        draw.line((x, y + 10, x + 48, y + 10), fill=color, width=5)
        draw_head(draw, (x + 36, y + 10), (x + 52, y + 10), color, 5)
        text(draw, (x + 66, y - 3), label, F_SMALL, MUTED)
        x += 310


def main() -> None:
    img = Image.new("RGB", (W, H), "#FFFFFF")
    draw = ImageDraw.Draw(img)
    rounded(draw, (0, 0, W, H), "#FFFFFF", "#FFFFFF", 0, 0)
    text(draw, (86, 44), "B3 前后 Skill Graph 变化", F_TITLE, TEXT)
    text(
        draw,
        (88, 98),
        "示例：20260507_114859 / 8B BootV3 -> B3 graph_v3；下图展示 actor decision 中的 graph 条件变化",
        F_BODY,
        MUTED,
        1400,
    )

    draw_panel(draw, 80, 165, "Before：Boot skill 图", "before")
    draw_panel(draw, 980, 165, "After：B3 proposed skill 图", "after")

    # Center bridge.
    rounded(draw, (782, 360, 1018, 535), PURPLE_FILL, PURPLE, 2, 14)
    text(draw, (812, 384), "B3 graph critic", F_H2, TEXT, 176)
    bridge_lines = [
        "收紧 INIT->WEB",
        "收紧 LOCAL->WEB",
        "收紧 WEB->REPAIR",
        "收紧 COMPUTE->VERIFY",
        "保护 COMPUTE 主路",
    ]
    yy = 424
    for line in bridge_lines:
        yy = text(draw, (812, yy), line, F_SMALL, MUTED, 180, 2)
    draw.line((748, 450, 782, 450), fill=PURPLE, width=4)
    draw_head(draw, (760, 450), (782, 450), PURPLE, 4)
    draw.line((1018, 450, 950, 450), fill=PURPLE, width=4)
    draw_head(draw, (1002, 450), (980, 450), PURPLE, 4)

    rounded(draw, (86, 825, 1714, 875), GRAY_FILL, LINE, 1, 10)
    text(
        draw,
        (112, 837),
        "B3 信号：73/83 轨迹落盘、19/83 成功；失败中 web retrieval 占 41/64。策略是保留六阶段骨架，给低收益边加进入条件。",
        F_BODY,
        TEXT,
        1560,
    )
    legend(draw)
    text(draw, (88, 962), "按 B3 actor_decision 中的 graph patch proposals 抽象成组会示意图。", F_TINY, MUTED, 1550)
    img.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
