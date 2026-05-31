# CC Sniffer

> English version: [README.md](README.md)

轻量级 MITM 代理，用于实时抓取和检查 LLM API 请求。专为调试 Claude Code 的 prompt 组装、缓存策略和工具定义而设计。

## 它能做什么

Claude Code 发送的 API 请求非常庞大——80%+ 的载荷是工具定义，其余是系统提示词、对话历史和模型参数。LLM Sniffer 位于 Claude Code 和 LLM 后端之间，记录每一个请求，让你能看清到底发送了什么。

**可以检查：**
- 完整的请求体，含 wire-order 的顶层 key 结构
- 全部 40+ 个工具定义及其 JSON Schema
- 按语义分段拆解的系统提示词（billing、identity、harness、memory、environment）
- `cache_control` 标记和 ephemeral cache 策略
- 对话历史，含可展开的 `tool_use` 输入和 `tool_result` 输出
- 模型参数（`thinking`、`output_config`、`max_tokens`、`context_management`）

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/your-username/cc-sniffer.git
cd cc-sniffer

# 2. 安装依赖（仅需 httpx + fastapi + uvicorn）
pip install httpx fastapi uvicorn

# 3. 启动代理
python llm_sniffer.py
```

然后将 Claude Code 指向它：

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8000 claude
```

浏览器打开 `http://127.0.0.1:8000` 查看 Inspector UI。

## 架构

```
Claude Code ──POST /v1/messages──▶ llm_sniffer.py ──转发──▶ DeepSeek / Anthropic API
                   │                      │
                   │               存入磁盘
                   │                      │
                   ▼                      ▼
          http://127.0.0.1:8000    cc-sniffer-logs/
          (实时 Inspector UI)      (JSON 日志文件)
```

代理拦截所有 HTTP 请求，保存为带时间戳的 JSON 文件，并通过 SSE 推送提供实时检查 UI。

## 文件

| 文件 | 用途 |
|------|------|
| `llm_sniffer.py` | FastAPI 代理服务器（~180 行） |
| `sniffer_ui.html` | Inspector UI（~1000 行，单文件，零依赖） |
| `cc-sniffer-logs/` | 自动创建的日志目录 |

仅需前两个文件即可运行。日志目录首次启动时自动创建。

## UI 标签页

**Overview** — Prompt 组成条形图（tools vs system vs messages 占比）、参数概要、缓存分析、连续请求间的 cache pollution 检测，以及 wire order 的 request body 顶层结构。

**Messages** — 完整对话历史。每个 content block 按类型渲染：text、thinking、tool_use（可展开 input）、tool_result（可展开 output）。`cache_control` 徽章标记已缓存块。system-reminder、local-command、suggestion mode 均有标签标注。

**System** — 系统提示词按语义标注：Billing Metadata、Identity、Harness Instructions（含 Memory、Environment、Context Management、Session Guidance 子段）。每块显示字符数、token 估算和缓存状态。

**Tools** — 所有工具定义，可搜索、可折叠。每个工具显示描述和完整 `input_schema`。按字母排序，显示每个工具的尺寸。

**Raw** — 带语法高亮的完整请求体 JSON，支持一键复制。

## 配置

环境变量：

| 变量 | 默认值 | 用途 |
|----------|---------|---------|
| `ANTHROPIC_TARGET` | `https://api.deepseek.com/anthropic` | `/v1/messages` 和 `/v1/models` 的上游地址 |
| `OPENAI_TARGET` | `https://api.openai.com` | `/v1/chat/completions` 和 `/v1/responses` 的上游地址 |

包含 `authorization`、`x-api-key` 或 `api-key` 的请求头在存储日志中会被脱敏。

## API 端点

| 端点 | 描述 |
|----------|-------------|
| `GET /` | Inspector UI |
| `GET /api/logs` | 列出最近 500 条日志（仅元数据） |
| `GET /api/logs/{filename}` | 单条日志完整 JSON |
| `GET /api/events` | SSE 实时通知流 |
| `*` | 代理转发 — 记录后转发到上游 |

## KV 缓存注意事项

如果你在构建消费这些请求的第三方推理引擎，朴素的 prefix cache 不会生效。以下是需要处理的关键点：

| 问题 | 细节 | 处理方式 |
|------|------|----------|
| **cch 滚动** | `system[0]` billing header 含 5 位 hex，每次请求随机变化。位于 prompt 最前端。 | 从 prompt 中移除 `system[0]`——它是元数据，不是指令。 |
| **tools 位置漂移** | tools（占 prompt 87%，内容全程不变）在 wire order 中排在 messages *之后*。对话增长 → 位置漂移 → 缓存失效。 | 将 tools 移到 messages 前面组装，或用独立 slot 缓存。 |
| **mid-conversation system** | Skills 列表作为 `role: "system"` 注入在 messages 中间，非顶层 `system[]`。随项目切换变化。 | 当作动态内容处理，不要让它破坏稳定的 cache 前缀。 |
| **thinking 清理** | `context_management.edits` 指示在组装前清除上一轮的 thinking 块。 | 遵循该指令，否则 prompt 被过期推理污染膨胀。 |
| **cache_control** | 仅零散几块标记了 cache_control（system[1-2]、最后一条用户输入）。tools 和历史对话*从不*缓存。5 分钟 TTL。 | 对稳定块（tools、harness）自行维护缓存。 |
| **信封噪音** | `time`、`content-length`、`body_sha256` 每次变化。 | 信封字段不要进入 prompt。 |

**正确的 prompt 组装顺序：**

```
system[2] (harness) → system[1] (identity) → tools[] → messages[]
```

移除 `system[0]` 和所有信封字段。将 system 块和 tools 与不断增长的消息历史分开缓存。

## License

MIT
