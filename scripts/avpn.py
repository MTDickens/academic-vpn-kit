#!/usr/bin/env python3
"""Academic VPN deployment and private artifact generation; no credentials in source."""
import argparse
import base64
import copy
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
import vpn_health

ROOT = Path(__file__).resolve().parents[1]
SB_VERSION = '1.14.2'
XR_VERSION = '26.3.27'
DEFAULT_STATE = Path.home() / '.local/share/academic-vpn'
UNIT_DIR = Path('/etc/systemd/system')
FLOW = 'xtls-rprx-vision'


def run(cmd, **kwargs):
    return subprocess.run([str(x) for x in cmd], check=True, **kwargs)


def private_dir(p):
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.chmod(0o700)


def write(p, content):
    private_dir(p.parent)
    # Atomic replacement, never follow an existing file symlink.
    fd, tmp = tempfile.mkstemp(dir=p.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def dump(p, obj):
    write(p, json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'academic-vpn-kit'})
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read()


def jsonc(text):
    # Keep // inside JSON strings (URLs) and support block comments/trailing commas.
    pat = r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*[\s\S]*?\*/'
    clean = re.sub(pat, lambda m: m.group(1) or '', text)
    clean = re.sub(r'("(?:\\.|[^"\\])*")|,(\s*[}\]])',
                   lambda m: m.group(1) if m.group(1) else m.group(2), clean)
    return json.loads(clean)


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def public_key(private):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    raw = base64.urlsafe_b64decode(private + '=' * (-len(private) % 4))
    return b64(X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw))


def validate_identity(d):
    uuid.UUID(d['uuid'])
    if public_key(d['private_key']) != d['public_key']:
        raise ValueError('REALITY 公私钥不匹配')
    if not re.fullmatch(r'(?:[a-fA-F0-9]{2}){1,8}', d['short_id']):
        raise ValueError('short-id 必须为 2–16 个偶数位十六进制字符')
    host = d['server']
    if not host or any(c.isspace() for c in host):
        raise ValueError('无效 server')
    for key in ('server', 'sni', 'handshake_host'):
        value = d[key]
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', value):
                raise ValueError('无效主机名: ' + key)
    if not 0 < int(d['port']) < 65536:
        raise ValueError('无效端口')
    if not 0 < int(d['handshake_port']) < 65536:
        raise ValueError('无效 REALITY 目标端口')


def identity(args):
    p = args.state / 'identity.json'
    if p.exists():
        d = json.loads(p.read_text())
        if args.import_xray:
            raise ValueError('身份已存在；请使用新的 --state 目录导入，避免覆盖凭据')
    elif args.import_xray:
        source = jsonc(Path(args.import_xray).read_text())
        inbound = next(x for x in source['inbounds'] if x.get('protocol') == 'vless'
                       and x.get('streamSettings', {}).get('security') == 'reality')
        clients = inbound['settings']['clients']
        if len(clients) != 1:
            raise ValueError('自动导入只支持单个 VLESS 用户，多个用户请先选择并导出')
        client = clients[0]
        if client.get('flow') != FLOW:
            raise ValueError('导入要求 xtls-rprx-vision')
        reality = inbound['streamSettings']['realitySettings']
        dest = str(reality.get('target', reality.get('dest', '')))
        host, sep, port = dest.rpartition(':')
        if not sep:
            raise ValueError('无法识别 REALITY handshake 目标')
        d = {'uuid': client['id'], 'private_key': reality['privateKey'],
             'public_key': public_key(reality['privateKey']),
             'short_id': next(x for x in reality['shortIds'] if x),
             'sni': reality['serverNames'][0], 'server': args.server,
             'port': args.port if args.port is not None else inbound['port'], 'handshake_host': host.strip('[]'),
             'handshake_port': int(port)}
    else:
        if not args.server:
            raise ValueError('首次生成需要 --server 公网IP或域名')
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
        private = b64(X25519PrivateKey.generate().private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
        d = {'uuid': str(uuid.uuid4()), 'private_key': private,
             'public_key': public_key(private), 'short_id': secrets.token_hex(8),
             'sni': args.sni or 'learn.microsoft.com', 'server': args.server,
             'port': args.port if args.port is not None else 443,
             'handshake_host': args.sni or 'learn.microsoft.com', 'handshake_port': 443}
    if args.server:
        d['server'] = args.server
    if args.port is not None:
        d['port'] = args.port
    if args.sni:
        d['sni'] = d['handshake_host'] = args.sni
    validate_identity(d)
    dump(p, d)
    return d


def domains(path):
    suffix, exact = set(), set()
    for line in Path(path).read_text().splitlines():
        v = line.split('#', 1)[0].strip().lower()
        if not v:
            continue
        target = exact if v.startswith('full:') else suffix
        v = v.removeprefix('full:')
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', v):
            raise ValueError('域名规则不支持此语法: ' + v)
        target.add(v)
    if not suffix and not exact:
        raise ValueError('学术名单不能为空')
    return sorted(suffix), sorted(exact)


def group_definitions():
    return json.loads((ROOT / 'rules/groups.json').read_text())


def resolve_groups(groups=None):
    definitions = group_definitions()
    if groups is None:
        groups = {}
    if not isinstance(groups, dict) or set(groups) - set(definitions):
        raise ValueError('分流开关包含未知规则组')
    if any(type(value) is not bool for value in groups.values()):
        raise ValueError('分流开关必须是 true/false')
    return {key: groups.get(key, definition['default']) for key, definition in definitions.items()}


def selected_domains(args):
    suffix, exact = set(), set()
    for key, definition in group_definitions().items():
        if not args.groups[key]:
            continue
        if 'file' in definition:
            path = args.rules if definition['file'] == '$academic' else ROOT / 'rules' / definition['file']
            group_suffix, group_exact = domains(path)
            suffix.update(group_suffix); exact.update(group_exact)
        for domain in definition.get('domains', []):
            value = domain.removeprefix('full:').lower()
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value):
                raise ValueError('无效规则组域名: ' + key)
            (exact if domain.startswith('full:') else suffix).add(value)
    return sorted(suffix), sorted(exact)


def auth_domains(args):
    """Public VPN login dependencies must work before the tunnel is available."""
    if args.vpn_kind != 'openconnect' or not args.vpn_server:
        return {}
    url = args.vpn_server if '://' in args.vpn_server else 'https://' + args.vpn_server
    hostname = urllib.parse.urlsplit(url).hostname
    if not hostname:
        raise ValueError('VPN 网关缺少主机名')
    providers = json.loads((ROOT / 'rules/vpn-auth.json').read_text())
    entry = providers.get(hostname, {})
    if set(entry) - {'domain', 'domain_suffix'}:
        raise ValueError('认证域名配置只支持 domain 和 domain_suffix')
    result = {}
    for key in ['domain', 'domain_suffix']:
        values = entry.get(key, [])
        if not isinstance(values, list) or any(not isinstance(x, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', x) for x in values):
            raise ValueError('无效认证域名列表: ' + hostname)
        result[key] = sorted(set(values + ([hostname] if key == 'domain' else [])))
    return {key: value for key, value in result.items() if value}


def apply_auth_routes(sb, xr, args):
    domains = auth_domains(args)
    if not domains or args.mode == 'none':
        return
    sb['route']['rules'].insert(1, {**domains, 'action': 'route', 'outbound': 'direct'})
    xr['routing']['rules'].insert(0, {'type': 'field', 'domain':
        ['full:' + x for x in domains.get('domain', [])] +
        ['domain:' + x for x in domains.get('domain_suffix', [])], 'outboundTag': 'direct'})


def apply_policy(sb, xr, policy, selected=((), ())):
    """Ordered hostname exceptions; same first-match semantics on both backends."""
    if set(policy) != {'default', 'rules'} or policy['default'] not in {'direct', 'vpn'}:
        raise ValueError('policy 需要 rules 数组和 default: direct/vpn')
    if not isinstance(policy['rules'], list):
        raise ValueError('policy.rules 必须为数组')
    # Match explicit domains before rejecting literal private IP destinations.
    # SOCKS5 forwards the hostname to the VPN worker for internal DNS resolution.
    sb_private, xr_private = sb['route']['rules'][1], xr['routing']['rules'][0]
    sb_rules, xr_rules = sb['route']['rules'][:1], []
    for rule in policy['rules']:
        if set(rule) != {'domains', 'outbound'} or rule['outbound'] not in {'direct', 'vpn', 'block'}:
            raise ValueError('policy 每条规则需要 domains 和 outbound: direct/vpn/block')
        if not isinstance(rule['domains'], list) or not rule['domains']:
            raise ValueError('policy domains 必须为非空数组')
        suffix, exact = [], []
        for domain in rule['domains']:
            if not isinstance(domain, str):
                raise ValueError('policy 域名必须为字符串')
            value = domain.removeprefix('full:').lower()
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value):
                raise ValueError('policy 域名语法不支持')
            (exact if domain.startswith('full:') else suffix).append(value)
        sr = {'domain_suffix': suffix, 'domain': exact}
        tag = {'direct': 'direct', 'vpn': 'vpn-proxy', 'block': 'block'}[rule['outbound']]
        if tag == 'block':
            sr['action'] = 'reject'
        else:
            sr.update({'action': 'route', 'outbound': tag})
        sb_rules.append(sr)
        xr_rules.append({'type': 'field', 'domain': ['domain:' + x for x in suffix] +
                         ['full:' + x for x in exact], 'outboundTag': tag})
    suffix, exact = selected
    if suffix or exact:
        sb_rules.append({'domain_suffix': list(suffix), 'domain': list(exact),
                         'action': 'route', 'outbound': 'vpn-proxy'})
        xr_rules.append({'type': 'field', 'domain': ['domain:' + x for x in suffix] +
                         ['full:' + x for x in exact], 'outboundTag': 'vpn-proxy'})
    sb_rules.append(sb_private)
    xr_rules.append(xr_private)
    default = 'direct' if policy['default'] == 'direct' else 'vpn-proxy'
    sb['route'].update({'rules': sb_rules, 'final': default})
    sb['route'].pop('rule_set', None)
    xr_rules.append({'type': 'field', 'network': 'tcp,udp', 'outboundTag': default})
    xr['routing']['rules'] = xr_rules


def share(d, name):
    host = '[' + d['server'] + ']' if ':' in d['server'] else d['server']
    query = urllib.parse.urlencode({'encryption': 'none', 'security': 'reality',
        'sni': d['sni'], 'fp': 'chrome', 'pbk': d['public_key'],
        'sid': d['short_id'], 'type': 'tcp', 'flow': FLOW})
    return f"vless://{d['uuid']}@{host}:{d['port']}?{query}#{urllib.parse.quote(name)}"


def clash_node(d, name):
    q = lambda s: "'" + str(s).replace("'", "''") + "'"
    return '\n'.join([
        "  - type: 'vless'", f'    name: {q(name)}', f"    server: {q(d['server'])}",
        f"    port: {d['port']}", f"    uuid: {q(d['uuid'])}", '    tls: true',
        f"    servername: {q(d['sni'])}", f'    flow: {q(FLOW)}',
        "    client-fingerprint: 'chrome'", '    reality-opts:',
        f"      public-key: {q(d['public_key'])}", f"      short-id: {q(d['short_id'])}",
        "    network: 'tcp'", '    udp: true', ''])


def sb_client(d):
    return {'log': {'level': 'info'},
        'inbounds': [{'type': 'mixed', 'tag': 'local', 'listen': '127.0.0.1', 'listen_port': 2080}],
        'outbounds': [{'type': 'vless', 'tag': 'server', 'server': d['server'],
            'server_port': d['port'], 'uuid': d['uuid'], 'flow': FLOW,
            'tls': {'enabled': True, 'server_name': d['sni'],
                'utls': {'enabled': True, 'fingerprint': 'chrome'},
                'reality': {'enabled': True, 'public_key': d['public_key'], 'short_id': d['short_id']}}}],
        'route': {'final': 'server'}}


def xr_client(d):
    return {'log': {'loglevel': 'warning'},
        'inbounds': [{'tag': 'local', 'listen': '127.0.0.1', 'port': 2080,
                     'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': True}}],
        'outbounds': [{'tag': 'server', 'protocol': 'vless',
            'settings': {'vnext': [{'address': d['server'], 'port': d['port'],
                'users': [{'id': d['uuid'], 'encryption': 'none', 'flow': FLOW}]}]},
            'streamSettings': {'network': 'tcp', 'security': 'reality',
                'realitySettings': {'serverName': d['sni'], 'fingerprint': 'chrome',
                    'publicKey': d['public_key'], 'shortId': d['short_id'], 'spiderX': '/'}}}]}


@vpn_health.serialized
def generate(args):
    """Build in a private staging directory; failed generation leaves existing files intact."""
    private_dir(args.state)
    final = args.state
    with tempfile.TemporaryDirectory(prefix='.generate-', dir=final.parent) as td:
        stage = Path(td)
        staged_args = copy.copy(args)
        staged_args.state = stage
        if (final / 'rules').exists():
            shutil.copytree(final / 'rules', stage / 'rules')
        for name in ['identity.json', 'upstream-secrets.json']:
            source = final / name
            if source.exists():
                private_dir((stage / name).parent)
                shutil.copyfile(source, stage / name)
                (stage / name).chmod(0o600)
        credentials = getattr(args, 'upstream_credentials', None)
        if credentials is not None:
            if credentials:
                dump(stage / 'upstream-secrets.json', credentials)
            else:
                (stage / 'upstream-secrets.json').unlink(missing_ok=True)
        if args.policy == 'none':
            (stage / 'rules/policy.json').unlink(missing_ok=True)
        _generate(staged_args)
        # Validate against staging paths before replacing the previous output.
        bins = final / 'bin'
        if all((bins / b).exists() for b in ['sing-box', 'xray']):
            check(staged_args, bin_dir=bins)
        for folder in ['config', 'outputs']:
            for p in (stage / folder).rglob('*.json'):
                def remap(value):
                    if isinstance(value, dict):
                        return {k: remap(v) for k, v in value.items()}
                    if isinstance(value, list):
                        return [remap(v) for v in value]
                    if isinstance(value, str) and value.startswith(str(stage) + '/'):
                        return str(final) + value[len(str(stage)):]
                    return value
                dump(p, remap(json.loads(p.read_text())))
        names = ['identity.json', 'settings.json', 'rules', 'config', 'outputs']
        if credentials is not None:
            names.append('upstream-secrets.json')
        backup = stage / '.previous'
        private_dir(backup)
        moved, published = [], []
        try:
            for name in names:
                if (final / name).exists():
                    os.replace(final / name, backup / name)
                    moved.append(name)
                if (stage / name).exists():
                    os.replace(stage / name, final / name)
                    published.append(name)
        except BaseException:
            for name in reversed(published):
                p = final / name
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            for name in reversed(moved):
                os.replace(backup / name, final / name)
            raise
    print('已生成两套私人产物：' + str(final / 'outputs'))
    print('两套使用相同节点身份，是可替换的服务端方案；不要同时占用同一端口。')


def _generate(args):
    private_dir(args.state)
    if args.mode != 'none' and args.vpn_kind == 'openconnect' and not args.vpn_server:
        raise ValueError('启用 OpenConnect 时需要 --vpn-server；CMU 也应显式指定')
    d = identity(args)
    if d['port'] in {args.vpn_port, args.api_port} and args.mode != 'none' and args.vpn_kind == 'openconnect':
        raise ValueError('入口和 VPN 本地端口冲突')
    args.groups = resolve_groups(getattr(args, 'groups', None))
    suffix, exact = selected_domains(args) if args.mode == 'rules' else ([], [])
    rules_path = args.state / 'rules/scholar.json'
    dump(rules_path, {'version': 3, 'rules': [{'domain_suffix': suffix, 'domain': exact}]})
    if Path(args.rules).exists():
        write(args.state / 'rules/domains.txt', Path(args.rules).read_text())
    dump(args.state / 'settings.json', {'vpn_server': args.vpn_server, 'auth_group': args.auth_group,
        'vpn_port': args.vpn_port, 'api_port': args.api_port, 'mode': args.mode,
        'vpn_kind': args.vpn_kind, 'vpn_flavor': args.vpn_flavor,
        'upstream_host': args.upstream_host, 'upstream_port': args.upstream_port,
        'backend': args.backend or 'sing-box', 'groups': args.groups})
    vpn = {'log': {'level': 'info', 'timestamp': True},
        'inbounds': [{'type': 'socks', 'tag': 'vpn-socks', 'listen': '127.0.0.1', 'listen_port': args.vpn_port}],
        'endpoints': [{'type': 'openconnect', 'tag': 'upstream-vpn', 'system': False,
            'server': args.vpn_server or 'https://vpn.example.edu', 'flavor': args.vpn_flavor,
            'auth_group': args.auth_group, 'domain_resolver': 'bootstrap'}],
        'dns': {'servers': [{'type': 'local', 'tag': 'bootstrap'},
                           {'type': 'openconnect', 'tag': 'upstream-dns', 'endpoint': 'upstream-vpn',
                            'accept_default_resolvers': True}], 'final': 'upstream-dns'},
        'route': {'final': 'upstream-vpn', 'default_domain_resolver': 'upstream-dns'},
        'services': [{'type': 'api', 'listen': '127.0.0.1', 'listen_port': args.api_port, 'dashboard': False}]}
    if args.vpn_kind == 'openconnect' and args.mode != 'none':
        dump(args.state / 'config/vpn.json', vpn)
    elif (args.state / 'config/vpn.json').exists():
        (args.state / 'config/vpn.json').unlink()
    upstream_host = '127.0.0.1' if args.vpn_kind == 'openconnect' else args.upstream_host
    upstream_port = args.vpn_port if args.vpn_kind == 'openconnect' else args.upstream_port
    sb = {'log': {'level': 'info', 'timestamp': True},
        'inbounds': [{'type': 'vless', 'tag': 'vless-in', 'listen': '::', 'listen_port': d['port'],
            'users': [{'name': 'owner', 'uuid': d['uuid'], 'flow': FLOW}],
            'tls': {'enabled': True, 'server_name': d['sni'], 'reality': {'enabled': True,
                'handshake': {'server': d['handshake_host'], 'server_port': d['handshake_port']},
                'private_key': d['private_key'], 'short_id': [d['short_id']]}}}],
        'outbounds': [{'type': 'direct', 'tag': 'direct'},
            {'type': 'socks', 'tag': 'vpn-proxy', 'server': upstream_host, 'server_port': upstream_port}],
        'route': {'rules': [{'action': 'sniff'}, {'ip_is_private': True, 'action': 'reject'},
            {'rule_set': ['selected'], 'action': 'route', 'outbound': 'vpn-proxy'}],
            'rule_set': [{'type': 'local', 'tag': 'selected', 'format': 'source', 'path': str(rules_path)}],
            'final': 'direct'}}
    xr = {'log': {'loglevel': 'warning'},
        'inbounds': [{'tag': 'vless-in', 'listen': '::', 'port': d['port'], 'protocol': 'vless',
            'settings': {'clients': [{'id': d['uuid'], 'flow': FLOW}], 'decryption': 'none'},
            'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': {
                'show': False, 'target': (f"[{d['handshake_host']}]:{d['handshake_port']}" if ':' in d['handshake_host'] else f"{d['handshake_host']}:{d['handshake_port']}"),
                'xver': 0, 'serverNames': [d['sni']], 'privateKey': d['private_key'], 'shortIds': [d['short_id']]}},
            'sniffing': {'enabled': True, 'destOverride': ['http', 'tls', 'quic'], 'routeOnly': True}}],
        'outbounds': [{'tag': 'direct', 'protocol': 'freedom'},
            {'tag': 'vpn-proxy', 'protocol': 'socks', 'settings': {'servers': [{'address': upstream_host, 'port': upstream_port}]}},
            {'tag': 'block', 'protocol': 'blackhole'}],
        'routing': {'domainStrategy': 'AsIs', 'rules': [
            {'type': 'field', 'ip': ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
                '127.0.0.0/8', '169.254.0.0/16', '::1/128', 'fc00::/7', 'fe80::/10'], 'outboundTag': 'block'},
            {'type': 'field', 'domain': ['domain:' + x for x in suffix] + ['full:' + x for x in exact],
             'outboundTag': 'vpn-proxy'}]}}
    if args.mode == 'none':
        sb['outbounds'] = [sb['outbounds'][0]]
        sb['route']['rules'] = sb['route']['rules'][:2]
        sb['route'].pop('rule_set')
        xr['outbounds'] = [x for x in xr['outbounds'] if x['tag'] != 'vpn-proxy']
        xr['routing']['rules'] = xr['routing']['rules'][:1]
    elif args.mode == 'all':
        sb['route']['rules'] = sb['route']['rules'][:2]
        sb['route'].pop('rule_set')
        sb['route']['final'] = 'vpn-proxy'
        xr['routing']['rules'][1] = {'type': 'field', 'network': 'tcp,udp', 'outboundTag': 'vpn-proxy'}
    if args.mode == 'rules':
        policy = {'default': 'direct', 'rules': []}
        if args.policy and args.policy != 'none':
            policy = json.loads(Path(args.policy).read_text())
            dump(args.state / 'rules/policy.json', policy)
        apply_policy(sb, xr, policy, (suffix, exact))
    apply_auth_routes(sb, xr, args)
    if args.vpn_kind == 'socks' and args.mode != 'none':
        secret_path = args.state / 'upstream-secrets.json'
        if secret_path.exists():
            credentials = json.loads(secret_path.read_text())
            proxy = next(x for x in sb['outbounds'] if x['tag'] == 'vpn-proxy')
            proxy.update({'username': credentials['username'], 'password': credentials['password']})
            proxy_xr = next(x for x in xr['outbounds'] if x['tag'] == 'vpn-proxy')
            proxy_xr['settings']['servers'][0]['users'] = [
                {'user': credentials['username'], 'pass': credentials['password']}]
    dump(args.state / 'config/sing-box-server.json', sb)
    dump(args.state / 'config/xray-server.json', xr)
    import qrcode
    import qrcode.image.svg
    for backend, client in [('sing-box', sb_client(d)), ('xray', xr_client(d))]:
        out = args.state / 'outputs' / backend
        private_dir(out)
        name = f'VLESS_REALITY_{backend}_{args.mode}'
        uri = share(d, name)
        write(out / 'share.txt', uri + '\n')
        dump(out / 'client.json', client)
        dump(out / 'server.json', sb if backend == 'sing-box' else xr)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
        qr.add_data(uri); qr.make(fit=True)
        qr.make_image().save(out / 'qr.png')
        qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(out / 'qr.svg')
        for image in ['qr.png', 'qr.svg']:
            (out / image).chmod(0o600)
        node = clash_node(d, name)
        write(out / 'clash-node.yaml', node)
        write(out / 'clash.yaml', "mixed-port: 7890\nallow-lan: false\nmode: rule\nproxies:\n" + node +
            f'proxy-groups:\n  - name: ACADEMIC\n    type: select\n    proxies: [{json.dumps(name)}]\n' +
            'rules:\n  - MATCH,ACADEMIC\n')


def install(args):
    machine = platform.machine()
    mapping = {'x86_64': ('amd64', '64'), 'aarch64': ('arm64', 'arm64-v8a')}
    if platform.system() != 'Linux' or machine not in mapping:
        raise ValueError('安装支持 Linux x86_64 / aarch64')
    sb_arch, xr_arch = mapping[machine]
    binaries = [('SagerNet/sing-box', SB_VERSION, f'sing-box-{SB_VERSION}-linux-{sb_arch}.tar.gz', 'sing-box'),
                ('XTLS/Xray-core', XR_VERSION, f'Xray-linux-{xr_arch}.zip', 'xray')]
    private_dir(args.state / 'bin')
    for repo, version, asset_name, binary in binaries:
        existing = args.state / 'bin' / binary
        if existing.exists():
            info = subprocess.run([str(existing), 'version'], capture_output=True, text=True)
            if info.returncode == 0 and re.search(r'\b' + re.escape(version) + r'\b', info.stdout):
                print(f'使用已安装的 {binary} {version}')
                continue
        download_url = f'https://github.com/{repo}/releases/download/v{version}/{asset_name}'
        data = fetch(download_url)
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / asset_name
            archive.write_bytes(data)
            if asset_name.endswith('.zip'):
                with zipfile.ZipFile(archive) as z:
                    body = z.read(binary)
            else:
                with tarfile.open(archive) as t:
                    members = [x for x in t.getmembers() if x.isfile() and Path(x.name).name == binary]
                    if len(members) != 1:
                        raise ValueError('归档中的可执行文件不明确')
                    body = t.extractfile(members[0]).read()
            target = args.state / 'bin' / binary
            temp = args.state / 'bin' / (binary + '.new')
            temp.write_bytes(body); temp.chmod(0o700); os.replace(temp, target)
        dump(args.state / 'bin' / (binary + '.provenance.json'),
             {'repository': repo, 'version': version, 'url': download_url})
        print(f'已安装 {binary} {version}')


def check(args, bin_dir=None):
    b = bin_dir or args.state / 'bin'
    for name in ['vpn.json', 'sing-box-server.json']:
        if not (args.state / 'config' / name).exists():
            continue
        run([b / 'sing-box', 'check', '-c', args.state / 'config' / name])
    run([b / 'xray', 'run', '-test', '-config', args.state / 'config/xray-server.json'],
        stdout=subprocess.DEVNULL)
    run([b / 'sing-box', 'check', '-c', args.state / 'outputs/sing-box/client.json'])
    run([b / 'xray', 'run', '-test', '-config', args.state / 'outputs/xray/client.json'],
        stdout=subprocess.DEVNULL)
    print('两种服务端、VPN worker 和两种客户端配置检查通过')


def systemd_path(p):
    # Unit values are not shell strings. Reject unsupported directory syntax explicitly.
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(p)):
        raise ValueError('systemd 部署路径只支持绝对路径的字母、数字、点、下划线、连字符')
    return str(p)


def units(args):
    state = systemd_path(args.state)
    for backend in ['vpn', 'sing-box', 'xray']:
        binary = 'xray' if backend == 'xray' else 'sing-box'
        config = 'vpn.json' if backend == 'vpn' else backend + '-server.json'
        flags = f'run -config {state}/config/{config}' if binary == 'xray' else f'run -D {state} -c {state}/config/{config}'
        content = f'''[Unit]
Description=Academic VPN {backend}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
UMask=0077
ExecStart={state}/bin/{binary} {flags}
Restart=on-failure
RestartSec=5
LimitNOFILE=65536
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={state}

[Install]
WantedBy=multi-user.target
'''
        write(args.state / 'units' / ('academic-vpn-' + backend + '.service'), content)


def free_port(port):
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        try:
            s.bind(('::', port))
            return True
        except OSError:
            return False


def copy_generated(source, destination):
    private_dir(destination)
    for name in ['config', 'rules', 'outputs']:
        target = destination / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source / name, target)
    for name in ['identity.json', 'settings.json']:
        shutil.copy2(source / name, destination / name)
    secret = source / 'upstream-secrets.json'
    if secret.exists():
        shutil.copy2(secret, destination / secret.name)
    else:
        (destination / secret.name).unlink(missing_ok=True)


@vpn_health.serialized
def deploy(args):
    if os.geteuid() != 0:
        raise ValueError('部署 systemd 需要 root')
    check(args); units(args)
    settings = json.loads((args.state / 'settings.json').read_text())
    d = json.loads((args.state / 'identity.json').read_text())
    uses_worker = settings['mode'] != 'none' and settings['vpn_kind'] == 'openconnect'
    worker = 'academic-vpn-vpn.service'
    gateway = f'academic-vpn-{args.backend}.service'
    services = ([worker] if uses_worker else []) + [gateway]
    other = 'academic-vpn-' + ('xray' if args.backend == 'sing-box' else 'sing-box') + '.service'
    owned = services + [other, worker]
    for service in set(owned):
        target = UNIT_DIR / service
        if target.exists() and target.read_text() != (args.state / 'units' / service).read_text():
            raise ValueError('已有不同部署使用同名服务: ' + service)
    def active(service):
        return subprocess.run(['systemctl', 'is-active', '--quiet', service]).returncode == 0
    before = {service: active(service) for service in set(owned)}
    enabled_before = {service: subprocess.run(['systemctl', 'is-enabled', '--quiet', service]).returncode == 0
                      for service in set(owned)}
    deployed = args.state / 'deployed'
    old_settings = json.loads((deployed / 'settings.json').read_text()) if (deployed / 'settings.json').exists() else {}
    old_identity = json.loads((deployed / 'identity.json').read_text()) if (deployed / 'identity.json').exists() else {}
    ports = [(d['port'], f'academic-vpn-{args.backend}.service')]
    if uses_worker:
        ports += [(settings['vpn_port'], 'academic-vpn-vpn.service'),
                  (settings['api_port'], 'academic-vpn-vpn.service')]
    for port, service in ports:
        previous_ports = ([old_settings.get('vpn_port'), old_settings.get('api_port')]
                          if service == worker else [old_identity.get('port')])
        # An existing managed gateway may own the same port during a backend switch.
        occupied_by_own_gateway = service == gateway and (before[gateway] or before[other]) and port in previous_ports
        occupied_by_own_worker = service == worker and before[worker] and port in previous_ports
        if not occupied_by_own_gateway and not occupied_by_own_worker and not free_port(port):
            raise ValueError(f'端口 {port} 已被占用。产物已生成；请选择其他端口或自行停止旧服务后再 deploy。')
    import host_network
    host_network.pause_for_deploy(sys.modules[__name__], args)
    for service in services:
        shutil.copyfile(args.state / 'units' / service, UNIT_DIR / service)
    run(['systemctl', 'daemon-reload'])
    changed = []
    # Assemble a complete snapshot before touching processes; keep the last working snapshot.
    next_snapshot = Path(tempfile.mkdtemp(prefix='.deployment-', dir=args.state))
    try:
        copy_generated(args.state, next_snapshot)
        if before[other]:
            run(['systemctl', 'stop', other]); changed.append(other)
        for service in services:
            saved = deployed / 'config/vpn.json'
            if service == worker and before[worker] and saved.exists() and saved.read_bytes() == (args.state / 'config/vpn.json').read_bytes():
                continue  # Restart only if the VPN configuration actually changed.
            changed.append(service)
            run(['systemctl', 'restart' if before[service] else 'start', service])
            run(['systemctl', 'is-active', '--quiet', service])
        for port, service in ports:
            for _ in range(100):
                if not active(service):
                    raise ValueError('服务启动失败: ' + service)
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1):
                        break
                except OSError:
                    time.sleep(.05)
            else:
                raise ValueError('服务未监听预期端口: ' + str(port))
        run(['systemctl', 'enable', *services])
        # Switching to a plain node/external SOCKS should leave no old VPN process at boot.
        disable = [other] + ([] if uses_worker else [worker])
        for service in disable:
            if (UNIT_DIR / service).exists():
                if before[service] and service not in changed:
                    changed.append(service)
                run(['systemctl', 'disable', '--now', service])
        previous_snapshot = args.state / '.previous-deployment'
        if previous_snapshot.exists():
            shutil.rmtree(previous_snapshot, ignore_errors=True)
        if deployed.exists():
            os.replace(deployed, previous_snapshot)
        try:
            os.replace(next_snapshot, deployed)
        except OSError:
            if previous_snapshot.exists():
                os.replace(previous_snapshot, deployed)
            raise
        if previous_snapshot.exists():
            shutil.rmtree(previous_snapshot, ignore_errors=True)
    except Exception:
        for service in reversed(changed):
            subprocess.run(['systemctl', 'stop', service])
        if (deployed / 'settings.json').exists():
            copy_generated(deployed, args.state)
        for service, enabled in enabled_before.items():
            if (UNIT_DIR / service).exists():
                subprocess.run(['systemctl', 'enable' if enabled else 'disable', service])
        for service in changed:
            if before[service]:
                subprocess.run(['systemctl', 'start', service])
        raise
    finally:
        if next_snapshot.exists():
            shutil.rmtree(next_snapshot)
    vpn_health.install(sys.modules[__name__], args)
    if uses_worker:
        print('已启动。后台探测连续失败时将自动改用普通出口；VPN 恢复后恢复原分流。')
    else:
        print('已启动。当前未启用内置 VPN 健康监测。')


@vpn_health.serialized
def prepare_auth(args, command):
    current = run(command + ['status'], capture_output=True, text=True)
    if not re.search(r'^State:\s+error\s*$', current.stdout, re.MULTILINE):
        return current
    # The auth CLI answers challenges; it cannot restart a failed endpoint.
    # Restart only this deployment's worker, leaving gateway and host TUN up.
    service = 'academic-vpn-vpn.service'
    target, expected = UNIT_DIR / service, args.state / 'units' / service
    state = systemd_path(args.state)
    start = f'ExecStart={state}/bin/sing-box run -D {state} -c {state}/config/vpn.json'
    if not target.exists() or not expected.exists() or target.read_text() != expected.read_text() or start not in target.read_text().splitlines():
        raise ValueError('VPN worker 不属于当前配置目录，无法自动重置；请检查部署目录和服务')
    if os.geteuid() != 0:
        raise ValueError('VPN 会话已失效，重置需要 root；请用 sudo 重新选择登录 VPN')
    print('VPN 会话已失效，正在重置 VPN worker，随后重新认证。', flush=True)
    run(['systemctl', 'restart', service])
    import host_network
    if host_network.capture_enabled(args.state):
        dump(args.state / 'host/resume.json', {'enabled': True})
    for _ in range(20):
        try:
            current = run(command + ['status'], capture_output=True, text=True, timeout=1)
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            time.sleep(.1)
    else:
        raise ValueError('VPN worker 重启后 API 未就绪，请检查 academic-vpn-vpn.service')
    if re.search(r'^State:\s+error\s*$', current.stdout, re.MULTILINE):
        raise ValueError('VPN worker 重置后仍报错，请查看 VPN 状态；未反复重启')
    return current


def auth(args):
    settings = json.loads((args.state / 'settings.json').read_text())
    if settings['mode'] == 'none' or settings['vpn_kind'] != 'openconnect':
        print('当前模式不需要 OpenConnect 认证'); return
    command = [args.state / 'bin/sing-box', 'api', '--url', f"http://127.0.0.1:{settings['api_port']}", 'openconnect']
    current = prepare_auth(args, command)
    if re.search(r'^State:\s+connected\s*$', current.stdout, re.MULTILINE):
        print('VPN 已连接，无需重复认证')
    else:
        run(command + ['auth'])
    import host_network
    with vpn_health.locked(args.state):
        if vpn_health.after_auth(sys.modules[__name__], args):
            host_network.resume_after_auth(sys.modules[__name__], args)


@vpn_health.serialized
def stop(args):
    if os.geteuid() != 0:
        raise ValueError('停止服务需要 root')
    service = f'academic-vpn-{args.backend}.service'
    units(args)
    target = UNIT_DIR / service
    if not target.exists() or target.read_text() != (args.state / 'units' / service).read_text():
        raise ValueError('该服务不属于指定的 --state，不会停止其他部署')
    run(['systemctl', 'disable', '--now', service])
    print('入口已停止，VPN worker 保留以便切换后端。')


def status(args):
    vpn_health.banner(args.state)
    settings = json.loads((args.state / 'settings.json').read_text())
    print('路由模式: ' + settings['mode'] + '; VPN类型: ' + settings['vpn_kind'])
    import host_network
    print('Linux 本机网络: ' + host_network.status_text(args.state))
    if 'groups' in settings:
        groups = resolve_groups(settings['groups'])
        print('分流开关（rules 时生效）: ' + '；'.join(
            definition['name'] + ('：开' if groups[key] else '：关')
            for key, definition in group_definitions().items()))
    else:
        print('当前为旧版配置；请进入快速调整菜单应用新的独立规则组。')
    if settings['mode'] == 'none' or settings['vpn_kind'] != 'openconnect':
        return
    run([args.state / 'bin/sing-box', 'api', '--url', f"http://127.0.0.1:{settings['api_port']}",
         'openconnect', 'status'])


def doctor(args):
    status(args)
    settings = json.loads((args.state / 'settings.json').read_text())
    host = '127.0.0.1' if settings['vpn_kind'] == 'openconnect' else settings['upstream_host']
    port = settings['vpn_port'] if settings['vpn_kind'] == 'openconnect' else settings['upstream_port']
    proxy = f"socks5h://{'[' + host + ']' if ':' in host else host}:{port}"
    curl_input = None
    secret_path = args.state / 'upstream-secrets.json'
    if settings['vpn_kind'] == 'socks' and secret_path.exists():
        credentials = json.loads(secret_path.read_text())
        pair = credentials['username'] + ':' + credentials['password']
        if any(c in pair for c in '\r\n\x00'):
            raise ValueError('SOCKS 凭据不能含换行或 NUL')
        curl_input = 'proxy-user = ' + json.dumps(pair) + '\n'
    probes = [('本机当前出口', [], 'https://api.ipify.org')]
    if settings['mode'] != 'none':
        probes += [
                             ('VPN出口', ['--proxy', proxy], 'https://api.ipify.org'),
                             ('Nature页面', ['--proxy', proxy], 'https://www.nature.com/')]
    for label, opts, url in probes:
        command = ['curl', '-fsSL', '--max-time', '25', '--noproxy', '' if opts else '*', *opts]
        if opts and curl_input:
            command += ['--config', '-']
        if label == 'Nature页面':
            command += ['-o', '/dev/null', '-w', '%{http_code}']
        r = subprocess.run(command + [url], capture_output=True, text=True,
                           input=curl_input if opts else None)
        print(label + ': ' + (r.stdout.strip() if r.returncode == 0 else '失败（未自动切换出口）'))
        if r.returncode:
            raise ValueError('网络检查未通过: ' + label)
    print('网页连通不等于付费全文权限；请再用浏览器验证实际文章。')


def update_community(args):
    # Resolve one commit first so nested includes cannot change partway through a download.
    repo = 'v2fly/domain-list-community'
    commit = json.loads(fetch(f'https://api.github.com/repos/{repo}/commits/master'))['sha']
    seen, values = set(), set()
    excluded = {'sci-hub', 'z-library'}
    def collect(name):
        if name in seen or name in excluded:
            return
        if not re.fullmatch(r'[a-zA-Z0-9_!.-]+', name):
            raise ValueError('无效 include 路径')
        seen.add(name)
        text = fetch(f'https://raw.githubusercontent.com/{repo}/{commit}/data/{name}').decode()
        for line in text.splitlines():
            pieces = line.split('#', 1)[0].strip().split()
            if not pieces:
                continue
            token = pieces[0]
            if token.startswith('include:'):
                collect(token[8:])
            elif token.startswith('regexp:'):
                raise ValueError('上游出现正则规则；需要人工迁移，旧规则未修改')
            else:
                values.add(token)
    collect('category-scholar-!cn')
    p = args.state / 'rules/community-domains.txt'
    write(p, '\n'.join(sorted(values)) + '\n')
    domains(p)
    dump(args.state / 'rules/community-provenance.json', {'repository': repo, 'commit': commit,
        'source': 'category-scholar-!cn', 'excluded_includes': sorted(excluded)})
    print('社区候选名单已下载到 ' + str(p))
    print('含开放资源和高校域名；审核后通过 generate --rules 指定，未自动应用。')


def parser():
    p = argparse.ArgumentParser(description='VLESS/REALITY + 学术分流 + CMU VPN；秘密存放于仓库外')
    p.add_argument('command', nargs='?', default='wizard', choices=['wizard', 'setup', 'install', 'generate', 'check', 'deploy',
        'auth', 'status', 'doctor', 'units', 'update-rules', 'show', 'stop'])
    p.add_argument('--state', type=Path, default=DEFAULT_STATE)
    p.add_argument('--server', help='公网 IP 或域名，不自动探测以免使用错误网卡地址')
    p.add_argument('--port', type=int)
    p.add_argument('--sni')
    p.add_argument('--import-xray', type=Path, help='仅导入已有节点身份，不迁移旧路由/WARP/多用户')
    p.add_argument('--backend', choices=['sing-box', 'xray'])
    p.add_argument('--mode', choices=['none', 'rules', 'all'], help='none 普通节点；rules 按名单；all 全代理出口')
    p.add_argument('--vpn-kind', choices=['openconnect', 'socks'])
    p.add_argument('--vpn-flavor', choices=['anyconnect', 'gp', 'fortinet', 'f5', 'pulse', 'nc'])
    p.add_argument('--vpn-server')
    p.add_argument('--auth-group')
    p.add_argument('--upstream-host')
    p.add_argument('--upstream-port', type=int)
    p.add_argument('--vpn-port', type=int)
    p.add_argument('--api-port', type=int)
    p.add_argument('--rules', type=Path)
    p.add_argument('--groups', type=json.loads, help='规则组开关 JSON；正常使用交互菜单')
    p.add_argument('--policy', help='有序域名例外 JSON；none 恢复单名单分流')
    p.add_argument('--profile', type=Path, help='公开提供商参数 JSON，不包含凭据')
    return p


def resolve_args(args):
    """Resolve saved settings without changing files, even for status/show/check."""
    args.state = args.state.expanduser().resolve()
    if args.state == ROOT or ROOT in args.state.parents:
        raise ValueError('--state 必须位于仓库之外，防止秘密被提交')
    old_settings = json.loads((args.state / 'settings.json').read_text()) if (args.state / 'settings.json').exists() else {}
    defaults = {'mode': 'none', 'vpn_kind': 'openconnect', 'vpn_flavor': 'anyconnect',
                'vpn_server': '', 'auth_group': '', 'upstream_host': '127.0.0.1', 'upstream_port': 1080,
                'vpn_port': 12080, 'api_port': 12081, 'backend': 'sing-box', 'groups': None}
    profile = json.loads(args.profile.read_text()) if args.profile else {}
    if not isinstance(profile, dict) or set(profile) - set(defaults):
        raise ValueError('profile 包含不支持的字段')
    for key, default in defaults.items():
        if getattr(args, key) is None:
            setattr(args, key, profile.get(key, old_settings.get(key, default)))
    for key, choices in {'mode': {'none', 'rules', 'all'}, 'backend': {'sing-box', 'xray'},
                          'vpn_kind': {'openconnect', 'socks'},
                          'vpn_flavor': {'anyconnect', 'gp', 'fortinet', 'f5', 'pulse', 'nc'}}.items():
        if getattr(args, key) not in choices:
            raise ValueError('profile 中无效的 ' + key)
    args.groups = resolve_groups(args.groups)
    old_policy = args.state / 'rules/policy.json'
    if args.policy is None and old_policy.exists():
        args.policy = str(old_policy)
    for port in [args.vpn_port, args.api_port, args.upstream_port, args.port]:
        if port is not None and (type(port) is not int or not 0 < port < 65536):
            raise ValueError('无效本地端口')
    if not args.rules:
        previous = args.state / 'rules/domains.txt'
        args.rules = previous if previous.exists() else ROOT / 'rules/academic-domains.txt'
    return args


def main():
    os.umask(0o077)
    parsed = parser().parse_args()
    if parsed.command == 'wizard':
        from wizard import wizard
        wizard(sys.modules[__name__], parsed.state)
        return
    args = resolve_args(parsed)
    actions = {'install': install, 'generate': generate, 'check': check, 'deploy': deploy,
               'auth': auth, 'status': status, 'doctor': doctor, 'units': units,
               'update-rules': update_community, 'stop': stop}
    if args.command == 'setup':
        install(args)
        with vpn_health.locked(args.state):
            generate(args); deploy(args)
        auth(args); doctor(args)
    elif args.command == 'show':
        out = args.state / 'outputs' / args.backend
        print((out / 'share.txt').read_text())
        print((out / 'clash-node.yaml').read_text())
    else:
        actions[args.command](args)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, StopIteration, OSError, subprocess.CalledProcessError) as e:
        print('错误: ' + str(e), file=sys.stderr)
        sys.exit(1)
