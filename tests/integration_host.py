#!/usr/bin/env python3
"""Host TUN integration inside a disposable namespace; host routes/DNS stay untouched."""
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import avpn
import host_network as hn


def main():
    args = avpn.resolve_args(avpn.parser().parse_args(['status', *sys.argv[1:]]))
    network = hn.inspect_network(args)
    ns = 'avpn-test-' + str(os.getpid())
    resolver_dir = Path('/etc/netns') / ns
    def nsrun(command):
        return subprocess.check_output(['ip', 'netns', 'exec', ns, *command], text=True).strip()
    def ip(url, proxy=None):
        command = ['curl', '-fsS', '--max-time', '15', '--noproxy', '' if proxy else '*']
        if proxy: command += ['--proxy', proxy]
        return subprocess.check_output(command + [url], text=True).strip()
    direct = ip('https://api.ipify.org')
    vpn = ip('https://api.ipify.org', f"socks5h://127.0.0.1:{network['vpn_port']}")
    assert direct != vpn, 'Need distinct exits'
    subprocess.run(['ip', 'netns', 'add', ns], check=True)
    try:
        subprocess.run(['ip', '-n', ns, 'link', 'set', 'lo', 'up'], check=True)
        original_rules = {family: nsrun(['ip', family, '-j', 'rule', 'show']) for family in ['-4', '-6']}
        resolver_dir.mkdir(parents=True)
        (resolver_dir / 'resolv.conf').write_text('nameserver ' + hn.DNS_ADDRESS + '\n')
        with tempfile.TemporaryDirectory(prefix='avpn-host-test-') as td:
            temp = Path(td)
            local_args = copy.copy(args)
            local_args.groups = {'academic': True, 'cmu': True}
            local_args.rules = temp / 'domains.txt'
            local_args.rules.write_text('api.ipify.org\n')
            local_args.policy = None
            unavailable = socket.socket()
            unavailable.bind(('127.0.0.1', 0))  # Reserved, but not listening.
            for mode in ['rules', 'all', 'unavailable']:
                config = hn.build_config(avpn, local_args, 'all' if mode == 'unavailable' else mode, network, netns=ns)
                if mode == 'unavailable':
                    config['outbounds'][1]['server_port'] = unavailable.getsockname()[1]
                config['log']['level'] = 'debug'
                path = temp / 'host.json'; path.write_text(json.dumps(config))
                with (temp / 'host.log').open('w') as log:
                    process = subprocess.Popen([str(args.state / 'bin/sing-box'), 'run', '-c', str(path)], stdout=log, stderr=log)
                    try:
                        for _ in range(50):
                            if process.poll() is not None:
                                raise RuntimeError((temp / 'host.log').read_text())
                            links = nsrun(['ip', '-j', 'link', 'show'])
                            if hn.INTERFACE in links: break
                            time.sleep(.1)
                        time.sleep(.3)
                        if mode == 'unavailable':
                            result = subprocess.run(['ip', 'netns', 'exec', ns, 'curl', '-fsS',
                                '--max-time', '5', '--noproxy', '*', 'https://api.ipify.org'],
                                capture_output=True, text=True)
                            assert result.returncode != 0 and not result.stdout.strip(), result
                            print('PASS unavailable VPN does not fall back to direct', flush=True)
                            continue
                        for url, expected in [('https://api.ipify.org', vpn),
                                              ('https://checkip.amazonaws.com', vpn if mode == 'all' else direct)]:
                            result = nsrun(['curl', '-fsS', '--max-time', '20', '--noproxy', '*', url])
                            assert result == expected, (mode, url, result, expected)
                        banner = nsrun(['/usr/bin/python3', '-c',
                            "import socket; s=socket.create_connection(('ece005.ece.local.cmu.edu',22),timeout=10); s.sendall(b'SSH-2.0-ConnectivityProbe' + bytes([13,10])); print(s.recv(128).decode().strip());s.close()"])
                        assert banner.startswith('SSH-2.0-'), banner
                        if mode == 'all':
                            probe = """import socket,struct,os
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(10)
transaction=os.urandom(12)
s.sendto(struct.pack('!HHI12s',1,0,0x2112a442,transaction),('stun.l.google.com',19302))
data=s.recv(2048);s.close()
assert data[:2]==bytes([1,1]) and data[8:20]==transaction
pos=20
while pos+4<=len(data):
 kind,length=struct.unpack('!HH',data[pos:pos+4]);value=data[pos+4:pos+4+length]
 if kind==0x20 and value[1]==1:
  address=bytes(a^b for a,b in zip(value[4:8],bytes.fromhex('2112a442')))
  print(socket.inet_ntoa(address));break
 pos+=4+((length+3)//4)*4
else:raise AssertionError('No XOR mapped address')
"""
                            assert nsrun(['/usr/bin/python3', '-c', probe]) == vpn
                            print('PASS raw UDP STUN uses VPN exit', flush=True)
                        print('PASS Linux namespace ' + mode + ': raw curl exits, DNS and ECE SSH without proxy', flush=True)
                    except BaseException:
                        print((temp / 'host.log').read_text()[-6000:], file=sys.stderr)
                        raise
                    finally:
                        process.terminate(); process.wait(timeout=10)
                assert hn.INTERFACE not in nsrun(['ip', '-j', 'link', 'show'])
                for family, original in original_rules.items():
                    assert nsrun(['ip', family, '-j', 'rule', 'show']) == original
                print('PASS host TUN interface and policy routes removed after stop', flush=True)
            unavailable.close()
    finally:
        shutil.rmtree(resolver_dir, ignore_errors=True)
        subprocess.run(['ip', 'netns', 'del', ns], check=True)


if __name__ == '__main__':
    main()
