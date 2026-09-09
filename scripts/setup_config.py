#!/usr/bin/env python3
"""Interactive first-time setup. Asks three questions, writes your config.

    python scripts/setup_config.py

This exists so nobody has to hand-edit YAML. Indentation, smart quotes pasted
from Notepad, and a `login` in quotes are all easy mistakes that produce a
confusing validation error much later; this writes the file correctly instead.

What it does:
  1. Checks your Python is 64-bit and the MetaTrader5 package is importable.
  2. Asks for your MT5 login, server, and password.
  3. Writes config/config.yaml with dry_run left ON.
  4. Stores the password as a Windows environment variable (setx), never in
     the config file. The file only ever contains ${MT5_PASSWORD}.
  5. Offers to run the connection check immediately.

Your password is not echoed as you type it, is not written to any file, and is
not printed back to you.
"""

from __future__ import annotations

import getpass
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "config.example.yaml"
TARGET = ROOT / "config" / "config.yaml"

OK, WARN, FAIL = "  [OK]  ", " [WARN] ", " [FAIL] "


def ask(prompt: str, validate=None, secret: bool = False) -> str:
    """Keep asking until the answer is usable. Empty input is never accepted."""
    while True:
        value = (getpass.getpass(prompt) if secret else input(prompt)).strip()
        if not value:
            print("        (that cannot be empty — try again)")
            continue
        if validate is not None:
            problem = validate(value)
            if problem:
                print(f"        {problem}")
                continue
        return value


def _bad_login(value: str) -> str | None:
    cleaned = value.replace(" ", "")
    if not cleaned.isdigit():
        return "an MT5 login is all digits, e.g. 10012605510 — no letters or spaces"
    return None


def _bad_server(value: str) -> str | None:
    if " " in value.strip():
        return "server names have no spaces — copy it exactly from the terminal"
    return None


# The MSIX package family name of the Store build. Matching this rather than the
# WindowsApps folder matters: app execution aliases — including the ones the
# python.org install manager creates — also live under WindowsApps, so matching
# the folder alone flags a perfectly good python.org install as sandboxed.
STORE_PACKAGE_MARKER = "pythonsoftwarefoundation.python"


def is_store_python() -> bool:
    """True if this is the Microsoft Store build of Python.

    It runs inside an app container with a virtualised filesystem and registry.
    That sandbox is a poor fit for MetaTrader5, which has to reach a separate
    desktop process over Windows IPC — and when it fails it fails obscurely, so
    it is worth naming up front rather than debugging later.
    """
    return any(
        STORE_PACKAGE_MARKER in path.lower() for path in (sys.executable, sys.prefix)
    )


def check_environment() -> bool:
    """Report anything that would make the rest pointless. True if we can go on."""
    print("Checking your setup")
    ok = True

    bits = platform.architecture()[0]
    if bits == "64bit":
        print(f"{OK}Python {platform.python_version()} ({bits})")
    else:
        print(f"{FAIL}Python {platform.python_version()} is {bits}. MetaTrader5 ships")
        print("        only 64-bit Windows wheels. Install 64-bit Python from python.org.")
        ok = False

    if platform.system() != "Windows":
        print(f"{WARN}You are on {platform.system()}. MetaTrader5 is Windows-only, so the")
        print("        connection check will not work here — but the config will still be written.")
    else:
        if is_store_python():
            print(f"{WARN}This is the Microsoft Store build of Python:")
            print(f"        {sys.executable}")
            print("        It runs sandboxed, which often stops MetaTrader5 from reaching the")
            print("        terminal. If the connection check fails with an initialize() error,")
            print("        install Python from python.org (64-bit), tick 'Add python.exe to")
            print("        PATH', and run setup again. Nothing else needs redoing.")

        try:
            import MetaTrader5  # noqa: F401

            print(f"{OK}MetaTrader5 package is installed")
        except ImportError:
            print(f"{FAIL}MetaTrader5 is not installed. Run this first, on its own line:")
            print("            pip install -r requirements.txt")
            ok = False

    if not EXAMPLE.exists():
        print(f"{FAIL}Cannot find {EXAMPLE}. Are you running this from the project folder?")
        ok = False

    print()
    return ok


def write_config(login: str, server: str) -> None:
    """Copy the example and substitute the three lines that are yours.

    `password` becomes a ${MT5_PASSWORD} reference — never the password itself —
    because this script is also what sets that environment variable. The example
    ships with it null so that a plain copy of the example loads with no
    environment set at all.

    Everything else — dry_run, the risk limits, the agents being off — stays
    exactly as shipped.
    """
    text = EXAMPLE.read_text(encoding="utf-8")
    text, n_login = re.subn(r"(?m)^  login: .*$", f"  login: {login}", text, count=1)
    text, n_server = re.subn(r"(?m)^  server: .*$", f'  server: "{server}"', text, count=1)
    text, n_pw = re.subn(r"(?m)^  password: .*$", "  password: ${MT5_PASSWORD}", text, count=1)
    if not (n_login and n_server and n_pw):
        raise SystemExit(
            "Could not find the login/server/password lines in config.example.yaml. "
            "Edit config/config.yaml by hand instead."
        )
    if TARGET.exists():
        backup = TARGET.with_suffix(".yaml.bak")
        shutil.copy2(TARGET, backup)
        print(f"{WARN}config.yaml already existed — saved a copy as {backup.name}")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(text, encoding="utf-8")


def store_password(password: str) -> bool:
    """Persist the password as a user environment variable, never in a file."""
    os.environ["MT5_PASSWORD"] = password  # so the check below can run right now
    if platform.system() != "Windows":
        print(f"{WARN}Not on Windows — set MT5_PASSWORD yourself before running the bot.")
        return False
    try:
        subprocess.run(
            ["setx", "MT5_PASSWORD", password],
            check=True, capture_output=True, text=True,
        )
        print(f"{OK}password stored as the MT5_PASSWORD environment variable")
        return True
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"{FAIL}could not store the password automatically: {exc}")
        print('        Run this yourself:   setx MT5_PASSWORD "your-password"')
        return False


def main() -> int:
    print("=" * 74)
    print("  goldbot — first-time setup")
    print("=" * 74)
    print()

    if not check_environment():
        print("Fix the [FAIL] items above, then run this again.")
        return 1

    print("Three questions. Find all of these in your MetaTrader 5 terminal.")
    print()
    login = ask("  1. MT5 login number (digits only): ", validate=_bad_login).replace(" ", "")
    server = ask("  2. Server name, exactly as shown (e.g. MetaQuotes-Demo): ", validate=_bad_server)
    print()
    print("  3. Account password. It will NOT appear as you type, and is never")
    print("     written to any file — only to a Windows environment variable.")
    password = ask("     Password: ", secret=True)
    print()

    write_config(login, server)
    print(f"{OK}wrote {TARGET.relative_to(ROOT)}")
    print(f"        login {login} on {server}")
    print(f"{OK}dry_run is ON — this config cannot send a real order")
    stored = store_password(password)
    print()

    print("-" * 74)
    answer = input("Run the connection check now? [Y/n] ").strip().lower()
    if answer in ("", "y", "yes"):
        print()
        return subprocess.call([sys.executable, str(ROOT / "scripts" / "check_connection.py")])

    print()
    print("When you are ready, with the MT5 terminal open and logged in:")
    print("    python scripts\\check_connection.py")
    if stored:
        # setx writes to the registry for *future* processes only.
        print()
        print("Note: setx only affects NEW Command Prompt windows. Open a fresh one")
        print("before running that, or the password will look unset.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled. Nothing was written.")
        raise SystemExit(130)
