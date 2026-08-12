import hashlib
import re


def relation_node_key(prefix: str, text: str) -> str:
    """Return a stable, normalized graph-node key for text."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    slug = re.sub(r"[^a-z0-9 ]", "", normalized)[:48].strip().replace(" ", "_")
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}:{slug}:{digest}"
