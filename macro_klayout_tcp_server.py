import json
import os
import traceback

import pya


HOST = "127.0.0.1"
PORT = 9009


class _JsonTcpServer(object):
    def __init__(self, host=HOST, port=PORT):
        self._host = host
        self._port = port
        self._server = pya.QTcpServer()
        self._buffers = {}
        self._server.newConnection.connect(self._on_new_connection)

    def start(self):
        host_addr = pya.QHostAddress(self._host)
        if not self._server.listen(host_addr, int(self._port)):
            raise RuntimeError("Failed to listen on %s:%s" % (self._host, self._port))
        print("KLayout JSON TCP server listening on %s:%s" % (self._host, self._port))

    def stop(self):
        for sock in list(self._buffers.keys()):
            try:
                sock.disconnectFromHost()
            except Exception:
                pass
        self._buffers = {}
        self._server.close()

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._buffers[sock] = bytearray()
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))

    def _on_disconnected(self, sock):
        if sock in self._buffers:
            del self._buffers[sock]
        try:
            sock.deleteLater()
        except Exception:
            pass

    def _on_ready_read(self, sock):
        try:
            data = sock.readAll()
            if data is None:
                return
            self._buffers[sock].extend(bytes(data))
            buffer = self._buffers[sock]
            while True:
                idx = buffer.find(b"\n")
                if idx < 0:
                    break
                line = bytes(buffer[:idx]).strip()
                del buffer[: idx + 1]
                if not line:
                    continue
                self._handle_line(sock, line)
        except Exception:
            self._send_error(sock, None, traceback.format_exc())

    def _handle_line(self, sock, line):
        try:
            req = json.loads(line.decode("utf-8"))
        except Exception:
            self._send_error(sock, None, "Invalid JSON")
            return
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        try:
            result = self._dispatch(method, params)
            self._send_ok(sock, req_id, result)
        except Exception as exc:
            self._send_error(sock, req_id, str(exc))

    def _send_ok(self, sock, req_id, result):
        resp = {"id": req_id, "ok": True, "result": result}
        self._send(sock, resp)

    def _send_error(self, sock, req_id, message):
        resp = {"id": req_id, "ok": False, "error": message}
        self._send(sock, resp)

    def _send(self, sock, resp):
        payload = (json.dumps(resp) + "\n").encode("utf-8")
        try:
            sock.write(payload)
            sock.flush()
        except Exception:
            pass

    def _dispatch(self, method, params):
        if method == "ping":
            return {"message": "pong"}
        if method == "shutdown":
            self.stop()
            return {"message": "server stopped"}
        if method == "open_layout":
            return _open_layout(params)
        if method == "load_gds":
            return _load_gds(params)
        if method == "get_cell_list":
            return _get_cell_list(params)
        if method == "export_gds":
            return _export_gds(params)
        raise RuntimeError("Unknown method: %s" % method)


def _get_main_window():
    app = pya.Application.instance()
    return app.main_window() if app else None


def _require_view():
    mw = _get_main_window()
    if mw is None:
        raise RuntimeError("No KLayout main window (GUI required)")
    view = mw.current_view()
    if view is None:
        raise RuntimeError("No active view")
    return view


def _require_layout():
    view = _require_view()
    cv = view.active_cellview()
    if cv is None or not cv.is_valid():
        raise RuntimeError("No active cellview")
    return cv.layout()


def _open_layout(params):
    path = params.get("path")
    if not path:
        raise RuntimeError("path is required")
    if not os.path.exists(path):
        raise RuntimeError("File not found: %s" % path)
    mw = _get_main_window()
    if mw is None:
        raise RuntimeError("No KLayout main window (GUI required)")
    cellview_index = int(params.get("cellview_index", 0))
    if hasattr(mw, "load_layout"):
        result = mw.load_layout(path, cellview_index)
        return {"opened": True, "result": str(result)}
    view = mw.create_layout(0)
    view.load_layout(path, cellview_index)
    view.show()
    return {"opened": True, "view": "new"}


def _load_gds(params):
    path = params.get("path")
    if not path:
        raise RuntimeError("path is required")
    if not os.path.exists(path):
        raise RuntimeError("File not found: %s" % path)
    layout = _require_layout()
    layout.read(path)
    return {"loaded": True, "cells": layout.cells()}


def _iter_cells(layout):
    try:
        for cell in layout.each_cell():
            yield cell
    except Exception:
        for idx in layout.each_cell():
            yield layout.cell(idx)


def _get_cell_list(params):
    layout = _require_layout()
    names = []
    for cell in _iter_cells(layout):
        try:
            names.append(cell.name)
        except Exception:
            pass
    return {"cells": sorted(set(names))}


def _export_gds(params):
    path = params.get("path")
    if not path:
        raise RuntimeError("path is required")
    out_dir = os.path.dirname(path)
    if out_dir and not os.path.isdir(out_dir):
        raise RuntimeError("Directory not found: %s" % out_dir)
    layout = _require_layout()
    layout.write(path)
    return {"exported": True, "path": path}


SERVER = _JsonTcpServer()
SERVER.start()
