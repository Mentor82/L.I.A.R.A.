# Proxmox Automation & Deployment Script

**Ziel**: Automatisierte Container-Erstellung mit vollständiger Docker-Setup  

---

## 1. Standalone Deployment Script

Speichern unter: `/root/deploy-ai-validator.sh`

```bash
#!/bin/bash

##############################################################################
# AI-Validator Deployment Script für Proxmox
# 
# Nutzen: ./deploy-ai-validator.sh [create|setup|deploy|destroy|status]
# Beispiel: ./deploy-ai-validator.sh create
#
# Cluster: MW-CLUSTER (astraeus)
# VMID: 301
# OS: Ubuntu 22.04 LTS
##############################################################################

set -e

# Konfiguration
VMID=301
HOSTNAME="ai-validator"
STORAGE="local-lvm"
ROOT_FS_SIZE="30G"
CPU_CORES=4
MEMORY=4096
SWAP=2048
TEMPLATE="local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst"
NETWORK_BRIDGE="vmbr0"
IP_ADDRESS="192.168.178.150"
IP_GATEWAY="192.168.178.1"
NAMESERVER="1.1.1.1"
SEARCH_DOMAIN="lab-web.local"

# Farben
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Funktionen
print_header() {
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║ $1${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
}

print_status() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_info() {
    echo -e "${YELLOW}ℹ${NC} $1"
}

# Prüfe ob Container existiert
container_exists() {
    pct status $VMID &>/dev/null && echo "true" || echo "false"
}

# Container erstellen
create_container() {
    print_header "CREATING LXC CONTAINER"
    
    if [ "$(container_exists)" == "true" ]; then
        print_error "Container $VMID existiert bereits!"
        exit 1
    fi
    
    print_info "Erstelle Container $VMID ($HOSTNAME)..."
    
    pct create $VMID $TEMPLATE \
        --hostname $HOSTNAME \
        --cores $CPU_CORES \
        --memory $MEMORY \
        --swap $SWAP \
        --storage $STORAGE \
        --rootfs $STORAGE:$ROOT_FS_SIZE \
        --net0 name=eth0,bridge=$NETWORK_BRIDGE,ip=${IP_ADDRESS}/24,gw=$IP_GATEWAY \
        --nameserver $NAMESERVER \
        --searchdomain $SEARCH_DOMAIN \
        --unprivileged 1 \
        --features nesting=1,keyctl=1
    
    print_status "Container erstellt: VMID=$VMID"
    
    print_info "Starte Container..."
    pct start $VMID
    sleep 5
    
    print_status "Container läuft"
}

# Docker im Container installieren
install_docker() {
    print_header "INSTALLING DOCKER"
    
    print_info "System aktualisieren..."
    pct exec $VMID -- apt update
    pct exec $VMID -- apt upgrade -y
    
    print_info "Abhängigkeiten installieren..."
    pct exec $VMID -- apt install -y \
        curl wget git vim \
        ca-certificates gnupg lsb-release \
        uidmap dbus systemd
    
    print_info "Docker Repository hinzufügen..."
    pct exec $VMID -- bash -c '
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
        
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    '
    
    print_info "Docker installieren..."
    pct exec $VMID -- apt update
    pct exec $VMID -- apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    
    print_info "Docker Compose installieren..."
    pct exec $VMID -- bash -c '
        curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        chmod +x /usr/local/bin/docker-compose
    '
    
    print_info "Docker aktivieren..."
    pct exec $VMID -- systemctl enable docker
    pct exec $VMID -- systemctl start docker
    
    # Test
    print_info "Docker Test..."
    pct exec $VMID -- docker run hello-world > /dev/null
    
    print_status "Docker installiert & aktiviert"
}

# AI-Validator Deploy
deploy_ai_validator() {
    print_header "DEPLOYING AI-VALIDATOR"
    
    print_info "Erstelle Verzeichnisse..."
    pct exec $VMID -- mkdir -p /opt/ai-validator
    pct exec $VMID -- mkdir -p /var/ai-validator/reports
    pct exec $VMID -- mkdir -p /var/ai-validator/metrics
    pct exec $VMID -- chmod 777 /var/ai-validator/reports /var/ai-validator/metrics
    
    print_info "Klone Repository..."
    pct exec $VMID -- bash -c '
        cd /opt/ai-validator
        git clone --depth 1 https://github.com/mirkowaldhauer/lab-web.git repo || echo "Repo bereits vorhanden"
        cd repo/ai-validator
    '
    
    print_info "Baue Docker Image..."
    pct exec $VMID -- bash -c '
        cd /opt/ai-validator/repo/ai-validator
        docker build -f Dockerfile.ai-validator -t lab-web/ai-validator:latest . 2>&1 | tail -20
    ' || print_error "Build fehlgeschlagen - möglicherweise RAM-Limit"
    
    print_status "AI-Validator deployed"
}

# Docker-Compose starten
start_services() {
    print_header "STARTING SERVICES"
    
    print_info "Erstelle docker-compose.yml..."
    pct exec $VMID -- bash -c 'cat > /opt/ai-validator/docker-compose-homelab.yml << '\''EOF'\''
version: '\''3.8'\''

services:
  ai-validator:
    image: lab-web/ai-validator:latest
    container_name: ai-validator-main
    
    deploy:
      resources:
        limits:
          cpus: '\''4'\''
          memory: 3G
        reservations:
          cpus: '\''2'\''
          memory: 2G
    
    volumes:
      - /var/ai-validator/reports:/app/reports
      - /var/ai-validator/metrics:/app/metrics
    
    environment:
      - WORKSPACE_PATH=/mnt/workspaces
      - REPORT_DIR=/app/reports
      - LOG_LEVEL=info
    
    ports:
      - "5000:5000"
    
    networks:
      - validator-net
    
    restart: unless-stopped

networks:
  validator-net:
    driver: bridge

volumes:
  ai-validator-reports:
    driver: local
  ai-validator-metrics:
    driver: local
EOF
    '
    
    print_info "Starte Docker Compose..."
    pct exec $VMID -- bash -c '
        cd /opt/ai-validator
        docker-compose -f docker-compose-homelab.yml up -d
        sleep 5
        docker-compose -f docker-compose-homelab.yml ps
    '
    
    print_status "Services laufen"
}

# Status anzeigen
show_status() {
    print_header "CONTAINER STATUS"
    
    if [ "$(container_exists)" == "false" ]; then
        print_error "Container $VMID existiert nicht!"
        exit 1
    fi
    
    echo -e "${BLUE}Container Information:${NC}"
    pct status $VMID
    
    echo ""
    echo -e "${BLUE}Container Konfiguration:${NC}"
    pct config $VMID | grep -E "^(cores|memory|swap|hostname|rootfs)"
    
    echo ""
    echo -e "${BLUE}Netzwerk:${NC}"
    pct exec $VMID -- hostname -I
    
    echo ""
    echo -e "${BLUE}Docker Status:${NC}"
    pct exec $VMID -- docker ps || print_error "Docker nicht verfügbar"
    
    echo ""
    echo -e "${BLUE}Disk Usage:${NC}"
    pct exec $VMID -- df -h | grep -E "^(Filesystem|/dev)"
}

# Container destroyen
destroy_container() {
    print_header "DESTROYING CONTAINER"
    
    if [ "$(container_exists)" == "false" ]; then
        print_error "Container $VMID existiert nicht!"
        exit 1
    fi
    
    print_info "Stopping container..."
    pct stop $VMID || true
    
    sleep 3
    
    print_info "Destroying container..."
    pct destroy $VMID
    
    print_status "Container zerstört"
}

# Vollständige Installation
full_setup() {
    print_header "FULL SETUP - AI-VALIDATOR ON PROXMOX"
    
    if [ "$(container_exists)" == "true" ]; then
        print_error "Container existiert bereits!"
        echo "Use: $0 destroy"
        exit 1
    fi
    
    create_container
    sleep 10
    install_docker
    deploy_ai_validator
    start_services
    show_status
    
    print_header "SETUP COMPLETE! ✓"
    echo ""
    echo "Dashboard: http://$IP_ADDRESS:5000"
    echo "SSH: ssh root@$IP_ADDRESS"
    echo ""
}

# Hilfemeldung
show_help() {
    cat << EOF
AI-Validator Proxmox Deployment Script

NUTZEN:
  ./deploy-ai-validator.sh [COMMAND]

COMMANDS:
  create      - Erstelle LXC Container
  docker      - Installiere Docker
  deploy      - Deploye AI-Validator
  start       - Starte Docker-Compose Services
  setup       - Vollständige Installation (create + docker + deploy + start)
  status      - Zeige Container Status
  destroy     - Zerstöre Container
  help        - Diese Hilfemeldung

KONFIGURATION:
  VMID:        $VMID
  Hostname:    $HOSTNAME
  IP:          $IP_ADDRESS
  CPU:         $CPU_CORES Cores
  RAM:         $MEMORY MB
  Disk:        $ROOT_FS_SIZE
  Storage:     $STORAGE

BEISPIELE:
  # Automatische Erstellung & Setup
  ./deploy-ai-validator.sh setup
  
  # Schritt für Schritt
  ./deploy-ai-validator.sh create
  ./deploy-ai-validator.sh docker
  ./deploy-ai-validator.sh deploy
  ./deploy-ai-validator.sh start
  
  # Status prüfen
  ./deploy-ai-validator.sh status
  
  # Entfernen
  ./deploy-ai-validator.sh destroy

EOF
}

# Main
case "${1:-help}" in
    create)
        create_container
        ;;
    docker)
        install_docker
        ;;
    deploy)
        deploy_ai_validator
        ;;
    start)
        start_services
        ;;
    setup)
        full_setup
        ;;
    status)
        show_status
        ;;
    destroy)
        destroy_container
        ;;
    help)
        show_help
        ;;
    *)
        print_error "Unbekannter Befehl: $1"
        show_help
        exit 1
        ;;
esac
```

---

## 2. Script verwenden

```bash
# Auf Proxmox hochladen
scp deploy-ai-validator.sh root@astraeus:/root/

# SSH zum Proxmox
ssh root@astraeus

# Ausführbar machen
chmod +x /root/deploy-ai-validator.sh

# Status prüfen
./deploy-ai-validator.sh status

# Vollständige Installation (EMPFOHLEN)
./deploy-ai-validator.sh setup

# Oder Schritt für Schritt
./deploy-ai-validator.sh create
./deploy-ai-validator.sh docker
./deploy-ai-validator.sh deploy
./deploy-ai-validator.sh start
```

---

## 3. Post-Deployment Konfiguration

Nach erfolgreichem Deploy:

```bash
# SSH in Container
ssh root@192.168.178.150

# Oder via Proxmox
pct exec 301 -- bash

# Setup Cron Jobs
cat > /etc/cron.d/ai-validator << 'EOF'
# Täglich um Mitternacht: Validierung
0 0 * * * root cd /opt/ai-validator && bash ./run-multi-workspace.sh validate parallel >> /var/log/ai-validator.log 2>&1

# Täglich um 01:00: Metrics
0 1 * * * root cd /opt/ai-validator && python3 metrics-collector.py /var/ai-validator/reports /var/ai-validator/metrics >> /var/log/ai-validator.log 2>&1
EOF

chmod 644 /etc/cron.d/ai-validator
```

---

## 4. Monitoring & Wartung

```bash
# Logs
docker logs ai-validator-main -f --tail 50

# Disk-Cleanup
find /var/ai-validator/reports -name "*.json" -mtime +30 -delete

# Container neustarten
docker-compose -f /opt/ai-validator/docker-compose-homelab.yml restart

# System-Status
htop  # oder: top
df -h
```

---

## 5. Backup & Restore

```bash
# Backup erstellen (auf astraeus als root)
pct backup 301 /var/lib/vz/dump

# Restore
pct restore /var/lib/vz/dump/<backup-id>
```

---

## Zusammenfassung

**Automatisierter Setup mit einer Zeile:**

```bash
ssh root@astraeus 'bash -c "
  cd /tmp
  git clone --depth 1 https://github.com/mirkowaldhauer/lab-web.git
  cd lab-web/ai-validator
  bash deploy-ai-validator.sh setup
"'
```

**Status nach Setup:**
- Container ID: 301
- Hostname: ai-validator
- IP: 192.168.178.150
- Dashboard: http://192.168.178.150:5000
- SSH: `ssh root@192.168.178.150`
