"""python3 test_inandout.py: the jump list, without herdr."""
import inandout as O

h = O.History()
assert h.step(-1) is None and h.step(1) is None          # empty: nowhere to go
for e in [("devbox", "w1:p1"), ("laptop", "w2:p1"), ("laptop", "w2:p1"), ("buildbox", "w5:p2")]:
    h.record(e)
assert h.entries == [("devbox", "w1:p1"), ("laptop", "w2:p1"), ("buildbox", "w5:p2")]  # a repeat is not a jump
assert h.step(-1) == ("laptop", "w2:p1") and h.step(-1) == ("devbox", "w1:p1") and h.step(-1) is None
assert h.step(1) == ("laptop", "w2:p1")
h.record(("nas", "w1:p1"))                        # a new jump drops what was ahead
assert h.entries[-1] == ("nas", "w1:p1") and ("buildbox", "w5:p2") not in h.entries
assert h.step(1) is None and h.step(-1) == ("laptop", "w2:p1")
big = O.History()
for i in range(O.LIMIT + 50):
    big.record(("m", f"w{i}:p1"))
assert len(big.entries) == O.LIMIT and big.at == O.LIMIT - 1 and big.entries[0] == ("m", "w50:p1")
assert O.History([("a", "b"), ("c", "d")], 0).step(1) == ("c", "d")   # reloaded from the file, place kept
h = O.History([("a", "1"), ("b", "2"), ("c", "3")], 2)
assert h.goto(0) == ("a", "1") and h.at == 0 and len(h.entries) == 3   # the list's choice: nothing dropped
assert h.goto(9) is None and h.goto("1") is None and h.at == 0
r = O.rows({"entries": [["a", "1"], ["b", "2"]], "at": 0, "meta": {"b 2": {"ws": "dots", "t": "vim"}}})
assert r == [(1, " ", "b", "dots", "vim"), (0, ">", "a", "?", "1")]       # newest first; no names: the pane id
import os, sys, tempfile
if sys.version_info < (3, 11):  # no tomllib (macOS's own python3 is 3.9): no Goto keys, so no blind keys
    assert O.goto_keys() is None
else:
    with tempfile.TemporaryDirectory() as t:                                   # the Goto keys from herdr's config
        os.environ["APPDATA" if O.WIN else "XDG_CONFIG_HOME"] = t
        assert O.goto_keys() == ["ctrl+b", "g"]                                # none: herdr's default
        os.makedirs(f"{t}/herdr")
        open(f"{t}/herdr/config.toml", "w").write('[keys]\nprefix = "ctrl+a"\ngoto = ["prefix+a", "prefix+g"]\n')
        assert O.goto_keys() == ["ctrl+a", "a"]
        open(f"{t}/herdr/config.toml", "w").write('[keys\n')
        assert O.goto_keys() is None                                          # unreadable: no blind keys
print("ok")

# 0.4.0 rename: the old plugin's history moves to the new state dir once; an existing new history is kept
import tempfile
with tempfile.TemporaryDirectory() as _d:
    _old, _new = os.path.join(_d, "benjmhart.oldinout"), os.path.join(_d, "benjmhart.inandout")
    os.makedirs(_old)
    for _n in ("history.json", "history.desk.json", "daemon.lock"):
        open(os.path.join(_old, _n), "w").write("[1]")
    O.migrate(_new)
    assert sorted(os.listdir(_new)) == ["history.desk.json", "history.json"], os.listdir(_new)
    open(os.path.join(_old, "history.json"), "w").write("[2]")
    O.migrate(_new)
    assert open(os.path.join(_new, "history.json")).read() == "[1]"
print("ok migrate")

# the helper on another machine honours its XDG_STATE_HOME / XDG_CONFIG_HOME (no herdr there: it stops at the socket)
if os.name != "nt":
    import subprocess
    with tempfile.TemporaryDirectory() as _x:
        _env = {k: v for k, v in os.environ.items() if k != "HERDR_SOCKET_PATH"}
        _env.update(XDG_STATE_HOME=_x + "/s", XDG_CONFIG_HOME=_x + "/c")
        os.makedirs(_x + "/c/herdr"); open(_x + "/c/herdr/herdr.sock", "w").close()  # not a socket: refused, not missing
        _r = subprocess.run([sys.executable, "-c", O.AGENT], env=_env, capture_output=True, text=True, timeout=10)
        assert os.path.exists(_x + "/s/herdr/plugins/benjmhart.inandout/requests")
        assert "ConnectionRefusedError" in _r.stderr, _r.stderr
print("ok xdg")
