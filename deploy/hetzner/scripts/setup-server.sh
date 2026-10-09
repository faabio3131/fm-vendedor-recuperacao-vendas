#!/usr/bin/env bash
# Preparação ÚNICA de um servidor Ubuntu/Debian novo (rodar como root, uma vez).
# Faz: atualizações automáticas de segurança, firewall (22/80/443), usuário fmapp, swap de 2 GB,
# Docker, e os agendamentos (backup diário, teste de restauração semanal, ops-check a cada 5 min).
# NÃO coloca segredos: o .env é criado à mão em /opt/fm-seller/.env (modelo: .env.example).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Rode como root." >&2; exit 1; }

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y ufw unattended-upgrades age curl git ca-certificates rclone fail2ban
dpkg-reconfigure -f noninteractive unattended-upgrades

# Rotação de logs do Docker (vale para qualquer contêiner da máquina, antes de instalar/reiniciar)
install -d /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "3" }
}
JSON

# Docker (repositório oficial)
if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh; fi
systemctl restart docker || true

# Firewall: só SSH, HTTP e HTTPS. O banco nunca é publicado.
ufw default deny incoming; ufw default allow outgoing
ufw allow 22/tcp; ufw allow 80/tcp; ufw allow 443/tcp
ufw --force enable

# SSH só por chave, sem senha e sem root
install -d /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/50-fm.conf <<'CONF'
PasswordAuthentication no
PermitRootLogin prohibit-password
CONF
systemctl reload ssh || systemctl reload sshd || true

# Proteção contra tentativa de senha no SSH: bloqueia o IP por 1 hora após 5 falhas
install -d /etc/fail2ban/jail.d
cat > /etc/fail2ban/jail.d/50-fm.local <<'CONF'
[sshd]
enabled = true
maxretry = 5
findtime = 10m
bantime = 1h
CONF
systemctl enable --now fail2ban
systemctl restart fail2ban || true

# Swap de 2 GB (folga contra falta de memória)
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

id fmapp >/dev/null 2>&1 || useradd -m -s /bin/bash -G docker fmapp
install -d -o fmapp -g fmapp /opt/fm-seller /var/backups/fm-seller

# Agendamentos (cron do usuário fmapp)
cat > /etc/cron.d/fm-seller <<'CRON'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Backup todo dia às 03:10 (UTC)
10 3 * * * fmapp /opt/fm-seller/deploy/hetzner/scripts/backup.sh >> /var/log/fm-seller-backup.log 2>&1
# Teste de restauração aos domingos às 04:30 (precisa de RESTORE_KEY; ver docs/HETZNER_PRODUCAO.md)
30 4 * * 0 fmapp RESTORE_KEY=/opt/fm-seller/restore-key.txt /opt/fm-seller/deploy/hetzner/scripts/restore-test.sh >> /var/log/fm-seller-backup.log 2>&1
# Alertas a cada 5 minutos
*/5 * * * * fmapp /opt/fm-seller/deploy/hetzner/scripts/ops-check.sh >> /var/log/fm-seller-ops.log 2>&1
CRON
chmod 644 /etc/cron.d/fm-seller
touch /var/log/fm-seller-backup.log /var/log/fm-seller-ops.log
chown fmapp /var/log/fm-seller-backup.log /var/log/fm-seller-ops.log
echo "Pronto. Próximos passos: docs/HETZNER_PRODUCAO.md (clonar o repositório em /opt/fm-seller, criar o .env, subir)."
