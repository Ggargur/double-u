"""
CLI MVP tests.
Run with: python test_cli.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile


_failures = 0


def check_ok(label: str, cmd: list[str], must_contain: str | None = None):
    global _failures
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        print(f"  FAIL {label}: exit {proc.returncode}\n{out}")
        _failures += 1
        return
    if must_contain and must_contain not in out:
        print(f"  FAIL {label}: output missing '{must_contain}'\n{out}")
        _failures += 1
        return
    print(f"  OK  {label}")


def check_fail(label: str, cmd: list[str], must_contain: str):
    global _failures
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        print(f"  FAIL {label}: expected failure\n{out}")
        _failures += 1
        return
    if must_contain not in out:
        print(f"  FAIL {label}: output missing '{must_contain}'\n{out}")
        _failures += 1
        return
    print(f"  OK  {label}")


def section(name: str):
    print(f"\n── {name} {'─' * (50 - len(name))}")


def main():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "main.lang")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "fn main!() -> int {\n"
                "    40 + 2\n"
                "}\n"
            )

        py = sys.executable
        cli = os.path.join(os.path.dirname(__file__), "cli.py")

        section("CLI commands")
        check_ok("build", [py, cli, "build", path], "Build succeeded")
        check_ok("run", [py, cli, "run", path], "42")
        check_fail(
            "run missing entry",
            [py, cli, "run", path, "--entry", "missing"],
            "Entry function 'missing' not found",
        )

    if _failures:
        print(f"\n{_failures} failure(s).")
        raise SystemExit(1)
    print("\nAll CLI tests passed.")


if __name__ == "__main__":
    main()
