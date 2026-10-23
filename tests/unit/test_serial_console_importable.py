"""Guard: every importable module in lib/ and scripts/ must COMPILE.

Regression guard for a real defect found 2026-10-21: `lib/serial_console.py`
carried a misplaced `from __future__ import annotations` (it sat *after* a
try/except delegation block), which is a hard SyntaxError:

    SyntaxError: from __future__ imports must occur at the beginning of the file

The module therefore could not be imported at all, and anything importing it
died at collection time — the whole MPTCP hardware lane reported "no tests ran"
instead of running. It survived a refactor and a scenario migration unnoticed.

Why this test uses `compile()` and not `ast.parse()`: `ast.parse()` ACCEPTS the
misplaced future import, so a parse-based guard would have passed on the broken
file. Only `compile()` enforces the placement rule. That difference is the whole
reason this defect lived as long as it did.
"""

from __future__ import annotations

import compileall
import io
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Files that legitimately require a NEWER interpreter than the one running this
# test. The repo's CI pins python-version "3.12" (all four workflows), and
# PEP 701 (f-strings may contain backslashes) landed in 3.12 — so a 3.11 local
# run sees a SyntaxError in a file CI compiles happily. These are NOT defects;
# they are version floors. Keep this list explicit and reasoned: a silent skip
# list is how a real SyntaxError hides.
NEEDS_PY312 = {
    # f-string expression part contains a backslash (PEP 701, 3.12+).
    "lib/cloud_lab/worker/conwrt_deploy.py",
}


def _is_known_version_floor(rel: str) -> bool:
    return rel in NEEDS_PY312 and sys.version_info < (3, 12)


def _python_files() -> list[Path]:
    files: list[Path] = []
    for sub in ("lib", "scripts"):
        d = PROJECT_ROOT / sub
        if d.is_dir():
            files.extend(sorted(p for p in d.rglob("*.py") if "__pycache__" not in p.parts))
    return files


def test_python_file_set_is_not_empty() -> None:
    """Guard the guard: if the globs break, the real check would vacuously pass."""
    files = _python_files()
    assert len(files) > 10, f"expected many python files, found {len(files)}"


def test_serial_console_is_importable() -> None:
    """The specific module that was broken, asserted directly and by name."""
    target = PROJECT_ROOT / "lib" / "serial_console.py"
    assert target.is_file(), f"missing {target}"
    src = target.read_text()
    # compile(), NOT ast.parse() — see module docstring.
    compile(src, str(target), "exec")
    # A misplaced future import is the exact failure mode; assert it is early.
    lines = [ln for ln in src.splitlines() if ln.strip()]
    first_code = next(
        ln for ln in lines if not ln.lstrip().startswith(("#", '"""', "'''"))
    )
    assert not first_code.startswith("from __future__") or lines.index(first_code) < 12


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.name))
def test_module_compiles(path: Path) -> None:
    """Every module must compile. compile() is the only check that catches a
    misplaced `from __future__`."""
    rel = str(path.relative_to(PROJECT_ROOT))
    if _is_known_version_floor(rel):
        pytest.skip(f"{rel} requires Python >= 3.12 (CI pins 3.12); running {sys.version.split()[0]}")
    src = path.read_text()
    try:
        compile(src, str(path), "exec")
    except SyntaxError as exc:  # pragma: no cover - only on regression
        pytest.fail(
            f"{path.relative_to(PROJECT_ROOT)} does not compile: "
            f"{exc.msg} (line {exc.lineno})"
        )


def test_compileall_agrees() -> None:
    """Cross-check with the stdlib tool a human would reach for."""
    buf = io.StringIO()
    ok = compileall.compile_dir(
        str(PROJECT_ROOT / "lib"),
        quiet=2,
        force=True,
        # Exclude version-floor files so this stays a real signal on 3.11.
        rx=re.compile(r".*(" + "|".join(re.escape(Path(p).name) for p in NEEDS_PY312) + r")$")
        if NEEDS_PY312 and sys.version_info < (3, 12)
        else None,
    )
    assert ok, f"compileall reported failures in lib/\n{buf.getvalue()}"
