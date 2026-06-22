"""
Setup OAuth — execute UMA ÚNICA VEZ para configurar tokens de longa duração.

Pré-requisitos (5 min em developer.webex.com):
  1. My Apps → Create New App → Integration
  2. Nome: qualquer (ex: "Vale-LeadTime Bot")
  3. Redirect URI: http://localhost:8080/callback
  4. Scopes: spark:messages_read  spark:messages_write  spark:attachments_write
  5. Copie o Client ID e Client Secret

Após rodar este script:
  - access_token (14 dias) salvo no .env como WEBEX_USER_TOKEN
  - refresh_token (90 dias, renova a cada uso) salvo como WEBEX_REFRESH_TOKEN
  - A partir daí, renew_token.py renova tudo automaticamente.
"""
import http.server
import re
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

REDIRECT_URI = "http://localhost:8080/callback"
# Scopes da integração criada — deve bater exatamente com o que foi configurado no portal
SCOPES = "spark:kms spark-admin:messages_read spark:messages_write spark:messages_read spark-compliance:messages_read"
ENV_FILE = Path(__file__).parent / ".env"


def _update_env(key: str, value: str) -> None:
    text = ENV_FILE.read_text(encoding="utf-8")
    if re.search(f"^{key}=", text, re.MULTILINE):
        text = re.sub(f'^{key}=.*', f'{key}="{value}"', text, flags=re.MULTILINE)
    else:
        text += f'\n{key}="{value}"'
    ENV_FILE.write_text(text, encoding="utf-8")


def _capture_code(timeout: int = 120) -> str:
    """Sobe servidor local na porta 8080 para capturar o code do OAuth callback."""
    code_holder = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                code_holder.append(params["code"][0])
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h2>Autorizado! Pode fechar esta janela.</h2>")
            else:
                error = params.get("error", ["?"])[0]
                desc = params.get("error_description", ["sem descricao"])[0]
                print(f"\nErro OAuth: {error} — {desc}")
                print(f"URL completa recebida: {self.path}")
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"Erro: {error} — {desc}".encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("localhost", 8080), Handler)
    server.timeout = timeout

    deadline = time.time() + timeout
    while not code_holder and time.time() < deadline:
        server.handle_request()

    if not code_holder:
        raise TimeoutError("Nenhum código recebido. Verifique se autorizou no browser.")
    return code_holder[0]


def main():
    from dotenv import load_dotenv
    import os
    load_dotenv()

    print("=== Configuração OAuth Webex (uma única vez) ===\n")

    client_id = os.getenv("WEBEX_CLIENT_ID", "").strip()
    client_secret = os.getenv("WEBEX_CLIENT_SECRET", "").strip()

    if client_id and client_secret:
        print(f"Credenciais carregadas do .env (client_id={client_id[:8]}...)")
    else:
        print("Você precisará do Client ID e Client Secret da sua Webex Integration.")
        print("(developer.webex.com → My Apps → sua Integration)\n")
        client_id = input("Client ID     : ").strip()
        client_secret = input("Client Secret : ").strip()

    if not client_id or not client_secret:
        print("Client ID e Client Secret são obrigatórios.")
        sys.exit(1)

    auth_url = (
        "https://webexapis.com/v1/authorize?"
        + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
        })
    )

    print(f"\nAbrindo browser para autorização...")
    print(f"Se não abrir automaticamente, acesse:\n{auth_url}\n")
    webbrowser.open(auth_url)

    print("Aguardando callback na porta 8080...")
    try:
        code = _capture_code()
    except TimeoutError as e:
        print(f"Erro: {e}")
        sys.exit(1)

    print("Código recebido. Trocando por tokens...")
    resp = requests.post(
        "https://webexapis.com/v1/access_token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
    )
    resp.raise_for_status()
    tokens = resp.json()

    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]
    expires_days = tokens.get("expires_in", 0) // 86400
    refresh_expires_days = tokens.get("refresh_token_expires_in", 0) // 86400

    _update_env("WEBEX_USER_TOKEN", access_token)
    _update_env("WEBEX_CLIENT_ID", client_id)
    _update_env("WEBEX_CLIENT_SECRET", client_secret)
    _update_env("WEBEX_REFRESH_TOKEN", refresh_token)

    print(f"\nTokens salvos no .env!")
    print(f"  access_token  : válido por {expires_days} dias")
    print(f"  refresh_token : válido por {refresh_expires_days} dias (renova a cada uso)")
    print(f"\nA partir de agora, 'python renew_token.py' renova tudo automaticamente.")


if __name__ == "__main__":
    main()
