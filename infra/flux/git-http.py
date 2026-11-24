"""Disposable, local-only Git smart HTTP fixture for the M4.2 test."""

import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit


GIT_COMMAND = ["git", "-c", "safe.directory=/srv/fixture.git", "http-backend"]


def parse_cgi(output):
    header, separator, body = output.partition(b"\r\n\r\n")
    if not separator:
        raise ValueError("missing CGI headers")
    headers = []
    status = 200
    try:
        for line in header.decode("ascii").split("\r\n"):
            name, value = line.split(": ", 1)
            if name == "Status":
                code = value.split(" ", 1)[0]
                if len(code) != 3 or not code.isdigit():
                    raise ValueError("invalid CGI status")
                status = int(code)
            else:
                if not name or "\n" in value:
                    raise ValueError("invalid CGI header")
                headers.append((name, value))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("malformed CGI headers") from exc
    if status == 200 and not any(name.lower() == "content-type" for name, _ in headers):
        raise ValueError("missing CGI content type")
    return status, headers, body


class GitHandler(BaseHTTPRequestHandler):
    def log_request(self, code="-", size="-"):
        pass  # Request paths and headers do not belong in fixture logs.

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
        try:
            response = subprocess.run(
                GIT_COMMAND,
                input=self.rfile.read(length),
                env=env,
                capture_output=True,
                check=False,
            )
            status, headers, body = parse_cgi(response.stdout)
        except (OSError, ValueError):
            self.log_error("git http-backend failed or returned malformed CGI output")
            self.send_error(502)
            return
        if response.returncode or status != 200:
            reason = "fixture repository ownership mismatch" if b"detected dubious ownership" in response.stderr else "backend failure"
            self.log_error("git http-backend %s (CGI status %d, exit %d)", reason, status, response.returncode)
            self.send_error(502)
            return
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 80), GitHandler).serve_forever()
