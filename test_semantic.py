"""
Basic semantic checks for module-level name resolution.
Run with: python test_semantic.py
"""

import sys

from parser import parse
from semantic import resolve_program, SemanticError


_failures = 0


def check_ok(label: str, src: str):
    global _failures
    try:
        program = parse(src + "\n")
        resolve_program(program)
        print(f"  OK  {label}")
    except Exception as e:
        print(f"  FAIL {label}: {e}")
        _failures += 1


def check_fail(label: str, src: str, must_contain: str):
    global _failures
    try:
        program = parse(src + "\n")
        resolve_program(program)
        print(f"  FAIL {label}: expected semantic error")
        _failures += 1
    except SemanticError as e:
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
    section("Semantic name resolution")
    check_ok("valid imports + decls", """\
import physics
import animation.{move as animate}

entity Position {
    x!: float
}
fn helper!() -> unit { }
const pi = 3.14
""")
    check_fail("duplicate function", """\
fn foo!() -> unit { }
fn foo!() -> unit { }
""", "Duplicate module name 'foo'")
    check_fail("import conflicts with function", """\
import physics
fn physics!() -> unit { }
""", "Duplicate module name 'physics'")
    check_fail("duplicate import alias", """\
import animation.{move as render, draw as render}
""", "Duplicate module name 'render'")
    check_fail("duplicate top-level binding", """\
const value = 1
let value = 2
""", "Duplicate module name 'value'")
    check_fail("entity conflicts with capability", """\
entity Shape {
    id!: int
}
capability Shape = { draw(Canvas) -> unit }
""", "Duplicate module name 'Shape'")
    check_fail("import item conflicts with declaration", """\
import physics.{move}
fn move!() -> unit { }
""", "Duplicate module name 'move'")

    if _failures:
        print(f"\n{_failures} failure(s).")
        sys.exit(1)
    print("\nAll semantic tests passed.")


if __name__ == "__main__":
    main()
