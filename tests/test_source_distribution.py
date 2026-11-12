"""Release checks must work without inheriting an enclosing repository."""
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'release_check', Path(__file__).resolve().parents[1] / 'scripts/check_release.py')
release_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_check)


class SourceDistributionTests(unittest.TestCase):
    def test_archive_scan_never_uses_parent_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            (parent / '.git').mkdir()
            root = parent / 'export'; root.mkdir()
            (root / 'README.md').write_text('Public source\n')
            with patch.object(release_check, 'ROOT', root), \
                    patch.object(release_check, 'git', side_effect=AssertionError('No Git access')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(release_check.has_git_metadata())
                self.assertTrue(release_check.scan_history_and_worktree())

    def test_archive_scanner_detects_sensitive_files_and_signatures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('SYNTHETIC=' + 'lf_' + 'x' * 32)
            with patch.object(release_check, 'ROOT', root), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertFalse(release_check.scan_history_and_worktree())
                self.assertIn('sensitive-file', output.getvalue())
                self.assertIn('lumenflow-token', output.getvalue())
                self.assertNotIn('x' * 32, output.getvalue())

    def test_archive_scanner_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'broken-link').symlink_to(root / 'missing')
            with patch.object(release_check, 'ROOT', root), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertFalse(release_check.scan_history_and_worktree())
                self.assertIn('unexpected-symlink', output.getvalue())

    def test_git_checkout_keeps_history_scanning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / '.git').mkdir()
            def fake_git(*args):
                return {('rev-list', '--objects', '--all'): b'abc old.txt\n',
                        ('cat-file', '-t', 'abc'): b'blob\n',
                        ('cat-file', 'blob', 'abc'): ('lf_' + 'x' * 32).encode(),
                        ('ls-files', '-z', '--cached', '--others', '--exclude-standard'): b''}[args]
            with patch.object(release_check, 'ROOT', root), \
                    patch.object(release_check, 'git', side_effect=fake_git), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertFalse(release_check.scan_history_and_worktree())
                self.assertIn('history:abc:old.txt', output.getvalue())
