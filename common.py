import asyncio
import re
from loguru import logger


def clean_name(raw_name: str) -> str :
    cleaned_name: str =  re.sub(r"\s*\d+\s*$", "", raw_name)
    return cleaned_name

async def safe_goto(page, url, retries=3, delay=2):
    for attempt in range(1, retries + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=5_000)
            return
        except Exception as e1:
            try:
                logger.warning(f"⚠️ Attempt {attempt}/{retries} failed (domcontentloaded) for {url}: {e1}")
                logger.info(f"Retrying with lighter wait condition...")
                await page.goto(url, wait_until="networkidle", timeout=10_000)
                return
            except Exception as e2:
                logger.warning(f"Second attempt (networkidle) failed: {e2}")
                if attempt < retries:
                    await asyncio.sleep(delay)
    raise RuntimeError(f"Failed to load {url} after {retries} attempts")


async def old_safe_goto(page, url, retries=3, delay=2):
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
