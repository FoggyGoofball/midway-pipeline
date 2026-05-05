# OpenGL 3.3 + SDL2 API Reference <a name="opengl-sdl-api"></a>
> Source: docs.gl + SDL Wiki | Generated: 2026-04-30
> Focus: Core windowing and rendering

## Table of Contents
- [SDL_CreateWindow](#sdl-createwindow)
- [SDL_GL_CreateContext](#sdl-gl-createcontext)
- [SDL_GL_SetAttribute](#sdl-gl-setattribute)
- [SDL_PollEvent](#sdl-pollevent)
- [SDL_GetWindowSize](#sdl-getwindowsize)
- [glCreateShader](#gl-createshader)
- [glShaderSource](#gl-shadersource)
- [glCompileShader](#gl-compileshader)
- [glCreateProgram](#gl-createprogram)
- [glAttachShader](#gl-attachshader)
- [glLinkProgram](#gl-linkprogram)
- [glUseProgram](#gl-useprogram)
- [glGenVertexArrays](#gl-genvertexarrays)
- [glBindVertexArray](#gl-bindvertexarray)
- [glGenBuffers](#gl-genbuffers)
- [glBindBuffer](#gl-bindbuffer)
- [glBufferData](#gl-bufferdata)
- [glVertexAttribPointer](#gl-vertexattribpointer)
- [glEnableVertexAttribArray](#gl-enablevertexattribarray)
- [glGetUniformLocation](#gl-getuniformlocation)
- [glUniform*](#gl-uniform)

## SDL2 Functions

### SDL_CreateWindow <a name="sdl-createwindow"></a>
```c
SDL_Window* SDL_CreateWindow(
    const char* title,
    int w, int h,
    Uint32 flags
);
```
- `flags`: `SDL_WINDOW_OPENGL | SDL_WINDOW_SHOWN`

### SDL_GL_CreateContext <a name="sdl-gl-createcontext"></a>
```c
SDL_GLContext SDL_GL_CreateContext(SDL_Window* window);
```

### SDL_GL_SetAttribute <a name="sdl-gl-setattribute"></a>
```c
int SDL_GL_SetAttribute(SDL_GLattr attr, int value);
```
Key attributes:
- `SDL_GL_CONTEXT_MAJOR_VERSION = 3`
- `SDL_GL_CONTEXT_MINOR_VERSION = 3`
- `SDL_GL_CONTEXT_PROFILE_MASK = SDL_GL_CONTEXT_PROFILE_CORE`
- `SDL_GL_DOUBLEBUFFER = 1`
- `SDL_GL_DEPTH_SIZE = 24`

### SDL_PollEvent <a name="sdl-pollevent"></a>
```c
int SDL_PollEvent(SDL_Event* event);
```

### SDL_GetWindowSize <a name="sdl-getwindowsize"></a>
```c
int SDL_GetWindowSize(SDL_Window* window, int* w, int* h);
```

## OpenGL 3.3 Core Functions


### glCreateShader <a name="gl-createshader"></a>
```c
GLuint glCreateShader(GLenum shaderType);
```
- `shaderType`: `GL_VERTEX_SHADER`, `GL_FRAGMENT_SHADER`

### glShaderSource <a name="gl-shadersource"></a>
```c
void glShaderSource(
    GLuint shader,
    GLsizei count,
    const GLchar** string,
    const GLint* length
);
```

### glCompileShader <a name="gl-compileshader"></a>
```c
void glCompileShader(GLuint shader);
```

### glCreateProgram <a name="gl-createprogram"></a>
```c
GLuint glCreateProgram(void);
```

### glAttachShader <a name="gl-attachshader"></a>
```c
void glAttachShader(GLuint program, GLuint shader);
```

### glLinkProgram <a name="gl-linkprogram"></a>
```c
void glLinkProgram(GLuint program);
```

### glUseProgram <a name="gl-useprogram"></a>
```c
void glUseProgram(GLuint program);
```

### glGenVertexArrays <a name="gl-genvertexarrays"></a>
```c
void glGenVertexArrays(GLsizei n, GLuint* arrays);
```

### glBindVertexArray <a name="gl-bindvertexarray"></a>
```c
void glBindVertexArray(GLuint array);
```

### glGenBuffers <a name="gl-genbuffers"></a>
```c
void glGenBuffers(GLsizei n, GLuint* buffers);
```

### glBindBuffer <a name="gl-bindbuffer"></a>
```c
void glBindBuffer(GLenum target, GLuint buffer);
```
- `target`: `GL_ARRAY_BUFFER`, `GL_ELEMENT_ARRAY_BUFFER`

### glBufferData <a name="gl-bufferdata"></a>
```c
void glBufferData(
    GLenum target,
    GLsizeiptr size,
    const void* data,
    GLenum usage
);
```
- `usage`: `GL_STATIC_DRAW`, `GL_DYNAMIC_DRAW`, `GL_STREAM_DRAW`

### glVertexAttribPointer <a name="gl-vertexattribpointer"></a>
```c
void glVertexAttribPointer(
    GLuint index,
    GLint size,
    GLenum type,
    GLboolean normalized,
    GLsizei stride,
    const void* pointer
);
```

### glEnableVertexAttribArray <a name="gl-enablevertexattribarray"></a>
```c
void glEnableVertexAttribArray(GLuint index);
```

### glGetUniformLocation <a name="gl-getuniformlocation"></a>
```c
GLint glGetUniformLocation(GLuint program, const GLchar* name);
```

### glUniform* <a name="gl-uniform"></a>
```c
void glUniform1f(GLint location, GLfloat v0);
void glUniform2f(GLint location, GLfloat v0, GLfloat v1);
void glUniform3f(GLint location, GLfloat v0, GLfloat v1, GLfloat v2);
void glUniform4f(GLint location, GLfloat v0, GLfloat v1, GLfloat v2, GLfloat v3);
void glUniformMatrix4fv(GLint location, GLsizei count, GLboolean transpose, const GLfloat* value);
```
