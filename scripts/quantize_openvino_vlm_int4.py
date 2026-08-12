"""Compress a split OpenVINO VLM export to NPU-compatible INT4 weights."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import nncf
import openvino as ov


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Existing OpenVINO VLM model directory")
    parser.add_argument("output", type=Path, help="New INT4 model directory")
    return parser.parse_args()


def _copy_support_files(source: Path, output: Path) -> None:
    for item in source.iterdir():
        if item.suffix.lower() in {".xml", ".bin"}:
            continue
        destination = output / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)

    for stem in ("openvino_tokenizer", "openvino_detokenizer"):
        for suffix in (".xml", ".bin"):
            source_file = source / f"{stem}{suffix}"
            if source_file.exists():
                shutil.copy2(source_file, output / source_file.name)


def quantize(source: Path, output: Path) -> None:
    source = source.resolve()
    output = output.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Source model directory not found: {source}")
    if source == output:
        raise ValueError("Output must differ from source")

    component_xmls = sorted(source.glob("openvino_*_model.xml"))
    if not component_xmls:
        raise FileNotFoundError(f"No OpenVINO component graphs found in: {source}")

    output.mkdir(parents=True, exist_ok=True)
    _copy_support_files(source, output)

    components: list[str] = []
    core = ov.Core()
    for xml_path in component_xmls:
        print(f"Compressing {xml_path.name} with INT4_SYM, group_size=-1", flush=True)
        model = core.read_model(xml_path)
        compressed_model = nncf.compress_weights(
            model,
            mode=nncf.CompressWeightsMode.INT4_SYM,
            ratio=1.0,
            group_size=-1,
        )
        ov.save_model(compressed_model, output / xml_path.name)
        components.append(xml_path.stem)

    metadata = {
        "source": str(source),
        "weight_format": "int4_sym",
        "group_size": -1,
        "ratio": 1.0,
        "openvino_version": ov.__version__,
        "nncf_version": nncf.__version__,
        "components": components,
    }
    (output / "int4_quantization.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="ascii",
    )
    print(f"INT4 model written to {output}", flush=True)


if __name__ == "__main__":
    args = _parse_args()
    quantize(args.source, args.output)