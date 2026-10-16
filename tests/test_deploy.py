import contextlib
import io
import json
import os
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote, quote_plus
import yaml
import deploy_github as d
from radar.collect import public_note


class DeploymentTests(unittest.TestCase):
    def test_valid_name(self):
        self.assertEqual(d.validate_name('paper-radar'), 'paper-radar')

    def test_reject_unsafe_or_account_wide_names(self):
        for name in ['../repo', '/tmp/x', '-danger', 'abc;rm', 'https://x', 'a'*101, 'haoranwy.github.io']:
            with self.subTest(name=name), self.assertRaises(d.DeployError):
                d.validate_name(name)

    def test_upload_allowlist_excludes_secrets_and_rendered_site(self):
        files = d.check_project(d.ROOT)
        self.assertIn('.github/workflows/daily.yml', files)
        self.assertFalse(any(p.startswith('site/') or p.startswith('.env') or p.startswith('.deploy-state') for p in files))

    def test_missing_project_rejected(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(d.DeployError):
            d.check_project(Path(td))

    def test_expected_account_must_match(self):
        d.require_owner('HaoranWy', 'haoranwy')
        with self.assertRaises(d.DeployError):
            d.require_owner('other-person', 'HaoranWy')

    def test_check_has_no_network_or_commands(self):
        with patch.object(d, 'api') as api, patch.object(d, 'command') as command, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(d.main(['--check']), 0)
            api.assert_not_called()
            command.assert_not_called()

    def test_explicit_public_authorization_required(self):
        with patch.object(d, 'deploy') as deploy, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(d.main([]), 1)
            deploy.assert_not_called()

    def test_existing_repository_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            def cmd(*args, **kwargs):
                return CompletedProcess(args, 128 if args[:2] == ('git', 'rev-parse') else 0, '', '')
            with patch.object(d, 'ROOT', Path(td)), patch.object(d, 'check_project', return_value=['config.yaml']), \
                 patch.object(d.shutil, 'which', return_value='/bin/tool'), patch.object(d, 'command', side_effect=cmd) as command, \
                 patch.object(d, 'api', side_effect=[{'login': 'HaoranWy', 'id': 7}, {'id': 99, 'private': False}]):
                with self.assertRaisesRegex(d.DeployError, 'already exists'):
                    d.deploy('HaoranWy', 'paper-radar')
                self.assertFalse(any(c.args[:3] == ('gh', 'repo', 'create') for c in command.call_args_list))

    def test_mocked_first_deployment_sequence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            made = False
            calls = []
            def cmd(*args, **kwargs):
                nonlocal made
                calls.append(args)
                if args[:3] == ('gh', 'repo', 'create'):
                    made = True
                if args[:2] == ('git', 'rev-parse') or args[:3] == ('git', 'remote', 'get-url'):
                    return CompletedProcess(args, 128, '', '')
                if args[:2] == ('git', 'symbolic-ref'):
                    return CompletedProcess(args, 0, 'main\n', '')
                if args[:2] == ('git', 'ls-files'):
                    return CompletedProcess(args, 0, 'config.yaml\n', '')
                if args[:3] == ('git', 'diff', '--cached'):
                    return CompletedProcess(args, 1, '', '')
                return CompletedProcess(args, 0, '', '')
            api_calls = []
            def api(endpoint, method='GET', fields=None, missing_ok=False):
                api_calls.append((endpoint, method, fields))
                if endpoint == 'user': return {'login': 'HaoranWy', 'id': 7}
                if endpoint == 'repos/HaoranWy/paper-radar' and method == 'GET':
                    return {'id': 99, 'private': False} if made else None
                if endpoint.endswith('/pages') and method == 'GET': return None
                return {}
            with patch.object(d, 'ROOT', root), patch.object(d, 'check_project', return_value=['config.yaml']), \
                 patch.object(d.shutil, 'which', return_value='/bin/tool'), patch.object(d, 'command', side_effect=cmd), \
                 patch.object(d, 'api', side_effect=api), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(d.deploy('HaoranWy', 'paper-radar'), 'HaoranWy/paper-radar')
            state = json.loads((root / d.STATE_NAME).read_text())
            self.assertTrue(state['pushed'])
            self.assertEqual(state['repository_id'], 99)
            self.assertIn(('repos/HaoranWy/paper-radar/pages', 'POST', {'build_type': 'workflow'}), api_calls)
            self.assertIn(('repos/HaoranWy/paper-radar/actions/workflows/daily.yml/dispatches', 'POST', {'ref': 'main'}), api_calls)
            self.assertFalse(any('--force' in cmd for cmd in calls))
            self.assertFalse(any(method == 'DELETE' for _, method, _ in api_calls))

    def test_workflow_utc_schedule_and_least_privilege(self):
        cfg = yaml.safe_load((d.ROOT / '.github/workflows/daily.yml').read_text())
        self.assertEqual(cfg['on']['schedule'][0]['cron'], '17 0 * * *')
        self.assertIn('workflow_dispatch', cfg['on'])
        self.assertEqual(cfg['permissions'], {'contents': 'read'})
        self.assertEqual(cfg['jobs']['build']['permissions'], {'contents': 'write'})
        self.assertEqual(cfg['jobs']['deploy']['permissions']['pages'], 'write')
        self.assertNotIn('pages', cfg['jobs']['build']['permissions'])

    def test_workflow_only_deploys_production_site(self):
        cfg = yaml.safe_load((d.ROOT / '.github/workflows/daily.yml').read_text())
        steps = cfg['jobs']['build']['steps']
        upload = next(s for s in steps if s.get('uses', '').startswith('actions/upload-pages-artifact@'))
        self.assertEqual(upload['with']['path'], 'site')
        builds = [s.get('run', '') for s in steps]
        self.assertTrue(any('python run.py --build-only' in s for s in builds))
        self.assertFalse(any('--preview' in s for s in builds))

    def test_contact_redacted_before_truncation(self):
        contact = 'private+radar@example.org'
        with patch.dict(os.environ, {'CROSSREF_MAILTO': contact}):
            for value in [contact, quote(contact, safe=''), quote_plus(contact)]:
                note = public_note('Request failed: ' + value)
                self.assertNotIn(value, note)
                self.assertIn('[redacted]', note)

    def test_credential_query_parameters_redacted(self):
        note = public_note('https://x.test/?api_key=abc123&token=secret42&mailto=user%40example.org')
        self.assertNotIn('abc123', note)
        self.assertNotIn('secret42', note)
        self.assertNotIn('user%40example.org', note)

if __name__ == '__main__':
    unittest.main()
