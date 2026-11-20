"""Árvore sintática. Os nós de expressão recebem `ty` durante a checagem."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .diagnostics import Pos


@dataclass(eq=False)
class Node:
    pos: Pos


# ---------------------------------------------------------------- tipos (sintaxe)
@dataclass(eq=False)
class TName(Node):
    name: str
    args: list["TypeExpr"] = field(default_factory=list)
    module: str | None = None


@dataclass(eq=False)
class TFunc(Node):
    params: list["TypeExpr"]
    ret: "TypeExpr"
    io: bool = False


@dataclass(eq=False)
class TTuple(Node):
    items: list["TypeExpr"]


TypeExpr = TName | TFunc | TTuple


# ---------------------------------------------------------------- padrões
@dataclass(eq=False)
class Pat(Node):
    pass


@dataclass(eq=False)
class PWild(Pat):
    pass


@dataclass(eq=False)
class PVar(Pat):
    name: str
    bang: bool = False  # `!x` em padrões de for


@dataclass(eq=False)
class PLit(Pat):
    value: Any
    kind: str  # int float bool str


@dataclass(eq=False)
class PCtor(Pat):
    name: str
    args: list[Pat]
    names: list[str | None]
    module: str | None = None


@dataclass(eq=False)
class PTuple(Pat):
    items: list[Pat]


# ---------------------------------------------------------------- expressões
@dataclass(eq=False)
class Expr(Node):
    pass


@dataclass(eq=False)
class Lit(Expr):
    value: Any
    kind: str  # int float bool unit


@dataclass(eq=False)
class InterpPart:
    expr: Expr
    spec: str | None
    pos: Pos


@dataclass(eq=False)
class Str(Expr):
    parts: list[str | InterpPart]


@dataclass(eq=False)
class Name(Expr):
    name: str


@dataclass(eq=False)
class Hole(Expr):
    pass


@dataclass(eq=False)
class Bang(Expr):
    expr: Expr


@dataclass(eq=False)
class Call(Expr):
    fn: Expr
    args: list[Expr]
    names: list[str | None]


@dataclass(eq=False)
class Dot(Expr):
    obj: Expr
    name: str
    name_pos: Pos | None = None


@dataclass(eq=False)
class TryOp(Expr):
    expr: Expr


@dataclass(eq=False)
class OpChain(Expr):
    """Sequência plana `a op b op c`; reassociada depois pela fixidez das traits."""

    items: list[Any]  # alterna Expr e OpTok


@dataclass(eq=False)
class OpTok:
    op: str
    pos: Pos
    word: bool  # infixa com nome em palavra


@dataclass(eq=False)
class BinOp(Expr):
    op: str
    left: Expr
    right: Expr
    word: bool = False


@dataclass(eq=False)
class Lambda(Expr):
    params: list["Param"]
    body: Expr


@dataclass(eq=False)
class If(Expr):
    cond: Expr
    then: Expr
    els: Expr | None


@dataclass(eq=False)
class Arm(Node):
    pat: Pat
    guard: Expr | None
    body: Expr


@dataclass(eq=False)
class Match(Expr):
    scrut: Expr
    arms: list[Arm]


@dataclass(eq=False)
class Tuple(Expr):
    items: list[Expr]


@dataclass(eq=False)
class ListLit(Expr):
    items: list[Expr]


@dataclass(eq=False)
class MapLit(Expr):
    pairs: list[tuple[Expr, Expr]]


@dataclass(eq=False)
class With(Expr):
    expr: Expr
    fields: list[tuple[str, Expr, Pos]]


@dataclass(eq=False)
class LazyE(Expr):
    expr: Expr


@dataclass(eq=False)
class BuiltinCall(Expr):
    name: str
    args: list[Any]


@dataclass(eq=False)
class Block(Expr):
    stmts: list["Stmt"]
    result: Expr | None  # None => ()


@dataclass(eq=False)
class While(Expr):
    cond: Expr
    body: Expr


@dataclass(eq=False)
class Gen(Node):
    pats: list[Pat]
    src: Expr
    conds: list[Expr]


@dataclass(eq=False)
class For(Expr):
    gens: list[Gen]
    body: Expr | None  # loop
    yld: Expr | None  # compreensão


@dataclass(eq=False)
class Yield(Expr):
    expr: Expr


# ---------------------------------------------------------------- instruções
@dataclass(eq=False)
class Stmt(Node):
    pass


@dataclass(eq=False)
class Let(Stmt):
    pat: Pat
    ann: TypeExpr | None
    expr: Expr
    is_var: bool = False


@dataclass(eq=False)
class Assign(Stmt):
    target: Expr  # Name, Dot ou Tuple de Names
    expr: Expr


@dataclass(eq=False)
class ExprStmt(Stmt):
    expr: Expr


# ---------------------------------------------------------------- declarações
@dataclass(eq=False)
class Param(Node):
    name: str
    ann: TypeExpr | None = None
    mode: str = "borrow"  # borrow mut own


@dataclass(eq=False)
class Constraint(Node):
    trait: str
    type: TypeExpr


@dataclass(eq=False)
class FnDecl(Node):
    name: str
    public: bool
    fixity: str  # prefix infix postfix
    params: list[Param]
    ret: TypeExpr | None
    io: bool
    given: list[Constraint]
    body: Expr | None
    assoc: str | None = None  # infixl infixr infix
    prec: int | None = None
    is_gen: bool = False
    doc: str | None = None


@dataclass(eq=False)
class Field(Node):
    name: str | None
    type: TypeExpr
    public: bool = False


@dataclass(eq=False)
class Variant(Node):
    name: str
    fields: list[Field]


@dataclass(eq=False)
class TypeDecl(Node):
    name: str
    public: bool
    params: list[str]
    record: list[Field] | None
    variants: list[Variant] | None
    attrs: dict[str, list[str]] = field(default_factory=dict)
    alias: TypeExpr | None = None


@dataclass(eq=False)
class TraitDecl(Node):
    name: str
    public: bool
    param: str
    methods: list[FnDecl]
    supers: list[Constraint] = field(default_factory=list)


@dataclass(eq=False)
class ImplDecl(Node):
    trait: str
    type: TypeExpr
    given: list[Constraint]
    methods: list[FnDecl]
    trait_module: str | None = None


@dataclass(eq=False)
class LetDecl(Node):
    name: str
    public: bool
    expr: Expr
    ann: TypeExpr | None = None


@dataclass(eq=False)
class Module(Node):
    name: str
    path: str
    decls: list[Node]
