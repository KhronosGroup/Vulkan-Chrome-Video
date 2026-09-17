# Two host limits that cap the Vulkan encode input tier

<!-- DOC-STATUS -->
> **Status: CURRENT — measured finding.** Measured host limits. Wayland half superseded — see the banner inside.
> Index: [`README.md`](README.md).


Both findings below make a browser row report a tier lower than the one it
asserts, or no encoder at all. **Neither is a defect in the Vulkan encoder, the
vendored library, or `media/gpu/vulkan`.** Both are properties of the host and
of upstream Chromium, and both are invisible to every test layer below a real
browser run.

They are written up together because they share a shape: the encoder asks the
platform for something reasonable, the platform refuses, and the code correctly
falls back — so the only symptom is a quieter tier and a green encode.

| | Finding 1 | Finding 2 |
| --- | --- | --- |
| Platform | X11 | Wayland |
| Symptom | tier `staging` where `cpu-dmabuf` is asserted | no VEA at all (`adopt=0/0 vulkan_refused`) |
| Cause | X server runs on a GPU whose GBM cannot allocate NV12 | Chromium has no Vulkan surface for Ozone/Wayland |
| Fixable in our code | No | No |
| Reproduce without a browser | Yes, ~5 s | Yes, source only |

---

## Finding 1 — the cpu-dmabuf tier is decided by which GPU the X server is on

### What the issue is

The `cpu-dmabuf` tier needs one thing from the platform: an **NV12
`SCANOUT_CPU_READ_WRITE`** buffer. Chromium asks Ozone, Ozone asks GBM.

On X11 the caller does not get to choose the GBM device:

- `X11SurfaceFactory::CreateNativePixmap`
  (`ui/ozone/platform/x11/x11_surface_factory.cc:213`) **ignores its
  `gpu::VulkanDeviceQueue* device_queue` argument entirely** and goes straight
  to the process-wide `ui::GBMSupportX11::GetInstance()`.
- `GBMSupportX11` takes whatever DRI3 hands back for the X root window
  (`ui/gfx/linux/gbm_support_x11.cc:53`, `dri3.Open({connection->default_root(), 0})`).

So the tier is decided by **which GPU the X server runs on**. A host whose X
server sits on a device that cannot allocate NV12 can never reach
`cpu-dmabuf`, no matter what the encoder does. `media/gpu/vulkan` passing its
own device queue instead of `nullptr` would change nothing — the argument is
discarded one frame up the stack.

On the reference VM the X server is on the virtio virtual display adapter,
while the encoder runs on the RTX 5080:

```
/dev/dri/renderD128 -> DRIVER=virtio-pci PCI_ID=1AF4:1050   NV12 SCANOUT|LINEAR -> DECLINED
/dev/dri/renderD129 -> DRIVER=nvidia     PCI_ID=10DE:2C02   NV12 SCANOUT|LINEAR -> OK
/dev/dri/renderD130 -> DRIVER=nvidia     PCI_ID=10DE:2208   NV12 SCANOUT|LINEAR -> OK
```

`10DE:2C02` is the device the encoder selects — its own log says
`device ID: 2c02`. The allocation and the encode are therefore on **different
GPUs**, so even a virtio device that could allocate would hand back a dma-buf
from the wrong one. The tier is structurally unavailable here, not degraded.

The code behaves correctly throughout: it detects the refusal and reroutes to
staging rather than shearing frames, and says so
(`media/gpu/vulkan/vulkan_video_encode_accelerator.cc:3252`).

### How to reproduce

**Fastest — no browser, about five seconds:**

```bash
chrome-test-harness/probes/gbm_nv12_per_node.sh
```

It asks every render node for the exact buffer the tier needs and prints the
node identity alongside. A node printing `DECLINED` for `SCANOUT|LINEAR` cannot
serve the tier.

`RENDERING|LINEAR` declining on NVIDIA is **expected and not a fault**: NV12 is
not a renderable format there, and the tier never asks for it.

For *which* node the GPU process actually opens, read
`chrome-test-harness/probes/gbm_device_node.sh`, which answers that question and
documents the same mixed-vendor node-selection fault on a different host (an AMD
iGPU rather than virtio). This is a recurring class, not a one-off.

**End to end, about thirty seconds:**

```bash
cd chrome-test-harness
printf 'u3  h264-main  cpu-i420  x11  cpu-dmabuf\n' > /tmp/x11only.rows
VVEA_CHROME=$HOME/rc/chrome/chrome DISPLAY=:0 XDG_SESSION_TYPE=x11 \
  XDG_RUNTIME_DIR=/run/user/$(id -u) __GL_DEBUG_BYPASS_ASSERT=C \
  ./run_matrix.sh --rows=/tmp/x11only.rows --continue
```

Expected on an affected host:

```
VVEAGATE FAIL [u3/h264-main/cpu-i420/x11] expected tier 'cpu-dmabuf' but
observed 'staging' (zero-copy=0 cpu-dmabuf=0 opaque-fd=480 [cpu total=480])
  page: VVEARESULT ... fed=480 chunks=480 keyframes=1 bytes=159410 err=none
```

The encode itself succeeds — a complete, valid H.264 stream. Only the tier is
lower.

**To see the decision rather than the verdict**, add

```bash
FLAGS+=("--vmodule=vulkan_video_encode_accelerator=1")
```

beside the `--ozone-platform` line in `run_lane.sh` (the harness has no flag
passthrough), re-run, and grep the row log for:

```
Ozone declined a 1280x720 (Y_UV, 420, 8unorm, ExtSamplerOff)
SCANOUT_CPU_READ_WRITE pixmap; CPU frames ride the staging arm for this descriptor.
```

`Y_UV` is NV12 — confirming the request was correct and the platform refused it,
rather than the encoder having asked for something exotic.

### What a fixed host looks like

Run the X server on the NVIDIA GPU. The probe predicts the outcome: if the node
DRI3 returns prints `OK`, the row reaches `cpu-dmabuf`.

---

> **Superseded on bare metal.** Finding 2 below was measured on a VM whose X
> server runs on a virtio adapter and which has no real Wayland session. On an
> RTX 4080 with a real Weston session, **zero-copy works**: `tier 'zero-copy'`,
> 480 frames, NVENC peak 35%. The Wayland finding below is wrong; see
> `E3_X11_ENCODE_PATH_RESPONSE.md` §4. Finding 1 (X11) still stands.

## Finding 2 — zero-copy is unreachable under Ozone/Wayland

### What the issue is

Chromium has no Vulkan surface implementation for Wayland:

```cpp
// ui/ozone/platform/wayland/gpu/vulkan_implementation_wayland.cc:53
std::unique_ptr<gpu::VulkanSurface>
VulkanImplementationWayland::CreateViewSurface(gfx::AcceleratedWidget window) {
  NOTIMPLEMENTED();
  return nullptr;
}
```

Display compositing therefore fails to create its surface, and Chromium
disables Vulkan **for the whole GPU process**:

```
'--ozone-platform=wayland' is not compatible with Vulkan.
  Consider switching to '--ozone-platform=x11' or disabling Vulkan
Failed to create vulkan surface.
```

The Vulkan VEA is offered unconditionally in ADOPT mode
(`media/gpu/gpu_video_encode_accelerator_factory.cc:331`) and adopts the
compositor's `VkInstance`. With Vulkan disabled there is no instance to adopt,
so the accelerator is never constructed — the row reports `adopt=0/0
vulkan_refused` and the page gets an encoder-initialization error.

### The tension this exposes

This is worth an explicit decision rather than a workaround:

- The design documents state that the **zero-copy tier is Wayland-only**.
- This Chromium **cannot run Vulkan on Wayland at all**.

Zero-copy is therefore unreachable today on every window system: unavailable on
X11 by design, and unavailable on Wayland because Vulkan is disabled there.
Closing that gap requires one of:

1. an upstream Vulkan surface for Ozone/Wayland (filling in the
   `NOTIMPLEMENTED` above), or
2. the VEA no longer requiring a compositor Vulkan instance to exist — its
   zero-copy arm is already documented as *device-free*, importing the
   producer's dma-buf with no Chromium `VkImage` and no Chromium `VkDevice`,
   so the dependency is on the instance's mere existence rather than on any
   use of it.

Option 2 is the smaller change and is worth evaluating, but it is a design
decision, not a bug fix.

### How to reproduce

**Source only, no hardware:**

```bash
sed -n '52,56p' ui/ozone/platform/wayland/gpu/vulkan_implementation_wayland.cc
```

That is the entire finding. It is unconditional upstream code, not
configuration.

**Live confirmation:**

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
weston --backend=headless-backend.so --width=1920 --height=1080 \
       --socket=wayland-1 --xwayland &

cd chrome-test-harness
VVEA_CHROME=$HOME/rc/chrome/chrome WAYLAND_DISPLAY=wayland-1 \
  XDG_SESSION_TYPE=wayland DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/$(id -u) \
  __GL_DEBUG_BYPASS_ASSERT=C ./run_matrix.sh --rows=rows/p0.rows --continue
```

Use `--backend=headless-backend.so`, **not** `--backend=headless`. The recipe in
`chrome-test-harness/README.md` is written for a newer Weston and fails on
Weston 10 with `Error: unknown backend "headless"`.

Then grep the row log for the three lines quoted above.

---

## Why no test below layer 3 catches either of these

This is the part worth carrying forward.

- **Layer 1** (`vvs_test_runner.py`, `ctest`) and **layer 2**
  (`video_encode_accelerator_tests`) both create the encoder in **OWN** mode and
  allocate nothing through Ozone. Neither the ADOPT path nor the input tier is
  exercised.
- `media_unittests` uses a fake platform and never reaches a driver.

During the run that produced this document, all of those were green — ctest
60/60, `media_unittests` 9156 passed, layer 2 passing on H.264, H.265 and AV1 —
while the browser could not construct an encoder at all. Two real ADOPT-path
defects were found only at layer 3 and are fixed separately (library
`5c813c9`, Chromium `d3cf2fd7a6e08`).

**Anything touching device adoption or input tiering requires a real browser
run.** That is the gap the harness exists to cover.

## A note on verdict classification

Both findings are currently scored **FAIL** by `gate.sh`, but neither measured
the encoder — they measured the host. By the harness's own three-verdict rule
these are **HARNESS** conditions: nothing was measured, so the setup should be
fixed rather than the row relaxed.

The correct place to fix that is `preflight.sh`, which should probe GBM NV12 on
the X server's DRI device and check Wayland/Vulkan viability, and report
HARNESS **before** the matrix runs. That change is not yet made, because it
alters how results are classified and the precondition should be agreed first.

Until then, read a `staging`-instead-of-`cpu-dmabuf` FAIL on X11, and a
`vulkan_refused` FAIL on Wayland, against this document before treating either
as an encoder regression.
