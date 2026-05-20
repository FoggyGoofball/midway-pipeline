"""
_doc_parsers.py — HTML/C++ header parsers and fetch helpers for fetch_api_docs.py
==================================================================================
Extracted from fetch_api_docs.py to keep that file under 1 000 lines.

No circular imports — this module only uses stdlib.
"""
import html.parser
import re
import urllib.request
import urllib.error
from pathlib import Path

# RAW_DIR is resolved relative to this file's directory (docs/_raw/)
RAW_DIR = Path(__file__).parent / "_raw"


# ── HTML Stripper ─────────────────────────────────────────────────────────────

class DocStripper(html.parser.HTMLParser):
    """Strips HTML to plain text, removing nav/footer/aside/script/style."""

    def __init__(self):
        super().__init__()
        self.output = []
        self.skip_tags = {"nav", "footer", "aside", "script", "style",
                          "noscript", "svg", "form", "select", "option"}
        self.skip_depth = 0
        self.in_skip = False
        self.in_pre = False
        self.in_code = False
        self.in_heading = False
        self.heading_level = 0

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        if tag_lower in self.skip_tags:
            self.skip_depth += 1
            self.in_skip = True
            return
        if self.in_skip:
            return
        if tag_lower in ("pre", "code"):
            self.in_code = True
            self.output.append("`")
        elif tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.in_heading = True
            self.heading_level = int(tag_lower[1])
            self.output.append("\n" + "#" * self.heading_level + " ")
        elif tag_lower == "br":
            self.output.append("\n")
        elif tag_lower == "p":
            self.output.append("\n\n")
        elif tag_lower == "li":
            self.output.append("\n- ")
        elif tag_lower == "tr":
            self.output.append("\n")
        elif tag_lower == "td":
            self.output.append(" | ")
        elif tag_lower == "th":
            self.output.append(" | **")
        elif tag_lower == "a":
            for name, val in attrs:
                if name == "href":
                    self.output.append(f"[")
        elif tag_lower == "img":
            for name, val in attrs:
                if name == "alt":
                    self.output.append(f"[Image: {val}]")

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in self.skip_tags:
            self.skip_depth -= 1
            if self.skip_depth <= 0:
                self.skip_depth = 0
                self.in_skip = False
            return
        if self.in_skip:
            return
        if tag_lower in ("pre", "code"):
            self.in_code = False
            self.output.append("`")
        elif tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.in_heading = False
            self.output.append("\n")
        elif tag_lower == "a":
            self.output.append("]")
        elif tag_lower == "th":
            self.output.append("** | ")

    def handle_data(self, data):
        if self.in_skip:
            return
        if self.in_code:
            self.output.append(data)
        else:
            cleaned = re.sub(r'\s+', ' ', data).strip()
            if cleaned:
                self.output.append(cleaned)

    def get_text(self):
        text = "".join(self.output)
        text = re.sub(r'\n{3,}', '\n\n', text)
        lines = [l.strip() for l in text.split('\n')]
        return '\n'.join(lines)


# ── C++ Header Parser ─────────────────────────────────────────────────────────

def parse_cpp_header(content: str, section_name: str) -> str:
    """Extract and format a C++ header section into condensed markdown."""
    lines = content.split('\n')
    output = []
    in_namespace = False
    namespace_depth = 0
    current_class = None
    brace_depth = 0
    in_comment_block = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith("// Copyright") or stripped.startswith("// SPDX"):
            continue
        if stripped.startswith("/*") and "Copyright" in stripped:
            in_comment_block = True
            continue
        if in_comment_block:
            if "*/" in stripped:
                in_comment_block = False
            continue

        ns_match = re.match(r'namespace\s+(\w+)\s*\{?', stripped)
        if ns_match and not stripped.endswith(';'):
            if not in_namespace:
                output.append(f"\n### Namespace: {ns_match.group(1)}\n")
                in_namespace = True
            namespace_depth += 1
            continue

        if stripped == '}' and namespace_depth > 0:
            namespace_depth -= 1
            if namespace_depth == 0:
                in_namespace = False
            continue

        cls_name = None
        cls_base = None
        cls_type = None

        m = re.match(
            r'(class|struct)\s+(?:'
            r'(?:\w+(?:_EXPORT)?(?:_GCC_BUG_WORKAROUND)?\s+)?'
            r'(\w+))'
            r'(?:\s*:\s*(?:public|protected|private)\s+(\w+))?'
            r'(?:\s*\{)?$',
            stripped
        )
        if m:
            cls_type = m.group(1)
            cls_name = m.group(2)
            cls_base = m.group(3) if m.lastindex and m.lastindex >= 3 else None

        if not cls_name and stripped in ('class', 'struct'):
            ahead = []
            for j in range(i + 1, min(i + 7, len(lines))):
                ahead.append(lines[j].strip())
            combined = ' '.join(ahead)
            m2 = re.search(
                r'(?:\w+(?:_EXPORT)?(?:_GCC_BUG_WORKAROUND)?\s+)?(\w+)'
                r'(?:\s*:\s*(?:public|protected|private)\s+(\w+))?',
                combined
            )
            if m2:
                cls_name = m2.group(1)
                cls_base = m2.group(2) if m2.lastindex and m2.lastindex >= 2 else None
                cls_type = 'class' if stripped == 'class' else 'struct'

        if not cls_name:
            m3 = re.match(r'(class|struct)\s+(?:JPH_EXPORT\s+)?(\w+)\s*\{', stripped)
            if m3:
                cls_type = m3.group(1)
                cls_name = m3.group(2)

        if cls_name and cls_type:
            anchor = cls_name.lower()
            output.append(f"\n---\n### {cls_type}: {cls_name} <a name=\"{anchor}\"></a>")
            if cls_base:
                output.append(f"  *Inherits: {cls_base}*")
            current_class = cls_name
            brace_depth = 1
            continue

        if '{' in stripped and current_class:
            brace_depth += stripped.count('{')
            continue
        if '}' in stripped and current_class:
            brace_depth -= stripped.count('}')
            if brace_depth <= 0:
                current_class = None
                brace_depth = 0
            continue

        enum_match = re.match(r'enum(?:\s+class)?\s+(\w+)\s*\{', stripped)
        if enum_match:
            enum_name = enum_match.group(1)
            anchor = enum_name.lower()
            output.append(f"\n### Enum: {enum_name} <a name=\"{anchor}\"></a>")
            output.append("```cpp")
            enum_lines = []
            depth = 1
            for j in range(i + 1, min(i + 50, len(lines))):
                el = lines[j].strip()
                if '{' in el:
                    depth += el.count('{')
                if '}' in el:
                    depth -= el.count('}')
                    if depth <= 0:
                        break
                if el and not el.startswith('//'):
                    enum_lines.append(el)
            output.extend(enum_lines)
            output.append("```")
            continue

        func_match = re.match(
            r'(virtual\s+)?(static\s+)?(const\s+)?([\w][\w<>:,\s*&]*)\s+'
            r'(\w+)\s*\(([^)]*)\)\s*(const\s*)?(=\s*0\s*)?;',
            stripped
        )
        if func_match and current_class:
            return_type = func_match.group(4).strip()
            func_name = func_match.group(5)
            params = func_match.group(6).strip()
            is_virtual = bool(func_match.group(1))
            is_pure = bool(func_match.group(8))
            is_const = bool(func_match.group(7))
            prefix = ""
            if is_virtual:
                prefix += "virtual "
            if is_pure:
                prefix += "pure "
            if is_const:
                prefix += "const "
            output.append(f"- {prefix}`{return_type} {func_name}({params})`")
            continue

        member_match = re.match(
            r'(m\w+|\w+)\s*:\s*(\w[\w<>,\s*&]*)\s*;',
            stripped
        )
        if member_match and current_class and not stripped.startswith('//'):
            var_name = member_match.group(1)
            var_type = member_match.group(2)
            if var_name.startswith('m') and var_name[1].isupper():
                output.append(f"  - `{var_name}: {var_type}`")

    return '\n'.join(output)


# ── Box2D C API Parser ────────────────────────────────────────────────────────

def parse_box2d_api(content: str) -> str:
    """Parse Box2D C API header into markdown sections grouped by namespace."""
    lines = content.split('\n')
    sections = {}
    current_comment = ""
    in_block_comment = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('/**'):
            in_block_comment = True
            current_comment = stripped[3:].strip()
            continue
        if in_block_comment:
            if '*/' in stripped:
                comment_end = stripped.index('*/')
                current_comment += ' ' + stripped[:comment_end].strip()
                in_block_comment = False
                continue
            current_comment += ' ' + stripped.strip()
            continue

        if stripped.startswith('B2_API '):
            sig = stripped[7:].strip()
            func_name_match = re.match(r'(\w[\w_]*)', sig.split('(')[0].split()[-1] if '(' in sig else sig)
            if func_name_match:
                full_name = func_name_match.group(0)
                ns_match = re.match(r'(b2\w+?)_', full_name)
                ns = ns_match.group(1) if ns_match else "b2"
                if ns not in sections:
                    sections[ns] = {"desc": "", "funcs": []}
                if current_comment and not sections[ns]["desc"]:
                    sections[ns]["desc"] = current_comment
                sections[ns]["funcs"].append(sig)
            current_comment = ""

    parts = []
    ns_order = ["b2World", "b2Body", "b2Shape", "b2Circle", "b2Polygon",
                "b2Segment", "b2Capsule", "b2MassData", "b2Vec2", "b2"]
    order_map = {n: i for i, n in enumerate(ns_order)}

    for ns in sorted(sections.keys(), key=lambda x: order_map.get(x, 999)):
        info = sections[ns]
        anchor = ns.lower()
        parts.append(f"\n---\n### {ns} <a name=\"{anchor}\"></a>")
        if info["desc"]:
            parts.append(f"\n{info['desc']}\n")
        parts.append("```c")
        for func_sig in info["funcs"][:30]:
            parts.append(f"{func_sig}")
        parts.append("```\n")

    return '\n'.join(parts)


# ── Token Counter ─────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars per token for code)."""
    return len(text) // 4


# ── Fetch Helpers ─────────────────────────────────────────────────────────────

def fetch_url(url: str, timeout: int = 30) -> str:
    """Download a URL and return its text content."""
    print(f"  Fetching: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "MidwayDocFetcher/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            print(f"    Got {len(content)} bytes")
            return content
    except urllib.error.HTTPError as e:
        print(f"    HTTP Error {e.code}: {e.reason}")
        return ""
    except urllib.error.URLError as e:
        print(f"    URL Error: {e.reason}")
        return ""
    except Exception as e:
        print(f"    Error: {e}")
        return ""


def save_raw(content: str, filename: str) -> None:
    """Save raw content to docs/_raw/."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / filename
    path.write_text(content, encoding="utf-8")
    print(f"    Saved raw: {path}")
