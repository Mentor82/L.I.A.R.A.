"""Quick smoke-test for sys command policy checkers."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1]))

from services.tools.builtin.sys_command_policy import check_command_request as chk

CASES = [
    # python3 — allowed
    (True,  "python3", ["-c", "print(7*13)"]),
    (True,  "python3", ["-c", "import math; print(math.sqrt(9))"]),
    (True,  "python3", ["-u", "-c", "print(1)"]),
    # python3 — blocked
    (False, "python3", ["-c", "import os; os.system('id')"]),
    (False, "python3", ["-c", "open('/etc/passwd')"]),
    (False, "python3", ["-c", "exec('x=1')"]),
    (False, "python3", ["-c", "eval('1+1')"]),
    (False, "python3", ["-c", "import subprocess"]),
    (False, "python3", ["-i"]),
    (False, "python3", ["script.py"]),
    # find — allowed
    (True,  "find", ["/home/liara/workspace", "-maxdepth", "2", "-type", "f"]),
    (True,  "find", ["/tmp", "-name", "*.txt"]),
    # find — blocked
    (False, "find", ["/etc", "-type", "f"]),
    (False, "find", ["/root"]),
    (False, "find", ["/home/liara/workspace", "-exec", "rm", "{}", "+"]),
    (False, "find", ["/home/liara/workspace", "-delete"]),
    (False, "find", ["/proc", "-type", "f"]),
    # cat — allowed
    (True,  "cat", ["/home/liara/workspace/notes.txt"]),
    (True,  "cat", ["-n", "/home/liara/workspace/data.csv"]),
    (True,  "cat", ["/tmp/output.txt"]),
    # cat — blocked
    (False, "cat", ["/etc/passwd"]),
    (False, "cat", ["/home/liara/.ssh/id_rsa"]),
    (False, "cat", ["/home/liara/.gnupg/secring.gpg"]),
    (False, "cat", ["/mnt/c/Windows/System32/config/SAM"]),
    (False, "cat", ["/var/log/syslog"]),
    # ls - allowed / blocked
    (True,  "ls", ["-la", "/home/liara/workspace"]),
    (True,  "ls", ["/tmp"]),
    (False, "ls", ["/etc"]),
    # grep - allowed / blocked
    (True,  "grep", ["-n", "todo", "/home/liara/workspace/notes.txt"]),
    (False, "grep", ["todo"]),
    (False, "grep", ["todo", "/etc/passwd"]),
    # head - allowed / blocked
    (True,  "head", ["-n", "20", "/home/liara/workspace/notes.txt"]),
    (False, "head", ["/etc/passwd"]),
    # tail - allowed / blocked
    (True,  "tail", ["-n", "5", "/tmp/output.txt"]),
    (False, "tail", ["-f", "/home/liara/workspace/notes.txt"]),
]

passed = 0
failed = 0
for expected_allow, cmd, args in CASES:
    r = chk(cmd, args)
    ok = r.allowed == expected_allow
    status = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    verdict = "OK  " if r.allowed else "BLK "
    print(f"[{status}] {verdict} {cmd} {args[:2]}  error={r.error or ''}")

print(f"\n{passed}/{passed+failed} passed")
sys.exit(0 if failed == 0 else 1)
