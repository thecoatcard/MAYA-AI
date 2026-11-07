"""Local Git MCP Connector with permission ceilings (Phase 08)."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.mcp.permissions import PermissionDeniedError

logger = logging.getLogger(__name__)


class LocalGitConnector:
    """Connector for local Git repository interactions within sandboxed directories."""

    def __init__(self, repo_path: Path | str) -> None:
        self.repo_path = Path(repo_path).resolve()
        if not self.repo_path.exists():
            raise FileNotFoundError(f"Git repository path '{self.repo_path}' does not exist.")

    def _run_git(self, args: list[str]) -> str:
        res = subprocess.run(
            ["git", *args],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
        return res.stdout.strip()

    def status(self) -> str:
        """Get repository working tree status."""
        return self._run_git(["status"])

    def diff(self, staged: bool = False) -> str:
        """Get repository diff."""
        args = ["diff", "--cached"] if staged else ["diff"]
        return self._run_git(args)

    def log(self, max_count: int = 10) -> str:
        """Get commit history."""
        return self._run_git(["log", f"-n{max_count}", "--oneline"])

    def branch(self) -> str:
        """Get current branch name."""
        return self._run_git(["branch", "--show-current"])

    def commit_local(self, message: str, files: list[str] | None = None) -> str:
        """Stage files and create a local commit (WRITE tier)."""
        if files:
            for f in files:
                self._run_git(["add", f])
        else:
            self._run_git(["add", "-A"])
        return self._run_git(["commit", "-m", message])

    def push(
        self,
        remote: str = "origin",
        branch: str = "main",
        has_valid_approval_token: bool = False,
    ) -> str:
        """Push local commits to remote repository (APPROVAL_REQUIRED tier)."""
        if not has_valid_approval_token:
            raise PermissionDeniedError(
                "Execution of APPROVAL_REQUIRED tool 'git_push' "
                "forbidden without valid approval token",
                tool_name="git_push",
            )
        # Disallow flag injection and refspec force-push (Decision D4 / RESTRICTED invariant)
        for arg in (remote, branch):
            if arg.startswith("-") or arg.startswith("+") or "--force" in arg or "-f" in arg:
                raise PermissionDeniedError(
                    "Force-push and flag injection are strictly RESTRICTED: " + arg,
                    tool_name="git_push",
                )
        return self._run_git(["push", remote, branch])
