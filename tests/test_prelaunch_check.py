"""Tests. They only talk to a throwaway HTTP server on 127.0.0.1: no external network."""
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prelaunch_check import __main__ as cli
from prelaunch_check.checks import has_noindex, robots_blocks_everything, run_all
from prelaunch_check.client import Fetcher, Response
from prelaunch_check.config import ConfigError, from_dict

PAGE = """<!doctype html><html><head><title>Home</title>
<meta name="description" content="A page"><meta name="viewport" content="width=device-width">
{extra}</head><body><h1>Welcome</h1><a href="/about/">About</a>{body}</body></html>"""


@contextlib.contextmanager
def serve(routes):
    """routes: path -> (status, headers dict, body str). Unknown paths give 404."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = routes.get(self.path, (404, {}, "not found"))
            data = body.encode("utf-8")
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # keep test output quiet
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


HTML = {"Content-Type": "text/html; charset=utf-8"}
CLOSED = {**HTML, "X-Robots-Tag": "noindex, nofollow"}


def closed_site_routes(extra_body=""):
    return {
        "/": (200, CLOSED, PAGE.format(extra="", body=extra_body)),
        "/about/": (200, CLOSED, PAGE.format(extra="", body="")),
        "/robots.txt": (200, {"Content-Type": "text/plain"}, "User-agent: *\nDisallow: /\n"),
        "/admin/": (302, {"Location": "https://login.example.invalid/start"}, ""),
    }


def levels(results, check=None):
    return [(r.level, r.subject) for r in results if check is None or r.check == check]


class ConfigTests(unittest.TestCase):
    def test_minimal_config(self):
        cfg = from_dict({"site": "https://example.com/"})
        self.assertEqual(cfg.origin, "https://example.com")
        self.assertFalse(cfg.expect_indexable)

    def test_rejects_bad_values(self):
        for bad in (
            {},
            {"site": "example.com"},
            {"site": "https://example.com/path"},
            {"site": "https://example.com", "public_paths": ["about"]},
            {"site": "https://example.com", "surprise": 1},
            {"site": "https://example.com", "expect_indexable": "yes"},
            {"site": "https://example.com", "max_links": -1},
            {"site": "https://example.com", "preview_hosts": ["not a url"]},
        ):
            with self.assertRaises(ConfigError, msg=str(bad)):
                from_dict(bad)


class HelperTests(unittest.TestCase):
    def test_robots_parser(self):
        self.assertTrue(robots_blocks_everything("User-agent: *\nDisallow: /"))
        self.assertTrue(robots_blocks_everything("# c\nUser-agent: *\nDisallow: / # all"))
        self.assertFalse(robots_blocks_everything("User-agent: *\nDisallow: /private/"))
        self.assertFalse(robots_blocks_everything("User-agent: *\nDisallow:"))
        self.assertFalse(robots_blocks_everything("User-agent: googlebot\nDisallow: /"))
        self.assertFalse(robots_blocks_everything("User-agent: googlebot\nDisallow: /\n\nUser-agent: *\nDisallow: /tmp/"))

    def test_noindex_detection(self):
        by_header = Response("u", 200, {"x-robots-tag": "NoIndex"}, b"")
        by_meta = Response("u", 200, {"content-type": "text/html"}, b'<html><head><meta name="robots" content="noindex,follow"></head></html>')
        neither = Response("u", 200, {"content-type": "text/html"}, b"<html><head></head></html>")
        self.assertTrue(has_noindex(by_header))
        self.assertTrue(has_noindex(by_meta))
        self.assertFalse(has_noindex(neither))


class SiteTests(unittest.TestCase):
    def run_checks(self, origin, **config):
        cfg = from_dict({"site": origin, **config})
        return run_all(cfg, Fetcher(timeout=5))

    def test_closed_site_passes(self):
        with serve(closed_site_routes()) as origin:
            results = self.run_checks(origin, protected_paths=["/admin/"], login_hosts=["login.example"])
        self.assertEqual([r for r in results if r.level == "FAIL"], [])
        self.assertIn(("PASS", "/"), levels(results, "indexing"))
        self.assertTrue(any(r.level == "PASS" and r.check == "protected" for r in results))
        self.assertTrue(any(r.level == "INFO" and r.check == "robots.txt" for r in results))

    def test_exposed_private_path_on_a_preview_host_fails(self):
        routes_main = closed_site_routes()
        routes_preview = {**closed_site_routes(), "/admin/": (200, HTML, "<h1>secret</h1>")}
        with serve(routes_main) as origin, serve(routes_preview) as preview:
            results = self.run_checks(origin, protected_paths=["/admin/"], preview_hosts=[preview])
        protected = levels(results, "protected")
        self.assertEqual(sum(1 for level, _ in protected if level == "PASS"), 1)
        self.assertEqual(sum(1 for level, _ in protected if level == "FAIL"), 1)

    def test_unexpected_login_host_warns(self):
        with serve(closed_site_routes()) as origin:
            results = self.run_checks(origin, protected_paths=["/admin/"], login_hosts=["accounts.other.test"])
        self.assertTrue(any(r.level == "WARN" and r.check == "protected" for r in results))

    def test_closed_site_without_noindex_fails(self):
        routes = closed_site_routes()
        routes["/"] = (200, HTML, PAGE.format(extra="", body=""))
        with serve(routes) as origin:
            results = self.run_checks(origin)
        self.assertIn(("FAIL", "/"), levels(results, "indexing"))

    def test_indexable_site_blocked_by_noindex_and_robots_fails(self):
        with serve(closed_site_routes()) as origin:
            results = self.run_checks(origin, expect_indexable=True)
        self.assertIn(("FAIL", "/"), levels(results, "indexing"))
        self.assertTrue(any(r.level == "FAIL" and r.check == "robots.txt" for r in results))
        self.assertTrue(any(r.level == "WARN" and r.check == "sitemap" for r in results))

    def test_indexable_site_ok(self):
        routes = {
            "/": (200, HTML, PAGE.format(extra="", body="")),
            "/about/": (200, HTML, PAGE.format(extra="", body="")),
            "/robots.txt": (200, {"Content-Type": "text/plain"}, "User-agent: *\nAllow: /\n"),
            "/sitemap.xml": (200, {"Content-Type": "application/xml"}, "<urlset/>"),
        }
        with serve(routes) as origin:
            results = self.run_checks(origin, expect_indexable=True)
        self.assertEqual([r for r in results if r.level == "FAIL"], [])

    def test_forbidden_strings_found(self):
        with serve(closed_site_routes("<p>Welcome to OldBrand</p>")) as origin:
            results = self.run_checks(origin, forbidden_strings=["oldbrand", "lorem ipsum"])
        self.assertIn(("FAIL", "/"), levels(results, "forbidden text"))

    def test_broken_internal_link_fails(self):
        with serve(closed_site_routes('<a href="/missing/">gone</a>')) as origin:
            results = self.run_checks(origin)
        self.assertTrue(any(r.level == "FAIL" and r.check == "links" for r in results))

    def test_cdn_internal_links_are_not_counted_as_broken(self):
        with serve(closed_site_routes('<a href="/cdn-cgi/l/email-protection">[email protected]</a>')) as origin:
            results = self.run_checks(origin)
        self.assertEqual([r for r in results if r.check == "links" and r.level == "FAIL"], [])

    def test_same_host_redirect_is_followed(self):
        routes = closed_site_routes()
        routes["/old/"] = (301, {"Location": "/about/"}, "")
        with serve(routes) as origin:
            fetcher = Fetcher(timeout=5)
            r = fetcher.get(origin + "/old/")
        self.assertEqual(r.status, 200)
        self.assertEqual(len(r.hops), 1)

    def test_redirect_to_another_host_is_reported_not_followed(self):
        with serve(closed_site_routes()) as origin:
            r = Fetcher(timeout=5).get(origin + "/admin/")
        self.assertEqual(r.status, 302)
        self.assertEqual(r.external_redirect, "https://login.example.invalid/start")

    def test_unreachable_site_fails_cleanly(self):
        results = self.run_checks("http://127.0.0.1:9")  # nothing listens on the discard port
        self.assertTrue(any(r.level == "FAIL" and r.check == "reachability" for r in results))


class CliTests(unittest.TestCase):
    def run_cli(self, config_dict, *args):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "prelaunch.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(config_dict, f)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main([path, *args])
        return code, out.getvalue()

    def test_exit_codes_and_json(self):
        with serve(closed_site_routes()) as origin:
            code, text = self.run_cli({"site": origin, "protected_paths": ["/admin/"]}, "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(text)["summary"]["FAIL"], 0)
            code, _ = self.run_cli({"site": origin}, "--fail-on-warn")
            self.assertEqual(code, 1)  # http (not https) and missing headers are warnings
            code, text = self.run_cli({"site": origin, "forbidden_strings": ["welcome"]})
            self.assertEqual(code, 1)
            self.assertIn("FAIL", text)

    def test_config_file_with_bom_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bom.json")
            with open(path, "w", encoding="utf-8-sig") as f:  # writes a BOM, like Windows PowerShell does
                f.write('{"site": "https://example.com"}')
            from prelaunch_check.config import load
            self.assertEqual(load(path).origin, "https://example.com")

    def test_config_error_exit_code(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(cli.main(["definitely-missing.json"]), 2)
        self.assertIn("not found", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
