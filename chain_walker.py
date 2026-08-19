#!/usr/bin/env python3
import asyncio
import re
import sys
from urllib.parse import urljoin

from playwright.async_api import async_playwright

FILE_RE = re.compile(
    r"https?://(?:www\.)?(?:mediafire\.com/[A-Za-z0-9/_-]+|devupload\.[a-z]+/[A-Za-z0-9]+|drive\.google\.com/[A-Za-z0-9/_-]+|mega\.nz/[A-Za-z0-9!_-]+)",
    re.IGNORECASE,
)
SHORT_RE = re.compile(r"https?://(?:vplink|linksgo|flylink|short|tinyurl|bit\.ly|t\.co)[^\"' <]*", re.IGNORECASE)
EARN_RE = re.compile(r"https?://[^\"' <]*earnlinks\.[a-z]+/[A-Za-z0-9]+", re.IGNORECASE)
CLICK_RE = re.compile(r"(get link|continue|unlock|proceed|download|next|click here|skip)", re.IGNORECASE)


async def scan(page):
    html = await page.content()
    files = set(FILE_RE.findall(html))
    earns = set(EARN_RE.findall(html))
    shorts = set(SHORT_RE.findall(html))
    for el in await page.query_selector_all("a[href]"):
        href = await el.get_attribute("href")
        if href:
            href = urljoin(page.url, href)
            if FILE_RE.search(href):
                files.add(href)
            if EARN_RE.search(href):
                earns.add(href)
    return files, earns, shorts


async def click_drill(page, max_clicks=10):
    for _ in range(max_clicks):
        clicked = await page.evaluate(
            """() => {
                const els = [...document.querySelectorAll('a,button')].filter(e =>
                    /(get link|continue|unlock|proceed|download|next|click here|skip ad|continue to)/i.test(e.textContent || ''));
                if (!els.length) return null;
                els[0].click();
                return els[0].textContent.trim().slice(0, 40);
            }"""
        )
        if not clicked:
            return False
        await page.wait_for_timeout(4000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        files, earns, _ = await scan(page)
        if files or earns:
            print("DRILL_CLICK: {!r} files={} earns={}".format(clicked, len(files), len(earns)))
        return True
    return False


async def run(start_url, wait_seconds=15):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
            viewport={"width": 412, "height": 915},
            locale="en-US",
        )
        page = await context.new_page()
        seen = set()
        all_file_links = set()
        all_earn_links = set()
        network_urls = []
        referer = "https://t.me/"

        page.on("response", lambda r: network_urls.append(r.url) if r.status in (200, 301, 302) else None)

        current = start_url
        for hop in range(15):
            if current in seen:
                break
            seen.add(current)
            print("\n=== HOP {} ===".format(hop))
            print("URL: " + current)
            try:
                await page.goto(current, wait_until="domcontentloaded", timeout=30000, referer=referer)
                await asyncio.sleep(min(wait_seconds, 8))
            except Exception as e:
                print("goto error: {}".format(type(e).__name__))
                break

            files, earns, shorts = await scan(page)
            all_file_links |= files
            all_earn_links |= earns
            if files or earns:
                print("DOM HIT: files={} earns={}".format(len(files), len(earns)))
            if files:
                break
            if earns:
                print("EARNLINKS_LINK=" + sorted(earns)[0])
                break

            await click_drill(page)
            files, earns, shorts = await scan(page)
            all_file_links |= files
            all_earn_links |= earns
            if files:
                print("POST-DRILL FILES: " + ", ".join(sorted(files)))
                break
            if earns:
                print("EARNLINKS_LINK=" + sorted(earns)[0])
                break

            final_url = page.url
            if final_url != current:
                print("NAVIGATED: " + final_url)
                current = final_url
                referer = current
                continue

            nxt = None
            for l in sorted(all_earn_links):
                nxt = l
                break
            if not nxt:
                for l in sorted(all_short_links):
                    if l not in seen:
                        nxt = l
                        break
            if nxt and nxt not in seen:
                current = nxt
                referer = final_url
                continue
            break

        print("\n=== FILE LINKS FOUND ===")
        for l in sorted(all_file_links):
            print(l)
        print("=== EARNLINKS LINKS ===")
        for l in sorted(all_earn_links):
            print(l)
        print("=== NETWORK URLS ===")
        for u in network_urls[:30]:
            print(u)
        print("SUMMARY_FILES=" + ",".join(sorted(all_file_links)))
        await browser.close()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "https://linksgo.in/jzgwzSU"
    wait = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    asyncio.run(run(target, wait))