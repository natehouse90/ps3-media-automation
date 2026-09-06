#!/usr/bin/env python3
import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location("ps3_search", Path(__file__).parents[1] / "scripts" / "ps3-search.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main() -> int:
    same = [{"title": "Example.PS3.Release", "size": 123}, {"title": "example ps3 release", "size": 123}]
    assert len(module.dedupe(same)) == 1
    different_size = [{"title": "Example.PS3.Release", "size": 123}, {"title": "example ps3 release", "size": 124}]
    assert len(module.dedupe(different_size)) == 2
    assert module.INDEXERS[0][2] == 1080
    assert module.INDEXERS[1][2] is None
    print("ps3-search tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
