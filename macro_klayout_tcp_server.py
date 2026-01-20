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
        self._selection_subscribers = set()
        self._selection_timer = pya.QTimer(self._server)
        self._selection_timer.setInterval(200)
        self._selection_timer.timeout.connect(self._on_selection_tick)
        self._selection_view = None
        self._last_selection = object()
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
        if sock in self._selection_subscribers:
            self._selection_subscribers.discard(sock)
            if not self._selection_subscribers:
                self._selection_timer.stop()
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
            result = self._dispatch(sock, method, params)
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

    def _subscribe_selection(self, sock):
        self._selection_subscribers.add(sock)
        bound = self._bind_selection_view()
        if not bound and not self._selection_timer.isActive():
            self._selection_timer.start()
        try:
            selection_str = _get_selected_polygon_string()
        except Exception:
            selection_str = None
        self._last_selection = selection_str
        return {"subscribed": True, "selection": selection_str}

    def _unsubscribe_selection(self, sock):
        self._selection_subscribers.discard(sock)
        if not self._selection_subscribers:
            self._selection_timer.stop()
            if self._selection_view is not None:
                try:
                    self._selection_view.on_selection_changed = None
                except Exception:
                    pass
                self._selection_view = None
        return {"subscribed": False}

    def _bind_selection_view(self):
        try:
            view = _require_view()
        except Exception:
            return False
        if self._selection_view is view:
            return True
        self._selection_view = view
        try:
            view.on_selection_changed = lambda v=view: self._notify_selection()
            return True
        except Exception:
            return False

    def _notify_selection(self):
        if not self._selection_subscribers:
            return
        try:
            selection_str = _get_selected_polygon_string()
        except Exception:
            selection_str = None
        if selection_str == self._last_selection:
            return
        self._last_selection = selection_str
        payload = {"event": "selection", "data": selection_str}
        for sock in list(self._selection_subscribers):
            if sock not in self._buffers:
                self._selection_subscribers.discard(sock)
                continue
            self._send(sock, payload)

    def _on_selection_tick(self):
        if not self._selection_subscribers:
            return
        self._notify_selection()

    def _dispatch(self, sock, method, params):
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
        if method == "subscribe_selection":
            return self._subscribe_selection(sock)
        if method == "unsubscribe_selection":
            return self._unsubscribe_selection(sock)
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


def _selection_string_from_view(view):
    selection = view.object_selection
    if not selection:
        return None
    cv = view.active_cellview()
    if cv is None or not cv.is_valid():
        return None
    layout = cv.layout()
    for sel in selection:
        try:
            shape = sel.shape
        except AttributeError:
            continue
        if shape is None or shape.is_null():
            continue

        trans = getattr(sel, "trans", pya.Trans())
        if callable(trans):
            trans = trans()
        if not isinstance(trans, (pya.Trans, pya.ICplxTrans, pya.CplxTrans)):
            trans = pya.Trans()

        if shape.is_polygon():
            poly = shape.polygon.transformed(trans)
        elif shape.is_box():
            poly = pya.Polygon(shape.box).transformed(trans)
        else:
            continue
        try:
            layer_index = sel.layer
        except AttributeError:
            continue
        layer_info = layout.get_info(layer_index)
        layer_str = "%s/%s" % (layer_info.layer, layer_info.datatype)

        coords = []
        for pt in poly.each_point_hull():
            coords.append("%s_%s" % (pt.x, pt.y))

        return layer_str + "@" + "_".join(coords)
    return None


def _get_selected_polygon_string():
    view = _require_view()
    return _selection_string_from_view(view)


SERVER = _JsonTcpServer()
SERVER.start()
