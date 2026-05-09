"""
Minimal semantic checks over the parsed AST.
Current scope: module-level name resolution and duplicate detection.
"""

from __future__ import annotations

from dataclasses import dataclass

from .parser import (
    Program,
    ImportStmt,
    EntityDecl,
    EntityAlias,
    ComponentDecl,
    FieldDecl,
    CapabilityDecl,
    AttributeDecl,
    ExceptionDecl,
    FunctionDecl,
    Binding,
    TypeRef,
)


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    node: object


class SemanticError(Exception):
    pass


def _decl_symbol(decl: object) -> Symbol | None:
    if isinstance(decl, EntityDecl):
        return Symbol(decl.name, "entity", decl)
    if isinstance(decl, EntityAlias):
        return Symbol(decl.name, "entity-alias", decl)
    if isinstance(decl, ComponentDecl):
        return Symbol(decl.name, "component", decl)
    if isinstance(decl, CapabilityDecl):
        return Symbol(decl.name, "capability", decl)
    if isinstance(decl, AttributeDecl):
        return Symbol(decl.name, "attribute", decl)
    if isinstance(decl, ExceptionDecl):
        return Symbol(decl.name, "exception", decl)
    if isinstance(decl, FunctionDecl):
        return Symbol(decl.name, "function", decl)
    if isinstance(decl, Binding):
        return Symbol(decl.name, f"{decl.kind}-binding", decl)
    return None


def _imported_names(stmt: ImportStmt) -> list[str]:
    if stmt.items:
        names: list[str] = []
        for item in stmt.items:
            alias = getattr(item, "alias", None)
            name = getattr(item, "name", None) or str(item)
            names.append(alias or name)
        return names
    return [stmt.module[-1]]


def build_module_symbols(program: Program) -> dict[str, Symbol]:
    symbols: dict[str, Symbol] = {}
    errors: list[str] = []

    def register(name: str, kind: str, node: object):
        prev = symbols.get(name)
        if prev is not None:
            errors.append(
                f"Duplicate module name '{name}': {prev.kind} conflicts with {kind}."
            )
            return
        symbols[name] = Symbol(name, kind, node)

    for imp in program.imports:
        for visible_name in _imported_names(imp):
            register(visible_name, "import", imp)

    for decl in program.decls:
        sym = _decl_symbol(decl)
        if sym is not None:
            register(sym.name, sym.kind, decl)

    if errors:
        raise SemanticError("\n".join(errors))
    return symbols


def _build_component_entity_map(program: Program, symbols: dict) -> dict[str, str]:
    component_names = {name for name, sym in symbols.items() if sym.kind == "component"}
    component_to_entity: dict[str, str] = {}
    errors: list[str] = []

    for decl in program.decls:
        if isinstance(decl, EntityDecl):
            for member in decl.members:
                if isinstance(member, FieldDecl):
                    field_type = getattr(member.type, "name", None)
                    if field_type in component_names:
                        if field_type in component_to_entity:
                            errors.append(
                                f"Component '{field_type}' is used by multiple entities "
                                f"('{component_to_entity[field_type]}' and '{decl.name}'). "
                                "Each component may be used by at most one entity."
                            )
                        else:
                            component_to_entity[field_type] = decl.name

    if errors:
        raise SemanticError("\n".join(errors))
    return component_to_entity


def resolve_program(program: Program) -> None:
    symbols = build_module_symbols(program)
    program.component_to_entity = _build_component_entity_map(program, symbols)
