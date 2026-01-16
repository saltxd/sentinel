"""Escalation module for AI-powered alert explanations."""

from .claude_client import ClaudeClient
from .decision import EscalationDecision, EscalationResult

__all__ = ["ClaudeClient", "EscalationDecision", "EscalationResult"]
