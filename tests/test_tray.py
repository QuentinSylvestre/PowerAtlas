"""Tests for tray icon creation."""

import sys
from unittest.mock import patch

import pytest

from PIL import Image

from power_atlas.tray import _create_icon


def test_create_icon_loads_ico():
    img = _create_icon()
    assert isinstance(img, Image.Image)
    assert img.mode == "RGBA"
    assert img.size[0] >= 16


def test_create_icon_fallback_on_missing_file():
    with patch("power_atlas.tray.Image.open", side_effect=OSError("not found")), \
         patch("power_atlas.tray.log") as mock_log:
        img = _create_icon()
    assert isinstance(img, Image.Image)
    assert img.size == (16, 16)
    assert img.mode == "RGBA"
    assert mock_log.warning.called


class _FakePystray:
    """`pystray.Menu`/`MenuItem` stand-ins that keep the keyword options."""

    @staticmethod
    def MenuItem(text, action, **kw):
        return {"text": text, "action": action, **kw}

    @staticmethod
    def Menu(*items):
        return list(items)


class _Controller:
    def __init__(self, ready):
        self.supports_app_mode = ready
        self.shown = 0

    def show_app(self):
        self.shown += 1


def _menu(monkeypatch, controller, platform="win32"):
    """`_build_menu` against fake pystray, with the given window controller.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """
    from power_atlas import peek as peek_mod
    from power_atlas import tray as tray_mod
    monkeypatch.setattr(tray_mod.pystray, "MenuItem", _FakePystray.MenuItem)
    monkeypatch.setattr(tray_mod.pystray, "Menu", _FakePystray.Menu)
    monkeypatch.setattr(tray_mod, "_window_controller", controller)
    monkeypatch.setattr(tray_mod.sys, "platform", platform)
    monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
    monkeypatch.setattr(tray_mod, "_warmup", lambda: None)
    return tray_mod, tray_mod._build_menu("http://127.0.0.1:4915")


def _visible(item):
    vis = item.get("visible", True)
    return vis(item) if callable(vis) else vis


def test_build_menu_labels_order_and_default(monkeypatch):
    _, items = _menu(monkeypatch, _Controller(ready=True))
    assert [i["text"] for i in items] == [
        "Open PowerAtlas", "Open in browser", "Copy login link", "Logs",
        "Restart", "Quit"]
    defaults = [i["text"] for i in items if i.get("default")]
    assert defaults == ["Open PowerAtlas"]


def test_open_in_browser_is_listed_before_the_window_is_ready(monkeypatch):
    """pystray builds the menu before readiness; visibility is static."""
    _, items = _menu(monkeypatch, _Controller(ready=False))
    assert _visible(items[1]) is True


def test_open_in_browser_is_hidden_without_a_window(monkeypatch):
    _, items = _menu(monkeypatch, None)
    assert _visible(items[1]) is False


def test_open_in_browser_is_hidden_off_windows(monkeypatch):
    _, items = _menu(monkeypatch, _Controller(ready=True), platform="linux")
    assert _visible(items[1]) is False


def test_open_poweratlas_uses_the_browser_until_the_window_is_ready(
        monkeypatch):
    ctrl = _Controller(ready=False)
    tray_mod, items = _menu(monkeypatch, ctrl)
    opened = []
    monkeypatch.setattr(tray_mod.doors, "open_in_browser", opened.append)
    monkeypatch.setattr(tray_mod.doors, "login_url", lambda u: u + "/signed")
    items[0]["action"](None, None)
    assert opened == ["http://127.0.0.1:4915/signed"]
    assert ctrl.shown == 0


def test_open_poweratlas_shows_the_app_window_when_ready(monkeypatch):
    ctrl = _Controller(ready=True)
    tray_mod, items = _menu(monkeypatch, ctrl)
    opened = []
    monkeypatch.setattr(tray_mod.doors, "open_in_browser", opened.append)
    items[0]["action"](None, None)
    assert ctrl.shown == 1
    assert opened == []


def test_quit_and_restart_stop_the_window_once_each(monkeypatch):
    class _Stoppable(_Controller):
        stops = 0

        def stop(self):
            type(self).stops += 1
            raise RuntimeError("a failing stop must not block Quit")

    class _Icon:
        stopped = 0

        def stop(self):
            _Icon.stopped += 1

    tray_mod, items = _menu(monkeypatch, _Stoppable(ready=True))
    monkeypatch.setattr(tray_mod, "_restart_requested", False)
    import threading
    monkeypatch.setattr(tray_mod, "_shutdown_event", threading.Event())
    by = {i["text"]: i["action"] for i in items}
    by["Quit"](_Icon(), None)
    by["Restart"](_Icon(), None)
    assert _Stoppable.stops == 2 and _Icon.stopped == 2
    assert tray_mod.restart_requested() is True
    assert tray_mod.get_shutdown_event().is_set()


def test_open_in_browser_visibility_does_not_import_peek(monkeypatch):
    """261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final
    review fix 21: a registered window is all it takes (one exists only when
    peek's dependencies loaded), so the tray never imports `peek` here (the
    import D-15 avoids). With `peek` unimportable it is still listed."""
    import sys
    _, items = _menu(monkeypatch, _Controller(ready=False))
    import power_atlas
    monkeypatch.setitem(sys.modules, "power_atlas.peek", None)
    monkeypatch.delattr(power_atlas, "peek", raising=False)
    assert _visible(items[1]) is True


def test_doors_import_neither_web_nor_the_tray():
    """261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5
    (follow-up 12): the door helpers live in a neutral module that `tray` and
    `peek` both import; importing it pulls in neither the web app (minted
    lazily) nor the tray UI module, and `tray` keeps no copy of them."""
    import subprocess
    import sys
    code = ("import sys, power_atlas.doors as d; "
            "print(sorted(m for m in ('power_atlas.web', 'power_atlas.tray', "
            "'power_atlas.peek') if m in sys.modules)); "
            "print(callable(d.login_url), callable(d.open_in_browser))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split("\n")[:2] == ["[]", "True True"]
    import power_atlas.tray as tray_mod
    assert not hasattr(tray_mod, "_login_url")
    assert not hasattr(tray_mod, "_open_in_browser")


class TestLandingFileOffWindows:
    """261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS
    follow-up 19: off Windows `xdg-open` would carry the login URL, and its
    live code, on a command line any local user can read
    (`/proc/<pid>/cmdline`). The door writes a private page that forwards to
    the URL, opens its `file://` URL instead, and deletes the file after a
    delay. Windows keeps `webbrowser.open` (ShellExecute)."""

    URL = "http://127.0.0.1:4915/local-auth?code=Zq7-LANDING-CODE&next=%2Facp"
    CODE = "Zq7-LANDING-CODE"

    def _doors(self, monkeypatch, tmp_path, popen=None):
        import subprocess
        import power_atlas.doors as doors
        monkeypatch.setattr(doors.sys, "platform", "linux")
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        argv = []

        def fake_popen(args, **kw):
            argv.append(list(args))
            if popen is not None:
                return popen(args)
            return None

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(doors.webbrowser, "open",
                            lambda *a, **k: argv.append(["webbrowser", *a]))
        return doors, argv

    def test_the_url_never_reaches_the_command_line(self, monkeypatch,
                                                    tmp_path, caplog):
        import logging
        from pathlib import Path
        doors, argv = self._doors(monkeypatch, tmp_path)
        monkeypatch.setattr(doors, "LANDING_FILE_TTL_S", 30.0)
        with caplog.at_level(logging.DEBUG):
            doors.open_in_browser(self.URL)
        assert len(argv) == 1 and argv[0][0] == "xdg-open"
        assert len(argv[0]) == 2
        joined = " ".join(argv[0])
        assert self.CODE not in joined and "local-auth" not in joined
        uri = argv[0][1]
        assert uri.startswith("file:")
        files = list(tmp_path.iterdir())
        assert len(files) == 1 and Path(files[0]).as_uri() == uri
        page = files[0].read_text(encoding="utf-8")
        # The attribute holds the HTML-escaped URL, the script the JSON one.
        assert ('content="0;url=http://127.0.0.1:4915/local-auth?code='
                'Zq7-LANDING-CODE&amp;next=%2Facp"') in page
        assert ('location.replace("http://127.0.0.1:4915/local-auth?code='
                'Zq7-LANDING-CODE&next=%2Facp")') in page
        assert self.CODE not in caplog.text
        assert str(files[0]) not in caplog.text
        doors._remove_all_landing_files()
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.skipif(sys.platform == "win32",
                        reason="POSIX permission bits")
    def test_the_file_is_private(self, monkeypatch, tmp_path):
        """0600: `tempfile.mkstemp` documents the file as "readable and
        writable only by the creating user ID"."""
        import stat
        doors, _ = self._doors(monkeypatch, tmp_path)
        doors.open_in_browser(self.URL)
        (f,) = list(tmp_path.iterdir())
        try:
            assert stat.S_IMODE(f.stat().st_mode) == 0o600
        finally:
            doors._remove_all_landing_files()

    def test_the_file_is_removed_after_the_delay(self, monkeypatch, tmp_path):
        import time
        doors, _ = self._doors(monkeypatch, tmp_path)
        monkeypatch.setattr(doors, "LANDING_FILE_TTL_S", 0.2)
        doors.open_in_browser(self.URL)
        assert len(list(tmp_path.iterdir())) == 1, "still there at first"
        deadline = time.monotonic() + 5
        while list(tmp_path.iterdir()) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert list(tmp_path.iterdir()) == []
        assert doors._landing_files == set()

    def test_a_failed_start_removes_the_file_and_never_retries_with_the_url(
            self, monkeypatch, tmp_path, caplog):
        import logging

        def boom(args):
            raise FileNotFoundError(f"no xdg-open for {args}")

        doors, argv = self._doors(monkeypatch, tmp_path, popen=boom)
        with caplog.at_level(logging.DEBUG):
            doors.open_in_browser(self.URL)
        assert len(argv) == 1, "no second attempt"
        assert self.CODE not in " ".join(argv[0])
        assert list(tmp_path.iterdir()) == []
        assert "FileNotFoundError" in caplog.text
        assert self.CODE not in caplog.text
        assert "power-atlas-login-" not in caplog.text

    def test_a_script_breakout_is_escaped(self, monkeypatch, tmp_path):
        """A `</script>` in the URL cannot close the script element."""
        doors, _ = self._doors(monkeypatch, tmp_path)
        doors.open_in_browser("http://127.0.0.1:1/?x=</script><b>")
        (f,) = list(tmp_path.iterdir())
        page = f.read_text(encoding="utf-8")
        doors._remove_all_landing_files()
        assert page.count("</script>") == 1
        assert "<b>" not in page

    def test_windows_keeps_webbrowser_open(self, monkeypatch, tmp_path):
        doors, argv = self._doors(monkeypatch, tmp_path)
        monkeypatch.setattr(doors.sys, "platform", "win32")
        doors.open_in_browser(self.URL)
        assert argv == [["webbrowser", self.URL]]
        assert list(tmp_path.iterdir()) == []
