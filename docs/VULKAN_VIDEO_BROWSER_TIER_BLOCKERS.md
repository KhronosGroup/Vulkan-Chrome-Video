# Two blockers between the browser tier gate and a real verdict

<!-- DOC-STATUS -->
> **Status: CURRENT — measured finding.** Blockers to the tier gate. B-1 corrected after bare-metal measurement.
> Index: [`README.md`](README.md).


**Status:** one is a Chromium upstream gap and cannot be worked around on any
machine. One is machine provisioning and is fixable with a display-manager
restart.

Neither is caused by the encoder library. An A/B against the pre-change
vendored library reproduces **both failures identically** -- same rows, same
messages, same counters -- so nothing here is a regression from the encoder
work.

---

## B-1 - Chromium has no Vulkan device selection under the Wayland ozone platform

**Effect:** every `--ozone-platform=wayland` row that expects a Vulkan encoder
fails with `OperationError: Encoder creation error`, counters all zero,
`adopt=0/0`. The encoder is never constructed.

**Cause, from Chromium's own source.**
`ui/ozone/platform/wayland/gpu/vulkan_implementation_wayland.cc`:

```cpp
bool VulkanImplementationWayland::GetPhysicalDevicePresentationSupport(
    VkPhysicalDevice device,
    const std::vector<VkQueueFamilyProperties>& queue_family_properties,
    uint32_t queue_family_index) {
  NOTIMPLEMENTED();
  return false;
}
```

`gpu/vulkan/vulkan_implementation.cc` binds that method as the predicate every
candidate physical device must satisfy:

```cpp
auto callback = base::BindRepeating(
    &VulkanImplementation::GetPhysicalDevicePresentationSupport,
    base::Unretained(vulkan_implementation));
```

Returning `false` unconditionally disqualifies every device, so the GPU process
never obtains a `VkDevice`. `CreateViewSurface` in the same file is
`NOTIMPLEMENTED()` as well.

Chromium states the limitation itself before any of this, in
`ui/ozone/platform/wayland/gpu/wayland_surface_factory.cc`:

```
'--ozone-platform=wayland' is not compatible with Vulkan.
Consider switching to '--ozone-platform=x11' or disabling Vulkan
```

...and then returns the implementation anyway, so the failure surfaces several
frames later as a video-capability refusal
(`VK_ERROR_VIDEO_PROFILE_OPERATION_NOT_SUPPORTED_KHR`) rather than as the
device-selection failure it is.

**Consequence for the tier gate. CORRECTED — this section overstated the
blocker.** Zero-copy IS reachable and has been measured: 480/480 zero-copy
frames on an RTX 4080 under headless weston, NVENC peak 35%. The upstream
`CreateViewSurface` gap below is real, and Chromium does log that Wayland is
not compatible with Vulkan and disables Vulkan COMPOSITING -- but that does not
stop the encoder reaching zero-copy, which is what this paragraph previously
claimed on any machine with any compositor. The earlier conclusion was drawn on
a VM with no real Wayland session. See
`VULKAN_VIDEO_ENCODE_SURFACES_AND_GPU_TOPOLOGY.md` §1.

**What would have to change upstream:** `GetPhysicalDevicePresentationSupport`
needs a real implementation for Wayland (the X11 ozone platform has one). Until
then, a zero-copy row is asserting something the browser cannot do.

---

## B-2 - A version-mismatched X server yields DRI3 without GBM

**Effect:** `--ozone-platform=x11` rows encode correctly but always land on the
`staging` tier. `handleType dma-buf=0 opaque-fd=480`: the browser hands the
encoder no dma-buf, so there is nothing to import and the cpu-dmabuf tier is
unreachable.

**Cause.** `ui/gfx/linux/gbm_support_x11.cc` obtains an authenticated DRM fd
from the X server over DRI3 and creates a GbmDevice from it:

```cpp
auto& dri3 = connection->dri3();
if (!dri3.present()) { LOG(WARNING) << "dri3 extension not supported."; return nullptr; }
auto reply = dri3.Open({connection->default_root(), 0}).Sync();
...
return ui::CreateGbmDevice(fd.release());
```

Two distinct failures were observed, in order:

1. **No DRI3 at all.** Pointing `DISPLAY` at an Xwayland running under a
   *headless* weston gives `dri3 extension not supported` -- headless weston has
   no DRM device to pass through, so its Xwayland exposes no DRI3. Confirmed
   with `xdpyinfo | grep -c DRI3` returning 0.

2. **DRI3 present, GBM still missing.** Pointing `DISPLAY` at a real X server
   clears that warning, but `Can't create buffer -- gbm device is missing`
   remains. On the machine measured, the running X server had loaded
   **NVIDIA X Driver 580.142** while the loaded kernel driver was **630.00**.
   An X server that predates a driver upgrade is a known cause of DRI3-present
   / GBM-broken.

All other NVIDIA GBM prerequisites were satisfied and are worth checking first
on any host before chasing this:

    /sys/module/nvidia_drm/parameters/modeset        -> Y
    /usr/lib/x86_64-linux-gnu/gbm/nvidia-drm_gbm.so  -> present
    /usr/lib/x86_64-linux-gnu/libnvidia-allocator.so -> present
    /dev/dri/renderD128..130                         -> present

**Fix:** restart the display manager (or reboot) so the X server runs on the
same driver version as the kernel module, then re-run the x11 rows. This was
not done on the measured host because it would disturb an active session.

---

## Checking a host before trusting a tier verdict

    xdpyinfo | grep -c DRI3                  # must be 1
    nvidia-smi --query-gpu=driver_version --format=csv,noheader
    grep -i 'NVIDIA dlloader X Driver' /var/log/Xorg.0.log
    # the two version numbers above must match

A tier of `staging` where `cpu-dmabuf` was expected means "no dma-buf was
handed over", which is a statement about the PRODUCER, not about the encoder.
Read `handleType dma-buf=N opaque-fd=N` in the accelerator's registration
summary before concluding anything about the encoder.
