"""流程编排: 串联步骤 1-8。

步骤 1-2: docx_io      → 读入 docx, 解压拿 XML
步骤 3-5: outline_detect → LLM 大纲识别, 保留已有, 统计
步骤 6:   format_extract → LLM 提取格式规则, 合并样式
步骤 7-8: doc_rebuild   → 创建新 styles.xml + document.xml
"""

from __future__ import annotations

from typing import Callable

from docx_io import (
    cleanup_temp, collect_used_style_ids,
    parse_document_xml, parse_styles_xml,
    rezip_docx, save_document_xml, save_styles_xml,
    unzip_docx,
)
from doc_rebuild import build_new_styles, rebuild_document
from format_extract import extract_format_rules, merge_rules
from outline_detect import (
    collect_used_levels, detect_outline_levels,
    extract_paragraphs,
)
from schemas import MergedStyle, OUTLINE_LABELS, OllamaClient, PipelineResult


def _report(cb: Callable | None, msg: str):
    if cb:
        cb(msg)


def _preview(text: str, n: int = 40) -> str:
    s = text.replace("\n", " ").strip()
    return s[:n] + "..." if len(s) > n else s


def run_pipeline(
    input_file: str,
    requirement_text: str,
    output_file: str,
    progress_callback: Callable[[str], None] | None = None,
    merged_styles: list[MergedStyle] | None = None,
    llm: OllamaClient | None = None,
) -> PipelineResult:
    """执行完整的 DocAutoFormat 流水线。"""
    temp_dir = None
    try:
        llm = llm or OllamaClient()

        # ── 步骤 1-2: 读入并解压 ──
        _report(progress_callback, "正在解压文档...")
        temp_dir = unzip_docx(input_file)

        _report(progress_callback, "正在解析 XML...")
        doc_tree = parse_document_xml(temp_dir)
        sty_tree = parse_styles_xml(temp_dir)
        doc_root = doc_tree.getroot()
        sty_root = sty_tree.getroot()

        # ── 步骤 3: 提取段落信息 ──
        _report(progress_callback, "正在提取段落...")
        paras = extract_paragraphs(doc_root, sty_root)
        has = sum(
            1 for p in paras
            if p.original_outline_level is not None
        )
        need = sum(
            1 for p in paras
            if p.original_outline_level is None
            and not p.is_table_cell and p.text.strip()
        )
        print(f"[INFO] 段落 {len(paras)} 个, "
              f"已有大纲 {has}, 需识别 {need}")

        # ── 步骤 3-4: LLM 大纲识别 ──
        _report(progress_callback, "正在识别大纲级别...")
        paras = detect_outline_levels(paras, llm)

        print("\n[INFO] 大纲识别预览:")
        for p in paras[:10]:
            if p.is_table_cell:
                continue
            lbl = OUTLINE_LABELS.get(
                p.outline_level, f"L{p.outline_level}",
            )
            kept = "(保留)" if p.original_outline_level is not None else ""
            print(f"  [{p.index}] {lbl}{kept}: "
                  f"{_preview(p.text)}")

        # ── 步骤 5: 统计大纲级别 ──
        used = collect_used_levels(paras)
        labels = [
            OUTLINE_LABELS.get(lv, str(lv))
            for lv in sorted(
                used,
                key=lambda x: (x is None,
                               x if x is not None else 999),
            )
        ]
        print(f"[INFO] 大纲级别: {', '.join(labels)}")

        # ── 步骤 6: 格式规则 ──
        if merged_styles is None:
            _report(progress_callback, "正在理解格式要求...")
            rules = extract_format_rules(
                requirement_text, used, llm,
            )
            merged = merge_rules(rules)
        else:
            _report(progress_callback, "正在应用网页格式配置...")
            merged = merged_styles

        print(f"\n[INFO] 生成 {len(merged)} 个样式:")
        for ms in merged:
            print(f"  {ms.style_id}: "
                  f"zh={ms.zh_font}, en={ms.en_font}, "
                  f"size={ms.font_size_half_pt}, "
                  f"bold={ms.bold}, "
                  f"spacing={ms.line_spacing_val}, "
                  f"align={ms.alignment}")

        # ── 步骤 7: 创建新 styles.xml ──
        _report(progress_callback, "正在生成新样式...")
        used_ids = collect_used_style_ids(temp_dir)
        new_sty = build_new_styles(sty_root, merged, used_ids)
        sty_tree._setroot(new_sty)
        save_styles_xml(sty_tree, temp_dir)

        # ── 步骤 8: 重建 document.xml ──
        _report(progress_callback, "正在重建文档...")
        rebuild_document(doc_root, paras, merged)
        save_document_xml(doc_tree, temp_dir)

        # ── 打包输出 ──
        _report(progress_callback, "正在打包输出...")
        rezip_docx(temp_dir, output_file)
        print(f"[INFO] 输出: {output_file}")

        _report(progress_callback, "任务完成")
        return PipelineResult(True, output_file, "任务完成")

    except Exception as exc:
        print(f"[ERROR] {exc}")
        return PipelineResult(False, message=str(exc))

    finally:
        if temp_dir:
            cleanup_temp(temp_dir)
