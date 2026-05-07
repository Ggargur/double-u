"""
Parser for the experimental language.
Uses Lark with Earley parsing.
"""

from __future__ import annotations
from pathlib import Path
from lark import Lark, Transformer, Tree, Token
from lark.exceptions import UnexpectedInput


# ── Load grammar ──────────────────────────────────────────────────────────────

_GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"

_parser = Lark(
    _GRAMMAR_PATH.read_text(),
    parser="earley",
    ambiguity="resolve",
    propagate_positions=True,
)


# ── AST Nodes ─────────────────────────────────────────────────────────────────

class Node:
    __slots__: tuple[str, ...] = ()

    def __repr__(self) -> str:
        fields = ", ".join(f"{s}={getattr(self, s)!r}" for s in self.__slots__)
        return f"{type(self).__name__}({fields})"


# Programs & imports
class Program(Node):
    __slots__ = ("imports", "decls")
    def __init__(self, imports, decls): self.imports = imports; self.decls = decls

class ImportStmt(Node):
    __slots__ = ("module", "items")
    def __init__(self, module, items): self.module = module; self.items = items

class ImportItem(Node):
    __slots__ = ("name", "alias")
    def __init__(self, name, alias=None): self.name = name; self.alias = alias

# Declarations
class EntityDecl(Node):
    __slots__ = ("attrs", "name", "generics", "members")
    def __init__(self, attrs, name, generics, members):
        self.attrs = attrs; self.name = name; self.generics = generics; self.members = members

class EntityAlias(Node):
    __slots__ = ("name", "generics", "target")
    def __init__(self, name, generics, target):
        self.name = name; self.generics = generics; self.target = target

class FieldDecl(Node):
    __slots__ = ("attrs", "name", "public", "type", "default")
    def __init__(self, attrs, name, public, type_, default):
        self.attrs = attrs; self.name = name; self.public = public
        self.type = type_; self.default = default

class MethodDecl(Node):
    __slots__ = ("attrs", "mut", "name", "public", "generics", "params", "ret", "where_", "body")
    def __init__(self, attrs, mut, name, public, generics, params, ret, where_, body):
        self.attrs = attrs; self.mut = mut; self.name = name; self.public = public
        self.generics = generics; self.params = params; self.ret = ret
        self.where_ = where_; self.body = body

class ConstructorDecl(Node):
    __slots__ = ("attrs", "params", "body")
    def __init__(self, attrs, params, body):
        self.attrs = attrs; self.params = params; self.body = body

class CapabilityDecl(Node):
    __slots__ = ("name", "generics", "body")
    def __init__(self, name, generics, body):
        self.name = name; self.generics = generics; self.body = body

class CapabilityBody(Node):
    __slots__ = ("members",)
    def __init__(self, members): self.members = members

class CapFieldMember(Node):
    __slots__ = ("name", "type")
    def __init__(self, name, type_): self.name = name; self.type = type_

class CapMethodMember(Node):
    __slots__ = ("mut", "name", "args", "ret")
    def __init__(self, mut, name, args, ret):
        self.mut = mut; self.name = name; self.args = args; self.ret = ret

class CapLikeMember(Node):
    __slots__ = ("mut", "name", "like")
    def __init__(self, mut, name, like):
        self.mut = mut; self.name = name; self.like = like

class ExtendDecl(Node):
    __slots__ = ("type", "methods")
    def __init__(self, type_, methods): self.type = type_; self.methods = methods

class AttributeDecl(Node):
    __slots__ = ("name", "params")
    def __init__(self, name, params): self.name = name; self.params = params

class Attribute(Node):
    __slots__ = ("name", "args")
    def __init__(self, name, args): self.name = name; self.args = args

class ExceptionDecl(Node):
    __slots__ = ("name", "params")
    def __init__(self, name, params): self.name = name; self.params = params

class FunctionDecl(Node):
    __slots__ = ("attrs", "name", "public", "generics", "params", "ret", "where_", "body")
    def __init__(self, attrs, name, public, generics, params, ret, where_, body):
        self.attrs = attrs; self.name = name; self.public = public
        self.generics = generics; self.params = params; self.ret = ret
        self.where_ = where_; self.body = body

class Binding(Node):
    __slots__ = ("kind", "name", "type", "value")
    def __init__(self, kind, name, type_, value):
        self.kind = kind; self.name = name; self.type = type_; self.value = value

# Generics
class GenericParam(Node):
    __slots__ = ("name", "bound", "default")
    def __init__(self, name, bound, default):
        self.name = name; self.bound = bound; self.default = default

class WhereClause(Node):
    __slots__ = ("bounds",)
    def __init__(self, bounds): self.bounds = bounds

class WhereBound(Node):
    __slots__ = ("name", "bound")
    def __init__(self, name, bound): self.name = name; self.bound = bound

class Param(Node):
    __slots__ = ("attrs", "name", "type", "default")
    def __init__(self, attrs, name, type_, default):
        self.attrs = attrs; self.name = name; self.type = type_; self.default = default

# Types
class IntersectionType(Node):
    __slots__ = ("types",)
    def __init__(self, types): self.types = types

class NullableType(Node):
    __slots__ = ("inner",)
    def __init__(self, inner): self.inner = inner

class PrimitiveType(Node):
    __slots__ = ("name",)
    def __init__(self, name): self.name = name

class SelfType(Node):
    __slots__ = ()

class WildcardType(Node):
    __slots__ = ()

class TypeRef(Node):
    __slots__ = ("name", "args")
    def __init__(self, name, args=None): self.name = name; self.args = args

class ListType(Node):
    __slots__ = ("element",)
    def __init__(self, element): self.element = element

class MapType(Node):
    __slots__ = ("key", "value")
    def __init__(self, key, value): self.key = key; self.value = value

# Statements
class Assignment(Node):
    __slots__ = ("target", "op", "value")
    def __init__(self, target, op, value):
        self.target = target; self.op = op; self.value = value

class IfStmt(Node):
    __slots__ = ("cond", "then", "else_")
    def __init__(self, cond, then, else_):
        self.cond = cond; self.then = then; self.else_ = else_

class ForStmt(Node):
    __slots__ = ("var", "iter", "body")
    def __init__(self, var, iter_, body):
        self.var = var; self.iter = iter_; self.body = body

class MatchStmt(Node):
    __slots__ = ("expr", "arms")
    def __init__(self, expr, arms): self.expr = expr; self.arms = arms

class MatchArm(Node):
    __slots__ = ("pattern", "guard", "body")
    def __init__(self, pattern, guard, body):
        self.pattern = pattern; self.guard = guard; self.body = body

class TryStmt(Node):
    __slots__ = ("body", "catches")
    def __init__(self, body, catches): self.body = body; self.catches = catches

class CatchClause(Node):
    __slots__ = ("var", "type", "body")
    def __init__(self, var, type_, body):
        self.var = var; self.type = type_; self.body = body

class ThrowStmt(Node):
    __slots__ = ("expr",)
    def __init__(self, expr): self.expr = expr

class EarlyReturn(Node):
    __slots__ = ("value",)
    def __init__(self, value): self.value = value

class SpawnStmt(Node):
    __slots__ = ("body",)
    def __init__(self, body): self.body = body

class SelectStmt(Node):
    __slots__ = ("arms",)
    def __init__(self, arms): self.arms = arms

class SelectArm(Node):
    __slots__ = ("recv", "body")
    def __init__(self, recv, body): self.recv = recv; self.body = body

class Block(Node):
    __slots__ = ("stmts", "tail")
    def __init__(self, stmts, tail): self.stmts = stmts; self.tail = tail

# Expressions
class BinOp(Node):
    __slots__ = ("op", "left", "right")
    def __init__(self, op, left, right):
        self.op = op; self.left = left; self.right = right

class UnaryOp(Node):
    __slots__ = ("op", "operand")
    def __init__(self, op, operand): self.op = op; self.operand = operand

class FieldAccess(Node):
    __slots__ = ("obj", "field")
    def __init__(self, obj, field): self.obj = obj; self.field = field

class OptionalChain(Node):
    __slots__ = ("obj", "field")
    def __init__(self, obj, field): self.obj = obj; self.field = field

class Call(Node):
    __slots__ = ("callee", "args")
    def __init__(self, callee, args): self.callee = callee; self.args = args

class Index(Node):
    __slots__ = ("obj", "idx")
    def __init__(self, obj, idx): self.obj = obj; self.idx = idx

class NonNull(Node):
    __slots__ = ("expr",)
    def __init__(self, expr): self.expr = expr

class NameExpr(Node):
    __slots__ = ("name",)
    def __init__(self, name): self.name = name

class SelfExpr(Node):
    __slots__ = ()

class ListLit(Node):
    __slots__ = ("elements",)
    def __init__(self, elements): self.elements = elements

class MapLit(Node):
    __slots__ = ("entries",)
    def __init__(self, entries): self.entries = entries

class MapEntry(Node):
    __slots__ = ("key", "value")
    def __init__(self, key, value): self.key = key; self.value = value

class ConstructorCall(Node):
    __slots__ = ("type_name", "generic_args", "args")
    def __init__(self, type_name, generic_args, args):
        self.type_name = type_name; self.generic_args = generic_args; self.args = args

class Argument(Node):
    __slots__ = ("label", "value")
    def __init__(self, label, value): self.label = label; self.value = value

# Literals
class IntLit(Node):
    __slots__ = ("value",)
    def __init__(self, value: int): self.value = value

class FloatLit(Node):
    __slots__ = ("value",)
    def __init__(self, value: float): self.value = value

class StringLit(Node):
    __slots__ = ("parts",)
    def __init__(self, parts): self.parts = parts  # list[str | ("interp", str)]

class BoolLit(Node):
    __slots__ = ("value",)
    def __init__(self, value: bool): self.value = value

class NullLit(Node):
    __slots__ = ()

# Patterns
class WildcardPat(Node): __slots__ = ()

class LiteralPat(Node):
    __slots__ = ("lit",)
    def __init__(self, lit): self.lit = lit

class BindingPat(Node):
    __slots__ = ("name",)
    def __init__(self, name): self.name = name

class NominalPattern(Node):
    __slots__ = ("type_name", "args")
    def __init__(self, type_name, args): self.type_name = type_name; self.args = args

class StructuralPattern(Node):
    __slots__ = ("members",)
    def __init__(self, members): self.members = members


# ── Internal postfix sentinels ─────────────────────────────────────────────────

class _FA:  # field access
    def __init__(self, f): self.f = f
class _OC:  # optional chain
    def __init__(self, f): self.f = f
class _CA:  # call
    def __init__(self, a): self.a = a
class _IX:  # index
    def __init__(self, i): self.i = i
class _NN:  # non-null
    pass


# ── Transformer ───────────────────────────────────────────────────────────────

class LangTransformer(Transformer):

    # helpers
    def _s(self, t) -> str: return str(t)

    def _is_node(self, x) -> bool: return isinstance(x, Node)

    # ── program ───────────────────────────────────────────────────────────────

    def start(self, items):
        imports = [i for i in items if isinstance(i, ImportStmt)]
        decls   = [i for i in items if isinstance(i, Node) and not isinstance(i, ImportStmt)]
        return Program(imports, decls)

    # ── imports ───────────────────────────────────────────────────────────────

    def import_stmt(self, items):
        lists = [i for i in items if isinstance(i, list)]
        qname = next(
            (lst for lst in lists if lst and isinstance(lst[0], str)),
            [],
        )
        import_items = next(
            (lst for lst in lists if lst and isinstance(lst[0], ImportItem)),
            None,
        )
        return ImportStmt(qname, import_items)

    def import_items(self, items): return items
    def import_item(self, items):
        identifiers = [i for i in items if isinstance(i, Token) and i.type == "IDENTIFIER"]
        name = self._s(identifiers[0]) if identifiers else ""
        alias = self._s(identifiers[1]) if len(identifiers) > 1 else None
        return ImportItem(name, alias)

    def qualified_name(self, items): return [self._s(t) for t in items]

    # ── top-level ─────────────────────────────────────────────────────────────

    def top_level_decl(self, items): return items[0]

    # ── entities ──────────────────────────────────────────────────────────────

    def entity_decl(self, items):
        attrs = [i for i in items if isinstance(i, Attribute)]
        rest  = [i for i in items if not isinstance(i, Attribute)]
        name     = self._s(rest[0])
        generics = rest[1] if len(rest) > 1 and isinstance(rest[1], list) else None
        members  = [i for i in rest[1:] if isinstance(i, Node)]
        return EntityDecl(attrs, name, generics, members)

    def entity_alias(self, items):
        name     = self._s(items[0])
        generics = items[1] if len(items) > 2 else None
        target   = items[-1]
        return EntityAlias(name, generics, target)

    def entity_member(self, items):
        return items[0]

    def field_decl(self, items):
        attrs   = [i for i in items if isinstance(i, Attribute)]
        tokens  = [i for i in items if isinstance(i, Token)]
        nodes   = [i for i in items if isinstance(i, Node)]
        name    = self._s(tokens[0]) if tokens else ""
        public  = any(self._s(t) == "!" for t in tokens)
        type_   = nodes[0] if nodes else None
        default = nodes[1] if len(nodes) > 1 else None
        return FieldDecl(attrs, name, public, type_, default)

    def method_decl(self, items):
        attrs   = [i for i in items if isinstance(i, Attribute)]
        tokens  = [i for i in items if isinstance(i, Token)]
        nodes   = [i for i in items if isinstance(i, Node) and not isinstance(i, Attribute)]

        mut    = any(self._s(t) == "mut" for t in tokens)
        name   = self._s(next(t for t in tokens if t.type == "IDENTIFIER"))
        public = any(self._s(t) == "!" for t in tokens)

        generics = next((n for n in nodes if isinstance(n, list)), None)
        params_  = next((n for n in nodes if isinstance(n, list) and
                         n and isinstance(n[0], Param)), None)
        where_   = next((n for n in nodes if isinstance(n, WhereClause)), None)
        body     = next((n for n in nodes if isinstance(n, Block)), None)
        ret      = next((n for n in nodes if isinstance(n, Node) and
                         not isinstance(n, (WhereClause, Block, Param))), None)
        return MethodDecl(attrs, mut, name, public, generics, params_, ret, where_, body)

    def constructor_decl(self, items):
        attrs  = [i for i in items if isinstance(i, Attribute)]
        nodes  = [i for i in items if isinstance(i, Node) and not isinstance(i, Attribute)]
        params_ = next((n for n in nodes if isinstance(n, list)), None)
        body   = next((n for n in nodes if isinstance(n, Block)), None)
        return ConstructorDecl(attrs, params_, body)

    # ── capabilities ──────────────────────────────────────────────────────────

    def capability_decl(self, items):
        tokens = [i for i in items if isinstance(i, Token)]
        name   = self._s(tokens[0])
        nodes  = [i for i in items if isinstance(i, Node)]
        generics = next((n for n in nodes if isinstance(n, list)), None)
        body     = next((n for n in nodes if isinstance(n, CapabilityBody)), None)
        return CapabilityDecl(name, generics, body)

    def capability_body(self, items):
        # items[0] is the cap_member_list result (a list)
        members = items[0] if items and isinstance(items[0], list) else items
        return CapabilityBody(members)

    def cap_field(self, items):
        return CapFieldMember(self._s(items[0]), items[1])

    def cap_member_list(self, items): return items

    def cap_method(self, items):
        tokens = [i for i in items if isinstance(i, Token)]
        nodes  = [i for i in items if isinstance(i, Node)]
        mut  = any(self._s(t) == "mut" for t in tokens)
        name = self._s(next(t for t in tokens if t.type == "IDENTIFIER"))
        args = next((n for n in nodes if isinstance(n, list)), [])
        ret  = next((n for n in nodes if isinstance(n, Node) and not isinstance(n, list)), None)
        return CapMethodMember(mut, name, args, ret)

    def cap_like(self, items):
        tokens = [i for i in items if isinstance(i, Token)]
        mut  = any(self._s(t) == "mut" for t in tokens)
        name = self._s(next(t for t in tokens if t.type == "IDENTIFIER"))
        like = next(i for i in items if isinstance(i, list))
        return CapLikeMember(mut, name, like)

    # ── extend ────────────────────────────────────────────────────────────────

    def extend_decl(self, items):
        nodes = [i for i in items if isinstance(i, Node)]
        return ExtendDecl(nodes[0], nodes[1:])

    # ── attributes & exceptions ───────────────────────────────────────────────

    def attribute_decl(self, items):
        name    = self._s(items[0])
        params_ = next((i for i in items if isinstance(i, list)), None)
        return AttributeDecl(name, params_)

    def attribute_line(self, items):
        return items[0]  # unwrap to Attribute

    def attribute(self, items):
        name = next(i for i in items if isinstance(i, list))
        args = next((i for i in items if isinstance(i, list) and
                     i and isinstance(i[0], Argument)), None)
        return Attribute(name, args)

    def exception_decl(self, items):
        name    = self._s(items[0])
        params_ = next((i for i in items if isinstance(i, list)), None)
        return ExceptionDecl(name, params_)

    # ── functions ─────────────────────────────────────────────────────────────

    def function_decl(self, items):
        attrs  = [i for i in items if isinstance(i, Attribute)]
        tokens = [i for i in items if isinstance(i, Token)]
        nodes  = [i for i in items if isinstance(i, Node) and not isinstance(i, Attribute)]

        name   = self._s(next(t for t in tokens if t.type == "IDENTIFIER"))
        public = any(self._s(t) == "!" for t in tokens)

        generics = next((n for n in nodes if isinstance(n, list) and
                         (not n or isinstance(n[0], GenericParam))), None)
        params_  = next((n for n in nodes if isinstance(n, list) and
                         n and isinstance(n[0], Param)), None)
        where_   = next((n for n in nodes if isinstance(n, WhereClause)), None)
        body     = next((n for n in nodes if isinstance(n, Block)), None)
        # ret is the remaining non-list, non-clause, non-block Node
        candidates = [n for n in nodes if not isinstance(n, (list, WhereClause, Block))]
        ret = candidates[0] if candidates else None
        return FunctionDecl(attrs, name, public, generics, params_, ret, where_, body)

    # ── bindings ──────────────────────────────────────────────────────────────

    def binding(self, items):
        kind   = self._s(items[0])   # "const" or "let"
        name   = self._s(items[1])
        nodes  = [i for i in items[2:] if isinstance(i, Node)]
        type_  = nodes[0] if len(nodes) > 1 else None
        value  = nodes[-1]
        return Binding(kind, name, type_, value)

    # ── generics ──────────────────────────────────────────────────────────────

    def generic_params(self, items): return items

    def generic_param(self, items):
        name    = self._s(items[0])
        nodes   = [i for i in items[1:] if isinstance(i, Node)]
        bound   = nodes[0] if nodes else None
        default = nodes[1] if len(nodes) > 1 else None
        return GenericParam(name, bound, default)

    def generic_args(self, items): return items

    def where_clause(self, items): return WhereClause(items)

    def where_bound(self, items):
        return WhereBound(self._s(items[0]), items[1])

    def params(self, items): return items

    def param(self, items):
        attrs  = [i for i in items if isinstance(i, Attribute)]
        tokens = [i for i in items if isinstance(i, Token)]
        nodes  = [i for i in items if isinstance(i, Node) and not isinstance(i, Attribute)]
        name   = self._s(next(t for t in tokens if t.type == "IDENTIFIER"))
        type_  = nodes[0] if nodes else None
        default = nodes[1] if len(nodes) > 1 else None
        return Param(attrs, name, type_, default)

    # ── types ─────────────────────────────────────────────────────────────────

    def type(self, items): return items[0]

    def intersection_type(self, items):
        return IntersectionType(items) if len(items) > 1 else items[0]

    def nullable_type(self, items):
        inner = items[0]
        nullable = any(isinstance(i, Token) and self._s(i) == "?" for i in items)
        return NullableType(inner) if nullable else inner

    def primary_type(self, items): return items[0]
    def self_type(self, items): return SelfType()
    def wildcard_type(self, items): return WildcardType()

    def primitive_type(self, items):
        return PrimitiveType(self._s(items[0]))

    def type_ref(self, items):
        name = self._s(items[0])
        args = items[1] if len(items) > 1 else None
        return TypeRef(name, args)

    def list_type(self, items): return ListType(items[0])
    def map_type(self, items): return MapType(items[0], items[1])
    def anonymous_capability(self, items): return items[0]
    def type_list(self, items): return items

    # ── statements ────────────────────────────────────────────────────────────

    def statement(self, items): return items[0]

    def assignment(self, items):
        return Assignment(items[0], self._s(items[1]), items[2])

    def assign_op(self, items): return items[0]
    def lvalue(self, items): return items[0]
    def expression_stmt(self, items): return items[0]

    def if_stmt(self, items):
        nodes = [i for i in items if isinstance(i, Node)]
        cond  = nodes[0]
        then  = nodes[1]
        else_ = nodes[2] if len(nodes) > 2 else None
        return IfStmt(cond, then, else_)

    def for_stmt(self, items):
        tokens = [i for i in items if isinstance(i, Token)]
        nodes  = [i for i in items if isinstance(i, Node)]
        var    = self._s(next(t for t in tokens if t.type == "IDENTIFIER"))
        return ForStmt(var, nodes[0], nodes[1])

    def match_stmt(self, items):
        nodes = [i for i in items if isinstance(i, Node)]
        return MatchStmt(nodes[0], nodes[1:])

    def match_arm(self, items):
        nodes = [i for i in items if isinstance(i, Node)]
        pat   = nodes[0]
        # guard is present if there are 3 node children
        if len(nodes) == 3:
            guard = nodes[1]; body = nodes[2]
        else:
            guard = None; body = nodes[1]
        return MatchArm(pat, guard, body)

    def try_stmt(self, items):
        nodes = [i for i in items if isinstance(i, Node)]
        return TryStmt(nodes[0], nodes[1:])

    def catch_clause(self, items):
        tokens = [i for i in items if isinstance(i, Token)]
        nodes  = [i for i in items if isinstance(i, Node)]
        var    = self._s(next((t for t in tokens if t.type == "IDENTIFIER"), None)) \
                 if any(t.type == "IDENTIFIER" for t in tokens) else None
        return CatchClause(var, nodes[0], nodes[1])

    def throw_stmt(self, items):
        return ThrowStmt(next(i for i in items if isinstance(i, Node)))

    def early_return(self, items):
        nodes = [i for i in items if isinstance(i, Node)]
        return EarlyReturn(nodes[0] if nodes else None)

    def spawn_stmt(self, items):
        return SpawnStmt(next(i for i in items if isinstance(i, Node)))

    def select_stmt(self, items):
        return SelectStmt([i for i in items if isinstance(i, SelectArm)])

    def select_arm(self, items):
        nodes  = [i for i in items if isinstance(i, Node)]
        tokens = [i for i in items if isinstance(i, Token)]
        is_timeout = any(self._s(t) == "timeout" for t in tokens)
        if is_timeout:
            recv = ("timeout", nodes[0])
            body = nodes[1]
        else:
            var  = self._s(next(t for t in tokens if t.type == "IDENTIFIER"))
            recv = (var, nodes[0])
            body = nodes[1]
        return SelectArm(recv, body)

    # ── block ─────────────────────────────────────────────────────────────────

    def block(self, items):
        return items[0] if items else Block([], None)

    def block_body(self, items):
        stmts = []
        tail  = None
        for item in items:
            if isinstance(item, Node):
                # Expression that ended without _NL is the tail
                # We detect tail as the last item if it's a non-statement expression
                stmts.append(item)
        # Heuristic: if the last node is a pure expression (not a statement), it's the tail
        _stmt_types = (Binding, Assignment, IfStmt, ForStmt, MatchStmt,
                       TryStmt, ThrowStmt, EarlyReturn, SpawnStmt, SelectStmt)
        if stmts and not isinstance(stmts[-1], _stmt_types):
            tail = stmts.pop()
        return Block(stmts, tail)

    # ── expressions ───────────────────────────────────────────────────────────

    def expression(self, items): return items[0]

    def _binop(self, items):
        if len(items) == 1: return items[0]
        result = items[0]
        i = 1
        while i < len(items):
            op    = self._s(items[i]); i += 1
            right = items[i]; i += 1
            result = BinOp(op, result, right)
        return result

    def or_expr(self, items):             return self._binop(items)
    def and_expr(self, items):            return self._binop(items)
    def coalesce_expr(self, items):       return self._binop(items)
    def equality_expr(self, items):       return self._binop(items)
    def comparison_expr(self, items):     return self._binop(items)
    def additive_expr(self, items):       return self._binop(items)
    def multiplicative_expr(self, items): return self._binop(items)

    def unary_op(self, items):
        return UnaryOp(self._s(items[0]), items[1])

    def unary_expr(self, items): return items[0]

    def postfix_expr(self, items):
        expr = items[0]
        for op in items[1:]:
            if isinstance(op, _FA):  expr = FieldAccess(expr, op.f)
            elif isinstance(op, _OC): expr = OptionalChain(expr, op.f)
            elif isinstance(op, _CA): expr = Call(expr, op.a)
            elif isinstance(op, _IX): expr = Index(expr, op.i)
            elif isinstance(op, _NN): expr = NonNull(expr)
        return expr

    def field_access(self, items):
        # items[0] is the IDENTIFIER token (the "." was anonymous)
        return _FA(self._s(items[0]))
    def optional_chain(self, items):
        # items[0] is IDENTIFIER; OPT_CHAIN token was consumed
        return _OC(self._s(items[0]))
    def call(self, items):
        args = items[0] if items and isinstance(items[0], list) else []
        return _CA(args)
    def index(self, items):           return _IX(items[0])
    def non_null(self, items):        return _NN()

    def primary_expr(self, items): return items[0]
    def self_expr(self, items):    return SelfExpr()
    def grouped(self, items):      return items[0]
    def name_expr(self, items):    return NameExpr(self._s(items[0]))
    def early_return_expr(self, items):
        return EarlyReturn(items[0] if items else None)
    def if_expr(self, items):   return items[0]
    def match_expr(self, items): return items[0]

    # ── literals ──────────────────────────────────────────────────────────────

    def literal(self, items): return items[0]

    def int_lit(self, items):
        return IntLit(int(self._s(items[0]).replace("_", "")))

    def float_lit(self, items):
        return FloatLit(float(self._s(items[0]).replace("_", "")))

    def string_lit(self, items):
        raw = self._s(items[0])[1:-1]  # strip quotes
        return StringLit(_parse_interp(raw))

    def bool_lit(self, items):
        return BoolLit(self._s(items[0]) == "true")

    def null_lit(self, items): return NullLit()

    # ── collections ───────────────────────────────────────────────────────────

    def list_literal(self, items):
        return ListLit([i for i in items if isinstance(i, Node)])

    def map_literal(self, items):
        return MapLit([i for i in items if isinstance(i, MapEntry)])

    def map_entry(self, items):
        return MapEntry(items[0], items[1])

    def constructor_call(self, items):
        tokens = [i for i in items if isinstance(i, Token)]
        name   = self._s(tokens[0])
        nodes  = [i for i in items if isinstance(i, Node) or isinstance(i, list)]
        generic_args_ = next((n for n in nodes if isinstance(n, list) and
                              (not n or isinstance(n[0], Node))), None)
        args   = next((n for n in nodes if isinstance(n, list) and
                       (not n or isinstance(n[0], Argument))), [])
        return ConstructorCall(name, generic_args_, args)

    def argument_list(self, items): return items

    def argument(self, items):
        tokens = [i for i in items if isinstance(i, Token)]
        nodes  = [i for i in items if isinstance(i, Node)]
        label  = self._s(tokens[0]) if tokens else None
        return Argument(label, nodes[0])

    # ── patterns ──────────────────────────────────────────────────────────────

    def pattern(self, items): return items[0]
    def wildcard_pat(self, items): return WildcardPat()
    def literal_pat(self, items): return LiteralPat(items[0])
    def binding_pat(self, items): return BindingPat(self._s(items[0]))

    def nominal_pattern(self, items):
        tokens = [i for i in items if isinstance(i, Token)]
        args   = [i for i in items if isinstance(i, Node) or isinstance(i, tuple)]
        name   = self._s(tokens[0]) if tokens else ""
        return NominalPattern(name, args)

    def named_pattern_arg(self, items):
        return (self._s(items[0]), items[1])

    def pos_pattern_arg(self, items): return items[0]

    def structural_pattern(self, items): return StructuralPattern(items)
    def struct_field_member(self, items):
        return ("field", self._s(items[0]), items[1])
    def struct_method_member(self, items):
        return ("method", self._s(items[0]), items[1:-1], items[-1])
    def struct_like_member(self, items):
        return ("like", self._s(items[0]), items[1])


# ── String interpolation helper ───────────────────────────────────────────────

def _parse_interp(raw: str) -> list:
    """Split raw string content into text segments and interpolation markers."""
    parts: list = []
    buf = ""
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            buf += ch + raw[i + 1]; i += 2
        elif ch == "{":
            depth = 0
            j = i
            while j < len(raw):
                if raw[j] == "{": depth += 1
                elif raw[j] == "}":
                    depth -= 1
                    if depth == 0: break
                j += 1
            if buf: parts.append(buf); buf = ""
            parts.append(("interp", raw[i + 1:j]))
            i = j + 1
        else:
            buf += ch; i += 1
    if buf: parts.append(buf)
    return parts or [""]


# ── Public API ────────────────────────────────────────────────────────────────

_transformer = LangTransformer()


def _format_syntax_error(source: str, err: UnexpectedInput) -> str:
    line = getattr(err, "line", "?")
    column = getattr(err, "column", "?")
    context = err.get_context(source, span=40).rstrip()
    parts = [f"Syntax error at line {line}, column {column}.", context]
    expected = sorted(getattr(err, "expected", set()) or [])
    if expected:
        preview = ", ".join(expected[:12])
        if len(expected) > 12:
            preview += ", ..."
        parts.append(f"Expected one of: {preview}")
    return "\n".join(parts)


def parse(source: str) -> Program:
    """Parse source code and return an AST."""
    try:
        tree = _parser.parse(source)
        return _transformer.transform(tree)
    except UnexpectedInput as e:
        raise SyntaxError(_format_syntax_error(source, e)) from e


def parse_tree(source: str) -> Tree:
    """Return the raw Lark parse tree (useful for debugging)."""
    return _parser.parse(source)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python parser.py <file.lang> [--tree]")
        sys.exit(1)
    src = Path(sys.argv[1]).read_text()
    if "--tree" in sys.argv:
        print(parse_tree(src).pretty())
    else:
        ast = parse(src)
        print(ast)
