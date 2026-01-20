import json
import os

from llm_klayout_logger import (
    AppLogger,
    _extract_content_from_event,
    _extract_klayout_commands,
    _send_klayout_command,
)


def _fake_sse_lines():
    payloads = [
        {"choices": [{"delta": {"content": "Hello "}}]},
        {"choices": [{"delta": {"content": "from LLM. "}}]},
        {"choices": [{"delta": {"content": "{"}}]},
        {"choices": [{"delta": {"content": '"tool": "klayout",'}}]},
        {"choices": [{"delta": {"content": ' "method": "ping",'}}]},
        {"choices": [{"delta": {"content": ' "params": {}'}}]},
        {"choices": [{"delta": {"content": "}"}}]},
        {"choices": [{"delta": {"content": " Done."}}]},
    ]
    for payload in payloads:
        yield "data: %s" % json.dumps(payload)
    yield "data: [DONE]"


def main():
    logger = AppLogger("llm_stream_sim.log")
    buffer_text = ""
    send_enabled = os.getenv("KLAYOUT_SEND") == "1"

    for line in _fake_sse_lines():
        logger.log(line)
        content = _extract_content_from_event(line)
        if not content:
            continue
        buffer_text += content
        commands, buffer_text = _extract_klayout_commands(buffer_text)
        for command in commands:
            logger.log("[SIM] parsed command: %s" % json.dumps(command))
            if send_enabled:
                _send_klayout_command(command, logger)
            else:
                logger.log("[SIM] skipping TCP send (KLAYOUT_SEND=1 to enable)")
        if len(buffer_text) > 8192:
            buffer_text = buffer_text[-4096:]


if __name__ == "__main__":
    main()
