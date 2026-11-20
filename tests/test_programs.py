"""Harness: cada arquivo .uu em tests/programs é compilado e executado pelo CLI.

Diretivas no topo do programa (comentários):
  //> texto          linha esperada na saída padrão (em ordem)
  // exit: N         código de saída esperado (padrão 0)
  // error: CAT L    erro de compilação esperado: categoria CAT na linha L (pode repetir)
  // flags: ...      flags extras para o CLI (ex.: --release)
  // stdin-file: X   (reservado)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent / "programs"
PROGRAMS = sorted(p for p in ROOT.rglob("*.uu") if not p.name.startswith("_"))


def parse_expectations(src: str):
    stdout, errors, flags, exit_code = [], [], [], 0
    for line in src.splitlines():
        if line.startswith("//>"):
            stdout.append(line[4:] if line.startswith("//> ") else line[3:])
        elif m := re.match(r"// exit: (-?\d+)", line):
            exit_code = int(m.group(1))
        elif m := re.match(r"// error: (\S+) (\d+)", line):
            errors.append((m.group(1), int(m.group(2))))
        elif m := re.match(r"// flags: (.*)", line):
            flags += m.group(1).split()
    return stdout, exit_code, errors, flags


def run_cli(*args: str, cwd: Path | None = None, timeout: int = 120):
    return subprocess.run(
        [sys.executable, "-m", "double_u", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )


@pytest.mark.parametrize("program", PROGRAMS, ids=lambda p: str(p.relative_to(ROOT)))
def test_program(program: Path):
    stdout, exit_code, errors, flags = parse_expectations(program.read_text("utf-8"))
    proc = run_cli("run", *flags, str(program), cwd=program.parent)
    if errors:
        assert proc.returncode != 0, f"esperava erro de compilação\n{proc.stdout}{proc.stderr}"
        got = set()
        for m in re.finditer(r"^(\S+?):(\d+):(\d+): erro\[([\w-]+)\]", proc.stderr, re.M):
            got.add((m.group(4), int(m.group(2))))
        for err in errors:
            assert err in got, f"faltou erro {err}; stderr:\n{proc.stderr}"
        return
    assert proc.stderr == "" or proc.returncode == exit_code, proc.stderr
    assert proc.stdout.splitlines() == stdout, proc.stderr
    assert proc.returncode == exit_code, proc.stderr


def test_build_gera_executavel(tmp_path: Path):
    src = tmp_path / "ola.uu"
    src.write_text(
        'fn +main() -> Pending(int) io =\n    for _ <- print("olá")\n        yield 3\n',
        "utf-8",
    )
    exe = tmp_path / "ola"
    proc = run_cli("build", str(src), "-o", str(exe))
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.stdout == "olá\n"
    assert run.returncode == 3


def test_build_release_aceito(tmp_path: Path):
    src = tmp_path / "ola.uu"
    src.write_text('fn +main() -> Pending(int) io =\n    for _ <- print("x")\n        yield 0\n', "utf-8")
    proc = run_cli("build", "--release", str(src), "-o", str(tmp_path / "ola"))
    assert proc.returncode == 0, proc.stderr


def test_erro_nao_gera_executavel(tmp_path: Path):
    src = tmp_path / "ruim.uu"
    src.write_text("fn +main() -> Pending(int) io =\n    $\n", "utf-8")
    exe = tmp_path / "ruim"
    proc = run_cli("build", str(src), "-o", str(exe))
    assert proc.returncode != 0
    assert not exe.exists()
