"""Apply Neo4j schema statements from a .cypher file.

Default target file: scripts/neo4j_schema_liara.cypher
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from typing import List


def _parse_statements(cypher_text: str) -> List[str]:
    statements: List[str] = []
    buffer: List[str] = []

    for raw_line in cypher_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        buffer.append(raw_line)

    joined = "\n".join(buffer)
    for chunk in joined.split(";"):
        statement = chunk.strip()
        if statement:
            statements.append(statement)
    return statements


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Neo4j schema file")
    parser.add_argument(
        "--file",
        default="scripts/neo4j_schema_liara.cypher",
        help="Path to .cypher file (default: scripts/neo4j_schema_liara.cypher)",
    )
    parser.add_argument("--url", default=os.getenv("NEO4J_URL", ""), help="Neo4j bolt URL")
    parser.add_argument("--user", default=os.getenv("NEO4J_USER", "neo4j"), help="Neo4j username")
    parser.add_argument("--password", default=os.getenv("NEO4J_PASSWORD", ""), help="Neo4j password")
    parser.add_argument("--database", default=os.getenv("NEO4J_DATABASE", "neo4j"), help="Neo4j database")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on first statement error",
    )
    args = parser.parse_args()

    if not args.url:
        print("ERROR: NEO4J_URL is not set and --url was not provided.")
        return 2

    cypher_path = pathlib.Path(args.file)
    if not cypher_path.exists():
        print(f"ERROR: schema file not found: {cypher_path}")
        return 2

    try:
        from neo4j import GraphDatabase  # type: ignore
    except Exception as exc:
        print(f"ERROR: neo4j driver import failed: {exc}")
        return 2

    statements = _parse_statements(cypher_path.read_text(encoding="utf-8"))
    if not statements:
        print("No schema statements found.")
        return 0

    applied = 0
    failed = 0

    driver = GraphDatabase.driver(args.url, auth=(args.user, args.password))
    try:
        with driver.session(database=args.database) as session:
            for idx, statement in enumerate(statements, start=1):
                try:
                    session.run(statement).consume()
                    applied += 1
                    print(f"[{idx}/{len(statements)}] OK")
                except Exception as exc:
                    failed += 1
                    print(f"[{idx}/{len(statements)}] ERROR: {exc}")
                    if args.strict:
                        return 1
    finally:
        driver.close()

    print(f"DONE: applied={applied}, failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
