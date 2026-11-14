# herdr-theoldinout

A [herdr](https://herdr.dev) plugin: vim's jump list (`ctrl+o` / `ctrl+i`) for herdr, **across every machine your
herdr window reaches** (`herdr machine list`). `prefix+o` takes you back to where you were, `prefix+i` forward again.

A jump is any change of the focused pane, or of the machine the window shows. Going back to another machine switches
the window there and focuses the pane. A new jump after going back drops what was ahead, as a browser does. The
history (200 places) survives a restart of the daemon.

## Install

```sh
herdr plugin install Benjmhart/herdr-theoldinout
```

In `~/.config/herdr/config.toml` (herdr's own `prefix+o` opens the notification target; move it):

```toml
[keys]
open_notification_target = "prefix+shift+o"

[[keys.command]]
key = "prefix+o"
type = "plugin_action"
command = "benjmhart.oldinout.back"
description = "jump back"

[[keys.command]]
key = "prefix+i"
type = "plugin_action"
command = "benjmhart.oldinout.forward"
description = "jump forward"
```

Then start the daemon with your desktop session, on the machine you sit at (xmonad startup hook, i3/sway `exec`):

```sh
python3 "$(ls -dt ~/.config/herdr/plugins/github-installations/benjmhart.oldinout-*/*/checkout/oldinout.py | head -1)" daemon
```

Install the plugin on every machine you reach, too: the keys run on the machine the window is showing.

## How it works

The keys run on the server the window shows, which may have no way back to your screen, so they only write
`back`/`forward` to the plugin's state there. The daemon keeps one ssh pipe per machine (the same ssh herdr uses). At
the far end a small helper streams that server's `pane.focused` events and those requests, and focuses panes on
command (`pane.focus`). The daemon records a jump when the focused pane changes in the window you are using, or the
window switches machine. To go to another machine it drives the window's Goto picker (`prefix+a`, `/`, the machine's
label, Enter), as [herdr-queues](https://github.com/Benjmhart/herdr-queues) does.

Needs X11 and `xdotool`, the Goto picker on `prefix+a` with `prefix` = ctrl+b, herdr's window title in its default
shape (`{hostname}: {workspace}`, a `herdr: ` prefix is fine), and python3 on every machine. A request is ignored
while the screen is locked. `oldinout.py history` prints the list (`>` marks where you are).

## Test

`python3 test_oldinout.py` (no herdr needed).

MIT licence.
