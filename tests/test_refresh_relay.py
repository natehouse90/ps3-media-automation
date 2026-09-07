import os
import importlib.util
from pathlib import Path


def main() -> int:
    os.environ.update({"PS3_IP": "ps3.local", "PS3_STATE_URL": "http://ps3.local/state", "PS3_IDLE_MARKER": "XMB_IDLE", "MANAGEMENT_ALLOW_IP": "192.168.1.20", "REFRESH_RELAY_TOKEN": "x" * 32})
    spec = importlib.util.spec_from_file_location("ps3_refresh_relay", Path(__file__).parents[1] / "scripts" / "ps3-refresh-relay.py")
    ps3_refresh_relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ps3_refresh_relay)
    ps3_refresh_relay.fetch = lambda _: (200, "XMB_IDLE")
    assert ps3_refresh_relay.is_idle()
    ps3_refresh_relay.fetch = lambda _: (200, "GAME_RUNNING")
    assert not ps3_refresh_relay.is_idle()
    status, result = ps3_refresh_relay.refresh()
    assert status == 202 and result["action"] == "xmb-refresh-pending"
    print("refresh relay tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
