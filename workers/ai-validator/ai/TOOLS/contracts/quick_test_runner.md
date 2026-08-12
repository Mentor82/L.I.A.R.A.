# Tool Contract: quick_test_runner

## Metadata
- **Tool Name:** quick_test_runner
- **Version:** 1.0.0
- **Owner:** testing-infrastructure
- **Status:** active

## Purpose
Quick test runner for rapid code validation (30 seconds total). Runs code quality checks, basic unit tests, and functionality validation without full integration tests.

## Interface

### Inputs
```
None - Optimized for speed with default configuration
```

### Outputs
```
Success:
  {
    "code_quality": {
      "score": number,
      "status": string
    },
    "unit_tests": {
      "classes_tested": ["TestMCPClientBasics", "TestMCPClientTools", "TestMCPClientModels"],
      "tests_passed": number,
      "tests_failed": number,
      "status": string
    },
    "functionality": {
      "models_found": number,
      "tools_discovered": number,
      "generation_working": boolean,
      "status": string
    },
    "performance": {
      "tool_discovery_time": number,
      "model_list_time": number,
      "status": string
    },
    "summary": {
      "total_duration": number,
      "all_passed": boolean,
      "status": string
    }
  }

Error:
  {
    "error": string,
    "step": string
  }
```

## Execution

### Command
```bash
bash run_tests.sh
```

## Constraints

### Permissions Required
- `read`: Access to test files
- `execute`: Run Python and shell scripts

### Dependencies
- Python 3.12+
- Virtual environment activated
- pytest (lightweight mode)
- bash shell

### Time Complexity
- Total execution: ~30 seconds
- Code quality: ~3 seconds
- Unit tests (subset): ~10 seconds
- Functionality test: ~10 seconds
- Performance: ~5 seconds

## Optimization Strategy

### What's Included
- Code quality analysis (workspace-level)
- Basic unit tests (3 test classes, 8 tests)
- Quick functionality verification (models, tools, generation)
- Performance benchmarks (tool discovery, model list)

### What's Excluded
- Full unit test suite (use run_unit_tests for that)
- Detailed error case testing
- Multi-turn chat testing
- Complete integration suite

## Use Cases

### When to Use quick_test_runner
- Before committing code
- Quick validation after changes
- CI/CD pipeline fast checks
- Development iteration cycles

### When to Use run_unit_tests
- Before production deployment
- Comprehensive validation needed
- Debugging specific issues
- Full regression testing

## Success Criteria
- Total duration < 30 seconds
- All code quality checks pass
- Core functionality working
- Performance within thresholds

## Related Documents
- `/Users/mirkowaldhauer/lab-web/run_tests.sh`
- `/Users/mirkowaldhauer/lab-web/TESTING_GUIDE.md`
- `ai/TOOLS/registry.md`
