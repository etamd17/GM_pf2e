"""Package the local D&D 5e data for upload to the server.

    python tools/dnd5e/bundle.py [data_dir] [out_dir]

Defaults: ``dnd5e_data`` -> ``dist/dnd5e``. Upload the parts at
``/dnd5e/admin/data`` (site admin), core part first. See systems/dnd5e/datapack.py.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from systems.dnd5e.datapack import build  # noqa: E402


def main(argv):
    data = argv[1] if len(argv) > 1 else os.path.join(ROOT, 'dnd5e_data')
    out = argv[2] if len(argv) > 2 else os.path.join(ROOT, 'dist', 'dnd5e')
    parts = build(data, out)
    for p in parts:
        print(f'{os.path.getsize(p) / 1e6:7.1f} MB  {p}')
    print(f'{len(parts)} parts')


if __name__ == '__main__':
    main(sys.argv)
