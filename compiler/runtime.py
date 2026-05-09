"""
Runtime MVP (synchronous) for executing parsed programs.
"""

from __future__ import annotations

from dataclasses import dataclass

from .parser import (
    Program,
    EntityDecl,
    ComponentDecl,
    FieldDecl,
    MethodDecl,
    FunctionDecl,
    Binding,
    Block,
    Param,
    Argument,
    Assignment,
    IfStmt,
    ForStmt,
    MatchStmt,
    MatchArm,
    TryStmt,
    ThrowStmt,
    EarlyReturn,
    SpawnStmt,
    SelectStmt,
    SelectArm,
    NameExpr,
    BinOp,
    UnaryOp,
    FieldAccess,
    OptionalChain,
    Call,
    Index,
    NonNull,
    SelfExpr,
    OurExpr,
    ListLit,
    MapLit,
    MapEntry,
    ConstructorCall,
    IntLit,
    FloatLit,
    StringLit,
    BoolLit,
    NullLit,
    WildcardPat,
    LiteralPat,
    BindingPat,
    NominalPattern,
    StructuralPattern,
)
from .semantic import resolve_program, SemanticError
from .type_checker import check_program, TypeCheckError


class RuntimeError(Exception):
    pass


@dataclass
class _FunctionValue:
    name: str
    params: list[Param]
    body: Block


class _ReturnSignal(Exception):
    def __init__(self, value):
        super().__init__("return")
        self.value = value


class _LangRaised(Exception):
    def __init__(self, type_name: str, value=None):
        super().__init__(type_name)
        self.type_name = type_name
        self.value = value


class _Env:
    def __init__(self):
        self.scopes: list[dict[str, object]] = [dict()]

    def push(self):
        self.scopes.append({})

    def pop(self):
        self.scopes.pop()

    def define(self, name: str, value):
        self.scopes[-1][name] = value

    def assign(self, name: str, value):
        for scope in reversed(self.scopes):
            if name in scope:
                scope[name] = value
                return
        raise RuntimeError(f"Undefined variable '{name}'.")

    def get(self, name: str):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        raise RuntimeError(f"Undefined variable '{name}'.")


class Interpreter:
    def __init__(self, program: Program):
        self.program = program
        self.env = _Env()
        self.functions: dict[str, _FunctionValue] = {}
        self.entity_decls: dict[str, object] = {}
        self.entity_fields_map: dict[str, list[str]] = {}
        self.entity_methods_map: dict[str, dict[str, MethodDecl]] = {}
        self.component_names: set[str] = set()
        self._install_builtins()

    def _install_builtins(self):
        self.env.define("print", print)
        self.env.define("len", len)
        self.env.define("abs", abs)

    def load(self):
        for decl in self.program.decls:
            if isinstance(decl, (EntityDecl, ComponentDecl)):
                self.entity_decls[decl.name] = decl
                self.entity_fields_map[decl.name] = [
                    m.name for m in decl.members if isinstance(m, FieldDecl)
                ]
                self.entity_methods_map[decl.name] = {
                    m.name: m for m in decl.members if isinstance(m, MethodDecl)
                }
                if isinstance(decl, ComponentDecl):
                    self.component_names.add(decl.name)

        for decl in self.program.decls:
            if isinstance(decl, FunctionDecl):
                self.functions[decl.name] = _FunctionValue(
                    name=decl.name,
                    params=decl.params or [],
                    body=decl.body,
                )
                self.env.define(decl.name, self.functions[decl.name])

        for decl in self.program.decls:
            if isinstance(decl, Binding):
                value = self.eval_expr(decl.value)
                self.env.define(decl.name, value)

    def run(self, entry: str = "main", args: list | None = None):
        fn = self.functions.get(entry)
        if fn is None:
            raise RuntimeError(f"Entry function '{entry}' not found.")
        return self._call_function(fn, args or [])

    def _call_function(self, fn: _FunctionValue, args: list):
        if len(args) != len(fn.params):
            raise RuntimeError(
                f"Function '{fn.name}' expects {len(fn.params)} args, got {len(args)}."
            )
        self.env.push()
        try:
            for param, arg_value in zip(fn.params, args):
                self.env.define(param.name, arg_value)
            try:
                return self.eval_block(fn.body)
            except _ReturnSignal as ret:
                return ret.value
        finally:
            self.env.pop()

    def eval_block(self, block: Block):
        self.env.push()
        try:
            for stmt in block.stmts:
                self.exec_stmt(stmt)
            if block.tail is not None:
                return self.eval_expr(block.tail)
            return None
        finally:
            self.env.pop()

    def exec_stmt(self, stmt):
        if isinstance(stmt, Binding):
            self.env.define(stmt.name, self.eval_expr(stmt.value))
            return
        if isinstance(stmt, Assignment):
            self._assign_lvalue(stmt, self.eval_expr(stmt.value))
            return
        if isinstance(stmt, IfStmt):
            self.eval_if(stmt)
            return
        if isinstance(stmt, ForStmt):
            self.eval_for(stmt)
            return
        if isinstance(stmt, MatchStmt):
            self.eval_match(stmt)
            return
        if isinstance(stmt, TryStmt):
            self.eval_try(stmt)
            return
        if isinstance(stmt, ThrowStmt):
            self.eval_throw(stmt)
            return
        if isinstance(stmt, EarlyReturn):
            value = self.eval_expr(stmt.value) if stmt.value is not None else None
            raise _ReturnSignal(value)
        if isinstance(stmt, SpawnStmt):
            # Synchronous MVP: execute immediately.
            self.eval_block(stmt.body)
            return
        if isinstance(stmt, SelectStmt):
            # Synchronous MVP: execute first arm.
            if stmt.arms:
                self.eval_select_arm(stmt.arms[0])
            return

        # expression statement fallback
        self.eval_expr(stmt)

    def eval_expr(self, expr):
        if isinstance(expr, IntLit):
            return expr.value
        if isinstance(expr, FloatLit):
            return expr.value
        if isinstance(expr, BoolLit):
            return expr.value
        if isinstance(expr, NullLit):
            return None
        if isinstance(expr, StringLit):
            return self._eval_string(expr)
        if isinstance(expr, NameExpr):
            return self.env.get(expr.name)
        if isinstance(expr, SelfExpr):
            return self.env.get("self")
        if isinstance(expr, OurExpr):
            return self.env.get("our")
        if isinstance(expr, ListLit):
            return [self.eval_expr(e) for e in expr.elements]
        if isinstance(expr, MapLit):
            return {self.eval_expr(e.key): self.eval_expr(e.value) for e in expr.entries}
        if isinstance(expr, BinOp):
            return self._eval_binop(expr)
        if isinstance(expr, UnaryOp):
            return self._eval_unary(expr)
        if isinstance(expr, FieldAccess):
            obj = self.eval_expr(expr.obj)
            return self._field_get(obj, expr.field)
        if isinstance(expr, OptionalChain):
            obj = self.eval_expr(expr.obj)
            if obj is None:
                return None
            return self._field_get(obj, expr.field)
        if isinstance(expr, Index):
            obj = self.eval_expr(expr.obj)
            idx = self.eval_expr(expr.idx)
            return obj[idx]
        if isinstance(expr, NonNull):
            value = self.eval_expr(expr.expr)
            if value is None:
                raise RuntimeError("Non-null assertion failed.")
            return value
        if isinstance(expr, Call):
            return self._eval_call(expr)
        if isinstance(expr, ConstructorCall):
            return self._eval_constructor_call(expr)
        if isinstance(expr, IfStmt):
            return self.eval_if(expr)
        if isinstance(expr, MatchStmt):
            return self.eval_match(expr)
        if isinstance(expr, TryStmt):
            return self.eval_try(expr)
        if isinstance(expr, ThrowStmt):
            self.eval_throw(expr)
            return None
        if isinstance(expr, Block):
            return self.eval_block(expr)
        if isinstance(expr, EarlyReturn):
            value = self.eval_expr(expr.value) if expr.value is not None else None
            raise _ReturnSignal(value)

        raise RuntimeError(f"Unsupported expression node: {type(expr).__name__}")

    def eval_if(self, node: IfStmt):
        cond = self.eval_expr(node.cond)
        if cond:
            return self.eval_block(node.then)
        if node.else_ is not None:
            if isinstance(node.else_, Block):
                return self.eval_block(node.else_)
            return self.eval_if(node.else_)
        return None

    def eval_for(self, node: ForStmt):
        iterable = self.eval_expr(node.iter)
        for value in iterable:
            self.env.push()
            try:
                self.env.define(node.var, value)
                self.eval_block(node.body)
            finally:
                self.env.pop()
        return None

    def eval_match(self, node: MatchStmt):
        value = self.eval_expr(node.expr)
        for arm in node.arms:
            matched, bindings = self._match_pattern(arm.pattern, value)
            if not matched:
                continue
            self.env.push()
            try:
                for name, val in bindings.items():
                    self.env.define(name, val)
                if arm.guard is not None and not self.eval_expr(arm.guard):
                    continue
                if isinstance(arm.body, Block):
                    return self.eval_block(arm.body)
                return self.eval_expr(arm.body)
            finally:
                self.env.pop()
        return None

    def eval_try(self, node: TryStmt):
        try:
            return self.eval_block(node.body)
        except _LangRaised as err:
            for catch in node.catches:
                catch_type = getattr(catch.type, "name", None)
                if catch_type == err.type_name:
                    self.env.push()
                    try:
                        if catch.var:
                            self.env.define(catch.var, err.value)
                        return self.eval_block(catch.body)
                    finally:
                        self.env.pop()
            raise

    def eval_throw(self, node: ThrowStmt):
        expr = node.expr
        if isinstance(expr, NameExpr):
            raise _LangRaised(expr.name)
        value = self.eval_expr(expr)
        if isinstance(value, dict) and "__type__" in value:
            raise _LangRaised(str(value["__type__"]), value)
        raise _LangRaised(type(value).__name__, value)

    def eval_select_arm(self, arm: SelectArm):
        if isinstance(arm.body, Block):
            return self.eval_block(arm.body)
        return self.eval_expr(arm.body)

    def _eval_call(self, node: Call):
        callee_expr = node.callee

        # obj.method(args) — resolve before evaluating callee as a plain expression
        if isinstance(callee_expr, FieldAccess):
            method_name = callee_expr.field
            obj_expr = callee_expr.obj

            # entity_expr.comp_field.method(args) — component method with 'our' injection
            if isinstance(obj_expr, FieldAccess):
                outer_obj = self.eval_expr(obj_expr.obj)
                comp_field_name = obj_expr.field
                if isinstance(outer_obj, dict) and "__type__" in outer_obj:
                    comp_type = self._get_component_field_type(
                        self.entity_decls.get(outer_obj["__type__"]), comp_field_name
                    )
                    if comp_type is not None:
                        comp_val = outer_obj.get(comp_field_name)
                        args = [self.eval_expr(a.value if isinstance(a, Argument) else a) for a in node.args]
                        return self._call_method(comp_val, method_name, args, our=outer_obj)

            obj = self.eval_expr(obj_expr)
            args = [self.eval_expr(a.value if isinstance(a, Argument) else a) for a in node.args]
            return self._call_method(obj, method_name, args)

        args = [self.eval_expr(a.value if isinstance(a, Argument) else a) for a in node.args]

        # Bare name call — check for implicit self method call first
        if isinstance(callee_expr, NameExpr):
            name = callee_expr.name
            try:
                self_obj = self.env.get("self")
                if isinstance(self_obj, dict) and "__type__" in self_obj:
                    methods = self.entity_methods_map.get(self_obj["__type__"], {})
                    if name in methods:
                        our = None
                        try:
                            our = self.env.get("our")
                        except RuntimeError:
                            pass
                        return self._call_entity_method(methods[name], self_obj, args, our=our)
            except RuntimeError:
                pass

        callee = self.eval_expr(callee_expr)
        if isinstance(callee, _FunctionValue):
            return self._call_function(callee, args)
        if callable(callee):
            return callee(*args)
        raise RuntimeError("Attempted to call a non-callable value.")

    def _get_component_field_type(self, entity_decl, field_name: str):
        if entity_decl is None:
            return None
        for member in entity_decl.members:
            if isinstance(member, FieldDecl) and member.name == field_name:
                t = getattr(member.type, "name", None)
                if t in self.component_names:
                    return t
        return None

    def _call_method(self, obj, method_name: str, args: list, our=None):
        if isinstance(obj, dict) and "__type__" in obj:
            type_name = obj["__type__"]
            methods = self.entity_methods_map.get(type_name, {})
            method_decl = methods.get(method_name)
            if method_decl:
                return self._call_entity_method(method_decl, obj, args, our=our)
        if isinstance(obj, dict) and method_name in obj:
            val = obj[method_name]
            if callable(val):
                return val(*args)
        if hasattr(obj, method_name) and callable(getattr(obj, method_name)):
            return getattr(obj, method_name)(*args)
        raise RuntimeError(f"Method '{method_name}' not found on {type(obj).__name__}.")

    def _call_entity_method(self, method_decl: MethodDecl, self_obj, args: list, our=None):
        self.env.push()
        try:
            self.env.define("self", self_obj)
            if our is not None:
                self.env.define("our", our)
            for param, arg_value in zip(method_decl.params or [], args):
                self.env.define(param.name, arg_value)
            try:
                return self.eval_block(method_decl.body)
            except _ReturnSignal as ret:
                return ret.value
        finally:
            self.env.pop()

    def _eval_constructor_call(self, node: ConstructorCall):
        values = [self.eval_expr(a.value if isinstance(a, Argument) else a) for a in node.args]
        field_names = self.entity_fields_map.get(node.type_name, [])
        payload = {"__type__": node.type_name}
        for idx, value in enumerate(values):
            key = field_names[idx] if idx < len(field_names) else f"arg{idx}"
            payload[key] = value
        return payload

    def _assign_lvalue(self, assign: Assignment, value):
        target = assign.target
        if isinstance(target, NameExpr):
            current = self.env.get(target.name)
            self.env.assign(target.name, self._apply_assign_op(assign.op, current, value))
            return
        if isinstance(target, FieldAccess):
            obj = self.eval_expr(target.obj)
            if isinstance(obj, dict):
                current = obj.get(target.field)
                obj[target.field] = self._apply_assign_op(assign.op, current, value)
                return
            raise RuntimeError("Field assignment requires a map-like object.")
        if isinstance(target, Index):
            obj = self.eval_expr(target.obj)
            idx = self.eval_expr(target.idx)
            current = obj[idx]
            obj[idx] = self._apply_assign_op(assign.op, current, value)
            return
        raise RuntimeError("Invalid assignment target.")

    def _apply_assign_op(self, op: str, current, value):
        if op == "=":
            return value
        if op == "+=":
            return current + value
        if op == "-=":
            return current - value
        if op == "*=":
            return current * value
        if op == "/=":
            return current / value
        if op == "%=":
            return current % value
        raise RuntimeError(f"Unsupported assignment operator '{op}'.")

    def _eval_binop(self, node: BinOp):
        left = self.eval_expr(node.left)
        if node.op == "and":
            return bool(left) and bool(self.eval_expr(node.right))
        if node.op == "or":
            return bool(left) or bool(self.eval_expr(node.right))
        if node.op == "??":
            return left if left is not None else self.eval_expr(node.right)
        right = self.eval_expr(node.right)
        # Entity operator dispatch
        _op_method = {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "mod"}.get(node.op)
        if _op_method:
            if isinstance(left, dict) and "__type__" in left:
                methods = self.entity_methods_map.get(left["__type__"], {})
                if _op_method in methods:
                    return self._call_entity_method(methods[_op_method], left, [right])
            if isinstance(right, dict) and "__type__" in right:
                methods = self.entity_methods_map.get(right["__type__"], {})
                if _op_method in methods:
                    return self._call_entity_method(methods[_op_method], right, [left])
        if node.op == "+":
            return left + right
        if node.op == "-":
            return left - right
        if node.op == "*":
            return left * right
        if node.op == "/":
            return left / right
        if node.op == "%":
            return left % right
        if node.op == "==":
            return left == right
        if node.op == "!=":
            return left != right
        if node.op == "<":
            return left < right
        if node.op == ">":
            return left > right
        if node.op == "<=":
            return left <= right
        if node.op == ">=":
            return left >= right
        raise RuntimeError(f"Unsupported binary operator '{node.op}'.")

    def _eval_unary(self, node: UnaryOp):
        value = self.eval_expr(node.operand)
        if node.op == "-":
            return -value
        if node.op == "!":
            return not value
        raise RuntimeError(f"Unsupported unary operator '{node.op}'.")

    def _field_get(self, obj, field: str):
        if isinstance(obj, dict):
            if field in obj:
                return obj[field]
            raise RuntimeError(f"Field '{field}' not found.")
        return getattr(obj, field)

    def _eval_string(self, node: StringLit):
        out: list[str] = []
        for part in node.parts:
            if isinstance(part, tuple) and len(part) == 2 and part[0] == "interp":
                out.append(str(self.env.get(part[1].strip())))
            else:
                out.append(str(part))
        return "".join(out)

    def _match_pattern(self, pattern, value):
        if isinstance(pattern, WildcardPat):
            return True, {}
        if isinstance(pattern, LiteralPat):
            expected = self.eval_expr(pattern.lit)
            return expected == value, {}
        if isinstance(pattern, BindingPat):
            return True, {pattern.name: value}
        if isinstance(pattern, NominalPattern):
            if not isinstance(value, dict):
                return False, {}
            if value.get("__type__") != pattern.type_name:
                return False, {}
            return True, {}
        if isinstance(pattern, StructuralPattern):
            if not isinstance(value, dict):
                return False, {}
            for member in pattern.members:
                if member[0] == "field" and member[1] not in value:
                    return False, {}
            return True, {}
        return False, {}


def run_program(program: Program, entry: str = "main", args: list | None = None):
    try:
        resolve_program(program)
        check_program(program)
    except (SemanticError, TypeCheckError) as e:
        raise RuntimeError(str(e)) from e

    interp = Interpreter(program)
    interp.load()
    try:
        return interp.run(entry=entry, args=args or [])
    except _LangRaised as e:
        raise RuntimeError(f"Unhandled exception: {e.type_name}") from e
