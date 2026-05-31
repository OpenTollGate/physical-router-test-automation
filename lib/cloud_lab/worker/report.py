"""Cloud lab worker — collect, render, publish."""

from __future__ import annotations

import json
import logging
import os
import shlex
from pathlib import Path
from typing import Any

from lib.cloud_lab.constants import CLOUD_ARCH, OPENWRT_IP, TEST_DIR
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.runner import pytest_collect_args, runner_scope
from lib.cloud_lab.worker.shell import _redact, _run, log

def collect_and_render(config: WorkerConfig, results_dir: str, started_at: str, finished_at: str) -> None:
    commit_arg = f"--sut-commit {config.sut_commit} " if config.sut_commit else ""
    pr_arg = f"--sut-pr {config.sut_pr} " if config.sut_pr else ""
    scope = runner_scope(config)
    pytest_runners = pytest_collect_args(config)
    _run(
        f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && set -a && source .env && set +a && "
        f"python3 scripts/collect-results.py --run-dir {results_dir} "
        f"{pytest_runners}"
        f"--run-id {config.run_id} "
        f"--sut-repo {config.artifact_repo} --sut-branch {shlex.quote(config.sut_branch)} "
        f"{commit_arg}{pr_arg}--sut-backend {config.backend} "
        f"--suite-commit {config.suite_ref} --client-type container "
        f"--portal {config.portal} "
        f"--router-id gcp-cloud --router-model gcp-n2-standard-2 --router-arch {CLOUD_ARCH} "
        f"--viewport desktop --test-plan cloud-api --query-router {OPENWRT_IP} --virtual-lab "
        f"--lab-type gcloud --tier api --scope {scope} --profile gcloud-api "
        f"--started-at {started_at} --finished-at {finished_at} --allow-failures",
        timeout=60,
    )
    _run(
        f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && "
        f"python3 scripts/render-report.py --run-dir {results_dir}",
        timeout=60,
    )
def create_minimal_run_json(
    config: WorkerConfig, results_dir: str, started_at: str, finished_at: str, test_exit: int
) -> None:
    results_path = Path(results_dir)
    run_json = results_path / "run.json"
    if run_json.exists():
        return

    passed = failed = skipped = error = 0
    for junit in results_path.rglob("junit.xml"):
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(junit)
            root = tree.getroot()
            passed += int(root.get("tests", 0)) - int(root.get("failures", 0)) - int(root.get("errors", 0)) - int(root.get("skipped", 0))
            failed += int(root.get("failures", 0))
            skipped += int(root.get("skipped", 0))
            error += int(root.get("errors", 0))
        except Exception:
            pass

    commit_short = (config.sut_commit or "")[:7] or "unknown"
    run_data = {
        "schema_version": 1,
        "run_id": config.run_id,
        "status": "failed" if test_exit != 0 else "passed",
        "started_at": started_at,
        "finished_at": finished_at,
        "counts": {"passed": passed, "failed": failed, "skipped": skipped, "error": error},
        "sut": {
            "repo": config.artifact_repo,
            "branch": config.sut_branch,
            "commit": config.sut_commit or "unknown",
            "commit_short": commit_short,
            "pr": config.sut_pr,
            "backend": config.backend,
            "portal": config.portal,
        },
        "lab": {"router_id": "gcp-cloud", "client_type": "container", "lab_type": "gcloud"},
        "note": "minimal run.json created after collect_and_render failure",
    }
    results_path.mkdir(parents=True, exist_ok=True)
    run_json.write_text(json.dumps(run_data, indent=2))
    log.info("Created minimal run.json: %d passed, %d failed, %d skipped", passed, failed, skipped)

    summary_path = results_path / "summary.json"
    if not summary_path.exists():
        summary_data = {"tests": [], "counts": run_data["counts"]}
        summary_path.write_text(json.dumps(summary_data, indent=2))

    report_dir = results_path / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    index_html = report_dir / "index.html"
    if not index_html.exists():
        index_html.write_text(
            f"<html><head><title>Run {config.run_id}</title></head>"
            f"<body><h1>Run {config.run_id}</h1>"
            f"<p>passed={passed} failed={failed} skipped={skipped}</p>"
            f"<p>collect_and_render failed — minimal report</p></body></html>"
        )
def publish_results(config: WorkerConfig, results_dir: str) -> str:
    run_json = Path(results_dir) / "run.json"
    if not run_json.exists():
        log.error("Cannot publish: run.json not found in %s", results_dir)
        return "https://tests.tollgate.me/"

    commit_short = config.sut_commit[:7]
    data = json.loads(run_json.read_text())
    nested = data.get("sut") or {}
    commit_short = nested.get("commit_short") or commit_short
    expected_url = f"https://tests.tollgate.me/reports/{commit_short}/{config.run_id}/report/index.html"
    log.info("Publishing from results_dir=%s → expected_url=%s", results_dir, expected_url)

    try:
        gh_token = os.environ.get("GH_TOKEN", "")
        _run(
            f"git config --global user.email 'tollgate-ci@users.noreply.github.com' && "
            f"git config --global user.name 'TollGate CI' && "
            f"git config --global --add safe.directory '*' && "
            f"cd {TEST_DIR} && GH_TOKEN={shlex.quote(gh_token)} TOLLGATE_GH_PAGES_CNAME=tests.tollgate.me "
            f"./scripts/publish-report.sh {shlex.quote(results_dir)}",
            timeout=1200,
        )
    except RuntimeError as exc:
        log.error("publish-report.sh failed: %s", _redact(str(exc))[:500])
        raise
    except Exception as exc:
        log.error("publish-report.sh unexpected error: %s", _redact(str(exc))[:500])
        raise

    return expected_url
def post_pr_comment(config: WorkerConfig, report_url: str, counts: dict[str, Any]) -> None:
    if not config.sut_pr:
        return
    body = (
        f"## Cloud lab results\n\n"
        f"**Run:** `{config.run_id}`\n\n"
        f"| Passed | Failed | Skipped |\n"
        f"|--------|--------|--------|\n"
        f"| {counts.get('passed', '?')} | {counts.get('failed', '?')} | {counts.get('skipped', '?')} |\n\n"
        f"[View full report]({report_url})\n"
    )
    repo = config.pr_repo or config.artifact_repo
    _run(
        f"gh pr comment {shlex.quote(config.sut_pr)} --repo {shlex.quote(repo)} "
        f"--body {shlex.quote(body)}",
        timeout=30,
        check=False,
    )
