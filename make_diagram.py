#!/usr/bin/env python3
"""Generate the README architecture diagram, one SVG per colour scheme.

Two files rather than one self-switching file: GitHub strips <style> blocks
from inline SVG, so a `prefers-color-scheme` media query inside the document is
silently dropped and the diagram renders in whichever theme was hardcoded. The
README pairs them with <picture>, which GitHub does honour.

SVG rather than PNG so the text stays crisp at any width and searchable in the
rendered page.

Deliberately simpler than the deployment: Cognito, the DynamoDB tables, the
OAuth authorization-server proxy, the share bucket and the SPA's S3 origin are
all real and none of them is on the request path this diagram exists to show.
Cognito appears as an edge label, because what matters about it here is that
both clients carry the same one.

Run:  python make_diagram.py
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Sized to the content, not to a video frame: a 16:9 canvas in a README is
# mostly empty margin.
W, H = 1180, 660
NH = 110                                   # node height
TITLE_PX, SUB_PX, EDGE_PX = 26, 17, 16

# Two clients on the left, the AWS chain descending on the right. The fork
# against the single column is the point: several ways in, one sandbox.
COL = [40, 620]
ROW = {"c_top": 90, "c_low": 300, "a_top": 90, "a_mid": 300, "a_low": 510}

THEMES = {
    "dark": {
        "bg": "#0d1117", "panel": "#161b22", "text": "#e6edf3",
        "muted": "#8b949e", "line": "#58a6ff", "local": "#39c5bb",
        "purple": "#c39be8", "navy": "#c8d6e8", "blue": "#58a6ff",
        "amber": "#f2c163",
    },
    "light": {
        "bg": "#ffffff", "panel": "#f6f8fa", "text": "#1f2328",
        "muted": "#59636e", "line": "#0969da", "local": "#137e77",
        "purple": "#8250df", "navy": "#475467", "blue": "#0969da",
        "amber": "#9a6700",
    },
}

# Lucide icon paths, on a 24-unit grid.
ICONS = {
    "bot": ['<path d="M12 8V4H8"/>',
            '<rect width="16" height="12" x="4" y="8" rx="2"/>',
            '<path d="M2 14h2"/>', '<path d="M20 14h2"/>',
            '<path d="M15 13v2"/>', '<path d="M9 13v2"/>'],
    "monitor": ['<rect width="20" height="14" x="2" y="3" rx="2"/>',
                '<line x1="8" x2="16" y1="21" y2="21"/>',
                '<line x1="12" x2="12" y1="17" y2="21"/>'],
    "route": ['<circle cx="6" cy="19" r="3"/>', '<circle cx="18" cy="5" r="3"/>',
              '<path d="M12 19h4.5a3.5 3.5 0 0 0 0-7h-8a3.5 3.5 0 0 1 0-7H12"/>'],
    "zap": ['<path d="M4 14h7l-1 8 10-12h-7l1-8z"/>'],
    "terminal": ['<polyline points="4 17 10 11 4 5"/>',
                 '<line x1="12" x2="20" y1="19" y2="19"/>'],
}

# id: (x, y, width, colour key, icon, title, subtitle)
NODES = {
    "claude":     (COL[0], ROW["c_top"], 460, "purple", "bot", "Claude",
                   "MCP connector"),
    "browser":    (COL[0], ROW["c_low"], 460, "navy", "monitor", "Web Console",
                   "debug view"),
    "api":        (COL[1], ROW["a_top"], 520, "blue", "route", "API Gateway",
                   "one HTTP API"),
    "controller": (COL[1], ROW["a_mid"], 520, "blue", "zap", "Controller Lambda",
                   "validates the token"),
    "microvm":    (COL[1], ROW["a_low"], 520, "amber", "terminal", "MicroVM",
                   "bash shell, up to 8 hours"),
}


def _r(nid):
    """Return (left, top, right, bottom, centre-x, centre-y) for a node."""
    x, y, w = NODES[nid][0], NODES[nid][1], NODES[nid][2]
    return x, y, x + w, y + NH, x + w / 2, y + NH / 2


def _h(a, b):
    """Horizontal hop, right edge of a to left edge of b."""
    return f"M{_r(a)[2]},{_r(a)[5]} L{_r(b)[0] - 5},{_r(b)[5]}"


def _v(a, b):
    """Vertical hop between two nodes sharing a column."""
    x = _r(a)[4]
    return f"M{x},{_r(a)[3]} L{x},{_r(b)[1] - 5}"


def _elbow(a, b):
    """Right, up, right: the lower client reaching the API above it.

    Turns up in the gutter between the columns rather than routing into the
    API's underside, which would cross the Controller box.
    """
    gutter = _r(b)[0] - 40
    return f"M{_r(a)[2]},{_r(a)[5]} H{gutter} V{_r(b)[5]} H{_r(b)[0] - 5}"


def _return(a, b):
    """The job id coming back, offset from the outbound line beside it."""
    x = _r(a)[4] + 80
    return f"M{x},{_r(b)[1]} L{x},{_r(a)[3] + 5}"


# id: (path, colour key, label, label_x, label_y, anchor)
EDGES = {
    "e_mcp": (_h("claude", "api"), "line", "One Cognito Identity",
              (_r("claude")[2] + _r("api")[0]) / 2, ROW["a_top"] - 16, "middle"),
    "e_web": (_elbow("browser", "api"), "line", "", 0, 0, "start"),
    "e_run": (_v("api", "controller"), "line", "", 0, 0, "start"),
    "e_vm":  (_v("controller", "microvm"), "local", "HTTPS + port-scoped token",
              _r("controller")[4] - 16,
              (ROW["a_mid"] + NH + ROW["a_low"]) / 2 + 6, "end"),
    "e_job": (_return("controller", "microvm"), "local", "job id",
              _r("controller")[4] + 96,
              (ROW["a_mid"] + NH + ROW["a_low"]) / 2 + 6, "start"),
}

FONT = "-apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif"


def node_svg(nid, t):
    """Render one service box: rounded panel, icon, title, subtitle."""
    x, y, w, key, icon, title, sub = NODES[nid]
    colour = t[key]
    scale = 1.25
    isz = 24 * scale
    ix, iy = x + 20, y + (NH - isz) / 2
    tx = x + 20 + isz + 14
    return "".join([
        f'<rect x="{x}" y="{y}" width="{w}" height="{NH}" rx="10" ry="10" '
        f'fill="{t["panel"]}" stroke="{colour}" stroke-width="2"/>',
        f'<g transform="translate({ix},{iy}) scale({scale})" fill="none" '
        f'stroke="{colour}" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round">' + "".join(ICONS[icon]) + "</g>",
        f'<text x="{tx}" y="{y + NH/2 - 4}" font-family="{FONT}" '
        f'font-size="{TITLE_PX}" font-weight="600" fill="{t["text"]}">{title}</text>',
        f'<text x="{tx}" y="{y + NH/2 + 22}" font-family="{FONT}" '
        f'font-size="{SUB_PX}" fill="{t["muted"]}">{sub}</text>',
    ])


def edge_svg(eid, t):
    """Render one arrow plus its label."""
    d, key, label, lx, ly, anchor = EDGES[eid]
    colour = t[key]
    out = (f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2" '
           f'marker-end="url(#a_{eid})"/>')
    if label:
        out += (f'<text x="{lx}" y="{ly}" text-anchor="{anchor}" '
                f'font-family="{FONT}" font-size="{EDGE_PX}" font-weight="600" '
                f'fill="{colour}">{label}</text>')
    return out


def build(t):
    """Assemble the diagram for one theme."""
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" role="img" '
           f'aria-label="Claude and a web console reach one MicroVM through an '
           f'API Gateway and a controller Lambda">', "<defs>"]
    # One marker per edge: a shared marker cannot carry per-edge colour.
    for eid, spec in EDGES.items():
        out.append(
            f'<marker id="a_{eid}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{t[spec[1]]}"/></marker>')
    out.append("</defs>")
    out.append(f'<rect width="{W}" height="{H}" fill="{t["bg"]}"/>')
    for eid in EDGES:
        out.append(edge_svg(eid, t))
    for nid in NODES:
        out.append(node_svg(nid, t))
    out.append("</svg>")
    return "".join(out)


def main():
    for name, theme in THEMES.items():
        path = os.path.join(HERE, f"architecture-{name}.svg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build(theme))
        print(f"  architecture-{name}.svg")


if __name__ == "__main__":
    main()
