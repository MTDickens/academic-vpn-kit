"""Interactive front end. CLI arguments remain available for automation only."""
import getpass
import json
from pathlib import Path
import os


def ask(label, default='', required=False):
    while True:
        text = input(f'{label}' + (f' [{default}]' if default != '' else '') + ': ').strip()
        value = text or str(default)
        if value or not required:
            return value
        print('这一项需要填写。')


def choice(label, options, default=0):
    print('\n' + label)
    for i, (value, title) in enumerate(options, 1):
        print(f'  {i}. {title}' + ('（默认）' if i - 1 == default else ''))
    while True:
        answer = ask('选择', default + 1)
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        print('请输入菜单中的数字。')


def configure_vpn(args):
    credentials = None
    state = args.state
    default_provider = 2 if args.vpn_kind == 'socks' else (0 if 'vpn.cmu.edu' in args.vpn_server or not args.vpn_server else 1)
    provider = choice('VPN 提供商', [('cmu', 'CMU Full VPN'), ('custom', '其他学校/提供商'),
                                    ('socks', '已有 SOCKS5 出口')], default_provider)
    if provider == 'socks':
        args.vpn_kind = 'socks'
        args.upstream_host = ask('SOCKS5 主机', args.upstream_host, required=True)
        while True:
            port = ask('SOCKS5 端口', args.upstream_port)
            if port.isdigit() and 0 < int(port) < 65536:
                args.upstream_port = int(port); break
            print('端口范围是 1–65535。')
        has_auth = (state / 'upstream-secrets.json').exists()
        login = choice('SOCKS5 认证', [('none', '不需要账号'), ('keep', '沿用已保存账号'),
                                      ('new', '输入账号和密码')], 1 if has_auth else 0)
        if login == 'new':
            credentials = {'username': ask('SOCKS5 用户名', required=True),
                           'password': getpass.getpass('SOCKS5 密码（隐藏输入）: ')}
        elif login == 'none':
            credentials = {}  # Explicitly remove previously saved authentication.
        elif not has_auth:
            raise ValueError('没有保存过 SOCKS5 账号，请选择输入账号或无需认证')
    else:
        args.vpn_kind = 'openconnect'
        if provider == 'cmu':
            args.vpn_server = 'https://vpn.cmu.edu'
            args.vpn_flavor = 'anyconnect'; args.auth_group = 'Full VPN'
        else:
            args.vpn_server = ask('学校官方 VPN 网关', args.vpn_server, required=True)
            flavors = [('anyconnect', 'Cisco AnyConnect'), ('gp', 'GlobalProtect'),
                       ('fortinet', 'Fortinet'), ('f5', 'F5'), ('pulse', 'Pulse'), ('nc', 'Network Connect')]
            args.vpn_flavor = choice('VPN 协议', flavors, [x[0] for x in flavors].index(args.vpn_flavor))
            group = ask('登录组（输入 - 清空，由认证流程选择）',
                        args.auth_group if default_provider == 1 else '')
            args.auth_group = '' if group == '-' else group
    return credentials


MODES = [('none', '关闭 VPN，使用普通出口'),
         ('rules', '按独立规则组分流（学术、CMU、Google、SheerID）'),
         ('all', '全部流量走 VPN（在已选择的接管范围内）')]


def select_mode(current):
    return choice('VPN 出口策略', MODES, [x[0] for x in MODES].index(current))


def edit_groups(kit, args):
    definitions = kit.group_definitions()
    keys = list(definitions)
    print('各规则组独立开关；命中任一开启组就走 VPN，其余走普通出口。')
    if args.policy and args.policy != 'none':
        print('已有自定义有序规则优先；它们的显式出口及默认出口仍然生效。')
    while True:
        print('\n分流开关（输入编号切换，回车应用）：')
        for i, key in enumerate(keys, 1):
            print(f'  {i}. [{"开" if args.groups[key] else "关"}] {definitions[key]["name"]}')
        print('  0. 应用当前选择（默认）')
        answer = ask('选择', '0')
        if answer == '0':
            return
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            key = keys[int(answer) - 1]
            args.groups[key] = not args.groups[key]
        else:
            print('请输入菜单中的数字。')


def collect_switch(kit, args):
    if not all((args.state / name).exists() for name in
               ['identity.json', 'settings.json', 'deployed/settings.json', 'bin/sing-box', 'bin/xray']):
        raise ValueError('此目录没有完整的已安装节点，请先选择“安装或更新节点”')
    print('当前模式：' + dict(MODES)[args.mode])
    previous = args.mode
    previous_groups = args.groups.copy()
    saved = json.loads((args.state / 'deployed/settings.json').read_text())
    needs_group_update = saved.get('groups') != args.groups
    args.mode = select_mode(previous)
    if args.mode == 'rules':
        edit_groups(kit, args)
    if args.mode == previous and args.groups == previous_groups and not needs_group_update:
        args.command = 'unchanged'
        return args, None
    credentials = None
    if args.mode != 'none' and args.vpn_kind == 'openconnect' and not args.vpn_server:
        print('首次启用 VPN，请补充连接设置。')
        credentials = configure_vpn(args)
    if args.mode == 'rules':
        print('沿用分流文件：' + str(args.policy if args.policy and args.policy != 'none' else args.rules))
    return args, credentials


def collect(kit, initial_state):
    """Gather choices without installing, modifying configuration or starting services."""
    print('VPN Route Kit：回车使用默认值；Ctrl+C 可取消。')
    state = Path(ask('私人配置目录', initial_state)).expanduser().resolve()
    args = kit.resolve_args(kit.parser().parse_args(['wizard', '--state', str(state)]))
    import vpn_health
    vpn_health.banner(state)
    action = choice('要做什么？', [
        ('setup', '安装或更新节点'), ('generate', '只生成配置、链接和二维码'),
        ('auth', '登录/重新登录 VPN'), ('doctor', '查看状态并检查连接'),
        ('show', '查看分享链接和 Clash 配置'), ('stop', '停止本仓库的节点服务'),
        ('update-rules', '下载社区学术域名名单'),
        ('switch', '快速调整 VPN：总开关 / 各域名组独立开关'),
        ('host-network', '接管 Linux 本机网络：独立开关')],
        7 if (state / 'deployed/settings.json').exists() else 0)
    args.command = action
    credentials = None
    if action == 'host-network':
        import host_network
        current = host_network.capture_enabled(args.state)
        print('当前 VPN 出口策略：' + dict(MODES)[args.mode])
        print('此开关只决定是否接管本机流量；分流策略和各域名组开关在菜单 8 设置。')
        args.host_enabled = choice('接管 Linux 本机网络', host_network.CAPTURE_CHOICES, int(current))
        return args, credentials
    if action == 'switch':
        return collect_switch(kit, args)
    if action not in {'setup', 'generate', 'show', 'stop'}:
        return args, credentials
    backends = [('sing-box', 'sing-box'), ('xray', 'Xray')]
    args.backend = choice('节点后端', backends, 0 if args.backend == 'sing-box' else 1)
    if action in {'show', 'stop'}:
        return args, credentials
    identity_path = state / 'identity.json'
    old = json.loads(identity_path.read_text()) if identity_path.exists() else {}
    if not old:
        imported = ask('导入已有 Xray 配置的路径（留空生成新身份）')
        args.import_xray = Path(imported).expanduser() if imported else None
        if args.import_xray:
            config = kit.jsonc(args.import_xray.read_text())
            inbound = next(x for x in config['inbounds'] if x.get('protocol') == 'vless'
                           and x.get('streamSettings', {}).get('security') == 'reality')
            old = {'port': inbound['port'], 'sni': inbound['streamSettings']['realitySettings']['serverNames'][0]}
    args.server = ask('服务器公网 IP 或域名', old.get('server', ''), required=True)
    default_port = old.get('port', 443)
    if action == 'setup' and not (state / 'deployed').exists() and not kit.free_port(default_port):
        print(f'端口 {default_port} 已被占用，旧服务会保留；可先用其他端口试跑。')
        if kit.free_port(8443):
            default_port = 8443
    while True:
        port = ask('节点端口', default_port)
        if port.isdigit() and 0 < int(port) < 65536:
            args.port = int(port); break
        print('端口范围是 1–65535。')
    selected_sni = ask('REALITY 握手域名', old.get('sni', 'learn.microsoft.com'), required=True)
    args.sni = None if selected_sni == old.get('sni') else selected_sni
    args.mode = select_mode(args.mode)
    if args.mode != 'none':
        credentials = configure_vpn(args)
    if args.mode == 'rules':
        policy = choice('分流规则', [('list', '域名名单'), ('policy', '有序规则：直连 / VPN / 阻断')],
                        1 if args.policy and args.policy != 'none' else 0)
        if policy == 'list':
            args.policy = 'none'
            args.rules = Path(ask('名单文件路径', args.rules, required=True)).expanduser()
        else:
            args.policy = ask('有序规则 JSON 路径', args.policy or '', required=True)
        edit_groups(kit, args)
    print(f'\n准备{ "安装/更新" if action == "setup" else "生成" }：{args.backend}，端口 {args.port}，模式 {args.mode}。')
    return args, credentials


def wizard(kit, initial_state):
    try:
        args, credentials = collect(kit, initial_state)
    except (EOFError, KeyboardInterrupt):
        print('\n已取消，未开始安装或部署。'); return
    if args.command == 'host-network':
        import host_network
        host_network.apply(kit, args, args.host_enabled)
        return
    if args.command == 'unchanged':
        print('已是所选模式，无需修改。')
        return
    if args.command == 'switch':
        if os.geteuid() != 0:
            raise ValueError('切换需要 root，请用 sudo ./entrypoint.sh 重新运行')
        args.upstream_credentials = credentials
        import vpn_health
        with vpn_health.locked(args.state):
            kit.generate(args)
            kit.deploy(args)
        kit.auth(args)
        print('已切换为：' + dict(MODES)[args.mode] + '。客户端继续使用原节点。')
        return
    if args.command in {'setup', 'generate'}:
        if args.command == 'setup' and os.geteuid() != 0:
            raise ValueError('安装需要 root，请用 sudo ./entrypoint.sh 重新运行')
        if args.command == 'setup':
            kit.run([kit.ROOT / 'entrypoint.sh', 'deps'])
        else:
            try:
                import cryptography, qrcode, PIL  # noqa: F401
            except ImportError:
                if os.geteuid() != 0:
                    raise ValueError('生成文件需要依赖，请先用 sudo ./entrypoint.sh deps 安装')
                kit.run([kit.ROOT / 'entrypoint.sh', 'deps'])
        args.upstream_credentials = credentials
        if args.command == 'setup':
            kit.install(args)
        import vpn_health
        with vpn_health.locked(args.state):
            kit.generate(args)
            if args.command == 'setup':
                kit.deploy(args)
        if args.command == 'setup':
            kit.auth(args); kit.doctor(args)
        print('分享链接、二维码和配置目录：' + str(args.state / 'outputs'))
    elif args.command == 'show':
        folder = args.state / 'outputs' / args.backend
        print((folder / 'share.txt').read_text())
        print((folder / 'clash-node.yaml').read_text())
        print('二维码：' + str(folder / 'qr.png'))
    else:
        {'auth': kit.auth, 'doctor': kit.doctor, 'stop': kit.stop,
         'update-rules': kit.update_community}[args.command](args)
