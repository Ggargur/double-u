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
                print(f"        Generated C:\n{c_code}")
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
fn main!() -> Vec2 { Vec2(1.0, 2.0) }
""",
        must_contain=[
            "typedef struct {",
            "double x;",
            "double y;",
            "} Vec2;",
            "Vec2 Vec2__new(_WArena* _a, double x, double y)",
        ],
    )
    check_ok(
        "entity with immutable method",
        """\
entity Circle {
    radius!: float
    fn area!() -> float { radius * radius }
}
fn main!() -> float { Circle(2.0).radius }
""",
        must_contain=[
            "double Circle__area(_WArena* _a, Circle self)",
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
            "void Counter__increment(_WArena* _a, Counter* self)",
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
    check_ok(
        "entity operator desugar passes arena",
        """\
entity Vec2 {
    x!: float
    y!: float
    fn add!(other: Vec2) -> Vec2 { Vec2(self.x + other.x, self.y + other.y) }
}
fn combine!(a: Vec2, b: Vec2) -> Vec2 { a + b }
""",
        must_contain=["return Vec2__add(_a, a, b);"],
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

    section("WStr + Arena")
    check_ok(
        "string interpolation uses WStr",
        'fn main!() -> int {\n'
        '    const name = 42\n'
        '    const _ = "hi {name}"\n'
        '    0\n'
        '}\n',
        must_contain=[
            '_wstr_from_snprintf(_a, "hi %ld", name)',
            "#include <stdio.h>",
            "WStr _ =",
        ],
    )
    check_ok(
        "string literal uses _wstr_from_lit",
        'fn greet!() -> string { "hello" }\n',
        must_contain=[
            '_wstr_from_lit("hello", 5)',
            "WStr greet(",
        ],
    )
    check_ok(
        "string concat uses _wstr_concat",
        """\
fn join!(a: string, b: string) -> string { a + b }
""",
        must_contain=[
            "_wstr_concat(_a, a, b)",
            "WStr join(",
        ],
    )
    check_ok(
        "string equality uses _wstr_eq",
        """\
fn same!(a: string, b: string) -> bool { a == b }
""",
        must_contain=["_wstr_eq(a, b)"],
    )
    check_ok(
        "string .length uses _wstr_len",
        """\
fn slen!(s: string) -> int { s.length }
""",
        must_contain=["_wstr_len(s)"],
    )
    check_ok(
        "string indexing uses _wstr_index",
        """\
fn char_at!(s: string, i: int) -> string { s[i] }
""",
        must_contain=["_wstr_from_char(_a, _wstr_index(s, i))"],
    )
    check_ok(
        "arena setup in main",
        """\
fn main!() -> int { 0 }
""",
        must_contain=[
            "_WArena* _a = _w_arena_new(4096);",
            "_w_arena_free(_a);",
        ],
    )
    check_ok(
        "arena param in non-main functions",
        """\
fn helper!(x: int) -> int { x + 1 }
""",
        must_contain=["long helper(_WArena* _a, long x)"],
    )
    check_ok(
        "WStr runtime is embedded",
        """\
fn main!() -> int { 0 }
""",
        must_contain=[
            "typedef struct {",
            "} WStr;",
            "_wstr_from_lit",
            "_wstr_concat",
            "_w_arena_new",
        ],
    )
    check_ok(
        "arena uses stable blocks",
        """\
fn main!() -> int { 0 }
""",
        must_contain=[
            "typedef struct _WArenaBlock {",
            "_WArenaBlock* head;",
            "_w_arena_block_new",
        ],
    )
    check_ok(
        "user fn wins over c wildcard extern",
        """\
import c.stdio
fn println!(s: string) -> unit { puts(s) }
fn main!() -> int {
    println("ok")
    0
}
""",
        must_contain=[
            "void println(_WArena* _a, WStr s)",
            "println(_a, _wstr_from_lit(\"ok\", 2));",
        ],
    )

    if _failures:
        print(f"\n{_failures} failure(s).")
        raise SystemExit(1)
    print("\nAll codegen tests passed.")


if __name__ == "__main__":
    main()
