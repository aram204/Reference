import os
import shutil
from playwright.sync_api import sync_playwright
import time

def test_extension_settings():
    path_to_extension = r"C:\Users\Aram\AppData\Local\Google\Chrome\User Data\Default\Extensions\dknlfmjaanfblgfdfebhijalfmhmjjjo\0.6.1_0"
    user_data_dir = os.path.abspath("test_extension_profile")
    
    # Clean up old profile to avoid ProcessSingleton lock errors
    if os.path.exists(user_data_dir):
        try:
            shutil.rmtree(user_data_dir)
        except Exception as e:
            print(f"Warning: Could not remove old profile dir: {e}")
            
    with sync_playwright() as p:
        print("Launching persistent context...")
        context = p.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            args=[
                f"--disable-extensions-except={path_to_extension}",
                f"--load-extension={path_to_extension}",
            ],
        )
        
        # Wait for the service worker to initialize
        print("Waiting for extension service worker to start...")
        while not context.service_workers:
            context.pages[0].wait_for_timeout(500)
            
        worker = context.service_workers[0]
        
        # 1. Define all your settings as a Python dictionary
        # (Note: Python uses True/False with capital letters, unlike JavaScript's true/false)
        captcha_settings = {
            "awscaptcha_auto_open": False,
            "awscaptcha_auto_solve": False,
            "awscaptcha_solve_delay": True,
            "awscaptcha_solve_delay_time": 1000,
            "disabled_hosts": [],
            "enabled": True,    
            "funcaptcha_auto_open": False,
            "funcaptcha_auto_solve": False,
            "funcaptcha_solve_delay": True,
            "funcaptcha_solve_delay_time": 1000,
            "geetest_auto_open": False,
            "geetest_auto_solve": False,
            "geetest_solve_delay": True,
            "geetest_solve_delay_time": 1000,
            "hcaptcha_auto_open": True,
            "hcaptcha_auto_solve": True,
            "hcaptcha_solve_delay": True,
            "hcaptcha_solve_delay_time": 3000,
            "hook_method": "auto",
            "input_method": "auto",
            "key": "sub_1U8uHlCRwBwvt6ptoXPACFIE",  # Your API key
            "keys": [],
            "lemincaptcha_auto_open": False,
            "lemincaptcha_auto_solve": False,
            "lemincaptcha_solve_delay": True,
            "lemincaptcha_solve_delay_time": 1000,
            "mouse_speed": "medium",
            "mouse_visualization": True,
            "perimeterx_auto_solve": False,
            "perimeterx_solve_delay": True,
            "perimeterx_solve_delay_time": 1000,
            "recaptcha_auto_open": True,
            "recaptcha_auto_solve": True,
            "recaptcha_solve_delay": True,
            "recaptcha_solve_delay_time": 2000,
            "textcaptcha_auto_solve": False,
            "textcaptcha_image_selector": "",
            "textcaptcha_input_selector": "",
            "textcaptcha_math_expression": False,
            "textcaptcha_solve_delay": True,
            "textcaptcha_solve_delay_time": 100,
            "turnstile_auto_solve": True,
            "turnstile_solve_delay": True,
            "turnstile_solve_delay_time": 5000,
            "_version": 0
        }

        # 2. Option A: Navigate page to the official setup URL
        setup_url = "https://nopecha.com/setup#_version=0|sub_1U8uHlCRwBwvt6ptoXPACFIE|keys=|enabled=true|disabled_hosts=|input_method=auto|hook_method=auto|mouse_speed=medium|mouse_visualization=true|awscaptcha_auto_open=false|awscaptcha_auto_solve=false|awscaptcha_solve_delay_time=1000|awscaptcha_solve_delay=true|geetest_auto_open=false|geetest_auto_solve=false|geetest_solve_delay_time=1000|geetest_solve_delay=true|funcaptcha_auto_open=false|funcaptcha_auto_solve=false|funcaptcha_solve_delay_time=1000|funcaptcha_solve_delay=true|hcaptcha_auto_open=true|hcaptcha_auto_solve=true|hcaptcha_solve_delay_time=3000|hcaptcha_solve_delay=true|lemincaptcha_auto_open=false|lemincaptcha_auto_solve=false|lemincaptcha_solve_delay_time=1000|lemincaptcha_solve_delay=true|perimeterx_auto_solve=false|perimeterx_solve_delay_time=1000|perimeterx_solve_delay=true|recaptcha_auto_open=true|recaptcha_auto_solve=true|recaptcha_solve_delay_time=2000|recaptcha_solve_delay=true|textcaptcha_auto_solve=false|textcaptcha_image_selector=|textcaptcha_input_selector=|textcaptcha_math_expression=false|textcaptcha_solve_delay_time=100|textcaptcha_solve_delay=true|turnstile_auto_solve=true|turnstile_solve_delay_time=5000|turnstile_solve_delay=true"
        
        page = context.pages[0]
        print("Navigating to NopeCHA setup URL to import settings and activate key...")
        page.goto(setup_url)
        page.wait_for_timeout(3000)

        # Option B: Also trigger settings::update event via service worker messaging to guarantee activation
        worker.evaluate(
            """async (settings) => {
                return await new Promise((resolve) => {
                    chrome.runtime.sendMessage(["0", "settings::update", settings], (res) => resolve(res));
                });
            }""",
            captcha_settings
        )

        print("Settings imported and subscription key activated successfully!")

        # Verify storage
        result = worker.evaluate("async () => await chrome.storage.local.get('settings')")
        print("Result from extension storage:", result.get('settings', {}))
        time.sleep(1000000)
        print("Closing context...")
        context.close()

if __name__ == "__main__":
    test_extension_settings()
