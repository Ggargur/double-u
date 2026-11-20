"""Resolução de nomes e checagem de tipos (Hindley–Milner com traits e HKT).

Anota a AST para o gerador de código:
  expr.ty   tipo (com metas resolvidas no fim)
  Name.ref / BinOp.ref / Dot.ref   Ref(kind, alvo, inst)
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

from . import ast as A
from .diagnostics import Abort, Diagnostics, Pos
from .types import (
    ALIASES, BOOL, FLOAT_TYPES, INT_TYPES, NUM_TYPES, TEXT, UNIT, EVar, Scheme, TApp, TCon, TCtor, TFn,
    TVar, Type, UnifyError, eff, free_vars, head_name, prune, show, subst, unify,
)

_uid = itertools.count(1)


# ====================================================================== entidades
@dataclass(eq=False)
class TypeInfo:
    name: str
    module: "ModuleInfo"
    vars: list[TVar]
    kind: str  # record sum builtin
    ctors: list["CtorInfo"] = field(default_factory=list)
    public: bool = True
    attrs: dict[str, list[str]] = field(default_factory=dict)
    decl: A.TypeDecl | None = None

    @property
    def is_record(self) -> bool:
        return self.kind == "record"


@dataclass(eq=False)
class CtorInfo:
    name: str
    type: TypeInfo
    index: int
    field_names: list[str | None]
    field_types: list[Type]
    field_public: list[bool]
    pos: Pos

    @property
    def arity(self) -> int:
        return len(self.field_types)

    def scheme(self) -> Scheme:
        res = TCon(self.type.name, list(self.type.vars))
        t: Type = TFn(list(self.field_types), res, False) if self.field_types else res
        return Scheme(list(self.type.vars), [], t)


@dataclass(eq=False)
class FnInfo:
    name: str
    module: "ModuleInfo"
    decl: A.FnDecl | None
    public: bool
    fixity: str
    params: list[A.Param]
    body: A.Expr | None
    io: bool
    prim: str | None = None
    scheme: Scheme | None = None
    mono: TFn | None = None
    annotated: bool = False
    uid: int = field(default_factory=lambda: next(_uid))
    impl: "ImplInfo | None" = None
    trait: "TraitInfo | None" = None
    is_gen: bool = False
    pos: Pos | None = None
    locals: list["LocalVar"] = field(default_factory=list)
    is_global: bool = False

    @property
    def symbol(self) -> str:
        base = re.sub(r"[^A-Za-z0-9_]", lambda m: f"_{ord(m.group()):x}", self.name)
        return f"{self.module.name}.{base}.{self.uid}"


@dataclass(eq=False)
class MethodInfo:
    trait: "TraitInfo"
    name: str
    scheme: Scheme
    decl: A.FnDecl
    fixity: str
    default: FnInfo | None = None

    @property
    def public(self) -> bool:
        return True


@dataclass(eq=False)
class TraitInfo:
    name: str
    module: "ModuleInfo"
    param: TVar
    methods: dict[str, MethodInfo] = field(default_factory=dict)
    supers: list[str] = field(default_factory=list)
    public: bool = True
    hkt: bool = False


@dataclass(eq=False)
class ImplInfo:
    trait: TraitInfo
    head: str
    vars: list[TVar]
    type: Type
    given: list[tuple[str, Type]]
    module: "ModuleInfo"
    pos: Pos
    methods: dict[str, FnInfo] = field(default_factory=dict)


@dataclass(eq=False)
class ModuleInfo:
    name: str
    path: str
    ast: A.Module | None
    values: dict[str, Any] = field(default_factory=dict)
    types: dict[str, TypeInfo] = field(default_factory=dict)
    traits: dict[str, TraitInfo] = field(default_factory=dict)
    impls: list[ImplInfo] = field(default_factory=list)
    is_prelude: bool = False


@dataclass(eq=False)
class ModuleRef:
    """Valor de `nome = @import("caminho")`: o módulo importado, ligado a um nome."""
    module: ModuleInfo
    public: bool
    pos: Pos


@dataclass(eq=False)
class Alias:
    """`nome = mod.membro` no topo: traz um membro de outro módulo para o escopo."""
    target: Any
    public: bool
    pos: Pos


@dataclass(eq=False)
class LocalVar:
    name: str
    ty: Type
    kind: str  # let var borrow mut own
    pos: Pos
    id: int = field(default_factory=lambda: next(_uid))
    owner: Any = None  # FnCtx que declarou

    @property
    def assignable(self) -> bool:
        return self.kind in ("var", "mut")


@dataclass(eq=False)
class Ref:
    kind: str  # local fn method ctor global
    target: Any
    inst: list[Type] = field(default_factory=list)


# ====================================================================== contexto
class FnCtx:
    def __init__(self, parent: "FnCtx | None", io: bool, ret: Type, info: FnInfo | None = None,
                 lam: A.Lambda | None = None):
        self.parent = parent
        self.io = io
        self.ret = ret
        self.info = info
        self.lam = lam
        self.scopes: list[dict[str, LocalVar]] = [{}]
        self.calls_io = False
        self.captures: dict[int, LocalVar] = {}
        self.byref = bool(lam is not None and getattr(lam, "byref", False))

    @property
    def root(self) -> "FnCtx":
        c = self
        while c.parent is not None:
            c = c.parent
        return c


def spec_parse(spec: str) -> tuple[int, int, int, int, int] | None:
    """[[fill]align][width][.precision][kind] -> (fill, align, width, precision, kind)."""
    m = re.fullmatch(r"(?:(.)?([<>^]))?(0)?(\d+)?(?:\.(\d+))?([dfexXsbo?])?", spec)
    if not m:
        return None
    fill, align, zero, width, prec, kind = m.groups()
    a = {"<": 1, ">": 2, "^": 3}.get(align or "", 0)
    f = ord(fill) if fill else (ord("0") if zero else ord(" "))
    if zero and not align:
        a = 4  # preenchimento numérico com zeros depois do sinal
    return (f, a, int(width or 0), int(prec) if prec is not None else -1, ord(kind) if kind else 0)


# ====================================================================== checker
class Checker:
    def __init__(self, diags: Diagnostics):
        self.diags = diags
        self.modules: dict[str, ModuleInfo] = {}
        self.prelude: ModuleInfo | None = None
        self.impls: dict[tuple[str, str], list[ImplInfo]] = {}
        self.level = 1
        self.fns: list[FnInfo] = []
        self.globals: list[FnInfo] = []
        self.ctx: FnCtx | None = None
        self.module: ModuleInfo | None = None
        self.wanted: list[tuple[str, Type, Pos]] = []
        self.global_wanted: list[tuple[str, Type, Pos]] = []
        self.givens: list[tuple[str, Type]] = []
        self.deferred: list[tuple[A.Dot, Pos]] = []
        self.lit_metas: list[TVar] = []
        self.fixity: dict[str, tuple[str, int]] = {}
        self.all_exprs: list[A.Expr] = []
        self.builtin_types: dict[str, TypeInfo] = {}

    # ------------------------------------------------------------------ erros
    def err(self, cat: str, pos: Pos, msg: str, *notes: str) -> None:
        self.diags.error(cat, pos, msg, *notes)

    def unify_at(self, a: Type, b: Type, pos: Pos, what: str = "") -> bool:
        try:
            unify(a, b)
            return True
        except UnifyError:
            pa, pb = prune(a), prune(b)
            cat = "tipo"
            if self._numeric(pa) and self._numeric(pb):
                cat = "numerico"
                msg = f"tipos numéricos misturados: {show(pb)} e {show(pa)}; converta explicitamente (ex.: `x.{show(pa)}`)"
            else:
                msg = f"tipo incompatível{(' ' + what) if what else ''}: esperava {show(pa)}, encontrei {show(pb)}"
            self.err(cat, pos, msg)
            return False

    def _numeric(self, t: Type) -> bool:
        t = prune(t)
        if isinstance(t, TCon) and t.name in NUM_TYPES:
            return True
        return isinstance(t, TVar) and bool(t.lits)

    def fresh(self, hint: str = "") -> TVar:
        return TVar(self.level, hint)

    # ------------------------------------------------------------------ coleta
    def builtin_type(self, name: str, nparams: int, mod: ModuleInfo) -> None:
        vs = [TVar(0, f"T{i}", rigid=True) for i in range(nparams)]
        ti = TypeInfo(name, mod, vs, "builtin")
        mod.types[name] = ti
        self.builtin_types[name] = ti

    def load_prelude(self, mod: ModuleInfo) -> None:
        self.prelude = mod
        mod.is_prelude = True
        for n in NUM_TYPES + ["bool", "()"]:
            self.builtin_type(n, 0, mod)
        for n, k in [("Text", 0), ("List", 1), ("Pending", 1), ("Gen", 1), ("Lazy", 1)]:
            self.builtin_type(n, k, mod)

    def collect(self, mod: ModuleInfo, loaded: dict[str, ModuleInfo] | None = None) -> None:
        assert mod.ast is not None
        self.modules[mod.name] = mod
        self.module = mod
        # imports e aliases (`squared = math.squared`) antes de tudo: tipos podem ser qualificados
        for d in mod.ast.decls:
            if isinstance(d, A.LetDecl) and isinstance(d.expr, A.BuiltinCall) and d.expr.name == "import":
                target = (loaded or {}).get(getattr(d, "import_key", None) or "")
                if target is not None:
                    self.define(mod, d.name, ModuleRef(target, d.public, d.pos), d.pos)
                d.skip = True  # type: ignore[attr-defined]
        for d in mod.ast.decls:
            if isinstance(d, A.LetDecl) and d.ann is None and isinstance(d.expr, A.Dot) \
                    and self.module_of(d.expr.obj) is not None:
                d.skip = True  # type: ignore[attr-defined]
                src = self.module_of(d.expr.obj)
                assert src is not None
                ti = src.types.get(d.expr.name)
                if ti is not None and d.name[:1].isupper():
                    # `World = ecs.World`: o tipo também fica visível pelo nome local
                    if not ti.public:
                        self.err("visibilidade", d.expr.pos, f"tipo `{ti.name}` é privado ao seu módulo")
                    mod.types.setdefault(d.name, ti)
                    if d.expr.name not in src.values:
                        continue
                try:
                    ent = self.qualified(d.expr)
                except Abort:
                    continue
                if isinstance(ent, ModuleRef):
                    ent = ModuleRef(ent.module, d.public, d.pos)
                self.define(mod, d.name, Alias(ent, d.public, d.pos), d.pos)
        # tipos primeiro (nomes), depois corpos de tipos, traits, funções e impls
        for d in mod.ast.decls:
            if isinstance(d, A.TypeDecl):
                if d.name in mod.types:
                    self.err("sobrecarga", d.pos, f"tipo `{d.name}` declarado duas vezes")
                    continue
                vs = [TVar(0, p, rigid=True) for p in d.params]
                kind = "record" if d.record is not None else "sum"
                mod.types[d.name] = TypeInfo(d.name, mod, vs, kind, public=d.public, attrs=d.attrs, decl=d)
        for d in mod.ast.decls:
            if isinstance(d, A.TypeDecl) and mod.types.get(d.name) and mod.types[d.name].decl is d:
                self.collect_type(mod.types[d.name], d)
        for d in mod.ast.decls:
            if isinstance(d, A.TraitDecl):
                self.collect_trait(mod, d)
        for d in mod.ast.decls:
            if isinstance(d, A.FnDecl):
                self.collect_fn(mod, d)
            elif isinstance(d, A.LetDecl) and not getattr(d, "skip", False):
                self.collect_global(mod, d)
        for d in mod.ast.decls:
            if isinstance(d, A.ImplDecl):
                self.collect_impl(mod, d)

    def define(self, mod: ModuleInfo, name: str, ent: Any, pos: Pos) -> None:
        if name in mod.values:
            old = mod.values[name]
            what = "função" if isinstance(ent, FnInfo) else "nome"
            self.err("sobrecarga", pos,
                     f"{what} `{name}` declarada duas vezes no mesmo módulo; não há sobrecarga",
                     f"a outra declaração está em {getattr(old, 'pos', None) or '?'}")
            return
        mod.values[name] = ent

    def collect_type(self, ti: TypeInfo, d: A.TypeDecl) -> None:
        tv = {v.hint: v for v in ti.vars}
        if d.record is not None:
            names = [f.name for f in d.record]
            types = [self.type_expr(f.type, tv, strict=True) for f in d.record]
            c = CtorInfo(d.name, ti, 0, names, types, [f.public for f in d.record], d.pos)
            ti.ctors.append(c)
            self.define(ti.module, d.name, c, d.pos)
        else:
            assert d.variants is not None
            for i, v in enumerate(d.variants):
                types = [self.type_expr(f.type, tv, strict=True) for f in v.fields]
                c = CtorInfo(v.name, ti, i, [f.name for f in v.fields], types, [True] * len(types), v.pos)
                ti.ctors.append(c)
                self.define(ti.module, v.name, c, v.pos)

    def collect_trait(self, mod: ModuleInfo, d: A.TraitDecl) -> None:
        param = TVar(0, d.param, rigid=True)
        tr = TraitInfo(d.name, mod, param, public=d.public, supers=[c.trait for c in d.supers])
        if d.name in mod.traits:
            self.err("sobrecarga", d.pos, f"trait `{d.name}` declarada duas vezes")
            return
        mod.traits[d.name] = tr
        for m in d.methods:
            tv: dict[str, TVar] = {d.param: param}
            ptypes = []
            for p in m.params:
                if p.ann is None:
                    self.err("assinatura", p.pos, f"parâmetro `{p.name}` de método de trait precisa de tipo")
                    ptypes.append(self.fresh())
                else:
                    ptypes.append(self.type_expr(p.ann, tv, rigid_new=True))
            ret = self.type_expr(m.ret, tv, rigid_new=True) if m.ret else UNIT
            if any(isinstance(prune(t), TApp) and prune(t).head is param for t in ptypes + [ret]):  # type: ignore[union-attr]
                tr.hkt = True
            fnt = TFn(ptypes, ret, m.io, [p.mode for p in m.params])
            extra = [v for v in tv.values() if v is not param]
            preds = [(d.name, param)] + [(c.trait, self.type_expr(c.type, tv)) for c in m.given]
            sch = Scheme([param] + extra, preds, fnt)
            mi = MethodInfo(tr, m.name, sch, m, m.fixity)
            if m.assoc is not None:
                self.fixity[m.name] = (m.assoc, m.prec or 0)
            tr.methods[m.name] = mi
            self.define(mod, m.name, mi, m.pos)
            if m.body is not None:
                fi = FnInfo(f"{d.name}.{m.name}", mod, m, True, m.fixity, m.params, m.body, m.io,
                            annotated=True, pos=m.pos, trait=tr)
                fi.scheme = sch
                mi.default = fi
                self.fns.append(fi)

    def fn_prim(self, d: A.FnDecl) -> str | None:
        b = d.body
        if isinstance(b, A.Block) and not b.stmts and b.result is not None:
            b = b.result
        if isinstance(b, A.BuiltinCall) and b.name == "prim":
            arg = b.args[0]
            if isinstance(arg, A.Str) and len(arg.parts) == 1 and isinstance(arg.parts[0], str):
                return arg.parts[0]
        return None

    def collect_fn(self, mod: ModuleInfo, d: A.FnDecl) -> None:
        fi = FnInfo(d.name, mod, d, d.public, d.fixity, d.params, d.body, d.io, pos=d.pos, is_gen=d.is_gen)
        fi.prim = self.fn_prim(d)
        if d.body is None:
            self.err("sintaxe", d.pos, f"função `{d.name}` sem corpo")
        annotated = all(p.ann is not None for p in d.params) and d.ret is not None
        if d.public and not annotated:
            self.err("assinatura", d.pos,
                     f"item público `{d.name}` precisa de assinatura anotada (tipos de todos os parâmetros e do retorno)")
        if d.fixity == "infix" and not d.name[0].isalpha() and not mod.is_prelude:
            self.err("fixidez", d.pos, f"operador simbólico `{d.name}` só pode ser definido por uma trait")
        fi.annotated = annotated
        if annotated:
            tv: dict[str, TVar] = {}
            ptypes = [self.type_expr(p.ann, tv, rigid_new=True) for p in d.params]  # type: ignore[arg-type]
            ret = self.type_expr(d.ret, tv, rigid_new=True)  # type: ignore[arg-type]
            preds = [(c.trait, self.type_expr(c.type, tv)) for c in d.given]
            fi.scheme = Scheme(list(tv.values()), preds, TFn(ptypes, ret, d.io, [p.mode for p in d.params]))
        self.define(mod, d.name, fi, d.pos)
        self.fns.append(fi)

    def collect_global(self, mod: ModuleInfo, d: A.LetDecl) -> None:
        fi = FnInfo(d.name, mod, None, d.public, "global", [], d.expr, False, pos=d.pos, is_global=True)
        fi.decl = A.FnDecl(d.pos, d.name, d.public, "prefix", [], d.ann, False, [], d.expr)
        self.define(mod, d.name, fi, d.pos)
        self.fns.append(fi)

    def collect_impl(self, mod: ModuleInfo, d: A.ImplDecl) -> None:
        tr = self.lookup_trait(d.trait, d.pos, d.trait_module)
        if tr is None:
            return
        tv: dict[str, TVar] = {}
        ty = self.type_expr(d.type, tv, rigid_new=True, allow_ctor=True)
        h = head_name(ty)
        if isinstance(ty, TCtor) and not ty.trailing:
            ti0 = self.find_type_info(ty.name)
            if ti0 is not None and len(ti0.vars) > 1:
                # `impl Monad(Result)`: os parâmetros finais ficam livres no impl
                for v in ti0.vars[1:]:
                    nv = TVar(0, v.hint, rigid=True)
                    tv[f"${v.hint}"] = nv
                    ty.trailing.append(nv)
        if h is None:
            self.err("trait", d.pos, "o tipo de um `impl` precisa ter um construtor conhecido")
            return
        tinfo = self.find_type_info(h)
        if not mod.is_prelude and tinfo is not None and tinfo.module is not mod and tr.module is not mod:
            self.err("orfao", d.pos,
                     f"`impl {tr.name}({h})` precisa estar no módulo do tipo `{h}` ou no da trait `{tr.name}`")
        given = [(c.trait, self.type_expr(c.type, tv)) for c in d.given]
        impl = ImplInfo(tr, h, list(tv.values()), ty, given, mod, d.pos)
        lst = self.impls.setdefault((tr.name, h), [])
        if lst:
            self.err("trait", d.pos, f"`impl {tr.name}({h})` duplicado")
            return
        lst.append(impl)
        mod.impls.append(impl)
        for m in d.methods:
            if m.name not in tr.methods:
                self.err("trait", m.pos, f"`{m.name}` não é método da trait `{tr.name}`")
                continue
            mi = tr.methods[m.name]
            for p, tp in zip(m.params, mi.decl.params):
                if p.mode == "borrow":
                    p.mode = tp.mode
            fi = FnInfo(f"{tr.name}({h}).{m.name}", mod, m, True, mi.fixity, m.params, m.body, mi.decl.io,
                        annotated=True, pos=m.pos, impl=impl)
            fi.prim = self.fn_prim(m)
            # esquema: variáveis do impl + variáveis próprias do método (cópias rígidas)
            extra = mi.scheme.vars[1:]
            copies = [TVar(0, v.hint, rigid=True) for v in extra]
            mp: dict[int, Type] = {tr.param.id: ty}
            for v, c in zip(extra, copies):
                mp[v.id] = c
            t = subst(mi.scheme.type, mp)
            preds = list(given) + [(p, subst(pt, mp)) for p, pt in mi.scheme.preds[1:]]
            fi.scheme = Scheme(impl.vars + copies, preds, t)
            impl.methods[m.name] = fi
            self.fns.append(fi)
        for name, mi in tr.methods.items():
            if name not in impl.methods and mi.default is None:
                self.err("trait", d.pos, f"`impl {tr.name}({h})` não define o método `{name}`")

    # ------------------------------------------------------------------ lookup
    def visible_modules(self) -> list[ModuleInfo]:
        out = [self.module] if self.module else []
        if self.prelude and self.prelude is not self.module:
            out.append(self.prelude)
        return out  # type: ignore[return-value]

    def lookup_value(self, name: str) -> Any:
        for m in self.visible_modules():
            if name in m.values:
                v = m.values[name]
                if m is self.module or getattr(v, "public", True):
                    return v.target if isinstance(v, Alias) else v
        return None

    def lookup_trait(self, name: str, pos: Pos, module: str | None = None) -> TraitInfo | None:
        if module is not None:
            m = self.module_named(module, pos)
            tr = m.traits.get(name)
            if tr is None:
                self.err("nome", pos, f"módulo `{module}` não tem trait `{name}`")
                return None
            if not tr.public:
                self.err("visibilidade", pos, f"trait `{name}` é privada ao módulo `{module}`")
            return tr
        for m in self.visible_modules():
            if name in m.traits:
                return m.traits[name]
        self.err("nome", pos, f"trait `{name}` não existe")
        return None

    # ------------------------------------------------------------------ módulos
    def module_of(self, e: A.Expr) -> ModuleInfo | None:
        """O módulo denotado por `e` (`math`, `pkg.math`), ou None se `e` não é um módulo."""
        if isinstance(e, A.Name):
            if self.ctx is not None and self.find_local_peek(e.name) is not None:
                return None
            v = self.lookup_value(e.name)
            return v.module if isinstance(v, ModuleRef) else None
        if isinstance(e, A.Dot):
            outer = self.module_of(e.obj)
            if outer is not None:
                v = outer.values.get(e.name)
                v = v.target if isinstance(v, Alias) else v
                if isinstance(v, ModuleRef):
                    if not v.public:
                        self.err("visibilidade", e.pos, f"módulo `{e.name}` é privado ao módulo que o importa")
                    return v.module
        return None

    def module_named(self, name: str, pos: Pos) -> ModuleInfo:
        v = self.lookup_value(name)
        if not isinstance(v, ModuleRef):
            self.err("nome", pos, f"`{name}` não é um módulo importado")
            raise Abort()
        return v.module

    def member(self, m: ModuleInfo, name: str, pos: Pos, what: str) -> Any:
        """Membro público `name` do módulo `m`; privado é erro de visibilidade."""
        v = m.values.get(name)
        if v is None:
            self.err("nome", pos, f"módulo `{what}` não tem membro `{name}`")
            raise Abort()
        if isinstance(v, (Alias, ModuleRef)):
            public = v.public
        elif isinstance(v, CtorInfo):
            public = v.type.public
        elif isinstance(v, MethodInfo):
            public = v.trait.public
        else:
            public = getattr(v, "public", True)
        if not public:
            self.err("visibilidade", pos, f"`{name}` é privado ao módulo `{what}`; marque-o com `+` para exportá-lo")
        return v.target if isinstance(v, Alias) else v

    def qualified(self, d: A.Dot) -> Any:
        m = self.module_of(d.obj)
        assert m is not None
        what = d.obj.name if isinstance(d.obj, A.Name) else m.name
        return self.member(m, d.name, d.pos, what)

    def qualified_type(self, t: A.TName) -> TypeInfo | None:
        assert t.module is not None
        m = self.module_named(t.module, t.pos)
        ti = m.types.get(ALIASES.get(t.name, t.name))
        if ti is None:
            self.err("nome", t.pos, f"módulo `{t.module}` não tem tipo `{t.name}`")
            raise Abort()
        if not ti.public:
            self.err("visibilidade", t.pos, f"tipo `{t.name}` é privado ao módulo `{t.module}`")
        return ti

    def find_type_info(self, name: str) -> TypeInfo | None:
        for m in [*self.modules.values(), self.prelude]:
            if m and name in m.types:
                return m.types[name]
        return None

    def lookup_type(self, name: str) -> TypeInfo | None:
        name = ALIASES.get(name, name)
        for m in self.visible_modules():
            if name in m.types:
                return m.types[name]
        return None

    def type_expr(self, t: A.TypeExpr, tv: dict[str, TVar], rigid_new: bool = False, strict: bool = False,
                  allow_ctor: bool = False) -> Type:
        if isinstance(t, A.TFunc):
            return TFn([self.type_expr(p, tv, rigid_new, strict) for p in t.params],
                       self.type_expr(t.ret, tv, rigid_new, strict), t.io)
        if isinstance(t, A.TTuple):
            return TCon("#", [self.type_expr(x, tv, rigid_new, strict) for x in t.items])
        args = [self.type_expr(a, tv, rigid_new, strict) for a in t.args]
        if t.module is not None:
            try:
                ti = self.qualified_type(t)
            except Abort:
                return self.fresh()
        elif t.name in tv:
            v = tv[t.name]
            return TApp(v, args) if args else v
        else:
            ti = self.lookup_type(t.name)
        if ti is None:
            if t.name[:1].isupper() and len(t.name) <= 2 or (t.name[:1].isupper() and not strict and t.name not in ("Text",)):
                if strict:
                    self.err("nome", t.pos, f"variável de tipo `{t.name}` não declarada no tipo")
                v = TVar(self.level if not rigid_new else 0, t.name, rigid=rigid_new)
                tv[t.name] = v
                return TApp(v, args) if args else v
            self.err("nome", t.pos, f"tipo `{t.name}` não existe")
            return self.fresh()
        if len(args) != len(ti.vars):
            if not args and ti.vars:
                return TCtor(ti.name)
            self.err("tipo", t.pos, f"`{t.name}` espera {len(ti.vars)} argumento(s) de tipo, recebeu {len(args)}")
            return self.fresh()
        return TCon(ti.name, args)

    # ------------------------------------------------------------------ esquemas
    def instantiate(self, sch: Scheme, pos: Pos) -> tuple[Type, list[Type]]:
        mp: dict[int, Type] = {}
        inst: list[Type] = []
        for v in sch.vars:
            nv = self.fresh(v.hint)
            mp[v.id] = nv
            inst.append(nv)
        for trait, t in sch.preds:
            self.wanted.append((trait, subst(t, mp), pos))
        return subst(sch.type, mp), inst

    def generalize(self, fi: FnInfo) -> None:
        assert fi.mono is not None
        constrained: set[int] = set()
        for _, t, _ in self.wanted:
            for v in free_vars(t):
                constrained.add(v.id)
        vs = []
        for v in free_vars(fi.mono):
            if v.level > 0 and not v.rigid and v.id not in constrained and not v.lits:
                vs.append(v)
            else:
                v.level = 0
        fi.scheme = Scheme(vs, [], fi.mono)

    # ------------------------------------------------------------------ restrições
    def entails_given(self, trait: str, t: Type) -> bool:
        for g, gt in self.givens:
            if prune(gt) is t and (g == trait or self.is_super(trait, g)):
                return True
        return False

    def is_super(self, want: str, have: str, seen: set[str] | None = None) -> bool:
        seen = seen or set()
        if have in seen:
            return False
        seen.add(have)
        tr = self.find_trait(have)
        if tr is None:
            return False
        for s in tr.supers:
            if s == want or self.is_super(want, s, seen):
                return True
        return False

    def find_trait(self, name: str) -> TraitInfo | None:
        for m in [*self.modules.values(), self.prelude]:
            if m and name in m.traits:
                return m.traits[name]
        return None

    def solve(self, wanted: list[tuple[str, Type, Pos]], final: bool = False) -> list[tuple[str, Type, Pos]]:
        rest: list[tuple[str, Type, Pos]] = []
        work = list(wanted)
        while work:
            trait, t, pos = work.pop()
            t = prune(t)
            if isinstance(t, TVar):
                if t.rigid:
                    if not self.entails_given(trait, t):
                        self.err("trait", pos, f"`{t.hint}` precisa de `{trait}`: adicione `given {trait}({t.hint})`")
                    continue
                if final:
                    self.err("tipo", pos, f"tipo ambíguo: não sei qual `{trait}` usar aqui")
                    continue
                rest.append((trait, t, pos))
                continue
            if isinstance(t, TApp):
                h = prune(t.head)
                if isinstance(h, TVar) and h.rigid:
                    if not self.entails_given(trait, h):
                        self.err("trait", pos, f"`{show(t)}` não implementa `{trait}`")
                    continue
                rest.append((trait, t, pos))
                continue
            h = head_name(t)
            impl = self.find_impl(trait, h)
            if impl is None:
                if self.derivable(trait, t):
                    for sub in self.derive_parts(t):
                        work.append((trait, sub, pos))
                    continue
                cat = "format" if trait == "Format" else "trait"
                self.err(cat, pos, f"`{show(t)}` não implementa a trait `{trait}`")
                continue
            mp = {v.id: self.fresh(v.hint) for v in impl.vars}
            pat = subst(impl.type, mp)
            try:
                if isinstance(pat, TCtor) and not pat.trailing:
                    pass  # impl sobre o construtor inteiro: casa pelo nome
                else:
                    unify(pat, t)
            except UnifyError:
                self.err("trait", pos, f"`{show(t)}` não implementa a trait `{trait}`")
                continue
            for g, gt in impl.given:
                work.append((g, subst(gt, mp), pos))
        return rest

    def find_impl(self, trait: str, head: str | None) -> ImplInfo | None:
        if head is None:
            return None
        lst = self.impls.get((trait, head))
        return lst[0] if lst else None

    DERIVABLE = ("Eq", "Format", "Hash", "Copy", "Clone")

    def derivable(self, trait: str, t: Type) -> bool:
        if trait not in self.DERIVABLE:
            return False
        t = prune(t)
        if not isinstance(t, TCon):
            return False
        if t.name == "#":
            return True
        ti = self.find_type_info(t.name)
        if ti is None or ti.kind == "builtin":
            return False
        if "noderive" in ti.attrs and (not ti.attrs["noderive"] or trait in ti.attrs["noderive"]):
            return False
        if trait == "Copy" and "nocopy" in ti.attrs:
            return False
        return True

    def derive_parts(self, t: Type) -> list[Type]:
        t = prune(t)
        assert isinstance(t, TCon)
        if t.name == "#":
            return list(t.args)
        ti = self.find_type_info(t.name)
        assert ti is not None
        mp = {v.id: a for v, a in zip(ti.vars, t.args)}
        out = []
        for c in ti.ctors:
            for ft in c.field_types:
                st = subst(ft, mp)
                if head_name(st) != t.name:
                    out.append(st)
        return out

    # ------------------------------------------------------------------ programa
    def check_program(self) -> None:
        unannotated = [f for f in self.fns if not f.annotated and f.body is not None]
        for f in unannotated:
            ptypes = []
            for p in f.params:
                ptypes.append(self.fresh())
            f.mono = TFn(ptypes, self.fresh(), f.io, [p.mode for p in f.params])
        for scc in self.sccs(unannotated):
            self.level = 1
            saved_wanted = self.wanted
            self.wanted = []
            for f in scc:
                self.check_fn(f)
            self.wanted = self.solve(self.wanted)
            for f in scc:
                self.generalize(f)
            self.global_wanted += self.wanted
            self.wanted = saved_wanted
        for f in self.fns:
            if f.annotated and f.body is not None:
                self.level = 1
                self.wanted = []
                self.check_fn(f)
                rest = self.solve(self.wanted)
                self.global_wanted += rest
        self.wanted = []
        self.finish()

    def sccs(self, fns: list[FnInfo]) -> list[list[FnInfo]]:
        ids = {id(f): f for f in fns}
        by_name: dict[tuple[str, str], FnInfo] = {(f.module.name, f.name): f for f in fns}
        edges: dict[int, list[FnInfo]] = {}
        for f in fns:
            out: list[FnInfo] = []
            for n in names_in(f.body):
                g = by_name.get((f.module.name, n))
                if g is not None:
                    out.append(g)
            edges[id(f)] = out
        index: dict[int, int] = {}
        low: dict[int, int] = {}
        stack: list[FnInfo] = []
        on: set[int] = set()
        result: list[list[FnInfo]] = []
        counter = [0]

        def strong(v: FnInfo) -> None:
            index[id(v)] = low[id(v)] = counter[0]
            counter[0] += 1
            stack.append(v)
            on.add(id(v))
            for w in edges[id(v)]:
                if id(w) not in index:
                    strong(w)
                    low[id(v)] = min(low[id(v)], low[id(w)])
                elif id(w) in on:
                    low[id(v)] = min(low[id(v)], index[id(w)])
            if low[id(v)] == index[id(v)]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(id(w))
                    comp.append(w)
                    if w is v:
                        break
                result.append(comp)

        for f in fns:
            if id(f) not in index:
                strong(f)
        del ids
        return result

    def finish(self) -> None:
        # defaulting de literais numéricos
        for v in self.lit_metas:
            t = prune(v)
            if isinstance(t, TVar) and not t.rigid:
                try:
                    unify(t, TCon("f64") if "float" in t.lits else TCon("i64"))
                except UnifyError:
                    pass
        rest = self.solve(self.global_wanted)
        rest = self.solve(rest, final=True)
        for dot, pos in self.deferred:
            if getattr(dot, "ref", None) is None:
                self.err("tipo", pos, f"não sei o tipo de `{dot.name}` aqui; anote o tipo")

    def check_main(self, mod: ModuleInfo) -> FnInfo | None:
        m = mod.values.get("main")
        if not isinstance(m, FnInfo):
            self.err("main", Pos(mod.path, 1, 1), "o programa precisa de `fn +main() -> Pending(int) io`")
            return None
        ok = m.scheme is not None and isinstance(m.scheme.type, TFn) and not m.scheme.type.params
        if ok:
            r = prune(m.scheme.type.ret)  # type: ignore[union-attr]
            ok = isinstance(r, TCon) and r.name == "Pending" and key_is_int(r.args[0]) and m.io
        if not ok:
            self.err("main", m.pos or Pos(mod.path, 1, 1), "`main` deve ter a assinatura `fn +main() -> Pending(int) io`")
            return None
        return m

    # ------------------------------------------------------------------ funções
    def check_fn(self, f: FnInfo) -> None:
        self.module = f.module
        if f.prim is not None:
            if f.scheme is None:
                self.err("assinatura", f.pos or Pos(f.module.path, 1, 1), "primitiva precisa de assinatura")
                return
            for p, pt in zip(f.params, f.scheme.type.params):  # type: ignore[union-attr]
                p.local = LocalVar(p.name, pt, p.mode, p.pos)  # type: ignore[attr-defined]
            return
        if f.annotated:
            assert f.scheme is not None
            ft = f.scheme.type
            self.givens = list(f.scheme.preds)
        else:
            ft = f.mono  # type: ignore[assignment]
            self.givens = []
            if f.decl is not None:
                tv: dict[str, TVar] = {}
                for p, pt in zip(f.params, ft.params):  # type: ignore[union-attr]
                    if p.ann is not None:
                        self.unify_at(pt, self.type_expr(p.ann, tv), p.pos)
                if f.decl.ret is not None:
                    self.unify_at(ft.ret, self.type_expr(f.decl.ret, tv), f.decl.pos)  # type: ignore[union-attr]
        assert isinstance(ft, TFn)
        ctx = FnCtx(None, f.io, ft.ret, f)
        self.ctx = ctx
        for p, pt in zip(f.params, ft.params):
            lv = LocalVar(p.name, pt, p.mode, p.pos, owner=ctx)
            p.local = lv  # type: ignore[attr-defined]
            ctx.scopes[0][p.name] = lv
            f.locals.append(lv)
        assert f.body is not None
        try:
            t = self.expr(f.body, ft.ret)
            self.unify_at(ft.ret, t, result_pos(f.body), "no retorno")
            mark_tail(f.body, True, self)
        except Abort:
            pass
        self.ctx = None

    # ------------------------------------------------------------------ escopos
    def declare(self, name: str, ty: Type, kind: str, pos: Pos) -> LocalVar:
        assert self.ctx is not None
        lv = LocalVar(name, ty, kind, pos, owner=self.ctx)
        if name != "_":
            self.ctx.scopes[-1][name] = lv
        root = self.ctx.root
        if root.info is not None:
            root.info.locals.append(lv)
        return lv

    def find_local(self, name: str) -> LocalVar | None:
        c = self.ctx
        path: list[FnCtx] = []
        while c is not None:
            for sc in reversed(c.scopes):
                if name in sc:
                    lv = sc[name]
                    for inner in path:
                        inner.captures[lv.id] = lv
                    return lv
            path.append(c)
            c = c.parent
        return None

    def push(self) -> None:
        assert self.ctx is not None
        self.ctx.scopes.append({})

    def pop(self) -> None:
        assert self.ctx is not None
        self.ctx.scopes.pop()

    # ------------------------------------------------------------------ expressões
    def expr(self, e: A.Expr, expected: Type | None = None) -> Type:
        t = self._expr(e, expected)
        e.ty = t  # type: ignore[attr-defined]
        return t

    def _expr(self, e: A.Expr, expected: Type | None) -> Type:
        m = getattr(self, "e_" + type(e).__name__, None)
        if m is None:
            self.err("sintaxe", e.pos, f"expressão não suportada aqui ({type(e).__name__})")
            return self.fresh()
        return m(e, expected)

    def e_Lit(self, e: A.Lit, expected: Type | None) -> Type:
        if e.kind == "int" or e.kind == "float":
            v = self.fresh()
            v.lits.add(e.kind)
            self.lit_metas.append(v)
            return v
        if e.kind == "bool":
            return BOOL
        return UNIT

    def e_Str(self, e: A.Str, expected: Type | None) -> Type:
        for p in e.parts:
            if isinstance(p, A.InterpPart):
                t = self.expr(p.expr)
                if p.spec is not None and spec_parse(p.spec) is None:
                    self.err("format", p.pos, f"spec de formatação inválido: `{p.spec}`")
                p.spec_val = spec_parse(p.spec or "") or (32, 0, 0, -1, 0)  # type: ignore[attr-defined]
                tr = self.find_trait("Format")
                assert tr is not None
                mi = tr.methods["format"]
                _, inst = self.instantiate(mi.scheme, p.expr.pos)
                self.unify_at(inst[0], t, p.expr.pos)
                pt = prune(t)
                if isinstance(pt, TFn):
                    self.err("format", p.expr.pos, f"`{show(pt)}` não implementa `Format`")
                p.ref = Ref("method", mi, inst)  # type: ignore[attr-defined]
        return TEXT

    def e_Name(self, e: A.Name, expected: Type | None) -> Type:
        lv = self.find_local(e.name)
        if lv is not None:
            e.ref = Ref("local", lv)  # type: ignore[attr-defined]
            return lv.ty
        ent = self.lookup_value(e.name)
        if ent is None:
            self.err("nome", e.pos, f"`{e.name}` não está definido")
            raise Abort()
        if isinstance(ent, ModuleRef):
            self.err("tipo", e.pos, f"`{e.name}` é um módulo; acesse seus membros com `{e.name}.membro`")
            raise Abort()
        return self.ref_entity(e, ent, e.pos)

    def ref_entity(self, e: Any, ent: Any, pos: Pos) -> Type:
        if isinstance(ent, FnInfo):
            if ent.scheme is not None:
                t, inst = self.instantiate(ent.scheme, pos)
            else:
                assert ent.mono is not None
                t, inst = ent.mono, []
            e.ref = Ref("global" if ent.is_global else "fn", ent, inst)
            if ent.is_global and isinstance(t, TFn):
                return t.ret
            return t
        if isinstance(ent, MethodInfo):
            t, inst = self.instantiate(ent.scheme, pos)
            e.ref = Ref("method", ent, inst)
            return t
        if isinstance(ent, CtorInfo):
            t, inst = self.instantiate(ent.scheme(), pos)
            e.ref = Ref("ctor", ent, inst)
            return t
        self.err("nome", pos, "nome inválido aqui")
        raise Abort()

    def callee_info(self, fn: A.Expr) -> tuple[list[str] | None, list[str] | None]:
        """Nomes e modos dos parâmetros, quando o alvo é conhecido."""
        ref = getattr(fn, "ref", None)
        if ref is None:
            return None, None
        if ref.kind == "fn":
            return [p.name for p in ref.target.params], [p.mode for p in ref.target.params]
        if ref.kind == "method":
            d = ref.target.decl
            return [p.name for p in d.params], [p.mode for p in d.params]
        if ref.kind == "ctor":
            c = ref.target
            return list(c.field_names), ["own"] * c.arity
        if ref.kind == "local":
            t = prune(ref.target.ty)
            if isinstance(t, TFn):
                return None, t.modes
        return None, None

    def e_Call(self, e: A.Call, expected: Type | None) -> Type:
        ft = self.expr(e.fn)
        names, modes = self.callee_info(e.fn)
        # argumentos nomeados
        if any(n is not None for n in e.names):
            if any(n is None for n in e.names):
                self.err("argumentos", e.pos, "não misture argumentos nomeados e posicionais na mesma chamada")
                raise Abort()
            if names is None:
                self.err("argumentos", e.pos, "argumentos nomeados exigem uma função conhecida")
                raise Abort()
            order: list[A.Expr | None] = [None] * len(names)
            for n, a in zip(e.names, e.args):
                if n not in names:
                    self.err("argumentos", a.pos, f"parâmetro `{n}` não existe")
                    raise Abort()
                order[names.index(n)] = a
            if any(a is None for a in order):
                missing = [names[i] for i, a in enumerate(order) if a is None]
                self.err("aridade", e.pos, f"faltam argumentos: {', '.join(missing)}")
                raise Abort()
            e.args = order  # type: ignore[assignment]
            e.names = [None] * len(order)
        pft = prune(ft)
        if isinstance(pft, TFn):
            if len(pft.params) != len(e.args):
                self.err("aridade", e.pos,
                         f"esperava {len(pft.params)} argumento(s), recebeu {len(e.args)}"
                         + ("; use `_` para aplicação parcial" if len(e.args) < len(pft.params) else ""))
                raise Abort()
            params = pft.params
            ret = pft.ret
            io = eff(pft.io)
            modes = modes or pft.modes
        else:
            params = [self.fresh() for _ in e.args]
            ret = self.fresh()
            io = EVar()
            if not self.unify_at(TFn(params, ret, io), ft, e.fn.pos, "na chamada"):
                raise Abort()
        for i, (a, pt) in enumerate(zip(e.args, params)):
            mode = modes[i] if modes and i < len(modes) else "borrow"
            self.arg(a, pt, mode)
        if io is True:
            self.use_io(e.pos)
        return ret

    def arg(self, a: A.Expr, pt: Type, mode: str) -> None:
        if isinstance(a, A.Bang):
            if mode != "mut":
                self.err("posse", a.pos, "`!` só pode marcar argumento de parâmetro `mut`")
            t = self.expr(a.expr, pt)
            a.ty = t  # type: ignore[attr-defined]
            self.unify_at(pt, t, a.pos)
            target = a.expr
            if isinstance(target, A.Name):
                lv = getattr(target, "ref", None)
                if lv is not None and lv.kind == "local" and not lv.target.assignable:
                    self.err("posse", a.pos,
                             f"`!{target.name}` exige uma ligação `var` ou um parâmetro `mut`")
            else:
                self.err("posse", a.pos, "`!` só pode marcar o nome de uma ligação")
            return
        if mode == "mut":
            self.err("posse", a.pos, "parâmetro `mut` exige marcar o argumento com `!`")
        t = self.expr(a, pt)
        self.unify_at(pt, t, a.pos)

    def use_io(self, pos: Pos) -> None:
        c = self.ctx
        assert c is not None
        if c.lam is not None:
            c.calls_io = True
            return
        if not c.io:
            self.err("io", pos, "função pura não pode chamar função `io`; marque a função com `io`")

    def e_Bang(self, e: A.Bang, expected: Type | None) -> Type:
        self.err("posse", e.pos, "`!` só pode aparecer em argumentos de chamada")
        return self.expr(e.expr)

    def e_Hole(self, e: A.Hole, expected: Type | None) -> Type:
        self.err("sintaxe", e.pos, "`_` só pode aparecer como argumento (aplicação parcial)")
        return self.fresh()

    def e_BinOp(self, e: A.BinOp, expected: Type | None) -> Type:
        if e.op in ("&&", "||"):
            self.unify_at(BOOL, self.expr(e.left, BOOL), e.left.pos)
            self.unify_at(BOOL, self.expr(e.right, BOOL), e.right.pos)
            return BOOL
        ent = self.lookup_value(e.op)
        lv = self.find_local(e.op) if e.word else None
        if lv is not None:
            ft = lv.ty
            e.ref = Ref("local", lv)  # type: ignore[attr-defined]
        elif ent is None:
            self.err("nome", e.pos, f"operador `{e.op}` não está definido")
            raise Abort()
        else:
            if isinstance(ent, FnInfo) and ent.fixity != "infix" and e.word:
                self.err("fixidez", e.pos, f"`{e.op}` não foi declarada infixa")
            ft = self.ref_entity(e, ent, e.pos)
        pft = prune(ft)
        if not isinstance(pft, TFn) or len(pft.params) != 2:
            self.err("tipo", e.pos, f"`{e.op}` não é uma função de dois argumentos")
            raise Abort()
        lt = self.expr(e.left, pft.params[0])
        self.unify_at(pft.params[0], lt, e.left.pos)
        rt = self.expr(e.right, pft.params[1])
        self.unify_at(pft.params[1], rt, e.right.pos)
        if eff(pft.io) is True:
            self.use_io(e.pos)
        return pft.ret

    def e_Dot(self, e: A.Dot, expected: Type | None) -> Type:
        if self.module_of(e.obj) is not None:
            # `math.cross`: membro de módulo (módulos são valores; é acesso a campo)
            ent = self.qualified(e)
            if isinstance(ent, ModuleRef):
                self.err("tipo", e.pos, f"`{e.name}` é um módulo; acesse seus membros com `.`")
                raise Abort()
            e.qualified = True  # type: ignore[attr-defined]
            return self.ref_entity(e, ent, e.pos)
        ot = self.expr(e.obj)
        return self.dot(e, ot)

    def postfix_entity(self, name: str) -> Any:
        ent = self.lookup_value(name)
        if isinstance(ent, FnInfo) and ent.fixity == "postfix":
            return ent
        if isinstance(ent, MethodInfo) and ent.fixity == "postfix":
            return ent
        return None

    def dot(self, e: A.Dot, ot: Type) -> Type:
        t = prune(ot)
        post = self.postfix_entity(e.name)
        lv = self.find_local(e.name)
        if lv is not None and isinstance(prune(lv.ty), TFn) and len(prune(lv.ty).params) == 1:  # type: ignore[union-attr]
            post = lv
        if isinstance(t, TVar) and not t.rigid:
            cands = self.records_with_field(e.name)
            if post is None and len(cands) == 1:
                ti = cands[0]
                inst = TCon(ti.name, [self.fresh() for _ in ti.vars])
                self.unify_at(t, inst, e.pos)
                t = prune(t)
            elif post is None and not cands:
                self.no_member(e, t)
            elif post is None:
                res = self.fresh()
                self.deferred.append((e, e.pos))
                e.ref = None  # type: ignore[attr-defined]
                self.err("tipo", e.pos, f"não sei o tipo do valor antes de `.{e.name}`; anote o tipo")
                return res
        fld = self.field_of(t, e.name)
        if fld is not None:
            if post is not None:
                self.err("ambiguidade", e.pos,
                         f"`.{e.name}` é ambíguo: existe o campo `{e.name}` e uma pós-fixa `{e.name}` no escopo")
            ctor, idx, ftype = fld
            if not ctor.field_public[idx] and ctor.type.module is not self.module:
                self.err("visibilidade", e.pos, f"campo `{e.name}` é privado")
            e.ref = Ref("field", (ctor, idx))  # type: ignore[attr-defined]
            return ftype
        if post is None:
            self.no_member(e, t)
        if isinstance(post, LocalVar):
            e.ref = Ref("local", post)  # type: ignore[attr-defined]
            ft = post.ty
        else:
            ft = self.ref_entity(e, post, e.pos)
        pft = prune(ft)
        assert isinstance(pft, TFn)
        self.unify_at(pft.params[0], ot, e.obj.pos)
        if eff(pft.io) is True:
            self.use_io(e.pos)
        e.postfix = True  # type: ignore[attr-defined]
        return pft.ret

    def no_member(self, e: A.Dot, t: Type) -> None:
        notes = []
        cur = self.module
        for alias, v in (cur.values.items() if cur else []):
            if isinstance(v, ModuleRef):
                f = v.module.values.get(e.name)
                if isinstance(f, (FnInfo, MethodInfo)) and f.fixity == "postfix" and f.public:
                    notes.append(f"traga a pós-fixa para o escopo: `{e.name} = {alias}.{e.name}`")
        what = "o valor" if isinstance(t, TVar) else f"`{show(t)}`"
        self.err("nome", e.pos, f"{what} não tem campo `{e.name}` e não há pós-fixa `{e.name}` no escopo", *notes)
        raise Abort()

    def records_with_field(self, name: str) -> list[TypeInfo]:
        out = []
        for m in self.visible_modules():
            for ti in m.types.values():
                if ti.kind == "record" and name in ti.ctors[0].field_names:
                    out.append(ti)
        return out

    def field_of(self, t: Type, name: str) -> tuple[CtorInfo, int, Type] | None:
        t = prune(t)
        if not isinstance(t, TCon):
            return None
        ti = self.find_type_info(t.name)
        if ti is None or ti.kind != "record":
            return None
        c = ti.ctors[0]
        if name not in c.field_names:
            return None
        i = c.field_names.index(name)
        mp = {v.id: a for v, a in zip(ti.vars, t.args)}
        return c, i, subst(c.field_types[i], mp)

    def e_TryOp(self, e: A.TryOp, expected: Type | None) -> Type:
        t = self.expr(e.expr)
        tr = self.find_trait("Try")
        assert tr is not None and self.ctx is not None
        mi = tr.methods["branch"]
        ft, inst = self.instantiate(mi.scheme, e.pos)
        assert isinstance(ft, TFn)
        self.unify_at(ft.params[0], t, e.expr.pos)
        e.ref = Ref("method", mi, inst)  # type: ignore[attr-defined]
        m = inst[0]
        ret_shape = TApp(m, [self.fresh()])
        if not self.unify_at(self.ctx.ret, ret_shape, e.pos, "(o `.?` retorna da função)"):
            pass
        return inst[1]

    def e_Lambda(self, e: A.Lambda, expected: Type | None) -> Type:
        assert self.ctx is not None
        exp = prune(expected) if expected is not None else None
        ptypes: list[Type] = []
        for i, p in enumerate(e.params):
            if isinstance(exp, TFn) and len(exp.params) == len(e.params):
                ptypes.append(exp.params[i])
            else:
                ptypes.append(self.fresh())
        ret = exp.ret if isinstance(exp, TFn) and len(exp.params) == len(e.params) else self.fresh()
        ctx = FnCtx(self.ctx, self.ctx.io, ret, lam=e)
        outer = self.ctx
        self.ctx = ctx
        try:
            for p, pt in zip(e.params, ptypes):
                mode = p.mode if p.mode != "borrow" else "own"
                if isinstance(exp, TFn) and exp.modes and p.mode == "borrow":
                    mode = "own"
                lv = self.declare(p.name, pt, mode, p.pos)
                p.local = lv  # type: ignore[attr-defined]
            bt = self.expr(e.body, ret)
            self.unify_at(ret, bt, result_pos(e.body))
        finally:
            self.ctx = outer
        e.captures = list(ctx.captures.values())  # type: ignore[attr-defined]
        for lv in e.captures:  # type: ignore[attr-defined]
            if lv.owner is not outer and outer.lam is not None:
                outer.captures[lv.id] = lv
        io = True if ctx.calls_io else EVar()
        if ctx.calls_io and outer.lam is None and not outer.io and ctx.byref:
            self.err("io", e.pos, "função pura não pode chamar função `io`; marque a função com `io`")
        if ctx.calls_io and outer.lam is not None and ctx.byref:
            outer.calls_io = True
        mark_tail(e.body, True, self)
        return TFn(ptypes, ret, io, [p.mode for p in e.params])

    def e_If(self, e: A.If, expected: Type | None) -> Type:
        ct = self.expr(e.cond, BOOL)
        self.unify_at(BOOL, ct, e.cond.pos, "na condição do `if`")
        self.push()
        tt = self.expr(e.then, expected)
        self.pop()
        if e.els is None:
            if not self.unify_at_silent(tt, UNIT):
                self.err("tipo", e.pos, f"`if` sem `else` precisa resultar em `()`, mas resulta em `{show(tt)}`")
            return UNIT
        self.push()
        et = self.expr(e.els, tt)
        self.pop()
        self.unify_at(tt, et, result_pos(e.els), "entre os ramos do `if`")
        return tt

    def unify_at_silent(self, a: Type, b: Type) -> bool:
        try:
            unify(a, b)
            return True
        except UnifyError:
            return False

    def e_Block(self, e: A.Block, expected: Type | None) -> Type:
        self.push()
        try:
            for s in e.stmts:
                try:
                    self.stmt(s)
                except Abort:
                    pass
            if e.result is None:
                return UNIT
            return self.expr(e.result, expected)
        finally:
            self.pop()

    def e_While(self, e: A.While, expected: Type | None) -> Type:
        self.unify_at(BOOL, self.expr(e.cond, BOOL), e.cond.pos)
        self.push()
        self.expr(e.body)
        self.pop()
        return UNIT

    def e_Tuple(self, e: A.Tuple, expected: Type | None) -> Type:
        return TCon("#", [self.expr(x) for x in e.items])

    def e_ListLit(self, e: A.ListLit, expected: Type | None) -> Type:
        el = self.fresh()
        for x in e.items:
            self.unify_at(el, self.expr(x, el), x.pos, "entre os itens da lista")
        return TCon("List", [el])

    def e_MapLit(self, e: A.MapLit, expected: Type | None) -> Type:
        k, v = self.fresh(), self.fresh()
        for a, b in e.pairs:
            self.unify_at(k, self.expr(a, k), a.pos)
            self.unify_at(v, self.expr(b, v), b.pos)
        self.wanted.append(("Eq", k, e.pos))
        ent = self.prelude.values.get("Map") if self.prelude else None
        e.ctor = ent  # type: ignore[attr-defined]
        return TCon("Map", [k, v])

    def e_With(self, e: A.With, expected: Type | None) -> Type:
        t = self.expr(e.expr, expected)
        for name, val, pos in e.fields:
            fld = self.field_of(t, name)
            if fld is None:
                self.err("nome", pos, f"`{show(t)}` não tem campo `{name}`")
                continue
            ctor, idx, ft = fld
            self.unify_at(ft, self.expr(val, ft), val.pos)
        pt = prune(t)
        e.ctor = self.find_type_info(pt.name).ctors[0] if isinstance(pt, TCon) and self.find_type_info(pt.name) else None  # type: ignore[attr-defined]
        return t

    def e_LazyE(self, e: A.LazyE, expected: Type | None) -> Type:
        lam = A.Lambda(e.pos, [], e.expr)
        e.lam = lam  # type: ignore[attr-defined]
        t = self.expr(lam)
        assert isinstance(t, TFn)
        return TCon("Lazy", [t.ret])

    def e_Yield(self, e: A.Yield, expected: Type | None) -> Type:
        assert self.ctx is not None
        root = self.ctx.root
        if self.ctx.lam is not None or root.info is None or not root.info.is_gen:
            self.err("sintaxe", e.pos, "`yield` só pode aparecer no corpo de um generator")
            return UNIT
        gt = prune(root.ret)
        el = gt.args[0] if isinstance(gt, TCon) and gt.name == "Gen" else self.fresh()
        self.unify_at(el, self.expr(e.expr, el), e.expr.pos)
        return UNIT

    def e_For(self, e: A.For, expected: Type | None) -> Type:
        # só chega aqui em generators (loop inline com yield)
        for g in e.gens:
            st = self.expr(g.src)
            el = self.fresh()
            self.unify_at(TCon(head_name(st) or "Gen", [el]) if isinstance(prune(st), TCon) else st, st, g.src.pos)
            pst = prune(st)
            if isinstance(pst, TCon) and pst.args:
                el = pst.args[-1] if pst.name != "Map" else TCon("#", list(pst.args))
            self.push()
            pat = g.pats[0] if len(g.pats) == 1 else A.PTuple(g.pos, g.pats)
            g.pat = pat  # type: ignore[attr-defined]
            self.pat(pat, el, "let")
        assert e.body is not None
        self.expr(e.body)
        for _ in e.gens:
            self.pop()
        return UNIT

    def e_BuiltinCall(self, e: A.BuiltinCall, expected: Type | None) -> Type:
        if e.name == "debug":
            if len(e.args) != 1:
                self.err("aridade", e.pos, "`@debug` recebe um argumento")
                raise Abort()
            t = self.expr(e.args[0], expected)
            tr = self.find_trait("Format")
            assert tr is not None
            mi = tr.methods["format"]
            _, inst = self.instantiate(mi.scheme, e.pos)
            self.unify_at(inst[0], t, e.pos)
            e.ref = Ref("method", mi, inst)  # type: ignore[attr-defined]
            return t
        if e.name == "tail":
            inner = e.args[0]
            if not isinstance(inner, A.Call):
                self.err("cauda", e.pos, "`@tail` precisa de uma chamada")
                return self.expr(inner, expected)
            inner.tail_required = True  # type: ignore[attr-defined]
            return self.expr(inner, expected)
        if e.name == "prim":
            self.err("sintaxe", e.pos, "`@prim` só existe no prelude")
            return self.fresh()
        self.err("nome", e.pos, f"builtin `@{e.name}` desconhecido aqui")
        raise Abort()

    def e_Match(self, e: A.Match, expected: Type | None) -> Type:
        st = self.expr(e.scrut)
        res = expected if expected is not None else self.fresh()
        res_t: Type | None = None
        for arm in e.arms:
            self.push()
            try:
                self.pat(arm.pat, st, "let")
                if arm.guard is not None:
                    self.unify_at(BOOL, self.expr(arm.guard, BOOL), arm.guard.pos)
                bt = self.expr(arm.body, res)
                if res_t is None:
                    res_t = bt
                else:
                    self.unify_at(res_t, bt, result_pos(arm.body), "entre os casos do `match`")
            except Abort:
                pass
            finally:
                self.pop()
        self.check_exhaustive(e, st)
        return res_t if res_t is not None else self.fresh()

    # ------------------------------------------------------------------ instruções
    def stmt(self, s: A.Stmt) -> None:
        assert self.ctx is not None
        if isinstance(s, A.ExprStmt):
            self.expr(s.expr)
            return
        if isinstance(s, A.Assign):
            self.assign_field(s)
            return
        assert isinstance(s, A.Let)
        # `x = e` com x já ligado vira atribuição
        if not s.is_var and s.ann is None and self.is_assignment(s.pat):
            s.assign = True  # type: ignore[attr-defined]
            for name, pos in pat_names(s.pat):
                lv = self.find_local(name)
                assert lv is not None
                if lv.kind == "let":
                    self.err("reatribuicao", pos,
                             f"`{name}` não é `var`: ligações são imutáveis; declare com `var {name} = ...`")
                elif lv.kind in ("borrow", "own"):
                    self.err("posse", pos,
                             f"`{name}` é um parâmetro emprestado; declare-o `mut` para poder alterá-lo")
                elif lv.owner is not self.ctx and not self.ctx_byref_chain(lv):
                    self.err("posse", pos, f"closure não pode reatribuir `{name}`, capturado por move")
            t = self.expr(s.expr)
            self.pat_assign_types(s.pat, t)
            return
        exp = None
        if s.ann is not None:
            exp = self.type_expr(s.ann, {})
        t = self.expr(s.expr, exp)
        if exp is not None:
            self.unify_at(exp, t, s.expr.pos)
        self.pat(s.pat, t, "var" if s.is_var else "let", irrefutable=True)

    def ctx_byref_chain(self, lv: LocalVar) -> bool:
        c = self.ctx
        while c is not None and c is not lv.owner:
            if not c.byref:
                return False
            c = c.parent
        return True

    def is_assignment(self, p: A.Pat) -> bool:
        names = pat_names(p)
        if not names:
            return False
        found = [self.find_local_peek(n) for n, _ in names]
        return all(f is not None for f in found)

    def find_local_peek(self, name: str) -> LocalVar | None:
        c = self.ctx
        while c is not None:
            for sc in reversed(c.scopes):
                if name in sc:
                    return sc[name]
            c = c.parent
        return None

    def pat_assign_types(self, p: A.Pat, t: Type) -> None:
        if isinstance(p, A.PVar):
            lv = self.find_local(p.name)
            assert lv is not None
            p.local = lv  # type: ignore[attr-defined]
            self.unify_at(lv.ty, t, p.pos)
        elif isinstance(p, A.PTuple):
            items = [self.fresh() for _ in p.items]
            self.unify_at(TCon("#", items), t, p.pos)
            for sp, st in zip(p.items, items):
                self.pat_assign_types(sp, st)

    def assign_field(self, s: A.Assign) -> None:
        tgt = s.target
        if not isinstance(tgt, A.Dot) or not isinstance(tgt.obj, A.Name):
            self.err("sintaxe", s.pos, "só é possível atribuir a `nome.campo`")
            return
        lv = self.find_local(tgt.obj.name)
        if lv is None:
            self.err("nome", tgt.obj.pos, f"`{tgt.obj.name}` não está definido")
            return
        if lv.kind == "let":
            self.err("reatribuicao", s.pos, f"`{lv.name}` não é `var`")
        elif lv.kind in ("borrow", "own"):
            self.err("posse", s.pos, f"`{lv.name}` é um parâmetro emprestado; declare-o `mut` para poder alterá-lo")
        ft = self.expr(tgt)
        if getattr(tgt, "ref", None) is None or tgt.ref.kind != "field":  # type: ignore[attr-defined]
            self.err("tipo", s.pos, f"`{tgt.name}` não é um campo")
            return
        self.unify_at(ft, self.expr(s.expr, ft), s.expr.pos)

    # ------------------------------------------------------------------ padrões
    def pat(self, p: A.Pat, t: Type, kind: str, irrefutable: bool = False) -> None:
        p.ty = t  # type: ignore[attr-defined]
        if isinstance(p, A.PWild):
            return
        if isinstance(p, A.PVar):
            k = kind
            lv = self.declare(p.name, t, k, p.pos)
            p.local = lv  # type: ignore[attr-defined]
            return
        if isinstance(p, A.PLit):
            if p.kind in ("int", "float"):
                v = self.fresh()
                v.lits.add(p.kind)
                self.lit_metas.append(v)
                self.unify_at(t, v, p.pos)
                self.wanted.append(("Eq", t, p.pos))
            elif p.kind == "bool":
                self.unify_at(t, BOOL, p.pos)
            else:
                self.unify_at(t, TEXT, p.pos)
            if irrefutable:
                self.err("exaustividade", p.pos, "padrão refutável numa ligação")
            return
        if isinstance(p, A.PTuple):
            items = [self.fresh() for _ in p.items]
            self.unify_at(t, TCon("#", items), p.pos)
            for sp, st in zip(p.items, items):
                self.pat(sp, st, kind, irrefutable)
            return
        if isinstance(p, A.PCtor):
            if p.module is not None:
                ent = self.member(self.module_named(p.module, p.pos), p.name, p.pos, p.module)
            else:
                ent = self.lookup_value(p.name)
            if not isinstance(ent, CtorInfo):
                self.err("nome", p.pos, f"construtor `{p.name}` não existe")
                raise Abort()
            sch = ent.scheme()
            ct, inst = self.instantiate(sch, p.pos)
            p.ctor = ent  # type: ignore[attr-defined]
            if isinstance(ct, TFn):
                ftypes, res = ct.params, ct.ret
            else:
                ftypes, res = [], ct
            self.unify_at(t, res, p.pos)
            args = p.args
            if any(n is not None for n in p.names):
                order: list[A.Pat] = [A.PWild(p.pos) for _ in ftypes]
                for n, a in zip(p.names, p.args):
                    if n not in ent.field_names:
                        self.err("nome", a.pos, f"`{p.name}` não tem campo `{n}`")
                        continue
                    order[ent.field_names.index(n)] = a
                args = order
                p.args = args
                p.names = [None] * len(args)
            if len(args) != len(ftypes):
                self.err("aridade", p.pos, f"`{p.name}` tem {len(ftypes)} campo(s), o padrão tem {len(args)}")
                raise Abort()
            for sp, st in zip(args, ftypes):
                self.pat(sp, st, kind, irrefutable)
            if irrefutable and ent.type.kind == "sum" and len(ent.type.ctors) > 1:
                self.err("exaustividade", p.pos, "padrão refutável numa ligação")
            return

    # ------------------------------------------------------------------ exaustividade
    def ctors_of(self, t: Type) -> list[tuple[str, list[Type]]] | None:
        t = prune(t)
        if isinstance(t, TCon):
            if t.name == "bool":
                return [("true", []), ("false", [])]
            if t.name == "()":
                return [("()", [])]
            if t.name == "#":
                return [("#", list(t.args))]
            ti = self.find_type_info(t.name)
            if ti is not None and ti.kind in ("record", "sum"):
                mp = {v.id: a for v, a in zip(ti.vars, t.args)}
                return [(c.name, [subst(ft, mp) for ft in c.field_types]) for c in ti.ctors]
        return None

    def pat_head(self, p: A.Pat) -> tuple[str, list[A.Pat]] | None:
        if isinstance(p, (A.PWild, A.PVar)):
            return None
        if isinstance(p, A.PTuple):
            return ("#", p.items)
        if isinstance(p, A.PCtor):
            return (p.name, p.args)
        if isinstance(p, A.PLit):
            if p.kind == "bool":
                return ("true" if p.value else "false", [])
            return (f"lit:{p.value!r}", [])
        return None

    def missing(self, rows: list[list[A.Pat]], tys: list[Type], depth: int = 0) -> list[list[str]]:
        if not tys:
            return [] if rows else [[]]
        if depth > 40:
            return []
        heads = [self.pat_head(r[0]) for r in rows]
        ctors = self.ctors_of(tys[0])
        used = {h[0] for h in heads if h is not None}
        if ctors is not None and ctors and all(c in used for c, _ in ctors):
            out: list[list[str]] = []
            for cname, argtys in ctors:
                n = len(argtys)
                spec = []
                for r, h in zip(rows, heads):
                    if h is None:
                        spec.append([A.PWild(r[0].pos)] * n + r[1:])
                    elif h[0] == cname:
                        spec.append(list(h[1]) + r[1:])
                for m in self.missing(spec, argtys + tys[1:], depth + 1):
                    args, rest = m[:n], m[n:]
                    out.append([render_ctor(cname, args)] + rest)
                if out:
                    return out
            return out
        default = [r[1:] for r, h in zip(rows, heads) if h is None]
        res = self.missing(default, tys[1:], depth + 1)
        if not res:
            return []
        if ctors is None:
            ex = "_"
        else:
            miss = [c for c, a in ctors if c not in used]
            cname = miss[0] if miss else "_"
            arity = dict(ctors).get(cname, [])
            ex = render_ctor(cname, ["_"] * len(arity)) if cname != "_" else "_"
            if len(miss) > 1:
                return [[render_ctor(c, ["_"] * len(dict(ctors)[c]))] + res[0] for c in miss]
        return [[ex] + m for m in res]

    def check_exhaustive(self, e: A.Match, st: Type) -> None:
        rows = [[arm.pat] for arm in e.arms if arm.guard is None]
        miss = self.missing(rows, [st])
        if miss:
            cases = ", ".join(m[0] for m in miss)
            self.err("exaustividade", e.pos, f"`match` não exaustivo: faltam {cases}")


def render_ctor(name: str, args: list[str]) -> str:
    if name == "#":
        return "#(" + " ".join(args) + ")"
    if not args:
        return name
    return f"{name}(" + " ".join(args) + ")"


def key_is_int(t: Type) -> bool:
    t = prune(t)
    if isinstance(t, TVar) and not t.rigid:
        try:
            unify(t, TCon("i64"))
        except UnifyError:
            return False
        return True
    return isinstance(t, TCon) and t.name == "i64"


def pat_names(p: A.Pat) -> list[tuple[str, Pos]]:
    if isinstance(p, A.PVar):
        return [(p.name, p.pos)]
    if isinstance(p, A.PTuple):
        out = []
        for x in p.items:
            out += pat_names(x)
        return out
    return []


def result_pos(e: A.Expr) -> Pos:
    while isinstance(e, A.Block) and e.result is not None:
        e = e.result
    return e.pos


def names_in(e: Any) -> list[str]:
    out: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, A.Name):
            out.append(x.name)
        elif isinstance(x, A.BinOp):
            out.append(x.op)
        elif isinstance(x, A.Dot):
            out.append(x.name)
        if isinstance(x, A.Node) or isinstance(x, A.InterpPart):
            for k, v in vars(x).items():
                if k in ("pos", "ty", "ref"):
                    continue
                walk(v)
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)

    walk(e)
    return out


def mark_tail(e: Any, tail: bool, ck: Checker) -> None:
    """Marca chamadas em posição de cauda e valida `@tail`."""
    if isinstance(e, A.Call):
        e.tail = tail  # type: ignore[attr-defined]
        if getattr(e, "tail_required", False) and not tail:
            ck.err("cauda", e.pos, "`@tail`: esta chamada não está em posição de cauda")
        mark_tail(e.fn, False, ck)
        for a in e.args:
            mark_tail(a, False, ck)
        return
    if isinstance(e, A.BuiltinCall) and e.name == "tail":
        mark_tail(e.args[0], tail, ck)
        return
    if isinstance(e, A.Block):
        for s in e.stmts:
            mark_tail(s, False, ck)
        if e.result is not None:
            mark_tail(e.result, tail, ck)
        return
    if isinstance(e, A.If):
        mark_tail(e.cond, False, ck)
        mark_tail(e.then, tail, ck)
        if e.els is not None:
            mark_tail(e.els, tail, ck)
        return
    if isinstance(e, A.Match):
        mark_tail(e.scrut, False, ck)
        for arm in e.arms:
            if arm.guard is not None:
                mark_tail(arm.guard, False, ck)
            mark_tail(arm.body, tail, ck)
        return
    if isinstance(e, A.Lambda):
        return  # marcada ao checar a própria lambda
    if isinstance(e, A.BinOp):
        e.tail = tail and e.op not in ("&&", "||")  # type: ignore[attr-defined]
        mark_tail(e.left, False, ck)
        mark_tail(e.right, e.op in ("&&", "||") and tail, ck)
        return
    if isinstance(e, (A.Node, A.InterpPart)):
        for k, v in vars(e).items():
            if k in ("pos", "ty", "ref"):
                continue
            if isinstance(v, (A.Node, A.InterpPart)):
                mark_tail(v, False, ck)
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, (A.Node, A.InterpPart)):
                        mark_tail(x, False, ck)
                    elif isinstance(x, tuple):
                        for y in x:
                            if isinstance(y, A.Node):
                                mark_tail(y, False, ck)


# ====================================================================== prelude gerado
INT_PRIMS = ["Add", "Sub", "Mul", "Div", "Rem", "Pow"]
OPS = {"Add": "+", "Sub": "-", "Mul": "*", "Div": "/", "Rem": "%", "Pow": "**"}
CONV_TARGETS = NUM_TYPES + ["int", "float"]


def generated_prelude() -> str:
    out: list[str] = []
    for tgt in CONV_TARGETS:
        cls = "To" + tgt.upper()
        out.append(f"trait +{cls}(T) =\n    fn +(x: T).{tgt} -> {tgt}\n")
    for t in NUM_TYPES:
        for tr in INT_PRIMS:
            out.append(f"impl {tr}({t}) =\n    fn (a) {OPS[tr]} (b) = @prim(\"op_{tr.lower()}\")\n")
        out.append(f"impl Eq({t}) =\n    fn (a) == (b) = @prim(\"op_eq\")\n    fn (a) != (b) = @prim(\"op_ne\")\n")
        out.append(
            f"impl Ord({t}) =\n    fn (a) < (b) = @prim(\"op_lt\")\n    fn (a) <= (b) = @prim(\"op_le\")\n"
            f"    fn (a) > (b) = @prim(\"op_gt\")\n    fn (a) >= (b) = @prim(\"op_ge\")\n")
        out.append(f"impl Num({t}) =\n    fn fromInt(x) = @prim(\"conv\")\n")
        out.append(f"impl Format({t}) =\n    fn format(x spec) = @prim(\"fmt_num\")\n")
        out.append(f"impl Clone({t}) =\n    fn (x).copy = x\n")
        for tgt in CONV_TARGETS:
            out.append(f"impl To{tgt.upper()}({t}) =\n    fn (x).{tgt} = @prim(\"conv\")\n")
    return "\n".join(out)


__all__ = ["Checker", "ModuleInfo", "ModuleRef", "Alias", "FnInfo", "Ref", "CtorInfo", "TypeInfo", "ImplInfo", "MethodInfo",
           "TraitInfo", "LocalVar", "generated_prelude", "FLOAT_TYPES", "INT_TYPES"]
