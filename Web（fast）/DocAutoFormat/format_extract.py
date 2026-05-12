"""步骤 6: LLM 提取格式规则。

对每种使用到的大纲级别，调用 LLM 从用户自然语言要求中
提取格式规则 (大纲级别 + 中/西文 → 样式)，
然后合并 zh/en 规则为 MergedStyle。
"""

from __future__ import annotations

import json
import re

from schemas import (
    ALIGNMENT_MAP, FONT_SIZE_MAP, LINE_SPACING_MAP,
    OUTLINE_LABELS, SCRIPTS,
    DeepSeekClient, FormatRule, MergedStyle,
    clean_llm_json,
)


# ── 辅助函数 ─────────────────────────────────────────────

def _label(level: int | None) -> str:
    return OUTLINE_LABELS.get(level, f"大纲级别{level}")


def _style_id(level: int | None) -> str:
    return "DocAF_Body" if level is None else f"DocAF_L{level}"


def _style_name(level: int | None) -> str:
    return f"DocAF {_label(level)}"


def _none(val):
    if val is None:
        return None
    if isinstance(val, str):
        if val.strip().lower() in ("none", "null", ""):
            return None
    return val


def _bool(val):
    val = _none(val)
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        lo = val.strip().lower()
        if lo in ("true", "yes", "是"):
            return True
        if lo in ("false", "no", "否"):
            return False
    return None


def _text(val):
    val = _none(val)
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return str(val)
    return val.strip() or None if isinstance(val, str) else None


def _number(val) -> float | None:
    val = _text(val)
    if val is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", val)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _first_line_chars(val) -> int | None:
    n = _number(val)
    if n is None or n <= 0:
        return None
    return int(round(n * 100))


def _space_twips(val) -> int | None:
    n = _number(val)
    if n is None or n < 0:
        return None
    return int(round(n * 20))


# ── LLM Prompt 构建 ─────────────────────────────────────

def _build_prompt(req_text: str, level: int | None) -> str:
    label = _label(level)
    lv = level if level is not None else None
    lv_json = "null" if level is None else str(level)

    example = json.dumps({
        "rules": [
            {"outline_level": lv, "script": "zh",
             "font_name": "黑体", "font_size": "三号",
             "bold": True, "line_spacing": "1.5",
             "alignment": "center", "first_line_indent": "0",
             "space_before": "12", "space_after": "6"},
            {"outline_level": lv, "script": "en",
             "font_name": "Times New Roman",
             "font_size": "三号", "bold": True,
             "line_spacing": "1.5",
             "alignment": "center", "first_line_indent": "0",
             "space_before": "12", "space_after": "6"},
        ],
        "ignored_requirements": ["页边距", "页码"],
    }, ensure_ascii=False, indent=2)

    return "\n".join([
        req_text.strip(),
        "",
        "以上是一份文档格式要求说明。",
        f'请只总结"{label}"这一类段落的格式要求。',
        "输出 2 条规则，分别对应中文和西文。",
        "",
        "输出要求：",
        "1. JSON 格式: "
        '{"rules":[...], "ignored_requirements":[...]}',
        "2. rules 恰好 2 个对象，顺序: zh, en。",
        "3. 每个对象包含字段: outline_level, script,"
        " font_name, font_size, bold,"
        " line_spacing, alignment, first_line_indent,"
        " space_before, space_after",
        f"4. outline_level 固定填 {lv_json}",
        "5. font_name: 宋体/黑体/楷体/仿宋/"
        "Times New Roman/Arial/none",
        "6. font_size: 初号~小五 或 none",
        "7. bold: true/false/null",
        "8. line_spacing: 1.0/1.5/2.0 等或 none",
        "9. alignment: left/center/right/justify/none",
        "10. first_line_indent: 首行缩进字符数，如 2 或 none",
        "11. space_before/space_after: 段前/段后磅值，如 6 或 none",
        "12. 原文没提到的填 none, 加粗没提到填 null",
        "只输出 JSON。",
        "",
        "示例：",
        example,
    ])


# ── 解析 LLM 响应 ───────────────────────────────────────

def _parse_rules(
    json_text: str, level: int | None,
) -> list[FormatRule]:
    data = json.loads(json_text)
    items = data.get("rules", [])
    rules: list[FormatRule] = []
    for it in items:
        sc = _text(it.get("script"))
        if sc not in SCRIPTS:
            continue
        rules.append(FormatRule(
            outline_level=level,
            script=sc,
            font_name=_text(it.get("font_name")),
            font_size=_text(it.get("font_size")),
            bold=_bool(it.get("bold")),
            line_spacing=_text(it.get("line_spacing")),
            alignment=_text(it.get("alignment")),
            first_line_indent=_text(it.get("first_line_indent")),
            space_before=_text(it.get("space_before")),
            space_after=_text(it.get("space_after")),
        ))
    return rules


# ── 步骤 6 主函数: 提取 + 合并 ──────────────────────────

def extract_format_rules(
    req_text: str,
    used_levels: set[int | None],
    llm: DeepSeekClient,
) -> list[FormatRule]:
    """对每个大纲级别调 LLM，收集格式规则。"""
    def _sort(x):
        return (x is None, x if x is not None else 999)

    rules: list[FormatRule] = []
    for level in sorted(used_levels, key=_sort):
        label = _label(level)
        print(f"\n[LLM] 格式需求: {label}")
        resp = llm.generate(_build_prompt(req_text, level))
        cleaned = clean_llm_json(resp)
        print(f"[LLM] 响应: {cleaned[:200]}...")
        try:
            rules.extend(_parse_rules(cleaned, level))
        except Exception as exc:
            raise RuntimeError(
                f"格式解析失败 ({label}): {exc}"
            ) from exc
    return rules


def merge_rules(rules: list[FormatRule]) -> list[MergedStyle]:
    """将同一大纲级别的 zh/en 规则合并为 MergedStyle。"""
    grouped: dict[int | None, dict[str, FormatRule]] = {}
    for r in rules:
        grouped.setdefault(r.outline_level, {})[r.script] = r

    def _sort(kv):
        k = kv[0]
        return (k is None, k if k is not None else 999)

    def _pick(zh_val, en_val):
        return zh_val if zh_val is not None else en_val

    styles: list[MergedStyle] = []
    for level, sr in sorted(grouped.items(), key=_sort):
        zh = sr.get("zh")
        en = sr.get("en")

        zh_font = zh.font_name if zh else None
        en_font = (en.font_name if en else None) or zh_font

        sz = _pick(
            zh.font_size if zh else None,
            en.font_size if en else None,
        )
        bold = _pick(
            zh.bold if zh else None,
            en.bold if en else None,
        )
        ls = _pick(
            zh.line_spacing if zh else None,
            en.line_spacing if en else None,
        )
        al = _pick(
            zh.alignment if zh else None,
            en.alignment if en else None,
        )
        first_indent = _pick(
            zh.first_line_indent if zh else None,
            en.first_line_indent if en else None,
        )
        space_before = _pick(
            zh.space_before if zh else None,
            en.space_before if en else None,
        )
        space_after = _pick(
            zh.space_after if zh else None,
            en.space_after if en else None,
        )

        styles.append(MergedStyle(
            outline_level=level,
            style_id=_style_id(level),
            style_name=_style_name(level),
            zh_font=zh_font,
            en_font=en_font,
            font_size_half_pt=FONT_SIZE_MAP.get(sz) if sz else None,
            bold=bold,
            line_spacing_val=LINE_SPACING_MAP.get(ls) if ls else None,
            alignment=ALIGNMENT_MAP.get(al) if al else None,
            first_line_chars=_first_line_chars(first_indent),
            space_before_twips=_space_twips(space_before),
            space_after_twips=_space_twips(space_after),
        ))

    return styles
