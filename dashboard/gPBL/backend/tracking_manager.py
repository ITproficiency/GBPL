from __future__ import annotations
import subprocess
import sys
from pathlib import Path

_active_process: subprocess.Popen | None = None


def start_tracking(source: str = "0") -> bool:
    global _active_process
    stop_tracking()

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    tracking_script = repo_root / "tracking_AI" / "blink_counter_and_EAR_plot.py"
    venv_python = repo_root / "myenv" / "bin" / "python"
    py_exec = str(venv_python) if venv_python.exists() else sys.executable

    if not tracking_script.exists():
        return False

    try:
        print(f"🚀 Launching Python AI Tracking Process with source '{source}' (headless mode)...")
        _active_process = subprocess.Popen([py_exec, str(tracking_script), str(source), "--no-gui"])
        return True
    except Exception as err:
        print(f"⚠️ Error starting AI tracking process: {err}")
        return False


import firebase_client


def stop_tracking() -> bool:
    global _active_process
    if _active_process and _active_process.poll() is None:
        try:
            print("⏹ Stopping Python AI Tracking Process...")
            _active_process.terminate()
            _active_process.wait(timeout=2)
        except Exception:
            _active_process.kill()
    _active_process = None
    try:
        firebase_client.reset_ai_data()
    except Exception:
        pass
    return True


def is_tracking_active() -> bool: 
    return _active_process is not None and _active_process.poll() is None
