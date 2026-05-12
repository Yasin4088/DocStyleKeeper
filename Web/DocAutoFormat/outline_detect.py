"""步骤 3-5: 大纲级别识别。

3. LLM 对大纲级别进行识别
4. 原文已经有大纲级别设定的段落保留不改
5. 统计所有使用了的大纲级别
"""

from __future__ import annotations

import json

from lxml import etree

from schemas import (
    NSMAP, OllamaClient, ParagraphInfo,
    clean_llm_json, qn,
)

BATCH_SIZE = 24


# ── 步骤 3 辅助: 从 XML 读取已有大纲级别 ────────────────

def _get_text(p_elem: etree._Element) -> str:
    """拼接段落中所有 w:t 文本。"""
    return "".join(
        t.text for t in p_elem.iter(qn("t")) if t.text
    )


def _outline_from_ppr(ppr) -> int | None:
    """从 pPr 读 outlineLvl。"""
    if ppr is None:
        return None
    el = ppr.find(f"{{{NSMAP['w']}}}outlineLvl")
    if el is not None:
        v = el.get(qn("val"))
        if v is not None and v.isdigit():
            return int(v)
    return None


def _outline_from_style(ppr, style_map) -> int | None:
    """看段落 pStyle 引用的样式是否自带 outlineLvl。"""
    if ppr is None:
        return None
    ps = ppr.find(f"{{{NSMAP['w']}}}pStyle")
    if ps is None:
        return None
    sid = ps.get(qn("val"))
    return style_map.get(sid) if sid else None


def _build_style_outline_map(
    styles_root: etree._Element,
) -> dict[str, int | None]:
    """从 styles.xml 构建 styleId → outlineLvl 映射。"""
    m: dict[str, int | None] = {}
    for s in styles_root.iter(qn("style")):
        sid = s.get(qn("styleId"))
        if sid is None:
            continue
        ppr = s.find(f"{{{NSMAP['w']}}}pPr")
        lvl = _outline_from_ppr(ppr)
        if lvl is not None:
            m[sid] = lvl
    return m


# ── 步骤 3: 提取段落 ────────────────────────────────────

def extract_paragraphs(
    doc_root: etree._Element,
    styles_root: etree._Element,
) -> list[ParagraphInfo]:
    """遍历 body 提取段落，同时读取已有大纲级别。"""
    style_map = _build_style_outline_map(styles_root)
    ns = NSMAP['w']
    body = doc_root.find(f"{{{ns}}}body")
    if body is None:
        return []

    paras: list[ParagraphInfo] = []
    idx = 0

    for child in body:
        tag = etree.QName(child).localname

        if tag == "p":
            text = _get_text(child)
            ppr = child.find(f"{{{ns}}}pPr")
            lvl = _outline_from_ppr(ppr)
            if lvl is None:
                lvl = _outline_from_style(ppr, style_map)

            paras.append(ParagraphInfo(
                index=idx, text=text,
                outline_level=lvl,
                original_outline_level=lvl,
                xml_element=child,
            ))
            idx += 1

        elif tag == "tbl":
            for p in child.iter(qn("p")):
                paras.append(ParagraphInfo(
                    index=idx, text=_get_text(p),
                    is_table_cell=True,
                    xml_element=p,
                ))
                idx += 1

    return paras


# ── 步骤 3-4: LLM 识别缺失的大纲级别 ───────────────────

def _build_prompt(batch: list[ParagraphInfo]) -> str:
    """构造让 LLM 判断大纲级别的 prompt。"""
    lines: list[str] = []
    for p in batch:
        t = p.text.replace("\n", " ").strip() or "(空段落)"
        lines.append(f'段落序号: "{p.index}", 内容: "{t}"')

    s, e = batch[0].index, batch[-1].index
    example = json.dumps(
        {"levels": [
            {"index": 0, "level": 0},
            {"index": 1, "level": "body"},
        ]}, ensure_ascii=False,
    )
    lines += [
        "",
        f"这是第 {s} 段到第 {e} 段的内容。",
        "请判断每个段落的大纲级别，输出 JSON：",
        example,
        "",
        "level 取值: 0=一级标题, 1=二级标题, "
        "2=三级标题, 3=四级标题, "
        '"body"=正文。',
        f"从段落 {s} 写到 {e}，不要漏掉。",
        "只输出 JSON。",
    ]
    return "\n".join(lines)


def _parse_response(text: str) -> dict[int, int | None]:
    """解析 LLM 的大纲识别 JSON。"""
    data = json.loads(text)
    items = data.get("levels", [])
    result: dict[int, int | None] = {}

    for item in items:
        idx = item.get("index")
        lvl = item.get("level")

        if isinstance(idx, str) and idx.isdigit():
            idx = int(idx)
        if not isinstance(idx, int):
            continue

        if isinstance(lvl, str):
            if lvl.lower() in ("body", "none", "null", ""):
                lvl = None
            elif lvl.isdigit():
                lvl = int(lvl)
            else:
                lvl = None
        if isinstance(lvl, int) and lvl > 8:
            lvl = None

        result[idx] = lvl
    return result


def detect_outline_levels(
    paras: list[ParagraphInfo],
    llm: OllamaClient,
) -> list[ParagraphInfo]:
    """步骤 3-4: 仅对缺失大纲级别的段落调 LLM。"""
    need = [
        p for p in paras
        if p.original_outline_level is None
        and not p.is_table_cell
        and p.text.strip()
    ]
    if not need:
        return paras

    level_map: dict[int, int | None] = {}

    for start in range(0, len(need), BATCH_SIZE):
        batch = need[start:start + BATCH_SIZE]
        s, e = batch[0].index, batch[-1].index
        print(f"[LLM] 大纲识别: 段落 {s}-{e}")

        resp = llm.generate(_build_prompt(batch))
        cleaned = clean_llm_json(resp)
        print(f"[LLM] 响应: {cleaned[:200]}...")

        try:
            level_map.update(_parse_response(cleaned))
        except Exception as exc:
            raise RuntimeError(
                f"大纲识别解析失败 ({s}-{e}): {exc}"
            ) from exc

    for p in paras:
        if (p.original_outline_level is None
                and not p.is_table_cell
                and p.index in level_map):
            p.outline_level = level_map[p.index]

    return paras


# ── 步骤 5: 统计使用了的大纲级别 ────────────────────────

def collect_used_levels(
    paras: list[ParagraphInfo],
) -> set[int | None]:
    """返回文档中实际出现的大纲级别集合。"""
    return {
        p.outline_level for p in paras if not p.is_table_cell
    }
