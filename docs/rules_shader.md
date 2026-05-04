# Shader & Visual Review Checklist — Midway to Nowhere
## GLSL 3.3+, Karmic-Temporal Transmutation Matrix, & Visual Effects

### Karmic-Temporal Transmutation Matrix (Dual-Axis Shader)
- [ ] **X-axis** (Temporal): Fades between PS1 vertex snapping (left) and smooth PBR (right).
- [ ] **Y-axis** (Karmic): Fades between Demonic (bottom) and Angelic (top).
- [ ] **Origin (0,0)**: PS1 vertex snapping + Demonic visuals = maximum distortion.
- [ ] **Far corner (1,1)**: Smooth PBR + Angelic bloom = maximum fidelity.
- [ ] Matrix is a **single GLSL shader** with uniform-driven interpolation. Not two separate shaders.
- [ ] Matrix state is driven by the player's Karma modifier value (GDD §4.2). Not by random or time.

### PS1 Vertex Snapping (X-axis negative)
- [ ] Vertex positions are snapped to a coarse grid in **clip space** (not world space).
- [ ] Snap grid size: ~1/256 of clip-space range. Adjustable via uniform.
- [ ] Snapping applies to **all world geometry** (booths, attractions, environment). Not to UI or Barker.
- [ ] Texture sampling uses **nearest-neighbor** (no bilinear filtering) when snap > 50%.
- [ ] No mipmapping when snap > 50%. Use `textureLod` with explicit LOD 0.

### Smooth PBR (X-axis positive)
- [ ] Standard Blinn-Phong or Cook-Torrance BRDF when snap < 10%.
- [ ] Bilinear/trilinear texture filtering. Full mipmap chain.
- [ ] Normal mapping enabled. Parallax mapping optional.
- [ ] Specular highlights, environment reflections.

### Demonic Visuals (Y-axis negative)
- [ ] **Demonic Skew**: 3D simplex/Perlin noise-driven vertex displacement. Applied in world space.
- [ ] Noise frequency: ~0.5–2.0 Hz. Amplitude scales with Karma magnitude.
- [ ] **Film grain**: Screen-space noise overlay. Intensity scales with Demonic severity.
- [ ] **Bruised skies**: Skybox tint shifts toward deep purple/crimson. Desaturation increases.
- [ ] **Flickering shadows**: Shadow map bias oscillates with noise. Not full shadow loss.
- [ ] **Chromatic aberration**: Offset R/G/B channels by noise-driven amount. Max 2px offset.
- [ ] **Vignette**: Dark corners intensify. Max 50% darkening at full Demonic.

### Angelic Visuals (Y-axis positive)
- [ ] **Warm bloom**: Gaussian blur + additive blend on bright regions. Threshold: luminance > 0.7.
- [ ] **Lens flare**: Ghosted artifacts on bright light sources. 3-5 ghost taps.
- [ ] **Soft lighting**: Ambient occlusion radius increases. Shadows soften.
- [ ] **Color grading**: Warm tint (golden hour). Slight saturation boost.
- [ ] **God rays**: Volumetric light shafts from skybox bright spots. Ray march 16-32 steps.

### The Barker's Exemption
- [ ] **Barker's 2D sprite** completely ignores the Transmutation Matrix.
- [ ] Barker renders at full resolution, fully lit, no vertex snapping, no Demonic Skew, no bloom, no grain.
- [ ] Barker uses a separate shader pass or is rendered after the matrix pass with override uniforms.
- [ ] Barker's sprite is always drawn on top of world geometry (highest draw order).
- [ ] Barker's dialog UI also ignores the matrix. Text must remain readable at all Karma levels.

### The Geometric Fracture (Boss Arena Transition)
- [ ] Environment spikes outward: vertex positions extruded along normals. Duration: ~1.5s.
- [ ] Implosion into void: all geometry scales to 0 at a central point. Duration: ~1.0s.
- [ ] Boss arena rises from darkness: geometry scales from 0 to 1, alpha fades in. Duration: ~1.0s.
- [ ] Fracture uses a **separate shader** or render pass. Not part of the Transmutation Matrix.
- [ ] Fracture shader receives a `phase` uniform (0.0–1.0) to drive the animation.

### Billboarding & Sprite Rendering
- [ ] All 2D sprites (Barker, UI elements, particle sprites) use **camera-facing billboarding**.
- [ ] Billboard quad is computed in the vertex shader: `modelView[3]` position, identity rotation.
- [ ] Sprite atlases supported. UV coordinates passed as per-vertex attributes or instance data.
- [ ] Alpha testing for hard edges (cutout). Alpha blending for smooth edges (premultiplied alpha).

### Particle Systems
- [ ] Particles are GL_POINTS with point sprites. Not geometry-based.
- [ ] Max 10,000 active particles. Pooled and recycled.
- [ ] Particle attributes: position, velocity, lifetime, size, color, texture index.
- [ ] Update on CPU (not compute shader — Steam Deck compatibility).
- [ ] Demonic particles: dark embers, smoke, ash. Angelic particles: light motes, sparkles, petals.

### Rendering Pipeline
- [ ] OpenGL 3.3+ core profile. No deprecated fixed-function pipeline.
- [ ] GLEW for extension loading. SDL2 for window/context creation.
- [ ] VBOs for vertex data. IBOs for indexed geometry. VAOs for attribute layout.
- [ ] UBOs for per-frame uniforms (projection, view, light position, matrix state).
- [ ] Shadow mapping: single directional light. 1024x1024 or 2048x2048 shadow map.
- [ ] Deferred shading or forward+ rendering. Not forward rendering (too many lights).
- [ ] Steam Deck target: 30 FPS minimum. 720p internal resolution, upscaled to 800p.

### Shader Organization
- [ ] Vertex shader: Transmutation Matrix vertex snapping + Demonic Skew displacement.
- [ ] Fragment shader: Transmutation Matrix color grading + film grain + bloom.
- [ ] Separate shader for Barker (bypasses matrix).
- [ ] Separate shader for Geometric Fracture.
- [ ] Separate shader for particles (point sprites + alpha).
- [ ] Separate shader for UI (no matrix effects, no lighting).
- [ ] All shaders compiled at load time. No runtime compilation.
- [ ] Shader errors logged to `midway.log` with full info log. Never crash on compile failure.

### Performance Budget
- [ ] Max 50 draw calls per frame (batching where possible).
- [ ] Max 100k vertices per frame (LOD system for distant geometry).
- [ ] Shadow map: max 2048x2048. Cascaded shadow maps if directional light covers large area.
- [ ] Bloom: downscale to 1/4 resolution, 5-tap Gaussian, upscale. Not full-res.
- [ ] Film grain: full-res pass, but only 1 sample per pixel (no multi-sample noise).
- [ ] Chromatic aberration: single-pass offset. Not multi-tap.
