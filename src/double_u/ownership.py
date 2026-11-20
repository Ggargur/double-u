"""Verificador de posse (ADR 0002): roda depois da tipagem.

Move: passar a um parâmetro `own`, guardar num construtor, lista, tupla ou
closure que escapa. Usar uma ligação depois do move é erro. Guardar um
parâmetro emprestado (não copiável) é erro. Tipos copiáveis nunca se movem.
"""

from __future__ import annotations

from typing import Any

from . import ast as A
from .checker import Checker, CtorInfo, FnInfo, LocalVar
from .diagnostics import Pos
from .types import SCALARS, TCon, TFn, TVar, prune, subst


class Ownership:
    def __init__(self, ck: Checker):
        self.ck = ck
        self.moved: dict[int, Pos] = {}
        self.cache: dict[str, bool] = {}
        self.lam_depth = 0
        self.fn_locals: set[int] = set()

    # ------------------------------------------------------------------ copiável
    def copyable(self, t: Any, seen: frozenset[str] = frozenset()) -> bool:
        t = prune(t)
        if isinstance(t, TVar):
            return bool(t.lits)
        if isinstance(t, TFn):
            return False
        if not isinstance(t, TCon):
            return False
        if t.name in SCALARS:
            return True
        if t.name == "#":
            return all(self.copyable(a, seen) for a in t.args)
        ti = self.ck.find_type_info(t.name)
        if ti is None or ti.kind == "builtin" or "nocopy" in ti.attrs:
            return False
        if t.name in seen:
            return False
        mp = {v.id: a for v, a in zip(ti.vars, t.args)}
        return all(self.copyable(subst(ft, mp), seen | {t.name}) for c in ti.ctors for ft in c.field_types)

    # ------------------------------------------------------------------ entrada
    def run(self) -> None:
        for f in self.ck.fns:
            if f.body is None or f.prim is not None:
                continue
            self.check_fn(f)

    def check_fn(self, f: FnInfo) -> None:
        self.moved = {}
        self.ck.module = f.module
        if f.is_gen:
            for p in f.params:
                lv = getattr(p, "local", None)
                if lv is not None and lv.kind == "borrow" and not self.copyable(lv.ty):
                    self.err(p.pos, f"o generator guarda o parâmetro emprestado `{p.name}` além da chamada",
                             f"declare o parâmetro como `own {p.name}`")
        self.expr(f.body)

    def err(self, pos: Pos, msg: str, *notes: str) -> None:
        self.ck.err("posse", pos, msg, *notes)

    # ------------------------------------------------------------------ usos
    def use(self, e: A.Name) -> None:
        ref = getattr(e, "ref", None)
        if ref is None or ref.kind != "local":
            return
        lv: LocalVar = ref.target
        if lv.id in self.moved:
            where = self.moved[lv.id]
            self.err(e.pos, f"`{lv.name}` foi movido e não pode mais ser usado",
                     f"movido em {where.file}:{where.line}:{where.col}; use `.copy` para manter uma cópia")

    def move(self, e: A.Expr, what: str) -> None:
        """`e` está sendo guardado/passado a `own`."""
        if not isinstance(e, A.Name):
            self.expr(e)
            return
        self.use(e)
        ref = getattr(e, "ref", None)
        if ref is None or ref.kind != "local":
            return
        lv: LocalVar = ref.target
        if self.copyable(lv.ty):
            return
        if lv.kind == "borrow":
            self.err(e.pos, f"o parâmetro emprestado `{lv.name}` não pode ser guardado {what}",
                     f"declare o parâmetro como `own {lv.name}` ou guarde `{lv.name}.copy`")
            return
        if lv.kind == "mut":
            return
        if self.lam_depth and lv.id not in self.fn_locals:
            return  # capturado: a closure continua dona
        self.moved[lv.id] = e.pos
        e.move = True  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ expressões
    def expr(self, e: Any) -> None:
        if e is None:
            return
        m = getattr(self, "x_" + type(e).__name__, None)
        if m is not None:
            m(e)
            return
        if isinstance(e, (A.Node, A.InterpPart)):
            for k, v in vars(e).items():
                if k in ("pos", "ty", "ref"):
                    continue
                self.walk(v)

    def walk(self, v: Any) -> None:
        if isinstance(v, (A.Expr, A.InterpPart)):
            self.expr(v)
        elif isinstance(v, A.Stmt):
            self.stmt(v)
        elif isinstance(v, list):
            for x in v:
                self.walk(x)
        elif isinstance(v, tuple):
            for x in v:
                self.walk(x)

    def x_Name(self, e: A.Name) -> None:
        self.use(e)

    def modes_of(self, fn: Any) -> list[str] | None:
        ref = getattr(fn, "ref", None)
        if ref is None:
            return None
        if ref.kind == "fn":
            return [p.mode for p in ref.target.params]
        if ref.kind == "method":
            return [p.mode for p in ref.target.decl.params]
        if ref.kind == "ctor":
            return ["own"] * ref.target.arity
        return None

    def call_args(self, modes: list[str] | None, args: list[A.Expr], ctor: bool) -> None:
        for i, a in enumerate(args):
            mode = modes[i] if modes and i < len(modes) else "borrow"
            if isinstance(a, A.Bang):
                self.expr(a.expr)
            elif mode == "own":
                self.move(a, "num construtor" if ctor else "num parâmetro `own`")
            else:
                self.expr(a)

    def x_Call(self, e: A.Call) -> None:
        self.expr(e.fn)
        ref = getattr(e.fn, "ref", None)
        self.call_args(self.modes_of(e.fn), e.args, ref is not None and ref.kind == "ctor")

    def x_BinOp(self, e: A.BinOp) -> None:
        self.call_args(self.modes_of(e), [e.left, e.right], False)

    def x_Tuple(self, e: A.Tuple) -> None:
        for x in e.items:
            self.move(x, "numa tupla")

    def x_ListLit(self, e: A.ListLit) -> None:
        for x in e.items:
            self.move(x, "numa lista")

    def x_MapLit(self, e: A.MapLit) -> None:
        for k, v in e.pairs:
            self.move(k, "num mapa")
            self.move(v, "num mapa")

    def x_With(self, e: A.With) -> None:
        self.expr(e.expr)
        for _, v, _ in e.fields:
            self.move(v, "num construtor")

    def x_Lambda(self, e: A.Lambda) -> None:
        byref = getattr(e, "byref", False)
        if not byref:
            for lv in getattr(e, "captures", []):
                if lv.id in self.moved:
                    where = self.moved[lv.id]
                    self.err(e.pos, f"`{lv.name}` foi movido e não pode ser capturado",
                             f"movido em {where.file}:{where.line}:{where.col}")
                    continue
                if self.copyable(lv.ty) or lv.kind == "mut":
                    continue
                if lv.kind == "borrow":
                    self.err(e.pos, f"a closure captura o parâmetro emprestado `{lv.name}`, e closures capturam por move",
                             f"declare o parâmetro como `own {lv.name}` ou capture `{lv.name}.copy`")
                    continue
                if self.lam_depth and lv.id not in self.fn_locals:
                    continue
                self.moved[lv.id] = e.pos
        saved_moved = self.moved
        saved_locals = self.fn_locals
        self.moved = {} if not byref else dict(self.moved)
        self.fn_locals = set() if not byref else set(self.fn_locals)
        for p in e.params:
            lv = getattr(p, "local", None)
            if lv is not None:
                self.fn_locals.add(lv.id)
        self.lam_depth += 1
        self.expr(e.body)
        self.lam_depth -= 1
        if byref:
            # loops: o corpo roda várias vezes
            self.expr(e.body)
            for k, v in self.moved.items():
                if k in saved_locals or not self.lam_depth:
                    saved_moved.setdefault(k, v)
        self.moved = saved_moved
        self.fn_locals = saved_locals

    def x_If(self, e: A.If) -> None:
        self.expr(e.cond)
        before = dict(self.moved)
        self.expr(e.then)
        after_then = self.moved
        self.moved = dict(before)
        self.expr(e.els)
        self.moved = {**after_then, **self.moved}

    def x_Match(self, e: A.Match) -> None:
        self.expr(e.scrut)
        before = dict(self.moved)
        acc: dict[int, Pos] = {}
        for arm in e.arms:
            self.moved = dict(before)
            self.bind_pat(arm.pat)
            self.expr(arm.guard)
            self.expr(arm.body)
            acc.update(self.moved)
        self.moved = {**before, **acc}

    def x_While(self, e: A.While) -> None:
        for _ in range(2):
            self.expr(e.cond)
            self.expr(e.body)

    def x_Block(self, e: A.Block) -> None:
        for s in e.stmts:
            self.stmt(s)
        self.expr(e.result)

    def x_Dot(self, e: A.Dot) -> None:
        if not getattr(e, "qualified", False):  # `math.cross` não usa valor nenhum
            self.expr(e.obj)

    def x_LazyE(self, e: A.LazyE) -> None:
        self.expr(getattr(e, "lam", None) or e.expr)

    def x_For(self, e: A.For) -> None:
        for g in e.gens:
            self.expr(g.src)
            for c in g.conds:
                self.expr(c)
            for p in g.pats:
                self.bind_pat(p)
        for _ in range(2):
            self.expr(e.body)

    def bind_pat(self, p: Any) -> None:
        if isinstance(p, A.PVar):
            lv = getattr(p, "local", None)
            if lv is not None:
                self.moved.pop(lv.id, None)
                self.fn_locals.add(lv.id)
        elif isinstance(p, A.PTuple):
            for x in p.items:
                self.bind_pat(x)
        elif isinstance(p, A.PCtor):
            for x in p.args:
                self.bind_pat(x)

    # ------------------------------------------------------------------ instruções
    def stmt(self, s: A.Stmt) -> None:
        if isinstance(s, A.ExprStmt):
            self.expr(s.expr)
        elif isinstance(s, A.Let):
            self.expr(s.expr)
            if getattr(s, "assign", False):
                for lv in pat_locals(s.pat):
                    self.moved.pop(lv.id, None)
            else:
                self.bind_pat(s.pat)
        elif isinstance(s, A.Assign):
            self.expr(s.expr)


def pat_locals(p: Any) -> list[LocalVar]:
    if isinstance(p, A.PVar):
        lv = getattr(p, "local", None)
        return [lv] if lv is not None else []
    if isinstance(p, A.PTuple):
        out: list[LocalVar] = []
        for x in p.items:
            out += pat_locals(x)
        return out
    return []


__all__ = ["Ownership", "CtorInfo"]
