#!/usr/bin/env python3
"""Prove the paid-exit admission chain against the real exit daemon.

See docs/fips-exit-admission-lane.md. This lane runs one isolated root network
namespace (private mount namespace, tmpfs on /run), builds a bridge `fips0`
carrying the exit's address `fd0e::1`, one client namespace per peer, and an
`inet` namespace that answers UDP :4444 with as many bytes as it was asked
for. The real `fips-exitd` is started on an explicit clients file; every
admission decision below is made by that daemon on real packets, and every
clients file it is given is produced by the `admissionctl` shim, which drives
the module's exported `fips_exit` API and nothing else.

Assertions, each with the OBSERVED value recorded rather than the expectation:

  a. UNPAID        a peer absent from the clients file gets no answer
  b. GRANT         a shim grant, written and reloaded, answers; usage is billed
  c. REVOKE        a shim revoke decision blocks a path that just worked
  d. UNMANAGED     reconcile keeps an operator line and reports it as needing a grant
  e. RESTART       a valid grant survives, an expired one stays blocked, and no
                   peer gains access merely from the restart

Nothing here is a payment: the purchase that produces a GrantRequest is out of
scope, and the module's layer is not modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

# Reuse the canonical netns command runner (run/ns/cleanup), not its radio work.
Runner = runpy.run_path(str(Path(__file__).with_name('hwsim-netns-poc.py')))['Runner']

EXIT_ACCOUNTING = Path('/home/c08r4d0r/worktrees/fips-exit-accounting')
MODULE_REPO = Path('/home/c08r4d0r/worktrees/tollgate-fips-exit-consumer')
GO = Path('/home/c08r4d0r/.cache/tollgate-tools/go/bin/go')

E = 'fd0e::1'            # the exit's fips address, on the bridge fips0
WAN4_EXIT = '8.8.8.1'    # the exit's uplink address
WAN4_INET = '8.8.8.2'    # the responder behind the uplink
TUN = 'fipsexit0'

# The peers. Keys are synthetic x-only public keys: fips_exit deliberately does
# not check curve membership (the FIPS handshake does), so sha256 of a label is
# a valid identity for the address derivation under test.
PEERS = ('c1', 'c2', 'c3', 'c4')
NAMESPACES = PEERS + ('inet',)


def key_for(label: str) -> str:
    return hashlib.sha256(('prta-admission-e2e/' + label).encode()).hexdigest()


class Lane(Runner):
    """The shared Runner, plus this lane's daemon and its own teardown."""

    def __init__(self, verbose: bool = False) -> None:
        super().__init__(verbose=verbose)
        self.daemon: subprocess.Popen | None = None

    def cleanup(self) -> None:
        if self.daemon is not None and self.daemon.poll() is None:
            self.daemon.terminate()
            try:
                self.daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.daemon.kill()
                self.daemon.wait(timeout=5)
        self.daemon = None
        for name in reversed(NAMESPACES):
            self.run(['ip', 'netns', 'delete', name], check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Recording and reading

def record(title: str, value) -> None:
    """One OBSERVED fact, as a line a log can be diffed on."""
    print(f'OBSERVED {title}: {value}', flush=True)


def say(command: list[str]) -> None:
    print('$ ' + ' '.join(command), flush=True)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_usage(path: Path) -> list[dict]:
    """The daemon's per-client report. A file that does not parse is a failure."""
    text = path.read_text()
    data = json.loads(text)
    if not isinstance(data, list):
        raise AssertionError(f'the usage report is not a JSON array: {text!r}')
    for row in data:
        missing = [k for k in ('client', 'from_client_bytes', 'to_client_bytes', 'packets')
                   if k not in row]
        if missing:
            raise AssertionError(f'a usage row has no {", ".join(missing)}: {row!r}')
    return data


def wait_report(path: Path, timeout: float = 10.0) -> list[dict]:
    """Wait for the daemon's first report: it writes on its own tick, not at start."""
    deadline = time.time() + timeout
    while True:
        if path.exists():
            return read_usage(path)
        if time.time() > deadline:
            raise AssertionError(f'{path} was never written')
        time.sleep(0.05)


def usage_row(report: Path, client: str, *, want: bool, timeout: float = 6.0) -> dict | None:
    """Wait until the report holds (want) or lacks (!want) a row for client."""
    deadline = time.time() + timeout
    while True:
        rows = read_usage(report)
        row = next((r for r in rows if r['client'] == client), None)
        if (row is not None) == want or time.time() > deadline:
            return row
        time.sleep(0.1)


def wait_link(r: Runner, name: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if r.run(['ip', 'link', 'show', name], check=False).returncode == 0:
            return
        time.sleep(0.2)
    raise AssertionError(f'{name} never appeared')


def count_log(path: Path, needle: str) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text().splitlines() if needle in line)


def wait_log(path: Path, needle: str, *, above: int = 0, timeout: float = 10.0) -> str:
    """Wait for a further occurrence of one line in the daemon's log."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = [line for line in path.read_text().splitlines() if needle in line] \
            if path.exists() else []
        if len(lines) > above:
            return lines[-1]
        time.sleep(0.05)
    raise AssertionError(f'{needle!r} never appeared again in {path}:\n'
                         f'{path.read_text() if path.exists() else "(no log)"}')


# ---------------------------------------------------------------------------
# Topology: the same commands the exit's own test/netns.sh uses.

NO_OFFLOAD = '''
import array, fcntl, socket, struct, sys
s = socket.socket()
for dev in sys.argv[1:]:
    for command in (0x17, 0x19, 0x1f):  # ETHTOOL_STXCSUM, _SSG, _STSO
        value = array.array("B", struct.pack("II", command, 0))
        try: fcntl.ioctl(s, 0x8946, struct.pack("16sP", dev.encode(), value.buffer_info()[0]))
        except OSError: pass
'''

# One client's request, every attempt reported: "attempts=a,b,c result=c".
ASK = '''
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(float(sys.argv[2]))
attempts = []
for _ in range(int(sys.argv[3])):
    s.sendto(sys.argv[4].encode(), (sys.argv[1], 4444))
    try:
        attempts.append(str(len(s.recv(4096))))
        break
    except socket.timeout:
        attempts.append("timeout")
print("attempts=" + ",".join(attempts) + " result=" + attempts[-1])
'''

RESPONDER = '''
import select, socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.IPPROTO_IP, 10, 2)  # IP_MTU_DISCOVER = IP_PMTUDISC_DO
s.bind(("0.0.0.0", 4444))
log = open(sys.argv[1], "w")
while True:
    for _ in select.select([s], [], [])[0]:
        data, peer = s.recvfrom(4096)
        log.write(peer[0] + " " + data.decode(errors="replace") + "\\n"); log.flush()
        s.sendto(b"x" * int(data), peer)
'''

METER = '''
table inet admission_lane {
  chain nat {
    type nat hook postrouting priority srcnat; policy accept;
    iifname "%s" oifname "wan" masquerade
  }
}
''' % TUN


def build_topology(r: Runner, peers: dict, lane: Path) -> None:
    run, ns = r.run, r.ns

    run(['ip', 'link', 'set', 'lo', 'up'])
    run(['ip', 'link', 'add', 'fips0', 'type', 'bridge'])
    run(['ip', 'link', 'set', 'fips0', 'mtu', '1280', 'up'])
    run(['ip', '-6', 'addr', 'add', f'{E}/128', 'dev', 'fips0', 'nodad'])
    run(['ip', '-6', 'route', 'add', 'fd00::/8', 'dev', 'fips0'])
    # Forwarding for the tunnel, and no filter: this lane isolates the daemon's
    # admission decision from any firewall policy.
    run(['sysctl', '-qw', 'net.ipv4.ip_forward=1'])

    run(['ip', 'link', 'add', 'wan', 'type', 'veth', 'peer', 'name', 'eth0'])
    run(['ip', 'link', 'set', 'wan', 'up'])
    run(['ip', 'addr', 'add', f'{WAN4_EXIT}/24', 'dev', 'wan'])
    run(['ip', 'netns', 'add', 'inet'])
    run(['ip', 'link', 'set', 'eth0', 'netns', 'inet'])
    ns('inet', ['ip', 'link', 'set', 'lo', 'up'])
    ns('inet', ['ip', 'link', 'set', 'eth0', 'up'])
    ns('inet', ['ip', 'addr', 'add', f'{WAN4_INET}/24', 'dev', 'eth0'])

    # The exit's own masquerade: a reply can only come back if the daemon
    # really forwarded the request out.
    print('$ nft -f - <<< ' + repr(METER), flush=True)
    subprocess.run(['nft', '-f', '-'], input=METER, text=True, check=True)

    for name in PEERS:
        peer = peers[name]
        # Created here, not by the launcher: the peer's namespace name is only
        # needed once its address is derived.
        run(['ip', 'netns', 'add', name])
        ns(name, ['ip', 'link', 'set', 'lo', 'up'])
        run(['ip', 'link', 'add', f'v{name}', 'type', 'veth', 'peer', 'name', 'fips0',
             'netns', name])
        run(['ip', 'link', 'set', f'v{name}', 'mtu', '1280', 'master', 'fips0', 'up'])
        ns(name, ['ip', 'link', 'set', 'fips0', 'mtu', '1280', 'up'])
        ns(name, ['ip', '-6', 'addr', 'add', f'{peer["outer"]}/128', 'dev', 'fips0', 'nodad'])
        ns(name, ['ip', '-6', 'route', 'add', 'fd00::/8', 'dev', 'fips0'])
        ns(name, ['ip', 'link', 'add', 'tun0', 'type', 'ip6tnl', 'mode', 'any',
                  'local', peer['outer'], 'remote', E, 'encaplimit', 'none'])
        ns(name, ['ip', 'link', 'set', 'tun0', 'mtu', '1280', 'up'])
        ns(name, ['ip', 'addr', 'add', f'{peer["inner"]}/32', 'dev', 'tun0'])
        ns(name, ['ip', 'route', 'add', 'default', 'dev', 'tun0'])
        ns(name, ['ip', '-6', 'route', 'add', 'default', 'dev', 'tun0', 'src', peer['outer']])
        ns(name, ['python3', str(lane / 'no_offload.py'), 'fips0', 'tun0'])


def route_replies(r: Runner, peers: dict) -> None:
    """Replies addressed to a client's inner address must enter the daemon's TUN."""
    for name in PEERS:
        r.run(['ip', 'route', 'replace', f'{peers[name]["inner"]}/32', 'dev', TUN])


# ---------------------------------------------------------------------------
# The shim, and the daemon it feeds

def shim(binary: Path, lane: Path, *args: str) -> str:
    """Run admissionctl with the lane's state directory, echoing it verbatim."""
    env = dict(os.environ, ADMISSIONCTL_DIR=str(lane / 'adm'))
    say([str(binary), *args])
    done = subprocess.run([str(binary), *args], env=env, capture_output=True, text=True)
    print(done.stdout, end='', flush=True)
    if done.stderr:
        print(done.stderr, end='', file=sys.stderr, flush=True)
    if done.returncode != 0:
        raise AssertionError(f'admissionctl {args[0]} failed rc={done.returncode}: {done.stderr}')
    return done.stdout


def field(output: str, name: str) -> str:
    for line in output.splitlines():
        if line.startswith(name + ' '):
            return line.split(None, 1)[1]
    raise AssertionError(f'no {name!r} line in:\n{output}')


def start_daemon(r: Runner, binary: Path, peers: dict, clients: Path, usage: Path,
                 log: Path) -> None:
    env = dict(os.environ, USAGE_REPORT=str(usage), USAGE_REPORT_INTERVAL_MS='200',
               DNS_UPSTREAMS='off')
    say(['USAGE_REPORT=' + str(usage), 'USAGE_REPORT_INTERVAL_MS=200', 'DNS_UPSTREAMS=off',
         str(binary), '--clients', str(clients), '--mesh-if', 'fips0', '--tun', TUN])
    with log.open('a') as err:
        r.daemon = subprocess.Popen(
            [str(binary), '--clients', str(clients), '--mesh-if', 'fips0', '--tun', TUN],
            env=env, stdout=subprocess.DEVNULL, stderr=err, text=True)
    wait_link(r, TUN)
    route_replies(r, peers)
    record('daemon pid and TUN', f'{r.daemon.pid} / {TUN} present')


def stop_daemon(r: Runner) -> None:
    assert r.daemon is not None
    say(['kill', '-TERM', str(r.daemon.pid)])
    r.daemon.terminate()
    r.daemon.wait(timeout=10)
    deadline = time.time() + 5
    while time.time() < deadline:
        if r.run(['ip', 'link', 'show', TUN], check=False).returncode != 0:
            break
        time.sleep(0.1)
    gone = r.run(['ip', 'link', 'show', TUN], check=False).returncode != 0
    record('daemon stopped, tunnel gone', gone)
    assert gone, f'{TUN} survived the daemon'
    r.daemon = None


def reload_clients(r: Runner, log: Path) -> None:
    """SIGHUP and prove from the daemon's own log that it re-read the file."""
    assert r.daemon is not None
    before = count_log(log, 'client list reloaded')
    say(['kill', '-HUP', str(r.daemon.pid)])
    r.daemon.send_signal(signal.SIGHUP)
    line = wait_log(log, 'client list reloaded', above=before)
    record('daemon reload line', line.split('fips-exitd: ', 1)[-1])


def ask(r: Runner, peer: str, script: Path, label: str) -> dict:
    """One client's request to the responder, with every attempt recorded."""
    say(['ip netns exec', peer, 'python3', str(script), WAN4_INET, '1.5', '3', '100'])
    done = r.ns(peer, ['python3', str(script), WAN4_INET, '1.5', '3', '100'])
    out = done.stdout.strip()
    record(f'{label} ask from {peer}', out)
    return {'raw': out,
            'attempts': out.split('attempts=')[1].split(' ')[0].split(','),
            'result': out.split('result=')[1]}


def render_usage(report: Path, label: str) -> list[dict]:
    rows = read_usage(report)
    record(f'{label} usage report', json.dumps(rows))
    return rows


# ---------------------------------------------------------------------------

def inside(args, host_net: str, host_mount: str, lane: Path) -> None:
    # Refuse to run anywhere but the private namespaces the launcher made.
    assert os.geteuid() == 0
    assert os.readlink('/proc/self/ns/net') != host_net
    assert os.readlink('/proc/self/ns/mnt') != host_mount
    mounts = Path('/proc/self/mounts').read_text()
    assert ' /run tmpfs ' in mounts, 'the private tmpfs is not on /run:\n' + mounts

    # A parent that dies must not leave this lane, its daemon or its namespaces
    # behind: an earlier attempt at this work did exactly that.
    def watchdog() -> None:
        while True:
            time.sleep(0.5)
            if not Path(f'/proc/{args.parent_pid}').exists():
                print('WATCHDOG: the parent is gone; tearing the lane down', flush=True)
                os._exit(3)

    threading.Thread(target=watchdog, daemon=True).start()

    lane = Path(lane)
    shim_bin = Path(args.shim)
    daemon_bin = Path(args.daemon)
    clients = lane / 'adm' / 'clients'
    usage = lane / 'usage.json'
    log = lane / 'daemon.log'

    r = Lane(verbose=True)
    try:
        # The two kernel-side helpers are real files, so the log shows what ran.
        (lane / 'no_offload.py').write_text(NO_OFFLOAD)
        (lane / 'ask.py').write_text(ASK)
        ask_script = lane / 'ask.py'
        peers = {}
        for name in PEERS:
            printed = shim(shim_bin, lane, 'grant', key_for(name), '60', '0')
            peers[name] = {'key': key_for(name), 'outer': field(printed, 'outer'),
                           'inner': field(printed, 'inner')}
            print(f'# {name}: key {peers[name]["key"]}', flush=True)
            print(f'# {name}: outer {peers[name]["outer"]} inner {peers[name]["inner"]}',
                  flush=True)
        # Those four grants existed only to read the derivation. The lane starts
        # from a cold state, exactly as a fresh exit would.
        shutil.rmtree(lane / 'adm', ignore_errors=True)
        clients.parent.mkdir(parents=True)

        build_topology(r, peers, lane)
        (lane / 'responder.py').write_text(RESPONDER)
        say(['ip netns exec inet python3', str(lane / 'responder.py'), str(lane / 'responder.log')])
        r.popen_ns('inet', ['python3', str(lane / 'responder.py'), str(lane / 'responder.log')])
        time.sleep(0.5)

        clients.write_text('')
        start_daemon(r, daemon_bin, peers, clients, usage, log)
        record('clients file given to the daemon', repr(clients.read_text()))
        record('usage report at start', json.dumps(wait_report(usage)))

        # --- (a) UNPAID ---------------------------------------------------
        print('\n=== (a) UNPAID: an absent clients-file line gets no answer', flush=True)
        a = ask(r, 'c1', ask_script, 'a.unpaid')
        record('a.unpaid verdict', a['result'])
        assert a['result'] == 'timeout', f'an unlisted peer was answered: {a}'
        rows = render_usage(usage, 'a.unpaid')
        assert not [row for row in rows if row['client'] == peers['c1']['outer']], rows
        record('a.unpaid billed rows for c1', 'none')

        # --- (b) GRANT ----------------------------------------------------
        print('\n=== (b) GRANT: a shim grant answers and is billed', flush=True)
        granted = shim(shim_bin, lane, 'grant', peers['c1']['key'], '3600', '200000')
        assert field(granted, 'outer') == peers['c1']['outer'], granted
        record('b.grant mode', field(granted, 'mode'))
        record('b.clients-file sha256', sha256_file(clients))
        record('b.clients-file bytes', repr(clients.read_text()))
        reload_clients(r, log)
        b = ask(r, 'c1', ask_script, 'b.grant')
        record('b.grant verdict', b['result'])
        assert b['result'] == '100', f'the granted peer was not answered: {b}'
        row = usage_row(usage, peers['c1']['outer'], want=True)
        assert row is not None, f'no usage row for the granted peer: {read_usage(usage)}'
        record('b.grant usage row', json.dumps(row))
        assert row['from_client_bytes'] > 0, row
        record('b.grant from_client_bytes > 0', row['from_client_bytes'])
        state = json.loads((lane / 'adm' / 'state.json').read_text())
        record('b.state revision', state['revision'])
        expiry_epoch = field(granted, 'expires_epoch')
        record('b.grant expires_epoch', expiry_epoch)

        # --- (c) REVOKE AN EXISTING GRANT ----------------------------------
        print('\n=== (c) REVOKE: an already-working path must stop', flush=True)
        past = int(expiry_epoch) + 1
        record('c.revoke now used / expires_epoch', f'{past} / {expiry_epoch}')
        revoked = shim(shim_bin, lane, 'verdict', str(past), '0')
        record('c.verdict line', revoked.splitlines()[0])
        record('c.clients-file bytes after revoke', repr(clients.read_text()))
        assert clients.read_text() == '', 'the revoked peer kept a clients-file line'
        reload_clients(r, log)
        c = ask(r, 'c1', ask_script, 'c.revoke')
        record('c.revoke verdict', c['result'])
        record('c.revoke attempts', ','.join(c['attempts']))
        record('c.revoke blocked on the first post-revoke attempt',
               c['attempts'][0] == 'timeout')
        assert c['result'] == 'timeout', f'the revoked peer still passed: {c}'
        rows = render_usage(usage, 'c.revoke')
        kept = next((row for row in rows if row['client'] == peers['c1']['outer']), None)
        record('c.revoke meter for the revoked peer', json.dumps(kept))

        # --- (d) UNMANAGED OPERATOR LINE -----------------------------------
        print('\n=== (d) UNMANAGED: an operator line survives reconcile', flush=True)
        operator_line = f'{peers["c2"]["outer"]} # operator-lan\n'
        with clients.open('a') as f:
            f.write(operator_line)
        record('d.clients-file with the operator line', repr(clients.read_text()))
        rec = shim(shim_bin, lane, 'reconcile', str(clients))
        preserved = [line for line in rec.splitlines() if line.startswith('  preserved ')]
        needs = [line for line in rec.splitlines() if line.startswith('  needs-grant ')]
        record('d.reconcile preserved lines', json.dumps(preserved))
        record('d.reconcile needs-grant lines', json.dumps(needs))
        record('d.reconcile rendered file', repr(clients.read_text()))
        assert operator_line in clients.read_text(), 'reconcile dropped the operator line'
        assert preserved and peers['c2']['outer'] in preserved[0], preserved
        assert needs and peers['c2']['outer'] in needs[0], needs
        reload_clients(r, log)
        d = ask(r, 'c2', ask_script, 'd.unmanaged')
        record('d.unmanaged verdict (a line with no grant is still admitted)', d['result'])

        # --- (e) RESTART ---------------------------------------------------
        print('\n=== (e) RESTART: a valid grant survives, an expired one does not',
              flush=True)
        shim(shim_bin, lane, 'grant', peers['c1']['key'], '3600', '0')
        short = shim(shim_bin, lane, 'grant', peers['c3']['key'], '5', '0')
        record('e.short-grant expires_epoch', field(short, 'expires_epoch'))
        reload_clients(r, log)
        e_running = ask(r, 'c3', ask_script, 'e.running-grant')
        record('e.running-grant c3 verdict', e_running['result'])
        assert e_running['result'] == '100', e_running
        # Let the short grant pass its expiry while its line stays in the file:
        # the daemon has no notion of expiry, so the line alone still admits.
        time.sleep(6)
        stale = ask(r, 'c3', ask_script, 'e.expired-line-still-present')
        record('e.expired-line-still-present c3 verdict', stale['result'])
        e_control = ask(r, 'c4', ask_script, 'e.never-granted (control)')
        record('e.never-granted c4 verdict', e_control['result'])
        assert e_control['result'] == 'timeout', e_control

        stop_daemon(r)
        # Restart recovery: fold the durable state into the observed file. This
        # is the only step that drops an expired line.
        rec2 = shim(shim_bin, lane, 'reconcile', str(clients))
        dropped = [line for line in rec2.splitlines() if line.startswith('  dropped ')]
        record('e.restart reconcile dropped lines', json.dumps(dropped))
        record('e.restart clients file', repr(clients.read_text()))
        assert peers['c3']['outer'] not in clients.read_text(), 'the expired line survived'
        assert peers['c1']['outer'] in clients.read_text(), 'the live line was dropped'
        start_daemon(r, daemon_bin, peers, clients, usage, log)
        # The report file outlives the daemon: it is only rewritten when the new
        # daemon's counters differ from its own empty set, so what a gate reading
        # it immediately after a restart sees is the PREVIOUS run's rows.
        record('e.restart usage report as inherited from the previous run',
               json.dumps(read_usage(usage)))
        after_live = ask(r, 'c1', ask_script, 'e.after-restart (valid grant)')
        record('e.after-restart c1 verdict', after_live['result'])
        assert after_live['result'] == '100', after_live
        after_expired = ask(r, 'c3', ask_script, 'e.after-restart (expired grant)')
        record('e.after-restart c3 verdict', after_expired['result'])
        assert after_expired['result'] == 'timeout', after_expired
        after_never = ask(r, 'c4', ask_script, 'e.after-restart (never granted)')
        record('e.after-restart c4 verdict', after_never['result'])
        assert after_never['result'] == 'timeout', after_never
        after_unmanaged = ask(r, 'c2', ask_script, 'e.after-restart (operator line)')
        record('e.after-restart c2 verdict', after_unmanaged['result'])
        render_usage(usage, 'e.after-restart')

        # What the responder actually saw, which is the exit's masqueraded
        # uplink address for every answer: proof the daemon forwarded these.
        peers_seen = sorted({line.split()[0] for line in
                             (lane / 'responder.log').read_text().splitlines() if line.strip()})
        record('responder saw these source addresses', json.dumps(peers_seen))
        assert peers_seen == [WAN4_EXIT], peers_seen
        record('final clients file', repr(clients.read_text()))
        state = json.loads((lane / 'adm' / 'state.json').read_text())
        record('final state revision', state['revision'])
        record('final state entitlements', json.dumps(
            [{k: e[k] for k in ('name', 'mode', 'expires_at', 'revision')}
             for e in state['entitlements']]))
        record('final state unmanaged',
               json.dumps([e['Addr'] + ' # ' + e['Name'] for e in state.get('unmanaged', [])]))

        print('\nPASS: (a) through (e) observed as recorded above', flush=True)
    finally:
        r.cleanup()
        left = r.run(['ip', 'netns', 'list'], check=False).stdout.strip()
        record('namespaces left in the private registry', repr(left))
        assert not left, 'namespace cleanup failed'
        # Only the daemon we started: this script's own command line carries
        # the lane path too, so it must not be what "no leftovers" looks at.
        leftovers = r.run(['pgrep', '-a', '-f', f'fips-exitd --clients {lane}'],
                          check=False).stdout.strip()
        record('lane processes left', repr(leftovers))
        assert not leftovers, f'processes left behind: {leftovers}'
        print('CLEANUP: private namespace registry empty, no daemon, no lane process', flush=True)


# ---------------------------------------------------------------------------

def host_run_snapshot() -> dict:
    """Every entry of the host's own /run, with the bytes of any regular file.

    The host is a live machine: systemd rewrites things under /run constantly,
    so a single digest of the whole tree would move for reasons that have
    nothing to do with this lane. The comparison below therefore separates
    *our* artefacts from that background churn instead of pretending the
    machine was idle.
    """
    entries = {}
    for root, dirs, files in os.walk('/run', onerror=lambda e: None):
        for name in sorted(dirs) + sorted(files):
            path = os.path.join(root, name)
            try:
                info = os.lstat(path)
            except OSError:
                continue
            digest = ''
            if stat.S_ISREG(info.st_mode):
                try:
                    digest = sha256_file(Path(path))
                except OSError:
                    digest = 'unreadable'
            entries[path] = (info.st_mode, info.st_size, digest)
    run = os.stat('/run')
    # The device of /run: a mount over it would change this, and so would a
    # tmpfs left in place where the host's own /run used to be.
    entries['/'] = ('mount', run.st_dev, run.st_ino)
    return entries


# Names no host /run entry has: they are this lane's own, so any of them
# appearing there would mean the isolation failed.
LANE_MARKS = ('netns', 'admission', 'fipsexit', 'prta-admission')


def lane_damage(before: dict, after: dict) -> list[str]:
    """Host /run paths this lane could have touched, and the churn that is not ours."""
    damage = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) == after.get(path):
            continue
        if any(mark in path for mark in LANE_MARKS):
            damage.append(path)
    return damage


def lane_processes() -> list[str]:
    """Any process of this lane's shape still alive on the host."""
    out = subprocess.run(['pgrep', '-a', '-f', 'fips-exitd --clients .*prta-admission'],
                         capture_output=True, text=True).stdout.strip()
    return [line for line in out.splitlines() if line]


def main() -> None:
    if not __debug__:
        raise RuntimeError('Python optimization disables lane assertions; refused')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module-repo', default=str(MODULE_REPO),
                        help='module checkout whose fips_exit the shim compiles against')
    parser.add_argument('--exit-repo', default=str(EXIT_ACCOUNTING),
                        help='fips-exit checkout holding target/release/fips-exitd')
    parser.add_argument('--go', default=str(GO), help='pinned go toolchain')
    parser.add_argument('--shim-dir', default=None,
                        help='tools/admissionctl in this repository (default: from this script)')
    parser.add_argument('--inside', nargs=3, metavar=('HOST_NET', 'HOST_MOUNT', 'LANE_DIR'))
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('--shim')
    parser.add_argument('--daemon')
    args = parser.parse_args()

    if args.inside:
        inside(args, *args.inside)
        return

    here = Path(__file__).resolve().parent.parent
    shim_dir = Path(args.shim_dir) if args.shim_dir else here / 'tools' / 'admissionctl'
    lane = Path(tempfile.mkdtemp(prefix='prta-admission-', dir=tempfile.gettempdir()))
    bindir = Path(tempfile.mkdtemp(prefix='prta-admission-bin-'))
    try:
        print('=== provenance', flush=True)
        for label, repo in (('module', Path(args.module_repo)), ('exit', Path(args.exit_repo))):
            print(f'{label} repo {repo}', flush=True)
            print(f'{label} sha {git(repo, "rev-parse", "HEAD")}', flush=True)
            print(f'{label} branch {git(repo, "rev-parse", "--abbrev-ref", "HEAD")}', flush=True)
            print(f'{label} status '
                  f'{"clean" if not git(repo, "status", "--porcelain") else "DIRTY"}', flush=True)
        print(f'shim repo {here}', flush=True)
        print(f'shim sha {git(here, "rev-parse", "HEAD")}', flush=True)
        print(f'go {subprocess.check_output([args.go, "version"], text=True).strip()}', flush=True)
        print(f'kernel {os.uname().release}', flush=True)
        print(f'nft {subprocess.check_output(["nft", "--version"], text=True).strip()}', flush=True)
        print(f'python {sys.version.split()[0]}', flush=True)

        shim_bin = bindir / 'admissionctl'
        say([args.go, 'build', '-o', str(shim_bin), '.'])
        subprocess.run([args.go, 'build', '-o', str(shim_bin), '.'], cwd=shim_dir, check=True,
                       env=dict(os.environ, GOTOOLCHAIN='local'))
        print(f'shim binary sha256 {sha256_file(shim_bin)}', flush=True)

        before, leaked_before = host_run_snapshot(), lane_processes()
        print(f'host /run surveyed before: {len(before)} entries', flush=True)
        net_ns, mnt_ns = os.readlink('/proc/self/ns/net'), os.readlink('/proc/self/ns/mnt')
        command = ['sudo', '-n', 'unshare', '-nm', '--propagation', 'private', 'sh', '-c',
                   f'mount -t tmpfs tmpfs /run && exec python3 "{Path(__file__).resolve()}" '
                   f'--inside "{net_ns}" "{mnt_ns}" "{lane}" --parent-pid {os.getpid()} '
                   f'--shim "{shim_bin}" '
                   f'--daemon "{Path(args.exit_repo) / "target/release/fips-exitd"}"']
        say(command)
        done = subprocess.run(command, text=True,
                              env=dict(os.environ, TMPDIR=tempfile.gettempdir()))
        print(f'$ # launcher exit status {done.returncode}', flush=True)

        # The host's /run is read from the host side, after the private mount
        # namespace that held the tmpfs has gone.
        after = host_run_snapshot()
        print(f'host /run surveyed after: {len(after)} entries', flush=True)
        damage = lane_damage(before, after)
        touched = [p for p in sorted(set(before) | set(after))
                   if before.get(p) != after.get(p)]
        record('host /run entries this lane could have made', json.dumps(damage))
        record('host /run entries that changed at all (live host, systemd churn)',
               json.dumps(touched))
        record('host /run mount identity unchanged',
               before['/'] == after['/'])
        leaked = [line for line in lane_processes() if line not in leaked_before]
        record('lane processes left behind', json.dumps(leaked))
        assert not damage, f'the host /run holds this lane\'s artefacts: {damage}'
        assert before['/'] == after['/'], 'the host /run is not the mount it was'
        assert not leaked, f'processes left behind: {leaked}'
        assert done.returncode == 0, f'the lane failed with status {done.returncode}'
        print('PASS: host /run carries no lane artefact and its mount identity is unchanged; '
              'no lane process left behind', flush=True)
    finally:
        shutil.rmtree(bindir, ignore_errors=True)
        shutil.rmtree(lane, ignore_errors=True)


if __name__ == '__main__':
    main()
