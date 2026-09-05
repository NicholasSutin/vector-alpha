# PRISM agent integration brief

You are a coding agent working inside an application repository. Your job is to wire live tracing into PRISM so sessions, model calls, tool use, errors and latency appear in the PRISM dashboard.

Read this document fully before editing. Propose a plan, then make the smallest change that emits real traces.

## Non-negotiables

- Authenticate with the header `X-PRISMtrace-Key`. This is **not** a bearer token. `Authorization: Bearer` carries dashboard sessions and will 401 an API key.
- Do **not** invent credentials, project ids or hosts. Use exactly the values the operator supplies.
- Do **not** install an npm package. None exists. For JavaScript and TypeScript, POST to the HTTP endpoint.
- Do **not** commit secrets. `.env` stays untracked; put key **names** only in `.env.example`.
- Do **not** report success from a handshake alone. A handshake proves a credential, not an integration.
- Do **not** point production traffic at an unverified setup without explicit approval. Instrument staging first.
- Editing files is not the deliverable. A trace arriving in PRISM is.

## Required inputs

| Variable | Format | Where the operator finds it |
| --- | --- | --- |
| `PRISMTRACE_HOST` | `https://prism.blockconvey.com` | Fixed. This is the only public host. |
| `PRISMTRACE_PROJECT_ID` | UUID | Settings, Project tab |
| `PRISMTRACE_API_KEY` | `pt-sk-...` | API keys page. Shown once at creation. |

If any is missing, stop and ask. Do not guess and do not read them out of an unrelated service.

## Step 1: choose a path

Choose from repository evidence, not from what the operator says the stack is. Grep the repository. Take the first rule that fires.

| Evidence | Path |
| --- | --- |
| `langchain`, `langgraph`, `LlmAgent`, `litellm` or `agents.Runner` in Python | Python SDK |
| `anthropic.Anthropic`, `openai.OpenAI`, `google.generativeai` or `AzureOpenAI` called directly, no framework wrapper | Zero-code proxy |
| `package.json`, `tsconfig.json`, `.ts` / `.tsx`, Next.js, Vercel AI SDK | HTTP ingest |
| Anything else | HTTP ingest |

AWS Bedrock is none of these. It connects with AWS credentials stored in the PRISM dashboard, not from application code.

Do not run `pip install` in a repository that contains no Python.

## Step 2: instrument

### Symbols that exist

Import these from `prismtrace`. The distribution is `prismtrace-sdk`; the import package is `prismtrace`. They differ on purpose.

| Symbol | Use for |
| --- | --- |
| `PRISMtrace` | Manual client. Has `trace_llm`, `submit_trajectory` and a `trace` decorator. |
| `PRISMtraceCallbackHandler` | LangChain |
| `PRISMtraceLangGraphHandler`, `wrap_langgraph` | LangGraph |
| `PRISMtraceADKAdapter` | Google ADK |
| `install_litellm` | LiteLLM |
| `install_openai_agents` | OpenAI Agents SDK |
| `ClaudeAgentTracer` | Anthropic Messages API |
| `PRISMtraceVoiceTracer` | ElevenLabs voice agents |

### Symbols that do not exist

Do not write any of these. Each has been produced by a model before, and each fails on the import line.

- `PRISMtraceLangchainCallback` — the export is `PRISMtraceCallbackHandler`
- `@prismtrace/sdk`, `prismtrace-js`, or any npm package
- `monitor()`, `wrap_bedrock()`, or a `blockconvey` module
- `prism-sdk` or `prismsdk` as the pip distribution — it is `prismtrace-sdk`
- An OpenTelemetry ingest endpoint. `/api/otlp/v1/traces` is not served.

### Install

```bash
pip install "prismtrace-sdk>=0.4.0"
```

### LangChain

```python
import os
from prismtrace import PRISMtraceCallbackHandler

handler = PRISMtraceCallbackHandler(
    api_key=os.environ["PRISMTRACE_API_KEY"],
    project_id=os.environ["PRISMTRACE_PROJECT_ID"],
    host=os.environ["PRISMTRACE_HOST"],
    agent_name="my-agent",
    session_id="conversation-1",
)
# Pass callbacks=[handler] into your chain, agent or RunnableConfig.
handler.flush()
```

Success for LangChain is a real application invocation that flushes spans. A curl to `/api/traces` proves the credential only; it does not attach callbacks.

### LangGraph

```python
import os
from prismtrace import PRISMtraceLangGraphHandler, wrap_langgraph

handler = PRISMtraceLangGraphHandler(
    api_key=os.environ["PRISMTRACE_API_KEY"],
    project_id=os.environ["PRISMTRACE_PROJECT_ID"],
    host=os.environ["PRISMTRACE_HOST"],
    agent_name="my-graph",
    session_id="graph-run-1",
)
graph = wrap_langgraph(compiled_graph, handler)
graph.invoke({"messages": [("user", "hello")]})
handler.flush()
```

`wrap_langgraph` injects callbacks on invoke and stream, so you do not thread config through every call site. Success is one real `invoke` or `stream` that flushes spans.

### Google ADK

```python
import os
from prismtrace import PRISMtraceADKAdapter
from google.adk.agents import LlmAgent

adapter = PRISMtraceADKAdapter(
    api_key=os.environ["PRISMTRACE_API_KEY"],
    project_id=os.environ["PRISMTRACE_PROJECT_ID"],
    agent_name="my-adk-agent",
)

agent = LlmAgent(
    model="gemini-2.0-flash",
    instruction="You are a helpful assistant.",
    before_model_callback=adapter.before_model,
    after_model_callback=adapter.after_model,
    before_tool_callback=adapter.before_tool,
    after_tool_callback=adapter.after_tool,
    before_agent_callback=adapter.before_agent,
    after_agent_callback=adapter.after_agent,
)
```

ADK does not deliver errors through its callbacks. Wrap model and tool calls and forward the exception explicitly, or failed turns are missing from the trajectory:

```python
try:
    ...
except Exception as exc:
    adapter.record_model_error(exc, callback_context=ctx)
    raise
```

### LiteLLM

```python
import os, litellm
from prismtrace import install_litellm

install_litellm(
    api_key=os.environ["PRISMTRACE_API_KEY"],
    project_id=os.environ["PRISMTRACE_PROJECT_ID"],
)
```

One call at startup registers a success and failure callback. Do not wrap individual call sites; that defeats the point of LiteLLM.

### OpenAI Agents SDK

```python
import os
from agents import Agent, Runner
from prismtrace import install_openai_agents

install_openai_agents(
    api_key=os.environ["PRISMTRACE_API_KEY"],
    project_id=os.environ["PRISMTRACE_PROJECT_ID"],
)
```

### TypeScript and JavaScript

There is no package to install. Instrument the completion handler so each turn POSTs one trace.

```ts
const res = await fetch(process.env.PRISMTRACE_HOST + "/api/traces", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-PRISMtrace-Key": process.env.PRISMTRACE_API_KEY,
  },
  body: JSON.stringify({
    project_id: process.env.PRISMTRACE_PROJECT_ID,
    model: "gpt-4o-mini",
    input_messages: [{ role: "user", content: input }],
    output_message: output,
    latency_ms: latencyMs,
    session_id: sessionId,
  }),
});
if (!res.ok) throw new Error("PRISM ingest " + res.status + ": " + (await res.text()));
```

On serverless, await the call so the function is not frozen mid-POST.

### Zero-code proxy

No import and no call-site change. Swap the base URL. Guardrails then run on the call itself: a blocked request never reaches the provider, and a blocked response is withheld while the original is still recorded.

```python
import anthropic, os

client = anthropic.Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    base_url="https://prism.blockconvey.com/proxy/anthropic",
    default_headers={"X-PRISMtrace-Key": os.environ["PRISMTRACE_API_KEY"]},
)
```

Three routes exist and only three:

| Provider | Base URL |
| --- | --- |
| Anthropic | `https://prism.blockconvey.com/proxy/anthropic` |
| OpenAI | `https://prism.blockconvey.com/proxy/openai/v1` |
| Gemini | `https://prism.blockconvey.com/proxy/gemini` via `client_options={"api_endpoint": ...}` |

Azure AI Foundry uses the OpenAI route with an `x-azure-endpoint` header naming your resource.

## Step 3: verify

**This step is required.** Run both checks. Every request sends `X-PRISMtrace-Key`. If a response says "Missing bearer token" or "Invalid or expired token", you used the wrong header. Add `X-PRISMtrace-Key` and retry once. Do not invent a JWT.

### Check 1: prove the credential

```bash
curl -sS -X POST "https://prism.blockconvey.com/api/setup-doctor/handshake" \
  -H "Content-Type: application/json" \
  -H "X-PRISMtrace-Key: $PRISMTRACE_API_KEY" \
  -d '{"project_id": "'"$PRISMTRACE_PROJECT_ID"'", "send_test_trace": true}'
```

This endpoint always authenticates and its errors are specific. Read `detail` and act on it rather than retrying.

| Response | Meaning | Action |
| --- | --- | --- |
| `200 ok` | Credential valid, test trace stored | Continue to check 2 |
| `401` no credential was sent | Header missing or variable unset | Export it, add the header, re-run |
| `401` does not look like a PRISM API key | Wrong value pasted, often the project id | Use the `pt-sk-` value |
| `401` was revoked | Key is dead | Ask for a new key. Do not retry. |
| `401` not recognised | Truncated paste or a key never saved | Ask for a new key |
| `401` that is a PRISM API key | Sent as `Authorization: Bearer` | Use `X-PRISMtrace-Key` |
| `403` belongs to project ... | Key and `project_id` are from different projects | Use the project id in the message |
| `404` | Project does not exist | Re-check `PRISMTRACE_PROJECT_ID` |

### Check 2: confirm real traffic arrived

```bash
curl -sS "https://prism.blockconvey.com/api/setup-doctor?project_id=$PRISMTRACE_PROJECT_ID" \
  -H "X-PRISMtrace-Key: $PRISMTRACE_API_KEY"
```

In a Python repository you may run `python -m prismtrace.verify` instead. In a TypeScript repository use curl; do not install Python to verify.

Read `live_connected` and `blocked_step`.

| Field | Meaning |
| --- | --- |
| `live_connected: true` | A real, non-demo trace arrived. Report LIVE CONNECTED. |
| `blocked_step: event_received` | Credential works, the application sent nothing |
| `blocked_step: trace_normalized` | Traces arrived without a shared `session_id` |
| `blocked_step: analysis_ready` | Traces arrived; scoring is still catching up. Not a setup failure. |

## Step 4: report

End with these lines and nothing vaguer.

- `CREDENTIAL OK` or `CREDENTIAL FAIL — <reason>`
- `LIVE CONNECTED — PRISM is receiving live traces from <what you instrumented>.`
- or `WAITING FOR LIVE — <blocked_step> — <next action>.`

Then list files changed, remaining manual steps, and risks.

"I have edited your files" is not an acceptable final answer.

### When you cannot run the application

Proving a live trace usually means starting the application, which needs provider keys, a database and a runtime. Sandboxes often have none of these. That is not a failed setup.

If the handshake returned 200, the code is wired and credentials are in the environment and `.env.example`:

- Stop. Do not loop. Do not ask for OpenAI, Anthropic or database keys purely to verify.
- Report `CREDENTIAL OK` and `WAITING FOR LIVE — instrumented, credential proven — a human must run the application once in their own environment.`

That is an acceptable final answer. Handshake-only is never `LIVE CONNECTED`.

## API contract

Base URL `https://prism.blockconvey.com`. Auth header `X-PRISMtrace-Key` on every request.

### POST /api/traces

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project UUID |
| `model` | string | yes | Model name |
| `input_messages` | array | yes | Objects of `{role, content}` |
| `output_message` | string | yes | Agent reply |
| `latency_ms` | int | yes | Send 0 if unmeasured |
| `trace_id` | string | no | Your id. Re-sending returns the existing trace rather than duplicating. |
| `session_id` | string | no | Groups traces into one conversation. Without it nothing assembles. |
| `user_identifier` | string | no | Your end-user id |
| `agent_id` | string | no | Stable agent id. Model Inventory and agent-scoped alerts match on it. |
| `agent_name` | string | no | Display name |
| `token_count_input` | int | no | Defaults to 0 |
| `token_count_output` | int | no | Defaults to 0 |
| `metadata` | object | no | Free-form, filterable |

`session_id`, `user_identifier`, `agent_id` and `agent_name` are also read from inside `metadata` for older callers. Top level wins.

An `api_key` body field still authenticates and is deprecated; responses using it carry `X-PRISMtrace-Deprecation`. Use the header.

Returns 200 with the stored trace, including `id` and `cost_usd`.

### POST /api/spans/ingest

Used by the SDK handlers, not usually by you directly. Body is `{trace_id, project_id, spans[], session_id?, metadata?}`. Each span carries `name`, `span_type`, `start_time`, and optionally `span_id`, `parent_span_id`, `input_text`, `output_text`, `end_time`, `duration_ms`, `status`, `error_message`, `token_count_input`, `token_count_output`, `cost_usd`, `model`.

### Errors

| Status | Meaning |
| --- | --- |
| `401` | Missing or invalid credential. Body says which. |
| `403` | Valid key, wrong project, or missing scope for this call |
| `402` | `insufficient_credits` carries price and balance; `plan_limit_reached` names the limit |
| `404` | No such project |
| `429` | Rate limited. Ingest allows 200 requests per minute. |

Over a plan volume ceiling the trace is still stored and the response carries `X-PRISMtrace-Plan-Warning`.

### Key scopes

| Scope | Allows |
| --- | --- |
| `ingest` | Send traces and spans. This is what an application should carry. |
| `read` | Read traces, analyses and balances |
| `operate` | Trigger actions that spend credits |

Use an `ingest`-scoped key in application code. A read-only key returns 403 on ingest by design.

## Account limits

Two independent systems. Plan limits are capacity; credits meter AI actions you trigger. Neither hides data already captured.

| Limit | Free | Builder |
| --- | --- | --- |
| Traces per month | 25,000 | 250,000 |
| Distinct models | 5 | 20 |
| Retention | 14 days | 90 days |
| Team members | 1 | 5 |
| Knowledge Base documents | 50 | 500 |
| Projects | unlimited | unlimited |
| Credits per 30-day cycle | 100 | 500 |

Free and Builder are the only plans. Guardrails, the Evaluators Hub and the expanded Model Inventory require Builder; everything else works on Free.

Zero credits are charged for anything you do while integrating: trace ingest, span ingest, reading traces, automatic per-trace scoring, trajectory assembly, and guardrail checks are all free. Credits are spent only on actions a human deliberately triggers in the dashboard.

At a zero balance, new AI actions pause. Ingest, reads, automatic scoring, guardrails and alerts all continue. Your integration cannot be blocked by a credit balance.

## Failure modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| 401 on every request | Bearer used instead of the key header | Send `X-PRISMtrace-Key` |
| 403 naming another project | Key and project id mismatched | Use the project id in the message |
| Handshake 200 but `live_connected` false | Nothing real has been sent yet | Run the application once |
| Traces arrive, no conversations | No shared `session_id` | Send one value per conversation |
| One agent appears as several | `agent_id` changes between runs | Send a stable `agent_id` |
| Import error on a PRISM symbol | The symbol does not exist | Check the symbols table above |
| `pip install` fails in a JS repo | Wrong path chosen | Use HTTP ingest |
| Scores missing on new traces | Scoring lags ingest | Wait. Not a setup failure and not billed. |
