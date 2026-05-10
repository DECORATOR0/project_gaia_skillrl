#!/usr/bin/env python3
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUT_DIR = Path(__file__).resolve().parent
W, H = 1536, 1024

FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

BLUE = "#2563eb"
BLUE_FILL = "#f4f8ff"
ORANGE = "#f97316"
ORANGE_FILL = "#fff7ed"
PURPLE = "#7c3aed"
PURPLE_FILL = "#faf5ff"
GREEN = "#16a34a"
GREEN_FILL = "#f0fdf4"
GRAY = "#4b5563"
GRAY_FILL = "#f9fafb"
TEXT = "#111827"
MUTED = "#4b5563"
LINE = "#374151"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)


F_TITLE = font(36, True)
F_SUBTITLE = font(18)
F_H = font(21, True)
F_BODY = font(17)
F_SMALL = font(14)
F_NUM = font(17, True)


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont) -> tuple[int, int]:
    if hasattr(draw, "textbbox"):
        box = draw.textbbox((0, 0), text, font=fnt)
        return box[2] - box[0], box[3] - box[1]
    if hasattr(fnt, "getbbox"):
        box = fnt.getbbox(text)
        return box[2] - box[0], box[3] - box[1]
    return draw.textsize(text, font=fnt)


def centered(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, fnt: ImageFont.FreeTypeFont, fill: str = TEXT) -> None:
    tw, th = text_size(draw, text, fnt)
    draw.text((x - tw / 2, y - th / 2), text, font=fnt, fill=fill)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in text:
        candidate = current + ch
        if not current or text_size(draw, candidate, fnt)[0] <= max_width:
            current = candidate
            continue
        lines.append(current.rstrip())
        current = ch.lstrip()
    if current:
        lines.append(current.rstrip())
    return lines


def canvas(title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), "#ffffff")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, W, H), fill="#ffffff")
    draw.rectangle((0, 104, W, H), fill="#fbfbfc")
    for y in (285, 705):
        draw.line((64, y, W - 64, y), fill="#e5e7eb", width=2)
    centered(draw, W // 2, 52, title, F_TITLE)
    centered(draw, W // 2, 90, subtitle, F_SUBTITLE, MUTED)
    return img, draw


def dashed_rect(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int], color: str, fill: str) -> None:
    x0, y0, x1, y1 = rect
    draw.rectangle(rect, fill=fill)
    dash, gap = 16, 10
    for x in range(x0, x1, dash + gap):
        draw.line((x, y0, min(x + dash, x1), y0), fill=color, width=2)
        draw.line((x, y1, min(x + dash, x1), y1), fill=color, width=2)
    for y in range(y0, y1, dash + gap):
        draw.line((x0, y, x0, min(y + dash, y1)), fill=color, width=2)
        draw.line((x1, y, x1, min(y + dash, y1)), fill=color, width=2)


def box(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    title: str,
    lines: list[str],
    outline: str,
    fill: str,
    number: str | None = None,
) -> dict[str, int]:
    draw.rectangle((x + 5, y + 6, x + w + 5, y + h + 6), fill="#e5e7eb")
    draw.rectangle((x, y, x + w, y + h), fill=fill, outline=outline, width=2)
    left_pad = 20
    if number:
        cx, cy = x + 24, y + 24
        draw.ellipse((cx - 15, cy - 15, cx + 15, cy + 15), fill=outline)
        centered(draw, cx, cy - 1, number, F_NUM, "white")
        left_pad = 50
    draw.text((x + left_pad, y + 13), title, font=F_H, fill=TEXT)
    ty = y + 43
    for line in lines:
        for part in wrap(draw, line, F_BODY, w - left_pad - 16):
            draw.text((x + left_pad, ty), part, font=F_BODY, fill=MUTED)
            ty += 23
    return {"x": x, "y": y, "w": w, "h": h}


def left(b: dict[str, int]) -> tuple[int, int]:
    return b["x"], b["y"] + b["h"] // 2


def right(b: dict[str, int]) -> tuple[int, int]:
    return b["x"] + b["w"], b["y"] + b["h"] // 2


def top(b: dict[str, int]) -> tuple[int, int]:
    return b["x"] + b["w"] // 2, b["y"]


def bottom(b: dict[str, int]) -> tuple[int, int]:
    return b["x"] + b["w"] // 2, b["y"] + b["h"]


def arrow(draw: ImageDraw.ImageDraw, points: list[tuple[int, int]], color: str = LINE, width: int = 3) -> None:
    draw.line(points, fill=color, width=width)
    x2, y2 = points[-1]
    x1, y1 = points[-2]
    angle = math.atan2(y2 - y1, x2 - x1)
    head = 13
    left_head = (x2 - head * math.cos(angle - math.pi / 7), y2 - head * math.sin(angle - math.pi / 7))
    right_head = (x2 - head * math.cos(angle + math.pi / 7), y2 - head * math.sin(angle + math.pi / 7))
    draw.polygon([(x2, y2), left_head, right_head], fill=color)


def connect_lr(draw: ImageDraw.ImageDraw, a: dict[str, int], b: dict[str, int], color: str = LINE) -> None:
    arrow(draw, [right(a), left(b)], color)


def fan_down(draw: ImageDraw.ImageDraw, source: dict[str, int], targets: list[dict[str, int]], y_bus: int, color: str) -> None:
    sx, sy = bottom(source)
    arrow(draw, [(sx, sy), (sx, y_bus)], color)
    for target in targets:
        tx, ty = top(target)
        arrow(draw, [(sx, y_bus), (tx, y_bus), (tx, ty)], color)


def save(img: Image.Image, name: str) -> None:
    img.save(OUT_DIR / name, optimize=True)


def draw_top_pipeline(draw: ImageDraw.ImageDraw) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    boot = box(draw, 95, 135, 230, 92, "BOOT", ["生成 seed skill"], BLUE, BLUE_FILL, "1")
    seed = box(draw, 380, 135, 270, 92, "Seed SKILL.md", ["内容层 + 图结构层"], BLUE, BLUE_FILL, "2")
    exe = box(draw, 710, 135, 230, 92, "Executor", ["跑 dev / train batch"], BLUE, BLUE_FILL, "3")
    log = box(draw, 1065, 135, 315, 92, "轨迹日志", ["task states / actions / paths"], BLUE, BLUE_FILL, "4")
    for a, b in ((boot, seed), (seed, exe), (exe, log)):
        connect_lr(draw, a, b, BLUE)
    return boot, seed, exe, log


def draw_loop(draw: ImageDraw.ImageDraw, eval_box: dict[str, int], log: dict[str, int]) -> None:
    ex, ey = right(eval_box)
    lx, ly = right(log)
    x_bus = 1460
    arrow(draw, [(ex, ey), (x_bus, ey), (x_bus, ly), (lx, ly)], PURPLE)


def draw_v3() -> None:
    img, draw = canvas(
        "FLOW-batch-V3：分层 Critic 的 SkillRL 闭环",
        "轨迹日志只向下供给结构层与内容层；闭环由下一轮 Executor rollout 产生新轨迹",
    )
    _, _, _, log = draw_top_pipeline(draw)

    dashed_rect(draw, (90, 300, 740, 682), BLUE, "#f8fbff")
    dashed_rect(draw, (795, 300, 1445, 682), ORANGE, "#fffaf5")
    dashed_rect(draw, (145, 740, 1415, 900), PURPLE, "#fbf8ff")
    draw.text((48, 465), "结构层", font=F_H, fill=BLUE)
    draw.text((1450, 465), "内容层", font=F_H, fill=ORANGE)
    draw.text((72, 812), "闭环优化", font=F_H, fill=PURPLE)

    mathp = box(draw, 250, 335, 340, 82, "Math Profiler", ["轨迹压缩与量化"], BLUE, BLUE_FILL)
    graphval = box(draw, 170, 455, 430, 96, "Graph value package", ["edge / phase advantage", "path / loop / missing transition"], BLUE, BLUE_FILL)
    graphview = box(draw, 135, 585, 330, 96, "Skill Graph View", ["去掉 per-phase Rules", "保留 phase / tools / Next / graph"], BLUE, BLUE_FILL)
    graphcritic = box(draw, 515, 585, 285, 96, "Graph Critic", ["只提图结构与 routing 建议"], BLUE, BLUE_FILL)

    fail = box(draw, 855, 335, 305, 92, "Failure-family batches", ["成败 / tool errors", "protocol friction"], ORANGE, ORANGE_FILL)
    full = box(draw, 1210, 335, 220, 92, "Full Skill Content", ["保留 Rules / allowlist", "handoff"], ORANGE, ORANGE_FILL)
    rules = box(draw, 990, 535, 360, 100, "Rules/List Critics", ["按 failure family 看内容", "只提 rules / allowlist / handoff"], ORANGE, ORANGE_FILL)

    agg = box(draw, 245, 775, 270, 96, "Aggregate Critic", ["合并建议 / 去冲突", "保护成功锚点"], PURPLE, PURPLE_FILL)
    actor = box(draw, 585, 775, 260, 96, "Actor", ["按明确 reward", "修改 SKILL.md"], PURPLE, PURPLE_FILL)
    new = box(draw, 905, 775, 230, 110, "New Skill", ["结构 + 内容补丁"], PURPLE, PURPLE_FILL)
    evalb = box(
        draw,
        1190,
        775,
        225,
        110,
        "Executor eval",
        ["dev/test rollout", "超步数 -> 直接答", "防止空答案"],
        PURPLE,
        PURPLE_FILL,
    )

    fan_down(draw, log, [mathp, fail, full], 270, BLUE)
    mx, my = bottom(mathp)
    gvx, gvy = top(graphval)
    arrow(draw, [(mx, my), (mx, 435), (gvx, 435), (gvx, gvy)], BLUE)
    gvx2, gvy2 = bottom(graphval)
    sgx, sgy = top(graphview)
    arrow(draw, [(gvx2, gvy2), (gvx2, 570), (sgx, 570), (sgx, sgy)], BLUE)
    connect_lr(draw, graphview, graphcritic, BLUE)
    arrow(draw, [bottom(fail), (1007, 490), (1170, 490), top(rules)], ORANGE)
    arrow(draw, [bottom(full), (1320, 490), (1170, 490), top(rules)], ORANGE)
    arrow(draw, [bottom(graphcritic), (657, 715), (380, 715), top(agg)], PURPLE)
    arrow(draw, [bottom(rules), (1170, 715), (380, 715), top(agg)], PURPLE)
    for a, b in ((agg, actor), (actor, new), (new, evalb)):
        connect_lr(draw, a, b, PURPLE)
    draw_loop(draw, evalb, log)
    draw.text((1095, 248), "三路下行供料", font=F_SMALL, fill=MUTED)
    save(img, "flow_batch_v3_layered_critic_v3.png")


def draw_single_critic_flow(
    *,
    name: str,
    subtitle: str,
    filename: str,
    layer_label: str,
    layer_color: str,
    layer_fill: str,
    input_boxes: list[tuple[str, list[str]]],
    critic_title: str,
    critic_lines: list[str],
    actor_title: str,
    actor_lines: list[str],
    new_skill_label: str,
) -> None:
    img, draw = canvas(name, subtitle)
    _, _, _, log = draw_top_pipeline(draw)
    dashed_rect(draw, (110, 320, 1425, 650), layer_color, layer_fill)
    dashed_rect(draw, (170, 735, 1385, 900), PURPLE, "#fbf8ff")
    draw.text((68, 465), layer_label, font=F_H, fill=layer_color)
    draw.text((86, 812), "闭环优化", font=F_H, fill=PURPLE)

    xs = [190, 525, 860, 1195][: len(input_boxes)]
    inputs: list[dict[str, int]] = []
    for x, (title, lines) in zip(xs, input_boxes):
        inputs.append(box(draw, x, 365, 275, 98, title, lines, layer_color, "#ffffff"))

    critic = box(draw, 565, 535, 405, 104, critic_title, critic_lines, layer_color, "#ffffff")
    actor = box(draw, 285, 775, 300, 96, actor_title, actor_lines, PURPLE, PURPLE_FILL)
    new = box(draw, 690, 775, 245, 110, "New Skill", [new_skill_label], PURPLE, PURPLE_FILL)
    evalb = box(
        draw,
        1035,
        775,
        245,
        110,
        "Executor eval",
        ["dev/test rollout", "超步数 -> 直接答", "防止空答案"],
        PURPLE,
        PURPLE_FILL,
    )

    fan_down(draw, log, inputs, 290, layer_color)
    for ib in inputs:
        ix, iy = bottom(ib)
        cx, cy = top(critic)
        arrow(draw, [(ix, iy), (ix, 500), (cx, 500), (cx, cy)], layer_color)
    arrow(draw, [bottom(critic), (767, 710), (435, 710), top(actor)], PURPLE)
    for a, b in ((actor, new), (new, evalb)):
        connect_lr(draw, a, b, PURPLE)
    draw_loop(draw, evalb, log)
    save(img, filename)


def draw_batch_v2() -> None:
    draw_single_critic_flow(
        name="FLOW-batch-V2：单 Critic 混合 graph_b2 信号",
        subtitle="B2 没有独立 Graph Critic；graph / rules / allowlist 信号混在同一个 critic 输入和 reward 里",
        filename="flow_batch_v2_graph_b2.png",
        layer_label="B2 Critic 输入",
        layer_color=ORANGE,
        layer_fill="#fffaf5",
        input_boxes=[
            ("Compact Rows", ["success / family", "tools / outcomes"]),
            ("Graph Profile", ["phase dwell / edge use", "loop / invalid next"]),
            ("Full Skill + Graph", ["完整 Rules", "graph signature"]),
        ],
        critic_title="B2 Critic",
        critic_lines=["一个 critic 统一看 graph_b2 + rules", "输出 actor-facing reward"],
        actor_title="Graph-unlocked Actor",
        actor_lines=["可改 graph / Next", "可改 allowlist / rules"],
        new_skill_label="B2 skill",
    )


def draw_batch_v1() -> None:
    draw_single_critic_flow(
        name="FLOW-batch-V1 / B1：单 Critic 的分桶输入",
        subtitle="图里按一个 Critic block 抽象；failure-family 分桶和聚合属于这个 block 内部实现",
        filename="flow_batch_v1_sharded.png",
        layer_label="B1 Critic 输入",
        layer_color=ORANGE,
        layer_fill="#fffaf5",
        input_boxes=[
            ("Failure-family batches", ["success anchors / no answer", "tool / protocol / retrieval"]),
            ("Full Skill", ["完整 Rules 与 allowlist", "phase graph 只读"]),
            ("Graph Lock", ["phase order", "Next target set 保持不变"]),
        ],
        critic_title="B1 Critic",
        critic_lines=["一个 critic block 消化分桶信息", "主要提出内容层 reward"],
        actor_title="Fixed-graph Actor",
        actor_lines=["保持 phase graph", "改 rules / allowlist / handoff"],
        new_skill_label="B1 skill",
    )


def draw_unified_v1() -> None:
    draw_single_critic_flow(
        name="FLOW-unified-V1 / A_full：单 Critic 直接闭环",
        subtitle="A_full 一次性把轨迹、完整 skill 和历史交给同一个 full critic",
        filename="flow_unified_v1_afull_direct.png",
        layer_label="A 模式输入",
        layer_color=GREEN,
        layer_fill="#f5fff8",
        input_boxes=[
            ("All Compact Rows", ["全量样本一次输入", "success / tools / outcomes"]),
            ("Full Skill + Graph", ["完整 Rules", "graph signature"]),
            ("Recent History", ["最近几轮 reward / skill"]),
        ],
        critic_title="A_full Critic",
        critic_lines=["single-pass synthesis", "直接输出 actor-facing reward"],
        actor_title="Fixed-graph Actor",
        actor_lines=["保持 graph", "改 common / node rules"],
        new_skill_label="A_full skill",
    )


def draw_agent_role_map() -> None:
    img = Image.new("RGB", (W, H), "#ffffff")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, W, H), fill="#ffffff")
    draw.rectangle((0, 116, W, H), fill="#fbfbfc")
    centered(draw, W // 2, 52, "GAIA 小模型 SkillRL：agent 角色简图", F_TITLE)
    centered(
        draw,
        W // 2,
        90,
        "先看角色分工：BOOT 生成 skill，小模型 Executor 跑轨迹，分层 Critic 给信号，Actor 改出下一版 skill",
        F_SUBTITLE,
        MUTED,
    )

    draw.rectangle((64, 135, 1472, 420), fill="#f8fbff")
    draw.rectangle((64, 445, 1472, 765), fill="#fbf8ff")
    draw.rectangle((64, 790, 1472, 970), fill="#f8fbff")
    draw.line((64, 145, 1472, 145), fill=BLUE, width=3)
    draw.line((64, 455, 1472, 455), fill=PURPLE, width=3)
    draw.line((64, 790, 1472, 790), fill=BLUE, width=3)
    draw.text((84, 160), "执行回合：小模型 Executor 使用 skill 解决 GAIA 任务", font=F_H, fill=BLUE)
    draw.text((84, 470), "学习回合：离线 critic / actor 根据轨迹修改 skill", font=F_H, fill=PURPLE)
    draw.text((84, 815), "测试阶段：把新 skill 直接加载到 Executor 上", font=F_H, fill=BLUE)

    boot = box(
        draw,
        90,
        295,
        260,
        100,
        "BOOT / Bootstrap Actor",
        ["离线生成 seed graph", "+ skill"],
        BLUE,
        BLUE_FILL,
        "1",
    )
    skill = box(
        draw,
        405,
        295,
        245,
        100,
        "SKILL.md",
        ["phase graph", "Rules / tools / handoff"],
        GRAY,
        GRAY_FILL,
    )
    executor = box(
        draw,
        705,
        295,
        260,
        100,
        "Executor",
        ["小模型 ReAct 执行", "按 skill 调用工具"],
        BLUE,
        BLUE_FILL,
        "2",
    )
    env = box(
        draw,
        705,
        175,
        260,
        88,
        "GAIA Toolbox",
        ["search / file / code", "multimodal"],
        GREEN,
        GREEN_FILL,
    )
    log = box(
        draw,
        1030,
        295,
        330,
        100,
        "Trajectory Log",
        ["states / actions", "answers / scores"],
        BLUE,
        BLUE_FILL,
        "3",
    )

    connect_lr(draw, boot, skill, BLUE)
    connect_lr(draw, skill, executor, BLUE)
    connect_lr(draw, executor, log, BLUE)
    tx, ty = bottom(env)
    ex, ey = top(executor)
    arrow(draw, [(tx, ty), (ex, ey)], GREEN)

    graph_critic = box(
        draw,
        105,
        540,
        310,
        110,
        "Graph Critic",
        ["输入：graph view / paths", "提 routing 与结构 patch"],
        PURPLE,
        PURPLE_FILL,
        "4",
    )
    content_critic = box(
        draw,
        105,
        660,
        310,
        90,
        "Content Critics",
        ["输入：failures / full skill", "提 rules / allowlist patch"],
        ORANGE,
        ORANGE_FILL,
        "5",
    )
    aggregate = box(
        draw,
        500,
        600,
        300,
        110,
        "Aggregate Critic",
        ["合并图与内容建议", "去冲突并保护成功路径"],
        PURPLE,
        PURPLE_FILL,
        "6",
    )
    actor = box(
        draw,
        875,
        600,
        230,
        110,
        "Actor",
        ["按 reward / patch", "修改 SKILL.md"],
        PURPLE,
        PURPLE_FILL,
        "7",
    )
    new_skill = box(
        draw,
        505,
        860,
        260,
        105,
        "New Skill",
        ["下一轮 graph + skill"],
        GRAY,
        GRAY_FILL,
    )
    evalb = box(
        draw,
        845,
        860,
        285,
        105,
        "Executor eval / test",
        ["加载 New Skill", "跑 dev/test"],
        BLUE,
        BLUE_FILL,
        "8",
    )

    draw.text((105, 505), "Trajectory Log 和当前 SKILL.md 作为 critic 输入", font=F_SMALL, fill=MUTED)
    lx, ly = bottom(log)
    arrow(draw, [(lx, ly), (lx, 500), (80, 500), (80, 595), left(graph_critic)], BLUE, width=2)
    arrow(draw, [(lx, 500), (80, 500), (80, 705), left(content_critic)], BLUE, width=2)
    arrow(draw, [right(graph_critic), (455, right(graph_critic)[1]), (455, 655), left(aggregate)], PURPLE)
    arrow(draw, [right(content_critic), (455, right(content_critic)[1]), (455, 655), left(aggregate)], ORANGE)
    connect_lr(draw, aggregate, actor, PURPLE)
    ax, ay = right(actor)
    nx, ny = top(new_skill)
    arrow(draw, [(ax, ay), (1180, ay), (1180, 830), (nx, 830), (nx, ny)], PURPLE)
    connect_lr(draw, new_skill, evalb, BLUE)
    evx, evy = right(evalb)
    ltx, lty = bottom(log)
    arrow(draw, [(evx, evy), (1450, evy), (1450, lty + 40), (ltx, lty + 40), (ltx, lty)], BLUE, width=2)
    draw.text((1140, 930), "测试 rollout 继续写回 Trajectory Log", font=F_SMALL, fill=MUTED)

    save(img, "gaia_skillrl_agent_roles_simple.png")


def main() -> None:
    draw_agent_role_map()
    draw_v3()
    draw_batch_v2()
    draw_batch_v1()
    draw_unified_v1()


if __name__ == "__main__":
    main()
