#!/bin/bash
# Provisioning inicial del VPS Hetzner para AgroVoz con Dokploy
# Uso: bash scripts/provision-vps.sh
# Requisitos: Ubuntu 24.04 LTS, acceso root o sudo

set -euo pipefail

echo "=== AgroVoz VPS Provisioning ==="
echo "Este script instala Docker, Dokploy y configura el firewall."
echo ""

# Verificar que somos root o tenemos sudo
if [ "$EUID" -ne 0 ]; then
  echo "Por favor ejecuta como root o con sudo:"
  echo "sudo bash scripts/provision-vps.sh"
  exit 1
fi

# Actualizar sistema
echo "→ Actualizando paquetes del sistema..."
apt update && apt upgrade -y

# Dependencias esenciales
echo "→ Instalando dependencias esenciales..."
apt install -y curl wget git ufw vim htop fail2ban

# Docker (Dokploy lo requiere)
if ! command -v docker &> /dev/null; then
  echo "→ Instalando Docker..."
  curl -fsSL https://get.docker.com | sh
  usermod -aG docker ubuntu
  systemctl enable docker
else
  echo "✓ Docker ya instalado"
fi

# Dokploy: instala Docker + Traefik + dashboard automaticamente
# Requisitos: Ubuntu 22.04+/24.04, >=2GB RAM, >=30GB disco, dominio con A record
if ! command -v dokploy &> /dev/null; then
  echo "→ Instalando Dokploy..."
  curl -sSL https://dokploy.com/install.sh | bash
else
  echo "✓ Dokploy ya instalado"
fi

# Firewall (Dokploy usa Traefik en 80/443, dashboard en 3000)
echo "→ Configurando firewall UFW..."
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp    # Traefik HTTP
ufw allow 443/tcp   # Traefik HTTPS
ufw allow 3000/tcp  # Dokploy dashboard
ufw --force enable
echo "✓ Firewall configurado"

# Fail2ban
echo "→ Configurando Fail2ban..."
systemctl enable fail2ban
systemctl start fail2ban
echo "✓ Fail2ban activado"

# Swap (4GB para margen con modelos de IA cargados)
if ! swapon --show | grep -q "/swapfile"; then
  echo "→ Creando swapfile 4GB..."
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "✓ Swapfile creado"
else
  echo "✓ Swapfile ya existe"
fi

# Directorio de la app
echo "→ Creando directorio /opt/agrovoz..."
mkdir -p /opt/agrovoz
chown ubuntu:ubuntu /opt/agrovoz

echo ""
echo "=== Provisioning completo ==="
echo ""
echo "Próximos pasos:"
echo "1. Configurar DNS:"
echo "   - agrovoz.cl A record → $(curl -s ifconfig.me)"
echo "   - api.agrovoz.cl A record → $(curl -s ifconfig.me)"
echo ""
echo "2. Acceder a Dokploy:"
echo "   - http://$(curl -s ifconfig.me):3000"
echo "   - Crear cuenta admin"
echo "   - Configurar dominio: agrovoz.cl"
echo ""
echo "3. Para escanear QR de Open-WA (SSH tunnel):"
echo "   ssh -L 2886:localhost:2886 ubuntu@$(curl -s ifconfig.me)"
echo ""
echo "4. Ver servicios:"
echo "   docker ps"
echo "   ufw status"
echo ""
