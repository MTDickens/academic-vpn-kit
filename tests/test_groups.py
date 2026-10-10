import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_review import avpn


class GroupTests(unittest.TestCase):
    def test_independent_groups_and_policy_priority(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            policy = Path(td) / 'policy.json'
            policy.write_text(json.dumps({'default': 'direct', 'rules': [
                {'domains': ['full:exception.cmu.edu'], 'outbound': 'block'}]}))
            for academic, cmu, google, sheerid in itertools.product([False, True], repeat=4):
                args = avpn.resolve_args(avpn.parser().parse_args(['generate', '--state', str(state),
                    '--server', 'example.org', '--mode', 'rules', '--vpn-kind', 'socks',
                    '--groups', json.dumps({'academic': academic, 'cmu': cmu,
                                           'google': google, 'sheerid': sheerid}), '--policy', str(policy)]))
                avpn.generate(args)
                sb = json.loads((state / 'config/sing-box-server.json').read_text())
                xr = json.loads((state / 'config/xray-server.json').read_text())
                self.assertEqual(sb['route']['rules'][1]['domain'], ['exception.cmu.edu'])
                self.assertEqual(sb['route']['rules'][1]['action'], 'reject')
                self.assertEqual(xr['routing']['rules'][0]['outboundTag'], 'block')
                selected = [r for r in sb['route']['rules'] if r.get('outbound') == 'vpn-proxy']
                domains = [d for r in selected for d in r['domain_suffix']]
                self.assertEqual('cmu.edu' in domains, cmu)
                self.assertEqual('acm.org' in domains, academic)
                self.assertEqual('google.com' in domains, google)
                self.assertEqual('sheerid.com' in domains, sheerid)
                xr_domains = [d for r in xr['routing']['rules'] if r.get('outboundTag') == 'vpn-proxy'
                              for d in r.get('domain', [])]
                self.assertEqual('domain:google.com' in xr_domains, google)
                self.assertEqual('domain:sheerid.com' in xr_domains, sheerid)
                self.assertTrue(sb['route']['rules'][-1]['ip_is_private'])
                self.assertEqual(xr['routing']['rules'][-2]['outboundTag'], 'block')

    def test_google_product_coverage_and_sheerid_separation(self):
        with tempfile.TemporaryDirectory() as td:
            args = avpn.resolve_args(avpn.parser().parse_args(['status', '--state', td,
                '--groups', '{"academic":false,"cmu":false,"google":true,"sheerid":false}']))
            suffix, exact = avpn.selected_domains(args)
            def matches(host):
                return host in exact or any(host == d or host.endswith('.' + d) for d in suffix)
            for host in ['www.google.com', 'www.google.co.jp', 'mail.google.com', 'drive.google.com',
                         'gemini.google.com', 'notebooklm.google.com', 'aistudio.google.com',
                         'www.youtube.com', 'i.ytimg.com', 'rr1.googlevideo.com',
                         'play.google.com', 'accounts.google.com', 'storage.googleapis.com',
                         'lh3.googleusercontent.com', 'fonts.gstatic.com', 'example.firebaseapp.com',
                         'www.kaggle.com', 'ai.google.dev']:
                self.assertTrue(matches(host), host)
            for host in ['services.sheerid.com', 'notgoogle.com', 'google.com.example.org',
                         'unrelated.app', 'unrelated.dev', 'cdn.jsdelivr.net', 'example.cmu.edu']:
                self.assertFalse(matches(host), host)
            args.groups.update(google=False, sheerid=True)
            self.assertEqual(avpn.selected_domains(args), (['sheerid.com'], []))

    def test_new_groups_default_off_for_existing_settings(self):
        self.assertEqual(avpn.resolve_groups({'academic': False, 'cmu': True}),
                         {'academic': False, 'cmu': True, 'google': False, 'sheerid': False})

    def test_disabled_academic_does_not_require_list_file(self):
        with tempfile.TemporaryDirectory() as td:
            args = avpn.resolve_args(avpn.parser().parse_args(['generate', '--state', td,
                '--server', 'example.org', '--mode', 'rules', '--vpn-kind', 'socks',
                '--groups', '{"academic":false,"cmu":true}', '--rules', td + '/missing.txt']))
            avpn.generate(args)

    def test_extensible_group_registry_and_strict_boolean(self):
        definitions = {**avpn.group_definitions(), 'other': {
            'name': 'Other university', 'default': False, 'domains': ['example.edu']}}
        with patch.object(avpn, 'group_definitions', return_value=definitions):
            groups = avpn.resolve_groups({'other': True, 'cmu': False})
            self.assertEqual(groups, {'academic': True, 'cmu': False, 'google': False, 'sheerid': False, 'other': True})
        with self.assertRaises(ValueError):
            avpn.resolve_groups({'academic': 'false'})
        with self.assertRaises(ValueError):
            avpn.resolve_groups({'unknown': True})
