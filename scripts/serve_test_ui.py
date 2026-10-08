"""Local static test server; all API traffic is intercepted by Playwright."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1] / "build/dashboard-static"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def resolve_route(self):
        path = urlsplit(self.path).path
        if path == "/":
            self.path = "/index.html"
        elif not Path(path).suffix:
            self.path = path + ".html"
    def do_GET(self):
        self.resolve_route()
        super().do_GET()

    def do_HEAD(self):
        self.resolve_route()
        super().do_HEAD()

    def log_message(self, *_args):
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # A browser navigation can cancel an in-flight static request.


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
