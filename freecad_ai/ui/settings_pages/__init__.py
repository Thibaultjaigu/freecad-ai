"""The settings pages both settings windows embed (#101).

Each page is one QWidget owning a slice of config.json. The Settings dialog
stacks all four in a scroll area; Edit → Preferences shows each as its own
page under "FreeCAD AI". Neither window holds settings logic of its own, so
the two cannot drift apart again (#12, #97).
"""

from .provider_page import ProviderPage
from .behavior_page import BehaviorPage
from .tools_page import ToolsPage
from .mcp_page import McpPage

__all__ = ["ProviderPage", "BehaviorPage", "ToolsPage", "McpPage"]
