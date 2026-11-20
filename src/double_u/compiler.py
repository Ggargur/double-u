"""Pipeline: leitura → desaçucaramento → checagem → LLVM IR → executável."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import ast as A
from .checker import Checker, ModuleInfo, generated_prelude
from .codegen import Codegen
from .desugar import Desugar
from .diagnostics import CompileFailed, Diagnostics
from .ownership import Ownership
from .parser import parse

HERE = Path(__file__).parent
RUNTIME = HERE / "runtime"


def gather_fixity(mods: list[A.Module]) -> dict[str, tuple[str, int]]:
    out: dict[str, tuple[str, int]] = {}
    for m in mods:
        for d in m.decls:
            if isinstance(d, A.TraitDecl):
                for f in d.methods:
                    if f.assoc is not None:
                        out[f.name] = (f.assoc, f.prec or 0)
    return out


def import_target(d: A.Node) -> str | None:
    """Caminho literal de `nome = @import("caminho")`, se a declaração for um import."""
    if isinstance(d, A.LetDecl) and isinstance(d.expr, A.BuiltinCall) and d.expr.name == "import":
        args = d.expr.args
        if len(args) == 1 and isinstance(args[0], A.Str) and all(isinstance(p, str) for p in args[0].parts):
            return "".join(args[0].parts)  # type: ignore[arg-type]
    return None


def resolve_import(importer: str, rel: str) -> str:
    base = os.path.dirname(os.path.abspath(importer))
    p = os.path.normpath(os.path.join(base, rel))
    if not p.endswith(".uu"):
        p += ".uu"
    return p


def load_modules(path: str, diags: Diagnostics) -> list[tuple[A.Module, list[tuple[A.LetDecl, str]]]]:
    """Carrega o arquivo principal e seus imports, em ordem de dependência (dependências primeiro).

    Cada import vira `decl.import_key` (caminho absoluto do módulo importado).
    Imports cíclicos são erro.
    """
    order: list[tuple[A.Module, list[tuple[A.LetDecl, str]]]] = []
    done: set[str] = set()
    stack: list[str] = []

    def visit(file: str, display: str, name: str) -> None:
        key = os.path.abspath(file)
        stack.append(key)
        ast = parse(Path(file).read_text("utf-8"), display, name, diags)
        imports: list[tuple[A.LetDecl, str]] = []
        for d in ast.decls:
            rel = import_target(d)
            if rel is None:
                if isinstance(d, A.LetDecl) and isinstance(d.expr, A.BuiltinCall) and d.expr.name == "import":
                    diags.error("import", d.pos, "`@import` recebe um caminho literal, sem interpolação")
                continue
            assert isinstance(d, A.LetDecl)
            target = resolve_import(file, rel)
            d.import_key = target  # type: ignore[attr-defined]
            if target in stack:
                chain = " -> ".join(os.path.basename(s) for s in stack[stack.index(target):] + [target])
                diags.error("import", d.pos, f"import cíclico: {chain}")
                d.import_key = None  # type: ignore[attr-defined]
                continue
            if not os.path.isfile(target):
                diags.error("import", d.pos, f"módulo `{rel}` não encontrado ({target})")
                d.import_key = None  # type: ignore[attr-defined]
                continue
            imports.append((d, target))
            if target not in done:
                shown = os.path.relpath(target) if not os.path.isabs(display) else target
                visit(target, shown, Path(target).stem)
        stack.pop()
        done.add(key)
        ast.key = key  # type: ignore[attr-defined]
        order.append((ast, imports))

    visit(path, path, "main")
    return order


def frontend(path: str, diags: Diagnostics) -> tuple[Checker, ModuleInfo]:
    prelude_src = (HERE / "lib" / "prelude.uu").read_text("utf-8") + "\n" + generated_prelude()
    prelude_ast = parse(prelude_src, "<prelude>", "prelude", diags)
    loaded = load_modules(path, diags)
    mods = [prelude_ast] + [m for m, _ in loaded]
    ds = Desugar(gather_fixity(mods), diags)
    for m in mods:
        ds.module(m)
    ck = Checker(diags)
    pre = ModuleInfo("prelude", "<prelude>", prelude_ast)
    ck.load_prelude(pre)
    ck.collect(pre)
    infos: dict[str, ModuleInfo] = {}
    used: set[str] = {"prelude"}
    main: ModuleInfo | None = None
    for ast, _ in loaded:
        name = re.sub(r"[^A-Za-z0-9_]", "_", ast.name) or "m"
        base, i = name, 1
        while name in used:
            i += 1
            name = f"{base}{i}"
        used.add(name)
        info = ModuleInfo(name, ast.path, ast)
        infos[ast.key] = info  # type: ignore[attr-defined]
        ck.collect(info, infos)
        main = info
    assert main is not None
    if not diags:
        ck.check_program()
        if not diags:
            Ownership(ck).run()
    return ck, main


def compile_file(path: str, out: str, release: bool = False, libs: list[str] | None = None,
                 keep_ir: str | None = None) -> None:
    diags = Diagnostics()
    ck, main = frontend(path, diags)
    if diags:
        raise CompileFailed(diags)
    entry = ck.check_main(main)
    if diags or entry is None:
        raise CompileFailed(diags)
    ir_text = Codegen(ck, release=release).program(entry)
    with tempfile.TemporaryDirectory() as tmp:
        ll = os.path.join(tmp, "prog.ll")
        Path(ll).write_text(ir_text)
        if keep_ir:
            shutil.copy(ll, keep_ir)
        cmd = [os.environ.get("UU_CC", "clang"), "-O1", "-w", ll, str(RUNTIME / "runtime.c"),
               "-I", str(RUNTIME), "-o", out, "-lm", "-lpthread"]
        for lib in libs or []:
            cmd.append(lib if lib.startswith("-") or "/" in lib or lib.endswith((".a", ".so", ".o", ".c"))
                       else f"-l{lib}")
        if release:
            cmd.append("-DUU_RELEASE")
        cmd += os.environ.get("UU_CFLAGS", "").split()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"falha ao ligar o executável:\n{proc.stderr}")
