# AI Validator - Universal Multi-Language Container

**Version:** 2.0.0 (Polyglot)  
**Status:** Production Ready  
**Scope:** Standalone, Independent, Deployable Anywhere  

---

## Overview

The **AI Validator** is a self-contained Docker container for code validation across 7+ programming languages. It's designed to:

- ✅ Run **locally** on development machines
- ✅ Deploy **centrally** in Homelab/Proxmox clusters
- ✅ Scale across **multiple devices**
- ✅ Work with **any workspace/project**
- ✅ Integrate with **CI/CD pipelines**
- ✅ Support **Codex/Copilot** AI workflows

---

## Supported Languages

| Language | Tools | Status |
|----------|-------|--------|
| Python | pytest, mypy, black, ruff, pylint, flake8, isort, bandit | ✅ |
| JavaScript/TypeScript | ESLint, Prettier, TypeScript | ✅ |
| Bash/Shell | ShellCheck | ✅ |
| HTML/CSS | HTMLHint, jsonlint | ✅ |
| PHP | PHP linter | ✅ |
| C/C++ | Clang | ✅ |
| JSON/YAML | jsonlint, yamllint | ✅ |
| Security | Bandit | ✅ |

---

## Quick Start

### 1. Local Development

```bash
# From repo root
cd ai-validator

# Build image locally
docker build -f Dockerfile.ai-validator -t lab-web/ai-validator:latest .

# Validate current directory (quick check)
docker-compose run --rm ai-validator quick

# Full validation
docker-compose run --rm ai-validator validate

# Specific language
docker-compose run --rm ai-validator python
docker-compose run --rm ai-validator javascript
```

### 2. Homelab/Proxmox Deployment

```bash
# Copy to cluster
scp -r ai-validator/ user@proxmox-host:/opt/ai-validator/

# On Proxmox host
cd /opt/ai-validator

# Build once
docker build -f Dockerfile.ai-validator -t lab-web/ai-validator:latest .

# Start service
docker-compose up -d ai-validator

# Then from any device:
# Mount workspace and validate
docker-compose run -e WORKSPACE_PATH=/mnt/project ai-validator validate
```

### 3. Remote/Network Access

```bash
# Create .env with cluster settings
echo "WORKSPACE_PATH=/mnt/shared-workspace" > .env

# Start with network exposure (optional)
# Uncomment ports in docker-compose.yml, then:
docker-compose up -d

# Access from any device on network
docker-compose run --rm ai-validator validate
```

---

## Usage

### Via Docker Compose

```bash
# Configuration
cp .env.example .env
# Edit .env for your environment

# Show supported languages
docker-compose run --rm ai-validator languages

# Quick validation (3 main languages, ~30 sec)
docker-compose run --rm ai-validator quick

# Full validation (all languages, tests, security, metrics)
docker-compose run --rm ai-validator validate

# Language-specific
docker-compose run --rm ai-validator python
docker-compose run --rm ai-validator javascript
docker-compose run --rm ai-validator bash
docker-compose run --rm ai-validator html
docker-compose run --rm ai-validator php
docker-compose run --rm ai-validator cpp
docker-compose run --rm ai-validator config

# Other commands
docker-compose run --rm ai-validator test              # Tests only
docker-compose run --rm ai-validator security          # Security scan only
docker-compose run --rm ai-validator reports           # List reports
docker-compose run --rm ai-validator metrics           # Show metrics
```

### Via Direct Docker Run

```bash
# Build
docker build -f Dockerfile.ai-validator -t lab-web/ai-validator:latest .

# Run (mount your workspace)
docker run --rm \
  -v /path/to/workspace:/workspace:ro \
  -v ai-reports:/workspace/.reports \
  lab-web/ai-validator:latest \
  validate
```

---

## Directory Structure

```
ai-validator/
├── Dockerfile.ai-validator           # Container definition
├── ai-validator-entrypoint.sh        # Multi-language validation logic
├── docker-compose.yml                # Service configuration
├── .env.example                      # Configuration template
└── README.md                         # This file
```

---

## Configuration

### Local Development (.env)

```bash
WORKSPACE_PATH=.
AI_POLICY_PATH=./ai
```

### Homelab/Proxmox (.env)

```bash
# Shared storage mount
WORKSPACE_PATH=/mnt/nfs/projects

# Central policies location
AI_POLICY_PATH=/opt/ai-policies

# Optional: expose service port
# AI_VALIDATOR_PORT=9090
```

### Multi-Device Network (.env)

```bash
# NFS mount accessible from all devices
WORKSPACE_PATH=/mnt/shared-workspace

# Uncomment ports in docker-compose.yml
# docker-compose up -d
# Then access from any device on network
```

---

## Reports Output

After validation, reports are in `.reports/`:

```
.reports/
├── python-syntax.log
├── python-ruff.json
├── python-format.log
├── python-types.log
├── javascript-eslint.json
├── javascript-prettier.log
├── bash-shellcheck.json
├── html-hint.json
├── css-stylelint.json
├── php-lint.log
├── cpp-clang.log
├── config-json.log
├── config-yaml.log
├── security-bandit.json
├── tests-junit.xml
├── coverage/
└── metrics.txt
```

---

## Policy Compliance

✅ **Policies:**
- `ai/POLICY/SECURITY.md` - Isolated, least privilege, read-only workspace
- `ai/POLICY/DATA-GOVERNANCE.md` - No sensitive data exposure

✅ **Labels:**
- `use_for_ai=true` - Codex/Copilot integration ready
- `languages=python,javascript,bash,html,css,php,c,cpp,typescript,yaml,json`

✅ **Features:**
- Workspace: Read-only (code protected)
- Reports: Isolated volume
- Network: Configurable for cluster deployment
- Logging: JSON format for monitoring

---

## Deployment Scenarios

### Scenario 1: Local Development

```bash
cd ai-validator
docker-compose run --rm ai-validator quick
```

### Scenario 2: CI/CD Pipeline

```bash
# In your CI/CD config
docker-compose run --rm ai-validator validate
exit_code=$?
[ $exit_code -ne 0 ] && exit 1
```

### Scenario 3: Multi-Workspace Validation

```bash
# Edit run-multi-workspace.sh and add your workspaces
WORKSPACES=(
    "project-a:/path/to/project-a"
    "my-project:/path/to/my-project"
    "another-project:/path/to/another-project"
)

# Validate all in sequence (safe, slower)
./run-multi-workspace.sh quick serial

# Validate all in parallel (faster, more resources)
./run-multi-workspace.sh validate parallel

# Report aggregation in /tmp/ai-validator-reports-*/
```

### Scenario 4: Homelab/Proxmox Central Service

```bash
# Copy to Proxmox node
cp -r ai-validator/ /opt/ai-validator/
cd /opt/ai-validator/

# Configure for NFS workspace
cat > .env.workspace-central << 'EOF'
WORKSPACE_NAME=central
WORKSPACE_PATH=/mnt/nfs/projects
CONTAINER_NAME=ai-validator-central
EOF

# Run as daemon service
docker-compose --env-file .env.workspace-central up -d

# Accessible from any device on network
docker logs ai-validator-central
```

---

## Multi-Workspace Setup

Validate **multiple projects** with a single command:

### Quick Setup (3 steps)

**Step 1:** Configure workspaces

```bash
# Copy template
cp .env.workspace-template .env.workspace-backend
cp .env.workspace-template .env.workspace-frontend

# Edit each file
# .env.workspace-backend: WORKSPACE_PATH=/path/to/backend
# .env.workspace-frontend: WORKSPACE_PATH=/path/to/frontend
```

**Step 2:** Edit run-multi-workspace.sh

```bash
# Open and modify WORKSPACES array
WORKSPACES=(
    "backend:/path/to/backend"
    "frontend:/path/to/frontend"
)
```

**Step 3:** Validate all

```bash
# Serial mode (one by one, safer)
./run-multi-workspace.sh quick serial

# Parallel mode (all at once, faster)
./run-multi-workspace.sh validate parallel
```

### Configuration Files

- `run-multi-workspace.sh` - Main batch validation script
- `docker-compose.multi-workspace.yml` - Docker Compose override
- `.env.workspace-template` - Configuration template
- `.env.workspace-*` - Per-workspace configurations
- `MULTI-WORKSPACE-SETUP.md` - Detailed guide

### Available Commands

```bash
./run-multi-workspace.sh [COMMAND] [MODE]

COMMAND:
  quick      Quick check (default)
  validate   Full validation
  python     Python only
  javascript JavaScript/TypeScript only
  bash       Bash/Shell only
  security   Security scan only

MODE:
  serial     One by one (default, safer)
  parallel   All at once (faster)
```

### Examples

```bash
# Quick check, serial
./run-multi-workspace.sh

# Full validation, parallel
./run-multi-workspace.sh validate parallel

# Python only, serial
./run-multi-workspace.sh python serial

# Security scan, parallel
./run-multi-workspace.sh security parallel
```

### Output

Reports aggregated in `/tmp/ai-validator-reports-<timestamp>/`:

```
/tmp/ai-validator-reports-1735062345/
├── backend/
│   ├── python-syntax.log
│   ├── javascript-eslint.json
│   └── metrics.txt
├── frontend/
│   ├── javascript-eslint.json
│   └── metrics.txt
└── SUMMARY.md
```

**👉 See [MULTI-WORKSPACE-SETUP.md](MULTI-WORKSPACE-SETUP.md) for advanced configuration and examples.**

---

## Deployment Scenarios

### Scenario 1: Local Development

```bash
cd ai-validator
docker-compose run --rm ai-validator quick
```

### Scenario 2: CI/CD Pipeline

```bash
# In your CI/CD config
docker-compose run --rm ai-validator validate
exit_code=$?
[ $exit_code -ne 0 ] && exit 1
```

### Scenario 3: Proxmox Cluster

```bash
# On cluster host
docker-compose up -d
# Service runs continuously

# From any machine on network
docker-compose run --rm ai-validator validate
```

### Scenario 4: Codex/Copilot Workflow

```yaml
# From AI agent
tool: ai_validator
command: validate
workspace: /any/path
output: reports/metrics.txt
```

---

## Troubleshooting

### Build fails
```bash
# Rebuild with verbose output
docker build -f Dockerfile.ai-validator -t lab-web/ai-validator:latest . --progress=plain
```

### Reports not generated
```bash
# Check volume
docker volume inspect ai-validator-reports

# Or use temporary directory
docker-compose run -e REPORT_DIR=/tmp/reports ai-validator validate
docker cp ai-validator:/tmp/reports .
```

### Workspace path issues
```bash
# Verify path exists and is readable
ls -la /path/to/workspace

# Test mount
docker run --rm -v /path/to/workspace:/workspace:ro alpine ls -la /workspace
```

---

## Integration Points

| System | Integration |
|--------|-----------|
| **AI Tools** | `ai/TOOLS/tools.yaml` (registered as tool #6) |
| **Tool Contracts** | `ai/TOOLS/contracts/ai_validator_container.md` |
| **Policies** | `ai/POLICY/SECURITY.md`, `DATA-GOVERNANCE.md` |
| **Registry** | `ai/TOOLS/registry.md` |

---

## Performance Characteristics

| Operation | Duration | Resources |
|-----------|----------|-----------|
| Build | ~3-5 min | ~827MB disk |
| Quick validate | ~30 sec | 1 CPU, 1GB RAM |
| Full validate | ~2-3 min | 2 CPUs, 2GB RAM |
| Report generation | Variable | Depends on codebase |

---

## Advanced Usage

### Custom workspace path

```bash
docker-compose run \
  -e WORKSPACE_PATH=/mnt/project1 \
  ai-validator validate
```

### Multiple projects simultaneously

```bash
# Project 1
docker-compose run -e WORKSPACE_PATH=/projects/app1 ai-validator validate &

# Project 2
docker-compose run -e WORKSPACE_PATH=/projects/app2 ai-validator validate &

wait
```

### Extract specific reports

```bash
# Get Python linting only
docker-compose run --rm ai-validator python

# Check security findings
docker volume inspect ai-validator-reports
# Then review security-bandit.json
```

---

## Support & Documentation

- **Quick Start:** See README.md (this file)
- **Container Spec:** `ai/TOOLS/contracts/ai_validator_container.md`
- **Policies:** `ai/POLICY/SECURITY.md`
- **Policies:** `ai/POLICY/DATA-GOVERNANCE.md`

---

**Status:** Production Ready ✅  
**License:** Aligned with project policies  
**Deployment:** Local, Network, Cluster-ready
