# io-schemas

This file documents input/output schema conventions for tools.

## Conventions
- Use JSON schema for structured inputs and outputs.
- Keep schemas minimal and explicit.
- Document required fields and defaults.
- Include examples for non-trivial structures.

## Example (template)
```
{
  "type": "object",
  "properties": {
    "example_field": { "type": "string" }
  },
  "required": ["example_field"]
}
```
