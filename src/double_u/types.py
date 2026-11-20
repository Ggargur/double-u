"""Representação de tipos, unificação e esquemas (HM com higher-kinded types)."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

_ids = itertools.count(1)


class Type:
    pass


class TVar(Type):
    __slots__ = ("id", "ref", "level", "hint", "rigid", "lits")

    def __init__(self, level: int, hint: str = "", rigid: bool = False):
        self.id = next(_ids)
        self.ref: Type | None = None
        self.level = level
        self.hint = hint
        self.rigid = rigid
        self.lits: set[str] = set()  # restrições de literal: "int", "float"

    def __repr__(self) -> str:
        return show(self)


@dataclass(eq=False)
class TCon(Type):
    name: str
    args: list[Type] = field(default_factory=list)

    def __repr__(self) -> str:
        return show(self)


class EVar:
    """Variável de efeito (io ou puro) para tipos de função."""

    __slots__ = ("ref",)

    def __init__(self) -> None:
        self.ref: bool | None = None


def eff(e: "bool | EVar") -> "bool | EVar":
    while isinstance(e, EVar) and e.ref is not None:
        e = e.ref
    return e


@dataclass(eq=False)
class TFn(Type):
    params: list[Type]
    ret: Type
    io: "bool | EVar" = False
    modes: list[str] | None = None

    def __repr__(self) -> str:
        return show(self)


@dataclass(eq=False)
class TApp(Type):
    """Aplicação de uma variável de tipo de tipo superior: M(A)."""

    head: Type
    args: list[Type]

    def __repr__(self) -> str:
        return show(self)


@dataclass(eq=False)
class TCtor(Type):
    """Construtor de tipo parcialmente aplicado: Result(_ E) vira TCtor('Result', [E])."""

    name: str
    trailing: list[Type] = field(default_factory=list)

    def __repr__(self) -> str:
        return show(self)


INT_TYPES = ["i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"]
FLOAT_TYPES = ["f32", "f64"]
NUM_TYPES = INT_TYPES + FLOAT_TYPES
ALIASES = {"int": "i64", "float": "f64"}
SCALARS = set(NUM_TYPES) | {"bool", "()"}

UNIT = TCon("()")
BOOL = TCon("bool")
INT = TCon("i64")
FLOAT = TCon("f64")
TEXT = TCon("Text")


def prune(t: Type) -> Type:
    while isinstance(t, TVar) and t.ref is not None:
        t = t.ref
    if isinstance(t, TApp):
        head = prune(t.head)
        if isinstance(head, TCtor):
            return TCon(head.name, [*t.args, *head.trailing])
        if isinstance(head, TCon):
            return TCon(head.name, [*head.args, *t.args])
        if head is not t.head:
            return TApp(head, t.args)
    return t


def show(t: Type, names: dict[int, str] | None = None) -> str:
    t = prune(t)
    if isinstance(t, TVar):
        if t.hint:
            return t.hint if t.rigid else f"?{t.hint}"
        return f"?{t.id}"
    if isinstance(t, TCon):
        n = {"i64": "int", "f64": "float"}.get(t.name, t.name)
        if t.name == "#":
            return "#(" + " ".join(show(a) for a in t.args) + ")"
        if not t.args:
            return n
        return f"{n}(" + " ".join(show(a) for a in t.args) + ")"
    if isinstance(t, TFn):
        ps = " ".join(show(p) for p in t.params)
        io = " io" if eff(t.io) is True else ""
        return f"({ps}) -> {show(t.ret)}{io}"
    if isinstance(t, TApp):
        return f"{show(t.head)}(" + " ".join(show(a) for a in t.args) + ")"
    if isinstance(t, TCtor):
        if t.trailing:
            return f"{t.name}(_ " + " ".join(show(a) for a in t.trailing) + ")"
        return t.name
    return repr(t)


class UnifyError(Exception):
    pass


def occurs(v: TVar, t: Type) -> bool:
    t = prune(t)
    if t is v:
        return True
    if isinstance(t, TCon):
        return any(occurs(v, a) for a in t.args)
    if isinstance(t, TFn):
        return any(occurs(v, p) for p in t.params) or occurs(v, t.ret)
    if isinstance(t, TApp):
        return occurs(v, t.head) or any(occurs(v, a) for a in t.args)
    if isinstance(t, TCtor):
        return any(occurs(v, a) for a in t.trailing)
    return False


def adjust_levels(v: TVar, t: Type) -> None:
    t = prune(t)
    if isinstance(t, TVar):
        if t.level > v.level:
            t.level = v.level
    elif isinstance(t, TCon):
        for a in t.args:
            adjust_levels(v, a)
    elif isinstance(t, TFn):
        for p in t.params:
            adjust_levels(v, p)
        adjust_levels(v, t.ret)
    elif isinstance(t, TApp):
        adjust_levels(v, t.head)
        for a in t.args:
            adjust_levels(v, a)
    elif isinstance(t, TCtor):
        for a in t.trailing:
            adjust_levels(v, a)


def lit_ok(lits: set[str], t: Type) -> bool:
    t = prune(t)
    if isinstance(t, TVar):
        return True
    if not isinstance(t, TCon):
        return False
    if "float" in lits and t.name not in FLOAT_TYPES:
        return False
    if "int" in lits and t.name not in NUM_TYPES:
        return False
    return True


def bind(v: TVar, t: Type) -> None:
    if v.rigid:
        tt = prune(t)
        if isinstance(tt, TVar) and not tt.rigid:
            bind(tt, v)
            return
        raise UnifyError()
    if occurs(v, t):
        raise UnifyError()
    if v.lits:
        tt = prune(t)
        if isinstance(tt, TVar):
            if tt.rigid:
                raise UnifyError()
            tt.lits |= v.lits
        elif not lit_ok(v.lits, tt):
            raise UnifyError()
    adjust_levels(v, t)
    v.ref = t


def unify(a: Type, b: Type) -> None:
    a = prune(a)
    b = prune(b)
    if a is b:
        return
    if isinstance(a, TVar) and not a.rigid:
        bind(a, b)
        return
    if isinstance(b, TVar) and not b.rigid:
        bind(b, a)
        return
    if isinstance(a, TVar) or isinstance(b, TVar):
        raise UnifyError()
    if isinstance(a, TCon) and isinstance(b, TCon):
        if a.name != b.name or len(a.args) != len(b.args):
            raise UnifyError()
        for x, y in zip(a.args, b.args):
            unify(x, y)
        return
    if isinstance(a, TFn) and isinstance(b, TFn):
        if len(a.params) != len(b.params):
            raise UnifyError()
        for x, y in zip(a.params, b.params):
            unify(x, y)
        unify(a.ret, b.ret)
        ea, eb = eff(a.io), eff(b.io)
        if isinstance(ea, EVar):
            if ea is not eb:
                ea.ref = eb  # type: ignore[assignment]
        elif isinstance(eb, EVar):
            eb.ref = ea
        return
    if isinstance(a, TApp) or isinstance(b, TApp):
        if not isinstance(a, TApp):
            a, b = b, a
        assert isinstance(a, TApp)
        n = len(a.args)
        if isinstance(b, TApp):
            if len(b.args) != n:
                raise UnifyError()
            unify(a.head, b.head)
            for x, y in zip(a.args, b.args):
                unify(x, y)
            return
        if isinstance(b, TCon) and len(b.args) >= n:
            for x, y in zip(a.args, b.args[:n]):
                unify(x, y)
            unify(a.head, TCtor(b.name, list(b.args[n:])))
            return
        raise UnifyError()
    if isinstance(a, TCtor) and isinstance(b, TCtor):
        if a.name != b.name or len(a.trailing) != len(b.trailing):
            raise UnifyError()
        for x, y in zip(a.trailing, b.trailing):
            unify(x, y)
        return
    if isinstance(a, TCtor) and isinstance(b, TCon) and not a.trailing and b.name == a.name and not b.args:
        return
    if isinstance(b, TCtor) and isinstance(a, TCon) and not b.trailing and a.name == b.name and not a.args:
        return
    raise UnifyError()


def head_name(t: Type) -> str | None:
    t = prune(t)
    if isinstance(t, TCon):
        return t.name
    if isinstance(t, TCtor):
        return t.name
    if isinstance(t, TFn):
        return "->"
    return None


def free_vars(t: Type, out: list[TVar] | None = None) -> list[TVar]:
    if out is None:
        out = []
    t = prune(t)
    if isinstance(t, TVar):
        if t not in out:
            out.append(t)
    elif isinstance(t, TCon):
        for a in t.args:
            free_vars(a, out)
    elif isinstance(t, TFn):
        for p in t.params:
            free_vars(p, out)
        free_vars(t.ret, out)
    elif isinstance(t, TApp):
        free_vars(t.head, out)
        for a in t.args:
            free_vars(a, out)
    elif isinstance(t, TCtor):
        for a in t.trailing:
            free_vars(a, out)
    return out


def subst(t: Type, m: dict[int, Type]) -> Type:
    t = prune(t)
    if isinstance(t, TVar):
        return m.get(t.id, t)
    if isinstance(t, TCon):
        return TCon(t.name, [subst(a, m) for a in t.args]) if t.args else t
    if isinstance(t, TFn):
        return TFn([subst(p, m) for p in t.params], subst(t.ret, m), t.io, t.modes)
    if isinstance(t, TApp):
        return prune(TApp(subst(t.head, m), [subst(a, m) for a in t.args]))
    if isinstance(t, TCtor):
        return TCtor(t.name, [subst(a, m) for a in t.trailing])
    return t


@dataclass(eq=False)
class Scheme:
    vars: list[TVar]
    preds: list[tuple[str, Type]]
    type: Type

    def __repr__(self) -> str:
        return f"∀{[show(v) for v in self.vars]}. {self.preds} => {show(self.type)}"


def key(t: Type) -> str:
    """Chave textual de um tipo concreto (para monomorfização)."""
    t = prune(t)
    if isinstance(t, TCon):
        if not t.args:
            return t.name
        return t.name + "(" + ",".join(key(a) for a in t.args) + ")"
    if isinstance(t, TFn):
        return "fn(" + ",".join(key(p) for p in t.params) + ")" + key(t.ret)
    if isinstance(t, TCtor):
        return t.name + "[" + ",".join(key(a) for a in t.trailing) + "]"
    if isinstance(t, TVar):
        return f"?{t.id}"
    if isinstance(t, TApp):
        return key(t.head) + "<" + ",".join(key(a) for a in t.args) + ">"
    raise TypeError(t)
