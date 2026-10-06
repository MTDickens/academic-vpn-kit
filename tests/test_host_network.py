import copy
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from test_review import avpn, wizard
import host_network as hn


class HostNetworkTests(unittest.TestCase):
    def test_crash_cleanup_removes_auxiliary_routes_only_with_ownership(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            rules = [{'priority': 1, 'table': '3050717650'},
                     {'priority': 18000, 'table': 18000},
                     {'priority': 32766, 'table': 'main'}]
            result = subprocess.CompletedProcess([], 0, json.dumps(rules))
            with patch.object(hn.subprocess, 'run', return_value=result) as run:
                hn.cleanup(state)
                run.assert_not_called()
                avpn.dump(state / 'host/network-owned.json', {'table': 18000})
                hn.cleanup(state)
                commands = [call.args[0] for call in run.call_args_list]
                for family in ['-4', '-6']:
                    self.assertIn(['ip', family, 'rule', 'del', 'pref', '1', 'table', '3050717650'], commands)
                    self.assertIn(['ip', family, 'route', 'flush', 'table', '3050717650'], commands)
                self.assertFalse(any('32766' in command for command in commands))

    def test_dns_symlink_and_regular_file_are_restored_exactly(self):
        for symlink in [True, False]:
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                resolver = root / 'resolv.conf'
                target = root / 'original'
                target.write_text('nameserver 9.9.9.9\n')
                if symlink:
                    resolver.symlink_to(target)
                else:
                    resolver.write_text(target.read_text()); resolver.chmod(0o644)
                with patch.object(hn, 'RESOLV', resolver), patch.object(hn.shutil, 'which', return_value=None):
                    hn.dns_on(avpn, root / 'state')
                    self.assertFalse(resolver.is_symlink())
                    self.assertEqual(resolver.read_text(), hn.RESOLVER_TEXT)
                    self.assertEqual(target.read_text(), 'nameserver 9.9.9.9\n')
                    hn.dns_off(root / 'state')
                    self.assertEqual(resolver.is_symlink(), symlink)
                    self.assertEqual(resolver.read_text(), 'nameserver 9.9.9.9\n')
                    self.assertFalse((root / 'state/host/resolver-backup.json').exists())

    def test_dns_external_change_is_not_clobbered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); resolver = root / 'resolv.conf'
            resolver.write_text('nameserver 9.9.9.9\n')
            with patch.object(hn, 'RESOLV', resolver):
                hn.dns_on(avpn, root / 'state')
                resolver.write_text('nameserver 8.8.8.8\n')
                with self.assertRaises(ValueError):
                    hn.dns_off(root / 'state')
                self.assertEqual(resolver.read_text(), 'nameserver 8.8.8.8\n')
                self.assertTrue((root / 'state/host/resolver-backup.json').exists())

    def test_host_routing_reuses_groups_and_preserves_control_plane(self):
        with tempfile.TemporaryDirectory() as td:
            args = avpn.resolve_args(avpn.parser().parse_args(['status', '--state', td,
                '--groups', '{"academic":false,"cmu":true}']))
            network = {'interface': 'eth0', 'exclude': ['203.0.113.7/32'],
                       'vpn_dns': '128.2.1.10', 'vpn_hostname': 'vpn.cmu.edu', 'vpn_port': 12080}
            rules = hn.build_config(avpn, args, 'rules', network)
            selected = [r for r in rules['route']['rules'] if r.get('outbound') == 'vpn']
            self.assertEqual(selected[0]['domain_suffix'], ['cmu.edu'])
            self.assertEqual(rules['route']['final'], 'direct')
            all_config = hn.build_config(avpn, args, 'all', network)
            self.assertEqual(all_config['route']['final'], 'vpn')
            self.assertEqual(all_config['dns']['final'], 'vpn-dns')
            self.assertEqual(all_config['inbounds'][0]['route_exclude_address'], network['exclude'])
            self.assertEqual(all_config['dns']['rules'][0]['domain'], ['vpn.cmu.edu'])
            self.assertTrue(any('process_path' in rule for rule in all_config['route']['rules']))
            self.assertEqual(args.groups, {'academic': False, 'cmu': True})

    def test_host_wizard_only_asks_scope(self):
        with tempfile.TemporaryDirectory() as td:
            with patch('builtins.input', side_effect=['', '9', '3']) as prompt:
                args, credentials = wizard.collect(avpn, Path(td))
            self.assertEqual(prompt.call_count, 3)
            self.assertEqual(args.command, 'host-network')
            self.assertEqual(args.host_mode, 'all')
            self.assertIsNone(credentials)

    def test_deployment_pauses_host_and_resumes_after_auth(self):
        with tempfile.TemporaryDirectory() as td:
            args = avpn.resolve_args(avpn.parser().parse_args(['status', '--state', td, '--mode', 'rules']))
            with patch.object(hn, 'current_mode', return_value='all'), patch.object(hn, 'stop') as stop:
                hn.pause_for_deploy(avpn, args)
                stop.assert_called_once()
            with patch.object(hn, 'apply') as apply:
                hn.resume_after_auth(avpn, args)
                apply.assert_called_once_with(avpn, args, 'all')
            self.assertFalse((args.state / 'host/resume.json').exists())
