# sol2 API Reference <a name="sol2-api"></a>
> Source: sol2 GitHub | Generated: 2026-04-30
> Focus: C++ to Lua binding, state management, pointer safety

## Table of Contents
- [sol::state](#sol-state)
- [sol::state_view](#sol-state-view)
- [sol::function](#sol-function)
- [sol::table](#sol-table)
- [sol::object](#sol-object)
- [sol::new_usertype](#new-usertype)
- [sol::set_function](#set-function)
- [sol::set_usertype](#set-usertype)
- [Pointer Safety](#pointer-safety)


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
```

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
