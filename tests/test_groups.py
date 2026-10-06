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
            for academic, cmu in itertools.product([False, True], repeat=2):
                args = avpn.resolve_args(avpn.parser().parse_args(['generate', '--state', str(state),
                    '--server', 'example.org', '--mode', 'rules', '--vpn-kind', 'socks',
                    '--groups', json.dumps({'academic': academic, 'cmu': cmu}), '--policy', str(policy)]))
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
                self.assertTrue(sb['route']['rules'][-1]['ip_is_private'])
                self.assertEqual(xr['routing']['rules'][-2]['outboundTag'], 'block')

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
            self.assertEqual(groups, {'academic': True, 'cmu': False, 'other': True})
        with self.assertRaises(ValueError):
            avpn.resolve_groups({'academic': 'false'})
        with self.assertRaises(ValueError):
            avpn.resolve_groups({'unknown': True})
