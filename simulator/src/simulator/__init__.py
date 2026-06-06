import sys
from pathlib import Path


def _add_repo_sources_to_path() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_directories = [
        repo_root / "analysis_agent" / "src",
        repo_root / "decision_agent" / "src",
        repo_root / "local_agent" / "src",
        repo_root / "log_router" / "src",
        repo_root / "log_service" / "src",
        repo_root / "schemas" / "src",
        repo_root / "token_service" / "src",
    ]
    for source_directory in source_directories:
        path_value = str(source_directory)
        if path_value not in sys.path:
            sys.path.insert(0, path_value)


_add_repo_sources_to_path()

__all__ = []
