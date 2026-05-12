"""共享数据模型、常量与 LLM 客户端。

被所有工作步骤模块引用，不包含业务逻辑。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
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
    first_line_indent: str | None = None
    space_before: str | None = None
    space_after: str | None = None


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
    first_line_chars: int | None = None
    space_before_twips: int | None = None
    space_after_twips: int | None = None


@dataclass
class PipelineResult:
    """pipeline 的返回结果。"""
    success: bool
    output_file: str = ""
    message: str = ""


# ── DeepSeek LLM 客户端 ─────────────────────────────────

DEFAULT_DEEPSEEK_CONFIG = Path(__file__).resolve().parent / "deepseek_config.json"


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class DeepSeekClient:
    """DeepSeek OpenAI 兼容 /chat/completions 的薄封装。"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        timeout: int | None = None,
        max_tokens: int | None = None,
        thinking: str | None = None,
    ):
        self.config_path = Path(
            os.getenv("DEEPSEEK_CONFIG_FILE", str(DEFAULT_DEEPSEEK_CONFIG))
        )
        self.config_error = ""
        config = self._load_config()

        self.api_key = (
            api_key
            or os.getenv("DEEPSEEK_API_KEY")
            or config.get("api_key")
            or ""
        ).strip()
        self.model = (
            model
            or os.getenv("DEEPSEEK_MODEL")
            or config.get("model")
            or "deepseek-v4-flash"
        )
        self.base_url = (
            base_url
            or os.getenv("DEEPSEEK_BASE_URL")
            or config.get("base_url")
            or "https://api.deepseek.com"
        ).rstrip("/")
        self.url = self.base_url + "/chat/completions"
        self.models_url = self.base_url + "/models"
        self.temperature = (
            temperature
            if temperature is not None
            else _to_float(
                os.getenv("DEEPSEEK_TEMPERATURE")
                or config.get("temperature"),
                0.1,
            )
        )
        self.timeout = (
            timeout
            if timeout is not None
            else _to_int(
                os.getenv("DEEPSEEK_TIMEOUT")
                or config.get("timeout"),
                600,
            )
        )
        self.max_tokens = (
            max_tokens
            if max_tokens is not None
            else _to_int(
                os.getenv("DEEPSEEK_MAX_TOKENS")
                or config.get("max_tokens"),
                4096,
            )
        )
        self.thinking = (
            thinking
            or os.getenv("DEEPSEEK_THINKING")
            or config.get("thinking")
            or "disabled"
        ).strip().lower()
        if self.thinking not in {"enabled", "disabled"}:
            self.thinking = "disabled"

    def _load_config(self) -> dict[str, Any]:
        if not self.config_path.is_file():
            return {}
        try:
            data = json.loads(
                self.config_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, json.JSONDecodeError) as exc:
            self.config_error = f"DeepSeek 配置文件读取失败: {exc}"
            return {}
        if not isinstance(data, dict):
            self.config_error = "DeepSeek 配置文件必须是一个 JSON 对象"
            return {}
        return data

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def health(self, timeout: int = 3) -> dict[str, Any]:
        """检查 DeepSeek API Key 和目标模型是否可用。"""
        payload = {
            "ok": False,
            "provider": "deepseek",
            "model": self.model,
            "base_url": self.base_url,
            "config_path": str(self.config_path),
        }
        if self.config_error:
            return payload | {"msg": self.config_error}
        if not self.api_key:
            return payload | {
                "msg": f"未配置 DeepSeek API Key，请填写: {self.config_path}",
            }

        try:
            r = requests.get(
                self.models_url,
                headers=self._headers(),
                timeout=timeout,
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            return payload | {"msg": f"DeepSeek API 未连接: {e}"}

        models = []
        for item in data.get("data", []):
            name = item.get("id") or item.get("model")
            if name:
                models.append(name)

        if self.model not in models:
            return payload | {
                "models": models,
                "msg": f"DeepSeek 模型未找到: {self.model}",
            }

        return payload | {
            "ok": True,
            "models": models,
            "msg": "DeepSeek API 已连接",
        }

    def generate(self, prompt: str) -> str:
        """发送 prompt，返回 JSON 文本。"""
        if not self.api_key:
            raise RuntimeError(
                f"未配置 DeepSeek API Key，请填写: {self.config_path}"
            )

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是一个严格的 JSON 生成器。"
                        "除合法 JSON 外不要输出解释、Markdown 或额外文本。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "response_format": {"type": "json_object"},
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "thinking": {"type": self.thinking},
        }
        try:
            r = requests.post(
                self.url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            detail = ""
            response = getattr(e, "response", None)
            if response is not None:
                detail = response.text[:300]
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"DeepSeek 请求失败: {e}{suffix}") from e

        error = data.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else error
            raise RuntimeError(f"DeepSeek 返回错误: {message}")

        try:
            text = data["choices"][0]["message"].get("content", "")
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"DeepSeek 响应格式异常: {data}") from exc
        if not text:
            raise RuntimeError("DeepSeek 返回了空响应")
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
