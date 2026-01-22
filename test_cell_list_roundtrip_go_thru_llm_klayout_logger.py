import json
import os
import time

import httpx

from llm_klayout_logger import _extract_content_from_event, _extract_klayout_commands

PROXY_ENDPOINT = "http://127.0.0.1:8001/chat/completions"
LOG_PATH = "./llm.log"

def _call_proxy_stream(messages):
    body = {"model": "ignored", "messages": messages, "stream": True}
    buffer_text = ""
    full_text = ""
    with httpx.Client(timeout=None) as client:
        with client.stream(
            "POST",
            PROXY_ENDPOINT,
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
                full_text += content
                buffer_text += content
                commands, buffer_text = _extract_klayout_commands(buffer_text)
                for command in commands:
                    return command, full_text
    return None, full_text


def _call_proxy_complete(messages):
    body = {"model": "ignored", "messages": messages, "stream": True}
    buffer_text = ""
    with httpx.Client(timeout=None) as client:
        with client.stream(
            "POST",
            PROXY_ENDPOINT,
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
                if content:
                    buffer_text += content
    return buffer_text


def _read_last_klayout_response(timeout_seconds=5.0, poll_interval=0.2):
    deadline = time.monotonic() + timeout_seconds
    while True:
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
            for line in reversed(lines):
                line = line.strip()
                if line.startswith("[KLAYOUT] response:"):
                    payload = line.split("[KLAYOUT] response:", 1)[1].strip()
                    return json.loads(payload)
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)
    raise RuntimeError(
        "No KLAYOUT response found in llm.log. Start llm_klayout_logger.py "
        "in this repo or set LLM_LOG_PATH."
    )


def main():
    tool_prompt = (
        "Output EXACTLY the following JSON tool block, nothing else: "
        '{"tool":"klayout","method":"get_cell_list","params":{}}'
    )
    command = None
    last_text = ""
    for _ in range(3):
        command, last_text = _call_proxy_stream(
            [
                {
                    "role": "system",
                    "content": "You must output only the JSON tool block with no extra text.",
                },
                {"role": "user", "content": tool_prompt},
            ]
        )
        if command:
            break
    if not command:
        raise RuntimeError(
            "No tool command returned by model. Last response: %s" % last_text
        )

    response = _read_last_klayout_response()
    if not response.get("ok"):
        raise RuntimeError("KLayout error: %s" % response.get("error"))

    cells = response.get("result", {}).get("cells", [])
    user_prompt = (
        "Given this cell list: %s, reply with exactly: "
        "<attempt_completion><result>Your cell list is ... </result></attempt_completion>"
    ) % json.dumps(cells)

    final = _call_proxy_complete(
        [
            {"role": "system", "content": "Format exactly as asked."},
            {"role": "user", "content": user_prompt},
        ]
    )
    print(final)


if __name__ == "__main__":
    main()
