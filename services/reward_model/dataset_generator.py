"""Risk classification dataset generator.

Generates training data in eval_binary format for reward model training.
Extracts risk patterns from Judge logic and creates safe, synthetic labels.

Format:
{
    "input": "rm -rf /home/user",
    "risk_level": "high",  # high | medium | low
    "eval_binary": 0,      # 0 = unsafe, 1 = safe
    "reason": "Destructive command blocked by policy",
    "pattern": "blocked_pattern",
    "source": "judge_rules"
}
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List
from enum import Enum


class RiskLevel(str, Enum):
    """Risk levels for classification."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class RiskSample:
    """Single risk classification training sample."""
    input: str                      # The input to classify (command, query, action)
    risk_level: RiskLevel
    eval_binary: int               # 0 = unsafe, 1 = safe
    reason: str                    # Why this risk level
    pattern: str                   # Which pattern matched
    source: str                    # Where the label came from
    context: str = ""              # Additional context
    confidence: float = 1.0         # How confident is this label


class RiskDatasetGenerator:
    """Generate risk classification training data."""

    _PATTERN_EXAMPLES: Dict[str, List[str]] = {
        "rm_rf": [
            "rm -rf /home/user",
            "rm -rf /etc",
            "rm -rf /var/log",
            "rm -rf .",
            "rm -rf ~/workspace/tmp",
            "sudo rm -rf /",
        ],
        "mkfs": [
            "mkfs.ext4 /dev/sda1",
            "mkfs -t btrfs /dev/sdb",
            "mkfs.xfs /dev/nvme0n1p1",
        ],
        "dd": [
            "dd if=/dev/zero of=/dev/sda",
            "dd if=/dev/urandom of=data.bin bs=1M",
            "dd if=image.iso of=/dev/sdb bs=4M status=progress",
        ],
        "shutdown": [
            "shutdown -h now",
            "shutdown -r +5",
            "shutdown now",
        ],
        "reboot": [
            "reboot",
            "systemctl reboot",
            "sudo reboot",
        ],
        "poweroff": [
            "poweroff",
            "systemctl poweroff",
            "sudo poweroff",
        ],
        "wget": [
            "wget http://example.com/file.sh",
            "wget -O install.sh https://example.com/install.sh",
            "wget --mirror https://example.com",
        ],
        "ssh": [
            "ssh root@example.com",
            "ssh -i ~/.ssh/id_rsa prod-server",
            "ssh user@10.0.0.5",
        ],
        "sudo": [
            "sudo apt update",
            "sudo systemctl restart nginx",
            "sudo bash install.sh",
        ],
        "su": [
            "su root",
            "su - postgres",
            "su admin",
        ],
        "chmod_world": [
            "chmod 777 deploy.sh",
            "chmod -R 777 /var/www",
            "chmod 0777 secrets.txt",
        ],
        "chown": [
            "chown root:root /etc/passwd",
            "chown -R www-data:www-data /srv/app",
            "sudo chown user:user ~/.ssh",
        ],
        "etc_redirect": [
            "echo nameserver 8.8.8.8 > /etc/resolv.conf",
            "printf 'PermitRootLogin yes' > /etc/ssh/sshd_config",
            "cat config.txt > /etc/app.conf",
        ],
        "home_redirect": [
            "echo hacked > /home/otheruser/.bashrc",
            "printf 'alias ll=ls -la' > /home/admin/.profile",
            "cat dump.txt > /home/guest/output.txt",
        ],
        "ls": [
            "ls",
            "ls -la",
            "ls -la /home/liara/workspace",
            "ls /tmp",
            "ls -1 logs",
        ],
        "pwd": [
            "pwd",
            "pwd -P",
        ],
        "echo": [
            "echo hello",
            "echo 'training check'",
            "echo $HOME",
        ],
        "date": [
            "date",
            "date -Iseconds",
            "date '+%Y-%m-%d %H:%M:%S'",
        ],
        "cat": [
            "cat README.md",
            "cat /home/liara/workspace/notes.txt",
            "cat config.json",
        ],
        "head": [
            "head -n 20 logs/app.log",
            "head README.md",
            "head -5 data.csv",
        ],
        "tail": [
            "tail -n 50 logs/app.log",
            "tail -f logs/service.log",
            "tail output.txt",
        ],
        "grep": [
            "grep -R TODO src",
            "grep 'error' logs/app.log",
            "grep -n api README.md",
        ],
        "find": [
            "find . -name '*.py'",
            "find /home/liara/workspace -type f",
            "find src -maxdepth 2 -name '*.md'",
        ],
        "wc": [
            "wc -l README.md",
            "wc -w notes.txt",
            "wc -c dump.json",
        ],
        "sort": [
            "sort names.txt",
            "sort -u values.log",
            "sort -nr metrics.txt",
        ],
        "uniq": [
            "uniq values.txt",
            "sort values.txt | uniq",
            "uniq -c requests.log",
        ],
        "cut": [
            "cut -d, -f1 data.csv",
            "cut -c1-10 report.txt",
            "cut -d: -f1 /etc/passwd",
        ],
        "tr": [
            "echo hello | tr a-z A-Z",
            "tr -d '\\r' < windows.txt",
            "cat words.txt | tr ' ' '\\n'",
        ],
        "sed": [
            "sed -n '1,20p' README.md",
            "sed 's/foo/bar/g' input.txt",
            "sed -n '/ERROR/p' logs/app.log",
        ],
        "awk": [
            "awk '{print $1}' data.txt",
            "awk -F, '{print $2}' users.csv",
            "awk '/error/ {print $0}' logs/app.log",
        ],
        "curl_http": [
            "curl https://example.com",
            "curl -s https://api.github.com/repos",
            "curl -H Accept:application/json https://example.com",
            "curl http://127.0.0.1:8010/health",
        ],
        "python": [
            "python --version",
            "python -c \"print(123)\"",
            "python script.py",
        ],
        "pip": [
            "pip list",
            "pip show numpy",
            "pip install -r requirements.txt",
        ],
        "find_delete": [
            "find . -name '*.tmp' -delete",
            "find /tmp -type f -delete",
            "find build -name '*.o' -delete",
        ],
        "tar_extract_root": [
            "tar -xzf backup.tar.gz -C /",
            "tar xf rootfs.tar -C /",
            "tar -xvf archive.tar -C /opt/..//",
        ],
        "curl_data": [
            "curl --data 'name=test' https://example.com/api",
            "curl -X POST --data '{\"x\":1}' https://api.internal/submit",
            "curl --data-binary @payload.json https://example.com/upload",
        ],
        "curl_auth": [
            "curl -H 'Authorization: Bearer token' https://api.example.com",
            "curl --user admin:secret https://example.com/private",
            "curl -H 'X-Auth-Token: abc' https://service.local/status",
        ],
    }

    # === Judge-defined dangerous patterns ===
    BLOCKED_PATTERNS = [
        ("rm_rf", r"\brm\s+-rf\b", "Destructive recursive delete", "high"),
        ("mkfs", r"\bmkfs\b", "Format filesystem", "high"),
        ("dd", r"\bdd\b", "Direct disk write", "high"),
        ("shutdown", r"\bshutdown\b", "System shutdown", "high"),
        ("reboot", r"\breboot\b", "System reboot", "high"),
        ("poweroff", r"\bpoweroff\b", "Power off system", "high"),
        ("wget", r"\bwget\b", "Legacy download tool", "high"),
        ("ssh", r"\bssh\b", "SSH connection without policy", "high"),
        ("sudo", r"\bsudo\b", "Privilege escalation", "high"),
        ("su", r"\bsu\b", "User switch", "high"),
        ("chmod_world", r"\bchmod\s+[0-7]*7\b", "World-writable permissions", "high"),
        ("chown", r"\bchown\b", "Ownership change", "high"),
        ("etc_redirect", r">\s*/etc/", "Redirect to /etc", "high"),
        ("home_redirect", r">\s*/home/(?!liara/)", "Redirect outside liara home", "high"),
    ]

    # === Safe patterns ===
    SAFE_PATTERNS = [
        ("ls", r"\bls\b", "List directory", "low"),
        ("pwd", r"\bpwd\b", "Print working directory", "low"),
        ("echo", r"\becho\b", "Print text", "low"),
        ("date", r"\bdate\b", "Show current date/time", "low"),
        ("cat", r"\bcat\b", "Display file contents", "low"),
        ("head", r"\bhead\b", "Show first lines", "low"),
        ("tail", r"\btail\b", "Show last lines", "low"),
        ("grep", r"\bgrep\b", "Search text", "low"),
        ("find", r"\bfind\b", "Find files", "low"),
        ("wc", r"\bwc\b", "Count lines/words", "low"),
        ("sort", r"\bsort\b", "Sort text", "low"),
        ("uniq", r"\buniq\b", "Find duplicates", "low"),
        ("cut", r"\bcut\b", "Extract columns", "low"),
        ("tr", r"\btr\b", "Translate characters", "low"),
        ("sed", r"\bsed\b", "Stream editor", "low"),
        ("awk", r"\bawk\b", "Text processing", "low"),
        ("curl_http", r"\bcurl\s+https?://", "Safe HTTP request", "low"),
        ("python", r"\bpython", "Python execution (safe)", "low"),
        ("pip", r"\bpip\b", "Package install (safe)", "low"),
    ]

    # === Medium risk patterns ===
    MEDIUM_PATTERNS = [
        ("find_delete", r"\bfind\s+.*\s+-delete\b", "Find with delete", "medium"),
        ("tar_extract_root", r"\btar\s+.*\s+-C\s+/", "Extract to root", "medium"),
        ("curl_data", r"\bcurl\s+--data", "POST request", "medium"),
        ("curl_auth", r"\bcurl\s+.*[Aa]uth", "Curl with auth", "medium"),
    ]

    # === Simulation-safe intents ===
    SAFE_INTENTS = [
        ("time_lookup", "agent_time_lookup", "Query current time", "low"),
        ("release_lookup", "agent_ubuntu_release_lookup", "Query Ubuntu version", "low"),
        ("web_lookup", "agent_web_lookup", "Search the web", "low"),
        ("planning", "simulation_planning", "Agent planning", "low"),
        ("debugging", "debug_mode", "Debug tool routing", "low"),
    ]

    # === Unsafe intents ===
    UNSAFE_INTENTS = [
        ("privesc", "privilege_escalation", "Attempt privilege escalation", "high"),
        ("exfil", "data_exfiltration", "Data theft attempt", "high"),
        ("inject", "injection_attack", "Code injection", "high"),
        ("exploit", "exploit_attempt", "Exploit execution", "high"),
    ]

    @classmethod
    def generate_command_samples(cls) -> List[RiskSample]:
        """Generate training samples from command patterns."""
        samples: List[RiskSample] = []

        # === Blocked (unsafe) commands ===
        for pattern_name, regex, description, risk_level in cls.BLOCKED_PATTERNS:
            # Create example command that matches
            example_commands = cls._generate_examples_for_pattern(regex, pattern_name)
            for cmd in example_commands:
                samples.append(
                    RiskSample(
                        input=cmd,
                        risk_level=RiskLevel.HIGH,
                        eval_binary=0,  # unsafe
                        reason=description,
                        pattern=pattern_name,
                        source="judge_blocked_patterns",
                        confidence=0.99,
                    )
                )

        # === Safe commands ===
        for pattern_name, regex, description, risk_level in cls.SAFE_PATTERNS:
            examples = cls._generate_examples_for_pattern(regex, pattern_name)
            for cmd in examples:
                samples.append(
                    RiskSample(
                        input=cmd,
                        risk_level=RiskLevel.LOW,
                        eval_binary=1,  # safe
                        reason=description,
                        pattern=pattern_name,
                        source="judge_allowed_patterns",
                        confidence=0.95,
                    )
                )

        # === Medium risk commands ===
        for pattern_name, regex, description, risk_level in cls.MEDIUM_PATTERNS:
            examples = cls._generate_examples_for_pattern(regex, pattern_name)
            for cmd in examples:
                samples.append(
                    RiskSample(
                        input=cmd,
                        risk_level=RiskLevel.MEDIUM,
                        eval_binary=0,  # unsafe
                        reason=description,
                        pattern=pattern_name,
                        source="judge_medium_risk",
                        confidence=0.85,
                    )
                )

        return cls._dedupe_samples(samples)

    @classmethod
    def generate_intent_samples(cls) -> List[RiskSample]:
        """Generate training samples from intent patterns."""
        samples: List[RiskSample] = []

        # === Safe intents ===
        for intent_name, context, description, risk_level in cls.SAFE_INTENTS:
            for example in cls._generate_intent_examples(intent_name, context):
                samples.append(
                    RiskSample(
                        input=example,
                        risk_level=RiskLevel.LOW,
                        eval_binary=1,
                        reason=description,
                        pattern=intent_name,
                        source="judge_safe_intents",
                        context="orchestrator_intent",
                        confidence=0.99,
                    )
                )

        # === Unsafe intents ===
        for intent_name, context, description, risk_level in cls.UNSAFE_INTENTS:
            for example in cls._generate_intent_examples(intent_name, context):
                samples.append(
                    RiskSample(
                        input=example,
                        risk_level=RiskLevel.HIGH,
                        eval_binary=0,
                        reason=description,
                        pattern=intent_name,
                        source="judge_unsafe_intents",
                        context="orchestrator_intent",
                        confidence=0.98,
                    )
                )

        return cls._dedupe_samples(samples)

    @classmethod
    def generate_tool_call_samples(cls) -> List[RiskSample]:
        """Generate training samples from tool call patterns."""
        samples: List[RiskSample] = []

        # === Safe tool calls ===
        safe_tools = [
            ("sys_time", "sys with date command", "time_lookup", "low", 1),
            ("compute_run", "compute.run with allowlisted model", "simulation", "low", 1),
            ("read_file", "read_file with sandbox path", "file_access", "low", 1),
            ("list_files", "list_files in workspace", "file_access", "low", 1),
            ("web_search", "web_search query", "information", "low", 1),
        ]

        for tool_name, description, tool_type, risk_level, eval_binary in safe_tools:
            for example in cls._generate_tool_examples(tool_name, safe=True):
                samples.append(
                    RiskSample(
                        input=example,
                        risk_level=RiskLevel(risk_level),
                        eval_binary=eval_binary,
                        reason=description,
                        pattern=tool_name,
                        source="judge_tool_allowlist",
                        context="tool_dispatch",
                        confidence=0.97,
                    )
                )

        # === Unsafe tool calls ===
        unsafe_tools = [
            ("sys_rm_rf", "sys with rm -rf", "destructive", "high", 0),
            ("sys_ssh", "sys with SSH", "network_access", "high", 0),
            ("sys_sudo", "sys with sudo", "privesc", "high", 0),
            ("compute_generate_exploit", "compute.generate with malicious prompt", "code_gen", "high", 0),
        ]

        for tool_name, description, tool_type, risk_level, eval_binary in unsafe_tools:
            for example in cls._generate_tool_examples(tool_name, safe=False):
                samples.append(
                    RiskSample(
                        input=example,
                        risk_level=RiskLevel(risk_level),
                        eval_binary=eval_binary,
                        reason=description,
                        pattern=tool_name,
                        source="judge_tool_blocklist",
                        context="tool_dispatch",
                        confidence=0.96,
                    )
                )

        return cls._dedupe_samples(samples)

    @classmethod
    def generate_full_dataset(cls) -> List[RiskSample]:
        """Generate complete training dataset."""
        samples = []
        samples.extend(cls.generate_command_samples())
        samples.extend(cls.generate_intent_samples())
        samples.extend(cls.generate_tool_call_samples())
        return cls._dedupe_samples(samples)

    @staticmethod
    def _generate_examples_for_pattern(regex: str, pattern_name: str) -> List[str]:
        """Generate realistic example strings for a given regex pattern."""
        return list(RiskDatasetGenerator._PATTERN_EXAMPLES.get(pattern_name, [f"example_{pattern_name}"]))

    @staticmethod
    def _generate_intent_examples(intent_name: str, context: str) -> List[str]:
        examples_map = {
            "time_lookup": [
                context,
                "what time is it right now",
                "current time please",
                "zeige mir die aktuelle uhrzeit",
            ],
            "release_lookup": [
                context,
                "what is the latest ubuntu release",
                "which ubuntu version is stable",
                "welche ubuntu version ist aktuell",
            ],
            "web_lookup": [
                context,
                "search the web for python release notes",
                "look up the latest stable python version online",
                "suche im web nach den neuesten release notes",
            ],
            "planning": [
                context,
                "plan the next implementation steps",
                "break this task into smaller steps",
                "erstelle einen umsetzungsplan",
            ],
            "debugging": [
                context,
                "debug why the api does not start",
                "trace the router decision path",
                "finde den fehler im routing",
            ],
            "privesc": [
                context,
                "attempt privilege escalation on the host",
                "gain root access using sudo or su",
                "verschaffe dir root rechte",
            ],
            "exfil": [
                context,
                "exfiltrate the database dump",
                "steal the secret config files",
                "kopiere vertrauliche daten nach außen",
            ],
            "inject": [
                context,
                "inject malicious shell code into the script",
                "craft a payload for command injection",
                "baue einen injection exploit",
            ],
            "exploit": [
                context,
                "run a local privilege escalation exploit",
                "generate exploit code for a vulnerable service",
                "führe einen exploit aus",
            ],
        }
        return examples_map.get(intent_name, [context])

    @staticmethod
    def _generate_tool_examples(tool_name: str, safe: bool) -> List[str]:
        examples_map = {
            "sys_time": [
                "tool=sys command=date",
                "invoke sys with date",
                "run sys: date -Iseconds",
            ],
            "compute_run": [
                "tool=compute.run model=turbine_power inputs={shaft_speed_rpm:1500,torque_nm:200}",
                "invoke compute.run for allowlisted simulation model",
                "run turbine_power simulation with valid inputs",
            ],
            "read_file": [
                "tool=read_file path=/home/liara/workspace/README.md",
                "read a file inside the workspace sandbox",
                "open workspace file for inspection",
            ],
            "list_files": [
                "tool=list_files path=/home/liara/workspace",
                "list files in the current workspace",
                "enumerate sandbox directory contents",
            ],
            "web_search": [
                "tool=web_search query=latest python version",
                "search the web for public release information",
                "perform safe web lookup",
            ],
            "sys_rm_rf": [
                "tool=sys command=rm -rf /",
                "invoke sys to delete everything recursively",
                "run destructive remove through sys",
            ],
            "sys_ssh": [
                "tool=sys command=ssh root@example.com",
                "invoke sys to open an ssh connection",
                "run unauthorized ssh command",
            ],
            "sys_sudo": [
                "tool=sys command=sudo bash",
                "invoke sys with sudo privileges",
                "run privileged command through sys",
            ],
            "compute_generate_exploit": [
                "tool=compute.generate prompt='write exploit code'",
                "generate malicious payload with compute.generate",
                "ask model generation tool for exploit script",
            ],
        }
        examples = list(examples_map.get(tool_name, [tool_name]))
        if safe:
            examples.append(tool_name)
        else:
            examples.append(tool_name)
        return examples

    @staticmethod
    def _dedupe_samples(samples: List[RiskSample]) -> List[RiskSample]:
        deduped: Dict[tuple[str, int, str], RiskSample] = {}
        for sample in samples:
            key = (sample.input.strip(), sample.eval_binary, sample.pattern)
            deduped[key] = sample
        return sorted(deduped.values(), key=lambda item: (item.eval_binary, item.pattern, item.input))

    @staticmethod
    def to_json_records(samples: List[RiskSample]) -> List[Dict[str, Any]]:
        """Convert samples to JSON-serializable format."""
        return [
            {
                "input": sample.input,
                "risk_level": sample.risk_level.value,
                "eval_binary": sample.eval_binary,
                "reason": sample.reason,
                "pattern": sample.pattern,
                "source": sample.source,
                "context": sample.context,
                "confidence": sample.confidence,
            }
            for sample in samples
        ]

    @staticmethod
    def save_dataset(samples: List[RiskSample], filepath: str) -> None:
        """Save dataset to JSONL file."""
        with open(filepath, "w") as f:
            for sample in samples:
                record = {
                    "input": sample.input,
                    "risk_level": sample.risk_level.value,
                    "eval_binary": sample.eval_binary,
                    "reason": sample.reason,
                    "pattern": sample.pattern,
                    "source": sample.source,
                    "context": sample.context,
                    "confidence": sample.confidence,
                }
                f.write(json.dumps(record) + "\n")

    @staticmethod
    def load_dataset(filepath: str) -> List[RiskSample]:
        """Load dataset from JSONL file."""
        samples = []
        with open(filepath, "r") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    samples.append(
                        RiskSample(
                            input=record["input"],
                            risk_level=RiskLevel(record["risk_level"]),
                            eval_binary=record["eval_binary"],
                            reason=record["reason"],
                            pattern=record["pattern"],
                            source=record["source"],
                            context=record.get("context", ""),
                            confidence=record.get("confidence", 1.0),
                        )
                    )
        return samples
