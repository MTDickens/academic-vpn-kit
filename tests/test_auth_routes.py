import json
import tempfile
import unittest
from pathlib import Path

from test_review import avpn
import host_network as hn


class AuthRoutesTests(unittest.TestCase):
    def test_auth_exceptions_precede_cmu_and_custom_policy_in_both_engines(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            policy = state / 'policy.json'
            policy.write_text(json.dumps({'default': 'vpn', 'rules': [{'domains': ['cmu.edu'], 'outbound': 'block'}]}))
            for mode in ['rules', 'all']:
                args = avpn.resolve_args(avpn.parser().parse_args(['generate', '--state', td,
                    '--server', 'example.org', '--mode', mode, '--vpn-server', 'https://vpn.cmu.edu', '--policy', str(policy)]))
                avpn.generate(args)
                sb = json.loads((state / 'config/sing-box-server.json').read_text())
                xr = json.loads((state / 'config/xray-server.json').read_text())
                sr, xrr = sb['route']['rules'][1], xr['routing']['rules'][0]
                self.assertEqual(sr['outbound'], 'direct')
                self.assertIn('login.cmu.edu', sr['domain'])
                self.assertIn('vpn.cmu.edu', sr['domain'])
                self.assertNotIn('cmu.edu', sr.get('domain_suffix', []))
                self.assertEqual(xrr['outboundTag'], 'direct')
                self.assertIn('full:login.cmu.edu', xrr['domain'])
                network = {'interface': 'eth0', 'exclude': ['127.0.0.0/8'], 'vpn_dns': '128.2.1.10',
                           'vpn_hostname': 'vpn.cmu.edu', 'vpn_port': 12080}
                host = hn.build_config(avpn, args, network)
                self.assertIn('login.cmu.edu', host['dns']['rules'][0]['domain'])
                self.assertEqual(host['dns']['rules'][0]['server'], 'direct-dns')
                direct = next(r for r in host['route']['rules'] if 'login.cmu.edu' in r.get('domain', []))
                self.assertEqual(direct['outbound'], 'direct')

    def test_other_providers_do_not_inherit_cmu_exceptions(self):
        with tempfile.TemporaryDirectory() as td:
            args = avpn.resolve_args(avpn.parser().parse_args(['generate', '--state', td,
                '--vpn-server', 'https://vpn.example.edu']))
            self.assertEqual(avpn.auth_domains(args), {'domain': ['vpn.example.edu']})
            args.vpn_kind = 'socks'
            self.assertEqual(avpn.auth_domains(args), {})
