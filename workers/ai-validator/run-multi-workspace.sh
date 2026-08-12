#!/bin/bash

################################################################################
# AI Validator - Multi-Workspace Validation Script
# Purpose: Validate multiple workspaces in sequence or parallel
# Usage: ./run-multi-workspace.sh [quick|validate|python|...] [serial|parallel]
################################################################################

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMAND="${1:-quick}"
MODE="${2:-serial}"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# ============================================
# Configuration
# ============================================

# Workspaces to validate (add more as needed)
WORKSPACES=(
    "project-a:/path/to/project-a"
    # "project-b:/path/to/project-b"
    # "project-c:/path/to/project-c"
)

# Report aggregation
REPORT_DIR="/tmp/ai-validator-reports-$(date +%s)"
mkdir -p "$REPORT_DIR"

################################################################################
# Functions
################################################################################

log_info() {
    echo -e "${BLUE}[INFO]${NC} $@"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $@"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $@"
}

log_workspace() {
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Workspace: $1${NC}"
    echo -e "${YELLOW}Command: $2${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

validate_workspace_serial() {
    local workspace_name="$1"
    local workspace_path="$2"
    local command="$3"
    
    log_workspace "$workspace_name" "$command"
    
    # Run validation with environment variables
    cd "$SCRIPT_DIR"
    
    if WORKSPACE_NAME="$workspace_name" \
       WORKSPACE_PATH="$workspace_path" \
       CONTAINER_NAME="ai-validator-${workspace_name}" \
       COMPOSE_PROJECT_NAME="ai-validator-${workspace_name}" \
       REPORT_DIR="${REPORT_DIR}/${workspace_name}" \
       docker-compose -f docker-compose.yml -f docker-compose.multi-workspace.yml \
       run --rm ai-validator "$command"; then
        log_success "$workspace_name validation completed"
        return 0
    else
        log_error "$workspace_name validation failed"
        return 1
    fi
}

validate_workspace_parallel() {
    local workspace_name="$1"
    local workspace_path="$2"
    local command="$3"
    
    log_workspace "$workspace_name" "$command (PARALLEL)"
    
    cd "$SCRIPT_DIR"
    
    # Run in background
    {
        if WORKSPACE_NAME="$workspace_name" \
           WORKSPACE_PATH="$workspace_path" \
           CONTAINER_NAME="ai-validator-${workspace_name}" \
           COMPOSE_PROJECT_NAME="ai-validator-${workspace_name}" \
           REPORT_DIR="${REPORT_DIR}/${workspace_name}" \
           docker-compose -f docker-compose.yml -f docker-compose.multi-workspace.yml \
           run --rm ai-validator "$command" > "$REPORT_DIR/${workspace_name}/stdout.log" 2>&1; then
            log_success "$workspace_name validation completed"
        else
            log_error "$workspace_name validation failed"
        fi
    } &
}

print_usage() {
    cat << 'EOF'
╔═══════════════════════════════════════════════════════════════╗
║         AI Validator - Multi-Workspace Script                ║
╚═══════════════════════════════════════════════════════════════╝

USAGE:
  ./run-multi-workspace.sh [COMMAND] [MODE]

COMMANDS:
  quick      Quick multi-language check (default)
  validate   Full validation suite
  python     Python-only validation
  javascript JavaScript/TypeScript validation
  bash       Bash/Shell validation
  html       HTML/CSS validation
  php        PHP validation
  cpp        C/C++ validation
  config     Config files validation
  security   Security scan (Bandit)

MODES:
  serial     Run validations one by one (default, safe)
  parallel   Run validations in parallel (faster, more resources)

EXAMPLES:
  # Quick check, serial mode (default)
  ./run-multi-workspace.sh

  # Full validation, parallel mode
  ./run-multi-workspace.sh validate parallel

  # Python only, serial
  ./run-multi-workspace.sh python serial

  # Security scan, parallel
  ./run-multi-workspace.sh security parallel

CONFIGURATION:
  1. Edit WORKSPACES array in this script
  2. Add: "workspace-name:/path/to/workspace"
  3. Or create .env.workspace-NAME files

OUTPUTS:
  Reports: $REPORT_DIR/{workspace-name}/
  Aggregated: $REPORT_DIR/SUMMARY.md

EOF
}

print_summary() {
    local total_workspaces=${#WORKSPACES[@]}
    
    echo ""
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║           MULTI-WORKSPACE VALIDATION SUMMARY                  ║${NC}"
    echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
    
    echo -e "Command:    ${YELLOW}$COMMAND${NC}"
    echo -e "Mode:       ${YELLOW}$MODE${NC}"
    echo -e "Workspaces: ${YELLOW}$total_workspaces${NC}"
    echo -e "Reports:    ${YELLOW}$REPORT_DIR${NC}"
    echo ""
    
    # List validated workspaces
    for workspace_config in "${WORKSPACES[@]}"; do
        IFS=':' read -r ws_name ws_path <<< "$workspace_config"
        if [ -d "$REPORT_DIR/$ws_name" ]; then
            echo -e "  ${GREEN}✓${NC} $ws_name ($ws_path)"
        else
            echo -e "  ${RED}✗${NC} $ws_name ($ws_path)"
        fi
    done
    
    echo ""
}

################################################################################
# Main
################################################################################

# Validate inputs
if [[ "$COMMAND" == "-h" || "$COMMAND" == "--help" ]]; then
    print_usage
    exit 0
fi

# Check if workspaces are configured
if [ ${#WORKSPACES[@]} -eq 0 ]; then
    log_error "No workspaces configured! Edit the WORKSPACES array in this script."
    print_usage
    exit 1
fi

log_info "Starting multi-workspace validation"
log_info "Command: $COMMAND"
log_info "Mode: $MODE"
log_info "Workspaces: ${#WORKSPACES[@]}"
log_info "Report directory: $REPORT_DIR"
echo ""

# Process each workspace
failed_workspaces=()
successful_workspaces=()

if [ "$MODE" = "parallel" ]; then
    # Parallel mode: start all in background
    log_info "Running in PARALLEL mode..."
    for workspace_config in "${WORKSPACES[@]}"; do
        IFS=':' read -r ws_name ws_path <<< "$workspace_config"
        validate_workspace_parallel "$ws_name" "$ws_path" "$COMMAND"
    done
    
    # Wait for all background jobs
    if wait; then
        log_success "All parallel validations completed"
    else
        log_error "Some parallel validations failed"
    fi
else
    # Serial mode: one after another
    log_info "Running in SERIAL mode..."
    for workspace_config in "${WORKSPACES[@]}"; do
        IFS=':' read -r ws_name ws_path <<< "$workspace_config"
        if validate_workspace_serial "$ws_name" "$ws_path" "$COMMAND"; then
            successful_workspaces+=("$ws_name")
        else
            failed_workspaces+=("$ws_name")
        fi
    done
fi

# Print summary
print_summary

# Exit with error if any workspace failed
if [ ${#failed_workspaces[@]} -gt 0 ]; then
    log_error "Failed workspaces: ${failed_workspaces[*]}"
    exit 1
else
    log_success "All workspaces validated successfully!"
    exit 0
fi
