"""Optional Linux host TUN, reusing the authenticated loopback VPN worker."""
import argparse
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse

SERVICE = 'academic-vpn-host.service'
INTERFACE = 'avpn-host'
DNS_ADDRESS = '198.18.0.2'
RESOLV = Path('/etc/resolv.conf')
RESOLVER_TEXT = '# Managed by academic-vpn-host; restored on stop.\nnameserver 198.18.0.2\noptions timeout:2 attempts:2\n'
MODES = [('off', '仅代理：不接管 Linux 本机网络'),
         ('rules', '本机分流：沿用学术 / CMU 等规则组'),
         ('all', '本机全局：TCP/UDP 全部走 VPN')]


def run(command, **kwargs):
    return subprocess.run([str(v) for v in command], check=True, **kwargs)


def active():
    return subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def current_mode(state):
    path = state / 'host/settings.json'
    return json.loads(path.read_text())['mode'] if path.exists() and active() else 'off'


def inspect_network(args):
    settings = json.loads((args.state / 'settings.json').read_text())
    if settings['vpn_kind'] != 'openconnect' or settings['mode'] == 'none':
        raise ValueError('本机接管需要已启用的内置 VPN；请先在快速调整中启用 VPN 并登录')
    status = run([args.state / 'bin/sing-box', 'api', '--url',
                  f"http://127.0.0.1:{settings['api_port']}", 'openconnect', 'status'],
                 capture_output=True, text=True).stdout
    if not re.search(r'^State:\s+connected\s*$', status, re.M):
        raise ValueError('VPN 尚未连接，请先从菜单登录 VPN，再开启本机接管')
    dns_line = re.search(r'^DNS:\s*(.+)$', status, re.M)
    if not dns_line:
        raise ValueError('VPN 未下发 DNS，无法配置本机解析')
    dns_server = str(ipaddress.ip_address(dns_line[1].split(',')[0].strip()))
    routes = json.loads(run(['ip', '-j', 'route', 'show', 'default'], capture_output=True, text=True).stdout)
    routes = [route for route in routes if route.get('dev') != INTERFACE]
    if not routes:
        raise ValueError('未找到物理网络的 IPv4 默认路由')
    interface = min(routes, key=lambda route: route.get('metric', 0))['dev']
    exclude = {'127.0.0.0/8', '::1/128', 'fe80::/10'}
    for family in ['-4', '-6']:
        routes = json.loads(run(['ip', family, '-j', 'route', 'show', 'dev', interface],
                                capture_output=True, text=True).stdout)
        for route in routes:
            if route.get('dst', 'default') != 'default' and not route.get('gateway'):
                exclude.add(str(ipaddress.ip_network(route['dst'], strict=False)))
    ssh = os.environ.get('SSH_CONNECTION', '').split()
    peers = [ssh[0]] if ssh else []
    sessions = run(['ss', '-Htnp', 'state', 'established'], capture_output=True, text=True).stdout
    for line in sessions.splitlines():
        fields = line.split()
        if 'sshd' in line and len(fields) >= 4:
            peers.append(fields[3].rsplit(':', 1)[0].strip('[]'))
    for peer in peers:
        address = ipaddress.ip_address(peer)
        address = getattr(address, 'ipv4_mapped', None) or address
        exclude.add(f'{address}/{address.max_prefixlen}')
    # Keep the physical VPN transport out of the TUN, including its bootstrap lookup.
    server = settings['vpn_server']
    hostname = urllib.parse.urlsplit(server if '://' in server else 'https://' + server).hostname
    for result in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(result[4][0]); exclude.add(f'{address}/{address.max_prefixlen}')
    return {'interface': interface, 'exclude': sorted(exclude), 'vpn_dns': dns_server,
            'vpn_hostname': hostname, 'vpn_port': settings['vpn_port']}


def build_config(kit, args, mode, network, netns=None):
    if mode not in {'rules', 'all'}:
        raise ValueError('无效本机接管模式')
    tun = {'type': 'tun', 'tag': 'host-tun', 'interface_name': INTERFACE,
           'address': ['198.18.0.1/30', 'fdfe:564e::1/126'],
           'mtu': 1400, 'stack': 'mixed', 'auto_route': True, 'auto_redirect': True,
           'strict_route': True, 'dns_mode': 'disabled',
           'iproute2_table_index': 18000, 'iproute2_rule_index': 18000,
           'auto_redirect_iproute2_fallback_rule_index': 33001,
           'auto_redirect_input_mark': '0x5641', 'auto_redirect_output_mark': '0x5642',
           'auto_redirect_reset_mark': '0x5643', 'auto_redirect_nfqueue': 181,
           'route_exclude_address': network['exclude']}
    if netns:
        tun['netns'] = netns
    rules = [{'port': 53, 'action': 'hijack-dns'}, {'action': 'sniff'},
             {'process_path': [str(args.state / 'bin/sing-box'), str(args.state / 'bin/xray')],
              'action': 'route', 'outbound': 'direct'},
             {'protocol': 'icmp', 'action': 'reject'}]
    # Explicitly preserve the VPN endpoint hostname, even in all/FakeIP mode.
    dns_rules = [{'domain': [network['vpn_hostname']], 'server': 'direct-dns'},
                 {'query_type': ['A', 'AAAA'], 'server': 'fakeip'}]
    final = 'vpn' if mode == 'all' else 'direct'
    if mode == 'rules':
        policy = {'default': 'direct', 'rules': []}
        if args.policy and args.policy != 'none':
            policy = json.loads(Path(args.policy).read_text())
        # Reuse policy validation and precedence from the gateway generator.
        sb = {'route': {'rules': [{'action': 'sniff'}, {'ip_is_private': True, 'action': 'reject'}]}}
        xr = {'routing': {'rules': [{'type': 'field', 'ip': ['127.0.0.0/8'], 'outboundTag': 'block'}]}}
        kit.apply_policy(sb, xr, policy, kit.selected_domains(args))
        for rule in sb['route']['rules'][1:-1]:
            rule = copy.deepcopy(rule)
            if rule.get('outbound') == 'vpn-proxy':
                rule['outbound'] = 'vpn'
            rules.append(rule)
            dns_rule = {key: value for key, value in rule.items() if key in {'domain', 'domain_suffix'}}
            if rule['action'] == 'reject':
                dns_rule['action'] = 'reject'
            else:
                dns_rule['server'] = 'vpn-dns' if rule['outbound'] == 'vpn' else 'direct-dns'
            dns_rules.append(dns_rule)
        final = 'vpn' if sb['route']['final'] == 'vpn-proxy' else 'direct'
    return {'log': {'level': 'warn'}, 'inbounds': [tun],
            'outbounds': [{'type': 'direct', 'tag': 'direct', 'bind_interface': network['interface']},
                          {'type': 'socks', 'tag': 'vpn', 'server': '127.0.0.1',
                           'server_port': network['vpn_port'], 'version': '5'}],
            'dns': {'servers': [
                {'type': 'udp', 'tag': 'direct-dns', 'server': '1.1.1.1', 'bind_interface': network['interface']},
                {'type': 'tcp', 'tag': 'vpn-dns', 'server': network['vpn_dns'], 'detour': 'vpn'},
                {'type': 'fakeip', 'tag': 'fakeip', 'inet4_range': '198.19.0.0/16', 'inet6_range': 'fc00:564e::/48'}],
                'rules': dns_rules, 'final': 'vpn-dns' if final == 'vpn' else 'direct-dns'},
            'route': {'rules': rules, 'final': final, 'default_interface': network['interface'],
                      'default_domain_resolver': 'direct-dns'}}


def dns_on(kit, state):
    backup = state / 'host/resolver-backup.json'
    if backup.exists():
        raise ValueError('上次 DNS 备份尚未恢复，请先关闭本机接管')
    if RESOLV.is_symlink():
        original = {'symlink': os.readlink(RESOLV)}
    else:
        original = {'text': RESOLV.read_text(), 'mode': RESOLV.stat().st_mode & 0o777}
    kit.dump(backup, original)
    # Atomic replacement of the link itself; never edit systemd-resolved's target.
    fd, name = tempfile.mkstemp(prefix='.avpn-dns-', dir=RESOLV.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(RESOLVER_TEXT)
        os.chmod(name, 0o644)
        os.replace(name, RESOLV)
    finally:
        Path(name).unlink(missing_ok=True)


def dns_off(state):
    backup = state / 'host/resolver-backup.json'
    if not backup.exists():
        return
    original = json.loads(backup.read_text())
    unchanged = (RESOLV.is_symlink() and original.get('symlink') == os.readlink(RESOLV))
    unchanged = unchanged or (not RESOLV.is_symlink() and original.get('text') == RESOLV.read_text())
    if not unchanged and RESOLV.read_text() != RESOLVER_TEXT:
        raise ValueError('系统 DNS 被其他程序修改，保留现场和 host/resolver-backup.json；未覆盖外部修改')
    if not unchanged:
        fd, name = tempfile.mkstemp(prefix='.avpn-dns-', dir=RESOLV.parent)
        os.close(fd)
        try:
            if 'symlink' in original:
                Path(name).unlink()
                os.symlink(original['symlink'], name)
            else:
                Path(name).write_text(original['text'])
                os.chmod(name, original['mode'])
            os.replace(name, RESOLV)
        finally:
            Path(name).unlink(missing_ok=True)
    backup.unlink()
    if shutil.which('resolvectl'):
        subprocess.run(['resolvectl', 'flush-caches'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def unit_text(kit, state):
    state = kit.systemd_path(state)
    script = kit.systemd_path(kit.ROOT / 'scripts/host_network.py')
    return f'''[Unit]
Description=Academic VPN Linux host routing
After=network-online.target academic-vpn-vpn.service

[Service]
Type=simple
UMask=0077
ExecStart={state}/bin/sing-box run -D {state}/host -c {state}/host/config.json
ExecStartPost=/usr/bin/python3 {script} dns-on {state}
ExecStopPost=/usr/bin/python3 {script} cleanup {state}
TimeoutStartSec=20
TimeoutStopSec=15
KillSignal=SIGTERM
NoNewPrivileges=true
'''


def recovery_unit_text(kit, state):
    state = kit.systemd_path(state)
    script = kit.systemd_path(kit.ROOT / 'scripts/host_network.py')
    return f'''[Unit]
Description=Restore academic VPN host DNS after an interrupted shutdown
DefaultDependencies=no
After=local-fs.target
Before=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {script} cleanup {state}

[Install]
WantedBy=multi-user.target
'''


def owned_unit(kit, state):
    path = kit.UNIT_DIR / SERVICE
    if path.exists() and path.read_text() != unit_text(kit, state):
        raise ValueError('本机网络服务属于其他部署目录')
    return path


def cleanup(state):
    # The interface and auto-routes normally disappear on graceful stop. Clean up
    # our reserved table/marks after a crash as well; ownership is recorded first.
    marker = state / 'host/network-owned.json'
    if marker.exists():
        for family in ['-4', '-6']:
            result = subprocess.run(['ip', family, '-j', 'rule', 'show'], capture_output=True, text=True)
            if result.returncode == 0:
                for rule in json.loads(result.stdout):
                    priority = rule.get('priority', -1)
                    # sing-tun's auto_redirect also allocates a random table at
                    # priority 1 for loopback redirect routes on physical links.
                    # We reserve this priority before starting, too. SIGKILL
                    # bypasses the native cleanup of these auxiliary tables.
                    table = str(rule.get('table', ''))
                    if priority == 1 and table.isdecimal() and int(table) > 255:
                        subprocess.run(['ip', family, 'rule', 'del', 'pref', '1', 'table', table],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        subprocess.run(['ip', family, 'route', 'flush', 'table', table],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    elif 18000 <= priority < 18020 or priority == 33001:
                        subprocess.run(['ip', family, 'rule', 'del', 'pref', str(priority)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['ip', family, 'route', 'flush', 'table', '18000'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['nft', 'delete', 'table', 'inet', 'sing-box'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        marker.unlink()
    dns_off(state)


def stop(kit, args):
    path = owned_unit(kit, args.state)
    if path.exists():
        run(['systemctl', 'stop', SERVICE])
    cleanup(args.state)
    kit.dump(args.state / 'host/settings.json', {'mode': 'off'})
    (args.state / 'host/resume.json').unlink(missing_ok=True)
    print('本机网络接管已关闭，原 DNS 和普通出口已恢复；代理节点继续运行。')


def verify_free_network_slots():
    if Path('/sys/class/net/' + INTERFACE).exists():
        raise ValueError('本机 TUN 接口名称已被占用')
    tables = json.loads(run(['nft', '-j', 'list', 'tables'], capture_output=True, text=True).stdout)
    if any(item.get('table', {}).get('name') == 'sing-box' for item in tables.get('nftables', [])):
        raise ValueError('已有其他 sing-box TUN 使用 nftables；请先关闭它')
    for family in ['-4', '-6']:
        rules = json.loads(run(['ip', family, '-j', 'rule', 'show'], capture_output=True, text=True).stdout)
        if any(18000 <= r.get('priority', -1) < 18020 or r.get('priority') in {1, 33001} for r in rules):
            raise ValueError('本机接管预留的策略路由优先级已被其他程序使用')
        routes = subprocess.run(
            ['ip', family, 'route', 'show', 'table', '18000'], capture_output=True, text=True)
        if routes.stdout.strip():
            raise ValueError('本机接管预留路由表已被占用')


def apply(kit, args, mode):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('本机网络接管需要 Linux root')
    if mode == 'off':
        stop(kit, args)
        return
    for command in ['ip', 'ss', 'nft', 'systemctl', 'systemd-run', 'curl']:
        if not shutil.which(command):
            raise ValueError('缺少命令 ' + command + '；需要 iproute2、nftables、systemd 和 curl')
    if not Path('/dev/net/tun').exists():
        raise ValueError('系统没有 /dev/net/tun')
    owned_unit(kit, args.state)
    recovery_unit = kit.UNIT_DIR / 'academic-vpn-host-recover.service'
    if recovery_unit.exists() and recovery_unit.read_text() != recovery_unit_text(kit, args.state):
        raise ValueError('本机 DNS 恢复服务属于其他部署')
    was_active = active()
    if was_active:
        stop(kit, args)
    network = inspect_network(args)
    config = build_config(kit, args, mode, network)
    vpn_exit = run(['curl', '-fsS', '--noproxy', '', '--max-time', '20',
                    '--proxy', f"socks5h://127.0.0.1:{network['vpn_port']}", 'https://api.ipify.org'],
                   capture_output=True, text=True).stdout.strip()
    ipaddress.ip_address(vpn_exit)
    directory = args.state / 'host'
    kit.dump(directory / 'config.json', config)
    run([args.state / 'bin/sing-box', 'check', '-c', directory / 'config.json'])
    verify_free_network_slots()
    kit.write(kit.UNIT_DIR / SERVICE, unit_text(kit, args.state))
    kit.write(recovery_unit, recovery_unit_text(kit, args.state))
    kit.dump(directory / 'network-owned.json', {'interface': INTERFACE, 'table': 18000})
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', 'academic-vpn-host-recover.service'])
    # A timed rollback still executes if a remote shell is lost during activation.
    timer = 'academic-vpn-host-rollback-' + str(os.getpid())
    run(['systemd-run', '--quiet', '--unit', timer, '--on-active=90s',
         '/usr/bin/systemctl', 'stop', SERVICE])
    try:
        run(['systemctl', 'start', SERVICE])
        run(['systemctl', 'is-active', '--quiet', SERVICE])
        host_exit = run(['curl', '-fsS', '--noproxy', '*', '--max-time', '20', 'https://api.ipify.org'],
                        capture_output=True, text=True).stdout.strip()
        if mode == 'all' and host_exit != vpn_exit:
            raise ValueError('本机出口未进入 VPN，已取消网络接管')
        kit.dump(directory / 'settings.json', {'mode': mode, 'network': network})
    except BaseException:
        subprocess.run(['systemctl', 'stop', SERVICE])
        cleanup(args.state)
        kit.dump(directory / 'settings.json', {'mode': 'off'})
        raise
    finally:
        subprocess.run(['systemctl', 'stop', timer + '.timer'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('本机网络已切换：' + dict(MODES)[mode])
    print('关闭入口：sudo ' + str(kit.ROOT / 'entrypoint.sh') + ' → 本机网络 → 仅代理')


def pause_for_deploy(kit, args):
    mode = current_mode(args.state)
    if mode != 'off':
        stop(kit, args)
        if args.mode != 'none' and args.vpn_kind == 'openconnect':
            kit.dump(args.state / 'host/resume.json', {'mode': mode})
        else:
            (args.state / 'host/resume.json').unlink(missing_ok=True)


def resume_after_auth(kit, args):
    pending = args.state / 'host/resume.json'
    if pending.exists():
        mode = json.loads(pending.read_text())['mode']
        apply(kit, args, mode)
        pending.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['dns-on', 'cleanup'])
    parser.add_argument('state', type=Path)
    a = parser.parse_args()
    import avpn
    if a.command == 'dns-on':
        for _ in range(100):
            if Path('/sys/class/net/' + INTERFACE).exists():
                break
            time.sleep(.05)
        else:
            raise ValueError('本机 TUN 未成功创建')
        dns_on(avpn, a.state)
    else:
        cleanup(a.state)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print('本机网络错误: ' + str(error), file=sys.stderr)
        sys.exit(1)
