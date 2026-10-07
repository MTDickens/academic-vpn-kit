import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_review import avpn
import host_network as hn


class AuthRecoveryTests(unittest.TestCase):
    def test_error_is_reset_once_then_auth_runs_with_host_refresh_pending(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / 'state'
            units = Path(td) / 'units'
            args = avpn.resolve_args(avpn.parser().parse_args(['auth', '--state', str(state), '--mode', 'rules']))
            avpn.dump(state / 'settings.json', {'mode': 'rules', 'vpn_kind': 'openconnect', 'api_port': 12081})
            avpn.dump(state / 'host/settings.json', {'enabled': True})
            avpn.units(args)
            units.mkdir()
            (units / 'academic-vpn-vpn.service').write_text((state / 'units/academic-vpn-vpn.service').read_text())
            statuses = iter(['State: error\nError: Max time exceeded\n', 'State: auth-pending\n'])
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, next(statuses) if command[-1] == 'status' else '')
            with patch.object(avpn, 'UNIT_DIR', units), patch.object(avpn.os, 'geteuid', return_value=0), patch.object(avpn, 'run', side_effect=run), patch.object(hn, 'resume_after_auth') as resume:
                avpn.auth(args)
                resume.assert_called_once()
            self.assertEqual([c for c in calls if c[0] == 'systemctl'], [['systemctl', 'restart', 'academic-vpn-vpn.service']])
            self.assertEqual(calls[-1][-1], 'auth')
            self.assertTrue((state / 'host/resume.json').exists())
            self.assertTrue(hn.capture_enabled(state))

    def test_connected_and_pending_endpoints_are_not_reset(self):
        with tempfile.TemporaryDirectory() as td:
            args = avpn.resolve_args(avpn.parser().parse_args(['auth', '--state', td]))
            for status in ['connected', 'auth-pending']:
                with self.subTest(status=status), patch.object(avpn, 'run', return_value=subprocess.CompletedProcess([], 0, 'State: ' + status + '\n')) as run:
                    avpn.prepare_auth(args, ['sing-box', 'api', 'openconnect'])
                    self.assertEqual(run.call_count, 1)

    def test_error_on_unowned_worker_is_not_restarted(self):
        with tempfile.TemporaryDirectory() as td:
            args = avpn.resolve_args(avpn.parser().parse_args(['auth', '--state', td]))
            with patch.object(avpn, 'UNIT_DIR', Path(td)), patch.object(avpn, 'run', return_value=subprocess.CompletedProcess([], 0, 'State: error\n')) as run:
                with self.assertRaisesRegex(ValueError, '不属于'):
                    avpn.prepare_auth(args, ['sing-box', 'api', 'openconnect'])
                self.assertEqual(run.call_count, 1)
