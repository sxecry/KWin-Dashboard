#!/usr/bin/env python3
import argparse
import asyncio
import json
import re
import os
import shutil
import subprocess
import shlex
import tempfile
import time
from datetime import datetime, timezone

def which_any(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None

def get_kwin_support_info() -> str | None:
    qdbus = which_any("qdbus", "qdbus6")
    if qdbus:
        try:
            cp = run([qdbus, "org.kde.KWin", "/KWin", "org.kde.KWin.supportInformation"])
            return cp.stdout
        except subprocess.CalledProcessError:
            return None

    gdbus = which_any("gdbus")
    if gdbus:
        try:
            cp = run([
                gdbus, "call", "--session",
                "--dest", "org.kde.KWin",
                "--object-path", "/KWin",
                "--method", "org.kde.KWin.supportInformation"
            ])
            return cp.stdout
        except subprocess.CalledProcessError:
            return None

    return None

def parse_screens_from_support_info(text: str) -> list[dict]:
    screens = []
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"Screen (\d+):", line)
        if m:
            if current and "x" in current:
                screens.append(current)
            current = {"index": int(m.group(1))}
            continue
        if not current:
            continue
        if line.startswith("Name:"):
            current["name"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Geometry:"):
            gm = re.search(r"Geometry:\s*(\d+),(\d+),(\d+)x(\d+)", line)
            if gm:
                current["x"] = int(gm.group(1))
                current["y"] = int(gm.group(2))
                current["width"] = int(gm.group(3))
                current["height"] = int(gm.group(4))
            continue
    if current and "x" in current:
        screens.append(current)
    return screens

def normalize_winid(winid: str | None) -> str:
    if not winid:
        return ""
    return winid.strip().lower().strip("{}")

def find_window_for_id(windows: list[dict], winid: str | None) -> dict | None:
    target = normalize_winid(winid)
    if not target:
        return None
    for w in windows:
        if normalize_winid(w.get("internalId")) == target:
            return w
        if normalize_winid(w.get("windowId")) == target:
            return w
        if normalize_winid(w.get("id")) == target:
            return w
    return None

def switch_to_screen_for_window(windows: list[dict], winid: str | None) -> None:
    w = find_window_for_id(windows, winid)
    if not w:
        return
    fg = w.get("frameGeometry") or {}
    if not fg:
        return
    cx = fg.get("x", 0) + (fg.get("width", 0) / 2)
    cy = fg.get("y", 0) + (fg.get("height", 0) / 2)

    info = get_kwin_support_info()
    if not info:
        return
    screens = parse_screens_from_support_info(info)
    if not screens:
        return
    idx = None
    for s in screens:
        sx, sy = s.get("x"), s.get("y")
        sw, sh = s.get("width"), s.get("height")
        if sx is None or sy is None or sw is None or sh is None:
            continue
        if sx <= cx < sx + sw and sy <= cy < sy + sh:
            idx = s.get("index")
            break
    if idx is None:
        return
    if invoke_kwin_shortcut(f"Switch to Screen {idx}"):
        return
    invoke_kwin_shortcut(f"Switch to Screen {idx + 1}")

def find_window_fullscreen(payload_state: dict, window_id: str) -> bool:
    for monitor in payload_state.get("monitors") or []:
        for desktop in monitor.get("desktops") or []:
            for win in desktop.get("windows") or []:
                if win.get("id") == window_id:
                    return bool(win.get("fullScreen"))
    return False

def find_window_monitor(payload_state: dict, window_id: str) -> int | None:
    for monitor in payload_state.get("monitors") or []:
        monitor_id = monitor.get("monitor_id") or monitor.get("monitorId")
        for desktop in monitor.get("desktops") or []:
            for win in desktop.get("windows") or []:
                if win.get("id") == window_id:
                    return monitor_id
    return None

def find_window_pinned(payload_state: dict, window_id: str) -> bool | None:
    for monitor in payload_state.get("monitors") or []:
        for desktop in monitor.get("desktops") or []:
            for win in desktop.get("windows") or []:
                if win.get("id") == window_id:
                    return bool(win.get("on_all_desktops"))
    return None

def is_monitor_all_pinned(payload_state: dict, monitor_id: int | None) -> bool:
    if monitor_id is None:
        return False
    target = None
    for monitor in payload_state.get("monitors") or []:
        mid = monitor.get("monitor_id") or monitor.get("monitorId")
        if mid == monitor_id:
            target = monitor
            break
    if not target:
        return False
    windows = []
    for desktop in target.get("desktops") or []:
        windows.extend(desktop.get("windows") or [])
    if not windows:
        return False
    return all(w.get("on_all_desktops") for w in windows)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def detect_kwin_service(preferred: str | None = None) -> str:
    if preferred and preferred != "auto":
        return preferred

    # Auto: use the active service.
    candidates = [
        "plasma-kwin_wayland.service",
        "plasma-kwin_x11.service",
        "kwin_wayland.service",
        "kwin_x11.service",
    ]
    systemctl = which_any("systemctl")
    if not systemctl:
        return "plasma-kwin_wayland.service"

    for svc in candidates:
        try:
            cp = subprocess.run(
                [systemctl, "--user", "is-active", svc],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            if cp.returncode == 0 and cp.stdout.strip() == "active":
                return svc
        except Exception:
            pass

    # fallback
    return "plasma-kwin_wayland.service"

def kwin_load_start_unload(js_path: str, script_id: str) -> None:
    qdbus = which_any("qdbus", "qdbus6")
    if qdbus:
        run([qdbus, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.loadScript", js_path, script_id])
        run([qdbus, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.start"])
        run([qdbus, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", script_id])
        return

    gdbus = which_any("gdbus")
    if gdbus:
        run([gdbus, "call", "--session",
             "--dest", "org.kde.KWin",
             "--object-path", "/Scripting",
             "--method", "org.kde.kwin.Scripting.loadScript",
             js_path, script_id])
        run([gdbus, "call", "--session",
             "--dest", "org.kde.KWin",
             "--object-path", "/Scripting",
             "--method", "org.kde.kwin.Scripting.start"])
        run([gdbus, "call", "--session",
             "--dest", "org.kde.KWin",
             "--object-path", "/Scripting",
             "--method", "org.kde.kwin.Scripting.unloadScript",
             script_id])
        return

    raise RuntimeError("Missing qdbus/qdbus6 or gdbus.")

def invoke_kwin_shortcut(shortcut_name: str) -> bool:
    qdbus = which_any("qdbus", "qdbus6")
    if qdbus:
        try:
            run([
                qdbus,
                "org.kde.kglobalaccel",
                "/component/kwin",
                "org.kde.kglobalaccel.Component.invokeShortcut",
                shortcut_name
            ])
            return True
        except subprocess.CalledProcessError:
            return False

    gdbus = which_any("gdbus")
    if gdbus:
        try:
            run([
                gdbus, "call", "--session",
                "--dest", "org.kde.kglobalaccel",
                "--object-path", "/component/kwin",
                "--method", "org.kde.kglobalaccel.Component.invokeShortcut",
                shortcut_name
            ])
            return True
        except subprocess.CalledProcessError:
            return False

    return False

def read_kwin_log_since(service: str, since_iso: str) -> list[str]:
    cp = run([
        "journalctl", "--user", "-u", service,
        "--since", since_iso,
        "-o", "cat",
        "--no-pager"
    ])
    lines = []
    for line in cp.stdout.splitlines():
        # KWin JS print() usually has the "js: " prefix.
        if line.startswith("js: "):
            lines.append(line[4:])
        else:
            lines.append(line)
    return lines

def safe_read_kwin_log_since(service: str, since_iso: str) -> list[str]:
    try:
        return read_kwin_log_since(service, since_iso)
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(
            f"journalctl error while reading '{service}' unit."
            + (f" Details: {msg}" if msg else "")
        ) from exc

def build_js(target_pid: int | None) -> str:
    pid_val = -1 if target_pid is None else target_pid
    return f"""
(function () {{
  const targetPid = {pid_val};
  const wins = workspace.stackingOrder;
  const resourceClassBlocklist = new Set([
    "org.kde.plasmashell"
  ]);
  const outputs = workspace.outputs || [];
  const desktops = workspace.desktops || [];
  const currentDesktop = workspace.currentDesktop || null;

  function rectToObj(r) {{
    if (!r) return null;
    return {{ x: r.x, y: r.y, width: r.width, height: r.height }};
  }}

  function outputToObj(o) {{
    if (!o) return null;
    return {{
      name: o.name || null,
      manufacturer: o.manufacturer || null,
      model: o.model || null,
      geometry: rectToObj(o.geometry)
    }};
  }}

  function desktopsToNames(w) {{
    if (w.onAllDesktops) return ["ALL"];
    const ds = w.desktops || [];
    const names = [];
    for (let i = 0; i < ds.length; i++) {{
      const d = ds[i];
      names.push(d && d.name ? d.name : String(d));
    }}
    return names;
  }}

  const outList = [];
  if (outputs && outputs.length) {{
    for (let i = 0; i < outputs.length; i++) {{
      outList.push(outputToObj(outputs[i]));
    }}
  }} else if (workspace.numScreens && workspace.numScreens > 0) {{
    for (let i = 0; i < workspace.numScreens; i++) {{
      const g = workspace.screenGeometry(i);
      outList.push({{
        name: "Screen " + (i + 1),
        manufacturer: null,
        model: null,
        geometry: rectToObj(g)
      }});
    }}
  }}

  const desktopList = [];
  if (desktops && desktops.length) {{
    for (let i = 0; i < desktops.length; i++) {{
      const d = desktops[i];
      desktopList.push({{ name: d && d.name ? d.name : String(d) }});
    }}
  }} else if (workspace.desktopCount && workspace.desktopCount > 0) {{
    for (let i = 1; i <= workspace.desktopCount; i++) {{
      const name = workspace.desktopName(i);
      desktopList.push({{ name: name ? name : String(i) }});
    }}
  }}

  const meta = {{
    __type: "meta",
    outputs: outList,
    desktops: desktopList,
    activeDesktopName: currentDesktop && currentDesktop.name ? currentDesktop.name : null
  }};
  print(JSON.stringify(meta));

  const aw = workspace.activeClient;
  for (let i = 0; i < wins.length; i++) {{
    const w = wins[i];
    if (!w) continue;

    // Useful filtering: skip panel/dock/desktop/special windows.
    if (w.deleted) continue;
    if (!w.managed) continue;
    if (w.desktopWindow || w.dock || w.specialWindow) continue;
    if (w.resourceClass && resourceClassBlocklist.has(w.resourceClass)) continue;

    if (targetPid !== -1 && w.pid !== targetPid) continue;

    const winId = (w.internalId !== undefined && w.internalId !== null)
      ? String(w.internalId)
      : (w.windowId !== undefined && w.windowId !== null ? String(w.windowId) : null);

    const maximizeMode = (w.maximizeMode !== undefined && w.maximizeMode !== null)
      ? Number(w.maximizeMode)
      : 0;
    const maximized = (maximizeMode > 0)
      || (w.maximized === true)
      || (w.maximizedHoriz === true && w.maximizedVert === true)
      || (w.maximizedHoriz === true)
      || (w.maximizedVert === true);

    const activeMatch = aw
      ? (normId(aw.internalId) === normId(internalId) || normId(aw.windowId) === normId(windowId))
      : false;
    const out = {{
      pid: w.pid,
      caption: w.caption || null,
      resourceName: w.resourceName || null,
      resourceClass: w.resourceClass || null,
      desktopFileName: w.desktopFileName || null,
      windowId: winId,
      internalId: (w.internalId !== undefined && w.internalId !== null) ? String(w.internalId) : null,

      onAllDesktops: !!w.onAllDesktops,
      desktops: desktopsToNames(w),

      // Window geometry (optional but useful).
      frameGeometry: rectToObj(w.frameGeometry),

      // Monitor / output info.
      output: outputToObj(w.output),

      minimized: !!w.minimized,
      maximized: maximized,
      fullScreen: !!w.fullScreen,
      active: (w.active === true) || activeMatch
    }};

    print(JSON.stringify(out));
  }}
}})();
""".strip()

def build_js_action(
    target_pid: int | None,
    target_winid: str | None,
    action: str | None,
    target_desktop: str | None,
    target_monitor: str | None,
) -> str:
    pid_val = -1 if target_pid is None else target_pid
    winid_val = "" if target_winid is None else target_winid
    action_val = "" if action is None else action
    desktop_val = "" if target_desktop is None else target_desktop
    monitor_val = "" if target_monitor is None else target_monitor
    return f"""
(function () {{
  const targetPid = {pid_val};
  const targetWinId = {json.dumps(winid_val)};
  const action = {json.dumps(action_val)};
  const targetDesktop = {json.dumps(desktop_val)};
  const targetMonitor = {json.dumps(monitor_val)};
  const wins = workspace.stackingOrder;

  function normId(v) {{
    if (v === undefined || v === null) return "";
    return String(v).toLowerCase().replace(/[{{}}]/g, "");
  }}

  if (action === "print-active") {{
    const aw = workspace.activeClient;
    const out = {{
      activeInternalId: (aw && aw.internalId !== undefined && aw.internalId !== null) ? String(aw.internalId) : null,
      activeWindowId: (aw && aw.windowId !== undefined && aw.windowId !== null) ? String(aw.windowId) : null
    }};
    print(JSON.stringify(out));
    return;
  }}

  if (action === "switch-desktop") {{
    const dnum = parseInt(targetDesktop, 10);
    if (Number.isFinite(dnum) && dnum > 0) {{
      if (workspace.desktops && workspace.desktops.length >= dnum) {{
        const d = workspace.desktops[dnum - 1];
        if (workspace.currentDesktop !== undefined && workspace.currentDesktop !== null) {{
          workspace.currentDesktop = d;
        }} else if (workspace.currentDesktopNumber !== undefined && workspace.currentDesktopNumber !== null) {{
          workspace.currentDesktopNumber = dnum;
        }}
      }} else if (workspace.currentDesktopNumber !== undefined && workspace.currentDesktopNumber !== null) {{
        workspace.currentDesktopNumber = dnum;
      }}
    }}
    return;
  }}

  for (let i = 0; i < wins.length; i++) {{
    const w = wins[i];
    if (!w) continue;
    if (w.deleted) continue;
    if (!w.managed) continue;
    if (w.desktopWindow || w.dock || w.specialWindow) continue;

    if (targetPid !== -1 && w.pid !== targetPid) continue;

    const internalId = (w.internalId !== undefined && w.internalId !== null) ? String(w.internalId) : null;
    const windowId = (w.windowId !== undefined && w.windowId !== null) ? String(w.windowId) : null;
    const matchId = targetWinId
      ? (normId(internalId) === normId(targetWinId) || normId(windowId) === normId(targetWinId))
      : true;

    if (!matchId) continue;

    if (action === "activate") {{
      if (w.minimized !== undefined && w.minimized !== null) {{
        w.minimized = false;
      }}
      if (!w.onAllDesktops) {{
        if (w.desktops && w.desktops.length && workspace.currentDesktop) {{
          workspace.currentDesktop = w.desktops[0];
        }} else if (w.desktop !== undefined && w.desktop !== null &&
                   workspace.currentDesktopNumber !== undefined &&
                   workspace.currentDesktopNumber !== null) {{
          workspace.currentDesktopNumber = w.desktop;
        }}
      }}
      if (typeof w.activate === "function") {{
        w.activate();
      }}
      if (workspace.activeClient !== undefined && workspace.activeClient !== null) {{
        workspace.activeClient = w;
      }}
      if (typeof workspace.activateClient === "function") {{
        workspace.activateClient(w);
      }}
      if (typeof workspace.raiseClient === "function") {{
        workspace.raiseClient(w);
      }}
      if (typeof w.requestActivate === "function") {{
        w.requestActivate();
      }}
      if (w.demandsAttention !== undefined && w.demandsAttention !== null) {{
        w.demandsAttention = true;
      }}
      if (typeof w.raise === "function") {{
        w.raise();
      }}
      if (typeof workspace.forceActiveClient === "function") {{
        workspace.forceActiveClient(w);
      }}
      if (w.active !== undefined && w.active !== null) {{
        w.active = true;
      }}
    }} else if (action === "clear-attention") {{
      if (w.demandsAttention !== undefined && w.demandsAttention !== null) {{
        w.demandsAttention = false;
      }}
    }} else if (action === "maximize") {{
      if (typeof w.setMaximize === "function") {{
        w.setMaximize(true, true);
      }} else if (w.maximized !== undefined && w.maximized !== null) {{
        w.maximized = true;
      }} else {{
        w.maximizedHoriz = true;
        w.maximizedVert = true;
      }}
    }} else if (action === "minimize") {{
      w.minimized = true;
    }} else if (action === "restore") {{
      w.minimized = false;
      if (typeof w.setMaximize === "function") {{
        w.setMaximize(false, false);
      }} else if (w.maximized !== undefined && w.maximized !== null) {{
        w.maximized = false;
      }} else {{
        w.maximizedHoriz = false;
        w.maximizedVert = false;
      }}
    }} else if (action === "fullscreen") {{
      w.fullScreen = true;
    }} else if (action === "fullscreen-exit") {{
      w.fullScreen = false;
    }} else if (action === "close") {{
      if (typeof w.closeWindow === "function") {{
        w.closeWindow();
      }} else if (typeof workspace.closeWindow === "function") {{
        workspace.closeWindow(w);
      }}
    }} else if (action === "pin-toggle") {{
      if (w.onAllDesktops !== undefined && w.onAllDesktops !== null) {{
        w.onAllDesktops = !w.onAllDesktops;
      }}
    }} else if (action === "move-desktop") {{
      const dnum = parseInt(targetDesktop, 10);
      if (Number.isFinite(dnum) && dnum > 0) {{
        if (workspace.desktops && workspace.desktops.length >= dnum) {{
          const d = workspace.desktops[dnum - 1];
          if (w.desktops !== undefined && w.desktops !== null) {{
            w.desktops = [d];
          }} else if (w.desktop !== undefined && w.desktop !== null) {{
            w.desktop = dnum;
          }}
        }} else if (w.desktop !== undefined && w.desktop !== null) {{
          w.desktop = dnum;
        }}
      }}
    }} else if (action === "move-monitor") {{
      let out = null;
      if (workspace.outputs && workspace.outputs.length) {{
        const mnum = parseInt(targetMonitor, 10);
        if (Number.isFinite(mnum) && mnum > 0 && workspace.outputs.length >= mnum) {{
          out = workspace.outputs[mnum - 1];
        }} else if (targetMonitor) {{
          const tname = String(targetMonitor).toLowerCase();
          for (let i = 0; i < workspace.outputs.length; i++) {{
            const o = workspace.outputs[i];
            if (o && o.name && String(o.name).toLowerCase() === tname) {{
              out = o;
              break;
            }}
          }}
        }}
      }}
      if (out) {{
        if (typeof workspace.sendClientToOutput === "function") {{
          workspace.sendClientToOutput(w, out);
        }} else if (typeof w.setOutput === "function") {{
          w.setOutput(out);
        }} else if (w.output !== undefined && w.output !== null) {{
          w.output = out;
        }}
      }}
    }}
  }}
}})();
""".strip()

def parse_json_lines(lines: list[str]) -> tuple[dict, list[dict]]:
    meta = {}
    windows = []
    for ln in lines:
        s = ln.strip()
        if not (s.startswith("{") and s.endswith("}")):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if obj.get("__type") == "meta":
            meta = obj
        else:
            windows.append(obj)
    return meta, windows

def sanitize_exec_command(value: str | None) -> str | None:
    if not value:
        return None
    placeholders = ("%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%i", "%c", "%k", "%v", "%m")
    parts = value.split()
    kept = [part for part in parts if not (part in placeholders or part.startswith("%"))]
    return " ".join(kept).strip() or None

def launch_exec_command(exec_cmd: str | None) -> bool:
    if not exec_cmd:
        return False
    try:
        args = shlex.split(exec_cmd)
        if not args:
            return False
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def send_keypress(key: str | None) -> bool:
    if not key:
        return False
    tool = which_any("wtype", "xdotool")
    if not tool:
        return False
    try:
        if tool.endswith("wtype"):
            if "+" in key:
                parts = [p for p in key.split("+") if p]
                if not parts:
                    return False
                modifiers = parts[:-1]
                base = parts[-1]
                cmd = [tool]
                for mod in modifiers:
                    cmd += ["-M", mod]
                cmd += ["-k", base]
                for mod in reversed(modifiers):
                    cmd += ["-m", mod]
                run(cmd)
                return True
            if len(key) == 1:
                run([tool, key])
                return True
            run([tool, "-k", key])
            return True
        if tool.endswith("xdotool"):
            run([tool, "key", "--clearmodifiers", key])
            return True
    except subprocess.CalledProcessError:
        return False
    return False

def iter_desktop_entry_info(path: str) -> tuple[str | None, str | None]:
    lang = os.environ.get("LANG", "")
    lang = lang.split(".", 1)[0]
    lang_short = lang.split("_", 1)[0] if "_" in lang else ""
    in_section = False
    name = None
    localized = None
    exec_cmd = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    in_section = (line == "[Desktop Entry]")
                    continue
                if not in_section or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if key == "Name":
                    name = val
                elif lang and key == f"Name[{lang}]":
                    localized = val
                elif lang_short and key == f"Name[{lang_short}]":
                    localized = val
                elif key == "X-GNOME-FullName" and not name:
                    name = val
                elif key == "Exec":
                    exec_cmd = sanitize_exec_command(val)
    except OSError:
        return None, None
    return localized or name, exec_cmd

def find_desktop_file(desktop_file_name: str) -> str | None:
    if not desktop_file_name:
        return None
    if os.path.isabs(desktop_file_name) and os.path.exists(desktop_file_name):
        return desktop_file_name

    names = [desktop_file_name]
    if not desktop_file_name.endswith(".desktop"):
        names.append(desktop_file_name + ".desktop")

    data_dirs = []
    data_dirs.append(os.path.expanduser("~/.local/share"))
    xdg_dirs = os.environ.get("XDG_DATA_DIRS", "")
    if xdg_dirs:
        data_dirs.extend([d for d in xdg_dirs.split(":") if d])
    data_dirs.extend([
        "/usr/local/share",
        "/usr/share",
        "/var/lib/flatpak/exports/share",
        "/usr/share/flatpak/exports/share",
        "/var/lib/snapd/desktop",
    ])

    for base in data_dirs:
        app_dir = os.path.join(base, "applications")
        for name in names:
            path = os.path.join(app_dir, name)
            if os.path.exists(path):
                return path
    return None

def enrich_app_names(windows: list[dict]) -> None:
    cache: dict[str, tuple[str | None, str | None]] = {}
    for w in windows:
        candidates = []
        for key in ("desktopFileName", "resourceClass", "resourceName"):
            val = w.get(key)
            if not val:
                continue
            candidates.append(val)
            lower = str(val).lower()
            if lower != val:
                candidates.append(lower)

        app_name = None
        app_exec = None
        for candidate in candidates:
            if candidate in cache:
                app_name, app_exec = cache[candidate]
            else:
                path = find_desktop_file(str(candidate))
                app_name, app_exec = iter_desktop_entry_info(path) if path else (None, None)
                cache[candidate] = (app_name, app_exec)
            if app_name or app_exec:
                break

        w["appName"] = app_name
        w["appExec"] = app_exec

def get_service_candidates(preferred: str | None) -> list[str]:
    candidates = [
        "plasma-kwin_wayland.service",
        "plasma-kwin_x11.service",
        "kwin_wayland.service",
        "kwin_x11.service",
    ]
    if preferred and preferred != "auto":
        return [preferred]
    return candidates

def order_candidates(preferred: str | None, candidates: list[str]) -> list[str]:
    service = detect_kwin_service(preferred)
    if service in candidates:
        return [service] + [c for c in candidates if c != service]
    return candidates

def resolve_services(preferred: str | None) -> list[str]:
    candidates = get_service_candidates(preferred)
    return order_candidates(preferred, candidates)

def collect_kwin_lines(services: list[str], since_iso: str) -> tuple[str, list[str]]:
    last_err = None
    for idx, service in enumerate(services):
        for _ in range(3):
            try:
                lines = safe_read_kwin_log_since(service, since_iso)
                if lines:
                    return service, lines
            except RuntimeError as exc:
                last_err = exc
            time.sleep(0.15)
        if idx == len(services) - 1:
            break
    if last_err:
        raise last_err
    return services[0], []

def collect_windows(pid: int | None, services: list[str], since_iso: str) -> tuple[str, dict, list[dict]]:
    script_id = f"winstate_sample_{os.getpid()}"
    js_code = build_js(pid)

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        js_path = f.name
        f.write(js_code)

    try:
        kwin_load_start_unload(js_path, script_id)
        service, lines = collect_kwin_lines(services, since_iso)
        meta, windows = parse_json_lines(lines)
        enrich_app_names(windows)
        return service, meta, windows
    finally:
        try:
            os.remove(js_path)
        except OSError:
            pass

def run_action(
    pid: int | None,
    winid: str | None,
    action: str | None,
    target_desktop: str | None,
    target_monitor: str | None
) -> None:
    script_id = f"winstate_action_{os.getpid()}"
    js_code = build_js_action(pid, winid, action, target_desktop, target_monitor)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        js_path = f.name
        f.write(js_code)
    try:
        kwin_load_start_unload(js_path, script_id)
    finally:
        try:
            os.remove(js_path)
        except OSError:
            pass

def format_monitor_name(output_obj: dict) -> str | None:
    name = output_obj.get("name")
    model = output_obj.get("model")
    if model and name:
        return f"{model} [{name}]"
    return model or name

def build_desktop_windows(desktop_names: list[str], windows: list[dict], active_name: str | None) -> list[dict]:
    results = []
    for dname in desktop_names:
        wins = []
        for w in windows:
            if w.get("onAllDesktops"):
                wins.append(w)
                continue
            if dname in (w.get("desktops") or []):
                wins.append(w)
        seen_ids = set()
        unique = []
        for w in wins:
            win_id = w.get("windowId")
            if not win_id or win_id in seen_ids:
                continue
            seen_ids.add(win_id)
            unique.append(w)
        wins = unique
        wins.sort(key=lambda w: (w.get("pid") is None, w.get("pid") or 0, w.get("windowId") or ""))
        results.append({
            "desktop_name": dname,
            "desktop_is_active": (active_name == dname) if active_name else False,
            "windows": [
                {
                    "id": w.get("windowId"),
                    "title": w.get("appName"),
                    "pid": w.get("pid"),
                    "caption": w.get("caption"),
                    "on_all_desktops": w.get("onAllDesktops"),
                    "minimized": w.get("minimized"),
                    "maximized": w.get("maximized"),
                    "fullScreen": w.get("fullScreen"),
                    "appExec": w.get("appExec"),
                    "active": w.get("active"),
                }
                for w in wins
            ],
        })
    return results

def collect_desktop_names(meta: dict, windows: list[dict]) -> list[str]:
    desktops = meta.get("desktops") or []
    names = [d.get("name") for d in desktops if d.get("name") is not None]
    if names:
        return names

    seen = set()
    for w in windows:
        for name in w.get("desktops") or []:
            if name == "ALL":
                continue
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names

def collect_outputs(meta: dict, windows: list[dict]) -> list[dict]:
    outputs = meta.get("outputs") or []
    if outputs:
        return outputs

    by_name = {}
    for w in windows:
        out = w.get("output") or {}
        name = out.get("name")
        if not name:
            continue
        if name not in by_name:
            by_name[name] = out
    return list(by_name.values())

def sort_outputs_by_geometry(outputs: list[dict]) -> list[dict]:
    def key_fn(out: dict) -> tuple[int, int]:
        geom = out.get("geometry") or {}
        x = geom.get("x")
        y = geom.get("y")
        return (y if isinstance(y, int) else 0, x if isinstance(x, int) else 0)
    return sorted(outputs, key=key_fn)

def build_monitors(meta: dict, windows: list[dict]) -> list[dict]:
    outputs = sort_outputs_by_geometry(collect_outputs(meta, windows))
    desktop_names = collect_desktop_names(meta, windows)
    active_name = meta.get("activeDesktopName")
    monitors = []

    for idx, out in enumerate(outputs, start=1):
        out_name = out.get("name")
        out_geom = out.get("geometry") or {}
        wins = [w for w in windows if (w.get("output") or {}).get("name") == out_name]
        monitors.append({
            "monitor_id": idx,
            "monitor_name": format_monitor_name(out),
            "monitor_x": out_geom.get("x"),
            "monitor_y": out_geom.get("y"),
            "monitor_width": out_geom.get("width"),
            "monitor_height": out_geom.get("height"),
            "reserved_bottom": 48,
            "on_all_desktops": all(w.get("onAllDesktops") for w in wins),
            "desktops": build_desktop_windows(desktop_names, wins, active_name),
        })

    return monitors

def build_payload(service: str, meta: dict, windows: list[dict]) -> dict:
    return {
        "service": service,
        "timestamp": round(time.time(), 2),
        "monitors": build_monitors(meta, windows),
    }

def get_state_snapshot(pid: int | None, services: list[str]) -> dict:
    since = datetime.now(timezone.utc).isoformat(timespec="seconds")
    service, meta, windows = collect_windows(pid, services, since)
    return build_payload(service, meta, windows)

async def send_ack(websocket, payload: dict, debug: bool) -> None:
    ack = {"type": "ack", "payload": payload}
    await websocket.send(json.dumps(ack, ensure_ascii=False))
    if debug:
        print(f"ack sent: {payload.get('command')}", flush=True)

def main():
    ap = argparse.ArgumentParser(
        description="KWin: window monitor/desktop state in JSON format (per sample.json)."
    )
    ap.add_argument("--pid", type=int, default=None, help="Filter by PID only (optional).")
    ap.add_argument("--service", default="auto",
                    help="KWin user service. 'auto' (default), vagy pl. plasma-kwin_wayland.service")
    ap.add_argument("--maximize", metavar="WINID", help="Maximize window by internalId/windowId.")
    ap.add_argument("--minimize", metavar="WINID", help="Minimize window by internalId/windowId.")
    ap.add_argument("--restore", metavar="WINID", help="Restore window (min/max off) by internalId/windowId.")
    ap.add_argument("--fullscreen", metavar="WINID", help="Set window fullscreen by internalId/windowId.")
    ap.add_argument("--fullscreen-exit", metavar="WINID", help="Exit fullscreen by internalId/windowId.")
    ap.add_argument("--pin-toggle", metavar="WINID", help="Toggle pin on all desktops by internalId/windowId.")
    ap.add_argument("--close", metavar="WINID", help="Close window by internalId/windowId.")
    ap.add_argument("--active", metavar="WINID", help="Activate window by internalId/windowId.")
    ap.add_argument("--move-desktop", nargs=2, metavar=("WINID", "DESKTOP"),
                    help="Move window to the given virtual desktop (number).")
    ap.add_argument("--move-monitor", nargs=2, metavar=("WINID", "MONITOR"),
                    help="Move window to the given monitor (1-based index or output name, e.g. DP-2).")
    ap.add_argument("--pretty", action="store_true", help="Pretty (indented) JSON output.")
    ap.add_argument("--ws", action="store_true", help="Start WebSocket server.")
    ap.add_argument("--host", default="0.0.0.0", help="WebSocket host (default: 0.0.0.0).")
    ap.add_argument("--port", type=int, default=8765, help="WebSocket port (default: 8765).")
    ap.add_argument("--interval", type=float, default=1.0, help="Update interval in seconds (default: 1.0).")
    ap.add_argument("--debug", action="store_true", help="Verbose WebSocket logs.")
    args = ap.parse_args()

    action = None
    winid = None
    target_desktop = None
    target_monitor = None
    for name in ("maximize", "minimize", "restore", "fullscreen", "fullscreen_exit", "pin_toggle", "close", "active"):
        val = getattr(args, name)
        if val:
            if action:
                ap.error("Only one action can be used at a time: --maximize/--minimize/--restore/--fullscreen/--fullscreen-exit/--pin-toggle/--close/--active")
            if name == "active":
                action = "activate"
            elif name == "fullscreen_exit":
                action = "fullscreen-exit"
            elif name == "pin_toggle":
                action = "pin-toggle"
            else:
                action = name
            winid = val
    if args.move_desktop:
        if action:
            ap.error("Only one action can be used at a time: --maximize/--minimize/--restore/--fullscreen/--fullscreen-exit/--pin-toggle/--close/--active/--move-desktop/--move-monitor")
        action = "move-desktop"
        winid = args.move_desktop[0]
        target_desktop = args.move_desktop[1]
    if args.move_monitor:
        if action:
            ap.error("Only one action can be used at a time: --maximize/--minimize/--restore/--fullscreen/--fullscreen-exit/--pin-toggle/--close/--active/--move-desktop/--move-monitor")
        action = "move-monitor"
        winid = args.move_monitor[0]
        target_monitor = args.move_monitor[1]
    if action and args.pid is not None:
        ap.error("When using actions, provide window id (internalId/windowId), not pid.")

    services = resolve_services(args.service)

    def build_state() -> dict:
        return get_state_snapshot(args.pid, services)

    def log_debug(message: str) -> None:
        if args.debug:
            print(message, flush=True)

    def parse_command_message(message: str) -> dict | None:
        try:
            obj = json.loads(message)
        except json.JSONDecodeError:
            return None
        payload = obj.get("payload") or {}
        is_command = obj.get("type") == "command"
        if not is_command and payload.get("name") and (payload.get("windowId") or payload.get("id")):
            is_command = True
        if not is_command:
            return None
        return {
            "name": payload.get("name"),
            "window_id": payload.get("windowId") or payload.get("id"),
            "desktop_index": payload.get("desktopIndex"),
            "target_monitor": payload.get("targetMonitor"),
            "target_desktop": payload.get("targetDesktop"),
            "exec_cmd": payload.get("exec"),
            "key": payload.get("key"),
            "raw": obj,
        }

    async def push_state(websocket) -> None:
        try:
            payload_now = build_state()
            await websocket.send(json.dumps({"type": "state", "payload": payload_now}, ensure_ascii=False))
            log_debug("state pushed after command")
        except Exception as exc:
            print(f"state push error: {exc}", flush=True)

    async def handle_command(cmd: dict, websocket) -> None:
        name = cmd.get("name")
        window_id = cmd.get("window_id")
        desktop_index = cmd.get("desktop_index")
        target_monitor = cmd.get("target_monitor")
        target_desktop = cmd.get("target_desktop")
        exec_cmd = cmd.get("exec_cmd")
        key = cmd.get("key")

        if name == "CloseEvent" and window_id:
            command = f"kwin close {window_id}"
            run_action(None, window_id, "close", None, None)
            await send_ack(websocket, {"name": name, "windowId": window_id, "command": command}, args.debug)
            await push_state(websocket)
            return
        if name == "MinimizeEvent" and window_id:
            command = f"kwin minimize {window_id}"
            run_action(None, window_id, "activate", None, None)
            run_action(None, window_id, "minimize", None, None)
            await send_ack(websocket, {"name": name, "windowId": window_id, "command": command}, args.debug)
            await push_state(websocket)
            return
        if name == "MaximizeEvent" and window_id:
            command = f"kwin maximize {window_id}"
            run_action(None, window_id, "activate", None, None)
            run_action(None, window_id, "maximize", None, None)
            await send_ack(websocket, {"name": name, "windowId": window_id, "command": command}, args.debug)
            await push_state(websocket)
            return
        if name == "RestoreEvent" and window_id:
            command = f"kwin restore {window_id}"
            run_action(None, window_id, "restore", None, None)
            await send_ack(websocket, {"name": name, "windowId": window_id, "command": command}, args.debug)
            await push_state(websocket)
            return
        if name == "FullscreenEvent" and window_id:
            command = f"kwin fullscreen {window_id}"
            run_action(None, window_id, "activate", None, None)
            run_action(None, window_id, "fullscreen", None, None)
            await send_ack(websocket, {"name": name, "windowId": window_id, "command": command}, args.debug)
            await push_state(websocket)
            return
        if name == "FullscreenExitEvent" and window_id:
            command = f"kwin fullscreen-exit {window_id}"
            run_action(None, window_id, "activate", None, None)
            run_action(None, window_id, "fullscreen-exit", None, None)
            await send_ack(websocket, {"name": name, "windowId": window_id, "command": command}, args.debug)
            await push_state(websocket)
            return
        if name == "PinToggleEvent" and window_id:
            command = f"kwin pin-toggle {window_id}"
            run_action(None, window_id, "pin-toggle", None, None)
            await send_ack(websocket, {"name": name, "windowId": window_id, "command": command}, args.debug)
            await push_state(websocket)
            return
        if name == "LaunchApp" and exec_cmd:
            command = f"exec {exec_cmd}"
            if launch_exec_command(exec_cmd):
                await send_ack(websocket, {"name": name, "command": command}, args.debug)
                await push_state(websocket)
            return
        if name == "KeyEvent" and key:
            command = f"key {key}"
            if window_id:
                run_action(None, window_id, "activate", None, None)
                time.sleep(0.1)
            if send_keypress(key):
                await send_ack(websocket, {"name": name, "windowId": window_id, "command": command}, args.debug)
                await push_state(websocket)
            else:
                await send_ack(
                    websocket,
                    {"name": name, "windowId": window_id, "command": f"error: key {key} failed"},
                    args.debug
                )
            return

        if name == "ActivateWindow" and window_id:
            command = f"kwin activate {window_id}"
            run_action(None, window_id, "activate", None, None)
            _, _, windows = collect_windows(
                args.pid,
                services,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            switch_to_screen_for_window(windows, window_id)
            invoke_kwin_shortcut("Activate Window Demanding Attention")
            run_action(None, window_id, "clear-attention", None, None)
            invoke_kwin_shortcut("Window Raise")
            run_action(None, window_id, "activate", None, None)
            await send_ack(websocket, {"name": name, "windowId": window_id, "command": command}, args.debug)
            await push_state(websocket)
            return

        if name == "SwitchDesktop" and desktop_index:
            command = f"kwin switch-desktop {desktop_index}"
            run_action(None, None, "switch-desktop", str(desktop_index), None)
            await send_ack(
                websocket,
                {"name": name, "command": command, "desktopIndex": desktop_index},
                args.debug,
            )
            await push_state(websocket)
            return

        if name == "MoveWindow" and window_id and target_desktop:
            commands = []
            payload_state = build_state()
            should_pin = is_monitor_all_pinned(payload_state, target_monitor)
            window_pinned = find_window_pinned(payload_state, window_id)
            if target_monitor:
                current_monitor = find_window_monitor(payload_state, window_id)
                if current_monitor is None or int(current_monitor) != int(target_monitor):
                    commands.append(f"kwin activate {window_id}")
                    run_action(None, window_id, "activate", None, None)
                    time.sleep(0.2)
                    used_shortcut = False
                    if str(target_monitor).isdigit():
                        n = int(target_monitor)
                        tried = []
                        if n >= 1:
                            tried.append(f"Window to Screen {n - 1}")
                        tried.append(f"Window to Screen {n}")
                        for shortcut_name in tried:
                            if invoke_kwin_shortcut(shortcut_name):
                                commands.append(f"kwin shortcut {shortcut_name}")
                                used_shortcut = True
                                break
                    if not used_shortcut:
                        commands.append(f"kwin move-monitor {window_id} {target_monitor}")
                        run_action(None, window_id, "move-monitor", None, str(target_monitor))
            commands.append(f"kwin move-desktop {window_id} {target_desktop}")
            run_action(None, window_id, "move-desktop", str(target_desktop), None)
            if should_pin and window_pinned is False:
                commands.append(f"kwin pin-toggle {window_id}")
                run_action(None, window_id, "pin-toggle", None, None)
            commands.append(f"kwin activate {window_id}")
            run_action(None, window_id, "activate", None, None)
            payload_state = build_state()
            if find_window_fullscreen(payload_state, window_id):
                commands.append("kwin fullscreen")
                run_action(None, window_id, "fullscreen", None, None)
            await send_ack(
                websocket,
                {"name": name, "windowId": window_id, "command": " ; ".join(commands)},
                args.debug,
            )
            await push_state(websocket)
            return

        log_debug(f"unknown command: {cmd}")

    async def ws_handler(websocket):
        last_payload_key = None
        peer = getattr(websocket, "remote_address", None)
        print(f"client connected: {peer}", flush=True)

        async def receiver():
            try:
                async for message in websocket:
                    log_debug(f"ws recv: {message}")
                    cmd = parse_command_message(message)
                    if not cmd:
                        continue
                    log_debug("command: " + json.dumps(cmd.get("raw"), ensure_ascii=False))
                    await handle_command(cmd, websocket)
            except Exception as exc:
                if args.debug:
                    print(f"receiver error: {exc}", flush=True)

        recv_task = asyncio.create_task(receiver())
        try:
            while True:
                payload = build_state()
                payload_key = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if payload_key != last_payload_key:
                    msg = json.dumps({"type": "state", "payload": payload}, ensure_ascii=False)
                    try:
                        await websocket.send(msg)
                    except Exception:
                        break
                    last_payload_key = payload_key
                await asyncio.sleep(args.interval)
        finally:
            recv_task.cancel()
            print(f"client disconnected: {peer}", flush=True)

    async def run_ws():
        try:
            import websockets
        except Exception as exc:  # pragma: no cover - runtime dependency check
            raise RuntimeError("Missing 'websockets' package. Install: pip install websockets") from exc
        async with websockets.serve(ws_handler, args.host, args.port):
            print(f"WebSocket: ws://{args.host}:{args.port}")
            print("Press Ctrl+C to exit.")
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

    if args.ws and action:
        ap.error("Actions are not allowed in WS mode.")
    if args.ws:
        try:
            asyncio.run(run_ws())
        except KeyboardInterrupt:
            print("Shutting down.")
    else:
        if action:
            if action == "move-monitor":
                run_action(args.pid, winid, "activate", None, None)
                used_shortcut = False
                if target_monitor and target_monitor.isdigit():
                    n = int(target_monitor)
                    tried = []
                    if n >= 1:
                        tried.append(f"Window to Screen {n - 1}")
                    tried.append(f"Window to Screen {n}")
                    for shortcut_name in tried:
                        if invoke_kwin_shortcut(shortcut_name):
                            used_shortcut = True
                            break
                if not used_shortcut:
                    run_action(args.pid, winid, action, None, target_monitor)
            else:
                run_action(args.pid, winid, action, target_desktop, target_monitor)
        payload = build_state()
        if args.pretty:
            print(json.dumps(payload, ensure_ascii=False, indent=4))
        else:
            print(json.dumps(payload, ensure_ascii=False))

if __name__ == "__main__":
    main()
