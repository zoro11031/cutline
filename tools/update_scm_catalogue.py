#!/usr/bin/env python3
"""Refresh the built-in catalogue from a Silhouette Card Maker checkout."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cut_proxies import geometry, importer, regmarks  # noqa: E402

CARDS = {'standard': 'Standard', 'poker': 'Poker', 'japanese': 'Japanese',
         'bridge': 'Bridge', 'catan': 'Catan', 'tarot': 'Tarot'}


def derive(source, layouts, paper, paper_name, card, borderless):
    variant = 'borderless' if borderless else 'default'
    defaults = layouts['defaults']['registration'][variant]
    settings = layouts['layouts'][paper][card][variant]
    stem = f'{paper}-{card}' + ('-borderless' if borderless else '')
    directory = Path('cutting_templates') / ('borderless' if borderless else '')
    relative = directory / f'{stem}-v{settings["version"]}.studio3'
    display = importer.load(source / relative)
    paths = display.paths
    count = settings['num_rows'] * settings['num_cols']
    if len(paths) != count or not all(path.closed for path in paths):
        raise ValueError(f'{relative} has unexpected cut geometry.')
    # The upstream borderless Studio3 page adds a 6.5 mm margin on each
    # edge to move Studio's 10 mm mark inset to 3.5 mm on the real paper.
    inset = float(defaults['inset'].removesuffix('mm'))
    shift = 10 - inset
    raw_arm = float(settings['registration']['length'].removesuffix('mm'))
    thickness = float(defaults['thickness'].removesuffix('mm'))
    _, arm, _ = regmarks.clamp(inset, raw_arm, thickness)
    orientation = settings['orientation']
    return dict(id=f'scm-{stem}', source=relative.as_posix(), sheet=paper_name, card=CARDS[card],
                size=geometry.SHEETS[paper_name].size(orientation == 'portrait'), orientation=orientation,
                inset=inset, arm=arm, thickness=thickness,
                paths=[[[round(x - shift, 5), round(y - shift, 5)] for x, y in path.points] for path in paths])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scm_checkout', type=Path)
    args = parser.parse_args()
    source = args.scm_checkout.resolve()
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    layouts = json.loads((source / 'assets' / 'layouts.json').read_text(encoding='utf-8'))
    rows = []
    for paper, paper_name in (('letter', 'Letter'), ('a4', 'A4')):
        rows.extend(derive(source, layouts, paper, paper_name, card, False) for card in CARDS)
        rows.append(derive(source, layouts, paper, paper_name, 'standard', True))
    output = ROOT / 'cut_proxies' / 'assets' / 'scm'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'LICENSE.txt').write_bytes((source / 'LICENSE.md').read_bytes())
    entries = ',\n'.join(json.dumps(row, separators=(',', ':')) for row in rows)
    (output / 'cutting-templates.json').write_text(
        '{"revision":' + json.dumps(revision) + ',"templates":[\n' + entries + '\n]}\n', encoding='utf-8')


if __name__ == '__main__':
    main()
