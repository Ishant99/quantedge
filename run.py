# run.py — Windows-friendly entry point
# Usage: python run.py
#        python run.py --dry-run

import sys
import os

# Add project root to path before any imports
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from main import run_agent
import argparse

parser = argparse.ArgumentParser(description="NSE Trading Agent")
parser.add_argument("--dry-run", action="store_true",
                    help="Generate signals without executing trades")
args = parser.parse_args()

run_agent(dry_run=args.dry_run)
