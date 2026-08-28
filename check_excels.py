import os
import glob
import openpyxl

def check_excel_rows(folder_path="Downloads/WA/Healthcare", expected_rows=251):
    print(f"Scanning for Excel files in '{folder_path}' folder...\n")
    
    # Resolve absolute path
    abs_folder = os.path.abspath(folder_path)
    if not os.path.exists(abs_folder):
        print(f"Error: Folder '{folder_path}' does not exist.")
        return

    # Find all .xlsx files
    excel_files = glob.glob(os.path.join(abs_folder, "*.xlsx"))
    
    if not excel_files:
        print("No Excel (.xlsx) files found in the folder.")
        return

    total_files = len(excel_files)
    correct_count = 0
    incorrect_files = []

    for i, file_path in enumerate(excel_files, 1):
        filename = os.path.basename(file_path)
        try:
            # Load workbook in read-only, data-only mode for performance
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            sheet = wb.active
            
            # Count the rows
            row_count = sheet.max_row
            wb.close()
            
            # Print status
            if row_count == expected_rows:
                print(f"[{i}/{total_files}] OK: '{filename}' has exactly {row_count} lines.")
                correct_count += 1
            else:
                print(f"[{i}/{total_files}] WARNING: '{filename}' has {row_count} lines! (Expected {expected_rows})")
                incorrect_files.append((filename, row_count))
                
        except Exception as e:
            print(f"[{i}/{total_files}] ERROR reading '{filename}': {e}")
            incorrect_files.append((filename, "ERROR"))

    # Summary Report
    print("\n" + "=" * 50)
    print("SUMMARY REPORT")
    print("=" * 50)
    print(f"Total files checked: {total_files}")
    print(f"Correct files ({expected_rows} lines): {correct_count}")
    print(f"Incorrect files: {len(incorrect_files)}")
    
    if incorrect_files:
        print("\nIncorrect files details:")
        for name, count in incorrect_files:
            print(f" - {name}: {count} lines")
    else:
        print("\nAll files are correct! 🎉")

if __name__ == "__main__":
    check_excel_rows()
    print("\nPress Enter to exit...")
    input()
