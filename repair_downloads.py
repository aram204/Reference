import os
import re
import glob
import time
import json
import openpyxl
from playwright.sync_api import sync_playwright

# Import core browser control functions from main.py
from main import (
    start_browser_session,
    jump_to_page,
    verify_checkbox,
    verify_page_change,
    get_current_page_number,
    close_browser,
    resume_to_page
)

def get_target_url():
    url = input("Enter the target URL (results page link) for the repairs: ").strip()
    if not url:
        print("URL cannot be empty.")
        import sys
        sys.exit(1)
    return url

def find_and_delete_bad_files(folder_path="Downloads", expected_rows=251):
    print(f"Scanning for bad Excel files in '{folder_path}' folder...\n")
    abs_folder = os.path.abspath(folder_path)
    
    if not os.path.exists(abs_folder):
        print(f"Error: Folder '{folder_path}' does not exist.")
        return []

    excel_files = glob.glob(os.path.join(abs_folder, "*.xlsx"))
    if not excel_files:
        print("No Excel (.xlsx) files found.")
        return []

    batches_to_repair = []
    
    # Regex to parse filenames like "Healthcare WA 231 - 240.xlsx"
    pattern = re.compile(r"^(.*?)\s+([A-Z]{2})\s+(\d+)\s*-\s*(\d+)\.xlsx$")

    # First pass: find the maximum end_page to identify the final batch
    max_end_page_overall = 0
    for file_path in excel_files:
        match = pattern.match(os.path.basename(file_path))
        if match:
            max_end_page_overall = max(max_end_page_overall, int(match.group(4)))

    for file_path in excel_files:
        filename = os.path.basename(file_path)
        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            sheet = wb.active
            row_count = sheet.max_row
            wb.close()
            
            if row_count != expected_rows:
                match = pattern.match(filename)
                is_last_batch = match and int(match.group(4)) == max_end_page_overall
                
                # The final batch can legitimately have fewer rows
                if is_last_batch and row_count > 1:
                    print(f"Skipping exact row check for final batch file: '{filename}' ({row_count} lines)")
                    continue

                print(f"Found bad file: '{filename}' ({row_count} lines). Deleting...")
                os.remove(file_path)
                
                if match:
                    industry = match.group(1).strip()
                    state = match.group(2).strip()
                    start_page = int(match.group(3))
                    end_page = int(match.group(4))
                    
                    batches_to_repair.append({
                        "industry": industry,
                        "state": state,
                        "start_page": start_page,
                        "end_page": end_page
                    })
                else:
                    print(f"Warning: Could not parse batch details from filename '{filename}'.")
                    
        except Exception as e:
            print(f"ERROR reading '{filename}': {e}. Deleting corrupted file...")
            os.remove(file_path)
            match = pattern.match(filename)
            if match:
                batches_to_repair.append({
                    "industry": match.group(1).strip(),
                    "state": match.group(2).strip(),
                    "start_page": int(match.group(3)),
                    "end_page": int(match.group(4))
                })

    batches_to_repair.sort(key=lambda x: x["start_page"])
    return batches_to_repair

def repair_batches(batches, target_url, headless=False, login_val="27244084654949", pass_val="3250", worker_id=None, status_dir="status", folder="Downloads"):
    url = "http://www.referenceusa.com.lapl.idm.oclc.org"

    browser = None
    context = None
    page = None
    cursor = None
    
    checkpoint_file = f"{worker_id}-repair_checkpoint.json" if worker_id else "repair_checkpoint.json"
    
    def load_chk():
        if os.path.exists(checkpoint_file):
            try:
                with open(checkpoint_file, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"batch_index": 0, "phase": "checking", "abs_page": None}
        
    def save_chk(c):
        with open(checkpoint_file, "w") as f:
            json.dump(c, f, indent=4)

    checkpoint = load_chk()

    with sync_playwright() as p:
        while checkpoint["batch_index"] < len(batches):
            batch_index = checkpoint["batch_index"]
            batch = batches[batch_index]
            industry = batch["industry"]
            state = batch["state"]
            start_page = batch["start_page"]
            end_page = batch["end_page"]
            
            print(f"\n{'='*50}")
            print(f"REPAIRING BATCH {batch_index + 1}/{len(batches)}: {industry} {state} (Pages {start_page} to {end_page})")
            print(f"{'='*50}")

            if browser is None:
                browser, context, page, cursor = start_browser_session(
                    p, url, login_val, pass_val, target_url, None, None, None, headless=headless
                )
                if checkpoint["abs_page"] is None:
                    checkpoint["abs_page"] = start_page
                    save_chk(checkpoint)
                else:
                    resume_to_page(page, cursor, checkpoint["abs_page"])
                    
                is_first_step_of_session = True
            else:
                is_first_step_of_session = False

            try:
                # ── PHASE: CHECKING ──────────────────────────────────────────
                if checkpoint["phase"] == "checking":
                    abs_page = checkpoint["abs_page"]
                    while abs_page <= end_page:
                        print(f"--- Checking page {abs_page} ---")
                        
                        actual_checked = False
                        if is_first_step_of_session:
                            actual_checked = page.locator('#checkboxCol').is_checked()
                            is_first_step_of_session = False
                            
                        if not actual_checked:
                            cursor.click(page.locator('#checkboxCol'))
                            page.wait_for_load_state("networkidle")
                            page.wait_for_timeout(2000)

                            popup = page.locator('div.ui-dialog-content', has_text="Your new selections would have exceeded the maximum number of records").first
                            if popup.is_visible():
                                raise RuntimeError(f"Hit max selections popup on page {abs_page}. Manual intervention required.")
                                
                            verify_checkbox(page, cursor, '#checkboxCol', True)
                        else:
                            print("Checkbox already checked (persisted from previous session).")

                        if abs_page < end_page:
                            expected_p = abs_page + 1
                            checkpoint["abs_page"] = expected_p
                            save_chk(checkpoint)
                            print("Clicking 'Next Page'...")
                            cursor.click(page.locator('div[role="button"][aria-label="Go to next page"]').first)
                            page.wait_for_load_state("networkidle")
                            page.wait_for_timeout(2000)
                            verify_page_change(
                                page, expected_p,
                                action_fn=lambda ep=expected_p: (
                                    cursor.click(page.locator('div[role="button"][aria-label="Go to next page"]').first)
                                )
                            )
                        
                        abs_page += 1

                    checkpoint["phase"] = "downloading"
                    save_chk(checkpoint)

                # ── PHASE: DOWNLOADING ───────────────────────────────────────
                if checkpoint["phase"] == "downloading":
                    print(f"Downloading batch from page {start_page} to {end_page}...")
                    cursor.click(page.locator('a.action.download').first)
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(2000)

                    print("Selecting Excel 2007 format...")
                    cursor.click(page.locator('#format_excel_2007'))
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(2000)
                    verify_checkbox(page, cursor, '#format_excel_2007', True)

                    print("Selecting Detail level...")
                    cursor.click(page.locator('#detailDetail'))
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(2000)
                    verify_checkbox(page, cursor, '#detailDetail', True)

                    print("Clicking final download...")
                    with page.expect_download() as download_info:
                        cursor.click(page.locator('a.originButton.action-download'))

                    download = download_info.value
                    save_path = os.path.join(folder, f"{industry} {state} {start_page} - {end_page}.xlsx")
                    download.save_as(save_path)
                    print(f"Repaired file successfully saved as: {save_path}")

                    checkpoint["phase"] = "unchecking"
                    checkpoint["abs_page"] = end_page
                    save_chk(checkpoint)

                # ── PHASE: UNCHECKING ────────────────────────────────────────
                if checkpoint["phase"] == "unchecking":
                    print("Returning to results page to uncheck...")
                    page.go_back()
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(2000)
                    
                    page.locator('#checkboxCol').wait_for(state="attached", timeout=5000)

                    abs_page = checkpoint["abs_page"]
                    while abs_page >= start_page:
                        print(f"--- Unchecking page {abs_page} ---")
                        
                        actual_checked = True
                        if is_first_step_of_session:
                            actual_checked = page.locator('#checkboxCol').is_checked()
                            is_first_step_of_session = False
                            
                        if actual_checked:
                            cursor.click(page.locator('#checkboxCol'))
                            page.wait_for_load_state("networkidle")
                            page.wait_for_timeout(2000)
                            verify_checkbox(page, cursor, '#checkboxCol', False)
                        else:
                            print("Checkbox already unchecked (persisted from previous session).")

                        if abs_page > start_page:
                            expected_p = abs_page - 1
                            checkpoint["abs_page"] = expected_p
                            save_chk(checkpoint)
                            print("Clicking 'Previous Page'...")
                            cursor.click(page.locator('div[role="button"][aria-label="Go to previous page"]').first)
                            page.wait_for_load_state("networkidle")
                            page.wait_for_timeout(2000)
                            verify_page_change(
                                page, expected_p,
                                action_fn=lambda ep=expected_p: (
                                    cursor.click(page.locator('div[role="button"][aria-label="Go to previous page"]').first)
                                )
                            )
                        
                        abs_page -= 1
                        
                print(f"Batch {start_page}-{end_page} fully repaired!")
                
                # Advance to next batch
                checkpoint["batch_index"] += 1
                checkpoint["phase"] = "checking"
                checkpoint["abs_page"] = None
                save_chk(checkpoint)
                    
                print(f"Batch {start_page}-{end_page} fully repaired!")

            except Exception as e:
                print(f"ERROR during repair of batch {start_page}-{end_page}: {e}")
                print(f"Repair checkpoint saved. Restarting browser and resuming from phase '{checkpoint['phase']}', page {checkpoint['abs_page']}...")
                close_browser(browser)
                browser = None
                context = None
                page = None
                cursor = None
                time.sleep(5)
                
        close_browser(browser)
        
        checkpoint["phase"] = "completed"
        save_chk(checkpoint)
        
        print("\nAll repairs completed!")


if __name__ == "__main__":
    import multiprocessing
    import argparse
    import json
    import os
    
    parser = argparse.ArgumentParser(description="Repair Bad Downloads")
    parser.add_argument("--url", type=str, help="Target URL (results page)")
    parser.add_argument("--headless", action="store_true", help="Run the browser headlessly")
    parser.add_argument("--worker-id", type=str, help="Worker ID for centralized config routing")
    parser.add_argument("--config-path", type=str, default="config.json", help="Path to centralized config.json")
    parser.add_argument("--status-dir", type=str, default="status", help="Directory for mirroring worker status")
    parser.add_argument("--download-dir", type=str, default="Downloads", help="Base directory for downloading files")
    args, unknown = parser.parse_known_args()

    multiprocessing.freeze_support()
    
    target = None
    login_val = "27244084654949"
    pass_val = "3250"
    if args.worker_id:
        print(f"Loading configuration for worker '{args.worker_id}' from {args.config_path}...")
        try:
            with open(args.config_path, "r") as f:
                configs = json.load(f)
            worker_conf = next((c for c in configs if c.get("worker_id") == args.worker_id), None)
            if not worker_conf:
                raise ValueError(f"Worker '{args.worker_id}' not found in {args.config_path}")
            
            target = worker_conf["url"]
            login_val = worker_conf["login"]
            pass_val = worker_conf["password"]
            industry = worker_conf["industry"]
            state = worker_conf["state"]
            
            folder = os.path.join(args.download_dir, state, industry)
        except Exception as e:
            print(f"Error loading worker config: {e}")
            import sys
            sys.exit(1)
    elif args.url:
        target = args.url
        print(f"Running via CLI arguments:\nTarget URL: {target}\nHeadless: {args.headless}")
        folder = args.download_dir
    else:
        target = get_target_url()
        folder = args.download_dir
        
    batches = find_and_delete_bad_files(folder_path=folder)
    if batches:
        print(f"\nFound {len(batches)} batches to repair.")
        repair_batches(batches, target, headless=args.headless, login_val=login_val, pass_val=pass_val, worker_id=args.worker_id, status_dir=args.status_dir, folder=folder)
    else:
        print("No bad files found. Everything looks good!")
            
    print("\nPress Enter to exit...")
    if not args.url and not args.worker_id:
        input()
