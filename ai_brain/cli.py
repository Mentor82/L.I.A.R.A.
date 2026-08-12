"""
AI-Brain Command-Line Interface.
Supports edge confirmation (`USER_CONFIRMED` provenance upgrade) and export ingestion.
"""

import sys
import argparse
from ai_brain.builder import BrainBuilder
from ai_brain.subgraph_engine import BoundedSubgraphEngine
from ai_brain.schema import EpistemicState, EdgeProvenance, BrainEdge


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-Brain CLI Tool")
    subparsers = parser.add_subparsers(dest="command")

    # Ingest Command
    ingest_p = subparsers.add_parser("ingest", help="Ingest ChatGPT Export Directory")
    ingest_p.add_argument("--export-dir", required=True, help="Path to export directory")
    ingest_p.add_argument("--entity-id", default="nephy", help="Entity ID (default: nephy)")
    ingest_p.add_argument("--limit", type=int, default=100, help="Thread limit")

    # Confirm Edge Command
    confirm_p = subparsers.add_parser("confirm-edge", help="Confirm an edge as USER_CONFIRMED")
    confirm_p.add_argument("--subject", required=True, help="Subject Node ID")
    confirm_p.add_argument("--predicate", required=True, help="Relation Predicate")
    confirm_p.add_argument("--object", required=True, help="Object Node ID")

    args = parser.parse_args()
    engine = BoundedSubgraphEngine()

    if args.command == "ingest":
        builder = BrainBuilder(engine=engine)
        results = builder.build_from_export(args.export_dir, entity_id=args.entity_id, limit=args.limit)
        print(f"[AI-Brain] Ingestion completed: {results}")

    elif args.command == "confirm-edge":
        edge_id = f"edge_confirmed_{args.subject}_{args.predicate}_{args.object}"
        edge = BrainEdge(
            id=edge_id,
            subject_id=args.subject,
            predicate=args.predicate,
            object_id=args.object,
            epistemic_state=EpistemicState.USER_CONFIRMED,
            provenance=EdgeProvenance(
                source_type="user_confirmed",
                confidence=1.0,
                verified=True,
                scope="projects:read",
            ),
        )
        engine.graph_store.upsert_edge(edge)
        print(f"[AI-Brain] Edge '{edge_id}' successfully upgraded to USER_CONFIRMED (confidence=1.0)")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
