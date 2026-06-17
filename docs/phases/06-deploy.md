# Fase 06: Deploy y Puesta en Marcha — VPS Hetzner + Dokploy

**Objetivo**: Desplegar AgroVoz en VPS Hetzner CX43 con Dokploy (Docker + Traefik + SSL automático).
Deben quedar productivos y accesibles vía `https://agrovoz.cl` (landing) y `https://api.agrovoz.cl` (backend/admin).
**Duración estimada**: 4 tareas (2-3 días)
**Dependencias**: Fase 03 completada (webhook Open-WA funcionando) + Fase 04 completada (landing)
**Archivos de contexto requeridos**:
- `AGENTS.md`
- `docs/ARCHITECTURE.md`

---

## Tareas

### T6.1: Provisioning del VPS Hetzner + Dokploy

- [ ] Contratar VPS Hetzner CX43 (8 vCPU, 16 GB RAM, 160 GB SSD, Ubuntu 24.04 LTS)
- [ ] Crear `scripts/provision-vps.sh`:
  ```bash
  #!/bin/bash
  # Provisioning inicial del VPS Hetzner para AgroVoz con Dokploy
  set -euo pipefail

  # Actualizar sistema
  apt update && apt upgrade -y

  # Dependencias esenciales
  apt install -y curl wget git ufw

  # Docker (Dokploy lo requiere)
  curl -fsSL https://get.docker.com | sh
  usermod -aG docker ubuntu
  systemctl enable docker

  # Dokploy: instala Docker + Traefik + dashboard automaticamente
  # Requisitos: Ubuntu 22.04+/24.04, >=2GB RAM, >=30GB disco, dominio con A record
  curl -sSL https://dokploy.com/install.sh | sudo bash

  # Firewall (Dokploy usa Traefik en 80/443, dashboard en 3000)
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow ssh
  ufw allow 80/tcp    # Traefik HTTP
  ufw allow 443/tcp   # Traefik HTTPS
  ufw allow 3000/tcp  # Dokploy dashboard
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
  echo "Dokploy instalado. Acceder a http://<vps-ip>:3000 para configurar dominio."
  echo "Próximo paso: configurar DNS (agrovoz.cl → IP del VPS)"
  ```
- [ ] Ejecutar script en el VPS (manual, una sola vez)
- [ ] Configurar DNS:
  - `agrovoz.cl` A record → IP del VPS (landing)
  - `api.agrovoz.cl` A record → misma IP (backend/admin)
- [ ] Acceder a `http://<vps-ip>:3000`, primer setup:
  - Crear cuenta admin
  - Configurar dominio principal: `agrovoz.cl`
  - Traefik obtiene SSL automatico via Let's Encrypt
- Archivos a crear: `scripts/provision-vps.sh`

### T6.2: Docker Compose para Dokploy

- [ ] Crear `docker-compose.prod.yml` en raíz del proyecto:
  ```yaml
  services:
    backend:
      build: ./backend
      restart: unless-stopped
      env_file:
        - .env.production
      volumes:
        - backend_models:/app/models    # Modelos IA (persisten entre deploys)
        - backend_data:/app/data        # SQLite DB + audio
      expose:
        - "8000"                        # Exponer a Traefik, NO bind host port
      networks:
        - dokploy-network              # Unir a red de Dokploy para dominio
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
    # Dashboard para escanear QR NO se expone públicamente (acceso via SSH tunnel)
    openwa:
      image: ghcr.io/rmyndharis/openwa:latest
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
      expose:
        - "2785"                   # Para backend interno
      networks:
        - dokploy-network
      logging:
        driver: json-file
        options:
          max-size: "10m"
          max-file: "3"

  volumes:
    backend_models:
    backend_data:
    openwa_data:

  networks:
    dokploy-network:
      external: true               # Red externa creada por Dokploy
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

### T6.3: Crear apps en Dokploy + dominios + SSL

- [ ] **Backend app** (en dashboard Dokploy):
  - Type: Docker Compose
  - Name: `agrovoz-backend`
  - Repository: conectar repo GitHub `sebitabravo/AgroVoz`
  - Branch: `main`
  - Compose path: `docker-compose.prod.yml`
  - Environment: auto-generar `.env` desde `.env.production` (Dokploy lo crea en `/etc/dokploy/compose/agrovoz-backend/.env`)
  - Domains:
    - `api.agrovoz.cl`
    - Enable HTTPS (Traefik auto- Let's Encrypt)
- [ ] **Landing app** (opción A: Dokploy static):
  - Type: Static
  - Name: `agrovoz-landing`
  - Build: conectar repo, `cd landing && bun run build`
  - Output directory: `landing/dist`
  - Domain: `agrovoz.cl`
  - HTTPS: enabled
- [ ] **Landing app** (opción B: Cloudflare Pages, más simple):
  - Conectar repo `sebitabravo/AgroVoz` en CF dashboard
  - Build command: `cd landing && bun install && bun run build`
  - Output directory: `dist`
  - Root directory: `landing`
  - Domain: `agrovoz.cl`
  - CF maneja SSL automaticamente
- [ ] **Verificar routing**:
  - `https://agrovoz.cl` → landing page
  - `https://api.agrovoz.cl/` → backend (404 esperado si root no definido)
  - `https://api.agrovoz.cl/api/v1/health` → `{"status": "ok"}`
  - `https://api.agrovoz.cl/admin/` → dashboard admin (con header `X-Admin-Key`)

> **Open-WA — emparejamiento WhatsApp (una sola vez)**
>
> El dashboard de Open-WA (puerto 2886) NO se expone públicamente. Para escanear
> el QR y vincular el número WhatsApp por primera vez, usar SSH tunnel desde tu PC:
> ```bash
> ssh -L 2886:localhost:2886 -L 2785:localhost:2785 ubuntu@api.agrovoz.cl
> ```
> Luego abrir `http://localhost:2886`, crear sesión default, escanear QR con WhatsApp.
> La sesión queda persistida en el volumen `openwa_data` (sobrevive reinicios).
> Para cerrar el tunnel: Ctrl+C en la terminal SSH.

### T6.4: CI/CD como quality gate (deploy por Dokploy)

- [ ] NO crear workflow `deploy.yml` — Dokploy hace auto-deploy en cada push a `main`
- [ ] `.github/workflows/ci.yml` ya existe como quality gate:
  - Lint (ruff), type check (mypy), tests (pytest) para backend
  - Build check para landing
  - Compose config check
  - Solo deja pasar código que pasa todas las validaciones
- [ ] Dokploy auto-deploy:
  - Cuando el CI pasa y se mergea a `main`, Dokploy detecta el cambio
  - Reconstruye imágenes (`docker compose build`)
  - Reinicia servicios (`docker compose up -d`)
  - Zero-downtime por rolling restart

### T6.5: Script de smoke test

- [ ] Crear `scripts/smoke-test.sh`:
  ```bash
  #!/bin/bash
  # Smoke test post-deploy: verifica que todo el pipeline responde
  set -euo pipefail
  BASE_URL="${1:-https://api.agrovoz.cl}"
  LANDING_URL="${2:-https://agrovoz.cl}"

  echo "→ Smoke test: $BASE_URL"

  # 1. Health check
  echo "1. GET /api/v1/health"
  curl -fsS "$BASE_URL/api/v1/health" | jq . || { echo "✗ Health check falló"; exit 1; }

  # 2. API de precios
  echo "2. GET /api/v1/prices/papa"
  curl -fsS "$BASE_URL/api/v1/prices/papa" | jq . || echo "⚠ No hay datos de papa (esperable si DB vacía)"

  # 3. Landing responde
  echo "3. GET / (landing)"
  curl -fsS -o /dev/null "$LANDING_URL" || { echo "✗ Landing no responde"; exit 1; }

  # 4. Admin dashboard
  echo "4. GET /admin/"
  curl -fsS -o /dev/null "$BASE_URL/admin/" || echo "⚠ Admin no accesible (esperable sin API key)"

  # 5. SSL válido
  echo "5. Verificando SSL (api)"
  echo | openssl s_client -connect api.agrovoz.cl:443 -servername api.agrovoz.cl 2>/dev/null | \
    openssl x509 -noout -dates || echo "⚠ No se pudo verificar SSL api"

  echo "6. Verificando SSL (landing)"
  echo | openssl s_client -connect agrovoz.cl:443 -servername agrovoz.cl 2>/dev/null | \
    openssl x509 -noout -dates || echo "⚠ No se pudo verificar SSL landing"

  echo "✓ Smoke test completo"
  ```
- Archivos a crear: `scripts/smoke-test.sh`
- Hacer ejecutable: `chmod +x scripts/smoke-test.sh`

---

## Validación

- [ ] `scripts/provision-vps.sh` ejecutado en VPS sin errores
- [ ] Dokploy dashboard accesible en `http://<vps-ip>:3000`
- [ ] Dominios configurados: `agrovoz.cl`, `api.agrovoz.cl` apuntando al VPS
- [ ] Apps creadas en Dokploy: `agrovoz-backend` + landing (static o CF Pages)
- [ ] HTTPS automatico activo (candado verde en navegador)
- [ ] `docker compose -f docker-compose.prod.yml ps` muestra `healthy` en backend y openwa
- [ ] `curl https://api.agrovoz.cl/api/v1/health` retorna `{"status": "ok"}`
- [ ] `curl https://agrovoz.cl/` retorna la landing page (HTML)
- [ ] CI/CD: push a main → tests pasan → Dokploy auto-deploy
- [ ] Smoke test pasa: `bash scripts/smoke-test.sh`

Comandos:
```bash
# Verificar servicios en VPS
ssh ubuntu@<vps-ip> "cd /opt/agrovoz && docker compose -f docker-compose.prod.yml ps"
ssh ubuntu@<vps-ip> "cd /opt/agrovoz && docker compose -f docker-compose.prod.yml logs backend --tail 50"

# Smoke test desde local
bash scripts/smoke-test.sh

# Logs Dokploy (dashboard o directo)
ssh ubuntu@<vps-ip> "docker logs dokploy -f"
```

---

## Output esperado

```
scripts/
├── provision-vps.sh
└── smoke-test.sh

docker-compose.prod.yml       (NUEVO)
.env.production.example       (NUEVO)
```

**Eliminados** (ya no necesarios con Dokploy):
- `nginx/` (todo el directorio)
- `scripts/deploy.sh`
- `.github/workflows/deploy.yml`

---

## Riesgos y notas

- **Dokploy prerequisitos**: dominio con A record apuntando al VPS ANTES de configurar dominios en dashboard. Si no, Traefik no puede obtener certificado Let's Encrypt.
- **Red externa dokploy-network**: Dokploy crea esta red automaticamente. El compose debe usar `external: true` para unirse.
- **Sin container_name**: Dokploy maneja nombres de contenedor automaticamente. No especificar `container_name` en compose.
- **Ports vs expose**: NO usar `ports: "80:80"` o similar. Traefik ya reserva 80/443 en el host. Usar `expose` para comunicacion interna.
- **SSL automatico**: Traefik obtiene certificados Let's Encrypt al vuelto. No hay comando manual como con certbot.
- **Rollback simple**: si un deploy rompe algo, `git revert` + push. Dokploy redeploya la version anterior. O manualmente `docker compose up -d --force-recreate` con commit anterior.
- **Base de datos SQLite**: está en volumen `backend_data`. Backup simple: `scp ubuntu@vps:/opt/agrovoz/data/agrovoz.db ./backups/`. Agregar cron de backup diario.
- **Monitoreo**: para MVP basta con dashboard Dokploy + health check. Post-MVP: UptimeRobot gratuito monitoreando `/api/v1/health`.
- **Landing en Cloudflare Pages**: alternativa más simple que Dokploy static si el equipo prefiere CF para frontend (CDN global, cache, preview deployments).
