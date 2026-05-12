"""
Type checker tests — covers all phases of the strong type system.
Run with: python -m tests.test_type_checker
"""

import sys

from compiler.parser import parse
from compiler.type_checker import check_program, TypeCheckError


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
    # ═══════════════════════════════════════════════════════════════════
    # Pass 1: TypeRef resolution (original v1 tests)
    # ═══════════════════════════════════════════════════════════════════

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

    # ═══════════════════════════════════════════════════════════════════
    # Pass 3: Body type checking
    # ═══════════════════════════════════════════════════════════════════

    section("Literal types")
    check_ok("int literal", """\
fn main!() -> int { 42 }
""")
    check_ok("float literal", """\
fn main!() -> float { 3.14 }
""")
    check_ok("bool literal", """\
fn main!() -> bool { true }
""")
    check_ok("string literal", """\
fn main!() -> string { "hello" }
""")

    section("No implicit numeric coercion")
    check_fail("int to float", """\
fn main!() -> float { 42 }
""", "expected return type float, body returns int")
    check_fail("float to int", """\
fn main!() -> int { 3.14 }
""", "expected return type int, body returns float")
    check_fail("int + float", """\
fn main!() -> float {
    const x = 1 + 2.0
    x
}
""", "both operands must be int")
    check_fail("float assigned int", """\
fn main!() -> unit {
    const x: float = 42
}
""", "declared type float, got int")

    section("Binding type inference")
    check_ok("infer int", """\
fn main!() -> int {
    const x = 10
    x
}
""")
    check_ok("infer float", """\
fn main!() -> float {
    const x = 10.0
    x
}
""")
    check_ok("infer bool", """\
fn main!() -> bool {
    const x = true
    x
}
""")
    check_ok("explicit annotation matches", """\
fn main!() -> int {
    const x: int = 42
    x
}
""")
    check_fail("annotation mismatch", """\
fn main!() -> unit {
    const x: string = 42
}
""", "declared type string, got int")

    section("Arithmetic operators")
    check_ok("int arithmetic", """\
fn main!() -> int { 1 + 2 * 3 - 4 / 2 }
""")
    check_ok("float arithmetic", """\
fn main!() -> float { 1.0 + 2.0 * 3.0 }
""")
    check_fail("mixed arithmetic", """\
fn main!() -> unit {
    const x = 1 + 2.0
}
""", "both operands must be int")
    check_ok("string concatenation", """\
fn main!() -> string { "hello" + " world" }
""")

    section("Comparison operators")
    check_ok("int comparison", """\
fn main!() -> bool { 1 < 2 }
""")
    check_ok("equality", """\
fn main!() -> bool { 1 == 1 }
""")
    check_fail("compare different types", """\
fn main!() -> bool { 1 < 2.0 }
""", "Cannot compare int with float")

    section("Boolean operators")
    check_ok("and/or", """\
fn main!() -> bool { true and false or true }
""")
    check_fail("non-bool and", """\
fn main!() -> bool { 1 and 2 }
""", "requires bool, got int")

    section("Unary operators")
    check_ok("negate int", """\
fn main!() -> int { -42 }
""")
    check_ok("not bool", """\
fn main!() -> bool { !true }
""")
    check_fail("negate string", """\
fn main!() -> unit {
    const x = -"hello"
}
""", "Unary '-' requires int or float, got string")
    check_fail("not int", """\
fn main!() -> unit {
    const x = !42
}
""", "Unary '!' requires bool, got int")

    section("Entity constructor type checking")
    check_ok("correct constructor", """\
entity Point {
    x!: float
    y!: float
}
fn main!() -> unit {
    const p = Point(1.0, 2.0)
}
""")
    check_fail("wrong arg count", """\
entity Point {
    x!: float
    y!: float
}
fn main!() -> unit {
    const p = Point(1.0)
}
""", "expects 2 arguments, got 1")
    check_fail("wrong arg type", """\
entity Point {
    x!: float
    y!: float
}
fn main!() -> unit {
    const p = Point(1, 2.0)
}
""", "expected float, got int")

    section("Field access")
    check_ok("access entity field", """\
entity Point {
    x!: float
    y!: float
}
fn main!() -> float {
    const p = Point(1.0, 2.0)
    p.x
}
""")
    check_fail("access nonexistent field", """\
entity Point {
    x!: float
    y!: float
}
fn main!() -> unit {
    const p = Point(1.0, 2.0)
    const z = p.z
}
""", "No field or method 'z' on Point")

    section("Method calls")
    check_ok("method call", """\
entity Vec2 {
    x!: float
    y!: float
    fn add!(other: Vec2) -> Vec2 { Vec2(self.x + other.x, self.y + other.y) }
}
fn main!() -> float {
    const a = Vec2(1.0, 2.0)
    const b = Vec2(3.0, 4.0)
    const c = a.add(b)
    c.x
}
""")
    check_fail("method wrong arg type", """\
entity Vec2 {
    x!: float
    y!: float
    fn scale!(factor: float) -> Vec2 { Vec2(self.x * factor, self.y * factor) }
}
fn main!() -> unit {
    const a = Vec2(1.0, 2.0)
    const b = a.scale(2)
}
""", "expected float, got int")

    section("Function calls")
    check_ok("correct function call", """\
fn add!(a: int, b: int) -> int { a + b }
fn main!() -> int { add(1, 2) }
""")
    check_fail("wrong arg count", """\
fn add!(a: int, b: int) -> int { a + b }
fn main!() -> int { add(1) }
""", "expects 2 arguments, got 1")
    check_fail("wrong arg type", """\
fn greet!(name: string) -> string { name }
fn main!() -> unit {
    const x = greet(42)
}
""", "expected string, got int")

    section("Entity operator desugaring")
    check_ok("operator + on entity", """\
entity Vec2 {
    x!: float
    y!: float
    fn add!(other: Vec2) -> Vec2 { Vec2(self.x + other.x, self.y + other.y) }
}
fn main!() -> float {
    const a = Vec2(1.0, 2.0)
    const b = Vec2(3.0, 4.0)
    const c = a + b
    c.x
}
""")

    section("If expression type checking")
    check_ok("if expression via early return", """\
fn main!() -> int {
    ^if true { 1 } else { 2 }
}
""")
    check_fail("if condition not bool", """\
fn main!() -> unit {
    if 42 { }
}
""", "condition must be bool, got int")

    section("For loop type checking")
    check_ok("for over list", """\
fn main!() -> int {
    let sum = 0
    for x in [1, 2, 3] { sum += x }
    sum
}
""")
    check_fail("for over non-iterable", """\
fn main!() -> unit {
    for x in 42 { }
}
""", "requires iterable, got int")

    section("Match type checking")
    check_ok("match with literals", """\
fn main!() -> string {
    ^match 1 {
        1 => "one",
        _ => "other"
    }
}
""")

    section("Nullable safety")
    check_ok("nullable assignment", """\
fn main!() -> unit {
    const x: int? = null
}
""")
    check_ok("non-null assertion", """\
fn main!() -> int {
    const x: int? = null
    const y: int? = 42
    y!!
}
""")
    check_ok("null coalesce", """\
fn main!() -> int {
    const x: int? = null
    x ?? 0
}
""")
    check_fail("null to non-nullable", """\
fn main!() -> unit {
    const x: int = null
}
""", "declared type int, got null")

    section("Mutability")
    check_fail("assign to const", """\
fn main!() -> unit {
    const x = 10
    x = 20
}
""", "Cannot assign to immutable binding 'x'")
    check_ok("assign to let", """\
fn main!() -> int {
    let x = 10
    x = 20
    x
}
""")
    check_fail("assign wrong type to let", """\
fn main!() -> unit {
    let x = 10
    x = "hello"
}
""", "Cannot assign string to 'x' of type int")
    check_fail("field assign in non-mut method", """\
entity Counter {
    value!: int
    fn try_set!(v: int) -> unit {
        self.value = v
    }
}
""", "Cannot assign to field 'value' in non-mutating method")
    check_ok("field assign in mut method", """\
entity Counter {
    value!: int
    mut fn set!(v: int) -> unit {
        self.value = v
    }
}
""")
    check_fail("assign to field of const binding", """\
entity Point {
    x!: float
    y!: float
}
fn main!() -> unit {
    const p = Point(1.0, 2.0)
    p.x = 3.0
}
""", "Cannot assign to field of immutable binding 'p'")

    section("Return type checking")
    check_ok("matching return type", """\
fn double!(x: int) -> int { x * 2 }
""")
    check_fail("return type mismatch", """\
fn double!(x: int) -> string { x * 2 }
""", "expected return type string, body returns int")
    check_ok("early return matching type", """\
fn foo!(x: int) -> int {
    if x > 0 { ^x }
    0
}
""")
    check_fail("early return wrong type", """\
fn foo!(x: int) -> int {
    ^"hello"
}
""", "Early return type mismatch: expected int, got string")

    section("Entity aliases")
    check_ok("alias is same type", """\
entity Vector {
    x!: float
    y!: float
}
entity Point = Vector
fn main!() -> float {
    const p = Point(1.0, 2.0)
    p.x
}
""")

    section("Implicit self")
    check_ok("implicit self field access", """\
entity Circle {
    radius!: float
    fn area!() -> float { radius * radius * 3.14159 }
}
""")
    check_ok("implicit self method call", """\
entity Vec2 {
    x!: float
    y!: float
    fn squared_mag!() -> float { x * x + y * y }
    fn uses_mag!() -> float { squared_mag() }
}
""")

    section("Global bindings")
    check_ok("global const visible in function", """\
const pi = 3.14159
fn main!() -> float { pi }
""")

    section("List and map literals")
    check_ok("homogeneous list", """\
fn main!() -> unit {
    const xs = [1, 2, 3]
}
""")
    check_fail("heterogeneous list", """\
fn main!() -> unit {
    const xs = [1, "two", 3]
}
""", "List element type mismatch")
    check_ok("map literal", """\
fn main!() -> unit {
    const m = {"a": 1, "b": 2}
}
""")

    section("Exception type checking")
    check_ok("throw and catch", """\
exception MyError
fn main!() -> int {
    let result = 0
    try { throw MyError }
    catch MyError { result = 42 }
    result
}
""")

    section("When statement")
    check_fail("when condition not bool", """\
fn main!() -> unit {
    let x = 0
    when x { }
}
""", "condition must be bool, got int")
    check_ok("when with bool condition", """\
fn main!() -> unit {
    let x = 0
    when x > 0 { }
}
""")

    section("Extension methods")
    check_ok("extension method visible", """\
entity Point {
    x!: float
    y!: float
}
extend Point {
    fn manhattan!(other: Point) -> float {
        const dx = self.x - other.x
        const dy = self.y - other.y
        dx + dy
    }
}
fn main!() -> float {
    const a = Point(1.0, 2.0)
    const b = Point(3.0, 4.0)
    a.manhattan(b)
}
""")

    section("String interpolation")
    check_ok("string interpolation", """\
fn main!() -> string {
    const name = "world"
    "hello {name}"
}
""")

    section("Compound assignment")
    check_ok("compound assignment same type", """\
fn main!() -> int {
    let x = 10
    x += 5
    x
}
""")
    check_fail("compound assignment wrong type", """\
fn main!() -> unit {
    let x = 10
    x += 5.0
}
""", "both operands must be int")

    section("Index access")
    check_ok("list index", """\
fn main!() -> int {
    const xs = [1, 2, 3]
    xs[0]
}
""")
    check_fail("list index not int", """\
fn main!() -> unit {
    const xs = [1, 2, 3]
    const v = xs["zero"]
}
""", "List index must be int, got string")

    # ═══════════════════════════════════════════════════════════════════

    if _failures:
        print(f"\n{_failures} failure(s).")
        sys.exit(1)
    print("\nAll type checker tests passed.")


if __name__ == "__main__":
    main()
