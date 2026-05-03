#!/usr/bin/env python3
"""
build.py  ·  Ángel Morán's website builder
-------------------------------------------------------------------------------
Reads everything in ./content/ and emits ./content.json which the static
index.html consumes at load time.

Stdlib only — no pip install required.

Notes
-----
- Markdown is converted with a small built-in renderer that preserves $...$
  and $$...$$ math intact (so KaTeX in the browser handles them).
- YAML frontmatter and publications.yaml are parsed by a tiny parser that
  understands the subset we use:  scalars, lists of mappings, '>' blocks.
"""

import json
import os
import re
import sys
from datetime import date

ROOT = os.path.abspath(os.path.dirname(__file__))
CONTENT = os.path.join(ROOT, "content")
OUT = os.path.join(ROOT, "content.json")

# ─────────────────────────────────────────────────────────────────────────────
#  Tiny YAML parser  (handles the subset we use)
# ─────────────────────────────────────────────────────────────────────────────
def parse_yaml(text):
    """
    Supports:
      - List of mappings starting with `- key: value`
      - Mapping with simple scalar values
      - Folded scalars introduced by `: >`
      - Strings, ints, floats, booleans (true/false), nulls (null/~)
      - # comments, blank lines
    """
    lines = text.splitlines()
    # strip comments + trailing whitespace, keep blank lines
    cleaned = []
    for raw in lines:
        # strip comments outside of quoted strings (simple heuristic: no quotes in our docs)
        if "#" in raw:
            # only treat as comment if the # is not inside content (we keep it simple)
            # safe rule: split at " #" or line starts with #
            stripped = raw.lstrip()
            if stripped.startswith("#"):
                cleaned.append("")
                continue
            # inline comment
            idx = raw.find(" #")
            if idx >= 0:
                raw = raw[:idx]
        cleaned.append(raw.rstrip())

    pos = [0]

    def cur():
        while pos[0] < len(cleaned) and cleaned[pos[0]].strip() == "":
            pos[0] += 1
        if pos[0] >= len(cleaned):
            return None
        return cleaned[pos[0]]

    def indent_of(line):
        return len(line) - len(line.lstrip(" "))

    def coerce(v):
        v = v.strip()
        if v == "" or v == "~" or v.lower() == "null":
            return None
        if v.lower() == "true":
            return True
        if v.lower() == "false":
            return False
        # quoted
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            return v[1:-1]
        # number
        try:
            if "." not in v and "e" not in v.lower():
                return int(v)
            return float(v)
        except ValueError:
            pass
        return v

    def parse_block_scalar(base_indent):
        """Read a > folded block (we just join into one paragraph, preserving blank-line breaks)."""
        out_parts = []
        current = []
        while pos[0] < len(cleaned):
            line = cleaned[pos[0]]
            if line.strip() == "":
                if current:
                    out_parts.append(" ".join(current))
                    current = []
                else:
                    out_parts.append("")
                pos[0] += 1
                continue
            ind = indent_of(line)
            if ind <= base_indent:
                break
            current.append(line.strip())
            pos[0] += 1
        if current:
            out_parts.append(" ".join(current))
        # collapse runs: join with single space, blanks become \n\n
        text = ""
        for i, part in enumerate(out_parts):
            if part == "":
                if text and not text.endswith("\n\n"):
                    text += "\n\n"
            else:
                if text and not text.endswith("\n\n") and text:
                    text += " "
                text += part
        return text.strip()

    def parse_mapping(base_indent):
        obj = {}
        while True:
            line = cur()
            if line is None:
                return obj
            ind = indent_of(line)
            if ind < base_indent:
                return obj
            stripped = line.strip()
            if stripped.startswith("- "):
                return obj  # caller handles
            m = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*(.*)$", stripped)
            if not m:
                return obj
            key, rest = m.group(1), m.group(2)
            pos[0] += 1
            if rest == ">" or rest == "|":
                obj[key] = parse_block_scalar(ind)
            elif rest == "":
                # nested mapping or list?
                nxt = cur()
                if nxt is None:
                    obj[key] = None
                elif nxt.lstrip().startswith("- "):
                    obj[key] = parse_list(indent_of(nxt))
                else:
                    obj[key] = parse_mapping(indent_of(nxt))
            else:
                obj[key] = coerce(rest)

    def parse_list(base_indent):
        out = []
        while True:
            line = cur()
            if line is None:
                return out
            ind = indent_of(line)
            stripped = line.strip()
            if ind < base_indent or not stripped.startswith("- "):
                return out
            # consume '- '
            first = stripped[2:]
            pos[0] += 1
            # if "- key: val", treat as start of mapping with that key
            m = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*(.*)$", first)
            if m:
                key, rest = m.group(1), m.group(2)
                item = {}
                # value of first key
                if rest == ">" or rest == "|":
                    item[key] = parse_block_scalar(ind + 2)
                elif rest == "":
                    nxt = cur()
                    if nxt is None:
                        item[key] = None
                    elif nxt.lstrip().startswith("- "):
                        item[key] = parse_list(indent_of(nxt))
                    else:
                        item[key] = parse_mapping(indent_of(nxt))
                else:
                    item[key] = coerce(rest)
                # continue mapping at indent ind+2
                more = parse_mapping(ind + 2)
                item.update(more)
                out.append(item)
            else:
                # bare scalar list item
                out.append(coerce(first))

    line = cur()
    if line is None:
        return []
    if line.lstrip().startswith("- "):
        return parse_list(indent_of(line))
    return parse_mapping(indent_of(line))


def parse_frontmatter(text):
    """Returns (meta_dict, body_str). If no frontmatter, meta is empty."""
    if not text.startswith("---"):
        return {}, text
    # find closing ---
    rest = text[3:]
    m = re.search(r"^---\s*$", rest, re.MULTILINE)
    if not m:
        return {}, text
    fm_text = rest[: m.start()].strip("\n")
    body = rest[m.end():].lstrip("\n")
    meta = parse_yaml(fm_text)
    if not isinstance(meta, dict):
        meta = {}
    return meta, body


# ─────────────────────────────────────────────────────────────────────────────
#  Tiny Markdown → HTML  (preserves $math$, $$math$$ verbatim for KaTeX)
# ─────────────────────────────────────────────────────────────────────────────
def md_to_html(md):
    if md is None:
        return ""

    # 1. Protect math by stashing it
    stash = {}
    def _stash(m):
        key = f"\x00MATH{len(stash)}\x00"
        stash[key] = m.group(0)
        return key
    # display first
    md = re.sub(r"\$\$(.+?)\$\$", _stash, md, flags=re.DOTALL)
    # inline (no spaces around delimiters by convention)
    md = re.sub(r"(?<!\\)\$([^\$\n]+?)\$", _stash, md)

    # 2. Protect fenced code
    def _stash_code(m):
        key = f"\x00CODE{len(stash)}\x00"
        # escape HTML inside the code block
        body = m.group(1)
        body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        stash[key] = f"<pre><code>{body}</code></pre>"
        return key
    md = re.sub(r"```(?:\w+)?\n(.*?)```", _stash_code, md, flags=re.DOTALL)

    # 3. Escape HTML in remaining text (but we'll re-introduce our markdown tags)
    md = md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 4. Headings
    md = re.sub(r"^######\s+(.+)$", r"<h6>\1</h6>", md, flags=re.MULTILINE)
    md = re.sub(r"^#####\s+(.+)$",  r"<h5>\1</h5>", md, flags=re.MULTILINE)
    md = re.sub(r"^####\s+(.+)$",   r"<h4>\1</h4>", md, flags=re.MULTILINE)
    md = re.sub(r"^###\s+(.+)$",    r"<h3>\1</h3>", md, flags=re.MULTILINE)
    md = re.sub(r"^##\s+(.+)$",     r"<h2>\1</h2>", md, flags=re.MULTILINE)
    md = re.sub(r"^#\s+(.+)$",      r"<h2>\1</h2>", md, flags=re.MULTILINE)  # h1→h2 (essay title is the H1)

    # 5. Images  ![alt](src)  → <figure> with caption if alt present
    def _img(m):
        alt, src2 = m.group(1).strip(), m.group(2).strip()
        if alt:
            return ('<figure class="md-figure"><img src="' + src2 + '" alt="' + alt + '">' +
                    '<figcaption>' + alt + '</figcaption></figure>')
        return '<img src="' + src2 + '" alt="">'
    md = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _img, md)

    # 6. Links  [text](href)
    md = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                r'<a href="\2" target="_blank" rel="noopener">\1</a>', md)

    # 7. Bold and italic
    md = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", md)
    md = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", md)
    # underscores for emphasis
    md = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"<em>\1</em>", md)

    # 8. Inline code `...`
    md = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", md)

    # 9. Lists  (very basic: consecutive `- ` lines → <ul>)
    def list_replacer(match):
        block = match.group(0)
        items = []
        for ln in block.splitlines():
            ln = ln.strip()
            if ln.startswith("- "):
                items.append(f"<li>{ln[2:].strip()}</li>")
        return "<ul>" + "".join(items) + "</ul>"
    md = re.sub(r"(?:^- .+(?:\n|$))+", list_replacer, md, flags=re.MULTILINE)

    # 10. Paragraphs: split on blank lines
    paragraphs = re.split(r"\n\s*\n", md)
    out = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # already a block-level element?
        if re.match(r"^\s*<(h\d|ul|ol|pre|blockquote|img)", p):
            out.append(p)
        else:
            # turn single newlines into <br> within a paragraph
            out.append("<p>" + p.replace("\n", "<br>") + "</p>")
    html = "\n".join(out)

    # 11. Restore stashed math + code blocks
    for k, v in stash.items():
        html = html.replace(k, v)

    return html


# ─────────────────────────────────────────────────────────────────────────────
#  Loaders
# ─────────────────────────────────────────────────────────────────────────────
def load_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def date_key(meta, fallback_year=None):
    d = meta.get("date")
    if isinstance(d, date):
        return d.isoformat()
    if isinstance(d, str):
        return d
    if fallback_year:
        return f"{fallback_year}-01-01"
    return "0000-01-01"


def collect_md_dir(folder):
    items = []
    if not os.path.isdir(folder):
        return items
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(folder, name)
        meta, body = parse_frontmatter(load_text(path))
        if meta.get("draft") is True:
            continue
        items.append({
            "id": os.path.splitext(name)[0],
            "title": meta.get("title", name),
            "date": meta.get("date") if isinstance(meta.get("date"), str) else (
                meta.get("date").isoformat() if hasattr(meta.get("date"), "isoformat") else None
            ),
            "type": meta.get("type", "essay"),
            "linked_publication": meta.get("linked_publication"),
            "html": md_to_html(body),
        })
    return items


def collect_photos():
    folder = os.path.join(CONTENT, "photography")
    if not os.path.isdir(folder):
        return []
    exts = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    items = []
    for name in sorted(os.listdir(folder)):
        ext = os.path.splitext(name)[1].lower()
        if ext in exts:
            items.append({
                "src": f"content/photography/{name}",
                "name": os.path.splitext(name)[0],
            })
    return items


def load_publications():
    path = os.path.join(CONTENT, "publications.yaml")
    if not os.path.isfile(path):
        return []
    data = parse_yaml(load_text(path))
    if not isinstance(data, list):
        return []
    out = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if entry.get("draft") is True:
            continue
        out.append(entry)
    return out


def load_profile():
    path = os.path.join(CONTENT, "profile", "about.md")
    if not os.path.isfile(path):
        return {"title": "Profile", "html": ""}
    meta, body = parse_frontmatter(load_text(path))
    # auto-detect portrait photo
    photo = None
    for ext in ("jpg", "jpeg", "png", "webp"):
        candidate = os.path.join(CONTENT, "profile", "photo." + ext)
        if os.path.isfile(candidate):
            photo = "content/profile/photo." + ext
            break
    return {
        "title": meta.get("title", "Profile"),
        "html": md_to_html(body),
        "photo": photo,
        "cv_pdf": "content/profile/cv.pdf"
            if os.path.isfile(os.path.join(CONTENT, "profile", "cv.pdf"))
            else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Build
# ─────────────────────────────────────────────────────────────────────────────
def build():
    if not os.path.isdir(CONTENT):
        print(f"❌  No ./content folder found at {CONTENT}")
        sys.exit(1)

    profile      = load_profile()
    publications = load_publications()
    essays       = collect_md_dir(os.path.join(CONTENT, "essays"))
    outreach     = collect_md_dir(os.path.join(CONTENT, "outreach"))
    photos       = collect_photos()

    # newest first
    essays.sort(key=lambda e: date_key(e), reverse=True)
    outreach.sort(key=lambda e: date_key(e), reverse=True)
    publications.sort(key=lambda p: p.get("year", 0), reverse=True)

    # render outreach HTML preview onto each pub if linked
    outreach_by_pub = {o["linked_publication"]: o for o in outreach if o.get("linked_publication")}
    for p in publications:
        related = p.get("related_outreach")
        if related and related in outreach_by_pub:
            p["divulgacion_html"] = outreach_by_pub[related]["html"]
            p["divulgacion_id"]   = related

    payload = {
        "generated_at": date.today().isoformat(),
        "profile":      profile,
        "publications": publications,
        "essays":       essays,
        "outreach":     outreach,
        "photos":       photos,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"   profile       · {'about.md found' if profile.get('html') else 'missing'}")
    print(f"   publications  · {len(publications)} entries")
    print(f"   essays/poems  · {len(essays)} entries")
    print(f"   outreach      · {len(outreach)} entries")
    print(f"   photos        · {len(photos)} files")
    print(f"   wrote         · {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    build()
