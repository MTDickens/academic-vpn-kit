#!/usr/bin/env python3
"""Ordered exceptions with a VPN default, tested against both gateway engines."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from integration import ROOT, available_port, wait_port, ip


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bin-dir', type=Path, required=True)
    p.add_argument('--vpn-port', type=int, required=True)
    a = p.parse_args()
    direct = ip(None, 'https://api.ipify.org')
    vpn = ip(a.vpn_port, 'https://api.ipify.org')
    assert direct != vpn
    with tempfile.TemporaryDirectory(prefix='vpn-route-policy-') as td:
        state = Path(td)
        policy = state / 'policy.json'
        policy.write_text(json.dumps({'default': 'vpn', 'rules': [
            {'domains': ['full:api.ipify.org'], 'outbound': 'direct'},
            {'domains': ['ipify.org'], 'outbound': 'vpn'},
            {'domains': ['full:example.com'], 'outbound': 'block'}]}))
        port = available_port()
        subprocess.run([str(ROOT / 'entrypoint.sh'), 'generate', '--state', str(state),
            '--server', '127.0.0.1', '--port', str(port), '--mode', 'rules', '--vpn-kind', 'socks',
            '--upstream-port', str(a.vpn_port), '--policy', str(policy)], check=True)
        for backend in ['sing-box', 'xray']:
            cfg = state / 'config' / (backend + '-server.json')
            d = json.loads(cfg.read_text()); d['inbounds'][0]['listen'] = '127.0.0.1'
            cfg.write_text(json.dumps(d))
            cp = available_port()
            c = json.loads((state / 'outputs/sing-box/client.json').read_text())
            c['inbounds'][0]['listen_port'] = cp
            ccfg = state / 'client.json'; ccfg.write_text(json.dumps(c))
            b = a.bin_dir.resolve()
            flags = ['run', '-c', str(cfg)] if backend == 'sing-box' else ['run', '-config', str(cfg)]
            with (state / 'test.log').open('w') as log:
                gateway = subprocess.Popen([str(b / backend), *flags], stdout=log, stderr=log)
                client = subprocess.Popen([str(b / 'sing-box'), 'run', '-c', str(ccfg)], stdout=log, stderr=log)
                try:
                    wait_port(port, gateway); wait_port(cp, client)
                    assert ip(cp, 'https://api.ipify.org') == direct, '精确直连例外应先于宽泛 VPN 规则'
                    assert ip(cp, 'https://checkip.amazonaws.com') == vpn, '未匹配连接应走 VPN 默认出口'
                    r = subprocess.run(['curl', '-fsS', '--max-time', '5', '--proxy',
                        f'socks5h://127.0.0.1:{cp}', 'https://example.com/'], capture_output=True)
                    assert r.returncode != 0, '阻断规则不应访问成功'
                    print(f'PASS ordered policy: {backend}')
                finally:
                    client.terminate(); gateway.terminate(); client.wait(timeout=5); gateway.wait(timeout=5)


if __name__ == '__main__':
    main()
