#!/usr/bin/env python3
"""
Midway to Nowhere — Offline API Documentation Acquisition Script
=================================================================
Downloads, strips, and compresses API documentation for the project's
tech stack into context-token-efficient markdown files with anchors.

Dependencies: Python 3.8+ (stdlib only — no pip installs needed)

Usage:
    python fetch_api_docs.py                    # Fetch all docs from web
    python fetch_api_docs.py --from-raw         # Process from docs/_raw/*.html
    python fetch_api_docs.py --list             # List available sources
    python fetch_api_docs.py --source jolt      # Fetch only Jolt docs

Output:
    docs/jolt_api.md
    docs/box2d_api.md
    docs/sol2_api.md
    docs/opengl_sdl_api.md
    docs/cpp17_api.md
    docs/api_index.md
"""

import html.parser
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────

DOCS_DIR = Path(__file__).parent.resolve()
RAW_DIR = DOCS_DIR / "_raw"
TOKEN_BUDGET = 8000  # Max tokens per file before splitting

SOURCES = {
    "jolt": {
        "name": "Jolt Physics",
        "files": [
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Collision/BroadPhase/BroadPhaseLayer.h",
                "local": "jolt_BroadPhaseLayer.h",
                "section": "BroadPhaseLayerInterface",
            },
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Body/BodyCreationSettings.h",
                "local": "jolt_BodyCreationSettings.h",
                "section": "BodyCreationSettings",
            },
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Constraints/TwoBodyConstraint.h",
                "local": "jolt_TwoBodyConstraint.h",
                "section": "TwoBodyConstraint",
            },
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Constraints/Constraint.h",
                "local": "jolt_Constraint.h",
                "section": "Constraint",
            },
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Body/Body.h",
                "local": "jolt_Body.h",
                "section": "Body",
            },
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Body/BodyID.h",
                "local": "jolt_BodyID.h",
                "section": "BodyID",
            },
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Collision/ObjectLayer.h",
                "local": "jolt_ObjectLayer.h",
                "section": "ObjectLayer",
            },
        ],
    },
    "box2d": {
        "name": "Box2D",
        "files": [
            {
                "url": "https://raw.githubusercontent.com/erincatto/box2d/main/include/box2d/box2d.h",
                "local": "box2d_box2d.h",
                "section": "box2d",
            },
        ],
    },

    "sol2": {
        "name": "sol2",
        "files": [
            {
                "url": "https://raw.githubusercontent.com/ThePhD/sol2/develop/include/sol/sol.hpp",
                "local": "sol2_sol.hpp",
                "section": "sol",
            },
        ],
    },
    "opengl_sdl": {
        "name": "OpenGL 3.3 + SDL2",
        "files": [
            {
                "url": "https://raw.githubusercontent.com/libsdl-org/SDL/SDL2/include/SDL_video.h",
                "local": "sdl_video.h",
                "section": "SDL_Video",
            },

        ],
    },
    "cpp17": {
        "name": "C++17 Standard Library",
        "files": [
            {
                "url": "https://en.cppreference.com/mwiki/index.php?title=Special%3AExport&page=cpp%2F17",
                "local": "cpp17_standard.html",
                "section": "C++17 Features",
            },
            {
                "url": "https://en.cppreference.com/mwiki/index.php?title=Special%3AExport&page=cpp%2Futility%2Foptional",
                "local": "cpp17_optional.html",
                "section": "std::optional",
            },
            {
                "url": "https://en.cppreference.com/mwiki/index.php?title=Special%3AExport&page=cpp%2Futility%2Fvariant",
                "local": "cpp17_variant.html",
                "section": "std::variant",
            },
            {
                "url": "https://en.cppreference.com/mwiki/index.php?title=Special%3AExport&page=cpp%2Fstring%2Fbasic_string_view",
                "local": "cpp17_string_view.html",
                "section": "std::string_view",
            },
            {
                "url": "https://en.cppreference.com/mwiki/index.php?title=Special%3AExport&page=cpp%2Ffilesystem",
                "local": "cpp17_filesystem.html",
                "section": "std::filesystem",
            },
        ],
    },
}


# ── HTML Parser for Stripping ──────────────────────────────────────────────

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
            # Collapse whitespace
            cleaned = re.sub(r'\s+', ' ', data).strip()
            if cleaned:
                self.output.append(cleaned)

    def get_text(self):
        text = "".join(self.output)
        # Collapse multiple newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Remove leading/trailing whitespace per line
        lines = [l.strip() for l in text.split('\n')]
        return '\n'.join(lines)


# ── C++ Header Parser ──────────────────────────────────────────────────────

def parse_cpp_header(content: str, section_name: str) -> str:
    """Extract and format a C++ header section into condensed markdown.
    
    Handles:
    - Standard class/struct declarations
    - Multi-line class declarations (e.g. Jolt's 'class' then '#ifndef' then name)
    - JPH_EXPORT / *_EXPORT macros before class names
    - Member functions (virtual/pure/static/const)
    - Member variables (m-prefixed)
    - Enums
    """
    lines = content.split('\n')
    output = []
    in_namespace = False
    namespace_depth = 0
    current_class = None
    brace_depth = 0
    in_comment_block = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Skip license blocks
        if stripped.startswith("// Copyright") or stripped.startswith("// SPDX"):
            continue
        if stripped.startswith("/*") and "Copyright" in stripped:
            in_comment_block = True
            continue
        if in_comment_block:
            if "*/" in stripped:
                in_comment_block = False
            continue

        # Track namespaces
        ns_match = re.match(r'namespace\s+(\w+)\s*\{?', stripped)
        if ns_match and not stripped.endswith(';'):
            if not in_namespace:
                output.append(f"\n### Namespace: {ns_match.group(1)}\n")
                in_namespace = True
            namespace_depth += 1
            continue

        # Track closing braces for namespace
        if stripped == '}' and namespace_depth > 0:
            namespace_depth -= 1
            if namespace_depth == 0:
                in_namespace = False
            continue

        # ── Class/struct detection (robust) ──
        cls_name = None
        cls_base = None
        cls_type = None

        # Attempt 1: single-line class/struct with optional *_EXPORT macro
        m = re.match(
            r'(class|struct)\s+(?:'                                        # 'class ' or 'struct '
            r'(?:\w+(?:_EXPORT)?(?:_GCC_BUG_WORKAROUND)?\s+)?'            # JPH_EXPORT etc (optional)
            r'(\w+))'                                                      # class name
            r'(?:\s*:\s*(?:public|protected|private)\s+(\w+))?'            # base class (optional)
            r'(?:\s*\{)?$',                                                # opening brace (optional)
            stripped
        )
        if m:
            cls_type = m.group(1)
            cls_name = m.group(2)
            cls_base = m.group(3) if m.lastindex and m.lastindex >= 3 else None

        # Attempt 2: multi-line — 'class' alone on its line
        if not cls_name and stripped in ('class', 'struct'):
            # Look ahead up to 6 lines for the class name
            ahead = []
            for j in range(i + 1, min(i + 7, len(lines))):
                ahead.append(lines[j].strip())
            combined = ' '.join(ahead)
            # Search for: optional macro, then name, optional : base
            m2 = re.search(
                r'(?:\w+(?:_EXPORT)?(?:_GCC_BUG_WORKAROUND)?\s+)?(\w+)'
                r'(?:\s*:\s*(?:public|protected|private)\s+(\w+))?',
                combined
            )
            if m2:
                cls_name = m2.group(1)
                cls_base = m2.group(2) if m2.lastindex and m2.lastindex >= 2 else None
                cls_type = 'class' if stripped == 'class' else 'struct'

        # Attempt 3: single-line 'class Name {'
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

        # Track braces for class body
        if '{' in stripped and current_class:
            brace_depth += stripped.count('{')
            continue
        if '}' in stripped and current_class:
            brace_depth -= stripped.count('}')
            if brace_depth <= 0:
                current_class = None
                brace_depth = 0
            continue

        # Extract enums
        enum_match = re.match(r'enum(?:\s+class)?\s+(\w+)\s*\{', stripped)
        if enum_match:
            enum_name = enum_match.group(1)
            anchor = enum_name.lower()
            output.append(f"\n### Enum: {enum_name} <a name=\"{anchor}\"></a>")
            output.append("```cpp")
            # Collect enum values
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

        # Extract function declarations (methods)
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

        # Extract member variables
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


# ── Box2D C API Parser ─────────────────────────────────────────────────────
#
# Box2D 3.x uses a flat C API (no C++ classes). Functions are declared:
#     B2_API return_type b2Namespace_FunctionName( params );
#
# This parser groups functions by their namespace prefix (b2World_, b2Body_, etc.)
# and formats them into markdown with anchors.

def parse_box2d_api(content: str) -> str:
    """Parse Box2D C API header into markdown sections grouped by namespace."""
    lines = content.split('\n')
    sections = {}  # prefix -> list of function signatures
    current_comment = ""
    in_block_comment = False

    for line in lines:
        stripped = line.strip()

        # Track block comments for section descriptions
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

        # Match B2_API declarations
        if stripped.startswith('B2_API '):
            sig = stripped[7:].strip()  # Remove 'B2_API '
            # Extract namespace prefix (e.g., b2World_, b2Body_, b2Shape_)
            prefix_match = re.match(r'(\w+)_(\w+)\s*\(', sig)
            func_name_match = re.match(r'(\w[\w_]*)', sig.split('(')[0].split()[-1] if '(' in sig else sig)
            if func_name_match:
                full_name = func_name_match.group(0)
                # Determine section from prefix (e.g., b2World, b2Body, b2Shape)
                ns_match = re.match(r'(b2\w+?)_', full_name)
                if ns_match:
                    ns = ns_match.group(1)
                else:
                    ns = "b2"
                if ns not in sections:
                    sections[ns] = {"desc": "", "funcs": []}
                if current_comment and not sections[ns]["desc"]:
                    sections[ns]["desc"] = current_comment
                sections[ns]["funcs"].append(sig)
            current_comment = ""

    # Build markdown
    parts = []
    # Define a preferred order
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
        for func_sig in info["funcs"][:30]:  # limit per section
            parts.append(f"{func_sig}")
        parts.append("```\n")

    return '\n'.join(parts)


# ── Token Counter ──────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars per token for code)."""
    return len(text) // 4


# ── Fetch Helpers ──────────────────────────────────────────────────────────

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


# ── Doc Generation ─────────────────────────────────────────────────────────

def generate_jolt_doc() -> str:
    """Generate docs/jolt_api.md from raw headers."""
    parts = [
        "# Jolt Physics API Reference <a name=\"jolt-api\"></a>",
        f"> Source: Jolt Physics GitHub | Generated: {datetime.now().strftime('%Y-%m-%d')}",
        "> Focus: BroadPhase layers, Body creation, 3D Constraints\n",
        "## Table of Contents",
        "- [BroadPhaseLayerInterface](#broadphaselayerinterface)",
        "- [ObjectLayerPairFilter](#objectlayerpairfilter)",
        "- [BodyCreationSettings](#bodycreationsettings)",
        "- [Body](#body)",
        "- [BodyID](#bodyid)",
        "- [Constraint](#constraint)",
        "- [TwoBodyConstraintSettings](#twobodyconstraintsettings)",
        "- [TwoBodyConstraint](#twobodyconstraint)",
        "- [EMotionType](#emotiontype)",
        "- [EActivation](#eactivation)",
        "",
    ]

    raw_dir = RAW_DIR
    if not raw_dir.is_dir():
        parts.append("> ⚠️ Raw headers not found. Run `fetch_api_docs.py` first.\n")
        return "\n".join(parts)

    # Process each Jolt header
    for file_info in SOURCES["jolt"]["files"]:
        raw_path = raw_dir / file_info["local"]
        if raw_path.is_file():
            content = raw_path.read_text(encoding="utf-8", errors="replace")
            parsed = parse_cpp_header(content, file_info["section"])
            if parsed:
                parts.append(parsed)
        else:
            parts.append(f"\n> ⚠️ {file_info['local']} not found\n")

    return "\n".join(parts)


def generate_box2d_doc() -> str:
    """Generate docs/box2d_api.md from raw headers - uses Box2D C API parser."""
    parts = [
        "# Box2D API Reference <a name=\"box2d-api\"></a>",
        f"> Source: Box2D GitHub | Generated: {datetime.now().strftime('%Y-%m-%d')}",
        "> Focus: Fixture creation, mass data recalculation, world stepping\n",
        "## Table of Contents",
        "- [b2World](#b2world)",
        "- [b2Body](#b2body)",
        "- [b2Shape](#b2shape)",
        "- [b2MassData](#b2massdata)",
        "- [b2Vec2](#b2vec2)",
        "",
    ]

    raw_dir = RAW_DIR
    if not raw_dir.is_dir():
        parts.append("> ⚠️ Raw headers not found. Run `fetch_api_docs.py` first.\n")
        return "\n".join(parts)

    raw_path = raw_dir / "box2d_box2d.h"
    if raw_path.is_file():
        content = raw_path.read_text(encoding="utf-8", errors="replace")
        parsed = parse_box2d_api(content)
        if parsed:
            parts.append(parsed)
    else:
        parts.append("\n> ⚠️ box2d_box2d.h not found. Run `fetch_api_docs.py` first.\n")

    return "\n".join(parts)


def generate_sol2_doc() -> str:
    """Generate docs/sol2_api.md — sol2 API reference with hand-curated content."""
    parts = [
        "# sol2 API Reference <a name=\"sol2-api\"></a>",
        f"> Source: sol2 GitHub | Generated: {datetime.now().strftime('%Y-%m-%d')}",
        "> Focus: C++ to Lua binding, state management, pointer safety\n",
        "## Table of Contents",
        "- [sol::state](#sol-state)",
        "- [sol::state_view](#sol-state-view)",
        "- [sol::function](#sol-function)",
        "- [sol::table](#sol-table)",
        "- [sol::object](#sol-object)",
        "- [sol::new_usertype](#new-usertype)",
        "- [sol::set_function](#set-function)",
        "- [sol::set_usertype](#set-usertype)",
        "- [Pointer Safety](#pointer-safety)",
        "",
    ]

    # Since the sol2_sol.hpp is an aggregator with only #includes,
    # we provide a comprehensive hand-curated reference.
    # This is more reliable than trying to parse individual headers
    # which are deeply templated and use preprocessor-heavy patterns.

    parts.append("""
---
### sol::state <a name="sol-state"></a>
Manages a lua_State* and provides the top-level interface for loading libraries, 
running scripts, and creating bindings.

```cpp
// Construction
sol::state lua;
sol::state lua(sol::c_call<decltype(&my_func), my_func>);

// Open standard libraries
lua.open_libraries(sol::lib::base, sol::lib::math, sol::lib::string);

// Script execution
lua.script("print('hello')");
lua.script_file("script.lua");
auto result = lua.script("return 1 + 2", sol::script_pass_on_error);
int value = result;

// Destructor closes lua_State automatically
```

---
### sol::state_view <a name="sol-state-view"></a>
Non-owning view into an existing lua_State. Useful when you already have 
a lua_State* but need sol2's binding syntax.

```cpp
lua_State* L = luaL_newstate();
sol::state_view lua(L);
lua.open_libraries();
lua.script("x = 42");
lua_close(L);  // state_view does NOT own L
```

---
### sol::function <a name="sol-function"></a>
Wraps a Lua function for calling from C++. Supports any return type 
and up to 20 arguments.

```cpp
// Get a Lua function
sol::function f = lua["my_function"];

// Call it
int result = f(1, 2.5, "hello");

// Protected call with error handling
auto result = f(sol::protected_function_call(1, 2.5));
if (!result.valid()) {
    sol::error err = result;
    std::cerr << err.what();
}

// Bind as std::function
std::function<int(int, int)> adder = lua["add"];
```

---
### sol::table <a name="sol-table"></a>
Represents a Lua table. Provides indexing, iteration, and nested table access.

```cpp
// Global table
sol::table global = lua.global();

// Indexing
int x = global["x"];
global["y"] = 42;

// Nested tables
sol::table config = lua["config"];
double speed = config["speed"];

// Iteration
for (auto& [key, value] : global) {
    // key: sol::object, value: sol::object
}

// Optional access
auto val = global.get<sol::optional<int>>("maybe_key");
if (val) { /* use *val */ }
```

---
### sol::object <a name="sol-object"></a>
Generic reference to any Lua value. Type checking and safe extraction.

```cpp
sol::object obj = lua["value"];

// Type checking
if (obj.is<int>()) { /* ... */ }
if (obj.is<std::string>()) { /* ... */ }
if (obj.get_type() == sol::type::number) { /* ... */ }

// Extraction (throws on mismatch)
int i = obj.as<int>();
std::string s = obj.as<std::string>();

// Safe extraction
int* pi = obj.as<sol::optional<int>>();
```

---
### new_usertype <a name="new-usertype"></a>
Bind a C++ class or struct to Lua with methods, properties, and constructors.

```cpp
struct Player {
    int health;
    void damage(int amount) { health -= amount; }
};

// Bind the type
lua.new_usertype<Player>("Player",
    sol::constructors<Player()>(),
    "health", &Player::health,
    "damage", &Player::damage
);

// From Lua:
// local p = Player.new()
// p:damage(10)
```

---
### set_function <a name="set-function"></a>
Bind a C++ function, lambda, or member function to a Lua name.

```cpp
// Free function
int add(int a, int b) { return a + b; }
lua.set_function("add", add);

// Lambda
lua.set_function("greet", [](const std::string& name) {
    return "Hello, " + name;
});

// Member function on usertype
lua.set_function("player_damage", &Player::damage);

// Lua usage:
// result = add(1, 2)
// greet("world")
```

---
### set_usertype <a name="set-usertype"></a>
Register a usertype on an existing table. Alternative to new_usertype for 
modular binding.

```cpp
sol::usertype<Player> player_type = lua.new_usertype<Player>("Player");
player_type["health"] = &Player::health;
player_type["damage"] = &Player::damage;
// Equivalent to new_usertype but allows additive registration
```""")

    # Pointer safety section (curated, since sol2 aggregator has no content)
    parts.append("""
### Pointer Safety <a name="pointer-safety"></a>
sol2 provides several ownership models for C++ objects exposed to Lua:

```cpp
// 1. unique_ptr (default for new_usertype) — Lua owns the object
lua.new_usertype<Player>("Player",
    sol::constructors<Player()>()
);
// Player created in Lua is destroyed when Lua GC runs

// 2. shared_ptr — shared ownership between C++ and Lua
auto player = std::make_shared<Player>();
lua["player"] = player;

// 3. raw pointer — no ownership transfer (caller manages lifetime)
Player p;
lua["player"] = &p;  // Ensure p outlives Lua usage!

// 4. reference — no ownership, lightweight
lua["player"] = std::ref(p);

// 5. lua_State* access for manual stack operations
lua_State* L = lua.lua_state();
// Push/pop values directly on the Lua stack
```

> **Note:** Prefer `unique_ptr` ownership for objects created in Lua. Use `shared_ptr` 
> when both C++ and Lua need to share lifetime. Never use raw pointers to stack objects 
> that may go out of scope before Lua is done with them.
""")

    return "\n".join(parts)


def generate_opengl_sdl_doc() -> str:
    """Generate docs/opengl_sdl_api.md."""
    parts = [
        "# OpenGL 3.3 + SDL2 API Reference <a name=\"opengl-sdl-api\"></a>",
        f"> Source: docs.gl + SDL Wiki | Generated: {datetime.now().strftime('%Y-%m-%d')}",
        "> Focus: Core windowing and rendering\n",
        "## Table of Contents",
        "- [SDL_CreateWindow](#sdl-createwindow)",
        "- [SDL_GL_CreateContext](#sdl-gl-createcontext)",
        "- [SDL_GL_SetAttribute](#sdl-gl-setattribute)",
        "- [SDL_PollEvent](#sdl-pollevent)",
        "- [SDL_GetWindowSize](#sdl-getwindowsize)",
        "- [glCreateShader](#gl-createshader)",
        "- [glShaderSource](#gl-shadersource)",
        "- [glCompileShader](#gl-compileshader)",
        "- [glCreateProgram](#gl-createprogram)",
        "- [glAttachShader](#gl-attachshader)",
        "- [glLinkProgram](#gl-linkprogram)",
        "- [glUseProgram](#gl-useprogram)",
        "- [glGenVertexArrays](#gl-genvertexarrays)",
        "- [glBindVertexArray](#gl-bindvertexarray)",
        "- [glGenBuffers](#gl-genbuffers)",
        "- [glBindBuffer](#gl-bindbuffer)",
        "- [glBufferData](#gl-bufferdata)",
        "- [glVertexAttribPointer](#gl-vertexattribpointer)",
        "- [glEnableVertexAttribArray](#gl-enablevertexattribarray)",
        "- [glGetUniformLocation](#gl-getuniformlocation)",
        "- [glUniform*](#gl-uniform)",
        "",
        "## SDL2 Functions\n",
        "### SDL_CreateWindow <a name=\"sdl-createwindow\"></a>",
        "```c",
        "SDL_Window* SDL_CreateWindow(",
        "    const char* title,",
        "    int w, int h,",
        "    Uint32 flags",
        ");",
        "```",
        "- `flags`: `SDL_WINDOW_OPENGL | SDL_WINDOW_SHOWN`",
        "",
        "### SDL_GL_CreateContext <a name=\"sdl-gl-createcontext\"></a>",
        "```c",
        "SDL_GLContext SDL_GL_CreateContext(SDL_Window* window);",
        "```",
        "",
        "### SDL_GL_SetAttribute <a name=\"sdl-gl-setattribute\"></a>",
        "```c",
        "int SDL_GL_SetAttribute(SDL_GLattr attr, int value);",
        "```",
        "Key attributes:",
        "- `SDL_GL_CONTEXT_MAJOR_VERSION = 3`",
        "- `SDL_GL_CONTEXT_MINOR_VERSION = 3`",
        "- `SDL_GL_CONTEXT_PROFILE_MASK = SDL_GL_CONTEXT_PROFILE_CORE`",
        "- `SDL_GL_DOUBLEBUFFER = 1`",
        "- `SDL_GL_DEPTH_SIZE = 24`",
        "",
        "### SDL_PollEvent <a name=\"sdl-pollevent\"></a>",
        "```c",
        "int SDL_PollEvent(SDL_Event* event);",
        "```",
        "",
        "### SDL_GetWindowSize <a name=\"sdl-getwindowsize\"></a>",
        "```c",
        "int SDL_GetWindowSize(SDL_Window* window, int* w, int* h);",
        "```",
        "",
        "## OpenGL 3.3 Core Functions\n",
        "",
        "### glCreateShader <a name=\"gl-createshader\"></a>",
        "```c",
        "GLuint glCreateShader(GLenum shaderType);",
        "```",
        "- `shaderType`: `GL_VERTEX_SHADER`, `GL_FRAGMENT_SHADER`",
        "",
        "### glShaderSource <a name=\"gl-shadersource\"></a>",
        "```c",
        "void glShaderSource(",
        "    GLuint shader,",
        "    GLsizei count,",
        "    const GLchar** string,",
        "    const GLint* length",
        ");",
        "```",
        "",
        "### glCompileShader <a name=\"gl-compileshader\"></a>",
        "```c",
        "void glCompileShader(GLuint shader);",
        "```",
        "",
        "### glCreateProgram <a name=\"gl-createprogram\"></a>",
        "```c",
        "GLuint glCreateProgram(void);",
        "```",
        "",
        "### glAttachShader <a name=\"gl-attachshader\"></a>",
        "```c",
        "void glAttachShader(GLuint program, GLuint shader);",
        "```",
        "",
        "### glLinkProgram <a name=\"gl-linkprogram\"></a>",
        "```c",
        "void glLinkProgram(GLuint program);",
        "```",
        "",
        "### glUseProgram <a name=\"gl-useprogram\"></a>",
        "```c",
        "void glUseProgram(GLuint program);",
        "```",
        "",
        "### glGenVertexArrays <a name=\"gl-genvertexarrays\"></a>",
        "```c",
        "void glGenVertexArrays(GLsizei n, GLuint* arrays);",
        "```",
        "",
        "### glBindVertexArray <a name=\"gl-bindvertexarray\"></a>",
        "```c",
        "void glBindVertexArray(GLuint array);",
        "```",
        "",
        "### glGenBuffers <a name=\"gl-genbuffers\"></a>",
        "```c",
        "void glGenBuffers(GLsizei n, GLuint* buffers);",
        "```",
        "",
        "### glBindBuffer <a name=\"gl-bindbuffer\"></a>",
        "```c",
        "void glBindBuffer(GLenum target, GLuint buffer);",
        "```",
        "- `target`: `GL_ARRAY_BUFFER`, `GL_ELEMENT_ARRAY_BUFFER`",
        "",
        "### glBufferData <a name=\"gl-bufferdata\"></a>",
        "```c",
        "void glBufferData(",
        "    GLenum target,",
        "    GLsizeiptr size,",
        "    const void* data,",
        "    GLenum usage",
        ");",
        "```",
        "- `usage`: `GL_STATIC_DRAW`, `GL_DYNAMIC_DRAW`, `GL_STREAM_DRAW`",
        "",
        "### glVertexAttribPointer <a name=\"gl-vertexattribpointer\"></a>",
        "```c",
        "void glVertexAttribPointer(",
        "    GLuint index,",
        "    GLint size,",
        "    GLenum type,",
        "    GLboolean normalized,",
        "    GLsizei stride,",
        "    const void* pointer",
        ");",
        "```",
        "",
        "### glEnableVertexAttribArray <a name=\"gl-enablevertexattribarray\"></a>",
        "```c",
        "void glEnableVertexAttribArray(GLuint index);",
        "```",
        "",
        "### glGetUniformLocation <a name=\"gl-getuniformlocation\"></a>",
        "```c",
        "GLint glGetUniformLocation(GLuint program, const GLchar* name);",
        "```",
        "",
        "### glUniform* <a name=\"gl-uniform\"></a>",
        "```c",
        "void glUniform1f(GLint location, GLfloat v0);",
        "void glUniform2f(GLint location, GLfloat v0, GLfloat v1);",
        "void glUniform3f(GLint location, GLfloat v0, GLfloat v1, GLfloat v2);",
        "void glUniform4f(GLint location, GLfloat v0, GLfloat v1, GLfloat v2, GLfloat v3);",
        "void glUniformMatrix4fv(GLint location, GLsizei count, GLboolean transpose, const GLfloat* value);",
        "```",
        "",
    ]

    return "\n".join(parts)


def generate_cpp17_doc() -> str:
    """Generate docs/cpp17_api.md — curated C++17 standard library reference.
    
    This is a hand-crafted reference covering the C++17 features and library
    types used by the Midway to Nowhere project. Each section has an HTML anchor
    for direct AI lookup via api_index.md.
    """
    parts = [
        "# C++17 Standard Library Reference <a name=\"cpp17-api\"></a>",
        f"> Source: ISO C++17 Standard (cppreference.com) | Generated: {datetime.now().strftime('%Y-%m-%d')}",
        "> Focus: Features and types used by the Midway to Nowhere engine\n",
        "## Table of Contents",
        "",
        "### Language Features",
        "- [Structured Bindings](#structured-bindings)",
        "- [if constexpr](#if-constexpr)",
        "- [Fold Expressions](#fold-expressions)",
        "- [Inline Variables](#inline-variables)",
        "- [[[nodiscard]]](#nodiscard)",
        "- [[[maybe_unused]]](#maybe-unused)",
        "- [[[fallthrough]]](#fallthrough)",
        "- [Template Argument Deduction (CTAD)](#ctad)",
        "",
        "### Standard Library Headers",
        "- [`std::filesystem`](#std-filesystem)",
        "- [`std::optional`](#std-optional)",
        "- [`std::variant`](#std-variant)",
        "- [`std::string_view`](#std-string-view)",
        "- [`std::any`](#std-any)",
        "- [`std::shared_mutex` / `std::shared_lock`](#std-shared-mutex)",
        "- [`std::scoped_lock`](#std-scoped-lock)",
        "- [`std::clamp`](#std-clamp)",
        "- [`std::gcd` / `std::lcm`](#std-gcd-lcm)",
        "- [`std::byte`](#std-byte)",
        "- [`std::apply`](#std-apply)",
        "- [`std::make_from_tuple`](#std-make-from-tuple)",
        "",
        # ── Language Features ──
        "",
        "---",
        "### Structured Bindings <a name=\"structured-bindings\"></a>",
        "Decompose arrays, tuples, pairs, or structs into named variables.",
        "```cpp",
        "// Unpack a std::pair from map insertion",
        "auto [iter, inserted] = myMap.insert({key, value});",
        "",
        "// Unpack a tuple",
        "auto [x, y, z] = getCoords();",
        "",
        "// Decompose a struct (all public non-static members)",
        "struct Point { double x, y, z; };",
        "auto [px, py, pz] = Point{1.0, 2.0, 3.0};",
        "",
        "// With const and reference qualifiers",
        "const auto& [k, v] = *myMap.begin();",
        "```",
        "",
        "---",
        "### if constexpr <a name=\"if-constexpr\"></a>",
        "Compile-time conditional that discards the dead branch from template instantiation.",
        "```cpp",
        "template <typename T>",
        "auto getValue(T v) {",
        "    if constexpr (std::is_integral_v<T>)",
        "        return static_cast<int64_t>(v);",
        "    else if constexpr (std::is_floating_point_v<T>)",
        "        return static_cast<double>(v);",
        "    else",
        "        return v;  // T must have a suitable conversion",
        "}",
        "```",
        "",
        "---",
        "### Fold Expressions <a name=\"fold-expressions\"></a>",
        "Reduce a parameter pack over a binary operator (4 forms).",
        "```cpp",
        "template <typename... Args>",
        "auto sum(Args... args) {",
        "    return (args + ...);          // unary right fold",
        "}",
        "",
        "template <typename... Args>",
        "bool allTrue(Args... args) {",
        "    return (true && ... && args); // binary left fold with init",
        "}",
        "",
        "template <typename... Args>",
        "void printAll(Args... args) {",
        "    (std::cout << ... << args);   // binary left fold over <<",
        "}",
        "```",
        "",
        "---",
        "### Inline Variables <a name=\"inline-variables\"></a>",
        "Define global constants/variables in headers without ODR violations.",
        "```cpp",
        "// header.h",
        "inline constexpr float GRAVITY = -9.81f;",
        "inline int globalCounter = 0;  // single definition across TUs",
        "```",
        "",
        "---",
        "### [[nodiscard]] <a name=\"nodiscard\"></a>",
        "Warns if return value is discarded. Apply to function or entire type.",
        "```cpp",
        "[[nodiscard]] bool tryActivate(int slotID);",
        "// Caller MUST check return value",
        "",
        "struct [[nodiscard]] ErrorCode { int code; };",
        "ErrorCode doSomething();  // return value cannot be ignored",
        "```",
        "",
        "---",
        "### [[maybe_unused]] <a name=\"maybe-unused\"></a>",
        "Suppress unused-variable warnings for deliberately unused names.",
        "```cpp",
        "void onEvent([[maybe_unused]] int eventType) {",
        "    // eventType may not be used yet",
        "}",
        "",
        "void example() {",
        "    [[maybe_unused]] int debugCounter = 0;",
        "    // OK: intentionally unused in release builds",
        "}",
        "```",
        "",
        "---",
        "### [[fallthrough]] <a name=\"fallthrough\"></a>",
        "Explicitly mark intentional fallthrough in switch statements.",
        "```cpp",
        "switch (state) {",
        "    case State::Idle:",
        "        prepareResources();",
        "        [[fallthrough]];",
        "    case State::Ready:",
        "        startProcessing();",
        "        break;",
        "}",
        "```",
        "",
        "---",
        "### Class Template Argument Deduction (CTAD) <a name=\"ctad\"></a>",
        "Template arguments deduced from constructor arguments.",
        "```cpp",
        "std::pair p(1, 2.5);              // deduced pair<int, double>",
        "std::optional o(42);              // deduced optional<int>",
        "std::mutex mtx;",
        "std::scoped_lock lock(mtx);       // deduced scoped_lock<mutex>",
        "std::vector v = {1, 2, 3};       // deduced vector<int>",
        "```",
        "",
        # ── Standard Library ──
        "",
        "---",
        "### std::filesystem <a name=\"std-filesystem\"></a>",
        "Filesystem traversal, path manipulation, file I/O (C++17 <filesystem>).",
        "```cpp",
        "#include <filesystem>",
        "namespace fs = std::filesystem;",
        "",
        "// Path construction & inspection",
        "fs::path dir(\"attractions/plinko\");",
        "dir.filename();      // \"plinko\"",
        "dir.extension();     // \"\" (no ext)",
        "dir.parent_path();   // \"attractions\"",
        "dir.root_name();     // \"\"",
        "",
        "// Directory iteration",
        "for (const auto& entry : fs::directory_iterator(\"./attractions\")) {",
        "    if (entry.is_regular_file() && entry.path().extension() == \".lua\")",
        "        loadScript(entry.path());",
        "}",
        "",
        "// Existence & status",
        "fs::exists(path);",
        "fs::is_directory(path);",
        "fs::is_regular_file(path);",
        "fs::file_size(path);",
        "fs::absolute(path);",
        "",
        "// Operations",
        "fs::create_directory(dir);",
        "fs::copy(source, dest, fs::copy_options::recursive);",
        "fs::remove(path);",
        "fs::remove_all(dir);",
        "fs::rename(old, new_path);",
        "fs::space(path);  // capacity, free, available",
        "",
        "// File times",
        "auto ftime = fs::last_write_time(path);",
        "// Error handling: overloads taking std::error_code&",
        "std::error_code ec;",
        "fs::create_directory(dir, ec);",
        "if (ec) { /* handle */ }",
        "```",
        "",
        "---",
        "### std::optional <a name=\"std-optional\"></a>",
        "A container that may or may not contain a value (C++17 <optional>).",
        "```cpp",
        "#include <optional>",
        "",
        "std::optional<int> tryParse(const std::string& s);",
        "",
        "// Usage patterns",
        "auto val = tryParse(\"42\");",
        "if (val) {                          // bool check",
        "    use(val.value());               // access with check",
        "}",
        "int x = val.value_or(0);            // default fallback",
        "auto& ref = val.value();            // throws bad_optional_access if empty",
        "",
        "// In-place construction",
        "std::optional<Expensive> opt;",
        "opt.emplace(arg1, arg2);            // no copy/move required",
        "",
        "// Monadic operations (C++23, but usable via helpers in C++17)",
        "val.transform([](int v) { return v * 2; });",
        "val.and_then([](int v) -> std::optional<int> { ... });",
        "val.or_else([] { return std::optional<int>(0); });",
        "```",
        "",
        "---",
        "### std::variant <a name=\"std-variant\"></a>",
        "Type-safe union (C++17 <variant>). Holds one of several types.",
        "```cpp",
        "#include <variant>",
        "",
        "using Value = std::variant<int, float, std::string>;",
        "Value v = 42;",
        "v = 3.14f;",
        "v = std::string(\"hello\");",
        "",
        "// Visiting (primary access pattern)",
        "std::visit([](auto&& arg) {",
        "    std::cout << arg;",
        "}, v);",
        "",
        "// Type-index based access",
        "if (std::holds_alternative<int>(v)) {",
        "    int i = std::get<int>(v);      // throws if wrong type",
        "}",
        "int* pi = std::get_if<int>(&v);     // nullptr if wrong type",
        "",
        "// Common idiom: variant + visit = visitor pattern",
        "struct ShapeVisitor {",
        "    void operator()(const Circle& c) { /* ... */ }",
        "    void operator()(const Rect& r)   { /* ... */ }",
        "};",
        "std::visit(ShapeVisitor{}, shapeVariant);",
        "```",
        "",
        "---",
        "### std::string_view <a name=\"std-string-view\"></a>",
        "Non-owning view into a string (C++17 <string_view>). Zero-copy string params.",
        "```cpp",
        "#include <string_view>",
        "",
        "// Preferred over const std::string& for read-only params",
        "void loadScript(std::string_view path) {",
        "    // path is a view into the caller's string — no allocation",
        "    auto pos = path.find('.');",
        "    auto stem = path.substr(0, pos);  // also a string_view",
        "}",
        "",
        "// Literals",
        "using namespace std::string_view_literals;",
        "auto sv = \"hello\"sv;",
        "",
        "// Conversion",
        "std::string s(sv);                   // allocate copy if needed",
        "std::string_view sv2 = s;            // view into std::string (no copy)",
        "const char* cstr = sv.data();        // NOT necessarily null-terminated!",
        "",
        "// Key methods",
        "sv.size(), sv.length(), sv.empty()",
        "sv.front(), sv.back(), sv[0], sv.data()",
        "sv.substr(pos, count)",
        "sv.find(substr), sv.rfind(substr)",
        "sv.starts_with(\"prefix\")",
        "sv.ends_with(\"suffix\")",
        "sv.remove_prefix(n), sv.remove_suffix(n)",
        "```",
        "",
        "---",
        "### std::any <a name=\"std-any\"></a>",
        "Type-erased container for any copy-constructible type (C++17 <any>).",
        "```cpp",
        "#include <any>",
        "",
        "std::any value = 42;",
        "value = std::string(\"hello\");",
        "",
        "// Safe extraction",
        "if (value.has_value()) {",
        "    try {",
        "        int i = std::any_cast<int>(value);",
        "    } catch (const std::bad_any_cast&) {",
        "        // wrong type",
        "    }",
        "}",
        "",
        "// Pointer access (no throw)",
        "auto* ptr = std::any_cast<std::string>(&value);",
        "if (ptr) { use(*ptr); }",
        "```",
        "",
        "---",
        "### std::shared_mutex / std::shared_lock <a name=\"std-shared-mutex\"></a>",
        "Reader-writer mutex (C++17 <shared_mutex>). Multiple readers, exclusive writer.",
        "```cpp",
        "#include <shared_mutex>",
        "",
        "class ThreadSafeCache {",
        "    mutable std::shared_mutex m_mutex;",
        "    std::map<int, Data> m_cache;",
        "",
        "    Data read(int key) const {",
        "        std::shared_lock lock(m_mutex);  // shared (reader) lock",
        "        return m_cache.at(key);",
        "    }",
        "",
        "    void write(int key, Data data) {",
        "        std::unique_lock lock(m_mutex);   // exclusive (writer) lock",
        "        m_cache[key] = data;",
        "    }",
        "};",
        "```",
        "",
        "---",
        "### std::scoped_lock <a name=\"std-scoped-lock\"></a>",
        "Deadlock-safe multiple mutex lock (C++17). Always prefer over std::lock_guard.",
        "```cpp",
        "#include <mutex>",
        "",
        "std::mutex mtx1, mtx2;",
        "",
        "void transfer(Account& from, Account& to, int amount) {",
        "    std::scoped_lock lock(from.mtx, to.mtx);  // deadlock-free ordering",
        "    from.balance -= amount;",
        "    to.balance += amount;",
        "}",
        "```",
        "",
        "---",
        "### std::clamp <a name=\"std-clamp\"></a>",
        "Constrain a value to [lo, hi] (C++17 <algorithm>).",
        "```cpp",
        "#include <algorithm>",
        "",
        "float clamped = std::clamp(value, 0.0f, 1.0f);",
        "int health = std::clamp(currentHP, 0, maxHP);",
        "// Returns reference to lo if value < lo, hi if value > hi, else value",
        "```",
        "",
        "---",
        "### std::gcd / std::lcm <a name=\"std-gcd-lcm\"></a>",
        "Greatest common divisor / least common multiple (C++17 <numeric>).",
        "```cpp",
        "#include <numeric>",
        "",
        "int g = std::gcd(12, 18);   // 6",
        "int l = std::lcm(12, 18);   // 36",
        "// constexpr, works with integer types",
        "```",
        "",
        "---",
        "### std::byte <a name=\"std-byte\"></a>",
        "Type-safe byte representation (C++17 <cstddef>). Not an integer or char.",
        "```cpp",
        "#include <cstddef>",
        "",
        "std::byte b{42};",
        "b <<= 2;                      // shift (not arithmetic)",
        "b |= std::byte{0x0F};",
        "// No implicit conversion to/from integer",
        "int val = std::to_integer<int>(b);",
        "```",
        "",
        "---",
        "### std::apply <a name=\"std-apply\"></a>",
        "Call a function with arguments from a tuple (C++17 <tuple>).",
        "```cpp",
        "#include <tuple>",
        "",
        "auto add = [](int a, float b) { return a + b; };",
        "auto tup = std::make_tuple(1, 2.5f);",
        "float result = std::apply(add, tup);  // 3.5",
        "",
        "// Useful with structured bindings + variadic templates",
        "```",
        "",
        "---",
        "### std::make_from_tuple <a name=\"std-make-from-tuple\"></a>",
        "Construct an object from a tuple of constructor args (C++17 <tuple>).",
        "```cpp",
        "#include <tuple>",
        "",
        "struct Player {",
        "    Player(int id, std::string name) : id(id), name(name) {}",
        "    int id;",
        "    std::string name;",
        "};",
        "",
        "auto args = std::make_tuple(1, \"Alice\");",
        "auto player = std::make_from_tuple<Player>(args);",
        "```",
        "",
    ]
    return "\n".join(parts)


def generate_api_index() -> str:
    """Generate docs/api_index.md — cross-reference lookup table."""
    parts = [
        "# API Documentation Index <a name=\"api-index\"></a>",
        f"> Auto-generated: {datetime.now().strftime('%Y-%m-%d')}",
        "> Use this index to find the correct doc file and anchor for any API lookup.\n",
        "",
        "## Jolt Physics → `docs/jolt_api.md`\n",
        "| Search Term | Section Anchor |",
        "|-------------|----------------|",
        "| BroadPhase layer, collision filter, object layer | [`#broadphaselayerinterface`](jolt_api.md#broadphaselayerinterface) |",
        "| Body creation, BodyCreationSettings, shape | [`#bodycreationsettings`](jolt_api.md#bodycreationsettings) |",
        "| Body, body interface, activation | [`#body`](jolt_api.md#body) |",
        "| BodyID, handle | [`#bodyid`](jolt_api.md#bodyid) |",
        "| Constraints, hinge, slider, fixed, six-dof | [`#constraint`](jolt_api.md#constraint) |",
        "| TwoBodyConstraint, constraint settings | [`#twobodyconstraintsettings`](jolt_api.md#twobodyconstraintsettings) |",
        "| Object layer, collision group | [`#objectlayer`](jolt_api.md#objectlayer) |",
        "| EMotionType, static, kinematic, dynamic | [`#emotiontype`](jolt_api.md#emotiontype) |",
        "",
        "## Box2D → `docs/box2d_api.md`\n",
        "| Search Term | Section Anchor |",
        "|-------------|----------------|",
        "| b2World, world creation, step, queries | [`#b2world`](box2d_api.md#b2world) |",
        "| b2Body, body creation, type, transform | [`#b2body`](box2d_api.md#b2body) |",
        "| b2Shape, shape creation, circle, polygon | [`#b2shape`](box2d_api.md#b2shape) |",
        "| b2MassData, mass, inertia | [`#b2massdata`](box2d_api.md#b2massdata) |",
        "| b2Vec2, vector operations | [`#b2vec2`](box2d_api.md#b2vec2) |",
        "",
        "## sol2 → `docs/sol2_api.md`\n",
        "| Search Term | Section Anchor |",
        "|-------------|----------------|",
        "| sol::state, lua_State, open libraries | [`#sol-state`](sol2_api.md#sol-state) |",
        "| sol::state_view, existing state | [`#sol-state-view`](sol2_api.md#sol-state-view) |",
        "| sol::function, call, operator() | [`#sol-function`](sol2_api.md#sol-function) |",
        "| sol::table, indexing, iteration | [`#sol-table`](sol2_api.md#sol-table) |",
        "| sol::object, type checking, get | [`#sol-object`](sol2_api.md#sol-object) |",
        "| new_usertype, usertype, metatable | [`#new-usertype`](sol2_api.md#new-usertype) |",
        "| set_function, bind C++ function | [`#set-function`](sol2_api.md#set-function) |",
        "| Pointer safety, ownership, unique_ptr | [`#pointer-safety`](sol2_api.md#pointer-safety) |",
        "",
        "## OpenGL 3.3 + SDL2 → `docs/opengl_sdl_api.md`\n",
        "| Search Term | Section Anchor |",
        "|-------------|----------------|",
        "| SDL_CreateWindow, SDL_Window, flags | [`#sdl-createwindow`](opengl_sdl_api.md#sdl-createwindow) |",
        "| SDL_GL_CreateContext, OpenGL context | [`#sdl-gl-createcontext`](opengl_sdl_api.md#sdl-gl-createcontext) |",
        "| SDL_GL_SetAttribute, context profile | [`#sdl-gl-setattribute`](opengl_sdl_api.md#sdl-gl-setattribute) |",
        "| SDL_PollEvent, event loop | [`#sdl-pollevent`](opengl_sdl_api.md#sdl-pollevent) |",
        "| SDL_GetWindowSize, window dimensions | [`#sdl-getwindowsize`](opengl_sdl_api.md#sdl-getwindowsize) |",
        "| glCreateShader, shader compilation | [`#gl-createshader`](opengl_sdl_api.md#gl-createshader) |",
        "| glShaderSource, shader source code | [`#gl-shadersource`](opengl_sdl_api.md#gl-shadersource) |",
        "| glCompileShader, compile shader | [`#gl-compileshader`](opengl_sdl_api.md#gl-compileshader) |",
        "| glCreateProgram, shader program | [`#gl-createprogram`](opengl_sdl_api.md#gl-createprogram) |",
        "| glAttachShader, attach to program | [`#gl-attachshader`](opengl_sdl_api.md#gl-attachshader) |",
        "| glLinkProgram, link shader program | [`#gl-linkprogram`](opengl_sdl_api.md#gl-linkprogram) |",
        "| glUseProgram, activate program | [`#gl-useprogram`](opengl_sdl_api.md#gl-useprogram) |",
        "| glGenVertexArrays, VAO creation | [`#gl-genvertexarrays`](opengl_sdl_api.md#gl-genvertexarrays) |",
        "| glBindVertexArray, VAO binding | [`#gl-bindvertexarray`](opengl_sdl_api.md#gl-bindvertexarray) |",
        "| glGenBuffers, VBO creation | [`#gl-genbuffers`](opengl_sdl_api.md#gl-genbuffers) |",
        "| glBindBuffer, VBO binding | [`#gl-bindbuffer`](opengl_sdl_api.md#gl-bindbuffer) |",
        "| glBufferData, upload vertex data | [`#gl-bufferdata`](opengl_sdl_api.md#gl-bufferdata) |",
        "| glVertexAttribPointer, vertex format | [`#gl-vertexattribpointer`](opengl_sdl_api.md#gl-vertexattribpointer) |",
        "| glEnableVertexAttribArray, enable attrib | [`#gl-enablevertexattribarray`](opengl_sdl_api.md#gl-enablevertexattribarray) |",
        "| glGetUniformLocation, uniform lookup | [`#gl-getuniformlocation`](opengl_sdl_api.md#gl-getuniformlocation) |",
        "| glUniform*, set uniform values | [`#gl-uniform`](opengl_sdl_api.md#gl-uniform) |",
        "",
        "## C++17 Standard Library → `docs/cpp17_api.md`\n",
        "| Search Term | Section Anchor |",
        "|-------------|----------------|",
        "| Structured bindings, decompose tuple/pair/struct | [`#structured-bindings`](cpp17_api.md#structured-bindings) |",
        "| if constexpr, compile-time conditional, SFINAE alternative | [`#if-constexpr`](cpp17_api.md#if-constexpr) |",
        "| Fold expressions, variadic template reduction, parameter pack | [`#fold-expressions`](cpp17_api.md#fold-expressions) |",
        "| Inline variables, ODR-safe header globals, inline constexpr | [`#inline-variables`](cpp17_api.md#inline-variables) |",
        "| nodiscard attribute, [[nodiscard]], must-use return value | [`#nodiscard`](cpp17_api.md#nodiscard) |",
        "| maybe_unused attribute, suppress unused warning | [`#maybe-unused`](cpp17_api.md#maybe-unused) |",
        "| fallthrough attribute, explicit switch fallthrough | [`#fallthrough`](cpp17_api.md#fallthrough) |",
        "| CTAD, class template argument deduction, pair/optional deduction | [`#ctad`](cpp17_api.md#ctad) |",
        "| std::filesystem, directory iteration, path, create_directory | [`#std-filesystem`](cpp17_api.md#std-filesystem) |",
        "| std::optional, optional value, value_or, has_value | [`#std-optional`](cpp17_api.md#std-optional) |",
        "| std::variant, type-safe union, std::visit, holds_alternative | [`#std-variant`](cpp17_api.md#std-variant) |",
        "| std::string_view, zero-copy string, string_view params | [`#std-string-view`](cpp17_api.md#std-string-view) |",
        "| std::any, type-erased value, any_cast | [`#std-any`](cpp17_api.md#std-any) |",
        "| std::shared_mutex, reader-writer lock, shared_lock | [`#std-shared-mutex`](cpp17_api.md#std-shared-mutex) |",
        "| std::scoped_lock, deadlock-safe multiple mutex lock | [`#std-scoped-lock`](cpp17_api.md#std-scoped-lock) |",
        "| std::clamp, clamp value to range, constrain value | [`#std-clamp`](cpp17_api.md#std-clamp) |",
        "| std::gcd, greatest common divisor, std::lcm | [`#std-gcd-lcm`](cpp17_api.md#std-gcd-lcm) |",
        "| std::byte, type-safe byte, to_integer | [`#std-byte`](cpp17_api.md#std-byte) |",
        "| std::apply, call function with tuple arguments | [`#std-apply`](cpp17_api.md#std-apply) |",
        "| std::make_from_tuple, construct from tuple | [`#std-make-from-tuple`](cpp17_api.md#std-make-from-tuple) |",
        "",
    ]

    return "\n".join(parts)


# ── Main ───────────────────────────────────────────────────────────────────

def fetch_all() -> None:
    """Fetch all raw sources from the web."""
    print("=" * 60)
    print("  Fetching API documentation sources...")
    print("=" * 60)

    for source_key, source_info in SOURCES.items():
        print(f"\n--- {source_info['name']} ---")
        for file_info in source_info["files"]:
            content = fetch_url(file_info["url"])
            if content:
                save_raw(content, file_info["local"])

    print("\nDone fetching.")


def generate_all() -> None:
    """Generate all API doc files from raw sources."""
    print("=" * 60)
    print("  Generating API documentation...")
    print("=" * 60)

    generators = [
        ("jolt_api.md", generate_jolt_doc),
        ("box2d_api.md", generate_box2d_doc),
        ("sol2_api.md", generate_sol2_doc),
        ("opengl_sdl_api.md", generate_opengl_sdl_doc),
        ("cpp17_api.md", generate_cpp17_doc),
        ("api_index.md", generate_api_index),
    ]

    for filename, generator in generators:
        print(f"\n  Generating {filename}...")
        content = generator()
        path = DOCS_DIR / filename
        path.write_text(content, encoding="utf-8")
        tokens = estimate_tokens(content)
        print(f"    Written: {path} (~{tokens} tokens)")

    print("\nDone generating.")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Midway API Documentation Acquisition Script"
    )
    parser.add_argument("--from-raw", action="store_true",
                        help="Process from docs/_raw/*.html instead of fetching")
    parser.add_argument("--list", action="store_true",
                        help="List available sources")
    parser.add_argument("--source", type=str, default=None,
                        help="Fetch only a specific source (jolt, box2d, sol2, opengl_sdl)")

    args = parser.parse_args()

    if args.list:
        print("Available sources:")
        for key, info in SOURCES.items():
            print(f"  {key}: {info['name']} ({len(info['files'])} files)")
        return

    if args.from_raw:
        print("Processing from raw files...")
        generate_all()
        return

    if args.source:
        if args.source not in SOURCES:
            print(f"Unknown source: {args.source}")
            print(f"Available: {', '.join(SOURCES.keys())}")
            sys.exit(1)
        print(f"Fetching only: {SOURCES[args.source]['name']}")
        for file_info in SOURCES[args.source]["files"]:
            content = fetch_url(file_info["url"])
            if content:
                save_raw(content, file_info["local"])
        generate_all()
        return

    # Default: fetch all then generate all
    fetch_all()
    generate_all()


if __name__ == "__main__":
    main()
