#!/usr/bin/env python3
"""Native routing tests with a recording SOCKS upstream; no university login required."""
import argparse
import itertools
import json
from pathlib import Path
import queue
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading

from test_review import avpn
from integration import available_port, wait_port


def receive(sock, count):
    data = b''
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError('Connection closed')
        data += chunk
    return data


def socks_connect(port, host, target_port=22):
    sock = socket.create_connection(('127.0.0.1', port), timeout=6)
    sock.settimeout(6)
    try:
        sock.sendall(b'\x05\x01\x00')
        if receive(sock, 2) != b'\x05\x00':
            raise ConnectionError('SOCKS authentication failed')
        domain = host.encode()
        sock.sendall(b'\x05\x01\x00\x03' + bytes([len(domain)]) + domain + struct.pack('!H', target_port))
        reply = receive(sock, 4)
        if reply[1] != 0:
            raise ConnectionError('SOCKS connection rejected')
        length = {1: 4, 4: 16}.get(reply[3])
        if reply[3] == 3:
            length = receive(sock, 1)[0]
        receive(sock, length + 2)
        return sock
    except BaseException:
        sock.close()
        raise


class Upstream(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            version, count = receive(self.request, 2)
            receive(self.request, count)
            self.request.sendall(b'\x05\x00')
            request = receive(self.request, 4)
            if request != b'\x05\x01\x00\x03':
                return
            host = receive(self.request, receive(self.request, 1)[0]).decode()
            receive(self.request, 2)
            self.server.seen.put(host)
            self.request.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x16SSH-2.0-route-test\r\n')
        except (OSError, ConnectionError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='vpn-groups-') as td, Upstream(('127.0.0.1', 0), Handler) as upstream:
        upstream.seen = queue.Queue()
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        try:
            state = Path(td)
            (state / 'bin').mkdir()
            for name in ['sing-box', 'xray']:
                (state / 'bin' / name).symlink_to(args.bin_dir.resolve() / name)
            academic = state / 'academic.txt'
            academic.write_text('journal.invalid\n')
            gateway_port = available_port()
            cases = []
            for pair in [('academic', 'cmu'), ('google', 'sheerid')]:
                for values in itertools.product([False, True], repeat=2):
                    groups = {key: False for key in avpn.group_definitions()}
                    groups.update(zip(pair, values))
                    cases.append((pair, groups))
            hosts = {'academic': 'journal.invalid', 'cmu': 'route-test.ece.local.cmu.edu',
                     'google': 'route-test.google.com', 'sheerid': 'route-test.sheerid.com'}
            for pair, groups in cases:
                domains = {key: hosts[key] for key in pair}
                a = avpn.resolve_args(avpn.parser().parse_args(['generate', '--state', str(state),
                    '--server', '127.0.0.1', '--port', str(gateway_port), '--mode', 'rules',
                    '--vpn-kind', 'socks', '--upstream-port', str(upstream.server_address[1]),
                    '--rules', str(academic), '--groups', json.dumps(groups)]))
                avpn.generate(a)  # Also validates every generated server/client with native binaries.
                for backend in ['sing-box', 'xray']:
                    config = state / 'config' / (backend + '-server.json')
                    data = json.loads(config.read_text())
                    data['inbounds'][0]['listen'] = '127.0.0.1'
                    config.write_text(json.dumps(data))
                    flags = ['run', '-c', str(config)] if backend == 'sing-box' else ['run', '-config', str(config)]
                    local = available_port()
                    c = json.loads((state / 'outputs/sing-box/client.json').read_text())
                    c['inbounds'][0]['listen_port'] = local
                    client_config = state / 'client.json'
                    client_config.write_text(json.dumps(c))
                    with (state / 'test.log').open('w') as log:
                        server = subprocess.Popen([str(state / 'bin' / backend), *flags], stdout=log, stderr=log)
                        client = subprocess.Popen([str(state / 'bin/sing-box'), 'run', '-c', str(client_config)], stdout=log, stderr=log)
                        try:
                            wait_port(gateway_port, server); wait_port(local, client)
                            for group, domain in domains.items():
                                banner = b''
                                try:
                                    with socks_connect(local, domain) as connection:
                                        banner = connection.recv(128)
                                except (OSError, ConnectionError):
                                    pass
                                assert (banner.startswith(b'SSH-2.0-route-test')) == groups[group], (backend, groups, group)
                                if groups[group]:
                                    assert upstream.seen.get(timeout=1) == domain, 'Hostname must reach upstream unchanged'
                                else:
                                    assert upstream.seen.empty(), 'Disabled group must not use VPN'
                            print(f'PASS {backend}: {groups}; hostname preserved')
                        finally:
                            client.terminate(); server.terminate()
                            client.wait(timeout=5); server.wait(timeout=5)
        finally:
            upstream.shutdown()


if __name__ == '__main__':
    main()
