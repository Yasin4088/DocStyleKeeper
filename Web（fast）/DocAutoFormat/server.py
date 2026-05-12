"""本地 HTTP 后端服务。

负责接收网页上传的 .docx 和样式配置，调用 DocAutoFormat 管线，
并提供处理后文件的下载接口。
"""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from pipeline import run_pipeline
from schemas import DeepSeekClient, MergedStyle, OUTLINE_LABELS


HOST = "127.0.0.1"
PORT = 5001
MAX_UPLOAD_BYTES = 80 * 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent
WEB_ROOT = BASE_DIR.parent
RUNTIME_DIR = BASE_DIR / "runtime"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
OUTPUT_DIR = RUNTIME_DIR / "outputs"

JOB_EXECUTOR = ThreadPoolExecutor(max_workers=1)
JOBS_LOCK = threading.Lock()
JOBS: dict[str, "JobState"] = {}
JOB_TTL_SECONDS = 24 * 60 * 60
MAX_FINISHED_JOBS = 100

STATIC_ROUTES = {
    "/": "Word 字体样式统一工具.html",
    "/index.html": "Word 字体样式统一工具.html",
    "/word.css": "word.css",
    "/word.js": "word.js",
}

PROGRESS_BY_MESSAGE = (
    ("正在排队", 5),
    ("正在解压文档", 10),
    ("正在解析 XML", 20),
    ("正在提取段落", 30),
    ("正在识别大纲级别", 45),
    ("正在应用网页格式配置", 65),
    ("正在理解格式要求", 65),
    ("正在生成新样式", 75),
    ("正在重建文档", 85),
    ("正在打包输出", 95),
    ("任务完成", 100),
)


def _safe_filename(name: str) -> str:
    name = Path(name or "document.docx").name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    return name or "document.docx"


@dataclass
class JobState:
    job_id: str
    input_path: Path
    output_path: Path
    original_name: str
    output_name: str
    style_config: dict[str, Any]
    status: str = "queued"
    progress: int = 5
    message: str = "正在排队，等待 DeepSeek 处理..."
    logs: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: str = ""

    def snapshot(self, origin: str) -> dict[str, Any]:
        payload = {
            "code": 200,
            "jobId": self.job_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "logs": self.logs[-30:],
            "fileName": self.output_name,
        }
        if self.status == "done":
            payload["downloadUrl"] = f"{origin}/download/{self.job_id}"
        if self.error:
            payload["msg"] = self.error
        return payload


def _estimate_progress(message: str) -> int:
    for key, value in PROGRESS_BY_MESSAGE:
        if key in message:
            return value
    return 50


def _update_job(
    job_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    error: str | None = None,
) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        if status is not None:
            job.status = status
        if progress is not None:
            job.progress = max(job.progress, min(progress, 100))
        if message is not None:
            job.message = message
            job.logs.append(message)
        if error is not None:
            job.error = error
        job.updated_at = time.time()


def _cleanup_jobs() -> None:
    now = time.time()
    with JOBS_LOCK:
        expired = [
            job_id for job_id, job in JOBS.items()
            if job.status in {"done", "failed"}
            and now - job.updated_at > JOB_TTL_SECONDS
        ]
        finished = [
            job for job in JOBS.values()
            if job.status in {"done", "failed"}
        ]
        finished.sort(key=lambda job: job.updated_at)
        overflow = max(0, len(finished) - MAX_FINISHED_JOBS)
        expired.extend(job.job_id for job in finished[:overflow])
        for job_id in set(expired):
            JOBS.pop(job_id, None)


def _run_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return

    _update_job(
        job_id,
        status="running",
        progress=8,
        message="正在检查 DeepSeek API...",
    )

    llm = DeepSeekClient()
    health = llm.health(timeout=5)
    if not health.get("ok"):
        msg = health.get("msg", "DeepSeek 检查失败")
        _update_job(
            job_id,
            status="failed",
            progress=100,
            message=msg,
            error=msg,
        )
        return

    _update_job(
        job_id,
        progress=9,
        message=f"DeepSeek 模型已就绪: {health.get('model')}",
    )

    def _on_progress(message: str) -> None:
        _update_job(
            job_id,
            status="running",
            progress=_estimate_progress(message),
            message=message,
        )

    result = run_pipeline(
        str(job.input_path),
        _config_summary(job.style_config),
        str(job.output_path),
        progress_callback=_on_progress,
        merged_styles=build_styles_from_web_config(job.style_config),
        llm=llm,
    )

    if result.success:
        _update_job(
            job_id,
            status="done",
            progress=100,
            message="任务完成，可以下载处理后的 Word 文件",
        )
    else:
        msg = result.message or "处理失败"
        _update_job(
            job_id,
            status="failed",
            progress=100,
            message=msg,
            error=msg,
        )


def _num(value: Any, default_value: float | None = None) -> float | None:
    if value is None or value == "":
        return default_value
    try:
        return float(value)
    except (TypeError, ValueError):
        return default_value


def _half_points(value: Any) -> int | None:
    n = _num(value)
    if n is None or n <= 0:
        return None
    return int(round(n * 2))


def _line_spacing(value: Any) -> int | None:
    n = _num(value)
    if n is None or n <= 0:
        return None
    return int(round(n * 240))


def _space_twips(value: Any) -> int | None:
    n = _num(value)
    if n is None or n < 0:
        return None
    return int(round(n * 20))


def _first_line_chars(value: Any) -> int | None:
    n = _num(value)
    if n is None or n <= 0:
        return None
    return int(round(n * 100))


def _alignment(value: Any, default: str) -> str | None:
    mapping = {
        "left": "left",
        "center": "center",
        "right": "right",
        "justify": "both",
        "both": "both",
    }
    text = str(value or default).strip().lower()
    return mapping.get(text, mapping.get(default))


def _bool_zh(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"是", "true", "yes", "1"}:
        return True
    if text in {"否", "false", "no", "0"}:
        return False
    return None


def _font(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _style_id(level: int | None) -> str:
    return "DocAF_Body" if level is None else f"DocAF_L{level}"


def _style_name(level: int | None) -> str:
    return f"DocAF {OUTLINE_LABELS.get(level, f'大纲级别{level}')}"


def _style_from_config(
    level: int | None,
    config: dict[str, Any],
    *,
    bold: bool | None,
    line_spacing_val: int | None,
    alignment: str | None,
    first_line_chars: int | None = None,
    space_before_twips: int | None = None,
    space_after_twips: int | None = None,
) -> MergedStyle:
    return MergedStyle(
        outline_level=level,
        style_id=_style_id(level),
        style_name=_style_name(level),
        zh_font=_font(config.get("font_cn")),
        en_font=_font(config.get("font_en")),
        font_size_half_pt=_half_points(config.get("size")),
        bold=bold,
        line_spacing_val=line_spacing_val,
        alignment=alignment,
        first_line_chars=first_line_chars,
        space_before_twips=space_before_twips,
        space_after_twips=space_after_twips,
    )


def build_styles_from_web_config(
    style_config: dict[str, Any],
) -> list[MergedStyle]:
    """将网页表单的样式配置转换为管线可直接使用的样式定义。"""
    content = style_config.get("content") or {}
    h1 = style_config.get("h1") or {}
    h2 = style_config.get("h2") or {}
    h3 = style_config.get("h3") or {}

    styles = [
        _style_from_config(
            None,
            content,
            bold=None,
            line_spacing_val=_line_spacing(content.get("line")),
            alignment=_alignment(content.get("alignment"), "justify"),
            first_line_chars=_first_line_chars(
                content.get("first_indent")
            ),
            space_before_twips=_space_twips(content.get("space_before")),
            space_after_twips=_space_twips(content.get("space_after")),
        ),
        _style_from_config(
            0,
            h1,
            bold=_bool_zh(h1.get("bold")),
            line_spacing_val=_line_spacing(h1.get("line")),
            alignment=_alignment(h1.get("alignment"), "center"),
            first_line_chars=_first_line_chars(h1.get("first_indent")),
            space_before_twips=_space_twips(h1.get("space_before")),
            space_after_twips=_space_twips(h1.get("space_after")),
        ),
        _style_from_config(
            1,
            h2,
            bold=_bool_zh(h2.get("bold")),
            line_spacing_val=_line_spacing(h2.get("line")),
            alignment=_alignment(h2.get("alignment"), "center"),
            first_line_chars=_first_line_chars(h2.get("first_indent")),
            space_before_twips=_space_twips(h2.get("space_before")),
            space_after_twips=_space_twips(h2.get("space_after")),
        ),
    ]

    # 网页只配置到三级标题，四级及以下沿用三级标题配置。
    for level in range(2, 9):
        styles.append(
            _style_from_config(
                level,
                h3,
                bold=_bool_zh(h3.get("bold")),
                line_spacing_val=_line_spacing(h3.get("line")),
                alignment=_alignment(h3.get("alignment"), "center"),
                first_line_chars=_first_line_chars(
                    h3.get("first_indent")
                ),
                space_before_twips=_space_twips(h3.get("space_before")),
                space_after_twips=_space_twips(h3.get("space_after")),
            ),
        )

    return styles


def _config_summary(style_config: dict[str, Any]) -> str:
    return json.dumps(style_config, ensure_ascii=False, indent=2)


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "DocAutoFormatHTTP/1.0"

    def _origin(self) -> str:
        host = self.headers.get("Host", f"{HOST}:{PORT}")
        return f"http://{host}"

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS",
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_api_error(
        self,
        status: int,
        message: str,
    ) -> None:
        self._send_json(status, {"code": status, "msg": message})

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            deepseek = DeepSeekClient().health(timeout=2)
            self._send_json(
                HTTPStatus.OK,
                {
                    "code": 200,
                    "status": "ok",
                    "deepseek": deepseek,
                },
            )
            return

        if path in STATIC_ROUTES:
            self._send_static(STATIC_ROUTES[path])
            return

        match = re.fullmatch(r"/jobs/([0-9a-f]{32})", path)
        if match:
            self._send_job_status(match.group(1))
            return

        match = re.fullmatch(r"/download/([0-9a-f]{32})", path)
        if match:
            self._send_download(match.group(1))
            return

        self._send_api_error(HTTPStatus.NOT_FOUND, "接口不存在")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/local-process-word":
            self._send_api_error(HTTPStatus.NOT_FOUND, "接口不存在")
            return
        self._handle_process_word()

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        match = re.fullmatch(r"/download/([0-9a-f]{32})", path)
        if match:
            self._delete_history_file(match.group(1))
            return
        self._send_api_error(HTTPStatus.NOT_FOUND, "接口不存在")

    def _read_body(self) -> bytes:
        length_text = self.headers.get("Content-Length", "0")
        try:
            length = int(length_text)
        except ValueError:
            raise ValueError("请求长度不合法")
        if length <= 0:
            raise ValueError("请求体为空")
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("文件过大，请控制在 80MB 以内")
        return self.rfile.read(length)

    def _parse_multipart(
        self,
        body: bytes,
    ) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("请求必须使用 multipart/form-data")

        raw = (
            f"Content-Type: {content_type}\r\n"
            "MIME-Version: 1.0\r\n\r\n"
        ).encode("utf-8") + body
        message = BytesParser(policy=default).parsebytes(raw)
        if not message.is_multipart():
            raise ValueError("无法解析上传表单")

        fields: dict[str, str] = {}
        files: dict[str, dict[str, Any]] = {}

        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue

            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename:
                files[name] = {
                    "filename": _safe_filename(filename),
                    "content": payload,
                }
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = payload.decode(charset, errors="replace")

        return fields, files

    def _handle_process_word(self) -> None:
        try:
            _cleanup_jobs()
            body = self._read_body()
            fields, files = self._parse_multipart(body)

            uploaded = files.get("file")
            if not uploaded:
                raise ValueError("缺少上传文件")

            original_name = uploaded["filename"]
            if Path(original_name).suffix.lower() != ".docx":
                raise ValueError("仅支持 .docx 文件")

            style_raw = fields.get("styleConfig", "{}")
            try:
                style_config = json.loads(style_raw)
            except json.JSONDecodeError as exc:
                raise ValueError("样式配置不是合法 JSON") from exc
            if not isinstance(style_config, dict):
                raise ValueError("样式配置格式不正确")

            job_id = uuid.uuid4().hex
            upload_dir = UPLOAD_DIR / job_id
            output_dir = OUTPUT_DIR / job_id
            upload_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            input_path = upload_dir / original_name
            input_path.write_bytes(uploaded["content"])

            output_name = f"{input_path.stem}-formatted.docx"
            output_path = output_dir / _safe_filename(output_name)

            job = JobState(
                job_id=job_id,
                input_path=input_path,
                output_path=output_path,
                original_name=original_name,
                output_name=output_path.name,
                style_config=style_config,
            )
            with JOBS_LOCK:
                JOBS[job_id] = job

            JOB_EXECUTOR.submit(_run_job, job_id)

            self._send_json(
                HTTPStatus.OK,
                {
                    "code": 202,
                    "msg": "任务已提交",
                    "jobId": job_id,
                    "statusUrl": f"{self._origin()}/jobs/{job_id}",
                    "fileName": output_path.name,
                },
            )
        except ValueError as exc:
            self._send_json(
                HTTPStatus.OK,
                {"code": 400, "msg": str(exc)},
            )
        except Exception as exc:
            self._send_json(
                HTTPStatus.OK,
                {"code": 500, "msg": f"后端处理异常: {exc}"},
            )

    def _send_job_status(self, job_id: str) -> None:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            payload = job.snapshot(self._origin()) if job else None
        if payload is None:
            self._send_api_error(HTTPStatus.NOT_FOUND, "任务不存在或已清理")
            return
        self._send_json(HTTPStatus.OK, payload)

    def _send_download(self, job_id: str) -> None:
        output_dir = OUTPUT_DIR / job_id
        if not output_dir.is_dir():
            self._send_api_error(HTTPStatus.NOT_FOUND, "下载文件不存在")
            return

        files = [p for p in output_dir.iterdir() if p.is_file()]
        if not files:
            self._send_api_error(HTTPStatus.NOT_FOUND, "下载文件不存在")
            return

        file_path = files[0]
        content_type = (
            mimetypes.guess_type(file_path.name)[0]
            or "application/octet-stream"
        )
        quoted_name = quote(file_path.name)

        self.send_response(HTTPStatus.OK)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{quoted_name}",
        )
        self.end_headers()
        with file_path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def _delete_history_file(self, job_id: str) -> None:
        deleted = False
        for root in (OUTPUT_DIR, UPLOAD_DIR):
            target = (root / job_id).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError:
                continue
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
                deleted = True

        with JOBS_LOCK:
            JOBS.pop(job_id, None)

        self._send_json(
            HTTPStatus.OK,
            {
                "code": 200,
                "msg": "历史文件已删除" if deleted else "历史文件已不存在",
                "deleted": deleted,
            },
        )

    def _send_static(self, route_file: str) -> None:
        file_path = (WEB_ROOT / route_file).resolve()
        try:
            file_path.relative_to(WEB_ROOT)
        except ValueError:
            self._send_api_error(HTTPStatus.FORBIDDEN, "禁止访问该文件")
            return

        if not file_path.is_file():
            self._send_api_error(HTTPStatus.NOT_FOUND, "页面文件不存在")
            return

        content_type = (
            mimetypes.guess_type(file_path.name)[0]
            or "application/octet-stream"
        )
        if file_path.suffix.lower() in {".html", ".css", ".js"}:
            content_type += "; charset=utf-8"

        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[HTTP] {self.address_string()} - {fmt % args}")


def main() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    httpd = ThreadingHTTPServer((HOST, PORT), ApiHandler)
    print(f"DocAutoFormat 后端已启动: http://{HOST}:{PORT}")
    print(f"网页入口: http://{HOST}:{PORT}/")
    print("按 Ctrl+C 停止服务。")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
