"""Diagnósticos de compilação: categoria estável, arquivo, linha e coluna."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Pos:
    file: str
    line: int
    col: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.col}"


NOPOS = Pos("<interno>", 0, 0)


@dataclass
class Diagnostic:
    category: str
    pos: Pos
    message: str
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        text = f"{self.pos}: erro[{self.category}]: {self.message}"
        for note in self.notes:
            text += f"\n    nota: {note}"
        return text


class Diagnostics:
    """Coleta erros independentes para reportá-los juntos."""

    def __init__(self) -> None:
        self.items: list[Diagnostic] = []
        self._seen: set[tuple[str, Pos, str]] = set()

    def error(self, category: str, pos: Pos, message: str, *notes: str) -> None:
        key = (category, pos, message)
        if key in self._seen:
            return
        self._seen.add(key)
        self.items.append(Diagnostic(category, pos, message, list(notes)))

    def __bool__(self) -> bool:
        return bool(self.items)

    def render(self) -> str:
        items = sorted(self.items, key=lambda d: (d.pos.file, d.pos.line, d.pos.col))
        return "\n".join(d.render() for d in items)


class CompileFailed(Exception):
    def __init__(self, diags: Diagnostics):
        super().__init__(diags.render())
        self.diags = diags


class Abort(Exception):
    """Erro irrecuperável dentro de um estágio; o diagnóstico já foi registrado."""
