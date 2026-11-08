#!/bin/bash
# Double-click this file to start Property Manager on a Mac.
# Keep the window that opens while you use the app. Closing it stops the app.

# Work from the folder this file is in.
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
  echo "Property Manager isn't set up on this computer yet."
  echo "Please double-click \"Set Up Property Manager.command\" first."
  echo
  read -r -p "Press Return to close this window."
  exit 1
fi

.venv/bin/python run.py
status=$?

# If the app stopped because of a problem, keep the window open so the message can be read.
if [ $status -ne 0 ]; then
  echo
  read -r -p "Press Return to close this window."
fi
exit $status
