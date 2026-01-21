import json
import socket

import httpx
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse


KLAYOUT_HOST = "127.0.0.1"
KLAYOUT_PORT = 9009
KLAYOUT_TOOL_NAME = "klayout"
LLM_ENDPOINT = "http://127.0.0.1:1234/v1/chat/completions"
LLM_MODEL = "qwen/qwen3-coder-30b"


class AppLogger:
    def __init__(self, log_file="llm.log"):
        self.log_file = log_file
        with open(self.log_file, "w", encoding="utf-8") as handle:
            handle.write("")

    def log(self, message):
        with open(self.log_file, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
        print(message)


def _send_klayout_command(command, logger):
    method = command.get("method")
    if not method:
        return
    payload = {
        "id": command.get("id", 1),
        "method": method,
        "params": command.get("params", {}),
    }
    try:
        sock = socket.create_connection((KLAYOUT_HOST, KLAYOUT_PORT), timeout=3)
    except OSError as exc:
        logger.log("[KLAYOUT] connect error: %s" % exc)
        return
    try:
        line = json.dumps(payload).encode("utf-8") + b"\n"
        sock.sendall(line)
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        if data:
            response = data.split(b"\n", 1)[0].decode("utf-8")
            logger.log("[KLAYOUT] response: %s" % response)
        else:
            logger.log("[KLAYOUT] empty response")
    except OSError as exc:
        logger.log("[KLAYOUT] send error: %s" % exc)
    finally:
        sock.close()


def _try_parse_json(text, start):
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : idx + 1]
                try:
                    return json.loads(candidate), idx + 1
                except json.JSONDecodeError:
                    return False, idx + 1
    return None, None


def _extract_klayout_commands(buffer_text):
    commands = []
    idx = 0
    while idx < len(buffer_text):
        if buffer_text[idx] != "{":
            idx += 1
            continue
        obj, end = _try_parse_json(buffer_text, idx)
        if obj is None:
            return commands, buffer_text[idx:]
        idx = end
        if obj is False:
            continue
        if isinstance(obj, dict) and obj.get("tool") == KLAYOUT_TOOL_NAME:
            commands.append(obj)
    return commands, buffer_text[idx:]


def _extract_content_from_event(line):
    if not line.startswith("data: "):
        return ""
    payload = line[6:].strip()
    if payload == "[DONE]":
        return ""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    choices = data.get("choices") or []
    if not choices:
        return ""
    choice = choices[0]
    delta = choice.get("delta") or {}
    content = delta.get("content")
    if content is not None:
        return content
    message = choice.get("message") or {}
    return message.get("content", "")


app = FastAPI(title="LLM + KLayout Logger")
logger = AppLogger("llm.log")


@app.post("/chat/completions")
async def proxy_request(request: Request):
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")
    logger.log(f"模型请求：{body_str}")
    body = await request.json()
    body["model"] = LLM_MODEL

    logger.log("模型返回：\n")

    async def event_stream():
        buffer_text = ""
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                LLM_ENDPOINT,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
            ) as response:
                async for line in response.aiter_lines():
                    logger.log(line)
                    content = _extract_content_from_event(line)
                    if content:
                        buffer_text += content
                        commands, buffer_text = _extract_klayout_commands(buffer_text)
                        for command in commands:
                            _send_klayout_command(command, logger)
                        if len(buffer_text) > 8192:
                            buffer_text = buffer_text[-4096:]
                    yield f"{line}\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
