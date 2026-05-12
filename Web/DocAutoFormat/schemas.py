"""共享数据模型、常量与 LLM 客户端。

被所有工作步骤模块引用，不包含业务逻辑。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import requests

# ── OOXML 命名空间 ───────────────────────────────────────

WORD_NS = (
    "http://schemas.openxmlformats.org/"
    "wordprocessingml/2006/main"
)
NSMAP = {"w": WORD_NS}


def qn(tag: str) -> str:
    """构造 w: 命名空间的 Clark 记法标签名。"""
    return f"{{{WORD_NS}}}{tag}"


# ── 映射表 ───────────────────────────────────────────────

OUTLINE_LABELS = {
    0: "一级标题", 1: "二级标题", 2: "三级标题",
    3: "四级标题", 4: "五级标题",
    None: "正文",
}

SCRIPTS = ["zh", "en"]

FONT_SIZE_MAP = {
    "初号": 84, "小初": 72, "一号": 52, "小一": 48,
    "二号": 44, "小二": 36, "三号": 32, "小三": 30,
    "四号": 28, "小四": 24, "五号": 21, "小五": 18,
}

LINE_SPACING_MAP = {
    "1": 240, "1.0": 240, "1.15": 276,
    "1.25": 300, "1.5": 360,
    "2": 480, "2.0": 480,
}

ALIGNMENT_MAP = {
    "left": "left", "center": "center",
    "right": "right", "justify": "both",
}


# ── 数据类 ───────────────────────────────────────────────

@dataclass
class ParagraphInfo:
    """从 document.xml 提取的一个段落。"""
    index: int
    text: str
    outline_level: int | None = None
    original_outline_level: int | None = None
    is_table_cell: bool = False
    xml_element: Any = None


@dataclass
class FormatRule:
    """一条 (大纲级别, 中/西文) 格式规则。"""
    outline_level: int | None
    script: str
    font_name: str | None = None
    font_size: str | None = None
    bold: bool | None = None
    line_spacing: str | None = None
    alignment: str | None = None


@dataclass
class MergedStyle:
    """合并 zh+en 规则后的一个完整样式定义。"""
    outline_level: int | None
    style_id: str
    style_name: str
    zh_font: str | None = None
    en_font: str | None = None
    font_size_half_pt: int | None = None
    bold: bool | None = None
    line_spacing_val: int | None = None
    line_spacing_rule: str = "auto"
    alignment: str | None = None


@dataclass
class PipelineResult:
    """pipeline 的返回结果。"""
    success: bool
    output_file: str = ""
    message: str = ""


# ── Ollama LLM 客户端 ───────────────────────────────────

class OllamaClient:
    """本地 Ollama /api/generate 的薄封装。"""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        timeout: int | None = None,
    ):
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen3:8b")
        self.base_url = (
            base_url
            or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        ).rstrip("/")
        self.url = self.base_url + "/api/generate"
        self.tags_url = self.base_url + "/api/tags"
        self.temperature = (
            temperature
            if temperature is not None
            else float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))
        )
        self.timeout = (
            timeout
            if timeout is not None
            else int(os.getenv("OLLAMA_TIMEOUT", "600"))
        )

    def health(self, timeout: int = 3) -> dict[str, Any]:
        """检查 Ollama 服务和目标模型是否可用。"""
        try:
            r = requests.get(self.tags_url, timeout=timeout)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            return {
                "ok": False,
                "model": self.model,
                "base_url": self.base_url,
                "msg": f"Ollama 未连接: {e}",
            }

        models = []
        for item in data.get("models", []):
            name = item.get("name") or item.get("model")
            if name:
                models.append(name)

        if self.model not in models:
            return {
                "ok": False,
                "model": self.model,
                "base_url": self.base_url,
                "models": models,
                "msg": f"本地模型未找到: {self.model}",
            }

        return {
            "ok": True,
            "model": self.model,
            "base_url": self.base_url,
            "models": models,
            "msg": "Ollama 已连接",
        }

    def generate(self, prompt: str) -> str:
        """发送 prompt，返回 JSON 文本。"""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "think": True,
            "format": "json",
            "options": {"temperature": self.temperature},
        }
        try:
            r = requests.post(
                self.url, json=payload, timeout=self.timeout,
            )
            r.raise_for_status()
            text = r.json().get("response", "")
        except requests.RequestException as e:
            raise RuntimeError(f"Ollama 请求失败: {e}") from e
        if not text:
            raise RuntimeError("Ollama 返回了空响应")
        return text


def clean_llm_json(text: str) -> str:
    """去除 <think> 标签，定位 JSON 起始位置。"""
    text = re.sub(
        r"<think>.*?</think>", "", text, flags=re.DOTALL,
    )
    text = text.strip()
    pos = text.find("{")
    if pos >= 0:
        text = text[pos:]
    return text
