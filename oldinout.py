#!/usr/bin/env python3
"""herdr-theoldinout: vim's jump list for herdr, across every machine in the window. prefix+o goes back to where you
were, prefix+i forward again.

  oldinout.py back | forward   the plugin's actions: ask the daemon (they run on the machine the window shows)
  oldinout.py daemon           run on the machine you sit at: keeps the history and carries out back/forward
  oldinout.py history          print the daemon's history (the newest last, > at the current place)
  oldinout.py pick             the plugin's action: open the history list in a popup (prefix+shift+i)
  oldinout.py ui               the list itself: newest first, Enter jumps to the entry

How: the daemon keeps one ssh pipe per machine in `herdr machine list` (plus this one). At the far end a small helper
streams that server's `pane.focused` events and its back/forward requests, and focuses panes when told to. A jump is
any change of the focused pane, or of the machine the herdr window shows. Going to another machine drives the window's
Goto picker (its Goto keys from herdr's config, /, its label, Enter), as herdr-queues does. Standard library only.
"""
import glob, json, os, re, socket, subprocess, sys, threading, time
try:
    import curses
except ImportError:  # Windows Python ships none: the list needs `py -m pip install windows-curses`
    curses = None

WIN = os.name == "nt"  # native Windows: herdr's socket is a named pipe; no fcntl, tail or getuid

# Windows before Python 3.15 reads files and subprocess output as cp1252: run again in UTF-8 mode (a child: herdr waits)
if WIN and not sys.flags.utf8_mode and __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-X", "utf8", *sys.argv]))

if WIN:  # no console window flashing up for each child of a windowless (pythonw) desk process; none needs one
    _popen = subprocess.Popen.__init__
    subprocess.Popen.__init__ = lambda self, *a, creationflags=0, **k: _popen(
        self, *a, creationflags=creationflags | subprocess.CREATE_NO_WINDOW, **k)

PLUGIN = "benjmhart.oldinout"
STATE_ROOT = (os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")) if os.name != "nt" else \
    os.environ["LOCALAPPDATA"]  # where herdr puts plugin state on Windows (HERDR_PLUGIN_STATE_DIR, herdr 0.9.5)
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.join(STATE_ROOT, "herdr/plugins", PLUGIN)
REQUESTS = os.path.join(STATE_DIR, "requests")
HISTORY = os.path.join(STATE_DIR, "history.json")  # the daemon's own; copies for the list: history.<desk>.json
DESK = socket.gethostname()
HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
PANE = re.compile(r"^[\w:.-]{1,64}$")
LIMIT = 200
QUIET = 2.0  # seconds after our own jump in which focus changes are its side effects, not yours

# Runs on each machine (python3 -c): one line of JSON per focus change or request; "focus <pane>" on stdin focuses.
AGENT = r'''
import json, os, socket, subprocess, sys, threading
P = os.environ.get("HERDR_SOCKET_PATH") or (os.path.join(os.environ["APPDATA"], "herdr", "herdr.sock") if os.name == "nt"
                                             else os.path.expanduser("~/.config/herdr/herdr.sock"))
if os.name == "nt" and not P.startswith("\\\\.\\pipe\\"):  # a path-shaped name in the pipe namespace
    P = "\\\\.\\pipe\\" + P
R = os.path.join(os.environ["LOCALAPPDATA"] if os.name == "nt" else os.path.expanduser("~/.local/state"),
                 "herdr", "plugins", "benjmhart.oldinout", "requests")
D = os.path.dirname(R)
NAME = lambda d: d.replace(".", "").replace("-", "").replace("_", "").isalnum() and len(d) < 64
lock = threading.Lock()
def out(**k):
    with lock:
        sys.stdout.write(json.dumps(k) + "\n"); sys.stdout.flush()
def conn():
    """herdr's socket: a Unix socket, or on Windows a named pipe. A file-like object for lines both ways."""
    if os.name == "nt":
        return open(P, "r+b", buffering=0)
    s = socket.socket(socket.AF_UNIX); s.connect(P)
    return s.makefile("rwb", buffering=0)
def call(method, params):
    with conn() as c:
        c.write((json.dumps({"id": "oio", "method": method, "params": params}) + "\n").encode())
        return json.loads(c.readline())
def focused(pane):
    """The pane, with its workspace and title as they are now (the list shows them later)."""
    try:
        p = call("pane.get", {"pane_id": pane})["result"]["pane"]
        w = call("workspace.get", {"workspace_id": p["workspace_id"]})["result"]["workspace"]
        out(f=pane, ws=w.get("label") or p["workspace_id"], t=p.get("label") or p.get("terminal_title_stripped") or "")
    except Exception:
        out(f=pane)
def events():
    c = conn()
    c.write((json.dumps({"id": "oio", "method": "events.subscribe",
                         "params": {"subscriptions": [{"type": "pane.focused"}]}}) + "\n").encode())
    for ln in iter(c.readline, b""):
        d = json.loads(ln)
        if d.get("event") == "pane_focused":
            focused(d["data"]["pane_id"])
    os._exit(1)
os.makedirs(os.path.dirname(R), exist_ok=True); open(R, "a").close()
def tail(p):  # tail -n0 -F, which Windows lacks
    import time
    f, buf = open(p), ""
    f.seek(0, 2)
    while True:
        chunk = f.read()
        if chunk:
            buf += chunk
            while "\n" in buf:
                ln, buf = buf.split("\n", 1)
                yield ln
            continue
        try:
            if os.path.getsize(p) < f.tell():
                f.seek(0); buf = ""
        except OSError:
            pass
        time.sleep(0.2)
def requests():
    for ln in tail(R):
        a = ln.split()
        if a in (["back"], ["forward"]):
            out(r=a[0])
        elif len(a) == 3 and a[0] == "goto" and NAME(a[1]) and a[2].isdigit():
            out(r="goto", desk=a[1], i=int(a[2]))
for p in call("pane.list", {})["result"]["panes"]:
    if p.get("focused"):
        focused(p["pane_id"])
threading.Thread(target=events, daemon=True).start()
threading.Thread(target=requests, daemon=True).start()
for ln in sys.stdin:
    a = ln.split()
    if len(a) == 2 and a[0] == "focus":
        try: call("pane.focus", {"pane_id": a[1]})
        except Exception: pass
    elif len(a) >= 3 and a[0] == "hist" and NAME(a[1]):  # a desk's history, for this machine's popup to list
        try:
            h = os.path.join(D, "history." + a[1] + ".json")
            os.makedirs(D, exist_ok=True)
            with open(h + ".tmp", "w") as f: f.write(ln.split(" ", 2)[2])
            os.replace(h + ".tmp", h)
        except OSError: pass
os._exit(0)
'''


# --- the history: vim's jump list ---------------------------------------------------------------------------------

class History:
    def __init__(self, entries=(), at=None):
        self.entries = [tuple(e) for e in entries][-LIMIT:]
        self.at = len(self.entries) - 1 if at is None else min(at, len(self.entries) - 1)

    def record(self, e):
        """A jump to e: anything ahead of the current place is dropped (as in a browser), then e goes on the end."""
        if self.entries and self.entries[self.at] == e:
            return False
        del self.entries[self.at + 1:]
        self.entries.append(e)
        del self.entries[:-LIMIT]
        self.at = len(self.entries) - 1
        return True

    def goto(self, i):
        """Straight to entry i (the list's choice): the place moves there, nothing is dropped."""
        if isinstance(i, int) and 0 <= i < len(self.entries):
            self.at = i
            return self.entries[i]
        return None

    def step(self, d):
        """Back (d=-1) or forward (d=1): the entry to go to, or None at either end."""
        j = self.at + d
        if 0 <= j < len(self.entries):
            self.at = j
            return self.entries[j]
        return None


# --- herdr and the window -----------------------------------------------------------------------------------------

def machines():
    """{label: (hostname, ssh target)} of the enabled machines; "" is this server. The hostname is what that machine
    calls itself (its window title says it), asked over ssh at start; guessed from the target ("user@carbon.lan" ->
    "carbon") only for a machine that doesn't answer, which is wrong for an alias (WSL as "wsl" is "wsl-arch",
    station-maintenance fleet 37, 2026-11-17). ponytail: asked once per daemon start; restart it after a rename."""
    out, targets = {"": (socket.gethostname(), None)}, {}
    try:
        r = subprocess.run([HERDR, "machine", "list"], capture_output=True, text=True, timeout=10)
        for ln in r.stdout.splitlines():
            f = ln.split("\t")
            if len(f) >= 5 and f[4] == "enabled":
                targets[f[1]] = f[2]
    except (OSError, subprocess.TimeoutExpired):
        pass
    names = {}
    def ask(t):
        try:
            names[t] = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", t, "uname -n"],
                                      capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    ts = [threading.Thread(target=ask, args=(t,)) for t in set(targets.values())]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    for lab, t in targets.items():
        out[lab] = (names.get(t) or t.split("@")[-1].split(".")[0], t)
    return out


def goto_keys():
    """The keys that open Goto in this desk's herdr client (its config.toml), e.g. ["ctrl+b", "g"]; None when that
    can't be known. herdr's default is prefix+g (as herdr-queues reads it)."""
    try:
        import tomllib
        cfg = os.path.join(os.environ["APPDATA"] if WIN else os.environ.get("XDG_CONFIG_HOME") or
                           os.path.expanduser("~/.config"), "herdr", "config.toml")
        try:
            with open(cfg, "rb") as f:
                k = tomllib.load(f).get("keys", {})
        except FileNotFoundError:
            k = {}
        goto = k.get("goto", "prefix+g")
        goto = goto if isinstance(goto, list) else [goto]
        g = next(g for g in goto if g)
        return [k.get("prefix", "ctrl+b"), g.removeprefix("prefix+")] if g.startswith("prefix+") else [g]
    except (ImportError, ValueError, StopIteration, AttributeError, TypeError):  # tomllib: Python 3.11+
        return None


def x11(*args):
    env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY") or ":0")
    try:
        return subprocess.run(["xdotool", *args], capture_output=True, text=True, env=env, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


# Windows, natively or from WSL (powershell.exe through WSL's interop): user32 and SendKeys, as herdr-queues does
# (station-maintenance fleet 37, 2026-11-16).
def windows_desktop():
    if WIN:
        return True
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


WINPS = r'''
Add-Type -TypeDefinition @"
using System; using System.Text; using System.Runtime.InteropServices;
public class HQ {
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
}
"@
function Main($op, $arg) {
  if ($op -eq "active") { [long][HQ]::GetForegroundWindow() }
  elseif ($op -eq "title") { $sb = New-Object Text.StringBuilder 512; [void][HQ]::GetWindowText([IntPtr][long]$arg, $sb, 512); $sb.ToString() }
  elseif ($op -eq "activate") {
    [HQ]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [HQ]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
    [void][HQ]::SetForegroundWindow([IntPtr][long]$arg) }
  elseif ($op -eq "keys") {  # one key per line, apart: herdr misses a prefix sent in one burst
    Add-Type -AssemblyName System.Windows.Forms
    foreach ($k in $arg -split "`n") { [System.Windows.Forms.SendKeys]::SendWait($k); Start-Sleep -Milliseconds 150 } }
  elseif ($op -eq "locked") { if (Get-Process LogonUI -ErrorAction SilentlyContinue) { "1" } else { "0" } }
}
# One operation per stdin line, "<op><tab><base64 UTF-8 arg>"; its output, then the end marker.
[Console]::OutputEncoding = [Text.Encoding]::UTF8
while ($null -ne ($line = [Console]::In.ReadLine())) {
  $op, $b = $line -split "`t", 2
  try { $r = Main $op ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b))) | Out-String } catch { $r = "" }
  [Console]::Out.Write($r); [Console]::Out.WriteLine("--hq-end--"); [Console]::Out.Flush()
}
'''


WINPS_PROC, WINPS_LOCK = [None], threading.Lock()


def winps(op, arg=""):
    """One WINPS operation in this process's resident PowerShell (the daemon polls the window every 0.4 s; starting
    one costs ~0.3 s): its output, "" on failure. A call stuck 30 s (90 s with the start) kills it; never re-sent."""
    import base64
    with WINPS_LOCK:
        p, wait = WINPS_PROC[0], 30
        try:
            if p is None or p.poll() is not None:
                wait = 90
                p = WINPS_PROC[0] = subprocess.Popen(
                    ["powershell" if WIN else "powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                     base64.b64encode(WINPS.encode("utf-16-le")).decode()],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8")
            p.stdin.write(op + "\t" + base64.b64encode(str(arg).encode()).decode() + "\n")
            p.stdin.flush()
            guard = threading.Timer(wait, p.kill)
            guard.start()
            out = []
            for ln in p.stdout:
                if ln.rstrip("\r\n") == "--hq-end--":
                    guard.cancel()
                    return "".join(out)
                out.append(ln)
            guard.cancel()
        except OSError:
            pass
        WINPS_PROC[0] = None
        return ""


SENDKEYS = {"slash": "/", "Return": "{ENTER}", "ctrl": "^", "shift": "+", "alt": "%"}


def sendkeys(text=None, names=()):
    """SendKeys syntax: literal text escaped, or key names ("ctrl+b", "slash", "Return")."""
    esc = lambda c: "{%s}" % c if c in "+^%~(){}[]" else c
    if text is not None:
        return "".join(esc(c) for c in text)
    return "".join("".join(SENDKEYS[m] for m in n.split("+")[:-1]) + esc(SENDKEYS.get(n.split("+")[-1], n.split("+")[-1]))
                   for n in names)


# The window you are using, by desktop: X11 (xdotool) or Windows.
def active():
    return winps("active").strip() if windows_desktop() else x11("getactivewindow").strip()


def title(win):
    return winps("title", win).strip() if windows_desktop() else x11("getwindowname", win).strip()


def activate(win):
    winps("activate", win) if windows_desktop() else x11("windowactivate", "--sync", win)


def keys(*names):
    winps("keys", "\n".join(sendkeys(names=[n]) for n in names)) if windows_desktop() else x11("key", "--clearmodifiers", *names)


def typ(text):
    winps("keys", sendkeys(text)) if windows_desktop() else x11("type", "--delay", "8", text)


def shows(win, hosts):
    """The host a herdr window shows, from its title "[herdr: ]<hostname>: <workspace>"; None if not a herdr window."""
    h = title(win).removeprefix("herdr: ").split(": ", 1)[0].lower()  # Windows: the upper-case computer name
    return next((x for x in hosts if x.lower() == h), None)


def locked():
    if windows_desktop():
        return winps("locked").strip() == "1"
    try:
        return subprocess.run(["pgrep", "-x", "-u", str(os.getuid()), "xsecurelock|i3lock|slock|swaylock|xlock"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


def die_with_parent():
    try:
        import ctypes, signal
        ctypes.CDLL(None).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG: the ssh goes when the daemon does
    except (OSError, AttributeError):
        pass


# --- the daemon ---------------------------------------------------------------------------------------------------

class Daemon:
    def __init__(self):
        self.ms = machines()
        self.hosts = {h for h, _ in self.ms.values()}
        self.label = {h: lab for lab, (h, _) in self.ms.items()}
        self.focused, self.links, self.shown, self.quiet, self.meta = {}, {}, None, 0, {}
        self.lock = threading.Lock()
        try:
            with open(HISTORY) as f:
                d = json.load(f)
            self.hist = History(d["entries"], d["at"])
            self.meta = d.get("meta") or {}
        except (OSError, ValueError, KeyError, TypeError):
            self.hist = History()

    def save(self):
        keep = {f"{h} {p}" for h, p in self.hist.entries}
        self.meta = {k: v for k, v in self.meta.items() if k in keep}
        text = json.dumps({"entries": self.hist.entries, "at": self.hist.at, "meta": self.meta})
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(HISTORY + ".tmp", "w") as f:
            f.write(text)
        os.replace(HISTORY + ".tmp", HISTORY)
        for p in list(self.links.values()):  # a copy on every machine (this one too), for its list (prefix+shift+i)
            if p.stdin:
                try:
                    p.stdin.write(f"hist {DESK} {text}\n")
                    p.stdin.flush()
                except OSError:
                    pass

    def record(self, host, pane):
        if pane and time.time() >= self.quiet and self.hist.record((host, pane)):
            self.save()

    def link(self, label):
        """One machine's helper, forever: its focus changes and requests in, focus commands out."""
        host, target = self.ms[label]
        cmd = [sys.executable, "-c", AGENT] if not target else [
            "ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15", "-o", "ConnectTimeout=10", target,
            "PATH=$HOME/.local/bin:$PATH python3 -c " + "'" + AGENT.replace("'", "'\\''") + "'"]
        while True:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 text=True, **({} if WIN else {"preexec_fn": die_with_parent}))
            self.links[host] = p
            with self.lock:
                self.save()  # its copy of the history, now rather than at the next jump
            for ln in p.stdout:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                with self.lock:
                    if "f" in d and PANE.match(d["f"]):
                        self.focused[host] = d["f"]
                        if "ws" in d:
                            self.meta[f"{host} {d['f']}"] = {"ws": str(d["ws"])[:60], "t": str(d.get("t", ""))[:120]}
                        if self.shown == host:  # you moved, in the window you are using
                            self.record(host, d["f"])
                    elif d.get("r") in ("back", "forward") and self.shown == host and not locked():
                        self.go(self.hist.step(-1 if d["r"] == "back" else 1))
                        self.save()
                    elif d.get("r") == "goto" and d.get("desk") == DESK and self.shown == host and not locked():
                        self.go(self.hist.goto(d.get("i", -1)))
                        self.save()
            self.links.pop(host, None)
            p.wait()
            time.sleep(10)  # machine down or rebooting: try again

    def go(self, e):
        if not e:
            return
        host, pane = e
        self.quiet = time.time() + QUIET
        win = active()
        g = goto_keys()
        if self.shown != host and host in self.label and g:  # no Goto keys known: no blind keys either
            label = self.label[host]
            activate(win)
            keys(*g, "slash")
            typ(label or "local")
            keys("Return")
            for _ in range(30):
                time.sleep(0.1)
                if shows(win, {host}):
                    break
            self.shown = host
        p = self.links.get(host)
        if p and p.stdin:
            try:
                p.stdin.write(f"focus {pane}\n")
                p.stdin.flush()
                self.focused[host] = pane
            except OSError:
                pass

    def watch_window(self):
        """Which machine the window you are using shows; switching it is a jump too."""
        while True:
            win = active()
            h = shows(win, self.hosts) if win else None
            with self.lock:
                if h and h != self.shown:
                    self.shown = h
                    self.record(h, self.focused.get(h))
            time.sleep(0.4)

    def run(self):
        for label in self.ms:
            threading.Thread(target=self.link, args=(label,), daemon=True).start()
        self.watch_window()


def daemon():
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(os.path.join(STATE_DIR, "daemon.lock"), "w")
    try:  # one per user: two would both type into the window
        if WIN:
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("herdr-theoldinout: a daemon is already running")
    Daemon().run()


def request(line):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(REQUESTS, "a") as f:
        f.write(line + "\n")
    try:
        if os.path.getsize(REQUESTS) > 64 * 1024:
            open(REQUESTS, "w").close()
    except OSError:
        pass


def rows(d):
    """The list's lines, newest first: (index, marker, host, workspace, title)."""
    meta = d.get("meta") or {}
    out = []
    for i in range(len(d["entries"]) - 1, -1, -1):
        h, p = d["entries"][i]
        m = meta.get(f"{h} {p}") or {}
        out.append((i, ">" if i == d["at"] else " ", h, m.get("ws") or "?", m.get("t") or p))
    return out


DRACULA = {"host": (141, 5), "ws": (117, 6), "title": (188, 7), "help": (103, 8), "select": (231, 7), "selectbg": (239, 4)}


def ui(scr):
    curses.curs_set(0)
    curses.use_default_colors()
    C = {}
    if curses.has_colors():
        pick = lambda n: DRACULA[n][0 if curses.COLORS >= 256 else 1]
        for n, (name, bg) in enumerate([("host", -1), ("ws", -1), ("title", -1), ("help", -1), ("select", "selectbg")], 1):
            try:
                curses.init_pair(n, pick(name), -1 if bg == -1 else pick(bg))
                C[name] = curses.color_pair(n)
            except curses.error:
                pass
    # One copy per desk running a daemon: the newest is the desk you are using (it records your jumps).
    copies = sorted(glob.glob(os.path.join(STATE_DIR, "history.*.json")), key=os.path.getmtime)
    desk = os.path.basename(copies[-1])[8:-5] if copies else ""
    try:
        with open(copies[-1]) as f:
            r = rows(json.load(f))
    except (IndexError, OSError, ValueError, KeyError):
        r = []
    sel = next((n for n, e in enumerate(r) if e[1] == ">"), 0)
    top = 0
    def put(y, x, text, attr=0):
        h, w = scr.getmaxyx()
        if 0 <= y < h and x < w - 1:
            try:
                scr.addstr(y, x, text[:w - 1 - x], attr)
            except curses.error:
                pass
        return x + len(text)
    while True:
        h, w = scr.getmaxyx()
        n = max(1, h - 3)
        top = min(max(top, sel - n + 1), sel)
        scr.erase()
        put(0, 1, "Jump history: newest first, > is where you are", curses.A_BOLD)
        if not r:
            put(2, 1, "Empty: the daemon writes it here as you move (herdr-oldinout on the machine you sit at).")
        for y, (i, mark, host, ws, title) in enumerate(r[top:top + n], 2):
            on = top + y - 2 == sel
            base = C.get("select", curses.A_REVERSE) if on else 0
            if on:
                put(y, 0, " " * (w - 1), base)
            x = put(y, 1, f"{mark} ", base | curses.A_BOLD)
            x = put(y, x, f"{host:<12} ", base if on else C.get("host", 0))
            x = put(y, x, f"{ws[:24]:<24} ", base if on else C.get("ws", 0))
            put(y, x, title, base if on else C.get("title", 0))
        put(h - 1, 1, "j/k ↑/↓ move   g/G top/bottom   Enter jump   q/Esc close", C.get("help", curses.A_DIM))
        scr.refresh()
        c = scr.getch()
        if c in (ord("q"), 27):
            return
        if c in (ord("j"), curses.KEY_DOWN):
            sel = min(sel + 1, len(r) - 1)
        elif c in (ord("k"), curses.KEY_UP):
            sel = max(sel - 1, 0)
        elif c in (ord("g"), curses.KEY_HOME):
            sel = 0
        elif c in (ord("G"), curses.KEY_END):
            sel = max(len(r) - 1, 0)
        elif c == curses.KEY_NPAGE:
            sel = min(sel + n, len(r) - 1)
        elif c == curses.KEY_PPAGE:
            sel = max(sel - n, 0)
        elif c in (10, 13, curses.KEY_ENTER) and r:
            request(f"goto {desk} {r[sel][0]}")
            return


def pipe(p):
    """herdr's socket on Windows: HERDR_SOCKET_PATH is a path-shaped name in the pipe namespace (herdr 0.9.5)."""
    return p if p.startswith("\\\\.\\pipe\\") else "\\\\.\\pipe\\" + p


def main(cmd):
    if cmd in ("back", "forward"):
        request(cmd)
    elif cmd == "ui":
        os.environ.setdefault("ESCDELAY", "25")
        if curses is None:
            input("herdr-theoldinout: the list needs curses. On Windows: py -m pip install windows-curses\n(Enter closes) ")
            return
        curses.wrapper(ui)
    elif cmd == "pick":
        line = (json.dumps({"id": "oldinout", "method": "plugin.pane.open", "params": {
            "plugin_id": os.environ["HERDR_PLUGIN_ID"], "entrypoint": "ui", "focus": True,
            "width": "90%", "height": "80%"}}) + "\n").encode()
        if WIN:  # a named pipe there
            with open(pipe(os.environ["HERDR_SOCKET_PATH"]), "r+b", buffering=0) as f:
                f.write(line)
                resp = json.loads(f.readline())
        else:
            with socket.socket(socket.AF_UNIX) as sk:
                sk.connect(os.environ["HERDR_SOCKET_PATH"])
                sk.sendall(line)
                resp = json.loads(sk.makefile().readline())
        if "error" in resp:
            sys.exit(f"herdr-theoldinout: plugin.pane.open: {resp['error']}")
    elif cmd == "daemon":
        daemon()
    elif cmd == "history":
        with open(HISTORY) as f:
            d = json.load(f)
        for i, (h, p) in enumerate(d["entries"]):
            print((">" if i == d["at"] else " "), h, p)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
