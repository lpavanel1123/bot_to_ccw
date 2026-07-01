"""
Verifica e renova o WEBEX_USER_TOKEN.

Com refresh token (após oauth_setup.py):
    Renova automaticamente, sem interação.

Sem refresh token (token manual):
    Pede o novo token e atualiza o .env.

Uso:
    python renew_token.py          # verifica e renova se necessário
    python renew_token.py --check  # só verifica, não renova
"""
import re
import sys
import requests
from pathlib import Path

ENV_FILE        = Path(__file__).parent / ".env"
TOKEN_INFO_FILE = Path(__file__).parent / "token_info.json"
WEBEX_ME_URL    = "https://webexapis.com/v1/people/me"
TOKEN_URL       = "https://webexapis.com/v1/access_token"


def _read_env() -> str:
    return ENV_FILE.read_text(encoding="utf-8")


def _get_env_value(env_text: str, key: str) -> str:
    match = re.search(rf'^{key}=["\']?([^"\'\\n]+)["\']?', env_text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _update_env(env_text: str, key: str, value: str) -> str:
    if re.search(f"^{key}=", env_text, re.MULTILINE):
        return re.sub(f'^{key}=.*', f'{key}="{value}"', env_text, flags=re.MULTILINE)
    return env_text + f'\n{key}="{value}"'


def _validate_token(token: str):
    """Retorna dict do usuário se token válido, None se 401 (expirado/inválido).
    403 = token válido mas scope insuficiente para /people/me — ainda conta como válido."""
    if not token:
        return None
    r = requests.get(WEBEX_ME_URL, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 200:
        return r.json()
    if r.status_code == 403:
        return {"displayName": "OAuth Integration", "emails": ["(scope limitado)"]}
    return None  # 401 = expirado/inválido


def _refresh_via_oauth(env_text: str) -> str:
    """Usa refresh_token para obter novo access_token. Retorna novo env_text ou lança exceção."""
    import json
    from datetime import datetime, timezone, timedelta

    client_id     = _get_env_value(env_text, "WEBEX_CLIENT_ID")
    client_secret = _get_env_value(env_text, "WEBEX_CLIENT_SECRET")
    refresh_token = _get_env_value(env_text, "WEBEX_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise ValueError("WEBEX_CLIENT_ID, WEBEX_CLIENT_SECRET ou WEBEX_REFRESH_TOKEN ausentes.")

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
    )
    resp.raise_for_status()
    tokens = resp.json()

    env_text = _update_env(env_text, "WEBEX_USER_TOKEN", tokens["access_token"])
    env_text = _update_env(env_text, "WEBEX_REFRESH_TOKEN", tokens["refresh_token"])

    # Grava metadados de expiração para o painel de observabilidade
    expires_in = tokens.get("expires_in", 1209600)
    now        = datetime.now(timezone.utc)
    info = {
        "refreshed_at":  now.isoformat(),
        "expires_in":    expires_in,
        "expires_at":    (now + timedelta(seconds=expires_in)).isoformat(),
        "refresh_expires_in": tokens.get("refresh_token_expires_in"),
    }
    TOKEN_INFO_FILE.write_text(json.dumps(info, indent=2), encoding="utf-8")
    _push_token_info_to_portal(info)

    return env_text


def _push_token_info_to_portal(token_info: dict) -> None:
    import os
    portal_url = os.getenv("PORTAL_URL", "")
    portal_key = os.getenv("PORTAL_API_KEY", "")
    if not portal_url or not portal_key:
        return
    try:
        requests.post(
            f"{portal_url.rstrip('/')}/api/v1/bot-status",
            json={"runs": [], "order_errors": {}, "token_info": token_info},
            headers={"Authorization": f"Bearer {portal_key}"},
            timeout=10,
        )
    except Exception:
        pass


def main():
    check_only = "--check" in sys.argv
    env_text = _read_env()
    current_token = _get_env_value(env_text, "WEBEX_USER_TOKEN")

    user = _validate_token(current_token)
    if user:
        print(f"Token OK — {user.get('displayName')} ({user.get('emails', ['?'])[0]})")
        return

    print("Token expirado ou inválido.")
    if check_only:
        sys.exit(1)

    # Tenta renovação automática via refresh_token
    has_refresh = bool(_get_env_value(env_text, "WEBEX_REFRESH_TOKEN"))
    if has_refresh:
        print("Renovando automaticamente via refresh token...")
        try:
            env_text = _refresh_via_oauth(env_text)
            ENV_FILE.write_text(env_text, encoding="utf-8")
            new_token = _get_env_value(env_text, "WEBEX_USER_TOKEN")
            user = _validate_token(new_token)
            print(f"Token renovado! {user.get('displayName')} ({user.get('emails', ['?'])[0]})")
            return
        except Exception as e:
            print(f"Falha na renovação automática: {e}")
            print("Caindo para renovação manual...\n")

    # Renovação manual
    print()
    print("1. Acesse: https://developer.webex.com")
    print("2. Clique no seu avatar → 'Copy personal access token'")
    print()

    while True:
        new_token = input("Cole o novo token: ").strip().strip('"').strip("'")
        if not new_token:
            print("Token vazio, tente novamente.")
            continue
        user = _validate_token(new_token)
        if user:
            env_text = _update_env(env_text, "WEBEX_USER_TOKEN", new_token)
            ENV_FILE.write_text(env_text, encoding="utf-8")
            print(f"Token atualizado! {user.get('displayName')}.")
            break
        print("Token inválido. Tente novamente.")


if __name__ == "__main__":
    main()
