"""Config package exports."""

# Load environment variables IMMEDIATELY when this module is imported
# This must happen BEFORE Settings class is evaluated
try:
	import os
	from pathlib import Path
	from dotenv import load_dotenv

	candidates = []
	project_root = os.getenv("LIARA_PROJECT_ROOT")
	if project_root:
		candidates.append(Path(project_root) / ".env")
	candidates.append(Path(__file__).parent.parent.parent / ".env")
	candidates.append(Path(__file__).parent.parent.parent.parent / ".env")

	loaded = set()
	for env_path in candidates:
		resolved = env_path.resolve()
		if resolved in loaded:
			continue
		loaded.add(resolved)
		if resolved.exists():
			load_dotenv(str(resolved), override=True)
			break
except ImportError:
	pass  # python-dotenv not installed, continue

from .settings import Settings

__all__ = ["Settings"]
