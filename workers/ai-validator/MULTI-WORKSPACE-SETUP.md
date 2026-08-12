# AI Validator - Multi-Workspace Setup Guide

## Overview

The AI Validator supports validating **multiple workspaces** simultaneously, enabling centralized code quality management across your entire project ecosystem.

## Use Cases

- **Multi-Project Organizations**: Validate Python backend, JavaScript frontend, and DevOps scripts in one operation
- **Monorepo Structure**: Validate each service independently with separate reports
- **CI/CD Pipelines**: Batch validation across all repositories
- **Homelab/Proxmox**: Central validation service for multiple developer machines

## Setup Options

### Option 1: Environment Variables (Recommended for CLI)

```bash
# Simple one-liner validation
cd ai-validator/

# Validate project-a workspace
WORKSPACE_NAME=project-a \
WORKSPACE_PATH=/path/to/project-a \
CONTAINER_NAME=ai-validator-project-a \
docker-compose run --rm ai-validator quick

# Validate another project
WORKSPACE_NAME=my-project \
WORKSPACE_PATH=/path/to/my-project \
CONTAINER_NAME=ai-validator-my-project \
docker-compose run --rm ai-validator validate
```

### Option 2: .env.workspace-NAME Files (Recommended for Persistence)

Create workspace-specific configuration files:

```bash
cd ai-validator/

# Copy template and customize
cp .env.workspace-template .env.workspace-project-a
cp .env.workspace-template .env.workspace-my-project

# Edit .env.workspace-project-a
# WORKSPACE_NAME=project-a
# WORKSPACE_PATH=/path/to/project-a
# CONTAINER_NAME=ai-validator-project-a

# Run with workspace config
docker-compose --env-file .env.workspace-project-a run --rm ai-validator quick
docker-compose --env-file .env.workspace-my-project run --rm ai-validator validate
```

### Option 3: Docker Compose Override (For Long-Running Services)

```bash
cd ai-validator/

# Start multiple validators as services
WORKSPACE_NAME=project-a \
WORKSPACE_PATH=/path/to/project-a \
CONTAINER_NAME=ai-validator-project-a \
docker-compose -f docker-compose.yml -f docker-compose.multi-workspace.yml up -d

# View logs
docker logs ai-validator-project-a

# Stop service
docker-compose -f docker-compose.yml -f docker-compose.multi-workspace.yml down
```

### Option 4: Batch Script (Recommended for Automation)

The easiest way to validate **all workspaces at once**:

```bash
cd ai-validator/

# Edit run-multi-workspace.sh and add your workspaces:
# WORKSPACES=(
#     "project-a:/path/to/project-a"
#     "my-project:/path/to/my-project"
#     "another-project:/path/to/another-project"
# )

# Quick validation (serial, one by one)
./run-multi-workspace.sh quick

# Full validation (parallel, faster)
./run-multi-workspace.sh validate parallel

# Python-only check, serial
./run-multi-workspace.sh python serial

# Security scan, parallel
./run-multi-workspace.sh security parallel
```

## Configuration Templates

### .env.workspace-project-a

```dotenv
WORKSPACE_NAME=project-a
WORKSPACE_PATH=/path/to/project-a
CONTAINER_NAME=ai-validator-project-a
COMPOSE_PROJECT_NAME=ai-validator-project-a
REPORT_DIR=/reports/project-a
STRICT_MODE=false
```

### .env.workspace-template

Copy this and modify for your project:

```dotenv
WORKSPACE_NAME=my-project
WORKSPACE_PATH=/path/to/my-project
CONTAINER_NAME=ai-validator-${WORKSPACE_NAME}
COMPOSE_PROJECT_NAME=ai-validator-${WORKSPACE_NAME}
REPORT_DIR=/reports/${WORKSPACE_NAME}
STRICT_MODE=false
DEBUG_MODE=false
```

## Reporting & Aggregation

### Individual Workspace Reports

Each workspace generates separate reports in its volume:

```
ai-validator-reports-project-a/
├── python-syntax.log
├── python-ruff.json
├── javascript-eslint.json
├── bash-shellcheck.json
├── html-hint.json
├── security-bandit.json
└── metrics.txt

ai-validator-reports-my-project/
├── [similar structure]
```

### Batch Report Aggregation

The `run-multi-workspace.sh` script creates a summary:

```
/tmp/ai-validator-reports-<timestamp>/
├── project-a/
│   ├── python-syntax.log
│   ├── security-bandit.json
│   └── metrics.txt
├── my-project/
│   ├── javascript-eslint.json
│   └── metrics.txt
└── SUMMARY.md
```

## Examples

### Example 1: Two Projects (Serial Mode)

```bash
# Setup: Create workspace configs
cp .env.workspace-template .env.workspace-backend
cp .env.workspace-template .env.workspace-frontend

# Edit configs
# .env.workspace-backend: WORKSPACE_PATH=/opt/backend
# .env.workspace-frontend: WORKSPACE_PATH=/opt/frontend

# Validate one by one (safe)
./run-multi-workspace.sh quick serial
```

**Output:**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Workspace: backend
Command: quick
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[INFO]   Syntax check...
[✓]     Python validation complete
[INFO]   ESLint checking...
[✓]     JavaScript/TypeScript validation complete
...
[✓] backend validation completed

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Workspace: frontend
Command: quick
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[INFO]   Syntax check...
[✓]     JavaScript/TypeScript validation complete
...
[✓] frontend validation completed

╔═══════════════════════════════════════════════════════════════╗
║           MULTI-WORKSPACE VALIDATION SUMMARY                  ║
╚═══════════════════════════════════════════════════════════════╝
Command:    quick
Mode:       serial
Workspaces: 2
Reports:    /tmp/ai-validator-reports-1735062345/

  ✓ backend (/opt/backend)
  ✓ frontend (/opt/frontend)
```

### Example 2: Three Projects (Parallel Mode - Faster)

```bash
# Edit run-multi-workspace.sh:
WORKSPACES=(
    "backend:/opt/backend"
    "frontend:/opt/frontend"
    "devops:/opt/devops"
)

# Run in parallel (all start simultaneously)
./run-multi-workspace.sh validate parallel

# Much faster than serial!
# Takes ~5 minutes instead of ~15 minutes
```

### Example 3: Continuous Integration

In your CI/CD pipeline (GitHub Actions, GitLab CI, etc.):

```yaml
# .github/workflows/validate.yml
name: Multi-Workspace Validation

on: [push, pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Setup Docker
        run: docker version
      - name: Validate all workspaces
        run: |
          cd ai-validator/
          ./run-multi-workspace.sh validate parallel
      - name: Upload reports
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: validation-reports
          path: /tmp/ai-validator-reports-*/
```

### Example 4: Homelab Central Service

Running on Proxmox with NFS mounts:

```bash
# On Proxmox node
cd /opt/ai-validator/

# Create workspace configs for mounted projects
cat > .env.workspace-nfs-project1 << 'EOF'
WORKSPACE_NAME=nfs-project1
WORKSPACE_PATH=/mnt/nfs/projects/project1
CONTAINER_NAME=ai-validator-project1
REPORT_DIR=/reports/project1
EOF

# Start as daemon service
docker-compose \
  --env-file .env.workspace-nfs-project1 \
  -f docker-compose.yml \
  -f docker-compose.multi-workspace.yml \
  up -d

# Accessible from any machine on network
docker logs ai-validator-project1
```

## Advanced Configuration

### Custom Reporting Paths

```bash
# Separate output directory per workspace
REPORT_DIR=/mnt/shared-reports/project-a ./run-multi-workspace.sh
REPORT_DIR=/mnt/shared-reports/my-project ./run-multi-workspace.sh
```

### Strict Mode (Fail on Errors)

```bash
# Exit with error if any issues found
STRICT_MODE=true ./run-multi-workspace.sh validate
```

### Debug Mode (Verbose Output)

```bash
# See detailed validation output
DEBUG_MODE=true ./run-multi-workspace.sh quick
```

### Custom Exclusions

```bash
# Skip certain file patterns
EXCLUDE_PATTERNS="node_modules,dist,build,.git" ./run-multi-workspace.sh
```

## Monitoring & Health Checks

### Docker Stats

```bash
# Monitor resource usage of all validators
docker stats ai-validator-*
```

### View Reports Real-Time

```bash
# Watch reports as they're generated
watch -n 2 'ls -lt /tmp/ai-validator-reports-*/*/json | head -20'
```

### Aggregate Metrics

```bash
# Combine metrics from all workspaces
for ws in /tmp/ai-validator-reports-*/*/metrics.txt; do
  echo "=== $(dirname $ws) ==="
  cat $ws
done
```

## Troubleshooting

### Issue: "No workspaces configured"

**Solution**: Edit `run-multi-workspace.sh` and add workspaces:

```bash
WORKSPACES=(
    "project-a:/path/to/project-a"
    "my-project:/path/to/my-project"
)
```

### Issue: Parallel validations run out of memory

**Solution**: Use serial mode instead:

```bash
./run-multi-workspace.sh validate serial
```

### Issue: Reports not being generated

**Solution**: Check volume permissions:

```bash
docker volume ls | grep ai-validator
docker volume inspect ai-validator-reports-project-a
```

### Issue: Container name conflicts

**Solution**: Use unique CONTAINER_NAME per workspace:

```bash
CONTAINER_NAME=ai-validator-project-a-$(date +%s) docker-compose run --rm ai-validator quick
```

## Best Practices

1. **Use .env.workspace-NAME files** for persistent configurations
2. **Serial mode by default**, parallel only for powerful machines
3. **Aggregate reports** for overview across projects
4. **Monitor disk space** for large workspaces (reports can be 500MB+)
5. **Use timestamps** in report directories to track history
6. **Set up cron jobs** for scheduled validations:

```bash
# Validate all workspaces daily at 2 AM
0 2 * * * cd /opt/ai-validator && ./run-multi-workspace.sh validate serial >> /var/log/ai-validator.log 2>&1
```

## Quick Reference

```bash
# Single workspace, quick check
WORKSPACE_PATH=/path/to/project docker-compose run --rm ai-validator quick

# Single workspace, full validation
WORKSPACE_PATH=/path/to/project docker-compose run --rm ai-validator validate

# Multiple workspaces, serial mode
./run-multi-workspace.sh quick serial

# Multiple workspaces, parallel mode
./run-multi-workspace.sh validate parallel

# Specific language only
./run-multi-workspace.sh python serial
./run-multi-workspace.sh javascript parallel

# Security scan only
./run-multi-workspace.sh security serial
```

## Integration Examples

### With Make

```makefile
validate:
	cd ai-validator && ./run-multi-workspace.sh quick

validate-full:
	cd ai-validator && ./run-multi-workspace.sh validate parallel
```

### With Docker Compose

```yaml
# docker-compose.yml (in your project)
services:
  validator:
    image: lab-web/ai-validator:latest
    volumes:
      - .:/workspace:ro
      - validator-reports:/reports
    environment:
      WORKSPACE: /workspace
      REPORT_DIR: /reports

volumes:
  validator-reports:
```

### With Shell Alias

```bash
# Add to ~/.zshrc or ~/.bashrc
alias validate-all='cd ~/ai-validator && ./run-multi-workspace.sh'
alias validate-quick='cd ~/ai-validator && ./run-multi-workspace.sh quick'
```

---

**Next Steps:**
1. Add workspaces to `run-multi-workspace.sh`
2. Create `.env.workspace-*` files for each project
3. Test with `./run-multi-workspace.sh --help`
4. Schedule batch validations in your CI/CD pipeline
