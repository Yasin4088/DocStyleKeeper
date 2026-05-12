"""步骤 1-2: 读入 docx → 解压 → 拿 document.xml 和 styles.xml。

负责 ZIP 解压/打包 和 XML 文件的读写。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

from schemas import NSMAP


# ── 解压与打包 ───────────────────────────────────────────

def unzip_docx(docx_path: str | Path) -> Path:
    """将 .docx 解压到临时目录，返回目录路径。"""
    docx_path = Path(docx_path).resolve()
    if not docx_path.exists():
        raise FileNotFoundError(f"文件不存在: {docx_path}")
    if docx_path.suffix.lower() != ".docx":
        raise ValueError("仅支持 .docx 文件")

    temp_dir = Path(tempfile.mkdtemp(prefix="docaf_"))
    with zipfile.ZipFile(docx_path, "r") as zf:
        zf.extractall(temp_dir)
    return temp_dir


def rezip_docx(temp_dir: str | Path, output: str | Path) -> None:
    """将目录重新打包为 .docx。"""
    temp_dir = Path(temp_dir)
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(temp_dir):
            for f in files:
                fp = Path(root) / f
                zf.write(fp, fp.relative_to(temp_dir))


def cleanup_temp(temp_dir: str | Path) -> None:
    """删除临时目录。"""
    p = Path(temp_dir)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


# ── XML 解析与保存 ───────────────────────────────────────

def parse_xml(path: str | Path) -> etree._ElementTree:
    """解析一个 XML 文件。"""
    parser = etree.XMLParser(remove_blank_text=False)
    return etree.parse(str(path), parser)


def parse_document_xml(temp_dir: Path) -> etree._ElementTree:
    """解析 word/document.xml。"""
    p = temp_dir / "word" / "document.xml"
    if not p.exists():
        raise FileNotFoundError("document.xml 未找到")
    return parse_xml(p)


def parse_styles_xml(temp_dir: Path) -> etree._ElementTree:
    """解析 word/styles.xml。"""
    p = temp_dir / "word" / "styles.xml"
    if not p.exists():
        raise FileNotFoundError("styles.xml 未找到")
    return parse_xml(p)


def save_xml(
    tree: etree._ElementTree, path: str | Path,
) -> None:
    """将 lxml tree 写回磁盘。"""
    tree.write(
        str(path), encoding="UTF-8",
        xml_declaration=True, standalone=True,
    )


def save_document_xml(tree: etree._ElementTree, d: Path):
    save_xml(tree, d / "word" / "document.xml")


def save_styles_xml(tree: etree._ElementTree, d: Path):
    save_xml(tree, d / "word" / "styles.xml")


# ── 收集文档中已引用的样式 ID ────────────────────────────

def collect_used_style_ids(temp_dir: Path) -> set[str]:
    """扫描 document/header/footer 中引用的 styleId。"""
    word_dir = temp_dir / "word"
    ids: set[str] = set()

    for f in word_dir.iterdir():
        if not (f.is_file() and f.suffix == ".xml"):
            continue
        name = f.name.lower()
        prefixes = (
            "document", "header", "footer",
            "footnotes", "endnotes",
        )
        if not name.startswith(prefixes):
            continue
        tree = parse_xml(f)
        for tag in ("pStyle", "rStyle", "tblStyle"):
            xpath = f"//w:{tag}/@w:val"
            for val in tree.xpath(xpath, namespaces=NSMAP):
                ids.add(val)

    return ids
