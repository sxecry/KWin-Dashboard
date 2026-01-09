# KWinDashboardServer

KWin window/monitor/desktop state monitor and control server with JSON output and an
optional WebSocket stream. The script uses KWin scripting (qdbus/gdbus) and collects
samples from the KWin journal log.

## Features

- Window/monitor/desktop state as JSON
- Window actions: activate, minimize, maximize, fullscreen, close
- Move windows between desktops or monitors
- Optional WebSocket server for live state and commands

## Requirements

- Python 3.10+ (for `|` type hints)
- KWin (Plasma)
- qdbus/qdbus6 or gdbus
- systemctl (user units) and journalctl
- Optional: `websockets` Python package (for WS mode)

## Install (CachyOS, native Python)

Base requirements:

<pre><code>sudo pacman -S python qt6-tools glib2
</code></pre>

Note: `qdbus6` is provided by the `qt6-tools` package.

With WebSocket support:

<pre><code>sudo pacman -S python python-websockets qt6-tools glib2
</code></pre>

## Usage

Get a state snapshot:

<pre><code>./kwin_dashboard.py
</code></pre>

Pretty JSON:

<pre><code>./kwin_dashboard.py --pretty
</code></pre>

Start WS server:

<pre><code>./kwin_dashboard.py --ws --host 0.0.0.0 --port 8765
</code></pre>

Filter by PID (optional):

<pre><code>./kwin_dashboard.py --pid 12345
</code></pre>

## Window actions

<pre><code>./kwin_dashboard.py --active &lt;WINID&gt;
./kwin_dashboard.py --minimize &lt;WINID&gt;
./kwin_dashboard.py --maximize &lt;WINID&gt;
./kwin_dashboard.py --restore &lt;WINID&gt;
./kwin_dashboard.py --fullscreen &lt;WINID&gt;
./kwin_dashboard.py --close &lt;WINID&gt;
</code></pre>

Move to desktop:

<pre><code>./kwin_dashboard.py --move-desktop &lt;WINID&gt; &lt;DESKTOP_INDEX&gt;
</code></pre>

Move to monitor:

<pre><code>./kwin_dashboard.py --move-monitor &lt;WINID&gt; &lt;MONITOR_INDEX_OR_NAME&gt;
</code></pre>

## WS commands (short)

Incoming message JSON example:

<pre><code>{
  "type": "command",
  "payload": {
    "name": "ActivateWindow",
    "windowId": "0x04200007"
  }
}
</code></pre>

Known `name` values:

- `ActivateWindow`
- `CloseEvent`
- `SwitchDesktop`
- `MoveWindow`

Responses:

- `type: "ack"` command acknowledgement
- `type: "state"` current state payload

## Android app (KWin Dashboard)

This WebSocket server is used by the Android "KWin Dashboard" app. An APK is included
in this repository:

<pre><code>KWinDashboard.apk
</code></pre>

Install the APK on your device, then set the WebSocket address in the app Settings
menu.

App capabilities:

- Show all open windows
- Switch between virtual desktops
- Close applications
- Move windows between monitors
- Activate (focus) a window

## Firewall

If you use `ufw`, allow the chosen WebSocket port:

<pre><code>sudo ufw allow 8765/tcp
</code></pre>

## Security

If you run `--ws` with `--host 0.0.0.0`, anyone on your network can send commands.
Use `127.0.0.1` or put auth/proxy protection in front if exposed.

## Stability note

Error handling is not exhaustive yet, so crashes may still occur.

## License

Not specified. If you want one, tell me and I will add it.

## Notes

This is a hobby project tailored to my own needs. If you have feedback, feel free to reach out! :)
