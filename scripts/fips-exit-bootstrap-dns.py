#!/usr/bin/env python3
"""Opt-in real dnsmasq bootstrap check; no host resolver or hardware changes."""
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
import time

Runner = runpy.run_path(str(Path(__file__).with_name('hwsim-netns-poc.py')))['Runner']
PIN = '9320319f05da69a2f13e78c8f0d78b16ab3fa376'


def render(repo, go):
    with tempfile.TemporaryDirectory(prefix='prta-bootstrap-render-') as temp:
        root = Path(temp)
        (root / 'fips_exit').mkdir()
        source = subprocess.check_output(['git', '-C', repo, 'show', f'{PIN}:src/fips_exit/bootstrap_dns.go'])
        (root / 'fips_exit/bootstrap_dns.go').write_bytes(source)
        (root / 'go.mod').write_text('module fixture\n\ngo 1.26.10\n')
        (root / 'main.go').write_text('''package main
import ("fmt"; "net/netip"; f "fixture/fips_exit")
func main() {
 s,e := f.RenderBootstrapDNS(true, netip.MustParseAddrPort("10.73.1.1:5353"), "portal.example", netip.MustParseAddr("10.73.1.1"))
 if e != nil { panic(e) }; fmt.Print(s)
}
''')
        env = dict(os.environ, GOTOOLCHAIN='local', GOPROXY='off')
        version = subprocess.check_output([go, 'version'], env=env, text=True).strip()
        if 'go1.26.10 ' not in version:
            raise RuntimeError(version)
        print(version, flush=True)
        return subprocess.check_output([go, 'run', '.'], cwd=root, env=env, text=True, timeout=120)


# Standard-library wire probe, no resolver libraries or host DNS calls.
PROBE = '''import socket,struct,json,sys
name=sys.argv[1]
q=struct.pack('!6H',1234,256,1,0,0,0)+b''.join(bytes([len(x)])+x.encode() for x in name.split('.'))+b'\\0'+struct.pack('!2H',1,1)
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(2)
s.sendto(q,('10.73.1.1',5353)); data,addr=s.recvfrom(4096)
h=struct.unpack('!6H',data[:12]); assert h[0]==1234 and h[1]&32768
print(json.dumps(dict(name=name,rcode=h[1]&15,answers=h[3],wire=data.hex(),last_ipv4=socket.inet_ntoa(data[-4:]) if h[3] else None)))
'''


def inside(config, host_net, host_mount, dnsmasq):
    assert os.geteuid() == 0
    assert os.readlink('/proc/self/ns/net') != host_net
    assert os.readlink('/proc/self/ns/mnt') != host_mount
    links = json.loads(subprocess.check_output(['ip', '-j', 'link']))
    assert all(x['ifname'] == 'lo' or (x['ifname'] == 'ip6tnl0' and 'UP' not in x['flags']) for x in links)
    subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', '/run/netns'], check=True)
    r = Runner()
    names = ('bootstrap', 'dns-guest', 'dns-wan')
    def ns(n, *args):
        return r.ns(n, list(args))
    def interrupted(signum, frame):
        raise RuntimeError(f'interrupted {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        path = r.tmp / 'dnsmasq.conf'
        path.write_text(config)
        result = r.run([dnsmasq, '--test', '--conf-file=' + str(path)], check=False)
        print(json.dumps(dict(parser_rc=result.returncode, stdout=result.stdout, stderr=result.stderr)), flush=True)
        assert result.returncode == 0, 'generated config parser failure; runtime not attempted'
        for n in names:
            r.run(['ip', 'netns', 'add', n])
            ns(n, 'ip', 'link', 'set', 'lo', 'up')
        for n, i in (('dns-guest', 1), ('dns-wan', 3)):
            iface = 'v' + str(i)
            ns('bootstrap', 'ip', 'link', 'add', iface, 'type', 'veth', 'peer', 'name', 'remote')
            ns('bootstrap', 'ip', 'link', 'set', 'remote', 'netns', n)
            ns('bootstrap', 'ip', 'addr', 'add', f'10.73.{i}.1/24', 'dev', iface)
            ns('bootstrap', 'ip', 'link', 'set', iface, 'up')
            ns(n, 'ip', 'addr', 'add', f'10.73.{i}.2/24', 'dev', 'remote')
            ns(n, 'ip', 'link', 'set', 'remote', 'up')
        ns('bootstrap', 'ip', 'route', 'add', 'default', 'via', '10.73.3.2')
        rule = 'table inet observe { chain incoming { type filter hook input priority 0; policy accept; udp dport 53 counter; tcp dport 53 counter; }; }'
        subprocess.run(['ip', 'netns', 'exec', 'dns-wan', 'nft', '-f', '-'], input=rule, text=True, check=True)
        def counters():
            data = json.loads(ns('dns-wan', 'nft', '-j', 'list', 'table', 'inet', 'observe').stdout)
            return [e['counter']['packets'] for x in data['nftables'] for e in x.get('rule', {}).get('expr', []) if 'counter' in e]
        # Positive control proves the WAN observation path counts DNS packets.
        ns('bootstrap', 'python3', '-c', "import socket; socket.socket(socket.AF_INET,socket.SOCK_DGRAM).sendto(b'control',('10.73.3.2',53))")
        before = counters()
        assert before == [1, 0], before
        proc = r.popen_ns('bootstrap', [dnsmasq, '--keep-in-foreground', '--conf-file=' + str(path), '--user=nobody', '--group=nogroup', '--pid-file='])
        for attempt in range(30):
            if proc.poll() is not None:
                raise RuntimeError(proc.communicate())
            listeners = ns('bootstrap', 'ss', '-lun').stdout
            if '10.73.1.1:5353' in listeners:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError('dnsmasq not listening within 3 seconds')
        for name in ('portal.example', 'unrelated.example', 'random-9320319f.portal.example'):
            response = json.loads(ns('dns-guest', 'python3', '-c', PROBE, name).stdout)
            print(json.dumps(response), flush=True)
            if name == 'portal.example':
                assert response['rcode'] == 0 and response['answers'] == 1 and response['last_ipv4'] == '10.73.1.1'
            else:
                assert response['rcode'] in (3, 5) and response['answers'] == 0, response
        after = counters()
        print(json.dumps(dict(wan_dns_before=before, wan_dns_after=after)), flush=True)
        assert after == before, 'WAN DNS leakage observed'
        print('PASS bootstrap only: real DNS replies; no paid DNS, FIPS transport or deployment claim', flush=True)
    finally:
        for proc in reversed(r.procs):
            if proc.poll() is None:
                proc.terminate()
            try:
                out, err = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = proc.communicate(timeout=3)
            print(json.dumps(dict(dnsmasq_exit=proc.returncode, stdout=out, stderr=err)), flush=True)
        for n in reversed(names):
            r.run(['ip', 'netns', 'delete', n], check=False)
        shutil.rmtree(r.tmp)
        assert not r.run(['ip', 'netns', 'list']).stdout.strip()
        assert not r.tmp.exists()
        print('CLEANUP: child reaped; private registry empty; temporary files removed', flush=True)


def main():
    if not __debug__:
        raise RuntimeError('optimized Python refused')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--module-repo', required=True)
    p.add_argument('--go', required=True)
    p.add_argument('--inside', nargs=2)
    args = p.parse_args()
    dnsmasq = shutil.which('dnsmasq')
    if not dnsmasq:
        raise RuntimeError('BLOCKED: dnsmasq missing; no install attempted')
    if args.inside:
        inside(sys.stdin.read(), args.inside[0], args.inside[1], dnsmasq)
        return
    config = render(args.module_repo, args.go)
    print(json.dumps(dict(module_sha=PIN, config_sha256=hashlib.sha256(config.encode()).hexdigest(), config=config)), flush=True)
    subprocess.run(['sudo', '-n', 'unshare', '--mount', '--net', '--propagation', 'private',
                    'env', 'TMPDIR=' + tempfile.gettempdir(), 'PATH=' + os.environ['PATH'],
                    'python3', str(Path(__file__).resolve()), '--module-repo', args.module_repo,
                    '--go', args.go, '--inside', os.readlink('/proc/self/ns/net'), os.readlink('/proc/self/ns/mnt')],
                   input=config, text=True, check=True, timeout=60)


if __name__ == '__main__':
    main()
