"""
Type checker v1 tests.
Run with: python test_type_checker.py
"""

import sys

from parser import parse
from type_checker import check_program, TypeCheckError


_failures = 0


def check_ok(label: str, src: str):
    global _failures
    try:
        program = parse(src + "\n")
        check_program(program)
        print(f"  OK  {label}")
    except Exception as e:
        print(f"  FAIL {label}: {e}")
        _failures += 1


def check_fail(label: str, src: str, must_contain: str):
    global _failures
    try:
        program = parse(src + "\n")
        check_program(program)
        print(f"  FAIL {label}: expected type error")
        _failures += 1
    except TypeCheckError as e:
        msg = str(e)
        if must_contain in msg:
            print(f"  OK  {label}")
        else:
            print(f"  FAIL {label}: unexpected message: {msg}")
            _failures += 1
    except Exception as e:
        print(f"  FAIL {label}: unexpected exception type: {e}")
        _failures += 1


def section(name: str):
    print(f"\n── {name} {'─' * (50 - len(name))}")


def main():
    section("Nominal type resolution")
    check_ok("known types", """\
entity Position {
    x!: float
    y!: float
}
fn magnitude!(p: Position) -> float { 0.0 }
""")
    check_fail("unknown field type", """\
entity Broken {
    value!: MissingType
}
""", "Unknown type 'MissingType'")
    check_fail("unknown extend target", """\
extend MissingType {
    fn decorate!() -> unit { }
}
""", "Unknown type 'MissingType'")

    section("Generics scope")
    check_ok("generic in scope", """\
fn id!<T>(value: T) -> T { value }
""")
    check_ok("entity generic in scope", """\
entity Box<T> {
    value!: T
    fn get!() -> T { value }
}
""")
    check_fail("generic out of scope", """\
fn bad!(value: T) -> unit { }
""", "Unknown type 'T'")
    check_fail("duplicate generic parameter", """\
fn dup!<T, T>(value: T) -> T { value }
""", "Duplicate generic parameter 'T'")
    check_fail("duplicate entity generic parameter", """\
entity Pair<T, T> {
    left!: T
}
""", "Duplicate generic parameter 'T'")
    check_fail("where unknown generic", """\
fn bounded!<T>(value: T) -> T
where
    U: int
{ value }
""", "Unknown generic 'U'")
    check_fail("unknown type in generic bound", """\
fn bounded!<T: MissingCap>(value: T) -> T { value }
""", "Unknown type 'MissingCap'")

    section("Nullability and wildcard")
    check_ok("wildcard in capability arg", """\
capability Movable = { move(_, _) -> unit }
""")
    check_ok("Self in entity method return", """\
entity Builder {
    fn clone!() -> Self { self }
}
""")
    check_fail("wildcard in regular annotation", """\
fn bad!(x: _) -> unit { }
""", "Wildcard type '_' is not allowed")
    check_fail("nullable wildcard", """\
fn bad!(x: _?) -> unit { }
""", "Wildcard nullable type '_?' is not allowed")
    check_fail("Self outside entity/capability", """\
fn bad!() -> Self { }
""", "'Self' is not allowed")
    check_fail("wildcard in generic arg", """\
fn bad!(x: Vec<_>) -> unit { }
""", "Wildcard type '_' is not allowed")

    if _failures:
        print(f"\n{_failures} failure(s).")
        sys.exit(1)
    print("\nAll type checker tests passed.")


if __name__ == "__main__":
    main()
