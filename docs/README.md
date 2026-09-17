# Vulkan Video in browsers — document index

Every document carries a **status banner** under its title. Read the banner
before the document: several here are point-in-time records that deliberately do
**not** describe the current code.

| Status | Meaning |
|---|---|
| **CURRENT** | Describes the code as it is. Fix it if it drifts. |
| **CURRENT — measured finding** | A result from hardware, with the host it was measured on. |
| **COMMUNICATIONS** | Blog/announcement/licensing material, not design. |

---

## Start here

| Document | What it answers |
|---|---|
| [`VULKAN_VIDEO_DEVELOPMENT_PLAN`](VULKAN_VIDEO_DEVELOPMENT_PLAN.md) | The 4-phase plan and where we are in it. |
| [`VULKAN_VIDEO_ENCODE_INTEGRATION_DESIGN_DOCUMENT`](VULKAN_VIDEO_ENCODE_INTEGRATION_DESIGN_DOCUMENT.md) | How encode integrates with Chromium. Design of record. |
| [`VULKAN_VIDEO_INTEGRATION_DESIGN_DOCUMENT`](VULKAN_VIDEO_INTEGRATION_DESIGN_DOCUMENT.md) | The same for decode. |
| [`VULKAN_VIDEO_ENCODER_INTERFACE_DESIGN`](VULKAN_VIDEO_ENCODER_INTERFACE_DESIGN.md) | The public `vk::video::enc` interface: roles, `Query<>`, the two exported symbols. |

## The interface and its rules

- [`VULKAN_VIDEO_ENCODER_INTERFACE_DESIGN`](VULKAN_VIDEO_ENCODER_INTERFACE_DESIGN.md) — roles, lifetimes, `IPlatformLifetime`.
- [`VULKAN_VIDEO_ENCODER_INTERFACE_STYLE`](VULKAN_VIDEO_ENCODER_INTERFACE_STYLE.md) — the style the interface is held to.
- [`VULKAN_VIDEO_ENCODER_OWNERSHIP_PRINCIPLES`](VULKAN_VIDEO_ENCODER_OWNERSHIP_PRINCIPLES.md) — **cited by source comments in 15 places.** Keep accurate.
- [`VULKAN_VIDEO_ENCODER_CONTEXT_DESIGN`](VULKAN_VIDEO_ENCODER_CONTEXT_DESIGN.md) — context and device ownership (OWN vs ADOPT).
- [`VULKAN_VIDEO_ENCODER_EXT_API_SEQUENCES`](VULKAN_VIDEO_ENCODER_EXT_API_SEQUENCES.md), [`..._EXT_OBJECT_LIFECYCLE`](VULKAN_VIDEO_ENCODER_EXT_OBJECT_LIFECYCLE.md) — the internal descriptor API the interface is a translation over.

## Surfaces, GPUs and why a tier is what it is

**Read this before attributing a tier result to the encoder.**

- [`VULKAN_VIDEO_ENCODE_SURFACES_AND_GPU_TOPOLOGY`](VULKAN_VIDEO_ENCODE_SURFACES_AND_GPU_TOPOLOGY.md) — X11, Wayland, hybrid GPUs; why the display server and not Chromium picks the GPU on Linux; the capability that gates `cpu-dmabuf`. ([diagram](diagrams/encode_tier_decision.svg))
- [`VULKAN_VIDEO_ENCODE_HOST_TIER_LIMITS`](VULKAN_VIDEO_ENCODE_HOST_TIER_LIMITS.md) — host limits, with the Wayland half superseded by bare-metal measurement.
- [`VULKAN_VIDEO_BROWSER_TIER_BLOCKERS`](VULKAN_VIDEO_BROWSER_TIER_BLOCKERS.md) — blockers to the tier gate; B-1 corrected.
- [`E3_X11_ENCODE_PATH_RESPONSE`](E3_X11_ENCODE_PATH_RESPONSE.md) — the x11 canvas/video defect and its root fix.

## Use cases, testing, and the surrounding stack

- [`VULKAN_VIDEO_ENCODER_PROCESSING_GRAPHS`](VULKAN_VIDEO_ENCODER_PROCESSING_GRAPHS.md) — browser encode use cases U1–U6 and frame flow.
- [`VULKAN_VIDEO_TEST_PLAN`](VULKAN_VIDEO_TEST_PLAN.md), [`TEST_INFRASTRUCTURE_AND_FALLBACK`](TEST_INFRASTRUCTURE_AND_FALLBACK.md) — test strategy across the three layers.
- [`VULKAN_VIDEO_LIBRARY_INTEGRATION_ANALYSIS`](VULKAN_VIDEO_LIBRARY_INTEGRATION_ANALYSIS.md) — **defines the `G-*` / `G-NEW-*` ids used in commit messages.**
- [`CHROMIUM_PRESENTATION_LAYER`](CHROMIUM_PRESENTATION_LAYER.md), [`CHROMIUM_VIDEO_DECODER_ARCHITECTURE_VERIFIED`](CHROMIUM_VIDEO_DECODER_ARCHITECTURE_VERIFIED.md) — Chromium-side background.
- [`VAAPI_vs_VULKAN_VIDEO_DETAILED_COMPARISON`](VAAPI_vs_VULKAN_VIDEO_DETAILED_COMPARISON.md) — comparison with the VA-API path.
- [`VULKAN_VIDEO_GLSLANG_SHADER_COMPILER`](VULKAN_VIDEO_GLSLANG_SHADER_COMPILER.md) — the shader-compiler seam.


## Communications

`blog_vulkan_video_browsers` · `email_announcement` · `APACHE_2_0_LICENSING_REPORT`

---

## Scope of this documentation set

These documents describe **the current design only**. Records of how the API got
here — superseded proposals, point-in-time reviews, evolution notes — are
deliberately not published: a reader integrating against this interface needs to
know what it *is*, and an account of what it used to be is at best noise and at
worst mistaken for current behaviour.

Where a document states a limit or a measurement, it names the hardware and the
configuration it was measured on, so a reader can tell a property of the design
from a property of a host.


## Diagrams

73 SVGs in [`diagrams/`](diagrams/). Every `.svg` link in every document
resolves — checked as part of this audit.
