# VPN Prime Panel

Панель управления VPN-сервисом на базе VLESS Reality.  
Поддерживает несколько серверов (кластер), тарифы по трафику, Telegram-уведомления.

---

## Стек технологий

| Слой | Технология |
|---|---|
| Backend | FastAPI + Uvicorn |
| База данных | SQLite (aiosqlite) + SQLAlchemy 2.0 async |
| Шаблоны | Jinja2 + Bootstrap 5 CDN |
| Сессии | itsdangerous (cookie-based, без JWT) |
| Пароли | passlib[bcrypt] |
| Фоновые задачи | APScheduler (asyncio) |
| VLESS API | httpx → Marzban/3x-ui REST API |
| Шифрование паролей | cryptography (Fernet) |
| Email | Resend.com REST API |
| Telegram | Bot API (httpx) |
| QR-коды | qrcode[pil] |
| Конфиг | pydantic-settings (.env) |
| Reverse proxy | Nginx |
| SSL | Let's Encrypt (certbot) |

---

## Архитектура

```
Клиент (Happ / v2rayNG / Hiddify)
        │
        │  GET /sub/{token}/json
        ▼
     Nginx (443)
        │
        ▼
  vpn-panel (FastAPI, :8080)
        │
        ├── /admin/*        → Jinja2 HTML (CRUD клиентов, серверов)
        ├── /cabinet        → Личный кабинет
        ├── /sub/{token}/*  → Генерация подписки (JSON/v2ray/Clash)
        └── /auth/*         → Логин, регистрация, сброс пароля
        │
        ▼
  VLESS API (Marzban, :8100, только localhost)
        │
        ├── Сервер NL (Нидерланды)
        └── Сервер RU (Россия)
```

### Мульти-серверный кластер

Каждый сервер добавляется через Админ → Серверы.  
У каждого сервера есть поле **min_plan** (`lite` / `standard` / `family`):

| Тариф клиента | Серверы в подписке |
|---|---|
| Lite | только серверы с `min_plan = lite` |
| Standard | lite + standard серверы |
| Family | все серверы |

Подписка клиента автоматически содержит ссылки со **всех** доступных серверов.  
Приложение само выбирает быстрейший.

### Тарифы

| Тариф | Трафик/мес | Серверы |
|---|---|---|
| Lite | 100 ГБ | только базовые |
| Standard | 500 ГБ | все |
| Family | Безлимит | все |

---

## Структура проекта

```
vpn-panel/
├── main.py              # FastAPI app, lifespan, routers
├── config.py            # pydantic-settings, .env
├── database.py          # SQLAlchemy async engine + session
├── models.py            # ORM: User, Client, Server, Payment, Promo, AuditLog
├── auth.py              # bcrypt, session helpers, require_user/require_admin
├── service.py           # Бизнес-логика: create_client, extend, deactivate...
├── subscription.py      # Сборка подписок (XRay JSON / v2ray / Clash)
├── scheduler.py         # APScheduler: трафик, истечения, health check
├── vless_api.py         # HTTP-клиент к VLESS API (Marzban)
├── crypto.py            # Fernet encrypt/decrypt для паролей серверов
├── email_service.py     # Resend.com
├── telegram.py          # Telegram Bot API
├── migrate_to_v2.py     # Одноразовый скрипт миграции БД
├── requirements.txt
├── .env.example
├── routers/
│   ├── auth.py          # /auth/login, register, reset
│   ├── subscription.py  # GET /sub/{token}[/json|/v2ray|/clash]
│   ├── webhook.py       # POST /webhook/* (платёжные системы)
│   ├── admin/
│   │   ├── dashboard.py # /admin, /admin/audit, /admin/payments
│   │   ├── clients.py   # CRUD клиентов
│   │   ├── servers.py   # CRUD серверов
│   │   └── promo.py     # Промокоды
│   └── client/
│       └── cabinet.py   # /cabinet (личный кабинет)
├── templates/           # Jinja2 HTML
└── static/              # CSS + JS
```

---

## Деплой на сервере (Ubuntu 24.04)

### 0. Предварительные требования

- Ubuntu 24.04
- Python 3.11+
- Nginx
- Marzban (или другая VLESS API панель) на `127.0.0.1:8100`
- Домен с DNS A-записью на IP сервера

### 1. Клонировать репозиторий

```bash
cd /opt
git clone https://github.com/zlov-33/My-vpn.git vpn-panel
cd /opt/vpn-panel/vpn-panel
```

### 2. Python окружение

```bash
apt install python3-venv python3-pip -y
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Настроить .env

```bash
cp .env.example .env
nano .env
```

```env
SERVER_HOST=0.0.0.0
SERVER_PORT=8080
SECRET_KEY=           # python3 -c "import secrets; print(secrets.token_hex(32))"

ADMIN_EMAIL=admin@vpn-prime.ru
ADMIN_PASSWORD=       # надёжный пароль

ENCRYPTION_KEY=       # python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

VLESS_API_URL=http://127.0.0.1:8100
VLESS_API_USER=admin
VLESS_API_PASS=       # пароль Marzban admin

SITE_URL=https://vpn-prime.ru
SUB_DEFAULT_FORMAT=json

TELEGRAM_BOT_TOKEN=
TELEGRAM_ADMIN_CHAT_ID=

RESEND_API_KEY=
RESEND_FROM_EMAIL=noreply@vpn-prime.ru

DATABASE_URL=sqlite+aiosqlite:///./vpn_panel.db
```

### 4. Проверить запуск вручную

```bash
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8080
# В другом терминале: curl http://localhost:8080/auth/login
# Ctrl+C после проверки
```

### 5. Systemd сервис

```bash
nano /etc/systemd/system/vpn-panel.service
```

```ini
[Unit]
Description=VPN Prime Panel
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/vpn-panel/vpn-panel
Environment=PATH=/opt/vpn-panel/vpn-panel/venv/bin
ExecStart=/opt/vpn-panel/vpn-panel/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable vpn-panel
systemctl start vpn-panel
systemctl status vpn-panel
```

### 6. Nginx

```bash
nano /etc/nginx/sites-available/vpn-panel
```

```nginx
server {
    listen 80;
    server_name vpn-prime.ru www.vpn-prime.ru;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name vpn-prime.ru www.vpn-prime.ru;

    ssl_certificate     /etc/letsencrypt/live/vpn-prime.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/vpn-prime.ru/privkey.pem;

    location /sub/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        add_header Cache-Control "no-store";
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
ln -s /etc/nginx/sites-available/vpn-panel /etc/nginx/sites-enabled/
nginx -t && nginx -s reload
```

### 7. SSL

```bash
apt install certbot python3-certbot-nginx -y
certbot --nginx -d vpn-prime.ru -d www.vpn-prime.ru
```

### 8. Миграция БД (если есть старые данные)

```bash
cd /opt/vpn-panel/vpn-panel
source venv/bin/activate
python3 migrate_to_v2.py --dry-run   # проверить
python3 migrate_to_v2.py             # применить
```

### 9. Первый запуск — добавить сервер в панели

1. Открыть `https://vpn-prime.ru/auth/login`
2. Войти с `ADMIN_EMAIL` / `ADMIN_PASSWORD` из `.env`
3. Перейти **Серверы → Добавить сервер**:
   - API URL: `http://127.0.0.1:8100`
   - API User: `admin`
   - API Pass: пароль Marzban
   - Reality SNI: `max.ru` (или ваш домен-мишень)
   - Min plan: `Lite`
4. Создать тестового клиента: **Клиенты → Новый клиент**
5. Открыть его карточку → скопировать Subscription URL → вставить в Happ

---

## Обновление

```bash
cd /opt/vpn-panel
git pull origin main
cd vpn-panel
source venv/bin/activate
pip install -r requirements.txt
systemctl restart vpn-panel
```

---

## Добавление второго сервера в кластер

1. На новом сервере установить Marzban/3x-ui
2. В панели **Серверы → Добавить**:
   - API URL: `https://node2.vpn-prime.ru:8100` (или через SSH-туннель)
   - Min plan: `lite` (все клиенты) или `standard` (только Standard+)
3. Существующие клиенты получат новый сервер в подписку **автоматически** при следующем обновлении конфига в приложении

> Новые клиенты, созданные после добавления сервера, будут зарегистрированы на нём сразу при создании.

---

## Фоновые задачи (APScheduler)

| Задача | Интервал | Что делает |
|---|---|---|
| `refresh_traffic_stats` | 1 час | Синхронизирует трафик из VLESS API |
| `check_expiring_subscriptions` | 1 час | Уведомляет за 3 и 1 день до истечения |
| `deactivate_expired_subscriptions` | 1 час | Отключает истёкших клиентов |
| `check_servers_health` | 5 минут | Пингует все серверы, уведомляет при падении |

Ручная синхронизация: **Дашборд → Синхронизировать трафик**.

---

## Форматы подписки

| URL | Формат | Приложения |
|---|---|---|
| `/sub/{token}` | по умолчанию (`.env SUB_DEFAULT_FORMAT`) | — |
| `/sub/{token}/json` | XRay JSON с routing rules | Happ, Hiddify, v2rayN |
| `/sub/{token}/v2ray` | base64 VLESS ссылок | v2rayNG, NekoBox |
| `/sub/{token}/clash` | YAML | Clash Meta |

Routing rules в JSON-конфиге: российские сервисы (ВКонтакте, Сбербанк, Госуслуги и др.) идут **напрямую**, не через VPN.
