import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/adaptive-learn'))
from runtime.notebook_cli import list_notebooks, choose_notebook, bind_notebook, list_sources
from runtime.session import SessionKernel
from runtime.contracts import LearningContract
from runtime.utils import read_json

NID = '5b63964b-4e2d-4e0e-b28a-a0cbc4b5b171'


class NotebookCLITests(unittest.TestCase):
    @patch('runtime.notebook_cli.shutil.which', return_value='/bin/notebooklm')
    @patch('runtime.notebook_cli.subprocess.run')
    def test_listing_and_safe_argv(self, run, which):
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps({'notebooks': [{'id': NID, 'title': 'Learning'}]}), '')
        catalog = list_notebooks()
        self.assertEqual(catalog['count'], 1)
        self.assertEqual(catalog['notebooks'][0]['id'], NID)
        self.assertEqual(run.call_args.args[0], ['/bin/notebooklm', 'list', '--json'])
        self.assertEqual(run.call_args.kwargs['timeout'], 30)
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
        with self.assertRaises(ValueError):
            choose_notebook(catalog, 'unknown')

    @patch('runtime.notebook_cli.shutil.which', return_value=None)
    def test_missing_cli_is_honest(self, which):
        self.assertEqual(list_notebooks()['status'], 'unavailable')

    @patch('runtime.notebook_cli.shutil.which', return_value='/bin/notebooklm')
    @patch('runtime.notebook_cli.subprocess.run')
    def test_no_auth_secrets_in_failures(self, run, which):
        run.return_value = subprocess.CompletedProcess([], 1, '', 'auth expired cookie=SECRET')
        result = list_notebooks()
        self.assertEqual(result['status'], 'authentication_required')
        self.assertNotIn('SECRET', json.dumps(result))
        run.side_effect = subprocess.TimeoutExpired('notebooklm', 30)
        self.assertEqual(list_notebooks()['status'], 'timeout')

    @patch('runtime.notebook_cli.shutil.which', return_value='/bin/nlm')
    @patch('runtime.notebook_cli.subprocess.run')
    def test_nlm_array_and_source_selection(self, run, which):
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps([{'id': NID, 'title': 'Learning'}]), '')
        selection = choose_notebook(list_notebooks('nlm'), NID)
        self.assertEqual(run.call_args.args[0], ['/bin/nlm', 'notebook', 'list', '--json'])
        list_sources(selection)
        self.assertEqual(run.call_args.args[0], ['/bin/nlm', 'source', 'list', NID, '--json'])

    def test_session_binding_and_frontend_preserve_learner_files(self):
        with tempfile.TemporaryDirectory() as td:
            kernel = SessionKernel(Path(td))
            session = kernel.start('Design', LearningContract(goal='Build prototypes'))
            d = kernel.dir(session['session_id'])
            submission = d / 'workspace/modules/first-attempt/submission/design.txt'
            submission.write_text('learner work')
            selection = choose_notebook({'status': 'ready', 'provider': 'notebooklm', 'notebooks': [{'id': NID, 'title': 'Learning', 'url': f'https://notebooklm.google.com/notebook/{NID}'}]}, NID)
            bind_notebook(d, selection)
            self.assertEqual(read_json(d / 'session.json')['notebooklm']['notebook']['id'], NID)
            self.assertEqual(read_json(d / 'workspace/frontend/src/data/roadmap.json')['notebooklm']['notebook']['id'], NID)
            self.assertEqual(submission.read_text(), 'learner work')
            self.assertIn('notebook_selected', (d / 'ledger.jsonl').read_text())
