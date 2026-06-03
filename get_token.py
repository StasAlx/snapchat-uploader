"""
Получение Snapchat OAuth2 токенов — ручной режим.

Использование:
    python get_token.py

Потребует ввести redirect_uri и URL после редиректа.
Требует .env с SNAP_CLIENT_ID и SNAP_CLIENT_SECRET.
"""
import os
import json
import urllib.parse
import webbrowser
from urllib.request import urlopen, Request
from urllib.parse import urlencode

from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ["SNAP_CLIENT_ID"]
CLIENT_SECRET = os.environ["SNAP_CLIENT_SECRET"]
TOKEN_URL = "https://accounts.snapchat.com/login/oauth2/access_token"
AUTH_URL = "https://accounts.snapchat.com/login/oauth2/authorize"


def main():
    print("=== Snapchat OAuth2 — получение токенов ===\n")

    redirect_uri = input(
        "Введи redirect_uri (тот же, что зарегистрирован в Snap App):\n"
        "Пример: https://quiz.mimika-app.com\n> "
    ).strip()

    auth_url = (
        f"{AUTH_URL}?client_id={CLIENT_ID}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&response_type=code&scope=snapchat-marketing-api"
    )

    print(f"\nОткрываю браузер...")
    webbrowser.open(auth_url)
    print("\nЕсли браузер не открылся — открой вручную:")
    print(f"  {auth_url}\n")

    print("После авторизации Snapchat перенаправит тебя на твой redirect_uri.")
    print("Страница может показать 404 — это нормально.")
    print("Скопируй ПОЛНЫЙ URL из адресной строки браузера.\n")

    redirected_url = input("Вставь сюда полный URL из адресной строки:\n> ").strip()

    # Извлекаем code из URL
    parsed = urllib.parse.urlparse(redirected_url)
    params = urllib.parse.parse_qs(parsed.query)
    code = params.get("code", [None])[0]

    if not code:
        print("\nОшибка: в URL не найден параметр ?code=...")
        print("Убедись, что скопировал полный URL после редиректа.")
        return

    print(f"\nКод получен: {code[:15]}...")

    # Обмениваем code на токены
    data = urlencode({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
    }).encode()

    req = Request(
        TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(req) as resp:
            tokens = json.loads(resp.read())
    except Exception as e:
        print(f"\nОшибка при обмене кода: {e}")
        return

    if "refresh_token" not in tokens:
        print(f"\nОшибка: {tokens}")
        return

    print("\n=== ТОКЕНЫ ПОЛУЧЕНЫ ===")
    print(f"expires_in: {tokens.get('expires_in')} сек ({tokens.get('expires_in', 0)//60} мин)")
    print()
    print("Добавь в .env:")
    print(f"SNAP_REFRESH_TOKEN={tokens['refresh_token']}")
    print()
    print("(access_token действует только 60 минут — скрипт обновляет его автоматически)")

    # Автоматически пишем в .env если файл есть
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            content = f.read()
        if "SNAP_REFRESH_TOKEN" in content:
            import re
            content = re.sub(r"SNAP_REFRESH_TOKEN=.*", f"SNAP_REFRESH_TOKEN={tokens['refresh_token']}", content)
        else:
            content += f"\nSNAP_REFRESH_TOKEN={tokens['refresh_token']}\n"
        with open(env_path, "w") as f:
            f.write(content)
        print("SNAP_REFRESH_TOKEN записан в .env автоматически.")


if __name__ == "__main__":
    main()
