"""步骤 7-8: 文档重建。

7. 创建新的 styles.xml —— 保留必需样式，添加 DocAF 自定义样式
8. 重建 document.xml —— 每段引用对应样式，清除冲突的内联格式
"""

from __future__ import annotations

from copy import deepcopy

from lxml import etree

from schemas import MergedStyle, NSMAP, ParagraphInfo, qn


# ═══════════════════════════════════════════════════════════
# 步骤 7: 创建新的 styles.xml
# ═══════════════════════════════════════════════════════════

ALWAYS_KEEP = {
    "Normal", "Default Paragraph Font",
    "Normal Table", "Table Grid",
    "header", "footer",
    "footnote text", "endnote text",
    "Hyperlink", "page number",
}


def _find_normal_id(styles_root: etree._Element) -> str:
    """找到默认段落样式的 styleId。"""
    for s in styles_root.iter(qn("style")):
        if (s.get(qn("type")) == "paragraph"
                and s.get(qn("default")) == "1"):
            sid = s.get(qn("styleId"))
            if sid:
                return sid
    return "Normal"


def _style_name(el: etree._Element) -> str | None:
    n = el.find(f"{{{NSMAP['w']}}}name")
    return n.get(qn("val")) if n is not None else None


def _based_on_chain(
    sid: str,
    all_styles: dict[str, etree._Element],
    visited: set[str] | None = None,
) -> set[str]:
    """递归收集样式依赖链。"""
    if visited is None:
        visited = set()
    if sid in visited:
        return visited
    visited.add(sid)
    el = all_styles.get(sid)
    if el is None:
        return visited
    for tag in ("basedOn", "link"):
        ref = el.find(f"{{{NSMAP['w']}}}{tag}")
        if ref is not None:
            rid = ref.get(qn("val"))
            if rid:
                _based_on_chain(rid, all_styles, visited)
    return visited


def _make_sub(parent, tag, **attrs):
    """创建 w: 命名空间子元素。"""
    el = etree.SubElement(parent, qn(tag))
    for k, v in attrs.items():
        if v is None:
            continue
        el.set(qn(k), str(v))
    return el


def _spacing_attrs(ms: MergedStyle) -> dict[str, str]:
    attrs: dict[str, str] = {}
    if ms.line_spacing_val is not None:
        attrs["line"] = str(ms.line_spacing_val)
        attrs["lineRule"] = ms.line_spacing_rule
    if ms.space_before_twips is not None:
        attrs["before"] = str(ms.space_before_twips)
    if ms.space_after_twips is not None:
        attrs["after"] = str(ms.space_after_twips)
    return attrs


def _build_style_element(
    ms: MergedStyle, normal_id: str,
) -> etree._Element:
    """为一个 MergedStyle 创建 <w:style> 节点。"""
    s = etree.Element(qn("style"))
    s.set(qn("type"), "paragraph")
    s.set(qn("customStyle"), "1")
    s.set(qn("styleId"), ms.style_id)

    _make_sub(s, "name", val=ms.style_name)
    _make_sub(s, "basedOn", val=normal_id)
    etree.SubElement(s, qn("qFormat"))

    # 段落属性
    ppr = etree.SubElement(s, qn("pPr"))
    if ms.outline_level is not None:
        _make_sub(ppr, "outlineLvl", val=str(ms.outline_level))
    spacing_attrs = _spacing_attrs(ms)
    if spacing_attrs:
        _make_sub(ppr, "spacing", **spacing_attrs)
    if ms.alignment is not None:
        _make_sub(ppr, "jc", val=ms.alignment)
    if ms.first_line_chars is not None:
        _make_sub(ppr, "ind", firstLineChars=str(ms.first_line_chars))

    # 字符属性
    rpr = etree.SubElement(s, qn("rPr"))
    if ms.zh_font or ms.en_font:
        fa = {}
        if ms.en_font:
            fa.update(ascii=ms.en_font, hAnsi=ms.en_font,
                      cs=ms.en_font)
        if ms.zh_font:
            fa["eastAsia"] = ms.zh_font
        _make_sub(rpr, "rFonts", **fa)

    if ms.bold is True:
        etree.SubElement(rpr, qn("b"))
        etree.SubElement(rpr, qn("bCs"))
    elif ms.bold is False:
        _make_sub(rpr, "b", val="0")
        _make_sub(rpr, "bCs", val="0")

    if ms.font_size_half_pt is not None:
        v = str(ms.font_size_half_pt)
        _make_sub(rpr, "sz", val=v)
        _make_sub(rpr, "szCs", val=v)

    return s


def build_new_styles(
    styles_root: etree._Element,
    merged: list[MergedStyle],
    used_ids: set[str],
) -> etree._Element:
    """步骤 7: 构建新的 styles 根节点。"""
    normal_id = _find_normal_id(styles_root)

    # 索引所有现有样式
    all_styles: dict[str, etree._Element] = {}
    for el in styles_root.iter(qn("style")):
        sid = el.get(qn("styleId"))
        if sid:
            all_styles[sid] = el

    # 计算需要保留的样式 ID (含依赖链)
    keep: set[str] = set()
    for sid in used_ids:
        keep |= _based_on_chain(sid, all_styles)
    for sid, el in all_styles.items():
        if el.get(qn("default")) == "1":
            keep.add(sid)
        name = _style_name(el)
        if name and name in ALWAYS_KEEP:
            keep |= _based_on_chain(sid, all_styles)

    docaf_ids = {ms.style_id for ms in merged}

    # 深拷贝后删除不需要的样式
    new_root = deepcopy(styles_root)
    to_rm = [
        el for el in new_root.iter(qn("style"))
        if el.get(qn("styleId")) not in keep
        and el.get(qn("styleId")) not in docaf_ids
    ]
    for el in to_rm:
        p = el.getparent()
        if p is not None:
            p.remove(el)

    # 删除同名旧 DocAF 样式
    for el in list(new_root.iter(qn("style"))):
        if el.get(qn("styleId")) in docaf_ids:
            p = el.getparent()
            if p is not None:
                p.remove(el)

    # 追加新 DocAF 样式
    for ms in merged:
        new_root.append(_build_style_element(ms, normal_id))

    return new_root


# ═══════════════════════════════════════════════════════════
# 步骤 8: 重建 document.xml
# ═══════════════════════════════════════════════════════════

_CLEAR_RPR_TAGS = {"rFonts", "sz", "szCs", "b", "bCs"}
_CLEAR_PPR_TAGS = {"jc", "spacing", "outlineLvl", "ind"}


def _clear_run_fmt(run: etree._Element) -> None:
    """清除 run 中与样式冲突的内联格式。"""
    rpr = run.find(f"{{{NSMAP['w']}}}rPr")
    if rpr is None:
        return
    for ch in list(rpr):
        if etree.QName(ch).localname in _CLEAR_RPR_TAGS:
            rpr.remove(ch)


def _clear_ppr_fmt(ppr: etree._Element) -> None:
    """清除 pPr 中与样式冲突的内联格式。"""
    for ch in list(ppr):
        if etree.QName(ch).localname in _CLEAR_PPR_TAGS:
            ppr.remove(ch)
    # pPr 内嵌的 rPr 也清理
    rpr = ppr.find(f"{{{NSMAP['w']}}}rPr")
    if rpr is not None:
        for ch in list(rpr):
            if etree.QName(ch).localname in _CLEAR_RPR_TAGS:
                rpr.remove(ch)


def _set_pstyle(p: etree._Element, style_id: str):
    """设置段落的 w:pStyle 引用。"""
    ns = NSMAP['w']
    ppr = p.find(f"{{{ns}}}pPr")
    if ppr is None:
        ppr = etree.SubElement(p, qn("pPr"))
        p.insert(0, ppr)

    _clear_ppr_fmt(ppr)

    ps = ppr.find(f"{{{ns}}}pStyle")
    if ps is None:
        ps = etree.SubElement(ppr, qn("pStyle"))
        ppr.insert(0, ps)
    ps.set(qn("val"), style_id)


def rebuild_document(
    doc_root: etree._Element,
    paras: list[ParagraphInfo],
    merged: list[MergedStyle],
) -> etree._Element:
    """步骤 8: 更新段落样式引用，清除冲突内联格式。"""
    level_map = {ms.outline_level: ms for ms in merged}
    para_map = {p.index: p for p in paras}

    body = doc_root.find(f"{{{NSMAP['w']}}}body")
    if body is None:
        return doc_root

    idx = 0
    for elem in body.iter(qn("p")):
        info = para_map.get(idx)
        idx += 1
        if info is None or info.is_table_cell:
            continue

        lvl = info.outline_level
        if lvl not in level_map and None in level_map:
            lvl = None
        ms = level_map.get(lvl)
        if ms is None:
            continue

        _set_pstyle(elem, ms.style_id)
        for run in elem.iter(qn("r")):
            _clear_run_fmt(run)

    return doc_root
