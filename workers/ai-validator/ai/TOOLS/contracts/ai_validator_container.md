# AI Validator Container Contract

**Version:** 1.0.0  
**Status:** Operational ✅  
**Owner:** AI Agent System  
**Created:** 2025-12-24  

---

## Purpose

Provides isolated, reproducible environment for code validation and testing workflows controlled by AI agents. Enforces security (least privilege, no external access) and compliance with `ai/POLICY/SECURITY.md` and `ai/POLICY/DATA-GOVERNANCE.md`.

---

## Metadata

```yaml
name: ai-validator
type: docker-container
labels:
  ai-validator: "true"
  use_for_ai: "true"
  policy: "ai/POLICY/SECURITY.md"
base_image: python:3.12-slim
platforms:
  - linux/amd64
  - linux/arm64
```

---

## Capabilities

### Validation Workflows

| Command | Purpose | Output | Duration |
|---------|---------|--------|----------|
| `validate` (default) | Full validation suite | All reports | ~45s |
| `quick` | Fast syntax + style check | Two reports | ~10s |
| `test` | Run pytest suite only | Test report | ~30s |
| `types` | MyPy type checking | types.log | ~15s |
| `security` | Security scan (bandit) | security.json | ~20s |
| `style` | Code style checks | ruff.json, format.log | ~10s |

### Tools Included

- **Python 3.12** - Core runtime
- **pytest 7.4.3** - Unit testing framework
- **mypy 1.7.1** - Static type checking
- **black 23.12.1** - Code formatting
- **ruff 0.1.9** - Fast linter
- **pylint 3.0.3** - Comprehensive linter
- **flake8 6.1.0** - Style guide checker
- **isort 5.13.2** - Import sorting
- **bandit 1.7.5** - Security scanner

---

## Interface

### Input

**Mount Points:**
```docker
VOLUME /workspace              # Read-only workspace (source code)
VOLUME /workspace/ai           # Read-only AI policies and configs
VOLUME /workspace/.reports     # Write: validation reports
```

**Environment Variables:**
```bash
WORKSPACE="/workspace"              # Code root (read-only)
REPORT_DIR="/workspace/.reports"    # Report output (read-write)
STRICT_MODE="false"                 # Exit on test failure?
PYTHONUNBUFFERED="1"                # Immediate logging
```

**Command Examples:**
```bash
# Full validation
docker run ai-validator:latest validate

# Quick checks
docker run ai-validator:latest quick

# Type checking only
docker run ai-validator:latest types

# Security scan
docker run ai-validator:latest security
```

### Output

**Report Directory Structure:**
```
.reports/
├── metrics.txt              # Code metrics summary
├── syntax.log               # Python syntax validation
├── types.log                # MyPy type checking results
├── ruff.json                # Ruff linting results (JSON)
├── format.log               # Black formatting issues
├── security.json            # Bandit security findings
├── tests.log                # Pytest output
├── junit.xml                # Test results (XML)
├── coverage/                # HTML coverage report
│   ├── index.html
│   ├── status.json
│   └── ...
```

**Exit Codes:**
```bash
0   # All validations passed
1   # Validation failed (test, syntax, or STRICT_MODE)
2   # Invalid command
```

---

## Execution

### Docker Run (Direct)

```bash
# Full validation
docker run -v /path/to/workspace:/workspace:ro \
  lab-web/ai-validator:latest validate

# With reports volume
docker run -v /path/to/workspace:/workspace:ro \
  -v ai-reports:/workspace/.reports \
  lab-web/ai-validator:latest validate
```

### Docker Compose (Recommended)

```yaml
ai-validator:
  container_name: ai-validator
  build:
    context: ../
    dockerfile: ./Dockerfile.ai-validator
  image: lab-web/ai-validator:latest
  environment:
    WORKSPACE: /workspace
    REPORT_DIR: /workspace/.reports
    STRICT_MODE: "false"
  volumes:
    - ../:/workspace:ro
    - ai-validator-reports:/workspace/.reports
  command: validate
  restart: "no"
```

### Via Make/Shell Script

```bash
# Build image
make build-ai-validator
# or: docker build -f Dockerfile.ai-validator -t lab-web/ai-validator .

# Run validation
make validate-ai
# or: (standalone) cd ai-validator && docker-compose run --rm ai-validator validate

# Check reports
make validate-reports
# or: docker run ai-validator:latest reports
```

---

## Constraints & Limitations

### Security (Policy: ai/POLICY/SECURITY.md)

✅ **Allowed:**
- Local file system access (read-only to workspace)
- Report generation (write to `/workspace/.reports`)
- Process spawning (Python, linters, test runners)
- Environment variable access

❌ **Forbidden:**
- External network access (no internet)
- Credential/secret exposure
- Writing outside `.reports` volume
- Privileged execution
- Access to host system resources

### Resource Limits

```yaml
# Recommended constraints
resources:
  limits:
    cpus: "2"
    memory: "2G"
  reservations:
    cpus: "1"
    memory: "1G"
```

### Timeouts

```bash
Full validate:  ~45 seconds
Quick:          ~10 seconds
Single tool:    ~20 seconds max
```

---

## Error Handling

### Known Error Cases

| Scenario | Behavior | Recovery |
|----------|----------|----------|
| Missing requirements.txt | Tests may fail | Provide test dependencies |
| No Python files found | Quick validation passes | Warnings in logs |
| Syntax error in code | Marked in syntax.log | Fix code before re-run |
| Import failures | Type checking skips | Install dependencies |
| Test failures | Logged unless STRICT_MODE=true | Fix tests or set STRICT_MODE=false |
| Security findings | Logged, container succeeds | Review bandit output |

### Debugging

```bash
# View last run logs
docker logs ai-validator

# Interactive shell
docker run -it ai-validator:latest /bin/bash

# Show reports
docker run ai-validator:latest reports

# Check tool versions
docker run ai-validator:latest bash -c "python --version && pytest --version"
```

---

## Permissions & Access Control

**Container User:** root (trusted environment)  
**File Permissions:**
- Workspace: Read-only (0444)
- Reports: Read-write (0777)

**Network:** None (isolated)  
**Volumes:** Mounted read-only except `.reports`

---

## Related Documentation

- Policy: [ai/POLICY/SECURITY.md](../../ai/POLICY/SECURITY.md)
- Policy: [ai/POLICY/DATA-GOVERNANCE.md](../../ai/POLICY/DATA-GOVERNANCE.md)
- Docker Reference: [Dockerfile.ai-validator](../../Dockerfile.ai-validator)
- Script: [scripts/ai-validator-entrypoint.sh](../../scripts/ai-validator-entrypoint.sh)
- Compose: [ai-validator/docker-compose.yml](../../../docker-compose.yml)
- Registry: [ai/TOOLS/registry.md](../registry.md)

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2025-12-24 | Initial release with full validation suite |

---

## Status & Health

**Container Status:** ✅ Operational  
**Last Test:** 2025-12-24  
**Health Check:** Every 30s (python -c "import sys; sys.exit(0)")  
**Restart Policy:** "no" (manual control recommended)

---

## Integration Examples

### In AI Workflows (playbooks/)

```yaml
# coding-flow playbook usage
- name: Validate code changes
  tool: ai-validator
  command: validate
  expect_success: true
  
- name: Quick check during development
  tool: ai-validator
  command: quick
  expect_success: true (STRICT_MODE=false)
```

### In CI/CD Pipeline

```bash
#!/bin/bash
cd ai-validator && docker-compose run --rm ai-validator validate
EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
  echo "Validation failed!"
  exit 1
fi
```

### Manual Usage

```bash
# Terminal 1: Start container and keep running
cd ai-validator && docker-compose up -d ai-validator

# Terminal 2: Check reports
cd ai-validator && docker-compose exec ai-validator reports
```

---

**Policy Alignment:** ✅ Fully compliant with SECURITY.md, DATA-GOVERNANCE.md  
**Testing Status:** ✅ Tested and operational  
**Ready for:** AI agent integration, CI/CD pipelines, development workflows
