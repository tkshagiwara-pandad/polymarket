"""Telegram通知テストスクリプト"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("APPRISE_URLS", "")
if not url:
    print("❌ APPRISE_URLS が .env に設定されていません")
    sys.exit(1)

import apprise
ap = apprise.Apprise()
if not ap.add(url):
    print(f"❌ URL が無効です: {url}")
    sys.exit(1)

print(f"📤 送信中... ({url[:20]}...)")
result = ap.notify(
    title="BTC Bot テスト",
    body="✅ Telegram通知の設定が完了しました！\nBTCボットが約定した時にここに通知が届きます。"
)
print("✅ 送信成功！" if result else "❌ 送信失敗。トークン/IDを確認してください。")
