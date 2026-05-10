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

def group_tests_by_pr(results_json, run_json):
    pr_markers = run_json.get('pr_markers', {})

    test_map = {}
    for test in results_json.get('tests', []):
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
    if outcome == 'passed':
        return '✅ PASS'
    elif outcome == 'failed':
        return '❌ FAIL'
    elif outcome == 'skipped':
        return '⏭️ SKIP'
    return outcome

def generate_comment(results_dir, gh_pages_url):
    results_dir = Path(results_dir)

    run_json_path = results_dir / 'run.json'
    results_json_path = results_dir / 'report' / 'results.json'

    if not run_json_path.exists():
        raise FileNotFoundError(f'run.json not found in {results_dir}')

    if not results_json_path.exists():
        raise FileNotFoundError(f'report/results.json not found in {results_dir}')

    with open(run_json_path) as f:
        run_data = json.load(f)

    with open(results_json_path) as f:
        results_data = json.load(f)

    pr_num = run_data.get('pr')
    commit = run_data.get('installed_version', 'unknown').split('.')[-1]
    branch = run_data.get('branch', 'unknown')
    router_model = run_data.get('router_model', 'unknown')
    router_arch = run_data.get('router_arch', 'unknown')
    router_ip = run_data.get('router_ip', 'unknown')
    installed_version = run_data.get('installed_version', 'unknown')

    summary = results_data.get('summary', {})
    passed = summary.get('passed', 0)
    failed = summary.get('failed', 0)
    skipped = summary.get('skipped', 0)
    total = summary.get('total', 0)

    router_state = run_data.get('router_state', 'unknown')
    mint_status = run_data.get('mint_status', {})

    pr_tests = group_tests_by_pr(results_data, run_data)

    repo = run_data.get('repo', 'OpenTollGate/tollgate-module-basic-go')
    pr_url = run_data.get('pr_url') or f'https://github.com/{repo}/pull/{pr_num}'
    commit_url = run_data.get('commit_url') or f'https://github.com/{repo}/commit/{commit}'
    compare_url = run_data.get('compare_url')
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
        except:
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
        for pr_num in pr_list:
            pr_tests_data = pr_tests[pr_num]
            pr_name = f'PR #{pr_num}'
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
    lines.append('### Router State')
    lines.append(f'- **Installed version**: {installed_version}')
    build_time_val = run_data.get('build_time', 'unknown')
    lines.append(f'- **Build time**: {build_time_val}')

    if router_state:
        lines.append(f'- **Router state**: {router_state}')

    if mint_status:
        mint_list = '\n'.join([f'- {k}: {v}' for k, v in mint_status.items()])
        lines.append(f'- **Mint status**:')
        lines.append(mint_list)

    lines.append('')
    lines.append(f'📊 **Full report**: [View on gh-pages]({gh_pages_url}/reports/{results_dir.name}/)')

    lines.append('')
    lines.append('---')
    lines.append('*Tests ran on physical hardware by [physical-router-test-automation](https://github.com/OpenTollGate/physical-router-test-automation).*')

    markdown = '\n'.join(lines)

    print(markdown)

    output_path = results_dir / 'pr-comment.md'
    with open(output_path, 'w') as f:
        f.write(markdown)

def main():
    parser = argparse.ArgumentParser(description='Generate PR comment from test results')
    parser.add_argument('--results-dir', required=True, help='Path to results directory containing run.json and report/results.json')
    parser.add_argument('--gh-pages-url', default='https://OpenTollGate.github.io/physical-router-test-automation', help='Base URL for gh-pages')

    args = parser.parse_args()

    generate_comment(args.results_dir, args.gh_pages_url)

if __name__ == '__main__':
    main()
