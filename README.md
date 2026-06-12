# DocStyleKeeper

基于浏览器的 **Word（.docx）字体与样式统一工具**：在网页中配置正文与各级标题的中英文字体、字号、行距、对齐等参数，上传文档后由本地后端解析 OOXML、用大语言模型辅助识别段落大纲级别，再按你的样式重建 `styles.xml` / `document.xml` 并输出格式化后的 `.docx`。

---

## 功能概览

- **样式面板**：正文、一级～三级标题（四级及以下在管线中与三级标题共用配置）的中/英字体、字号、粗体、行距、对齐等（`Web（fast）` 中对标题还包含首行缩进、段前段后可配置项）。
- **文档处理**：解压 `.docx` → 解析 XML → **LLM 识别大纲级别**（含原文已有级别的保留与补全）→ 按网页配置生成合并样式 → 写回并重新打包。
- **任务与下载**：异步任务、`/jobs/{id}` 查询进度、`/download/{id}` 下载结果文件；支持在历史面板中清理任务相关临时文件。

---

## 仓库结构

| 目录 | 说明 |
|------|------|
| `Web/` | **本地模型版**：默认通过 [Ollama](https://ollama.com/) 调用本地 LLM（默认模型可通过环境变量指定）。适合完全离线或不愿使用云端 API 的场景。 |
| `Web（fast）/` | **云端 API 版**：通过 **DeepSeek** OpenAI 兼容接口调用模型，需在配置中填写 API Key。通常推理环境更易就绪、首包配置简单。 |

两套目录各自包含同名前端（`Word 字体样式统一工具.html`、`word.css`、`word.js`）与 Python 后端 `DocAutoFormat/`。**请勿同时启动两套后端**，它们默认都监听同一端口。

---

## 环境要求

- **Python 3**
- **依赖**（在各目录的 `DocAutoFormat` 下安装）：

```bash
pip install -r DocAutoFormat/requirements.txt
```

当前 `requirements.txt` 包含：`lxml`、`requests`。

### 使用 `Web/`（Ollama）时

- 安装并启动 **Ollama**，并拉取你在环境变量中指定的模型（默认为 `qwen3:8b`，可通过 `OLLAMA_MODEL` 覆盖）。

### 使用 `Web（fast）/`（DeepSeek）时

- 在 DeepSeek 控制台创建 API Key，并写入配置或环境变量（见下文）。

---

## 快速启动（Windows）

在 **对应版本** 的根目录下双击 **`启动网页.bat`**：脚本会打开新窗口运行 `python DocAutoFormat/server.py`，并在约 2 秒后尝试用浏览器打开：

`http://127.0.0.1:5001/`

也可手动执行：

```bash
cd Web\DocAutoFormat
python -B server.py
```

（将 `Web` 换成 `Web（fast）` 即启动另一套。）

服务启动后控制台会提示监听地址；默认 **`127.0.0.1:5001`**。

---

## 配置说明

### `Web/` — Ollama（环境变量，可选）

| 变量 | 含义 | 默认 |
|------|------|------|
| `OLLAMA_MODEL` | 使用的模型名 | `qwen3:8b` |
| `OLLAMA_BASE_URL` | Ollama 服务地址 | `http://127.0.0.1:11434` |
| `OLLAMA_TEMPERATURE` | 温度 | `0.1` |
| `OLLAMA_TIMEOUT` | 请求超时（秒） | `600` |

前端健康检查：`GET /health` 会返回 `ollama` 字段，用于判断本地模型是否可用。

### `Web（fast）/` — DeepSeek

优先顺序一般为 **环境变量** > **`DocAutoFormat/deepseek_config.json`**。

建议在 `deepseek_config.json` 中填写 `api_key`，或通过环境变量 `DEEPSEEK_API_KEY` 传入。其余可用环境变量包括：`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_TEMPERATURE`、`DEEPSEEK_TIMEOUT`、`DEEPSEEK_MAX_TOKENS`、`DEEPSEEK_THINKING`、`DEEPSEEK_CONFIG_FILE`（自定义配置文件路径）。

前端健康检查：`GET /health` 会返回 `deepseek` 字段。

---

## HTTP 接口（简要）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 主页面（静态资源同端口提供） |
| `GET` | `/health` | 后端与 LLM 可用性探测 |
| `POST` | `/local-process-word` | `multipart/form-data`：`file`（`.docx`）、`styleConfig`（JSON 字符串） |
| `GET` | `/jobs/{jobId}` | 查询任务状态与进度 |
| `GET` | `/download/{jobId}` | 处理成功后下载输出文件 |
| `DELETE` | `/jobs/{jobId}` | 删除任务及关联临时目录 |

上传大小上限：**80MB**（见服务端 `MAX_UPLOAD_BYTES`）。仅支持 **`.docx`**。

运行时产生的上传与输出文件位于各版本下的 `DocAutoFormat/runtime/`（可按需加入 `.gitignore` 以免提交大文件）。

---

## 常见问题

- **端口被占用**：修改对应 `server.py` 中的 `PORT`（并确保前端通过同一源访问，或直接打开后端提供的首页以使用相对路径）。
- **`Web/` 一直提示 Ollama 不可用**：确认 Ollama 已启动、`OLLAMA_MODEL` 已通过 `ollama pull` 拉取、且 `OLLAMA_BASE_URL` 正确。
- **`Web（fast）/` 提示 DeepSeek 不可用**：检查 API Key、网络与 `deepseek_config.json` / 环境变量是否一致。
