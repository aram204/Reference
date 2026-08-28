import os
import json
import time
import random
from playwright.sync_api import sync_playwright
from python_ghost_cursor.playwright_sync import create_cursor

def type_like_human(locator, text):
    """Simulate human typing."""
    locator.focus()
    for char in text:
        locator.press(char)
        time.sleep(random.uniform(0.1, 0.35))

def test_extension():
    print("Loading config.json...")
    try:
        with open("config.json", "r") as f:
            config = json.load(f)[0]
        login_val = config.get("login", "")
        pass_val = config.get("password", "")  # In config.json it's "password"
        target_url = config.get("url", "")     # In config.json it's "url"
    except Exception as e:
        print(f"Error loading config.json: {e}")
        return

    start_url = "http://www.referenceusa.com.lapl.idm.oclc.org"

    # Absolute path to the unpacked extension folder
    path_to_extension = r"C:\Users\Aram\AppData\Local\Google\Chrome\User Data\Default\Extensions\dknlfmjaanfblgfdfebhijalfmhmjjjo\0.6.1_0"
    
    # Playwright requires launch_persistent_context to load unpacked extensions.
    user_data_dir = os.path.abspath("extension_user_data")
    
    with sync_playwright() as p:
        print("Launching browser with extension installed...")
        context = p.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            args=[
                f"--disable-extensions-except={path_to_extension}",
                f"--load-extension={path_to_extension}",
            ],
            ignore_https_errors=True
        )
        
        # When using launch_persistent_context, a default page is already open
        page = context.pages[0]
        cursor = create_cursor(page)
        
        try:
            print(f"Navigating to login page: {start_url}...")
            for attempt in range(3):
                try:
                    page.goto(start_url, timeout=60000)
                    page.wait_for_load_state("networkidle")
                    break
                except Exception as e:
                    print(f"Attempt {attempt + 1} failed for {start_url}: {e}")
                    if attempt == 2:
                        raise
                    print("Retrying in 5 seconds...")
                    time.sleep(5)
            
            print("Filling login credentials...")
            type_like_human(page.locator('input[name="user"]'), login_val)
            type_like_human(page.locator('input[name="pass"]'), pass_val)

            print("Submitting form...")
            cursor.click(page.locator('input[type="submit"][value="Login"]'))
            page.wait_for_load_state("networkidle")
            
            print("Waiting for session to settle (5 seconds)...")
            time.sleep(5)
            current_url = page.url
            print(f"Landed on: {current_url}")
            
            print(f"Navigating to target URL: {target_url}...")
            for attempt in range(3):
                try:
                    page.goto(target_url, timeout=60000)
                    page.wait_for_load_state("networkidle")
                    break
                except Exception as e:
                    print(f"Attempt {attempt + 1} failed for target {target_url}: {e}")
                    if attempt == 2:
                        raise
                    print("Retrying in 5 seconds...")
                    time.sleep(5)
            page.wait_for_load_state("networkidle")
            
            print("\n=======================================================")
            print("Landed on target page!")
            print("Leaving browser open for 3 minutes.")
            print("Please watch the browser window to see if the extension")
            print("automatically solves the captcha.")
            print("=======================================================\n")
            
            # Keep browser open for a while so you can test if the extension works
            time.sleep(180)
            
        except Exception as e:
            print(f"An error occurred: {e}")
        finally:
            print("Closing browser...")
            context.close()

if __name__ == "__main__":
    test_extension()
