from types import SimpleNamespace

import pytest
from PIL import Image

from android_use.runtime import Runtime

XML = """<hierarchy rotation="0"><node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" enabled="true">
<node class="android.widget.EditText" resource-id="demo:id/input" text="" focused="true" enabled="true" focusable="true" bounds="[50,200][900,300]"/>
<node class="android.widget.Button" text="同名" clickable="true" enabled="true" bounds="[0,400][200,500]"/>
<node class="android.widget.Button" text="同名" clickable="true" enabled="true" bounds="[0,600][200,700]"/>
<node class="android.widget.Button" text="屏外" enabled="true" bounds="[0,2500][200,2600]"/>
<node class="android.widget.EditText" text="secret" password="true" enabled="true" bounds="[0,800][200,900]"/>
</node></hierarchy>"""


class FakeBackend:
    def __init__(self, serial):
        self.serial = serial
        self.d = SimpleNamespace(info={"screenOn": True})
        self.is_locked = False
        self.actions = []
        self.xml = XML
        self.front = {"package": "demo.app", "activity": "Main"}
        self.fail_write = False
        self.tree_reads = 0
        self.screenshots = 0
        self.actual_text = ""
        self.sanitize_non_bmp = False

    def locked(self):
        return self.is_locked

    def identity(self):
        return "demo"

    def size(self):
        return 1080, 2400

    def foreground(self):
        return self.front

    def tree(self):
        self.tree_reads += 1
        return self.xml

    def screenshot(self):
        self.screenshots += 1
        return Image.new("RGB", self.size())

    def tap(self, x, y):
        self.actions.append(("tap", x, y))
        if self.fail_write:
            raise TimeoutError("Action may already have executed")
        return True

    def set_text(self, text, selector):
        import xml.etree.ElementTree as ET

        self.actions.append(("type", text))
        self.actual_text = text
        if self.sanitize_non_bmp:
            text = "".join(c if ord(c) <= 0xFFFF else ".." for c in text)
        root = ET.fromstring(self.xml)
        next(n for n in root.iter("node") if n.get("focused") == "true").set("text", text)
        self.xml = ET.tostring(root, encoding="unicode")
        return True

    def get_text(self, selector):
        return self.actual_text

    def press(self, key):
        self.actions.append(("press", key))

    def swipe(self, start, end, duration):
        self.actions.append(("swipe", start, end))

    def launch(self, package):
        self.actions.append(("launch", package))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("ANDROID_USE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("android_use.runtime.choose_serial", lambda serial: serial or "demo")
    backend = FakeBackend("demo")
    runtime = Runtime("demo", backend_factory=lambda serial: backend)
    return runtime, backend
