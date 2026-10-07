#!/usr/bin/env python3
"""Test public login access with an unavailable VPN, for both gateways and host TUN."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from test_review import avpn
import host_network as hn
from integration import available_port, wait_port


def probe(prefix, proxy=None):
    curl = ['curl', '-sS', '--max-time', '10', '--noproxy', '' if proxy else '*']
    if proxy:
        curl += ['--proxy', f'socks5h://127.0.0.1:{proxy}']
    for host in ['vpn.cmu.edu', 'login.cmu.edu']:
        result = subprocess.check_output(prefix + curl + ['-o', '/dev/null', '-w', '%{http_code}', 'https://' + host + '/'], text=True)
        assert result == '200', (host, result)
    result = subprocess.run(prefix + curl + ['https://dl.acm.org/cdn-cgi/trace'], capture_output=True, text=True)
    assert result.returncode != 0 and not result.stdout.strip(), 'Academic traffic bypassed the unavailable VPN'


def main():
    installed = avpn.resolve_args(avpn.parser().parse_args(['status']))
    with tempfile.TemporaryDirectory(prefix='avpn-auth-routes-') as td, socket.socket() as dead:
        dead.bind(('127.0.0.1', 0))  # Hold a non-listening port: VPN definitely unavailable.
        state = Path(td)
        (state / 'bin').mkdir()
        for name in ['sing-box', 'xray']:
            (state / 'bin' / name).symlink_to(installed.state / 'bin' / name)
        for mode in ['rules', 'all']:
            port, local = available_port(), available_port()
            args = avpn.resolve_args(avpn.parser().parse_args(['generate', '--state', td,
                '--server', '127.0.0.1', '--port', str(port), '--mode', mode,
                '--vpn-server', 'https://vpn.cmu.edu', '--vpn-port', str(dead.getsockname()[1])]))
            avpn.generate(args)
            for backend in ['sing-box', 'xray']:
                path = state / 'config' / (backend + '-server.json')
                config = json.loads(path.read_text()); config['inbounds'][0]['listen'] = '127.0.0.1'
                path.write_text(json.dumps(config))
                client_path = state / 'client.json'
                client_config = json.loads((state / 'outputs/sing-box/client.json').read_text())
                client_config['inbounds'][0]['listen_port'] = local
                client_path.write_text(json.dumps(client_config))
                with (state / 'test.log').open('w') as log:
                    server = subprocess.Popen([str(state / 'bin' / backend), 'run', '-c' if backend == 'sing-box' else '-config', str(path)], stdout=log, stderr=log)
                    client = subprocess.Popen([str(state / 'bin/sing-box'), 'run', '-c', str(client_path)], stdout=log, stderr=log)
                    try:
                        wait_port(port, server); wait_port(local, client)
                        probe([], local)
                        print('PASS ' + backend + '/' + mode + ': login works, academic fails with dead VPN', flush=True)
                    finally:
                        client.terminate(); server.terminate(); client.wait(timeout=5); server.wait(timeout=5)
            ns = 'avpn-auth-' + str(os.getpid())
            resolver = Path('/etc/netns') / ns
            subprocess.run(['ip', 'netns', 'add', ns], check=True)
            try:
                subprocess.run(['ip', '-n', ns, 'link', 'set', 'lo', 'up'], check=True)
                resolver.mkdir(parents=True)
                (resolver / 'resolv.conf').write_text('nameserver ' + hn.DNS_ADDRESS + '\n')
                iface = json.loads(subprocess.check_output(['ip', '-j', 'route', 'show', 'default']))[0]['dev']
                network = {'interface': iface, 'exclude': ['127.0.0.0/8', '::1/128'],
                           'vpn_dns': '128.2.1.10', 'vpn_hostname': 'vpn.cmu.edu', 'vpn_port': dead.getsockname()[1]}
                path = state / 'host.json'
                path.write_text(json.dumps(hn.build_config(avpn, args, network, netns=ns)))
                subprocess.run([str(state / 'bin/sing-box'), 'check', '-c', str(path)], check=True)
                with (state / 'host.log').open('w') as log:
                    process = subprocess.Popen([str(state / 'bin/sing-box'), 'run', '-c', str(path)], stdout=log, stderr=log)
                    try:
                        for _ in range(50):
                            assert process.poll() is None, (state / 'host.log').read_text()
                            if hn.INTERFACE in subprocess.check_output(['ip', '-n', ns, 'link'], text=True):
                                break
                            time.sleep(.1)
                        time.sleep(.2)
                        probe(['ip', 'netns', 'exec', ns])
                        print('PASS host/' + mode + ': native DNS and login work, academic fails with dead VPN', flush=True)
                    finally:
                        process.terminate(); process.wait(timeout=5)
            finally:
                shutil.rmtree(resolver, ignore_errors=True)
                subprocess.run(['ip', 'netns', 'del', ns], check=True)


if __name__ == '__main__':
    main()
