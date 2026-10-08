"""Small authenticated gateway client shared by read/explicit-run commands."""
import getpass
import os
import sys
from contextlib import contextmanager
from urllib.parse import urlsplit

import httpx


def connection_arguments(parser):
    parser.add_argument("--gateway", default="http://localhost:8080")
    parser.add_argument("--username", help="Prompt for password; otherwise use OSMEDEUS_TOKEN")


@contextmanager
def gateway(args):
    url = urlsplit(args.gateway)
    if (url.scheme not in ("http", "https") or not url.hostname or url.username or
            url.password or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("--gateway must be an HTTP(S) origin without credentials")
    with httpx.Client(base_url=args.gateway.rstrip("/"), timeout=60,
                      follow_redirects=False, trust_env=False) as client:
        token = os.getenv("OSMEDEUS_TOKEN", "")
        if args.username:
            body = get_json(client, "POST", "/osm/api/login", json={
                "username": args.username, "password": getpass.getpass("Osmedeus password: ")})
            token = body.get("token")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("Use --username or provide OSMEDEUS_TOKEN")
        client.headers["Authorization"] = "Bearer " + token
        yield client


def get_json(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("Unexpected gateway response")
    return body


def entrypoint(main):
    try:
        return main()
    except (httpx.HTTPError, ValueError, KeyError, RuntimeError) as exc:
        # Do not expose credentials, upstream response bodies or URLs in errors.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print("Command failed: " + message, file=sys.stderr)
        return 1
