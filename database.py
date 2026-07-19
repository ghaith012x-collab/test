import os, threading, json, time

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

_lock = threading.Lock()

def _path(username):
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (username or "default"))
    return os.path.join(LOG_DIR, f"{safe}.log")

def append_log(username, message):
    line = f"{time.strftime('%H:%M:%S')} {message}"
    try:
        with _lock:
            with open(_path(username), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass

def get_log(username, limit=200):
    try:
        with _lock:
            with open(_path(username), "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        return "\n".join(lines[-limit:])
    except FileNotFoundError:
        return ""
    except Exception:
        return ""
