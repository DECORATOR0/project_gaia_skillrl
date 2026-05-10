#!/usr/bin/env python3
from __future__ import annotations

import html
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image


BASE_DIR = Path(__file__).resolve().parent
DOC_DIR = BASE_DIR.parent
OUT = DOC_DIR / "GAIA小模型SkillRL阶段性进展_组会稿_V3_20260507.pptx"

SLIDE_W = 12192000
SLIDE_H = 6858000
EMU_PER_INCH = 914400

FONT = "Microsoft YaHei"
TEXT = "111827"
MUTED = "4B5563"
BLUE = "2563EB"
BLUE_FILL = "EFF6FF"
PURPLE = "7C3AED"
PURPLE_FILL = "F5F3FF"
ORANGE = "F97316"
ORANGE_FILL = "FFF7ED"
GREEN = "16A34A"
GREEN_FILL = "F0FDF4"
GRAY_FILL = "F8FAFC"
LINE = "CBD5E1"


def emu(inches: float) -> int:
    return int(inches * EMU_PER_INCH)


def esc(s: str) -> str:
    return html.escape(s, quote=True)


@dataclass
class ImageRef:
    path: Path
    x: int
    y: int
    cx: int
    cy: int


@dataclass
class Slide:
    title: str | None = None
    elements: list[str] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)


def run_xml(text: str, size: int, color: str, bold: bool = False) -> str:
    b = ' b="1"' if bold else ""
    return (
        f'<a:r><a:rPr lang="zh-CN" sz="{size}"{b}>'
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        f'<a:latin typeface="{FONT}"/><a:ea typeface="{FONT}"/><a:cs typeface="{FONT}"/>'
        f"</a:rPr><a:t>{esc(text)}</a:t></a:r>"
    )


def para_xml(text: str, size: int, color: str = TEXT, bold: bool = False, bullet: bool = False) -> str:
    mar = ' marL="260000" indent="-180000"' if bullet else ""
    bu = '<a:buChar char="•"/>' if bullet else ""
    return f"<a:p><a:pPr{mar}>{bu}</a:pPr>{run_xml(text, size, color, bold)}</a:p>"


def textbox(
    sid: int,
    x: int,
    y: int,
    cx: int,
    cy: int,
    lines: Iterable[str],
    *,
    size: int = 2400,
    color: str = TEXT,
    bold: bool = False,
    fill: str | None = None,
    line: str | None = None,
    bullet: bool = False,
) -> str:
    fill_xml = (
        f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>' if fill else "<a:noFill/>"
    )
    line_xml = (
        f'<a:ln w="12000"><a:solidFill><a:srgbClr val="{line}"/></a:solidFill></a:ln>'
        if line
        else "<a:ln><a:noFill/></a:ln>"
    )
    paras = "".join(para_xml(line_text, size, color, bold, bullet) for line_text in lines)
    return f"""
      <p:sp>
        <p:nvSpPr><p:cNvPr id="{sid}" name="TextBox {sid}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
          {fill_xml}{line_xml}
        </p:spPr>
        <p:txBody><a:bodyPr wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720"><a:spAutoFit/></a:bodyPr><a:lstStyle/>{paras}</p:txBody>
      </p:sp>
    """


def rect(sid: int, x: int, y: int, cx: int, cy: int, fill: str, line: str = "FFFFFF") -> str:
    return f"""
      <p:sp>
        <p:nvSpPr><p:cNvPr id="{sid}" name="Rect {sid}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
          <a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>
          <a:ln w="6350"><a:solidFill><a:srgbClr val="{line}"/></a:solidFill></a:ln>
        </p:spPr>
      </p:sp>
    """


def title(slide: Slide, text: str, subtitle: str | None = None) -> None:
    slide.elements.append(textbox(10, emu(0.55), emu(0.28), emu(12.2), emu(0.45), [text], size=3000, bold=True))
    if subtitle:
        slide.elements.append(textbox(11, emu(0.58), emu(0.78), emu(12.0), emu(0.35), [subtitle], size=1550, color=MUTED))


def bullet_block(slide: Slide, x: float, y: float, w: float, lines: list[str], *, size: int = 1900) -> None:
    base_id = 100 + len(slide.elements)
    slide.elements.append(
        textbox(base_id, emu(x), emu(y), emu(w), emu(0.42 * len(lines) + 0.25), lines, size=size, bullet=True)
    )


def card(slide: Slide, x: float, y: float, w: float, h: float, heading: str, lines: list[str], *, color: str = BLUE, fill: str = BLUE_FILL) -> None:
    sid = 200 + len(slide.elements)
    slide.elements.append(rect(sid, emu(x), emu(y), emu(w), emu(h), fill, color))
    slide.elements.append(textbox(sid + 1, emu(x + 0.12), emu(y + 0.08), emu(w - 0.24), emu(0.35), [heading], size=1800, bold=True, color=TEXT))
    slide.elements.append(textbox(sid + 2, emu(x + 0.12), emu(y + 0.48), emu(w - 0.24), emu(h - 0.55), lines, size=1450, color=MUTED))


def fit_image(path: Path, x: float, y: float, w: float, h: float) -> ImageRef:
    img = Image.open(path)
    iw, ih = img.size
    box_w, box_h = emu(w), emu(h)
    scale = min(box_w / iw, box_h / ih)
    cx, cy = int(iw * scale), int(ih * scale)
    off_x = emu(x) + (box_w - cx) // 2
    off_y = emu(y) + (box_h - cy) // 2
    return ImageRef(path, off_x, off_y, cx, cy)


def pic_xml(sid: int, rid: str, x: int, y: int, cx: int, cy: int) -> str:
    return f"""
      <p:pic>
        <p:nvPicPr><p:cNvPr id="{sid}" name="Picture {sid}"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>
        <p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
        <p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
      </p:pic>
    """


def slide_xml(slide: Slide, idx: int, image_rels: list[tuple[str, str]]) -> str:
    elements = [rect(2, 0, 0, SLIDE_W, SLIDE_H, "FFFFFF", "FFFFFF")]
    elements.extend(slide.elements)
    for i, (rid, _) in enumerate(image_rels, start=1):
        img = slide.images[i - 1]
        elements.append(pic_xml(700 + i, rid, img.x, img.y, img.cx, img.cy))
    sp_tree = "\n".join(elements)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree>
    <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
    <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
    {sp_tree}
  </p:spTree></p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>
"""


def rels_xml(rels: list[tuple[str, str, str]]) -> str:
    body = "".join(
        f'<Relationship Id="{rid}" Type="{typ}" Target="{target}"/>' for rid, typ, target in rels
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{body}</Relationships>
"""


def content_types(n: int) -> str:
    slide_overrides = "\n".join(
        f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(1, n + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  {slide_overrides}
</Types>
"""


def presentation_xml(n: int) -> str:
    sld_ids = "\n".join(
        f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>' for i in range(1, n + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
  <p:sldIdLst>{sld_ids}</p:sldIdLst>
  <p:sldSz cx="{SLIDE_W}" cy="{SLIDE_H}" type="screen16x9"/>
  <p:notesSz cx="6858000" cy="9144000"/>
  <p:defaultTextStyle/>
</p:presentation>
"""


def slide_master_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
  <p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>
  <p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>
</p:sldMaster>
"""


def slide_layout_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1">
  <p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>
"""


def theme_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="GAIA">
  <a:themeElements>
    <a:clrScheme name="GAIA"><a:dk1><a:srgbClr val="111827"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="1F2937"/></a:dk2><a:lt2><a:srgbClr val="F8FAFC"/></a:lt2><a:accent1><a:srgbClr val="{BLUE}"/></a:accent1><a:accent2><a:srgbClr val="{PURPLE}"/></a:accent2><a:accent3><a:srgbClr val="{ORANGE}"/></a:accent3><a:accent4><a:srgbClr val="{GREEN}"/></a:accent4><a:accent5><a:srgbClr val="64748B"/></a:accent5><a:accent6><a:srgbClr val="0F766E"/></a:accent6><a:hlink><a:srgbClr val="{BLUE}"/></a:hlink><a:folHlink><a:srgbClr val="{PURPLE}"/></a:folHlink></a:clrScheme>
    <a:fontScheme name="GAIA"><a:majorFont><a:latin typeface="{FONT}"/><a:ea typeface="{FONT}"/><a:cs typeface="{FONT}"/></a:majorFont><a:minorFont><a:latin typeface="{FONT}"/><a:ea typeface="{FONT}"/><a:cs typeface="{FONT}"/></a:minorFont></a:fontScheme>
    <a:fmtScheme name="GAIA"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>
  </a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/>
</a:theme>
"""


def core_xml() -> str:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>GAIA 小模型 SkillRL 阶段性进展</dc:title><dc:creator>Codex</dc:creator><cp:lastModifiedBy>Codex</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>
"""


def app_xml(n: int) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application><PresentationFormat>On-screen Show (16:9)</PresentationFormat><Slides>{n}</Slides><Company></Company>
</Properties>
"""


def build_slides() -> list[Slide]:
    slides: list[Slide] = []

    s = Slide()
    s.elements.append(rect(3, 0, 0, SLIDE_W, SLIDE_H, "F8FAFC", "F8FAFC"))
    s.elements.append(textbox(4, emu(0.72), emu(1.35), emu(11.8), emu(0.65), ["GAIA 上小模型 SkillRL 的阶段性进展"], size=3700, bold=True))
    s.elements.append(textbox(5, emu(0.75), emu(2.1), emu(11.3), emu(0.45), ["从 ReAct 执行到 graph + skill，再到分层 critic"], size=2200, color=BLUE, bold=True))
    s.elements.append(textbox(6, emu(0.78), emu(5.95), emu(5.8), emu(0.35), ["2026-05-07｜组会报告稿"], size=1500, color=MUTED))
    slides.append(s)

    s = Slide()
    title(s, "先看角色：执行、学习、测试三段", "所有 agent / module 先放在同一张图里，后面再展开方法细节")
    s.images.append(fit_image(BASE_DIR / "gaia_skillrl_agent_roles_simple.png", 0.55, 1.05, 12.2, 6.0))
    slides.append(s)

    s = Slide()
    title(s, "核心结论", "GAIA 是压力测试环境，方法收益来自 skill 对小模型执行链路的外部结构化约束")
    bullet_block(s, 0.8, 1.35, 11.8, [
        "执行阶段保持小模型闭环：Qwen3 / Qwen3.5 executor + atomic tools + scorer。",
        "强模型只用于离线 bootstrap、critic、actor，产出或修改 SKILL.md。",
        "当前 test best：Qwen3.5-9B + ARCH-V5.2 + BOOT-V3 + B_sharded = 39/82。",
        "新 ARCH-V5.3 图更规范，但 B1/B2 test 回落；结构清晰需要同时降低 executor 执行成本。",
        "下一步最值得看 B3：把 graph critic 与 content critic 分层，输出更明确的 patch。"
    ], size=1900)
    slides.append(s)

    s = Slide()
    title(s, "阶段性分数", "看相对变化：9B 明显强于 8B，旧 B_sharded 仍是当前 test best")
    rows = [
        ("8B direct aligned", "test 13/81", "可跑通，上限低"),
        ("9B p04 clean direct", "test 32/82", "executor 升级带来主收益"),
        ("ARCH-V5.2 BOOT-V3 boot", "dev 40/83 / test 35/82", "boot skill 有收益"),
        ("ARCH-V5.2 B_sharded", "dev 42/83 / test 39/82", "当前 test best"),
        ("ARCH-V5.3 B1/B2", "test 32/82 / 31/82", "图更清楚，但执行成本变高"),
    ]
    for i, (a, b, c) in enumerate(rows):
        y = 1.28 + i * 0.86
        card(s, 0.7, y, 3.2, 0.62, a, [""], color=BLUE, fill=BLUE_FILL)
        s.elements.append(textbox(500 + i, emu(4.2), emu(y + 0.05), emu(2.7), emu(0.45), [b], size=1700, bold=True, color=TEXT))
        s.elements.append(textbox(520 + i, emu(7.0), emu(y + 0.05), emu(5.5), emu(0.45), [c], size=1600, color=MUTED))
    slides.append(s)

    s = Slide()
    title(s, "方法结构：六个主轴", "把实验命名拆开，方便定位每次变化到底改了什么")
    axes = [
        ("BOOT", "初始 skill 怎样生成", BLUE, BLUE_FILL),
        ("FLOW", "boot states 怎样进入 critic / actor / aggregate", PURPLE, PURPLE_FILL),
        ("ROLE", "bootstrap actor / offline critic / actor 的模型和接口", ORANGE, ORANGE_FILL),
        ("EXEC", "executor 小模型、vLLM、tokenizer、窗口和端口", BLUE, BLUE_FILL),
        ("TOOL", "executor 可见工具集合，当前主线 atomic_v2", GREEN, GREEN_FILL),
        ("ARCH", "runtime 状态机、答案协议、phase guard、上下文披露", PURPLE, PURPLE_FILL),
    ]
    for i, (h, body, color, fill) in enumerate(axes):
        x = 0.75 + (i % 2) * 6.1
        y = 1.25 + (i // 2) * 1.45
        card(s, x, y, 5.5, 1.05, h, [body], color=color, fill=fill)
    slides.append(s)

    s = Slide()
    title(s, "Skill 从文本规则推进到 graph + skill", "BOOT 直接生成可执行状态图，executor 每一步按当前 phase 约束行动")
    bullet_block(s, 0.8, 1.3, 5.9, [
        "phase node 包含 role / rules / allowlist / actions / next。",
        "状态图控制 evidence、compute、verify、repair 等阶段跳转。",
        "ARCH-V5.2：no-CONCLUDE + any-phase answer，降低答案出口摩擦。",
        "max-step 后 forced answer，避免空提交影响可比较性。"
    ], size=1800)
    s.elements.append(textbox(900, emu(7.1), emu(1.35), emu(5.5), emu(3.9), [
        "phase node",
        "  role: 阶段职责",
        "  rules: 阶段内经验",
        "  allowlist: 可用工具",
        "  actions: 可输出动作",
        "  next: 合法后继与条件",
    ], size=1550, color=TEXT, fill="F8FAFC", line=LINE))
    slides.append(s)

    s = Slide()
    title(s, "旧 best 与新图差异", "新图更规范，但对小模型 executor 来说更规范不等于更高分")
    bullet_block(s, 0.8, 1.25, 11.7, [
        "旧 ARCH-V5.2 B_sharded 的收益更像：phase 少跳 + VERIFY/REPAIR 工具宽 + prompt-only 快路径 + WEB 硬预算。",
        "新 ARCH-V5.3 B1/B2 拆出 MEDIA_EVIDENCE、VERIFY_AND_ANSWER，语义更清楚。",
        "代价是 phase hop 增加，部分工具面收窄，9B 更容易在 24 步内拖到 forced answer。",
        "后续图结构优化要同时看 path cost、loop、missing transition 和 answer exit。"
    ], size=1850)
    slides.append(s)

    s = Slide()
    title(s, "B1 / B2 / B3 的演化", "核心变化是 critic 输入和 actor patch 权限逐步结构化")
    card(s, 0.75, 1.25, 3.7, 3.6, "B1 sharded", [
        "按 failure family 分桶",
        "aggregate 后给 actor",
        "graph 锁住",
        "主要改 rules / allowlist",
    ], color=ORANGE, fill=ORANGE_FILL)
    card(s, 4.85, 1.25, 3.7, 3.6, "B2 graph-aware", [
        "critic 输出 graph_structure_signals",
        "actor 可改 graph / Next",
        "实际 graph signature 基本没变",
        "说明 patch schema 仍不够明确",
    ], color=PURPLE, fill=PURPLE_FILL)
    card(s, 8.95, 1.25, 3.7, 3.6, "B3 graph_v3", [
        "Graph Critic 看结构统计",
        "Content Critics 看失败内容",
        "Aggregate Critic 合并去冲突",
        "目标：明确 graph + skill patch",
    ], color=BLUE, fill=BLUE_FILL)
    slides.append(s)

    s = Slide()
    title(s, "FLOW-batch-V3：分层 critic 闭环", "把结构奖励和内容奖励拆开，减少一个 critic 同时消化所有信号的负担")
    s.images.append(fit_image(BASE_DIR / "flow_batch_v3_layered_critic_v3.png", 0.55, 1.05, 12.2, 6.05))
    slides.append(s)

    s = Slide()
    title(s, "Benchmark 迁移：从 GAIA 压力测试走向 Skill 评测", "调研近两年 skill / skill learning 论文后，下一批实验优先和 EvoSkill / OfficeQA 对齐")
    card(s, 0.7, 1.22, 3.75, 2.35, "调研看到的主线", [
        "SkillsBench / SkillFlow / SkillLearnBench：skill-native",
        "ALFWorld / WebShop：经典 skill 对齐组",
        "OfficeQA / SealQA / BrowseComp：证据和文档 QA",
    ], color=BLUE, fill=BLUE_FILL)
    card(s, 4.8, 1.22, 3.75, 2.35, "EvoSkill 对齐价值", [
        "OfficeQA / SealQA / BrowseComp 都在同一条 skill 迁移线上",
        "失败分析 -> skill edit -> validation promotion",
        "OfficeQA 60.6% -> 67.9%",
        "SealQA 26.6% -> 38.7%",
    ], color=PURPLE, fill=PURPLE_FILL)
    card(s, 8.9, 1.22, 3.75, 2.35, "为什么先选 OfficeQA", [
        "本机已有 evoskill_corpus",
        "数据 / scorer / split / corpus 输入已补齐",
        "比 GAIA 更聚焦",
        "先验证 skill 闭环是否稳定提分",
    ], color=ORANGE, fill=ORANGE_FILL)
    bullet_block(s, 0.8, 4.05, 11.5, [
        "迁移策略：先 OfficeQA evoskill_corpus 跑 baseline vs skill 小闭环，再扩到 SealQA / BrowseComp。",
        "方法主干保持不变：Executor 仍加载 SKILL.md，轨迹进入 critic，actor 产出下一版 skill。",
        "评价目标从单一 GAIA 刷分变成：多个 benchmark 上相对各自 baseline 有稳定收益。"
    ], size=1750)
    slides.append(s)

    s = Slide()
    title(s, "OfficeQA / EvoSkill 口径：先跑文档 QA SkillRL", "任务更聚焦：给问题和文档语料，agent 找证据、抽答案，scorer 按容错口径评分")
    # Flow diagram.
    card(s, 0.65, 1.35, 2.15, 1.18, "Q", ["question"], color=BLUE, fill=BLUE_FILL)
    s.elements.append(textbox(800, emu(2.85), emu(1.67), emu(0.42), emu(0.35), ["→"], size=2300, bold=True, color=BLUE))
    card(s, 3.25, 1.35, 2.15, 1.18, "Corpus", ["TXT / PDF", "gold or route"], color=GREEN, fill=GREEN_FILL)
    s.elements.append(textbox(801, emu(5.45), emu(1.67), emu(0.42), emu(0.35), ["→"], size=2300, bold=True, color=BLUE))
    card(s, 5.85, 1.35, 2.15, 1.18, "Read", ["retrieve", "locate evidence"], color=PURPLE, fill=PURPLE_FILL)
    s.elements.append(textbox(802, emu(8.05), emu(1.67), emu(0.42), emu(0.35), ["→"], size=2300, bold=True, color=BLUE))
    card(s, 8.45, 1.35, 2.15, 1.18, "Exec", ["load skill", "answer"], color=BLUE, fill=BLUE_FILL)
    s.elements.append(textbox(803, emu(10.65), emu(1.67), emu(0.42), emu(0.35), ["→"], size=2300, bold=True, color=BLUE))
    card(s, 11.05, 1.35, 1.75, 1.18, "Score", ["tolerance", "reward"], color=ORANGE, fill=ORANGE_FILL)

    card(s, 0.75, 3.05, 3.7, 2.55, "规模与当前资产", [
        "officeqa_pro：133 题",
        "officeqa_full：246 题",
        "transformed TXT corpus：697 个 txt，约 383MB",
        "官方 PDF corpus：约 20GB，当前未下载",
    ], color=BLUE, fill=BLUE_FILL)
    card(s, 4.85, 3.05, 3.7, 2.55, "EvoSkill 对齐设置", [
        "input-mode：evoskill_corpus",
        "只给 question + corpus/data_dirs",
        "隐藏 source_files / docs / text",
        "train/val/test 固定切分已生成",
    ], color=PURPLE, fill=PURPLE_FILL)
    card(s, 8.95, 3.05, 3.7, 2.55, "影响性能的开关", [
        "TXT vs PDF/OCR",
        "gold 文件直给 vs corpus 路由",
        "容错阈值 τ = 0/0.01/0.025/0.05/0.10",
        "检索 top-k、文件路由、answer normalization",
    ], color=ORANGE, fill=ORANGE_FILL)
    s.elements.append(textbox(804, emu(0.85), emu(5.9), emu(11.6), emu(0.45), [
        "预期实验：同一 workflow 下先跑 no-skill baseline，再跑 boot skill / critic-actor skill，观察 OfficeQA、SealQA、BrowseComp 上的相对提升。"
    ], size=1650, color=TEXT, fill="FFFFFF", line=LINE))
    slides.append(s)

    s = Slide()
    title(s, "下一步", "GAIA 保留为回归和失败分析环境，主目标转成多 benchmark 相对 baseline 提升")
    bullet_block(s, 0.8, 1.25, 11.6, [
        "短期：完成 remain20 的 B1/B2/B3 评测，确认 B3 graph-value critic 是否真的改图。",
        "若 B3 继续回落：恢复旧 B 的关键能力，补 prompt-only 快路径、VERIFY/REPAIR 工具面、WEB 硬预算。",
        "迁移第一步：OfficeQA evoskill_corpus，先跑 no-skill baseline vs boot/critic-actor skill。",
        "迁移第二步：SealQA / BrowseComp / SkillsBench / SkillFlow，逐步验证跨 benchmark 泛化。",
        "主结论：价值在于把 skill 从文本规则推进到 graph + skill，再把 critic 推进到 graph critic + content critic。"
    ], size=1850)
    slides.append(s)

    return slides


def build_slides_v2() -> list[Slide]:
    slides: list[Slide] = []

    s = Slide()
    s.elements.append(rect(3, 0, 0, SLIDE_W, SLIDE_H, "F8FAFC", "F8FAFC"))
    s.elements.append(textbox(4, emu(0.72), emu(1.55), emu(11.8), emu(0.7), ["GAIA 上小模型 SkillRL 的阶段性进展"], size=3700, bold=True))
    s.elements.append(textbox(5, emu(0.78), emu(5.95), emu(5.8), emu(0.35), ["2026-05-07｜组会 V2"], size=1500, color=MUTED))
    slides.append(s)

    s = Slide()
    title(s, "角色图")
    s.images.append(fit_image(BASE_DIR / "gaia_skillrl_agent_roles_simple.png", 0.55, 1.05, 12.2, 6.0))
    slides.append(s)

    s = Slide()
    title(s, "当前实验配置")
    card(s, 0.7, 1.15, 3.85, 4.75, "测试环境", [
        "GAIA dev / validation_test",
        "Executor：Qwen3-8B-local 与 Qwen3.5-9B-local",
        "ARCH-V5.3：stale phase prompt hiding",
        "no-CONCLUDE / any-phase answer",
        "max-step 后 forced answer 防空答",
        "tool profile：atomic_v2",
    ], color=BLUE, fill=BLUE_FILL)
    card(s, 4.85, 1.15, 3.55, 4.75, "Web Search", [
        "主后端：Serper",
        "fallback：DDG",
        "配置：configs/search_runtime.json",
        "HTTP_PROXY=http://127.0.0.1:17890",
        "HTTPS_PROXY=http://127.0.0.1:17890",
        "启动时清理 ALL_PROXY",
    ], color=GREEN, fill=GREEN_FILL)
    card(s, 8.7, 1.15, 3.85, 4.75, "上下文与预算", [
        "MAX_CONTEXT_CHARS=0",
        "no-trunc 由 tokenizer guard 控制",
        "8B max_model_len=40960",
        "9B max_model_len=49152",
        "max_tokens=12288，margin=512",
        "max_steps=24，eval concurrency=20",
        "enable_thinking=1，thinking_budget=None",
    ], color=PURPLE, fill=PURPLE_FILL)
    slides.append(s)

    s = Slide()
    title(s, "A / B1 / B2 / B3 设计")
    card(s, 0.65, 1.05, 2.9, 1.38, "A full critic", [
        "full trajectory / full skill",
        "one critic -> actor",
    ], color=GREEN, fill=GREEN_FILL)
    card(s, 3.8, 1.05, 2.9, 1.38, "B1 sharded", [
        "failure family 分桶",
        "fixed graph + rules patch",
    ], color=ORANGE, fill=ORANGE_FILL)
    card(s, 6.95, 1.05, 2.9, 1.38, "B2 graph-aware", [
        "graph_structure_signals",
        "actor may patch graph",
    ], color=PURPLE, fill=PURPLE_FILL)
    card(s, 10.1, 1.05, 2.9, 1.38, "B3 graph_v3", [
        "structure + content critics",
        "aggregate -> actor",
    ], color=BLUE, fill=BLUE_FILL)
    s.images.append(fit_image(BASE_DIR / "flow_batch_v3_layered_critic_v3.png", 1.8, 2.45, 9.75, 4.38))
    s.elements.append(textbox(900, emu(0.78), emu(6.88), emu(11.75), emu(0.32), [
        "共同口径：同一 executor、tool profile、Serper/search、上下文预算和 scorer；B3 重点展示结构 critic 与内容 critic 分层合并。"
    ], size=1300, color=MUTED))
    slides.append(s)

    s = Slide()
    s.images.append(fit_image(BASE_DIR / "skill_graph_before_after_b3_example.png", 0.12, 0.12, 13.1, 7.25))
    slides.append(s)

    s = Slide()
    title(s, "8B / 9B 分数")
    s.elements.append(textbox(1000, emu(0.72), emu(0.95), emu(11.8), emu(0.34), [
        "来源：26.5.07_0123 主表 13:19 快照 + 26.5.05 9B boot-only 历史记录；done=已落盘/总题数。"
    ], size=1450, color=MUTED))

    def score_table(
        slide: Slide,
        base_id: int,
        x: float,
        y: float,
        title_text: str,
        rows: list[tuple[str, str, str, str, bool, bool]],
        *,
        accent: str,
        fill: str,
    ) -> None:
        slide.elements.append(textbox(base_id, emu(x), emu(y), emu(5.85), emu(0.42), [title_text], size=1650, bold=True, color=TEXT, fill=fill, line=accent))
        headers = ["run", "dev", "test", "done"]
        xs = [x, x + 1.95, x + 3.15, x + 4.35]
        ws = [1.85, 1.1, 1.1, 1.5]
        y0 = y + 0.52
        for j, h in enumerate(headers):
            slide.elements.append(textbox(base_id + 10 + j, emu(xs[j]), emu(y0), emu(ws[j]), emu(0.34), [h], size=1150, bold=True, color=TEXT, fill=GRAY_FILL, line=LINE))
        for i, row in enumerate(rows):
            yy = y0 + 0.39 * (i + 1)
            values = row[:4]
            highlight = {1: row[4], 2: row[5]}
            for j, value in enumerate(values):
                color = TEXT if j == 0 else MUTED
                cell_fill = GREEN_FILL if highlight.get(j, False) else "FFFFFF"
                cell_line = GREEN if highlight.get(j, False) else LINE
                slide.elements.append(textbox(base_id + 30 + i * 10 + j, emu(xs[j]), emu(yy), emu(ws[j]), emu(0.34), [value], size=1050, color=color, fill=cell_fill, line=cell_line))

    score_table(s, 1100, 0.65, 1.35, "Qwen3-8B-local", [
        ("direct", "13/83", "14/82", "82/83;80/82", False, False),
        ("BootV3", "19/83", "18/82", "83/83;82/82", False, False),
        ("BootV4", "22/83", "16/82", "83/83;82/82", True, False),
        ("BootV3 B1", "21/83", "14/82", "81/83;81/82", False, False),
        ("BootV3 B2", "16/83", "10/82", "81/83;82/82", False, False),
        ("BootV3 B3", "14/83", "19/82", "80/83;81/82", False, True),
        ("BootV4 B1", "17/83", "12/82", "81/83;82/82", False, False),
        ("BootV4 B2", "15/83", "16/82", "82/83;82/82", False, False),
        ("BootV4 B3", "21/83", "14/82", "83/83;82/82", False, False),
    ], accent=BLUE, fill=BLUE_FILL)
    score_table(s, 1500, 6.75, 1.35, "Qwen3.5-9B-local", [
        ("direct", "43/83", "29/82", "81/83;82/82", False, False),
        ("BootV3", "40/83", "36/82", "83/83;81/82", False, False),
        ("BootV4", "40/83", "29/82", "81/83;78/82", False, False),
        ("BootV3 B1", "38/83", "38/82", "81/83;81/82", False, True),
        ("BootV3 B2", "40/83", "33/82", "81/83;82/82", False, False),
        ("BootV3 B3", "38/83", "34/82", "81/83;81/82", False, False),
        ("BootV4 B1", "41/83", "31/82", "80/83;80/82", False, False),
        ("BootV4 B2", "41/83", "37/82", "82/83;82/82", False, True),
        ("BootV4 B3", "42/83", "34/82", "80/83;80/82", True, False),
    ], accent=PURPLE, fill=PURPLE_FILL)
    s.elements.append(textbox(1300, emu(0.72), emu(6.25), emu(11.7), emu(0.72), [
        "简析：8B 的可讲信号在 BootV4 dev 与 BootV3 B3 test；9B test 的好信号集中在 BootV3 B1 / BootV4 B2，BootV4 B3 dev 高但 test 未同步。",
        "9B BootV3/BootV4 来自 2026-05-05 ARCH5.3 boot-only；GPT-5.2 direct 因 provider/SSE 问题不纳入本页。"
    ], size=1180, color=MUTED, fill="FFFFFF", line=LINE))
    slides.append(s)

    s = Slide()
    title(s, "Benchmark 探索")
    card(s, 0.7, 1.2, 3.75, 2.35, "调研看到的主线", [
        "SkillsBench / SkillFlow / SkillLearnBench：skill-native",
        "ALFWorld / WebShop：经典 skill 对齐组",
        "OfficeQA / SealQA / BrowseComp：证据和文档 QA",
    ], color=BLUE, fill=BLUE_FILL)
    card(s, 4.8, 1.2, 3.75, 2.35, "EvoSkill 对齐价值", [
        "EvoSkill 跑 OfficeQA / SealQA",
        "SealQA skill 迁移到 BrowseComp",
        "失败分析 -> skill edit -> validation promotion",
        "OfficeQA 60.6% -> 67.9%",
        "SealQA 26.6% -> 38.7%",
    ], color=PURPLE, fill=PURPLE_FILL)
    card(s, 8.9, 1.2, 3.75, 2.35, "先选 OfficeQA", [
        "本机已有 evoskill_corpus",
        "数据 / scorer / split / corpus 输入已补齐",
        "比 GAIA 更聚焦",
        "先验证 skill 闭环是否稳定提分",
    ], color=ORANGE, fill=ORANGE_FILL)
    bullet_block(s, 0.8, 4.0, 11.5, [
        "迁移策略：先 OfficeQA evoskill_corpus 跑 baseline vs skill 小闭环，再扩到 SealQA / BrowseComp。",
        "工作流基本不变：Executor 加载 SKILL.md，轨迹进入 critic，actor 产出下一版 skill。",
        "评价目标从单一 GAIA 刷分变成：多个 benchmark 上相对各自 baseline 有稳定收益。"
    ], size=1750)
    slides.append(s)

    s = Slide()
    title(s, "OfficeQA / EvoSkill 口径")
    card(s, 0.65, 1.35, 2.15, 1.18, "Q", ["question"], color=BLUE, fill=BLUE_FILL)
    s.elements.append(textbox(800, emu(2.85), emu(1.67), emu(0.42), emu(0.35), ["→"], size=2300, bold=True, color=BLUE))
    card(s, 3.25, 1.35, 2.15, 1.18, "Corpus", ["TXT / PDF", "gold or route"], color=GREEN, fill=GREEN_FILL)
    s.elements.append(textbox(801, emu(5.45), emu(1.67), emu(0.42), emu(0.35), ["→"], size=2300, bold=True, color=BLUE))
    card(s, 5.85, 1.35, 2.15, 1.18, "Read", ["retrieve", "locate evidence"], color=PURPLE, fill=PURPLE_FILL)
    s.elements.append(textbox(802, emu(8.05), emu(1.67), emu(0.42), emu(0.35), ["→"], size=2300, bold=True, color=BLUE))
    card(s, 8.45, 1.35, 2.15, 1.18, "Exec", ["load skill", "answer"], color=BLUE, fill=BLUE_FILL)
    s.elements.append(textbox(803, emu(10.65), emu(1.67), emu(0.42), emu(0.35), ["→"], size=2300, bold=True, color=BLUE))
    card(s, 11.05, 1.35, 1.75, 1.18, "Score", ["tolerance", "reward"], color=ORANGE, fill=ORANGE_FILL)
    card(s, 0.75, 3.05, 3.7, 2.55, "规模与任务", [
        "full=246，pro=133",
        "697 个 transformed TXT，约 383MB",
        "原始 PDF 约 20GB",
        "通常需从 697 个文件中找 1-2 个 source docs",
        "再定位表格 / 行列 / 月份 / 口径",
    ], color=BLUE, fill=BLUE_FILL)
    card(s, 4.85, 3.05, 3.7, 2.55, "工作流保持", [
        "input-mode：evoskill_corpus",
        "Executor 加载 OfficeQA skill",
        "轨迹记录 route / table / calc 错误",
        "critic 生成 retrieval / table / answer patch",
        "actor 更新 SKILL.md",
    ], color=PURPLE, fill=PURPLE_FILL)
    card(s, 8.95, 3.05, 3.7, 2.55, "影响性能的开关", [
        "TXT vs PDF/OCR",
        "full corpus / retrieval_topk / oracle source_files",
        "容错：exact / multi-tolerance",
        "retrieval top-k 与 gold source recall",
        "answer normalization",
    ], color=ORANGE, fill=ORANGE_FILL)
    s.elements.append(textbox(804, emu(0.85), emu(5.9), emu(11.6), emu(0.45), [
        "近期验收：先跑 oracle_source_files 和 retrieval_topk 诊断，再比较 no-skill baseline、手写 skill、boot skill、critic-actor skill。"
    ], size=1650, color=TEXT, fill="FFFFFF", line=LINE))
    slides.append(s)

    return slides


def write_pptx(slides: list[Slide], out: Path) -> None:
    media_map: dict[Path, str] = {}
    for slide in slides:
        for img in slide.images:
            if img.path not in media_map:
                media_map[img.path] = f"image{len(media_map) + 1}{img.path.suffix.lower()}"

    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types(len(slides)))
        z.writestr("_rels/.rels", rels_xml([
            ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument", "ppt/presentation.xml"),
            ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties", "docProps/core.xml"),
            ("rId3", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties", "docProps/app.xml"),
        ]))
        z.writestr("docProps/core.xml", core_xml())
        z.writestr("docProps/app.xml", app_xml(len(slides)))
        pres_rels = [("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster", "slideMasters/slideMaster1.xml")]
        pres_rels.extend(
            (f"rId{i + 1}", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide", f"slides/slide{i}.xml")
            for i in range(1, len(slides) + 1)
        )
        z.writestr("ppt/presentation.xml", presentation_xml(len(slides)))
        z.writestr("ppt/_rels/presentation.xml.rels", rels_xml(pres_rels))
        z.writestr("ppt/slideMasters/slideMaster1.xml", slide_master_xml())
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", rels_xml([
            ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout", "../slideLayouts/slideLayout1.xml"),
            ("rId2", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme", "../theme/theme1.xml"),
        ]))
        z.writestr("ppt/slideLayouts/slideLayout1.xml", slide_layout_xml())
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", rels_xml([
            ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster", "../slideMasters/slideMaster1.xml")
        ]))
        z.writestr("ppt/theme/theme1.xml", theme_xml())

        for src, name in media_map.items():
            z.write(src, f"ppt/media/{name}")

        for i, slide in enumerate(slides, start=1):
            rels: list[tuple[str, str, str]] = []
            image_rels: list[tuple[str, str]] = []
            for j, img in enumerate(slide.images, start=1):
                rid = f"rId{j}"
                target = f"../media/{media_map[img.path]}"
                rels.append((rid, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image", target))
                image_rels.append((rid, target))
            z.writestr(f"ppt/slides/slide{i}.xml", slide_xml(slide, i, image_rels))
            z.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", rels_xml(rels))


def main() -> None:
    write_pptx(build_slides_v2(), OUT)
    print(OUT)


if __name__ == "__main__":
    main()
