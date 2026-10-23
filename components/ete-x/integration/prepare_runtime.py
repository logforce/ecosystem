#!/usr/bin/env python3
"""Copy immutable publication sources to an explicit writable runtime directory.

See component LICENSE.md.
"""
import argparse
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from etex_bridge.backends import packaged_root, resolve_ete_x_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True,
                        help="New directory to contain a writable commercial_lab copy; must not exist")
    parser.add_argument("--ete-x-root", type=Path, default=packaged_root())
    args = parser.parse_args()
    source = resolve_ete_x_root(args.ete_x_root) / "commercial_lab"
    destination = args.destination.absolute()
    if destination.exists() or destination.is_symlink():
        parser.error("Destination must be new; this helper never overwrites an existing tree")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copytree(source, destination / "commercial_lab",
                        ignore=shutil.ignore_patterns(".build", "__pycache__", "*.pyc"))
    except BaseException:
        # Preserve a failed partial copy for operator inspection. It cannot be
        # mistaken for an overwritten original release; no recursive deletion.
        print("RUNTIME_COPY incomplete; inspect the newly created destination", file=sys.stderr)
        raise
    print("RUNTIME_COPY ready; native build caches will be created only inside this writable copy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
