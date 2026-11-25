#!/usr/bin/env python3
"""One opt-in, hardware-free kernel lane; see docs/fips-exit-packet-lane.md."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile

# Reuse the canonical netns command runner, not its hardware/radio lifecycle.
Runner = runpy.run_path(str(Path(__file__).with_name('hwsim-netns-poc.py')))['Runner']
PIN = 'b2e4f615cff9fa369eb3918406be7d559001f9ad'
NAMES = ('consumer', 'guest', 'peer', 'wan', 'other')


class Lane(Runner):
    def cleanup(self):
        for name in reversed(NAMES):
            self.run(['ip', 'netns', 'delete', name], check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)


def render(repo):
    """Compile unmodified renderer dependencies taken from pinned git objects."""
    with tempfile.TemporaryDirectory(prefix='prta-render-') as temp:
        root = Path(temp)
        (root / 'fips_exit').mkdir()
        for name in ('address.go', 'config.go', 'render.go', 'plan.go'):
            data = subprocess.check_output(['git', '-C', repo, 'show', f'{PIN}:src/fips_exit/{name}'])
            (root / 'fips_exit' / name).write_bytes(data)
        (root / 'go.mod').write_text('module fixture\n\ngo 1.22\n')
        (root / 'main.go').write_text('''package main
import ("encoding/json"; "os"; f "fixture/fips_exit")
func main() {
 c := f.Config{Enabled:true, Selection:f.GuestForwarding,
 LocalPublicKey:[32]byte{1}, ExitPublicKey:[32]byte{2},
 GuestInterface:"guest-test", FIPSInterface:"mesh-test", TunnelInterface:"exit-test",
 RoutingTable:200, RulePriority:10000, MTU:1240}
 fragment, err := f.RenderFW4(c, c.GuestInterface); if err != nil { panic(err) }
 outer, inner := f.Derive(c.LocalPublicKey)
 peerOuter, peerInner := f.Derive(c.ExitPublicKey)
 // Synthetic fixture attestations, not FIPS/NDS readiness evidence.
 e := f.Environment{Interfaces:map[string]int{"guest-test":1500,"mesh-test":1500},
 LocalPublicKey:c.LocalPublicKey, LocalFIPSAddress:outer,
 Verified:true, TunnelNameUnused:true, TableUnused:true, RulePriorityUnused:true,
 AddressesConflictFree:true, PolicyOrderSafe:true, OuterRouteVerified:true}
 g := f.GuardReadiness{Forwarding:true,DNS:true,NativeIPv6:true,NAT:true,MSS:true,PortalAuthorization:true}
 ops, err := f.BuildPlan(c,e,g); if err != nil { panic(err) }
 if err := f.ValidateOperations(c,g,ops); err != nil { panic(err) }
 json.NewEncoder(os.Stdout).Encode(map[string]interface{}{"fragment":fragment,"inner":inner.String(),
 "outer":outer.String(),"peer_outer":peerOuter.String(),"peer_inner":peerInner.String(),"operations":ops})
}
''')
        return json.loads(subprocess.check_output(['go', 'run', '.'], cwd=root))


def ipip6(r, payload):
    """Kernel-only fixture: exact consumer plan, IPv6 veth replacing FIPS."""
    def ns(name, *argv):
        return r.ns(name, list(argv))
    for name, iface in (('guest', 'guest-test'), ('peer', 'mesh-test')):
        ns('consumer', 'ip', 'link', 'add', iface, 'type', 'veth', 'peer', 'name', 'remote')
        ns('consumer', 'ip', 'link', 'set', 'remote', 'netns', name)
        ns('consumer', 'ip', 'link', 'set', iface, 'up')
        ns(name, 'ip', 'link', 'set', 'remote', 'up')
    ns('consumer', 'ip', 'addr', 'add', '10.73.1.1/24', 'dev', 'guest-test')
    ns('guest', 'ip', 'addr', 'add', '10.73.1.2/24', 'dev', 'remote')
    ns('guest', 'ip', 'route', 'add', 'default', 'via', '10.73.1.1')
    for name, iface, local, remote in (
            ('consumer', 'mesh-test', payload['outer'], payload['peer_outer']),
            ('peer', 'remote', payload['peer_outer'], payload['outer'])):
        ns(name, 'ip', '-6', 'addr', 'add', local + '/128', 'dev', iface, 'nodad')
        ns(name, 'ip', '-6', 'route', 'add', remote + '/128', 'dev', iface)
    ns('consumer', 'sysctl', '-qw', 'net.ipv4.ip_forward=1')
    ns('consumer', 'sysctl', '-qw', 'net.ipv4.conf.all.rp_filter=0')
    ns('consumer', 'sysctl', '-qw', 'net.ipv4.conf.default.rp_filter=0')
    ns('consumer', 'ping', '-6', '-n', '-c', '1', '-W', '2', payload['peer_outer'])
    rules = 'table inet fw4 {\ndefine tg_portal_if = "guest-test"\n' + payload['fragment'] + '}\n'
    subprocess.run(['ip', 'netns', 'exec', 'consumer', 'nft', '-f', '-'],
                   input=rules, text=True, check=True)
    for op in payload['operations']:
        print(json.dumps({'planner_operation': op}), flush=True)
        # Deliberately no repair/retry/fallback: preserve planner failures.
        r.ns('consumer', [op['Program'], *op['Args']])
    ns('peer', 'ip', '-6', 'tunnel', 'add', 'peer-exit', 'mode', 'ipip6',
       'local', payload['peer_outer'], 'remote', payload['outer'], 'dev', 'remote',
       'encaplimit', 'none', 'hoplimit', '64')
    ns('peer', 'ip', 'link', 'set', 'peer-exit', 'mtu', '1240', 'up')
    ns('peer', 'ip', 'addr', 'add', payload['peer_inner'] + '/32', 'dev', 'peer-exit')
    ns('peer', 'ip', 'route', 'add', payload['inner'] + '/32', 'dev', 'peer-exit')
    observation = ('table inet observation { chain observed { type filter hook input priority 0; '
                   'policy accept; iifname "peer-exit" ip saddr ' + payload['inner'] +
                   ' ip daddr ' + payload['peer_inner'] + ' icmp type echo-request counter; }; }\n')
    subprocess.run(['ip', 'netns', 'exec', 'peer', 'nft', '-f', '-'],
                   input=observation, text=True, check=True)
    result = ns('guest', 'ping', '-n', '-c', '1', '-W', '2', payload['peer_inner'])
    print(result.stdout, flush=True)
    observed = json.loads(ns('peer', 'nft', '-j', 'list', 'chain', 'inet', 'observation', 'observed').stdout)
    count = sum(expr['counter']['packets'] for item in observed['nftables']
                for expr in item.get('rule', {}).get('expr', []) if 'counter' in expr)
    assert count > 0, 'peer did not observe derived inner IPv4 source'
    print(json.dumps({'peer_observed_snat_source': payload['inner'], 'echo_requests': count}), flush=True)
    print('PASS: kernel ipip6 guest roundtrip only; not FIPS transport, NDS, or paid service', flush=True)


def inside(payload, host_net, host_mount, mode='veth'):
    # Refuse direct execution or a namespace connected to a host interface.
    assert os.geteuid() == 0
    assert os.readlink('/proc/self/ns/net') != host_net
    assert os.readlink('/proc/self/ns/mnt') != host_mount
    links = json.loads(subprocess.check_output(['ip', '-j', 'link']))
    assert any(link['ifname'] == 'lo' for link in links), links
    # Loading ip6_tunnel creates this inert fallback in every fresh netns.
    assert all(link['ifname'] == 'lo' or (
        link['ifname'] == 'ip6tnl0' and link['link_type'] == 'tunnel6'
        and link['address'] == '::' and link['broadcast'] == '::'
        and link['operstate'] == 'DOWN' and 'UP' not in link['flags']
        and link.get('link') is None) for link in links), links
    subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', '/run/netns'], check=True)
    r = Lane()
    def ns(name, *args):
        return r.ns(name, list(args))
    def nft(text):
        # stdin preserves exact rendered bytes; no nft textual rewrites.
        subprocess.run(['ip', 'netns', 'exec', 'consumer', 'nft', '-f', '-'], input=text, text=True, check=True)
    def counters(chain, table='fw4'):
        data = json.loads(ns('consumer', 'nft', '-j', 'list', 'chain', 'inet', table, chain).stdout)
        return sum(expr['counter']['packets'] for item in data['nftables']
                   for expr in item.get('rule', {}).get('expr', []) if 'counter' in expr)
    def probe(name, target, allowed, chain=None):
        before = counters(chain) if chain else 0
        result = r.ns(name, ['ping', '-n', '-c', '1', '-W', '1', target], check=False)
        assert result.returncode in (0, 1), (result.returncode, result.stderr)
        assert (result.returncode == 0) == allowed, (name, target, result.stdout, result.stderr)
        delta = counters(chain) - before if chain else None
        if chain:
            assert delta is not None and delta > 0, (chain, delta)
        print(json.dumps({'probe': f'{name}->{target}', 'allowed': allowed, 'drop_counter_delta': delta}), flush=True)
    def blocked_probes(guard):
        probe('guest', '10.73.3.2', not guard, 'tg_fexit_forward' if guard else None)
        probe('peer', '10.73.2.1', not guard, 'tg_fexit_input' if guard else None)
        probe('peer', '10.73.4.2', not guard, 'tg_fexit_forward' if guard else None)
        probe('peer', '10.73.1.2', not guard, 'tg_fexit_forward' if guard else None)
    def interrupted(signum, frame):
        raise RuntimeError(f'interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        for name in NAMES:
            r.run(['ip', 'netns', 'add', name])
            ns(name, 'ip', 'link', 'set', 'lo', 'up')
        if mode == 'ipip6':
            ipip6(r, payload)
            return
        for index, (name, iface) in enumerate((('guest', 'guest-test'), ('peer', 'exit-test'), ('wan', 'wan-test'), ('other', 'other-test')), 1):
            ns('consumer', 'ip', 'link', 'add', iface, 'type', 'veth', 'peer', 'name', 'remote')
            ns('consumer', 'ip', 'link', 'set', 'remote', 'netns', name)
            ns('consumer', 'ip', 'addr', 'add', f'10.73.{index}.1/24', 'dev', iface)
            ns('consumer', 'ip', 'link', 'set', iface, 'up')
            ns(name, 'ip', 'addr', 'add', f'10.73.{index}.2/24', 'dev', 'remote')
            ns(name, 'ip', 'link', 'set', 'remote', 'up')
            ns(name, 'ip', 'route', 'add', 'default', 'via', f'10.73.{index}.1')
        ns('consumer', 'sysctl', '-qw', 'net.ipv4.ip_forward=1')
        ns('consumer', 'ip', 'addr', 'add', payload['inner'] + '/32', 'dev', 'exit-test')
        rules = 'table inet fw4 {\ndefine tg_portal_if = "guest-test"\n' + payload['fragment'] + '}\n'
        # A later observation hook counts established returns without changing guard bytes.
        nft('table inet observation { chain returns { type filter hook forward priority -1; policy accept; iifname "exit-test" oifname "guest-test" ct state established counter; }; }\n')
        print('RED control: without guard, forbidden paths must really work', flush=True)
        blocked_probes(False)
        for cycle in range(2):
            nft(rules)
            print(f'GREEN guard enabled cycle={cycle}', flush=True)
            blocked_probes(True)
            before = counters('returns', 'observation')
            probe('guest', '10.73.2.2', True)
            delta = counters('returns', 'observation') - before
            assert delta > 0, 'no established return observed after guard'
            print(json.dumps({'established_return_counter_delta': delta}), flush=True)
            print(ns('consumer', 'nft', '-j', 'list', 'ruleset').stdout, flush=True)
            nft('delete table inet fw4')
            if cycle == 0:
                print('NEGATIVE control: guard removed', flush=True)
                blocked_probes(False)
        print('PASS: simple-veth guard only; not ipip6, FIPS transport, NDS, or paid service', flush=True)
    finally:
        r.cleanup()
        assert not r.run(['ip', 'netns', 'list']).stdout.strip(), 'namespace cleanup failed'
        print('CLEANUP: private namespace registry empty', flush=True)


def main():
    if not __debug__:
        raise RuntimeError('Python optimization disables lane assertions; refused')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module-repo')
    parser.add_argument('--mode', choices=('veth', 'ipip6'), default='veth')
    parser.add_argument('--inside', nargs=2, metavar=('HOST_NET', 'HOST_MOUNT'))
    args = parser.parse_args()
    if args.inside:
        inside(json.load(sys.stdin), *args.inside, mode=args.mode)
        return
    if not args.module_repo:
        parser.error('--module-repo is required')
    payload = render(args.module_repo)
    print(json.dumps({'module_sha': PIN, 'fragment_sha256': hashlib.sha256(payload['fragment'].encode()).hexdigest(), 'inner': payload['inner']}), flush=True)
    # Private mount namespace prevents named-netns bind mounts escaping to the host.
    subprocess.run(['sudo', '-n', 'unshare', '--mount', '--net', '--propagation', 'private',
                    'env', 'TMPDIR=' + tempfile.gettempdir(), 'python3', str(Path(__file__).resolve()),
                    '--mode', args.mode, '--inside', os.readlink('/proc/self/ns/net'), os.readlink('/proc/self/ns/mnt')],
                   input=json.dumps(payload), text=True, check=True)


if __name__ == '__main__':
    main()
