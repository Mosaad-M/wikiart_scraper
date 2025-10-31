import asyncio
import json
from urllib.parse import urljoin
from playwright.async_api import async_playwright, Browser, Page, ElementHandle, JSHandle
from typing import List, Union, Dict
import argparse
from pydantic import Json
from common import clean_name, load_all_artworks, safe_goto, close_popup_if_present
from loguru import logger

BASE_URL = "https://www.wikiart.org"
START_URL = f"{BASE_URL}/en/artists-by-art-movement"



async def get_art_movements(browser: Browser, header_filter:str):
    """Return all sub-period movements under a given header."""
    page: Page = await browser.new_page()
    logger.info(f"Navigating to {START_URL}...")
    await safe_goto(page, START_URL)
    await close_popup_if_present(page)
    await page.wait_for_selector("li.header span")

    headers: List[ElementHandle] = await page.query_selector_all("li.header span")
    logger.info(f"Found {len(headers)} art movement categories.")

    all_movements = []
    for header in headers:
        header_text: str = (await header.text_content()).strip() 
        clean_header: str = " ".join(header_text.split())

        if header_filter and header_filter.lower() not in clean_header.lower():
            continue

        logger.info(f"Found matching header: {clean_header}")

        dotted_items: JSHandle = await page.evaluate_handle(
            """(header) => {
                const results = [];
                let el = header.parentElement.nextElementSibling;
                while (el && !el.classList.contains('header')) {
                    if (el.classList.contains('dottedItem')) {
                        const link = el.querySelector('a');
                        if (link) {
                            results.push({
                                name: link.textContent.trim(),
                                href: link.getAttribute('href')
                            });
                        }
                    }
                    el = el.nextElementSibling;
                }
                return results;
            }""",
            header
        )

        movements: Json = await dotted_items.json_value()
        for m in movements:
            all_movements.append({
                "name": clean_name(m["name"]),
                "url": urljoin(BASE_URL, m["href"])
            })

    await page.close()
    logger.info(f"Found {len(all_movements)} movements.")
    return all_movements


async def get_artists_for_movement(browser: Browser, movement):
    """Get all artists from a given movement page."""
    page: Page = await browser.new_page()
    try:
        await safe_goto(page, movement["url"])
        await close_popup_if_present(page)
        await page.wait_for_selector("ul.wiki-artistgallery-container li", timeout=10000)

        artist_items: List[ElementHandle] = await page.query_selector_all("ul.wiki-artistgallery-container li.ng-scope")
        artists: List = []
        for li in artist_items:
            try:
                name_tag: Union[ElementHandle, None] = await li.query_selector("div.artist-name a")
                if not name_tag:
                    continue
                name: str = (await name_tag.text_content()).strip()
                href: Union[str, None] = await name_tag.get_attribute("href")
                img_tag: Union[ElementHandle, None] = await li.query_selector("a.image-wrapper img")
                img_url: str = await img_tag.get_attribute("src") if img_tag else "Unknown"
                works_tag: Union[ElementHandle, None] = await li.query_selector("div.works-count")
                works_count: str = (await works_tag.text_content()).strip() if works_tag else "Unknown"

                artists.append({
                    "name": name,
                    "url": urljoin(BASE_URL, href),
                    "image": img_url,
                    "works_count": works_count,
                    "movement": movement["name"]
                })
            except Exception as e:
                logger.info(f"Error parsing artist: {e}")

        logger.info(f"Found {len(artists)} artists in {movement['name']}")
        return artists
    finally:
        await page.close()


async def get_works_for_artist(browser: Browser, artist: Dict):
    """Get all works for an artist (with retries and safety)."""
    page: Page = await browser.new_page()
    art_works = []
    artist_name = artist.get("name", "Unknown")
    artist_url = artist.get("url", "").rstrip("/")

    try:
        logger.info(f"Scraping art works for: {artist_name}")
        await safe_goto(page, artist_url)
        await close_popup_if_present(page)

        # Handle "View all artworks" link
        try:
            view_all = await page.query_selector("a.btn-view-all")
            if view_all:
                href: Union[ElementHandle, None] = await view_all.get_attribute("href")
                if href and not href.startswith("javascript:") and not href.startswith("#"):
                    full_url: str = href if href.startswith("http") else urljoin(BASE_URL, href)
                    logger.info(f"Found 'View all artworks' link → Navigating to {full_url}")
                    await safe_goto(page, full_url)
                    await close_popup_if_present(page)
        except Exception as e:
            logger.info(f"No 'View all artworks' button for {artist_name} ({e})")

        # Fallback to "all works" page if necessary
        current_url = page.url
        if "all-works" not in current_url:
            alt_url = f"{artist_url}/all-works#!#filterName:all-works,resultType:masonry"
            logger.info(f"Loading all works page: {alt_url}")
            await safe_goto(page, alt_url)
            await close_popup_if_present(page)

        # Ensure all works are loaded (click "LOAD MORE" until done)
        await load_all_artworks(page)

        # Lazy-load by scrolling (trigger dynamic loading)
        for _ in range(8):
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await asyncio.sleep(0.8)

        # Try multiple possible selectors
        selectors = [
            "a.artwork-name",
            "ul.wiki-masonry-container li a",
            "ul.masonry-container li a",
            "ul.painting-list-text li a",
            "li.painting-list-text-row a",
            "ul.masonry-list li a",
            "ul.masonry-list-text li a",
        ]

        nodes = []
        for sel in selectors:
            nodes: List[ElementHandle] = await page.query_selector_all(sel)
            if nodes:
                nodes = [
                    n for n in nodes
                    if await n.evaluate("el => el.offsetWidth > 0 || el.offsetHeight > 0")
                ]
            if nodes:
                break

        if not nodes:
            logger.info(f"No art works found for {artist_name}")
            return art_works

        seen_urls = set()
        for a in nodes:
            try:
                href: Union[str, None] = await a.get_attribute("href")
                if not href or href.startswith("javascript:") or href.startswith("#"):
                    continue

                full_url: str = href if href.startswith("http") else urljoin(BASE_URL, href)
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                # Extract title text
                title: str = (await a.text_content() or "").strip()
                if not title:
                    img: Union[ElementHandle, None] = await a.query_selector("img")
                    if img:
                        title = (
                            (await img.get_attribute("title"))
                            or (await img.get_attribute("alt"))
                            or ""
                        ).strip()

                # Extract image URL with fallbacks
                img_url = None
                img: Union[ElementHandle, None] = await a.query_selector("img")
                if img:
                    img_url: Union[str, None] = await img.get_attribute("src") or await img.get_attribute("data-src")

                if not img_url:
                    parent_li: JSHandle = await a.evaluate_handle("el => el.closest('li')")
                    if parent_li:
                        img2: Union[ElementHandle, None] = await parent_li.query_selector("img")
                        if img2:
                            img_url = await img2.get_attribute("src") or await img2.get_attribute("data-src")

                art_works.append({
                    "title": title or "Unknown",
                    "url": full_url,
                    "image": img_url or "Unknown",
                    "artist": artist.get("name"),
                    "movement": artist.get("movement"),
                })

            except Exception as e:
                logger.info(f"Error parsing art works for {artist_name}: {e}")
                continue

        logger.info(f"Found {len(art_works)} art works for {artist_name}")
        return art_works

    except Exception as e:
        logger.info(f"Error scraping art works for {artist_name}: {e}")
        return art_works
    finally:
        await page.close()



async def get_location(browser: Browser, art_work_url: str):
    """Fetch the current location of an art work (if available)."""
    page: Page = await browser.new_page()
    try:
        await safe_goto(page, art_work_url)
        await asyncio.sleep(1)
        loc_el: Union[ElementHandle, None] = await page.query_selector("li.dictionary-values-gallery span")
        if loc_el:
            return (await loc_el.text_content()).strip()
    except:
        pass
    finally:
        await page.close()
    return "Unknown"


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="WikiArt Scraper — collect artists, artworks, and metadata."
    )
    parser.add_argument(
        "--movement",
        type=str,
        default="",
        help="Art movement to scrape (e.g., 'Renaissance'). Leave empty for all.",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="If set, export results to JSON file.",
    )
    return parser.parse_args()


async def main(movement: str, export: bool):
    
    logger.info(f"Getting art works for art movement: {movement}")

    async with async_playwright() as p:
        browser: Browser  = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-background-timer-throttling",
                "--disable-background-networking",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ]
        )

        movements: List = await get_art_movements(browser, header_filter=movement)
        all_artists, all_art_works = [], []

        for mv in movements:
            artists: List = await get_artists_for_movement(browser, mv)
            all_artists.extend(artists)
            for artist in artists:
                art_works: List = await get_works_for_artist(browser, artist)
                for art_work in art_works:
                    art_work["location"] = await get_location(browser, art_work["url"])
                    all_art_works.append(art_work)

        logger.info(f"\nTotal artists collected: {len(all_artists)}")
        logger.info(f"Total art works collected: {len(all_art_works)}")

        if export:
            file_name = f"{movement} wikiart data.json"
            data = {"artists": all_artists, "art works": all_art_works}
            with open(file_name, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logger.info(f"Data saved to {file_name}")

        await browser.close()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args.movement, args.export))
