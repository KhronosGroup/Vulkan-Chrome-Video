#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-FileCopyrightText: 2026 The Khronos Group, Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Convert a blog Markdown source into the styled, self-contained HTML page.

    python3 build_blog.py [input.md] [output.html]

Defaults: blog_vulkan_video_browsers.md -> blog_vulkan_video_browsers.html
(relative to this script's directory).

Zero external dependencies (Python 3 standard library only) so it runs
anywhere — CI, a fresh checkout, or a reviewer's laptop.

Supported Markdown: a leading `<!--meta ... -->` comment (badges, byline,
footer) plus an optional leading `![alt](src)` banner image, a `# Title`,
and a subtitle paragraph — all three go into the hero, so the banner shows
both on GitHub and on the built page. (`hero:`/`hero_alt:` meta keys
override the banner if you need one that GitHub should not show.)
Then `##`/`###` headings (with optional `<!--accent:xx-->` and
`<!--sidebar-->` markers on `##`; `<!--sidebar-->` wraps the heading and the
list that follows it in a boxed `<aside>`, for a Key-Takeaways panel),
paragraphs, `-`/`*` and `1.` lists, `>` block-quotes
(rendered as a callout), `![caption](src)` images (rendered as a captioned
figure), horizontal rules, and inline `**bold**`, `*italic*`/`_italic_`,
`` `code` ``, and `[text](url)`. Lines beginning with `<` pass through as
raw HTML.
"""
import html
import re
import sys
from pathlib import Path

LICENSE_HEADER = (
    "<!--\n"
    "SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.\n"
    "SPDX-FileCopyrightText: 2026 The Khronos Group, Inc.\n"
    "SPDX-License-Identifier: Apache-2.0\n\n"
    "Generated from the Markdown source by build_blog.py — edit the .md, not this file.\n"
    "-->\n"
)

ACCENT_CLASS = {"vk": "vk", "ff": "ff", "cr": "cr", "nvidia": "", "": ""}

CSS = """
        :root {
            --bg-primary: #0a0e17;
            --bg-card: #1a2332;
            --accent-vulkan: #ac2d2d;
            --accent-vulkan-bright: #e05252;
            --accent-chrome: #5b9bff;
            --accent-nvidia: #8bd90a;
            --accent-firefox: #ff9d3d;
            --text-primary: #eef2f8;
            --text-body: #d3dce8;
            --text-secondary: #b3c0d1;
            --text-muted: #8a97a8;
            --border: #2d3a4f;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Outfit', system-ui, sans-serif; font-weight: 400;
            background: var(--bg-primary); color: var(--text-body);
            line-height: 1.75; font-size: 17px;
        }
        .bg-pattern {
            position: fixed; inset: 0; z-index: -1;
            background:
                radial-gradient(ellipse at 20% 10%, rgba(172, 45, 45, 0.10) 0%, transparent 50%),
                radial-gradient(ellipse at 80% 90%, rgba(66, 133, 244, 0.08) 0%, transparent 50%),
                radial-gradient(ellipse at 60% 40%, rgba(118, 185, 0, 0.05) 0%, transparent 60%);
        }
        .container { max-width: 980px; margin: 0 auto; padding: 0 1.5rem; }
        header.hero { padding: 4rem 0 2.5rem; text-align: center; }
        .logo-strip {
            display: flex; justify-content: center; gap: 1rem; flex-wrap: wrap;
            margin-bottom: 2rem; font-family: 'JetBrains Mono', monospace;
            font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.15em;
        }
        .logo-strip span { padding: 0.4rem 0.8rem; border-radius: 4px; border: 1px solid var(--border); }
        .logo-strip .vulkan { color: var(--accent-vulkan-bright); border-color: var(--accent-vulkan); }
        .logo-strip .nvidia { color: var(--accent-nvidia); border-color: var(--accent-nvidia); }
        .logo-strip .ff { color: var(--accent-firefox); border-color: var(--accent-firefox); }
        .logo-strip .chrome { color: var(--accent-chrome); border-color: var(--accent-chrome); }
        .hero-banner {
            display: block; width: 100%; height: auto;
            border-radius: 12px; margin-bottom: 2rem;
        }
        h1 {
            font-size: 2.7rem; font-weight: 700; line-height: 1.15; margin-bottom: 1rem;
            color: var(--text-primary);
        }
        .hero .dek { font-size: 1.2rem; color: var(--text-secondary); max-width: 760px; margin: 0 auto; }
        .byline { margin-top: 1.5rem; font-size: 0.9rem; color: var(--text-muted); font-family: 'JetBrains Mono', monospace; }
        article { padding-bottom: 5rem; }
        h2 {
            font-size: 1.7rem; font-weight: 700; margin: 3.2rem 0 1.1rem; color: var(--text-primary);
            padding-left: 0.9rem; border-left: 4px solid var(--accent-nvidia);
        }
        h2.ff { border-left-color: var(--accent-firefox); }
        h2.cr { border-left-color: var(--accent-chrome); }
        h2.vk { border-left-color: var(--accent-vulkan-bright); }
        h3 { font-size: 1.2rem; font-weight: 600; margin: 2rem 0 0.7rem; color: var(--text-primary); }
        p { margin-bottom: 1.1rem; color: var(--text-body); }
        p strong, li strong { color: var(--text-primary); }
        em { color: var(--text-primary); font-style: italic; }
        ul, ol { margin: 0 0 1.1rem 1.5rem; color: var(--text-body); }
        li { margin-bottom: 0.5rem; }
        a { color: var(--accent-chrome); text-decoration: none; }
        a:hover { text-decoration: underline; }
        code {
            font-family: 'JetBrains Mono', monospace; font-size: 0.83em;
            background: var(--bg-card); border: 1px solid var(--border);
            padding: 0.1rem 0.35rem; border-radius: 4px; color: #a8ecb2;
        }
        figure.diagram {
            margin: 1.8rem 0; padding: 1rem; background: #ffffff;
            border: 1px solid var(--border); border-radius: 12px; text-align: center;
        }
        figure.diagram img, figure.diagram object { max-width: 100%; height: auto; }
        figure.diagram figcaption { margin-top: 0.6rem; font-size: 0.85rem; color: #475569; font-style: italic; }
        blockquote.pull {
            border-left: 4px solid var(--accent-vulkan-bright);
            background: var(--bg-card); border-radius: 0 10px 10px 0;
            padding: 1.1rem 1.4rem; margin: 1.6rem 0;
            font-size: 1.03rem; color: var(--text-primary);
        }
        blockquote.pull p { color: var(--text-primary); margin: 0; }
        aside.takeaways {
            background: var(--bg-card); border: 1px solid var(--border);
            border-left: 4px solid var(--accent-vulkan-bright);
            border-radius: 0 12px 12px 0; padding: 1.4rem 1.7rem;
            margin: 2.2rem 0 2.6rem;
        }
        aside.takeaways h2 {
            font-size: 0.95rem; margin: 0 0 0.9rem; padding: 0; border-left: none;
            font-family: 'JetBrains Mono', monospace; text-transform: uppercase;
            letter-spacing: 0.12em; color: var(--accent-vulkan-bright);
        }
        aside.takeaways ul { margin: 0 0 0 1.2rem; }
        aside.takeaways li:last-child { margin-bottom: 0; }
        hr { border: none; border-top: 1px solid var(--border); margin: 2.5rem 0; }
        footer {
            border-top: 1px solid var(--border); padding: 2rem 0 3.5rem;
            color: var(--text-muted); font-size: 0.88rem; text-align: center;
        }
        @media (max-width: 700px) { h1 { font-size: 1.9rem; } }
"""


def inline(text):
    """Render inline Markdown (code, links, bold, italic) to safe HTML."""
    codes = []

    def stash_code(m):
        codes.append(html.escape(m.group(1)))
        return "\x00CODE%d\x00" % (len(codes) - 1)

    text = re.sub(r"`([^`]+)`", stash_code, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"<em>\1</em>", text)
    text = re.sub(r"\x00CODE(\d+)\x00", lambda m: "<code>%s</code>" % codes[int(m.group(1))], text)
    return text


def parse_meta(src):
    """Read a leading `<!--meta ... -->` comment (key: value lines) for badges,
    byline, and footer; discard any other leading HTML comment (the license
    header). Keeping metadata in a hidden comment — rather than a YAML front
    matter block — lets the file render cleanly when viewed directly on GitHub."""
    meta = {}
    src = src.lstrip()
    while src.startswith("<!--"):
        end = src.find("-->")
        if end == -1:
            break
        comment = src[4:end].strip()
        src = src[end + 3:].lstrip()
        if comment.startswith("meta"):
            for line in comment[4:].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
    return meta, src


def split_lead(body_md, meta):
    """Pull the leading banner image, the `# Title`, and the subtitle paragraph
    out of the body so the hero renders them and the article does not repeat
    them. An `![alt](src)` on the first line becomes the hero banner — writing
    it as ordinary Markdown, rather than a `hero:` meta key, is what makes it
    visible when the source file is viewed on GitHub. Values fall back to
    `meta` if the body has none."""
    lines = body_md.split("\n")

    def skip_blank(i):
        while i < len(lines) and not lines[i].strip():
            i += 1
        return i

    i = skip_blank(0)
    hero = hero_alt = None
    if i < len(lines):
        m = re.match(r"!\[(.*)\]\(([^)]+)\)\s*$", lines[i].strip())
        if m:
            hero_alt, hero = m.group(1), m.group(2)
            i = skip_blank(i + 1)
    title = meta.get("title")
    if i < len(lines) and lines[i].startswith("# "):
        title = lines[i][2:].strip()
        i += 1
    i = skip_blank(i)
    subtitle = meta.get("subtitle")
    if i < len(lines) and lines[i].strip() and not _is_block_start(lines[i]):
        buf = []
        while i < len(lines) and lines[i].strip() and not _is_block_start(lines[i]):
            buf.append(lines[i].strip())
            i += 1
        sub = " ".join(buf).strip()
        m = re.match(r"^[_*]+(.+?)[_*]+$", sub)
        subtitle = m.group(1).strip() if m else sub
    return title, subtitle, hero, hero_alt, "\n".join(lines[i:])


def render_body(md):
    lines = md.split("\n")
    out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        # skip HTML comments spanning lines
        if line.lstrip().startswith("<!--") and "-->" not in line:
            while i < n and "-->" not in lines[i]:
                i += 1
            i += 1
            continue
        if line.strip() in ("---", "***", "___"):
            out.append("<hr>")
            i += 1
            continue
        # image / figure
        m = re.match(r"!\[(.*)\]\(([^)]+)\)\s*$", line.strip())
        if m:
            cap, src = m.group(1), m.group(2)
            out.append(
                '<figure class="diagram">\n'
                '  <img src="%s" alt="%s" loading="lazy">\n'
                "  <figcaption>%s</figcaption>\n"
                "</figure>" % (src, html.escape(cap, quote=True), inline(cap))
            )
            i += 1
            continue
        # headings
        if line.startswith("### "):
            out.append("<h3>%s</h3>" % inline(line[4:].strip()))
            i += 1
            continue
        if line.startswith("## "):
            text = line[3:]
            sidebar = bool(re.search(r"<!--\s*sidebar\s*-->", text))
            am = re.search(r"<!--\s*accent:(\w+)\s*-->", text)
            accent = ACCENT_CLASS.get(am.group(1), "") if am else ""
            text = re.sub(r"<!--.*?-->", "", text)
            cls = ' class="%s"' % accent if accent else ""
            heading = "<h2%s>%s</h2>" % (cls, inline(text.strip()))
            i += 1
            if sidebar:
                # A sidebar heading takes the list that follows it into a
                # boxed <aside> — used for the Key Takeaways panel.
                j = i
                while j < n and not lines[j].strip():
                    j += 1
                items = []
                while j < n and re.match(r"[-*]\s+", lines[j]):
                    items.append("  <li>%s</li>" % inline(re.sub(r"^[-*]\s+", "", lines[j])))
                    j += 1
                if items:
                    out.append(
                        '<aside class="takeaways">\n%s\n<ul>\n%s\n</ul>\n</aside>'
                        % (heading, "\n".join(items))
                    )
                    i = j
                    continue
            out.append(heading)
            continue
        # blockquote (callout)
        if line.startswith(">"):
            buf = []
            while i < n and lines[i].startswith(">"):
                buf.append(lines[i].lstrip(">").strip())
                i += 1
            out.append('<blockquote class="pull"><p>%s</p></blockquote>' % inline(" ".join(buf)))
            continue
        # unordered list
        if re.match(r"[-*]\s+", line):
            items = []
            while i < n and re.match(r"[-*]\s+", lines[i]):
                items.append("  <li>%s</li>" % inline(re.sub(r"^[-*]\s+", "", lines[i])))
                i += 1
            out.append("<ul>\n%s\n</ul>" % "\n".join(items))
            continue
        # ordered list
        if re.match(r"\d+\.\s+", line):
            items = []
            while i < n and re.match(r"\d+\.\s+", lines[i]):
                items.append("  <li>%s</li>" % inline(re.sub(r"^\d+\.\s+", "", lines[i])))
                i += 1
            out.append("<ol>\n%s\n</ol>" % "\n".join(items))
            continue
        # raw HTML passthrough
        if line.lstrip().startswith("<"):
            out.append(line)
            i += 1
            continue
        # paragraph
        buf = []
        while i < n and lines[i].strip() and not _is_block_start(lines[i]):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>%s</p>" % inline(" ".join(buf)))
    return "\n\n".join(out)


def _is_block_start(line):
    return (
        line.startswith(("#", ">", "!["))
        or re.match(r"[-*]\s+", line)
        or re.match(r"\d+\.\s+", line)
        or line.strip() in ("---", "***", "___")
        or line.lstrip().startswith("<")
    )


def render_badges(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        label, _, cls = part.partition(":")
        out.append('<span class="%s">%s</span>' % (cls.strip(), html.escape(label.strip())))
    return "\n            ".join(out)


def build(md_path, html_path):
    meta, body_md = parse_meta(Path(md_path).read_text(encoding="utf-8"))
    title, subtitle, hero, hero_alt, body_md = split_lead(body_md, meta)
    title = title or "Untitled"
    subtitle = subtitle or ""
    byline = meta.get("byline", "")
    footer = meta.get("footer", "")
    badges = render_badges(meta.get("badges", ""))
    body = render_body(body_md)
    hero = meta.get("hero", hero or "")
    hero_img = (
        '        <img class="hero-banner" src="%s" alt="%s">\n'
        % (hero, html.escape(meta.get("hero_alt", hero_alt or title), quote=True))
    ) if hero else ""

    doc = (
        "<!DOCTYPE html>\n" + LICENSE_HEADER + '<html lang="en">\n<head>\n'
        '    <meta charset="UTF-8">\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        "    <title>" + html.escape(title) + "</title>\n"
        '    <link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Outfit:wght@400;600;700&display=swap" rel="stylesheet">\n'
        "    <style>" + CSS + "    </style>\n</head>\n<body>\n"
        '<div class="bg-pattern"></div>\n\n'
        '<header class="hero">\n    <div class="container">\n' + hero_img +
        '        <div class="logo-strip">\n            ' + badges + "\n        </div>\n"
        "        <h1>" + html.escape(title) + "</h1>\n"
        '        <p class="dek">' + inline(subtitle) + "</p>\n"
        '        <div class="byline">' + html.escape(byline) + "</div>\n"
        "    </div>\n</header>\n\n"
        '<article>\n<div class="container">\n\n' + body + "\n\n</div>\n</article>\n\n"
        '<footer>\n    <div class="container">\n        <p>' + html.escape(footer) + "</p>\n"
        "    </div>\n</footer>\n</body>\n</html>\n"
    )
    Path(html_path).write_text(doc, encoding="utf-8")
    return doc


def main(argv):
    here = Path(__file__).resolve().parent
    src = argv[1] if len(argv) > 1 else "blog_vulkan_video_browsers.md"
    dst = argv[2] if len(argv) > 2 else "blog_vulkan_video_browsers.html"
    src = src if Path(src).is_absolute() else here / src
    dst = dst if Path(dst).is_absolute() else here / dst
    build(src, dst)
    print("wrote %s (%d bytes) from %s" % (dst, Path(dst).stat().st_size, Path(src).name))


if __name__ == "__main__":
    main(sys.argv)
