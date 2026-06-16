# Fase 06: Deploy y Puesta en Marcha — VPS Hetzner + Docker + nginx

**Objetivo**: Desplegar AgroVoz en VPS Hetzner CX43 con Docker Compose, nginx como reverse proxy,
Let's Encrypt SSL, y CI/CD con GitHub Actions. Debe quedar productivo y accesible vía `https://agrovoz.cl`.
**Duración estimada**: 5 tareas (2-3 días)
**Dependencias**: Fase 03 completada (webhook Open-WA funcionando) + Fase 04 completada (landing)
**Archivos de contexto requeridos**:
- `AGENTS.md`
- `docs/ARCHITECTURE.md`

---

## Tareas

### T6.1: Provisioning del VPS Hetzner

- [ ] Contratar VPS Hetzner CX43 (8 vCPU, 16 GB RAM, 160 GB SSD, Ubuntu 24.04 LTS)
- [ ] Crear `scripts/provision-vps.sh`:
  ```bash
  #!/bin/bash
  # Provisioning inicial del VPS Hetzner para AgroVoz
  set -euo pipefail

  # Actualizar sistema
  apt update && apt upgrade -y

  # Dependencias esenciales
  apt install -y curl wget git ufw nginx certbot python3-certbot-nginx

  # Docker
  curl -fsSL https://get.docker.com | sh
  usermod -aG docker ubuntu
  systemctl enable docker

  # Docker Compose (standalone, más simple que el plugin para CI/CD)
  curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 -o /usr/local/bin/docker-compose
  chmod +x /usr/local/bin/docker-compose

  # Firewall
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow ssh
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable

  # Fail2ban
  apt install -y fail2ban
  systemctl enable fail2ban

  # Swap (4GB para margen con modelos de IA cargados)
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab

  # Directorio de la app
  mkdir -p /opt/agrovoz
  chown ubuntu:ubuntu /opt/agrovoz

  echo "=== Provisioning completo ==="
  echo "Próximo paso: configurar DNS (agrovoz.cl → IP del VPS)"
  ```
- [ ] Ejecutar script en el VPS (manual, una sola vez)
- [ ] Configurar DNS: `agrovoz.cl` A record → IP del VPS
- Archivos a crear: `scripts/provision-vps.sh`

### T6.2: Docker Compose producción

- [ ] Crear `docker-compose.prod.yml` en raíz del proyecto:
  ```yaml
  services:
    backend:
      build: ./backend
      container_name: agrovoz-backend
      restart: unless-stopped
      env_file:
        - .env.production
      volumes:
        - backend_models:/app/models    # Modelos IA (persisten entre deploys)
        - backend_data:/app/data        # SQLite DB + audio
      networks:
        - agrovoz-net
      logging:
        driver: json-file
        options:
          max-size: "10m"
          max-file: "3"
      healthcheck:
        test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
        interval: 30s
        timeout: 10s
        retries: 3

    # Open-WA: gateway WhatsApp self-hosted (protocolo WhatsApp Web via QR scan)
    # Dashboard para escanear QR NO se expone públicamente (ver T6.3, acceso via SSH tunnel)
    openwa:
      image: ghcr.io/rmyndharis/openwa:latest
      container_name: agrovoz-openwa
      restart: unless-stopped
      env_file:
        - .env.production
      environment:
        - NODE_ENV=production
        - DATABASE_PROVIDER=sqlite
        - API_KEY=${OPENWA_API_KEY}
        - WEBHOOK_URL=http://backend:8000/api/v1/webhook/whatsapp
        - WEBHOOK_SECRET=${OPENWA_WEBHOOK_SECRET}
      volumes:
        - openwa_data:/app/data    # Sesión WhatsApp persiste entre reinicios
      networks:
        - agrovoz-net
      logging:
        driver: json-file
        options:
          max-size: "10m"
          max-file: "3"

    nginx:
      image: nginx:1.27-alpine
      container_name: agrovoz-nginx
      restart: unless-stopped
      ports:
        - "80:80"
        - "443:443"
      volumes:
        - ./nginx/nginx.prod.conf:/etc/nginx/nginx.conf:ro
        - ./landing/dist:/usr/share/nginx/html/landing:ro
        - certbot_www:/var/www/certbot:ro
        - certbot_conf:/etc/letsencrypt
      depends_on:
        - backend
      networks:
        - agrovoz-net
      logging:
        driver: json-file
        options:
          max-size: "10m"
          max-file: "3"

    certbot:
      image: certbot/certbot:latest
      container_name: agrovoz-certbot
      volumes:
        - certbot_www:/var/www/certbot
        - certbot_conf:/etc/letsencrypt
      entrypoint: "/bin/sh -c 'trap exit TERM; while :; do certbot renew; sleep 12h & wait $${!}; done;'"

  volumes:
    backend_models:
    backend_data:
    openwa_data:
    certbot_www:
    certbot_conf:

  networks:
    agrovoz-net:
      driver: bridge
  ```
- [ ] Crear `.env.production.example`:
  ```
  # AgroVoz — Variables de entorno PRODUCCIÓN
  # Copiar a .env.production en el VPS: cp .env.production.example .env.production
  # NUNCA commitees .env.production al repo.

  APP_ENV=production
  DEBUG=false

  OPENWA_API_KEY=
  OPENWA_API_URL=http://openwa:2785
  OPENWA_WEBHOOK_SECRET=

  OPENWEATHER_API_KEY=

  WHISPER_MODEL=small
  LLM_MODEL_PATH=/app/models/qwen2.5-3b-q4_k_m.gguf
  PIPER_VOICE=es_ES-carlfm-x_low

  ADMIN_API_KEY=

  RATE_LIMIT_PER_MINUTE=60
  AUDIO_RETENTION_HOURS=24
  ```
- Archivos a crear: `docker-compose.prod.yml`, `.env.production.example`
- Archivos a modificar: `.gitignore` (agregar `.env.production`)

### T6.3: nginx reverse proxy + Let's Encrypt

- [ ] Crear `nginx/nginx.prod.conf`:
  ```nginx
  events {
      worker_connections 1024;
  }

  http {
      include /etc/nginx/mime.types;
      default_type application/octet-stream;

      # Logging
      access_log /var/log/nginx/access.log;
      error_log /var/log/nginx/error.log warn;

      # Rate limiting global
      limit_req_zone $binary_remote_addr zone=api_limit:10m rate=60r/m;

      # Gzip
      gzip on;
      gzip_types text/plain application/json text/css application/javascript;

      # ── HTTP → HTTPS redirect ──
      server {
          listen 80;
          server_name agrovoz.cl www.agrovoz.cl;

          # Let's Encrypt challenge
          location /.well-known/acme-challenge/ {
              root /var/www/certbot;
          }

          location / {
              return 301 https://$host$request_uri;
          }
      }

      # ── HTTPS ──
      server {
          listen 443 ssl;
          server_name agrovoz.cl www.agrovoz.cl;

          ssl_certificate /etc/letsencrypt/live/agrovoz.cl/fullchain.pem;
          ssl_certificate_key /etc/letsencrypt/live/agrovoz.cl/privkey.pem;
          ssl_protocols TLSv1.2 TLSv1.3;
          ssl_ciphers HIGH:!aNULL:!MD5;

          # Landing (estática, servida por nginx directo)
          location / {
              root /usr/share/nginx/html/landing;
              try_files $uri $uri/ /index.html;
          }

          # API (reverse proxy a FastAPI)
          location /api/ {
              limit_req zone=api_limit burst=10 nodelay;
              proxy_pass http://backend:8000;
              proxy_set_header Host $host;
              proxy_set_header X-Real-IP $remote_addr;
              proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
              proxy_set_header X-Forwarded-Proto $scheme;
              proxy_read_timeout 60s;  # Pipeline de voz puede tardar
          }

          # Admin dashboard (reverse proxy a FastAPI)
          location /admin/ {
              proxy_pass http://backend:8000;
              proxy_set_header Host $host;
              proxy_set_header X-Real-IP $remote_addr;
              proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
              proxy_set_header X-Forwarded-Proto $scheme;
          }

          # Audio responses (servir .ogg generados)
          location /audio/ {
              proxy_pass http://backend:8000;
              proxy_set_header Host $host;
              add_header Cache-Control "no-store";
          }
      }
  }
  ```
- [ ] Generar certificado SSL inicial (manual, una vez):
  ```bash
  docker compose -f docker-compose.prod.yml run --rm certbot \
    certonly --webroot --webroot-path=/var/www/certbot \
    --email sebastian.bravo77@inacapmail.cl \
    --agree-tos --no-eff-email \
    -d agrovoz.cl -d www.agrovoz.cl
  ```
- [ ] Recargar nginx después de obtener el certificado:
  ```bash
  docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
  ```
- Archivos a crear: `nginx/nginx.prod.conf`
- Archivos a modificar: nginx/nginx.conf existente renombrarlo a `nginx/nginx.dev.conf`

> **Open-WA — emparejamiento WhatsApp (una sola vez)**
>
> El dashboard de Open-WA (puerto 2886) NO se expone públicamente. Para escanear
> el QR y vincular el número WhatsApp por primera vez, usar SSH tunnel desde tu PC:
> ```bash
> ssh -L 2886:localhost:2886 -L 2785:localhost:2785 ubuntu@agrovoz.cl
> ```
> Luego abrir `http://localhost:2886`, crear sesión default, escanear QR con WhatsApp.
> La sesión queda persistida en el volumen `openwa_data` (sobrevive reinicios).
> Para cerrar el tunnel: Ctrl+C en la terminal SSH.

### T6.4: GitHub Actions CI/CD

- [ ] Crear `.github/workflows/deploy.yml`:
  ```yaml
  name: Deploy AgroVoz

  on:
    push:
      branches: [main]

  concurrency:
    group: deploy
    cancel-in-progress: false

  jobs:
    test-backend:
      name: Test Backend
      runs-on: ubuntu-24.04
      steps:
        - uses: actions/checkout@v4
        - uses: astral-sh/setup-uv@v5
          with:
            python-version: "3.12"
        - name: Install dependencies
          run: cd backend && uv sync --dev
        - name: Lint (ruff)
          run: cd backend && uv run ruff check app/
        - name: Type check (mypy)
          run: cd backend && uv run mypy app/
        - name: Test
          run: cd backend && uv run pytest tests/ -v --cov=app --cov-report=term-missing

    test-landing:
      name: Test Landing
      runs-on: ubuntu-24.04
      steps:
        - uses: actions/checkout@v4
        - uses: oven-sh/setup-bun@v2
          with:
            bun-version: latest
        - name: Install dependencies
          run: cd landing && bun install
        - name: Build
          run: cd landing && bun run build

    deploy:
      name: Deploy to VPS
      needs: [test-backend, test-landing]
      runs-on: ubuntu-24.04
      if: github.ref == 'refs/heads/main'
      steps:
        - uses: actions/checkout@v4

        - name: Build landing
          uses: oven-sh/setup-bun@v2
          with:
            bun-version: latest
        - run: cd landing && bun install && bun run build

        - name: Deploy via SSH
          uses: appleboy/ssh-action@v1
          with:
            host: ${{ secrets.VPS_HOST }}
            username: ${{ secrets.VPS_USER }}
            key: ${{ secrets.VPS_SSH_KEY }}
            script: |
              cd /opt/agrovoz
              git pull origin main
              docker compose -f docker-compose.prod.yml up -d --build backend
              docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
              docker system prune -f  # Limpiar imágenes viejas

        - name: Smoke test
          run: |
            sleep 5
            curl -f https://agrovoz.cl/api/v1/health || echo "WARNING: Health check falló"
  ```
- [ ] Configurar secrets en GitHub:
  - `VPS_HOST`: IP del VPS Hetzner
  - `VPS_USER`: `ubuntu`
  - `VPS_SSH_KEY`: clave privada SSH del VPS
- Archivos a crear: `.github/workflows/deploy.yml`

### T6.5: Scripts de operación y smoke test

- [ ] Crear `scripts/deploy.sh` (deploy manual desde local):
  ```bash
  #!/bin/bash
  # Deploy manual a VPS (alternativa si CI/CD no está disponible)
  set -euo pipefail
  VPS_HOST="${VPS_HOST:-}"
  VPS_USER="${VPS_USER:-ubuntu}"

  if [ -z "$VPS_HOST" ]; then
    echo "ERROR: VPS_HOST no definido. Ejecutar: VPS_HOST=<ip> ./scripts/deploy.sh"
    exit 1
  fi

  echo "→ Construyendo landing..."
  cd landing && bun install && bun run build && cd ..

  echo "→ Sincronizando código al VPS..."
  rsync -avz --exclude='.git' --exclude='models/' --exclude='data/' \
    --exclude='node_modules' --exclude='.venv' --exclude='__pycache__' \
    ./ "$VPS_USER@$VPS_HOST:/opt/agrovoz/"

  echo "→ Reconstruyendo backend en VPS..."
  ssh "$VPS_USER@$VPS_HOST" "\
    cd /opt/agrovoz && \
    docker compose -f docker-compose.prod.yml up -d --build backend && \
    docker compose -f docker-compose.prod.yml exec nginx nginx -s reload && \
    docker system prune -f"

  echo "→ Smoke test..."
  sleep 5
  curl -f https://agrovoz.cl/api/v1/health

  echo "✓ Deploy completo"
  ```
- [ ] Crear `scripts/smoke-test.sh`:
  ```bash
  #!/bin/bash
  # Smoke test post-deploy: verifica que todo el pipeline responde
  set -euo pipefail
  BASE_URL="${1:-https://agrovoz.cl}"

  echo "→ Smoke test: $BASE_URL"

  # 1. Health check
  echo "1. GET /api/v1/health"
  curl -fsS "$BASE_URL/api/v1/health" | jq . || { echo "✗ Health check falló"; exit 1; }

  # 2. API de precios
  echo "2. GET /api/v1/prices/papa"
  curl -fsS "$BASE_URL/api/v1/prices/papa" | jq . || echo "⚠ No hay datos de papa (esperable si DB vacía)"

  # 3. Landing responde
  echo "3. GET / (landing)"
  curl -fsS -o /dev/null "$BASE_URL/" || { echo "✗ Landing no responde"; exit 1; }

  # 4. Admin dashboard
  echo "4. GET /admin/"
  curl -fsS -o /dev/null "$BASE_URL/admin/" || echo "⚠ Admin no accesible (esperable sin API key)"

  # 5. SSL válido
  echo "5. Verificando SSL"
  echo | openssl s_client -connect agrovoz.cl:443 -servername agrovoz.cl 2>/dev/null | \
    openssl x509 -noout -dates || echo "⚠ No se pudo verificar SSL"

  echo "✓ Smoke test completo"
  ```
- Archivos a crear: `scripts/deploy.sh`, `scripts/smoke-test.sh`
- Hacer ejecutables: `chmod +x scripts/*.sh`

---

## Validación

- [ ] `scripts/provision-vps.sh` ejecutado en VPS sin errores
- [ ] `docker compose -f docker-compose.prod.yml up -d` levanta todos los servicios sin errores
- [ ] `docker compose -f docker-compose.prod.yml ps` muestra `healthy` en backend y nginx
- [ ] `curl https://agrovoz.cl/api/v1/health` retorna `{"status": "ok"}`
- [ ] `curl https://agrovoz.cl/` retorna la landing page (HTML)
- [ ] SSL válido: candado verde en navegador
- [ ] CI/CD: push a main → tests pasan → deploy automático
- [ ] Smoke test pasa: `bash scripts/smoke-test.sh`

Comandos:
```bash
# Verificar servicios
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs backend --tail 50

# Smoke test
bash scripts/smoke-test.sh

# Rollback (si algo falla)
ssh ubuntu@<vps-ip> "cd /opt/agrovoz && git log --oneline -5 && docker compose -f docker-compose.prod.yml up -d backend"
```

---

## Output esperado

```
scripts/
├── provision-vps.sh
├── deploy.sh
└── smoke-test.sh

nginx/
├── nginx.dev.conf           (renombrado de nginx.conf)
└── nginx.prod.conf          (NUEVO)

.github/
└── workflows/
    └── deploy.yml            (NUEVO)

docker-compose.prod.yml       (NUEVO)
.env.production.example       (NUEVO)
```

---

## Riesgos y notas

- **Certificado SSL inicial**: requiere que el DNS ya apunte al VPS. Si no, el certbot falla. Alternativa: usar IP directa para testeo inicial y configurar SSL después.
- **CI/CD con SSH**: la clave privada del VPS se guarda en GitHub Secrets. Rotarla periódicamente.
- **Modelos IA en volumen**: los modelos (~4-6 GB) se descargan UNA VEZ en el volumen `backend_models`. No se re-descargan en cada deploy.
- **Rollback simple**: si un deploy rompe algo, `git revert` + push, o manualmente `docker compose up -d backend` con la imagen anterior.
- **Base de datos SQLite**: está en volumen `backend_data`. Backup simple: `scp ubuntu@vps:/opt/agrovoz/data/agrovoz.db ./backups/`. Agregar cron de backup diario.
- **Monitoreo**: para MVP basta con `docker compose ps` y el health check. Post-MVP: UptimeRobot gratuito monitoreando `/api/v1/health`.
