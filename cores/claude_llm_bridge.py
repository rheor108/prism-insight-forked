"""
Claude Code LLM Bridge

Subprocess wrapper for `claude -p` (non-interactive mode).
Replaces OpenAIAugmentedLLM.generate_str() calls with Claude Code Max Plan credits.

Usage:
    result = await claude_generate(
        system_prompt="You are a stock analyst.",
        user_message="Analyze Samsung Electronics.",
        model="opus"
    )
"""

import asyncio
import logging
import os
import shutil
from asyncio.subprocess import PIPE
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = str(Path(__file__).parent.parent)

# Resolve claude binary path at module load time
# Priority: 1) CLAUDE_BIN env var 2) ~seungbum/.local/bin/claude 3) PATH lookup
_CLAUDE_BIN = os.environ.get("CLAUDE_BIN")
if not _CLAUDE_BIN:
    _candidate = Path("/home/seungbum/.local/bin/claude")
    if _candidate.exists():
        _CLAUDE_BIN = str(_candidate)
    else:
        _CLAUDE_BIN = shutil.which("claude") or "claude"

# Ensure HOME points to a directory that actually has claude auth state.
# Host cron runs as `seungbum` (~/.claude lives in /home/seungbum/.claude);
# Docker cron runs as root with the host's .claude bind-mounted to
# /root/.claude. Hard-coding /home/seungbum (the previous default) caused
# `claude -p` to exit=1 with empty stderr inside Docker because the auth
# file was simply not where HOME pointed. Explicit CLAUDE_HOME still wins.
def _resolve_claude_home() -> str:
    explicit = os.environ.get("CLAUDE_HOME")
    if explicit:
        return explicit
    candidates = [
        os.environ.get("HOME"),  # whatever the runtime user already has
        "/home/seungbum",        # host install
        "/root",                 # Docker container (HOME=/root by default)
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / ".claude").is_dir():
            return candidate
    return os.environ.get("HOME") or "/root"

_CLAUDE_HOME = _resolve_claude_home()


async def claude_generate(
    system_prompt: str,
    user_message: str,
    model: str = "opus",
    max_turns: int = 3,
    timeout: int = 300,
) -> str:
    """
    Call Claude via `claude -p` subprocess. Uses Max Plan credits ($0 cost).

    Args:
        system_prompt: System prompt (agent instruction)
        user_message: User message (analysis request)
        model: Claude model - "opus" | "sonnet" | "haiku"
        max_turns: Max agentic turns (for MCP tool use)
        timeout: Timeout in seconds

    Returns:
        str: Claude response text

    Raises:
        TimeoutError: If the subprocess exceeds timeout
        RuntimeError: If the subprocess returns non-zero exit code
    """
    cmd = [
        _CLAUDE_BIN, "-p",
        "--model", model,
        "--output-format", "text",
        "--max-turns", str(max_turns),
    ]

    # The user's host settings.json sets `permissions.defaultMode = bypassPermissions`,
    # which translates to `--dangerously-skip-permissions` internally. Claude refuses
    # that combination under root (security). Inside the Docker container we run as
    # root and inherit the same .claude/ via bind mount, so every call exited=1 with
    # 100% failure rate. Override the mode when running as root; on the host (non-root)
    # we honor the user's settings exactly as before.
    if os.geteuid() == 0:
        cmd.extend(["--permission-mode", "acceptEdits"])

    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    logger.info(f"claude -p call: model={model}, max_turns={max_turns}, "
                f"prompt_len={len(user_message)}, bin={_CLAUDE_BIN}")

    # Build env with correct HOME so claude picks up ~/.claude.json
    env = os.environ.copy()
    env["HOME"] = _CLAUDE_HOME
    env.pop("CLAUDECODE", None)  # Allow subprocess even inside a Claude Code session

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
            cwd=PROJECT_ROOT,
            env=env,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=user_message.encode("utf-8")),
            timeout=timeout,
        )

        result = stdout.decode("utf-8").strip()

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8").strip()
            stdout_msg = result  # Some CLIs emit error text on stdout, not stderr.
            logger.error(
                "claude -p failed (exit=%s) | stderr=%r | stdout=%r | "
                "cmd=%s | HOME=%s | model=%s | max_turns=%s | prompt_len=%d",
                process.returncode,
                error_msg[:500],
                stdout_msg[:500],
                cmd,
                _CLAUDE_HOME,
                model,
                max_turns,
                len(user_message),
            )
            # Surface whichever stream carried the diagnostic so callers see it.
            detail = error_msg or stdout_msg or "(no output)"
            raise RuntimeError(
                f"claude -p exited with code {process.returncode}: {detail[:200]}"
            )

        # Detect "Reached max turns" error returned as text output
        if result.startswith("Error: Reached max turns"):
            logger.error(f"claude -p hit max_turns limit: {result} (max_turns={max_turns})")
            raise RuntimeError(
                f"claude -p reached max turns ({max_turns}). "
                f"Increase max_turns for this agent."
            )

        logger.info(f"claude -p response: {len(result)} chars")
        return result

    except asyncio.TimeoutError:
        logger.error(f"claude -p timed out after {timeout}s")
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        raise TimeoutError(f"claude -p timed out after {timeout} seconds")
