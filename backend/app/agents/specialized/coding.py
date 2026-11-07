"""Coding Specialist Agent for Workflow 3 (Bug reproduction and PR creation)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.agents.base import BaseSpecialistAgent
from app.agents.schemas import (
    AgentExecutionStatus,
    SpecialistAgentResult,
    SpecialistAgentRole,
)
from app.mcp.client_manager import MCPClientManager
from app.mcp.connectors.github import GitHubConnector
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.sandbox.runner import SandboxCommandRequest, SandboxRunner
from app.tasks.queue import SubtaskJob

logger = logging.getLogger(__name__)


class CodingAgent(BaseSpecialistAgent):
    """Specialist agent implementing Workflow 3 with isolated sandbox verification and gated PRs."""

    role = SpecialistAgentRole.CODING
    model_alias = "heavy"
    permission_ceiling = ToolTier.APPROVAL_REQUIRED
    assigned_tools = frozenset(
        {
            "file_read",
            "file_write_artifact",
            "git_status",
            "git_diff",
            "git_commit_local",
            "github_create_pull_request",
            "run_sandbox_command",
        }
    )

    def __init__(
        self,
        sandbox_runner: SandboxRunner | None = None,
        github_connector: GitHubConnector | None = None,
        permission_registry: ToolPermissionRegistry | None = None,
        client_manager: MCPClientManager | None = None,
    ) -> None:
        super().__init__(permission_registry=permission_registry, client_manager=client_manager)
        self.sandbox_runner = sandbox_runner
        self.github_connector = github_connector

    async def execute_subtask(
        self,
        job: SubtaskJob,
        context: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """Execute Workflow 3: Reproduce in sandbox, patch, verify to exit code 0, open PR."""
        ctx = context or {}
        repo_dir = Path(ctx.get("repo_dir", "."))
        test_command = ctx.get("test_command", ["python", "-m", "pytest"])
        patch_file = ctx.get("patch_file")
        patch_content = ctx.get("patch_content")
        has_approval = bool(ctx.get("has_valid_approval_token", False))

        if not self.sandbox_runner:
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.FAILED,
                summary="SandboxRunner is required for CodingAgent execution.",
                verification_proof={"error": "missing_sandbox_runner"},
            )

        # 1. Reproduce bug in sandbox (initial test)
        init_run = self.sandbox_runner.execute_command(
            SandboxCommandRequest(command=test_command, workspace_dir=repo_dir)
        )
        initial_passed = init_run.exit_code == 0

        # 2. Apply patch if provided (strictly enforcing repository containment)
        if patch_file and patch_content:
            resolved_repo = await asyncio.to_thread(repo_dir.resolve)
            target_path = await asyncio.to_thread((repo_dir / patch_file).resolve)
            try:
                if not target_path.is_relative_to(resolved_repo):
                    return SpecialistAgentResult(
                        agent_role=self.role,
                        status=AgentExecutionStatus.FAILED,
                        summary=f"Security violation: Patch '{patch_file}' escapes repo root.",
                        verification_proof={"error": "path_traversal", "patch_file": patch_file},
                    )
            except ValueError:
                return SpecialistAgentResult(
                    agent_role=self.role,
                    status=AgentExecutionStatus.FAILED,
                    summary=f"Security violation: Patch '{patch_file}' escapes repository root.",
                    verification_proof={"error": "path_traversal", "patch_file": patch_file},
                )

            await asyncio.to_thread(target_path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target_path.write_text, patch_content, encoding="utf-8")

        # 3. Re-run test in sandbox to verify resolution (Principle 2: No success without proof)
        retest_run = self.sandbox_runner.execute_command(
            SandboxCommandRequest(command=test_command, workspace_dir=repo_dir)
        )
        retest_passed = retest_run.exit_code == 0

        if not retest_passed:
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.FAILED,
                summary=f"Bug fix failed sandbox verification (exit code {retest_run.exit_code}).",
                verification_proof={
                    "initial_test_passed": initial_passed,
                    "retest_passed": False,
                    "retest_exit_code": retest_run.exit_code,
                    "stdout": retest_run.stdout,
                    "stderr": retest_run.stderr,
                },
            )

        # 4. Check approval for opening Pull Request
        if not has_approval:
            return SpecialistAgentResult(
                agent_role=self.role,
                status=AgentExecutionStatus.PARTIAL,
                summary="Bug fix verified in sandbox. Retest passed. Awaiting approval to open PR.",
                verification_proof={
                    "initial_test_passed": initial_passed,
                    "retest_passed": retest_passed,
                    "retest_exit_code": retest_run.exit_code,
                },
                requires_human_approval=True,
                approval_context={
                    "repo": ctx.get("repo", "maya-ai/maya"),
                    "title": ctx.get("pr_title", "Fix identified bug"),
                    "head": ctx.get("pr_head", "fix/bug"),
                    "base": ctx.get("pr_base", "main"),
                    "body": (
                        f"Automated fix verified in sandbox with exit code {retest_run.exit_code}."
                    ),
                },
            )

        # 5. Execute gated PR creation with approval token
        pr_url = ""
        if self.github_connector:
            pr_data = await self.github_connector.create_pull_request(
                repo=ctx.get("repo", "maya-ai/maya"),
                title=ctx.get("pr_title", "Fix identified bug"),
                head=ctx.get("pr_head", "fix/bug"),
                base=ctx.get("pr_base", "main"),
                body=f"Automated fix verified in sandbox with exit code {retest_run.exit_code}.",
                has_valid_approval_token=True,
            )
            pr_url = pr_data.get("html_url", "")

        return SpecialistAgentResult(
            agent_role=self.role,
            status=AgentExecutionStatus.SUCCESS,
            summary=f"Bug fix successfully verified and submitted to PR: {pr_url}",
            verification_proof={
                "initial_test_passed": initial_passed,
                "retest_passed": retest_passed,
                "retest_exit_code": retest_run.exit_code,
                "pr_url": pr_url,
            },
        )
