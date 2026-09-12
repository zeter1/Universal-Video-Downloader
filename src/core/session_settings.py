"""Read download settings safely from UI, persisted settings, or session snapshot."""

import threading


def session_setting(app, name):
    """Return a setting without making worker threads depend on Tk variables."""
    snapshot = getattr(app, "download_settings_snapshot", None)
    settings = getattr(app, "settings", {})

    if threading.current_thread() is not threading.main_thread():
        if isinstance(snapshot, dict) and name in snapshot:
            return snapshot[name]
        if isinstance(settings, dict) and name in settings:
            return settings[name]
        raise KeyError(f"Session setting is unavailable: {name}")

    variable = getattr(app, name, None)
    if variable is not None and hasattr(variable, "get"):
        try:
            return variable.get()
        except Exception:
            pass

    if isinstance(settings, dict) and name in settings:
        return settings[name]
    if isinstance(snapshot, dict) and name in snapshot:
        return snapshot[name]
    raise AttributeError(f"Setting is unavailable: {name}")
