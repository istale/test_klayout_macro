from fastapi.testclient import TestClient

import llm_klayout_logger as proxy


def main():
    client = TestClient(proxy.app)
    response = client.post(
        "/chat/completions",
        json={
            "model": "ignored",
            "messages": [
                {
                    "role": "user",
                    "content": 'Reply with a short sentence and include this JSON tool block on its own line: {"tool":"klayout","method":"open_layout","params":{"path":"./test.gds"}}',
                }
            ],
            "stream": True,
        },
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    body = response.text
    assert "data:" in body
    assert "[DONE]" in body
    print("PASS: proxy_request live stream")


if __name__ == "__main__":
    main()
