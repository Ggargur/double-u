"""
Type checker v1.
Current scope:
- TypeRef resolution against builtins, module symbols and generic scope
- Generic parameter scope/duplication checks
- Basic nullability and wildcard rules
"""

from __future__ import annotations

from dataclasses import dataclass

from parser import (
    Program,
    EntityDecl,
    EntityAlias,
    FieldDecl,
    MethodDecl,
    ConstructorDecl,
    CapabilityDecl,
    CapFieldMember,
    CapMethodMember,
    FunctionDecl,
    Binding,
    Param,
    AttributeDecl,
    ExceptionDecl,
    ExtendDecl,
    GenericParam,
    WhereClause,
    PrimitiveType,
    NullableType,
    IntersectionType,
    TypeRef,
    ListType,
    MapType,
    CapabilityBody,
    SelfType,
    WildcardType,
)
from semantic import build_module_symbols, SemanticError


BUILTIN_TYPES = {"int", "float", "bool", "string", "unit"}


class TypeCheckError(Exception):
    pass


@dataclass
class _TypeContext:
    where: str
    allow_self: bool = False
    allow_wildcard: bool = False


def _dedupe_generic_names(params: list[GenericParam], where: str, errors: list[str]) -> set[str]:
    names: set[str] = set()
    for gp in params:
        if gp.name in names:
            errors.append(f"Duplicate generic parameter '{gp.name}' in {where}.")
        names.add(gp.name)
    return names


def _is_type_symbol(kind: str) -> bool:
    return kind in {"entity", "entity-alias", "capability", "exception"}


def _check_type_ref(name: str, generic_scope: set[str], module_symbols: dict, where: str, errors: list[str]) -> None:
    if name in BUILTIN_TYPES:
        return
    if name in generic_scope:
        return
    sym = module_symbols.get(name)
    if sym and _is_type_symbol(sym.kind):
        return
    errors.append(f"Unknown type '{name}' in {where}.")


def _check_type(type_node, ctx: _TypeContext, generic_scope: set[str], module_symbols: dict, errors: list[str]) -> None:
    if type_node is None:
        return

    if isinstance(type_node, PrimitiveType):
        return

    if isinstance(type_node, SelfType):
        if not ctx.allow_self:
            errors.append(f"'Self' is not allowed in {ctx.where}.")
        return

    if isinstance(type_node, WildcardType):
        if not ctx.allow_wildcard:
            errors.append(f"Wildcard type '_' is not allowed in {ctx.where}.")
        return

    if isinstance(type_node, TypeRef):
        _check_type_ref(type_node.name, generic_scope, module_symbols, ctx.where, errors)
        for arg in (type_node.args or []):
            _check_type(
                arg,
                _TypeContext(ctx.where, allow_self=ctx.allow_self, allow_wildcard=ctx.allow_wildcard),
                generic_scope,
                module_symbols,
                errors,
            )
        return

    if isinstance(type_node, ListType):
        _check_type(type_node.element, ctx, generic_scope, module_symbols, errors)
        return

    if isinstance(type_node, MapType):
        _check_type(type_node.key, ctx, generic_scope, module_symbols, errors)
        _check_type(type_node.value, ctx, generic_scope, module_symbols, errors)
        return

    if isinstance(type_node, CapabilityBody):
        for member in type_node.members:
            if isinstance(member, CapFieldMember):
                _check_type(
                    member.type,
                    _TypeContext(ctx.where, allow_self=ctx.allow_self, allow_wildcard=True),
                    generic_scope,
                    module_symbols,
                    errors,
                )
            elif isinstance(member, CapMethodMember):
                for arg in member.args:
                    _check_type(
                        arg,
                        _TypeContext(ctx.where, allow_self=ctx.allow_self, allow_wildcard=True),
                        generic_scope,
                        module_symbols,
                        errors,
                    )
                if member.ret is not None:
                    _check_type(
                        member.ret,
                        _TypeContext(ctx.where, allow_self=ctx.allow_self, allow_wildcard=True),
                        generic_scope,
                        module_symbols,
                        errors,
                    )
        return

    if isinstance(type_node, IntersectionType):
        for part in type_node.types:
            _check_type(part, ctx, generic_scope, module_symbols, errors)
        return

    if isinstance(type_node, NullableType):
        if isinstance(type_node.inner, WildcardType):
            errors.append(f"Wildcard nullable type '_?' is not allowed in {ctx.where}.")
            return
        _check_type(type_node.inner, ctx, generic_scope, module_symbols, errors)
        return


def _check_generic_params(params: list[GenericParam] | None, where: str, inherited_scope: set[str], module_symbols: dict, errors: list[str]) -> set[str]:
    params = params or []
    local_names = _dedupe_generic_names(params, where, errors)
    scope = set(inherited_scope) | local_names
    for gp in params:
        if gp.bound is not None:
            _check_type(gp.bound, _TypeContext(f"{where} generic bound"), scope, module_symbols, errors)
        if gp.default is not None:
            _check_type(gp.default, _TypeContext(f"{where} generic default"), scope, module_symbols, errors)
    return scope


def _check_params(params: list[Param] | None, where: str, scope: set[str], module_symbols: dict, errors: list[str], allow_self: bool = False) -> None:
    for p in (params or []):
        _check_type(p.type, _TypeContext(f"{where} param '{p.name}'", allow_self=allow_self), scope, module_symbols, errors)


def _check_where_clause(where_clause: WhereClause | None, where: str, scope: set[str], module_symbols: dict, errors: list[str]) -> None:
    if where_clause is None:
        return
    for bound in where_clause.bounds:
        if bound.name not in scope:
            errors.append(f"Unknown generic '{bound.name}' referenced in {where} where-clause.")
        _check_type(bound.bound, _TypeContext(f"{where} where-clause for '{bound.name}'"), scope, module_symbols, errors)


def check_program(program: Program) -> None:
    errors: list[str] = []
    try:
        module_symbols = build_module_symbols(program)
    except SemanticError as e:
        raise TypeCheckError(str(e)) from e

    for decl in program.decls:
        if isinstance(decl, EntityDecl):
            entity_scope = _check_generic_params(decl.generics, f"entity '{decl.name}'", set(), module_symbols, errors)
            for member in decl.members:
                if isinstance(member, FieldDecl):
                    _check_type(member.type, _TypeContext(f"field '{decl.name}.{member.name}'", allow_self=True), entity_scope, module_symbols, errors)
                elif isinstance(member, MethodDecl):
                    method_scope = _check_generic_params(
                        member.generics,
                        f"method '{decl.name}.{member.name}'",
                        entity_scope,
                        module_symbols,
                        errors,
                    )
                    _check_params(member.params, f"method '{decl.name}.{member.name}'", method_scope, module_symbols, errors, allow_self=True)
                    if member.ret is not None:
                        _check_type(member.ret, _TypeContext(f"method '{decl.name}.{member.name}' return", allow_self=True), method_scope, module_symbols, errors)
                    _check_where_clause(member.where_, f"method '{decl.name}.{member.name}'", method_scope, module_symbols, errors)
                elif isinstance(member, ConstructorDecl):
                    _check_params(member.params, f"constructor '{decl.name}'", entity_scope, module_symbols, errors, allow_self=True)

        elif isinstance(decl, EntityAlias):
            alias_scope = _check_generic_params(decl.generics, f"entity alias '{decl.name}'", set(), module_symbols, errors)
            _check_type(decl.target, _TypeContext(f"entity alias '{decl.name}' target"), alias_scope, module_symbols, errors)

        elif isinstance(decl, CapabilityDecl):
            cap_scope = _check_generic_params(decl.generics, f"capability '{decl.name}'", set(), module_symbols, errors)
            _check_type(decl.body, _TypeContext(f"capability '{decl.name}' body", allow_self=True, allow_wildcard=True), cap_scope, module_symbols, errors)

        elif isinstance(decl, FunctionDecl):
            fn_scope = _check_generic_params(decl.generics, f"function '{decl.name}'", set(), module_symbols, errors)
            _check_params(decl.params, f"function '{decl.name}'", fn_scope, module_symbols, errors)
            if decl.ret is not None:
                _check_type(decl.ret, _TypeContext(f"function '{decl.name}' return"), fn_scope, module_symbols, errors)
            _check_where_clause(decl.where_, f"function '{decl.name}'", fn_scope, module_symbols, errors)

        elif isinstance(decl, Binding):
            if decl.type is not None:
                _check_type(decl.type, _TypeContext(f"binding '{decl.name}'"), set(), module_symbols, errors)

        elif isinstance(decl, AttributeDecl):
            _check_params(decl.params, f"attribute '{decl.name}'", set(), module_symbols, errors)

        elif isinstance(decl, ExceptionDecl):
            _check_params(decl.params, f"exception '{decl.name}'", set(), module_symbols, errors)

        elif isinstance(decl, ExtendDecl):
            _check_type(decl.type, _TypeContext("extend target"), set(), module_symbols, errors)
            for method in decl.methods:
                method_scope = _check_generic_params(
                    method.generics,
                    f"extend method '{method.name}'",
                    set(),
                    module_symbols,
                    errors,
                )
                _check_params(method.params, f"extend method '{method.name}'", method_scope, module_symbols, errors, allow_self=True)
                if method.ret is not None:
                    _check_type(method.ret, _TypeContext(f"extend method '{method.name}' return", allow_self=True), method_scope, module_symbols, errors)
                _check_where_clause(method.where_, f"extend method '{method.name}'", method_scope, module_symbols, errors)

    if errors:
        raise TypeCheckError("\n".join(errors))
