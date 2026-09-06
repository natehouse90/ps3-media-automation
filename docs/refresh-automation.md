# Refresh automation

`ps3-refresh.py --scan-only` requests a library scan. `--xmb` sends a protected
request to a management relay. The relay must inspect webMAN state and return:

- HTTP 200 with `action=net-refresh-xmb` after a scan and safe XMB reload.
- HTTP 202 with `action=xmb-refresh-pending` when a game is active.

The client records pending state and returns exit code 3 in the latter case.
`ps3ctl net-refresh` and `ps3ctl net-refresh-xmb` are convenience wrappers.
There is no reboot or automatic game launch. Keep the relay restricted to an
administrator-controlled management network.
