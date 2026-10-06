# Self-hosted real-layout viewer

The `Integer demo` workflow also listens for successful `Integer Q matrix
layout`, `Integer prefix state layout` and `Integer layout` runs. It accepts only checked artifacts from
successful main-branch runs in this repository. A push to this directory
also picks up an already completed GDS run, without rerouting it.

The cloud build restores the published `gds/catalog.json` and every listed
file with its SHA-256 before adding a new layout. This preserves previous
designs during demo-only deployments, even after source artifacts expire.
A failed download, hash check, conversion or browser check leaves the
current Pages deployment intact. Source artifacts are verified against
the GitHub ZIP digest and their own GDS/OAS hashes; DRC/LVS/XOR and mapped
functional checks must pass. Antenna and slew/cap counts remain visible.

Cloud conversion: OAS → KLayout GDS with expanded instance arrays →
GDS2glTF one physical layer at a time → glTF Transform 4.5.0:

```
optimize input.gltf layer.glb --compress meshopt --palette false \
  --texture-compress false --simplify false
```

`--palette false` is mandatory. The build verifies unique material names,
unchanged per-layer colors, no palette texture, and meshopt compression.
The visual mesh is quantized; the original GDS/OAS remains the geometry
reference. Arrays are expanded without flattening cell geometry. The
converter rejects unsupported rotations/magnifications rather than
silently displaying them incorrectly.

Each `gds/<design>/` contains original GDS/OAS, checks, `cells.json`,
per-layer GLBs, viewer metadata and the template-based page. GPU instancing
is retained; only the substrate and upper metal layers load initially.
Switching a layer off disposes its meshes. Cell hover uses a spatial grid
and substrate-plane projection, avoiding ray tests over every transistor
triangle. Three.js 0.169.0 and its decoder are hosted under `gds/assets/`;
the viewer makes no external runtime requests. The integer model remains
at the site root, with an additional 3D entry and a Tiny Tapeout link.

Local checks are stdlib/Node syntax and tiny publication-helper fixtures:

```
python3 viewer3d/check.py
node --check viewer3d/viewer.mjs
node --check viewer3d/check.mjs
```

Do not run `build.py` or `gds2gltf.py` locally. Actions installs pinned
geometry dependencies, converts the actual layout, and tests Chromium
WebGL, layer loading/unloading, height and camera controls, hover, a
390px viewport and the absence of external asset requests before deploy.
Conversion logs, screenshots, browser results and the npm lock are kept
in `integer-layout-viewer-evidence`.

The HTML/visual design follows the H2 `viewer3d/index.html` sample. The
converter is derived from [mbalestrini/GDS2glTF](https://github.com/mbalestrini/GDS2glTF)
(Apache-2.0; license included), via the H2 unique-color version, whose
SHA-256 is `87eab8aefa24e4126a64a28c85babe8a92dffb731d442475b6712f237f9e4c3d`.
Changes: cloud guard, layer argument, linear array assembly instead of
quadratic appends, omission of empty instance nodes and strict transforms.
The layer thickness/color table and triangulation algorithm are retained.
CLI options were checked against the [official glTF Transform documentation](https://gltf-transform.dev/cli)
and KLayout array handling against its [Instance API](https://www.klayout.de/doc-qt5/code/class_Instance.html#method74).
