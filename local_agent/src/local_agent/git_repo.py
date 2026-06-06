from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_git_command(*, repo_path: str | None, args: list[str], timeout_seconds: int = 120) -> GitCommandResult:
    command = ["git", *args]
    completed = subprocess.run(
        command,
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return GitCommandResult(completed.returncode, completed.stdout, completed.stderr)


def ensure_repo(*, repo_url: str, repo_path: str, branch: str, command_runner=run_git_command) -> None:
    path = Path(repo_path)
    if not path.exists() or not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        result = command_runner(repo_path=None, args=["clone", "--branch", branch, repo_url, repo_path])
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr or result.stdout}")


def refresh_repo(*, repo_url: str, repo_path: str, branch: str, command_runner=run_git_command) -> None:
    ensure_repo(repo_url=repo_url, repo_path=repo_path, branch=branch, command_runner=command_runner)
    fetch_result = command_runner(repo_path=repo_path, args=["fetch", "origin", branch])
    if fetch_result.returncode != 0:
        raise RuntimeError(f"git fetch failed: {fetch_result.stderr or fetch_result.stdout}")
    reset_result = command_runner(repo_path=repo_path, args=["reset", "--hard", f"origin/{branch}"])
    if reset_result.returncode != 0:
        raise RuntimeError(f"git reset failed: {reset_result.stderr or reset_result.stdout}")
    clean_result = command_runner(repo_path=repo_path, args=["clean", "-fd"])
    if clean_result.returncode != 0:
        raise RuntimeError(f"git clean failed: {clean_result.stderr or clean_result.stdout}")


def read_config_text(*, repo_path: str, config_file_path: str) -> str:
    return (Path(repo_path) / config_file_path).read_text(encoding="utf-8")


def write_config_text(*, repo_path: str, config_file_path: str, content: str) -> None:
    path = Path(repo_path) / config_file_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def current_revision(*, repo_path: str, command_runner=run_git_command) -> str:
    result = command_runner(repo_path=repo_path, args=["rev-parse", "HEAD"])
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {result.stderr or result.stdout}")
    return result.stdout.strip()


def commit_and_push(
    *,
    repo_path: str,
    config_file_path: str,
    branch: str,
    commit_message: str,
    command_runner=run_git_command,
) -> tuple[bool, str]:
    add_result = command_runner(repo_path=repo_path, args=["add", config_file_path])
    if add_result.returncode != 0:
        return False, add_result.stderr or add_result.stdout

    commit_result = command_runner(repo_path=repo_path, args=["commit", "-m", commit_message])
    if (
        commit_result.returncode != 0
        and "nothing to commit" not in (commit_result.stderr + commit_result.stdout).lower()
    ):
        return False, commit_result.stderr or commit_result.stdout

    push_result = command_runner(repo_path=repo_path, args=["push", "origin", branch])
    if push_result.returncode != 0:
        return False, push_result.stderr or push_result.stdout
    return True, push_result.stdout.strip() or "push succeeded"
