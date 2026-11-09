#!/usr/bin/env python3
"""Runnable demonstration of CatalogCopilot."""

import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from catalog_copilot.cli import cmd_demo
import argparse

if __name__ == "__main__":
    print(f"Running CatalogCopilot Demo from: {PROJECT_ROOT}\n")
    sys.exit(cmd_demo(argparse.Namespace()))
