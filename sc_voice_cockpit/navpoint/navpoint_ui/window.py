"""
navpoint_ui/window.py — Browser opener for NavPoint HUD
Author: Mallachi
"""

import logging
import webbrowser


logger = logging.getLogger(__name__)


class NavPointWindow:
    """Opens the NavPoint HUD in the default system browser."""

    def __init__(self, url: str) -> None:
        self._url = url

    def open(self) -> None:
        """Open the HUD in the default browser."""
        try:
            webbrowser.open(self._url)
            logger.info("NavPoint HUD opened at %s", self._url)
        except Exception as e:
            logger.error("Failed to open NavPoint HUD: %s", e)

    def close(self) -> None:
        """No-op — browser tabs cannot be programmatically closed."""
        pass
