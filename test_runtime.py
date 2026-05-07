"""
Runtime MVP integration tests.
Run with: python test_runtime.py
"""

import sys

from parser import parse
from runtime import run_program, RuntimeError


_failures = 0


def check_ok(label: str, src: str, expected, entry: str = "main"):
    global _failures
    try:
        program = parse(src + "\n")
        result = run_program(program, entry=entry)
        if result != expected:
            print(f"  FAIL {label}: expected {expected!r}, got {result!r}")
            _failures += 1
            return
        print(f"  OK  {label}")
    except Exception as e:
        print(f"  FAIL {label}: {e}")
        _failures += 1


def check_fail(label: str, src: str, must_contain: str, entry: str = "main"):
    global _failures
    try:
        program = parse(src + "\n")
        run_program(program, entry=entry)
        print(f"  FAIL {label}: expected runtime error")
        _failures += 1
    except RuntimeError as e:
        msg = str(e)
        if must_contain in msg:
            print(f"  OK  {label}")
        else:
            print(f"  FAIL {label}: unexpected error message: {msg}")
            _failures += 1
    except Exception as e:
        print(f"  FAIL {label}: unexpected exception type: {e}")
        _failures += 1


def section(name: str):
    print(f"\n── {name} {'─' * (50 - len(name))}")


def main():
    section("Basic execution")
    check_ok("global binding + arithmetic", """\
const base = 10
fn main!() -> int { base + 5 }
""", 15)
    check_ok("if expression", """\
fn main!() -> int {
    ^if 2 > 1 { 7 } else { 0 }
}
""", 7)
    check_ok("for loop accumulation", """\
fn main!() -> int {
    let acc = 0
    for x in [1, 2, 3] { acc += x }
    acc
}
""", 6)
    check_ok("match literal", """\
fn main!() -> string {
    ^match 2 {
        1 => "one",
        2 => "two",
        _ => "other"
    }
}
""", "two")

    section("Exceptions")
    check_ok("try catch handles throw", """\
exception DivisionByZero
fn main!() -> int {
    let out = 0
    try { throw DivisionByZero }
    catch DivisionByZero { out = 42 }
    out
}
""", 42)
    check_fail("unhandled exception", """\
exception Boom
fn main!() -> int {
    throw Boom
}
""", "Unhandled exception: Boom")

    section("Functions and calls")
    check_ok("function call", """\
fn add!(a: int, b: int) -> int { a + b }
fn main!() -> int { add(2, 3) }
""", 5)
    check_fail("entry not found", """\
fn helper!() -> unit { }
""", "Entry function 'main' not found")

    if _failures:
        print(f"\n{_failures} failure(s).")
        sys.exit(1)
    print("\nAll runtime tests passed.")


if __name__ == "__main__":
    main()
