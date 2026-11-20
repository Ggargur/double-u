"""Geração de LLVM IR (llvmlite) com monomorfização.

Todo valor é uma palavra de 64 bits: escalares direto, o resto é ponteiro para
um objeto com contagem de referência. Cada expressão produz uma referência
própria (+1); os locais são liberados ao sair do escopo.
Funções usam `tailcc`; chamadas em posição de cauda usam `musttail`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llvmlite import ir

from . import ast as A
from .checker import Checker, CtorInfo, FnInfo, ImplInfo, LocalVar, MethodInfo, Ref
from .types import (
    FLOAT_TYPES, INT_TYPES, SCALARS, TApp, TCon, TCtor, TFn, TVar, Type, head_name, key, prune, subst,
)

I64 = ir.IntType(64)
I32 = ir.IntType(32)
I8 = ir.IntType(8)
I1 = ir.IntType(1)
F64 = ir.DoubleType()
F32 = ir.FloatType()
PTR = ir.PointerType(I8)
VOID = ir.VoidType()

K_DATA, K_CLOS, K_TEXT = 0, 1, 2


def resolve(t: Type) -> Type:
    t = prune(t)
    if isinstance(t, TVar):
        return TCon("()")
    if isinstance(t, TCon):
        return TCon(t.name, [resolve(a) for a in t.args]) if t.args else t
    if isinstance(t, TFn):
        return TFn([resolve(p) for p in t.params], resolve(t.ret), t.io, t.modes)
    if isinstance(t, TCtor):
        return TCtor(t.name, [resolve(a) for a in t.trailing])
    if isinstance(t, TApp):
        return resolve(prune(TApp(resolve(t.head), t.args)))
    return t


def is_heap(t: Type) -> bool:
    t = prune(t)
    if isinstance(t, TCon):
        return t.name not in SCALARS
    return True


def int_bits(name: str) -> int:
    return int(name[1:])


@dataclass
class Local:
    kind: str  # slot (alloca) ref (ponteiro em i64) env (índice no env)
    ptr: Any
    ty: Type
    owned: bool = True


@dataclass
class FnState:
    fn: ir.Function
    sub: dict[int, Type]
    locals: dict[int, Local] = field(default_factory=dict)
    scopes: list[list[tuple[Any, Type]]] = field(default_factory=list)
    env: Any = None
    entry: Any = None
    ret_ty: Type | None = None
    lam: A.Lambda | None = None
    allocas: Any = None


class Codegen:
    def __init__(self, ck: Checker, release: bool = False):
        self.ck = ck
        self.release = release
        self.mod = ir.Module(name="double_u")
        self.mod.triple = ""
        self.instances: dict[tuple, ir.Function] = {}
        self.work: list[tuple[Any, ...]] = []
        self.rt: dict[str, ir.Function] = {}
        self.strings: dict[str, Any] = {}
        self.closures: dict[str, Any] = {}
        self.counter = 0
        self.b: ir.IRBuilder = None  # type: ignore[assignment]
        self.st: FnState = None  # type: ignore[assignment]

    # ------------------------------------------------------------------ util
    def name(self, base: str) -> str:
        self.counter += 1
        return f"{base}.{self.counter}"

    def runtime(self, name: str, nargs: int, ret: Any = I64, args: list[Any] | None = None) -> ir.Function:
        if name not in self.rt:
            ft = ir.FunctionType(ret, args if args is not None else [I64] * nargs)
            self.rt[name] = ir.Function(self.mod, ft, name=name)
        return self.rt[name]

    def call_rt(self, name: str, *args: Any, ret: Any = I64, types: list[Any] | None = None) -> Any:
        f = self.runtime(name, len(args), ret, types)
        return self.b.call(f, list(args))

    def conc(self, t: Type) -> Type:
        return resolve(subst(t, self.st.sub))

    def ty(self, e: Any) -> Type:
        return self.conc(e.ty)

    def inc(self, v: Any, t: Type) -> None:
        if is_heap(t):
            self.call_rt("uu_inc", v, ret=VOID)

    def dec(self, v: Any, t: Type) -> None:
        if is_heap(t):
            self.call_rt("uu_dec", v, ret=VOID)

    def const(self, n: int) -> Any:
        return ir.Constant(I64, n)

    def to_ptr(self, v: Any, ty: Any = None) -> Any:
        return self.b.inttoptr(v, ir.PointerType(ty or I64))

    def field_ptr(self, obj: Any, i: int) -> Any:
        # Obj: rc(8) kind(4) tag(4) n(4) aux(4) mask(8) => f[] em +32
        p = self.to_ptr(obj)
        return self.b.gep(p, [self.const(4 + i)])

    def load_field(self, obj: Any, i: int) -> Any:
        return self.b.load(self.field_ptr(obj, i))

    def load_tag(self, obj: Any) -> Any:
        p = self.b.inttoptr(obj, ir.PointerType(I32))
        tagp = self.b.gep(p, [ir.Constant(I32, 3)])
        return self.b.zext(self.b.load(tagp), I64)

    # ------------------------------------------------------------------ programa
    def program(self, main: FnInfo) -> str:
        for n in range(4):
            self.gen_apply(n)
        entry = self.instance(main, [])
        f = ir.Function(self.mod, ir.FunctionType(I32, []), name="main")
        b = ir.IRBuilder(f.append_basic_block("entry"))
        self.b = b
        p = b.call(entry, [], cconv="tailcc")
        code = b.call(self.runtime("uu_run", 1, I32), [p])
        b.ret(code)
        while self.work:
            item = self.work.pop()
            self.gen_body(*item)
        return str(self.mod)

    def gen_apply(self, n: int) -> None:
        """uu_applyN(clo, args...) em convenção C, chamando o ponteiro tailcc da closure."""
        f = ir.Function(self.mod, ir.FunctionType(I64, [I64] * (n + 1)), name=f"uu_apply{n}")
        b = ir.IRBuilder(f.append_basic_block("entry"))
        clo = f.args[0]
        p = b.inttoptr(clo, ir.PointerType(I64))
        fp = b.load(b.gep(p, [ir.Constant(I64, 4)]))
        fty = ir.FunctionType(I64, [I64] * (n + 1))
        fn = b.inttoptr(fp, fty.as_pointer())
        r = b.call(fn, list(f.args), cconv="tailcc")
        b.ret(r)

    # ------------------------------------------------------------------ instâncias
    def instance(self, fi: FnInfo, inst: list[Type]) -> ir.Function:
        inst = [resolve(t) for t in inst]
        k = (fi.uid, tuple(key(t) for t in inst))
        if k in self.instances:
            return self.instances[k]
        if fi.scheme is not None:
            sub = {v.id: t for v, t in zip(fi.scheme.vars, inst)}
            ft = resolve(subst(fi.scheme.type, sub))
        else:
            sub = {}
            assert fi.mono is not None
            ft = resolve(fi.mono)
        nparams = len(fi.params)
        name = fi.symbol + ("" if not inst else "<" + ",".join(k[1]) + ">")
        f = ir.Function(self.mod, ir.FunctionType(I64, [I64] * nparams), name=self.name(name))
        f.calling_convention = "tailcc"
        self.instances[k] = f
        self.work.append((fi, f, sub, ft))
        return f

    def gen_body(self, fi: FnInfo, f: ir.Function, sub: dict[int, Type], ft: Type) -> None:
        saved = (self.b, self.st)
        self.start_fn(f, FnState(f, sub, scopes=[[]]))
        self.st.ret_ty = ft.ret if isinstance(ft, TFn) else ft
        for p, arg, pt in zip(fi.params, f.args, ft.params if isinstance(ft, TFn) else []):
            lv: LocalVar = p.local  # type: ignore[attr-defined]
            if p.mode == "mut":
                self.st.locals[lv.id] = Local("ref", arg, pt, owned=False)
            else:
                slot = self.new_slot(p.name)
                self.b.store(arg, slot)
                self.st.locals[lv.id] = Local("slot", slot, pt)
                self.st.scopes[-1].append((slot, pt))
        if fi.prim is not None:
            self.gen_prim(fi, f, ft)
        else:
            assert fi.body is not None
            self.tail(fi.body)
        self.finish_fn()
        self.b, self.st = saved

    # ------------------------------------------------------------------ retorno / cauda
    def cleanup_all(self) -> None:
        for scope in reversed(self.st.scopes):
            for slot, t in reversed(scope):
                if is_heap(t):
                    self.dec(self.b.load(slot), t)
        if self.st.env is not None:
            self.call_rt("uu_dec", self.st.env, ret=VOID)

    def ret(self, v: Any) -> None:
        self.cleanup_all()
        self.b.ret(v)

    def tail(self, e: A.Expr) -> None:
        """Gera `e` em posição de cauda: emite o retorno."""
        if self.b.block.is_terminated:
            return
        if isinstance(e, A.Block):
            self.push()
            for s in e.stmts:
                self.stmt(s)
                if self.b.block.is_terminated:
                    return
            if e.result is None:
                self.ret(self.const(0))
            else:
                self.tail(e.result)
            self.st.scopes.pop()
            return
        if isinstance(e, A.If) and e.els is not None:
            c = self.cond(e.cond)
            tb = self.b.append_basic_block("then")
            eb = self.b.append_basic_block("else")
            self.b.cbranch(c, tb, eb)
            self.b.position_at_end(tb)
            self.scoped_tail(e.then)
            self.b.position_at_end(eb)
            self.scoped_tail(e.els)
            return
        if isinstance(e, A.Match):
            self.match(e, tail=True)
            return
        if isinstance(e, A.BuiltinCall) and e.name == "tail":
            self.tail(e.args[0])
            return
        if isinstance(e, (A.Call, A.BinOp)) and getattr(e, "tail", False):
            r = self.call_expr(e, tail=True)
            if r is not None and not self.b.block.is_terminated:
                self.ret(r)
            return
        v = self.expr(e)
        if not self.b.block.is_terminated:
            self.ret(v)

    def scoped_tail(self, e: A.Expr) -> None:
        self.push()
        self.tail(e)
        self.st.scopes.pop()

    def push(self) -> None:
        self.st.scopes.append([])

    def pop(self) -> None:
        scope = self.st.scopes.pop()
        if self.b.block.is_terminated:
            return
        for slot, t in reversed(scope):
            if is_heap(t):
                self.dec(self.b.load(slot), t)

    def new_slot(self, name: str = "") -> Any:
        # allocas sempre no bloco de entrada (que salta para o corpo no fim)
        b = ir.IRBuilder(self.st.allocas)
        return b.alloca(I64, name=name)

    def start_fn(self, f: ir.Function, st: "FnState") -> None:
        st.allocas = f.append_basic_block("allocas")
        body = f.append_basic_block("body")
        self.st = st
        self.b = ir.IRBuilder(body)

    def finish_fn(self) -> None:
        b = ir.IRBuilder(self.st.allocas)
        b.branch(self.st.fn.basic_blocks[1])

    def bind_local(self, lv: LocalVar, v: Any, t: Type) -> None:
        slot = self.new_slot(lv.name)
        self.b.store(v, slot)
        self.st.locals[lv.id] = Local("slot", slot, t)
        self.st.scopes[-1].append((slot, t))

    def temp(self, v: Any, t: Type) -> Any:
        slot = self.new_slot("tmp")
        self.b.store(v, slot)
        self.st.scopes[-1].append((slot, t))
        return slot

    # ------------------------------------------------------------------ instruções
    def stmt(self, s: A.Stmt) -> None:
        if isinstance(s, A.ExprStmt):
            v = self.expr(s.expr)
            if not self.b.block.is_terminated:
                self.dec(v, self.ty(s.expr))
            return
        if isinstance(s, A.Let):
            v = self.expr(s.expr)
            t = self.ty(s.expr)
            if getattr(s, "assign", False):
                self.assign_pat(s.pat, v, t)
            else:
                self.bind_pat(s.pat, v, t)
            return
        if isinstance(s, A.Assign):
            self.assign_field(s)
            return
        raise NotImplementedError(type(s).__name__)

    def local_addr(self, lv: LocalVar) -> tuple[Any, Type]:
        loc = self.st.locals.get(lv.id)
        if loc is None:
            raise KeyError(lv.name)
        if loc.kind == "slot":
            return loc.ptr, loc.ty
        if loc.kind == "ref":
            return self.to_ptr(loc.ptr), loc.ty
        if loc.kind == "envref":
            return self.to_ptr(self.load_field(self.st.env, loc.ptr)), loc.ty
        raise KeyError(lv.name)

    def assign_pat(self, p: A.Pat, v: Any, t: Type) -> None:
        if isinstance(p, A.PVar):
            ptr, lt = self.local_addr(p.local)  # type: ignore[attr-defined]
            old = self.b.load(ptr)
            self.b.store(v, ptr)
            self.dec(old, lt)
        elif isinstance(p, A.PTuple):
            tt = prune(t)
            assert isinstance(tt, TCon)
            for i, (sp, st_) in enumerate(zip(p.items, tt.args)):
                fv = self.load_field(v, i)
                self.inc(fv, st_)
                self.assign_pat(sp, fv, st_)
            self.dec(v, t)

    def assign_field(self, s: A.Assign) -> None:
        tgt = s.target
        assert isinstance(tgt, A.Dot) and isinstance(tgt.obj, A.Name)
        ctor, idx = tgt.ref.target  # type: ignore[attr-defined]
        lv = tgt.obj.ref.target  # type: ignore[attr-defined]
        ptr, rt = self.local_addr(lv)
        newv = self.expr(s.expr)
        old = self.b.load(ptr)
        upd = self.with_update(old, rt, ctor, {idx: (newv, self.ty(s.expr))})
        self.b.store(upd, ptr)

    def bind_pat(self, p: A.Pat, v: Any, t: Type) -> None:
        """Liga um padrão irrefutável a um valor próprio."""
        if isinstance(p, A.PVar):
            self.bind_local(p.local, v, t)  # type: ignore[attr-defined]
            return
        if isinstance(p, A.PWild):
            self.dec(v, t)
            return
        tt = prune(t)
        if isinstance(p, A.PTuple):
            assert isinstance(tt, TCon)
            for i, (sp, st_) in enumerate(zip(p.items, tt.args)):
                fv = self.load_field(v, i)
                self.inc(fv, st_)
                self.bind_pat(sp, fv, st_)
            self.dec(v, t)
            return
        if isinstance(p, A.PCtor):
            ctor: CtorInfo = p.ctor  # type: ignore[attr-defined]
            ftys = self.ctor_field_types(ctor, tt)
            for i, (sp, st_) in enumerate(zip(p.args, ftys)):
                fv = self.load_field(v, i)
                self.inc(fv, st_)
                self.bind_pat(sp, fv, st_)
            self.dec(v, t)
            return
        self.dec(v, t)

    def ctor_field_types(self, ctor: CtorInfo, t: Type) -> list[Type]:
        t = prune(t)
        args = t.args if isinstance(t, TCon) else []
        mp = {v.id: a for v, a in zip(ctor.type.vars, args)}
        return [resolve(subst(ft, mp)) for ft in ctor.field_types]

    # ------------------------------------------------------------------ expressões
    def expr(self, e: A.Expr) -> Any:
        m = getattr(self, "x_" + type(e).__name__)
        return m(e)

    def cond(self, e: A.Expr) -> Any:
        v = self.expr(e)
        return self.b.trunc(v, I1)

    def x_Lit(self, e: A.Lit) -> Any:
        t = self.ty(e)
        if e.kind in ("int", "float"):
            assert isinstance(t, TCon)
            return self.num_const(e.value, t.name)
        if e.kind == "bool":
            return self.const(1 if e.value else 0)
        return self.const(0)

    def num_const(self, value: Any, tname: str) -> Any:
        if tname == "f64":
            return self.b.bitcast(ir.Constant(F64, float(value)), I64)
        if tname == "f32":
            v = self.b.bitcast(ir.Constant(F32, float(value)), I32)
            return self.b.zext(v, I64)
        bits = int_bits(tname)
        v = int(value) & ((1 << bits) - 1)
        if tname.startswith("i") and v >= 1 << (bits - 1):
            v -= 1 << bits
        return self.const(v)

    def text_const(self, s: str) -> Any:
        if s not in self.strings:
            data = s.encode("utf-8")
            n = len(data)
            sty = ir.LiteralStructType([I64, I32, I32, I32, I32, I64, ir.ArrayType(I8, n + 1)])
            g = ir.GlobalVariable(self.mod, sty, name=self.name("str"))
            g.global_constant = False
            g.linkage = "private"
            g.initializer = ir.Constant(sty, [
                ir.Constant(I64, -1), ir.Constant(I32, K_TEXT), ir.Constant(I32, 0), ir.Constant(I32, n),
                ir.Constant(I32, 0), ir.Constant(I64, 0), ir.Constant(ir.ArrayType(I8, n + 1), bytearray(data + b"\0")),
            ])
            self.strings[s] = g
        return self.b.ptrtoint(self.strings[s], I64)

    def x_Str(self, e: A.Str) -> Any:
        acc = None
        for p in e.parts:
            if isinstance(p, str):
                if not p:
                    continue
                v = self.text_const(p)
            else:
                xv = self.expr(p.expr)
                spec = self.spec_value(p.spec_val)  # type: ignore[attr-defined]
                f = self.resolve_ref(p.ref)  # type: ignore[attr-defined]
                v = self.b.call(f, [xv, spec], cconv="tailcc")
            acc = v if acc is None else self.call_rt("uu_text_concat", acc, v)
        return acc if acc is not None else self.text_const("")

    def spec_value(self, sv: tuple[int, ...]) -> Any:
        o = self.call_rt("uu_alloc", self.const(K_DATA), self.const(0), self.const(5), self.const(0))
        for i, x in enumerate(sv):
            self.b.store(self.const(x), self.field_ptr(o, i))
        return o

    def x_Name(self, e: A.Name) -> Any:
        ref = e.ref  # type: ignore[attr-defined]
        if getattr(e, "move", False) and ref.kind == "local":
            loc = self.st.locals.get(ref.target.id)
            if loc is not None and loc.kind == "slot":
                # move: a referência sai da ligação sem incremento
                v = self.b.load(loc.ptr)
                self.b.store(self.const(0), loc.ptr)
                return v
        return self.ref_value(ref, self.ty(e))

    def load_local(self, lv: LocalVar) -> tuple[Any, Type]:
        loc = self.st.locals.get(lv.id)
        if loc is None:
            raise KeyError(f"local não encontrado: {lv.name}")
        if loc.kind == "env":
            v = self.load_field(self.st.env, loc.ptr)
            return v, loc.ty
        ptr, t = self.local_addr(lv)
        return self.b.load(ptr), t

    def ref_value(self, ref: Ref, t: Type) -> Any:
        if ref.kind == "local":
            v, lt = self.load_local(ref.target)
            self.inc(v, lt)
            return v
        if ref.kind == "global":
            f = self.instance(ref.target, ref.inst)
            return self.b.call(f, [], cconv="tailcc")
        if ref.kind == "ctor":
            c: CtorInfo = ref.target
            if c.arity == 0:
                return self.call_rt("uu_nullary", self.const(c.index))
            return self.fn_closure(("ctor", c, tuple(key(self.conc(x)) for x in ref.inst)), t,
                                   lambda args: self.construct(c, args, self.conc(prune(t).ret)))  # type: ignore[union-attr]
        f = self.resolve_ref(ref)
        return self.fn_closure(("fn", f.name), t, lambda args: self.b.call(f, args, cconv="tailcc"))

    def fn_closure(self, k: Any, t: Type, body: Any) -> Any:
        """Closure imortal estática para uma função conhecida."""
        name = repr(k)
        if name not in self.closures:
            pt = prune(t)
            n = len(pt.params) if isinstance(pt, TFn) else 0
            w = ir.Function(self.mod, ir.FunctionType(I64, [I64] * (n + 1)), name=self.name("clo"))
            w.calling_convention = "tailcc"
            saved = (self.b, self.st)
            self.start_fn(w, FnState(w, {}, scopes=[[]]))
            r = body(list(w.args[1:]))
            self.b.ret(r)
            self.finish_fn()
            self.b, self.st = saved
            sty = ir.LiteralStructType([I64, I32, I32, I32, I32, I64, I64])
            g = ir.GlobalVariable(self.mod, sty, name=self.name("cloobj"))
            g.linkage = "private"
            g.initializer = ir.Constant(sty, [
                ir.Constant(I64, -1), ir.Constant(I32, K_CLOS), ir.Constant(I32, 0), ir.Constant(I32, 1),
                ir.Constant(I32, 0), ir.Constant(I64, 0), w.ptrtoint(I64),
            ])
            self.closures[name] = g
        return self.b.ptrtoint(self.closures[name], I64)

    # ------------------------------------------------------------------ resolução de refs
    def resolve_ref(self, ref: Ref) -> ir.Function:
        if ref.kind == "fn":
            return self.instance(ref.target, [self.conc(t) for t in ref.inst])
        if ref.kind == "method":
            return self.method_instance(ref.target, [self.conc(t) for t in ref.inst])
        raise NotImplementedError(ref.kind)

    def method_instance(self, mi: MethodInfo, inst: list[Type]) -> ir.Function:
        param = inst[0]
        h = head_name(param)
        impl = self.ck.find_impl(mi.trait.name, h)
        if impl is None:
            if self.ck.derivable(mi.trait.name, param):
                return self.derived(mi, inst)
            raise CodegenError(f"sem impl de {mi.trait.name} para {param}")
        fi = impl.methods.get(mi.name)
        if fi is None:
            assert mi.default is not None
            return self.instance(mi.default, inst)
        mp: dict[int, Type] = {}
        match_type(impl.type, param, mp)
        ivals = [mp.get(v.id, TCon("()")) for v in impl.vars]
        return self.instance(fi, ivals + inst[1:])

    # ------------------------------------------------------------------ chamadas
    def x_Call(self, e: A.Call) -> Any:
        return self.call_expr(e, tail=False)

    def x_BinOp(self, e: A.BinOp) -> Any:
        if e.op in ("&&", "||"):
            l = self.expr(e.left)
            res = self.new_slot("sc")
            self.b.store(l, res)
            rb = self.b.append_basic_block("rhs")
            done = self.b.append_basic_block("sc_done")
            c = self.b.trunc(l, I1)
            if e.op == "&&":
                self.b.cbranch(c, rb, done)
            else:
                self.b.cbranch(c, done, rb)
            self.b.position_at_end(rb)
            r = self.expr(e.right)
            self.b.store(r, res)
            self.b.branch(done)
            self.b.position_at_end(done)
            return self.b.load(res)
        return self.call_expr(e, tail=False)

    def args_of(self, e: Any) -> list[A.Expr]:
        if isinstance(e, A.BinOp):
            return [e.left, e.right]
        return e.args

    def gen_args(self, args: list[A.Expr]) -> list[Any]:
        out = []
        for a in args:
            if isinstance(a, A.Bang):
                lv = a.expr.ref.target  # type: ignore[attr-defined]
                ptr, _ = self.local_addr(lv)
                out.append(self.b.ptrtoint(ptr, I64))
            else:
                out.append(self.expr(a))
        return out

    def call_expr(self, e: Any, tail: bool) -> Any:
        ref = getattr(e, "ref", None) if isinstance(e, A.BinOp) else getattr(e.fn, "ref", None)
        args_e = self.args_of(e)
        has_bang = any(isinstance(a, A.Bang) for a in args_e)
        if ref is not None and ref.kind in ("fn", "method"):
            f = self.resolve_ref(ref)
            args = self.gen_args(args_e)
            return self.emit_call(f, args, tail and not has_bang)
        if ref is not None and ref.kind == "ctor":
            c: CtorInfo = ref.target
            args = self.gen_args(args_e)
            return self.construct(c, args, self.ty(e))
        # valor de função (closure)
        fn_e = e.fn if isinstance(e, A.Call) else None
        clo = self.expr(fn_e) if fn_e is not None else self.ref_value(e.ref, TFn([], TCon("()")))
        args = self.gen_args(args_e)
        p = self.to_ptr(clo)
        fp = self.b.load(self.b.gep(p, [self.const(4)]))
        fty = ir.FunctionType(I64, [I64] * (len(args) + 1))
        fn = self.b.inttoptr(fp, fty.as_pointer())
        return self.emit_call(fn, [clo] + args, tail and not has_bang)

    def emit_call(self, f: Any, args: list[Any], tail: bool) -> Any:
        if tail:
            self.cleanup_all()
            r = self.b.call(f, args, cconv="tailcc", tail="musttail")
            self.b.ret(r)
            return None
        return self.b.call(f, args, cconv="tailcc")

    def construct(self, c: CtorInfo, args: list[Any], t: Type) -> Any:
        if c.arity == 0:
            return self.call_rt("uu_nullary", self.const(c.index))
        ftys = self.ctor_field_types(c, t)
        return self.alloc_data(c.index, args, ftys)

    def alloc_data(self, tag: int, vals: list[Any], tys: list[Type]) -> Any:
        mask = 0
        for i, t in enumerate(tys):
            if is_heap(t):
                mask |= 1 << i
        if mask >= 1 << 63:
            mask -= 1 << 64
        o = self.call_rt("uu_alloc", self.const(K_DATA), self.const(tag), self.const(len(vals)), self.const(mask))
        for i, v in enumerate(vals):
            self.b.store(v, self.field_ptr(o, i))
        return o

    # ------------------------------------------------------------------ demais expressões
    def x_Dot(self, e: A.Dot) -> Any:
        ref: Ref = e.ref  # type: ignore[attr-defined]
        if ref.kind == "field":
            obj = self.expr(e.obj)
            ctor, idx = ref.target
            ot = self.ty(e.obj)
            ft = self.ty(e)
            v = self.load_field(obj, idx)
            self.inc(v, ft)
            self.dec(obj, ot)
            return v
        # pós-fixa
        if ref.kind == "local":
            clo = self.ref_value(ref, TFn([], TCon("()")))
            arg = self.expr(e.obj)
            fp = self.b.load(self.b.gep(self.to_ptr(clo), [self.const(4)]))
            fn = self.b.inttoptr(fp, ir.FunctionType(I64, [I64, I64]).as_pointer())
            return self.b.call(fn, [clo, arg], cconv="tailcc")
        f = self.resolve_ref(ref)
        arg = self.expr(e.obj)
        return self.b.call(f, [arg], cconv="tailcc")

    def x_Block(self, e: A.Block) -> Any:
        self.push()
        for s in e.stmts:
            self.stmt(s)
        v = self.expr(e.result) if e.result is not None else self.const(0)
        self.pop()
        return v

    def x_If(self, e: A.If) -> Any:
        res = self.new_slot("if")
        c = self.cond(e.cond)
        tb = self.b.append_basic_block("then")
        eb = self.b.append_basic_block("else")
        done = self.b.append_basic_block("endif")
        self.b.cbranch(c, tb, eb)
        self.b.position_at_end(tb)
        self.push()
        v = self.expr(e.then)
        if e.els is None:
            self.dec(v, self.ty(e.then))
            v = self.const(0)
        if not self.b.block.is_terminated:
            self.b.store(v, res)
        self.pop()
        if not self.b.block.is_terminated:
            self.b.branch(done)
        self.b.position_at_end(eb)
        self.push()
        v = self.expr(e.els) if e.els is not None else self.const(0)
        if not self.b.block.is_terminated:
            self.b.store(v, res)
        self.pop()
        if not self.b.block.is_terminated:
            self.b.branch(done)
        self.b.position_at_end(done)
        return self.b.load(res)

    def x_While(self, e: A.While) -> Any:
        head = self.b.append_basic_block("while")
        body = self.b.append_basic_block("body")
        done = self.b.append_basic_block("endwhile")
        self.b.branch(head)
        self.b.position_at_end(head)
        c = self.cond(e.cond)
        self.b.cbranch(c, body, done)
        self.b.position_at_end(body)
        self.push()
        v = self.expr(e.body)
        self.dec(v, self.ty(e.body))
        self.pop()
        self.b.branch(head)
        self.b.position_at_end(done)
        return self.const(0)

    def x_Tuple(self, e: A.Tuple) -> Any:
        vals = [self.expr(x) for x in e.items]
        return self.alloc_data(0, vals, [self.ty(x) for x in e.items])

    def x_With(self, e: A.With) -> Any:
        base = self.expr(e.expr)
        t = self.ty(e.expr)
        ctor = e.ctor  # type: ignore[attr-defined]
        upd = {}
        for name, val, _ in e.fields:
            idx = ctor.field_names.index(name)
            upd[idx] = (self.expr(val), self.ty(val))
        return self.with_update(base, t, ctor, upd)

    def with_update(self, base: Any, t: Type, ctor: CtorInfo, upd: dict[int, tuple[Any, Type]]) -> Any:
        ftys = self.ctor_field_types(ctor, t)
        vals = []
        for i, ft in enumerate(ftys):
            if i in upd:
                vals.append(upd[i][0])
            else:
                v = self.load_field(base, i)
                self.inc(v, ft)
                vals.append(v)
        o = self.alloc_data(0, vals, ftys)
        self.dec(base, t)
        return o

    def x_TryOp(self, e: A.TryOp) -> Any:
        v = self.expr(e.expr)
        f = self.resolve_ref(e.ref)  # type: ignore[attr-defined]
        either = self.b.call(f, [v], cconv="tailcc")
        tag = self.load_tag(either)
        left = self.b.append_basic_block("try_ok")
        right = self.b.append_basic_block("try_ret")
        self.b.cbranch(self.b.icmp_unsigned("==", tag, self.const(0)), left, right)
        self.b.position_at_end(right)
        r = self.load_field(either, 0)
        self.call_rt("uu_inc", r, ret=VOID)
        self.call_rt("uu_dec", either, ret=VOID)
        self.ret(r)
        self.b.position_at_end(left)
        x = self.load_field(either, 0)
        t = self.ty(e)
        self.inc(x, t)
        self.call_rt("uu_dec", either, ret=VOID)
        return x

    def x_BuiltinCall(self, e: A.BuiltinCall) -> Any:
        if e.name == "tail":
            return self.expr(e.args[0])
        if e.name == "debug":
            v = self.expr(e.args[0])
            if self.release:
                return v
            t = self.ty(e.args[0])
            self.inc(v, t)
            f = self.resolve_ref(e.ref)  # type: ignore[attr-defined]
            txt = self.b.call(f, [v, self.spec_value((32, 0, 0, -1, 0))], cconv="tailcc")
            self.call_rt("uu_debug", txt)
            return v
        raise CodegenError(f"builtin @{e.name} não suportado no gerador")

    # ------------------------------------------------------------------ lambdas
    def x_Lambda(self, e: A.Lambda) -> Any:
        caps: list[LocalVar] = getattr(e, "captures", [])
        byref = getattr(e, "byref", False)
        ft = self.ty(e)
        assert isinstance(ft, TFn)
        n = len(e.params)
        lf = ir.Function(self.mod, ir.FunctionType(I64, [I64] * (n + 1)), name=self.name("lambda"))
        lf.calling_convention = "tailcc"
        # monta o env no chamador
        vals: list[Any] = [lf.ptrtoint(I64)]
        tys: list[Type] = [TCon("()")]
        layout: list[tuple[LocalVar, str]] = []
        for lv in caps:
            loc = self.st.locals.get(lv.id)
            if loc is None:
                continue
            if byref and lv.assignable or loc.kind in ("ref", "envref") and byref:
                ptr, t = self.local_addr(lv)
                vals.append(self.b.ptrtoint(ptr, I64))
                tys.append(TCon("()"))
                layout.append((lv, "envref"))
            else:
                v, t = self.load_local(lv)
                self.inc(v, t)
                vals.append(v)
                tys.append(t)
                layout.append((lv, "env"))
        mask = 0
        for i, t in enumerate(tys):
            if is_heap(t):
                mask |= 1 << i
        clo = self.call_rt("uu_alloc", self.const(K_CLOS), self.const(0), self.const(len(vals)), self.const(mask))
        for i, v in enumerate(vals):
            self.b.store(v, self.field_ptr(clo, i))
        # corpo
        saved = (self.b, self.st)
        st = FnState(lf, self.st.sub, scopes=[[]], lam=e)
        st.env = lf.args[0]
        self.start_fn(lf, st)
        for i, (lv, kind) in enumerate(layout):
            st.locals[lv.id] = Local(kind, i + 1, saved[1].locals[lv.id].ty, owned=False)
        for p, arg, pt in zip(e.params, lf.args[1:], ft.params):
            lv = p.local  # type: ignore[attr-defined]
            if p.mode == "mut":
                st.locals[lv.id] = Local("ref", arg, pt, owned=False)
            else:
                slot = self.new_slot(p.name)
                self.b.store(arg, slot)
                st.locals[lv.id] = Local("slot", slot, pt)
                st.scopes[-1].append((slot, pt))
        self.tail(e.body)
        self.finish_fn()
        self.b, self.st = saved
        return clo

    def x_ListLit(self, e: A.ListLit) -> Any:
        t = self.ty(e)
        assert isinstance(t, TCon)
        el = t.args[0]
        lst = self.call_rt("uu_list_new", self.const(len(e.items)), self.const(1 if is_heap(el) else 0))
        slot = self.temp(lst, t)
        for i, x in enumerate(e.items):
            v = self.expr(x)
            self.b.store(v, self.field_ptr(self.b.load(slot), i))
        self.st.scopes[-1].remove((slot, t))
        return self.b.load(slot)

    def x_MapLit(self, e: A.MapLit) -> Any:
        t = self.ty(e)
        assert isinstance(t, TCon)
        kt, vt = t.args
        pt = TCon("#", [kt, vt])
        lst = self.call_rt("uu_list_new", self.const(len(e.pairs)), self.const(1))
        lt = TCon("List", [pt])
        slot = self.temp(lst, lt)
        for i, (k, v) in enumerate(e.pairs):
            kv, vv = self.expr(k), self.expr(v)
            pair = self.alloc_data(0, [kv, vv], [kt, vt])
            self.b.store(pair, self.field_ptr(self.b.load(slot), i))
        self.st.scopes[-1].remove((slot, lt))
        return self.alloc_data(0, [self.b.load(slot)], [lt])

    def x_LazyE(self, e: A.LazyE) -> Any:
        raise CodegenError("lazy ainda não suportado")

    # ------------------------------------------------------------------ match
    def x_Match(self, e: A.Match) -> Any:
        return self.match(e, tail=False)

    def match(self, e: A.Match, tail: bool) -> Any:
        st = self.ty(e.scrut)
        sv = self.expr(e.scrut)
        self.push()
        sslot = self.temp(sv, st)
        res = None if tail else self.new_slot("match")
        done = None if tail else self.b.append_basic_block("endmatch")
        for arm in e.arms:
            nxt = self.b.append_basic_block("arm_next")
            self.push()
            self.test_pat(arm.pat, self.b.load(sslot), st, nxt)
            self.bind_match(arm.pat, self.b.load(sslot), st)
            if arm.guard is not None:
                g = self.cond(arm.guard)
                ok = self.b.append_basic_block("guard_ok")
                fail = self.b.append_basic_block("guard_fail")
                self.b.cbranch(g, ok, fail)
                self.b.position_at_end(fail)
                # libera ligações do padrão antes de tentar o próximo caso
                for slot, t in reversed(self.st.scopes[-1]):
                    self.dec(self.b.load(slot), t)
                self.b.branch(nxt)
                self.b.position_at_end(ok)
            if tail:
                self.tail(arm.body)
                self.st.scopes.pop()
            else:
                v = self.expr(arm.body)
                if not self.b.block.is_terminated:
                    self.b.store(v, res)
                self.pop()
                if not self.b.block.is_terminated:
                    self.b.branch(done)
            self.b.position_at_end(nxt)
        self.call_rt("uu_panic_c", self.cstr("match sem caso correspondente"), ret=VOID, types=[PTR])
        self.b.unreachable()
        if tail:
            self.st.scopes.pop()
            return None
        self.b.position_at_end(done)
        v = self.b.load(res)
        self.pop()
        return v

    def cstr(self, s: str) -> Any:
        data = bytearray(s.encode() + b"\0")
        g = ir.GlobalVariable(self.mod, ir.ArrayType(I8, len(data)), name=self.name("cstr"))
        g.linkage = "private"
        g.global_constant = True
        g.initializer = ir.Constant(ir.ArrayType(I8, len(data)), data)
        return self.b.bitcast(g, PTR)

    def test_pat(self, p: A.Pat, v: Any, t: Type, fail: Any) -> None:
        if isinstance(p, (A.PWild, A.PVar)):
            return
        tt = prune(t)
        if isinstance(p, A.PLit):
            if p.kind == "str":
                eq = self.call_rt("uu_text_eq_borrow", v, self.text_const(p.value))
                c = self.b.trunc(eq, I1)
            else:
                assert isinstance(tt, TCon)
                lit = self.num_const(p.value, tt.name) if p.kind != "bool" else self.const(int(p.value))
                c = self.b.icmp_unsigned("==", v, lit)
            ok = self.b.append_basic_block("lit_ok")
            self.b.cbranch(c, ok, fail)
            self.b.position_at_end(ok)
            return
        if isinstance(p, A.PTuple):
            assert isinstance(tt, TCon)
            for i, (sp, st_) in enumerate(zip(p.items, tt.args)):
                self.test_pat(sp, self.load_field(v, i), st_, fail)
            return
        if isinstance(p, A.PCtor):
            ctor: CtorInfo = p.ctor  # type: ignore[attr-defined]
            if ctor.type.kind == "sum" and len(ctor.type.ctors) > 1:
                tag = self.load_tag(v)
                ok = self.b.append_basic_block("tag_ok")
                self.b.cbranch(self.b.icmp_unsigned("==", tag, self.const(ctor.index)), ok, fail)
                self.b.position_at_end(ok)
            for i, (sp, st_) in enumerate(zip(p.args, self.ctor_field_types(ctor, tt))):
                self.test_pat(sp, self.load_field(v, i), st_, fail)

    def bind_match(self, p: A.Pat, v: Any, t: Type) -> None:
        """Liga as variáveis do padrão (com inc) sem consumir `v`."""
        if isinstance(p, A.PVar):
            self.inc(v, t)
            self.bind_local(p.local, v, t)  # type: ignore[attr-defined]
            return
        tt = prune(t)
        if isinstance(p, A.PTuple):
            assert isinstance(tt, TCon)
            for i, (sp, st_) in enumerate(zip(p.items, tt.args)):
                self.bind_match(sp, self.load_field(v, i), st_)
        elif isinstance(p, A.PCtor):
            ctor: CtorInfo = p.ctor  # type: ignore[attr-defined]
            for i, (sp, st_) in enumerate(zip(p.args, self.ctor_field_types(ctor, tt))):
                self.bind_match(sp, self.load_field(v, i), st_)

    # ------------------------------------------------------------------ primitivas
    def gen_prim(self, fi: FnInfo, f: ir.Function, ft: Type) -> None:
        assert isinstance(ft, TFn)
        name = fi.prim
        args = [self.b.load(self.st.locals[p.local.id].ptr) for p in fi.params]  # type: ignore[attr-defined]
        # primitivas consomem os argumentos: esvazia a lista de limpeza
        self.st.scopes = [[]]
        pts = ft.params
        b = self.b
        if name and name.startswith("op_"):
            self.b.ret(self.num_op(name[3:], args, pts[0]))
            return
        if name == "conv":
            self.b.ret(self.convert(args[0], pts[0], ft.ret))
            return
        if name == "fmt_num":
            t = prune(pts[0])
            assert isinstance(t, TCon)
            if t.name == "f64":
                d = b.bitcast(args[0], F64)
                b.ret(self.call_rt("uu_fmt_f64", d, args[1], types=[F64, I64]))
            elif t.name == "f32":
                d = b.fpext(b.bitcast(b.trunc(args[0], I32), F32), F64)
                b.ret(self.call_rt("uu_fmt_f32", d, args[1], types=[F64, I64]))
            elif t.name.startswith("u"):
                b.ret(self.call_rt("uu_fmt_u64", args[0], args[1]))
            else:
                b.ret(self.call_rt("uu_fmt_i64", args[0], args[1]))
            return
        if name in ("list_map", "list_bind", "list_pure"):
            rt = prune(ft.ret)
            assert isinstance(rt, TCon)
            b.ret(self.call_rt("uu_" + name, *args, self.const(1 if is_heap(rt.args[0]) else 0)))
            return
        if name == "text_len":
            b.ret(self.call_rt("uu_text_len_prim", *args))
            return
        if name in ("fmt_text", "text_concat", "text_eq", "req_print", "list_len", "list_get", "list_at",
                    "list_push", "list_set", "list_concat", "list_fold", "list_each", "list_filter",
                    "list_range"):
            b.ret(self.call_rt("uu_" + name, *args))
            return
        if name == "panic":
            self.call_rt("uu_panic", args[0], ret=VOID)
            b.unreachable()
            return
        if name in ("pending_pure", "pending_map", "pending_bind"):
            rt = prune(ft.ret)
            assert isinstance(rt, TCon)
            flag = self.const(1 if is_heap(rt.args[0]) else 0)
            b.ret(self.call_rt("uu_" + name, *args, flag))
            return
        raise CodegenError(f"primitiva desconhecida: {name}")

    def num_op(self, op: str, args: list[Any], t: Type) -> Any:
        t = prune(t)
        assert isinstance(t, TCon)
        n = t.name
        b = self.b
        if n == "bool" or n == "()":
            if op == "eq":
                return b.zext(b.icmp_unsigned("==", args[0], args[1]), I64)
            if op == "ne":
                return b.zext(b.icmp_unsigned("!=", args[0], args[1]), I64)
            raise CodegenError(op)
        if n in FLOAT_TYPES:
            ft = F64 if n == "f64" else F32
            if n == "f64":
                x, y = b.bitcast(args[0], F64), b.bitcast(args[1], F64)
            else:
                x, y = b.bitcast(b.trunc(args[0], I32), F32), b.bitcast(b.trunc(args[1], I32), F32)
            cmp = {"eq": "==", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
            if op in cmp:
                return b.zext(b.fcmp_ordered(cmp[op], x, y) if op != "ne" else b.fcmp_unordered("!=", x, y), I64)
            if op == "add":
                r = b.fadd(x, y)
            elif op == "sub":
                r = b.fsub(x, y)
            elif op == "mul":
                r = b.fmul(x, y)
            elif op == "div":
                r = b.fdiv(x, y)
            elif op == "rem":
                r = b.frem(x, y)
            elif op == "pow":
                fn = self.mod.declare_intrinsic("llvm.pow", [ft])
                r = b.call(fn, [x, y])
            else:
                raise CodegenError(op)
            if n == "f64":
                return b.bitcast(r, I64)
            return b.zext(b.bitcast(r, I32), I64)
        bits = int_bits(n)
        it = ir.IntType(bits)
        signed = n.startswith("i")
        x, y = (b.trunc(args[0], it), b.trunc(args[1], it)) if bits < 64 else (args[0], args[1])
        cmp = {"eq": "==", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
        if op in cmp:
            c = b.icmp_signed(cmp[op], x, y) if signed else b.icmp_unsigned(cmp[op], x, y)
            return b.zext(c, I64)
        if op in ("div", "rem"):
            zero = b.icmp_unsigned("==", y, ir.Constant(it, 0))
            bad = b.append_basic_block("divzero")
            good = b.append_basic_block("divok")
            b.cbranch(zero, bad, good)
            b.position_at_end(bad)
            self.call_rt("uu_panic_c", self.cstr("divisão por zero"), ret=VOID, types=[PTR])
            b.unreachable()
            b.position_at_end(good)
        if op == "add":
            r = b.add(x, y)
        elif op == "sub":
            r = b.sub(x, y)
        elif op == "mul":
            r = b.mul(x, y)
        elif op == "div":
            r = b.sdiv(x, y) if signed else b.udiv(x, y)
        elif op == "rem":
            r = b.srem(x, y) if signed else b.urem(x, y)
        elif op == "pow":
            xx = b.sext(x, I64) if signed and bits < 64 else (b.zext(x, I64) if bits < 64 else x)
            yy = b.sext(y, I64) if signed and bits < 64 else (b.zext(y, I64) if bits < 64 else y)
            r = self.call_rt("uu_pow_i64", xx, yy)
            if bits < 64:
                r = b.trunc(r, it)
        else:
            raise CodegenError(op)
        if bits < 64:
            return b.sext(r, I64) if signed else b.zext(r, I64)
        return r

    def convert(self, v: Any, src: Type, dst: Type) -> Any:
        s, d = prune(src), prune(dst)
        assert isinstance(s, TCon) and isinstance(d, TCon)
        b = self.b
        sn, dn = s.name, d.name
        if sn == dn:
            return v
        # para um valor LLVM do tipo de origem
        if sn == "f64":
            x = b.bitcast(v, F64)
        elif sn == "f32":
            x = b.bitcast(b.trunc(v, I32), F32)
        else:
            x = b.trunc(v, ir.IntType(int_bits(sn))) if int_bits(sn) < 64 else v
        if dn in FLOAT_TYPES:
            ft = F64 if dn == "f64" else F32
            if sn in FLOAT_TYPES:
                r = b.fpext(x, ft) if sn == "f32" and dn == "f64" else b.fptrunc(x, ft)
            elif sn.startswith("i"):
                r = b.sitofp(x, ft)
            else:
                r = b.uitofp(x, ft)
            return b.bitcast(r, I64) if dn == "f64" else b.zext(b.bitcast(r, I32), I64)
        bits = int_bits(dn)
        it = ir.IntType(bits)
        if sn in FLOAT_TYPES:
            r = b.fptosi(x, it) if dn.startswith("i") else b.fptoui(x, it)
        else:
            sb = int_bits(sn)
            if sb > bits:
                r = b.trunc(x, it)
            elif sb < bits:
                r = b.sext(x, it) if sn.startswith("i") else b.zext(x, it)
            else:
                r = x
        if bits < 64:
            return b.sext(r, I64) if dn.startswith("i") else b.zext(r, I64)
        return r


class _Derive:
    """Implementações derivadas estruturalmente (Eq, Format, Clone)."""

    def derived(self, mi: MethodInfo, inst: list[Type]) -> ir.Function:
        param = inst[0]
        k = ("derive", mi.trait.name, mi.name, key(param))
        if k in self.instances:
            return self.instances[k]
        if mi.name not in ("==", "format", "copy"):
            if mi.default is None:
                raise CodegenError(f"não sei derivar {mi.trait.name}.{mi.name}")
            return self.instance(mi.default, inst)
        n = len(mi.decl.params)
        f = ir.Function(self.mod, ir.FunctionType(I64, [I64] * n), name=self.name(f"derive.{mi.trait.name}"))
        f.calling_convention = "tailcc"
        self.instances[k] = f
        saved = (self.b, self.st)
        self.start_fn(f, FnState(f, {}, scopes=[[]]))
        getattr(self, "derive_" + {"==": "eq", "format": "format", "copy": "copy"}[mi.name])(mi, param, f)
        self.finish_fn()
        self.b, self.st = saved
        return f

    def concrete_ctors(self, t: Type) -> list[tuple[str, list[Type]]]:
        t = prune(t)
        assert isinstance(t, TCon)
        if t.name == "#":
            return [("#", list(t.args))]
        ti = self.ck.find_type_info(t.name)
        assert ti is not None
        return [(c.name, self.ctor_field_types(c, t)) for c in ti.ctors]

    def derive_copy(self, mi: MethodInfo, t: Type, f: ir.Function) -> None:
        self.b.ret(f.args[0])

    def derive_eq(self, mi: MethodInfo, t: Type, f: ir.Function) -> None:
        a, b_ = f.args
        b = self.b
        ctors = self.concrete_ctors(t)
        res = self.new_slot("eq")
        b.store(self.const(1), res)
        fail = b.append_basic_block("ne")
        done = b.append_basic_block("eq_done")
        if len(ctors) > 1:
            ta, tb = self.load_tag(a), self.load_tag(b_)
            ok = b.append_basic_block("tags_ok")
            b.cbranch(b.icmp_unsigned("==", ta, tb), ok, fail)
            b.position_at_end(ok)
        for i, (_, ftys) in enumerate(ctors):
            nxt = b.append_basic_block("next_ctor")
            if len(ctors) > 1:
                here = b.append_basic_block("ctor")
                b.cbranch(b.icmp_unsigned("==", self.load_tag(a), self.const(i)), here, nxt)
                b.position_at_end(here)
            for j, ft in enumerate(ftys):
                fa, fb = self.load_field(a, j), self.load_field(b_, j)
                self.inc(fa, ft)
                self.inc(fb, ft)
                g = self.method_instance(mi, [ft])
                r = b.call(g, [fa, fb], cconv="tailcc")
                cont = b.append_basic_block("field_ok")
                b.cbranch(b.trunc(r, I1), cont, fail)
                b.position_at_end(cont)
            b.branch(done)
            b.position_at_end(nxt)
            if len(ctors) == 1:
                break
        if not b.block.is_terminated:
            b.branch(done)
        b.position_at_end(fail)
        b.store(self.const(0), res)
        b.branch(done)
        b.position_at_end(done)
        self.dec(a, t)
        self.dec(b_, t)
        b.ret(b.load(res))

    def derive_format(self, mi: MethodInfo, t: Type, f: ir.Function) -> None:
        x, spec = f.args
        b = self.b
        ctors = self.concrete_ctors(t)
        res = self.new_slot("fmt")
        done = b.append_basic_block("fmt_done")
        for i, (name, ftys) in enumerate(ctors):
            nxt = b.append_basic_block("next_ctor")
            if len(ctors) > 1:
                here = b.append_basic_block("ctor")
                b.cbranch(b.icmp_unsigned("==", self.load_tag(x), self.const(i)), here, nxt)
                b.position_at_end(here)
            if not ftys:
                acc = self.text_const(name)
            else:
                acc = self.text_const(name + "(")
                for j, ft in enumerate(ftys):
                    if j:
                        acc = self.call_rt("uu_text_concat", acc, self.text_const(" "))
                    fv = self.load_field(x, j)
                    self.inc(fv, ft)
                    g = self.method_instance(mi, [ft])
                    txt = b.call(g, [fv, self.spec_value((32, 0, 0, -1, 0))], cconv="tailcc")
                    if isinstance(ft, TCon) and ft.name == "Text":
                        txt = self.call_rt("uu_text_concat", self.text_const('"'), txt)
                        txt = self.call_rt("uu_text_concat", txt, self.text_const('"'))
                    acc = self.call_rt("uu_text_concat", acc, txt)
                acc = self.call_rt("uu_text_concat", acc, self.text_const(")"))
            b.store(acc, res)
            b.branch(done)
            b.position_at_end(nxt)
            if len(ctors) == 1:
                break
        if not b.block.is_terminated:
            self.call_rt("uu_panic_c", self.cstr("format: variante inválida"), ret=VOID, types=[PTR])
            b.unreachable()
        b.position_at_end(done)
        self.dec(x, t)
        b.ret(self.call_rt("uu_fmt_text", b.load(res), spec))


for _n, _v in vars(_Derive).items():
    if callable(_v) and not _n.startswith("__"):
        setattr(Codegen, _n, _v)


class CodegenError(Exception):
    pass


def match_type(pat: Type, conc: Type, mp: dict[int, Type]) -> None:
    pat = prune(pat)
    conc = prune(conc)
    if isinstance(pat, TVar):
        mp[pat.id] = conc
        return
    if isinstance(pat, TCon) and isinstance(conc, TCon):
        for a, b in zip(pat.args, conc.args):
            match_type(a, b, mp)
    elif isinstance(pat, TCtor) and isinstance(conc, TCtor):
        for a, b in zip(pat.trailing, conc.trailing):
            match_type(a, b, mp)
