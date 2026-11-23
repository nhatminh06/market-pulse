"""Disposable, local-only Git smart HTTP fixture for the M4.2 test."""

import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit


class GitHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.serve_git()

    def do_POST(self):
        self.serve_git()

    def serve_git(self):
        url = urlsplit(self.path)
        if not url.path.startswith("/fixture.git/"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        env = os.environ.copy()
        env.update(
            GIT_PROJECT_ROOT="/srv",
            GIT_HTTP_EXPORT_ALL="1",
            REQUEST_METHOD=self.command,
            PATH_INFO=url.path,
            QUERY_STRING=url.query,
            CONTENT_TYPE=self.headers.get("Content-Type", ""),
            CONTENT_LENGTH=str(length),
        )
        response = subprocess.run(
            ["git", "http-backend"],
            input=self.rfile.read(length),
            env=env,
            capture_output=True,
            check=False,
        )
        header, separator, body = response.stdout.partition(b"\r\n\r\n")
        if not separator or response.returncode:
            self.log_error("git http-backend: %s", response.stderr.decode("utf-8", "replace"))
            self.send_error(502)
            return
        lines = header.decode("ascii").split("\r\n")
        status = next((line[8:] for line in lines if line.startswith("Status: ")), "200 OK")
        self.send_response(int(status.split()[0]))
        for line in lines:
            if line and not line.startswith("Status: "):
                name, value = line.split(": ", 1)
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


HTTPServer(("0.0.0.0", 80), GitHandler).serve_forever()
