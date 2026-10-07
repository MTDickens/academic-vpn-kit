#!/usr/bin/env python3
"""Exercise ordinary-egress fallback and restoration on both native gateways."""
import json
from pathlib import Path
import socket
import subprocess
import tempfile

from test_review import avpn
from integration import available_port, wait_port
import vpn_health as health


def trace(port):
    return subprocess.run(['curl', '-fsS', '--max-time', '6', '--noproxy', '', '--proxy',
        f'socks5h://127.0.0.1:{port}', 'https://1.1.1.1/cdn-cgi/trace'], capture_output=True, text=True)


def main():
    installed = avpn.resolve_args(avpn.parser().parse_args(['status']))
    with tempfile.TemporaryDirectory(prefix='avpn-fallback-') as td, socket.socket() as dead:
        state = Path(td); (state / 'bin').mkdir()
        for name in ['sing-box', 'xray']:
            (state / 'bin' / name).symlink_to(installed.state / 'bin' / name)
        dead.bind(('127.0.0.1', 0))
        port, local = available_port(), available_port()
        args = avpn.resolve_args(avpn.parser().parse_args(['generate', '--state', td, '--server', '127.0.0.1',
            '--port', str(port), '--mode', 'all', '--vpn-kind', 'socks', '--upstream-port', str(dead.getsockname()[1])]))
        avpn.generate(args)
        client_config = json.loads((state / 'outputs/sing-box/client.json').read_text())
        client_config['inbounds'][0]['listen_port'] = local
        client_path = state / 'client.json'; client_path.write_text(json.dumps(client_config))
        for backend in ['sing-box', 'xray']:
            original = json.loads((state / 'config' / (backend + '-server.json')).read_text())
            original['inbounds'][0]['listen'] = '127.0.0.1'
            for label, config, expected in [('before', original, False),
                                            ('fallback', health.direct_config(original, backend), True),
                                            ('restored', original, False)]:
                path = state / 'server.json'; path.write_text(json.dumps(config))
                validator = (['check', '-c', str(path)] if backend == 'sing-box' else ['run', '-test', '-config', str(path)])
                subprocess.run([str(state / 'bin' / backend), *validator], check=True, stdout=subprocess.DEVNULL)
                with (state / 'test.log').open('w') as log:
                    server = subprocess.Popen([str(state / 'bin' / backend), 'run', '-c' if backend == 'sing-box' else '-config', str(path)], stdout=log, stderr=log)
                    client = subprocess.Popen([str(state / 'bin/sing-box'), 'run', '-c', str(client_path)], stdout=log, stderr=log)
                    try:
                        wait_port(port, server); wait_port(local, client)
                        result = trace(local)
                        assert (result.returncode == 0 and '\nip=' in result.stdout) == expected, (backend, label, result.stderr)
                        print('PASS ' + backend + ': ' + label, flush=True)
                    finally:
                        client.terminate(); server.terminate(); client.wait(timeout=5); server.wait(timeout=5)


if __name__ == '__main__':
    main()
