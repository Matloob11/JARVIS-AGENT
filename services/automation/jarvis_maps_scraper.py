import asyncio
import logging
import os
import re
import subprocess
import pandas as pd
from datetime import datetime
from typing import Any, Optional, List
from dataclasses import dataclass, asdict
from playwright.async_api import async_playwright, Page

from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_bridge import notify_thinking, notify_tool_action
from services.ai_core.jarvis_plugin_manager import jarvis_tool

logger = setup_logger("JARVIS-MAPS")
_bg_tasks = set()

async def _safe_notify(notify_func, *args, **kwargs):
    """Helper to run notifications in the background."""
    try:
        t = asyncio.create_task(notify_func(*args, **kwargs))
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
    except Exception:
        pass

@dataclass
class Place:
    name: str = ""
    address: str = ""
    website: str = ""
    phone: str = ""
    rating: Optional[float] = None
    reviews: Optional[int] = None
    email: str = ""
    socials: str = ""

class MapsScraper:
    def __init__(self):
        # Resolve path relative to project root
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
        self.output_dir = os.path.join(project_root, "Jarvis_Outputs", "leads")
        os.makedirs(self.output_dir, exist_ok=True)

    async def _extract_text(self, page, selector):
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0:
                text = await loc.inner_text()
                return text.strip()
            return ""
        except Exception:
            return ""

    async def scrape_leads(self, category: str, location: str, total: int, headless: bool = False):
        query = f"{category} in {location}" if location else category
        results = []
        
        async with async_playwright() as p:
            # slow_mo helps visibility and reduces CPU spikes
            browser = await p.chromium.launch(headless=headless, slow_mo=50)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            try:
                await page.goto("https://www.google.com/maps", timeout=60000)
                
                # Handling cookies/accept
                for btn in ['button:has-text("Accept all")', 'button[aria-label="Accept all"]']:
                    try:
                        if await page.locator(btn).is_visible(timeout=3000):
                            await page.locator(btn).click()
                            break
                    except: pass

                # Search
                search_box = page.locator('input#searchboxinput, input[role="combobox"]').first
                await search_box.fill(query)
                await page.keyboard.press("Enter")
                
                result_selector = 'a.hfpxzc'
                try:
                    await page.wait_for_selector(result_selector, timeout=15000)
                except:
                    return [] # No results

                # Scrolling logic
                prev_count = 0
                while len(await page.locator(result_selector).all()) < total:
                    await page.hover(result_selector)
                    # Scroll more gradually
                    await page.mouse.wheel(0, 2000)
                    await asyncio.sleep(1)
                    count = len(await page.locator(result_selector).all())
                    if count == prev_count: break
                    prev_count = count

                listings = (await page.locator(result_selector).all())[:total]
                
                for i, listing in enumerate(listings):
                    try:
                        await listing.scroll_into_view_if_needed()
                        await listing.click()
                        await page.wait_for_selector('h1.DUwDvf', timeout=10000)
                        
                        place = Place(
                            name=await self._extract_text(page, 'h1.DUwDvf'),
                            address=await self._extract_text(page, 'button[data-item-id="address"] .Io6YTe'),
                            website=await self._extract_text(page, 'a[data-item-id="authority"] .Io6YTe'),
                            phone=await self._extract_text(page, 'button[data-item-id^="phone:tel:"] .Io6YTe')
                        )
                        
                        # Extra cleanup for website and phone
                        if place.website and not place.website.startswith("http"):
                            place.website = "https://" + place.website
                        
                        results.append(place)
                        await _safe_notify(notify_thinking, f"Sir, {i+1} leads collect ho chuki hain: {place.name}")
                    except Exception as e:
                        logger.warning(f"Error scraping listing {i}: {e}")
                        continue
            finally:
                await browser.close()
        
        return results

    def save_results(self, places: List[Place], filename: str, format: str = "csv"):
        if not places: return None
        
        df = pd.DataFrame([asdict(p) for p in places])
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r'[^\w\s-]', '', filename).strip().replace(' ', '_')
        base_path = os.path.join(self.output_dir, f"{safe_name}_{timestamp}")
        
        final_file = ""
        if format.lower() == "csv":
            final_file = f"{base_path}.csv"
            df.to_csv(final_file, index=False, encoding='utf-8-sig')
        elif format.lower() == "json":
            final_file = f"{base_path}.json"
            df.to_json(final_file, orient='records', indent=4)
        elif format.lower() in ["excel", "xlsx"]:
            final_file = f"{base_path}.xlsx"
            df.to_excel(final_file, index=False)
        else:
            # Default to csv
            final_file = f"{base_path}.csv"
            df.to_csv(final_file, index=False, encoding='utf-8-sig')
            
        return final_file

scraper_service = MapsScraper()

@jarvis_tool(execution_timeout=300.0) # Long timeout for scraping
async def scrape_google_maps_leads(category: str, location: str = "", count: int = 10, format: str = "csv") -> dict[str, Any]:
    """
    Sir, is tool se main Google Maps par kisi bhi category ke leads (name, address, phone, website) scrap karta hoon.
    Main browser open karke aapko dikhaon ga ke kaam kaise ho raha hai.
    """
    try:
        await notify_thinking(f"Sir, main {category} ke liye leads dhondna shuru kar raha hoon...")
        
        # We run headless=False to show the user the browser
        leads = await scraper_service.scrape_leads(category, location, count, headless=False)
        
        if not leads:
            return {"status": "error", "message": "❌ Koi leads nahi mili. Query check karein."}
        
        filename = f"{category}_{location if location else 'global'}"
        file_path = scraper_service.save_results(leads, filename, format)
        
        # Open the file automatically for the user
        if os.path.exists(file_path):
            os.startfile(file_path)
            
        msg = f"✅ Success! {len(leads)} leads scrape ho gayi hain aur '{os.path.basename(file_path)}' mein save kar di gayi hain."
        await notify_tool_action("google_maps_scraper", f"Scraped {len(leads)} leads for {category}")
        
        return {
            "status": "success",
            "count": len(leads),
            "file_path": file_path,
            "message": msg
        }
    except Exception as e:
        logger.exception("Scraper Tool Error: %s", e)
        return {"status": "error", "message": f"❌ Scraper error: {str(e)}"}

@jarvis_tool
async def open_leads_file(name: str) -> dict[str, Any]:
    """
    Sir, agar aap ne pehle koi leads scrape ki hain, to unka naam bataein, main file open kar doon ga.
    """
    output_dir = os.path.join(os.getcwd(), "Jarvis_Outputs", "leads")
    if not os.path.exists(output_dir):
        return {"status": "error", "message": "❌ Abhi tak koi leads folder nahi bana."}
        
    import glob
    # Search for files matching the name
    files = glob.glob(os.path.join(output_dir, f"*{name}*"))
    
    if not files:
        return {"status": "error", "message": f"❌ '{name}' ke naam se koi file nahi mili."}
        
    # Sort by recent
    files.sort(key=os.path.getmtime, reverse=True)
    target = files[0]
    
    os.startfile(target)
    return {
        "status": "success",
        "opened_file": os.path.basename(target),
        "message": f"📂 File '{os.path.basename(target)}' open kar di gayi hai."
    }
