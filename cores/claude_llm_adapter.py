"""
Claude Code LLM Adapter

Drop-in replacement for OpenAIAugmentedLLM. Mimics the `attach_llm` + `generate_str`
pattern so existing code requires minimal changes.

Usage (replaces OpenAIAugmentedLLM pattern):

    # Before:
    llm = await agent.attach_llm(OpenAIAugmentedLLM)
    result = await llm.generate_str(message=msg, request_params=RequestParams(model="gpt-5.2"))

    # After:
    from cores.claude_llm_adapter import ClaudeCodeLLM
    llm = ClaudeCodeLLM(instruction=agent.instruction)
    result = await llm.generate_str(message=msg)
"""

import logging
from typing import Optional

try:
    from cores.claude_llm_bridge import claude_generate
except (ModuleNotFoundError, ImportError):
    # Fallback for when 'cores' resolves to a different package (e.g. prism-us/cores/)
    import importlib.util as _ilu
    from pathlib import Path as _Path
    _spec = _ilu.spec_from_file_location(
        "claude_llm_bridge", _Path(__file__).parent / "claude_llm_bridge.py"
    )
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    claude_generate = _mod.claude_generate

logger = logging.getLogger(__name__)

# Map old OpenAI model names to Claude models
_MODEL_MAP = {
    # Heavy reasoning models → opus
    "gpt-5.2": "opus",
    "gpt-5.1": "opus",
    "gpt-5": "opus",
    "gpt-4.1": "opus",
    "gpt-4o": "sonnet",
    # Lightweight models → haiku
    "gpt-5-nano": "haiku",
    "gpt-5-mini": "haiku",
    "gpt-4o-mini": "haiku",
    "gpt-4.1-mini": "haiku",
    "gpt-4.1-nano": "haiku",
}


def _resolve_model(request_params=None, default: str = "opus") -> str:
    """Resolve Claude model from RequestParams or default."""
    if request_params is None:
        return default

    old_model = getattr(request_params, "model", None) or ""

    # Direct mapping
    if old_model in _MODEL_MAP:
        return _MODEL_MAP[old_model]

    # Pattern matching
    lower = old_model.lower()
    if "nano" in lower or "mini" in lower:
        return "haiku"
    if "5.2" in lower or "5.1" in lower:
        return "opus"

    return default


class ClaudeCodeLLM:
    """
    Drop-in adapter for OpenAIAugmentedLLM.

    Wraps `claude -p` subprocess calls behind the same generate_str() interface
    that the codebase already uses.
    """

    def __init__(
        self,
        instruction: str = "",
        server_names: Optional[list] = None,
        default_model: str = "opus",
        max_turns: int = 3,
        timeout: int = 300,
    ):
        """
        Args:
            instruction: System prompt (agent instruction text)
            server_names: MCP server names (used by claude -p via .mcp.json)
            default_model: Default Claude model if not specified in request_params
            max_turns: Default max agentic turns for MCP tool use
            timeout: Default timeout in seconds
        """
        self.system_prompt = instruction
        self.server_names = server_names or []
        self.default_model = default_model
        self.max_turns = max_turns
        self.timeout = timeout

    async def generate_str(
        self,
        message: str = "",
        request_params=None,
    ) -> str:
        """
        Generate text response via claude -p.

        Compatible with the existing codebase pattern:
            result = await llm.generate_str(message=msg, request_params=RequestParams(...))

        Args:
            message: User prompt / analysis request
            request_params: Optional RequestParams (model field is mapped to Claude model)

        Returns:
            str: Generated text
        """
        model = _resolve_model(request_params, self.default_model)

        # Extract max_turns from request_params if available
        max_turns = self.max_turns
        if request_params:
            max_iterations = getattr(request_params, "max_iterations", None)
            if max_iterations:
                max_turns = max_iterations

        return await claude_generate(
            system_prompt=self.system_prompt,
            user_message=message,
            model=model,
            max_turns=max_turns,
            timeout=self.timeout,
        )
