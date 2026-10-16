"""Join stories.yaml with live pytest results into the behaviour report.

The differential axis of user-stories.md: every backend gets a column,
every story a row. Statuses come from stories.yaml unless a live junit
result proves otherwise for a backend that actually ran this invocation —
parity is measured, never asserted by editing the yaml.

CLI:
    python3 lib/behavior_report.py \\
        --stories tests/conformance/stories.yaml \\
        [--junit results/behavior/rust-v2.xml ...] \\
        [--out results/behavior/behavior-report.md]
"""

from __future__ import annotations

import argparse
import datetime
import glob
import os
import sys
import xml.etree.ElementTree as ET

import yaml

LIVE_MARK = "¹"
LIVE_NOTE = "¹ proven by a test that ran in this invocation"


def junit_outcomes(paths: list[str]) -> dict[str, str]:
    """test name -> passed|failed, from one or more pytest junit files."""
    outcomes: dict[str, str] = {}
    for path in paths:
        root = ET.parse(path).getroot()
        for case in root.iter("testcase"):
            name = case.get("name", "").split("[")[0]
            if case.find("failure") is not None or case.find("error") is not None:
                outcomes[name] = "failed"
            elif case.find("skipped") is not None:
                outcomes[name] = "skipped"
            else:
                outcomes[name] = "passed"
    return outcomes


def live_backend_key(path: str) -> str:
    """rust-v2.xml -> rust-v2 (junit filename carries the backend column)."""
    return os.path.basename(path).rsplit(".", 1)[0]


def render(spec: dict, junit_paths: list[str]) -> str:
    backends = [b["key"] for b in spec["backends"]]
    live: dict[str, dict[str, str]] = {}
    for path in junit_paths:
        live[live_backend_key(path)] = junit_outcomes([path])

    lines: list[str] = []
    lines.append("# TollGate behaviour report")
    lines.append("")
    lines.append(f"_generated {datetime.datetime.now().isoformat(timespec='seconds')}_")
    if live:
        lines.append(f"_backends run live: {', '.join(sorted(live))}_")
    lines.append("")
    lines.append("| Story | Title | " + " | ".join(backends) + " |")
    lines.append("|---|---|" + "---|" * len(backends))

    counts: dict[str, dict[str, int]] = {b: {} for b in backends}
    for story in spec["stories"]:
        cells = []
        for backend in backends:
            entry = story.get("backends", {}).get(backend, {})
            status = entry.get("status", "pending-audit")
            if backend in live and entry.get("test"):
                test_name = entry["test"].split("::")[-1]
                outcome = live[backend].get(test_name)
                if outcome == "passed":
                    status = "pass" + LIVE_MARK
                elif outcome == "failed":
                    status = "FAIL" + LIVE_MARK
                elif outcome == "skipped":
                    status = status  # keep the yaml status; the run skipped it
            elif backend in live and status == "pass":
                # yaml claims pass but this run produced no live proof — say so
                status = "pass (not re-run)"
            counts[backend][status.rstrip(LIVE_MARK) if status != "pass (not re-run)" else "pass"] = (
                counts[backend].get(
                    status.rstrip(LIVE_MARK) if status != "pass (not re-run)" else "pass", 0
                ) + 1
            )
            cells.append(status)
        lines.append("| " + " | ".join([story["id"], story.get("title", "")] + cells) + " |")

    lines.append("")
    lines.append("## Totals")
    lines.append("")
    lines.append("| backend | " + " | ".join(sorted({s for c in counts.values() for s in c})) + " |")
    lines.append("|---|" + "---|" * len({s for c in counts.values() for s in c}))
    for backend in backends:
        row = [backend]
        for status in sorted({s for c in counts.values() for s in c}):
            row.append(str(counts[backend].get(status, 0)))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    diverging = [
        (story["id"], backend, entry)
        for story in spec["stories"]
        for backend, entry in story.get("backends", {}).items()
        if entry.get("status") == "diverges" or story.get("diverges")
    ]
    if diverging:
        lines.append("## Documented divergences (full tradeoffs in user-stories.md)")
        lines.append("")
        seen: set[tuple] = set()
        for sid, backend, entry in diverging:
            if (sid, backend) in seen or entry.get("status") not in ("diverges",):
                continue
            seen.add((sid, backend))
            note = entry.get("note", "")
            lines.append(f"- **{sid} / {backend}**: {note}")
        lines.append("")

    if live:
        lines.append(f"_{LIVE_NOTE}_")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stories", default="tests/conformance/stories.yaml")
    ap.add_argument("--junit", nargs="*", default=[])
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    junit_paths = []
    for pattern in args.junit:
        junit_paths.extend(sorted(glob.glob(pattern)))

    with open(args.stories) as f:
        spec = yaml.safe_load(f)
    report = render(spec, junit_paths)

    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w") as f:
            f.write(report)
        print(f"wrote {args.out}", file=sys.stderr)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
