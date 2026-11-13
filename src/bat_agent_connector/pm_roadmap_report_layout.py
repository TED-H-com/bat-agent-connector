"""Pinned, conservative report layout; not full Unicode grapheme segmentation.

Attach marks/selectors/modifiers and ZWJ-linked code points, pair regional
indicators. Cluster width is the widest constituent (wide/fullwidth=2, other=1).
An overlong cluster becomes visible escapes. No runtime Unicode properties,
font measurement, locale collation or platform newline decisions are used.
"""
from __future__ import annotations

import html
from bisect import bisect_right

from . import pm_roadmap_unicode as ucd
from .operations import OperationError

PAGE_WIDTH, PAGE_HEIGHT, PAGE_GAP = 960, 1280, 24
MAX_SVG_PAGES = 32
MAX_CLUSTER = 32


def contains(value, ranges):
    index = bisect_right(ranges, (value, 0x110000)) - 1
    return index >= 0 and value <= ranges[index][1]


def _union(*tables):
    result = []
    for first, last in sorted(item for table in tables for item in table):
        if result and first <= result[-1][1] + 1:
            result[-1] = result[-1][0], max(last, result[-1][1])
        else:
            result.append((first, last))
    return tuple(result)


_EXTEND = _union(ucd.MARK, ucd.VARIATION, ucd.MODIFIER, ucd.ZWJ)
_WIDE = _union(ucd.WIDE, ucd.REGIONAL)
_ESCAPE = _union(ucd.CONTROL, ucd.BIDI, ucd.NONCHARACTER, ucd.SURROGATE)


def literal(value):
    text = str(value).replace('\r\n', '\n').replace('\r', '\n').expandtabs(4)
    return ''.join(f'\\u{ord(c):04x}' if contains(ord(c), _ESCAPE) else c for c in text)


def clusters(value):
    current = ''
    for char in value:
        if char == '\n':
            if current:
                yield current
                current = ''
            yield char
            continue
        number = ord(char)
        attached = contains(number, _EXTEND)
        pair = (len(current) == 1 and contains(ord(current), ucd.REGIONAL) and contains(number, ucd.REGIONAL))
        if current and not (attached or current.endswith('\u200d') or pair):
            yield current
            current = ''
        current += char
    if current:
        yield current


def normalize(value):
    # Escaping pathological clusters is part of all three formats, not SVG-only.
    return ''.join(cluster if len(cluster) <= MAX_CLUSTER else ''.join(f'\\u{ord(c):04x}' for c in cluster)
                   for cluster in clusters(literal(value)))


def wrap(value, columns):
    """Yield (start,end,text) slices. Newlines have explicit zero-width slices."""
    start, offset, width, line = 0, 0, 0, ''
    for cluster in clusters(value):
        if cluster == '\n':
            if line:
                yield start, offset, line
            yield offset, offset + 1, '\n'
            offset += 1
            start, width, line = offset, 0, ''
            continue
        units = max((2 if contains(ord(c), _WIDE) else 1 for c in cluster), default=1)
        if line and width + units > columns:
            yield start, offset, line
            start, width, line = offset, 0, ''
        line += cluster
        offset += len(cluster)
        width += units
    if line or not value:
        yield start, offset, line


def svg(blocks, locale, identity, page_label):
    pages, page, y = [], [], 76
    for block, (kind, value) in enumerate(blocks):
        size = 20 if kind == 'title' else 16 if kind == 'heading' else 14 if kind in {'note', 'source'} else 15
        y += 12 if kind in {'title', 'heading', 'subheading'} else 4
        # One width unit is conservatively one em, not an OS font metric.
        for start, end, line in wrap(value, (PAGE_WIDTH - 80) // size):
            if y + size + 8 > PAGE_HEIGHT - 52:
                pages.append(page)
                if len(pages) >= MAX_SVG_PAGES:
                    raise OperationError('PM_EXPORT_TOO_LARGE', 'The complete report exceeds the SVG page bound', 413)
                page, y = [], 76
            page.append((block, start, end, y, size, line))
            y += size + 8
    pages.append(page)
    height = len(pages) * (PAGE_HEIGHT + PAGE_GAP) - PAGE_GAP
    escape = html.escape
    nodes = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{PAGE_WIDTH}" height="{height}" viewBox="0 0 {PAGE_WIDTH} {height}" xml:lang="{locale}" role="img">',
             f'<title>{escape(blocks[0][1])}</title>']
    for index, lines in enumerate(pages):
        nodes += [f'<g id="page-{index + 1}" transform="translate(0 {index * (PAGE_HEIGHT + PAGE_GAP)})">',
                  '<rect width="960" height="1280" fill="#fff" stroke="#dfe3ea"/>',
                  '<g font-family="system-ui, Segoe UI, Noto Sans TC, sans-serif" fill="#1d2330">',
                  f'<text x="40" y="36" font-size="14">{escape(identity)}</text>']
        for block, start, end, offset, size, line in lines:
            # Exact offsets prove completeness without conflating wrapping with LF.
            content = escape(line).replace('\n', '&#10;')
            nodes.append(f'<text x="40" y="{offset}" font-size="{size}" xml:space="preserve" data-block="{block}" data-start="{start}" data-end="{end}">{content}</text>')
        nodes += [f'<text x="40" y="1256" font-size="14">{page_label} {index + 1} / {len(pages)}</text>', '</g></g>']
    return '\n'.join([*nodes, '</svg>']) + '\n', len(pages)
