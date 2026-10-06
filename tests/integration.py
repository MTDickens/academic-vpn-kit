#!/usr/bin/env python3
"""Opt-in live tests on loopback. Uses a caller-provided existing SOCKS VPN exit."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def available_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait_port(port, process):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('测试进程提前退出；查看私人测试日志')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.1):
                return
        except OSError:
            time.sleep(.05)
    raise RuntimeError('测试进程未监听')


def ip(port, url):
    args = ['curl', '-fsSL', '--max-time', '20']
    if port:
        args += ['--proxy', f'socks5h://127.0.0.1:{port}']
    return subprocess.check_output(args + [url], text=True).strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bin-dir', type=Path, required=True)
    p.add_argument('--vpn-port', type=int, required=True)
    a = p.parse_args()
    direct = ip(None, 'https://api.ipify.org')
    vpn = ip(a.vpn_port, 'https://api.ipify.org')
    if direct == vpn:
        raise RuntimeError('需要出口 IP 与服务器不同的 SOCKS VPN 来验证分流')
    with tempfile.TemporaryDirectory(prefix='vpn-route-integration-') as td:
        state = Path(td)
        (state / 'bin').mkdir()
        for b in ['sing-box', 'xray']:
            (state / 'bin' / b).symlink_to(a.bin_dir.resolve() / b)
        rule = state / 'test-domains.txt'; rule.write_text('full:api.ipify.org\n')
        gateway_port = available_port()
        for mode in ['none', 'rules', 'all']:
            subprocess.run([str(ROOT / 'entrypoint.sh'), 'generate', '--state', str(state),
                '--server', '127.0.0.1', '--port', str(gateway_port), '--mode', mode,
                '--vpn-kind', 'socks', '--upstream-port', str(a.vpn_port), '--rules', str(rule)], check=True)
            subprocess.run([str(ROOT / 'entrypoint.sh'), 'check', '--state', str(state)], check=True)
            for backend in ['sing-box', 'xray']:
                cfg = state / 'config' / (backend + '-server.json')
                data = json.loads(cfg.read_text())
                data['inbounds'][0]['listen'] = '127.0.0.1'
                cfg.write_text(json.dumps(data))
                flags = ['run', '-c', str(cfg)] if backend == 'sing-box' else ['run', '-config', str(cfg)]
                with (state / 'gateway.log').open('w') as log:
                    gateway = subprocess.Popen([str(state / 'bin' / backend), *flags], stdout=log, stderr=log)
                    try:
                        wait_port(gateway_port, gateway)
                        for client_backend in ['sing-box', 'xray']:
                            client_port = available_port()
                            client_data = json.loads((state / 'outputs' / client_backend / 'client.json').read_text())
                            client_data['inbounds'][0]['listen_port' if client_backend == 'sing-box' else 'port'] = client_port
                            client_cfg = state / 'test-client.json'; client_cfg.write_text(json.dumps(client_data))
                            flags_c = ['run', '-c', str(client_cfg)] if client_backend == 'sing-box' else ['run', '-config', str(client_cfg)]
                            with (state / 'client.log').open('w') as cl:
                                client = subprocess.Popen([str(state / 'bin' / client_backend), *flags_c], stdout=cl, stderr=cl)
                                try:
                                    wait_port(client_port, client)
                                    selected = ip(client_port, 'https://api.ipify.org')
                                    ordinary = ip(client_port, 'https://checkip.amazonaws.com')
                                    expected_selected = direct if mode == 'none' else vpn
                                    expected_ordinary = vpn if mode == 'all' else direct
                                    assert selected == expected_selected, (mode, backend, client_backend, 'selected')
                                    assert ordinary == expected_ordinary, (mode, backend, client_backend, 'ordinary')
                                    print(f'PASS {mode}: {client_backend} client → {backend} server')
                                finally:
                                    client.terminate(); client.wait(timeout=5)
                    finally:
                        gateway.terminate(); gateway.wait(timeout=5)
        # Fail-closed with unavailable upstream; direct unselected connections still work.
        dead = available_port()
        subprocess.run([str(ROOT / 'entrypoint.sh'), 'generate', '--state', str(state),
            '--mode', 'rules', '--vpn-kind', 'socks', '--upstream-port', str(dead)], check=True)
        for backend in ['sing-box', 'xray']:
            cfg = state / 'config' / (backend + '-server.json')
            data = json.loads(cfg.read_text()); data['inbounds'][0]['listen'] = '127.0.0.1'
            cfg.write_text(json.dumps(data))
            client_cfg = state / 'test-client.json'
            c = json.loads((state / 'outputs/sing-box/client.json').read_text())
            local = available_port(); c['inbounds'][0]['listen_port'] = local
            client_cfg.write_text(json.dumps(c))
            flags = ['run', '-c', str(cfg)] if backend == 'sing-box' else ['run', '-config', str(cfg)]
            with (state / 'failure.log').open('w') as log:
                gateway = subprocess.Popen([str(state / 'bin' / backend), *flags], stdout=log, stderr=log)
                client = subprocess.Popen([str(state / 'bin/sing-box'), 'run', '-c', str(client_cfg)], stdout=log, stderr=log)
                try:
                    wait_port(gateway_port, gateway); wait_port(local, client)
                    r = subprocess.run(['curl', '-fsS', '--max-time', '5', '--proxy',
                        f'socks5h://127.0.0.1:{local}', 'https://api.ipify.org'], capture_output=True)
                    assert r.returncode != 0, 'VPN 断开时不应回退直连'
                    assert ip(local, 'https://checkip.amazonaws.com') == direct
                    print(f'PASS fail-closed: {backend}')
                finally:
                    client.terminate(); gateway.terminate(); client.wait(timeout=5); gateway.wait(timeout=5)


if __name__ == '__main__':
    main()
