"""
NetOps — No AI. User enters device and command; app runs it via SSH and shows raw output.
Same login and credential cache as the main app; no DeepSeek/Claude.
"""
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

# Import DeviceSession from parent project
import sys
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))
try:
    from device_session import DeviceSession
except ImportError:
    from automation.device_session import DeviceSession

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE_DIR))
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "no-ai-change-me")
app.permanent_session_lifetime = 86400  # 24 hours

CREDENTIAL_CACHE_TTL = 86400
_cred_cache: dict = {}
_active_conns: dict = {}


def _get_conn_id() -> str:
    if "conn_id" not in session:
        session.permanent = True
        session["conn_id"] = str(uuid.uuid4())
    return session["conn_id"]


def _store_creds(conn_id: str, username: str, password: str,
                 enable_secret: Optional[str], platform: Optional[str], devices: dict) -> None:
    _cred_cache[conn_id] = {
        "username": username,
        "password": password,
        "enable_secret": enable_secret,
        "platform": platform,
        "devices": devices,
        "stored_at": time.time(),
    }


def _get_creds(conn_id: str) -> Optional[dict]:
    entry = _cred_cache.get(conn_id)
    if not entry:
        return None
    if (time.time() - entry["stored_at"]) > CREDENTIAL_CACHE_TTL:
        del _cred_cache[conn_id]
        for k in list(_active_conns.keys()):
            if k.startswith(conn_id + ":"):
                try:
                    _active_conns[k].disconnect()
                except Exception:
                    pass
                del _active_conns[k]
        return None
    return entry


def _clear_creds(conn_id: str) -> None:
    _cred_cache.pop(conn_id, None)
    for k in list(_active_conns.keys()):
        if k.startswith(conn_id + ":"):
            try:
                _active_conns[k].disconnect()
            except Exception:
                pass
            del _active_conns[k]


def _resolve_host(conn_id: str, device_name: str) -> str:
    creds = _get_creds(conn_id)
    if not creds:
        raise RuntimeError("Session expired. Please log in again.")
    devices = creds.get("devices", {})
    for name, ip in devices.items():
        if name.upper() == device_name.upper():
            return ip
    return device_name.strip()


def _get_or_connect(conn_id: str, device_name: str) -> DeviceSession:
    host = _resolve_host(conn_id, device_name)
    key = f"{conn_id}:{host}"
    if key in _active_conns:
        return _active_conns[key]
    creds = _get_creds(conn_id)
    if not creds:
        raise RuntimeError("Session expired. Please log in again.")
    ds = DeviceSession(
        host=host,
        username=creds["username"],
        password=creds["password"],
        platform=creds.get("platform"),
        enable_secret=creds.get("enable_secret"),
    )
    ds.connect()
    _active_conns[key] = ds
    return ds


def _execute_command(conn_id: str, device_name: str, command: str) -> str:
    try:
        ds = _get_or_connect(conn_id, device_name)
        return ds.view(command.strip()) or "(No output returned)"
    except RuntimeError as e:
        return f"[ERROR] {e}"
    except Exception as e:
        msg = str(e).lower()
        if "timed out" in msg or "timeout" in msg:
            return f"[ERROR] Timed out connecting to {device_name}."
        if "authentication" in msg or "permission denied" in msg:
            return f"[ERROR] Authentication failed on {device_name}."
        if "connection refused" in msg:
            return f"[ERROR] SSH refused on {device_name}."
        try:
            host = _resolve_host(conn_id, device_name)
            _active_conns.pop(f"{conn_id}:{host}", None)
        except Exception:
            pass
        return f"[ERROR] {e}"


@app.route("/")
def root():
    if _get_creds(_get_conn_id()):
        return redirect(url_for("run"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    conn_id = _get_conn_id()
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        enable_secret = (request.form.get("enable_secret") or "").strip() or None
        platform = (request.form.get("platform") or "").strip() or None
        raw_devices = request.form.get("devices") or ""
        devices = {}
        for line in raw_devices.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"[=:\s]+", line, maxsplit=1)
            if len(parts) == 2:
                name, ip = parts[0].strip().upper(), parts[1].strip()
                if name and ip:
                    devices[name] = ip
        if not username or not password:
            error = "Username and password are required."
        else:
            _store_creds(conn_id, username, password, enable_secret, platform, devices)
            return redirect(url_for("run"))
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    conn_id = session.get("conn_id")
    if conn_id:
        _clear_creds(conn_id)
    session.clear()
    return redirect(url_for("login"))


@app.route("/run")
def run():
    conn_id = _get_conn_id()
    creds = _get_creds(conn_id)
    if not creds:
        return redirect(url_for("login"))
    devices = list(creds.get("devices", {}).keys())
    return render_template("run.html", devices=devices)


@app.route("/api/run", methods=["POST"])
def api_run():
    conn_id = _get_conn_id()
    if not _get_creds(conn_id):
        return jsonify({"error": "Session expired. Please log in again."}), 401
    data = request.get_json(silent=True) or {}
    device_name = (data.get("device") or "").strip()
    command = (data.get("command") or "").strip()
    if not device_name or not command:
        return jsonify({"error": "Device and command are required."}), 400
    output = _execute_command(conn_id, device_name, command)
    return jsonify({"output": output, "device": device_name, "command": command})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8890, debug=True)
