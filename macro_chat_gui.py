# KLayout_Dockable_AI_Chat_Fixed2.py
# Dockable chat panel inside KLayout + OpenAI-compatible endpoint call
#
# Fixes / Features:
# - Dockable QDockWidget in KLayout main window
# - Transcript + input + Send/Clear
# - Enter to send, Shift+Enter newline (implemented via subclass keyPressEvent, not eventFilter)
# - Qt getter compatibility: in pya bindings, getters may be methods OR properties
#
# Endpoint:
# - Assumes OpenAI-compatible /v1/chat/completions:
#   response: {"choices":[{"message":{"content":"..."}}]}
# - If your server returns different JSON, adapt _parse_llm_response().

import json
import traceback
import urllib.request
import urllib.error

import pya


DOCK_OBJECT_NAME = "KLAYOUT_AI_CHAT_DOCK_V1"


# ---------- Qt compatibility helpers ----------
def _qt_value(x):
    """
    x 可能是：
    - 值（str/int/...）
    - 方法（callable）
    - Qt 屬性（可能回傳 callable 或值）
    統一：callable 就呼叫，否則直接用；最後回傳原值。
    """
    try:
        return x() if callable(x) else x
    except Exception:
        return x


def _qline_text(w):
    # QLineEdit.text 可能是方法或屬性
    return str(_qt_value(getattr(w, "text")))


def _qplain_text(w):
    # QPlainTextEdit.toPlainText 可能是方法或屬性
    return str(_qt_value(getattr(w, "toPlainText")))


def _event_mods(event):
    # event.modifiers 可能是屬性；轉 int 以便位元運算最穩
    try:
        return int(_qt_value(getattr(event, "modifiers")))
    except Exception:
        try:
            return int(event.modifiers)
        except Exception:
            return 0


def _find_existing_dock(mw):
    # KLayout 的 findChildren 只接受 name 或正規表示式
    matches = mw.findChildren(DOCK_OBJECT_NAME)
    if matches and len(matches) > 0:
        return matches[0]
    return None


def _parse_llm_response(raw_json_text):
    """
    Parse OpenAI chat.completions-like response.
    Expected:
      {"choices":[{"message":{"role":"assistant","content":"..."}}]}
    """
    obj = json.loads(raw_json_text)
    try:
        return obj["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError("無法解析回覆格式，原始回覆如下：\n" + raw_json_text[:2000])


# ---------- Input box (Enter send / Shift+Enter newline) ----------
class _InputBox(pya.QPlainTextEdit):
    def __init__(self, parent=None, on_send=None):
        super(_InputBox, self).__init__(parent)
        self._on_send_cb = on_send

    def keyPressEvent(self, event):
        try:
            key = event.key()
            mods = _event_mods(event)

            # 某些綁定下 key 可能是 enum 或 int，這裡都嘗試覆蓋
            is_enter = (key == pya.Qt.Key_Return) or (key == pya.Qt.Key_Enter) \
                       or (key == int(pya.Qt.Key_Return)) or (key == int(pya.Qt.Key_Enter))

            if is_enter:
                # Shift+Enter => newline (default behavior)
                if mods & int(pya.Qt.ShiftModifier):
                    return super(_InputBox, self).keyPressEvent(event)

                # Enter => send (do NOT insert newline)
                if callable(self._on_send_cb):
                    self._on_send_cb()
                    return
        except Exception:
            # If anything goes wrong, fall back to default behavior
            pass

        return super(_InputBox, self).keyPressEvent(event)


class _ChatPanel(pya.QWidget):
    def __init__(self, parent=None):
        super(_ChatPanel, self).__init__(parent)

        self._messages = []  # [{"role": "user"/"assistant", "content": "..."}]
        self._system_prompt = (
            "你是在 KLayout 內的助理。"
            "回答請以繁體中文、精簡且可執行為主。"
            "若不確定，請先說不確定並提出需要的資訊。"
        )

        root = pya.QVBoxLayout(self)

        # --- minimal settings row ---
        settings = pya.QHBoxLayout()
        self.ed_base_url = pya.QLineEdit("http://127.0.0.1:1234", self)
        self.ed_endpoint = pya.QLineEdit("/v1/chat/completions", self)
        self.ed_model = pya.QLineEdit("qwen3-vl-8b-instruct-mlx", self)

        self.btn_clear = pya.QPushButton("Clear", self)
        self.btn_clear.clicked.connect(self._on_clear)

        settings.addWidget(pya.QLabel("URL", self))
        settings.addWidget(self.ed_base_url, 2)
        settings.addWidget(pya.QLabel("EP", self))
        settings.addWidget(self.ed_endpoint, 2)
        settings.addWidget(pya.QLabel("Model", self))
        settings.addWidget(self.ed_model, 2)
        settings.addWidget(self.btn_clear, 0)

        root.addLayout(settings)

        # --- transcript ---
        self.txt_log = pya.QTextEdit(self)
        self.txt_log.setReadOnly(True)
        root.addWidget(self.txt_log, 1)

        # --- input row ---
        bottom = pya.QHBoxLayout()
        self.ed_input = _InputBox(self, on_send=self._on_send)
        self.ed_input.setPlaceholderText("輸入訊息…（Shift+Enter 換行，Enter 送出）")
        self.ed_input.setFixedHeight(80)

        self.btn_send = pya.QPushButton("Send", self)
        self.btn_send.clicked.connect(self._on_send)

        bottom.addWidget(self.ed_input, 1)
        bottom.addWidget(self.btn_send, 0)
        root.addLayout(bottom)

        self._append("system", "Dock AI Chat 已啟動。你可以先輸入：請用一句話介紹你自己。")

    def _append(self, role, text):
        if role == "user":
            prefix = "你："
        elif role == "assistant":
            prefix = "AI："
        elif role == "system":
            prefix = "[系統]"
        else:
            prefix = "[錯誤]"
        self.txt_log.append(f"{prefix}\n{text}\n")

    def _on_clear(self):
        self._messages = []
        self.txt_log.clear()
        self._append("system", "已清空對話。")

    def _on_send(self):
        user_text = _qplain_text(self.ed_input).strip()
        if not user_text:
            return
        self.ed_input.setPlainText("")

        self._append("user", user_text)
        self._messages.append({"role": "user", "content": user_text})

        try:
            assistant_text = self._call_llm(self._system_prompt, self._messages)
            if not assistant_text:
                assistant_text = "(空回覆)"
            self._append("assistant", assistant_text)
            self._messages.append({"role": "assistant", "content": assistant_text})
        except Exception as e:
            self._append("error", f"呼叫失敗：{e}\n\n{traceback.format_exc()}")

    def _call_llm(self, system_prompt, messages):
        base_url = _qline_text(self.ed_base_url).strip().rstrip("/")
        endpoint = _qline_text(self.ed_endpoint).strip()
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        url = base_url + endpoint

        model = _qline_text(self.ed_model).strip()

        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "temperature": 0.2,
            "stream": False
        }
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url=url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code} {e.reason}\n{raw}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"URL Error: {e}")

        return _parse_llm_response(raw)


def show_dockable_ai_chat():
    mw = pya.Application.instance().main_window()
    if mw is None:
        raise RuntimeError("找不到 KLayout main_window()，請確認在 GUI 模式執行。")

    existing = _find_existing_dock(mw)
    if existing is not None:
        existing.show()
        try:
            existing.raise_()
        except Exception:
            pass
        return

    dock = pya.QDockWidget("AI Chat", mw)
    dock.setObjectName(DOCK_OBJECT_NAME)
    dock.setAllowedAreas(
        pya.Qt.LeftDockWidgetArea
        | pya.Qt.RightDockWidgetArea
        | pya.Qt.TopDockWidgetArea
        | pya.Qt.BottomDockWidgetArea
    )
    dock.setFeatures(
        pya.QDockWidget.DockWidgetMovable
        | pya.QDockWidget.DockWidgetFloatable
        | pya.QDockWidget.DockWidgetClosable
    )

    panel = _ChatPanel(dock)
    dock.setWidget(panel)

    mw.addDockWidget(pya.Qt.RightDockWidgetArea, dock)
    dock.show()


# Entry point
show_dockable_ai_chat()