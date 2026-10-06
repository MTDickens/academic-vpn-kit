import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('avpn', ROOT / 'scripts/avpn.py')
avpn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(avpn)
browser_spec = importlib.util.spec_from_file_location('browser_auth', ROOT / 'scripts/browser_auth.py')
browser_auth = importlib.util.module_from_spec(browser_spec)
browser_spec.loader.exec_module(browser_auth)


class ArtifactTests(unittest.TestCase):
    def test_import_preserves_identity_and_all_exports_agree(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            args = avpn.parser().parse_args(['generate', '--state', str(state), '--server', 'example.org',
                                            '--mode', 'none'])
            # main supplies defaults; resolve here without depending on the host.
            args.rules = ROOT / 'rules/academic-domains.txt'
            args.vpn_kind = 'openconnect'; args.vpn_flavor = 'anyconnect'
            args.vpn_server = ''; args.auth_group = ''; args.vpn_port = 12080; args.api_port = 12081
            args.upstream_host = '127.0.0.1'; args.upstream_port = 1080
            avpn.generate(args)
            original = json.loads((state / 'identity.json').read_text())
            args2 = avpn.parser().parse_args(['generate', '--state', str(Path(td) / 'imported'),
                '--server', 'example.org', '--import-xray', str(state / 'config/xray-server.json')])
            args2.mode = 'none'
            for attr in ['rules', 'vpn_kind', 'vpn_flavor', 'vpn_server', 'auth_group', 'vpn_port',
                         'api_port', 'upstream_host', 'upstream_port']:
                setattr(args2, attr, getattr(args, attr))
            avpn.generate(args2)
            imported = json.loads((args2.state / 'identity.json').read_text())
            self.assertEqual(original, imported)
            for backend in ['sing-box', 'xray']:
                out = state / 'outputs' / backend
                uri = urllib.parse.urlsplit((out / 'share.txt').read_text().strip())
                params = urllib.parse.parse_qs(uri.query)
                self.assertEqual(uri.username, original['uuid'])
                self.assertEqual(params['pbk'][0], avpn.public_key(original['private_key']))
                client = (out / 'client.json').read_text()
                self.assertNotIn(original['private_key'], client)
                self.assertIn(original['uuid'], client)
                self.assertTrue((out / 'qr.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
                self.assertIn('<svg', (out / 'qr.svg').read_text())
                for file in out.iterdir():
                    self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            avpn.generate(args)
            self.assertEqual(original, json.loads((state / 'identity.json').read_text()))

    def test_jsonc_keeps_urls_and_comma_strings(self):
        value = avpn.jsonc('{/*hi*/"url":"https://example.org/a//b", "text":",}", //ok\n"v":[1,],}')
        self.assertEqual(value, {'url': 'https://example.org/a//b', 'text': ',}', 'v': [1]})

    def test_cookie_helper_rejects_idp_cookie(self):
        cookies = [{'name': 'token', 'domain': 'login.example.edu', 'value': 'wrong'},
                   {'name': 'token', 'domain': 'vpn.example.edu', 'value': 'right'}]
        self.assertEqual(browser_auth.cookie_value(cookies, 'token', 'https://vpn.example.edu/'), 'right')
        self.assertIsNone(browser_auth.cookie_value(cookies, 'other', 'https://vpn.example.edu/'))
        self.assertEqual(browser_auth.login_url('go https://vpn.example.edu/saml/sp/login?foo=1\n'),
                         'https://vpn.example.edu/saml/sp/login?foo=1')


if __name__ == '__main__':
    unittest.main()
