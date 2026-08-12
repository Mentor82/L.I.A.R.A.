from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.reward_model.dataset_generator import RiskDatasetGenerator
from services.reward_model.reward_model import RewardModelTrainer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train the LIARA reward model.")
    parser.add_argument(
        "--output-dir",
        default="artifacts/reward_model",
        help="Directory where model artifacts will be written.",
    )
    parser.add_argument(
        "--dataset-input",
        default="",
        help="Optional JSONL dataset path. If omitted, generate from built-in judge patterns.",
    )
    parser.add_argument(
        "--model-name",
        default="risk_classifier_v1",
        help="Model name stored inside the artifact.",
    )
    parser.add_argument(
        "--test-split",
        type=float,
        default=0.2,
        help="Fraction of samples reserved for test evaluation.",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="Random seed used for training reproducibility.",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print JSON summary to stdout after training.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not 0.0 < args.test_split < 1.0:
        parser.error("--test-split must be between 0 and 1.")

    if args.dataset_input:
        model, metrics, samples = RewardModelTrainer.train_from_dataset_file(
            args.dataset_input,
            model_name=args.model_name,
            test_split=args.test_split,
            random_state=args.random_state,
        )
        dataset_source = str(Path(args.dataset_input).resolve())
    else:
        samples = RiskDatasetGenerator.generate_full_dataset()
        model, metrics = RewardModelTrainer.train_from_samples(
            samples,
            model_name=args.model_name,
            test_split=args.test_split,
            random_state=args.random_state,
        )
        dataset_source = "generated"

    artifact_paths = RewardModelTrainer.persist_training_artifacts(
        model=model,
        samples=samples,
        output_dir=args.output_dir,
        extra_metadata={
            "dataset_source": dataset_source,
            "random_state": args.random_state,
            "test_split": args.test_split,
        },
    )

    summary = {
        "model_name": args.model_name,
        "sample_count": len(samples),
        "dataset_source": dataset_source,
        "metrics": metrics,
        **artifact_paths,
    }

    print(f"Reward model trained: {artifact_paths['model_path']}")
    print(f"Samples: {len(samples)} | Test accuracy: {metrics.get('test_accuracy', 0.0):.3f}")
    if args.print_summary:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())