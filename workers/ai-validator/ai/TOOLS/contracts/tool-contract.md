# tool-contract

This document defines the minimum contract for any tool.

## Required fields
- name
- version
- description
- inputs (schema reference)
- outputs (schema reference)
- error codes
- permissions
- examples

## Security and compliance
- Identify sensitive inputs and outputs.
- Document authentication and authorization expectations.
- Note any data retention or logging behavior.

## Dependencies and limits
- List external dependencies and services.
- Document rate limits and timeouts.

## Change rules
- Backward compatible changes preferred.
- Breaking changes require a new major version and updated docs.
