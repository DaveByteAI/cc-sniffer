# CC Sniffer

A lightweight MITM proxy that captures and inspects LLM API requests in real time. Built for debugging Claude Code's prompt assembly, cache strategy, and tool definitions.

## What it does

Claude Code sends massive API requests — 80%+ of the payload is tool definitions, the rest is system prompts, conversation history, and model parameters. LLM Sniffer sits between Claude Code and the LLM backend, logging every request so you can see exactly what's being sent.

**Inspect:**
- Full request body with wire-order key structure
- All 40+ tool definitions with complete JSON schemas
- System prompt broken down by semantic section (billing, identity, harness, memory, environment)
- `cache_control` markers and ephemeral cache strategy
- Message history with expandable `tool_use` inputs and `tool_result` outputs
- Model parameters (`thinking`, `output_config`, `max_tokens`, `context_management`)

## Quick start

```bash
# 1. Clone
git clone https://github.com/your-username/cc-sniffer.git
cd cc-sniffer

# 2. Install dependencies (only httpx + fastapi + uvicorn)
pip install httpx fastapi uvicorn

# 3. Start the proxy
python cc_sniffer.py
```

Then point Claude Code at it:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8000 claude
```

Open `http://127.0.0.1:8000` in a browser to see the inspector UI.

## Architecture

```
Claude Code ──POST /v1/messages──▶ cc_sniffer.py ──forward──▶ DeepSeek / Anthropic API
                   │                      │
                   │               saves to disk
                   │                      │
                   ▼                      ▼
          http://127.0.0.1:8000    cc-sniffer-logs/
          (live inspector UI)      (JSON log files)
```

The proxy intercepts all HTTP requests, saves them as timestamped JSON files, and serves a real-time inspection UI with SSE push for new requests.

## Files

| File | Purpose |
|------|---------|
| `cc_sniffer.py` | FastAPI proxy server (~180 lines) |
| `sniffer_ui.html` | Inspector UI (~1000 lines, single-file, zero dependencies) |
| `cc-sniffer-logs/` | Auto-created directory for intercepted request logs |

Only the first two files are required. The logs directory is created automatically on first run.

## UI Tabs

**Overview** — Prompt composition bar (tools vs system vs messages breakdown by size), parameter summary, cache analysis, cache pollution detection between consecutive requests, and the request body top-level structure in wire order.

**Messages** — Full conversation history. Each content block is rendered by type: text, thinking, tool_use (expandable input), tool_result (expandable output). `cache_control` badges mark cached blocks. System reminders, local commands, and suggestion mode are tagged.

**System** — System prompt blocks labeled semantically: Billing Metadata, Identity, Harness Instructions (with subsections: Memory, Environment, Context Management, Session Guidance). Each block shows character count, token estimate, and cache status.

**Tools** — All tool definitions, searchable and collapsible. Each tool shows its description and full `input_schema`. Tools are sorted alphabetically with per-tool size indicators.

**Raw** — Syntax-highlighted JSON of the complete request body with copy-to-clipboard.

## Configuration

Environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_TARGET` | `https://api.deepseek.com/anthropic` | Upstream for `/v1/messages` and `/v1/models` |
| `OPENAI_TARGET` | `https://api.openai.com` | Upstream for `/v1/chat/completions` and `/v1/responses` |

Headers containing `authorization`, `x-api-key`, or `api-key` are redacted in stored logs.

## API endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Inspector UI |
| `GET /api/logs` | List 500 most recent log files (metadata only) |
| `GET /api/logs/{filename}` | Full JSON content of a single log |
| `GET /api/events` | SSE stream for real-time request notifications |
| `*` | Catch-all proxy — forwards to upstream after logging |

## KV Cache Considerations

If you're building a third-party inference engine that consumes these requests, naive prefix caching won't work:

| Issue | Detail | Fix |
|-------|--------|-----|
| **cch rotation** | `system[0]` billing header contains a 5-char hex that changes every request. Placed at prompt position 0. | Strip `system[0]` — it's metadata, not instruction text. |
| **tools position drift** | tools (87% of prompt, stable across all requests) sit *after* messages in wire order. As conversation grows, tool positions shift. | Move tools before messages, or cache in a separate slot. |
| **mid-conversation system** | Skills list injected as `role: "system"` in messages. Changes per project. | Treat as dynamic; don't let it break the stable prefix. |
| **thinking cleanup** | `context_management.edits` instructs the backend to strip previous `thinking` blocks. | Honor the directive, or prompt bloats with stale reasoning. |
| **cache_control: ephemeral** | Only system[1-2] and last user message are marked. Tools and history are *never* cached. 5-min TTL. | Maintain your own cache for stable blocks. |
| **envelope noise** | `time`, `content-length`, `body_sha256` change every request. | Don't feed envelope fields into the prompt. |

**Correct prompt assembly order:**

```
system[2] (harness) → system[1] (identity) → tools[] → messages[]
```

Strip `system[0]` and envelope fields. Cache system blocks and tools independently from the growing message history.

> 中文版见 [README_CN.md](README_CN.md)

## License

MIT
