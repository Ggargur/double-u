"""
IR -> C code generator.
"""

from __future__ import annotations

from .ir import (
    IRProgram,
    IRFunction,
    IRStructType,
    IRTypeAlias,
    IRVarDecl,
    IRAssign,
    IRFieldAssign,
    IRPtrFieldAssign,
    IRArrayDecl,
    IRExprStmt,
    IRIf,
    IRFor,
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


class CodegenError(Exception):
    pass


# ── Type name helper ──────────────────────────────────────────────────────────

def _c_type(type_name: str) -> str:
    mapping = {
        "int":         "long",
        "float":       "double",
        "bool":        "bool",
        "string":      "const char*",
        "unit":        "void",
        "long":        "long",
        "double":      "double",
        "const char*": "const char*",
        "void":        "void",
    }
    return mapping.get(type_name, type_name)


# ── Expression emitter ────────────────────────────────────────────────────────

def _emit_expr(expr: IRExpr) -> str:
    if isinstance(expr, IRConst):
        if expr.value is None:
            return "0"
        if isinstance(expr.value, bool):
            return "true" if expr.value else "false"
        if isinstance(expr.value, str):
            # The lexer preserves raw escape sequences (e.g. \n, \t, \\).
            # Only bare " needs escaping; backslashes are already C-valid.
            return f'"{expr.value}"'
        if isinstance(expr.value, float):
            # Ensure float literal is valid C (e.g. 1.0, not 1)
            s = repr(expr.value)
            return s if "." in s or "e" in s else s + ".0"
        return str(expr.value)

    if isinstance(expr, IRVar):
        return expr.name

    if isinstance(expr, IRUnaryOp):
        return f"({expr.op}{_emit_expr(expr.value)})"

    if isinstance(expr, IRBinOp):
        op = expr.op
        if op == "and":
            op = "&&"
        elif op == "or":
            op = "||"
        elif op == "??":
            left = _emit_expr(expr.left)
            right = _emit_expr(expr.right)
            return f"(({left}) ? ({left}) : ({right}))"
        return f"({_emit_expr(expr.left)} {op} {_emit_expr(expr.right)})"

    if isinstance(expr, IRCall):
        args = ", ".join(_emit_expr(a) for a in expr.args)
        return f"{expr.callee}({args})"

    if isinstance(expr, IRFieldAccess):
        return f"{_emit_expr(expr.obj)}.{expr.field}"

    if isinstance(expr, IRPtrFieldAccess):
        return f"{_emit_expr(expr.obj)}->{expr.field}"

    if isinstance(expr, IRIndex):
        return f"{_emit_expr(expr.obj)}[{_emit_expr(expr.idx)}]"

    if isinstance(expr, IRNew):
        if not expr.field_names:
            return f"({expr.type_name}){{}}"
        inits = ", ".join(
            f".{n} = {_emit_expr(v)}"
            for n, v in zip(expr.field_names, expr.args)
        )
        return f"({expr.type_name}){{{inits}}}"

    if isinstance(expr, IRCast):
        return f"(({expr.c_type}){_emit_expr(expr.value)})"

    if isinstance(expr, IRTernary):
        return f"({_emit_expr(expr.cond)} ? {_emit_expr(expr.then_val)} : {_emit_expr(expr.else_val)})"

    raise CodegenError(f"Unsupported IR expression: {type(expr).__name__}")


# ── Statement emitter ─────────────────────────────────────────────────────────

def _emit_stmt(stmt: IRStmt, out: list[str], indent: str = "    "):
    if isinstance(stmt, IRVarDecl):
        ct = _c_type(stmt.type_name)
        out.append(f"{indent}{ct} {stmt.name} = {_emit_expr(stmt.value)};")
        return

    if isinstance(stmt, IRArrayDecl):
        ct = _c_type(stmt.elem_type)
        items = ", ".join(_emit_expr(e) for e in stmt.elements)
        out.append(f"{indent}{ct} {stmt.name}[] = {{{items}}};")
        return

    if isinstance(stmt, IRCharBuf):
        out.append(f"{indent}char {stmt.name}[{stmt.size}];")
        return

    if isinstance(stmt, IRAssign):
        out.append(f"{indent}{stmt.name} {stmt.op} {_emit_expr(stmt.value)};")
        return

    if isinstance(stmt, IRFieldAssign):
        out.append(f"{indent}{stmt.obj_name}.{stmt.field} = {_emit_expr(stmt.value)};")
        return

    if isinstance(stmt, IRPtrFieldAssign):
        out.append(f"{indent}{stmt.obj_name}->{stmt.field} = {_emit_expr(stmt.value)};")
        return

    if isinstance(stmt, IRExprStmt):
        out.append(f"{indent}{_emit_expr(stmt.expr)};")
        return

    if isinstance(stmt, IRReturn):
        if stmt.value is None:
            out.append(f"{indent}return;")
        else:
            out.append(f"{indent}return {_emit_expr(stmt.value)};")
        return

    if isinstance(stmt, IRIf):
        out.append(f"{indent}if ({_emit_expr(stmt.cond)}) {{")
        for s in stmt.then_stmts:
            _emit_stmt(s, out, indent + "    ")
        out.append(f"{indent}}}")
        if stmt.else_stmts:
            out.append(f"{indent}else {{")
            for s in stmt.else_stmts:
                _emit_stmt(s, out, indent + "    ")
            out.append(f"{indent}}}")
        return

    if isinstance(stmt, IRFor):
        idx = f"_i_{stmt.var}"
        out.append(f"{indent}for (long {idx} = 0; {idx} < {_emit_expr(stmt.iter_len)}; {idx}++) {{")
        inner = indent + "    "
        out.append(f"{inner}{_c_type(stmt.var_type)} {stmt.var} = {_emit_expr(stmt.iter_data)}[{idx}];")
        for s in stmt.body:
            _emit_stmt(s, out, inner)
        out.append(f"{indent}}}")
        return

    raise CodegenError(f"Unsupported IR statement: {type(stmt).__name__}")


# ── Struct emitter ────────────────────────────────────────────────────────────

def _emit_struct(s: IRStructType, out: list[str]):
    out.append(f"typedef struct {{")
    for f in s.fields:
        out.append(f"    {_c_type(f.c_type)} {f.name};")
    out.append(f"}} {s.name};")
    out.append("")


# ── Function emitter ──────────────────────────────────────────────────────────

def _emit_function(fn: IRFunction, out: list[str]):
    params = ", ".join(f"{_c_type(t)} {n}" for n, t in fn.params)
    ret = _c_type(fn.ret_type)
    out.append(f"{ret} {fn.name}({params}) {{")
    for stmt in fn.stmts:
        _emit_stmt(stmt, out)
    if not fn.stmts or not isinstance(fn.stmts[-1], IRReturn):
        if ret == "void":
            out.append("    return;")
    out.append("}")
    out.append("")


# ── List struct emitter ───────────────────────────────────────────────────────

_EMITTED_LIST_TYPES: set[str] = set()


def _maybe_emit_list_type(c_elem_type: str, out: list[str]):
    from re import sub
    safe = c_elem_type.replace("*", "ptr").replace(" ", "_")
    struct_name = f"_LangList_{safe}"
    if struct_name in _EMITTED_LIST_TYPES:
        return
    _EMITTED_LIST_TYPES.add(struct_name)
    out.append(f"typedef struct {{")
    out.append(f"    long len;")
    out.append(f"    {c_elem_type}* data;")
    out.append(f"}} {struct_name};")
    out.append("")


def _collect_list_types(ir_prog: IRProgram) -> set[str]:
    """Walk IR to find all list element types used."""
    found: set[str] = set()

    def walk_expr(e: IRExpr):
        if isinstance(e, IRNew):
            if e.type_name.startswith("_LangList_"):
                # The struct has a 'data' field; infer elem type from data pointer arg
                if len(e.args) >= 2:
                    # args[1] is IRVar(arr_name); elem type from arr decl not available here
                    # Use a heuristic based on struct name
                    found.add(e.type_name)
            for a in e.args:
                walk_expr(a)
        elif isinstance(e, (IRBinOp,)):
            walk_expr(e.left); walk_expr(e.right)
        elif isinstance(e, IRFieldAccess):
            walk_expr(e.obj)
        elif isinstance(e, IRIndex):
            walk_expr(e.obj); walk_expr(e.idx)
        elif isinstance(e, IRCall):
            for a in e.args:
                walk_expr(a)

    def walk_stmt(s: IRStmt):
        if isinstance(s, IRVarDecl):
            walk_expr(s.value)
        elif isinstance(s, IRArrayDecl):
            for e in s.elements:
                walk_expr(e)
        elif isinstance(s, IRAssign):
            walk_expr(s.value)
        elif isinstance(s, IRExprStmt):
            walk_expr(s.expr)
        elif isinstance(s, IRReturn) and s.value:
            walk_expr(s.value)
        elif isinstance(s, IRIf):
            walk_expr(s.cond)
            for x in s.then_stmts + s.else_stmts:
                walk_stmt(x)
        elif isinstance(s, IRFor):
            walk_expr(s.iter_len); walk_expr(s.iter_data)
            for x in s.body:
                walk_stmt(x)

    for fn in ir_prog.functions:
        for s in fn.stmts:
            walk_stmt(s)
    return found


# ── Struct ordering ───────────────────────────────────────────────────────────

def _topo_sort_structs(structs: list[IRStructType]) -> list[IRStructType]:
    """Return structs in dependency order: depended-upon structs come first."""
    name_to_struct = {s.name: s for s in structs}
    visited: set[str] = set()
    result: list[IRStructType] = []

    def visit(name: str):
        if name in visited or name not in name_to_struct:
            return
        visited.add(name)
        for f in name_to_struct[name].fields:
            field_base = f.c_type.rstrip("*").strip()
            visit(field_base)
        result.append(name_to_struct[name])

    for s in structs:
        visit(s.name)
    return result


# ── Program emitter ───────────────────────────────────────────────────────────

def emit_c_program(ir_prog: IRProgram) -> str:
    _EMITTED_LIST_TYPES.clear()
    out: list[str] = ["#include <stdbool.h>"]
    for inc in ir_prog.c_includes:
        out.append(f"#include {inc}")
    out.append("")

    # Build alias-by-target map so each alias is emitted right after its struct
    alias_by_target: dict[str, IRTypeAlias] = {a.target: a for a in ir_prog.type_aliases}
    # Aliases whose target is not a local struct (e.g. primitive alias) — emit upfront
    local_struct_names = {s.name for s in ir_prog.struct_types}
    for a in ir_prog.type_aliases:
        if a.target not in local_struct_names:
            out.append(f"typedef {a.target} {a.name};")
    if any(a.target not in local_struct_names for a in ir_prog.type_aliases):
        out.append("")

    # Struct declarations + inline typedefs (dependency order)
    for s in _topo_sort_structs(ir_prog.struct_types):
        _emit_struct(s, out)
        if s.name in alias_by_target:
            a = alias_by_target[s.name]
            out.append(f"typedef {a.target} {a.name};")
            out.append("")

    # List type structs (detected by walking IR)
    list_struct_names = _collect_list_types(ir_prog)
    for ls_name in sorted(list_struct_names):
        # Reconstruct elem type from struct name: _LangList_<safe_elem>
        prefix = "_LangList_"
        safe_elem = ls_name[len(prefix):]
        # Reverse the sanitization: ptr→*, _→ (space in "const char*")
        elem_c_type = safe_elem.replace("ptr", "*").replace("const_char", "const char")
        if elem_c_type.endswith("*") and not elem_c_type.startswith("const"):
            elem_c_type = elem_c_type  # keep as-is for non-const pointers
        _maybe_emit_list_type(elem_c_type, out)

    # Global variables
    for g in ir_prog.globals:
        ct = _c_type(g.type_name)
        out.append(f"static {ct} {g.name} = {_emit_expr(g.value)};")
    if ir_prog.globals:
        out.append("")

    # Forward declarations — lets callers precede callees in any order
    for fn in ir_prog.functions:
        params = ", ".join(f"{_c_type(t)} {n}" for n, t in fn.params)
        out.append(f"{_c_type(fn.ret_type)} {fn.name}({params});")
    if ir_prog.functions:
        out.append("")

    # Function definitions
    for fn in ir_prog.functions:
        _emit_function(fn, out)

    return "\n".join(out).rstrip() + "\n"
