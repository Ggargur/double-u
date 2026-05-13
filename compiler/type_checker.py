"""
Type checker for the double-u language.

Pass 1: TypeRef resolution (original v1 logic — validates type names exist)
Pass 2: Declaration registry (collect entity/function/capability info)
Pass 3: Body checking (type-check all expressions and statements)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .parser import (
    Program,
    EntityDecl,
    EntityAlias,
    ComponentDecl,
    FieldDecl,
    MethodDecl,
    ConstructorDecl,
    CapabilityDecl,
    CapFieldMember,
    CapMethodMember,
    FunctionDecl,
    Binding,
    Param,
    Block,
    Argument,
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
    # Expressions
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
    FieldAccess,
    OptionalChain,
    Call,
    Index,
    NonNull,
    ConstructorCall,
    ListLit,
    MapLit,
    MapEntry,
    # Statements
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
    WhenStmt,
    # Patterns
    WildcardPat,
    LiteralPat,
    BindingPat,
    NominalPattern,
    StructuralPattern,
)
from .semantic import build_module_symbols, _build_component_entity_map, SemanticError
from .types import (
    WType,
    WPrimitive,
    WEntity,
    WComponent,
    WException,
    WNullable,
    WList,
    WMap,
    WFunction,
    WCapability,
    WIntersection,
    WTypeVar,
    WNever,
    WNull,
    CapField,
    CapMethod,
    INT,
    FLOAT,
    BOOL,
    STRING,
    UNIT,
    NEVER,
    NULL,
    PRIMITIVE_MAP,
    is_assignable,
    common_type,
    substitute,
    unwrap_nullable,
    match_type_pattern,
    type_name,
)

BUILTIN_TYPES = {"int", "float", "bool", "string", "unit"}


class TypeCheckError(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Pass 1: TypeRef resolution (original v1 logic)
# ═══════════════════════════════════════════════════════════════════════════════

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
    return kind in {"entity", "entity-alias", "capability", "exception", "component"}


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


def _pass1_check_decl_types(program: Program, module_symbols: dict, errors: list[str]) -> None:
    """Pass 1: validate all type references in declarations."""
    for decl in program.decls:
        if isinstance(decl, EntityDecl):
            entity_scope = _check_generic_params(decl.generics, f"entity '{decl.name}'", set(), module_symbols, errors)
            for member in decl.members:
                if isinstance(member, FieldDecl):
                    _check_type(member.type, _TypeContext(f"field '{decl.name}.{member.name}'", allow_self=True), entity_scope, module_symbols, errors)
                elif isinstance(member, MethodDecl):
                    method_scope = _check_generic_params(
                        member.generics, f"method '{decl.name}.{member.name}'", entity_scope, module_symbols, errors)
                    _check_params(member.params, f"method '{decl.name}.{member.name}'", method_scope, module_symbols, errors, allow_self=True)
                    if member.ret is not None:
                        _check_type(member.ret, _TypeContext(f"method '{decl.name}.{member.name}' return", allow_self=True), method_scope, module_symbols, errors)
                    _check_where_clause(member.where_, f"method '{decl.name}.{member.name}'", method_scope, module_symbols, errors)
                elif isinstance(member, ConstructorDecl):
                    _check_params(member.params, f"constructor '{decl.name}'", entity_scope, module_symbols, errors, allow_self=True)

        elif isinstance(decl, ComponentDecl):
            comp_scope = _check_generic_params(decl.generics, f"component '{decl.name}'", set(), module_symbols, errors)
            for req in getattr(decl, "requires", []):
                _check_type(req, _TypeContext(f"component '{decl.name}' requires-clause"), comp_scope, module_symbols, errors)
                sym = module_symbols.get(req.name)
                if sym is None or sym.kind != "capability":
                    errors.append(
                        f"Component '{decl.name}' requires '{req.name}', but only capabilities are allowed."
                    )
                if req.args:
                    errors.append(
                        f"Component '{decl.name}' requires '{req.name}' with generic args, "
                        "but generic capabilities in requires are not supported yet."
                    )
            for member in decl.members:
                if isinstance(member, FieldDecl):
                    _check_type(member.type, _TypeContext(f"field '{decl.name}.{member.name}'", allow_self=True), comp_scope, module_symbols, errors)
                elif isinstance(member, MethodDecl):
                    method_scope = _check_generic_params(member.generics, f"method '{decl.name}.{member.name}'", comp_scope, module_symbols, errors)
                    _check_params(member.params, f"method '{decl.name}.{member.name}'", method_scope, module_symbols, errors, allow_self=True)
                    if member.ret is not None:
                        _check_type(member.ret, _TypeContext(f"method '{decl.name}.{member.name}' return", allow_self=True), method_scope, module_symbols, errors)
                    _check_where_clause(member.where_, f"method '{decl.name}.{member.name}'", method_scope, module_symbols, errors)

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
                    method.generics, f"extend method '{method.name}'", set(), module_symbols, errors)
                _check_params(method.params, f"extend method '{method.name}'", method_scope, module_symbols, errors, allow_self=True)
                if method.ret is not None:
                    _check_type(method.ret, _TypeContext(f"extend method '{method.name}' return", allow_self=True), method_scope, module_symbols, errors)
                _check_where_clause(method.where_, f"extend method '{method.name}'", method_scope, module_symbols, errors)


# ═══════════════════════════════════════════════════════════════════════════════
# Pass 2: Declaration registry
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FieldInfo:
    name: str
    type: WType
    public: bool


@dataclass
class MethodInfo:
    name: str
    params: list[tuple[str, WType]]
    ret: WType
    mut: bool
    public: bool
    generics: list[GenericParam] | None = None


@dataclass
class EntityInfo:
    name: str
    fields: dict[str, FieldInfo] = field(default_factory=dict)
    methods: dict[str, MethodInfo] = field(default_factory=dict)
    constructor_params: list[tuple[str, WType]] | None = None
    generics: list[GenericParam] | None = None
    is_component: bool = False


@dataclass
class FunctionInfo:
    name: str
    params: list[tuple[str, WType]]
    ret: WType
    generics: list[GenericParam] | None = None


@dataclass
class CapabilityInfo:
    name: str
    body: WCapability
    generics: list[GenericParam] | None = None


@dataclass
class Registries:
    entities: dict[str, EntityInfo] = field(default_factory=dict)
    functions: dict[str, FunctionInfo] = field(default_factory=dict)
    capabilities: dict[str, CapabilityInfo] = field(default_factory=dict)
    exceptions: dict[str, list[tuple[str, WType]]] = field(default_factory=dict)
    type_aliases: dict[str, str] = field(default_factory=dict)
    module_symbols: dict = field(default_factory=dict)
    _program_imports: list = field(default_factory=list)
    # Generic type var counter
    _tv_counter: int = 0

    def fresh_typevar(self, name: str) -> WTypeVar:
        self._tv_counter += 1
        return WTypeVar(name, self._tv_counter)


def _resolve_type_node(
    node, generic_scope: dict[str, WTypeVar], registries: Registries,
    self_entity: str | None = None,
) -> WType:
    """Convert an AST type node to a WType."""
    if node is None:
        return UNIT

    if isinstance(node, PrimitiveType):
        return PRIMITIVE_MAP[node.name]

    if isinstance(node, SelfType):
        if self_entity:
            return WEntity(self_entity)
        return UNIT

    if isinstance(node, WildcardType):
        return UNIT  # wildcards in capabilities handled specially

    if isinstance(node, TypeRef):
        # Check if it's a generic type variable
        if node.name in generic_scope:
            return generic_scope[node.name]
        # Check if it's an entity, component, capability, or exception
        sym = registries.module_symbols.get(node.name)
        if sym:
            args = tuple(
                _resolve_type_node(a, generic_scope, registries, self_entity)
                for a in (node.args or [])
            )
            if sym.kind in ("entity", "entity-alias"):
                canonical = node.name
                while canonical in registries.type_aliases:
                    canonical = registries.type_aliases[canonical]
                return WEntity(canonical, args)
            if sym.kind == "component":
                return WComponent(node.name, args)
            if sym.kind == "exception":
                return WException(node.name)
            if sym.kind == "capability":
                return WEntity(node.name, args)
        return WEntity(node.name)

    if isinstance(node, NullableType):
        return WNullable(_resolve_type_node(node.inner, generic_scope, registries, self_entity))

    if isinstance(node, ListType):
        return WList(_resolve_type_node(node.element, generic_scope, registries, self_entity))

    if isinstance(node, MapType):
        return WMap(
            _resolve_type_node(node.key, generic_scope, registries, self_entity),
            _resolve_type_node(node.value, generic_scope, registries, self_entity),
        )

    if isinstance(node, IntersectionType):
        return WIntersection(tuple(
            _resolve_type_node(t, generic_scope, registries, self_entity)
            for t in node.types
        ))

    if isinstance(node, CapabilityBody):
        members = []
        for m in node.members:
            if isinstance(m, CapFieldMember):
                members.append(CapField(
                    m.name,
                    _resolve_type_node(m.type, generic_scope, registries, self_entity),
                ))
            elif isinstance(m, CapMethodMember):
                members.append(CapMethod(
                    m.name,
                    m.mut,
                    tuple(_resolve_type_node(a, generic_scope, registries, self_entity) for a in m.args),
                    _resolve_type_node(m.ret, generic_scope, registries, self_entity) if m.ret else None,
                ))
        return WCapability(tuple(members))

    return UNIT


def _build_generic_scope(params: list[GenericParam] | None, registries: Registries) -> dict[str, WTypeVar]:
    scope: dict[str, WTypeVar] = {}
    for gp in (params or []):
        scope[gp.name] = registries.fresh_typevar(gp.name)
    return scope


def _build_registries(program: Program, module_symbols: dict, errors: list[str]) -> Registries:
    """Pass 2: collect type information from all declarations."""
    reg = Registries(module_symbols=module_symbols, _program_imports=program.imports)
    reg._component_to_entity = getattr(program, 'component_to_entity', {})

    # First pass: register entity aliases
    for decl in program.decls:
        if isinstance(decl, EntityAlias):
            target_name = getattr(decl.target, "name", None)
            if target_name:
                reg.type_aliases[decl.name] = target_name

    # Second pass: register entities and components
    for decl in program.decls:
        if isinstance(decl, (EntityDecl, ComponentDecl)):
            generic_scope = _build_generic_scope(decl.generics, reg)
            info = EntityInfo(
                name=decl.name,
                generics=decl.generics,
                is_component=isinstance(decl, ComponentDecl),
            )
            for member in decl.members:
                if isinstance(member, FieldDecl):
                    ftype = _resolve_type_node(member.type, generic_scope, reg, decl.name)
                    info.fields[member.name] = FieldInfo(member.name, ftype, member.public)
                elif isinstance(member, MethodDecl):
                    m_generic_scope = dict(generic_scope)
                    m_generic_scope.update(_build_generic_scope(member.generics, reg))
                    params = []
                    for p in (member.params or []):
                        pt = _resolve_type_node(p.type, m_generic_scope, reg, decl.name)
                        params.append((p.name, pt))
                    ret = _resolve_type_node(member.ret, m_generic_scope, reg, decl.name) if member.ret else UNIT
                    info.methods[member.name] = MethodInfo(
                        name=member.name, params=params, ret=ret,
                        mut=member.mut, public=member.public,
                        generics=member.generics,
                    )
                elif isinstance(member, ConstructorDecl):
                    params = []
                    for p in (member.params or []):
                        pt = _resolve_type_node(p.type, generic_scope, reg, decl.name)
                        params.append((p.name, pt))
                    info.constructor_params = params
            reg.entities[decl.name] = info

    # Resolve entity aliases: alias inherits fields/methods from target
    for alias_name, target_name in reg.type_aliases.items():
        canonical = target_name
        while canonical in reg.type_aliases:
            canonical = reg.type_aliases[canonical]
        target_info = reg.entities.get(canonical)
        if target_info and alias_name not in reg.entities:
            reg.entities[alias_name] = target_info

    # Register extension methods
    for decl in program.decls:
        if isinstance(decl, ExtendDecl):
            target_name = getattr(decl.type, "name", None)
            if target_name:
                canonical = target_name
                while canonical in reg.type_aliases:
                    canonical = reg.type_aliases[canonical]
                info = reg.entities.get(canonical)
                if info:
                    for method in decl.methods:
                        m_generic_scope = _build_generic_scope(method.generics, reg)
                        params = []
                        for p in (method.params or []):
                            pt = _resolve_type_node(p.type, m_generic_scope, reg, canonical)
                            params.append((p.name, pt))
                        ret = _resolve_type_node(method.ret, m_generic_scope, reg, canonical) if method.ret else UNIT
                        info.methods[method.name] = MethodInfo(
                            name=method.name, params=params, ret=ret,
                            mut=method.mut, public=method.public,
                            generics=method.generics,
                        )

    # Register top-level functions
    for decl in program.decls:
        if isinstance(decl, FunctionDecl):
            generic_scope = _build_generic_scope(decl.generics, reg)
            params = []
            for p in (decl.params or []):
                pt = _resolve_type_node(p.type, generic_scope, reg)
                params.append((p.name, pt))
            ret = _resolve_type_node(decl.ret, generic_scope, reg) if decl.ret else UNIT
            reg.functions[decl.name] = FunctionInfo(
                name=decl.name, params=params, ret=ret, generics=decl.generics,
            )

    # Register capabilities
    for decl in program.decls:
        if isinstance(decl, CapabilityDecl):
            cap_scope = _build_generic_scope(decl.generics, reg)
            cap_body = _resolve_type_node(decl.body, cap_scope, reg, decl.name)
            if isinstance(cap_body, WCapability):
                reg.capabilities[decl.name] = CapabilityInfo(
                    name=decl.name,
                    body=cap_body,
                    generics=decl.generics,
                )

    # Register exceptions
    for decl in program.decls:
        if isinstance(decl, ExceptionDecl):
            params = []
            for p in (decl.params or []):
                pt = _resolve_type_node(p.type, {}, reg)
                params.append((p.name, pt))
            reg.exceptions[decl.name] = params

    return reg


def _types_compatible(expected: WType, actual: WType, aliases: dict[str, str]) -> bool:
    return is_assignable(expected, actual, aliases) and is_assignable(actual, expected, aliases)


def _entity_satisfies_capability(
    entity_info: EntityInfo,
    capability: WCapability,
    aliases: dict[str, str],
) -> tuple[bool, str | None]:
    for member in capability.members:
        if isinstance(member, CapField):
            field_info = entity_info.fields.get(member.name)
            if field_info is not None:
                if not _types_compatible(member.type, field_info.type, aliases):
                    return False, (
                        f"member '{member.name}' type mismatch: "
                        f"expected {type_name(member.type)}, got {type_name(field_info.type)}"
                    )
                continue

            method_info = entity_info.methods.get(member.name)
            if method_info is not None and not method_info.params:
                if not _types_compatible(member.type, method_info.ret, aliases):
                    return False, (
                        f"member '{member.name}' return type mismatch: "
                        f"expected {type_name(member.type)}, got {type_name(method_info.ret)}"
                    )
                continue

            return False, f"missing member '{member.name}'"

        method_info = entity_info.methods.get(member.name)
        if method_info is None:
            return False, f"missing method '{member.name}'"
        if method_info.mut != member.mut:
            required = "mut " if member.mut else ""
            actual = "mut " if method_info.mut else ""
            return False, f"method '{member.name}' mutability mismatch: expected {required}fn, got {actual}fn"
        if len(method_info.params) != len(member.args):
            return False, (
                f"method '{member.name}' arity mismatch: expected {len(member.args)} args, "
                f"got {len(method_info.params)}"
            )
        for (_, actual_type), expected_type in zip(method_info.params, member.args):
            if not _types_compatible(expected_type, actual_type, aliases):
                return False, (
                    f"method '{member.name}' parameter mismatch: "
                    f"expected {type_name(expected_type)}, got {type_name(actual_type)}"
                )

        expected_ret = member.ret if member.ret is not None else UNIT
        if not _types_compatible(expected_ret, method_info.ret, aliases):
            return False, (
                f"method '{member.name}' return mismatch: "
                f"expected {type_name(expected_ret)}, got {type_name(method_info.ret)}"
            )

    return True, None


def _check_component_requires(program: Program, registries: Registries, errors: list[str]) -> None:
    component_to_entity = getattr(registries, "_component_to_entity", {})
    for decl in program.decls:
        if not isinstance(decl, ComponentDecl):
            continue
        if not getattr(decl, "requires", None):
            continue

        owner_name = component_to_entity.get(decl.name)
        if owner_name is None:
            continue
        owner_info = registries.entities.get(owner_name)
        if owner_info is None:
            continue

        for req in decl.requires:
            cap_info = registries.capabilities.get(req.name)
            if cap_info is None:
                continue
            ok, reason = _entity_satisfies_capability(owner_info, cap_info.body, registries.type_aliases)
            if not ok:
                errors.append(
                    f"Entity '{owner_name}' uses component '{decl.name}', which requires capability "
                    f"'{req.name}', but {reason}."
                )


# ═══════════════════════════════════════════════════════════════════════════════
# Pass 3: Body checking — TypeEnv + expression/statement type checking
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class VarBinding:
    type: WType
    mutable: bool


class TypeEnv:
    def __init__(
        self,
        parent: TypeEnv | None = None,
        current_entity: str | None = None,
        current_ret: WType | None = None,
        self_mutable: bool = False,
    ):
        self._bindings: dict[str, VarBinding] = {}
        self._parent = parent
        self.current_entity = current_entity if current_entity is not None else (parent.current_entity if parent else None)
        self.current_ret = current_ret if current_ret is not None else (parent.current_ret if parent else None)
        self.self_mutable = self_mutable if parent is None or current_entity is not None else (parent.self_mutable if parent else False)

    def define(self, name: str, type_: WType, mutable: bool = False):
        self._bindings[name] = VarBinding(type_, mutable)

    def lookup(self, name: str) -> VarBinding | None:
        b = self._bindings.get(name)
        if b is not None:
            return b
        if self._parent is not None:
            return self._parent.lookup(name)
        return None

    def child(self, **kwargs) -> TypeEnv:
        return TypeEnv(parent=self, **kwargs)


# ── Operator method mapping ──────────────────────────────────────────────────

# Sentinel type for C library functions (skip arg checking)
_C_LIB_SENTINEL = WFunction((), UNIT)

_OP_METHOD = {
    "+": "add", "-": "sub", "*": "mul", "/": "div", "%": "mod",
}


class _Checker:
    """Stateful checker that walks function/method bodies."""

    # Known C library function names (from runtime builtins)
    _C_LIB_FUNCTIONS = {
        "printf", "puts", "putchar",  # stdio
        "sqrt", "cbrt", "pow", "fabs", "floor", "ceil", "round",  # math
        "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
        "exp", "log", "log2", "log10",
        "abs", "exit",  # stdlib
        "strlen",  # string
        "println",  # convenience
    }

    def __init__(self, registries: Registries, errors: list[str]):
        self.reg = registries
        self.errors = errors
        self._top_level_bindings: dict[str, WType] = {}
        self._c_lib_names: set[str] = set()
        self._collect_c_imports()

    def _collect_c_imports(self):
        """Collect C library function names from imports."""
        for imp in getattr(self.reg, '_program_imports', []):
            if imp.module and imp.module[0] == "c" and len(imp.module) >= 2:
                if imp.items:
                    for item in imp.items:
                        name = getattr(item, "name", None) or str(item)
                        alias = getattr(item, "alias", None) or name
                        self._c_lib_names.add(alias)
                else:
                    # import c.stdio — expose all known functions from that lib
                    self._c_lib_names.update(self._C_LIB_FUNCTIONS)

    def error(self, msg: str):
        self.errors.append(msg)

    # ── Resolve entity info ──────────────────────────────────────────────

    def _entity_info(self, ty: WType) -> EntityInfo | None:
        if isinstance(ty, WEntity):
            canonical = ty.name
            while canonical in self.reg.type_aliases:
                canonical = self.reg.type_aliases[canonical]
            return self.reg.entities.get(canonical)
        if isinstance(ty, WComponent):
            return self.reg.entities.get(ty.name)
        return None

    # ── Check expressions ────────────────────────────────────────────────

    def check_expr(self, expr, env: TypeEnv) -> WType:
        if expr is None:
            return UNIT

        if isinstance(expr, IntLit):
            return INT
        if isinstance(expr, FloatLit):
            return FLOAT
        if isinstance(expr, BoolLit):
            return BOOL
        if isinstance(expr, NullLit):
            return NULL
        if isinstance(expr, StringLit):
            # Check interpolated expressions
            for part in expr.parts:
                if isinstance(part, tuple) and part[0] == "interp":
                    self.check_expr(part[1], env)
            return STRING

        if isinstance(expr, NameExpr):
            return self._check_name_expr(expr, env)

        if isinstance(expr, SelfExpr):
            if env.current_entity:
                return WEntity(env.current_entity)
            self.error("'self' used outside of entity method.")
            return UNIT

        if isinstance(expr, OurExpr):
            # 'our' refers to the owning entity of the component
            if env.current_entity:
                component_to_entity = getattr(self.reg, '_component_to_entity', {})
                owner = component_to_entity.get(env.current_entity)
                if owner:
                    return WEntity(owner)
            return UNIT

        if isinstance(expr, ListLit):
            return self._check_list_lit(expr, env)

        if isinstance(expr, MapLit):
            return self._check_map_lit(expr, env)

        if isinstance(expr, BinOp):
            return self._check_binop(expr, env)

        if isinstance(expr, UnaryOp):
            return self._check_unary(expr, env)

        if isinstance(expr, FieldAccess):
            return self._check_field_access(expr, env)

        if isinstance(expr, OptionalChain):
            return self._check_optional_chain(expr, env)

        if isinstance(expr, Index):
            return self._check_index(expr, env)

        if isinstance(expr, NonNull):
            return self._check_non_null(expr, env)

        if isinstance(expr, Call):
            return self._check_call(expr, env)

        if isinstance(expr, ConstructorCall):
            return self._check_constructor_call(expr, env)

        if isinstance(expr, IfStmt):
            return self._check_if(expr, env)

        if isinstance(expr, MatchStmt):
            return self._check_match(expr, env)

        if isinstance(expr, Block):
            return self._check_block(expr, env)

        if isinstance(expr, EarlyReturn):
            return self._check_early_return(expr, env)

        if isinstance(expr, TryStmt):
            return self._check_try(expr, env)

        if isinstance(expr, ThrowStmt):
            self.check_stmt(expr, env)
            return NEVER

        return UNIT

    def _check_name_expr(self, expr: NameExpr, env: TypeEnv) -> WType:
        # Check local variables
        binding = env.lookup(expr.name)
        if binding is not None:
            return binding.type

        # Check implicit self fields
        if env.current_entity:
            info = self.reg.entities.get(env.current_entity)
            if info:
                field_info = info.fields.get(expr.name)
                if field_info:
                    return field_info.type
                # Check methods (for bare method name reference)
                method_info = info.methods.get(expr.name)
                if method_info:
                    return WFunction(
                        tuple(t for _, t in method_info.params),
                        method_info.ret,
                    )

        # Check top-level functions
        fn_info = self.reg.functions.get(expr.name)
        if fn_info:
            return WFunction(
                tuple(t for _, t in fn_info.params),
                fn_info.ret,
            )

        # Check entity names (for constructor-like use)
        if expr.name in self.reg.entities:
            return UNIT  # Will be handled at constructor call

        # Check exception names (bare exception name, e.g. in throw)
        if expr.name in self.reg.exceptions:
            return WException(expr.name)

        # Check top-level bindings in registries
        if expr.name in self._top_level_bindings:
            return self._top_level_bindings[expr.name]

        self.error(f"Undefined variable '{expr.name}'.")
        return UNIT

    def _check_list_lit(self, expr: ListLit, env: TypeEnv) -> WType:
        if not expr.elements:
            return WList(UNIT)  # Empty list; type annotation needed upstream
        types = [self.check_expr(e, env) for e in expr.elements]
        result = types[0]
        for i, t in enumerate(types[1:], 1):
            unified = common_type(result, t, self.reg.type_aliases)
            if unified is None:
                self.error(f"List element type mismatch: expected {type_name(result)}, got {type_name(t)}.")
                break
            result = unified
        return WList(result)

    def _check_map_lit(self, expr: MapLit, env: TypeEnv) -> WType:
        if not expr.entries:
            return WMap(UNIT, UNIT)
        key_types = []
        val_types = []
        for entry in expr.entries:
            key_types.append(self.check_expr(entry.key, env))
            val_types.append(self.check_expr(entry.value, env))
        key_t = key_types[0]
        val_t = val_types[0]
        for kt in key_types[1:]:
            if kt != key_t:
                self.error(f"Map key type mismatch: expected {type_name(key_t)}, got {type_name(kt)}.")
                break
        for vt in val_types[1:]:
            unified = common_type(val_t, vt, self.reg.type_aliases)
            if unified is None:
                self.error(f"Map value type mismatch: expected {type_name(val_t)}, got {type_name(vt)}.")
                break
            val_t = unified
        return WMap(key_t, val_t)

    def _check_binop(self, expr: BinOp, env: TypeEnv) -> WType:
        # Short-circuit operators
        if expr.op in ("and", "or"):
            lt = self.check_expr(expr.left, env)
            rt = self.check_expr(expr.right, env)
            if not isinstance(lt, WNever) and lt != BOOL:
                self.error(f"Operator '{expr.op}' requires bool, got {type_name(lt)}.")
            if not isinstance(rt, WNever) and rt != BOOL:
                self.error(f"Operator '{expr.op}' requires bool, got {type_name(rt)}.")
            return BOOL

        # Null coalesce
        if expr.op == "??":
            lt = self.check_expr(expr.left, env)
            rt = self.check_expr(expr.right, env)
            inner = unwrap_nullable(lt)
            if inner is None and not isinstance(lt, WNull) and not isinstance(lt, WNever):
                self.error(f"Operator '??' requires nullable left operand, got {type_name(lt)}.")
                return rt
            if inner is not None:
                if not is_assignable(inner, rt, self.reg.type_aliases):
                    pass  # Allow different types; result is common_type
                return common_type(inner, rt, self.reg.type_aliases) or inner
            return rt

        lt = self.check_expr(expr.left, env)
        rt = self.check_expr(expr.right, env)

        # Comparison operators
        if expr.op in ("==", "!="):
            # Allow comparing same types
            if not isinstance(lt, WNever) and not isinstance(rt, WNever):
                if not is_assignable(lt, rt, self.reg.type_aliases) and not is_assignable(rt, lt, self.reg.type_aliases):
                    self.error(f"Cannot compare {type_name(lt)} with {type_name(rt)}.")
            return BOOL

        if expr.op in ("<", ">", "<=", ">="):
            if not isinstance(lt, WNever) and not isinstance(rt, WNever):
                if lt != rt:
                    self.error(f"Cannot compare {type_name(lt)} with {type_name(rt)}.")
                if lt not in (INT, FLOAT) and not isinstance(lt, WEntity):
                    self.error(f"Operator '{expr.op}' not supported for {type_name(lt)}.")
            return BOOL

        # Arithmetic operators
        method_name = _OP_METHOD.get(expr.op)
        if method_name:
            # Primitives: same type required, no implicit coercion
            if isinstance(lt, WPrimitive) and lt in (INT, FLOAT):
                if lt != rt and not isinstance(rt, WNever):
                    self.error(f"Operator '{expr.op}': both operands must be {type_name(lt)}, got {type_name(lt)} and {type_name(rt)}.")
                return lt
            if isinstance(lt, WPrimitive) and lt == STRING and expr.op == "+":
                if rt != STRING and not isinstance(rt, WNever):
                    self.error(f"Operator '+': cannot concatenate {type_name(lt)} with {type_name(rt)}.")
                return STRING

            # Entity operator dispatch
            if isinstance(lt, WEntity):
                info = self._entity_info(lt)
                if info:
                    m = info.methods.get(method_name)
                    if m:
                        return m.ret
                self.error(f"Operator '{expr.op}' not defined for {type_name(lt)}.")
                return lt

            self.error(f"Operator '{expr.op}' not supported for {type_name(lt)}.")
            return lt

        return UNIT

    def _check_unary(self, expr: UnaryOp, env: TypeEnv) -> WType:
        t = self.check_expr(expr.operand, env)
        if isinstance(t, WNever):
            return NEVER
        if expr.op == "-":
            if t not in (INT, FLOAT):
                # Check for entity neg method
                if isinstance(t, WEntity):
                    info = self._entity_info(t)
                    if info and "neg" in info.methods:
                        return info.methods["neg"].ret
                self.error(f"Unary '-' requires int or float, got {type_name(t)}.")
            return t
        if expr.op == "!":
            if t != BOOL:
                self.error(f"Unary '!' requires bool, got {type_name(t)}.")
            return BOOL
        return t

    def _check_field_access(self, expr: FieldAccess, env: TypeEnv) -> WType:
        obj_type = self.check_expr(expr.obj, env)
        return self._resolve_field(obj_type, expr.field, f"field access '.{expr.field}'")

    def _resolve_field(self, obj_type: WType, field_name: str, where: str) -> WType:
        if isinstance(obj_type, WNever):
            return NEVER

        info = self._entity_info(obj_type)
        if info:
            # Check fields
            fi = info.fields.get(field_name)
            if fi:
                return fi.type
            # Check zero-arg methods (act like fields)
            mi = info.methods.get(field_name)
            if mi:
                if not mi.params:
                    return mi.ret
                return WFunction(tuple(t for _, t in mi.params), mi.ret)
            self.error(f"No field or method '{field_name}' on {type_name(obj_type)}.")
            return UNIT

        # Built-in field access for collections
        if isinstance(obj_type, WList):
            if field_name == "length":
                return INT
        if isinstance(obj_type, WMap):
            if field_name == "length":
                return INT
        if isinstance(obj_type, WPrimitive) and obj_type == STRING:
            if field_name == "length":
                return INT

        self.error(f"Cannot access '{field_name}' on {type_name(obj_type)}.")
        return UNIT

    def _check_optional_chain(self, expr: OptionalChain, env: TypeEnv) -> WType:
        obj_type = self.check_expr(expr.obj, env)
        inner = unwrap_nullable(obj_type)
        if inner is None:
            if isinstance(obj_type, WNever):
                return NEVER
            # Allow optional chain on non-nullable (just acts like field access)
            field_type = self._resolve_field(obj_type, expr.field, f"optional chain '?.{expr.field}'")
            return field_type
        field_type = self._resolve_field(inner, expr.field, f"optional chain '?.{expr.field}'")
        if isinstance(field_type, WNullable):
            return field_type
        return WNullable(field_type)

    def _check_index(self, expr: Index, env: TypeEnv) -> WType:
        obj_type = self.check_expr(expr.obj, env)
        idx_type = self.check_expr(expr.idx, env)
        if isinstance(obj_type, WList):
            if idx_type != INT and not isinstance(idx_type, WNever):
                self.error(f"List index must be int, got {type_name(idx_type)}.")
            return obj_type.element
        if isinstance(obj_type, WMap):
            if not is_assignable(obj_type.key, idx_type, self.reg.type_aliases) and not isinstance(idx_type, WNever):
                self.error(f"Map key type mismatch: expected {type_name(obj_type.key)}, got {type_name(idx_type)}.")
            return obj_type.value
        if isinstance(obj_type, WPrimitive) and obj_type == STRING:
            if idx_type != INT and not isinstance(idx_type, WNever):
                self.error(f"String index must be int, got {type_name(idx_type)}.")
            return STRING
        # Entity with get method
        if isinstance(obj_type, WEntity):
            info = self._entity_info(obj_type)
            if info and "get" in info.methods:
                return info.methods["get"].ret
        self.error(f"Type {type_name(obj_type)} is not indexable.")
        return UNIT

    def _check_non_null(self, expr: NonNull, env: TypeEnv) -> WType:
        t = self.check_expr(expr.expr, env)
        inner = unwrap_nullable(t)
        if inner is not None:
            return inner
        if isinstance(t, WNull):
            self.error("Non-null assertion '!!' on null literal will always fail.")
            return UNIT
        if isinstance(t, WNever):
            return NEVER
        # Allow !! on non-nullable (no-op, but not an error)
        return t

    def _check_call(self, expr: Call, env: TypeEnv) -> WType:
        callee = expr.callee
        args = [self._eval_arg(a, env) for a in expr.args]
        arg_types = [t for _, t in args]

        # Method call: obj.method(args)
        if isinstance(callee, FieldAccess):
            obj_type = self.check_expr(callee.obj, env)
            return self._check_method_call(obj_type, callee.field, arg_types, expr)

        # Bare name call
        if isinstance(callee, NameExpr):
            name = callee.name

            # Check implicit self method call
            if env.current_entity:
                info = self.reg.entities.get(env.current_entity)
                if info and name in info.methods:
                    return self._check_method_call(
                        WEntity(env.current_entity), name, arg_types, expr)

            # Check top-level function
            fn_info = self.reg.functions.get(name)
            if fn_info:
                self._check_arg_count(name, fn_info.params, arg_types)
                for i, ((pname, ptype), atype) in enumerate(zip(fn_info.params, arg_types)):
                    if not is_assignable(ptype, atype, self.reg.type_aliases) and not isinstance(atype, WNever):
                        self.error(f"Function '{name}' param '{pname}': expected {type_name(ptype)}, got {type_name(atype)}.")
                return fn_info.ret

            # Check builtins (print, len, abs)
            if name == "print":
                return UNIT
            if name == "len":
                return INT
            if name == "abs":
                if arg_types:
                    return arg_types[0]
                return INT

            # Check if it's a callable variable
            binding = env.lookup(name)
            if binding and isinstance(binding.type, WFunction):
                ft = binding.type
                # C library sentinel — skip arg checking
                if ft is _C_LIB_SENTINEL:
                    return UNIT
                self._check_arg_count(name, [(f"arg{i}", t) for i, t in enumerate(ft.params)], arg_types)
                for i, (ptype, atype) in enumerate(zip(ft.params, arg_types)):
                    if not is_assignable(ptype, atype, self.reg.type_aliases) and not isinstance(atype, WNever):
                        self.error(f"Callable '{name}' arg {i}: expected {type_name(ptype)}, got {type_name(atype)}.")
                return ft.ret

            # Unknown function — allow (might be C library etc.)
            self.check_expr(callee, env)
            return UNIT

        # General callee
        callee_type = self.check_expr(callee, env)
        if isinstance(callee_type, WFunction):
            return callee_type.ret
        return UNIT

    def _check_method_call(self, obj_type: WType, method_name: str, arg_types: list[WType], node) -> WType:
        if isinstance(obj_type, WNever):
            return NEVER

        info = self._entity_info(obj_type)
        if info:
            mi = info.methods.get(method_name)
            if mi:
                self._check_arg_count(f"{type_name(obj_type)}.{method_name}", mi.params, arg_types)
                for i, ((pname, ptype), atype) in enumerate(zip(mi.params, arg_types)):
                    if not is_assignable(ptype, atype, self.reg.type_aliases) and not isinstance(atype, WNever):
                        self.error(
                            f"Method '{type_name(obj_type)}.{method_name}' param '{pname}': "
                            f"expected {type_name(ptype)}, got {type_name(atype)}."
                        )
                return mi.ret

        # Built-in methods for collections
        if isinstance(obj_type, WList):
            if method_name in ("push", "append", "add"):
                return UNIT
            if method_name in ("pop", "remove"):
                return obj_type.element
            if method_name == "length":
                return INT
        if isinstance(obj_type, WMap):
            if method_name in ("get",):
                return obj_type.value
            if method_name in ("set", "put"):
                return UNIT
            if method_name in ("keys",):
                return WList(obj_type.key)
            if method_name in ("values",):
                return WList(obj_type.value)

        # String methods
        if obj_type == STRING:
            if method_name == "length":
                return INT
            # Allow any string method call
            return UNIT

        # Allow calls to methods found by Python interop (C lib builtins etc.)
        if isinstance(obj_type, WPrimitive):
            if method_name in ("to_float", "to_int", "to_string"):
                if method_name == "to_float":
                    return FLOAT
                if method_name == "to_int":
                    return INT
                return STRING
            return UNIT

        self.error(f"No method '{method_name}' on {type_name(obj_type)}.")
        return UNIT

    def _check_constructor_call(self, expr: ConstructorCall, env: TypeEnv) -> WType:
        name = expr.type_name
        # Resolve alias
        canonical = name
        while canonical in self.reg.type_aliases:
            canonical = self.reg.type_aliases[canonical]

        info = self.reg.entities.get(canonical)
        if info is None:
            # Could be an exception
            if name in self.reg.exceptions:
                exc_params = self.reg.exceptions[name]
                args = [self._eval_arg(a, env) for a in expr.args]
                arg_types = [t for _, t in args]
                self._check_arg_count(name, exc_params, arg_types)
                for i, ((pname, ptype), atype) in enumerate(zip(exc_params, arg_types)):
                    if not is_assignable(ptype, atype, self.reg.type_aliases) and not isinstance(atype, WNever):
                        self.error(f"Exception '{name}' param '{pname}': expected {type_name(ptype)}, got {type_name(atype)}.")
                return WException(name)
            self.error(f"Unknown entity '{name}'.")
            return WEntity(name)

        args = [self._eval_arg(a, env) for a in expr.args]
        arg_types = [t for _, t in args]

        if info.constructor_params is not None:
            # Explicit constructor
            self._check_arg_count(name, info.constructor_params, arg_types)
            for i, ((pname, ptype), atype) in enumerate(zip(info.constructor_params, arg_types)):
                if not is_assignable(ptype, atype, self.reg.type_aliases) and not isinstance(atype, WNever):
                    self.error(f"Constructor '{name}' param '{pname}': expected {type_name(ptype)}, got {type_name(atype)}.")
        else:
            # Implicit constructor: args match fields in order
            field_list = list(info.fields.values())
            self._check_arg_count(name, [(f.name, f.type) for f in field_list], arg_types)
            for i, (fi, atype) in enumerate(zip(field_list, arg_types)):
                if not is_assignable(fi.type, atype, self.reg.type_aliases) and not isinstance(atype, WNever):
                    self.error(f"Constructor '{name}' field '{fi.name}': expected {type_name(fi.type)}, got {type_name(atype)}.")

        entity_info = self.reg.entities.get(canonical)
        if entity_info and entity_info.is_component:
            return WComponent(canonical)
        return WEntity(canonical)

    def _eval_arg(self, arg, env: TypeEnv) -> tuple[str | None, WType]:
        if isinstance(arg, Argument):
            return (arg.label, self.check_expr(arg.value, env))
        return (None, self.check_expr(arg, env))

    def _check_arg_count(self, name: str, params: list, arg_types: list):
        if len(arg_types) != len(params):
            self.error(f"'{name}' expects {len(params)} arguments, got {len(arg_types)}.")

    # ── Check statements ─────────────────────────────────────────────────

    def check_stmt(self, stmt, env: TypeEnv):
        if isinstance(stmt, Binding):
            self._check_binding(stmt, env)
            return

        if isinstance(stmt, Assignment):
            self._check_assignment(stmt, env)
            return

        if isinstance(stmt, IfStmt):
            self._check_if(stmt, env)
            return

        if isinstance(stmt, ForStmt):
            self._check_for(stmt, env)
            return

        if isinstance(stmt, MatchStmt):
            self._check_match(stmt, env)
            return

        if isinstance(stmt, TryStmt):
            self._check_try(stmt, env)
            return

        if isinstance(stmt, ThrowStmt):
            self._check_throw(stmt, env)
            return

        if isinstance(stmt, EarlyReturn):
            self._check_early_return(stmt, env)
            return

        if isinstance(stmt, SpawnStmt):
            self._check_block(stmt.body, env)
            return

        if isinstance(stmt, WhenStmt):
            cond_type = self.check_expr(stmt.cond, env)
            if cond_type != BOOL and not isinstance(cond_type, WNever):
                self.error(f"'when' condition must be bool, got {type_name(cond_type)}.")
            self._check_block(stmt.body, env)
            return

        if isinstance(stmt, SelectStmt):
            for arm in stmt.arms:
                if isinstance(arm.body, Block):
                    self._check_block(arm.body, env)
            return

        # Expression statement
        self.check_expr(stmt, env)

    def _check_binding(self, stmt: Binding, env: TypeEnv):
        value_type = self.check_expr(stmt.value, env)
        mutable = stmt.kind == "let"

        if stmt.type is not None:
            # Explicit type annotation
            declared = _resolve_type_node(stmt.type, {}, self.reg)
            if not is_assignable(declared, value_type, self.reg.type_aliases) and not isinstance(value_type, WNever):
                self.error(
                    f"Binding '{stmt.name}': declared type {type_name(declared)}, "
                    f"got {type_name(value_type)}."
                )
            env.define(stmt.name, declared, mutable)
        else:
            # Infer type
            inferred = value_type
            # Don't infer null or never as the type
            if isinstance(inferred, WNull):
                self.error(f"Cannot infer type of '{stmt.name}' from null. Add a type annotation.")
                inferred = UNIT
            elif isinstance(inferred, WNever):
                inferred = UNIT
            env.define(stmt.name, inferred, mutable)

    def _check_assignment(self, stmt: Assignment, env: TypeEnv):
        value_type = self.check_expr(stmt.value, env)
        target = stmt.target

        if isinstance(target, NameExpr):
            binding = env.lookup(target.name)
            if binding is None:
                # Check implicit self field
                if env.current_entity:
                    info = self.reg.entities.get(env.current_entity)
                    if info:
                        fi = info.fields.get(target.name)
                        if fi:
                            if not env.self_mutable:
                                self.error(f"Cannot assign to field '{target.name}' in non-mutating method.")
                            elif not is_assignable(fi.type, value_type, self.reg.type_aliases) and not isinstance(value_type, WNever):
                                self.error(f"Field '{target.name}': expected {type_name(fi.type)}, got {type_name(value_type)}.")
                            return
                self.error(f"Undefined variable '{target.name}'.")
                return
            if not binding.mutable:
                self.error(f"Cannot assign to immutable binding '{target.name}'.")
                return
            target_type = binding.type
            # Handle compound assignment
            if stmt.op != "=":
                op_char = stmt.op[0]  # "+=" -> "+"
                method_name = _OP_METHOD.get(op_char)
                if method_name:
                    # The result of the operation must be assignable to target
                    if isinstance(target_type, WPrimitive) and target_type in (INT, FLOAT):
                        if target_type != value_type and not isinstance(value_type, WNever):
                            self.error(f"Operator '{stmt.op}': both operands must be {type_name(target_type)}, got {type_name(target_type)} and {type_name(value_type)}.")
                    return
            if not is_assignable(target_type, value_type, self.reg.type_aliases) and not isinstance(value_type, WNever):
                self.error(f"Cannot assign {type_name(value_type)} to '{target.name}' of type {type_name(target_type)}.")

        elif isinstance(target, FieldAccess):
            obj_type = self.check_expr(target.obj, env)
            # Check field exists and types match
            info = self._entity_info(obj_type)
            if info:
                fi = info.fields.get(target.field)
                if fi:
                    if not is_assignable(fi.type, value_type, self.reg.type_aliases) and not isinstance(value_type, WNever):
                        self.error(f"Field '{target.field}': expected {type_name(fi.type)}, got {type_name(value_type)}.")
                    # Check mutability
                    if isinstance(target.obj, SelfExpr) and not env.self_mutable:
                        self.error(f"Cannot assign to field '{target.field}' in non-mutating method.")
                    elif isinstance(target.obj, NameExpr):
                        b = env.lookup(target.obj.name)
                        if b and not b.mutable:
                            self.error(f"Cannot assign to field of immutable binding '{target.obj.name}'.")
                else:
                    self.error(f"No field '{target.field}' on {type_name(obj_type)}.")

        elif isinstance(target, Index):
            self.check_expr(target.obj, env)
            self.check_expr(target.idx, env)

    def _check_if(self, stmt: IfStmt, env: TypeEnv) -> WType:
        cond_type = self.check_expr(stmt.cond, env)
        if cond_type != BOOL and not isinstance(cond_type, WNever):
            self.error(f"'if' condition must be bool, got {type_name(cond_type)}.")

        then_type = self._check_block(stmt.then, env)

        if stmt.else_ is not None:
            if isinstance(stmt.else_, Block):
                else_type = self._check_block(stmt.else_, env)
            elif isinstance(stmt.else_, IfStmt):
                else_type = self._check_if(stmt.else_, env)
            else:
                else_type = self.check_expr(stmt.else_, env)
            # Unify branch types for if-as-expression
            unified = common_type(then_type, else_type, self.reg.type_aliases)
            return unified if unified is not None else UNIT
        return UNIT

    def _check_for(self, stmt: ForStmt, env: TypeEnv):
        iter_type = self.check_expr(stmt.iter, env)
        elem_type = UNIT
        if isinstance(iter_type, WList):
            elem_type = iter_type.element
        elif isinstance(iter_type, WMap):
            elem_type = iter_type.key
        elif not isinstance(iter_type, WNever):
            self.error(f"'for' requires iterable, got {type_name(iter_type)}.")

        child = env.child()
        child.define(stmt.var, elem_type, mutable=False)
        self._check_block(stmt.body, child)

    def _check_match(self, stmt: MatchStmt, env: TypeEnv) -> WType:
        match_type = self.check_expr(stmt.expr, env)
        arm_types: list[WType] = []

        for arm in stmt.arms:
            child = env.child()
            self._check_pattern(arm.pattern, match_type, child)
            if arm.guard is not None:
                guard_type = self.check_expr(arm.guard, child)
                if guard_type != BOOL and not isinstance(guard_type, WNever):
                    self.error(f"Match guard must be bool, got {type_name(guard_type)}.")
            if isinstance(arm.body, Block):
                arm_types.append(self._check_block(arm.body, child))
            else:
                arm_types.append(self.check_expr(arm.body, child))

        if not arm_types:
            return UNIT

        result = arm_types[0]
        for t in arm_types[1:]:
            unified = common_type(result, t, self.reg.type_aliases)
            if unified is not None:
                result = unified
        return result

    def _check_pattern(self, pattern, match_type: WType, env: TypeEnv):
        if isinstance(pattern, WildcardPat):
            return
        if isinstance(pattern, LiteralPat):
            self.check_expr(pattern.lit, env)
            return
        if isinstance(pattern, BindingPat):
            env.define(pattern.name, match_type, mutable=False)
            return
        if isinstance(pattern, NominalPattern):
            info = self.reg.entities.get(pattern.type_name)
            if info:
                # Bind destructured fields
                field_list = list(info.fields.values())
                for i, arg in enumerate(pattern.args or []):
                    if isinstance(arg, BindingPat):
                        ft = field_list[i].type if i < len(field_list) else UNIT
                        env.define(arg.name, ft, mutable=False)
                    elif isinstance(arg, tuple) and len(arg) == 2:
                        # Named pattern arg
                        name, pat = arg
                        fi = info.fields.get(name)
                        if fi and isinstance(pat, BindingPat):
                            env.define(pat.name, fi.type, mutable=False)
            return
        if isinstance(pattern, StructuralPattern):
            for member in pattern.members:
                if member[0] == "field" and len(member) >= 3:
                    field_name = member[1]
                    env.define(field_name, UNIT, mutable=False)
            return

    def _check_try(self, stmt: TryStmt, env: TypeEnv) -> WType:
        body_type = self._check_block(stmt.body, env)
        catch_types = []
        for catch in stmt.catches:
            child = env.child()
            if catch.var:
                exc_type = _resolve_type_node(catch.type, {}, self.reg)
                child.define(catch.var, exc_type, mutable=False)
            catch_types.append(self._check_block(catch.body, child))

        if catch_types:
            result = body_type
            for ct in catch_types:
                unified = common_type(result, ct, self.reg.type_aliases)
                if unified is not None:
                    result = unified
            return result
        return body_type

    def _check_throw(self, stmt: ThrowStmt, env: TypeEnv):
        self.check_expr(stmt.expr, env)

    def _check_early_return(self, stmt: EarlyReturn, env: TypeEnv) -> WType:
        if stmt.value is not None:
            val_type = self.check_expr(stmt.value, env)
            if env.current_ret is not None:
                if not is_assignable(env.current_ret, val_type, self.reg.type_aliases) and not isinstance(val_type, WNever):
                    self.error(
                        f"Early return type mismatch: expected {type_name(env.current_ret)}, "
                        f"got {type_name(val_type)}."
                    )
        return NEVER

    def _check_block(self, block: Block, env: TypeEnv) -> WType:
        child = env.child()
        last_stmt_type = UNIT
        for stmt in block.stmts:
            self.check_stmt(stmt, child)
            # Track if the last statement is an early return or throw
            if isinstance(stmt, EarlyReturn) or isinstance(stmt, ThrowStmt):
                last_stmt_type = NEVER
            else:
                last_stmt_type = UNIT
        if block.tail is not None:
            return self.check_expr(block.tail, child)
        # If the last statement was an early return/throw, the block never "falls through"
        if isinstance(last_stmt_type, WNever):
            return NEVER
        return UNIT

    # ── Check function and method bodies ─────────────────────────────────

    def check_function_body(self, decl: FunctionDecl):
        if decl.body is None:
            return
        fn_info = self.reg.functions.get(decl.name)
        if fn_info is None:
            return

        env = TypeEnv(current_ret=fn_info.ret)
        # Define params
        for pname, ptype in fn_info.params:
            env.define(pname, ptype, mutable=False)
        # Define top-level functions and builtins for body access
        self._populate_global_env(env)

        body_type = self._check_block(decl.body, env)

        if fn_info.ret != UNIT and not isinstance(body_type, WNever):
            if not is_assignable(fn_info.ret, body_type, self.reg.type_aliases):
                self.error(
                    f"Function '{decl.name}': expected return type {type_name(fn_info.ret)}, "
                    f"body returns {type_name(body_type)}."
                )

    def check_method_body(self, entity_name: str, method: MethodDecl):
        if method.body is None:
            return
        info = self.reg.entities.get(entity_name)
        if info is None:
            return
        mi = info.methods.get(method.name)
        if mi is None:
            return

        env = TypeEnv(
            current_entity=entity_name,
            current_ret=mi.ret,
            self_mutable=method.mut,
        )
        # Define self
        env.define("self", WEntity(entity_name), mutable=method.mut)
        # Define params
        for pname, ptype in mi.params:
            env.define(pname, ptype, mutable=False)
        # Populate globals
        self._populate_global_env(env)

        body_type = self._check_block(method.body, env)

        if mi.ret != UNIT and not isinstance(body_type, WNever):
            if not is_assignable(mi.ret, body_type, self.reg.type_aliases):
                self.error(
                    f"Method '{entity_name}.{method.name}': expected return type {type_name(mi.ret)}, "
                    f"body returns {type_name(body_type)}."
                )

    def check_constructor_body(self, entity_name: str, ctor: ConstructorDecl):
        if ctor.body is None:
            return
        info = self.reg.entities.get(entity_name)
        if info is None:
            return

        env = TypeEnv(
            current_entity=entity_name,
            current_ret=UNIT,
            self_mutable=True,
        )
        env.define("self", WEntity(entity_name), mutable=True)
        if info.constructor_params:
            for pname, ptype in info.constructor_params:
                env.define(pname, ptype, mutable=False)
        self._populate_global_env(env)
        self._check_block(ctor.body, env)

    def _populate_global_env(self, env: TypeEnv):
        # Define top-level functions
        for name, fn_info in self.reg.functions.items():
            env.define(name, WFunction(
                tuple(t for _, t in fn_info.params), fn_info.ret,
            ), mutable=False)
        # Define top-level bindings
        for name, ty in self._top_level_bindings.items():
            env.define(name, ty, mutable=False)
        # Builtins
        env.define("print", WFunction((UNIT,), UNIT), mutable=False)
        env.define("len", WFunction((UNIT,), INT), mutable=False)
        env.define("abs", WFunction((INT,), INT), mutable=False)
        # C library imports — register as known names (type checking is lenient)
        for name in self._c_lib_names:
            if env.lookup(name) is None:
                env.define(name, _C_LIB_SENTINEL, mutable=False)


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════

def check_program(program: Program) -> None:
    errors: list[str] = []
    try:
        module_symbols = build_module_symbols(program)
        program.component_to_entity = _build_component_entity_map(program, module_symbols)
    except SemanticError as e:
        raise TypeCheckError(str(e)) from e

    # Pass 1: TypeRef validation
    _pass1_check_decl_types(program, module_symbols, errors)
    if errors:
        raise TypeCheckError("\n".join(errors))

    # Pass 2: Build registries
    registries = _build_registries(program, module_symbols, errors)
    _check_component_requires(program, registries, errors)
    if errors:
        raise TypeCheckError("\n".join(errors))

    # Pass 3: Check function and method bodies
    checker = _Checker(registries, errors)

    # Pre-collect top-level binding types so they're available to function bodies
    for decl in program.decls:
        if isinstance(decl, Binding):
            env = TypeEnv()
            checker._populate_global_env(env)
            val_type = checker.check_expr(decl.value, env)
            if decl.type is not None:
                declared = _resolve_type_node(decl.type, {}, registries)
                if not is_assignable(declared, val_type, registries.type_aliases) and not isinstance(val_type, WNever):
                    errors.append(
                        f"Binding '{decl.name}': declared type {type_name(declared)}, "
                        f"got {type_name(val_type)}."
                    )
                checker._top_level_bindings[decl.name] = declared
            else:
                if isinstance(val_type, WNull):
                    errors.append(f"Cannot infer type of '{decl.name}' from null. Add a type annotation.")
                    checker._top_level_bindings[decl.name] = UNIT
                elif isinstance(val_type, WNever):
                    checker._top_level_bindings[decl.name] = UNIT
                else:
                    checker._top_level_bindings[decl.name] = val_type

    for decl in program.decls:
        if isinstance(decl, FunctionDecl):
            checker.check_function_body(decl)
        elif isinstance(decl, (EntityDecl, ComponentDecl)):
            for member in decl.members:
                if isinstance(member, MethodDecl):
                    checker.check_method_body(decl.name, member)
                elif isinstance(member, ConstructorDecl):
                    checker.check_constructor_body(decl.name, member)
        elif isinstance(decl, ExtendDecl):
            target_name = getattr(decl.type, "name", None)
            if target_name:
                canonical = target_name
                while canonical in registries.type_aliases:
                    canonical = registries.type_aliases[canonical]
                for method in decl.methods:
                    checker.check_method_body(canonical, method)

    if errors:
        raise TypeCheckError("\n".join(errors))
