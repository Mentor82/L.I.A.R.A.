"""
ChatGPT Export Parser for AI-Brain.
Parses `conversations-*.json` export files, extracting threads, turns, ground-truth facts, and relation tuples.
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Tuple


class ChatGPTExportParser:
    """Parses ChatGPT Export JSON archives for entity memory ingestion."""

    def __init__(self, export_dir: str) -> None:
        self.export_dir = Path(export_dir)

    def find_conversation_files(self) -> List[Path]:
        """Locate conversations-*.json files in export directory."""
        files = sorted(list(self.export_dir.glob("conversations-*.json")))
        if not files and (self.export_dir / "conversations.json").exists():
            files = [self.export_dir / "conversations.json"]
        return files

    def parse_threads(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Parse conversation threads and turns from export files."""
        files = self.find_conversation_files()
        threads = []

        count = 0
        for fpath in files:
            if count >= limit:
                break
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        if count >= limit:
                            break
                        thread_id = item.get("id") or item.get("conversation_id", f"thread_{count}")
                        title = item.get("title", "Untitled Conversation")
                        create_time = item.get("create_time") or 0.0

                        mapping = item.get("mapping", {})
                        turns = []
                        for msg_id, msg_data in mapping.items():
                            message = msg_data.get("message")
                            if not message:
                                continue
                            author = message.get("author", {}).get("role", "unknown")
                            content_parts = message.get("content", {}).get("parts", [])
                            text = "".join([str(p) for p in content_parts if isinstance(p, str)])
                            if text.strip():
                                turns.append({
                                    "turn_id": msg_id,
                                    "role": author,
                                    "content": text.strip(),
                                    "create_time": message.get("create_time") or create_time,
                                })

                        if turns:
                            threads.append({
                                "thread_id": thread_id,
                                "title": title,
                                "create_time": create_time,
                                "turns": turns,
                            })
                            count += 1
            except Exception as e:
                print(f"[Warning] Failed parsing export file {fpath}: {e}")

        return threads
