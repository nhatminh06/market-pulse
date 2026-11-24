"""Focused tests for the disposable Git smart HTTP fixture."""

import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[2] / "infra/flux/git-http.py"
spec = importlib.util.spec_from_file_location("git_http", MODULE_PATH)
git_http = importlib.util.module_from_spec(spec)
spec.loader.exec_module(git_http)


class GitHttpTests(unittest.TestCase):
    def request(self, output, stderr=b"", returncode=0):
        handler = object.__new__(git_http.GitHandler)
        handler.path = "/fixture.git/info/refs?service=git-upload-pack"
        handler.command = "GET"
        handler.headers = {}
        handler.rfile = io.BytesIO()
        handler.wfile = io.BytesIO()
        events = []
        handler.log_error = lambda fmt, *args: events.append(fmt % args)
        handler.send_error = lambda code: events.append(code)
        handler.send_response = lambda code: events.append(code)
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None
        response = SimpleNamespace(stdout=output, stderr=stderr, returncode=returncode)
        with patch.object(git_http.subprocess, "run", return_value=response) as run:
            handler.serve_git()
        self.assertEqual(run.call_args.args[0], ["git", "-c", "safe.directory=/srv/fixture.git", "http-backend"])
        return events, handler.wfile.getvalue()

    def test_valid_smart_http(self):
        output = b"Content-Type: application/x-git-upload-pack-advertisement\r\n\r\n001e# service=git-upload-pack\n0000"
        events, body = self.request(output)
        self.assertEqual(events, [200])
        self.assertTrue(body.startswith(b"001e# service=git-upload-pack\n0000"))

    def test_cgi_500_with_zero_exit_is_sanitized(self):
        output = b"Status: 500 Internal Server Error\r\nExpires: Fri, 01 Jan 1980 00:00:00 GMT\r\n\r\nsensitive-body"
        events, body = self.request(output, b"fatal: detected dubious ownership; token=SECRET")
        self.assertEqual(events[-1], 502)
        self.assertNotIn(b"sensitive-body", body)
        self.assertIn("fixture repository ownership mismatch", events[0])
        self.assertNotIn("SECRET", str(events))

    def test_nonzero_backend_error_is_sanitized(self):
        events, _ = self.request(b"Content-Type: text/plain\r\n\r\n", b"token=SECRET", 1)
        self.assertEqual(events[-1], 502)
        self.assertIn("backend failure", events[0])
        self.assertNotIn("SECRET", str(events))

    def test_malformed_cgi_is_rejected(self):
        for output in (b"no headers", b"Status: nope\r\nContent-Type: text/plain\r\n\r\n", b"X: x\r\n\r\n"):
            with self.subTest(output=output):
                events, _ = self.request(output)
                self.assertEqual(events[-1], 502)
                self.assertIn("malformed CGI output", events[0])


@unittest.skipUnless(os.environ.get("M4_GIT_FIXTURE_DOCKER_TEST") == "1", "opt-in disposable Docker ownership test")
class GitOwnershipTests(unittest.TestCase):
    def test_uid_mismatch_and_exact_safe_directory(self):
        image = "python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89"
        with tempfile.TemporaryDirectory(prefix="market-pulse-git-http-") as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
            subprocess.run(["git", "-C", str(work), "config", "user.name", "Fixture"], check=True)
            subprocess.run(["git", "-C", str(work), "config", "user.email", "fixture@example.invalid"], check=True)
            (work / "file.txt").write_text("fixture\n")
            subprocess.run(["git", "-C", str(work), "add", "file.txt"], check=True)
            subprocess.run(["git", "-C", str(work), "commit", "-qm", "initial"], check=True)
            subprocess.run(["git", "clone", "-q", "--bare", str(work), str(root / "fixture.git")], check=True)
            script = '''
apk add --no-cache git git-daemon >/dev/null
cp -R /input /srv/fixture.git
chown -R 1001:1001 /srv/fixture.git
export GIT_PROJECT_ROOT=/srv GIT_HTTP_EXPORT_ALL=1 REQUEST_METHOD=GET PATH_INFO=/fixture.git/info/refs QUERY_STRING=service=git-upload-pack
git http-backend >/tmp/before 2>/tmp/before-err
git -c safe.directory=/srv/fixture.git http-backend >/tmp/after 2>/tmp/after-err
python - <<'PY'
from pathlib import Path
before = Path('/tmp/before').read_bytes()
after = Path('/tmp/after').read_bytes()
assert b'Status: 500 Internal Server Error' in before
assert b'detected dubious ownership' in Path('/tmp/before-err').read_bytes()
assert b'Status: 500' not in after
assert b'application/x-git-upload-pack-advertisement' in after
assert b'001e# service=git-upload-pack\\n0000' in after
print('UID mismatch produced CGI 500 with exit 0; exact safe.directory restored advertisement')
PY
'''
            result = subprocess.run(
                ["docker", "run", "--rm", "--mount", f"type=bind,src={root / 'fixture.git'},dst=/input,readonly",
                 "--entrypoint", "sh", image, "-ec", script], capture_output=True, text=True, check=True
            )
            self.assertIn("exact safe.directory restored advertisement", result.stdout)
