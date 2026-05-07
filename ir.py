"""
Intermediate representation (IR) for AOT compilation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Expressions ───────────────────────────────────────────────────────────────

@dataclass
class IRExpr:
    pass


@dataclass
class IRConst(IRExpr):
    value: object


@dataclass
class IRVar(IRExpr):
    name: str


@dataclass
class IRBinOp(IRExpr):
    op: str
    left: IRExpr
    right: IRExpr


@dataclass
class IRUnaryOp(IRExpr):
    op: str
    value: IRExpr


@dataclass
class IRCall(IRExpr):
    callee: str
    args: list[IRExpr]


@dataclass
class IRFieldAccess(IRExpr):
    obj: IRExpr
    field: str


@dataclass
class IRPtrFieldAccess(IRExpr):
    """Arrow operator: ptr->field."""
    obj: IRExpr
    field: str


@dataclass
class IRIndex(IRExpr):
    obj: IRExpr
    idx: IRExpr


@dataclass
class IRNew(IRExpr):
    """Struct compound literal: (TypeName){ .f0 = v0, ... }"""
    type_name: str
    field_names: list[str]
    args: list[IRExpr]


@dataclass
class IRCast(IRExpr):
    c_type: str
    value: IRExpr


# ── Statements ────────────────────────────────────────────────────────────────

@dataclass
class IRStmt:
    pass


@dataclass
class IRVarDecl(IRStmt):
    name: str
    type_name: str
    value: IRExpr


@dataclass
class IRAssign(IRStmt):
    name: str
    op: str
    value: IRExpr


@dataclass
class IRFieldAssign(IRStmt):
    obj_name: str
    field: str
    value: IRExpr


@dataclass
class IRPtrFieldAssign(IRStmt):
    obj_name: str
    field: str
    value: IRExpr


@dataclass
class IRExprStmt(IRStmt):
    expr: IRExpr


@dataclass
class IRIf(IRStmt):
    cond: IRExpr
    then_stmts: list[IRStmt]
    else_stmts: list[IRStmt]


@dataclass
class IRFor(IRStmt):
    """for var in iterable { body }"""
    var: str
    var_type: str
    iter_len: IRExpr     # expression for the length
    iter_data: IRExpr    # expression for the data pointer / array name
    body: list[IRStmt]


@dataclass
class IRArrayDecl(IRStmt):
    """static C array: <type> <name>[] = { ... };"""
    name: str
    elem_type: str
    elements: list[IRExpr]


@dataclass
class IRReturn(IRStmt):
    value: IRExpr | None


# ── Function ──────────────────────────────────────────────────────────────────

@dataclass
class IRFunction:
    name: str
    params: list[tuple[str, str]]   # (param_name, c_type)
    ret_type: str
    stmts: list[IRStmt] = field(default_factory=list)


# ── Struct type ───────────────────────────────────────────────────────────────

@dataclass
class IRStructField:
    name: str
    c_type: str


@dataclass
class IRStructType:
    name: str
    fields: list[IRStructField]


# ── Type alias ────────────────────────────────────────────────────────────────

@dataclass
class IRTypeAlias:
    """entity Foo = Bar  →  typedef Bar Foo;"""
    name: str
    target: str   # C type name the alias expands to


# ── Program ───────────────────────────────────────────────────────────────────

@dataclass
class IRProgram:
    struct_types: list[IRStructType] = field(default_factory=list)
    type_aliases: list[IRTypeAlias] = field(default_factory=list)
    globals: list[IRVarDecl] = field(default_factory=list)
    functions: list[IRFunction] = field(default_factory=list)
