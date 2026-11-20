"""CLI: `uu build arquivo.uu -o saida` e `uu run arquivo.uu`."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

from .compiler import compile_file
from .diagnostics import CompileFailed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="uu", description="Compilador da double-u")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "run"):
        p = sub.add_parser(name)
        p.add_argument("file")
        p.add_argument("--release", action="store_true", help="build de release (remove @debug)")
        p.add_argument("-l", "--lib", action="append", default=[], help="biblioteca C para ligar")
        p.add_argument("--emit-ir", help="salva o LLVM IR gerado neste caminho")
        if name == "build":
            p.add_argument("-o", "--output")
        else:
            p.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        out = a.output or os.path.splitext(a.file)[0]
        return build(a.file, out, a.release, a.lib, a.emit_ir)
    with tempfile.TemporaryDirectory() as tmp:
        exe = os.path.join(tmp, "prog")
        code = build(a.file, exe, a.release, a.lib, a.emit_ir)
        if code != 0:
            return code
        sys.stdout.flush()
        return subprocess.run([exe, *a.args]).returncode


def build(file: str, out: str, release: bool, libs: list[str], emit_ir: str | None) -> int:
    try:
        compile_file(file, out, release=release, libs=libs, keep_ir=emit_ir)
    except CompileFailed as e:
        print(e.diags.render(), file=sys.stderr)
        n = len(e.diags.items)
        print(f"{n} erro(s) de compilação", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"uu: {e}", file=sys.stderr)
        return 1
    return 0
