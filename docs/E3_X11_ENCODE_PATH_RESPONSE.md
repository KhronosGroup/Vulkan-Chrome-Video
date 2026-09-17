# Response to E3: the x11 canvas/video encode path

<!-- DOC-STATUS -->
> **Status: CURRENT — measured finding.** Response to the E3 x11 canvas/video defect, with the root fix.
> Index: [`README.md`](README.md).


Reviewing `E3_X11_ENCODE_PATH.md` and `0001-e3-x11-canvas-input-path.patch`.
Everything below was re-measured on **bare-metal NVIDIA hardware** (RTX 4080,
driver 580.173.02, X on the NVIDIA GPU), not on a VM.

**Summary.** The diagnosis is right, the work-around is sound within its stated
scope, and the write-up is honest about being a work-around. Two corrections:
the root fix the document proposes as option **A is the wrong one to take**, and
option **B alone is sufficient** — it needs no help from A, and it is now
implemented and validated.

---

## 1. The work-around reproduces exactly as described

Applied to `d3cf2fd7a6e08`, built, and run on the affected rows:

| row | RC-7 | RC-7 + work-around |
|---|---|---|
| u4 canvas-gpu x11 | fed nothing, all-zero | 228 frames, 395 KB, `err=none` |
| u3 canvas-gpu x11 | page died, no `VVEADONE` | fed=480 chunks=480, 817 KB, `err=none` |

`factory-miss = 0`, `OpenH264 = 0`, `context-lost = 0` afterwards. The stated
mechanism is the mechanism.

**A note for anyone re-validating.** `shared_image_factory.cc` compiles into
**`libgpu_gles2.so`**, not `libgpu.so`. A first attempt here produced a false
negative from a stale binary; the giveaway was the error reporting a line number
the patched source no longer had. Stage all libraries, not the obvious ones.

---

## 2. §2.3 is right about the outcome, and the reason is worth stating precisely

The document concludes: *"The declaration is unconditional; its satisfiability is
not."*

That is correct in effect, but the mechanism is not that x11 lacks the factory.
`OzoneImageBackingFactory` **is** registered on x11 — registration is gated on
`supports_native_pixmaps` (`shared_image_factory.cc:318-321`), which
`OzonePlatformX11` sets true whenever GBM holds a device
(`ozone_platform_x11.cc:224`) — and it **does** advertise the encode bit
(`ozone_image_backing_factory.cc:68`). What routes the allocation away from it is
`OzonePlatformX11::IsNativePixmapConfigSupported()`, a hardcoded `return false`
that ignores both its arguments (`ozone_platform_x11.cc:236-238`), while
`X11SurfaceFactory::CanCreateNativePixmapForFormat()` already answers the same
question truthfully from GBM (`x11_surface_factory.cc:237`).

So x11 contradicts itself: it registers the factory and then declares that no
configuration is supported.

**This was tested, and making that predicate truthful makes things worse.**
Changing it to consult GBM does move the allocation to the native branch —
confirmed, `gmb_type` becomes `platform` instead of `shared_memory` — but
`OzoneImageBackingFactory` then refuses for reasons beyond the encode bit, and
that branch has **no fallback to shared memory**. The row fails as before. The
predicate is dishonest, but honesty alone does not fix anything, and shipping it
would trade a recoverable failure for an unrecoverable one.

Recorded so it is not retried as a fifth narrower attempt alongside §4's four.

---

## 3. Recommendation on the two proposed root fixes

### A — declare conditionally: **do not take this**

Gating the encode bit on a capability in
`renderable_mappable_shared_image_video_frame_pool.cc` makes the symptom
disappear by formally agreeing that x11 never receives an encode-capable
SharedImage. It converts a defect into policy, and it does so in the requester,
which is the party least able to know what the service can serve. It also leaves
§2.4 — selection having no notion of an optional usage — entirely in place, so
the next usage with the same property reproduces the same crash.

### B — optional usages: **this is the fix, and it is enough**

B addresses the failure mode rather than one instance of it, and it does not
need A. Implemented as `32d771c186641`:

- `OptionalSharedImageUsages()` (`shared_image_usage.h/.cc`) names the usages a
  caller may be granted less than it asked for. It is deliberately narrow: a
  usage qualifies only when the **caller's own contract** says the service may
  decline it and proceed. `VIDEO_ENCODE_ACCELERATOR` qualifies and says so at
  the point it is requested.
- `GetFactoryByUsage()` retries without those usages when nothing serves the
  full set. The retry lives in that one function, so **all five** selection
  sites inherit it — including the native-buffer arm the work-around leaves
  untouched (§5's fourth bullet).
- The granted set is reported back through `granted_usage`, and every call site
  builds its backing from **that**. This closes §5's first bullet: the service
  no longer records a capability the image does not have. A caller handed a
  copy-tier image is described as holding one.

What B does **not** do, and neither does the work-around: report the granted
usage to the *client*. `ClientSharedImage::usage()` still returns the requested
metadata, constructed client-side. That is the mojom plumbing §5 describes, and
it remains open.

---

## 4. Validation of B, on bare metal

Same host, same rows, same harness; only the binary varies.

| row | baseline `d3cf2fd7a6e08` | + fix B |
|---|---|---|
| u4 canvas-gpu x11 | **GPU process crashed** | PASS `cpu-dmabuf` |
| u3 canvas-gpu x11 | **GPU process crashed** | PASS `cpu-dmabuf`, nvenc peak 46% |

The baseline failure is more severe on real hardware than the document records:
the GPU process crashes rather than merely feeding nothing.

Full sweep, 8 row files, **132 rows**: 59 pass, 15 fail, 58 harness, 4 skipped.
The harness count is dominated by absent conformance measurement (12) and codecs
this build does not ship; none of it is encoder behaviour.

Wayland is unaffected and keeps the zero-copy import:

```
wayland  u3/h264-main/cpu-i420  PASS  tier 'zero-copy'  zero-copy=480  nvenc 35%
x11      u3/h264-main/cpu-i420  PASS  tier 'cpu-dmabuf' cpu-dmabuf=398 nvenc 28%
```

**This retracts an earlier finding of ours.** `VULKAN_VIDEO_ENCODE_HOST_TIER_LIMITS.md`
records zero-copy as unreachable on every window system. That conclusion was
drawn on a VM whose X server runs on a virtio adapter; on real hardware with a
real Wayland session, zero-copy works. The document's X11 section still stands —
the tier is decided by which GPU the X server runs on — but its Wayland section
is wrong and is superseded by this.

---

## 5. Mixed-vendor: Intel renderer, NVIDIA encode

Tested on a host carrying both an Intel iGPU and an RTX 4080, with a Weston
compositor rendering on Intel (verified: `GL renderer: Mesa Intel(R) Graphics
(RPL-S)`) and the encoder free to select its own device (it selects the 4080).

Same binary, same row, only the compositor's GPU differing:

| compositor GPU | result |
|---|---|
| NVIDIA | PASS, tier `zero-copy`, 480 frames, nvenc 35% |
| Intel | **browser process killed** |

Root cause, from the compositor's own log — three occurrences against the Intel
compositor, zero against the NVIDIA one:

```
weston:   Error during import, and no known conversion for format AB24 in the renderer
weston:   libwayland: error in client communication
chromium: FATAL wayland_event_watcher.cc:356
          Check failed: err == 32 || err == 104. Wayland protocol error.
```

Chromium allocates its **presentation** buffer (`AB24` = `DRM_FORMAT_ABGR8888`,
the composited page output) on the NVIDIA GPU and submits it to a compositor
whose renderer is Intel, which cannot import it. Weston raises a Wayland
protocol error; Chromium tolerates only errno 32/104 and `CHECK`s on anything
else, so a negotiation failure becomes a browser kill.

**This is not the encoder.** The log shows the Vulkan encoder working normally
right up to the moment of death — `[R10ZCDF] device-free zero-copy resolve OK`
repeatedly, release fences returned, encoding on the NVIDIA GPU as intended. The
compositing path takes the process down around it.

Two separable defects:

1. Chromium selects a presentation buffer unsuited to the compositor's render
   device on mixed-vendor systems. Same class as this bug and as
   `probes/gbm_device_node.sh`: a buffer allocated against one GPU handed to
   code running on another.
2. A Wayland protocol error is a `CHECK`. A recoverable failure kills the
   browser instead of degrading.

A lead, not a finding: `WaylandZwpLinuxDmabuf::OnTrancheFlags` is
`NOTIMPLEMENTED`. Tranche flags are how a compositor communicates which device
and modifiers a buffer must suit, so ignoring them is a plausible reason the
wrong buffer is chosen. It is **not** the discriminator — it prints in passing
runs too — and the link is unproven.

---

## 6. What we recommend

1. **Land the work-around** if a fix is needed before review completes. It is
   sound within its scope and its invariant is exact.
2. **Land B** (`32d771c186641`) as the root fix, and supersede the work-around
   with it rather than stacking them: B covers the same path generically, covers
   the four sites the work-around does not, and keeps the service's record
   truthful.
3. **Drop A.** Record `IsNativePixmapConfigSupported`'s hardcoded `false` as a
   separate upstream defect — it is real — but note that fixing it in isolation
   regresses the path, because the native branch cannot fall back.
4. **File the mixed-vendor presentation-buffer crash separately.** It is not in
   this change's scope and it is not an encoder defect.

## 7. Two notes on the patch as written

- The `DVLOG_IF` near the end of the shared-memory branch still logs
  `CreateLabelForSharedImageUsage(usage)` while the backing was created from
  `shm_usage`. The log will claim an encode-capable image was produced when it
  was not.
- `LogGetFactoryFailed` is called with the reduced set on that path, so a
  genuine failure reports a usage set the caller never asked for. Reporting the
  request and the granted set separately is clearer.
