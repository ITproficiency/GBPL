from __future__ import annotations
import subprocess
import sys
from pathlib import Path

_active_process: subprocess.Popen | None = None


import socket
import time

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


def start_tracking(source: str = "0") -> bool:
    global _active_process
    stop_tracking()

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    tracking_script = repo_root / "tracking_AI" / "blink_counter_and_EAR_plot.py"
    venv_py = Path("/Users/yangtao/.platformio/penv/bin/python")
    py_exec = str(venv_py) if venv_py.exists() else sys.executable

    if not tracking_script.exists():
        return False

    try:
        print(f"🚀 Launching Python AI Tracking Process using '{py_exec}' with source '{source}'...")
        log_file = open("/tmp/tracking_ai.log", "w")
        _active_process = subprocess.Popen(
            [py_exec, str(tracking_script), str(source), "--no-gui"],
            stdout=log_file,
            stderr=log_file
        )
        ready = wait_for_mjpeg_server(ports=[8089, 8090, 8091], timeout=15.0)
        if ready:
            print("🟢 MJPEG Stream Server is READY!")
        else:
            if _active_process.poll() is not None:
                print(f"⚠️ Tracking process exited with code {_active_process.poll()}")
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
            try:
                _active_process.kill()
            except Exception:
                pass
    _active_process = None
    
    # Ensure any stray background tracking process is killed to prevent camera conflicts
    try:
        subprocess.run(["pkill", "-9", "-f", "blink_counter_and_EAR_plot.py"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    try:
        firebase_client.reset_ai_data()
    except Exception:
        pass
    return True


def is_tracking_active() -> bool: 
    return _active_process is not None and _active_process.poll() is None
