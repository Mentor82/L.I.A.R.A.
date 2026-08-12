# Liara Inference Layout

This folder is the runtime-oriented project layout.

## Structure

- helper/
  - LiaraHelper.exe (build output)
  - plugins/
    - instruct.dll (deployment artifact)
    - coder.dll (deployment artifact)
- scheduler/
  - LiaraHelperScheduler.exe (build output)
- common/
  - protocol.hpp
  - heartbeat.hpp
  - task_contract.hpp
  - helper_contract.hpp
  - crc8.hpp

## Build Targets

- LiaraHelper
- LiaraHelperScheduler
- HelperContractTests

The build outputs are configured into helper/ and scheduler/ directories.

## Current Runtime Contract

- Helper keeps both models warm in memory: Instruct + Coder.
- Routing rule: quick_extract -> Instruct, code_* -> Coder.
- Warm-state metrics are exposed on startup logs:
  - warm_age_ms
  - reload_count
