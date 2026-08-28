"""
solve_captcha.py
================
Standalone login + hCaptcha auto-solver for the ReferenceUSA / LAPL portal.

Flow
----
1. Launch Playwright (stealth) browser.
2. Navigate to the login page and fill credentials.
3. Submit the form and wait for the captcha page.
4. Detect the hCaptcha widget (#captchaValidation).
5. Click the hCaptcha checkbox inside the iframe to trigger the image challenge.
6. Scrape the challenge image(s) and question text from the challenge iframe.
7. Send to CaptchaSonic API -> receive solved answers.
8. Apply the answers (click tiles / coordinates / drag) inside the iframe,
   then hit Verify to close the challenge.
9. Retry up to MAX_SOLVE_RETRIES if the attempt fails.
10. Fall back to manual page.pause() if all automated attempts are exhausted.

CaptchaSonic API key
--------------------
The key is stored in CAPTCHASONIC_API_KEY below.
You can also override it per-worker by adding "captchasonic_api_key" to config.json.
"""

import os
import sys
import time
import json
import base64
import random
import requests

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from python_ghost_cursor.playwright_sync import create_cursor

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CAPTCHASONIC_API_KEY  = "sonic_P3xIBX3LpazHzlqd2V0h1dZB"
CAPTCHASONIC_ENDPOINT = "https://api.captchasonic.com/createTask"

LOGIN_URL     = "http://www.referenceusa.com.lapl.idm.oclc.org"
DEFAULT_LOGIN = "27244084654949"
DEFAULT_PASS  = "3250"

MAX_SOLVE_RETRIES = 3
CAPTCHA_WAIT_MS   = 8_000
CHALLENGE_WAIT_MS = 6_000
NETWORK_IDLE_MS   = 60_000


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def load_config(path="config.json"):
    """Optionally load credentials / API key from config.json."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data[0] if data else {}
        return data
    except Exception as e:
        print(f"[WARN] Could not read {path}: {e}")
        return {}


def type_like_human(locator, text):
    locator.focus()
    locator.press("Control+A")
    locator.press("Backspace")
    for char in text:
        locator.press(char)
        time.sleep(random.uniform(0.08, 0.28))


# ---------------------------------------------------------------------------
# CaptchaSonic API wrappers
# ---------------------------------------------------------------------------

def _post_sonic(payload: dict) -> dict:
    resp = requests.post(CAPTCHASONIC_ENDPOINT, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def solve_classify(question: str, images_b64: list) -> list:
    """Grid/Classify: returns list of bool aligned with images_b64."""
    data = _post_sonic({
        "apiKey": CAPTCHASONIC_API_KEY,
        "task": {
            "type": "PopularCaptchaImage",
            "questionType": "objectClassify",
            "question": question,
            "queries": images_b64,
        }
    })
    if data.get("code") != 200:
        raise RuntimeError(f"CaptchaSonic error (classify): {data}")
    return data["answers"]


def solve_click(question: str, image_b64: str) -> list:
    """Click: returns list of {x, y} dicts."""
    data = _post_sonic({
        "apiKey": CAPTCHASONIC_API_KEY,
        "task": {
            "type": "PopularCaptchaImage",
            "questionType": "objectClick",
            "question": question,
            "queries": [image_b64],
        }
    })
    if data.get("code") != 200:
        raise RuntimeError(f"CaptchaSonic error (click): {data}")
    answers = data.get("answers", [[]])
    return answers[0] if answers else []


def solve_drag(question: str, background_b64: str, piece_b64: str) -> dict:
    """Drag & Drop: returns {start: [x,y], end: [x,y]}."""
    data = _post_sonic({
        "apiKey": CAPTCHASONIC_API_KEY,
        "task": {
            "type": "PopularCaptchaImage",
            "questionType": "objectDrag",
            "question": question,
            "queries": [background_b64],
            "examples": [piece_b64],
        }
    })
    if data.get("code") != 200:
        raise RuntimeError(f"CaptchaSonic error (drag): {data}")
    answers = data.get("answers", [[{}]])
    return answers[0][0] if answers and answers[0] else {}


# ---------------------------------------------------------------------------
# hCaptcha frame helpers
# ---------------------------------------------------------------------------

def _get_checkbox_frame(page):
    """FrameLocator for the hCaptcha checkbox iframe."""
    return page.frame_locator('iframe[title*="hCaptcha"]').first


def _get_challenge_frame(page):
    """
    FrameLocator for the hCaptcha image-challenge popup iframe.
    Returns None if it does not appear within the timeout.
    """
    print("  -> Searching for challenge frame iframe[title*='hCaptcha challenge']...")
    try:
        fl = page.frame_locator('iframe[title*="hCaptcha challenge"]').first
        print("  -> Found frame, waiting for body to attach...")
        fl.locator("body").wait_for(state="attached", timeout=5000)
        print("  -> Challenge frame body attached.")
        return fl
    except Exception as e:
        print(f"  [WARN] Failed to get challenge frame: {e}")
        return None


def _read_question(challenge_frame) -> str:
    print("  -> Reading challenge question...")
    for sel in ['.prompt-text', '.challenge-prompt', '[class*="prompt"]',
                'h2', '.task-description']:
        print(f"    -> Trying selector: {sel}")
        try:
            el = challenge_frame.locator(sel).first
            el.wait_for(state="attached", timeout=2000)
            text = el.inner_text().strip()
            if text:
                print(f"    -> Success: Found question text: '{text}'")
                return text
            else:
                print(f"    -> Found element, but text is empty.")
        except Exception as e:
            print(f"    -> Failed with selector {sel}: {e}")
            continue
    print("  [WARN] Failed to find question text. Falling back to default.")
    return "Please select all matching images"


def _detect_type(challenge_frame) -> str:
    """Returns 'objectClassify', 'objectClick', or 'objectDrag'."""
    drag_sels = ['.drag', 'canvas', '.drag-drop', '.puzzle']
    for sel in drag_sels:
        try:
            if challenge_frame.locator(sel).count() > 0:
                return "objectDrag"
        except Exception:
            pass

    click_sels = ['.task-image .image', '.challenge-container > img']
    for sel in click_sels:
        try:
            if challenge_frame.locator(sel).count() == 1:
                return "objectClick"
        except Exception:
            pass

    return "objectClassify"


def _collect_grid_images(challenge_frame) -> list:
    """Screenshot each grid tile, return list of base64 strings."""
    tile_sels = [
        '.task-image .image',
        '.challenge-container .image',
        'div.image-item img',
        '.task-wrapper img',
        '.image-holder img',
        'img.challenge-image',
    ]
    for sel in tile_sels:
        try:
            count = challenge_frame.locator(sel).count()
            if count > 0:
                print(f"  -> Found {count} grid tile(s) [{sel}]")
                result = []
                for i in range(count):
                    raw = challenge_frame.locator(sel).nth(i).screenshot()
                    result.append(base64.b64encode(raw).decode())
                return result
        except Exception:
            continue
    # Fallback: screenshot entire challenge body
    try:
        raw = challenge_frame.locator("body").screenshot()
        return [base64.b64encode(raw).decode()]
    except Exception as e:
        print(f"  [ERROR] Screenshot fallback failed: {e}")
        return []


def _collect_single_image(challenge_frame) -> str:
    """Screenshot single large image for click/drag challenges."""
    single_sels = [
        '.task-image .image',
        '.challenge-container > img',
        '.challenge-image',
        'img',
    ]
    for sel in single_sels:
        try:
            el = challenge_frame.locator(sel).first
            el.wait_for(state="visible", timeout=2000)
            raw = el.screenshot()
            return base64.b64encode(raw).decode()
        except Exception:
            continue
    raw = challenge_frame.locator("body").screenshot()
    return base64.b64encode(raw).decode()


def _apply_classify(page, challenge_frame, answers: list):
    """Click the tiles whose answer is True."""
    tile_sels = [
        '.task-image .image',
        '.challenge-container .image',
        'div.image-item img',
        '.task-wrapper img',
        '.image-holder img',
        'img.challenge-image',
    ]
    for sel in tile_sels:
        try:
            count = challenge_frame.locator(sel).count()
            if count == len(answers):
                for i, should_click in enumerate(answers):
                    if should_click:
                        challenge_frame.locator(sel).nth(i).click()
                        page.wait_for_timeout(random.randint(150, 350))
                return
        except Exception:
            continue
    print("  [WARN] Could not match tile count to answers for classify.")


def _apply_click(page, challenge_frame, coords: list):
    """Click at each {x, y} coordinate inside the challenge image."""
    img_sel = '.task-image .image, .challenge-container > img, .challenge-image, img'
    for coord in coords:
        try:
            challenge_frame.locator(img_sel).first.click(
                position={"x": coord["x"], "y": coord["y"]}
            )
            page.wait_for_timeout(random.randint(150, 350))
        except Exception as e:
            print(f"  [WARN] Click at {coord} failed: {e}")


def _submit_challenge(challenge_frame):
    """Click the Verify/Submit button inside the challenge iframe."""
    submit_sels = [
        'button.button-submit',
        'button[type="submit"]',
        '.button-submit',
        'button:has-text("Verify")',
        'button:has-text("Submit")',
        '.verify-button',
    ]
    for sel in submit_sels:
        try:
            btn = challenge_frame.locator(sel).first
            if btn.is_visible():
                btn.click()
                print("  -> Clicked submit/verify button.")
                return
        except Exception:
            continue
    print("  [WARN] Could not find challenge submit button.")


def _is_solved(page) -> bool:
    """Return True ONLY if the response token is filled."""
    try:
        for name in ['h-captcha-response', 'g-recaptcha-response']:
            val = page.locator(f'textarea[name="{name}"]').first.input_value()
            if val and len(val) > 10:
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Main solver entry point
# ---------------------------------------------------------------------------

def solve_hcaptcha(page, cursor) -> bool:
    """
    Attempt to automatically solve the hCaptcha via CaptchaSonic.
    Returns True on success, False if all retries exhausted.
    """
    for attempt in range(1, MAX_SOLVE_RETRIES + 1):
        print(f"\n[CaptchaSolver] Attempt {attempt}/{MAX_SOLVE_RETRIES}")
        try:
            # ── Step 0: click somewhere neutral to dismiss any open popup ──
            print("  -> Dismissing any open popup (clicking neutral area)...")
            try:
                page.mouse.click(10, 10)
            except Exception:
                pass
            page.wait_for_timeout(1000)

            # ── Step 1: click the checkbox ─────────────────────────────────
            print("  -> Clicking hCaptcha checkbox...")
            checkbox_frame = _get_checkbox_frame(page)
            checkbox = checkbox_frame.locator('#checkbox')
            checkbox.wait_for(state="visible", timeout=10_000)
            try:
                cursor.click(checkbox)
            except Exception:
                checkbox.click()
            page.wait_for_timeout(2000)

            # ── Step 2: wait for challenge popup ───────────────────────────
            page.wait_for_timeout(CHALLENGE_WAIT_MS)
            challenge_frame = _get_challenge_frame(page)

            if challenge_frame is None:
                if _is_solved(page):
                    print("  [OK] No popup appeared — challenge auto-passed.")
                    return True
                print("  [WARN] No challenge popup and not solved. Retrying.")
                page.wait_for_timeout(2000)
                continue

            # ── Step 3: read question & detect type ────────────────────────
            question = _read_question(challenge_frame)
            challenge_type = _detect_type(challenge_frame)
            print(f"  -> Question : {question}")
            print(f"  -> Type     : {challenge_type}")

            # ── Step 4: collect images & call API ──────────────────────────
            if challenge_type == "objectClassify":
                images_b64 = _collect_grid_images(challenge_frame)
                if not images_b64:
                    print("  [WARN] No images collected. Retrying.")
                    continue
                print(f"  -> Sending {len(images_b64)} tile(s) to CaptchaSonic...")
                answers = solve_classify(question, images_b64)
                print(f"  -> Answers  : {answers}")
                _apply_classify(page, challenge_frame, answers)

            elif challenge_type == "objectClick":
                image_b64 = _collect_single_image(challenge_frame)
                print("  -> Sending click-image to CaptchaSonic...")
                coords = solve_click(question, image_b64)
                print(f"  -> Coords   : {coords}")
                _apply_click(page, challenge_frame, coords)

            elif challenge_type == "objectDrag":
                bg_b64 = _collect_single_image(challenge_frame)
                piece_b64 = bg_b64  # fallback
                try:
                    raw = challenge_frame.locator(
                        '.challenge-piece, .puzzle-piece, canvas'
                    ).first.screenshot()
                    piece_b64 = base64.b64encode(raw).decode()
                except Exception:
                    pass
                print("  -> Sending drag images to CaptchaSonic...")
                drag = solve_drag(question, bg_b64, piece_b64)
                print(f"  -> Drag     : {drag}")
                try:
                    body = challenge_frame.locator("body")
                    start = drag.get("start", [0, 0])
                    end   = drag.get("end",   [0, 0])
                    body.drag_to(
                        body,
                        source_position={"x": start[0], "y": start[1]},
                        target_position={"x": end[0],   "y": end[1]},
                    )
                except Exception as e:
                    print(f"  [WARN] Drag action failed: {e}")

            page.wait_for_timeout(1000)

            # ── Step 5: submit and check ───────────────────────────────────
            _submit_challenge(challenge_frame)
            page.wait_for_timeout(3000)

            if _is_solved(page):
                print("  [OK] Captcha solved!")
                return True

            print(f"  [WARN] Not solved after attempt {attempt}. Trying to refresh captcha...")
            # Try clicking the captcha refresh/reload button to get a new challenge
            try:
                refresh_sels = [
                    'button[aria-label*="refresh"]',
                    'button[aria-label*="Refresh"]',
                    '.refresh',
                    '[data-action="reload"]',
                ]
                refreshed = False
                for sel in refresh_sels:
                    try:
                        btn = challenge_frame.locator(sel).first if challenge_frame else None
                        if btn and btn.is_visible():
                            btn.click()
                            print("  -> Clicked captcha refresh button.")
                            refreshed = True
                            break
                    except Exception:
                        continue
                if not refreshed:
                    print("  -> No refresh button found; will retry from checkbox.")
            except Exception:
                pass
            page.wait_for_timeout(2000)

        except Exception as e:
            print(f"  [ERROR] Exception on attempt {attempt}: {e}")
            page.wait_for_timeout(2000)

    print(f"\n[CaptchaSolver] All {MAX_SOLVE_RETRIES} attempts exhausted.")
    return False


def check_for_captcha_auto(page, cursor) -> bool:
    """
    Detects hCaptcha by checking if the captcha response textarea exists
    with an empty value (meaning an unsolved captcha is blocking the page).
    If found, attempts automated solving via CaptchaSonic.
    Returns True if a captcha was detected and solved successfully.
    Returns False if no captcha, already solved, or solving failed.
    Does NOT restart the session — the caller should just continue.
    """
    # ── Detection: check if captcha response textarea exists ──────────────
    needs_solving = False
    try:
        for name in ['h-captcha-response', 'g-recaptcha-response']:
            textarea = page.locator(f'textarea[name="{name}"]')
            if textarea.count() > 0:
                val = textarea.first.input_value()
                if not val or len(val) < 10:
                    # Textarea exists but empty → captcha is pending
                    needs_solving = True
                    print(f"  [DETECT] Found unsolved captcha (textarea '{name}' is empty).")
                    break
                else:
                    # Textarea exists and has a token → already solved
                    return False
    except Exception:
        pass

    if not needs_solving:
        return False

    print("\n!!! hCaptcha detected — attempting automated solve (CaptchaSonic)...")

    solved = solve_hcaptcha(page, cursor)

    if solved:
        print(">>> Captcha solved successfully! Continuing from where we left off...")
        # Wait for the page to process the solved captcha and load content
        try:
            page.wait_for_timeout(3000)
            page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_MS)
        except Exception:
            pass
    else:
        print(">>> Automated solve failed after all attempts.")

    return solved


# ---------------------------------------------------------------------------
# Login / session bootstrap
# ---------------------------------------------------------------------------

def start_session(p, login_url: str, login_val: str, pass_val: str,
                  headless: bool = False):
    """
    Launch a stealth Playwright browser, navigate to the login page,
    fill credentials, submit, and handle any captcha.

    Returns (browser, context, page, cursor).
    """
    print(f"\n=== Starting browser session (headless={headless}) ===")
    browser = p.chromium.launch(headless=headless)
    context = browser.new_context(ignore_https_errors=True)
    Stealth().apply_stealth_sync(context)
    page = context.new_page()
    cursor = create_cursor(page)

    print(f"Navigating to {login_url} ...")
    for attempt in range(3):
        try:
            page.goto(login_url, timeout=60_000)
            page.wait_for_load_state("networkidle", timeout=60_000)
            check_for_captcha_auto(page, cursor)
            break
        except Exception as e:
            print(f"  [WARN] Navigation attempt {attempt + 1}/3 failed: {e}")
            if attempt == 2:
                raise
            time.sleep(5)

    print("Filling login credentials...")
    try:
        type_like_human(page.locator('input[name="user"]'), login_val)
        type_like_human(page.locator('input[name="pass"]'), pass_val)
    except Exception as e:
        raise RuntimeError(f"Could not fill login form: {e}")

    print("Submitting...")
    try:
        cursor.click(page.locator('input[type="submit"][value="Login"]'))
    except Exception:
        page.locator('input[type="submit"][value="Login"]').click()

    page.wait_for_load_state("networkidle", timeout=60_000)
    check_for_captcha_auto(page, cursor)

    print(f"Logged in. Current URL: {page.url}")
    page.wait_for_timeout(3000)
    return browser, context, page, cursor


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    from urllib.parse import urlparse

    parser = argparse.ArgumentParser(
        description="ReferenceUSA hCaptcha auto-solver (CaptchaSonic)"
    )
    parser.add_argument("--url",      type=str, help="Login base URL")
    parser.add_argument("--login",    type=str, help="Username / library card")
    parser.add_argument("--password", type=str, help="PIN / password")
    parser.add_argument("--config",   type=str, default="config.json")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--navigate", type=str, default=None,
                        help="Navigate to this URL after login")
    args = parser.parse_args()

    cfg = load_config(args.config)

    global CAPTCHASONIC_API_KEY
    if cfg.get("captchasonic_api_key"):
        CAPTCHASONIC_API_KEY = cfg["captchasonic_api_key"]

    raw_url   = args.url or cfg.get("url", LOGIN_URL)
    parsed    = urlparse(raw_url)
    login_url = f"{parsed.scheme}://{parsed.netloc}"

    login_val = args.login    or cfg.get("login", DEFAULT_LOGIN)
    pass_val  = args.password or cfg.get("password", DEFAULT_PASS)

    print("=" * 60)
    print("  ReferenceUSA hCaptcha Auto-Solver  [CaptchaSonic]")
    print("=" * 60)
    print(f"  Login URL  : {login_url}")
    print(f"  Username   : {login_val}")
    print(f"  API Key    : {CAPTCHASONIC_API_KEY[:16]}...")
    print(f"  Headless   : {args.headless}")
    print("=" * 60)

    with sync_playwright() as p:
        browser, context, page, cursor = start_session(
            p, login_url, login_val, pass_val, headless=args.headless
        )

        target = args.navigate or (raw_url if raw_url != login_url else None)
        if target:
            print(f"\nNavigating to target URL: {target}")
            for attempt in range(3):
                try:
                    page.goto(target, timeout=60_000)
                    page.wait_for_load_state("networkidle", timeout=60_000)
                    check_for_captcha_auto(page, cursor)
                    break
                except Exception as e:
                    print(f"  [WARN] Attempt {attempt + 1}/3 failed: {e}")
                    if attempt == 2:
                        raise
                    time.sleep(5)

        print("\n[OK] Session ready. Press Ctrl+C to exit.\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

        browser.close()
        print("Browser closed.")


if __name__ == "__main__":
    main()
