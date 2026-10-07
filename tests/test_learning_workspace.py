import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'adaptive-learn'))
from runtime.session import SessionKernel
from runtime.contracts import LearningContract
from runtime.learning_workspace import normalize_plan


class WorkspaceTests(unittest.TestCase):
    def test_goal_specific_plan_and_escaped_html(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            plan = {'sources': ['https://roadmap.sh'], 'nodes': [
                {'id': 'brief', 'title': '<script>alert(1)</script>', 'task': 'Create your design brief.', 'lesson': 'A brief describes the intended outcome.', 'stage': 'Foundations'},
                {'id': 'prototype', 'title': 'Prototype', 'task': 'Build and justify a prototype.', 'requires': ['brief']}]}
            s = k.start('Product design', LearningContract(goal='Design independently'), roadmap=plan)
            root = Path(s['learning_workspace']['directory'])
            self.assertTrue((root / 'modules/prototype/submission').is_dir())
            self.assertTrue((root / 'frontend/src/components/Lesson.jsx').is_file())
            import json
            data = json.loads((root / 'frontend/src/data/roadmap.json').read_text())
            self.assertEqual(data['nodes'][0]['lesson'], 'A brief describes the intended outcome.')
            self.assertEqual(data['nodes'][0]['stage'], 'Foundations')
            self.assertEqual(data['workspace_directory'], str(root.resolve()))
            self.assertIn('&lt;script&gt;', (root / 'index.html').read_text())
            self.assertNotIn('<script>', (root / 'index.html').read_text())
            self.assertIn('Create your design brief.', (root / 'modules/brief/TASK.md').read_text())
            work = root / 'modules/brief/submission/notes.txt'
            work.write_text('my work')
            k.start('Another topic', LearningContract(goal='Another goal'))
            self.assertEqual(work.read_text(), 'my work')

    def test_invalid_plan_creates_no_session(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            for nodes in ([{'id': '../escape', 'title': 'x', 'task': 'x'}],
                          [{'id': 'a', 'title': 'x', 'task': 'x', 'requires': ['a']}],
                          [{'id': 'a', 'title': 'x', 'task': 'x'}, {'id': 'a', 'title': 'y', 'task': 'y'}]):
                with self.assertRaises(ValueError):
                    k.start('Any', LearningContract(goal='x'), roadmap={'nodes': nodes})
            self.assertFalse(k.sessions_dir.exists())

    def test_delivery_does_not_force_learning_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            s = SessionKernel(Path(td)).start('Any', LearningContract(goal='deliver', goal_mode='deliver_artifact'))
            self.assertNotIn('learning_workspace', s)
