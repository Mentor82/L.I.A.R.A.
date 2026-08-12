# Tool Contract: code_quality_check

## Metadata
- **Tool Name:** code_quality_check
- **Version:** 1.0.0
- **Owner:** testing-infrastructure
- **Status:** active

## Purpose
Static code analysis for PEP 8 compliance, cyclomatic complexity, docstring coverage, and naming conventions.

## Interface

### Inputs
```
Optional:
  file_or_directory: string
    - Path to file or directory to analyze
    - Default: /Users/mirkowaldhauer/lab-web
    - If directory: analyzes all .py files recursively
    - If file: analyzes single file
```

### Outputs
```
Success:
  {
    "syntax_valid": boolean,
    "average_score": number (0-100),
    "grade": string ("A" | "B" | "C" | "D"),
    "files_analyzed": number,
    "metrics": {
      "syntax": percentage,
      "line_length": percentage,
      "docstrings": percentage,
      "complexity": percentage,
      "naming": percentage
    },
    "issues": [
      {
        "file": string,
        "type": string,
        "severity": "error" | "warning" | "info",
        "line": number,
        "message": string
      }
    ],
    "recommendations": [string]
  }

Error:
  {
    "error": string,
    "file": string,
    "reason": string
  }
```

## Execution

### Command
```bash
# Analyze workspace
python code_quality.py

# Analyze specific file
python code_quality.py /path/to/file.py

# Analyze directory
python code_quality.py /path/to/directory
```

## Constraints

### Permissions Required
- `read`: Access to Python source files

### Dependencies
- Python 3.12+
- `ast` module (standard library)

### Time Complexity
- Single file: ~100ms
- Directory: ~1 second per file
- Full workspace: ~5 seconds

## Grading Scale

| Score | Grade | Assessment |
|-------|-------|------------|
| 90-100 | A | Excellent - Production ready |
| 80-89 | B | Good - Minor improvements |
| 70-79 | C | Acceptable - Needs attention |
| 0-69 | D | Poor - Significant issues |

## Analysis Metrics

### Syntax (100%)
- Valid Python that parses without errors

### Line Length (0-100%)
- Lines <= 88 characters (PEP 8)
- 1% penalty per line exceeding limit

### Docstrings (0-100%)
- Functions and classes should have docstrings
- Missing docstring = -5 points per item

### Complexity (0-100%)
- Cyclomatic complexity <= 5 is ideal
- Higher complexity = lower score

### Naming (0-100%)
- Classes: CapCase
- Functions: snake_case
- Constants: UPPER_SNAKE_CASE

## Related Documents
- `/Users/mirkowaldhauer/lab-web/code_quality.py`
- `/Users/mirkowaldhauer/lab-web/TESTING_GUIDE.md`
- `ai/TOOLS/registry.md`
