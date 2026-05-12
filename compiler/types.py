"""
Semantic type representation for the double-u type checker.
These types are used internally for type comparison, inference,
and error reporting — distinct from AST type nodes in parser.py.
"""

from __future__ import annotations

from dataclasses import dataclass


# ── Base ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WType:
    """Abstract base for all semantic types."""


# ── Concrete types ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WPrimitive(WType):
    name: str  # "int", "float", "bool", "string", "unit"

    def __repr__(self) -> str:
        return self.name


@dataclass(frozen=True)
class WEntity(WType):
    name: str
    type_args: tuple[WType, ...] = ()

    def __repr__(self) -> str:
        if self.type_args:
            args = ", ".join(repr(a) for a in self.type_args)
            return f"{self.name}<{args}>"
        return self.name


@dataclass(frozen=True)
class WComponent(WType):
    name: str
    type_args: tuple[WType, ...] = ()

    def __repr__(self) -> str:
        if self.type_args:
            args = ", ".join(repr(a) for a in self.type_args)
            return f"{self.name}<{args}>"
        return self.name


@dataclass(frozen=True)
class WException(WType):
    name: str

    def __repr__(self) -> str:
        return self.name


@dataclass(frozen=True)
class WNullable(WType):
    inner: WType

    def __repr__(self) -> str:
        return f"{self.inner}?"


@dataclass(frozen=True)
class WList(WType):
    element: WType

    def __repr__(self) -> str:
        return f"[{self.element}]"


@dataclass(frozen=True)
class WMap(WType):
    key: WType
    value: WType

    def __repr__(self) -> str:
        return f"{{{self.key}: {self.value}}}"


@dataclass(frozen=True)
class WFunction(WType):
    params: tuple[WType, ...]
    ret: WType

    def __repr__(self) -> str:
        ps = ", ".join(repr(p) for p in self.params)
        return f"fn({ps}) -> {self.ret}"


@dataclass(frozen=True)
class CapField:
    name: str
    type: WType


@dataclass(frozen=True)
class CapMethod:
    name: str
    mut: bool
    args: tuple[WType, ...]
    ret: WType | None


@dataclass(frozen=True)
class WCapability(WType):
    members: tuple[CapField | CapMethod, ...]

    def __repr__(self) -> str:
        parts = []
        for m in self.members:
            if isinstance(m, CapField):
                parts.append(f"{m.name}: {m.type}")
            else:
                args = ", ".join(repr(a) for a in m.args)
                ret = f" -> {m.ret}" if m.ret else ""
                mut = "mut " if m.mut else ""
                parts.append(f"{mut}{m.name}({args}){ret}")
        return "{" + ", ".join(parts) + "}"


@dataclass(frozen=True)
class WIntersection(WType):
    types: tuple[WType, ...]

    def __repr__(self) -> str:
        return " & ".join(repr(t) for t in self.types)


@dataclass(frozen=True)
class WTypeVar(WType):
    name: str
    id: int

    def __repr__(self) -> str:
        return self.name


@dataclass(frozen=True)
class WNever(WType):
    """Bottom type — result of throw, early return. Assignable to anything."""

    def __repr__(self) -> str:
        return "never"


@dataclass(frozen=True)
class WNull(WType):
    """Type of the null literal. Assignable only to nullable types."""

    def __repr__(self) -> str:
        return "null"


# ── Singleton instances for common types ─────────────────────────────────────

INT = WPrimitive("int")
FLOAT = WPrimitive("float")
BOOL = WPrimitive("bool")
STRING = WPrimitive("string")
UNIT = WPrimitive("unit")
NEVER = WNever()
NULL = WNull()

PRIMITIVE_MAP = {
    "int": INT,
    "float": FLOAT,
    "bool": BOOL,
    "string": STRING,
    "unit": UNIT,
}


# ── Type utilities ───────────────────────────────────────────────────────────

def is_assignable(target: WType, source: WType, aliases: dict[str, str] | None = None) -> bool:
    """Check if a value of type `source` can be assigned to a location of type `target`.

    Rules:
    - Identical types: OK
    - WNever -> anything: OK (bottom type)
    - WNull -> WNullable(T): OK
    - WNullable(T) -> WNullable(U) if T assignable to U
    - WList(T) -> WList(U) if T == U (invariant)
    - WMap(K1,V1) -> WMap(K2,V2) if K1==K2 and V1==V2 (invariant)
    - Entity aliases resolved transitively
    - No implicit numeric coercion
    """
    if aliases is None:
        aliases = {}

    # Resolve entity aliases
    target = _resolve_alias(target, aliases)
    source = _resolve_alias(source, aliases)

    # Identical types
    if target == source:
        return True

    # Bottom type is assignable to anything
    if isinstance(source, WNever):
        return True

    # null is assignable to any nullable type
    if isinstance(source, WNull) and isinstance(target, WNullable):
        return True

    # T assignable to T? (auto-wrap)
    if isinstance(target, WNullable):
        if is_assignable(target.inner, source, aliases):
            return True

    # Nullable covariance: T? assignable to U? if T assignable to U
    if isinstance(target, WNullable) and isinstance(source, WNullable):
        return is_assignable(target.inner, source.inner, aliases)

    # List invariance
    if isinstance(target, WList) and isinstance(source, WList):
        return target.element == source.element

    # Map invariance
    if isinstance(target, WMap) and isinstance(source, WMap):
        return target.key == source.key and target.value == source.value

    # Function type compatibility
    if isinstance(target, WFunction) and isinstance(source, WFunction):
        if len(target.params) != len(source.params):
            return False
        # Contravariant params, covariant return
        for tp, sp in zip(target.params, source.params):
            if not is_assignable(sp, tp, aliases):
                return False
        return is_assignable(target.ret, source.ret, aliases)

    return False


def _resolve_alias(ty: WType, aliases: dict[str, str]) -> WType:
    """Resolve entity aliases transitively."""
    if isinstance(ty, WEntity) and ty.name in aliases:
        canonical = ty.name
        while canonical in aliases:
            canonical = aliases[canonical]
        return WEntity(canonical, ty.type_args)
    if isinstance(ty, WComponent) and ty.name in aliases:
        canonical = ty.name
        while canonical in aliases:
            canonical = aliases[canonical]
        return WComponent(canonical, ty.type_args)
    return ty


def common_type(a: WType, b: WType, aliases: dict[str, str] | None = None) -> WType | None:
    """Find the common type of two types (for if/match branch unification).

    Returns None if no common type exists.
    """
    if aliases is None:
        aliases = {}

    a = _resolve_alias(a, aliases)
    b = _resolve_alias(b, aliases)

    # Identical
    if a == b:
        return a

    # Never is absorbed
    if isinstance(a, WNever):
        return b
    if isinstance(b, WNever):
        return a

    # Null + T -> T?
    if isinstance(a, WNull) and not isinstance(b, WNull):
        if isinstance(b, WNullable):
            return b
        return WNullable(b)
    if isinstance(b, WNull) and not isinstance(a, WNull):
        if isinstance(a, WNullable):
            return a
        return WNullable(a)

    # T + T? -> T?
    if isinstance(a, WNullable) and not isinstance(b, WNullable):
        if is_assignable(a.inner, b, aliases):
            return a
    if isinstance(b, WNullable) and not isinstance(a, WNullable):
        if is_assignable(b.inner, a, aliases):
            return b

    # Both nullable
    if isinstance(a, WNullable) and isinstance(b, WNullable):
        inner = common_type(a.inner, b.inner, aliases)
        if inner is not None:
            return WNullable(inner)

    return None


def substitute(ty: WType, mapping: dict[int, WType]) -> WType:
    """Replace type variables with concrete types according to mapping.

    mapping keys are WTypeVar.id values.
    """
    if isinstance(ty, WTypeVar):
        return mapping.get(ty.id, ty)

    if isinstance(ty, WNullable):
        return WNullable(substitute(ty.inner, mapping))

    if isinstance(ty, WList):
        return WList(substitute(ty.element, mapping))

    if isinstance(ty, WMap):
        return WMap(substitute(ty.key, mapping), substitute(ty.value, mapping))

    if isinstance(ty, WEntity):
        if ty.type_args:
            return WEntity(ty.name, tuple(substitute(a, mapping) for a in ty.type_args))
        return ty

    if isinstance(ty, WComponent):
        if ty.type_args:
            return WComponent(ty.name, tuple(substitute(a, mapping) for a in ty.type_args))
        return ty

    if isinstance(ty, WFunction):
        return WFunction(
            tuple(substitute(p, mapping) for p in ty.params),
            substitute(ty.ret, mapping),
        )

    if isinstance(ty, WIntersection):
        return WIntersection(tuple(substitute(t, mapping) for t in ty.types))

    # Primitives, WNever, WNull, WException — no type vars inside
    return ty


def unwrap_nullable(ty: WType) -> WType | None:
    """If ty is T?, return T. Otherwise return None."""
    if isinstance(ty, WNullable):
        return ty.inner
    return None


def match_type_pattern(
    pattern: WType, actual: WType, bindings: dict[int, WType],
    aliases: dict[str, str] | None = None,
) -> bool:
    """Unidirectional pattern matching for generic type argument inference.

    pattern contains WTypeVar instances; actual is a concrete type.
    Populates bindings mapping type var id -> concrete type.
    Returns True if the pattern matches.
    """
    if aliases is None:
        aliases = {}

    pattern = _resolve_alias(pattern, aliases)
    actual = _resolve_alias(actual, aliases)

    if isinstance(pattern, WTypeVar):
        existing = bindings.get(pattern.id)
        if existing is not None:
            return existing == actual
        bindings[pattern.id] = actual
        return True

    if type(pattern) != type(actual):
        return False

    if isinstance(pattern, WPrimitive):
        return pattern.name == actual.name

    if isinstance(pattern, WEntity):
        assert isinstance(actual, WEntity)
        if pattern.name != actual.name:
            return False
        if len(pattern.type_args) != len(actual.type_args):
            return False
        return all(
            match_type_pattern(p, a, bindings, aliases)
            for p, a in zip(pattern.type_args, actual.type_args)
        )

    if isinstance(pattern, WList):
        assert isinstance(actual, WList)
        return match_type_pattern(pattern.element, actual.element, bindings, aliases)

    if isinstance(pattern, WMap):
        assert isinstance(actual, WMap)
        return (
            match_type_pattern(pattern.key, actual.key, bindings, aliases)
            and match_type_pattern(pattern.value, actual.value, bindings, aliases)
        )

    if isinstance(pattern, WNullable):
        assert isinstance(actual, WNullable)
        return match_type_pattern(pattern.inner, actual.inner, bindings, aliases)

    if isinstance(pattern, WFunction):
        assert isinstance(actual, WFunction)
        if len(pattern.params) != len(actual.params):
            return False
        return (
            all(
                match_type_pattern(p, a, bindings, aliases)
                for p, a in zip(pattern.params, actual.params)
            )
            and match_type_pattern(pattern.ret, actual.ret, bindings, aliases)
        )

    # For other types, fall back to equality
    return pattern == actual


def type_name(ty: WType) -> str:
    """Human-readable name of a type for error messages."""
    return repr(ty)
