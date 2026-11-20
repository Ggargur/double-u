"""Lexer com layout: transforma indentação em INDENT/DEDENT/NEWLINE."""

from __future__ import annotations

from dataclasses import dataclass, field

from .diagnostics import Diagnostics, Pos

KEYWORDS = {
    "fn", "type", "trait", "impl", "if", "then", "else", "match", "while", "for",
    "yield", "where", "var", "mut", "own", "io", "given", "lazy", "with", "true",
    "false", "infixl", "infixr", "infix",
}

OP_CHARS = set("+-*/%<>=!&|^")
OPEN = {"(": ")", "[": "]", "{": "}", "#(": ")"}


@dataclass
class Token:
    kind: str  # NAME UNAME INT FLOAT STRING KW OP PUNCT BUILTIN NEWLINE INDENT DEDENT WHERE EOF
    value: object
    pos: Pos
    spaced: bool = True  # havia espaço (ou início de linha) antes do token
    end_col: int = 0

    def is_(self, kind: str, value: object = None) -> bool:
        return self.kind == kind and (value is None or self.value == value)

    def __repr__(self) -> str:
        return f"{self.kind}:{self.value!r}@{self.pos.line}:{self.pos.col}"


@dataclass
class StrPart:
    """Parte interpolada de uma string: código-fonte da expressão e spec de formato."""

    src: str
    spec: str | None
    pos: Pos
    spec_pos: Pos | None = None


@dataclass
class StringLit:
    parts: list[str | StrPart] = field(default_factory=list)


class Lexer:
    def __init__(self, src: str, file: str, diags: Diagnostics, layout: bool = True,
                 line: int = 1, col: int = 1):
        self.src = src
        self.file = file
        self.diags = diags
        self.layout = layout
        self.i = 0
        self.line = line
        self.col = col
        self.tokens: list[Token] = []

    def pos(self) -> Pos:
        return Pos(self.file, self.line, self.col)

    def peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.src[j] if j < len(self.src) else ""

    def advance(self, n: int = 1) -> str:
        s = self.src[self.i:self.i + n]
        for ch in s:
            if ch == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
        self.i += n
        return s

    # ------------------------------------------------------------------
    def tokenize(self) -> list[Token]:
        raw = self._raw_tokens()
        if not self.layout:
            raw.append(Token("EOF", None, self.pos()))
            return raw
        return self._layout(raw)

    def _raw_tokens(self) -> list[Token]:
        toks: list[Token] = []
        spaced = True
        while self.i < len(self.src):
            c = self.peek()
            if c == "\n":
                toks.append(Token("NL", None, self.pos()))
                self.advance()
                spaced = True
                continue
            if c in " \r":
                self.advance()
                spaced = True
                continue
            if c == "\t":
                self.diags.error("indentacao", self.pos(), "tabulação não é permitida; use espaços")
                self.advance()
                spaced = True
                continue
            if c == "/" and self.peek(1) == "/":
                while self.i < len(self.src) and self.peek() != "\n":
                    self.advance()
                continue
            start = self.pos()
            tok = self._one(toks, spaced)
            if tok is not None:
                tok.pos = start
                tok.spaced = spaced
                tok.end_col = self.col
                toks.append(tok)
                spaced = False
            else:
                spaced = True
        return toks

    def _one(self, toks: list[Token], spaced: bool) -> Token | None:
        c = self.peek()
        if c.isdigit() or (c == "-" and self.peek(1).isdigit() and self._neg_ok(toks, spaced)):
            return self._number()
        if c.isalpha() or c == "_":
            j = self.i
            while j < len(self.src) and (self.src[j].isalnum() or self.src[j] == "_"):
                j += 1
            word = self.advance(j - self.i)
            if word == "_":
                return Token("PUNCT", "_", self.pos())
            if word in KEYWORDS:
                return Token("KW", word, self.pos())
            return Token("UNAME" if word[0].isupper() else "NAME", word, self.pos())
        if c == "@":
            self.advance()
            j = self.i
            while j < len(self.src) and (self.src[j].isalnum() or self.src[j] == "_"):
                j += 1
            name = self.advance(j - self.i)
            if not name:
                self.diags.error("lexico", self.pos(), "'@' precisa de um nome de builtin")
                return None
            return Token("BUILTIN", name, self.pos())
        if c == '"':
            return self._string()
        if c == "#" and self.peek(1) == "(":
            self.advance(2)
            return Token("PUNCT", "#(", self.pos())
        if c in "()[]{},:":
            self.advance()
            return Token("PUNCT", c, self.pos())
        if c == ".":
            self.advance()
            if self.peek() == "?":
                self.advance()
                return Token("PUNCT", ".?", self.pos())
            return Token("PUNCT", ".", self.pos())
        if c in OP_CHARS:
            j = self.i
            while j < len(self.src) and self.src[j] in OP_CHARS:
                if self.src[j] == "/" and j + 1 < len(self.src) and self.src[j + 1] == "/":
                    break
                j += 1
            op = self.advance(j - self.i)
            if op == "=":
                return Token("PUNCT", "=", self.pos())
            if op in ("->", "=>", "<-"):
                return Token("PUNCT", op, self.pos())
            if op.startswith("!") and op != "!=":
                # `!x`: marca de empréstimo mutável; o resto (se houver) é outro operador
                self.i -= len(op) - 1
                self.col -= len(op) - 1
                return Token("PUNCT", "!", self.pos())
            return Token("OP", op, self.pos())
        self.diags.error("lexico", self.pos(), f"caractere inválido {c!r}")
        self.advance()
        return None

    def _neg_ok(self, toks: list[Token], spaced: bool) -> bool:
        if not toks:
            return True
        prev = toks[-1]
        if prev.kind == "NL":
            return True
        if prev.kind == "PUNCT" and prev.value in ("(", "[", "#(", "{", ",", ":", "=", "->", "=>", "<-"):
            return True
        if prev.kind in ("OP", "KW"):
            return True
        return spaced and not (self.peek(1) == " ")

    def _number(self) -> Token:
        j = self.i
        if self.src[j] == "-":
            j += 1
        while j < len(self.src) and (self.src[j].isdigit() or self.src[j] == "_"):
            j += 1
        is_float = False
        if j + 1 < len(self.src) and self.src[j] == "." and self.src[j + 1].isdigit():
            is_float = True
            j += 1
            while j < len(self.src) and self.src[j].isdigit():
                j += 1
        if j < len(self.src) and self.src[j] in "eE" and (
            self.src[j + 1:j + 2].isdigit() or self.src[j + 1:j + 3][:1] in "+-" and self.src[j + 2:j + 3].isdigit()
        ):
            is_float = True
            j += 2
            while j < len(self.src) and self.src[j].isdigit():
                j += 1
        text = self.advance(j - self.i).replace("_", "")
        if is_float:
            return Token("FLOAT", float(text), self.pos())
        return Token("INT", int(text), self.pos())

    def _string(self) -> Token:
        start = self.pos()
        self.advance()  # "
        lit = StringLit()
        buf: list[str] = []
        escapes = {"n": "\n", "t": "\t", "\\": "\\", '"': '"', "r": "\r", "0": "\0"}
        while True:
            c = self.peek()
            if c == "" or c == "\n":
                self.diags.error("lexico", start, "string não terminada")
                break
            if c == '"':
                self.advance()
                break
            if c == "\\":
                e = self.peek(1)
                if e in escapes:
                    buf.append(escapes[e])
                    self.advance(2)
                else:
                    self.diags.error("lexico", self.pos(), f"escape inválido '\\{e}'")
                    self.advance(2)
                continue
            if c == "{":
                if self.peek(1) == "{":
                    buf.append("{")
                    self.advance(2)
                    continue
                if buf:
                    lit.parts.append("".join(buf))
                    buf = []
                lit.parts.append(self._interp())
                continue
            if c == "}":
                if self.peek(1) == "}":
                    self.advance(2)
                else:
                    self.advance()
                buf.append("}")
                continue
            buf.append(self.advance())
        if buf or not lit.parts:
            lit.parts.append("".join(buf))
        return Token("STRING", lit, start)

    def _interp(self) -> StrPart:
        self.advance()  # {
        pos = self.pos()
        depth = 0
        chars: list[str] = []
        spec: list[str] | None = None
        spec_pos = None
        while True:
            c = self.peek()
            if c == "" or c == "\n":
                self.diags.error("lexico", pos, "interpolação não terminada")
                break
            if spec is None:
                if c == '"':
                    # string aninhada dentro da interpolação
                    j = self.i + 1
                    while j < len(self.src) and self.src[j] not in '"\n':
                        j += 2 if self.src[j] == "\\" else 1
                    chars.append(self.advance(j + 1 - self.i))
                    continue
                if c in "([{":
                    depth += 1
                elif c in ")]":
                    depth -= 1
                elif c == "}":
                    if depth == 0:
                        self.advance()
                        break
                    depth -= 1
                elif c == ":" and depth == 0:
                    self.advance()
                    spec = []
                    spec_pos = self.pos()
                    continue
                chars.append(self.advance())
            else:
                if c == "}":
                    self.advance()
                    break
                spec.append(self.advance())
        return StrPart("".join(chars), "".join(spec) if spec is not None else None, pos, spec_pos)

    # ------------------------------------------------------------------
    def _layout(self, raw: list[Token]) -> list[Token]:
        out: list[Token] = []
        stack = [1]
        depth = 0
        at_line_start = True
        for tok in raw:
            if tok.kind == "NL":
                at_line_start = True
                continue
            if at_line_start and depth == 0:
                at_line_start = False
                col = tok.pos.col
                if tok.is_("KW", "where"):
                    while col < stack[-1]:
                        stack.pop()
                        out.append(Token("DEDENT", None, tok.pos))
                    tok = Token("WHERE", "where", tok.pos, tok.spaced, tok.end_col)
                    out.append(tok)
                    continue
                if out:
                    if col > stack[-1]:
                        stack.append(col)
                        out.append(Token("INDENT", None, tok.pos))
                    else:
                        while col < stack[-1]:
                            stack.pop()
                            out.append(Token("DEDENT", None, tok.pos))
                        if col != stack[-1]:
                            self.diags.error("indentacao", tok.pos,
                                             "indentação inconsistente com os blocos abertos")
                            stack.append(col)
                            out.append(Token("INDENT", None, tok.pos))
                        out.append(Token("NEWLINE", None, tok.pos))
                elif col != 1:
                    self.diags.error("indentacao", tok.pos, "a primeira linha não pode estar indentada")
                    stack = [col]
            elif at_line_start:
                at_line_start = False
            if tok.kind == "PUNCT" and tok.value in OPEN:
                depth += 1
            elif tok.kind == "PUNCT" and tok.value in (")", "]", "}"):
                depth = max(0, depth - 1)
            out.append(tok)
        end = Pos(self.file, self.line, self.col)
        out.append(Token("NEWLINE", None, end))
        while len(stack) > 1:
            stack.pop()
            out.append(Token("DEDENT", None, end))
        out.append(Token("EOF", None, end))
        return out


def tokenize(src: str, file: str, diags: Diagnostics) -> list[Token]:
    return Lexer(src, file, diags).tokenize()
