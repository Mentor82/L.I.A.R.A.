#!/usr/bin/env python3
from services.orchestrator.sys_selector import needs_sys, select_sys_command

queries = [
    'Was ist die Zeit?',
    'Nenne mir die Uhrzeit',
    'aktuelle Zeit',
]
for q in queries:
    needs = needs_sys(q)
    if needs:
        sel = select_sys_command(q)
        print(f'{q}: {sel.command} {sel.args[0] if sel.args else "(no args)"}')
