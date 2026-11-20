"""Parser descendente recursivo. Nunca depende de resolução de nomes."""

from __future__ import annotations

from . import ast as A
from .diagnostics import Abort, Diagnostics, Pos
from .lexer import Lexer, StrPart, StringLit, Token

STMT_END = ("NEWLINE", "DEDENT", "EOF", "WHERE")


class Parser:
    def __init__(self, tokens: list[Token], file: str, diags: Diagnostics):
        self.toks = tokens
        self.i = 0
        self.file = file
        self.diags = diags
        self.gen_mode = False

    # ------------------------------------------------------------ utilitários
    @property
    def tok(self) -> Token:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> Token:
        j = min(self.i + k, len(self.toks) - 1)
        return self.toks[j]

    def next(self) -> Token:
        t = self.toks[self.i]
        if self.i < len(self.toks) - 1:
            self.i += 1
        return t

    def at(self, kind: str, value: object = None) -> bool:
        return self.tok.is_(kind, value)

    def at_p(self, value: str) -> bool:
        return self.tok.is_("PUNCT", value)

    def at_kw(self, value: str) -> bool:
        return self.tok.is_("KW", value)

    def accept(self, kind: str, value: object = None) -> Token | None:
        if self.at(kind, value):
            return self.next()
        return None

    def error(self, msg: str, tok: Token | None = None) -> Abort:
        t = tok or self.tok
        self.diags.error("sintaxe", t.pos, msg)
        return Abort()

    def expect(self, kind: str, value: object = None, what: str | None = None) -> Token:
        if self.at(kind, value):
            return self.next()
        desc = what or (repr(value) if value is not None else kind)
        raise self.error(f"esperava {desc}, encontrei {self.describe(self.tok)}")

    def describe(self, t: Token) -> str:
        if t.kind in ("NEWLINE",):
            return "fim de linha"
        if t.kind == "INDENT":
            return "bloco indentado"
        if t.kind == "DEDENT":
            return "fim de bloco"
        if t.kind == "EOF":
            return "fim do arquivo"
        if t.kind == "STRING":
            return "string"
        return repr(t.value)

    def glued(self) -> bool:
        return self.at_p("(") and not self.tok.spaced

    def sync_decl(self) -> None:
        """Recuperação de erro: pula até a próxima declaração de topo."""
        depth = 0
        while not self.at("EOF"):
            t = self.next()
            if t.kind == "INDENT":
                depth += 1
            elif t.kind == "DEDENT":
                depth -= 1
            elif t.kind == "NEWLINE" and depth <= 0:
                return

    def sync_stmt(self) -> None:
        depth = 0
        while not self.at("EOF"):
            if depth == 0 and self.tok.kind in ("NEWLINE", "DEDENT"):
                return
            t = self.next()
            if t.kind == "INDENT":
                depth += 1
            elif t.kind == "DEDENT":
                depth -= 1

    # ------------------------------------------------------------ módulo
    def parse_module(self, name: str) -> A.Module:
        decls: list[A.Node] = []
        while True:
            while self.accept("NEWLINE"):
                pass
            if self.at("EOF"):
                break
            if self.at("DEDENT"):
                self.next()
                continue
            try:
                d = self.decl()
                if d is not None:
                    decls.append(d)
                if not self.at("EOF") and not self.at("NEWLINE"):
                    raise self.error(f"esperava fim de linha, encontrei {self.describe(self.tok)}")
            except Abort:
                self.sync_decl()
        return A.Module(Pos(self.file, 1, 1), name, self.file, decls)

    def decl(self) -> A.Node | None:
        attrs: dict[str, list[str]] = {}
        while self.at("BUILTIN") and self.tok.value in ("nocopy", "noderive"):
            b = self.next()
            args: list[str] = []
            if self.glued():
                self.next()
                while not self.at_p(")"):
                    args.append(str(self.expect("UNAME", what="nome de trait").value))
                self.expect("PUNCT", ")")
            attrs[str(b.value)] = args
            self.accept("NEWLINE")
        if self.at_kw("fn"):
            return self.fn_decl()
        if self.at_kw("type"):
            d = self.type_decl()
            d.attrs = attrs
            return d
        if self.at_kw("trait"):
            return self.trait_decl()
        if self.at_kw("impl"):
            return self.impl_decl()
        start = self.tok
        public = bool(self.accept("OP", "+"))
        if self.at("NAME") or self.at("UNAME"):
            name = self.next()
            ann = None
            if self.accept("PUNCT", ":"):
                ann = self.type_()
            self.expect("PUNCT", "=")
            expr = self.body()
            return A.LetDecl(start.pos, str(name.value), public, expr, ann)
        raise self.error(f"esperava uma declaração, encontrei {self.describe(self.tok)}")

    # ------------------------------------------------------------ fn
    def param(self, need_type: bool = False) -> A.Param:
        mode = "borrow"
        if self.accept("KW", "mut"):
            mode = "mut"
        elif self.accept("KW", "own"):
            mode = "own"
        name = self.expect("NAME", what="nome de parâmetro")
        ann = None
        if self.accept("PUNCT", ":"):
            ann = self.type_()
        return A.Param(name.pos, str(name.value), ann, mode)

    def fn_decl(self) -> A.FnDecl:
        start = self.expect("KW", "fn")
        public = False
        if self.at("OP", "+") and (self.peek().kind == "NAME" or self.peek().is_("PUNCT", "(")):
            # `fn +(a) + (b)` é infixa pública chamada `+`; `fn +nome` é pública
            if not (self.peek().is_("PUNCT", "(") and self._is_op_def_without_vis()):
                self.next()
                public = True
        if self.at("NAME"):
            name = self.next()
            fixity = "prefix"
            params: list[A.Param] = []
            self.expect("PUNCT", "(")
            while not self.at_p(")"):
                params.append(self.param())
            self.expect("PUNCT", ")")
        elif self.at_p("("):
            self.next()
            p1 = self.param()
            self.expect("PUNCT", ")")
            if self.accept("PUNCT", "."):
                name = self.expect("NAME", what="nome da pós-fixa")
                fixity = "postfix"
                params = [p1]
            else:
                if self.at("OP") or self.at("NAME"):
                    name = self.next()
                else:
                    raise self.error("esperava o nome da infixa")
                fixity = "infix"
                self.expect("PUNCT", "(")
                p2 = self.param()
                self.expect("PUNCT", ")")
                params = [p1, p2]
        else:
            raise self.error("esperava o nome da função")
        ret = None
        if self.accept("PUNCT", "->"):
            ret = self.type_(allow_io=False)
        io = bool(self.accept("KW", "io"))
        given = self.given()
        assoc = prec = None
        if self.at("KW") and self.tok.value in ("infixl", "infixr", "infix"):
            assoc = str(self.next().value)
            prec = int(self.expect("INT", what="precedência").value)
        is_gen = isinstance(ret, A.TName) and ret.name == "Gen" and ret.module is None
        body = None
        if self.accept("PUNCT", "="):
            saved = self.gen_mode
            self.gen_mode = is_gen
            try:
                body = self.body()
            finally:
                self.gen_mode = saved
        return A.FnDecl(start.pos, str(name.value), public, fixity, params, ret, io, given, body,
                        assoc, prec, is_gen)

    def _is_op_def_without_vis(self) -> bool:
        # distingue `fn +(a: T) + (b: T)` (pública) de `fn (a) + (b)`: sempre pública aqui
        return False

    def given(self) -> list[A.Constraint]:
        out: list[A.Constraint] = []
        if self.accept("KW", "given"):
            while self.at("UNAME"):
                t = self.next()
                self.expect("PUNCT", "(")
                ty = self.type_()
                self.expect("PUNCT", ")")
                out.append(A.Constraint(t.pos, str(t.value), ty))
        return out

    # ------------------------------------------------------------ type / trait / impl
    def type_decl(self) -> A.TypeDecl:
        start = self.expect("KW", "type")
        public = bool(self.accept("OP", "+"))
        name = self.expect("UNAME", what="nome do tipo")
        params: list[str] = []
        if self.glued():
            self.next()
            while not self.at_p(")"):
                params.append(str(self.expect("UNAME", what="parâmetro de tipo").value))
            self.expect("PUNCT", ")")
        self.expect("PUNCT", "=")
        if self.accept("PUNCT", "{"):
            fields: list[A.Field] = []
            while not self.at_p("}"):
                fpub = bool(self.accept("OP", "+"))
                fname = self.expect("NAME", what="nome de campo")
                self.expect("PUNCT", ":")
                fields.append(A.Field(fname.pos, str(fname.value), self.type_(), fpub))
                self.accept("PUNCT", ",")
            self.expect("PUNCT", "}")
            return A.TypeDecl(start.pos, str(name.value), public, params, fields, None)
        layout = bool(self.accept("INDENT"))
        variants: list[A.Variant] = []
        while True:
            if layout:
                self.accept("NEWLINE")
            self.accept("OP", "|")
            vt = self.expect("UNAME", what="nome de variante")
            vfields: list[A.Field] = []
            if self.glued():
                self.next()
                while not self.at_p(")"):
                    if self.at("NAME") and self.peek().is_("PUNCT", ":"):
                        fn_ = self.next()
                        self.next()
                        vfields.append(A.Field(fn_.pos, str(fn_.value), self.type_(), True))
                    else:
                        tp = self.tok.pos
                        vfields.append(A.Field(tp, None, self.type_(), True))
                self.expect("PUNCT", ")")
            variants.append(A.Variant(vt.pos, str(vt.value), vfields))
            if self.at("OP", "|") or (layout and self.at("NEWLINE") and self.peek().is_("OP", "|")):
                continue
            break
        if layout:
            self.accept("NEWLINE")
            self.expect("DEDENT")
        return A.TypeDecl(start.pos, str(name.value), public, params, None, variants)

    def trait_decl(self) -> A.TraitDecl:
        start = self.expect("KW", "trait")
        public = bool(self.accept("OP", "+"))
        name = self.expect("UNAME", what="nome da trait")
        self.expect("PUNCT", "(")
        param = self.expect("UNAME", what="parâmetro da trait")
        self.expect("PUNCT", ")")
        supers = self.given()
        self.expect("PUNCT", "=")
        methods = self.member_block()
        return A.TraitDecl(start.pos, str(name.value), public, str(param.value), methods, supers)

    def impl_decl(self) -> A.ImplDecl:
        start = self.expect("KW", "impl")
        module = None
        trait = self.expect("UNAME", what="nome da trait")
        self.expect("PUNCT", "(")
        ty = self.type_()
        self.expect("PUNCT", ")")
        given = self.given()
        self.expect("PUNCT", "=")
        methods = self.member_block()
        return A.ImplDecl(start.pos, str(trait.value), ty, given, methods, module)

    def member_block(self) -> list[A.FnDecl]:
        out: list[A.FnDecl] = []
        if not self.accept("INDENT"):
            if self.at_kw("fn"):
                return [self.fn_decl()]
            raise self.error("esperava um bloco de funções")
        while not self.at("DEDENT") and not self.at("EOF"):
            if self.accept("NEWLINE"):
                continue
            try:
                out.append(self.fn_decl())
            except Abort:
                self.sync_stmt()
        self.expect("DEDENT")
        return out

    # ------------------------------------------------------------ tipos
    def type_(self, allow_io: bool = True) -> A.TypeExpr:
        t = self.type_atom()
        if self.at_p("->"):
            self.next()
            ret = self.type_(allow_io)
            io = allow_io and bool(self.accept("KW", "io"))
            params = t.items if isinstance(t, A.TTuple) and getattr(t, "_paren", False) else [t]
            return A.TFunc(t.pos, params, ret, io)
        return t

    def type_atom(self) -> A.TypeExpr:
        t = self.tok
        if self.accept("PUNCT", "("):
            if self.accept("PUNCT", ")"):
                return A.TName(t.pos, "()")
            items = [self.type_()]
            while not self.at_p(")"):
                items.append(self.type_())
            self.expect("PUNCT", ")")
            if len(items) == 1 and not self.at_p("->"):
                return items[0]
            tt = A.TTuple(t.pos, items)
            tt._paren = True  # type: ignore[attr-defined]
            return tt
        if self.accept("PUNCT", "#("):
            items = []
            while not self.at_p(")"):
                items.append(self.type_())
            self.expect("PUNCT", ")")
            return A.TTuple(t.pos, items)
        if self.at("NAME") and self.peek().is_("PUNCT", ".") and self.peek(2).kind == "UNAME":
            mod = str(self.next().value)
            self.next()
            name = self.next()
            return A.TName(name.pos, str(name.value), self.type_args(), mod)
        if self.at("UNAME") or self.at("NAME"):
            name = self.next()
            return A.TName(name.pos, str(name.value), self.type_args())
        raise self.error(f"esperava um tipo, encontrei {self.describe(self.tok)}")

    def type_args(self) -> list[A.TypeExpr]:
        args: list[A.TypeExpr] = []
        if self.glued():
            self.next()
            while not self.at_p(")"):
                args.append(self.type_())
            self.expect("PUNCT", ")")
        return args

    # ------------------------------------------------------------ corpos e blocos
    def body(self) -> A.Expr:
        """Corpo após `=`, `then`, `else`, `->`: bloco indentado ou expressão na linha."""
        if self.at("INDENT"):
            e = self.block()
        else:
            e = self.expr()
        if self.at("WHERE"):
            w = self.next()
            stmts = self.block_stmts()
            if isinstance(e, A.Block):
                e = A.Block(w.pos, stmts + e.stmts, e.result)
            else:
                e = A.Block(w.pos, stmts, e)
        return e

    def block(self) -> A.Block:
        start = self.tok.pos
        stmts = self.block_stmts()
        result: A.Expr | None = None
        if stmts and isinstance(stmts[-1], A.ExprStmt):
            result = stmts.pop().expr  # type: ignore[union-attr]
        return A.Block(start, stmts, result)

    def block_stmts(self) -> list[A.Stmt]:
        self.expect("INDENT", what="bloco indentado")
        stmts: list[A.Stmt] = []
        while not self.at("DEDENT") and not self.at("EOF"):
            if self.accept("NEWLINE"):
                continue
            try:
                stmts.append(self.stmt())
                if not self.at("NEWLINE") and not self.at("DEDENT") and not self.at("WHERE"):
                    raise self.error(f"esperava fim de linha, encontrei {self.describe(self.tok)}")
            except Abort:
                self.sync_stmt()
        self.expect("DEDENT")
        return stmts

    def stmt(self) -> A.Stmt:
        t = self.tok
        if self.accept("KW", "var"):
            pat = self.pattern()
            ann = None
            if self.accept("PUNCT", ":"):
                ann = self.type_()
            self.expect("PUNCT", "=")
            return A.Let(t.pos, pat, ann, self.body(), True)
        if self.at("NAME") and self.peek().is_("PUNCT", ":"):
            name = self.next()
            self.next()
            ann = self.type_()
            self.expect("PUNCT", "=")
            return A.Let(t.pos, A.PVar(name.pos, str(name.value)), ann, self.body())
        e = self.expr()
        if self.accept("PUNCT", "="):
            rhs = self.body()
            if isinstance(e, A.Name):
                return A.Let(t.pos, A.PVar(e.pos, e.name), None, rhs)
            if isinstance(e, A.Tuple):
                return A.Let(t.pos, self.expr_to_pat(e), None, rhs)
            if isinstance(e, A.Dot):
                return A.Assign(t.pos, e, rhs)
            if isinstance(e, A.Hole):
                return A.Let(t.pos, A.PWild(e.pos), None, rhs)
            raise self.error("lado esquerdo inválido numa ligação", t)
        return A.ExprStmt(t.pos, e)

    def expr_to_pat(self, e: A.Expr) -> A.Pat:
        if isinstance(e, A.Name):
            return A.PVar(e.pos, e.name)
        if isinstance(e, A.Hole):
            return A.PWild(e.pos)
        if isinstance(e, A.Tuple):
            return A.PTuple(e.pos, [self.expr_to_pat(x) for x in e.items])
        self.diags.error("sintaxe", e.pos, "padrão inválido")
        raise Abort()

    # ------------------------------------------------------------ expressões
    def expr(self) -> A.Expr:
        t = self.tok
        if t.kind == "KW":
            if t.value == "if":
                return self.if_()
            if t.value == "match":
                return self.match_()
            if t.value == "while":
                return self.while_()
            if t.value == "for":
                return self.for_()
            if t.value == "lazy":
                self.next()
                return A.LazyE(t.pos, self.expr())
            if t.value == "yield":
                self.next()
                if not self.gen_mode:
                    raise self.error("`yield` fora de generator ou compreensão", t)
                return A.Yield(t.pos, self.expr())
        if self.is_lambda():
            return self.lambda_()
        return self.op_chain()

    def is_lambda(self) -> bool:
        if self.at("NAME") and self.peek().is_("PUNCT", "=>"):
            return True
        if self.at_p("(") or self.at_p("#("):
            j = self.i + 1
            while self.toks[j].kind == "NAME" or self.toks[j].is_("KW", "mut") or self.toks[j].is_("KW", "own") \
                    or self.toks[j].is_("PUNCT", "_"):
                j += 1
            return self.toks[j].is_("PUNCT", ")") and self.toks[j + 1].is_("PUNCT", "=>")
        return False

    def lambda_(self) -> A.Lambda:
        t = self.tok
        params: list[A.Param] = []
        if self.at("NAME"):
            n = self.next()
            params.append(A.Param(n.pos, str(n.value)))
        else:
            self.next()
            while not self.at_p(")"):
                mode = "borrow"
                if self.accept("KW", "mut"):
                    mode = "mut"
                elif self.accept("KW", "own"):
                    mode = "own"
                if self.accept("PUNCT", "_"):
                    params.append(A.Param(self.tok.pos, "_", None, mode))
                    continue
                n = self.next()
                params.append(A.Param(n.pos, str(n.value), None, mode))
            self.next()
        self.expect("PUNCT", "=>")
        body = self.body() if self.at("INDENT") else self.expr()
        return A.Lambda(t.pos, params, body)

    def op_chain(self) -> A.Expr:
        start = self.tok
        items: list[object] = [self.operand()]
        while True:
            t = self.tok
            if t.kind == "OP" and not (t.value == "|" and False):
                self.next()
                items.append(A.OpTok(str(t.value), t.pos, False))
                items.append(self.operand())
            elif t.kind == "NAME":
                self.next()
                items.append(A.OpTok(str(t.value), t.pos, True))
                items.append(self.operand())
            else:
                break
        e: A.Expr = items[0] if len(items) == 1 else A.OpChain(start.pos, items)  # type: ignore[assignment]
        if self.at_kw("with"):
            w = self.next()
            fields: list[tuple[str, A.Expr, Pos]] = []
            while self.at("NAME") and self.peek().is_("PUNCT", ":"):
                f = self.next()
                self.next()
                fields.append((str(f.value), self.item(), f.pos))
            if not fields:
                raise self.error("`with` precisa de pelo menos um campo `nome: valor`")
            e = A.With(w.pos, e, fields)
        return e

    def operand(self) -> A.Expr:
        t = self.tok
        if t.is_("OP", "-"):
            self.next()
            inner = self.operand()
            return A.OpChain(t.pos, [A.Lit(t.pos, 0, "int"), A.OpTok("-", t.pos, False), inner])
        if t.kind == "KW" and t.value in ("if", "match", "lazy"):
            return self.expr()
        if t.is_("PUNCT", "!"):
            raise self.error("`!` só marca argumentos de parâmetros `mut` e padrões de `for`")
        return self.postfix(self.atom())

    def postfix(self, e: A.Expr) -> A.Expr:
        while True:
            if self.at_p(".") and not self.tok.spaced:
                self.next()
                n = self.tok
                if n.kind in ("NAME", "UNAME", "KW"):
                    self.next()
                    e = A.Dot(n.pos, e, str(n.value), n.pos)
                else:
                    raise self.error("esperava um nome depois de `.`")
            elif self.at_p(".?") and not self.tok.spaced:
                t = self.next()
                e = A.TryOp(t.pos, e)
            elif self.glued():
                e = self.call(e)
            else:
                return e

    def call(self, fn: A.Expr) -> A.Call:
        t = self.expect("PUNCT", "(")
        args, names = self.items(")", allow_names=True)
        return A.Call(t.pos, fn, args, names)

    def items(self, close: str, allow_names: bool = False) -> tuple[list[A.Expr], list[str | None]]:
        args: list[A.Expr] = []
        names: list[str | None] = []
        while not self.at_p(close):
            if self.at("EOF"):
                raise self.error(f"esperava {close!r}")
            name = None
            if allow_names and self.at("NAME") and self.peek().is_("PUNCT", ":"):
                name = str(self.next().value)
                self.next()
            if self.is_lambda():
                lam = self.lambda_()
                args.append(lam)
                names.append(name)
                if not self.at_p(close):
                    raise self.error("uma lambda sem parênteses só pode ser o último item da lista")
                break
            args.append(self.item())
            names.append(name)
            if self.at("OP"):
                raise self.error(
                    f"`{self.tok.value}` não é atômico: itens de listas de argumentos, tuplas e listas "
                    "precisam de parênteses em expressões com operador")
            if self.at_kw("with"):
                raise self.error("`with` dentro de uma lista precisa de parênteses")
        self.expect("PUNCT", close)
        return args, names

    def item(self) -> A.Expr:
        t = self.tok
        if self.accept("PUNCT", "!"):
            return A.Bang(t.pos, self.postfix(self.atom()))
        return self.postfix(self.atom())

    def atom(self) -> A.Expr:
        t = self.tok
        k = t.kind
        if k == "INT":
            self.next()
            return A.Lit(t.pos, t.value, "int")
        if k == "FLOAT":
            self.next()
            return A.Lit(t.pos, t.value, "float")
        if k == "STRING":
            self.next()
            return self.string(t)
        if k == "KW" and t.value in ("true", "false"):
            self.next()
            return A.Lit(t.pos, t.value == "true", "bool")
        if k in ("NAME", "UNAME"):
            self.next()
            return A.Name(t.pos, str(t.value))
        if k == "BUILTIN":
            return self.builtin()
        if k == "PUNCT":
            v = t.value
            if v == "_":
                self.next()
                return A.Hole(t.pos)
            if v == "(":
                self.next()
                if self.accept("PUNCT", ")"):
                    return A.Lit(t.pos, None, "unit")
                e = self.expr()
                self.expect("PUNCT", ")")
                return e
            if v == "#(":
                self.next()
                items, _ = self.items(")")
                return A.Tuple(t.pos, items)
            if v == "[":
                self.next()
                items, _ = self.items("]")
                return A.ListLit(t.pos, items)
            if v == "{":
                self.next()
                pairs: list[tuple[A.Expr, A.Expr]] = []
                while not self.at_p("}"):
                    k_ = self.item()
                    self.expect("PUNCT", ":")
                    v_ = self.item()
                    pairs.append((k_, v_))
                    if self.at("OP"):
                        raise self.error(f"`{self.tok.value}` não é atômico dentro de um literal de mapa")
                self.expect("PUNCT", "}")
                return A.MapLit(t.pos, pairs)
        raise self.error(f"esperava uma expressão, encontrei {self.describe(t)}")

    def builtin(self) -> A.Expr:
        t = self.next()
        name = str(t.value)
        if name == "tail":
            inner = self.operand()
            return A.BuiltinCall(t.pos, "tail", [inner])
        if name == "extern":
            self.expect("PUNCT", "(")
            strs: list[str] = []
            while self.at("STRING"):
                s = self.next().value
                assert isinstance(s, StringLit)
                strs.append("".join(p for p in s.parts if isinstance(p, str)))
            self.expect("PUNCT", "(")
            params: list[A.Param] = []
            while not self.at_p(")"):
                params.append(self.param())
            self.expect("PUNCT", ")")
            self.expect("PUNCT", "->")
            ret = self.type_atom()
            affinity = "main"
            if self.at("NAME"):
                affinity = str(self.next().value)
            self.expect("PUNCT", ")")
            return A.BuiltinCall(t.pos, "extern", [strs, params, ret, affinity])
        if not self.glued():
            return A.BuiltinCall(t.pos, name, [])
        self.next()
        args, _ = self.items(")")
        return A.BuiltinCall(t.pos, name, args)

    def string(self, t: Token) -> A.Str:
        lit = t.value
        assert isinstance(lit, StringLit)
        parts: list[str | A.InterpPart] = []
        for p in lit.parts:
            if isinstance(p, str):
                parts.append(p)
                continue
            assert isinstance(p, StrPart)
            sub = Lexer(p.src, self.file, self.diags, layout=False, line=p.pos.line, col=p.pos.col).tokenize()
            sp = Parser(sub, self.file, self.diags)
            sp.gen_mode = self.gen_mode
            if sub[0].kind == "EOF":
                self.diags.error("sintaxe", p.pos, "interpolação vazia")
                raise Abort()
            e = sp.expr()
            if not sp.at("EOF"):
                raise sp.error("sobra texto na interpolação")
            parts.append(A.InterpPart(e, p.spec, p.spec_pos or p.pos))
        return A.Str(t.pos, parts)

    # ------------------------------------------------------------ controle
    def if_(self) -> A.If:
        t = self.expect("KW", "if")
        cond = self.expr()
        if self.accept("KW", "then"):
            then = self.body()
        elif self.at("INDENT"):
            then = self.block()
        else:
            raise self.error("esperava `then` ou um bloco indentado depois da condição do `if`")
        els = None
        if self.at("NEWLINE") and self.peek().is_("KW", "else"):
            self.next()
        if self.accept("KW", "else"):
            if self.at_kw("if"):
                els = self.if_()
            else:
                els = self.body()
        return A.If(t.pos, cond, then, els)

    def match_(self) -> A.Match:
        t = self.expect("KW", "match")
        scrut = self.expr()
        self.expect("INDENT", what="bloco de casos do `match`")
        arms: list[A.Arm] = []
        while not self.at("DEDENT") and not self.at("EOF"):
            if self.accept("NEWLINE"):
                continue
            try:
                pt = self.tok
                pat = self.pattern()
                guard = None
                if self.accept("KW", "if"):
                    guard = self.expr()
                self.expect("PUNCT", "->")
                arms.append(A.Arm(pt.pos, pat, guard, self.body()))
            except Abort:
                self.sync_stmt()
        self.expect("DEDENT")
        return A.Match(t.pos, scrut, arms)

    def while_(self) -> A.While:
        t = self.expect("KW", "while")
        cond = self.expr()
        body = self.block()
        return A.While(t.pos, cond, body)

    def gen_line(self) -> A.Gen:
        t = self.tok
        pats: list[A.Pat] = []
        while not self.at_p("<-"):
            if self.at("NEWLINE") or self.at("EOF") or self.at("INDENT"):
                raise self.error("esperava `<-` na compreensão")
            pats.append(self.pattern())
        self.next()
        src = self.op_chain()
        conds: list[A.Expr] = []
        while self.accept("PUNCT", ","):
            conds.append(self.op_chain())
        return A.Gen(t.pos, pats, src, conds)

    def line_has_larrow(self) -> bool:
        j = self.i
        depth = 0
        while True:
            t = self.toks[j]
            if t.kind in ("NEWLINE", "EOF", "INDENT", "DEDENT") and depth == 0:
                return False
            if t.kind == "PUNCT" and t.value in ("(", "[", "{", "#("):
                depth += 1
            elif t.kind == "PUNCT" and t.value in (")", "]", "}"):
                depth -= 1
            elif t.is_("PUNCT", "<-") and depth == 0:
                return True
            j += 1

    def for_(self) -> A.For:
        t = self.expect("KW", "for")
        gens = [self.gen_line()]
        if self.at_kw("yield") and not self.gen_mode:
            self.next()
            return A.For(t.pos, gens, None, self.expr())
        self.expect("INDENT", what="corpo do `for`")
        while True:
            while self.accept("NEWLINE"):
                pass
            if self.line_has_larrow():
                gens.append(self.gen_line())
                continue
            break
        if self.at_kw("yield") and not self.gen_mode:
            yt = self.next()
            y = self.expr()
            while self.accept("NEWLINE"):
                pass
            if not self.at("DEDENT"):
                raise self.error("`yield` deve ser a última linha da compreensão", yt)
            self.expect("DEDENT")
            return A.For(t.pos, gens, None, y)
        stmts: list[A.Stmt] = []
        while not self.at("DEDENT") and not self.at("EOF"):
            if self.accept("NEWLINE"):
                continue
            try:
                stmts.append(self.stmt())
            except Abort:
                self.sync_stmt()
        self.expect("DEDENT")
        result = None
        if stmts and isinstance(stmts[-1], A.ExprStmt):
            result = stmts.pop().expr  # type: ignore[union-attr]
        return A.For(t.pos, gens, A.Block(t.pos, stmts, result), None)

    # ------------------------------------------------------------ padrões
    def pattern(self) -> A.Pat:
        t = self.tok
        if self.accept("PUNCT", "_"):
            return A.PWild(t.pos)
        if self.accept("PUNCT", "!"):
            n = self.expect("NAME", what="nome")
            return A.PVar(n.pos, str(n.value), bang=True)
        if self.at("NAME"):
            self.next()
            return A.PVar(t.pos, str(t.value))
        if self.at("INT") or self.at("FLOAT"):
            self.next()
            return A.PLit(t.pos, t.value, "int" if t.kind == "INT" else "float")
        if self.at("KW") and t.value in ("true", "false"):
            self.next()
            return A.PLit(t.pos, t.value == "true", "bool")
        if self.at("STRING"):
            self.next()
            s = t.value
            assert isinstance(s, StringLit)
            if any(not isinstance(p, str) for p in s.parts):
                raise self.error("padrão de string não pode ter interpolação", t)
            return A.PLit(t.pos, "".join(s.parts), "str")  # type: ignore[arg-type]
        if self.accept("PUNCT", "#("):
            items = []
            while not self.at_p(")"):
                items.append(self.pattern())
            self.expect("PUNCT", ")")
            return A.PTuple(t.pos, items)
        if self.at("UNAME"):
            self.next()
            args: list[A.Pat] = []
            names: list[str | None] = []
            if self.glued():
                self.next()
                while not self.at_p(")"):
                    nm = None
                    if self.at("NAME") and self.peek().is_("PUNCT", ":"):
                        nm = str(self.next().value)
                        self.next()
                    args.append(self.pattern())
                    names.append(nm)
                self.expect("PUNCT", ")")
            return A.PCtor(t.pos, str(t.value), args, names)
        if self.accept("PUNCT", "("):
            p = self.pattern()
            self.expect("PUNCT", ")")
            return p
        raise self.error(f"esperava um padrão, encontrei {self.describe(t)}")


def parse(src: str, file: str, module_name: str, diags: Diagnostics) -> A.Module:
    lexer = Lexer(src, file, diags)
    toks = lexer.tokenize()
    p = Parser(toks, file, diags)
    return p.parse_module(module_name)
