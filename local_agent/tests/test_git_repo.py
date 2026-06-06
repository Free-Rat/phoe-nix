import unittest

from local_agent.git_repo import GitCommandResult, commit_and_push, ensure_repo, refresh_repo


class GitRepoTests(unittest.TestCase):
    def test_ensure_repo_clones_when_missing(self) -> None:
        calls = []

        def runner(*, repo_path, args, timeout_seconds=120):
            del timeout_seconds
            calls.append((repo_path, args))
            return GitCommandResult(0, "ok", "")

        ensure_repo(
            repo_url="https://example/repo.git", repo_path="/tmp/does-not-exist", branch="main", command_runner=runner
        )
        self.assertEqual(calls[0][1][:2], ["clone", "--branch"])

    def test_refresh_repo_runs_fetch_reset_and_clean(self) -> None:
        calls = []

        def runner(*, repo_path, args, timeout_seconds=120):
            del timeout_seconds
            calls.append((repo_path, args))
            return GitCommandResult(0, "ok", "")

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tempdir:
            Path(tempdir, ".git").mkdir()
            refresh_repo(repo_url="https://example/repo.git", repo_path=tempdir, branch="main", command_runner=runner)
        self.assertEqual(calls[0][1][:2], ["fetch", "origin"])
        self.assertEqual(calls[1][1][:2], ["reset", "--hard"])
        self.assertEqual(calls[2][1], ["clean", "-fd"])

    def test_commit_and_push_returns_failure_on_push_error(self) -> None:
        responses = iter(
            [
                GitCommandResult(0, "", ""),
                GitCommandResult(0, "[main abc] msg", ""),
                GitCommandResult(1, "", "push rejected"),
            ]
        )

        def runner(*, repo_path, args, timeout_seconds=120):
            del repo_path, args, timeout_seconds
            return next(responses)

        success, message = commit_and_push(
            repo_path="/tmp/repo",
            config_file_path="configuration.nix",
            branch="main",
            commit_message="test",
            command_runner=runner,
        )
        self.assertFalse(success)
        self.assertIn("push rejected", message)
