"""VPN health monitor: preserve desired policy, temporarily use ordinary egress."""
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import fcntl
from functools import wraps
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

SERVICE = 'academic-vpn-health.service'
TIMER = 'academic-vpn-health.timer'
FAILURES = 3
_HELD = set()


@contextmanager
def locked(state, wait=True):
    state = Path(state).resolve()
    if state in _HELD:
        yield True
        return
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / '.operation.lock').open('a') as handle:
        os.chmod(handle.name, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        _HELD.add(state)
        try:
            yield True
        finally:
            _HELD.remove(state)
            fcntl.flock(handle, fcntl.LOCK_UN)


def serialized(function):
    @wraps(function)
    def wrapper(args, *a, **kw):
        with locked(args.state):
            return function(args, *a, **kw)
    return wrapper


def read(state):
    path = state / 'health/status.json'
    return json.loads(path.read_text()) if path.exists() else {}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def probe(args):
    command = [str(args.state / 'bin/sing-box'), 'api', '--url',
               f'http://127.0.0.1:{args.api_port}', 'openconnect', 'status']
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3)
        if result.returncode:
            return False, 'VPN worker 的 API 不可用'
        match = re.search(r'^State:\s+(\S+)', result.stdout, re.M)
        status = match[1] if match else 'unknown'
        if status != 'connected':
            reason = 'VPN 状态：' + status
            if 'Max time exceeded' in result.stdout:
                reason += '（会话达到服务器时限）'
            return False, reason
        # A session can claim connected while its data path is broken. One
        # successful independent destination suffices; don't trust HTTP alone.
        for url in ['https://api.ipify.org', 'https://checkip.amazonaws.com']:
            try:
                response = subprocess.run(['curl', '-fsS', '--max-time', '5', '--noproxy', '',
                    '--proxy', f'socks5h://127.0.0.1:{args.vpn_port}', url],
                    capture_output=True, text=True, timeout=6)
                if response.returncode == 0:
                    ipaddress.ip_address(response.stdout.strip())
                    return True, 'VPN 会话及出口探测正常'
            except (ValueError, subprocess.TimeoutExpired):
                pass
        return False, 'VPN 出口探测失败（两个地址均不可达）'
    except (OSError, subprocess.TimeoutExpired):
        return False, 'VPN worker 的 API 不可用'


def direct_config(config, backend):
    config = copy.deepcopy(config)
    for index, outbound in enumerate(config['outbounds']):
        if outbound.get('tag') == 'vpn-proxy':
            config['outbounds'][index] = ({'type': 'direct', 'tag': 'vpn-proxy'} if backend == 'sing-box'
                                           else {'protocol': 'freedom', 'tag': 'vpn-proxy'})
    return config


def active(service):
    return subprocess.run(['systemctl', 'is-active', '--quiet', service],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def switch_gateway(kit, args, fallback):
    backend = args.backend
    service = f'academic-vpn-{backend}.service'
    installed, expected = kit.UNIT_DIR / service, args.state / 'units' / service
    if not installed.exists() or not expected.exists() or installed.read_text() != expected.read_text():
        raise ValueError('节点服务不属于当前部署')
    desired = json.loads((args.state / 'deployed/config' / (backend + '-server.json')).read_text())
    config = direct_config(desired, backend) if fallback else desired
    destination = args.state / 'config' / (backend + '-server.json')
    current = json.loads(destination.read_text())
    if current == config:
        return
    if current not in (desired, direct_config(desired, backend)):
        raise ValueError('节点配置存在未部署的修改，请完成部署；后台不会覆盖它')
    candidate = args.state / 'health' / (backend + '-candidate.json')
    kit.dump(candidate, config)
    command = ([args.state / 'bin/sing-box', 'check', '-c', candidate] if backend == 'sing-box' else
               [args.state / 'bin/xray', 'run', '-test', '-config', candidate])
    kit.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    previous = destination.read_text()
    running = active(service)
    kit.dump(destination, config)
    try:
        if running:
            kit.run(['systemctl', 'restart', service])
            kit.run(['systemctl', 'is-active', '--quiet', service])
            inbound = config['inbounds'][0]
            port = inbound.get('listen_port', inbound.get('port'))
            for _ in range(50):
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                raise ValueError('节点重启后未监听预期端口')
    except Exception:
        kit.write(destination, previous)
        if running:
            subprocess.run(['systemctl', 'restart', service])
        raise


def installed_args(kit, state):
    args = kit.resolve_args(kit.parser().parse_args(['status', '--state', str(state)]))
    # A generate-only action must not replace the deployed policy during recovery.
    saved = json.loads((state / 'deployed/settings.json').read_text())
    for key, value in saved.items():
        setattr(args, key, value)
    args.groups = kit.resolve_groups(saved.get('groups'))
    args.rules = state / 'deployed/rules/domains.txt'
    policy = state / 'deployed/rules/policy.json'
    args.policy = str(policy) if policy.exists() else None
    return args


def tick(kit, state):
    import host_network as host
    with locked(state, wait=False) as acquired:
        if not acquired:
            return
        if not (state / 'deployed/settings.json').exists():
            return
        args = installed_args(kit, state)
        previous = read(state)
        report = {**previous, 'checked_at': now()}
        if args.mode == 'none' or args.vpn_kind != 'openconnect':
            kit.dump(state / 'health/status.json', {'status': 'disabled', 'fallback': False, 'checked_at': now()})
            return
        healthy, reason = probe(args)
        report['reason'] = reason
        report['failures'] = 0 if healthy else previous.get('failures', 0) + 1
        try:
            if healthy:
                if previous.get('fallback'):
                    # Clear the marker only after both gateway and host recover.
                    switch_gateway(kit, args, False)
                    host.resume_after_auth(kit, args)
                report.update(status='healthy', fallback=False)
                report.pop('transition_error', None)
                report.pop('since', None)
            elif previous.get('fallback') or report['failures'] >= FAILURES:
                report.update(status='fallback', fallback=True)
                report.setdefault('since', now())
                # Persist intent before mutations, including across a crash/reboot.
                kit.dump(state / 'health/status.json', report)
                if host.active():
                    host.stop(kit, args, preserve_choice=True)
                if host.capture_enabled(state):
                    kit.dump(state / 'host/resume.json', {'enabled': True})
                switch_gateway(kit, args, True)
                report.pop('transition_error', None)
            else:
                report['status'] = 'suspect'
        except Exception as error:
            report['transition_error'] = type(error).__name__ + ': ' + str(error)[:180]
            report['status'] = 'error'
        kit.dump(state / 'health/status.json', report)


def banner(state):
    report = read(state)
    if not report:
        return
    status = report.get('status')
    if report.get('transition_error'):
        text, color = '🔴 自动切换未完成，请检查：' + report['transition_error'], '31'
    elif report.get('fallback'):
        text, color = '🔴 VPN 不可用，已自动改用普通出口。选择 3 重新登录后恢复原分流。', '31'
    elif status == 'healthy':
        checked = datetime.fromisoformat(report['checked_at'])
        if (datetime.now(timezone.utc) - checked).total_seconds() > 120:
            text, color = '🟡 VPN 健康检查超过两分钟未更新，请检查后台监测服务。', '33'
        else:
            text, color = '🟢 VPN 正常，正在使用原分流策略。', '32'
    elif status == 'disabled':
        text, color = '⚪ VPN 已关闭，当前使用普通出口。', '0'
    else:
        text, color = f"🟡 VPN 探测异常（{report.get('failures', 0)}/{FAILURES}），正在确认。", '33'
    if sys.stdout.isatty() and not os.environ.get('NO_COLOR'):
        text = '\033[' + color + 'm' + text + '\033[0m'
    print(text)
    if report.get('reason') and status != 'healthy':
        print('原因：' + report['reason'])
    print('最近检查：' + report.get('checked_at', '未知'))


def install(kit, args):
    path = kit.systemd_path(args.state)
    script = kit.systemd_path(kit.ROOT / 'scripts/vpn_health.py')
    service = f'''[Unit]
Description=Academic VPN health and ordinary-egress fallback
After=network-online.target

[Service]
Type=oneshot
UMask=0077
ExecStart=/usr/bin/python3 {script} {path}
TimeoutStartSec=90
'''
    timer = f'''[Unit]
Description=Check Academic VPN every 30 seconds

[Timer]
OnBootSec=30s
OnUnitInactiveSec=30s
AccuracySec=1s
Unit={SERVICE}

[Install]
WantedBy=timers.target
'''
    for name, body in [(SERVICE, service), (TIMER, timer)]:
        target = kit.UNIT_DIR / name
        if target.exists() and target.read_text() != body:
            raise ValueError('健康检查服务属于其他部署: ' + name)
    if args.mode == 'none' or args.vpn_kind != 'openconnect':
        if (kit.UNIT_DIR / TIMER).exists():
            kit.run(['systemctl', 'disable', '--now', TIMER])
        kit.dump(args.state / 'health/status.json', {'status': 'disabled', 'fallback': False, 'checked_at': now()})
        return
    for name, body in [(SERVICE, service), (TIMER, timer)]:
        kit.write(kit.UNIT_DIR / name, body)
    kit.run(['systemctl', 'daemon-reload'])
    kit.run(['systemctl', 'enable', '--now', TIMER])


def after_auth(kit, args):
    if not (kit.UNIT_DIR / TIMER).exists():
        return True
    tick(kit, args.state)
    banner(args.state)
    return not read(args.state).get('fallback', False)


if __name__ == '__main__':
    # avpn and host_network must share this process's reentrant lock registry.
    sys.modules['vpn_health'] = sys.modules[__name__]
    import avpn
    os.umask(0o077)
    tick(avpn, Path(sys.argv[1]).resolve())
