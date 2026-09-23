"""Obtain a durable read-only Search Console grant through the browser consent flow.

Usage (the client JSON is the "Desktop app" OAuth client downloaded from Google Cloud Console):

    python scripts/gsc_oauth_login.py --client-file C:/path/client_secret_XXXX.json

Opens the Google consent page, receives the code on a loopback address (127.0.0.1), exchanges it
for a refresh token with the ``webmasters.readonly`` scope only and stores it with
``oauth.save_grant("gsc", ...)`` (``~/.config/gsc/oauth.json``). No token or secret is printed.
Afterwards it lists the Search Console properties the account can read.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from seohead.data_sources import gsc, oauth

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def load_client(path: Path) -> tuple[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    client = data.get("installed")
    if not isinstance(client, dict):
        sys.exit(
            "Нужен OAuth-клиент типа «Приложение для компьютера» (в JSON должен быть ключ installed)."
        )
    return client["client_id"], client["client_secret"]


def wait_for_code(state: str):
    """Start a one-shot loopback server; returns (redirect_uri, thread, result, server)."""
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [""])[0] == state and "code" in query:
                result["code"] = query["code"][0]
                body = "Доступ получен. Окно можно закрыть."
            else:
                result["error"] = query.get("error", ["unknown"])[0]
                body = "Доступ не получен: " + result["error"]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    return redirect, thread, result, server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--client-file", required=True, type=Path)
    args = ap.parse_args()

    if oauth.grant_available("gsc"):
        sys.exit(
            "Доступ GSC уже сохранён. Чтобы подключить заново: seohead provider-auth gsc disconnect"
        )
    client_id, client_secret = load_client(args.client_file)

    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    redirect, thread, result, server = wait_for_code(state)
    url = (
        AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    print(
        "Открываю браузер для согласия Google. Если не открылся — откройте ссылку вручную:\n" + url
    )
    webbrowser.open(url)
    thread.join(timeout=300)
    server.server_close()
    if "code" not in result:
        sys.exit("Код не получен: " + result.get("error", "таймаут 5 минут"))

    payload = urllib.parse.urlencode(
        {
            "code": result["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
    ).encode()
    with urllib.request.urlopen(
        urllib.request.Request(oauth.TOKEN_HOST, data=payload), timeout=30
    ) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if "refresh_token" not in body:
        sys.exit(
            "Google не вернул refresh token — удалите доступ приложения в аккаунте Google и повторите."
        )
    oauth.save_grant(
        "gsc",
        {
            "refresh_token": body["refresh_token"],
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": [SCOPE],
        },
    )
    print("Доступ GSC сохранён (~/.config/gsc/oauth.json).")

    props = gsc.discover_properties()
    if not props.get("ok"):
        sys.exit("Проверка не прошла: " + str(props.get("error")))
    print("Ресурсы Search Console, доступные на чтение:")
    for p in props["properties"]:
        print(f"  {p['site_url']}  ({p['permission_level']})")


if __name__ == "__main__":
    main()
