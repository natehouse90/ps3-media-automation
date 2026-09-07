from pathlib import Path


def main() -> int:
    root = Path(__file__).parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (root / "docker-entrypoint.sh").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "gosu" in dockerfile and "docker-entrypoint.sh" in dockerfile
    assert "chown app:app" in entrypoint and "/srv/ps3-library/PS3ISO" in entrypoint
    assert "8787:8787" in compose and "app-state" in compose
    print("docker config tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
