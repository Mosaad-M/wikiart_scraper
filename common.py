import asyncio
import re
from loguru import logger


def clean_name(raw_name: str) -> str :
    cleaned_name: str =  re.sub(r"\s*\d+\s*$", "", raw_name)
    return cleaned_name


async def intercept_route(route):
    """Abort requests for images, fonts, media, and tracking scripts."""
    req = route.request
    resource_type = req.resource_type

    blocked_types = {"image", "stylesheet", "font", "media"}
    blocked_domains = ["google-analytics.com", "doubleclick.net", "facebook.com"]

    if (
        resource_type in blocked_types
        or any(domain in req.url for domain in blocked_domains)
    ):
        await route.abort()
    else:
        await route.continue_()


async def safe_goto(page, url, retries=3, delay=2):

    for attempt in range(1, retries + 1):
        try:
            await page.route("**/*", lambda route: asyncio.create_task(intercept_route(route)))
            await page.goto(url, wait_until="domcontentloaded", timeout=5_000 * attempt)
            return
        except Exception as e1:
            try:
                logger.warning(f"⚠️ Attempt {attempt}/{retries} failed (domcontentloaded) for {url}: {e1}")
                logger.info(f"Retrying with heavier wait condition...")
                await page.route("**/*", lambda route: asyncio.create_task(intercept_route(route)))
                await page.goto(url, wait_until="networkidle", timeout=5_000  * attempt)
                return
            except Exception as e2:
                logger.warning(f"Second attempt (networkidle) failed: {e2}")
                if attempt < retries:
                    await asyncio.sleep(delay)
    raise RuntimeError(f"Failed to load {url} after {retries} attempts")


async def domcontentloaded_safe_goto(page, url, retries=3, delay=2):
    for attempt in range(1, retries + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=5_000 * retries)
            return
        except Exception as e1:
            try:
                logger.warning(f"⚠️ Attempt {attempt}/{retries} failed (domcontentloaded) for {url}: {e1}")
                logger.info(f"Retrying with heavier wait condition...")
                await page.goto(url, wait_until="networkidle", timeout=10_000  * retries)
                return
            except Exception as e2:
                logger.warning(f"Second attempt (networkidle) failed: {e2}")
                if attempt < retries:
                    await asyncio.sleep(delay)
    raise RuntimeError(f"Failed to load {url} after {retries} attempts")


async def networkidle_safe_goto(page, url, retries=3, delay=2):
    for attempt in range(1, retries + 1):
        try:
            await page.goto(url, wait_until="networkidle", timeout=10_000)
            return
        except Exception as e1:
            try:
                logger.warning(f"Attempt {attempt}/{retries} failed (networkidle) for {url}: {e1}")
                logger.info(f"Retrying with lighter wait condition...")
                await page.goto(url, wait_until="domcontentloaded", timeout=10_000)
                return
            except Exception as e2:
                logger.warning(f"Second attempt (domcontentloaded) failed: {e2}")
                if attempt < retries:
                    await asyncio.sleep(delay)
    raise RuntimeError(f"Failed to load {url} after {retries} attempts")


async def close_popup_if_present(page):
    """Close the occasional login/newsletter popup if visible."""
    try:
        await page.wait_for_selector("#close-popup", timeout=5000)
        await page.click("#close-popup")
        await asyncio.sleep(1)
        logger.info("Closed popup.")
    except:
        pass


async def load_all_artworks(page):
    """Keep clicking the 'LOAD MORE' button until all artworks are loaded or hidden."""
    try:
        for i in range(30):  # limit to 30 clicks max to avoid infinite loops
            button = await page.query_selector("a.masonry-load-more-button")
            if not button:
                logger.info("All artworks loaded (no more button found).")
                break

            visible = await button.is_visible()
            if not visible:
                logger.info("Load more button found but not visible. Scrolling...")
                await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                await asyncio.sleep(1)
                visible = await button.is_visible()

            if not visible:
                logger.info("Button still not visible — stopping loop.")
                break

            # Scroll into view just in case
            await button.scroll_into_view_if_needed()
            logger.info(f"Clicking 'LOAD MORE' ({i+1})...")
            try:
                await button.click(timeout=10000)
            except Exception as click_err:
                logger.warning(f"Click attempt {i+1} failed: {click_err}")
                await asyncio.sleep(2)
                continue  # retry next loop iteration

            # Wait for new content to appear
            await asyncio.sleep(2.5)
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await asyncio.sleep(0.5)

            # If the button disappears after the click, we’re done
            still_exists = await page.query_selector("a.masonry-load-more-button")
            if not still_exists:
                logger.info("No more 'LOAD MORE' button visible. Finished loading artworks.")
                break

    except Exception as e:
        logger.warning(f"Load more loop stopped due to error: {e}")