#!/usr/bin/env python3
"""Optional local, visible-browser helper for OpenConnect's SAML-cookie challenge."""
import argparse
import re
import shlex
import sys
import time
import urllib.parse


def login_url(text):
    urls = re.findall(r'https://[^\s<>]+', text)
    return next((u for u in urls if '/saml/sp/login' in u), None)


def cookie_value(cookies, name, gateway):
    # Accept only gateway cookies, not matching names set by the university IdP.
    host = urllib.parse.urlsplit(gateway).hostname
    for c in cookies:
        domain = c.get('domain', '').lstrip('.')
        if c.get('name') == name and host and (host == domain or host.endswith('.' + domain)):
            return c.get('value')
    return None


def main():
    p = argparse.ArgumentParser(description='在自己的电脑运行：可见浏览器登录，SAML Cookie 经 SSH 提交，不写入文件')
    p.add_argument('--ssh', required=True, help='user@server，使用 SSH 密钥或本机 ssh-agent')
    p.add_argument('--entrypoint', required=True, help='远程 entrypoint.sh 绝对路径')
    p.add_argument('--state', required=True, help='远程私人 state 绝对路径')
    p.add_argument('--timeout', type=int, default=600)
    args = p.parse_args()
    if args.ssh.startswith('-') or any(c.isspace() for c in args.ssh):
        p.error('--ssh 必须为单个 user@host')
    if not args.entrypoint.startswith('/') or not args.state.startswith('/'):
        p.error('远程路径必须为绝对路径')
    import pexpect
    from playwright.sync_api import sync_playwright
    command = shlex.join([args.entrypoint, 'auth', '--state', args.state])
    # Allocate a remote TTY: sing-box reads secrets from a terminal without echo.
    child = pexpect.spawn('ssh', ['-tt', '-o', 'BatchMode=yes', args.ssh, command],
                          encoding='utf-8', timeout=30, echo=False)
    child.logfile = None
    try:
        found = child.expect([r'Cookie "([^"\r\n]+)"[^\r\n]*: ', r'already connected',
                              r'cmu-vpn: connected|upstream-vpn: connected', pexpect.EOF])
        if found in [1, 2]:
            print('VPN 已连接'); return
        if found == 3:
            raise RuntimeError('没有收到 Cookie 登录提示；先用终端 auth 检查 SSH 和认证模式')
        name = child.match.group(1)
        url = login_url(child.before)
        if not url:
            raise RuntimeError('不是受支持的 AnyConnect SAML Cookie 流程，请使用终端或官方图形客户端')
        print('打开可见浏览器，请正常输入学校账号、密码并完成 MFA。助手不会填写或保存密码。')
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
            deadline = time.monotonic() + args.timeout
            value = None
            while time.monotonic() < deadline:
                value = cookie_value(context.cookies(), name, url)
                if value:
                    break
                page.wait_for_timeout(500)
            if not value:
                raise RuntimeError('未读取到登录 Cookie，请改用终端 auth 的 F12 方法')
            child.sendline(value)
            del value
            result = child.expect([r'(?:cmu-vpn|upstream-vpn): connected', r'failed:',
                                   r'Cookie "', pexpect.EOF], timeout=60)
            if result != 0:
                raise RuntimeError('VPN 未报告连接成功，请回到终端 auth 查看状态')
            browser.close()
            print('VPN 已连接，登录令牌已通过 SSH 提交；没有写入仓库或文件。')
    except (pexpect.TIMEOUT, pexpect.EOF):
        # pexpect's exception repr contains terminal buffers, which may include credentials.
        raise RuntimeError('SSH/认证等待超时或连接已结束；请运行终端 auth 检查状态') from None
    finally:
        # Interrupt the auth client only; the remote VPN service and pending challenge remain.
        if child.isalive():
            child.sendcontrol('c')
        child.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('浏览器助手未完成: ' + str(e), file=sys.stderr)
        sys.exit(1)
