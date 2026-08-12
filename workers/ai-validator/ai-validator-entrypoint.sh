#!/bin/bash

# AI Validator Entrypoint Script - Multi-Language Edition
# Purpose: Orchestrate validation workflows for Python, JavaScript, Bash, HTML, PHP, C/C++, etc.
# Policy: ai/POLICY/SECURITY.md + ai/POLICY/DATA-GOVERNANCE.md

set -e

WORKSPACE="${WORKSPACE:-.}"
REPORT_DIR="${REPORT_DIR:-./.reports}"
STRICT_MODE="${STRICT_MODE:-false}"

# Create reports directory (ensure it's writable)
mkdir -p "${REPORT_DIR}" 2>/dev/null || {
    REPORT_DIR="/tmp/.reports"
    mkdir -p "${REPORT_DIR}"
}

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[!]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }
log_lang() { echo -e "${CYAN}[$(echo "$1" | tr '[:lower:]' '[:upper:]')]${NC}"; }

# ============================================================
# PYTHON VALIDATORS
# ============================================================

validate_python() {
    log_lang "Python"
    local py_files=$(find "${WORKSPACE}" -name "*.py" -not -path "*/.venv/*" -not -path "*/__pycache__/*" -not -path "*/.archived/*" 2>/dev/null | wc -l)
    [ $py_files -eq 0 ] && log_warn "No Python files found" && return 0
    
    log_info "  Syntax check..."
    find "${WORKSPACE}" -name "*.py" -not -path "*/.venv/*" -not -path "*/__pycache__/*" -not -path "*/.archived/*" 2>/dev/null | xargs -I {} python3 -m py_compile {} 2>&1 | tee -a "${REPORT_DIR}/python-syntax.log" >/dev/null || true
    
    log_info "  Ruff linting..."
    ruff check "${WORKSPACE}" --output-format=json > "${REPORT_DIR}/python-ruff.json" 2>&1 || true
    
    log_info "  Black formatting..."
    black --check "${WORKSPACE}" --quiet 2>&1 | tee -a "${REPORT_DIR}/python-format.log" >/dev/null 2>&1 || true
    
    log_info "  MyPy type checking..."
    mypy "${WORKSPACE}" --ignore-missing-imports 2>&1 | tee -a "${REPORT_DIR}/python-types.log" >/dev/null 2>&1 || true
    
    log_success "  Python validation complete"
}

# ============================================================
# JAVASCRIPT / TYPESCRIPT VALIDATORS
# ============================================================

validate_javascript() {
    log_lang "JavaScript/TypeScript"
    local js_files=$(find "${WORKSPACE}" \( -name "*.js" -o -name "*.ts" -o -name "*.jsx" -o -name "*.tsx" \) 2>/dev/null | wc -l)
    [ $js_files -eq 0 ] && log_warn "No JavaScript/TypeScript files found" && return 0
    
    log_info "  ESLint checking..."
    eslint "${WORKSPACE}" --format=json > "${REPORT_DIR}/javascript-eslint.json" 2>&1 || true
    
    log_info "  Prettier checking..."
    prettier "${WORKSPACE}" --check --loglevel=warn 2>&1 | tee -a "${REPORT_DIR}/javascript-prettier.log" >/dev/null 2>&1 || true
    
    log_success "  JavaScript/TypeScript validation complete"
}

# ============================================================
# BASH / SHELL VALIDATORS
# ============================================================

validate_bash() {
    log_lang "Bash/Shell"
    local sh_files=$(find "${WORKSPACE}" -name "*.sh" -not -path "*/.archived/*" 2>/dev/null | wc -l)
    [ $sh_files -eq 0 ] && log_warn "No Bash scripts found" && return 0
    
    log_info "  ShellCheck scanning..."
    find "${WORKSPACE}" -name "*.sh" -not -path "*/.archived/*" 2>/dev/null | xargs shellcheck --format=json > "${REPORT_DIR}/bash-shellcheck.json" 2>&1 || true
    
    log_success "  Bash/Shell validation complete"
}

# ============================================================
# HTML / CSS VALIDATORS
# ============================================================

validate_html() {
    log_lang "HTML/CSS"
    local html_files=$(find "${WORKSPACE}" \( -name "*.html" -o -name "*.css" \) 2>/dev/null | wc -l)
    [ $html_files -eq 0 ] && log_warn "No HTML/CSS files found" && return 0
    
    log_info "  HTMLHint checking..."
    find "${WORKSPACE}" -name "*.html" 2>/dev/null | xargs htmlhint --format=json > "${REPORT_DIR}/html-hint.json" 2>&1 || true
    
    log_info "  StyleLint checking..."
    stylelint "${WORKSPACE}/**/*.css" --formatter=json > "${REPORT_DIR}/css-stylelint.json" 2>&1 || true
    
    log_success "  HTML/CSS validation complete"
}

# ============================================================
# PHP VALIDATORS
# ============================================================

validate_php() {
    log_lang "PHP"
    local php_files=$(find "${WORKSPACE}" -name "*.php" 2>/dev/null | wc -l)
    [ $php_files -eq 0 ] && log_warn "No PHP files found" && return 0
    
    log_info "  PHP linting..."
    find "${WORKSPACE}" -name "*.php" 2>/dev/null | while read php_file; do
        php -l "$php_file" 2>&1 || log_warn "PHP error in $php_file"
    done | tee -a "${REPORT_DIR}/php-lint.log" >/dev/null
    
    log_success "  PHP validation complete"
}

# ============================================================
# C/C++ VALIDATORS
# ============================================================

validate_cpp() {
    log_lang "C/C++"
    local cpp_files=$(find "${WORKSPACE}" \( -name "*.cpp" -o -name "*.c" -o -name "*.hpp" -o -name "*.h" \) 2>/dev/null | wc -l)
    [ $cpp_files -eq 0 ] && log_warn "No C/C++ files found" && return 0
    
    log_info "  Clang checking..."
    find "${WORKSPACE}" \( -name "*.cpp" -o -name "*.c" -o -name "*.hpp" -o -name "*.h" \) 2>/dev/null | xargs -I {} clang --analyze {} 2>&1 | tee -a "${REPORT_DIR}/cpp-clang.log" >/dev/null 2>&1 || true
    
    log_success "  C/C++ validation complete"
}

# ============================================================
# JSON / YAML VALIDATORS
# ============================================================

validate_config() {
    log_lang "Config Files (JSON/YAML)"
    
    log_info "  JSON validation..."
    find "${WORKSPACE}" -name "*.json" 2>/dev/null | while read json_file; do
        jsonlint "$json_file" 2>&1 || log_warn "JSON error in $json_file"
    done | tee -a "${REPORT_DIR}/config-json.log" >/dev/null 2>&1 || true
    
    log_info "  YAML validation..."
    yamllint "${WORKSPACE}" 2>&1 | tee -a "${REPORT_DIR}/config-yaml.log" >/dev/null 2>&1 || true
    
    log_success "  Config file validation complete"
}

# ============================================================
# SECURITY SCAN
# ============================================================

validate_security() {
    log_info "Running security scan (Bandit)..."
    bandit -r "${WORKSPACE}" --exclude="${WORKSPACE}/.venv,${WORKSPACE}/.archived" -f json > "${REPORT_DIR}/security-bandit.json" 2>&1 || true
    log_success "Security scan complete"
}

# ============================================================
# RUN TESTS
# ============================================================

run_tests() {
    log_info "Running test suite..."
    pytest "${WORKSPACE}" --tb=short --verbose --cov="${WORKSPACE}" --cov-report=term-missing --cov-report=html:"${REPORT_DIR}/coverage" --junit-xml="${REPORT_DIR}/tests-junit.xml" 2>&1 | tee "${REPORT_DIR}/tests.log" || {
        if [ "${STRICT_MODE}" == "true" ]; then
            log_error "Tests failed (STRICT_MODE=true)"
            exit 1
        else
            log_warn "Tests failed but continuing (STRICT_MODE=false)"
        fi
    }
    log_success "Tests complete"
}

# ============================================================
# METRICS
# ============================================================

calculate_metrics() {
    log_info "Calculating code metrics..."
    {
        echo "=== Comprehensive Code Quality Metrics ==="
        echo "Timestamp: $(date)"
        echo ""
        echo "=== Language Breakdown ==="
        echo "Python files: $(find "${WORKSPACE}" -name "*.py" -not -path "*/.venv/*" 2>/dev/null | wc -l)"
        echo "JavaScript files: $(find "${WORKSPACE}" -name "*.js" 2>/dev/null | wc -l)"
        echo "TypeScript files: $(find "${WORKSPACE}" -name "*.ts" 2>/dev/null | wc -l)"
        echo "Bash scripts: $(find "${WORKSPACE}" -name "*.sh" -not -path "*/.archived/*" 2>/dev/null | wc -l)"
        echo "HTML files: $(find "${WORKSPACE}" -name "*.html" 2>/dev/null | wc -l)"
        echo "CSS files: $(find "${WORKSPACE}" -name "*.css" 2>/dev/null | wc -l)"
        echo "PHP files: $(find "${WORKSPACE}" -name "*.php" 2>/dev/null | wc -l)"
        echo "C/C++ files: $(find "${WORKSPACE}" \( -name "*.cpp" -o -name "*.c" \) 2>/dev/null | wc -l)"
        echo ""
        echo "=== Tool Versions ==="
        echo "Python: $(python3 --version 2>&1)"
        echo "Node.js: $(node --version)"
        echo "PHP: $(php --version 2>&1 | head -1)"
        echo ""
        echo "=== Reports ==="
        ls -lh "${REPORT_DIR}"/ 2>/dev/null | tail -n +2 || echo "No reports yet"
    } | tee "${REPORT_DIR}/metrics.txt"
    
    log_success "Metrics calculated"
}

# ============================================================
# WORKFLOW COMMANDS
# ============================================================

validate() {
    log_info "==== Multi-Language Validation Suite ===="
    log_info "Workspace: ${WORKSPACE}"
    log_info "Reports: ${REPORT_DIR}"
    echo ""
    
    validate_python && echo ""
    validate_javascript && echo ""
    validate_bash && echo ""
    validate_html && echo ""
    validate_php && echo ""
    validate_cpp && echo ""
    validate_config && echo ""
    validate_security && echo ""
    run_tests && echo ""
    calculate_metrics && echo ""
    
    log_info "==== Validation Suite Complete ===="
}

quick_find() {
    local roots=()
    local candidate
    for candidate in services tests scripts workers frontend app lib; do
        [ -d "${WORKSPACE}/${candidate}" ] && roots+=("${WORKSPACE}/${candidate}")
    done
    if [ ${#roots[@]} -eq 0 ]; then
        roots=("${WORKSPACE}")
    fi
    find "${roots[@]}" \
        \( -type d \( \
            -name .git -o -name .venv -o -name __pycache__ -o \
            -name node_modules -o -name .next -o -name .next-node26 -o \
            -name .pytest_cache -o -name .liara_scan_tmp -o \
            -name backups -o -name artifacts -o -name logs -o \
            -name data -o -name build -o -name dist -o -name coverage \
        \) -prune \) -o "$@" -print0
    find "${WORKSPACE}" -maxdepth 1 "$@" -print0
}

quick_validate() {
    log_info "==== Quick Multi-Language Check ===="
    log_info "Scope: first-party source roots; vendor/generated/runtime directories pruned"

    log_lang "Python"
    local py_count
    py_count=$(quick_find -type f -name "*.py" | tr -cd '\0' | wc -c)
    if [ "${py_count}" -gt 0 ]; then
        log_info "  ${py_count} files: syntax check..."
        quick_find -type f -name "*.py" | xargs -0 -r python3 -m py_compile \
            2>&1 | tee -a "${REPORT_DIR}/python-syntax.log" >/dev/null || true
        log_info "  Ruff linting..."
        quick_find -type f -name "*.py" | xargs -0 -r ruff check --output-format=concise \
            2>&1 | tee -a "${REPORT_DIR}/python-ruff.log" >/dev/null || true
        log_success "  Python quick validation complete"
    else
        log_warn "No Python files found"
    fi
    echo ""

    log_lang "JavaScript/TypeScript"
    local js_count
    js_count=$(quick_find -type f \( -name "*.js" -o -name "*.ts" -o -name "*.jsx" -o -name "*.tsx" \) | tr -cd '\0' | wc -c)
    if [ "${js_count}" -gt 0 ]; then
        local web_root="${WORKSPACE}/frontend/web-ui"
        if [ -x "${web_root}/node_modules/.bin/tsc" ] && [ -f "${web_root}/tsconfig.json" ]; then
            log_info "  ${js_count} files: project TypeScript check..."
            (cd "${web_root}" && ./node_modules/.bin/tsc --noEmit --incremental false) \
                2>&1 | tee -a "${REPORT_DIR}/typescript.log" >/dev/null || true
            log_success "  TypeScript quick validation complete"
        else
            log_warn "Project-local TypeScript runtime unavailable; skipped in quick scope"
        fi
    else
        log_warn "No JavaScript/TypeScript files found"
    fi
    echo ""

    log_lang "Bash/Shell"
    local sh_count
    sh_count=$(quick_find -type f -name "*.sh" | tr -cd '\0' | wc -c)
    if [ "${sh_count}" -gt 0 ]; then
        log_info "  ${sh_count} files: ShellCheck scanning..."
        quick_find -type f -name "*.sh" | xargs -0 -r shellcheck --format=json \
            > "${REPORT_DIR}/bash-shellcheck.json" 2>&1 || true
        log_success "  Bash quick validation complete"
    else
        log_warn "No Bash scripts found"
    fi
}

langs_only() {
    log_info "==== Language Validators Only ===="
    validate_python && echo "" && validate_javascript && echo "" && validate_bash && echo "" && validate_html && echo "" && validate_php && echo "" && validate_cpp && echo "" && validate_config
}

show_reports() {
    log_info "==== Available Reports ===="
    [ -d "${REPORT_DIR}" ] && ls -lh "${REPORT_DIR}"/ || log_warn "No reports found"
}

show_languages() {
    cat << 'EOF'
╔════════════════════════════════════════════════════════════╗
║          SUPPORTED LANGUAGES & VALIDATORS                 ║
╚════════════════════════════════════════════════════════════╝

✓ Python 3.12
  └─ pytest, mypy, black, ruff, pylint, flake8, isort, bandit

✓ JavaScript/TypeScript
  └─ ESLint, Prettier, TypeScript, ts-eslint

✓ Bash/Shell
  └─ ShellCheck

✓ HTML/CSS
  └─ HTMLHint, StyleLint

✓ PHP
  └─ PHP linter, PHPStan

✓ C/C++
  └─ Clang

✓ Config Files
  └─ JSON (jsonlint), YAML (yamllint)

✓ Security
  └─ Bandit

EOF
}

# Main
case "${1:-validate}" in
    validate) validate; exit $? ;;
    quick) quick_validate; exit $? ;;
    langs) langs_only; exit $? ;;
    python) validate_python; exit $? ;;
    javascript) validate_javascript; exit $? ;;
    bash) validate_bash; exit $? ;;
    html) validate_html; exit $? ;;
    php) validate_php; exit $? ;;
    cpp) validate_cpp; exit $? ;;
    config) validate_config; exit $? ;;
    test) run_tests; exit $? ;;
    security) validate_security; exit $? ;;
    reports) show_reports; exit $? ;;
    languages) show_languages; exit $? ;;
    metrics) calculate_metrics; exit $? ;;
    *) echo "Usage: $0 {validate|quick|langs|python|javascript|bash|html|php|cpp|config|test|security|reports|languages|metrics}"; exit 1 ;;
esac
