import os
import subprocess
import sys


def routes_when(env: str) -> str:
    # Fresh Python process, because the routes are decided when app.main is imported
    result = subprocess.run(
        [sys.executable, "-c", "from app.main import app; print([r.path for r in app.routes])"],
        env=os.environ | {"ENV": env},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_dev_routes_do_not_exist_in_production():
    assert "/dev/seed" not in routes_when("production")


def test_dev_routes_do_not_exist_with_unknown_env():
    # Typo like ENV=prod must also hide them (allow-list, fail closed)
    assert "/dev/seed" not in routes_when("prod")


def test_dev_routes_exist_in_dev():
    assert "/dev/seed" in routes_when("dev")
