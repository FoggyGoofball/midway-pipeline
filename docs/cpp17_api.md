# C++17 Standard Library Reference <a name="cpp17-api"></a>
> Source: ISO C++17 Standard (cppreference.com) | Generated: 2026-04-30
> Focus: Features and types used by the Midway to Nowhere engine

## Table of Contents

### Language Features
- [Structured Bindings](#structured-bindings)
- [if constexpr](#if-constexpr)
- [Fold Expressions](#fold-expressions)
- [Inline Variables](#inline-variables)
- [[[nodiscard]]](#nodiscard)
- [[[maybe_unused]]](#maybe-unused)
- [[[fallthrough]]](#fallthrough)
- [Template Argument Deduction (CTAD)](#ctad)

### Standard Library Headers
- [`std::filesystem`](#std-filesystem)
- [`std::optional`](#std-optional)
- [`std::variant`](#std-variant)
- [`std::string_view`](#std-string-view)
- [`std::any`](#std-any)
- [`std::shared_mutex` / `std::shared_lock`](#std-shared-mutex)
- [`std::scoped_lock`](#std-scoped-lock)
- [`std::clamp`](#std-clamp)
- [`std::gcd` / `std::lcm`](#std-gcd-lcm)
- [`std::byte`](#std-byte)
- [`std::apply`](#std-apply)
- [`std::make_from_tuple`](#std-make-from-tuple)


---
### Structured Bindings <a name="structured-bindings"></a>
Decompose arrays, tuples, pairs, or structs into named variables.
```cpp
// Unpack a std::pair from map insertion
auto [iter, inserted] = myMap.insert({key, value});

// Unpack a tuple
auto [x, y, z] = getCoords();

// Decompose a struct (all public non-static members)
struct Point { double x, y, z; };
auto [px, py, pz] = Point{1.0, 2.0, 3.0};

// With const and reference qualifiers
const auto& [k, v] = *myMap.begin();
```

---
### if constexpr <a name="if-constexpr"></a>
Compile-time conditional that discards the dead branch from template instantiation.
```cpp
template <typename T>
auto getValue(T v) {
    if constexpr (std::is_integral_v<T>)
        return static_cast<int64_t>(v);
    else if constexpr (std::is_floating_point_v<T>)
        return static_cast<double>(v);
    else
        return v;  // T must have a suitable conversion
}
```

---
### Fold Expressions <a name="fold-expressions"></a>
Reduce a parameter pack over a binary operator (4 forms).
```cpp
template <typename... Args>
auto sum(Args... args) {
    return (args + ...);          // unary right fold
}

template <typename... Args>
bool allTrue(Args... args) {
    return (true && ... && args); // binary left fold with init
}

template <typename... Args>
void printAll(Args... args) {
    (std::cout << ... << args);   // binary left fold over <<
}
```

---
### Inline Variables <a name="inline-variables"></a>
Define global constants/variables in headers without ODR violations.
```cpp
// header.h
inline constexpr float GRAVITY = -9.81f;
inline int globalCounter = 0;  // single definition across TUs
```

---
### [[nodiscard]] <a name="nodiscard"></a>
Warns if return value is discarded. Apply to function or entire type.
```cpp
[[nodiscard]] bool tryActivate(int slotID);
// Caller MUST check return value

struct [[nodiscard]] ErrorCode { int code; };
ErrorCode doSomething();  // return value cannot be ignored
```

---
### [[maybe_unused]] <a name="maybe-unused"></a>
Suppress unused-variable warnings for deliberately unused names.
```cpp
void onEvent([[maybe_unused]] int eventType) {
    // eventType may not be used yet
}

void example() {
    [[maybe_unused]] int debugCounter = 0;
    // OK: intentionally unused in release builds
}
```

---
### [[fallthrough]] <a name="fallthrough"></a>
Explicitly mark intentional fallthrough in switch statements.
```cpp
switch (state) {
    case State::Idle:
        prepareResources();
        [[fallthrough]];
    case State::Ready:
        startProcessing();
        break;
}
```

---
### Class Template Argument Deduction (CTAD) <a name="ctad"></a>
Template arguments deduced from constructor arguments.
```cpp
std::pair p(1, 2.5);              // deduced pair<int, double>
std::optional o(42);              // deduced optional<int>
std::mutex mtx;
std::scoped_lock lock(mtx);       // deduced scoped_lock<mutex>
std::vector v = {1, 2, 3};       // deduced vector<int>
```


---
### std::filesystem <a name="std-filesystem"></a>
Filesystem traversal, path manipulation, file I/O (C++17 <filesystem>).
```cpp
#include <filesystem>
namespace fs = std::filesystem;

// Path construction & inspection
fs::path dir("attractions/plinko");
dir.filename();      // "plinko"
dir.extension();     // "" (no ext)
dir.parent_path();   // "attractions"
dir.root_name();     // ""

// Directory iteration
for (const auto& entry : fs::directory_iterator("./attractions")) {
    if (entry.is_regular_file() && entry.path().extension() == ".lua")
        loadScript(entry.path());
}

// Existence & status
fs::exists(path);
fs::is_directory(path);
fs::is_regular_file(path);
fs::file_size(path);
fs::absolute(path);

// Operations
fs::create_directory(dir);
fs::copy(source, dest, fs::copy_options::recursive);
fs::remove(path);
fs::remove_all(dir);
fs::rename(old, new_path);
fs::space(path);  // capacity, free, available

// File times
auto ftime = fs::last_write_time(path);
// Error handling: overloads taking std::error_code&
std::error_code ec;
fs::create_directory(dir, ec);
if (ec) { /* handle */ }
```

---
### std::optional <a name="std-optional"></a>
A container that may or may not contain a value (C++17 <optional>).
```cpp
#include <optional>

std::optional<int> tryParse(const std::string& s);

// Usage patterns
auto val = tryParse("42");
if (val) {                          // bool check
    use(val.value());               // access with check
}
int x = val.value_or(0);            // default fallback
auto& ref = val.value();            // throws bad_optional_access if empty

// In-place construction
std::optional<Expensive> opt;
opt.emplace(arg1, arg2);            // no copy/move required

// Monadic operations (C++23, but usable via helpers in C++17)
val.transform([](int v) { return v * 2; });
val.and_then([](int v) -> std::optional<int> { ... });
val.or_else([] { return std::optional<int>(0); });
```

---
### std::variant <a name="std-variant"></a>
Type-safe union (C++17 <variant>). Holds one of several types.
```cpp
#include <variant>

using Value = std::variant<int, float, std::string>;
Value v = 42;
v = 3.14f;
v = std::string("hello");

// Visiting (primary access pattern)
std::visit([](auto&& arg) {
    std::cout << arg;
}, v);

// Type-index based access
if (std::holds_alternative<int>(v)) {
    int i = std::get<int>(v);      // throws if wrong type
}
int* pi = std::get_if<int>(&v);     // nullptr if wrong type

// Common idiom: variant + visit = visitor pattern
struct ShapeVisitor {
    void operator()(const Circle& c) { /* ... */ }
    void operator()(const Rect& r)   { /* ... */ }
};
std::visit(ShapeVisitor{}, shapeVariant);
```

---
### std::string_view <a name="std-string-view"></a>
Non-owning view into a string (C++17 <string_view>). Zero-copy string params.
```cpp
#include <string_view>

// Preferred over const std::string& for read-only params
void loadScript(std::string_view path) {
    // path is a view into the caller's string — no allocation
    auto pos = path.find('.');
    auto stem = path.substr(0, pos);  // also a string_view
}

// Literals
using namespace std::string_view_literals;
auto sv = "hello"sv;

// Conversion
std::string s(sv);                   // allocate copy if needed
std::string_view sv2 = s;            // view into std::string (no copy)
const char* cstr = sv.data();        // NOT necessarily null-terminated!

// Key methods
sv.size(), sv.length(), sv.empty()
sv.front(), sv.back(), sv[0], sv.data()
sv.substr(pos, count)
sv.find(substr), sv.rfind(substr)
sv.starts_with("prefix")
sv.ends_with("suffix")
sv.remove_prefix(n), sv.remove_suffix(n)
```

---
### std::any <a name="std-any"></a>
Type-erased container for any copy-constructible type (C++17 <any>).
```cpp
#include <any>

std::any value = 42;
value = std::string("hello");

// Safe extraction
if (value.has_value()) {
    try {
        int i = std::any_cast<int>(value);
    } catch (const std::bad_any_cast&) {
        // wrong type
    }
}

// Pointer access (no throw)
auto* ptr = std::any_cast<std::string>(&value);
if (ptr) { use(*ptr); }
```

---
### std::shared_mutex / std::shared_lock <a name="std-shared-mutex"></a>
Reader-writer mutex (C++17 <shared_mutex>). Multiple readers, exclusive writer.
```cpp
#include <shared_mutex>

class ThreadSafeCache {
    mutable std::shared_mutex m_mutex;
    std::map<int, Data> m_cache;

    Data read(int key) const {
        std::shared_lock lock(m_mutex);  // shared (reader) lock
        return m_cache.at(key);
    }

    void write(int key, Data data) {
        std::unique_lock lock(m_mutex);   // exclusive (writer) lock
        m_cache[key] = data;
    }
};
```

---
### std::scoped_lock <a name="std-scoped-lock"></a>
Deadlock-safe multiple mutex lock (C++17). Always prefer over std::lock_guard.
```cpp
#include <mutex>

std::mutex mtx1, mtx2;

void transfer(Account& from, Account& to, int amount) {
    std::scoped_lock lock(from.mtx, to.mtx);  // deadlock-free ordering
    from.balance -= amount;
    to.balance += amount;
}
```

---
### std::clamp <a name="std-clamp"></a>
Constrain a value to [lo, hi] (C++17 <algorithm>).
```cpp
#include <algorithm>

float clamped = std::clamp(value, 0.0f, 1.0f);
int health = std::clamp(currentHP, 0, maxHP);
// Returns reference to lo if value < lo, hi if value > hi, else value
```

---
### std::gcd / std::lcm <a name="std-gcd-lcm"></a>
Greatest common divisor / least common multiple (C++17 <numeric>).
```cpp
#include <numeric>

int g = std::gcd(12, 18);   // 6
int l = std::lcm(12, 18);   // 36
// constexpr, works with integer types
```

---
### std::byte <a name="std-byte"></a>
Type-safe byte representation (C++17 <cstddef>). Not an integer or char.
```cpp
#include <cstddef>

std::byte b{42};
b <<= 2;                      // shift (not arithmetic)
b |= std::byte{0x0F};
// No implicit conversion to/from integer
int val = std::to_integer<int>(b);
```

---
### std::apply <a name="std-apply"></a>
Call a function with arguments from a tuple (C++17 <tuple>).
```cpp
#include <tuple>

auto add = [](int a, float b) { return a + b; };
auto tup = std::make_tuple(1, 2.5f);
float result = std::apply(add, tup);  // 3.5

// Useful with structured bindings + variadic templates
```

---
### std::make_from_tuple <a name="std-make-from-tuple"></a>
Construct an object from a tuple of constructor args (C++17 <tuple>).
```cpp
#include <tuple>

struct Player {
    Player(int id, std::string name) : id(id), name(name) {}
    int id;
    std::string name;
};

auto args = std::make_tuple(1, "Alice");
auto player = std::make_from_tuple<Player>(args);
```
