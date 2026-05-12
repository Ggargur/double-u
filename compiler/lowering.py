"""
AST -> IR lowering.
Handles functions, entities (structs + method functions), for loops,
match (literal patterns), field access, constructor calls, and index.

Key features:
- Operator desugaring: a + b → TypeName__add(a, b) when left is entity type
- Implicit self: bare field/method names inside entity methods
- Expression type inference: let v = entity_method() → correct C type
- Entity aliases: entity Foo = Bar → typedef Bar Foo
"""

from __future__ import annotations

from .parser import (
    Program,
    FunctionDecl,
    EntityDecl,
    EntityAlias,
    ComponentDecl,
    FieldDecl,
    MethodDecl,
    Binding,
    Block,
    Param,
    PrimitiveType,
    NullableType,
    TypeRef,
    ListType,
    IntersectionType,
    IntLit,
    FloatLit,
    StringLit,
    BoolLit,
    NullLit,
    NameExpr,
    SelfExpr,
    OurExpr,
    BinOp,
    UnaryOp,
    Call,
    FieldAccess,
    OptionalChain,
    Index,
    Argument,
    IfStmt,
    ForStmt,
    MatchStmt,
    MatchArm,
    Assignment,
    EarlyReturn,
    ConstructorCall,
    WildcardPat,
    LiteralPat,
    BindingPat,
    NominalPattern,
    ListLit,
    MapLit,
    WhenStmt,
)
from .ir import (
    IRProgram,
    IRFunction,
    IRStructType,
    IRStructField,
    IRTypeAlias,
    IRVarDecl,
    IRAssign,
    IRFieldAssign,
    IRPtrFieldAssign,
    IRExprStmt,
    IRIf,
    IRFor,
    IRArrayDecl,
    IRReturn,
    IRConst,
    IRVar,
    IRBinOp,
    IRUnaryOp,
    IRCall,
    IRFieldAccess,
    IRPtrFieldAccess,
    IRIndex,
    IRNew,
    IRCast,
    IRTernary,
    IRCharBuf,
    IRExpr,
    IRStmt,
)


class LoweringError(Exception):
    pass


# ── Type helpers ──────────────────────────────────────────────────────────────

PRIMITIVE_C = {
    "int":    "long",
    "float":  "double",
    "bool":   "bool",
    "string": "const char*",
    "unit":   "void",
}

# Operator symbol → entity method name (language design §11)
OPERATOR_METHODS: dict[str, str] = {
    "+": "add",
    "-": "sub",
    "*": "mul",
    "/": "div",
    "%": "mod",
}


def _type_to_c(type_node) -> str:
    if type_node is None:
        return "void"
    if isinstance(type_node, PrimitiveType):
        return PRIMITIVE_C.get(type_node.name, "long")
    if isinstance(type_node, NullableType):
        inner = _type_to_c(type_node.inner)
        if inner in ("long", "double", "bool"):
            return inner
        return f"{inner}*"
    if isinstance(type_node, TypeRef):
        if type_node.name in PRIMITIVE_C:
            return PRIMITIVE_C[type_node.name]
        return type_node.name
    if isinstance(type_node, ListType):
        elem = _type_to_c(type_node.element)
        return f"_LangList_{_sanitize(elem)}"
    if isinstance(type_node, IntersectionType):
        return _type_to_c(type_node.types[0])
    return "long"


def _type_to_name(type_node) -> str:
    if type_node is None:
        return "unit"
    if isinstance(type_node, PrimitiveType):
        return type_node.name
    if isinstance(type_node, NullableType):
        return _type_to_name(type_node.inner)
    if isinstance(type_node, TypeRef):
        return type_node.name
    if isinstance(type_node, ListType):
        return f"[{_type_to_name(type_node.element)}]"
    return "unit"


def _lang_type_to_c(lang_type: str) -> str:
    return PRIMITIVE_C.get(lang_type, lang_type)


def _sanitize(s: str) -> str:
    return s.replace("*", "ptr").replace(" ", "_").replace("[", "").replace("]", "")


# ── Type environment ──────────────────────────────────────────────────────────

class _TypeEnv:
    def __init__(self):
        self._scopes: list[dict[str, str]] = [{}]

    def push(self):
        self._scopes.append({})

    def pop(self):
        self._scopes.pop()

    def define(self, name: str, lang_type: str):
        self._scopes[-1][name] = lang_type

    def get(self, name: str) -> str | None:
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        return None


# ── Lowering context ──────────────────────────────────────────────────────────

class _LowerCtx:
    def __init__(
        self,
        type_env: _TypeEnv,
        entity_fields: dict[str, list[str]],
        entity_methods: dict[str, dict[str, MethodDecl]],
        entity_field_types: dict[str, dict[str, str]],
        entity_method_rets: dict[str, dict[str, str]],
        type_aliases: dict[str, str] | None = None,
        component_names: set[str] | None = None,
        component_to_entity: dict[str, str] | None = None,
        fn_return_types: dict[str, str] | None = None,
        current_entity: str | None = None,
        current_method_mut: bool = False,
        current_is_component: bool = False,
        current_entity_for_component: str | None = None,
    ):
        self.type_env = type_env
        self.entity_fields = entity_fields
        self.entity_methods = entity_methods
        self.entity_field_types = entity_field_types   # entity → {field → lang_type}
        self.entity_method_rets = entity_method_rets   # entity → {method → lang_type_ret}
        self.type_aliases: dict[str, str] = type_aliases or {}
        self.component_names: set[str] = component_names or set()
        self.component_to_entity: dict[str, str] = component_to_entity or {}
        self.fn_return_types: dict[str, str] = fn_return_types or {}  # top-level fn → return type
        self.current_entity = current_entity
        self.current_method_mut = current_method_mut
        self.current_is_component = current_is_component
        self.current_entity_for_component = current_entity_for_component
        self._tmp_counter = 0
        self.extra_includes: set[str] = set()  # shared across child contexts via _child()
        self.watchers: list[tuple] = []  # [(cond_ir, body_ir_stmts, last_var)]

    def canonical(self, lang_type: str) -> str:
        """Resolve a type-alias chain to the root entity that owns the methods."""
        seen: set[str] = set()
        while lang_type in self.type_aliases and lang_type not in seen:
            seen.add(lang_type)
            lang_type = self.type_aliases[lang_type]
        return lang_type

    def fresh(self, prefix: str = "_t") -> str:
        self._tmp_counter += 1
        return f"{prefix}{self._tmp_counter}"

    def child(self, **kwargs) -> "_LowerCtx":
        """Create a child context sharing the same extra_includes set."""
        c = _LowerCtx(
            type_env=_TypeEnv(),
            entity_fields=self.entity_fields,
            entity_methods=self.entity_methods,
            entity_field_types=self.entity_field_types,
            entity_method_rets=self.entity_method_rets,
            type_aliases=self.type_aliases,
            component_names=self.component_names,
            component_to_entity=self.component_to_entity,
            fn_return_types=self.fn_return_types,
            **kwargs,
        )
        c.extra_includes = self.extra_includes
        return c


# ── Expression type resolution ────────────────────────────────────────────────

def _resolve_expr_type(expr, ctx: _LowerCtx) -> str | None:
    """Return the language-level type of an expression, or None if unknown."""
    if isinstance(expr, NameExpr):
        # 1. Locals / params
        t = ctx.type_env.get(expr.name)
        if t:
            return t
        # 2. Implicit self field
        if ctx.current_entity:
            t = ctx.entity_field_types.get(ctx.current_entity, {}).get(expr.name)
            if t:
                return t
        return None

    if isinstance(expr, SelfExpr):
        return ctx.current_entity

    if isinstance(expr, OurExpr):
        return ctx.current_entity_for_component

    if isinstance(expr, (FieldAccess, OptionalChain)):
        obj_type = _resolve_expr_type(expr.obj, ctx)
        if obj_type:
            # Could be a field or a zero-arg method
            ft = ctx.entity_field_types.get(obj_type, {}).get(expr.field)
            if ft:
                return ft
            mt = ctx.entity_method_rets.get(obj_type, {}).get(expr.field)
            if mt:
                return mt
        return None

    if isinstance(expr, Call):
        callee = expr.callee
        if isinstance(callee, FieldAccess):
            obj_type = _resolve_expr_type(callee.obj, ctx)
            if obj_type:
                return ctx.entity_method_rets.get(obj_type, {}).get(callee.field)
        if isinstance(callee, NameExpr):
            name = callee.name
            if _is_type_name(name):
                return name   # constructor → returns the type
            if ctx.current_entity:
                ret = ctx.entity_method_rets.get(ctx.current_entity, {}).get(name)
                if ret:
                    return ret
            # Top-level function return type
            ret = ctx.fn_return_types.get(name)
            if ret:
                return ret
        return None

    if isinstance(expr, BinOp):
        # Comparison, equality, and logical operators always yield bool.
        if expr.op in ("==", "!=", "<", ">", "<=", ">=", "and", "or"):
            return "bool"
        left_type  = _resolve_expr_type(expr.left,  ctx)
        right_type = _resolve_expr_type(expr.right, ctx)
        method = OPERATOR_METHODS.get(expr.op)
        # Entity operator → return type of the method
        if left_type and left_type in ctx.entity_methods and method:
            ret = ctx.entity_method_rets.get(left_type, {}).get(method)
            if ret: return ret
        if right_type and right_type in ctx.entity_methods and method:
            ret = ctx.entity_method_rets.get(right_type, {}).get(method)
            if ret: return ret
        # Numeric promotion: if either side is float, result is float
        float_types = {"float", "double"}
        if left_type in float_types or right_type in float_types:
            return "float"
        return None

    if isinstance(expr, ConstructorCall):
        return expr.type_name

    return None


def _is_type_name(name: str) -> bool:
    return bool(name) and name[0].isupper()


def _is_mut_receiver(expr, ctx: _LowerCtx) -> bool:
    """True when expr is a pointer receiver (mut self, or our which is always a pointer)."""
    if isinstance(expr, OurExpr):
        return True  # our is always EntityName* in component methods
    return isinstance(expr, SelfExpr) and ctx.current_method_mut


# ── String literal helpers ────────────────────────────────────────────────────

def _fmt_spec(lang_type: str | None) -> str:
    if lang_type == "float":  return "%f"
    if lang_type == "bool":   return "%s"
    if lang_type == "string": return "%s"
    return "%ld"


def _lower_string_lit(node: StringLit, ctx: _LowerCtx, out_preamble: list) -> IRExpr:
    has_interp = any(isinstance(p, tuple) for p in node.parts)
    if not has_interp:
        return IRConst("".join(str(p) for p in node.parts))

    ctx.extra_includes.add("<stdio.h>")

    fmt_parts: list[str] = []
    args: list[IRExpr] = []
    for part in node.parts:
        if isinstance(part, tuple) and part[0] == "interp":
            expr_node = part[1]
            expr_ir   = lower_expr(expr_node, ctx, out_preamble)
            expr_type = _resolve_expr_type(expr_node, ctx)
            if expr_type == "bool":
                expr_ir = IRTernary(expr_ir, IRConst("true"), IRConst("false"))
            fmt_parts.append(_fmt_spec(expr_type))
            args.append(expr_ir)
        else:
            fmt_parts.append(str(part).replace("%", "%%"))

    buf = ctx.fresh("_s")
    out_preamble.append(IRCharBuf(buf, 512))
    out_preamble.append(IRExprStmt(
        IRCall("snprintf", [IRVar(buf), IRConst(512), IRConst("".join(fmt_parts))] + args)
    ))
    return IRVar(buf)


# ── Expression lowering ───────────────────────────────────────────────────────

def lower_expr(expr, ctx: _LowerCtx, out_preamble: list[IRStmt]) -> IRExpr:
    if isinstance(expr, IntLit):
        return IRConst(expr.value)
    if isinstance(expr, FloatLit):
        return IRConst(expr.value)
    if isinstance(expr, BoolLit):
        return IRConst(expr.value)
    if isinstance(expr, NullLit):
        return IRConst(None)
    if isinstance(expr, StringLit):
        return _lower_string_lit(expr, ctx, out_preamble)

    if isinstance(expr, NameExpr):
        name = expr.name
        # Implicit self: bare field name inside entity method → self.field
        if ctx.current_entity and name in ctx.entity_fields.get(ctx.current_entity, []):
            if ctx.type_env.get(name) is None:  # not shadowed by local
                if ctx.current_method_mut:
                    return IRPtrFieldAccess(IRVar("self"), name)
                return IRFieldAccess(IRVar("self"), name)
        return IRVar(name)

    if isinstance(expr, SelfExpr):
        return IRVar("self")

    if isinstance(expr, OurExpr):
        return IRVar("our")

    if isinstance(expr, BinOp):
        method     = OPERATOR_METHODS.get(expr.op)
        left_type  = _resolve_expr_type(expr.left,  ctx)
        right_type = _resolve_expr_type(expr.right, ctx)

        # Forward dispatch: left is entity → left.method(right)
        if left_type and left_type in ctx.entity_methods and method:
            if method in ctx.entity_methods.get(left_type, {}):
                left_ir  = lower_expr(expr.left,  ctx, out_preamble)
                right_ir = lower_expr(expr.right, ctx, out_preamble)
                canon = ctx.canonical(left_type)
                return IRCall(f"{canon}__{method}", [left_ir, right_ir])

        # Reverse dispatch: right is entity, left is not → right.method(left)
        # Handles: scalar * vector, scalar + vector, etc.
        if right_type and right_type in ctx.entity_methods and method:
            if method in ctx.entity_methods.get(right_type, {}):
                left_ir  = lower_expr(expr.left,  ctx, out_preamble)
                right_ir = lower_expr(expr.right, ctx, out_preamble)
                canon = ctx.canonical(right_type)
                return IRCall(f"{canon}__{method}", [right_ir, left_ir])

        # Primitive binary op
        left  = lower_expr(expr.left,  ctx, out_preamble)
        right = lower_expr(expr.right, ctx, out_preamble)
        return IRBinOp(expr.op, left, right)

    if isinstance(expr, UnaryOp):
        return IRUnaryOp(expr.op, lower_expr(expr.operand, ctx, out_preamble))

    if isinstance(expr, FieldAccess):
        obj = lower_expr(expr.obj, ctx, out_preamble)
        if _is_mut_receiver(expr.obj, ctx):
            return IRPtrFieldAccess(obj, expr.field)
        return IRFieldAccess(obj, expr.field)

    if isinstance(expr, OptionalChain):
        obj = lower_expr(expr.obj, ctx, out_preamble)
        return IRFieldAccess(obj, expr.field)

    if isinstance(expr, Index):
        obj = lower_expr(expr.obj, ctx, out_preamble)
        idx = lower_expr(expr.idx, ctx, out_preamble)
        return IRIndex(IRFieldAccess(obj, "data"), idx)

    if isinstance(expr, Call):
        return _lower_call(expr, ctx, out_preamble)

    if isinstance(expr, ConstructorCall):
        return _lower_constructor(expr, ctx, out_preamble)

    if isinstance(expr, ListLit):
        return _lower_list_literal(expr, ctx, out_preamble)

    if isinstance(expr, IfStmt):
        raise LoweringError(
            "If-expression in non-statement position is not supported by C backend."
        )

    if isinstance(expr, MatchStmt):
        raise LoweringError(
            "Match expression in non-statement position is not supported by C backend."
        )

    raise LoweringError(f"Unsupported expression for C lowering: {type(expr).__name__}")


def _lower_call(node: Call, ctx: _LowerCtx, preamble: list[IRStmt]) -> IRExpr:
    callee = node.callee
    args_ir = [lower_expr(a.value if isinstance(a, Argument) else a, ctx, preamble)
               for a in node.args]

    # obj.method(args) — method call on explicit receiver
    if isinstance(callee, FieldAccess):
        obj_ir = lower_expr(callee.obj, ctx, preamble)
        method_name = callee.field

        obj_lang_type = _resolve_expr_type(callee.obj, ctx)

        # Component method call: entity_expr.comp_field.method(args)
        if obj_lang_type and obj_lang_type in ctx.component_names:
            method_decl = ctx.entity_methods.get(obj_lang_type, {}).get(method_name)
            if method_decl:
                fn_name = f"{obj_lang_type}__{method_name}"
                self_arg = _self_arg_for_call(obj_ir, callee.obj, method_decl, ctx)
                # Build 'our': &entity_expr (the entity that owns this component)
                if isinstance(callee.obj, FieldAccess):
                    entity_expr = callee.obj.obj
                    entity_ir = lower_expr(entity_expr, ctx, preamble)
                    our_ir = IRUnaryOp("&", entity_ir)
                else:
                    our_ir = IRVar("our")  # already inside component method
                return IRCall(fn_name, [self_arg, our_ir] + args_ir)

        if obj_lang_type and obj_lang_type in ctx.entity_methods:
            method_decl = ctx.entity_methods[obj_lang_type].get(method_name)
            if method_decl:
                canon = ctx.canonical(obj_lang_type)
                fn_name = f"{canon}__{method_name}"
                self_arg = _self_arg_for_call(obj_ir, callee.obj, method_decl, ctx)
                return IRCall(fn_name, [self_arg] + args_ir)

        return IRCall(f"__unresolved__{method_name}", [obj_ir] + args_ir)

    # name(args) — direct call, constructor, or implicit-self method call
    if isinstance(callee, NameExpr):
        name = callee.name

        # Constructor (Earley resolves TypeName(args) as Call+NameExpr)
        if _is_type_name(name):
            field_names = ctx.entity_fields.get(name, [])
            if field_names or name in ctx.entity_fields:
                return IRNew(name, field_names[:len(args_ir)], args_ir)
            return IRCall(f"{name}__new", args_ir)

        # Implicit self method call: bare method name inside entity method body
        if ctx.current_entity and name in ctx.entity_methods.get(ctx.current_entity, {}):
            fn_name = f"{ctx.canonical(ctx.current_entity)}__{name}"
            method_decl = ctx.entity_methods[ctx.current_entity][name]
            # Adjust self arg based on caller/callee mut combination
            if method_decl.mut and not ctx.current_method_mut:
                # calling mut from non-mut: need &self (take address of value)
                self_arg = IRVar("&self")
            elif method_decl.mut:
                # calling mut from mut: self already pointer
                self_arg = IRVar("self")
            elif ctx.current_method_mut:
                # calling non-mut from mut: deref pointer to get value
                self_arg = IRUnaryOp("*", IRVar("self"))
            else:
                self_arg = IRVar("self")
            return IRCall(fn_name, [self_arg] + args_ir)

        return IRCall(name, args_ir)

    raise LoweringError(f"Unsupported callee form: {type(callee).__name__}")


def _self_arg_for_call(obj_ir: IRExpr, obj_expr, method_decl: MethodDecl, ctx: _LowerCtx) -> IRExpr:
    """Build the correct self argument for a method call on obj_expr."""
    caller_is_ptr = ctx.current_method_mut and isinstance(obj_expr, SelfExpr)

    if method_decl.mut:
        # Method expects TypeName* — if we have a value, take its address
        if isinstance(obj_ir, (IRFieldAccess, IRPtrFieldAccess)):
            # Field of self — need to take address; emit as compound expression in C via temp
            # For now emit directly (GCC will catch true issues)
            return obj_ir
        if not caller_is_ptr:
            # Obj is a local variable — take its address
            if isinstance(obj_ir, IRVar):
                return IRVar(f"&{obj_ir.name}")
        return obj_ir
    else:
        # Method expects TypeName (value) — if we have pointer, deref
        if caller_is_ptr:
            return IRUnaryOp("*", obj_ir)
        return obj_ir


def _lower_constructor(node: ConstructorCall, ctx: _LowerCtx, preamble: list[IRStmt]) -> IRExpr:
    type_name = node.type_name
    args_ir = [lower_expr(a.value if isinstance(a, Argument) else a, ctx, preamble)
               for a in node.args]
    field_names = ctx.entity_fields.get(type_name, [])
    if not field_names and args_ir:
        return IRCall(f"{type_name}__new", args_ir)
    return IRNew(type_name, field_names[:len(args_ir)], args_ir)


def _lower_list_literal(node: ListLit, ctx: _LowerCtx, preamble: list[IRStmt]) -> IRExpr:
    if not node.elements:
        raise LoweringError("Empty list literals in C backend require an explicit type annotation.")
    elems = [lower_expr(e, ctx, preamble) for e in node.elements]
    elem_c_type = _infer_const_c_type(elems[0])
    arr_name = ctx.fresh("_arr")
    list_name = ctx.fresh("_list")
    preamble.append(IRArrayDecl(arr_name, elem_c_type, elems))
    list_type = f"_LangList_{_sanitize(elem_c_type)}"
    preamble.append(IRVarDecl(list_name, list_type,
                               IRNew("_LangList_" + _sanitize(elem_c_type),
                                     ["len", "data"],
                                     [IRConst(len(elems)), IRVar(arr_name)])))
    return IRVar(list_name)


def _infer_const_c_type(ir: IRExpr) -> str:
    if isinstance(ir, IRConst):
        if isinstance(ir.value, bool):   return "bool"
        if isinstance(ir.value, int):    return "long"
        if isinstance(ir.value, float):  return "double"
        if isinstance(ir.value, str):    return "const char*"
    return "long"


# ── Watcher check emission ────────────────────────────────────────────────────

def _emit_watcher_checks(ctx: _LowerCtx, out: list[IRStmt]):
    """Emit watcher checks: fire if condition is true (value change is
    handled by the caller saving old value and comparing)."""
    for cond_ir, body_stmts in ctx.watchers:
        out.append(IRIf(cond_ir, list(body_stmts), []))


# ── Statement lowering ────────────────────────────────────────────────────────

def lower_stmt(stmt, ctx: _LowerCtx, out: list[IRStmt]):
    if isinstance(stmt, Binding):
        preamble: list[IRStmt] = []
        val = lower_expr(stmt.value, ctx, preamble)
        out.extend(preamble)
        if stmt.type:
            lang_type = _type_to_name(stmt.type)
            c_type    = _type_to_c(stmt.type)
        else:
            lang_type = _resolve_expr_type(stmt.value, ctx) or ""
            c_type    = _lang_type_to_c(lang_type) if lang_type else _infer_const_c_type(val)
        if c_type == "void":
            c_type = "long"
        out.append(IRVarDecl(stmt.name, c_type, val))
        ctx.type_env.define(stmt.name, lang_type)
        return

    if isinstance(stmt, Assignment):
        preamble: list[IRStmt] = []
        val = lower_expr(stmt.value, ctx, preamble)
        out.extend(preamble)
        target = stmt.target

        # Determine the IR for the target value (to save old value for watchers)
        has_watchers = len(ctx.watchers) > 0
        old_var = None
        target_ir = None

        if isinstance(target, NameExpr):
            tname = target.name
            # Implicit self field assignment in mut method
            if (ctx.current_entity and ctx.current_method_mut
                    and tname in ctx.entity_fields.get(ctx.current_entity, [])
                    and ctx.type_env.get(tname) is None):
                if has_watchers:
                    old_var = ctx.fresh("_old")
                    target_ir = IRPtrFieldAccess(IRVar("self"), tname)
                    lang_t = ctx.entity_field_types.get(ctx.current_entity, {}).get(tname, "")
                    out.append(IRVarDecl(old_var, _lang_type_to_c(lang_t) if lang_t else "long", target_ir))
                out.append(IRPtrFieldAssign("self", tname, val))
                if has_watchers and old_var:
                    target_ir_after = IRPtrFieldAccess(IRVar("self"), tname)
            else:
                if has_watchers:
                    old_var = ctx.fresh("_old")
                    lang_t = ctx.type_env.get(tname) or ""
                    out.append(IRVarDecl(old_var, _lang_type_to_c(lang_t) if lang_t else "long", IRVar(tname)))
                out.append(IRAssign(tname, stmt.op, val))
                if has_watchers and old_var:
                    target_ir_after = IRVar(tname)
        elif isinstance(target, FieldAccess):
            obj = target.obj
            if isinstance(obj, SelfExpr):
                if ctx.current_method_mut:
                    if has_watchers:
                        old_var = ctx.fresh("_old")
                        out.append(IRVarDecl(old_var, "long", IRPtrFieldAccess(IRVar("self"), target.field)))
                    out.append(IRPtrFieldAssign("self", target.field, val))
                    if has_watchers and old_var:
                        target_ir_after = IRPtrFieldAccess(IRVar("self"), target.field)
                else:
                    if has_watchers:
                        old_var = ctx.fresh("_old")
                        out.append(IRVarDecl(old_var, "long", IRFieldAccess(IRVar("self"), target.field)))
                    out.append(IRFieldAssign("self", target.field, val))
                    if has_watchers and old_var:
                        target_ir_after = IRFieldAccess(IRVar("self"), target.field)
            elif isinstance(obj, OurExpr):
                if has_watchers:
                    old_var = ctx.fresh("_old")
                    out.append(IRVarDecl(old_var, "long", IRPtrFieldAccess(IRVar("our"), target.field)))
                out.append(IRPtrFieldAssign("our", target.field, val))
                if has_watchers and old_var:
                    target_ir_after = IRPtrFieldAccess(IRVar("our"), target.field)
            elif isinstance(obj, NameExpr):
                if has_watchers:
                    old_var = ctx.fresh("_old")
                    out.append(IRVarDecl(old_var, "long", IRFieldAccess(IRVar(obj.name), target.field)))
                out.append(IRFieldAssign(obj.name, target.field, val))
                if has_watchers and old_var:
                    target_ir_after = IRFieldAccess(IRVar(obj.name), target.field)
            else:
                raise LoweringError("Complex assignment target not supported by C backend.")
        else:
            raise LoweringError(f"Unsupported assignment target: {type(target).__name__}")

        if has_watchers and old_var:
            # Only check watchers if the value actually changed
            watcher_stmts: list[IRStmt] = []
            _emit_watcher_checks(ctx, watcher_stmts)
            out.append(IRIf(IRBinOp("!=", target_ir_after, IRVar(old_var)), watcher_stmts, []))
        return

    if isinstance(stmt, EarlyReturn):
        preamble: list[IRStmt] = []
        value = lower_expr(stmt.value, ctx, preamble) if stmt.value is not None else None
        out.extend(preamble)
        out.append(IRReturn(value))
        return

    if isinstance(stmt, IfStmt):
        preamble: list[IRStmt] = []
        cond = lower_expr(stmt.cond, ctx, preamble)
        out.extend(preamble)
        then_out: list[IRStmt] = []
        _lower_block(stmt.then, ctx, then_out)
        else_out: list[IRStmt] = []
        if stmt.else_ is not None:
            if isinstance(stmt.else_, Block):
                _lower_block(stmt.else_, ctx, else_out)
            else:
                lower_stmt(stmt.else_, ctx, else_out)
        out.append(IRIf(cond, then_out, else_out))
        return

    if isinstance(stmt, ForStmt):
        preamble: list[IRStmt] = []
        iterable = lower_expr(stmt.iter, ctx, preamble)
        out.extend(preamble)
        iter_lang_type = _resolve_expr_type(stmt.iter, ctx)
        var_c_type = _resolve_for_var_c_type(iter_lang_type)
        body_out: list[IRStmt] = []
        ctx.type_env.push()
        ctx.type_env.define(stmt.var, _c_type_to_lang(var_c_type))
        _lower_block(stmt.body, ctx, body_out)
        ctx.type_env.pop()
        out.append(IRFor(
            var=stmt.var,
            var_type=var_c_type,
            iter_len=IRFieldAccess(iterable, "len"),
            iter_data=IRFieldAccess(iterable, "data"),
            body=body_out,
        ))
        return

    if isinstance(stmt, WhenStmt):
        preamble: list[IRStmt] = []
        cond_ir = lower_expr(stmt.cond, ctx, preamble)
        out.extend(preamble)
        body_out: list[IRStmt] = []
        _lower_block_no_return(stmt.body, ctx, body_out)
        ctx.watchers.append((cond_ir, body_out))
        return

    if isinstance(stmt, MatchStmt):
        preamble: list[IRStmt] = []
        val = lower_expr(stmt.expr, ctx, preamble)
        out.extend(preamble)
        tmp = ctx.fresh("_mv")
        val_lang_type = _resolve_expr_type(stmt.expr, ctx) or ""
        val_c_type = _lang_type_to_c(val_lang_type) if val_lang_type else _infer_ir_c_type(val)
        out.append(IRVarDecl(tmp, val_c_type, val))
        _lower_match(stmt.arms, IRVar(tmp), ctx, out)
        return

    # Expression statement fallback
    preamble: list[IRStmt] = []
    ir_expr = lower_expr(stmt, ctx, preamble)
    out.extend(preamble)
    out.append(IRExprStmt(ir_expr))


def _resolve_for_var_c_type(iter_lang_type: str | None) -> str:
    if iter_lang_type and iter_lang_type.startswith("["):
        inner = iter_lang_type[1:-1]
        return PRIMITIVE_C.get(inner, inner)
    return "long"


def _c_type_to_lang(c_type: str) -> str:
    reverse = {v: k for k, v in PRIMITIVE_C.items()}
    return reverse.get(c_type, c_type)


def _infer_ir_c_type(ir: IRExpr) -> str:
    if isinstance(ir, IRConst):
        return _infer_const_c_type(ir)
    if isinstance(ir, IRBinOp):
        return _infer_ir_c_type(ir.left)
    return "long"


def _lower_match(arms: list[MatchArm], val_ir: IRExpr, ctx: _LowerCtx, out: list[IRStmt]):
    if not arms:
        return
    arm  = arms[0]
    rest = arms[1:]
    pat  = arm.pattern

    if isinstance(pat, (WildcardPat, BindingPat)):
        body_out: list[IRStmt] = []
        if isinstance(pat, BindingPat):
            body_out.append(IRVarDecl(pat.name, _infer_ir_c_type(val_ir), val_ir))
        _lower_arm_body(arm, ctx, body_out)
        out.extend(body_out)
        return

    if isinstance(pat, LiteralPat):
        preamble: list[IRStmt] = []
        pat_val = lower_expr(pat.lit, ctx, preamble)
        out.extend(preamble)
        then_out: list[IRStmt] = []
        _lower_arm_body(arm, ctx, then_out)
        else_out: list[IRStmt] = []
        _lower_match(rest, val_ir, ctx, else_out)
        out.append(IRIf(IRBinOp("==", val_ir, pat_val), then_out, else_out))
        return

    if isinstance(pat, NominalPattern):
        body_out: list[IRStmt] = []
        _lower_arm_body(arm, ctx, body_out)
        if rest:
            else_out: list[IRStmt] = []
            _lower_match(rest, val_ir, ctx, else_out)
            out.append(IRIf(IRConst(True), body_out, else_out))
        else:
            out.extend(body_out)
        return

    raise LoweringError(f"Unsupported pattern for C backend: {type(pat).__name__}")


def _lower_arm_body(arm: MatchArm, ctx: _LowerCtx, out: list[IRStmt]):
    if isinstance(arm.body, Block):
        _lower_block(arm.body, ctx, out)
    else:
        preamble: list[IRStmt] = []
        expr_ir = lower_expr(arm.body, ctx, preamble)
        out.extend(preamble)
        out.append(IRReturn(expr_ir))


def _fix_void_tail(stmts: list[IRStmt]):
    """If the last stmt is IRReturn(non-None) in a void context, turn it into
    IRExprStmt + IRReturn(None).  Called by callers that know ret_type is void."""
    if stmts and isinstance(stmts[-1], IRReturn) and stmts[-1].value is not None:
        expr_val = stmts.pop().value
        stmts.append(IRExprStmt(expr_val))
        stmts.append(IRReturn(None))


def _lower_block(block: Block, ctx: _LowerCtx, out: list[IRStmt]):
    ctx.type_env.push()

    stmts = list(block.stmts)
    tail  = block.tail

    # Earley ambiguity: `^expr` may parse as EarlyReturn(None) + expr as tail.
    # Detect and merge back so `^0.0` becomes EarlyReturn(FloatLit(0.0)).
    if (stmts
            and isinstance(stmts[-1], EarlyReturn)
            and stmts[-1].value is None
            and tail is not None):
        stmts[-1] = EarlyReturn(tail)
        tail = None

    for stmt in stmts:
        lower_stmt(stmt, ctx, out)
    if tail is not None:
        preamble: list[IRStmt] = []
        tail_ir = lower_expr(tail, ctx, preamble)
        out.extend(preamble)
        out.append(IRReturn(tail_ir))
    ctx.type_env.pop()


def _lower_block_no_return(block: Block, ctx: _LowerCtx, out: list[IRStmt]):
    """Like _lower_block but treats the tail as an expression statement, not a return."""
    ctx.type_env.push()
    stmts = list(block.stmts)
    tail  = block.tail
    if (stmts
            and isinstance(stmts[-1], EarlyReturn)
            and stmts[-1].value is None
            and tail is not None):
        stmts[-1] = EarlyReturn(tail)
        tail = None
    for stmt in stmts:
        lower_stmt(stmt, ctx, out)
    if tail is not None:
        preamble: list[IRStmt] = []
        tail_ir = lower_expr(tail, ctx, preamble)
        out.extend(preamble)
        out.append(IRExprStmt(tail_ir))
    ctx.type_env.pop()


# ── Entity lowering ───────────────────────────────────────────────────────────

def _lower_entity(decl: EntityDecl, ctx: _LowerCtx, out_struct: list, out_fns: list):
    name = decl.name
    fields: list[IRStructField] = []
    field_names: list[str] = []
    for member in decl.members:
        if isinstance(member, FieldDecl):
            c_type = _type_to_c(member.type)
            fields.append(IRStructField(member.name, c_type))
            field_names.append(member.name)

    out_struct.append(IRStructType(name, fields))
    ctx.entity_fields[name] = field_names

    if field_names:
        params = [(f.name, f.c_type) for f in fields]
        out_fns.append(IRFunction(
            name=f"{name}__new",
            params=params,
            ret_type=name,
            stmts=[IRReturn(IRNew(name, field_names, [IRVar(n) for n, _ in params]))],
        ))

    for member in decl.members:
        if isinstance(member, MethodDecl):
            _lower_method(member, name, ctx, out_fns)


def _lower_method(method: MethodDecl, entity_name: str, ctx: _LowerCtx, out_fns: list):
    method_ctx = ctx.child(
        current_entity=entity_name,
        current_method_mut=method.mut,
    )
    method_ctx.type_env.define("self", entity_name)

    if method.mut:
        params = [("self", f"{entity_name}*")] + [
            (p.name, _type_to_c(p.type)) for p in (method.params or [])
        ]
    else:
        params = [("self", entity_name)] + [
            (p.name, _type_to_c(p.type)) for p in (method.params or [])
        ]

    for p in (method.params or []):
        method_ctx.type_env.define(p.name, _type_to_name(p.type))

    ret_type = _type_to_c(method.ret) if method.ret else "void"
    stmts: list[IRStmt] = []
    _lower_block(method.body, method_ctx, stmts)
    if ret_type == "void":
        _fix_void_tail(stmts)

    out_fns.append(IRFunction(
        name=f"{entity_name}__{method.name}",
        params=params,
        ret_type=ret_type,
        stmts=stmts,
    ))


# ── Component lowering ────────────────────────────────────────────────────────

def _lower_component(decl: ComponentDecl, ctx: _LowerCtx, out_struct: list, out_fns: list):
    name = decl.name
    entity_name = ctx.component_to_entity.get(name)

    fields: list[IRStructField] = []
    field_names: list[str] = []
    for member in decl.members:
        if isinstance(member, FieldDecl):
            c_type = _type_to_c(member.type)
            fields.append(IRStructField(member.name, c_type))
            field_names.append(member.name)

    out_struct.append(IRStructType(name, fields))
    ctx.entity_fields[name] = field_names

    for member in decl.members:
        if isinstance(member, MethodDecl):
            _lower_component_method(member, name, entity_name, ctx, out_fns)


def _lower_component_method(
    method: MethodDecl,
    comp_name: str,
    entity_name: str | None,
    ctx: _LowerCtx,
    out_fns: list,
):
    method_ctx = ctx.child(
        current_entity=comp_name,
        current_method_mut=method.mut,
        current_is_component=True,
        current_entity_for_component=entity_name,
    )
    method_ctx.type_env.define("self", comp_name)
    if entity_name:
        method_ctx.type_env.define("our", entity_name)

    if method.mut:
        self_param: tuple[str, str] = ("self", f"{comp_name}*")
    else:
        self_param = ("self", comp_name)

    our_c_type = f"{entity_name}*" if entity_name else "void*"
    our_param: tuple[str, str] = ("our", our_c_type)

    extra_params = [
        (p.name, _type_to_c(p.type)) for p in (method.params or [])
    ]
    params = [self_param, our_param] + extra_params

    for p in (method.params or []):
        method_ctx.type_env.define(p.name, _type_to_name(p.type))

    ret_type = _type_to_c(method.ret) if method.ret else "void"
    stmts: list[IRStmt] = []
    _lower_block(method.body, method_ctx, stmts)
    if ret_type == "void":
        _fix_void_tail(stmts)

    out_fns.append(IRFunction(
        name=f"{comp_name}__{method.name}",
        params=params,
        ret_type=ret_type,
        stmts=stmts,
    ))


# ── Top-level lowering ────────────────────────────────────────────────────────

def lower_function(fn: FunctionDecl, ctx: _LowerCtx) -> IRFunction:
    fn_ctx = ctx.child()
    params: list[tuple[str, str]] = []
    for p in (fn.params or []):
        c_type = _type_to_c(p.type)
        params.append((p.name, c_type))
        fn_ctx.type_env.define(p.name, _type_to_name(p.type))

    ret_type = _type_to_c(fn.ret) if fn.ret else "void"
    stmts: list[IRStmt] = []
    _lower_block(fn.body, fn_ctx, stmts)
    if ret_type == "void":
        _fix_void_tail(stmts)
    return IRFunction(name=fn.name, params=params, ret_type=ret_type, stmts=stmts)


def _extract_c_includes(program: Program) -> list[str]:
    """Collect #include directives from `import c.X` statements."""
    seen: set[str] = set()
    result: list[str] = []
    for imp in program.imports:
        if imp.module and imp.module[0] == "c" and len(imp.module) >= 2:
            header = f"<{imp.module[1]}.h>"
            if header not in seen:
                seen.add(header)
                result.append(header)
    return result


def lower_program(program: Program) -> IRProgram:
    # ── First pass: collect entity/component metadata ─────────────────────────
    entity_fields: dict[str, list[str]] = {}
    entity_methods: dict[str, dict[str, MethodDecl]] = {}
    entity_field_types: dict[str, dict[str, str]] = {}
    entity_method_rets: dict[str, dict[str, str]] = {}

    lang_type_aliases: dict[str, str] = {}   # alias → canonical (e.g. "Point" → "Vector")
    component_names: set[str] = set()
    component_to_entity: dict[str, str] = getattr(program, "component_to_entity", {})

    for decl in program.decls:
        if isinstance(decl, (EntityDecl, ComponentDecl)):
            entity_fields[decl.name] = [
                m.name for m in decl.members if isinstance(m, FieldDecl)
            ]
            entity_methods[decl.name] = {
                m.name: m for m in decl.members if isinstance(m, MethodDecl)
            }
            entity_field_types[decl.name] = {
                m.name: _type_to_name(m.type)
                for m in decl.members if isinstance(m, FieldDecl)
            }
            entity_method_rets[decl.name] = {
                m.name: _type_to_name(m.ret) if m.ret else "unit"
                for m in decl.members if isinstance(m, MethodDecl)
            }
            if isinstance(decl, ComponentDecl):
                component_names.add(decl.name)
        elif isinstance(decl, EntityAlias):
            target_name = getattr(decl.target, "name", None)
            if target_name:
                lang_type_aliases[decl.name] = target_name

    # Collect top-level function return types
    fn_return_types: dict[str, str] = {}
    for decl in program.decls:
        if isinstance(decl, FunctionDecl) and decl.ret:
            fn_return_types[decl.name] = _type_to_name(decl.ret)

    ctx = _LowerCtx(
        type_env=_TypeEnv(),
        entity_fields=entity_fields,
        entity_methods=entity_methods,
        entity_field_types=entity_field_types,
        entity_method_rets=entity_method_rets,
        type_aliases=lang_type_aliases,
        component_names=component_names,
        component_to_entity=component_to_entity,
        fn_return_types=fn_return_types,
    )

    # ── Second pass: emit IR ──────────────────────────────────────────────────
    struct_types: list[IRStructType] = []
    type_aliases: list[IRTypeAlias] = []
    fns_from_entities: list[IRFunction] = []
    globals_out: list[IRVarDecl] = []
    functions_out: list[IRFunction] = []

    for decl in program.decls:
        if isinstance(decl, EntityDecl):
            _lower_entity(decl, ctx, struct_types, fns_from_entities)

        elif isinstance(decl, ComponentDecl):
            _lower_component(decl, ctx, struct_types, fns_from_entities)

        elif isinstance(decl, EntityAlias):
            target_c = _type_to_c(decl.target)
            type_aliases.append(IRTypeAlias(name=decl.name, target=target_c))
            # Propagate field/method info so Point can dispatch as Vector
            target_name = getattr(decl.target, "name", None)
            if target_name and target_name in entity_fields:
                entity_fields[decl.name]      = entity_fields[target_name]
                entity_methods[decl.name]     = entity_methods.get(target_name, {})
                entity_field_types[decl.name] = entity_field_types.get(target_name, {})
                entity_method_rets[decl.name] = entity_method_rets.get(target_name, {})

        elif isinstance(decl, Binding):
            preamble: list[IRStmt] = []
            val = lower_expr(decl.value, ctx, preamble)
            if preamble:
                raise LoweringError("Global list/complex expressions not supported in C global scope.")
            lang_type = _resolve_expr_type(decl.value, ctx) or ""
            c_type = (_type_to_c(decl.type) if decl.type
                      else _lang_type_to_c(lang_type) if lang_type
                      else _infer_const_c_type(val))
            if c_type == "void":
                c_type = "long"
            globals_out.append(IRVarDecl(decl.name, c_type, val))
            ctx.type_env.define(decl.name, _type_to_name(decl.type) if decl.type else lang_type)

        elif isinstance(decl, FunctionDecl):
            functions_out.append(lower_function(decl, ctx))

    seen_includes: set[str] = set()
    c_includes: list[str] = []
    for h in _extract_c_includes(program):
        if h not in seen_includes:
            seen_includes.add(h)
            c_includes.append(h)
    for h in sorted(ctx.extra_includes):
        if h not in seen_includes:
            seen_includes.add(h)
            c_includes.append(h)

    return IRProgram(
        struct_types=struct_types,
        type_aliases=type_aliases,
        globals=globals_out,
        functions=fns_from_entities + functions_out,
        c_includes=c_includes,
    )
