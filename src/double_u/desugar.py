"""Desaçucaramento sintático: fixidez, aplicação parcial com `_` e compreensões.

Roda depois da coleta de declarações (precisa da tabela de fixidez das traits)
e antes da checagem de tipos.
"""

from __future__ import annotations

import itertools
from typing import Any

from . import ast as A
from .diagnostics import Diagnostics

BUILTIN_FIXITY = {"&&": ("infixr", 3), "||": ("infixr", 2)}
_tmp = itertools.count(1)


def fresh(prefix: str = "t") -> str:
    return f"${prefix}{next(_tmp)}"


class Desugar:
    def __init__(self, fixity: dict[str, tuple[str, int]], diags: Diagnostics):
        self.fixity = {**BUILTIN_FIXITY, **fixity}
        self.diags = diags
        self.gen_mode = False

    # -------------------------------------------------------------- genérico
    def module(self, m: A.Module) -> None:
        for d in m.decls:
            self.decl(d)

    def decl(self, d: A.Node) -> None:
        if isinstance(d, A.FnDecl):
            if d.body is not None:
                saved = self.gen_mode
                self.gen_mode = d.is_gen
                d.body = self.expr(d.body)
                self.gen_mode = saved
        elif isinstance(d, (A.TraitDecl, A.ImplDecl)):
            for f in d.methods:
                self.decl(f)
        elif isinstance(d, A.LetDecl):
            d.expr = self.expr(d.expr)

    def expr(self, e: Any) -> Any:
        if isinstance(e, A.OpChain):
            return self.expr(self.reassoc(e))
        if isinstance(e, A.BinOp):
            e.left = self.expr(e.left)
            e.right = self.expr(e.right)
            if isinstance(e.left, A.Hole) or isinstance(e.right, A.Hole):
                return self.section(e)
            return e
        if isinstance(e, A.Call):
            e.fn = self.expr(e.fn)
            e.args = [self.expr(a) for a in e.args]
            if any(isinstance(a, A.Hole) for a in e.args):
                return self.partial(e)
            return e
        if isinstance(e, A.For):
            return self.for_(e)
        if isinstance(e, A.Str):
            for p in e.parts:
                if isinstance(p, A.InterpPart):
                    p.expr = self.expr(p.expr)
            return e
        if isinstance(e, A.Block):
            e.stmts = [self.stmt(s) for s in e.stmts]
            if e.result is not None:
                e.result = self.expr(e.result)
            return e
        if isinstance(e, A.Match):
            e.scrut = self.expr(e.scrut)
            for arm in e.arms:
                if arm.guard is not None:
                    arm.guard = self.expr(arm.guard)
                arm.body = self.expr(arm.body)
            return e
        if isinstance(e, A.MapLit):
            e.pairs = [(self.expr(k), self.expr(v)) for k, v in e.pairs]
            return e
        if isinstance(e, A.With):
            e.expr = self.expr(e.expr)
            e.fields = [(n, self.expr(v), p) for n, v, p in e.fields]
            return e
        if isinstance(e, A.BuiltinCall):
            if e.name != "extern":
                e.args = [self.expr(a) for a in e.args]
            return e
        if isinstance(e, A.Node):
            for name, val in list(vars(e).items()):
                if name == "pos":
                    continue
                if isinstance(val, A.Expr):
                    setattr(e, name, self.expr(val))
                elif isinstance(val, list) and val and isinstance(val[0], A.Expr):
                    setattr(e, name, [self.expr(x) for x in val])
        return e

    def stmt(self, s: A.Stmt) -> A.Stmt:
        if isinstance(s, (A.Let, A.Assign)):
            s.expr = self.expr(s.expr)
            if isinstance(s, A.Assign):
                s.target = self.expr(s.target)
        elif isinstance(s, A.ExprStmt):
            s.expr = self.expr(s.expr)
        return s

    # -------------------------------------------------------------- fixidez
    def fix(self, op: A.OpTok) -> tuple[str, int]:
        if op.word:
            return ("infix", 0)
        return self.fixity.get(op.op, ("infixl", 9))

    def reassoc(self, chain: A.OpChain) -> A.Expr:
        items = chain.items
        words = [it for it in items[1::2] if it.word]
        if len(words) > 1:
            self.diags.error(
                "fixidez", words[1].pos,
                f"infixas com nome em palavra não se encadeiam sem parênteses: "
                f"`{words[0].op}` e `{words[1].op}`")
        operands: list[A.Expr] = [items[0]]
        ops: list[A.OpTok] = []

        def reduce() -> None:
            op = ops.pop()
            r = operands.pop()
            l = operands.pop()
            operands.append(A.BinOp(op.pos, op.op, l, r, op.word))

        for k in range(1, len(items), 2):
            op = items[k]
            assoc, prec = self.fix(op)
            while ops:
                top_assoc, top_prec = self.fix(ops[-1])
                if top_prec > prec or (top_prec == prec and assoc == "infixl" and top_assoc == "infixl"):
                    reduce()
                    continue
                if top_prec == prec and (assoc == "infix" or top_assoc == "infix" or assoc != top_assoc):
                    if not (op.word and ops[-1].word):
                        self.diags.error(
                            "fixidez", op.pos,
                            f"`{ops[-1].op}` e `{op.op}` são não-associativos: use parênteses")
                    reduce()
                    continue
                break
            ops.append(op)
            operands.append(items[k + 1])
        while ops:
            reduce()
        return operands[0]

    # -------------------------------------------------------------- aplicação parcial
    def section(self, e: A.BinOp) -> A.Expr:
        params: list[A.Param] = []
        if isinstance(e.left, A.Hole):
            n = fresh("x")
            params.append(A.Param(e.left.pos, n))
            e.left = A.Name(e.left.pos, n)
        if isinstance(e.right, A.Hole):
            n = fresh("x")
            params.append(A.Param(e.right.pos, n))
            e.right = A.Name(e.right.pos, n)
        return A.Lambda(e.pos, params, e)

    def partial(self, e: A.Call) -> A.Expr:
        params: list[A.Param] = []
        stmts: list[A.Stmt] = []
        new_args: list[A.Expr] = []
        for a in e.args:
            if isinstance(a, A.Hole):
                n = fresh("x")
                params.append(A.Param(a.pos, n))
                new_args.append(A.Name(a.pos, n))
            elif isinstance(a, (A.Name, A.Lit)):
                new_args.append(a)
            else:
                n = fresh("c")
                stmts.append(A.Let(a.pos, A.PVar(a.pos, n), None, a))
                new_args.append(A.Name(a.pos, n))
        lam = A.Lambda(e.pos, params, A.Call(e.pos, e.fn, new_args, e.names))
        lam.partial = True  # type: ignore[attr-defined]
        if not stmts:
            return lam
        return A.Block(e.pos, stmts, lam)

    # -------------------------------------------------------------- compreensões
    def lam_for(self, pats: list[A.Pat], body: A.Expr, pos: Any, mut: bool = False) -> A.Lambda:
        mode = "mut" if mut else "borrow"
        if len(pats) == 1 and isinstance(pats[0], (A.PVar, A.PWild)):
            p = pats[0]
            name = p.name if isinstance(p, A.PVar) else "_"
            return A.Lambda(pos, [A.Param(p.pos, name, None, mode)], body)
        if mut:
            params = []
            for p in pats:
                if isinstance(p, A.PVar):
                    params.append(A.Param(p.pos, p.name, None, "mut" if p.bang else "borrow"))
                else:
                    params.append(A.Param(p.pos, "_", None, "borrow"))
            lam = A.Lambda(pos, params, body)
            lam.multi = True  # type: ignore[attr-defined]
            return lam
        n = fresh("p")
        pat: A.Pat = pats[0] if len(pats) == 1 else A.PTuple(pos, pats)
        inner = A.Block(pos, [A.Let(pos, pat, None, A.Name(pos, n))], body)
        return A.Lambda(pos, [A.Param(pos, n)], inner)

    def for_(self, e: A.For) -> A.Expr:
        for g in e.gens:
            g.src = self.expr(g.src)
            g.conds = [self.expr(c) for c in g.conds]
        if e.yld is not None:
            e.yld = self.expr(e.yld)
            for g in e.gens:
                for p in g.pats:
                    if isinstance(p, A.PVar) and p.bang:
                        self.diags.error("posse", p.pos,
                                         "`!` num padrão de compreensão com `yield` faria o empréstimo mutável escapar")
            return self.comprehension(e.gens, e.yld)
        assert e.body is not None
        body = self.expr(e.body)
        if self.gen_mode:
            e.body = body
            return e
        return self.loop(e.gens, body, e.pos)

    def loop(self, gens: list[A.Gen], body: A.Expr, pos: Any) -> A.Expr:
        g = gens[0]
        inner = body if len(gens) == 1 else self.loop(gens[1:], body, pos)
        for c in reversed(g.conds):
            inner = A.If(c.pos, c, inner, None)
        bang = any(isinstance(p, A.PVar) and p.bang for p in g.pats)
        lam = self.lam_for(g.pats, inner, g.pos, mut=bang)
        lam.byref = True  # type: ignore[attr-defined]
        if bang:
            src: A.Expr = A.Bang(g.src.pos, g.src)
            fn = "eachMut"
        else:
            src = g.src
            fn = "each"
        return A.Call(g.pos, A.Name(g.pos, fn), [src, lam], [None, None])

    def comprehension(self, gens: list[A.Gen], yld: A.Expr) -> A.Expr:
        g = gens[0]
        src = g.src
        for c in g.conds:
            src = A.Call(c.pos, A.Name(c.pos, "filter"), [src, self.lam_for(g.pats, c, c.pos)], [None, None])
        if len(gens) == 1:
            return A.Call(g.pos, A.Name(g.pos, "map"), [src, self.lam_for(g.pats, yld, g.pos)], [None, None])
        rest = self.comprehension(gens[1:], yld)
        return A.BinOp(g.pos, ">>=", src, self.lam_for(g.pats, rest, g.pos))
