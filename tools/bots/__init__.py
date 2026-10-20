"""Co-op bot action layer. See docs/tools/T6a-actions.md.

    import sys; sys.path.insert(0, "<repo>/tools")
    from bots import Bot, FakeBridge, World, ActionFailed
"""
from .bot import ActionFailed, ActionResult, Bot, Pos
from .fake import FakeBridge, World

__all__ = ["ActionFailed", "ActionResult", "Bot", "Pos", "FakeBridge", "World"]
