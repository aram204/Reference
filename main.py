import os
import sys
# Configure Playwright to look for browsers in a "local-browsers" directory right next to the executable
# if getattr(sys, 'frozen', False):
#     base_dir = os.path.dirname(sys.executable)
# else:
#     base_dir = os.path.dirname(os.path.abspath(__file__))
# os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(base_dir, "local-browsers")

import time
import json
import random
import tkinter as tk
from tkinter import messagebox, ttk
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from python_ghost_cursor.playwright_sync import create_cursor
from datetime import datetime
import builtins
import os

GLOBAL_WORKER_ID = None

# Override print to include timestamps and optionally write to log file
_original_print = builtins.print
def _timestamped_print(*args, **kwargs):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Write to standard output
    _original_print(f"[{ts}]", *args, **kwargs)
    
    # Write to file if worker ID is known
    if GLOBAL_WORKER_ID:
        try:
            import io
            buf = io.StringIO()
            _original_print(f"[{ts}]", *args, file=buf, **{k:v for k,v in kwargs.items() if k != 'file'})
            
            log_path = f"/var/www/webui/logs/{GLOBAL_WORKER_ID}.txt"
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(buf.getvalue())
        except Exception:
            pass

builtins.print = _timestamped_print

def type_like_human(locator, text):
    locator.focus()
    # Select all and delete to clear existing text safely
    locator.press("Control+A")
    locator.press("Backspace")
    for char in text:
        locator.press(char)
        time.sleep(random.uniform(0.1, 0.35))



def safe_wait_networkidle(page, timeout_ms=3000):
    """Wait for networkidle safely, timing out early to avoid hangs from background scripts (like captcha solvers)."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass

def get_current_page_number(page):
    try:
        # Wait for the spinbutton element to be attached
        page.locator('div[role="spinbutton"][aria-label="Enter page number"]').first.wait_for(state="attached", timeout=5000)
        text = page.locator('div[role="spinbutton"][aria-label="Enter page number"]').first.inner_text().strip()
        if text.isdigit():
            return int(text)
        return None
    except Exception as e:
        print(f"Error reading page number: {e}")
        return None

def get_max_page_number(page):
    """Read the total number of pages from the .data-page-max element's text content."""
    try:
        # Ensure the dynamic record count is loaded first to avoid reading the placeholder '1'
        record_count_el = page.locator('span.data-total-record-count').first
        try:
            record_count_el.wait_for(state="attached", timeout=5000)
            for _ in range(20):
                text = record_count_el.inner_text().strip()
                cleaned = text.replace(",", "").replace(" ", "").strip()
                if cleaned and cleaned.isdigit():
                    break
                page.wait_for_timeout(500)
        except Exception as wait_err:
            print(f"Warning waiting for record count selector: {wait_err}")

        el = page.locator('.data-page-max').first
        el.wait_for(state="attached", timeout=5000)
        raw = el.inner_text().strip()
        if raw:
            # Strip commas/spaces (e.g. "5,990" -> 5990)
            cleaned = raw.replace(",", "").replace(" ", "").strip()
            if cleaned.isdigit():
                val = int(cleaned)
                if val > 0:
                    return val
        return None
    except Exception as e:
        print(f"Error reading max page number: {e}")
        return None

def verify_checkbox(page, cursor, selector, should_be_checked: bool, retries=3, timeout_ms=1000):
    for attempt in range(retries):
        is_checked = page.locator(selector).is_checked()
        if is_checked == should_be_checked:
            print(f"[CHECK PASSED] {selector} is successfully {'checked' if should_be_checked else 'unchecked'}.")
            return True
        print(f"Verification failed (attempt {attempt + 1}/{retries}): {selector} is {'checked' if is_checked else 'unchecked'}. Retrying action...")
        try:
            cursor.click(page.locator(selector))
        except Exception as e:
            print(f"Failed to perform check/uncheck action on {selector}: {e}")
        page.wait_for_timeout(timeout_ms)
    
    # Final check
    if page.locator(selector).is_checked() != should_be_checked:
        raise RuntimeError(f"Checkbox state verification failed: {selector} should be {'checked' if should_be_checked else 'unchecked'}")

def jump_to_page(page, cursor, page_num):
    trigger_div = page.get_by_role("spinbutton", name="Enter page number").first
    cursor.click(trigger_div)
    actual_input = page.get_by_role("textbox", name="Enter page number")
    type_like_human(actual_input, str(page_num))
    actual_input.press("Enter")

def verify_page_change(page, expected_page, action_fn=None, retries=5, delay_ms=1000):
    for attempt in range(retries):
        current = get_current_page_number(page)
        if current == expected_page:
            print(f"[CHECK PASSED] Navigated successfully to expected page {expected_page}.")
            return True
        print(f"Verification failed (attempt {attempt + 1}/{retries}): Expected page {expected_page}, but current page is {current}.")
        if action_fn is not None:
            print("Retrying page navigation action...")
            try:
                action_fn()
            except Exception as e:
                print(f"Error during retry action: {e}")
        page.wait_for_timeout(delay_ms)
    
    # Final check
    current = get_current_page_number(page)
    if current != expected_page:
        raise RuntimeError(f"Page navigation verification failed: Expected page {expected_page}, but current page is {current}")

def verify_previous_10(error_page, cursor, page):
    start_jump_page = ((error_page - 1) // 10) * 10
    if start_jump_page < 1:
        start_jump_page = 1

    print(f"Jumping to page {start_jump_page} and searching backwards for the checked page...")
    jump_to_page(page, cursor, start_jump_page)
    safe_wait_networkidle(page)
    page.wait_for_timeout(3000)
    verify_page_change(page, start_jump_page, action_fn=lambda: jump_to_page(page, cursor, start_jump_page))

    current = start_jump_page
    while current >= 1:
        is_all_checked = page.locator('#checkboxCol').is_checked()
        individual_checked = page.locator('input.action-record-tagged:checked')
        individual_count = individual_checked.count()

        if is_all_checked or individual_count > 0:
            print(f"Page {current}: Found checked checkboxes. Unchecking...")
            if is_all_checked:
                cursor.click(page.locator('#checkboxCol'))
                safe_wait_networkidle(page)
                page.wait_for_timeout(1500)
                verify_checkbox(page, cursor, '#checkboxCol', False)
            
            # Re-check individual boxes in case some are still checked
            individual_checked = page.locator('input.action-record-tagged:checked')
            individual_count = individual_checked.count()
            if individual_count > 0:
                print(f"Unchecking {individual_count} individually checked records...")
                for _ in range(individual_count):
                    try:
                        el = page.locator('input.action-record-tagged:checked').first
                        cursor.click(el)
                        page.wait_for_timeout(300)
                    except Exception as e:
                        print(f"Warning: Could not uncheck individual box: {e}")
                safe_wait_networkidle(page)

            print("Found and cleared the errant page. Returning to error page as requested...")
            break
        else:
            print(f"Page {current}: All checkboxes are already unchecked.")

        if current > 1:
            print(f"Going to previous page (expected {current - 1})...")
            cursor.click(page.locator('div[role="button"][aria-label="Go to previous page"]').first)
            safe_wait_networkidle(page)
            page.wait_for_timeout(2000)
            expected_p = current - 1
            verify_page_change(
                page, expected_p,
                action_fn=lambda ep=expected_p: (
                    cursor.click(page.locator('div[role="button"][aria-label="Go to previous page"]').first)
                )
            )
            current = expected_p
        else:
            break

    print(f"Returning to error page {error_page}...")
    jump_to_page(page, cursor, error_page)
    safe_wait_networkidle(page)
    page.wait_for_timeout(3000)
    verify_page_change(page, error_page, action_fn=lambda: jump_to_page(page, cursor, error_page))

def get_user_inputs_gui():
    inputs = {"target_url": "", "industry": "", "state": ""}
    
    def on_submit():
        url = url_entry.get().strip()
        industry = industry_combo.get().strip()
        state = state_combo.get().strip()
        
        if not url:
            messagebox.showerror("Error", "Target URL cannot be empty.")
            return

        if not industry:
            messagebox.showerror("Error", "Please select an Industry.")
            return

        if not state:
            messagebox.showerror("Error", "Please select a State.")
            return
            
        inputs["target_url"] = url
        inputs["industry"] = industry
        inputs["state"] = state
        root.destroy()  # Close the GUI window and proceed

    root = tk.Tk()
    root.title("ReferenceUSA Export Automation Tool")
    root.geometry("600x400")
    root.resizable(False, False)
    root.configure(bg="#f4f6f9")
    
    # Title
    title_label = tk.Label(
        root, 
        text="ReferenceUSA Export Tool", 
        font=("Helvetica", 16, "bold"), 
        bg="#f4f6f9", 
        fg="#2c3e50"
    )
    title_label.pack(pady=15)
    
    # URL Input Frame
    url_frame = tk.Frame(root, bg="#f4f6f9")
    url_frame.pack(fill="x", padx=40, pady=5)
    
    url_label = tk.Label(
        url_frame, 
        text="Target URL (results page link):", 
        font=("Helvetica", 10), 
        bg="#f4f6f9", 
        fg="#34495e", 
        anchor="w"
    )
    url_label.pack(fill="x")
    
    url_entry = tk.Entry(url_frame, font=("Helvetica", 10), width=50)
    url_entry.pack(fill="x", pady=5)
    url_entry.focus_set()
    

    # Industry Input Frame
    industry_frame = tk.Frame(root, bg="#f4f6f9")
    industry_frame.pack(fill="x", padx=40, pady=5)

    industry_label = tk.Label(
        industry_frame, 
        text="Select Industry:", 
        font=("Helvetica", 10), 
        bg="#f4f6f9", 
        fg="#34495e", 
        anchor="w"
    )
    industry_label.pack(fill="x")

    industries = [
        "Commercial",
        "Industrial",
        "Construction",
        "Oil & Gas",
        "Utility",
        "Mining",
        "Agriculture",
        "Education",
        "Healthcare",
        "Events",
        "Logistics & Warehousing",
        "Others"
    ]
    industry_combo = ttk.Combobox(industry_frame, values=industries, state="readonly", font=("Helvetica", 10), width=30)
    industry_combo.pack(anchor="w", pady=5)
    industry_combo.set(industries[0])

    # State Input Frame
    state_frame = tk.Frame(root, bg="#f4f6f9")
    state_frame.pack(fill="x", padx=40, pady=5)

    state_label = tk.Label(
        state_frame, 
        text="Select State:", 
        font=("Helvetica", 10), 
        bg="#f4f6f9", 
        fg="#34495e", 
        anchor="w"
    )
    state_label.pack(fill="x")

    states = ["NC", "WA", "NM", "TX"]
    state_combo = ttk.Combobox(state_frame, values=states, state="readonly", font=("Helvetica", 10), width=10)
    state_combo.pack(anchor="w", pady=5)
    state_combo.set(states[1]) # Default to "WA"
    
    # Submit Button
    submit_button = tk.Button(
        root, 
        text="Start Automation", 
        font=("Helvetica", 11, "bold"), 
        bg="#3498db", 
        fg="white", 
        activebackground="#2980b9", 
        activeforeground="white",
        command=on_submit,
        cursor="hand2"
    )
    submit_button.pack(pady=20)
    
    root.mainloop()
    
    if not inputs["target_url"] or not inputs["industry"] or not inputs["state"]:
        print("Automation cancelled by user.")
        import sys
        sys.exit(0)
        
    print("\nStarting automation... Opening Chrome...")
    return inputs["target_url"], inputs["industry"], inputs["state"]

def start_browser_session(p, url, login_val, pass_val, target_url, cursor_holder, page_holder, browser_holder, headless=False, profile_name=None, profile_base_dir="/var/www/workers/profiles"):
    """Launch a fresh browser, log in, and navigate to the target URL. Returns (browser, context, page, cursor)."""
    import os
    import shutil
    import tempfile
    print(f"\n=== Starting fresh browser session (headless={headless}) ===")
    
    path_to_extension = r"C:\Users\Aram\AppData\Local\Google\Chrome\User Data\Default\Extensions\dknlfmjaanfblgfdfebhijalfmhmjjjo\0.6.1_0"
    if not os.path.exists(path_to_extension):
        path_to_extension = os.path.abspath("extension_0_6_1")
    
    if profile_name:
        if os.path.exists(profile_base_dir) or os.path.isabs(profile_base_dir):
            user_data_dir = os.path.join(profile_base_dir, profile_name)
        else:
            user_data_dir = os.path.abspath(os.path.join("profiles", profile_name))
        os.makedirs(user_data_dir, exist_ok=True)
        print(f"Using worker profile directory: {user_data_dir}")

        # Clean up any orphaned lockfiles from crashed sessions
        for lock_name in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
            lock_path = os.path.join(user_data_dir, lock_name)
            if os.path.lexists(lock_path):
                try:
                    os.remove(lock_path)
                except Exception:
                    pass
    else:
        # Clean up old orphaned temp profiles from previous crashes to save disk space
        base_tmp = tempfile.gettempdir()
        for item in os.listdir(base_tmp):
            if item.startswith("refusa_ext_profile_"):
                try:
                    shutil.rmtree(os.path.join(base_tmp, item), ignore_errors=True)
                except Exception:
                    pass

        # Generate a temporary profile folder for un-profiled runs
        user_data_dir = tempfile.mkdtemp(prefix="refusa_ext_profile_")
        print(f"Using temporary profile directory: {user_data_dir}")
    
    # Use persistent context to load unpacked extensions
    context = p.chromium.launch_persistent_context(
        user_data_dir,
        headless=headless,
        args=[
            f"--disable-extensions-except={path_to_extension}",
            f"--load-extension={path_to_extension}",
        ],
        ignore_https_errors=True
    )
    # browser object doesn't exist separately when using launch_persistent_context, 
    # so we return the context itself as the browser handle to be closed later
    browser = context 
    Stealth().apply_stealth_sync(context)
    
    page = context.pages[0]
    # Give the extension plenty of time (150 seconds) to solve captchas before failing
    page.set_default_timeout(120000)
    page.set_default_navigation_timeout(120000)
    
    cursor = create_cursor(page)

    # Initialize NopeCHA extension settings and activate subscription key
    setup_url = "https://nopecha.com/setup#_version=0|sub_1U94AaCRwBwvt6ptxI1cGPQS|keys=|enabled=true|disabled_hosts=|input_method=auto|hook_method=auto|mouse_speed=medium|mouse_visualization=true|awscaptcha_auto_open=false|awscaptcha_auto_solve=false|awscaptcha_solve_delay_time=1000|awscaptcha_solve_delay=true|geetest_auto_open=false|geetest_auto_solve=false|geetest_solve_delay_time=1000|geetest_solve_delay=true|funcaptcha_auto_open=false|funcaptcha_auto_solve=false|funcaptcha_solve_delay_time=1000|funcaptcha_solve_delay=true|hcaptcha_auto_open=true|hcaptcha_auto_solve=true|hcaptcha_solve_delay_time=3000|hcaptcha_solve_delay=true|lemincaptcha_auto_open=false|lemincaptcha_auto_solve=false|lemincaptcha_solve_delay_time=1000|lemincaptcha_solve_delay=true|perimeterx_auto_solve=false|perimeterx_solve_delay_time=1000|perimeterx_solve_delay=true|recaptcha_auto_open=false|recaptcha_auto_solve=false|recaptcha_solve_delay_time=2000|recaptcha_solve_delay=true|textcaptcha_auto_solve=false|textcaptcha_image_selector=|textcaptcha_input_selector=|textcaptcha_math_expression=false|textcaptcha_solve_delay_time=100|textcaptcha_solve_delay=true|turnstile_auto_solve=false|turnstile_solve_delay_time=5000|turnstile_solve_delay=true"
    try:
        print("Configuring NopeCHA extension and activating subscription key...")
        page.goto(setup_url, timeout=30000)
        page.wait_for_timeout(2000)
    except Exception as setup_err:
        print(f"Warning: Could not load NopeCHA setup URL: {setup_err}")

    try:
        print(f"Navigating to {target_url}...")
        for attempt in range(3):
            try:
                page.goto(target_url, timeout=60000)
                safe_wait_networkidle(page, 5000)
                break
            except Exception as e:
                print(f"Attempt {attempt + 1} failed for {target_url}: {e}")
                if attempt == 2:
                    raise
                print("Retrying in 5 seconds...")
                time.sleep(5)

        # The library portal will redirect to the login page if not authenticated
        try:
            # Short wait just in case we are already logged in via persistent profile
            page.locator('input[name="user"]').wait_for(timeout=10000)
            
            print("Filling login credentials...")
            type_like_human(page.locator('input[name="user"]'), login_val)
            type_like_human(page.locator('input[name="pass"]'), pass_val)
    
            print("Submitting form...")
            cursor.click(page.locator('input[type="submit"][value="Login"]'))
            
            print("Waiting for login redirect to settle on target URL...")
            safe_wait_networkidle(page, 5000)
            # Wait for a key element on the results page instead of networkidle
            try:
                page.locator('div[role="spinbutton"][aria-label="Enter page number"]').first.wait_for(state="attached", timeout=120000)
            except Exception:
                # If the spinbutton isn't found, it may be a different landing page — just continue
                page.wait_for_timeout(5000)
        except Exception:
            print("No login form found. Assuming already logged in.")

        current_url = page.url
        print(f"Landed on: {current_url}")

        return browser, context, page, cursor
    except Exception as err:
        if 'page' in locals() and page is not None:
            try:
                import os
                worker_name = profile_name or "unknown_worker"
                screenshot_path = f"/var/www/webui/screenshots/{worker_name}.jpg"
                os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
                page.screenshot(path=screenshot_path, type="jpeg")
                print(f"Saved session-start error screenshot to {screenshot_path}")
            except Exception as ss_err:
                print(f"Failed to save session-start error screenshot: {ss_err}")

        try:
            context.close()
            browser.close()
        except Exception:
            pass
        raise err


def close_browser(browser):
    """Safely close the browser, ignoring any errors."""
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass


def resume_to_page(page, cursor, target_page):
    """Navigate to the target_page and verify arrival."""
    print(f"Resuming: jumping to page {target_page}...")
    try:
        jump_to_page(page, cursor, target_page)
        safe_wait_networkidle(page)
        page.wait_for_timeout(3000)
        verify_page_change(page, target_page, action_fn=lambda: jump_to_page(page, cursor, target_page))
    except Exception as e:
        raise e


CHECKPOINT_FILE = "checkpoint.json"

class Checkpoint(dict):
    def __init__(self, data, mirror_path=None, checkpoint_file=CHECKPOINT_FILE):
        super().__init__(data)
        self.mirror_path = mirror_path
        self.checkpoint_file = checkpoint_file

    def save(self):
        try:
            with open(self.checkpoint_file, "w") as f:
                json.dump(self, f, indent=4)
                
            if self.mirror_path:
                os.makedirs(os.path.dirname(os.path.abspath(self.mirror_path)), exist_ok=True)
                with open(self.mirror_path, "w") as f:
                    json.dump(self, f, indent=4)
        except Exception as e:
            print(f"Failed to save checkpoint: {e}")

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self.save()

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.save()

def load_checkpoint(mirror_path=None, checkpoint_file=CHECKPOINT_FILE):
    if os.path.exists(checkpoint_file):
        try:
            with open(checkpoint_file, "r") as f:
                data = json.load(f)
                print(f"Loaded previous checkpoint from {checkpoint_file}.")
                return Checkpoint(data, mirror_path, checkpoint_file)
        except Exception as e:
            print(f"Failed to load checkpoint file: {e}")
            
    checkpoint = Checkpoint({
        "batch": 0,
        "phase": "checking",
        "page_in_batch": 0,
        "absolute_page": None,
        "max_page": None,
        "download_pending": False,
        "start_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, mirror_path, checkpoint_file)
    checkpoint.save()
    return checkpoint

def run():
    import argparse
    parser = argparse.ArgumentParser(description="ReferenceUSA Export Automation")
    parser.add_argument("--url", type=str, help="Target URL (results page)")
    parser.add_argument("--industry", type=str, help="Industry name")
    parser.add_argument("--state", type=str, help="State abbreviation")
    parser.add_argument("--headless", action="store_true", help="Run the browser headlessly")
    parser.add_argument("--worker-id", type=str, help="Worker ID for centralized config routing")
    parser.add_argument("--config-path", type=str, default="config.json", help="Path to centralized config.json")
    parser.add_argument("--status-dir", type=str, default="status", help="Directory for mirroring worker status")
    parser.add_argument("--download-dir", type=str, default="Downloads", help="Base directory for downloading files")
    parser.add_argument("--profile", type=str, help="Worker profile name (defaults to worker-id if set)")
    parser.add_argument("--profile-dir", type=str, default="/var/www/workers/profiles", help="Base directory for worker profiles (default: /var/www/workers/profiles)")
    
    args, unknown = parser.parse_known_args()
    
    mirror_path = None
    url = "http://www.referenceusa.com.lapl.idm.oclc.org"
    login_val = "27244084654949"
    pass_val = "3250"
    worker_id = args.worker_id
    
    global GLOBAL_WORKER_ID
    GLOBAL_WORKER_ID = worker_id or "unknown_worker"
    
    checkpoint_file_name = f"{worker_id}-checkpoint.json" if worker_id else CHECKPOINT_FILE

    profile_name = args.profile or worker_id
    profile_base_dir = args.profile_dir

    if worker_id:
        config_path = args.config_path
        print(f"Loading configuration for worker '{worker_id}' from {config_path}...")
        try:
            with open(config_path, "r") as f:
                configs = json.load(f)
            worker_conf = next((c for c in configs if c.get("worker_id") == worker_id), None)
            if not worker_conf:
                raise ValueError(f"Worker '{worker_id}' not found in {config_path}")
            
            target_url = worker_conf["url"]
            industry = worker_conf["industry"]
            state = worker_conf["state"]
            login_val = worker_conf["login"]
            pass_val = worker_conf["password"]
            headless = args.headless

            if "profile" in worker_conf:
                profile_name = worker_conf["profile"]
            if "profile_dir" in worker_conf:
                profile_base_dir = worker_conf["profile_dir"]
            
            mirror_path = os.path.join(args.status_dir, f"{worker_id}.json")
            print(f"Mirroring checkpoint status to: {mirror_path}")
        except Exception as e:
            print(f"Error loading worker config: {e}")
            import sys
            sys.exit(1)
            
    elif args.url and args.industry and args.state:
        target_url = args.url
        industry = args.industry
        state = args.state
        headless = args.headless
        print(f"Running via CLI arguments:\nTarget URL: {target_url}\nIndustry: {industry}\nState: {state}\nHeadless: {headless}")
    else:
        target_url, industry, state = get_user_inputs_gui()
        headless = False

    folder = os.path.join(args.download_dir, state, industry)
    os.makedirs(folder, exist_ok=True)

    # Load checkpoint from file or start fresh
    checkpoint = load_checkpoint(mirror_path, checkpoint_file=checkpoint_file_name)

    browser = None
    context = None
    page = None
    cursor = None
    is_first_step_of_session = False


    with sync_playwright() as p:
        while True:
            try:
                # Check if we've gone past the last page (max_page known from a previous batch)
                if checkpoint.get("max_page") is not None and checkpoint.get("absolute_page") is not None and checkpoint["absolute_page"] > checkpoint["max_page"]:
                    print("All pages have been processed. Exiting loop.")
                    break

                # ── Ensure a browser session is open ──────────────────────────────
                if browser is None:
                    try:
                        browser, context, page, cursor = start_browser_session(
                            p, url, login_val, pass_val, target_url,
                            None, None, None, headless=headless,
                            profile_name=profile_name, profile_base_dir=profile_base_dir
                        )
                    except Exception as session_err:
                        print(f"[!] Session start failed: {session_err}. Closing and retrying in 10s...")
                        close_browser(browser)
                        browser = None
                        context = None
                        page = None
                        cursor = None
                        time.sleep(10)
                        continue
                    if checkpoint["absolute_page"] is None:
                        # Very first session: read the actual page number the target URL opens on
                        actual_start = get_current_page_number(page)
                        if actual_start is None:
                            raise RuntimeError("Cannot determine the starting page from the target URL. Make sure you are already on the results page.")
                        print(f"Detected starting page from target URL: {actual_start}")
                        checkpoint["absolute_page"] = actual_start
                    else:
                        # Resumed session after crash: navigate to the exact checkpoint page
                        resume_to_page(page, cursor, checkpoint["absolute_page"])
                    is_first_step_of_session = True

                batch = checkpoint["batch"]
                max_known = checkpoint["max_page"] if checkpoint["max_page"] is not None else "?"
                print(f"\n=== Batch {batch + 1} | Max page: {max_known} | Phase: {checkpoint['phase']} | "
                      f"Page in batch: {checkpoint['page_in_batch'] + 1} | "
                      f"Absolute page: {checkpoint['absolute_page']} ===")

                # ── PHASE: CHECKING ───────────────────────────────────────────
                if checkpoint["phase"] == "checking":
                    i = checkpoint["page_in_batch"]

                    # Determine how many pages are actually in this batch
                    # Retry reading max_page to allow CAPTCHAs to solve or page to load
                    max_page = None
                    for attempt in range(6):
                        max_page = get_max_page_number(page)
                        if max_page is not None and max_page > 0:
                            break
                        print(f"[!] Warning: Unable to read valid max_page (attempt {attempt + 1}/6). Waiting 5s for CAPTCHA/page load...")
                        page.wait_for_timeout(5000)

                    if max_page is None or max_page <= 0:
                        raise RuntimeError("Cannot determine total page count (max_page is 0 or None). Possible CAPTCHA or page load failure.")

                    checkpoint["max_page"] = max_page  # persist so outer loop can terminate
                    print(f"Total pages on site: {max_page}")

                    # Check if absolute page exceeds max_page
                    if checkpoint["absolute_page"] > max_page:
                        print(f"Absolute page {checkpoint['absolute_page']} exceeds max page {max_page}. All pages downloaded.")
                        break

                    pages_in_this_batch = min(10, max_page - checkpoint["absolute_page"] + 1)
                    if pages_in_this_batch <= 0:
                        raise RuntimeError(f"Invalid pages_in_this_batch ({pages_in_this_batch}). Checkpoint absolute_page ({checkpoint['absolute_page']}) vs max_page ({max_page}).")

                    print(f"Pages in this batch: {pages_in_this_batch}")

                    while i < pages_in_this_batch:
                        abs_page = checkpoint["absolute_page"]
                        print(f"--- Batch {batch + 1}, Page {i + 1}/{pages_in_this_batch} (site page {abs_page}) ---")

                        # Read actual checkbox state only on the first action of a session to avoid race conditions during normal navigation
                        actual_checked = False
                        if is_first_step_of_session:
                            actual_checked = page.locator('#checkboxCol').is_checked()
                            is_first_step_of_session = False

                        if actual_checked:
                            print("Checkbox already checked (persisted from previous session). Skipping check action.")
                        else:
                            # Update checkpoint BEFORE the risky action
                            checkpoint.update({
                                "phase": "checking",
                                "page_in_batch": i,
                                "absolute_page": abs_page,
                            })
                            print("Checking the 'Select All' checkbox...")
                            cursor.click(page.locator('#checkboxCol'))
                            safe_wait_networkidle(page)
                            page.wait_for_timeout(2000)
                            
                            # Check if the limit exceeded popup is visible
                            popup = page.locator('div.ui-dialog-content', has_text="Your new selections would have exceeded the maximum number of records").first
                            if popup.is_visible():
                                print("Detected maximum selections exceeded popup!")
                                ok_btn = page.locator('a.originButton', has_text="Ok").first
                                cursor.click(ok_btn)
                                page.wait_for_timeout(1000)
                                
                                current_page = get_current_page_number(page)
                                verify_previous_10(current_page, cursor, page)
                                
                                print("Resuming checking from the current page...")
                                continue
                                
                            verify_checkbox(page, cursor, '#checkboxCol', True)

                        # Advance to next page (except after the last page of this batch)
                        is_last_page_of_batch = (i >= pages_in_this_batch - 1)
                        is_last_page_of_site = (abs_page >= max_page)
                        if not is_last_page_of_batch and not is_last_page_of_site:
                            expected_p = abs_page + 1
                            checkpoint.update({
                                "phase": "checking",
                                "page_in_batch": i + 1,
                                "absolute_page": expected_p,
                            })
                            print("Clicking 'Next Page'...")
                            cursor.click(page.locator('div[role="button"][aria-label="Go to next page"]').first)
                            safe_wait_networkidle(page)
                            page.wait_for_timeout(2000)
                            verify_page_change(
                                page, expected_p,
                                action_fn=lambda ep=expected_p: (
                                    cursor.click(page.locator('div[role="button"][aria-label="Go to next page"]').first)
                                )
                            )
                        elif is_last_page_of_site:
                            print(f"Reached the last page of the site ({abs_page}/{max_page}). Stopping pagination.")

                        i += 1

                    # All pages in batch checked — transition to downloading
                    downloaded_pages = get_current_page_number(page)
                    if downloaded_pages is None:
                        downloaded_pages = checkpoint["absolute_page"]

                    checkpoint.update({
                        "phase": "downloading",
                        "page_in_batch": pages_in_this_batch - 1,
                        "absolute_page": downloaded_pages,
                        "download_pending": True,
                    })

                # ── PHASE: DOWNLOADING ────────────────────────────────────────
                if checkpoint["phase"] == "downloading":
                    downloaded_pages = checkpoint["absolute_page"]
                    start_p = max(1, downloaded_pages - 9)
                    print(f"Downloading from page {start_p} to page {downloaded_pages}")

                    print("Clicking the 'Download' button...")
                    cursor.click(page.locator('a.action.download').first)
                    safe_wait_networkidle(page)
                    page.wait_for_timeout(2000)

                    print("Selecting Excel 2007 format...")
                    cursor.click(page.locator('#format_excel_2007'))
                    safe_wait_networkidle(page)
                    page.wait_for_timeout(2000)
                    verify_checkbox(page, cursor, '#format_excel_2007', True)

                    print("Selecting Detail level...")
                    cursor.click(page.locator('#detailDetail'))
                    safe_wait_networkidle(page)
                    page.wait_for_timeout(2000)
                    verify_checkbox(page, cursor, '#detailDetail', True)

                    print("Clicking final download...")
                    with page.expect_download() as download_info:
                        cursor.click(page.locator('a.originButton.action-download'))

                    download = download_info.value
                    suggested = download.suggested_filename
                    print(f"Suggested filename from server: {suggested}")

                    # The first page of this batch = downloaded_pages - (pages_in_this_batch - 1)
                    pages_in_dl_batch = checkpoint["page_in_batch"] + 1
                    save_path = f"{folder}/{industry} {state} {downloaded_pages - pages_in_dl_batch + 1} - {downloaded_pages}.xlsx"
                    download.save_as(save_path)
                    print(f"Downloaded file successfully saved as: {save_path}")

                    # Download done — transition to unchecking phase, starting from last page of this batch
                    checkpoint.update({
                        "phase": "unchecking",
                        "page_in_batch": checkpoint["page_in_batch"],  # already set to pages_in_this_batch - 1
                        "absolute_page": downloaded_pages,
                        "download_pending": False,
                    })

                # ── PHASE: UNCHECKING ─────────────────────────────────────────
                if checkpoint["phase"] == "unchecking":
                    # Go back to the results page first if not already there
                    if not page.locator('#checkboxCol').is_visible():
                        print("Going back to the results page...")
                        page.go_back()
                        safe_wait_networkidle(page)
                        page.wait_for_timeout(2000)

                        # Verify we're back on the results page
                        back_retries = 3
                        for attempt in range(back_retries):
                            try:
                                page.locator('#checkboxCol').wait_for(state="attached", timeout=5000)
                                print("[CHECK PASSED] Successfully returned to results page.")
                                break
                            except Exception as e:
                                print(f"Warning: Could not confirm return to results page (attempt {attempt + 1}/{back_retries}): {e}")
                                if attempt < back_retries - 1:
                                    page.go_back()
                                    safe_wait_networkidle(page)
                                    page.wait_for_timeout(2000)
                        else:
                            raise RuntimeError("Failed to return to results page after multiple retries.")
                    else:
                        print("Already on results page. Skipping 'go_back' action.")

                    u = 9 - checkpoint["page_in_batch"]  # how many pages we've already unchecked
                    pages_to_uncheck = checkpoint["page_in_batch"] + 1  # remaining pages to uncheck
                    abs_page = checkpoint["absolute_page"]

                    print("Unchecking all checked pages...")
                    for step in range(pages_to_uncheck):
                        print(f"Unchecking site page {abs_page} (step {step + 1}/{pages_to_uncheck})...")

                        # Read actual checkbox state only on the first action of a session to avoid race conditions during normal navigation
                        actual_checked = True
                        if is_first_step_of_session:
                            actual_checked = page.locator('#checkboxCol').is_checked()
                            is_first_step_of_session = False

                        if not actual_checked:
                            print("Checkbox already unchecked (persisted from previous session). Skipping uncheck action.")
                        else:
                            checkpoint.update({
                                "phase": "unchecking",
                                "page_in_batch": checkpoint["page_in_batch"],
                                "absolute_page": abs_page,
                            })
                            cursor.click(page.locator('#checkboxCol'))
                            safe_wait_networkidle(page)
                            page.wait_for_timeout(2000)
                            verify_checkbox(page, cursor, '#checkboxCol', False)

                        # Navigate to previous page (except after last uncheck)
                        if step < pages_to_uncheck - 1:
                            expected_p = abs_page - 1
                            checkpoint.update({
                                "phase": "unchecking",
                                "page_in_batch": checkpoint["page_in_batch"] - 1,
                                "absolute_page": expected_p,
                            })
                            print("Clicking 'Previous Page'...")
                            cursor.click(page.locator('div[role="button"][aria-label="Go to previous page"]').first)
                            safe_wait_networkidle(page)
                            page.wait_for_timeout(2000)
                            verify_page_change(
                                page, expected_p,
                                action_fn=lambda ep=expected_p: (
                                    cursor.click(page.locator('div[role="button"][aria-label="Go to previous page"]').first)
                                )
                            )
                            abs_page = expected_p

                    print("All pages unchecked successfully.")

                # ── BATCH COMPLETE ─────────────────────────────────────────────
                batch += 1
                next_abs_page = checkpoint["absolute_page"] + 10
                checkpoint.update({
                    "batch": batch,
                    "phase": "checking",
                    "page_in_batch": 0,
                    "absolute_page": next_abs_page,
                    "download_pending": False,
                })

                # Navigate to next batch start page only if there are more pages to process
                if checkpoint["max_page"] is None or next_abs_page <= checkpoint["max_page"]:
                    print(f"Navigating to page {next_abs_page} for next batch...")
                    jump_to_page(page, cursor, next_abs_page)
                    safe_wait_networkidle(page)
                    page.wait_for_timeout(5000)
                    verify_page_change(page, next_abs_page, action_fn=lambda np=next_abs_page: jump_to_page(page, cursor, np))
                else:
                    print(f"Page {next_abs_page} exceeds max page {checkpoint['max_page']}. All pages downloaded.")

            except Exception as err:

                # Non-captcha error - log it and restart browser
                print(f"\n!!! Error at batch {checkpoint.get('batch', 0) + 1}, phase '{checkpoint.get('phase', 'unknown')}', "
                      f"page_in_batch {checkpoint.get('page_in_batch', 0) + 1}, "
                      f"absolute_page {checkpoint.get('absolute_page', '?')}: {err}")
                
                if page is not None:
                    try:
                        worker_name = getattr(args, "worker_id", None) or "unknown_worker"
                        screenshot_path = f"/var/www/webui/screenshots/{worker_name}.jpg"
                        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
                        page.screenshot(path=screenshot_path, type="jpeg")
                        print(f"Saved error screenshot to {screenshot_path}")
                    except Exception as ss_err:
                        print(f"Failed to save error screenshot: {ss_err}")

                print("Checkpoint saved. Closing browser and restarting from the exact failed step...")
                close_browser(browser)
                browser = None
                context = None
                page = None
                cursor = None
                time.sleep(2)
                # Loop continues: browser is None so it will restart, then resume from checkpoint

        print("All pages downloaded successfully. Starting verification and repair phase...")
        from repair_downloads import find_and_delete_bad_files, repair_batches
        batches = find_and_delete_bad_files(folder_path=folder)
        if batches:
            print(f"Found {len(batches)} batches needing repair. Running repair...")
            repair_batches(batches, target_url, headless=headless, login_val=login_val, pass_val=pass_val, worker_id=worker_id, status_dir=args.status_dir, folder=folder)
        else:
            print("Verification passed! No bad files found.")

        print("Updating checkpoint phase to 'completed'...")
        checkpoint.update({
            "phase": "completed",
            "end_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        
        time.sleep(5)
        close_browser(browser)

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    run()
