# Roundtrip Flow Notes

This note explains the end-to-end flow between `llm_klayout_logger.py` and `test_cell_list_roundtrip_go_thru_llm_klayout_logger.py` and how `llm.log` is used.

## Components

- **KLayout JSON TCP server**
  - Listens on `127.0.0.1:9009` and accepts JSON-RPC-ish commands with `method` + `params`.
  - Returns a single-line JSON response that is logged by the proxy as `[KLAYOUT] response: ...`.

- **LLM proxy/logger (`llm_klayout_logger.py`)**
  - FastAPI service exposing `POST /chat/completions`.
  - Forwards requests to `LLM_ENDPOINT` (OpenAI-compatible chat completions).
  - Streams SSE responses back to the caller while parsing tool commands and forwarding them to KLayout.
  - Logs both request/stream content and KLayout responses into `llm.log`.

- **Roundtrip client (`test_cell_list_roundtrip_go_thru_llm_klayout_logger.py`)**
  - Calls the proxy endpoint with a tool-only prompt.
  - Parses the streamed output to extract the tool JSON command.
  - Reads the latest KLayout response from `llm.log`.
  - Sends a second prompt to format a final response containing the cell list.

## Detailed Sequence

1. **Client → Proxy (tool prompt)**
   - The client sends a streaming chat request to the proxy:
     - Endpoint: `PROXY_ENDPOINT` (default `http://127.0.0.1:8001/chat/completions`)
     - Body contains the tool-only prompt with `stream: True`.

2. **Proxy → LLM (forward request)**
   - The proxy logs the raw request payload to `llm.log`.
   - The proxy forwards the request to `LLM_ENDPOINT` using `httpx.AsyncClient.stream()`.

3. **LLM → Proxy (SSE stream)**
   - The proxy iterates SSE lines (`data: ...`).
   - `_extract_content_from_event` extracts:
     - `choices[0].delta.content` or `choices[0].message.content` (plain text), OR
     - Tool-call arguments if present (`choices[0].delta.tool_calls`).
   - The proxy accumulates content into `buffer_text`.

4. **Tool command extraction (Proxy → KLayout)**
   - `_extract_klayout_commands` scans `buffer_text` for JSON objects whose `tool == "klayout"`.
   - When found, each command is sent to KLayout via TCP using `_send_klayout_command`.

5. **KLayout response logging (KLayout → Proxy)**
   - The KLayout TCP response is logged as:
     - `[KLAYOUT] response: {"id":..., "ok": true, "result": {...}}`
   - This is the data the roundtrip test consumes.
   - The proxy does **not** send this response back to the LLM; it only logs it and continues streaming the original LLM SSE output to the client.

6. **Proxy → Client (stream back)**
   - The proxy yields the original SSE lines downstream without modifying them.

7. **Client parses tool command**
   - The client reuses `_extract_content_from_event` and `_extract_klayout_commands` to find the tool JSON.
   - If no tool command is found, it raises `RuntimeError`.

8. **Client reads `llm.log`**
   - `_read_last_klayout_response` scans the log for the latest `[KLAYOUT] response:` line.
   - This response should include a `result.cells` list for `get_cell_list`.

9. **Client → Proxy (final response)**
   - The client sends a second streaming prompt with the cell list and expects a formatted completion.
   - The output is printed to stdout.

## Required Runtime Conditions

- `llm_klayout_logger.py` must be running and bound to `PROXY_PORT` (default `8001`).
- The proxy must be the *only* service listening on that port.
- The LLM endpoint (`LLM_ENDPOINT`) must stream SSE responses in OpenAI-compatible format.
- KLayout JSON TCP server must be available on `127.0.0.1:9009`.
- `llm.log` must be writable in the repo root.

## Failure Modes to Watch

- **`No tool command returned by model`**
  - Proxy not reached (wrong port or another service bound).
  - LLM not emitting tool JSON or tool-calls.
  - SSE parsing mismatch (payload format differs).

- **Empty `llm.log`**
  - Requests never hit the proxy (wrong `PROXY_ENDPOINT`).
  - Proxy failed before writing logs.

- **`No KLAYOUT response found in llm.log`**
  - KLayout server not running or not accessible on port `9009`.
  - Tool command was never detected and sent to KLayout.

## Quick Run Recipe

1. Start the proxy:
   - `python llm_klayout_logger.py`
2. Run the roundtrip client:
   - `python test_cell_list_roundtrip_go_thru_llm_klayout_logger.py`
