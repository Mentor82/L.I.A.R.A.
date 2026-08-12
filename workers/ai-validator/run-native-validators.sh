#!/bin/bash

################################################################################
# AI Validator - Native Validators (no Docker)
# Purpose: Validate workspaces with native tools (pylint, bandit, shellcheck)
# Usage: ./run-native-validators.sh [COMMAND] [MODE]
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
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ============================================
# Configuration
# ============================================

# Workspaces to validate
WORKSPACES=(
    "project-a:/path/to/project-a"
    # "project-b:/path/to/project-b"
)

# Report directory
REPORT_DIR="/tmp/ai-validator-reports-$(date +%s)"
mkdir -p "$REPORT_DIR"

# AI Policy Path
AI_POLICY_PATH="${SCRIPT_DIR}/ai"

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

log_header() {
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}$1${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

################################################################################
# Validators
################################################################################

validate_python() {
    local workspace_path="$1"
    local report_file="$2"
    
    log_info "Validating Python files..."
    
    if find "$workspace_path" -name "*.py" -type f | grep -q .; then
        pylint --exit-zero \
            --output-format=text \
            $(find "$workspace_path" -name "*.py" -type f | head -20) \
            > "$report_file" 2>&1 || true
        log_success "Python validation completed: $report_file"
    else
        echo "No Python files found" > "$report_file"
        log_info "No Python files found"
    fi
}

validate_bash() {
    local workspace_path="$1"
    local report_file="$2"
    
    log_info "Validating Bash/Shell files..."
    
    if find "$workspace_path" -name "*.sh" -type f | grep -q .; then
        (
            echo "=== ShellCheck Report ==="
            find "$workspace_path" -name "*.sh" -type f | while read -r file; do
                echo ""
                echo "File: $file"
                shellcheck "$file" 2>&1 || true
            done
        ) > "$report_file" 2>&1 || true
        log_success "Bash validation completed: $report_file"
    else
        echo "No Bash files found" > "$report_file"
        log_info "No Bash files found"
    fi
}

validate_security() {
    local workspace_path="$1"
    local report_file="$2"
    
    log_info "Running security scan (Bandit)..."
    
    if find "$workspace_path" -name "*.py" -type f | grep -q .; then
        bandit -r "$workspace_path" -ll -f txt \
            > "$report_file" 2>&1 || true
        log_success "Security scan completed: $report_file"
    else
        echo "No Python files found for security scan" > "$report_file"
        log_info "No Python files for security scan"
    fi
}

validate_config() {
    local workspace_path="$1"
    local report_file="$2"
    
    log_info "Validating config files..."
    
    (
        echo "=== Config Files Found ==="
        find "$workspace_path" \
            -name "*.json" -o -name "*.yaml" -o -name "*.yml" \
            -o -name "*.toml" -o -name "*.env*" 2>/dev/null | while read -r file; do
            echo "  ✓ $file"
            
            # Validate JSON
            if [[ "$file" == *.json ]]; then
                if jq empty "$file" 2>/dev/null; then
                    echo "    → Valid JSON"
                else
                    echo "    → Invalid JSON syntax"
                fi
            fi
        done
    ) > "$report_file" 2>&1 || true
    
    log_success "Config validation completed: $report_file"
}

################################################################################
# Main Validation
################################################################################

validate_workspace() {
    local workspace_name="$1"
    local workspace_path="$2"
    local command="$3"
    
    log_header "Workspace: $workspace_name | Command: $command"
    
    # Check if workspace exists
    if [[ ! -d "$workspace_path" ]]; then
        log_error "Workspace path not found: $workspace_path"
        return 1
    fi
    
    local ws_report_dir="$REPORT_DIR/$workspace_name"
    mkdir -p "$ws_report_dir"
    
    case "$command" in
        quick)
            validate_python "$workspace_path" "$ws_report_dir/python.txt"
            validate_bash "$workspace_path" "$ws_report_dir/bash.txt"
            ;;
        validate)
            validate_python "$workspace_path" "$ws_report_dir/python.txt"
            validate_bash "$workspace_path" "$ws_report_dir/bash.txt"
            validate_security "$workspace_path" "$ws_report_dir/security.txt"
            validate_config "$workspace_path" "$ws_report_dir/config.txt"
            ;;
        python)
            validate_python "$workspace_path" "$ws_report_dir/python.txt"
            ;;
        bash)
            validate_bash "$workspace_path" "$ws_report_dir/bash.txt"
            ;;
        security)
            validate_security "$workspace_path" "$ws_report_dir/security.txt"
            ;;
        config)
            validate_config "$workspace_path" "$ws_report_dir/config.txt"
            ;;
        *)
            log_error "Unknown command: $command"
            return 1
            ;;
    esac
    
    log_success "$workspace_name completed"
    return 0
}

################################################################################
# Main Loop
################################################################################

main() {
    log_header "AI Validator - Native Mode (No Docker)"
    log_info "Command: $COMMAND"
    log_info "Mode: $MODE"
    log_info "Workspaces: ${#WORKSPACES[@]}"
    log_info "Report Directory: $REPORT_DIR"
    echo ""
    
    # Parse workspaces
    local failed_count=0
    local success_count=0
    
    if [[ "$MODE" == "serial" ]]; then
        for workspace in "${WORKSPACES[@]}"; do
            local name="${workspace%%:*}"
            local path="${workspace##*:}"
            
            if validate_workspace "$name" "$path" "$COMMAND"; then
                ((success_count++))
            else
                ((failed_count++))
            fi
        done
    else
        log_error "Parallel mode not yet implemented for native validators"
        exit 1
    fi
    
    # Summary
    echo ""
    log_header "VALIDATION SUMMARY"
    echo "Command:   $COMMAND"
    echo "Mode:      $MODE"
    echo "Success:   $success_count"
    echo "Failed:    $failed_count"
    echo "Reports:   $REPORT_DIR"
    echo ""
    
    if [[ $failed_count -gt 0 ]]; then
        log_error "Some validations failed!"
        exit 1
    else
        log_success "All validations passed!"
        exit 0
    fi
}

################################################################################
# Help
################################################################################

show_help() {
    cat << 'EOF'
╔═══════════════════════════════════════════════════════════════╗
║     AI Validator - Native Validators (No Docker)             ║
╚═══════════════════════════════════════════════════════════════╝

USAGE:
  ./run-native-validators.sh [COMMAND] [MODE]

COMMANDS:
  quick      Quick validation (Python + Bash)
  validate   Full validation (Python + Bash + Security + Config)
  python     Python-only validation (pylint)
  bash       Bash-only validation (shellcheck)
  security   Security scan (bandit)
  config     Config files validation (JSON, YAML, etc.)

MODES:
  serial     Run validations one by one (default)
  parallel   Run validations in parallel (coming soon)

EXAMPLES:
  # Quick check (default)
  ./run-native-validators.sh

  # Full validation
  ./run-native-validators.sh validate serial

  # Python only
  ./run-native-validators.sh python serial

  # Security scan
  ./run-native-validators.sh security serial

CONFIGURATION:
  1. Edit WORKSPACES array in this script
  2. Add: "workspace-name:/path/to/workspace"

TOOLS USED:
  - pylint (Python)
  - shellcheck (Bash/Shell)
  - bandit (Security)
  - jq (JSON validation)

OUTPUTS:
  Reports in: /tmp/ai-validator-reports-<TIMESTAMP>/
EOF
}

# Show help if requested
if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    show_help
    exit 0
fi

# Run main
main
