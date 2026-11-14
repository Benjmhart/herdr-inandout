"""python3 test_oldinout.py: the jump list, without herdr."""
import oldinout as O

h = O.History()
assert h.step(-1) is None and h.step(1) is None          # empty: nowhere to go
for e in [("micro", "w1:p1"), ("carbon", "w2:p1"), ("carbon", "w2:p1"), ("beast-arch", "w5:p2")]:
    h.record(e)
assert h.entries == [("micro", "w1:p1"), ("carbon", "w2:p1"), ("beast-arch", "w5:p2")]  # a repeat is not a jump
assert h.step(-1) == ("carbon", "w2:p1") and h.step(-1) == ("micro", "w1:p1") and h.step(-1) is None
assert h.step(1) == ("carbon", "w2:p1")
h.record(("media-center", "w1:p1"))                        # a new jump drops what was ahead
assert h.entries[-1] == ("media-center", "w1:p1") and ("beast-arch", "w5:p2") not in h.entries
assert h.step(1) is None and h.step(-1) == ("carbon", "w2:p1")
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
print("ok")
