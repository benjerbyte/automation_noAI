# NetOps (no AI)

Same login and SSH as the main app, but **no AI**. You choose the device and type the exact CLI command; the app runs it and shows raw output.

## Run

From the **project root** (parent of `no_AI`), so that `device_session` can be imported:

```bash
cd /path/to/network_mcp
python no_AI/app.py
```

Then open **http://localhost:8890** (port 8890 to avoid clashing with the main app on 8889).

## Flow

1. **Login** — Same as main app: username, password, optional enable secret, platform, optional device list (NAME=IP).
2. **Run** — Pick a device (from dropdown if you registered any, or type an IP/hostname) and enter the exact command (e.g. `show interfaces status`). Click RUN. Raw CLI output appears below.

No API keys, no DeepSeek/Claude. Requires only Flask and Netmiko (and `device_session` from the parent project).
