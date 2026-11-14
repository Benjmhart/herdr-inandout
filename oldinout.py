#!/usr/bin/env python3
"""herdr-theoldinout: vim's jump list for herdr, across every machine in the window. prefix+o goes back to where you
were, prefix+i forward again.

  oldinout.py back | forward   the plugin's actions: ask the daemon (they run on the machine the window shows)
  oldinout.py daemon           run on the machine you sit at: keeps the history and carries out back/forward
  oldinout.py history          print the daemon's history (the newest last, > at the current place)

How: the daemon keeps one ssh pipe per machine in `herdr machine list` (plus this one). At the far end a small helper
streams that server's `pane.focused` events and its back/forward requests, and focuses panes when told to. A jump is
any change of the focused pane, or of the machine the herdr window shows. Going to another machine drives the window's
Goto picker (prefix+a, /, its label, Enter), as herdr-queues does. Standard library only.
"""
import json, os, re, socket, subprocess, sys, threading, time

PLUGIN = "benjmhart.oldinout"
STATE_ROOT = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.join(STATE_ROOT, "herdr/plugins", PLUGIN)
REQUESTS = os.path.join(STATE_DIR, "requests")
HISTORY = os.path.join(STATE_DIR, "history.json")
HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
PANE = re.compile(r"^[\w:.-]{1,64}$")
LIMIT = 200
QUIET = 2.0  # seconds after our own jump in which focus changes are its side effects, not yours

# Runs on each machine (python3 -c): one line of JSON per focus change or request; "focus <pane>" on stdin focuses.
AGENT = r'''
import json, os, socket, subprocess, sys, threading
P = os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")
R = os.path.expanduser("~/.local/state/herdr/plugins/benjmhart.oldinout/requests")
lock = threading.Lock()
def out(**k):
    with lock:
        sys.stdout.write(json.dumps(k) + "\n"); sys.stdout.flush()
def call(method, params):
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(P); s.sendall((json.dumps({"id": "oio", "method": method, "params": params}) + "\n").encode())
        return json.loads(s.makefile().readline())
def events():
    s = socket.socket(socket.AF_UNIX); s.connect(P)
    s.sendall((json.dumps({"id": "oio", "method": "events.subscribe",
                           "params": {"subscriptions": [{"type": "pane.focused"}]}}) + "\n").encode())
    for ln in s.makefile():
        d = json.loads(ln)
        if d.get("event") == "pane_focused":
            out(f=d["data"]["pane_id"])
    os._exit(1)
os.makedirs(os.path.dirname(R), exist_ok=True); open(R, "a").close()
t = subprocess.Popen(["tail", "-n0", "-F", R], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
def requests():
    for ln in t.stdout:
        if ln.strip() in ("back", "forward"):
            out(r=ln.strip())
for p in call("pane.list", {})["result"]["panes"]:
    if p.get("focused"):
        out(f=p["pane_id"])
threading.Thread(target=events, daemon=True).start()
threading.Thread(target=requests, daemon=True).start()
for ln in sys.stdin:
    a = ln.split()
    if len(a) == 2 and a[0] == "focus":
        try: call("pane.focus", {"pane_id": a[1]})
        except Exception: pass
t.kill(); os._exit(0)
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

    def step(self, d):
        """Back (d=-1) or forward (d=1): the entry to go to, or None at either end."""
        j = self.at + d
        if 0 <= j < len(self.entries):
            self.at = j
            return self.entries[j]
        return None


# --- herdr and the window -----------------------------------------------------------------------------------------

def machines():
    """{label: (hostname, ssh target)} of the enabled machines; "" is this server."""
    out = {"": (socket.gethostname(), None)}
    try:
        r = subprocess.run([HERDR, "machine", "list"], capture_output=True, text=True, timeout=10)
        for ln in r.stdout.splitlines():
            f = ln.split("\t")
            if len(f) >= 5 and f[4] == "enabled":
                out[f[1]] = (f[2].split("@")[-1].split(".")[0], f[2])
    except (OSError, subprocess.TimeoutExpired):
        pass
    return out


def x11(*args):
    env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY") or ":0")
    try:
        return subprocess.run(["xdotool", *args], capture_output=True, text=True, env=env, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def shows(win, hosts):
    """The host a herdr window shows, from its title "[herdr: ]<hostname>: <workspace>"; None if not a herdr window."""
    h = x11("getwindowname", win).strip().removeprefix("herdr: ").split(": ", 1)[0]
    return h if h in hosts else None


def locked():
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
        self.focused, self.links, self.shown, self.quiet = {}, {}, None, 0
        self.lock = threading.Lock()
        try:
            with open(HISTORY) as f:
                d = json.load(f)
            self.hist = History(d["entries"], d["at"])
        except (OSError, ValueError, KeyError, TypeError):
            self.hist = History()

    def save(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(HISTORY + ".tmp", "w") as f:
            json.dump({"entries": self.hist.entries, "at": self.hist.at}, f)
        os.replace(HISTORY + ".tmp", HISTORY)

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
                                 text=True, preexec_fn=die_with_parent)
            self.links[host] = p
            for ln in p.stdout:
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                with self.lock:
                    if "f" in d and PANE.match(d["f"]):
                        self.focused[host] = d["f"]
                        if self.shown == host:  # you moved, in the window you are using
                            self.record(host, d["f"])
                    elif d.get("r") in ("back", "forward") and self.shown == host and not locked():
                        self.go(self.hist.step(-1 if d["r"] == "back" else 1))
                        self.save()
            self.links.pop(host, None)
            p.wait()
            time.sleep(10)  # machine down or rebooting: try again

    def go(self, e):
        if not e:
            return
        host, pane = e
        self.quiet = time.time() + QUIET
        win = x11("getactivewindow").strip()
        if self.shown != host and host in self.label:
            label = self.label[host]
            x11("windowactivate", "--sync", win)
            x11("key", "--clearmodifiers", "ctrl+b", "a", "slash")
            x11("type", "--delay", "8", label or "local")
            x11("key", "Return")
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
            win = x11("getactivewindow").strip()
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
    import fcntl
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(os.path.join(STATE_DIR, "daemon.lock"), "w")
    try:  # one per user: two would both type into the window
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("herdr-theoldinout: a daemon is already running")
    Daemon().run()


def main(cmd):
    if cmd in ("back", "forward"):
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(REQUESTS, "a") as f:
            f.write(cmd + "\n")
        try:
            if os.path.getsize(REQUESTS) > 64 * 1024:
                open(REQUESTS, "w").close()
        except OSError:
            pass
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
