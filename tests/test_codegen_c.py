"""
C backend tests (AST -> IR -> C).
Run with: python test_codegen_c.py
"""

from __future__ import annotations

import sys

from compiler.parser import parse
from compiler.lowering import lower_program, LoweringError
from compiler.codegen_c import emit_c_program


_failures = 0


def check_ok(label: str, src: str, must_contain: list[str] | None = None):
    global _failures
    try:
        program = parse(src + "\n")
        ir_prog = lower_program(program)
        c_code = emit_c_program(ir_prog)
        for needle in must_contain or []:
            if needle not in c_code:
                print(f"  FAIL {label}: missing '{needle}' in generated C")
                _failures += 1
                return
        print(f"  OK  {label}")
    except Exception as e:
        print(f"  FAIL {label}: {e}")
        _failures += 1


def check_fail(label: str, src: str, must_contain: str):
    global _failures
    try:
        program = parse(src + "\n")
        lower_program(program)
        print(f"  FAIL {label}: expected lowering failure")
        _failures += 1
    except LoweringError as e:
        if must_contain in str(e):
            print(f"  OK  {label}")
        else:
            print(f"  FAIL {label}: unexpected error: {e}")
            _failures += 1
    except Exception as e:
        print(f"  FAIL {label}: unexpected exception type: {e}")
        _failures += 1


def section(name: str):
    print(f"\n── {name} {'─' * (50 - len(name))}")


def main():
    section("C codegen")
    check_ok(
        "simple function",
        """\
fn main!() -> int { 40 + 2 }
""",
        must_contain=["long main(", "return (40 + 2);"],
    )
    check_ok(
        "global binding",
        """\
const base = 10
fn main!() -> int { base + 1 }
""",
        must_contain=["static long base = 10;", "return (base + 1);"],
    )
    check_ok(
        "if statement lowering",
        """\
fn main!() -> int {
    let v = 0
    if 2 > 1 { v = 7 }
    v
}
""",
        must_contain=["if ((2 > 1)) {"],
    )

    section("Entity structs")
    check_ok(
        "entity becomes typedef struct",
        """\
entity Vec2 {
    x!: float
    y!: float
}
fn main!() -> Vec2 { Vec2__new(1.0, 2.0) }
""",
        must_contain=[
            "typedef struct {",
            "double x;",
            "double y;",
            "} Vec2;",
            "Vec2 Vec2__new(double x, double y)",
        ],
    )
    check_ok(
        "entity with immutable method",
        """\
entity Circle {
    radius!: float
    fn area!() -> float { radius * radius }
}
fn main!() -> float { Circle__new(2.0).radius }
""",
        must_contain=[
            "double Circle__area(Circle self)",
            "return (self.radius * self.radius);",
        ],
    )
    check_ok(
        "entity with mut method",
        """\
entity Counter {
    value!: int
    mut fn increment!() -> unit { self.value = self.value + 1 }
}
fn main!() -> int { 0 }
""",
        must_contain=[
            "void Counter__increment(Counter* self)",
            "self->value = (self->value + 1);",
        ],
    )
    check_ok(
        "constructor call",
        """\
entity Point {
    x!: float
    y!: float
}
fn origin!() -> Point { Point(0.0, 0.0) }
""",
        must_contain=["return (Point){.x = 0.0, .y = 0.0};"],
    )

    section("Field access")
    check_ok(
        "field access on param",
        """\
entity Point {
    x!: float
    y!: float
}
fn get_x!(p: Point) -> float { p.x }
""",
        must_contain=["return p.x;"],
    )
    check_ok(
        "chained field access",
        """\
entity Vec2 {
    x!: float
    y!: float
}
fn add_x!(a: Vec2, b: Vec2) -> float { a.x + b.x }
""",
        must_contain=["return (a.x + b.x);"],
    )

    section("For loops")
    check_ok(
        "for loop over int list",
        """\
fn sum_list!() -> int {
    let acc = 0
    for x in [1, 2, 3] { acc = acc + x }
    acc
}
""",
        must_contain=[
            "long _arr",
            "for (long _i_x = 0; _i_x <",
            "long x =",
            "acc = (acc + x);",
        ],
    )

    section("Match")
    check_ok(
        "match literal int",
        """\
fn describe!(x: int) -> int {
    match x {
        0 => 100,
        1 => 200,
        _ => 0
    }
}
""",
        must_contain=[
            "if ((_mv",
            "== 0))",
            "return 100;",
            "return 200;",
            "return 0;",
        ],
    )

    section("String interpolation")
    check_ok(
        "interpolation lowers to snprintf",
        'fn main!() -> int {\n'
        '    const name = 42\n'
        '    const _ = "hi {name}"\n'
        '    0\n'
        '}\n',
        must_contain=[
            "char _s1[512];",
            'snprintf(_s1, 512, "hi %ld", name);',
            "#include <stdio.h>",
        ],
    )

    if _failures:
        print(f"\n{_failures} failure(s).")
        raise SystemExit(1)
    print("\nAll codegen tests passed.")


if __name__ == "__main__":
    main()
