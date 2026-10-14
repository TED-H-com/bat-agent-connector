/*!
ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

---

The following Lucide icons are derived from the Feather project:

airplay, alert-circle, alert-octagon, alert-triangle, aperture, arrow-down-circle, arrow-down-left, arrow-down-right, arrow-down, arrow-left-circle, arrow-left, arrow-right-circle, arrow-right, arrow-up-circle, arrow-up-left, arrow-up-right, arrow-up, at-sign, calendar, cast, check, chevron-down, chevron-left, chevron-right, chevron-up, chevrons-down, chevrons-left, chevrons-right, chevrons-up, circle, clipboard, clock, code, columns, command, compass, corner-down-left, corner-down-right, corner-left-down, corner-left-up, corner-right-down, corner-right-up, corner-up-left, corner-up-right, crosshair, database, divide-circle, divide-square, dollar-sign, download, external-link, feather, frown, hash, headphones, help-circle, info, italic, key, layout, life-buoy, link-2, link, loader, lock, log-in, log-out, maximize, meh, minimize, minimize-2, minus-circle, minus-square, minus, monitor, moon, more-horizontal, more-vertical, move, music, navigation-2, navigation, octagon, pause-circle, pause, play, percent, plus-circle, plus-square, plus, power, radio, rss, search, server, share, shopping-bag, sidebar, smartphone, smile, square, table-2, tablet, target, terminal, trash-2, trash, triangle, tv, type, upload, x-circle, x-octagon, x-square, x, zoom-in, zoom-out

The MIT License (MIT) (for the icons listed above)

Copyright (c) 2013-present Cole Bemis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Source: https://github.com/lucide-icons/lucide (icons/, retrieved 2026-10-13).
*/
const paths = {
  "star": [
    ["polygon", {"points": "12 3 14.8 8.7 21 9.6 16.5 14 17.6 20.2 12 17.3 6.4 20.2 7.5 14 3 9.6 9.2 8.7 12 3"}]
  ],
  "grip-vertical": [
    ["circle", {"cx": "9", "cy": "5", "r": "1"}],
    ["circle", {"cx": "9", "cy": "12", "r": "1"}],
    ["circle", {"cx": "9", "cy": "19", "r": "1"}],
    ["circle", {"cx": "15", "cy": "5", "r": "1"}],
    ["circle", {"cx": "15", "cy": "12", "r": "1"}],
    ["circle", {"cx": "15", "cy": "19", "r": "1"}]
  ],
  "pencil": [
    ["path", {"d": "m16 3 5 5L7 22H2v-5L16 3Z"}],
    ["path", {"d": "m14 5 5 5"}]
  ],
  "panel-right": [
    ["rect", {"x": "3", "y": "3", "width": "18", "height": "18", "rx": "2"}],
    ["path", {"d": "M15 3v18"}]
  ],
  "clipboard": [
    ["rect", {"width": "8", "height": "4", "x": "8", "y": "2", "rx": "1", "ry": "1"}],
    ["path", {"d": "M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"}]
  ],
  "external-link": [
    ["path", {"d": "M15 3h6v6"}],
    ["path", {"d": "M10 14 21 3"}],
    ["path", {"d": "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"}]
  ],
  "layout-dashboard": [
    [
      "rect",
      {
        "width": "7",
        "height": "9",
        "x": "3",
        "y": "3",
        "rx": "1"
      }
    ],
    [
      "rect",
      {
        "width": "7",
        "height": "5",
        "x": "14",
        "y": "3",
        "rx": "1"
      }
    ],
    [
      "rect",
      {
        "width": "7",
        "height": "9",
        "x": "14",
        "y": "12",
        "rx": "1"
      }
    ],
    [
      "rect",
      {
        "width": "7",
        "height": "5",
        "x": "3",
        "y": "16",
        "rx": "1"
      }
    ]
  ],
  "message-square": [
    [
      "path",
      {
        "d": "M22 17a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 21.286V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z"
      }
    ]
  ],
  "list-checks": [
    [
      "path",
      {
        "d": "M13 5h8"
      }
    ],
    [
      "path",
      {
        "d": "M13 12h8"
      }
    ],
    [
      "path",
      {
        "d": "M13 19h8"
      }
    ],
    [
      "path",
      {
        "d": "m3 17 2 2 4-4"
      }
    ],
    [
      "path",
      {
        "d": "m3 7 2 2 4-4"
      }
    ]
  ],
  "calendar-range": [
    [
      "rect",
      {
        "x": "3",
        "y": "3",
        "width": "18",
        "height": "18",
        "rx": "2"
      }
    ],
    [
      "path",
      {
        "d": "M16 2v3"
      }
    ],
    [
      "path",
      {
        "d": "M3 9h18"
      }
    ],
    [
      "path",
      {
        "d": "M8 2v3"
      }
    ],
    [
      "path",
      {
        "d": "M17 13h-6"
      }
    ],
    [
      "path",
      {
        "d": "M13 17H7"
      }
    ],
    [
      "path",
      {
        "d": "M7 13h.01"
      }
    ],
    [
      "path",
      {
        "d": "M17 17h.01"
      }
    ]
  ],
  "network": [
    [
      "rect",
      {
        "x": "16",
        "y": "16",
        "width": "6",
        "height": "6",
        "rx": "1"
      }
    ],
    [
      "rect",
      {
        "x": "2",
        "y": "16",
        "width": "6",
        "height": "6",
        "rx": "1"
      }
    ],
    [
      "rect",
      {
        "x": "9",
        "y": "2",
        "width": "6",
        "height": "6",
        "rx": "1"
      }
    ],
    [
      "path",
      {
        "d": "M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"
      }
    ],
    [
      "path",
      {
        "d": "M12 12V8"
      }
    ]
  ],
  "circle-alert": [
    [
      "circle",
      {
        "cx": "12",
        "cy": "12",
        "r": "10"
      }
    ],
    [
      "line",
      {
        "x1": "12",
        "x2": "12",
        "y1": "8",
        "y2": "12"
      }
    ],
    [
      "line",
      {
        "x1": "12",
        "x2": "12.01",
        "y1": "16",
        "y2": "16"
      }
    ]
  ],
  "circle-x": [
    [
      "circle",
      {
        "cx": "12",
        "cy": "12",
        "r": "10"
      }
    ],
    [
      "path",
      {
        "d": "m15 9-6 6"
      }
    ],
    [
      "path",
      {
        "d": "m9 9 6 6"
      }
    ]
  ],
  "cpu": [
    [
      "path",
      {
        "d": "M12 20v2"
      }
    ],
    [
      "path",
      {
        "d": "M12 2v2"
      }
    ],
    [
      "path",
      {
        "d": "M17 20v2"
      }
    ],
    [
      "path",
      {
        "d": "M17 2v2"
      }
    ],
    [
      "path",
      {
        "d": "M2 12h2"
      }
    ],
    [
      "path",
      {
        "d": "M2 17h2"
      }
    ],
    [
      "path",
      {
        "d": "M2 7h2"
      }
    ],
    [
      "path",
      {
        "d": "M20 12h2"
      }
    ],
    [
      "path",
      {
        "d": "M20 17h2"
      }
    ],
    [
      "path",
      {
        "d": "M20 7h2"
      }
    ],
    [
      "path",
      {
        "d": "M7 20v2"
      }
    ],
    [
      "path",
      {
        "d": "M7 2v2"
      }
    ],
    [
      "rect",
      {
        "x": "4",
        "y": "4",
        "width": "16",
        "height": "16",
        "rx": "2"
      }
    ],
    [
      "rect",
      {
        "x": "8",
        "y": "8",
        "width": "8",
        "height": "8",
        "rx": "1"
      }
    ]
  ],
  "kanban": [
    [
      "path",
      {
        "d": "M5 3v14"
      }
    ],
    [
      "path",
      {
        "d": "M12 3v8"
      }
    ],
    [
      "path",
      {
        "d": "M19 3v18"
      }
    ]
  ],
  "folder": [
    [
      "path",
      {
        "d": "M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"
      }
    ]
  ],
  "inbox": [
    [
      "polyline",
      {
        "points": "22 12 16 12 14 15 10 15 8 12 2 12"
      }
    ],
    [
      "path",
      {
        "d": "M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"
      }
    ]
  ],
  "settings": [
    [
      "path",
      {
        "d": "M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915"
      }
    ],
    [
      "circle",
      {
        "cx": "12",
        "cy": "12",
        "r": "3"
      }
    ]
  ],
  "wrench": [
    [
      "path",
      {
        "d": "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.106-3.105c.32-.322.863-.22.983.218a6 6 0 0 1-8.259 7.057l-7.91 7.91a1 1 0 0 1-2.999-3l7.91-7.91a6 6 0 0 1 7.057-8.259c.438.12.54.662.219.984z"
      }
    ]
  ],
  "search": [
    [
      "path",
      {
        "d": "m21 21-4.34-4.34"
      }
    ],
    [
      "circle",
      {
        "cx": "11",
        "cy": "11",
        "r": "8"
      }
    ]
  ],
  "refresh-cw": [
    [
      "path",
      {
        "d": "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"
      }
    ],
    [
      "path",
      {
        "d": "M21 3v5h-5"
      }
    ],
    [
      "path",
      {
        "d": "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"
      }
    ],
    [
      "path",
      {
        "d": "M8 16H3v5"
      }
    ]
  ],
  "chevron-right": [
    [
      "path",
      {
        "d": "m9 18 6-6-6-6"
      }
    ]
  ],
  "chevron-down": [
    [
      "path",
      {
        "d": "m6 9 6 6 6-6"
      }
    ]
  ],
  "chevron-up": [
    [
      "path",
      {
        "d": "m18 15-6-6-6 6"
      }
    ]
  ],
  "chevron-left": [
    [
      "path",
      {
        "d": "m15 18-6-6 6-6"
      }
    ]
  ],
  "x": [
    [
      "path",
      {
        "d": "M18 6 6 18"
      }
    ],
    [
      "path",
      {
        "d": "m6 6 12 12"
      }
    ]
  ],
  "more-horizontal": [
    [
      "circle",
      {
        "cx": "12",
        "cy": "12",
        "r": "1"
      }
    ],
    [
      "circle",
      {
        "cx": "19",
        "cy": "12",
        "r": "1"
      }
    ],
    [
      "circle",
      {
        "cx": "5",
        "cy": "12",
        "r": "1"
      }
    ]
  ],
  "plus": [
    [
      "path",
      {
        "d": "M5 12h14"
      }
    ],
    [
      "path",
      {
        "d": "M12 5v14"
      }
    ]
  ],
  "check": [
    [
      "path",
      {
        "d": "M20 6 9 17l-5-5"
      }
    ]
  ],
  "menu": [
    [
      "path",
      {
        "d": "M4 5h16"
      }
    ],
    [
      "path",
      {
        "d": "M4 12h16"
      }
    ],
    [
      "path",
      {
        "d": "M4 19h16"
      }
    ]
  ],
  "pause": [
    [
      "rect",
      {
        "x": "6",
        "y": "4",
        "width": "4",
        "height": "16"
      }
    ],
    [
      "rect",
      {
        "x": "14",
        "y": "4",
        "width": "4",
        "height": "16"
      }
    ]
  ],
  "play": [
    [
      "polygon",
      {
        "points": "5 3 19 12 5 21 5 3"
      }
    ]
  ]
};
export function icon(name, {label, size = 16} = {}) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor',
    'stroke-width': 1.75, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', focusable: 'false', class: 'ui-icon',
    ...(label ? {role: 'img', 'aria-label': label} : {'aria-hidden': 'true'})})) svg.setAttribute(key, String(value));
  for (const [tag, attrs] of paths[name] || paths['circle-alert']) {
    const node = document.createElementNS(svg.namespaceURI, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    svg.append(node);
  }
  return svg;
}
