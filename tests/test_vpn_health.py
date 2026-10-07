import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_review import avpn
import host_network as hn
import vpn_health as health


class HealthTests(unittest.TestCase):
    def prepare(self, root):
        args = avpn.resolve_args(avpn.parser().parse_args(['generate', '--state', str(root),
            '--server', 'example.org', '--mode', 'rules', '--vpn-server', 'https://vpn.cmu.edu']))
        avpn.generate(args)
        avpn.copy_generated(root, root / 'deployed')
        avpn.dump(root / 'host/settings.json', {'enabled': True})
        return args

    def test_three_failures_fallback_then_recovery_preserves_user_settings(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td); args = self.prepare(state)
            original = (state / 'settings.json').read_bytes()
            with patch.object(health, 'probe', return_value=(False, 'expired')), patch.object(health, 'switch_gateway') as gateway, patch.object(hn, 'active', return_value=True), patch.object(hn, 'stop') as stop:
                health.tick(avpn, state); health.tick(avpn, state)
                gateway.assert_not_called(); stop.assert_not_called()
                health.tick(avpn, state)
                self.assertTrue(health.read(state)['fallback'])
                self.assertTrue(gateway.call_args.args[2])
                self.assertTrue(stop.call_args.kwargs['preserve_choice'])
            self.assertEqual((state / 'settings.json').read_bytes(), original)
            self.assertTrue(hn.capture_enabled(state))
            with patch.object(health, 'probe', return_value=(True, 'good')), patch.object(health, 'switch_gateway') as gateway, patch.object(hn, 'resume_after_auth') as resume:
                health.tick(avpn, state)
                self.assertFalse(gateway.call_args.args[2]); resume.assert_called_once()
                self.assertEqual(health.read(state)['status'], 'healthy')
                self.assertFalse(health.read(state)['fallback'])
            self.assertEqual((state / 'settings.json').read_bytes(), original)

    def test_success_resets_consecutive_failure_counter(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td); self.prepare(state)
            with patch.object(health, 'probe', side_effect=[(False, 'a'), (False, 'b'), (True, 'ok'), (False, 'c')]), patch.object(health, 'switch_gateway') as gateway:
                for _ in range(4): health.tick(avpn, state)
                gateway.assert_not_called()
                self.assertEqual(health.read(state)['failures'], 1)

    def test_fallback_keeps_block_rules_and_identity_and_removes_socks_credentials(self):
        for backend in ['sing-box', 'xray']:
            config = {'inbounds': [{'users': ['unchanged']}], 'outbounds': [
                {'tag': 'direct'}, {'tag': 'vpn-proxy', 'server': 'private', 'password': 'private'}, {'tag': 'block'}],
                'routing': {'rules': [{'outboundTag': 'block'}]}}
            original = copy.deepcopy(config)
            fallback = health.direct_config(config, backend)
            self.assertEqual(config, original)
            self.assertEqual(fallback['inbounds'], original['inbounds'])
            self.assertEqual(fallback['routing'], original['routing'])
            self.assertNotIn('password', fallback['outbounds'][1])
            self.assertEqual(fallback['outbounds'][1]['type' if backend == 'sing-box' else 'protocol'],
                             'direct' if backend == 'sing-box' else 'freedom')

    def test_off_does_not_probe_or_reenable_vpn(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td); self.prepare(state)
            path = state / 'deployed/settings.json'
            settings = json.loads(path.read_text()); settings['mode'] = 'none'; avpn.dump(path, settings)
            with patch.object(health, 'probe') as probe, patch.object(health, 'switch_gateway') as gateway:
                health.tick(avpn, state)
                probe.assert_not_called(); gateway.assert_not_called()
            self.assertEqual(health.read(state)['status'], 'disabled')

    def test_generate_only_does_not_replace_deployed_recovery_policy(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td); args = self.prepare(state)
            args.mode = 'all'; avpn.generate(args)
            self.assertEqual(health.installed_args(avpn, state).mode, 'rules')

    def test_monitor_does_not_overwrite_unpublished_gateway_edits(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td); args = self.prepare(state)
            avpn.units(args)
            unit_dir = state / 'units'
            path = state / 'config/sing-box-server.json'
            changed = json.loads(path.read_text())
            changed['log']['level'] = 'debug'
            avpn.dump(path, changed)
            with patch.object(avpn, 'UNIT_DIR', unit_dir), patch.object(avpn, 'run') as run:
                with self.assertRaisesRegex(ValueError, '未部署'):
                    health.switch_gateway(avpn, args, True)
                run.assert_not_called()
            self.assertEqual(json.loads(path.read_text()), changed)

    def test_probe_uses_second_destination_and_validates_ip(self):
        with tempfile.TemporaryDirectory() as td:
            args = avpn.resolve_args(avpn.parser().parse_args(['status', '--state', td]))
            results = [subprocess.CompletedProcess([], 0, 'State: connected\n'),
                       subprocess.CompletedProcess([], 0, '<html>failure</html>'),
                       subprocess.CompletedProcess([], 0, '203.0.113.1')]
            with patch.object(health.subprocess, 'run', side_effect=results):
                self.assertTrue(health.probe(args)[0])

    def test_transition_failure_stays_red_and_retries(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td); self.prepare(state)
            avpn.dump(state / 'health/status.json', {'fallback': True, 'failures': 3})
            with patch.object(health, 'probe', return_value=(False, 'expired')), patch.object(hn, 'active', return_value=False), patch.object(health, 'switch_gateway', side_effect=ValueError('failed')):
                health.tick(avpn, state)
            self.assertIn('transition_error', health.read(state))
            with patch('sys.stdout', new=io.StringIO()) as out:
                health.banner(state)
                self.assertIn('未完成', out.getvalue())
                self.assertNotIn('🟢', out.getvalue())
            with patch.object(health, 'probe', return_value=(False, 'expired')), patch.object(hn, 'active', return_value=False), patch.object(health, 'switch_gateway'):
                health.tick(avpn, state)
            self.assertNotIn('transition_error', health.read(state))
