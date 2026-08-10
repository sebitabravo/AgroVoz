# Deployment — AgroVoz en producción

Documenta el deploy real (agosto 2026), que reemplaza la arquitectura planeada
originalmente en #9 (Hetzner CX43 + Dokploy standalone + UFW). Referencia para
mantener/reproducir el deploy, y "definición de hecho" real de #9, #7, #306 y
#307.

## Por qué cambió el plan

#9 planeaba: VPS Hetzner CX43 dedicado, Dokploy standalone con su propio
Traefik haciendo TLS, firewall UFW, `agrovoz.cl`/`api.agrovoz.cl`.

Lo que existe realmente: un NAS Proxmox propio (control plane permanente) y un
VPS Hostinger con fecha de expiración conocida (abril 2027) que ya corre
**Pangolin** (gerbil + traefik + crowdsec) como ingress para otros servicios,
y un **Proxmox Backup Server** nativo. Meter Dokploy standalone ahí habría
competido por los puertos 80/443 con Pangolin y duplicado TLS/proxy.

Decisión: Dokploy corre en el NAS (control plane, sobrevive al VPS). El VPS
Hostinger se registra como **Remote Server** de ese Dokploy — Dokploy le habla
por SSH saliente, no necesita un Dokploy propio. Pangolin (ya en el VPS) hace
de ingress; el Traefik propio de Dokploy queda deshabilitado en ese server.
Los dominios usan la zona `sbravo.app` de Pangolin, no `agrovoz.cl`.

Reemplazar el VPS en abril 2027 es: borrar el Remote Server, agregar el
reemplazo, redeploy. Env vars, secrets, historial de deploys y config viven en
el Postgres de Dokploy del NAS — no se pierden con el VPS.

## Arquitectura

```
NAS (Proxmox, permanente)
└── LXC 101: Dokploy (control plane)
    ├── project "AgroVoz"
    │   ├── compose "agrovoz-backend"   (backend FastAPI + Open-WA)
    │   └── application "AgroVoz Landing" (Astro estático)
    └── Remote Server "VPS Hostinger" ──SSH──┐
                                              ▼
VPS Hostinger 93.127.212.206 (expira abril 2027)
├── Pangolin (gerbil + traefik + crowdsec) — ingress, TLS, 80/443
│   ├── site "vps-hostinger-local" (type=local)
│   ├── resource agrovoz.sbravo.app  → target agrovoz-backend:8000
│   └── resource landing.sbravo.app  → target app-<random>:80 (swarm service)
├── docker compose (Dokploy compose): backend + openwa
│   └── red "pangolin" (alias agrovoz-backend) + red "dokploy-network"
├── docker swarm service (Dokploy application): landing (nginx + estático)
│   └── red "dokploy-network" (overlay, attachable)
├── Ollama (sin relación con AgroVoz, límites propios)
└── Proxmox Backup Server nativo (sin relación con AgroVoz)
```

Dos modelos de deploy conviven en el mismo Dokploy:

- **compose** (backend): contenedores planos (`docker compose up`), se unen
  directo a la red `pangolin` con un alias de red.
- **application** (landing): corre como **Docker Swarm service**
  (`docker service create`) en `dokploy-network` (sí es overlay+attachable).
  No puede unirse a `pangolin` (red bridge simple) — en cambio, `gerbil` (el
  contenedor de Pangolin que comparte netns con traefik) se conectó a
  `dokploy-network`, persistido en `/opt/pangolin/docker-compose.yml`.

**Gotcha central de todo el deploy:** `127.0.0.1` dentro de un contenedor es
siempre su propio loopback, nunca el del host. Publicar el backend en
`127.0.0.1:8000:8000` (loopback del host) lo hacía inalcanzable para
Traefik/Pangolin (corren en otro contenedor) — 502 Bad Gateway pese a que el
backend respondía sano en el VPS mismo. La solución en ambos casos es unir el
contenedor de la app a una red que Traefik también integre, nunca publicar en
loopback ni en `0.0.0.0` (eso sí lo expondría directo a internet, saltándose
Pangolin).

## Reproducir desde cero

### 1. Prerrequisitos en el VPS destino

- Docker instalado, Swarm activo de un solo nodo (`docker swarm init`).
- **Si el VPS ya corre otro reverse proxy en 80/443** (como Pangolin acá):
  pre-crear `docker_gwbridge` con subred explícita ANTES de `swarm init`, para
  que no colisione con las redes existentes (`docker network create --subnet
  172.30.0.0/16 -o com.docker.network.bridge.name=docker_gwbridge ...`).
- Clave SSH root del VPS agregada a `authorized_keys`.

### 2. Registrar el VPS como Remote Server en Dokploy

En Dokploy (NAS): **Servers → Add Server**, IP/puerto/usuario del VPS, SSH key.
Antes de "Setup Server", usar **"Modify Script"**: el script por defecto
instala su propio Traefik (`dokploy-traefik`, puertos 80/443) — si el VPS ya
tiene un ingress (Pangolin u otro), hay que sacar ese bloque completo del
script antes de correrlo. El resto (Docker, red `dokploy-network`, Nixpacks/
Buildpacks/Railpack) queda igual.

`server.validate` (o el botón equivalente en la UI) debe devolver todo
`enabled: true` y `isDokployNetworkInstalled: true` antes de seguir.

### 3. Repo privado — SSH deploy key

Este repo es privado. Clonar por HTTPS sin credenciales falla con `could not
read Username for 'https://github.com'`. Se necesita:

1. Generar una SSH key en Dokploy (Settings → SSH Keys), o vía la API
   (`sshKey.generate` + `sshKey.create`).
2. Agregar su clave pública como **Deploy Key de solo lectura** en GitHub:
   `gh repo deploy-key add --allow-write=false`.
3. Usar `git@github.com:sebitabravo/AgroVoz.git` (formato SSH, no HTTPS) como
   URL del repo en Dokploy, con esa key asociada.

### 4. Backend (Dokploy Compose)

Project → Create Compose, apuntar a `docker-compose.prod.yml`, branch `main`.

**Secrets obligatorios** (la app bloquea el arranque sin ellos) — generar cada
uno con `openssl rand -hex 32` o equivalente, nunca reusar:

| Variable | Uso |
|---|---|
| `OPENWA_API_KEY` | Auth compartida backend↔Open-WA |
| `OPENWA_WEBHOOK_SECRET` | HMAC de los webhooks de Open-WA |
| `ADMIN_API_KEY` | Endpoints JSON del dashboard admin |
| `ADMIN_SESSION_SECRET` | Firma de la cookie de sesión del dashboard (itsdangerous) |
| `PHONE_HASH_PEPPER` | Pepper HMAC-SHA256 para anonimizar teléfonos |

**Variables que el compose original no pasaba al contenedor** (hardcoded a
`agrovoz.cl`, faltan si el dominio real es otro) — agregar en el `environment:`
del servicio `backend` con su propio `${VAR:-default}`, y setear el valor real
en Dokploy:

| Variable | Por qué | Valor en este deploy |
|---|---|---|
| `EXTRA_ALLOWED_HOSTS` | `TrustedHostMiddleware` solo acepta `agrovoz.cl`/`.agrovoz.cl` por defecto → 400 "Invalid host header" a todo tráfico real | `agrovoz.sbravo.app` |
| `CORS_ORIGINS` | Igual, solo `agrovoz.cl`/`www.agrovoz.cl` por defecto → el navegador bloquea el fetch del demo desde la landing | `https://landing.sbravo.app,https://agrovoz.sbravo.app` |
| `DEMO_ENDPOINT_ENABLED` | Apagado por diseño en prod ("evitar abuso del LLM/TTS"), decisión de producto no de infra — activar solo si se quiere el demo público (rate limit 5/min ya incluido en el código) | `true` |

**Networking** — el compose original asumía el Traefik propio de Dokploy
(`expose` + `dokploy-network`, sin publicar puertos). Con Pangolin como
ingress: `backend` se une ADEMÁS a la red externa `pangolin` con un alias de
red fijo (`agrovoz-backend`), no depende del container name random que genera
Dokploy. Sin `ports:`, solo `expose:`.

**Recursos** — verificar que `deploy.resources.limits.cpus` no exceda el total
de vCPU reales del VPS (Docker rechaza cualquier límite por encima del total
de núcleos del host, con un error de creación de contenedor, no de build).

**`openwa`** — la imagen (`ghcr.io/rmyndharis/openwa:latest`) necesita 3
capabilities que `cap_drop: ALL` le saca por defecto: `CHOWN` (ajusta owner de
`/app/data` al arrancar) y `SETUID`+`SETGID` (baja de root al usuario `openwa`
vía gosu/su-exec). Sin las 3, crash-loop con "Operation not permitted".

### 5. Landing (Dokploy Application, Astro estático)

Ni `buildType: static` (no corre ningún build, solo copia `dist/` ya
existente) ni `nixpacks` (nixpkgs con Node 18 EOL removido del canal en el
momento de este deploy) sirven para Astro+bun. Se necesita `landing/Dockerfile`
propio, `buildType: dockerfile`, `customGitBuildPath: /landing`.

`PUBLIC_API_URL` (URL del backend, ej. `https://agrovoz.sbravo.app`) tiene que
pasarse como **build arg**, no env var de runtime — Astro/Vite inlinea
`import.meta.env.PUBLIC_*` en el bundle al compilar, demasiado tarde en el
nginx final. `ARG PUBLIC_API_URL` + `ENV PUBLIC_API_URL=${PUBLIC_API_URL}`
antes de `bun run build` en el Dockerfile; el valor real va en Build Args de
Dokploy, no hardcodeado en el repo.

Application (no Compose) en Dokploy corre como **Docker Swarm service** — ver
sección de networking arriba para cómo conectarlo al ingress.

### 6. Pangolin — dominios

Un **site tipo "local"** por servidor que corra recursos en el mismo host que
Pangolin (`type: "newt"` es para redes remotas por túnel, no sirve acá — daba
502 con "Invalid site ID" al confundirlo). Un **resource** por dominio,
**target** apuntando al alias/nombre de red del contenedor (no IP, no
loopback), puerto interno del servicio. `sso: false` en cada resource si el
backend/app maneja su propia auth — si no, el login de Pangolin bloquea
health checks y webhooks (llamadas de máquina, no pueden pasar por un login
humano).

## Troubleshooting rápido

| Síntoma | Causa | Dónde mirar |
|---|---|---|
| 502 Bad Gateway | Target apunta a `127.0.0.1`/loopback en vez de un alias de red compartida | `docker network inspect pangolin` / `dokploy-network` |
| 400 "Invalid host header" | Falta `EXTRA_ALLOWED_HOSTS` con el dominio real | `app/core/security.py` |
| CORS bloqueado en el navegador (curl funciona) | Falta `CORS_ORIGINS` con el origen de la landing | `app/main.py` (CORSMiddleware) |
| 503 en `/api/v1/demo/preguntar` | `DEMO_ENDPOINT_ENABLED=false` (default) | `app/core/config.py` |
| Contenedor crash-loop "Operation not permitted" | Falta alguna capability que el entrypoint de la imagen necesita | `docker logs <container>` para ver en qué paso falla |
| Docker rechaza crear el contenedor, build OK | `cpus` del límite excede los vCPU reales del host | `docker service ls` / `docker info` |
| `could not read Username for 'https://github.com'` | Repo privado clonado por HTTPS sin credenciales | Usar SSH + deploy key |

## Referencias

- #9 — plan original (Hetzner + Dokploy standalone), reemplazado por lo de acá
- #7 — reglas del compose original (decisión #12), actualizadas por #306
- #306 — alinear el ingress del compose con Pangolin
- #307 — límite de CPU real del VPS
- PR con todos los fixes de este deploy: revisar el historial de
  `docker-compose.prod.yml` y `landing/Dockerfile` en `main`
