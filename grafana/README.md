# Grafana セットアップ手順

## ローカル環境（Mac/Linux）でのインストール

```bash
# Mac
brew install grafana
brew services start grafana

# Ubuntu/Debian
sudo apt-get install -y gnupg curl
curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor | sudo tee /etc/apt/keyrings/grafana.gpg
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | sudo tee /etc/apt/sources.list.d/grafana.list
sudo apt-get update && sudo apt-get install grafana
sudo systemctl start grafana-server
```

## アクセス
- URL: http://localhost:3000
- ユーザー: admin
- パスワード: admin

## SQLite プラグインのインストール

```bash
grafana-cli plugins install frser-sqlite-datasource
sudo systemctl restart grafana-server
```

## データソース接続

1. Grafana → Configuration → Data Sources → Add data source
2. "SQLite" を選択
3. Path: `/home/user/polymarket/data/trades.db`

## ダッシュボードのインポート

1. Grafana → Dashboards → Import
2. `grafana/dashboard.json` をアップロード
