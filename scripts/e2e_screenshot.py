"""Headless end-to-end check: plan Dallas -> Denver, wait for the heatmap, scrub the slider,
open a checkpoint popup and save a screenshot.

Usage: python scripts/e2e_screenshot.py [base_url] [output.png] [origin] [destination] [load_lb] [slider_hour]
"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else "screenshot.png"
ORIGIN = sys.argv[3] if len(sys.argv) > 3 else "Dallas, TX"
DEST = sys.argv[4] if len(sys.argv) > 4 else "Denver, CO"
LOAD = sys.argv[5] if len(sys.argv) > 5 else "40000"
HOUR = sys.argv[6] if len(sys.argv) > 6 else "6"
CHROME = "/usr/bin/google-chrome"

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME, headless=True)
    page = browser.new_page(viewport={"width": 1500, "height": 940}, timezone_id="America/Chicago")
    errors = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE, wait_until="networkidle")
    page.get_by_label("Origin").fill(ORIGIN)
    page.get_by_label("Destination").fill(DEST)
    page.get_by_label("Load weight (lb)").fill(LOAD)
    t0 = time.time()
    page.get_by_role("button", name="Plan safest route").click()
    page.wait_for_selector(".route-card", timeout=90_000)
    t_routes = time.time() - t0
    page.wait_for_selector(".slider-wrap input[type=range]", timeout=90_000)
    t_heat = time.time() - t0
    cards = page.locator(".route-card").all_inner_texts()

    # Scrub the slider through all 48 hours and time how fast frames swap.
    slider = page.locator(".slider-wrap input[type=range]")
    t1 = time.time()
    for h in range(0, 49):
        slider.fill(str(h))
    scrub_ms = (time.time() - t1) * 1000 / 49
    slider.fill(HOUR)
    page.wait_for_timeout(300)

    page.wait_for_timeout(800)
    page.screenshot(path=OUT)

    # Click the first checkpoint marker found on the map (canvas-rendered, so locate it by
    # colour in a screenshot) and save a second image with its popup open.
    popup_text = None
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
        w, h = img.size
        target = None
        for y in range(260, h - 260, 3):
            for x in range(460, w - 20, 3):
                r, g, b = img.getpixel((x, y))
                if abs(r - 22) < 12 and abs(g - 163) < 12 and abs(b - 74) < 12:
                    target = (x, y)
                    break
            if target:
                break
        if target:
            page.mouse.click(*target)
            page.wait_for_selector(".cp-popup", timeout=3000)
            page.wait_for_timeout(900)  # let auto-pan finish
            popup_text = page.locator(".cp-popup").inner_text()
            page.screenshot(path=OUT.replace(".png", "-popup.png"))
    except Exception as exc:  # popup check is best-effort
        popup_text = f"popup check skipped: {exc}"
    print(f"routes in {t_routes:.1f}s, heatmap in {t_heat:.1f}s, avg slider step {scrub_ms:.1f} ms")
    for c in cards:
        print("----\n" + c)
    print("checkpoint popup:", popup_text)
    print("console errors:", errors or "none")
    browser.close()
