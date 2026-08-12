#!/usr/bin/env python3
"""Initialize date/time commands in policy DB."""
import sys
sys.path.insert(0, '.')

from services.tools.builtin.sys_command_policy import _DATE_TIME_POLICY_DEFAULTS
from services.tools.builtin.policy_db import load_command_policy

# Load/create policy for date and time commands
for cmd in ("date", "time"):
    policy = load_command_policy(cmd, defaults=_DATE_TIME_POLICY_DEFAULTS)
    print(f"✓ Initialized {cmd}: whitelist={len(policy.whitelist)}, greylist={len(policy.greylist)}, blacklist={len(policy.blacklist)}")

# Verify they're now available
from services.tools.builtin.policy_db import list_policy_commands
commands = list_policy_commands()
print(f"\n✓ Available commands: {sorted(commands)}")
print(f"✓ date available: {'date' in commands}")
print(f"✓ time available: {'time' in commands}")
