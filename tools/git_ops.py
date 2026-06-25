"""
Local git and shell operations tool.

Runs git commands and the project test suite in a subprocess so the
orchestrator can pull changes and validate them locally after a merge.
"""
import asyncio
import os
import shlex
from pathlib import Path
from typing import Optional

from tools.registry import ToolBase, ToolSafety


class GitOpsTool(ToolBase):
    name = "git_ops"
    description = (
        "Local git operations: clone a repo, pull latest changes, run the "
        "project test suite, and get the current diff. Used after merging a PR "
        "to validate that the main branch is green locally."
    )
    input_schema = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["clone", "pull", "run_tests", "get_diff", "checkout"],
            },
            "repo_url": {
                "type": "string",
                "description": "HTTPS clone URL, e.g. https://github.com/owner/repo.git",
            },
            "work_dir": {
                "type": "string",
                "description": "Local directory to run the command in",
            },
            "branch": {"type": "string", "description": "Branch to checkout / pull"},
            "test_command": {
                "type": "string",
                "description": "Shell command to run the test suite, e.g. 'pytest -x'",
            },
        },
    }

    action_policies = {
        "clone":      ToolSafety(action_type="write", risk_level="low"),
        "checkout":   ToolSafety(action_type="write", risk_level="low"),
        "pull":       ToolSafety(action_type="write", risk_level="low"),
        "get_diff":   ToolSafety(action_type="read",  risk_level="low"),
        "run_tests":  ToolSafety(action_type="write", risk_level="low"),
    }

    async def run(self, action: str, **kwargs) -> str:
        handler = {
            "clone":     self._clone,
            "checkout":  self._checkout,
            "pull":      self._pull,
            "get_diff":  self._get_diff,
            "run_tests": self._run_tests,
        }.get(action)
        if not handler:
            raise ValueError(f"Unknown action: {action}")
        return await handler(**kwargs)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _exec(
        self,
        cmd: str,
        cwd: Optional[str] = None,
        timeout: int = 300,
    ) -> tuple[int, str, str]:
        """Run a shell command, return (returncode, stdout, stderr)."""
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return -1, "", f"Command timed out after {timeout}s: {cmd}"

        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    # ── Actions ───────────────────────────────────────────────────────────────

    async def _clone(self, repo_url: str, work_dir: str, branch: str = "") -> str:
        Path(work_dir).parent.mkdir(parents=True, exist_ok=True)
        branch_flag = f"--branch {shlex.quote(branch)}" if branch else ""
        cmd = f"git clone --depth 1 {branch_flag} {shlex.quote(repo_url)} {shlex.quote(work_dir)}"
        rc, out, err = await self._exec(cmd, timeout=120)
        if rc != 0:
            raise RuntimeError(f"git clone failed (rc={rc}): {err.strip()}")
        return f"Cloned {repo_url} into {work_dir}"

    async def _checkout(self, work_dir: str, branch: str) -> str:
        rc, out, err = await self._exec(
            f"git fetch origin {shlex.quote(branch)} && git checkout {shlex.quote(branch)}",
            cwd=work_dir,
        )
        if rc != 0:
            raise RuntimeError(f"git checkout failed (rc={rc}): {err.strip()}")
        return f"Checked out {branch}"

    async def _pull(self, work_dir: str, branch: str = "main") -> str:
        cmd = f"git pull origin {shlex.quote(branch)}"
        rc, out, err = await self._exec(cmd, cwd=work_dir, timeout=60)
        if rc != 0:
            raise RuntimeError(f"git pull failed (rc={rc}): {err.strip()}")
        return out.strip() or f"Pulled {branch}"

    async def _get_diff(self, work_dir: str, branch: str = "") -> str:
        if branch:
            cmd = f"git diff main..{shlex.quote(branch)}"
        else:
            cmd = "git diff HEAD~1 HEAD"
        rc, out, err = await self._exec(cmd, cwd=work_dir)
        if rc != 0:
            return err.strip()
        return out[:6000] or "(no diff)"

    async def _run_tests(self, work_dir: str, test_command: str, timeout: int = 300) -> dict:
        rc, out, err = await self._exec(test_command, cwd=work_dir, timeout=timeout)
        combined = (out + "\n" + err).strip()
        return {
            "passed": rc == 0,
            "returncode": rc,
            "output": combined[:4000],
        }
