# Tool Contract: run_unit_tests

## Metadata
- **Tool Name:** run_unit_tests
- **Version:** 1.0.0
- **Owner:** testing-infrastructure
- **Status:** active

## Purpose
Execute comprehensive MCP client unit test suite (24 unit tests across 8 test classes).

## Interface

### Inputs
```
Optional:
  test_class: string
    - Specific test class to run (e.g., "TestMCPClientBasics")
    - If omitted, runs all tests
    - Valid values: TestMCPClientBasics, TestMCPClientTools, TestMCPClientModels, 
      TestMCPClientGenerate, TestMCPClientChat, TestErrorHandling, TestCodeQuality, 
      TestPerformance
```

### Outputs
```
Success:
  {
    "total_tests": number,
    "passed": number,
    "failed": number,
    "skipped": number,
    "success_rate": percentage,
    "duration_seconds": number,
    "test_details": [
      {
        "test_name": string,
        "status": "PASSED" | "FAILED" | "SKIPPED",
        "message": string
      }
    ]
  }

Error:
  {
    "error": string,
    "exit_code": number
  }
```

## Execution

### Command
```bash
# All tests
python -m pytest test_mcp_client.py -v

# Specific class
python -m pytest test_mcp_client.py::TestMCPClientBasics -v

# Specific method
python -m pytest test_mcp_client.py::TestMCPClientBasics::test_client_initialization -v
```

### Exit Codes
- `0` - All tests passed
- `1` - Some tests failed
- `2` - Test collection error
- `3` - Internal error

## Constraints

### Permissions Required
- `read`: Access to test files
- `execute`: Run Python test suite

### Dependencies
- pytest 9.0+
- Python 3.12+
- Virtual environment activated
- ollama-mcp server running on localhost:3333 (for integration tests)

### Time Complexity
- Quick tests (basics): ~5 seconds
- Full suite: ~50 seconds
- With coverage: ~60 seconds

## Success Criteria
- Exit code is 0
- All non-skipped tests pass
- Success rate >= 95%

## Error Handling

### Known Error Cases
1. **Server unavailable**: If ollama-mcp not running, some tests skip gracefully
2. **Import errors**: Indicates missing dependencies
3. **Timeout**: Tests should complete within 60 seconds

### Recovery Actions
1. Ensure virtual environment is activated
2. Verify ollama-mcp server is running (docker ps)
3. Run `pip install -r requirements.txt` if import errors occur

## Related Documents
- `/Users/mirkowaldhauer/lab-web/test_mcp_client.py`
- `/Users/mirkowaldhauer/lab-web/TESTING_GUIDE.md`
- `ai/TOOLS/registry.md`
