# registry

This file lists the canonical tool inventory.

## Format
Each tool entry should include:
- name
- purpose
- owner
- inputs/outputs (reference to contracts)
- permissions and constraints
- error codes reference

## Sync rules
- `ai/TOOLS/tools.yaml` is the structured source of truth.
- This file explains intent and usage at a human-readable level.

## Tools

### Testing & Validation Infrastructure

#### 1. run_unit_tests
- **Purpose:** Execute comprehensive MCP client unit test suite (24 tests across 8 classes)
- **Owner:** testing-infrastructure
- **Permissions:** read, execute
- **Input:** test_class (optional, e.g., "TestMCPClientBasics")
- **Output:** Test results with pass/fail counts, coverage metrics
- **Contract:** ai/TOOLS/contracts/run_unit_tests.md
- **Usage:** `python -m pytest test_mcp_client.py -v` or specific class tests

#### 2. code_quality_check
- **Purpose:** Static code analysis for PEP 8 compliance, complexity, docstrings
- **Owner:** testing-infrastructure
- **Permissions:** read
- **Input:** file_or_directory path (optional)
- **Output:** Quality metrics (syntax, docstrings, complexity, line length)
- **Contract:** ai/TOOLS/contracts/code_quality_check.md
- **Usage:** `python code_quality.py`

#### 3. validation_dashboard
- **Purpose:** Full validation dashboard combining all test metrics and performance benchmarks
- **Owner:** testing-infrastructure
- **Permissions:** read
- **Input:** None
- **Output:** Comprehensive dashboard with test results, code quality, performance
- **Contract:** ai/TOOLS/contracts/validation_dashboard.md
- **Usage:** `python test_dashboard.py`

#### 4. quick_test_runner
- **Purpose:** Quick test runner for rapid validation (30 seconds total)
- **Owner:** testing-infrastructure
- **Permissions:** read, execute
- **Input:** None
- **Output:** Quick test results with summary
- **Contract:** ai/TOOLS/contracts/quick_test_runner.md
- **Usage:** `bash run_tests.sh`

#### 5. generate_test_report
- **Purpose:** Generate comprehensive testing and validation report
- **Owner:** testing-infrastructure
- **Permissions:** read
- **Input:** None
- **Output:** Detailed test report saved to FINAL_TESTING_SUMMARY.txt
- **Contract:** ai/TOOLS/contracts/generate_test_report.md
- **Usage:** `python generate_testing_summary.py`

#### 6. ai_validator
- **Purpose:** Isolated Docker container for AI-driven code validation and testing
- **Owner:** ai-agent-system
- **Type:** docker-container
- **Permissions:** read, execute, write_reports
- **Image:** lab-web/ai-validator:latest
- **Dockerfile:** Dockerfile.ai-validator
- **Entrypoint:** scripts/ai-validator-entrypoint.sh
- **Policy:** ai/POLICY/SECURITY.md (least privilege, isolated)
- **Labels:** use_for_ai=true
- **Contract:** ai/TOOLS/contracts/ai_validator_container.md
- **Included Tools:** pytest, mypy, black, ruff, pylint, flake8, isort, bandit
- **Usage Examples (universal/standalone):**
  - Build: `docker build -f ai-validator/Dockerfile.ai-validator -t lab-web/ai-validator:latest ai-validator`
  - Quick (repo root): `docker run --rm -v $(pwd):/workspace:ro lab-web/ai-validator:latest quick`
  - Full (repo root): `docker run --rm -v $(pwd):/workspace:ro lab-web/ai-validator:latest validate`
  - Quick (standalone compose): `cd ai-validator && docker-compose run --rm ai-validator quick`
  - Full (standalone compose): `cd ai-validator && docker-compose run --rm ai-validator validate`
  - Types: `cd ai-validator && docker-compose run --rm ai-validator types`
  - Security: `cd ai-validator && docker-compose run --rm ai-validator security`
  - Tests: `cd ai-validator && docker-compose run --rm ai-validator test`
- **Volume Mounts:**
  - `/workspace` → read-only workspace
  - `/workspace/ai` → read-only AI policies
  - `/workspace/.reports` → read-write report output
- **Exit Codes:** 0=success, 1=validation failed, 2=invalid command
- **Environment:** WORKSPACE, REPORT_DIR, STRICT_MODE (configurable)
- **Reports:** All findings in `.reports/` (metrics, logs, JSON, HTML coverage)
