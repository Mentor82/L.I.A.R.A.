# Tool Contract: validation_dashboard

## Metadata
- **Tool Name:** validation_dashboard
- **Version:** 1.0.0
- **Owner:** testing-infrastructure
- **Status:** active

## Purpose
Full validation dashboard combining unit tests, code quality analysis, API endpoint verification, and performance benchmarks.

## Interface

### Inputs
```
None - Runs comprehensive validation automatically
```

### Outputs
```
Success:
  {
    "unit_tests": {
      "passed": number,
      "failed": number,
      "skipped": number,
      "status": "pass" | "fail"
    },
    "code_quality": {
      "average_score": number,
      "files_analyzed": number,
      "status": "pass" | "warn" | "fail"
    },
    "integration": {
      "endpoints_tested": number,
      "endpoints_passed": number,
      "status": "pass" | "partial" | "fail"
    },
    "performance": {
      "tool_discovery": {time: number, status: string},
      "model_list": {time: number, status: string},
      "api_response": {time: number, status: string}
    },
    "summary": {
      "overall_score": percentage,
      "grade": string,
      "status": string,
      "recommendations": [string]
    }
  }

Error:
  {
    "error": string,
    "phase": string,
    "details": string
  }
```

## Execution

### Command
```bash
python test_dashboard.py
```

## Constraints

### Permissions Required
- `read`: Access to test and source files
- `execute`: Run test suite and analysis tools
- `network`: Connect to localhost:3333 for API tests

### Dependencies
- Python 3.12+
- pytest 9.0+
- requests library
- Virtual environment with all packages

### Time Complexity
- Unit tests: ~50 seconds
- Code quality: ~5 seconds
- API tests: ~10 seconds
- Performance: ~5 seconds
- Total: ~70 seconds

## Validation Phases

### Phase 1: Unit Tests
- Runs pytest with verbose output
- Collects pass/fail/skip statistics

### Phase 2: Code Quality
- Analyzes Python files for style, complexity, docstrings
- Generates quality score

### Phase 3: Integration Tests
- Tests API endpoints (GET /models, /tools, POST /generate, /chat)
- Verifies connectivity and response formats

### Phase 4: Performance
- Benchmarks tool discovery speed
- Measures model list retrieval time
- Tests API response latency

## Success Criteria
- Unit tests: 95%+ success rate
- Code quality: Average >= 70/100
- API endpoints: All 4 responding
- Performance: All under target thresholds

## Related Documents
- `/Users/mirkowaldhauer/lab-web/test_dashboard.py`
- `/Users/mirkowaldhauer/lab-web/TESTING_GUIDE.md`
- `/Users/mirkowaldhauer/lab-web/FINAL_TESTING_SUMMARY.txt`
- `ai/TOOLS/registry.md`
