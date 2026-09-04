from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import firebase_client

_active_process: subprocess.Popen | None = None
_log_file = None
_last_error: str | None = None

_PROBE_IMPORTS = "import mediapipe, cv2"
_PROBE_TIMEOUT_SEC = 20.0


def get_last_error() -> str | None:
    """Reason the last start_tracking() failed, or None after a successful spawn.

    Session/API can read this and include it as ``error`` on the JSON returned
    to /api/tracking/start. This module still returns bool from start_tracking
    so existing session_manager callers keep working.
    """
    return _last_error


def _set_last_error(message: str | None) -> None:
    global _last_error
    _last_error = message
    if message:
        print(f"⚠️ Tracking start error: {message}")


def wait_for_mjpeg_server(ports: list[int] = [8089, 8090, 8091], timeout: float = 5.0) -> bool:
    start_t = time.time()
    while time.time() - start_t < timeout:
        for port in ports:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    return True
            except Exception:
                pass
        time.sleep(0.2)
    return False


def _probe_imports(py_exec: str) -> bool:
    """Return True if py_exec can import mediapipe and cv2. Catch-all; never raise."""
    run_kwargs: dict = {
        "capture_output": True,
        "timeout": _PROBE_TIMEOUT_SEC,
        "check": False,
    }
    if os.name == "nt":
        run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [py_exec, "-c", _PROBE_IMPORTS],
            **run_kwargs,
        )
        return result.returncode == 0
    except Exception:
        return False


def _resolve_python(repo_root: Path) -> str | None:
    """Prefer tracking_AI/.venv. Never return an interpreter that lacks mediapipe.

    sys.executable is only used if it also passes the mediapipe/cv2 probe.
    Returns None when no candidate works so start_tracking does not Popen.
    """
    candidates = [
        repo_root / "tracking_AI" / ".venv" / "Scripts" / "python.exe",
        repo_root / "tracking_AI" / ".venv" / "bin" / "python",
        repo_root / "myenv" / "Scripts" / "python.exe",
        repo_root / "myenv" / "bin" / "python",
    ]
    seen: set[str] = set()
    for path in candidates:
        if not path.exists():
            continue
        exe = str(path)
        if exe in seen:
            continue
        seen.add(exe)
        if _probe_imports(exe):
            return exe

    backend_exe = sys.executable
    if backend_exe not in seen and _probe_imports(backend_exe):
        return backend_exe
    return None


def _close_log_file() -> None:
    global _log_file
    if _log_file is not None:
        try:
            _log_file.close()
        except Exception:
            pass
        _log_file = None


def start_tracking(source: str = "0") -> bool:
    global _active_process, _log_file
    _set_last_error(None)
    stop_tracking()

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    tracking_dir = repo_root / "tracking_AI"
    tracking_script = tracking_dir / "blink_counter_and_EAR_plot.py"

    if not tracking_script.exists():
        _set_last_error(f"Tracking script not found: {tracking_script}")
        return False

    py_exec = _resolve_python(repo_root)
    if not py_exec:
        _set_last_error(
            "No Python with mediapipe/cv2 found. Create tracking_AI/.venv "
            "(Python 3.10-3.12) and pip install -r tracking_AI/requirements.txt. "
            "The dashboard interpreter is not used as a fallback."
        )
        return False

    try:
        print(f"🚀 Launching Python AI Tracking Process using '{py_exec}' with source '{source}'...")
        log_path = tracking_dir / "tracking_ai.log"
        _log_file = open(log_path, "w")
        child_env = os.environ.copy()
        child_env["POSTURECARE_API_URL"] = "http://127.0.0.1:8080"
        popen_kwargs: dict = {
            "stdout": _log_file,
            "stderr": _log_file,
            "cwd": str(tracking_dir),
            "env": child_env,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        _active_process = subprocess.Popen(
            [py_exec, str(tracking_script), str(source), "--no-gui"],
            **popen_kwargs,
        )
        ready = wait_for_mjpeg_server(ports=[8089, 8090, 8091], timeout=15.0)
        if ready:
            print("🟢 MJPEG Stream Server is READY!")
            _set_last_error(None)
            return True
        if _active_process.poll() is not None:
            code = _active_process.poll()
            _set_last_error(f"Tracking process exited before MJPEG ready (code {code})")
        else:
            _set_last_error("MJPEG stream server did not become ready")
        return False
    except Exception as err:
        _set_last_error(f"Failed to start tracking process: {err}")
        _close_log_file()
        return False


def stop_tracking() -> bool:
    global _active_process
    if _active_process and _active_process.poll() is None:
        try:
            print("⏹ Stopping Python AI Tracking Process...")
            pid = _active_process.pid
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    _active_process.wait(timeout=2)
                except Exception:
                    pass
            else:
                _active_process.terminate()
                try:
                    _active_process.wait(timeout=2)
                except Exception:
                    _active_process.kill()
        except Exception:
            try:
                _active_process.kill()
            except Exception:
                pass
    _active_process = None

    _close_log_file()

    try:
        firebase_client.reset_ai_data()
    except Exception:
        pass
    return True


def is_tracking_active() -> bool:
    return _active_process is not None and _active_process.poll() is None
