"""
_doc_generators.py — API documentation generators for fetch_api_docs.py
=========================================================================
Extracted from fetch_api_docs.py to keep that file under 1 000 lines.

Import direction (no cycles):
    fetch_api_docs.py
        → _doc_generators.py  (generators)
                → _doc_parsers.py   (parsers + fetch helpers, stdlib only)

Functions that reference SOURCES (the config dict defined in fetch_api_docs.py)
accept it as an explicit parameter to avoid a circular import.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_DOCS_DIR = Path(__file__).parent.resolve()
if str(_DOCS_DIR) not in sys.path:
    sys.path.insert(0, str(_DOCS_DIR))

from _doc_parsers import RAW_DIR, parse_cpp_header, parse_box2d_api

DOCS_DIR = Path(__file__).parent.resolve()


# ── Jolt ─────────────────────────────────────────────────────────────────────

def generate_jolt_doc(sources: dict) -> str:
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

    if not RAW_DIR.is_dir():
        parts.append("> ⚠️ Raw headers not found. Run `fetch_api_docs.py` first.\n")
        return "\n".join(parts)

    for file_info in sources["jolt"]["files"]:
        raw_path = RAW_DIR / file_info["local"]
        if raw_path.is_file():
            content = raw_path.read_text(encoding="utf-8", errors="replace")
            parsed = parse_cpp_header(content, file_info["section"])
            if parsed:
                parts.append(parsed)
        else:
            parts.append(f"\n> ⚠️ {file_info['local']} not found\n")

    return "\n".join(parts)


# ── Box2D ─────────────────────────────────────────────────────────────────────

def generate_box2d_doc() -> str:
    """Generate docs/box2d_api.md from raw headers — uses Box2D C API parser."""
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

    if not RAW_DIR.is_dir():
        parts.append("> ⚠️ Raw headers not found. Run `fetch_api_docs.py` first.\n")
        return "\n".join(parts)

    raw_path = RAW_DIR / "box2d_box2d.h"
    if raw_path.is_file():
        content = raw_path.read_text(encoding="utf-8", errors="replace")
        parsed = parse_box2d_api(content)
        if parsed:
            parts.append(parsed)
    else:
        parts.append("\n> ⚠️ box2d_box2d.h not found. Run `fetch_api_docs.py` first.\n")

    return "\n".join(parts)


# ── sol2 ─────────────────────────────────────────────────────────────────────

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


# ── OpenGL / SDL2 ─────────────────────────────────────────────────────────────

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


# ── C++17 ─────────────────────────────────────────────────────────────────────

def generate_cpp17_doc() -> str:
    """Generate docs/cpp17_api.md — curated C++17 standard library reference."""
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
        "---",
        "### Structured Bindings <a name=\"structured-bindings\"></a>",
        "Decompose arrays, tuples, pairs, or structs into named variables.",
        "```cpp",
        "auto [iter, inserted] = myMap.insert({key, value});",
        "auto [x, y, z] = getCoords();",
        "struct Point { double x, y, z; };",
        "auto [px, py, pz] = Point{1.0, 2.0, 3.0};",
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
        "        return v;",
        "}",
        "```",
        "",
        "---",
        "### Fold Expressions <a name=\"fold-expressions\"></a>",
        "Reduce a parameter pack over a binary operator (4 forms).",
        "```cpp",
        "template <typename... Args>",
        "auto sum(Args... args) { return (args + ...); }",
        "template <typename... Args>",
        "bool allTrue(Args... args) { return (true && ... && args); }",
        "template <typename... Args>",
        "void printAll(Args... args) { (std::cout << ... << args); }",
        "```",
        "",
        "---",
        "### Inline Variables <a name=\"inline-variables\"></a>",
        "Define global constants/variables in headers without ODR violations.",
        "```cpp",
        "inline constexpr float GRAVITY = -9.81f;",
        "inline int globalCounter = 0;",
        "```",
        "",
        "---",
        "### [[nodiscard]] <a name=\"nodiscard\"></a>",
        "```cpp",
        "[[nodiscard]] bool tryActivate(int slotID);",
        "struct [[nodiscard]] ErrorCode { int code; };",
        "```",
        "",
        "---",
        "### [[maybe_unused]] <a name=\"maybe-unused\"></a>",
        "```cpp",
        "void onEvent([[maybe_unused]] int eventType) {}",
        "[[maybe_unused]] int debugCounter = 0;",
        "```",
        "",
        "---",
        "### [[fallthrough]] <a name=\"fallthrough\"></a>",
        "```cpp",
        "case State::Idle:",
        "    prepareResources();",
        "    [[fallthrough]];",
        "case State::Ready:",
        "    startProcessing(); break;",
        "```",
        "",
        "---",
        "### Class Template Argument Deduction (CTAD) <a name=\"ctad\"></a>",
        "```cpp",
        "std::pair p(1, 2.5);",
        "std::optional o(42);",
        "std::scoped_lock lock(mtx);",
        "std::vector v = {1, 2, 3};",
        "```",
        "",
        "---",
        "### std::filesystem <a name=\"std-filesystem\"></a>",
        "```cpp",
        "#include <filesystem>",
        "namespace fs = std::filesystem;",
        "fs::path dir(\"attractions/plinko\");",
        "for (const auto& entry : fs::directory_iterator(\"./attractions\")) {",
        "    if (entry.is_regular_file() && entry.path().extension() == \".lua\")",
        "        loadScript(entry.path());",
        "}",
        "fs::exists(path); fs::is_directory(path); fs::file_size(path);",
        "fs::create_directory(dir); fs::copy(source, dest, fs::copy_options::recursive);",
        "```",
        "",
        "---",
        "### std::optional <a name=\"std-optional\"></a>",
        "```cpp",
        "#include <optional>",
        "std::optional<int> tryParse(const std::string& s);",
        "auto val = tryParse(\"42\");",
        "if (val) { use(val.value()); }",
        "int x = val.value_or(0);",
        "opt.emplace(arg1, arg2);",
        "```",
        "",
        "---",
        "### std::variant <a name=\"std-variant\"></a>",
        "```cpp",
        "#include <variant>",
        "using Value = std::variant<int, float, std::string>;",
        "std::visit([](auto&& arg) { std::cout << arg; }, v);",
        "if (std::holds_alternative<int>(v)) { int i = std::get<int>(v); }",
        "int* pi = std::get_if<int>(&v);",
        "```",
        "",
        "---",
        "### std::string_view <a name=\"std-string-view\"></a>",
        "```cpp",
        "#include <string_view>",
        "void loadScript(std::string_view path) { auto stem = path.substr(0, path.find('.')); }",
        "using namespace std::string_view_literals;",
        "auto sv = \"hello\"sv;",
        "std::string s(sv);",
        "```",
        "",
        "---",
        "### std::any <a name=\"std-any\"></a>",
        "```cpp",
        "#include <any>",
        "std::any value = 42; value = std::string(\"hello\");",
        "auto* ptr = std::any_cast<std::string>(&value);",
        "```",
        "",
        "---",
        "### std::shared_mutex / std::shared_lock <a name=\"std-shared-mutex\"></a>",
        "```cpp",
        "#include <shared_mutex>",
        "mutable std::shared_mutex m_mutex;",
        "std::shared_lock lock(m_mutex);  // reader lock",
        "std::unique_lock lock(m_mutex);  // writer lock",
        "```",
        "",
        "---",
        "### std::scoped_lock <a name=\"std-scoped-lock\"></a>",
        "```cpp",
        "std::scoped_lock lock(from.mtx, to.mtx);  // deadlock-free ordering",
        "```",
        "",
        "---",
        "### std::clamp <a name=\"std-clamp\"></a>",
        "```cpp",
        "float clamped = std::clamp(value, 0.0f, 1.0f);",
        "```",
        "",
        "---",
        "### std::gcd / std::lcm <a name=\"std-gcd-lcm\"></a>",
        "```cpp",
        "int g = std::gcd(12, 18);  // 6",
        "int l = std::lcm(12, 18);  // 36",
        "```",
        "",
        "---",
        "### std::byte <a name=\"std-byte\"></a>",
        "```cpp",
        "std::byte b{42}; b <<= 2; b |= std::byte{0x0F};",
        "int val = std::to_integer<int>(b);",
        "```",
        "",
        "---",
        "### std::apply <a name=\"std-apply\"></a>",
        "```cpp",
        "auto tup = std::make_tuple(1, 2.5f);",
        "float result = std::apply([](int a, float b){ return a + b; }, tup);",
        "```",
        "",
        "---",
        "### std::make_from_tuple <a name=\"std-make-from-tuple\"></a>",
        "```cpp",
        "auto args = std::make_tuple(1, \"Alice\");",
        "auto player = std::make_from_tuple<Player>(args);",
        "```",
        "",
    ]
    return "\n".join(parts)


# ── API Index ─────────────────────────────────────────────────────────────────

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
        "| Fold expressions, variadic template reduction | [`#fold-expressions`](cpp17_api.md#fold-expressions) |",
        "| Inline variables, ODR-safe header globals | [`#inline-variables`](cpp17_api.md#inline-variables) |",
        "| nodiscard attribute, must-use return value | [`#nodiscard`](cpp17_api.md#nodiscard) |",
        "| maybe_unused attribute | [`#maybe-unused`](cpp17_api.md#maybe-unused) |",
        "| fallthrough attribute | [`#fallthrough`](cpp17_api.md#fallthrough) |",
        "| CTAD, class template argument deduction | [`#ctad`](cpp17_api.md#ctad) |",
        "| std::filesystem, directory iteration, path | [`#std-filesystem`](cpp17_api.md#std-filesystem) |",
        "| std::optional, optional value, value_or | [`#std-optional`](cpp17_api.md#std-optional) |",
        "| std::variant, type-safe union, std::visit | [`#std-variant`](cpp17_api.md#std-variant) |",
        "| std::string_view, zero-copy string | [`#std-string-view`](cpp17_api.md#std-string-view) |",
        "| std::any, type-erased value, any_cast | [`#std-any`](cpp17_api.md#std-any) |",
        "| std::shared_mutex, reader-writer lock | [`#std-shared-mutex`](cpp17_api.md#std-shared-mutex) |",
        "| std::scoped_lock, deadlock-safe multiple mutex | [`#std-scoped-lock`](cpp17_api.md#std-scoped-lock) |",
        "| std::clamp, clamp value to range | [`#std-clamp`](cpp17_api.md#std-clamp) |",
        "| std::gcd, greatest common divisor, std::lcm | [`#std-gcd-lcm`](cpp17_api.md#std-gcd-lcm) |",
        "| std::byte, type-safe byte | [`#std-byte`](cpp17_api.md#std-byte) |",
        "| std::apply, call function with tuple arguments | [`#std-apply`](cpp17_api.md#std-apply) |",
        "| std::make_from_tuple, construct from tuple | [`#std-make-from-tuple`](cpp17_api.md#std-make-from-tuple) |",
        "",
    ]

    return "\n".join(parts)
