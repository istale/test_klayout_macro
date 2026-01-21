import json
import os
import socket

import httpx

from llm_klayout_logger import (
    LLM_ENDPOINT,
    LLM_MODEL,
    _extract_content_from_event,
    _extract_klayout_commands,
)


KLAYOUT_HOST = "127.0.0.1"
KLAYOUT_PORT = 9009


def _send_klayout(payload):
    sock = socket.create_connection((KLAYOUT_HOST, KLAYOUT_PORT), timeout=5)
    try:
        line = json.dumps(payload).encode("utf-8") + b"\n"
        sock.sendall(line)
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        if not data:
            raise RuntimeError("No response from KLayout")
        return json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
    finally:
        sock.close()


def _open_layout(path):
    payload = {"id": 1, "method": "open_layout", "params": {"path": path}}
    return _send_klayout(payload)


def _call_llm_stream(messages):
    body = {"model": LLM_MODEL, "messages": messages, "stream": True}
    buffer_text = ""
    with httpx.Client(timeout=None) as client:
        with client.stream(
            "POST",
            LLM_ENDPOINT,
            json=body,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        ) as response:
            for line in response.iter_lines():
                line = (
                    line.decode("utf-8")
                    if isinstance(line, (bytes, bytearray))
                    else line
                )
                content = _extract_content_from_event(line)
                if not content:
                    continue
                buffer_text += content
                commands, buffer_text = _extract_klayout_commands(buffer_text)
                for command in commands:
                    return command
    return None


def _call_llm_complete(messages):
    body = {"model": LLM_MODEL, "messages": messages, "stream": False}
    with httpx.Client(timeout=None) as client:
        response = client.post(
            LLM_ENDPOINT,
            json=body,
            headers={"Content-Type": "application/json"},
        )
    response.raise_for_status()
    payload = response.json()
    return payload["choices"][0]["message"]["content"]


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    gds_path = os.path.join(root, "test.gds")
    _open_layout(gds_path)

    tool_prompt = (
        "Return ONLY this JSON tool block on its own line: "
        '{"tool":"klayout","method":"get_cell_list","params":{}}'
    )
    command = _call_llm_stream(
        [
            {"role": "system", "content": "You are a tool router."},
            {"role": "user", "content": tool_prompt},
        ]
    )
    if not command:
        raise RuntimeError("No tool command returned by model")

    response = _send_klayout(
        {
            "id": command.get("id", 2),
            "method": command["method"],
            "params": command.get("params", {}),
        }
    )
    if not response.get("ok"):
        raise RuntimeError("KLayout error: %s" % response.get("error"))

    cells = response.get("result", {}).get("cells", [])
    user_prompt = (
        "Given this cell list: %s, reply with exactly: "
        "<attempt_completion><result>Your cell list is ... </result></attempt_completion>"
    ) % json.dumps(cells)

    final = _call_llm_complete(
        [
            {"role": "system", "content": "Format exactly as asked."},
            {"role": "user", "content": user_prompt},
        ]
    )
    print(final)


if __name__ == "__main__":
    main()
