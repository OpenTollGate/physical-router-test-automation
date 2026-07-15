import argparse
import json
import re
from pathlib import Path
from datetime import datetime

PR_FILE_MAP = {
    "test_degraded_mode": 118,
    "test_mint_health": 118,
    "test_mint_502_handling": 118,
    "test_discovery_mints": 118,
    "test_hostname": 117,
    "test_mint_url_normalization": 104,
    "test_profit_share_validation": 86,
    "test_netbird_firewall": 108,
    "test_luci_admin_ui": 114,
    "test_crypto_rand_password": 111,
    "test_session_expiry_and_scan": 106,
}


def parse_pr_marker(nodeid, pr_markers):
    match = re.search(r'\[pr(\d+)\]', nodeid, re.IGNORECASE)
    if match:
        return int(match.group(1))

    file_match = re.match(r'^tests/api/(test_[a-z_]+)\.py', nodeid)
    if file_match:
        basename = file_match.group(1)
        if basename in PR_FILE_MAP:
            return PR_FILE_MAP[basename]

    for pr_num, info in pr_markers.items():
        pr_name = f'pr{pr_num}'
        if pr_name in nodeid.lower():
            return int(pr_num)

    return None


def group_tests_by_pr(summary_data, run_data):
    pr_markers = run_data.get('pr_markers', {})

    test_map = {}
    for test in summary_data.get('tests', []):
        nodeid = test.get('nodeid', '')
        outcome = test.get('outcome', 'unknown')
        if nodeid:
            test_map[nodeid] = outcome

    pr_test_map = {}

    if pr_markers:
        for pr_num in pr_markers:
            pr_num = int(pr_num)
            if pr_num not in pr_test_map:
                pr_test_map[pr_num] = []

    for test_nodeid, outcome in test_map.items():
        pr_num = parse_pr_marker(test_nodeid, pr_markers)

        if pr_num is not None:
            if pr_num not in pr_test_map:
                pr_test_map[pr_num] = []
            pr_test_map[pr_num].append({
                'nodeid': test_nodeid,
                'outcome': outcome
            })

    return {pr_num: tests for pr_num, tests in pr_test_map.items() if tests}


def format_status(outcome):
    if outcome in ('passed', '✅'):
        return '✅'
    elif outcome in ('failed', '❌'):
        return '❌'
    elif outcome in ('skipped', '⏭️'):
        return '⏭️'
    return outcome


def format_duration(ms):
    if ms is None:
        return "—"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m{secs}s"


def generate_comment(results_dir, dashboard_url):
    results_dir = Path(results_dir)

    run_json_path = results_dir / 'run.json'
    summary_json_path = results_dir / 'summary.json'

    if not run_json_path.exists():
        raise FileNotFoundError(f'run.json not found in {results_dir}')

    with open(run_json_path) as f:
        run_data = json.load(f)

    schema_version = run_data.get('schema_version', 0)
    if schema_version >= 1:
        return _generate_comment_canonical(run_data, results_dir, summary_json_path, dashboard_url)
    else:
        return _generate_comment_legacy(run_data, results_dir, dashboard_url)


def _generate_comment_canonical(run_data, results_dir, summary_json_path, dashboard_url):
    sut = run_data.get('sut', {})
    lab = run_data.get('lab', {})
    counts = run_data.get('counts', {})
    runners = run_data.get('runners', [])

    commit_short = sut.get('commit_short', 'unknown')
    branch = sut.get('branch', 'unknown')
    pr_num = sut.get('pr')
    backend = sut.get('backend', 'unknown')
    repo = sut.get('repo', 'OpenTollGate/tollgate-module-basic-go')

    router_model = lab.get('router_model', 'unknown')
    router_arch = lab.get('router_arch', 'unknown')
    client_type = lab.get('client_type', 'unknown')

    run_status = run_data.get('status', 'unknown')
    total = counts.get('total', 0)
    passed = counts.get('passed', 0)
    failed = counts.get('failed', 0)
    skipped = counts.get('skipped', 0)
    if run_status == 'passed':
        status_badge = '✅ PASSED'
    elif run_status == 'failed':
        status_badge = '❌ FAILED'
    elif run_status == 'error':
        status_badge = '⚠️ ERROR'
    else:
        status_badge = f'❓ {run_status.upper()}'

    commit_url = f'https://github.com/{repo}/commit/{commit_short}' if commit_short != 'unknown' else None

    summary_data = {}
    if summary_json_path.exists():
        with open(summary_json_path) as f:
            summary_data = json.load(f)
    else:
        pass

    lines = []
    lines.append(f'## Physical Router Test Results: {status_badge}')
    lines.append('')

    commit_link = f'[`{commit_short}`]({commit_url})' if commit_url else f'`{commit_short}`'
    pr_info = f' | **PR**: #{pr_num}' if pr_num else ''
    lines.append(f'**Commit**: {commit_link} | **Branch**: {branch} | **Backend**: {backend}{pr_info}')
    lines.append(f'**Router**: {router_model} ({router_arch}) | **Client**: {client_type}')
    lines.append('')

    if runners:
        lines.append('### Runners')
        lines.append('| Runner | Status | Passed | Failed | Skipped | Duration |')
        lines.append('|--------|--------|--------|--------|---------|----------|')
        for runner in runners:
            rname = runner.get('name', 'unknown')
            rstatus = format_status(runner.get('status', 'unknown'))
            rcounts = runner.get('counts', {})
            rp = rcounts.get('passed', 0)
            rf = rcounts.get('failed', 0)
            rs = rcounts.get('skipped', 0)
            rd = format_duration(runner.get('duration_ms'))
            lines.append(f'| {rname} | {rstatus} | {rp} | {rf} | {rs} | {rd} |')
        lines.append('')

    failed_tests = summary_data.get('failed_tests', [])
    if failed_tests:
        lines.append('### Failed Tests')
        lines.append('| Runner | Test | Message |')
        lines.append('|--------|------|---------|')
        for ft in failed_tests:
            runner_name = ft.get('runner', '—')
            test_name = ft.get('name', ft.get('nodeid', 'unknown'))
            message = ft.get('message', ft.get('failure_message', ''))
            message = message.replace('|', '\\|').replace('\n', ' ')[:200]
            lines.append(f'| {runner_name} | `{test_name}` | {message} |')
        lines.append('')
    else:
        lines.append('### Failed Tests')
        lines.append('None')
        lines.append('')

    skipped_tests = [t for t in summary_data.get('tests', []) if t.get('outcome') == 'skipped']
    if skipped_tests:
        lines.append(f'### Skipped Tests ({len(skipped_tests)})')
        lines.append('<details>')
        lines.append('<summary>Click to expand</summary>')
        lines.append('')
        lines.append('| Runner | Test | Reason |')
        lines.append('|--------|------|--------|')
        for st in skipped_tests:
            runner_name = st.get('runner', '—')
            test_name = st.get('name', st.get('nodeid', 'unknown'))
            reason = st.get('reason', st.get('skip_reason', ''))
            reason = reason.replace('|', '\\|').replace('\n', ' ')[:200]
            lines.append(f'| {runner_name} | `{test_name}` | {reason} |')
        lines.append('')
        lines.append('</details>')
        lines.append('')

    run_id = run_data.get('run_id', results_dir.name)
    report_url = dashboard_url
    lines.append(f'📊 **Full report**: [View on dashboard]({report_url}) — select run `{run_id}`')
    lines.append('')
    lines.append('---')
    lines.append('*Tests ran on physical hardware by [physical-router-test-automation](https://github.com/OpenTollGate/physical-router-test-automation).*')

    markdown = '\n'.join(lines)
    print(markdown)

    output_path = results_dir / 'pr-comment.md'
    with open(output_path, 'w') as f:
        f.write(markdown)

    return markdown


def _generate_comment_legacy(run_data, results_dir, dashboard_url):
    results_json_path = results_dir / 'report' / 'results.json'

    if not results_json_path.exists():
        raise FileNotFoundError(f'report/results.json not found in {results_dir}')

    with open(results_json_path) as f:
        results_data = json.load(f)

    pr_num = run_data.get('pr')
    commit = run_data.get('installed_version', 'unknown').split('.')[-1]
    branch = run_data.get('branch', 'unknown')
    router_model = run_data.get('router_model', 'unknown')
    router_arch = run_data.get('router_arch', 'unknown')
    router_ip = run_data.get('router_ip', 'unknown')

    summary = results_data.get('summary', {})
    passed = summary.get('passed', 0)
    failed = summary.get('failed', 0)
    skipped = summary.get('skipped', 0)
    total = summary.get('total', 0)

    pr_tests = group_tests_by_pr(results_data, run_data)

    repo = run_data.get('repo', 'OpenTollGate/tollgate-module-basic-go')
    pr_url = run_data.get('pr_url') or f'https://github.com/{repo}/pull/{pr_num}'
    commit_url = run_data.get('commit_url') or f'https://github.com/{repo}/commit/{commit}'

    lines = []
    lines.append('## 🧪 Physical Router Test Results')

    if pr_num:
        pr_title = run_data.get('pr_title', branch)
        lines.append(f'**PR**: [#{pr_num} {pr_title}]({pr_url})')
    else:
        lines.append(f'**Branch**: {branch}')

    lines.append(f'**Commit**: [`{commit}`]({commit_url})')
    lines.append(f'**Router**: {router_model} ({router_arch}) @ {router_ip}')

    timestamp = run_data.get('timestamp')
    if timestamp:
        try:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            lines.append(f'**Date**: {dt.strftime("%Y-%m-%dT%H:%M:%SZ")}')
        except Exception:
            lines.append(f'**Date**: {timestamp}')

    lines.append('')
    lines.append('### Summary')
    lines.append('| | Count |')
    lines.append('|---|---|')
    lines.append(f'| ✅ Passed | {passed} |')
    lines.append(f'| ❌ Failed | {failed} |')
    lines.append(f'| ⏭️ Skipped | {skipped} |')
    lines.append(f'| **Total** | **{total}** |')

    if pr_tests:
        lines.append('')
        lines.append('### PR-Specific Tests')

        pr_list = sorted(pr_tests.keys())
        for pr_num_key in pr_list:
            pr_tests_data = pr_tests[pr_num_key]
            pr_name = f'PR #{pr_num_key}'
            lines.append('')
            lines.append(f'#### {pr_name}')
            lines.append('')
            lines.append('| Test | Result |')
            lines.append('|---|---|')

            for test in pr_tests_data:
                nodeid = test['nodeid']
                outcome = test['outcome']
                status = format_status(outcome)

                test_name = nodeid.split('::')[-1] if '::' in nodeid else nodeid
                lines.append(f'| `{test_name}` | {status} |')
    else:
        lines.append('')
        lines.append('### Test Summary')
        lines.append('All tests were run. PR-specific grouping could not be determined.')

    lines.append('')
    lines.append(f'📊 **Full report**: [View on dashboard]({dashboard_url}) — select run `{results_dir.name}`')

    lines.append('')
    lines.append('---')
    lines.append('*Tests ran on physical hardware by [physical-router-test-automation](https://github.com/OpenTollGate/physical-router-test-automation).*')

    markdown = '\n'.join(lines)
    print(markdown)

    output_path = results_dir / 'pr-comment.md'
    with open(output_path, 'w') as f:
        f.write(markdown)

    return markdown


def main():
    parser = argparse.ArgumentParser(description='Generate PR comment from test results')
    parser.add_argument('--results-dir', required=True, help='Path to canonical run directory containing run.json')
    parser.add_argument('--dashboard-url', dest='dashboard_url', default='https://tests.tollgate.me', help='Dashboard URL for report links')
    parser.add_argument('--gh-pages-url', dest='dashboard_url', help=argparse.SUPPRESS)

    args = parser.parse_args()

    generate_comment(args.results_dir, args.dashboard_url)


if __name__ == '__main__':
    main()
