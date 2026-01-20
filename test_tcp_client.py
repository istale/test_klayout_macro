import json
import os
import socket
import sys


HOST = "127.0.0.1"
PORT = 9009


def _send(sock, payload):
    line = json.dumps(payload).encode("utf-8") + b"\n"
    sock.sendall(line)
    data = b""
    while b"\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    if not data:
        raise RuntimeError("No response from server")
    return json.loads(data.split(b"\n", 1)[0].decode("utf-8"))


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    gds_path = os.path.join(root, "test.gds")
    out_path = os.path.join(root, "test_export.gds")

    if not os.path.exists(gds_path):
        raise RuntimeError("Missing test.gds at %s" % gds_path)

    sock = socket.create_connection((HOST, PORT), timeout=5)
    try:
        print("Ping:", _send(sock, {"id": 1, "method": "ping"}))
        print(
            "Open layout:",
            _send(
                sock, {"id": 2, "method": "open_layout", "params": {"path": gds_path}}
            ),
        )
        print("Cell list:", _send(sock, {"id": 3, "method": "get_cell_list"}))
        print(
            "Export:",
            _send(
                sock, {"id": 4, "method": "export_gds", "params": {"path": out_path}}
            ),
        )
    finally:
        sock.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", exc)
        sys.exit(1)
