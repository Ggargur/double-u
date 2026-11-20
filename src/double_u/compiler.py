"""Pipeline: leitura → desaçucaramento → checagem → LLVM IR → executável."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import ast as A
from .checker import Checker, ModuleInfo, generated_prelude
from .codegen import Codegen
from .desugar import Desugar
from .diagnostics import CompileFailed, Diagnostics
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


def frontend(path: str, diags: Diagnostics) -> tuple[Checker, ModuleInfo]:
    prelude_src = (HERE / "lib" / "prelude.uu").read_text("utf-8") + "\n" + generated_prelude()
    prelude_ast = parse(prelude_src, "<prelude>", "prelude", diags)
    src = Path(path).read_text("utf-8")
    main_ast = parse(src, path, "main", diags)
    mods = [prelude_ast, main_ast]
    ds = Desugar(gather_fixity(mods), diags)
    for m in mods:
        ds.module(m)
    ck = Checker(diags)
    pre = ModuleInfo("prelude", "<prelude>", prelude_ast)
    ck.load_prelude(pre)
    ck.collect(pre)
    main = ModuleInfo("main", path, main_ast)
    ck.collect(main)
    if not diags:
        ck.check_program()
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
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"falha ao ligar o executável:\n{proc.stderr}")
