"""
GitHub tool — wraps the mcp__github__ MCP server into a ToolBase.

Actions (read):
  get_file_contents, get_pr_status, get_check_runs, get_job_logs, list_branches

Actions (write — require confirmation unless autonomous_mode=True):
  create_branch, create_or_update_file, create_pr, add_comment,
  merge_pr, delete_branch
"""
import json
import os
from typing import Any

from tools.registry import ToolBase, ToolSafety


class GitHubTool(ToolBase):
    name = "github"
    description = (
        "Interact with GitHub: create branches, create/update files, open PRs, "
        "check CI status, read job logs, merge PRs, delete branches, post comments."
    )
    input_schema = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "get_file_contents",
                    "get_pr_status",
                    "get_check_runs",
                    "get_job_logs",
                    "list_branches",
                    "create_branch",
                    "create_or_update_file",
                    "create_pr",
                    "add_comment",
                    "merge_pr",
                    "delete_branch",
                ],
            },
            "repo": {"type": "string", "description": "owner/repo"},
            "branch": {"type": "string"},
            "base_branch": {"type": "string", "description": "Base branch for new branch or PR"},
            "path": {"type": "string", "description": "File path within repo"},
            "content": {"type": "string", "description": "File content (plain text)"},
            "message": {"type": "string", "description": "Commit message"},
            "sha": {"type": "string", "description": "Existing file SHA for updates"},
            "pr_number": {"type": "integer"},
            "pr_title": {"type": "string"},
            "pr_body": {"type": "string"},
            "merge_method": {"type": "string", "enum": ["merge", "squash", "rebase"]},
            "comment": {"type": "string"},
            "run_id": {"type": "integer", "description": "GitHub Actions run ID"},
            "job_id": {"type": "integer", "description": "GitHub Actions job ID"},
        },
    }

    action_policies = {
        "get_file_contents": ToolSafety(action_type="read", risk_level="low"),
        "get_pr_status":     ToolSafety(action_type="read", risk_level="low"),
        "get_check_runs":    ToolSafety(action_type="read", risk_level="low"),
        "get_job_logs":      ToolSafety(action_type="read", risk_level="low"),
        "list_branches":     ToolSafety(action_type="read", risk_level="low"),
        "create_branch":     ToolSafety(action_type="write", risk_level="low"),
        "create_or_update_file": ToolSafety(action_type="write", risk_level="low"),
        "create_pr":         ToolSafety(action_type="write", risk_level="low"),
        "add_comment":       ToolSafety(action_type="write", risk_level="low"),
        "merge_pr":          ToolSafety(action_type="write", risk_level="medium",
                                        requires_confirmation=False,
                                        reason="Merges PR into the base branch"),
        "delete_branch":     ToolSafety(action_type="write", risk_level="medium",
                                        requires_confirmation=False,
                                        reason="Permanently deletes the remote branch"),
    }

    def __init__(self, autonomous_mode: bool = False):
        # autonomous_mode=True skips confirmation gates during orchestration
        self._autonomous = autonomous_mode

    def safety_for(self, kwargs: dict) -> ToolSafety:
        policy = super().safety_for(kwargs)
        if self._autonomous:
            return ToolSafety(
                action_type=policy.action_type,
                risk_level=policy.risk_level,
                requires_confirmation=False,
                reason=policy.reason,
            )
        return policy

    async def run(self, action: str, **kwargs) -> Any:
        handler = {
            "get_file_contents":     self._get_file_contents,
            "get_pr_status":         self._get_pr_status,
            "get_check_runs":        self._get_check_runs,
            "get_job_logs":          self._get_job_logs,
            "list_branches":         self._list_branches,
            "create_branch":         self._create_branch,
            "create_or_update_file": self._create_or_update_file,
            "create_pr":             self._create_pr,
            "add_comment":           self._add_comment,
            "merge_pr":              self._merge_pr,
            "delete_branch":         self._delete_branch,
        }.get(action)

        if not handler:
            raise ValueError(f"Unknown action: {action}")
        return await handler(**kwargs)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _parse_repo(self, repo: str) -> tuple[str, str]:
        owner, _, name = repo.partition("/")
        if not name:
            raise ValueError(f"repo must be 'owner/name', got: {repo!r}")
        return owner, name

    async def _call_mcp(self, tool_name: str, **params) -> Any:
        """
        Calls an mcp__github__ tool by name.

        In production Jarvis sessions the MCP tools are injected into the
        process as async callables accessible via the tool registry MCP bridge.
        We import them lazily so the module loads fine in unit tests.
        """
        try:
            from mcp_bridge import call_mcp_tool  # provided by the Claude Code runtime
            return await call_mcp_tool(f"mcp__github__{tool_name}", params)
        except ImportError:
            # Fallback: use the GitHub REST API directly via httpx
            return await self._rest_fallback(tool_name, params)

    async def _rest_fallback(self, tool_name: str, params: dict) -> Any:
        """Direct GitHub API calls when MCP bridge is unavailable."""
        import base64
        try:
            import httpx
        except ImportError:
            raise RuntimeError(
                "httpx not installed and mcp_bridge not available. "
                "Run: pip install httpx"
            )

        token = os.environ.get("GITHUB_TOKEN", "")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        base = "https://api.github.com"

        async with httpx.AsyncClient(headers=headers, timeout=30) as client:
            owner = params.get("owner", "")
            repo = params.get("repo", "")

            if tool_name == "get_file_contents":
                path = params.get("path", "")
                branch = params.get("branch", "")
                url = f"{base}/repos/{owner}/{repo}/contents/{path}"
                r = await client.get(url, params={"ref": branch} if branch else {})
                r.raise_for_status()
                data = r.json()
                if isinstance(data, dict) and data.get("content"):
                    return base64.b64decode(data["content"]).decode()
                return json.dumps(data)

            elif tool_name == "list_branches":
                r = await client.get(f"{base}/repos/{owner}/{repo}/branches")
                r.raise_for_status()
                return json.dumps([b["name"] for b in r.json()])

            elif tool_name == "create_branch":
                # Need SHA of base branch
                base_b = params.get("from_branch", "main")
                r = await client.get(f"{base}/repos/{owner}/{repo}/git/ref/heads/{base_b}")
                r.raise_for_status()
                sha = r.json()["object"]["sha"]
                r2 = await client.post(
                    f"{base}/repos/{owner}/{repo}/git/refs",
                    json={"ref": f"refs/heads/{params['branch']}", "sha": sha},
                )
                r2.raise_for_status()
                return json.dumps(r2.json())

            elif tool_name == "create_or_update_file":
                path = params.get("path", "")
                content_b64 = base64.b64encode(
                    params.get("content", "").encode()
                ).decode()
                body: dict = {
                    "message": params.get("message", "update file"),
                    "content": content_b64,
                    "branch": params.get("branch", "main"),
                }
                if params.get("sha"):
                    body["sha"] = params["sha"]
                r = await client.put(
                    f"{base}/repos/{owner}/{repo}/contents/{path}", json=body
                )
                r.raise_for_status()
                return json.dumps(r.json())

            elif tool_name == "create_pull_request":
                r = await client.post(
                    f"{base}/repos/{owner}/{repo}/pulls",
                    json={
                        "title": params.get("title", ""),
                        "body": params.get("body", ""),
                        "head": params.get("head", ""),
                        "base": params.get("base", "main"),
                    },
                )
                r.raise_for_status()
                return json.dumps(r.json())

            elif tool_name == "pull_request_read":
                pr_num = params.get("pullNumber", params.get("pr_number"))
                r = await client.get(f"{base}/repos/{owner}/{repo}/pulls/{pr_num}")
                r.raise_for_status()
                return json.dumps(r.json())

            elif tool_name == "actions_list":
                branch = params.get("branch", "")
                url = f"{base}/repos/{owner}/{repo}/actions/runs"
                query: dict = {}
                if branch:
                    query["branch"] = branch
                r = await client.get(url, params=query)
                r.raise_for_status()
                return json.dumps(r.json())

            elif tool_name == "get_check_run":
                run_id = params.get("runId", params.get("run_id"))
                r = await client.get(f"{base}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs")
                r.raise_for_status()
                return json.dumps(r.json())

            elif tool_name == "get_job_logs":
                job_id = params.get("jobId", params.get("job_id"))
                r = await client.get(
                    f"{base}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
                    follow_redirects=True,
                )
                return r.text[:8000]

            elif tool_name == "merge_pull_request":
                pr_num = params.get("pullNumber", params.get("pr_number"))
                r = await client.put(
                    f"{base}/repos/{owner}/{repo}/pulls/{pr_num}/merge",
                    json={"merge_method": params.get("mergeMethod", "squash")},
                )
                r.raise_for_status()
                return json.dumps(r.json())

            elif tool_name == "add_issue_comment":
                issue_num = params.get("issueNumber", params.get("pr_number"))
                r = await client.post(
                    f"{base}/repos/{owner}/{repo}/issues/{issue_num}/comments",
                    json={"body": params.get("body", "")},
                )
                r.raise_for_status()
                return json.dumps(r.json())

            elif tool_name == "delete_branch_ref":
                branch = params.get("branch", "")
                r = await client.delete(
                    f"{base}/repos/{owner}/{repo}/git/refs/heads/{branch}"
                )
                if r.status_code == 422:
                    return json.dumps({"deleted": False, "reason": "branch not found"})
                r.raise_for_status()
                return json.dumps({"deleted": True, "branch": branch})

            else:
                raise NotImplementedError(f"No REST fallback for mcp tool: {tool_name}")

    # ── Action implementations ────────────────────────────────────────────────

    async def _get_file_contents(self, repo: str, path: str, branch: str = "") -> str:
        owner, name = self._parse_repo(repo)
        result = await self._call_mcp(
            "get_file_contents", owner=owner, repo=name, path=path, branch=branch
        )
        return str(result)

    async def _list_branches(self, repo: str) -> str:
        owner, name = self._parse_repo(repo)
        result = await self._call_mcp("list_branches", owner=owner, repo=name)
        return str(result)

    async def _create_branch(self, repo: str, branch: str, base_branch: str = "main") -> str:
        owner, name = self._parse_repo(repo)
        result = await self._call_mcp(
            "create_branch",
            owner=owner,
            repo=name,
            branch=branch,
            from_branch=base_branch,
        )
        return str(result)

    async def _create_or_update_file(
        self,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        sha: str = "",
    ) -> str:
        owner, name = self._parse_repo(repo)
        params: dict = {
            "owner": owner,
            "repo": name,
            "path": path,
            "content": content,
            "message": message,
            "branch": branch,
        }
        if sha:
            params["sha"] = sha
        result = await self._call_mcp("create_or_update_file", **params)
        return str(result)

    async def _get_file_sha(self, repo: str, path: str, branch: str) -> str:
        """Return the current SHA of a file, or '' if it doesn't exist."""
        try:
            import base64
            import httpx
            owner, name = self._parse_repo(repo)
            token = os.environ.get("GITHUB_TOKEN", "")
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"https://api.github.com/repos/{owner}/{name}/contents/{path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                    params={"ref": branch},
                )
                if r.status_code == 404:
                    return ""
                r.raise_for_status()
                return r.json().get("sha", "")
        except Exception:
            return ""

    async def _create_pr(
        self,
        repo: str,
        pr_title: str,
        pr_body: str,
        branch: str,
        base_branch: str = "main",
    ) -> dict:
        owner, name = self._parse_repo(repo)
        result = await self._call_mcp(
            "create_pull_request",
            owner=owner,
            repo=name,
            title=pr_title,
            body=pr_body,
            head=branch,
            base=base_branch,
        )
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                pass
        return result if isinstance(result, dict) else {"raw": str(result)}

    async def _get_pr_status(self, repo: str, pr_number: int) -> dict:
        owner, name = self._parse_repo(repo)
        pr_data = await self._call_mcp(
            "pull_request_read", owner=owner, repo=name, pullNumber=pr_number
        )
        if isinstance(pr_data, str):
            try:
                pr_data = json.loads(pr_data)
            except json.JSONDecodeError:
                pr_data = {}

        # Also fetch latest CI runs for the PR head SHA
        runs_data = await self._call_mcp(
            "actions_list",
            owner=owner,
            repo=name,
            branch=pr_data.get("head", {}).get("ref", ""),
        )
        if isinstance(runs_data, str):
            try:
                runs_data = json.loads(runs_data)
            except json.JSONDecodeError:
                runs_data = {}

        runs = runs_data.get("workflow_runs", []) if isinstance(runs_data, dict) else []
        latest_run = runs[0] if runs else {}

        return {
            "pr": pr_data,
            "latest_run": latest_run,
            "ci_status": latest_run.get("conclusion") or latest_run.get("status", "unknown"),
            "mergeable": pr_data.get("mergeable"),
            "state": pr_data.get("state", "unknown"),
        }

    async def _get_check_runs(self, repo: str, run_id: int) -> dict:
        owner, name = self._parse_repo(repo)
        result = await self._call_mcp(
            "get_check_run", owner=owner, repo=name, runId=run_id
        )
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                pass
        return result if isinstance(result, dict) else {"raw": str(result)}

    async def _get_job_logs(self, repo: str, job_id: int) -> str:
        owner, name = self._parse_repo(repo)
        result = await self._call_mcp(
            "get_job_logs", owner=owner, repo=name, jobId=job_id
        )
        return str(result)[:8000]

    async def _add_comment(self, repo: str, pr_number: int, comment: str) -> str:
        owner, name = self._parse_repo(repo)
        result = await self._call_mcp(
            "add_issue_comment",
            owner=owner,
            repo=name,
            issueNumber=pr_number,
            body=comment,
        )
        return str(result)

    async def _merge_pr(
        self, repo: str, pr_number: int, merge_method: str = "squash"
    ) -> dict:
        owner, name = self._parse_repo(repo)
        result = await self._call_mcp(
            "merge_pull_request",
            owner=owner,
            repo=name,
            pullNumber=pr_number,
            mergeMethod=merge_method,
        )
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                pass
        return result if isinstance(result, dict) else {"raw": str(result)}

    async def _delete_branch(self, repo: str, branch: str) -> dict:
        owner, name = self._parse_repo(repo)
        # GitHub MCP server may not have a delete-branch action; use REST fallback
        result = await self._rest_fallback(
            "delete_branch_ref", owner=owner, repo=name, branch=branch
        )
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                pass
        return result if isinstance(result, dict) else {"raw": str(result)}
