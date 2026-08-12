# Reference

Quick lookup for operators and developers.

## Current Commands

```text
.\build.ps1
.\build\heartbeat_demo.exe
.\build\HelperContractTests.exe
.\build\openvino_probe.exe <model.xml> --device=npu --infer-smoke --smoke-seq-len=128
```

## Helper Runtime Contract (Current)

```text
Profiles:
- Instruct
- Coder

Required state:
- both ready
- both warm in memory

Routing:
- quick_extract -> Instruct
- code_* -> Coder

Metrics:
- warm_age_ms
- reload_count
```

## Goal

Keep this section concise and copy-paste friendly.

## Code Structure Conventions

Primary source:

- `../../src/README.md` (see `src/README.md` in repository root)

Team summary:

- use descriptive snake_case file names
- keep `.cpp` files small (target <= 200 lines)
- split by responsibility (CLI, runner, serialize, validation, helpers)
- prefer explicit entrypoints like `*_main.cpp`
- list every source file explicitly in `CMakeLists.txt`
