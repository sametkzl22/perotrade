import json
import os
import sys

def get_app_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

SETTINGS_FILE = os.path.join(get_app_path(), "active_mode.json")

def is_real_mode_active() -> bool:
    """Returns True if Real Mode is active, otherwise False (Demo)."""
    if not os.path.exists(SETTINGS_FILE):
        return False
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("use_real_api", False)
    except Exception:
        return False

def set_real_mode(active: bool):
    """Sets the active mode and saves it."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"use_real_api": active}, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to write active_mode.json: {e}")
