#!/bin/bash

##############################################################################
# AI-Validator Deployment Script für Proxmox (Executable Version)
# 
# Nutzen: ./proxmox-deploy-ai-validator.sh [create|setup|deploy|destroy|status]
# 
# Cluster: MW-CLUSTER (astraeus)
# VMID: 301
# OS: Ubuntu 22.04 LTS
# 
# Beispiel: ./proxmox-deploy-ai-validator.sh setup
##############################################################################

set -e

# ============================================================================
# KONFIGURATION
# ============================================================================

VMID=301
HOSTNAME="ai-validator"
STORAGE="local-lvm"
ROOT_FS_SIZE="30"
CPU_CORES=4
MEMORY=4096
SWAP=2048
TEMPLATE="local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst"
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
NC='\033[0m'

# ============================================================================
# HELPER FUNKTIONEN
# ============================================================================

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

container_exists() {
    pct status $VMID &>/dev/null && echo "true" || echo "false"
}

# ============================================================================
# HAUPTFUNKTIONEN
# ============================================================================

create_container() {
    print_header "CREATING LXC CONTAINER (VMID $VMID)"
    
    if [ "$(container_exists)" == "true" ]; then
        print_error "Container $VMID existiert bereits!"
        exit 1
    fi
    
    print_info "Erstelle Container mit folgenden Parametern:"
    echo "  - Hostname: $HOSTNAME"
    echo "  - CPU: $CPU_CORES Cores"
    echo "  - RAM: $MEMORY MB"
    echo "  - Disk: ${ROOT_FS_SIZE}G"
    echo "  - IP: $IP_ADDRESS"
    echo "  - Storage: $STORAGE"
    echo ""
    
    pct create $VMID $TEMPLATE \
        --hostname $HOSTNAME \
        --cores $CPU_CORES \
        --memory $MEMORY \
        --swap $SWAP \
        --storage $STORAGE \
        --rootfs ${STORAGE}:${ROOT_FS_SIZE} \
        --net0 name=eth0,bridge=$NETWORK_BRIDGE,ip=${IP_ADDRESS}/24,gw=$IP_GATEWAY \
        --nameserver $NAMESERVER \
        --searchdomain $SEARCH_DOMAIN \
        --unprivileged 1 \
        --features nesting=1,keyctl=1
    
    print_status "Container erstellt"
    
    print_info "Starte Container..."
    pct start $VMID
    sleep 5
    
    print_status "Container läuft"
}

install_docker() {
    print_header "INSTALLING DOCKER"
    
    print_info "System aktualisieren..."
    pct exec $VMID -- apt-get update > /dev/null
    pct exec $VMID -- apt-get upgrade -y > /dev/null
    
    print_info "Abhängigkeiten installieren..."
    pct exec $VMID -- apt-get install -y curl wget git vim ca-certificates gnupg lsb-release uidmap dbus systemd > /dev/null
    
    print_info "Docker Repository konfigurieren..."
    pct exec $VMID -- bash << 'INSTALL_DOCKER'
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg 2>/dev/null

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
INSTALL_DOCKER
    
    print_info "Docker & Docker Compose installieren..."
    pct exec $VMID -- apt-get update > /dev/null
    pct exec $VMID -- apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin > /dev/null
    
    print_info "Docker aktivieren..."
    pct exec $VMID -- systemctl enable docker
    pct exec $VMID -- systemctl start docker
    
    print_info "Verifiziere Docker Installation..."
    pct exec $VMID -- docker run --rm hello-world > /dev/null
    
    print_status "Docker installiert & aktiviert"
}

setup_ai_validator() {
    print_header "SETTING UP AI-VALIDATOR"
    
    print_info "Erstelle Verzeichnisse..."
    pct exec $VMID -- mkdir -p /opt/ai-validator
    pct exec $VMID -- mkdir -p /var/ai-validator/reports
    pct exec $VMID -- mkdir -p /var/ai-validator/metrics
    pct exec $VMID -- chmod 777 /var/ai-validator/reports
    pct exec $VMID -- chmod 777 /var/ai-validator/metrics
    
    print_info "Klone Repository..."
    pct exec $VMID -- bash << 'CLONE_REPO'
cd /opt/ai-validator
if [ ! -d "repo" ]; then
    git clone --depth 1 https://github.com/mirkowaldhauer/lab-web.git repo 2>&1 | grep -E "(Cloning|done)" || echo "Repository geklont"
else
    echo "Repository bereits vorhanden"
fi
CLONE_REPO
    
    print_info "Baue Docker Image (dies kann 2-5 Minuten dauern)..."
    pct exec $VMID -- bash << 'BUILD_IMAGE'
cd /opt/ai-validator/repo/ai-validator
docker build -f Dockerfile.ai-validator -t lab-web/ai-validator:latest . 2>&1 | tail -5
BUILD_IMAGE
    
    print_status "AI-Validator Setup abgeschlossen"
}

start_services() {
    print_header "STARTING DOCKER-COMPOSE SERVICES"
    
    print_info "Erstelle docker-compose-homelab.yml..."
    pct exec $VMID -- bash << 'CREATE_COMPOSE'
mkdir -p /opt/ai-validator

cat > /opt/ai-validator/docker-compose-homelab.yml << 'COMPOSE_EOF'
version: '3.8'

services:
  ai-validator:
    image: lab-web/ai-validator:latest
    container_name: ai-validator-main
    
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 3G
        reservations:
          cpus: '2'
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
COMPOSE_EOF
CREATE_COMPOSE
    
    print_info "Starte Services..."
    pct exec $VMID -- bash << 'START_SERVICES'
cd /opt/ai-validator
docker-compose -f docker-compose-homelab.yml up -d
sleep 3
docker-compose -f docker-compose-homelab.yml ps
START_SERVICES
    
    print_status "Services gestartet"
}

show_status() {
    print_header "CONTAINER STATUS"
    
    if [ "$(container_exists)" == "false" ]; then
        print_error "Container $VMID existiert nicht!"
        exit 1
    fi
    
    echo -e "${BLUE}Container Information:${NC}"
    pct status $VMID
    
    echo ""
    echo -e "${BLUE}Ressourcen-Konfiguration:${NC}"
    pct config $VMID | grep -E "^(cores|memory|swap|hostname|rootfs)" || true
    
    echo ""
    echo -e "${BLUE}Netzwerk:${NC}"
    pct exec $VMID -- hostname -I
    
    echo ""
    echo -e "${BLUE}Docker Status:${NC}"
    pct exec $VMID -- docker ps || print_error "Docker nicht verfügbar"
    
    echo ""
    echo -e "${BLUE}Disk Usage:${NC}"
    pct exec $VMID -- df -h | grep -E "^(/|rootfs)" || true
}

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

full_setup() {
    print_header "COMPLETE SETUP - AI-VALIDATOR ON PROXMOX"
    
    if [ "$(container_exists)" == "true" ]; then
        print_error "Container $VMID existiert bereits!"
        echo ""
        echo "Optionen:"
        echo "  - Nutze: ./proxmox-deploy-ai-validator.sh status (für Status)"
        echo "  - Nutze: ./proxmox-deploy-ai-validator.sh destroy (zum Löschen)"
        exit 1
    fi
    
    create_container
    sleep 10
    install_docker
    setup_ai_validator
    start_services
    
    echo ""
    show_status
    
    print_header "SETUP COMPLETE! ✓"
    echo ""
    echo -e "${GREEN}Dashboard:${NC} http://$IP_ADDRESS:5000"
    echo -e "${GREEN}SSH:${NC}       ssh root@$IP_ADDRESS"
    echo -e "${GREEN}Logs:${NC}      docker logs -f ai-validator-main"
    echo ""
}

show_help() {
    cat << EOF
╔════════════════════════════════════════════════════════════════╗
║   AI-Validator Proxmox Deployment Script                      ║
╚════════════════════════════════════════════════════════════════╝

NUTZEN:
  ./proxmox-deploy-ai-validator.sh [COMMAND]

COMMANDS:
  create      - Erstelle LXC Container
  docker      - Installiere Docker im Container
  setup       - Setup AI-Validator & Services
  start       - Starte Docker-Compose Services
  full-setup  - Vollständige Installation (EMPFOHLEN)
  status      - Zeige Container Status
  destroy     - Zerstöre Container (WARNUNG!)
  help        - Diese Hilfemeldung

KONFIGURATION:
  VMID:        $VMID
  Hostname:    $HOSTNAME
  IP:          $IP_ADDRESS
  CPU:         $CPU_CORES Cores
  RAM:         $MEMORY MB
  Disk:        ${ROOT_FS_SIZE}G
  Storage:     $STORAGE

QUICKSTART:
  # Automatische Installation (empfohlen)
  ./proxmox-deploy-ai-validator.sh full-setup
  
  # Oder Schritt-für-Schritt
  ./proxmox-deploy-ai-validator.sh create
  ./proxmox-deploy-ai-validator.sh docker
  ./proxmox-deploy-ai-validator.sh setup
  ./proxmox-deploy-ai-validator.sh start
  
  # Status prüfen
  ./proxmox-deploy-ai-validator.sh status

NACH DEM SETUP:
  Dashboard: http://$IP_ADDRESS:5000
  SSH:       ssh root@$IP_ADDRESS
  
  Setup Cron Jobs:
    pct exec $VMID -- cat > /etc/cron.d/ai-validator << 'CRON'
0 0 * * * root cd /opt/ai-validator && bash ./run-multi-workspace.sh validate parallel >> /var/log/ai-validator.log 2>&1
CRON

EOF
}

# ============================================================================
# MAIN
# ============================================================================

case "${1:-help}" in
    create)
        create_container
        ;;
    docker)
        install_docker
        ;;
    setup)
        setup_ai_validator
        ;;
    start)
        start_services
        ;;
    full-setup)
        full_setup
        ;;
    status)
        show_status
        ;;
    destroy)
        destroy_container
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        print_error "Unbekannter Befehl: $1"
        echo ""
        show_help
        exit 1
        ;;
esac
