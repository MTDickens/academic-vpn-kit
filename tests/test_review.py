import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import avpn
import wizard


class ReviewTests(unittest.TestCase):
    def args(self, state, *extra):
        return avpn.resolve_args(avpn.parser().parse_args(
            ['generate', '--state', str(state), '--server', 'example.org', *extra]))

    def test_failed_generation_preserves_identity_rules_and_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            args = self.args(state)
            avpn.generate(args)
            avpn.dump(state / 'upstream-secrets.json', {'username': 'old', 'password': 'private'})
            original = {str(p.relative_to(state)): p.read_bytes() for p in state.rglob('*') if p.is_file()}
            invalid = Path(td) / 'invalid.txt'
            invalid.write_text('regexp:.*\n')
            args = self.args(state, '--mode', 'rules', '--vpn-kind', 'socks', '--rules', str(invalid))
            args.upstream_credentials = {'username': 'new', 'password': 'changed'}
            with self.assertRaises(ValueError):
                avpn.generate(args)
            self.assertEqual(original, {str(p.relative_to(state)): p.read_bytes() for p in state.rglob('*') if p.is_file()})

    def test_saved_backend_sni_and_explicit_port(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            avpn.generate(self.args(state, '--backend', 'xray'))
            self.assertEqual(self.args(state).backend, 'xray')
            avpn.generate(self.args(state, '--sni', 'www.microsoft.com'))
            identity = json.loads((state / 'identity.json').read_text())
            self.assertEqual(identity['sni'], 'www.microsoft.com')
            with self.assertRaises(ValueError):
                self.args(state, '--port', '0')

    def test_resolve_does_not_delete_policy(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            avpn.dump(state / 'rules/policy.json', {'default': 'direct', 'rules': []})
            self.args(state, '--policy', 'none')
            self.assertTrue((state / 'rules/policy.json').exists())

    def test_wizard_defaults_and_saved_identity(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            avpn.generate(self.args(state, '--backend', 'xray'))
            with patch('builtins.input', side_effect=['', '2', '', '', '', '', '']):
                args, credentials = wizard.collect(avpn, state)
            self.assertEqual(args.command, 'generate')
            self.assertEqual(args.backend, 'xray')
            self.assertEqual(args.mode, 'none')
            self.assertIsNone(args.sni)
            self.assertIsNone(credentials)

    def test_credentials_are_published_and_removed_with_generated_files(self):
        with tempfile.TemporaryDirectory() as td:
            args = self.args(Path(td) / 'state')
            args.upstream_credentials = {'username': 'test', 'password': 'private'}
            avpn.generate(args)
            self.assertEqual(json.loads((args.state / 'upstream-secrets.json').read_text()), args.upstream_credentials)
            args.upstream_credentials = {}
            avpn.generate(args)
            self.assertFalse((args.state / 'upstream-secrets.json').exists())

    def test_deployment_switch_and_failure_restore_previous_backend(self):
        import subprocess
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            unit_dir = Path(td) / 'units'
            unit_dir.mkdir()
            args = self.args(state)
            avpn.generate(args)
            active, enabled, commands = set(), set(), []
            fail = [False]
            def systemctl(command, **kwargs):
                verb, *services = command[1:]
                services = [x for x in services if not x.startswith('--')]
                commands.append((verb, tuple(services)))
                rc = 0
                if verb == 'is-active': rc = 0 if services[0] in active else 3
                elif verb == 'is-enabled': rc = 0 if services[0] in enabled else 1
                elif verb in {'start', 'restart'}:
                    if fail[0] and services[0] == 'academic-vpn-xray.service':
                        rc = 1
                    else: active.update(services)
                elif verb == 'stop': active.difference_update(services)
                elif verb == 'enable': enabled.update(services)
                elif verb == 'disable':
                    enabled.difference_update(services)
                    if '--now' in command: active.difference_update(services)
                if kwargs.get('check') and rc:
                    raise subprocess.CalledProcessError(rc, command)
                return subprocess.CompletedProcess(command, rc)
            with patch.object(avpn, 'UNIT_DIR', unit_dir), patch.object(avpn, 'check'), \
                 patch.object(avpn, 'free_port', return_value=True), \
                 patch.object(avpn.subprocess, 'run', side_effect=systemctl), \
                 patch.object(avpn.socket, 'create_connection', return_value=MagicMock()):
                avpn.deploy(args)
                self.assertIn('academic-vpn-sing-box.service', active)
                args = self.args(state, '--backend', 'xray')
                avpn.generate(args)
                fail[0] = True
                with self.assertRaises(subprocess.CalledProcessError):
                    avpn.deploy(args)
                self.assertEqual(json.loads((state / 'settings.json').read_text())['backend'], 'sing-box')
                self.assertIn('academic-vpn-sing-box.service', active)
                self.assertNotIn('academic-vpn-xray.service', enabled)
                fail[0] = False
                avpn.generate(args)
                avpn.deploy(args)
                self.assertIn('academic-vpn-xray.service', active)
                self.assertNotIn('academic-vpn-sing-box.service', active)
                self.assertNotIn('academic-vpn-sing-box.service', enabled)
