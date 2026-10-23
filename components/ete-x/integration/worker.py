#!/usr/bin/env python3
"""Entry point, including Python -I. See component LICENSE.md."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from etex_bridge.worker import main

if __name__ == "__main__":
    raise SystemExit(main())
