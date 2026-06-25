"""
PR Orchestrator — Jarvis's autonomous PR lifecycle manager.

Reads a YAML task list, then for each task drives the full workflow:

  PENDING
    → BRANCH_CREATED
    → CODE_WRITTEN
    → PR_OPENED
    → CI_MONITORING  (polls every N seconds)
    → CI_FAILED_RETRY_{1,2,3}   (auto-fix via code_assistant + push)
    → CI_FAILED_NEEDS_HUMAN      (after max retries)
    → MERGED
    → BRANCH_DELETED
    → LOCAL_PULLED
    → LOCAL_TESTS_RUNNING
    → LOCAL_TEST_FAILED_RETRY_{1,2,3}
    → LOCAL_TEST_FAILED_NEEDS_HUMAN
    → COMPLETE
    → SKIPPED

State for each task is persisted to the pr_tasks SQLite table so runs
survive restarts. The orchestrator picks up from the last persisted state.

Usage:
    orchestrator = PROrchestrator(memory, modes, context, registry, config)
    async for update in orchestrator.run("pr_tasks.yaml"):
        print(update, end="", flush=True)
"""
import asyncio
import json
import os
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional

import anthropic
import yaml


# ── Task data model ───────────────────────────────────────────────────────────

@dataclass
class FileSpec:
    path: str
    prompt: str          # natural-language instruction for code gen
    content: str = ""    # filled in during CODE_WRITTEN phase


@dataclass
class PRTask:
    id: str
    repo: str
    branch: str
    pr_title: str
    description: str
    files_to_create: list[FileSpec] = field(default_factory=list)
    files_to_modify: list[FileSpec] = field(default_factory=list)
    pr_body: str = ""
    base_branch: str = "main"
    merge_strategy: str = "squash"

    # runtime state
    state: str = "PENDING"
    pr_number: Optional[int] = None
    pr_url: str = ""
    fix_attempt: int = 0
    local_fix_attempt: int = 0
    error_log: str = ""
    work_dir: str = ""


# ── Orchestrator ──────────────────────────────────────────────────────────────

class PROrchestrator:
    MODEL = "claude-sonnet-4-6"

    def __init__(self, memory, mode_manager, context_aggregator, tool_registry, config: dict):
        self._memory = memory
        self._modes = mode_manager
        self._context = context_aggregator
        self._tools = tool_registry
        self._config = config
        self._client = anthropic.AsyncAnthropic()

        orch_cfg = config.get("pr_orchestration", {})
        self._ci_poll_seconds   = int(orch_cfg.get("ci_poll_interval_seconds", 60))
        self._ci_timeout_min    = int(orch_cfg.get("ci_timeout_minutes", 30))
        self._max_fix_retries   = int(orch_cfg.get("max_fix_retries", 3))
        self._default_merge     = orch_cfg.get("default_merge_strategy", "squash")
        self._task_file         = orch_cfg.get("task_file", "./pr_tasks.yaml")

        self._github = None
        self._git_ops = None
        self._code_assist = None

    # ── Entry point ───────────────────────────────────────────────────────────

    async def run(self, task_file: Optional[str] = None) -> AsyncIterator[str]:
        task_file = task_file or self._task_file
        yield f"PR Orchestrator starting — loading tasks from {task_file}\n"

        try:
            tasks, global_cfg = self._load_tasks(task_file)
        except Exception as e:
            yield f"Failed to load task file: {e}\n"
            return

        self._github   = self._tools.get("github")
        self._git_ops  = self._tools.get("git_ops")
        self._code_assist = self._tools.get("code_assistant")

        if not self._github:
            yield "Error: 'github' tool not registered. Check tools/registry.py.\n"
            return

        yield f"Loaded {len(tasks)} task(s).\n\n"

        for task in tasks:
            # Restore persisted state
            self._restore_state(task)

            if task.state == "COMPLETE":
                yield f"[{task.id}] Already complete — skipping.\n"
                continue
            if task.state in ("CI_FAILED_NEEDS_HUMAN", "LOCAL_TEST_FAILED_NEEDS_HUMAN"):
                yield f"[{task.id}] Waiting for human input — skipping for now.\n"
                continue

            yield f"\n{'─'*60}\n"
            yield f"Task: {task.id} — {task.pr_title}\n"
            yield f"Repo: {task.repo}  Branch: {task.branch}\n"
            yield f"{'─'*60}\n"

            async for chunk in self._run_task(task, global_cfg):
                yield chunk

        yield "\nAll tasks processed.\n"

    # ── Task lifecycle ────────────────────────────────────────────────────────

    async def _run_task(self, task: PRTask, global_cfg: dict) -> AsyncIterator[str]:
        test_command = global_cfg.get("test_command", "")

        try:
            if task.state == "PENDING":
                async for c in self._step_create_branch(task):
                    yield c

            if task.state == "BRANCH_CREATED":
                async for c in self._step_write_code(task):
                    yield c

            if task.state == "CODE_WRITTEN":
                async for c in self._step_open_pr(task):
                    yield c

            if task.state == "PR_OPENED":
                async for c in self._step_monitor_ci(task):
                    yield c

            if task.state == "MERGED":
                async for c in self._step_delete_branch(task):
                    yield c

            if task.state == "BRANCH_DELETED":
                if self._git_ops and test_command:
                    async for c in self._step_local_pull(task, global_cfg):
                        yield c
                else:
                    task.state = "COMPLETE"
                    self._persist_state(task)

            if task.state == "LOCAL_PULLED":
                async for c in self._step_local_tests(task, test_command):
                    yield c

            if task.state == "COMPLETE":
                yield f"  ✓ Task {task.id} complete.\n"
                self._memory.add_episode(
                    "assistant",
                    f"PR orchestrator completed task {task.id}: {task.pr_title} in {task.repo}",
                )

        except Exception as e:
            task.state = "SKIPPED"
            task.error_log = str(e)
            self._persist_state(task)
            yield f"  ✗ Task {task.id} failed unexpectedly: {e}\n"

    # ── Steps ─────────────────────────────────────────────────────────────────

    async def _step_create_branch(self, task: PRTask) -> AsyncIterator[str]:
        yield f"  → Creating branch {task.branch} from {task.base_branch}…\n"
        await self._github.run(
            action="create_branch",
            repo=task.repo,
            branch=task.branch,
            base_branch=task.base_branch,
        )
        task.state = "BRANCH_CREATED"
        self._persist_state(task)
        yield f"  ✓ Branch created.\n"

    async def _step_write_code(self, task: PRTask) -> AsyncIterator[str]:
        yield f"  → Generating code for {len(task.files_to_create)} new + {len(task.files_to_modify)} modified file(s)…\n"

        for spec in task.files_to_create:
            yield f"    Generating {spec.path}…\n"
            spec.content = await self._generate_code(task, spec, is_new=True)
            await self._github.run(
                action="create_or_update_file",
                repo=task.repo,
                path=spec.path,
                content=spec.content,
                message=f"feat({task.id}): create {spec.path}",
                branch=task.branch,
            )
            yield f"    ✓ Created {spec.path}\n"

        for spec in task.files_to_modify:
            yield f"    Modifying {spec.path}…\n"
            # Fetch current content + SHA
            try:
                existing = await self._github.run(
                    action="get_file_contents",
                    repo=task.repo,
                    path=spec.path,
                    branch=task.base_branch,
                )
            except Exception:
                existing = ""

            sha = await self._github._get_file_sha(task.repo, spec.path, task.branch)
            spec.content = await self._generate_code(task, spec, is_new=False, existing=existing)
            await self._github.run(
                action="create_or_update_file",
                repo=task.repo,
                path=spec.path,
                content=spec.content,
                message=f"feat({task.id}): update {spec.path}",
                branch=task.branch,
                sha=sha,
            )
            yield f"    ✓ Updated {spec.path}\n"

        task.state = "CODE_WRITTEN"
        self._persist_state(task)
        yield f"  ✓ Code written.\n"

    async def _step_open_pr(self, task: PRTask) -> AsyncIterator[str]:
        yield f"  → Opening PR…\n"

        body = task.pr_body
        if not body and self._code_assist:
            body = await self._code_assist.run(
                action="pr_description",
                diff=task.description,
                context=f"Task: {task.pr_title}\n{task.description}",
            )

        result = await self._github.run(
            action="create_pr",
            repo=task.repo,
            pr_title=task.pr_title,
            pr_body=body or task.description,
            branch=task.branch,
            base_branch=task.base_branch,
        )

        if isinstance(result, dict):
            task.pr_number = result.get("number")
            task.pr_url = result.get("html_url", "")
        task.state = "PR_OPENED"
        self._persist_state(task)
        yield f"  ✓ PR #{task.pr_number} opened: {task.pr_url}\n"

    async def _step_monitor_ci(self, task: PRTask) -> AsyncIterator[str]:
        yield f"  → Monitoring CI for PR #{task.pr_number}…\n"
        deadline = asyncio.get_event_loop().time() + self._ci_timeout_min * 60
        task.fix_attempt = getattr(task, "fix_attempt", 0)

        while True:
            if asyncio.get_event_loop().time() > deadline:
                yield "  ✗ CI timed out.\n"
                task.error_log = "CI timed out"
                task.state = "CI_FAILED_NEEDS_HUMAN"
                self._persist_state(task)
                await self._notify_human(task, "CI timed out")
                return

            status = await self._github.run(
                action="get_pr_status",
                repo=task.repo,
                pr_number=task.pr_number,
            )
            ci = status.get("ci_status", "unknown") if isinstance(status, dict) else "unknown"
            yield f"    CI status: {ci}\n"

            if ci in ("success", "completed"):
                task.state = "MERGED"
                # Actually merge
                async for c in self._step_merge(task):
                    yield c
                return

            elif ci in ("failure", "cancelled", "timed_out", "action_required"):
                task.fix_attempt += 1
                if task.fix_attempt > self._max_fix_retries:
                    yield f"  ✗ CI still failing after {self._max_fix_retries} fix attempts.\n"
                    task.state = "CI_FAILED_NEEDS_HUMAN"
                    self._persist_state(task)
                    await self._notify_human(task, task.error_log)
                    return

                yield f"  ⟳ CI failed — auto-fix attempt {task.fix_attempt}/{self._max_fix_retries}…\n"
                error_log = await self._fetch_ci_logs(task, status)
                task.error_log = error_log
                self._persist_state(task)

                async for c in self._ci_fix(task, error_log):
                    yield c

                # reset deadline after each fix push
                deadline = asyncio.get_event_loop().time() + self._ci_timeout_min * 60

            else:
                # still pending / in_progress — keep polling
                yield f"    Waiting {self._ci_poll_seconds}s…\n"
                await asyncio.sleep(self._ci_poll_seconds)

    async def _step_merge(self, task: PRTask) -> AsyncIterator[str]:
        yield f"  → Merging PR #{task.pr_number}…\n"
        await self._github.run(
            action="merge_pr",
            repo=task.repo,
            pr_number=task.pr_number,
            merge_method=task.merge_strategy or self._default_merge,
        )
        task.state = "MERGED"
        self._persist_state(task)
        yield f"  ✓ PR #{task.pr_number} merged.\n"

    async def _step_delete_branch(self, task: PRTask) -> AsyncIterator[str]:
        yield f"  → Deleting branch {task.branch}…\n"
        try:
            await self._github.run(
                action="delete_branch",
                repo=task.repo,
                branch=task.branch,
            )
            yield f"  ✓ Branch deleted.\n"
        except Exception as e:
            yield f"  ! Branch delete failed (non-fatal): {e}\n"
        task.state = "BRANCH_DELETED"
        self._persist_state(task)

    async def _step_local_pull(self, task: PRTask, global_cfg: dict) -> AsyncIterator[str]:
        yield f"  → Pulling {task.base_branch} locally…\n"
        repo_url = global_cfg.get("repo_url", "")
        if not repo_url:
            owner_name = task.repo
            repo_url = f"https://github.com/{owner_name}.git"

        work_dir = task.work_dir
        if not work_dir:
            work_dir = str(Path(tempfile.gettempdir()) / "jarvis_pr" / task.id)
            task.work_dir = work_dir
            self._persist_state(task)

        if not Path(work_dir).exists():
            yield f"    Cloning into {work_dir}…\n"
            await self._git_ops.run(
                action="clone",
                repo_url=repo_url,
                work_dir=work_dir,
                branch=task.base_branch,
            )
        else:
            await self._git_ops.run(
                action="pull",
                work_dir=work_dir,
                branch=task.base_branch,
            )

        task.state = "LOCAL_PULLED"
        self._persist_state(task)
        yield f"  ✓ Local repo updated.\n"

    async def _step_local_tests(self, task: PRTask, test_command: str) -> AsyncIterator[str]:
        yield f"  → Running local tests: {test_command}\n"
        task.local_fix_attempt = getattr(task, "local_fix_attempt", 0)

        while True:
            result = await self._git_ops.run(
                action="run_tests",
                work_dir=task.work_dir,
                test_command=test_command,
            )
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    result = {"passed": False, "output": result, "returncode": -1}

            if result.get("passed"):
                task.state = "COMPLETE"
                self._persist_state(task)
                yield f"  ✓ Local tests passed.\n"
                return

            task.local_fix_attempt += 1
            output = result.get("output", "")
            yield f"  ✗ Local tests failed (attempt {task.local_fix_attempt}).\n"
            yield f"    {output[:300]}\n"

            if task.local_fix_attempt > self._max_fix_retries:
                task.state = "LOCAL_TEST_FAILED_NEEDS_HUMAN"
                task.error_log = output
                self._persist_state(task)
                await self._notify_human(task, output)
                yield f"  ✗ Local tests still failing after {self._max_fix_retries} attempts — waiting for human.\n"
                return

            yield f"  ⟳ Attempting local fix {task.local_fix_attempt}/{self._max_fix_retries}…\n"
            async for c in self._local_fix(task, output):
                yield c

    # ── Fix loops ─────────────────────────────────────────────────────────────

    async def _ci_fix(self, task: PRTask, error_log: str) -> AsyncIterator[str]:
        """Diagnose CI failure with Claude, push a fix commit."""
        if not self._code_assist:
            yield "    No code_assistant tool available — cannot auto-fix.\n"
            return

        yield "    Diagnosing CI failure with Claude…\n"
        diagnosis = await self._code_assist.run(
            action="review",
            code=error_log[:3000],
            context=(
                f"This is a CI failure log for PR '{task.pr_title}' in {task.repo}. "
                f"The branch is '{task.branch}'. Identify the root cause and "
                f"provide a specific fix. Be concise and actionable."
            ),
        )
        yield f"    Diagnosis: {diagnosis[:400]}\n"

        # Generate a fix for each affected file
        fix_applied = False
        for spec in list(task.files_to_create) + list(task.files_to_modify):
            if not spec.content:
                continue
            fixed = await self._generate_fix(task, spec, error_log, diagnosis)
            if fixed and fixed != spec.content:
                sha = await self._github._get_file_sha(task.repo, spec.path, task.branch)
                await self._github.run(
                    action="create_or_update_file",
                    repo=task.repo,
                    path=spec.path,
                    content=fixed,
                    message=f"fix({task.id}): CI fix attempt {task.fix_attempt}",
                    branch=task.branch,
                    sha=sha,
                )
                spec.content = fixed
                fix_applied = True
                yield f"    ✓ Pushed fix to {spec.path}\n"

        if not fix_applied:
            yield "    Could not determine which file to fix — CI may re-run on existing commit.\n"

        self._persist_state(task)
        yield f"    Waiting {self._ci_poll_seconds}s for CI to re-trigger…\n"
        await asyncio.sleep(self._ci_poll_seconds)

    async def _local_fix(self, task: PRTask, error_log: str) -> AsyncIterator[str]:
        """Generate and push a fix for local test failures, then re-pull."""
        if not self._code_assist:
            yield "    No code_assistant tool available — cannot auto-fix.\n"
            return

        yield "    Diagnosing local test failure with Claude…\n"
        diagnosis = await self._code_assist.run(
            action="review",
            code=error_log[:3000],
            context=(
                f"These are local test failures after merging PR '{task.pr_title}' "
                f"in {task.repo} (branch: {task.base_branch}). "
                f"Identify the root cause and provide a specific fix."
            ),
        )
        yield f"    Diagnosis: {diagnosis[:400]}\n"

        fix_applied = False
        for spec in list(task.files_to_create) + list(task.files_to_modify):
            if not spec.content:
                continue
            fixed = await self._generate_fix(task, spec, error_log, diagnosis)
            if fixed and fixed != spec.content:
                # Push fix to a new branch derived from base
                fix_branch = f"fix/{task.id}-local-{task.local_fix_attempt}"
                try:
                    await self._github.run(
                        action="create_branch",
                        repo=task.repo,
                        branch=fix_branch,
                        base_branch=task.base_branch,
                    )
                except Exception:
                    pass

                await self._github.run(
                    action="create_or_update_file",
                    repo=task.repo,
                    path=spec.path,
                    content=fixed,
                    message=f"fix({task.id}): local test fix attempt {task.local_fix_attempt}",
                    branch=fix_branch,
                )
                # Open and auto-merge a fix PR
                fix_pr = await self._github.run(
                    action="create_pr",
                    repo=task.repo,
                    pr_title=f"fix: local test failure after {task.id}",
                    pr_body=f"Auto-fix for local test failure.\n\n```\n{error_log[:500]}\n```",
                    branch=fix_branch,
                    base_branch=task.base_branch,
                )
                spec.content = fixed
                fix_applied = True
                yield f"    ✓ Pushed local fix PR for {spec.path}\n"

        if not fix_applied:
            yield "    Could not auto-generate a local fix.\n"

        # Re-pull after fix
        if self._git_ops and task.work_dir:
            await self._git_ops.run(
                action="pull",
                work_dir=task.work_dir,
                branch=task.base_branch,
            )

    # ── Code generation ───────────────────────────────────────────────────────

    async def _generate_code(
        self,
        task: PRTask,
        spec: FileSpec,
        is_new: bool,
        existing: str = "",
    ) -> str:
        action_verb = "Create" if is_new else "Modify"
        context = textwrap.dedent(f"""
            Task: {task.pr_title}
            Description: {task.description}
            Repo: {task.repo}
            File: {spec.path}
            Instruction: {spec.prompt}
        """).strip()

        if not is_new and existing:
            context += f"\n\nExisting file content:\n```\n{existing[:3000]}\n```"

        resp = await self._client.messages.create(
            model=self.MODEL,
            max_tokens=2000,
            system=(
                f"{action_verb} production-quality code. "
                "Output ONLY the complete file content with no markdown fences, "
                "no explanations, no preamble. Just the raw file."
            ),
            messages=[{"role": "user", "content": context}],
        )
        return resp.content[0].text.strip()

    async def _generate_fix(
        self,
        task: PRTask,
        spec: FileSpec,
        error_log: str,
        diagnosis: str,
    ) -> str:
        resp = await self._client.messages.create(
            model=self.MODEL,
            max_tokens=2000,
            system=(
                "You are fixing a bug in a source file. "
                "Output ONLY the complete corrected file content with no markdown fences, "
                "no explanations. Just the raw fixed file."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"File: {spec.path}\n\n"
                    f"Current content:\n```\n{spec.content[:3000]}\n```\n\n"
                    f"Error log:\n```\n{error_log[:2000]}\n```\n\n"
                    f"Diagnosis: {diagnosis[:1000]}\n\n"
                    "Output the complete fixed file."
                ),
            }],
        )
        return resp.content[0].text.strip()

    # ── CI log extraction ─────────────────────────────────────────────────────

    async def _fetch_ci_logs(self, task: PRTask, status: dict) -> str:
        """Try to pull CI job logs for the failing run."""
        try:
            run = status.get("latest_run", {}) if isinstance(status, dict) else {}
            run_id = run.get("id")
            if not run_id:
                return "No run ID found — could not fetch logs."

            jobs_data = await self._github.run(
                action="get_check_runs",
                repo=task.repo,
                run_id=run_id,
            )
            jobs = jobs_data.get("jobs", []) if isinstance(jobs_data, dict) else []
            failed_jobs = [j for j in jobs if j.get("conclusion") == "failure"]

            if not failed_jobs:
                return f"Run {run_id} failed but no individual job failures found."

            logs = []
            for job in failed_jobs[:2]:
                job_id = job.get("id")
                if job_id:
                    log_text = await self._github.run(
                        action="get_job_logs",
                        repo=task.repo,
                        job_id=job_id,
                    )
                    logs.append(f"Job: {job.get('name', job_id)}\n{log_text}")
            return "\n\n".join(logs)[:4000]
        except Exception as e:
            return f"Could not fetch CI logs: {e}"

    # ── Human notification ────────────────────────────────────────────────────

    async def _notify_human(self, task: PRTask, error_log: str) -> None:
        try:
            from core.push import PushNotifier
            push = PushNotifier()
            if push.available:
                await push.send(
                    title=f"Jarvis needs help: {task.id}",
                    message=(
                        f"Task '{task.pr_title}' in {task.repo} is stuck.\n"
                        f"PR: {task.pr_url}\n"
                        f"Error: {error_log[:300]}"
                    ),
                    priority="high",
                )
        except Exception:
            pass

        # Also post a comment on the PR if we have one
        if task.pr_number:
            try:
                await self._github.run(
                    action="add_comment",
                    repo=task.repo,
                    pr_number=task.pr_number,
                    comment=(
                        f"🤖 **Jarvis needs human review.**\n\n"
                        f"Auto-fix exhausted ({self._max_fix_retries} attempts).\n\n"
                        f"**Last error:**\n```\n{error_log[:1000]}\n```"
                    ),
                )
            except Exception:
                pass

    # ── State persistence ─────────────────────────────────────────────────────

    def _persist_state(self, task: PRTask) -> None:
        self._memory.upsert_pr_task(task)

    def _restore_state(self, task: PRTask) -> None:
        row = self._memory.get_pr_task(task.id)
        if row:
            task.state          = row.get("state", task.state)
            task.pr_number      = row.get("pr_number") or task.pr_number
            task.pr_url         = row.get("pr_url", task.pr_url)
            task.fix_attempt    = row.get("fix_attempt", 0)
            task.error_log      = row.get("error_log", "")
            task.work_dir       = row.get("work_dir", "")

    # ── YAML loader ───────────────────────────────────────────────────────────

    def _load_tasks(self, task_file: str) -> tuple[list[PRTask], dict]:
        with open(task_file) as f:
            data = yaml.safe_load(f)

        global_cfg = {
            "repo": data.get("repo", ""),
            "test_command": data.get("test_command", ""),
            "repo_url": data.get("repo_url", ""),
        }

        tasks = []
        for raw in data.get("tasks", []):
            repo = raw.get("repo") or global_cfg["repo"]
            files_create = [
                FileSpec(path=fc["path"], prompt=fc.get("prompt", ""))
                for fc in raw.get("files_to_create", [])
            ]
            files_modify = [
                FileSpec(path=fm["path"], prompt=fm.get("prompt", ""))
                for fm in raw.get("files_to_modify", [])
            ]
            tasks.append(PRTask(
                id=str(raw["id"]),
                repo=repo,
                branch=raw.get("branch", f"jarvis/{raw['id']}"),
                pr_title=raw.get("pr_title", raw.get("title", str(raw["id"]))),
                description=raw.get("description", ""),
                files_to_create=files_create,
                files_to_modify=files_modify,
                pr_body=raw.get("pr_body", ""),
                base_branch=raw.get("base_branch", data.get("base_branch", "main")),
                merge_strategy=raw.get("merge_strategy", self._default_merge),
            ))

        return tasks, global_cfg
