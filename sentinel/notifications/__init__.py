"""Notifications module for alert delivery."""

from .discord import DiscordNotifier

__all__ = ["DiscordNotifier"]
