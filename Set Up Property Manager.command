#!/bin/bash
# One-time setup for Property Manager on a Mac. Double-click this file once.
# It needs Python 3.12 (from python.org) and an internet connection.
# Running it again is safe; it just re-installs the same packages.

# Work from the folder this file is in.
cd "$(dirname "$0")" || exit 1

echo "Setting up Property Manager. This takes a few minutes and needs the internet."
echo

# Find Python 3.12 or newer. Specific versions are checked first, because the plain
# "python3" on a new Mac may be too old or may ask to install developer tools.
PYTHON=""
for candidate in \
    python3.12 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 \
    /opt/homebrew/bin/python3.12 \
    python3.13 \
    python3.14; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "Python 3.12 isn't installed on this computer."
  echo "Your browser will now open the Python download page. Install Python 3.12,"
  echo "then double-click \"Set Up Property Manager.command\" again."
  open "https://www.python.org/downloads/"
  echo
  read -r -p "Press Return to close this window."
  exit 1
fi

echo "Using $("$PYTHON" --version)."

# Create a private copy of Python for this app (.venv), install its packages there,
# then make the app's icon (in Applications, with a shortcut on the Desktop).
if "$PYTHON" -m venv .venv &&
   .venv/bin/python -m pip install --upgrade pip &&
   .venv/bin/python -m pip install -r requirements.txt &&
   .venv/bin/python install_launcher.py; then
  chmod +x "Start Property Manager.command"
  echo
  echo "Setup finished. To open the app, double-click OwlNest on the Desktop"
  echo "(it's also in the Applications folder inside your home folder)."
  echo
  read -r -p "Press Return to close this window."
  exit 0
else
  echo
  echo "Setup didn't finish. Please check the internet connection and try again."
  echo "If it keeps failing, send a photo of this window to whoever helps you with the app."
  echo
  read -r -p "Press Return to close this window."
  exit 1
fi
