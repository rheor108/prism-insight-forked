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
from asyncio.subprocess import PIPE
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = str(Path(__file__).parent.parent)


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
        "claude", "-p",
        "--model", model,
        "--output-format", "text",
        "--max-turns", str(max_turns),
    ]

    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    logger.info(f"claude -p call: model={model}, max_turns={max_turns}, "
                f"prompt_len={len(user_message)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
            cwd=PROJECT_ROOT,
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

        logger.info(f"claude -p response: {len(result)} chars")
        return result

    except asyncio.TimeoutError:
        logger.error(f"claude -p timed out after {timeout}s")
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        raise TimeoutError(f"claude -p timed out after {timeout} seconds")
