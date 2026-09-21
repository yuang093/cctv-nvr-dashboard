# 部署指南（Deployment Guide）

> Week 5 新增：HTTPS 與 reverse proxy 章節（#013）
> 適用版本：v1+ Web UI（port 8444 dashboard / 8555 clips）

---

## 1. 基本部署

詳見 [overview.md](overview.md) 與 [api_endpoints.md](api_endpoints.md)。

最小部署：
```bash
git clone <repo>
cd dashboard
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements-web.txt
python -m web.app
# → http://127.0.0.1:8444
```

---

## 2. HTTPS 部署（Week 5 #013）

### 2.1 模式選擇

| 模式 | 適用 | 設定 |
|---|---|---|
| `none`（預設） | 開發 / 內網 | HTTP，無憑證 |
| `adhoc` | 開發 HTTPS（瀏覽器會警告自簽） | Werkzeug 自動生 self-signed |
| `cert`（reverse proxy 後方用） | 生產 HTTPS | 讀 PEM 檔 |

### 2.2 Dev：Werkzeug adhoc SSL

```bash
$ NVR_HTTPS_ENABLED=1 python -m web.app
# 或
$ python -m web.app --https=adhoc
# → https://127.0.0.1:8444（瀏覽器彈「不安全」警告 → 進階 → 繼續）
```

⚠️ **限制**：
- 每次重啟 server 會重新產生憑證，瀏覽器每次都要重新信任
- 僅供開發用，**不要**用在生產

### 2.3 Prod：Caddy（推薦，自動 Let's Encrypt）

```caddyfile
# /etc/caddy/Caddyfile（或 Caddy 任意位置）
nvr.example.com {
    reverse_proxy 127.0.0.1:8444
}
```

Caddy 會自動申請 + renew Let's Encrypt 憑證。Flask 仍跑 HTTP（無 `--https`）。

### 2.4 Prod：nginx

```nginx
# /etc/nginx/sites-available/nvr
server {
    listen 443 ssl http2;
    server_name nvr.example.com;

    ssl_certificate     /etc/letsencrypt/live/nvr.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/nvr.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8444;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Let's Encrypt 申請：
```bash
sudo certbot --nginx -d nvr.example.com
# 或手動：certbot certonly --nginx -d nvr.example.com
```

### 2.5 Prod：自管 PEM（特殊場景）

如果不用 reverse proxy，要讓 Flask 直接 HTTPS：

```bash
python -m web.app --https=cert --cert /etc/ssl/certs/nvr.crt --key /etc/ssl/private/nvr.key
```

⚠️ 憑證 renewal 需手動（建議用 reverse proxy + Let's Encrypt）。

---

## 3. 環境變數總覽

### 3.1 Web（dashboard / clips）

| 變數 | 預設 | 說明 |
|---|---|---|
| `NVR_WEB_HOST` | `127.0.0.1` | bind IP（Day-0 預設只綁本機） |
| `NVR_WEB_PORT` | `8444`（dashboard）/ `8555`（clips） | HTTP/HTTPS port |
| `NVR_DB_PATH` | `./nvr_scan.db` | SQLite 檔路徑 |
| `NVR_HTTPS_ENABLED` | `0` | `1` = Werkzeug adhoc SSL（dev 用） |

### 3.2 資安（Week 5）

| 變數 | 預設 | 說明 |
|---|---|---|
| `NVR_AUTH_ENABLED` | `0` | `1` = Flask-Login + IP 白名單（Week 6 翻） |
| `NVR_AUDIT_ENABLED` | `0` | `1` = 寫 audit_log（Week 6 翻） |
| `NVR_RATE_LIMIT_ENABLED` | `0` | `1` = 啟用 flask-limiter（Week 6 翻） |
| `NVR_NVR_DOWNSCOPED` | `0` | `1` = 驗證 NVR 帳號 api_reader 權限（Week 6 翻） |
| `NVR_OPS_TRUSTED_CIDRS` | 內網 4 段 | 內網白名單（逗號分隔 CIDR） |
| `NVR_ADMIN_DEFAULT_PASSWORD` | `admin` | 預設 admin 密碼（首次啟動用） |

---

## 4. NSSM / systemd 範例

### 4.1 NSSM（Windows 工作排程器）

```cmd
nssm install NVR-Dashboard "C:\cc\NVR\dashboard\venv\Scripts\python.exe" "-m web.app"
nssm set NVR-Dashboard AppDirectory "C:\cc\NVR\dashboard"
nssm set NVR-Dashboard AppEnvironmentExtra NVR_WEB_PORT=8444 NVR_DB_PATH=C:\cc\NVR\dashboard\nvr_scan.db
nssm start NVR-Dashboard
```

### 4.2 systemd（Linux）

```ini
# /etc/systemd/system/nvr-dashboard.service
[Unit]
Description=NVR Dashboard Web UI
After=network.target

[Service]
Type=simple
User=nvr
WorkingDirectory=/opt/nvr
Environment="NVR_WEB_HOST=127.0.0.1"
Environment="NVR_WEB_PORT=8444"
Environment="NVR_DB_PATH=/opt/nvr/nvr_scan.db"
ExecStart=/opt/nvr/venv/bin/python -m web.app
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nvr-dashboard
```
