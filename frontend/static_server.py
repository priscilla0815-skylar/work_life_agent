"""Minimal static server for local frontend preview."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent
IDENTITY_HOST = re.compile(
    r"^userpool-[a-z0-9-]+\.userpool\.auth\.id\.[a-z0-9-]+\.volces\.com$"
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        if urlparse(self.path).path in {
            "/",
            "/index.html",
            "/auth.html",
            "/agent.html",
            "/stream.html",
            "/config.js",
        }:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if urlparse(self.path).path == "/oauth/signup":
            self.redirect_to_identity_signup()
            return
        super().do_GET()

    def redirect_to_identity_signup(self):
        query = parse_qs(urlparse(self.path).query)
        authorization_url = (query.get("authorization_url") or [""])[0]
        parsed = urlparse(authorization_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not IDENTITY_HOST.fullmatch(parsed.hostname)
            or parsed.path != "/authorize"
        ):
            self.send_error(400, "Invalid Identity authorization URL")
            return

        try:
            build_opener(NoRedirect()).open(
                Request(authorization_url, headers={"User-Agent": "work-life-agent"}),
                timeout=10,
            )
        except HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                self.send_error(502, "Identity authorization failed")
                return
            location = error.headers.get("Location", "")
        except OSError:
            self.send_error(502, "Identity authorization failed")
            return
        else:
            self.send_error(502, "Identity did not create an authorization request")
            return

        target = urlparse(location)
        target_query = parse_qs(target.query)
        if (
            target.scheme != "https"
            or target.netloc != parsed.netloc
            or target.path != "/login"
            or not (target_query.get("authRequestId") or [""])[0]
        ):
            self.send_error(502, "Identity returned an invalid authorization request")
            return

        signup_url = urlunparse(
            target._replace(path="/signup", query=urlencode(target_query, doseq=True))
        )
        self.send_response(302)
        self.send_header("Location", signup_url)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), FrontendHandler).serve_forever()
