"""Create a private Cosmere rules bundle for the site-admin upload page."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.cosmere.rules.datapack import build
from systems.cosmere.rules.paths import data_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=str(data_dir()))
    parser.add_argument('--output', default='dist/cosmere-rules.tar.gz')
    args = parser.parse_args()
    print(build(args.data_dir, args.output))


if __name__ == '__main__':
    main()
