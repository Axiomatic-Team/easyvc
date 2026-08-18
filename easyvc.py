#!/usr/bin/env python3

import os
import sys
import json
import shutil
import hashlib
import ftplib
import getpass
import fnmatch
from pathlib import Path, PurePosixPath
from datetime import datetime
from difflib import unified_diff


# Enable colors on Windows if possible
if os.name == "nt":
    os.system("")


class C:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    END = "\033[0m"


ROOT = Path.cwd()
VC = ROOT / ".easyvc"
CONFIG = VC / "settings.json"
COMMITS = VC / "history.json"
BLOBS = VC / "storage"

IGNORE_LIST = [
    ".easyvc",
    ".git",
    ".env",
    "*.log",
    ".DS_Store",
    "Thumbs.db",
    "node_modules",
    "vendor",
    "config.local.php",
]

MAX_PREVIEW_LINES = 400


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def pause():
    input(f"\n{C.BLUE}Press Enter to go back to the menu...{C.END}")


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_json(path):
    if not path.exists():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"{C.RED}ERROR: {path} is broken. Please fix or delete it.{C.END}")
        print(e)
        sys.exit(1)


def ensure_repo():
    first_time = not COMMITS.exists()

    VC.mkdir(parents=True, exist_ok=True)
    BLOBS.mkdir(parents=True, exist_ok=True)

    if not COMMITS.exists():
        save_json(COMMITS, {
            "stable": None,
            "beta": None,
            "history": {}
        })

    return first_time


def load_settings():
    return load_json(CONFIG)


def save_settings(settings):
    save_json(CONFIG, settings)


def ftp_configured():
    settings = load_settings()
    return bool(settings.get("ftp_host")) and bool(settings.get("ftp_user"))


def ftp_summary():
    settings = load_settings()

    host = settings.get("ftp_host")
    user = settings.get("ftp_user")
    folder = settings.get("ftp_folder")

    if not host or not user:
        return "not set up yet"

    if not folder:
        folder = "(FTP root)"

    return f"{user}@{host} folder: {folder}"


def setup_ftp_wizard():
    clear_screen()

    print(f"{C.BOLD}⚙️ FTP Setup{C.END}")
    print()
    print("You can skip FTP and still use local saving.")
    print("You can set up FTP anytime from the menu.")
    print()

    host = input("1. FTP Hostname (example: ftpupload.net), leave blank to cancel: ").strip()
    if not host:
        print(f"{C.YELLOW}FTP setup canceled.{C.END}")
        pause()
        return

    user = input("2. FTP Username (example: if0_12345678): ").strip()

    password = getpass.getpass("3. FTP Password (typing hidden): ").strip()

    remember = input("4. Remember password on this computer? y/n: ").strip().lower()

    raw_folder = input("5. Remote folder (leave blank for htdocs, type . for none): ").strip()
    if raw_folder == ".":
        folder = ""
    else:
        folder = raw_folder or "htdocs"

    settings = {
        "ftp_host": host,
        "ftp_user": user,
        "ftp_folder": folder,
    }

    if remember.startswith("y"):
        settings["ftp_pass"] = password

    save_settings(settings)

    print(f"\n{C.GREEN}✅ FTP settings saved.{C.END}")

    if "ftp_pass" not in settings:
        print(f"{C.YELLOW}Password will be asked each time you deploy or test FTP.{C.END}")

    pause()


def show_ftp_settings():
    clear_screen()

    print(f"{C.BOLD}📁 FTP Settings{C.END}\n")

    settings = load_settings()

    if not settings.get("ftp_host") or not settings.get("ftp_user"):
        print(f"{C.YELLOW}FTP is not set up yet.{C.END}")
        print("Use menu option 9 to set it up.")
        pause()
        return

    print("FTP Host:", settings.get("ftp_host"))
    print("FTP User:", settings.get("ftp_user"))
    print("FTP Folder:", settings.get("ftp_folder") or "(FTP root)")

    if "ftp_pass" in settings:
        print("Password: saved on this computer")
    else:
        print("Password: will ask each time")

    pause()


def get_ftp_password(settings):
    password = settings.get("ftp_pass")

    if password is not None:
        return password

    return getpass.getpass("FTP Password: ").strip()


def is_ignored(rel):
    rel = rel.replace(os.sep, "/")

    if rel == ".easyvc" or rel.startswith(".easyvc/"):
        return True

    parts = PurePosixPath(rel).parts

    for pat in IGNORE_LIST:
        if fnmatch.fnmatch(rel, pat):
            return True

        if fnmatch.fnmatch(os.path.basename(rel), pat):
            return True

        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True

    return False


def get_all_files():
    files = {}

    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT).as_posix()
        if rel_dir == ".":
            rel_dir = ""

        keep = []
        for d in dirnames:
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if not is_ignored(rel):
                keep.append(d)

        dirnames[:] = keep

        for f in filenames:
            rel = f"{rel_dir}/{f}" if rel_dir else f
            full = Path(dirpath) / f

            if is_ignored(rel):
                continue

            if full.is_file():
                files[rel] = full

    return files


def hash_file(path):
    h = hashlib.sha256()

    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)

    return h.hexdigest()


def is_probably_text(path):
    try:
        with open(path, "rb") as f:
            chunk = f.read(4096)
        return b"\0" not in chunk
    except Exception:
        return False


def read_text_safe(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def print_colored_lines(lines, color, prefix):
    count = 0

    for line in lines:
        if count >= MAX_PREVIEW_LINES:
            print(f"   {C.YELLOW}... preview stopped after {MAX_PREVIEW_LINES} lines.{C.END}")
            break

        print(f"   {color}{prefix} {line}{C.END}")
        count += 1


def show_line_diff(old_text, new_text, rel):
    diff = unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile=f"old/{rel}",
        tofile=f"new/{rel}",
        lineterm=""
    )

    count = 0

    for line in diff:
        if line.startswith("---") or line.startswith("+++"):
            continue

        if count >= MAX_PREVIEW_LINES:
            print(f"   {C.YELLOW}... preview stopped after {MAX_PREVIEW_LINES} lines.{C.END}")
            break

        if line.startswith("+"):
            print(f"   {C.GREEN}{line}{C.END}")
        elif line.startswith("-"):
            print(f"   {C.RED}{line}{C.END}")
        elif line.startswith("@@"):
            print(f"   {C.BLUE}{line}{C.END}")
        else:
            print(f"   {line}")

        count += 1

    if count == 0:
        print("   (no line differences)")


def get_history_items(history):
    return sorted(
        history.get("history", {}).items(),
        key=lambda item: (item[1].get("date", ""), item[0]),
        reverse=True
    )


def version_label(history, commit_id):
    if not commit_id:
        return "None"

    commit = history.get("history", {}).get(commit_id)

    if not commit:
        return commit_id

    return commit.get("name") or commit.get("message") or commit_id


def print_versions(history):
    items = get_history_items(history)

    if not items:
        print("No saved versions yet.")
        return []

    for i, (commit_id, commit) in enumerate(items, 1):
        marks = []

        if history.get("stable") == commit_id:
            marks.append("STABLE")

        if history.get("beta") == commit_id:
            marks.append("BETA")

        mark_text = f" [{', '.join(marks)}]" if marks else ""

        label = commit.get("name") or commit.get("message") or "(no message)"
        date = commit.get("date", "unknown date")

        print(f"  {i}. {label} - {date} - ID {commit_id}{mark_text}")

    return items


def choose_version(history):
    items = print_versions(history)

    if not items:
        return None

    choice = input("\nType version number, or 0 to cancel: ").strip()

    if choice == "0":
        return None

    if not choice.isdigit():
        print(f"{C.RED}Please type a number from the list.{C.END}")
        return None

    index = int(choice) - 1

    if index < 0 or index >= len(items):
        print(f"{C.RED}That version number does not exist.{C.END}")
        return None

    return items[index]


def restore_version_files(commit):
    files = commit.get("files", {})

    # Delete current files that are not in the old version
    for rel, path in get_all_files().items():
        if rel not in files:
            try:
                path.unlink()
            except Exception:
                pass

    # Restore files from the old version
    for rel, meta in files.items():
        blob_path = BLOBS / meta["hash"][:2] / meta["hash"]

        if not blob_path.exists():
            print(f"{C.RED}Missing saved file for {rel}{C.END}")
            continue

        dest = ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(blob_path, dest)


def show_versions():
    clear_screen()

    print(f"{C.BOLD}📜 Saved versions{C.END}\n")

    history = load_json(COMMITS)
    print_versions(history)

    pause()


def go_back_to_version():
    clear_screen()

    print(f"{C.BOLD}⏪ Go back to a saved version{C.END}\n")

    history = load_json(COMMITS)
    selected = choose_version(history)

    if not selected:
        pause()
        return

    commit_id, commit = selected

    label = commit.get("name") or commit.get("message") or commit_id

    print(f"\nYou chose: {C.BOLD}{label}{C.END}")
    print()
    print("What should I do?")
    print()
    print("  1. Restore my files and make this version BETA")
    print("  2. Restore my files and make this version BETA + STABLE")
    print("  3. Cancel")
    print()

    mode = input("Type 1, 2, or 3: ").strip()

    if mode not in ("1", "2"):
        print(f"{C.YELLOW}Canceled.{C.END}")
        pause()
        return

    confirm = input("This will overwrite your current files. Are you sure? y/n: ").strip().lower()

    if not confirm.startswith("y"):
        print(f"{C.YELLOW}Canceled.{C.END}")
        pause()
        return

    restore_version_files(commit)

    history = load_json(COMMITS)
    history["beta"] = commit_id

    if mode == "2":
        history["stable"] = commit_id

    save_json(COMMITS, history)

    print(f"\n{C.GREEN}✅ Your files were restored to:{C.END} {label}")

    if mode == "2":
        print(f"{C.GREEN}This version is now BETA and STABLE.{C.END}")
        print("If you want it on your website, use option 4.")
    else:
        print(f"{C.GREEN}This version is now BETA.{C.END}")

    pause()


def rename_version():
    clear_screen()

    print(f"{C.BOLD}✏️ Rename a saved version{C.END}\n")

    history = load_json(COMMITS)
    selected = choose_version(history)

    if not selected:
        pause()
        return

    commit_id, commit = selected

    current_label = commit.get("name") or commit.get("message") or commit_id

    new_name = input(f"New name for '{current_label}': ").strip()

    if not new_name:
        print(f"{C.YELLOW}Rename canceled.{C.END}")
        pause()
        return

    history["history"][commit_id]["name"] = new_name

    save_json(COMMITS, history)

    print(f"\n{C.GREEN}✅ Version renamed to:{C.END} {new_name}")

    pause()


def see_changes():
    clear_screen()

    print(f"{C.BOLD}👀 What did I change?{C.END}\n")

    history = load_json(COMMITS)
    beta_id = history.get("beta")

    old_files = {}
    if beta_id:
        old_files = history.get("history", {}).get(beta_id, {}).get("files", {})

    current_files = get_all_files()
    current_hashes = {rel: hash_file(path) for rel, path in current_files.items()}

    added = [f for f in current_hashes if f not in old_files]
    deleted = [f for f in old_files if f not in current_hashes]
    modified = [
        f for f in current_hashes
        if f in old_files and current_hashes[f] != old_files[f]["hash"]
    ]

    if not added and not deleted and not modified:
        print(f"{C.GREEN}✨ No changes! Your files are exactly the same as your last save.{C.END}")
        pause()
        return

    if added:
        print(f"{C.GREEN}➕ New files:{C.END}")

        for f in added:
            print(f"\n   {C.BOLD}{f}{C.END}")

            path = current_files[f]

            if not is_probably_text(path):
                print("   (This is not a plain text file, so line view is hidden.)")
                continue

            text = read_text_safe(path)

            if text is None:
                print("   (Cannot read this file.)")
                continue

            lines = text.splitlines()

            if not lines:
                print("   (empty file)")
            else:
                print_colored_lines(lines, C.GREEN, "+")

    if deleted:
        print(f"\n{C.RED}➖ Deleted files:{C.END}")

        for f in deleted:
            print(f"\n   {C.BOLD}{f}{C.END}")

            old_hash = old_files[f]["hash"]
            blob_path = BLOBS / old_hash[:2] / old_hash

            if not blob_path.exists():
                print("   (Cannot show deleted file contents.)")
                continue

            if not is_probably_text(blob_path):
                print("   (This is not a plain text file, so line view is hidden.)")
                continue

            text = read_text_safe(blob_path)

            if text is None:
                print("   (Cannot read this file.)")
                continue

            lines = text.splitlines()

            if not lines:
                print("   (empty file)")
            else:
                print_colored_lines(lines, C.RED, "-")

    if modified:
        print(f"\n{C.YELLOW}📝 Modified files:{C.END}")

        for f in modified:
            print(f"\n   {C.BOLD}{f}{C.END}")

            old_hash = old_files[f]["hash"]
            blob_path = BLOBS / old_hash[:2] / old_hash

            if not blob_path.exists():
                print("   (Cannot show old version.)")
                continue

            if not is_probably_text(blob_path) or not is_probably_text(current_files[f]):
                print("   (This file is not plain text, so line diff is hidden.)")
                continue

            old_text = read_text_safe(blob_path)
            new_text = read_text_safe(current_files[f])

            if old_text is None or new_text is None:
                print("   (Cannot read this file.)")
                continue

            show_line_diff(old_text, new_text, f)

    pause()


def save_to_beta():
    clear_screen()

    print(f"{C.BOLD}💾 Save my work to BETA{C.END}\n")
    print("This saves your current files as a test version.")

    msg = input("What did you do? (example: added login box): ").strip()

    if not msg:
        msg = "Saved my work"

    name = input("Optional version name (press Enter to skip): ").strip()

    current_files = get_all_files()
    manifest = {}

    for rel, path in current_files.items():
        h = hash_file(path)

        blob_dir = BLOBS / h[:2]
        blob_path = blob_dir / h

        if not blob_path.exists():
            blob_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, blob_path)

        manifest[rel] = {
            "hash": h
        }

    history = load_json(COMMITS)

    now = datetime.now()

    commit_id = hashlib.sha256(f"{msg}{now.isoformat()}".encode()).hexdigest()[:12]

    commit_data = {
        "message": msg,
        "date": now.strftime("%Y-%m-%d %H:%M:%S"),
        "files": manifest
    }

    if name:
        commit_data["name"] = name

    history.setdefault("history", {})[commit_id] = commit_data
    history["beta"] = commit_id

    save_json(COMMITS, history)

    print(f"\n{C.GREEN}✅ Saved to BETA!{C.END}")
    print(f"Save ID: {commit_id}")

    if name:
        print(f"Version name: {name}")

    pause()


def make_stable():
    clear_screen()

    print(f"{C.BOLD}🌟 Make BETA the new STABLE{C.END}\n")

    history = load_json(COMMITS)

    if not history.get("beta"):
        print(f"{C.RED}❌ You have not saved anything to BETA yet.{C.END}")
        pause()
        return

    history["stable"] = history["beta"]

    save_json(COMMITS, history)

    print(f"{C.GREEN}✅ BETA is now STABLE!{C.END}")
    print("It is ready to be sent to your website.")

    pause()


def ftp_enter_folder(ftp, name):
    if not name or name == ".":
        return

    try:
        ftp.cwd(name)
    except ftplib.error_perm:
        try:
            ftp.mkd(name)
        except ftplib.error_perm:
            pass

        try:
            ftp.cwd(name)
        except ftplib.error_perm as e:
            raise Exception(f"Cannot enter folder '{name}': {e}")


def ftp_enter_path(ftp, path):
    path = str(path).strip().strip("/")

    if not path:
        return

    for part in PurePosixPath(path).parts:
        ftp_enter_folder(ftp, part)


def test_ftp():
    clear_screen()

    print(f"{C.BOLD}🔎 Test FTP Connection{C.END}\n")

    settings = load_settings()

    if not ftp_configured():
        print(f"{C.YELLOW}FTP is not set up yet.{C.END}")
        choice = input("Set up FTP now? y/n: ").strip().lower()

        if choice.startswith("y"):
            setup_ftp_wizard()
            settings = load_settings()

            if not ftp_configured():
                return
        else:
            pause()
            return

    password = get_ftp_password(settings)

    host = settings.get("ftp_host")
    user = settings.get("ftp_user")
    folder = settings.get("ftp_folder", "")

    try:
        ftp = ftplib.FTP(host, timeout=30)
        ftp.login(user, password)
        ftp.set_pasv(True)

        home = ftp.pwd()

        ftp.cwd(home)

        if folder:
            ftp_enter_path(ftp, folder)

        print(f"\n{C.GREEN}✅ FTP connection OK!{C.END}")
        print("Current FTP folder:", ftp.pwd())

        ftp.quit()

    except Exception as e:
        print(f"\n{C.RED}❌ FTP connection failed.{C.END}")
        print(e)

    pause()


def send_to_web():
    clear_screen()

    print(f"{C.BOLD}🌍 Send STABLE to the internet!{C.END}\n")

    history = load_json(COMMITS)
    stable_id = history.get("stable")

    if not stable_id:
        print(f"{C.RED}❌ You do not have a STABLE version yet.{C.END}")
        print("Make BETA stable first.")
        pause()
        return

    settings = load_settings()

    if not ftp_configured():
        print(f"{C.YELLOW}FTP is not set up yet.{C.END}")
        choice = input("Set up FTP now? y/n: ").strip().lower()

        if choice.startswith("y"):
            setup_ftp_wizard()
            settings = load_settings()

            if not ftp_configured():
                return
        else:
            pause()
            return

    password = get_ftp_password(settings)

    host = settings.get("ftp_host")
    user = settings.get("ftp_user")
    folder = settings.get("ftp_folder", "")

    files = history.get("history", {}).get(stable_id, {}).get("files", {})

    try:
        ftp = ftplib.FTP(host, timeout=60)
        ftp.login(user, password)
        ftp.set_pasv(True)

        home = ftp.pwd()

        print(f"Connected to {host}.")
        print(f"Uploading {len(files)} file(s)...\n")

        for rel, meta in files.items():
            blob_path = BLOBS / meta["hash"][:2] / meta["hash"]

            if not blob_path.exists():
                raise Exception(f"Missing saved file for {rel}")

            # Always start from home folder
            ftp.cwd(home)

            # Enter root folder like htdocs
            if folder:
                ftp_enter_path(ftp, folder)

            # Enter subfolders for this file
            parent = PurePosixPath(rel).parent
            if str(parent) not in (".", "/"):
                ftp_enter_path(ftp, parent.as_posix())

            filename = PurePosixPath(rel).name

            with open(blob_path, "rb") as f:
                ftp.storbinary(f"STOR {filename}", f)

            print(f"   {C.GREEN}➡️ Sent: {rel}{C.END}")

        ftp.quit()

        print(f"\n{C.BOLD}{C.GREEN}🎉 SUCCESS! Your website is updated!{C.END}")

    except Exception as e:
        print(f"\n{C.RED}❌ Upload failed.{C.END}")
        print(e)

    pause()


def main():
    first_time = ensure_repo()

    if first_time:
        clear_screen()

        print(f"{C.BOLD}👋 Welcome to Easy Code Manager!{C.END}")
        print()
        print("You can use this without FTP.")
        print("FTP is optional. You can set it up later from the menu.")
        print()
        print("Local saving works like this:")
        print("  1. Save your work to BETA")
        print("  2. Test it")
        print("  3. Make BETA the new STABLE")
        print("  4. Send STABLE to your website when ready")

        pause()

    while True:
        clear_screen()

        history = load_json(COMMITS)

        stable = version_label(history, history.get("stable"))
        beta = version_label(history, history.get("beta"))

        print(f"{C.BOLD}🌟 EASY CODE MANAGER 🌟{C.END}")
        print()
        print(f"STABLE: {C.GREEN}{stable}{C.END}")
        print(f"BETA:   {C.YELLOW}{beta}{C.END}")
        print(f"FTP:    {C.BLUE}{ftp_summary()}{C.END}")
        print()

        print("What do you want to do?")
        print()
        print("  1. 👀 See what I changed")
        print("  2. 💾 Save my work to BETA")
        print("  3. 🌟 Make BETA the new STABLE")
        print("  4. 🌍 Send STABLE to the internet")
        print("  5. 🔎 Test FTP connection")
        print("  6. 📜 Show all saved versions")
        print("  7. ⏪ Go back to a saved version")
        print("  8. ✏️ Rename a saved version")
        print("  9. ⚙️ Set up / change FTP")
        print("  10. 📁 Show FTP settings")
        print("  0. 👋 Quit")
        print()

        choice = input("Type a number and press Enter: ").strip()

        if choice == "1":
            see_changes()

        elif choice == "2":
            save_to_beta()

        elif choice == "3":
            make_stable()

        elif choice == "4":
            send_to_web()

        elif choice == "5":
            test_ftp()

        elif choice == "6":
            show_versions()

        elif choice == "7":
            go_back_to_version()

        elif choice == "8":
            rename_version()

        elif choice == "9":
            setup_ftp_wizard()

        elif choice == "10":
            show_ftp_settings()

        elif choice == "0":
            print("Goodbye! 👋")
            sys.exit()

        else:
            print(f"{C.RED}Oops! Please type a number from the menu.{C.END}")
            pause()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nGoodbye! 👋")
        sys.exit()
