"""
daemon.py — Background clipboard watcher for jot.

Polls clipboard every 500 ms for both text and images.
PID file: ~/.jot/daemon.pid
Images saved to /tmp/jot/ (or %TEMP%\\jot on Windows) as timestamped PNGs.
"""

from __future__ import annotations

import io
import os
import signal
import sys
import time
from pathlib import Path

import pyperclip

from storage import add_text_entry, add_image_entry, sha256_of_bytes

PID_FILE      = Path.home() / ".jot" / "daemon.pid"
POLL_INTERVAL = 0.5  # seconds


# ── PIL import (optional for image support) ────────────────────────────────────

try:
    from PIL import ImageGrab, Image  # type: ignore
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


# ── PID helpers ────────────────────────────────────────────────────────────────


def _write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except ValueError:
        return None


def _remove_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_is_alive(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            exit_code = ctypes.c_ulong(0)
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))  # type: ignore[attr-defined]
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            STILL_ACTIVE = 259
            return exit_code.value == STILL_ACTIVE
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


# ── Image clipboard helpers ────────────────────────────────────────────────────


def _grab_clipboard_image_bytes() -> bytes | None:
    """
    Return raw PNG bytes of the current clipboard image, or None if there is
    no image on the clipboard (or PIL is unavailable).
    """
    if not _PIL_AVAILABLE:
        return None
    try:
        img = ImageGrab.grabclipboard()
        if img is None or not isinstance(img, Image.Image):
            return None
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


# ── Watcher loop ───────────────────────────────────────────────────────────────


def _watcher_loop() -> None:
    """Poll clipboard forever, storing new text and image content."""
    last_text: str       = ""
    last_img_hash: str   = ""

    # Seed with current clipboard content so we don't double-store on start
    try:
        last_text = pyperclip.paste() or ""
    except Exception:
        last_text = ""

    img_bytes = _grab_clipboard_image_bytes()
    if img_bytes:
        last_img_hash = sha256_of_bytes(img_bytes)

    while True:
        # ── Text ──────────────────────────────────────────────────────────────
        try:
            current_text = pyperclip.paste() or ""
        except Exception:
            current_text = last_text

        if current_text and current_text != last_text:
            try:
                add_text_entry(current_text)
            except Exception:
                pass
            last_text = current_text

        # ── Image ─────────────────────────────────────────────────────────────
        img_bytes = _grab_clipboard_image_bytes()
        if img_bytes:
            current_hash = sha256_of_bytes(img_bytes)
            if current_hash != last_img_hash:
                try:
                    add_image_entry(img_bytes)
                except Exception:
                    pass
                last_img_hash = current_hash

        time.sleep(POLL_INTERVAL)


# ── Public API ─────────────────────────────────────────────────────────────────


def start() -> tuple[bool, str]:
    """Launch the daemon. Returns (success, message)."""
    pid = _read_pid()
    if pid and _pid_is_alive(pid):
        return False, f"Daemon already running (PID {pid})."

    script = Path(__file__).resolve()

    if sys.platform == "win32":
        import subprocess

        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000

        proc = subprocess.Popen(
            [sys.executable, str(script), "--run-watcher"],
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        _write_pid(proc.pid)
        return True, f"Daemon started (PID {proc.pid})."
    else:
        pid = os.fork()
        if pid > 0:
            time.sleep(0.3)
            child_pid = _read_pid()
            return True, f"Daemon started (PID {child_pid})."

        os.setsid()
        pid2 = os.fork()
        if pid2 > 0:
            os._exit(0)

        _write_pid(os.getpid())

        with open(os.devnull, "r") as dnr, open(os.devnull, "w") as dnw:
            os.dup2(dnr.fileno(), sys.stdin.fileno())
            os.dup2(dnw.fileno(), sys.stdout.fileno())
            os.dup2(dnw.fileno(), sys.stderr.fileno())

        _watcher_loop()
        os._exit(0)


def stop() -> tuple[bool, str]:
    """Stop the daemon. Returns (success, message)."""
    pid = _read_pid()
    if not pid:
        return False, "No PID file found — daemon is probably not running."

    if not _pid_is_alive(pid):
        _remove_pid()
        return False, f"Daemon PID {pid} is not alive (stale PID file cleaned up)."

    try:
        if sys.platform == "win32":
            import subprocess

            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        return False, f"Failed to stop daemon (PID {pid}): {exc}"

    _remove_pid()
    return True, f"Daemon stopped (PID {pid})."


def status() -> tuple[bool, str]:
    """Return (running, message)."""
    pid = _read_pid()
    if not pid:
        return False, "Daemon is not running (no PID file)."

    if _pid_is_alive(pid):
        img_note = "" if _PIL_AVAILABLE else "  [dim](install Pillow for image capture)[/dim]"
        return True, f"Daemon is running (PID {pid}).{img_note}"

    _remove_pid()
    return False, f"Daemon is not running (stale PID {pid} cleaned up)."


# ── Subprocess entry-point (Windows) ──────────────────────────────────────────

if __name__ == "__main__":
    if "--run-watcher" in sys.argv:
        _watcher_loop()
