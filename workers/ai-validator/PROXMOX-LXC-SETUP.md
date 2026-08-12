# Proxmox LXC Container Setup für AI-Validator

**Cluster**: MW-CLUSTER (astraeus)  
**Container VMID**: 301  
**Betriebssystem**: Ubuntu 22.04 LTS  
**Ressourcen**: 4 CPU, 4GB RAM, 30GB Disk  
**Storage**: local-lvm  

---

## 1. Template Vorbereitung

Stelle sicher, dass Ubuntu 22.04 LTS Template verfügbar ist:

```bash
# Via Web UI: Datacenter > astraeus > Container Templates
# Oder via CLI:
pveam update
pveam list local
# Suche nach: ubuntu-22.04-standard_*.tar.zst
```

Falls nicht vorhanden, herunterladen:

```bash
pveam download local ubuntu-22.04-standard_22.04-1_amd64.tar.zst
```

---

## 2. Container erstellen (Via CLI)

### Option A: Einfache Methode (CLI)

```bash
# SSH auf Proxmox (als root)
ssh root@astraeus

# Container erstellen
pct create 301 local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst \
  --hostname ai-validator \
  --cores 4 \
  --memory 4096 \
  --swap 2048 \
  --storage local-lvm \
  --rootfs local-lvm:30 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --nameserver 1.1.1.1 \
  --searchdomain lab-web.local \
  --unprivileged 1 \
  --features nesting=1,keyctl=1

# Container starten
pct start 301

# Container-IP prüfen
pct exec 301 -- hostname -I
```

### Option B: Mit statischer IP

```bash
pct create 301 local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst \
  --hostname ai-validator \
  --cores 4 \
  --memory 4096 \
  --swap 2048 \
  --storage local-lvm \
  --rootfs local-lvm:30 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.178.150/24,gw=192.168.178.1 \
  --nameserver 1.1.1.1 \
  --searchdomain lab-web.local \
  --unprivileged 1 \
  --features nesting=1,keyctl=1

pct start 301
```

---

## 3. Container-Zugang

```bash
# SSH in den Container
ssh root@192.168.178.150

# Oder via pct exec
pct exec 301 -- bash
```

---

## 4. Docker Installation im Container

**SSH in den Container** und führe folgende Befehle aus:

### Schritt 1: System aktualisieren

```bash
apt update && apt upgrade -y
apt install -y \
  curl wget git vim \
  ca-certificates gnupg lsb-release \
  uidmap dbus systemd
```

### Schritt 2: Docker Repository hinzufügen

```bash
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
```

### Schritt 3: Docker installieren

```bash
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Alternatif: docker-compose als standalone
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose
```

### Schritt 4: Docker aktivieren

```bash
systemctl enable docker
systemctl start docker

# Test
docker run hello-world
```

---

## 5. AI-Validator Clone & Deploy

```bash
# Arbeitsverzeichnis
mkdir -p /opt/ai-validator
cd /opt/ai-validator

# Repository klonen (oder Docker Image verwenden)
git clone https://github.com/mirkowaldhauer/lab-web.git .
# oder
git clone --depth 1 https://github.com/mirkowaldhauer/lab-web.git ai-validator-repo

# Docker Image bauen (falls nicht vorhanden)
cd ai-validator-repo/ai-validator
docker build -f Dockerfile.ai-validator -t lab-web/ai-validator:latest .

# Oder Image vom Docker Hub pullen (später)
# docker pull lab-web/ai-validator:latest
```

---

## 6. Docker-Compose Setup für Persistente Storage

Erstelle `/opt/ai-validator/docker-compose-homelab.yml`:

```yaml
version: '3.8'

services:
  ai-validator:
    image: lab-web/ai-validator:latest
    container_name: ai-validator-main
    
    # Ressourcen
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 3G
        reservations:
          cpus: '2'
          memory: 2G
    
    # Volumes (persistent storage)
    volumes:
      # Reports (lokal persistent)
      - /var/ai-validator/reports:/app/reports
      - /var/ai-validator/metrics:/app/metrics
      
      # Workspaces (optional, für remote validation)
      # - /mnt/workspaces:/mnt/workspaces:ro
    
    # Umgebungsvariablen
    environment:
      - WORKSPACE_PATH=/mnt/workspaces
      - REPORT_DIR=/app/reports
      - LOG_LEVEL=info
      - ENABLE_API=true
      - API_PORT=5000
    
    # Ports für Dashboard
    ports:
      - "5000:5000"  # Metrics Server
      - "3333:3333"  # Optional: MCP if needed
    
    # Netzwerk
    networks:
      - validator-net
    
    # Auto-Restart
    restart: unless-stopped
    
    # Health Check
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health || true"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 30s

  # Optional: Metrics Server (separate container)
  metrics-server:
    image: python:3.12-slim
    container_name: ai-validator-metrics
    
    working_dir: /app
    volumes:
      - /var/ai-validator/metrics:/app/metrics
      - /opt/ai-validator/metrics-server.py:/app/metrics-server.py:ro
    
    command: python -m pip install flask && python /app/metrics-server.py /app/metrics
    
    ports:
      - "5001:5000"
    
    networks:
      - validator-net
    
    restart: unless-stopped
    
    environment:
      - FLASK_APP=metrics-server.py
      - FLASK_ENV=production

networks:
  validator-net:
    driver: bridge

volumes:
  ai-validator-reports:
    driver: local
  ai-validator-metrics:
    driver: local
```

---

## 7. Persistent Storage vorbereiten

```bash
# Im Container (LXC):
mkdir -p /var/ai-validator/reports
mkdir -p /var/ai-validator/metrics
chmod 777 /var/ai-validator/reports
chmod 777 /var/ai-validator/metrics

# Optional: NFS/SMB mount für Workspaces
mkdir -p /mnt/workspaces
# mount.cifs //fritzchen/... /mnt/workspaces -o ...
```

---

## 8. Docker-Compose starten

```bash
cd /opt/ai-validator

# Starten
docker-compose -f docker-compose-homelab.yml up -d

# Logs prüfen
docker-compose -f docker-compose-homelab.yml logs -f

# Status prüfen
docker-compose -f docker-compose-homelab.yml ps
```

---

## 9. Dashboard & API testen

```bash
# Lokal im Container
curl http://localhost:5000/

# Vom Host (astraeus)
curl http://192.168.178.150:5000/

# Vom Mac (nur im gleichen Netz)
curl http://192.168.178.150:5000/
# oder wenn konfiguriert: http://ai-validator.lab-web.local:5000/
```

---

## 10. Cron Jobs für regelmäßige Validierung

Erstelle `/etc/cron.d/ai-validator`:

```cron
# Täglich um Mitternacht: Multi-Workspace Validierung
0 0 * * * root cd /opt/ai-validator && ./run-multi-workspace.sh validate parallel >> /var/log/ai-validator.log 2>&1

# Täglich um 01:00: Metrics sammeln
0 1 * * * root cd /opt/ai-validator && python3 metrics-collector.py /var/ai-validator/reports /var/ai-validator/metrics >> /var/log/ai-validator.log 2>&1

# Täglich um 02:00: Reports archivieren (älter als 30 Tage)
0 2 * * * root find /var/ai-validator/reports -name "*.json" -mtime +30 -delete
```

---

## 11. Netzwerk & Remote Zugang

### Reverse Proxy (Nginx - Optional)

Falls du über edge01 oder nginx zugreifen möchtest:

```nginx
server {
    listen 80;
    server_name ai-validator.lab-web.local;

    location / {
        proxy_pass http://192.168.178.150:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Firewall (Falls benötigt)

```bash
# Im Container:
ufw allow 5000/tcp    # Metrics Server
ufw allow 3333/tcp    # Optional
ufw enable
```

---

## 12. Troubleshooting

### Container hochfahren nicht vollständig

```bash
# Log prüfen
pct exec 301 -- journalctl -n 50

# Systemd services prüfen
pct exec 301 -- systemctl status docker
```

### Docker nicht verfügbar

```bash
# Nesting ermöglichen
pct set 301 -features nesting=1

# Container neustarten
pct stop 301
pct start 301
```

### Keine Netzwerk-Konnektivität

```bash
# IP prüfen
pct exec 301 -- ip addr show
pct exec 301 -- ip route show

# Vom Host testen
ping 192.168.178.150
```

### Disk Platz prüfen

```bash
# Im Container
df -h

# Vom Host
pvesh get /nodes/astraeus/lxc/301/status
```

---

## 13. Container verwalten

```bash
# Status prüfen
pct status 301

# Starten/Stoppen
pct start 301
pct stop 301
pct reboot 301

# Shell Zugang
pct exec 301 -- bash

# SSH
ssh root@192.168.178.150

# Konfiguration ändern
pct config 301 | grep cores
pct set 301 -cores 8  # CPU erhöhen
pct set 301 -memory 8192  # RAM erhöhen

# Backup erstellen
pct backup 301 /var/lib/vz/dump
```

---

## 14. Monitoring & Logs

```bash
# Docker logs
docker logs ai-validator-main -f --tail 100

# System logs
journalctl -u docker -f
journalctl -u cron -f

# Disk usage
du -sh /var/ai-validator/*
du -sh /var/lib/docker/*
```

---

## Zusätzliche Ressourcen

- [Proxmox LXC Dokumentation](https://pve.proxmox.com/wiki/Linux_Container)
- [Docker im LXC Container](https://pve.proxmox.com/wiki/Nested_Guests)
- [Ubuntu 22.04 LTS](https://releases.ubuntu.com/22.04/)

---

## Zusammenfassung

| Element | Wert |
|---------|------|
| VMID | 301 |
| Hostname | ai-validator |
| IP | 192.168.178.150 (oder dhcp) |
| CPU | 4 Cores |
| RAM | 4 GB |
| Disk | 30 GB |
| Storage | local-lvm |
| OS | Ubuntu 22.04 LTS |
| Runtime | Docker + Docker Compose |
| Dashboard | http://192.168.178.150:5000 |

**Status**: 🚀 Ready for deployment
