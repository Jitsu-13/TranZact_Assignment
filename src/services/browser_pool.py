"""
Browser Pool Manager for Playwright.

Maintains a pool of reusable browser instances to avoid the overhead of
launching a new browser for every PDF render. Each browser instance uses
~50-80MB base memory. We keep the pool small (default 3) since each PDF
render temporarily spikes to ~400MB.

Pool sizing rationale:
- r6i.large has 16GB RAM, Django monolith uses ~4-6GB
- 3 browsers * 400MB peak = 1.2GB peak concurrent rendering
- Leaves headroom for OS, Redis, and Celery worker overhead

Note on event loops: Since generate_pdf() runs on a persistent background
event loop (required because it's called from sync Flask context), we use
a threading lock for init protection. The pool auto-recovers from stale
Playwright connections by reinitializing when all browsers are dead.
"""

import asyncio
import threading
from playwright.async_api import async_playwright, Browser, Page
from src.config import Config
from src.logger import logger


class BrowserPool:
    def __init__(self, pool_size: int = None):
        self._pool_size = pool_size or Config.BROWSER_POOL_SIZE
        self._max_browsers = self._pool_size * 2
        self._browsers: list[Browser] = []
        self._playwright = None
        self._init_lock = threading.Lock()  # Thread-safe init guard
        self._initialized = False

    async def initialize(self):
        """Initialize browser pool. Thread-safe, idempotent."""
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            self._playwright = await async_playwright().start()
            for _ in range(self._pool_size):
                browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-extensions",
                        "--single-process",
                    ],
                )
                self._browsers.append(browser)
            self._initialized = True
            logger.info(f"Browser pool initialized with {self._pool_size} instances")

    async def _reinitialize(self):
        """Force reinitialize after Playwright connection death."""
        logger.warning("Playwright connection dead — reinitializing browser pool")
        # Clean up old state
        for browser in self._browsers:
            try:
                await browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browsers = []
        self._playwright = None
        self._initialized = False
        # Reinitialize fresh
        await self.initialize()

    async def acquire_page(self) -> tuple[Browser, Page]:
        """Acquire a browser from pool and create a new page.

        Handles stale browsers where is_connected() returns True but the
        browser context is actually dead. If the entire Playwright connection
        is dead, reinitializes the pool automatically.
        """
        # Try existing browsers first
        for browser in self._browsers:
            if browser.is_connected():
                try:
                    ctx = await browser.new_context()
                    page = await ctx.new_page()
                    return browser, page
                except Exception as e:
                    logger.warning(f"Browser reported connected but failed: {e}")
                    continue

        # All existing browsers failed — try launching a new one
        if len(self._browsers) < self._max_browsers:
            try:
                browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                )
                self._browsers.append(browser)
                ctx = await browser.new_context()
                page = await ctx.new_page()
                return browser, page
            except Exception as e:
                logger.warning(f"Failed to launch new browser: {e}")

        # Everything is dead — reinitialize the entire pool
        await self._reinitialize()

        # Try once more with fresh pool
        browser = self._browsers[0]
        ctx = await browser.new_context()
        page = await ctx.new_page()
        return browser, page

    async def release_page(self, page: Page):
        """Close the page/context to free memory."""
        try:
            ctx = page.context
            await page.close()
            await ctx.close()
        except Exception as e:
            logger.warning(f"Error releasing page: {e}")

    async def shutdown(self):
        """Shutdown all browsers and Playwright."""
        for browser in self._browsers:
            try:
                await browser.close()
            except Exception:
                pass
        if self._playwright:
            await self._playwright.stop()
        self._browsers = []
        self._playwright = None
        self._initialized = False
        logger.info("Browser pool shut down")


# Singleton instance
_pool = BrowserPool()


def get_browser_pool() -> BrowserPool:
    return _pool
