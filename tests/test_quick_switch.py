import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_review import avpn, wizard


class QuickSwitchTests(unittest.TestCase):
    def installed(self, state, *extra):
        args = avpn.resolve_args(avpn.parser().parse_args([
            'generate', '--state', str(state), '--server', 'example.org', '--backend', 'xray', *extra]))
        avpn.generate(args)
        avpn.copy_generated(state, state / 'deployed')
        (state / 'bin').mkdir()
        for name in ['sing-box', 'xray']:
            (state / 'bin' / name).touch()
        return args

    def test_switch_modes_preserves_node_and_custom_rules_without_install(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            policy = Path(td) / 'policy.json'
            policy.write_text(json.dumps({'default': 'vpn', 'rules': [
                {'domains': ['full:example.org'], 'outbound': 'direct'}]}))
            self.installed(state, '--mode', 'rules', '--vpn-server', 'https://vpn.example.edu',
                           '--policy', str(policy))
            original_identity = (state / 'identity.json').read_bytes()
            original_policy = (state / 'rules/policy.json').read_bytes()
            original_share = (state / 'outputs/xray/share.txt').read_bytes()
            with patch.object(avpn, 'install') as install, patch.object(avpn, 'run') as run, \
                 patch.object(avpn, 'check'), patch.object(avpn, 'auth'), \
                 patch.object(avpn, 'deploy') as deploy, patch.object(wizard.os, 'geteuid', return_value=0):
                for number, mode in [('3', 'all'), ('1', 'none'), ('2', 'rules')]:
                    with patch('builtins.input', side_effect=['', '', number] + ([''] if mode == 'rules' else [])) as prompt:
                        wizard.wizard(avpn, state)
                    self.assertEqual(prompt.call_count, 4 if mode == 'rules' else 3)
                    settings = json.loads((state / 'settings.json').read_text())
                    self.assertEqual(settings['mode'], mode)
                    self.assertEqual(settings['backend'], 'xray')
                    self.assertEqual(settings['vpn_server'], 'https://vpn.example.edu')
                    self.assertEqual((state / 'identity.json').read_bytes(), original_identity)
                    self.assertEqual((state / 'rules/policy.json').read_bytes(), original_policy)
                    # The display label includes mode; connection parameters stay identical.
                    self.assertEqual((state / 'outputs/xray/share.txt').read_bytes().split(b'#')[0],
                                     original_share.split(b'#')[0])
                install.assert_not_called()
                run.assert_not_called()
                self.assertEqual(deploy.call_count, 3)

    def test_same_mode_does_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            self.installed(state)
            with patch('builtins.input', side_effect=['', '', '']), \
                 patch.object(avpn, 'generate') as generate, patch.object(avpn, 'deploy') as deploy:
                wizard.wizard(avpn, state)
            generate.assert_not_called()
            deploy.assert_not_called()

    def test_first_vpn_use_only_asks_provider(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            self.installed(state)
            with patch('builtins.input', side_effect=['', '', '2', '', '1']) as prompt:
                args, credentials = wizard.collect(avpn, state)
            self.assertEqual(prompt.call_count, 5)
            self.assertEqual(args.vpn_server, 'https://vpn.cmu.edu')
            self.assertEqual(args.auth_group, 'Full VPN')
            self.assertIsNone(credentials)

    def test_uninstalled_state_has_clear_error(self):
        with tempfile.TemporaryDirectory() as td:
            with patch('builtins.input', side_effect=['', '8']):
                with self.assertRaisesRegex(ValueError, '安装或更新节点'):
                    wizard.collect(avpn, Path(td))

    def test_group_toggle_in_current_rules_mode_is_applied_and_remembered(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            self.installed(state, '--mode', 'rules', '--vpn-server', 'https://vpn.cmu.edu')
            with patch.object(avpn, 'check'), patch.object(avpn, 'auth'), \
                 patch.object(avpn, 'deploy') as deploy, patch.object(wizard.os, 'geteuid', return_value=0):
                # Keep rules mode, toggle academic off, leave CMU on, apply.
                with patch('builtins.input', side_effect=['', '', '', '1', '']):
                    wizard.wizard(avpn, state)
                self.assertEqual(json.loads((state / 'settings.json').read_text())['groups'],
                                 {'academic': False, 'cmu': True})
                self.assertEqual(deploy.call_count, 1)
                # Temporarily disable VPN, then return to rules; group choices survive.
                for answers in [['', '', '1'], ['', '', '2', '']]:
                    with patch('builtins.input', side_effect=answers):
                        wizard.wizard(avpn, state)
                self.assertEqual(json.loads((state / 'settings.json').read_text())['groups'],
                                 {'academic': False, 'cmu': True})

    def test_legacy_deployment_applies_new_defaults_even_when_mode_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            self.installed(state, '--mode', 'rules', '--vpn-server', 'https://vpn.cmu.edu')
            for path in [state / 'settings.json', state / 'deployed/settings.json']:
                saved = json.loads(path.read_text())
                saved.pop('groups')
                path.write_text(json.dumps(saved))
            with patch('builtins.input', side_effect=['', '', '', '']):
                args, _ = wizard.collect(avpn, state)
            self.assertEqual(args.command, 'switch')
            self.assertEqual(args.groups, {'academic': True, 'cmu': True})
