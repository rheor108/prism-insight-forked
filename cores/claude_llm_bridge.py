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

# Ensure HOME points to seungbum's home for .claude.json auth config
_CLAUDE_HOME = os.environ.get("CLAUDE_HOME", "/home/seungbum")


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
            logger.error(f"claude -p failed (exit={process.returncode}): {error_msg[:500]}")
            raise RuntimeError(
                f"claude -p exited with code {process.returncode}: {error_msg[:200]}"
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
