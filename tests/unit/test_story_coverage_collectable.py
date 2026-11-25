"""story-coverage collectability contract (#296).

Marker text alone is not coverage: a file carrying story/tip marks but
ZERO pytest-collectable items must not count toward Covered. The #296
case in the wild: tests/api/test_gateway_token_format.py — a standalone
runner (custom TestResult class + main()) whose story("R3")/tip("TIP-02")
text claimed R3 Covered while `pytest --collect-only` yields nothing.
"""
import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "story-coverage.py"
_spec = importlib.util.spec_from_file_location("story_coverage", _SCRIPT)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

pytestmark = [pytest.mark.api]


def test_module_level_test_function_collects():
    assert sc.collects_tests(
        'pytestmark = [' 'pytest' '.mark' '.story("G2")' ']\n'
        'def test_a():\n    pass\n'
    )


def test_async_test_function_collects():
    assert sc.collects_tests("async def test_a():\n    pass\n")


def test_test_class_methods_collect():
    assert sc.collects_tests(
        "class TestThing:\n    def test_a(self):\n        pass\n"
    )


def test_standalone_runner_with_main_does_not_collect():
    # the exact #296 shape: custom TestResult harness + main()
    assert not sc.collects_tests(
        'pytestmark = [' 'pytest' '.mark' '.story("R3")' ']\n\n'
        "class TestResult:\n"
        "    def run(self):\n"
        "        return []\n\n"
        "def main():\n"
        "    r = TestResult()\n"
        "    return r.run()\n"
    )


def test_non_test_methods_in_test_prefixed_class_do_not_collect():
    # "TestResult" matches pytest's Test* prefix but has no test_* methods
    assert not sc.collects_tests("class TestResult:\n    def run(self):\n        pass\n")


def test_bare_marks_without_functions_do_not_collect():
    assert not sc.collects_tests('pytestmark = [' 'pytest' '.mark' '.story("R3")' ']\n')


def test_syntax_error_is_not_collectable():
    assert not sc.collects_tests("def broken(:\n")


def test_the_r3_standalone_runner_file_is_uncollectable():
    p = _SCRIPT.parent.parent / "tests" / "api" / "test_gateway_token_format.py"
    assert p.is_file(), "the #296 subject moved — update this rail"
    assert not sc.collects_tests(p.read_text(errors="replace"))


def test_scan_routes_the_standalone_runner_to_dead_not_marks():
    marks, dead = sc.scan_tests()
    rel = "tests/api/test_gateway_token_format.py"
    assert rel not in marks, "uncollectable file counted as proving tests"
    assert rel in dead
    assert "R3" in dead[rel]["stories"]


class TestPartialEvidence:
    """#301: STORY_PARTIAL renders a story Partial, not Covered."""

    def test_partial_declaration_is_captured(self):
        marks, _ = sc.scan_tests()
        g7 = marks["tests/unit/test_portal_failure_copy.py"]
        assert g7["partial"], "G7 rail must declare its partial evidence"
        p1 = marks["tests/unit/test_session_verify.py"]
        assert p1["partial"], "P1 mock suite must declare its partial evidence"

    def test_full_evidence_files_have_empty_partial(self):
        marks, _ = sc.scan_tests()
        # a fully-proving file (no STORY_PARTIAL) records the empty string
        assert marks["tests/api/test_sentinel_error.py"]["partial"] == ""

    def test_partial_regex_matches_the_declaration_form(self):
        text = (
            'pytestmark = [' 'pytest' '.mark' '.story("G7")' ']\n\n'
            'STORY_PARTIAL = "driver-level only"\n'
        )
        m = sc.STORY_PARTIAL_RE.search(text)
        assert m and m.group(1) == "driver-level only"

    def test_no_declaration_means_no_match(self):
        assert not sc.STORY_PARTIAL_RE.search(
            'pytestmark = [' 'pytest' '.mark' '.story("G7")' ']\n'
        )
