"""
Compiler CLI MVP.
Usage:
  python cli.py build <file>
  python cli.py run <file> [--entry main]
  python cli.py compile <file> [-o output] [--emit-c-only]
  python cli.py test
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_ROOT = str(Path(__file__).parent.resolve())

from compiler.parser import parse
from compiler.semantic import resolve_program
from compiler.type_checker import check_program
from compiler.runtime import run_program
from compiler.lowering import lower_program
from compiler.codegen_c import emit_c_program


def _load_program(path: Path):
    source = path.read_text()
    return parse(source if source.endswith("\n") else source + "\n")


def cmd_build(path: Path) -> int:
    program = _load_program(path)
    resolve_program(program)
    check_program(program)
    print(f"Build succeeded: {path}")
    return 0


def cmd_run(path: Path, entry: str) -> int:
    program = _load_program(path)
    result = run_program(program, entry=entry)
    if result is not None:
        print(result)
    return 0


def cmd_compile(path: Path, output: str | None, emit_c_only: bool, cc: str) -> int:
    program = _load_program(path)
    resolve_program(program)
    check_program(program)
    ir_prog = lower_program(program)
    c_code = emit_c_program(ir_prog)

    out_base = Path(output) if output else path.with_suffix("")
    c_path = out_base.with_suffix(".c")
    c_path.write_text(c_code)

    if emit_c_only:
        print(f"C emitted: {c_path}")
        return 0

    bin_path = out_base
    rc = subprocess.call([cc, str(c_path), "-o", str(bin_path)])
    if rc != 0:
        raise RuntimeError(f"C compiler failed with exit code {rc}.")
    print(f"Binary emitted: {bin_path}")
    return 0


def cmd_test() -> int:
    tests = [
        "tests/test_parser.py",
        "tests/test_semantic.py",
        "tests/test_type_checker.py",
        "tests/test_runtime.py",
        "tests/test_codegen_c.py",
        "tests/test_cli.py",
    ]
    env = {**os.environ, "PYTHONPATH": _ROOT}
    for test in tests:
        rc = subprocess.call([sys.executable, test], env=env)
        if rc != 0:
            return rc
    print("All tests passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lang")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build")
    p_build.add_argument("file")

    p_run = sub.add_parser("run")
    p_run.add_argument("file")
    p_run.add_argument("--entry", default="main")

    p_compile = sub.add_parser("compile")
    p_compile.add_argument("file")
    p_compile.add_argument("-o", "--output", default=None)
    p_compile.add_argument("--emit-c-only", action="store_true")
    p_compile.add_argument("--cc", default="gcc")

    sub.add_parser("test")

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            return cmd_build(Path(args.file))
        if args.command == "run":
            return cmd_run(Path(args.file), args.entry)
        if args.command == "compile":
            return cmd_compile(
                Path(args.file),
                output=args.output,
                emit_c_only=args.emit_c_only,
                cc=args.cc,
            )
        if args.command == "test":
            return cmd_test()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
