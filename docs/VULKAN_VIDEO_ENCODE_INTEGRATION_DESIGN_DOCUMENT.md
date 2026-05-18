# Vulkan Video **Encode** Integration into Chromium: Complete Design Document

**Version**: 1.1 (incorporates `VULKAN_VIDEO_ENCODE_DESIGN_REVIEW_FINDINGS.md`)
**Date**: May 19, 2026
**Authors**: Engineering Team
**Status**: Draft (proposal)
**Scope**: Hardware-accelerated **video encoding** for `H.264`, `H.265/HEVC` and `AV1` via `VK_KHR_video_encode_*` extensions, integrated as a first-class backend behind Chromium’s existing `VideoEncodeAccelerator` (VEA) interface.

### Changelog vs v1.0

| # | Section | Change |
|---|---|---|
| F1, F8 | §2.5, §5.3, §11 | HEVC reference switched from non-existent VA-API to **D3D12 H.265** (`media/gpu/windows/d3d12_video_encode_h265_delegate.cc`) plus Vulkan Samples. Added a new D3D12 second-reference subsection. |
| F2 | §8 | Bitstream composition now reserves `dstBufferOffset` and sets `precedingExternallyEncodedBytes` so driver rate control accounts for app-prepended SPS/PPS/VPS/AUD. |
| F3 | §8.2 | Added `VK_KHR_video_maintenance1` inline-query fallback to explicit `vkCmdBeginQuery` / `vkCmdEndQuery` (the path the Vulkan Samples use); restricted the "grow + re-encode on INSUFFICIENT" claim to replay-safe states. |
| F4 | §7.1, new §7.1.1 | Added Vulkan VUID rate-control rules and a new `ValidateRateControl()` location (`VulkanVideoEncoderWrapper`), with the `layerCount == 0` for `DEFAULT`/`DISABLED` constraint enforced. |
| F5 | §7.2 | External / CQP rate-control description now sets the codec-specific `constantQp` (e.g. `VkVideoEncodeH264NaluSliceInfoKHR::constantQp`) **in addition to** keeping `StdVideoEncodeH264SliceHeader::slice_qp_delta` consistent. |
| F6 | §6.3 | Shared-image input path rewritten to go through `CommandBufferHelper::GetSharedImageManager()` → `SharedImageManager::ProduceVideo()` → scoped read access + Vulkan-typed representation, **not** a direct mailbox-to-`VkImage` shortcut. |
| F7 | §6.0, §6.1, §6.2, §6.3 | Distinguish `VideoEncodeAccelerator::Config::StorageType::kGpuMemoryBuffer` (negotiated) from `VideoFrame::storage_type()` (actually seen, today commonly `STORAGE_MAPPABLE_SHARED_IMAGE`). DMA-BUF / shared-handle / AHardwareBuffer are listed as platform realisations of the GPU-backed mode. |
| F9 | new §7.7 | Manual reference buffer control (`Config::manual_reference_buffer_control`, `EncodeOptions::reference_buffers`, `update_buffer`) — MVP: decline; design for slot mapping to `VkVideoEncodeInfoKHR::pReferenceSlots` later. |
| F10 | new §3.7 | Concrete `VideoEncoderInfo` initial values for the Vulkan backend. |
| F11 | §11.1, §11.5 | Device-ownership promoted from "next step" to Phase-1 blocking decision: **private media `VkDevice` first**, compositor-device zero-copy in Phase 4. |
| F12 | §10, §11 | Protected encode is a non-goal for MVP; removed from Phase 5; will only be added when a real Chrome/CDM consumer exists. |
| F13 | §11 | Execution plan reordered to **6 phases** so the first deliverable is H.264 CQP on a private Vulkan device (lowest-risk milestone) before driver RC, shared-image zero-copy, and other codecs. |

---

## Glossary (acronyms used throughout)

| Acronym | Stands for | What it is |
|---|---|---|
| **VEA** | `VideoEncodeAccelerator` (`media/video/video_encode_accelerator.h`) | Chromium's C++ interface for HW video encoders. Mojo-stable; every backend (VA-API, D3D12, MFT, the new Vulkan one) implements it. |
| **VDA** | `VideoDecodeAccelerator` | The decode peer of VEA. |
| **VEA Delegate** | `VideoEncoderDelegate` (per-backend, per-codec) | Backend-specific glue between the generic VEA surface and the codec-specific driver calls (e.g. `H264VaapiVideoEncoderDelegate`, our new `H264VulkanVideoEncoderDelegate`). |
| **VA-API** | Video Acceleration API (`libva`) | Linux/ChromeOS HW codec stack; Chromium's incumbent encode backend on those platforms. |
| **D3D12 VEA** | `D3D12VideoEncodeAccelerator` | Chromium's Windows D3D12-based VEA backend (the canonical second reference for HEVC and SharedImage flow in this design). |
| **MFT** / **MFVEA** | Media Foundation Transform / `MediaFoundationVideoEncodeAccelerator` | Chromium's legacy Windows VEA backend. |
| **DPB** | Decoded / Reconstructed Picture Buffer | The encoder's pool of reconstructed reference frames. Each codec has its own slot-management rules. |
| **GOP** | Group of Pictures | Repeating I/P/B-frame pattern (`IPBBP...`); drives reference-frame management. |
| **IDR** | Instantaneous Decoder Refresh | A keyframe at which all references are discarded; decoder can join the stream. |
| **CQP** | Constant Quantization Parameter | Rate-control mode where every frame uses the same QP. |
| **CBR / VBR** | Constant / Variable Bit Rate | Standard rate-control modes the driver tracks. |
| **SVC** | Scalable Video Coding | Multi-layer encode where layers can be dropped (T = temporal, S = spatial). |
| **SPS / PPS / VPS** | Sequence / Picture / Video Parameter Set | Codec metadata blobs that must precede payload bytes in Annex-B streams. |
| **AUD** | Access Unit Delimiter | Per-frame separator NALU; Chromium-side compositor wants these. |
| **Std-Video** | The `StdVideo*` family in `vulkan_video_codec_*_std_decode.h` / `_std_encode.h` | Vulkan's normative codec-syntax structs (`StdVideoH264SequenceParameterSet`, etc.) that map 1:1 to spec fields. |
| **SDP** | Session Description Protocol | What WebRTC uses to negotiate codecs across peers. |
| **WPT** | Web Platform Tests | The W3C cross-browser test suite WebCodecs / WebRTC plug into. |
| **OOP-VEA** | Out-of-process VEA | Chromium pattern where the VEA runs in a sandboxed utility process behind a Mojo interface. Already shipping for VA-API on Linux/ChromeOS. |
| **G-NEW-N** | Gap-resolving New extension #N | Upstream contribution to Vulkan-Video-Samples that closes a gap from the sufficiency table. Catalogued in `VULKAN_VIDEO_LIBRARY_INTEGRATION_ANALYSIS.md` §3. |

---

## Table of Contents

1.  [Executive Summary](#1-executive-summary)
2.  [Current HW-Accelerated Encode Architecture (VA-API)](#2-current-vaapi) — §2.5 D3D12 second reference (HEVC, manual refs, shared image)
3.  [Plug-in and Interface Requirements](#3-plugin-requirements) — §3.7 `VideoEncoderInfo` initial values
4.  [Vulkan Video Encode API Design](#4-vulkan-encode-design)
5.  [Mapping: VA-API → Vulkan Video Encode (per codec)](#5-mapping) — §5.3 HEVC from D3D12, **not** VA-API
6.  [Input Path: `VideoFrame` → `VkImage` (VIDEO\_ENCODE\_SRC)](#6-input-path) — §6.3 shared-image representation path
7.  [Rate Control, GOP, Quality Level and QP Maps](#7-rate-control) — §7.1.1 VUIDs, §7.2 per-codec `constantQp`, §7.7 manual ref control
8.  [Bitstream Output, Queries and Feedback](#8-bitstream-output) — §8.2.3 inline-query fallback, §8.3 `dstBufferOffset` + `precedingExternallyEncodedBytes`
9.  [Synchronization, DMA-BUF and Cross-Queue Handoffs](#9-sync)
10. [Protected (DRM) Content for Encode — **Non-goal**](#10-drm)
11. [Execution Plan — 6 phases, H.264 CQP first](#11-execution-plan)
12. [Test Plan](#12-test-plan)
13. [Open Questions, Risks and Speculations](#13-risks)
14. [References](#14-references)

---

## 1. Executive Summary <a name="1-executive-summary"></a>

### 1.1 Project Goal

Add `VulkanVideoEncodeAccelerator` as a peer of `VaapiVideoEncodeAccelerator`, `V4L2VideoEncodeAccelerator`, `D3D12VideoEncodeAccelerator` and `MediaFoundationVideoEncodeAccelerator` so Chromium can use `VK_KHR_video_encode_queue` for:

- Cross-platform HW encode (Linux, Android, Windows, Stadia-like cloud) under a single backend.
- Vendor-agnostic encode that ships in the same Vulkan ICD used by the rasterizer/compositor — no separate VA-API or MFT drivers needed.
- Direct integration with Chromium's Vulkan compositor (`SkiaRenderer`) — input frames stay as `VkImage` end-to-end.
- A unified backend for **decode + encode** (transcoding, WebRTC loopback, screen capture).

### 1.2 Interface boundary — Chromium-side surface is codec-neutral

> **Where does codec-specific code live?** This is the question that drives the integration shape. There are three reasonable layers and only the middle one moves between options:
>
> | Layer | Codec-aware? | Who writes it? |
> |---|---|---|
> | **A.** Chromium VEA / WebRTC / WebCodecs (Mojo-stable) | No — generic `VideoEncodeAccelerator` | Chromium (already exists) |
> | **B.** Codec driver loop: parameter sets, DPB slots, slice headers, `vkCmdEncodeVideoKHR` with `VkVideoEncodeH264PictureInfoKHR` / `StdVideoEncodeH264*` | **Yes — heavily** | Either Chromium (from-scratch plan) or the upstream library (recommended Model D) |
> | **C.** Vulkan driver / `VK_KHR_video_encode_h264` | Yes (this is the codec spec) | NVIDIA / AMD / Intel ICD |
>
> The **recommended Model D / Model A** moves Layer B *inside* the upstream Vulkan-Video-Samples library. The Chromium-side surface that talks to the library is:
>
> ```
> Per-session (set once at Initialize):
>     VkVideoEncoderConfig {
>         codec  = VK_VIDEO_CODEC_OPERATION_ENCODE_H264_BIT_KHR  ← codec selected here, once
>         encodeWidth/Height, inputFormat, bitDepth,
>         rateControlMode, GOP, color/VUI params,
>         externalDevice, externalInstance, ...
>     };
>
> Per-frame (codec-neutral):
>     SubmitExternalFrame(VkVideoEncodeInputFrame {
>         VkImage image, ...,
>         VkBool32 forceIDR, int32_t qpOverride,
>         wait/signal semaphores,
>     });
>     GetEncodedFrame(VkVideoEncodeResult {
>         const uint8_t* pBitstreamData, uint32_t bitstreamSize,
>         uint32_t pictureType (0=I, 1=P, 2=B), VkBool32 isIDR,
>     });
> ```
>
> **No `VkVideoEncodeH264PictureInfoKHR`, no `StdVideoEncodeH264SliceHeader`, no per-codec union ever appears on the Chromium ↔ library surface.** That is exactly the codec-neutral "frame-in / bitstream-out" model.
>
> Sections §1.2 through §5 below were written for the original **from-scratch plan** (where Chromium would have written Layer B itself, with per-codec `H264VulkanVideoEncoderDelegate` / `H265…` / `AV1…` classes). They are preserved as a reference for how the codec driver loop works internally — useful for review, for fallback Model B (vendor codec layer), and for understanding what the library does on our behalf. **For the recommended Model D / Model A path, all of §1.2 §4 §5 collapses into "configure `VkVideoEncoderConfig.codec`; the library does the rest."** See `VULKAN_VIDEO_LIBRARY_INTEGRATION_ANALYSIS.md` for the recommended path.

### 1.2.2 Reading guide — which sections apply to which model?

The remainder of this document was written for the **from-scratch plan** (Chromium writes Layer B itself). For the **recommended Model D / Model A** path, Layer B lives inside the upstream library and the Chromium-side code is much thinner. Use this table to navigate:

| Section | Layer | From-scratch plan / Model B fallback | Model D / A (recommended) |
|---|---|---|---|
| §1.1 Project Goal | A | Read normally | Read normally |
| §1.2 Interface boundary (the codec-neutral surface) | A | Read normally | Read normally |
| §1.3 Conversion Code Estimate (~9 k LoC for `H264SPS → StdVideoH264SequenceParameterSet`, etc.) | **B** | Read — this is what you'd write | **Skip — the library handles it.** Chromium-side LoC is ~6,200, not ~21 k. |
| §2 Current VA-API architecture (reference) | A + B | Read for context | Read for context |
| §2.5 D3D12 second reference | A + B | Read for context | Read for context |
| §3 Plug-in / `VEA` requirements | A | Read normally | Read normally |
| §4.1 Vulkan class hierarchy with per-codec `H264VulkanVideoEncoderDelegate` / `H265…` / `AV1…` | **B** | Read — this is what you'd write | **Replace with**: a single `VulkanVideoEncodeAccelerator` shim that calls `VulkanVideoEncoderExt`. See `VULKAN_VIDEO_LIBRARY_INTEGRATION_ANALYSIS.md` §7. |
| §4.2–§4.6 `VulkanVideoEncoder{Wrapper,Delegate}` + per-frame submission sequence + DPB slot allocation + codec-specific delegate | **B** | Read — this is what you'd write | **Skip — the library does this.** Useful as a reference for what's running inside `vk_video_encoder/libs/VkVideoEncoder/`. |
| §5 Mapping VA-API → Vulkan per codec (`H264SPS` → `StdVideoH264SequenceParameterSet`, slice headers, etc.) | **B** | Read — this is the mapping table you implement | **Skip — done inside the library.** |
| §6 Input path (`VideoFrame` → `VkImage`) | A + B | Read normally | Read normally — the proposed `SharedImageManager::ProduceVulkanVideo()` flow is Chromium-side regardless, but this API does not exist in ToT yet. |
| §7 Rate Control, GOP, QP Maps | A + B | Read normally — codec-specific `constantQp` is what you set in slice info | Read for context. Chromium sets the **mode** in `VkVideoEncoderConfig`; the library handles the codec-specific fields. |
| §7.5 Quantization maps | B (per-codec block size) | Read normally | The block size comes back from `EnumerateVulkanVideoEncoderCapabilities` (G-NEW-1); Chromium passes the QP map as a `VkImage` per frame. |
| §7.7 Manual reference buffer control | **B** | Read normally — slot mapping is per-codec | **Skip — G-NEW-7 in the library exposes the WebRTC index model**; library translates to slots internally. |
| §8 Bitstream output, queries, `dstBufferOffset` + `precedingExternallyEncodedBytes` | A + B | Read — this is what you'd write | The library does the query plumbing; Chromium owns the prepended-header policy (still relevant for SPS/PPS/AUD that Chromium inserts). |
| §9 Sync / DMA-BUF / timeline semaphores | A | Read normally | Read normally |
| §10 Protected encode (non-goal) | n/a | Skip | Skip |
| §11 Execution plan — 6 phases for from-scratch | meta | Read — this is the from-scratch plan | **Supersession**: the library-recommended plan is in `VULKAN_VIDEO_LIBRARY_INTEGRATION_ANALYSIS.md` §7, ~10.5–12 weeks instead of ~19–21. |
| §12 Test plan summary | A | Read normally | Read normally (full plan in `VULKAN_VIDEO_TEST_PLAN.md`) |
| §13 Risks | meta | Read normally | Some risks are absorbed by the library (R3 device sharing, R4 stdio); see `VULKAN_VIDEO_LIBRARY_INTEGRATION_ANALYSIS.md` §5. |

So if you only care about the recommended path: read §1.1–§1.2, §2 for context, §3, §6, §9, §10, §12, §13 in this document — and read the library integration analysis (`VULKAN_VIDEO_LIBRARY_INTEGRATION_ANALYSIS.md`) end-to-end for everything else. The Chromium-side code is codec-neutral throughout.

### 1.2.1 Key Insight (from-scratch plan): GOP / DPB / Rate Control are **NOT** Shared

> **Applies to**: from-scratch plan and Model B fallback. The whole "DPB / GOP / rate control is per-backend" framing below describes work the library has already done internally for Model D / A — Chromium just sets the parameters in `VkVideoEncoderConfig` once.

Decoding in Chromium reuses parsers (`H264Parser`, `H265Parser`, ...) across backends. **Encoding is the opposite**:

| Component | Decode (existing) | Encode (this design) |
|---|---|---|
| Bitstream **parsing** | Shared in `media/parsers/` | **N/A** — encoder *emits* |
| **DPB / reference frame** logic | Shared in `H264Decoder`, `H265Decoder` ... | **Per-backend** (`H264VaapiVideoEncoderDelegate` ↔ `H264VulkanVideoEncoderDelegate`) |
| **GOP / rate control** | Decoder consumes | **Per-backend** (must be re-implemented) |
| Bitstream **builder** | `H264SliceHeader → parser` | Shared in `media/filters/h26x_annex_b_bitstream_builder.h`, `media/gpu/h26{4,5}_builder.h` |
| HW driver call | `vkCmdDecodeVideoKHR` | `vkCmdEncodeVideoKHR` |

What Chromium **already gives us** for encode:

- `media/video/video_encode_accelerator.h` — the stable IPC-facing **`VideoEncodeAccelerator` (VEA) interface**: the C++ contract every HW encoder backend (`VaapiVideoEncodeAccelerator`, `D3D12VideoEncodeAccelerator`, `MediaFoundationVideoEncodeAccelerator`, the proposed `VulkanVideoEncodeAccelerator`) implements. It is Mojo-stable, so the renderer process talks to whichever backend the GPU process selected without knowing which one it is.
- `media/gpu/h264_dpb.{h,cc}`, `media/gpu/h264_rate_controller.{h,cc}`, `media/gpu/h264_ratectrl_rtc.{h,cc}` — DPB and software bitrate controller logic (reusable from `VaapiVideoEncoderDelegate`).
- `media/gpu/h264_builder.{h,cc}`, `media/filters/h26x_annex_b_bitstream_builder.h` — Annex-B SPS/PPS/slice header builders (reusable to emit packed headers).
- `media/gpu/svc_layers.{h,cc}` — SVC scalability layer math.
- `media/gpu/gpu_video_encode_accelerator_helpers.h` — bitstream buffer size estimator.

What we **must write** for Vulkan Video encode:

- `VulkanVideoEncodeAccelerator` (peer of `VaapiVideoEncodeAccelerator`).
- `VulkanVideoEncoderWrapper` (peer of `VaapiWrapper` for encode mode).
- `VulkanVideoEncoderDelegate` base class (peer of `VaapiVideoEncoderDelegate`).
- Per-codec delegates:
    - `H264VulkanVideoEncoderDelegate`
    - `H265VulkanVideoEncoderDelegate`
    - `AV1VulkanVideoEncoderDelegate`
  H.264 mirrors `H264VaapiVideoEncoderDelegate`; AV1 mirrors `AV1VaapiVideoEncoderDelegate`; H.265 mirrors `D3D12VideoEncodeH265Delegate` plus the Vulkan Samples H.265 encoder because Chromium has no VA-API HEVC encoder.
- Conversion code: `H264SPS`/`H264PPS`/`H264SliceHeader` → `StdVideoH264SequenceParameterSet`/`StdVideoH264PictureParameterSet`/`StdVideoEncodeH264SliceHeader` (and similarly for HEVC / AV1).
- DPB slot manager bound to `VkVideoReferenceSlotInfoKHR[]`.
- Bitstream readback path using `VK_QUERY_TYPE_VIDEO_ENCODE_FEEDBACK_KHR`.

### 1.3 Conversion Code Estimate

> **Applies to**: from-scratch plan and Model B fallback. The estimate below (~thousands of LoC of `H264SPS → StdVideoH264SequenceParameterSet` mapping, slice-header conversion, etc.) is **Layer B work that the upstream Vulkan-Video-Samples library has already done**. For Model D / Model A, the Chromium-side LoC is ~6,200 (per the library integration analysis), not ~21 k.

| Codec | Chromium structs | Vulkan `StdVideo*` structs | Approx. conv. LOC |
|---|---|---|---|
| H.264 | `H264SPS`, `H264PPS`, `H264SliceHeader`, `H264Picture` (port of `H264VaapiVideoEncoderDelegate`) | `StdVideoH264SequenceParameterSet`, `StdVideoH264PictureParameterSet`, `StdVideoEncodeH264SliceHeader`, `StdVideoEncodeH264ReferenceInfo` | ~700 |
| H.265 | `H265VPS`, `H265SPS`, `H265PPS`, `H265SliceHeader` — **referencing D3D12 H265 delegate**, not VA-API (which has no HEVC encode) | `StdVideoH265VideoParameterSet`, `StdVideoH265SequenceParameterSet`, `StdVideoH265PictureParameterSet`, `StdVideoEncodeH265SliceSegmentHeader` | ~900 |
| AV1 | `Av1FrameHeader`, `Av1SequenceHeader` (port of VA-API AV1 + Vulkan Samples) | `StdVideoAV1SequenceHeader`, `StdVideoEncodeAV1PictureInfo` | ~700 |
| **Total** | | | **~2,300** |

(Encoder structs are *larger* than decoder ones because they also carry reference-list reordering and ref-pic-marking commands.)

> HEVC differs from H.264/AV1 here: there is no in-tree VA-API HEVC encoder to port from. The HEVC delegate borrows its Chromium-side GOP/reference/RC state machine from **`d3d12_video_encode_h265_delegate.cc`** and its bitstream construction from **`media/gpu/h265_builder.{h,cc}`** (already in tree, used by software-side HEVC stream writing).

### 1.4 Scope and Priorities

| Component | Status | Priority |
|---|---|---|
| Vulkan Video Encode (H.264 CQP) — *earliest milestone* | New | P0 |
| Vulkan Video Encode (H.264 CBR/VBR) | New | P0 |
| Vulkan Video Encode (HEVC main/main10) | New | P0 |
| Vulkan Video Encode (AV1) | New | P1 |
| SVC temporal layers (T=2, T=3) | New | P1 |
| SVC spatial layers (S=2, S=3) for AV1 | New | P2 |
| Quantization Maps (delta-QP, emphasis-map) | New | P2 |
| Intra-refresh (`VK_KHR_video_encode_intra_refresh`) | New | P2 |
| Manual reference buffer control | New | P2 |
| Protected encode (`VK_VIDEO_SESSION_CREATE_PROTECTED_CONTENT_BIT_KHR`) | **Non-goal** (Review F12) | — |

### 1.5 Non-Goals

- Software-only encoder fallback (already covered by `VpxVideoEncoder`, `OpenH264VideoEncoder`).
- VP8 / VP9 encode (no `VK_KHR_video_encode_vp9` exists yet — fall back to VA-API/V4L2/SW).
- JPEG encode (covered by `VaapiJpegEncodeAccelerator`).
- **Protected (DRM) encode** — no Chrome/CDM consumer exists; see §10 (Review F12).
- VA-API HEVC encode as a porting source — it **does not exist** in tree; HEVC ports from D3D12 H.265 (see §2.5 and §5.3, Review F1).

---

## 2. Current HW-Accelerated Encode Architecture (VA-API) <a name="2-current-vaapi"></a>

### 2.1 Layered View

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       Renderer Process                                       │
│  Blink::VideoEncoder (WebCodecs) │ WebRTC PeerConnection │ MediaRecorder    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │ Mojo
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          GPU Process                                         │
│  MojoVideoEncodeAcceleratorService                                          │
│            │                                                                │
│            ▼                                                                │
│  GpuVideoEncodeAcceleratorFactory::CreateVEA()                              │
│            │ picks the first VEA whose GetSupportedProfiles() matches       │
│            ▼                                                                │
│   VideoEncodeAccelerator (abstract — media/video/video_encode_accelerator.h)│
│            │                                                                │
│      ┌─────┼─────────────────┬────────────────────┬───────────────────────┐ │
│      ▼     ▼                 ▼                    ▼                       ▼ │
│  VaapiVEA  V4L2VEA  D3D12VideoEncodeAccel  MFVideoEncodeAccel  NdkVideoEncodeAccel │
│  (Linux/   (ARM/    (Windows)              (Windows)           (Android)    │
│   CrOS)    CrOS)                                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

`GpuVideoEncodeAcceleratorFactory::CreateVEA()` (in `media/gpu/gpu_video_encode_accelerator_factory.cc`) is the single entry-point — it walks `GetVEAFactoryFunctions()` and probes each VEA in priority order. **This is where we plug `VulkanVideoEncodeAccelerator` in.**

### 2.2 VA-API Class Hierarchy (verified against `media/gpu/vaapi/`)

```
                        ┌───────────────────────────┐
                        │  VideoEncodeAccelerator   │
                        │  (media/video/...)        │
                        └───────────────────────────┘
                                    ▲
                                    │ implements
                        ┌───────────┴───────────────┐
                        │ VaapiVideoEncodeAccelerator│
                        │ (media/gpu/vaapi/...)      │
                        │                            │
                        │ • Owns VaapiWrapper        │
                        │ • Owns input/recon VASurface pools │
                        │ • Owns BitstreamBuffer queue       │
                        │ • Drives encoder_task_runner_      │
                        └──────────┬─────────────────┘
                                   │
                                   │ owns
                                   ▼
                        ┌───────────────────────────┐
                        │ VaapiVideoEncoderDelegate │
                        │ (base class, codec-agnostic)│
                        │                           │
                        │ • Encode(EncodeJob&)      │
                        │ • PrepareEncodeJob()  ▲   │
                        │ • GetMetadata()       │   │
                        │ • UpdateRates()       │   │
                        └──────────┬────────────┘   │
                                   │ virtual        │
                ┌──────────────────┼────────────────┼─────────────────┐
                ▼                  ▼                ▼                 ▼
   ┌──────────────────────┐ ┌───────────────────┐ ┌────────────┐ ┌────────────┐
   │ H264Vaapi…Delegate   │ │ H265Vaapi…Delegate│ │ VP8/9…     │ │ AV1Vaapi…  │
   │                      │ │                   │ │            │ │            │
   │ • Owns H264DPB       │ │                   │ │            │ │            │
   │ • Owns H264SPS/PPS   │ │                   │ │            │ │            │
   │ • UpdateSPS/UpdatePPS│ │                   │ │            │ │            │
   │ • GeneratePackedSlice│ │                   │ │            │ │            │
   │ • Software rate ctrl │ │                   │ │            │ │            │
   │   (H264RateCtrlRTC)  │ │                   │ │            │ │            │
   └──────────────────────┘ └───────────────────┘ └────────────┘ └────────────┘
                       │
                       │ submits buffers via
                       ▼
              ┌──────────────────────┐
              │   VaapiWrapper       │
              │   (libva)            │
              │                      │
              │ • vaCreateContext()  │
              │ • SubmitBuffer()     │
              │ • SubmitBuffers()    │
              │ • ExecuteAndDestroyPendingBuffers() │
              │ • DownloadFromVABuffer() │
              └──────────────────────┘
```

### 2.3 VA-API Encode Submission Sequence (per-frame)

The H.264 delegate (`H264VaapiVideoEncoderDelegate::SubmitFrameParameters`) packs three primary VA buffer types and three "misc" buffers per frame:

```cpp
// 1. Sequence Parameter Buffer (SPS-derived)
VAEncSequenceParameterBufferH264 seq_param = {};
seq_param.level_idc        = sps.level_idc;
seq_param.intra_idr_period = kIDRPeriod;     // 2048
seq_param.ip_period        = 1;              // no B frames
seq_param.bits_per_second  = bitrate_bps;
seq_param.max_num_ref_frames = sps.max_num_ref_frames;
seq_param.picture_width_in_mbs  = coded.width / 16;
seq_param.picture_height_in_mbs = coded.height / 16;
seq_param.seq_fields.bits.chroma_format_idc   = sps.chroma_format_idc;
seq_param.seq_fields.bits.frame_mbs_only_flag = sps.frame_mbs_only_flag;
// ... ~30 more fields

// 2. Picture Parameter Buffer
VAEncPictureParameterBufferH264 pic_param = {};
pic_param.CurrPic.picture_id        = va_surface_id;   // reconstructed surface
pic_param.CurrPic.TopFieldOrderCnt  = pic->top_field_order_cnt;
pic_param.coded_buf                 = job.coded_buffer_id(); // bitstream out
pic_param.pic_init_qp               = pps.pic_init_qp_minus26 + 26;
pic_param.pic_fields.bits.idr_pic_flag       = pic->idr;
pic_param.pic_fields.bits.reference_pic_flag = pic->ref;
// ... reference frame list filled here:
for (size_t i = 0; i < ref_pic_list0.size(); ++i) {
    pic_param.ReferenceFrames[i].picture_id = ref_pic_list0[i]->va_surface_id();
    pic_param.ReferenceFrames[i].flags = VA_PICTURE_H264_SHORT_TERM_REFERENCE;
    pic_param.ReferenceFrames[i].frame_idx = ref_pic_list0[i]->frame_num;
}

// 3. Slice Parameter Buffer
VAEncSliceParameterBufferH264 slice_param = {};
slice_param.num_macroblocks = (w/16) * (h/16);
slice_param.slice_type       = pic->type; // I / P
slice_param.num_ref_idx_l0_active_minus1 = ref_pic_list0.size() - 1;
slice_param.slice_qp_delta   = qp - 26;
// RefPicList0[] mirrors pic_param.ReferenceFrames[] subset

// 4. Misc parameter buffers (rate control, frame rate, HRD)
VAEncMiscParameterRateControl rc  = { .bits_per_second = bps, .min_qp, .max_qp, ... };
VAEncMiscParameterFrameRate   fr  = { .framerate = framerate };
VAEncMiscParameterHRD         hrd = { .buffer_size = cpb, .initial_buffer_fullness = cpb/2 };

// 5. Packed headers (when submit_packed_headers_ is true).
// SPS:
VAEncPackedHeaderParameterBuffer packed_sps_hdr = {
    .type = VAEncPackedHeaderSequence,
    .bit_length = packed_sps.BytesInBuffer() * 8,
    .has_emulation_bytes = 0,
};
// PPS, slice header: same pattern.

// One-shot submission
vaapi_wrapper_->SubmitBuffers({
    {VAEncSequenceParameterBufferType, sizeof(seq_param), &seq_param},
    {VAEncPictureParameterBufferType,  sizeof(pic_param), &pic_param},
    {VAEncSliceParameterBufferType,    sizeof(slice_param), &slice_param},
    {VAEncMiscParameterBufferType, rc_buf.size(),  rc_buf.data()},
    {VAEncMiscParameterBufferType, fr_buf.size(),  fr_buf.data()},
    {VAEncMiscParameterBufferType, hrd_buf.size(), hrd_buf.data()},
    {VAEncPackedHeaderParameterBufferType, sizeof(packed_sps_hdr), &packed_sps_hdr},
    {VAEncPackedHeaderDataBufferType,      packed_sps.BytesInBuffer(), packed_sps.data().data()},
    ...
});

// Execute on the input surface (BeginPicture + RenderPicture + EndPicture internally)
vaapi_wrapper_->ExecuteAndDestroyPendingBuffers(encode_job.input_surface_id());
```

### 2.4 VA-API Encode Resource Lifecycle

```
            ┌─────────────────────────────────────────────────┐
            │ EncodeTask(frame, force_keyframe)              │
            │   • push into input_queue_                      │
            │   • EncodePendingInputs()                       │
            └───────────────┬─────────────────────────────────┘
                            │
                            ▼ for each available frame
            ┌─────────────────────────────────────────────────┐
            │ GetOrCreateInputSurface()    (NV12 VPP target or│
            │                               direct DMA-BUF)   │
            │ GetOrCreateReconstructedSurface()               │
            │ CreateEncodeJob(...)                            │
            └───────────────┬─────────────────────────────────┘
                            ▼
            ┌─────────────────────────────────────────────────┐
            │ delegate_->Encode(*job)                         │
            │   • PrepareEncodeJob() — fills H264SPS/PPS,     │
            │     submits all VA buffers                      │
            │   • vaapi_wrapper_->ExecuteAndDestroyPendingBuffers│
            │     (implicit BeginPicture/RenderPicture/EndPicture)│
            └───────────────┬─────────────────────────────────┘
                            ▼
            ┌─────────────────────────────────────────────────┐
            │ pending_encode_results_.push(EncodeResult)      │
            │   • holds the VABufferID with coded bitstream   │
            └───────────────┬─────────────────────────────────┘
                            ▼ when a free BitstreamBuffer arrives
            ┌─────────────────────────────────────────────────┐
            │ ReturnBitstreamBuffer(result, buffer)           │
            │   • vaSyncSurface()                             │
            │   • vaMapBuffer()                               │
            │   • memcpy(shared_mem_buffer, codedBuf, size)   │
            │   • vaUnmapBuffer()                             │
            │   • client_->BitstreamBufferReady(id, metadata) │
            └─────────────────────────────────────────────────┘
```

> **Verified file map** (Chromium HEAD as of May 2026):
> - `media/gpu/vaapi/vaapi_video_encode_accelerator.{h,cc}`
> - `media/gpu/vaapi/vaapi_video_encoder_delegate.{h,cc}`
> - `media/gpu/vaapi/h264_vaapi_video_encoder_delegate.{h,cc}` (~1250 LOC, **largest** VA-API delegate)
> - `media/gpu/vaapi/av1_vaapi_video_encoder_delegate.{h,cc}`
> - `media/gpu/vaapi/vp8_vaapi_video_encoder_delegate.{h,cc}`
> - `media/gpu/vaapi/vp9_vaapi_video_encoder_delegate.{h,cc}`
>
> **Important**: there is **no `h265_vaapi_video_encoder_delegate.{h,cc}`** in tree — `VaapiVideoEncodeAccelerator` does **not** support HEVC encode today (the unit test in `vaapi_video_encode_accelerator_unittest.cc` documents this explicitly). For HEVC and the newer manual-reference / shared-image features, see §2.5 (D3D12 second reference) below.

### 2.5 D3D12 Encode — Second Reference (HEVC, manual references, shared image)

VA-API is the right reference for the H.264 delegate state machine and the AV1 delegate. It is **not** the right reference for HEVC, manual reference control, or GPU-backed input — those are most cleanly expressed in the newer D3D12 encode backend.

| Concern | Reference path |
|---|---|
| H.264 delegate state machine, GOP, software rate controller | `media/gpu/vaapi/h264_vaapi_video_encoder_delegate.{h,cc}` |
| AV1 Chromium-side encoder behavior | `media/gpu/vaapi/av1_vaapi_video_encoder_delegate.{h,cc}` |
| **HEVC Chromium-side encoder behavior** | `media/gpu/windows/d3d12_video_encode_h265_delegate.{h,cc}` |
| **Manual reference buffer control** (`Config::manual_reference_buffer_control`, `EncodeOptions::reference_buffers`, `update_buffer`) | `media/gpu/windows/d3d12_video_encode_delegate.{h,cc}` + per-codec delegates |
| **GPU-backed input via SharedImage** | `media/gpu/windows/d3d12_video_encode_accelerator.cc` (lines 140–250 — uses `CommandBufferHelper::GetSharedImageManager()` → `SharedImageManager::ProduceVideo()` → scoped read access → typed representation) |
| `VideoEncoderInfo` modern fields (`gpu_supported_pixel_formats`, `supports_gpu_shared_images`, `number_of_manual_reference_buffers`, `reports_average_qp`, `requested_resolution_alignment`) | `d3d12_video_encode_accelerator.cc::NotifyEncoderInfoChange()` |
| Encoder-init UMA + status codes (`EncoderStatus::Codes::kBadReferenceBuffer`, …) | `d3d12_video_encode_delegate.{h,cc}` |

**Verified file map** (Chromium HEAD as of May 2026):
- `media/gpu/windows/d3d12_video_encode_accelerator.{h,cc}`
- `media/gpu/windows/d3d12_video_encode_delegate.{h,cc}`
- `media/gpu/windows/d3d12_video_encode_h264_delegate.{h,cc}`
- `media/gpu/windows/d3d12_video_encode_h265_delegate.{h,cc}`  ← **HEVC reference for our Vulkan HEVC delegate**
- `media/gpu/windows/d3d12_video_encode_av1_delegate.{h,cc}`
- `media/gpu/windows/d3d12_video_encoder_wrapper.{h,cc}`

The Vulkan backend should mirror **VA-API** for queueing and the H.264 delegate, and mirror **D3D12** for the newer VEA capabilities surface and GPU-backed input.

### 2.6 VA-API Limitations We Are Trying to Eliminate

| Pain point | Vulkan Video remedy |
|---|---|
| Implicit synchronization, no `VkSemaphore` interop with compositor | Explicit `VkSemaphore` / `VkFence` (incl. timeline semaphores) |
| Driver-allocated `VASurfaceID` opaque to Skia/SkiaRenderer | `VkImage` reusable by graphics queue, sampler conversion, blit |
| `vaMapBuffer()`-based bitstream readback on the same queue | `VK_QUERY_TYPE_VIDEO_ENCODE_FEEDBACK_KHR` + persistent-mapped `VkBuffer` |
| Vendor-specific entry points (`VA_RT_FORMAT_YUV420`, fourcc) | Unified `VkFormat` (`VK_FORMAT_G8_B8R8_2PLANE_420_UNORM` etc.) |
| No standardized SVC / temporal layer API across vendors | `VkVideoEncodeRateControlInfoKHR::layerCount` + per-codec extensions |
| Linux only | Cross-platform (Linux desktop, Android via Vulkan, Windows w/ VK_KHR_video) |
| Cannot share the GPU queue used by Skia | Same `VkDevice` and external memory model |

---

## 3. Plug-in and Interface Requirements <a name="3-plugin-requirements"></a>

Any new HW encode backend in Chromium must satisfy these contracts.

### 3.1 The Stable Top Boundary: `VideoEncodeAccelerator`

`media/video/video_encode_accelerator.h` (lines 191–528):

```cpp
class MEDIA_EXPORT VideoEncodeAccelerator {
 public:
  virtual SupportedProfiles GetSupportedProfiles() = 0;

  virtual EncoderStatus Initialize(const Config& config,
                                   Client* client,
                                   std::unique_ptr<MediaLog> media_log) = 0;

  virtual void Encode(scoped_refptr<VideoFrame> frame, bool force_keyframe) = 0;
  virtual void Encode(scoped_refptr<VideoFrame> frame,
                      const VideoEncoder::EncodeOptions& options);

  virtual void UseOutputBitstreamBuffer(BitstreamBuffer buffer) = 0;

  virtual void RequestEncodingParametersChange(
      const Bitrate& bitrate, uint32_t framerate,
      const std::optional<gfx::Size>& size) = 0;

  virtual void RequestEncodingParametersChange(
      const VideoBitrateAllocation&, uint32_t framerate,
      const std::optional<gfx::Size>& size);

  virtual void Destroy() = 0;
  virtual void Flush(FlushCallback);
  virtual bool IsFlushSupported();

  virtual void SetCommandBufferHelperCB(
      base::RepeatingCallback<scoped_refptr<CommandBufferHelper>()> cb,
      scoped_refptr<base::SingleThreadTaskRunner> gpu_task_runner);
  ...
};
```

And the client-side callback surface:

```cpp
class Client {
 public:
  virtual void RequireBitstreamBuffers(unsigned int input_count,
                                       const gfx::Size& input_coded_size,
                                       size_t output_buffer_size) = 0;
  virtual void BitstreamBufferReady(int32_t bitstream_buffer_id,
                                    const BitstreamBufferMetadata& metadata) = 0;
  virtual void NotifyErrorStatus(const EncoderStatus& status) = 0;
  virtual void NotifyEncoderInfoChange(const VideoEncoderInfo& info);
};
```

**Mandatory contract** that any backend (including ours) must honor:

| Requirement | Why |
|---|---|
| `Initialize()` is **synchronous** w.r.t. acceptance/refusal — return `kOk` only if everything that can fail later has been validated | `GpuVideoEncodeAcceleratorFactory::CreateVEA()` walks the list of factories and stops at the first one that returns kOk |
| `GetSupportedProfiles()` may be called **before** `Initialize()` | Used at GPU startup to populate `gpu::GPUInfo` |
| `Encode()` must accept `VideoFrame` with either `STORAGE_SHMEM` (CPU mapped) or `STORAGE_GPU_MEMORY_BUFFER` (DMA-BUF / D3D / IOSurface) per `Config::storage_type` | Renderer can supply either; backend negotiates via `RequireBitstreamBuffers` |
| `UseOutputBitstreamBuffer()` provides a shmem buffer; encoder fills it and calls `BitstreamBufferReady` once with `BitstreamBufferMetadata{ payload_size, key_frame, timestamp, qp, ...}` | Bitstream is transferred **out-of-band** via shmem; not via Mojo |
| `RequireBitstreamBuffers()` must be called whenever `Initialize()` succeeds *and* whenever the input resolution changes | `VideoEncodeAcceleratorAdapter` uses this as a re-config signal |
| `Flush()` must drain all pending frames and signal exactly once | WebRTC and MediaRecorder depend on this |
| Backend must be destructible on any sequence after `Destroy()` | `std::default_delete<VideoEncodeAccelerator>` is specialized to call `Destroy()` |

### 3.2 Initialization `Config` (must be supported in full)

```cpp
struct MEDIA_EXPORT Config {
  enum class StorageType { kShmem, kGpuMemoryBuffer };
  enum class ContentType { kCamera, kDisplay };

  VideoPixelFormat   input_format;           // typically NV12 / I420
  gfx::Size          input_visible_size;
  VideoCodecProfile  output_profile;         // H264PROFILE_HIGH, HEVC_MAIN, AV1_MAIN, …
  Bitrate            bitrate;                // CBR / VBR / EXTERNAL (CQ-managed by client)
  uint32_t           framerate;
  StorageType        storage_type;
  ContentType        content_type;
  std::optional<uint32_t> gop_length;
  std::optional<uint8_t>  h264_output_level;
  bool               is_constrained_h264;
  uint8_t            drop_frame_thresh_percentage;
  std::vector<SpatialLayer> spatial_layers;  // SVC layers
  SVCInterLayerPredMode inter_layer_pred;
  bool               require_low_delay = true;
  EncoderType        required_encoder_type = EncoderType::kHardware;
  bool               manual_reference_buffer_control = false;
};
```

A Vulkan-Video-backed VEA **must**:

1. Map `VideoCodecProfile → VkVideoProfileInfoKHR + StdVideo*ProfileIdc`.
2. Resolve `Bitrate` mode → `VkVideoEncodeRateControlModeFlagBitsKHR` (`CBR_BIT_KHR` / `VBR_BIT_KHR` / `DISABLED_BIT_KHR` for CQP / `DEFAULT_KHR` for driver-managed).
3. Decline initialization if `vkGetPhysicalDeviceVideoCapabilitiesKHR()` doesn't cover the requested resolution/profile/level.
4. Honor `Config::storage_type` — for `kGpuMemoryBuffer`, take the producer's DMA-BUF and import it as `VkImage` with `VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT`.

### 3.3 The Bottom Boundary: Vulkan + Std-Video Headers

The new backend depends on:

| Spec / header | Purpose |
|---|---|
| `vulkan/vulkan.h` (loader 1.3.236+) | Core API |
| `vk_video/vulkan_video_codecs_common.h` | Profile structs |
| `vk_video/vulkan_video_codec_h264std.h`, `_h264std_encode.h` | `StdVideoH264SequenceParameterSet` etc. |
| `vk_video/vulkan_video_codec_h265std.h`, `_h265std_encode.h` | HEVC equivalents |
| `vk_video/vulkan_video_codec_av1std.h`, `_av1std_encode.h` | AV1 |
| `VK_KHR_video_queue` | Common video session lifecycle |
| `VK_KHR_video_encode_queue` | `vkCmdEncodeVideoKHR`, feedback queries |
| `VK_KHR_video_encode_h264` | H.264 encode capabilities + structs |
| `VK_KHR_video_encode_h265` | HEVC equivalent |
| `VK_KHR_video_encode_av1` | AV1 (provisional in some drivers; check `apiVersion`) |
| `VK_KHR_video_maintenance1` | Inline queries (avoids round-trip) |
| `VK_KHR_synchronization2`  | Required by `VK_KHR_video_encode_queue` |
| `VK_KHR_external_memory_fd` + `VK_EXT_external_memory_dma_buf` | DMA-BUF import (Linux) |
| `VK_KHR_external_semaphore_fd` + `VK_KHR_timeline_semaphore` | Producer/consumer sync |

### 3.4 Plug-in Surface inside the GPU process

`media/gpu/gpu_video_encode_accelerator_factory.cc`:

```cpp
#if BUILDFLAG(USE_VAAPI)
  vea_factory_functions->push_back(base::BindRepeating(&CreateVaapiVEA));
#elif BUILDFLAG(USE_V4L2_CODEC)
  vea_factory_functions->push_back(base::BindRepeating(&CreateV4L2VEA));
#endif
```

We add a new branch guarded by `BUILDFLAG(ENABLE_VULKAN_VIDEO_ENCODE)`, preferentially **before** VA-API on systems where Vulkan video encode is supported, so the factory probes Vulkan first and falls back to VA-API on failure:

```cpp
#if BUILDFLAG(ENABLE_VULKAN_VIDEO_ENCODE)
  if (base::FeatureList::IsEnabled(kVulkanVideoEncode)) {
    vea_factory_functions->push_back(base::BindRepeating(&CreateVulkanVEA,
                                                          gpu_preferences,
                                                          gpu_workarounds,
                                                          gpu_device));
  }
#endif
```

`CreateVulkanVEA()` returns a `std::unique_ptr<VideoEncodeAccelerator>` that wraps `VulkanVideoEncodeAccelerator`. If `GetSupportedProfiles()` returns empty (no `VK_QUEUE_VIDEO_ENCODE_BIT_KHR` queue, no encode extensions), the factory's caller silently drops to the next backend.

### 3.5 Mojo Interface

**No changes** are required to `media/mojo/mojom/video_encode_accelerator.mojom` — the Mojo IPC interface is backend-agnostic. All Mojo traffic continues to flow through `MojoVideoEncodeAcceleratorService` and `MojoVideoEncodeAccelerator`.

### 3.6 Build-system requirements

Encoder gets enabled via `media/gpu/args.gni`:

```
declare_args() {
  # Existing
  use_vaapi = ...
  # New
  enable_vulkan_video_encode = is_linux || is_android || is_chromeos || is_win
}
```

`media/gpu/BUILD.gn` adds a new source set `vulkan_video` (sibling of `vaapi`) and links against `//gpu/vulkan` for the loader. The new directory is `media/gpu/vulkan/`.

### 3.7 `VideoEncoderInfo` — concrete initial values for the Vulkan backend

> Review F10: every backend publishes a `VideoEncoderInfo` via `Client::NotifyEncoderInfoChange()`. The design needs explicit MVP values so callers (WebRTC's bitrate adjuster, the simulcast adapter) can reason about us.

For Phase-1 / MVP we publish:

| Field | Vulkan MVP value | Rationale |
|---|---|---|
| `implementation_name` | `"VulkanVideoEncodeAccelerator"` | matches VA-API naming pattern |
| `is_hardware_accelerated` | `true` | by definition |
| `supports_native_handle` | `true` once §6.3 lands; `false` in earliest Phase 1 | gated by GPU-backed input |
| `supports_gpu_shared_images` | `true` once §6.3 lands; `false` before | per `DoesSupportGpuSharedImages()` |
| `has_trusted_rate_controller` | `true` only for `DEFAULT`/`CBR`/`VBR` modes; `false` for `DISABLED` (we control the QP) | mirrors D3D12 |
| `reports_average_qp` | `true` for CQP / external modes (QP is deterministic); `false` for CBR/VBR (driver chooses) | matches D3D12 H264/H265 delegate's behaviour |
| `gpu_supported_pixel_formats` | derived from `vkGetPhysicalDeviceVideoFormatPropertiesKHR(VK_IMAGE_USAGE_VIDEO_ENCODE_SRC_BIT_KHR)` mapped to Chromium's `VideoPixelFormat` (typically `PIXEL_FORMAT_NV12`; sometimes `PIXEL_FORMAT_P010` for 10-bit) | dynamic per device |
| `number_of_manual_reference_buffers` | `0` for MVP (see §7.7) | manual ref control declined |
| `requested_resolution_alignment` | `max(codec_mb_size, caps.encodeInputPictureGranularity.width / .height)` | from `VkVideoCapabilitiesKHR.pictureAccessGranularity` and codec block size |
| `apply_alignment_to_all_simulcast_layers` | `true` | identical to all current HW VEAs |
| `supports_simulcast` | `false` for MVP | parity with VA-API |
| `supports_frame_size_change` | `false` for MVP (require recreate) | safer until `RequestEncodingParametersChange()` with `size` is hardened |
| `frame_delay` | `0` for low-delay; `>=1` only when B-frames are explicitly enabled | matches the GOP we configure |
| `input_capacity` | `kMinNumFramesInFlight = 4` | matches `VaapiVEA::kMinNumFramesInFlight` |
| `resolution_rate_limits` | per `vkGetPhysicalDeviceVideoCapabilitiesKHR` (maxBitrate × maxCodedExtent) | dynamic |
| `fps_allocation` | derived from `Config::spatial_layers[i].framerate` (uniform if absent) | matches WebRTC adapter expectations |

`VulkanVideoEncodeAccelerator::Initialize()` builds the info struct as the last step before returning `EncoderStatus::Codes::kOk` and calls `Client::NotifyEncoderInfoChange(info)`. The struct is updated when `RequestEncodingParametersChange()` materially changes the encoder shape (e.g. a new spatial-layer configuration).

---

## 4. Vulkan Video Encode API Design <a name="4-vulkan-encode-design"></a>

> **Applies to**: from-scratch plan and Model B fallback. §4.1–§4.6 describe **Layer B** — the codec driver loop with per-codec `H264VulkanVideoEncoderDelegate` / `H265…` / `AV1…` classes, DPB slot management, per-frame `VkVideoEncodeH264PictureInfoKHR` / `StdVideoEncodeH264*` chaining, etc. For the recommended **Model D / A**, this whole section is inside the upstream Vulkan-Video-Samples library; the Chromium-side code is a single codec-neutral `VulkanVideoEncodeAccelerator` shim that calls `VulkanVideoEncoderExt::SubmitExternalFrame(VkVideoEncodeInputFrame)`. The Chromium-side surface is documented in §1.2 (the codec-neutral contract) and in `VULKAN_VIDEO_LIBRARY_INTEGRATION_ANALYSIS.md` §7 (the recommended-plan implementation).

### 4.1 Class Hierarchy (mirrors VA-API)

```
                        ┌───────────────────────────┐
                        │  VideoEncodeAccelerator   │
                        │  (media/video/...)        │
                        └───────────────────────────┘
                                    ▲
                                    │ implements
                        ┌───────────┴───────────────┐
                        │ VulkanVideoEncodeAccelerator│  ← NEW
                        │ (media/gpu/vulkan/...)      │
                        │                            │
                        │ • Owns VulkanVideoEncoder  │
                        │   Wrapper                  │
                        │ • Owns VulkanVideoImagePool│
                        │ • Drives encoder_task_runner_ │
                        └──────────┬─────────────────┘
                                   │
                                   │ owns
                                   ▼
                        ┌───────────────────────────┐
                        │VulkanVideoEncoderDelegate │  ← NEW
                        │ (base class)              │
                        │                           │
                        │ • Encode(EncodeJob&)      │
                        │ • PrepareEncodeJob()      │
                        │ • UpdateRates()           │
                        │ • UpdateSessionParameters()│
                        └──────────┬────────────────┘
                                   │ virtual
                ┌──────────────────┼────────────────┐
                ▼                  ▼                ▼
   ┌────────────────────────┐ ┌─────────────────┐ ┌──────────────┐
   │ H264VulkanVideoEncoder │ │ H265Vulkan…     │ │ AV1Vulkan…   │
   │ Delegate               │ │ Delegate        │ │ Delegate     │
   │                        │ │                 │ │              │
   │ • Owns H264DPB         │ │                 │ │              │
   │ • Owns H264SPS/PPS     │ │                 │ │              │
   │ • Converts:            │ │                 │ │              │
   │   - H264SPS →          │ │                 │ │              │
   │     StdVideoH264SPS    │ │                 │ │              │
   │   - H264PPS →          │ │                 │ │              │
   │     StdVideoH264PPS    │ │                 │ │              │
   │   - H264SliceHeader →  │ │                 │ │              │
   │     StdVideoEncode…SH  │ │                 │ │              │
   │ • Generates packed     │ │                 │ │              │
   │   headers (Annex-B)    │ │                 │ │              │
   │ • Optional SW rate ctrl│ │                 │ │              │
   │   (H264RateCtrlRTC)    │ │                 │ │              │
   └────────────────────────┘ └─────────────────┘ └──────────────┘
                       │
                       │ submits via
                       ▼
              ┌────────────────────────────┐
              │   VulkanVideoEncoderWrapper│  ← NEW (peer of VaapiWrapper for encode)
              │                            │
              │ • VkVideoSessionKHR        │
              │ • VkVideoSessionParametersKHR│
              │ • VulkanCommandBufferPool  │
              │ • Encode-feedback QueryPool│
              │ • BitstreamBuffer pool     │
              │ • Image pools (input/recon/DPB)│
              └────────────────────────────┘
```

> **Naming**: peers of decoder classes follow the existing `…Delegate` / `…Wrapper` convention so file-by-file diff with the decode design doc is trivial.

### 4.2 `VulkanVideoEncodeAccelerator` (peer of `VaapiVideoEncodeAccelerator`)

**Proposed header** `media/gpu/vulkan/vulkan_video_encode_accelerator.h`:

```cpp
class MEDIA_GPU_EXPORT VulkanVideoEncodeAccelerator
    : public VideoEncodeAccelerator,
      public base::trace_event::MemoryDumpProvider {
 public:
  VulkanVideoEncodeAccelerator(scoped_refptr<gpu::VulkanImplementation> vk_impl,
                               const gpu::GpuDriverBugWorkarounds&);
  ~VulkanVideoEncodeAccelerator() override;

  // VideoEncodeAccelerator
  SupportedProfiles GetSupportedProfiles() override;
  EncoderStatus    Initialize(const Config& config, Client* client,
                              std::unique_ptr<MediaLog>) override;
  void Encode(scoped_refptr<VideoFrame> frame, bool force_keyframe) override;
  void UseOutputBitstreamBuffer(BitstreamBuffer buffer) override;
  void RequestEncodingParametersChange(const Bitrate&, uint32_t framerate,
                                       const std::optional<gfx::Size>&) override;
  void RequestEncodingParametersChange(const VideoBitrateAllocation&,
                                       uint32_t framerate,
                                       const std::optional<gfx::Size>&) override;
  void Destroy() override;
  void Flush(FlushCallback) override;
  bool IsFlushSupported() override;

 private:
  // Tasks (mirrors VaapiVideoEncodeAccelerator).
  void InitializeTask(const Config& config);
  void EncodeTask(scoped_refptr<VideoFrame> frame, bool force_keyframe);
  void UseOutputBitstreamBufferTask(BitstreamBuffer);
  void EncodePendingInputs();
  void TryToReturnBitstreamBuffers();

  scoped_refptr<gpu::VulkanImplementation> vk_impl_;
  std::unique_ptr<VulkanDeviceContext>    vk_dev_ctx_;
  std::unique_ptr<VulkanVideoEncoderWrapper> vk_enc_;

  // Codec-specific delegate (H.264, H.265, AV1)
  std::unique_ptr<VulkanVideoEncoderDelegate> encoder_;

  // Recyclable image pools.
  scoped_refptr<VulkanVideoImagePool> input_image_pool_;   // VIDEO_ENCODE_SRC
  scoped_refptr<VulkanVideoImagePool> recon_image_pool_;   // VIDEO_ENCODE_DPB
  scoped_refptr<VulkanBitstreamBufferPool> bitstream_pool_;
  scoped_refptr<VulkanCommandBufferPool> cmd_pool_;

  // VideoFrame queues, bitstream queues — same as Vaapi VEA.
  base::queue<InputFrameRef> input_queue_ GUARDED_BY_CONTEXT(encoder_seq_);
  base::queue<BitstreamBuffer> available_bitstream_buffers_
      GUARDED_BY_CONTEXT(encoder_seq_);
  base::queue<std::unique_ptr<EncodeResult>> pending_encode_results_
      GUARDED_BY_CONTEXT(encoder_seq_);

  // Timeline semaphore: monotonically increments, value N = frame N submitted.
  VkSemaphore timeline_sem_ = VK_NULL_HANDLE;
  uint64_t    timeline_val_ = 0;
};
```

### 4.3 `VulkanVideoEncoderWrapper` (peer of `VaapiWrapper`)

Responsibilities:

| Method | What it does | VA-API counterpart |
|---|---|---|
| `Create(profile, settings)` | Pick physical device, create `VkVideoSessionKHR` + parameters | `VaapiWrapper::Create(kEncodeCBR, …)` |
| `CreateInputImage(size, fmt)` | `VkImageCreateInfo` with `VK_IMAGE_USAGE_VIDEO_ENCODE_SRC_BIT_KHR` | `CreateContextAndSurfaces(VA_RT_FORMAT_YUV420, ...)` with `kVideoEncoder` hint |
| `CreateDpbImage(size, fmt)` | Same + `VK_IMAGE_USAGE_VIDEO_ENCODE_DPB_BIT_KHR`, layered | (recon surfaces) |
| `ImportDmaBufAsInputImage(fd, layout)` | External memory import | `CreateVASurfaceForPixmap()` |
| `CreateBitstreamBuffer(size)` | `VkBuffer` with `VK_BUFFER_USAGE_VIDEO_ENCODE_DST_BIT_KHR`, host-visible+coherent or device-local+staging | `vaCreateBuffer(VAEncCodedBufferType)` |
| `UpdateSessionParameters(StdVideoH264SequenceParameterSet*, StdVideoH264PictureParameterSet*)` | Recreate `VkVideoSessionParametersKHR` (or use updateInfo if codec supports it) | `VaapiWrapper::SubmitBuffer(VAEncSequenceParameterBufferType, …)` (driver caches) |
| `ExecuteEncode(EncodeJob*)` | Build cmdbuf: BeginCoding → ControlCoding (rate/QL) → EncodeVideo → EndCoding; Submit on `VK_QUEUE_VIDEO_ENCODE_BIT_KHR` queue | `ExecuteAndDestroyPendingBuffers(input_surface_id)` |
| `WaitForEncode(fence/timeline_value)` | `vkWaitSemaphores` on timeline | `vaSyncSurface()` |
| `GetEncodeFeedback(query_index, &offset, &size, &status)` | `vkGetQueryPoolResults(VK_QUERY_TYPE_VIDEO_ENCODE_FEEDBACK_KHR)` | Compute size from `vaMapBuffer()` returned VABuffer info |
| `MapBitstreamBuffer(buf)` | `vkMapMemory()` or persistent map | `vaMapBuffer()` |

### 4.4 Vulkan Video Encode Submission Sequence (per-frame)

```cpp
// === 1. Session and Session Parameters (created once / on SPS/PPS change) ===

VkVideoProfileInfoKHR profile_info = {
    .sType                = VK_STRUCTURE_TYPE_VIDEO_PROFILE_INFO_KHR,
    .pNext                = &h264_profile_info,           // chained
    .videoCodecOperation  = VK_VIDEO_CODEC_OPERATION_ENCODE_H264_BIT_KHR,
    .chromaSubsampling    = VK_VIDEO_CHROMA_SUBSAMPLING_420_BIT_KHR,
    .lumaBitDepth         = VK_VIDEO_COMPONENT_BIT_DEPTH_8_BIT_KHR,
    .chromaBitDepth       = VK_VIDEO_COMPONENT_BIT_DEPTH_8_BIT_KHR,
};
VkVideoEncodeH264ProfileInfoKHR h264_profile_info = {
    .sType         = VK_STRUCTURE_TYPE_VIDEO_ENCODE_H264_PROFILE_INFO_KHR,
    .stdProfileIdc = STD_VIDEO_H264_PROFILE_IDC_HIGH,
};

VkVideoSessionCreateInfoKHR session_info = {
    .sType                    = VK_STRUCTURE_TYPE_VIDEO_SESSION_CREATE_INFO_KHR,
    .flags                    = VK_VIDEO_SESSION_CREATE_ALLOW_ENCODE_PARAMETER_OPTIMIZATIONS_BIT_KHR,
    .pVideoProfile            = &profile_info,
    .pictureFormat            = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM,
    .maxCodedExtent           = {1920, 1088},
    .referencePictureFormat   = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM,
    .maxDpbSlots              = 16,
    .maxActiveReferencePictures = 1,
    .pStdHeaderVersion        = &h264_encode_std_extension,
};
vkCreateVideoSessionKHR(device_, &session_info, nullptr, &video_session_);

// Bind memory (1..N memory bindings depending on driver)
GetVideoSessionMemoryRequirementsKHR(...);
BindVideoSessionMemoryKHR(...);

// Session parameters (SPS + PPS)
VkVideoEncodeH264SessionParametersAddInfoKHR add_info = {
    .sType        = VK_STRUCTURE_TYPE_VIDEO_ENCODE_H264_SESSION_PARAMETERS_ADD_INFO_KHR,
    .stdSPSCount  = 1,
    .pStdSPSs     = &std_sps,
    .stdPPSCount  = 1,
    .pStdPPSs     = &std_pps,
};
VkVideoEncodeH264SessionParametersCreateInfoKHR h264_params_info = {
    .sType            = VK_STRUCTURE_TYPE_VIDEO_ENCODE_H264_SESSION_PARAMETERS_CREATE_INFO_KHR,
    .maxStdSPSCount   = 1,
    .maxStdPPSCount   = 1,
    .pParametersAddInfo = &add_info,
};
VkVideoSessionParametersCreateInfoKHR params_info = {
    .sType             = VK_STRUCTURE_TYPE_VIDEO_SESSION_PARAMETERS_CREATE_INFO_KHR,
    .pNext             = &h264_params_info,
    .videoSession      = video_session_,
};
vkCreateVideoSessionParametersKHR(device_, &params_info, nullptr, &video_params_);

// === 2. Per-frame command buffer recording ===

vkBeginCommandBuffer(cmd_buf, ...);

// Reset query pool slot before each encode.
vkCmdResetQueryPool(cmd_buf, encode_feedback_query_pool_, query_slot, 1);

// Pipeline barrier: input image into VIDEO_ENCODE_SRC layout.
TransitionImage(cmd_buf, input_image_, VK_IMAGE_LAYOUT_VIDEO_ENCODE_SRC_KHR);
// Reconstructed image into VIDEO_ENCODE_DPB layout.
TransitionImage(cmd_buf, recon_image_, VK_IMAGE_LAYOUT_VIDEO_ENCODE_DPB_KHR);

// Begin video coding scope.
VkVideoBeginCodingInfoKHR begin = {
    .sType                  = VK_STRUCTURE_TYPE_VIDEO_BEGIN_CODING_INFO_KHR,
    .videoSession           = video_session_,
    .videoSessionParameters = video_params_,
    .referenceSlotCount     = ref_count + 1,   // +1 reconstructed
    .pReferenceSlots        = reference_slots_,
};
// Chain rate control state.
VkVideoEncodeRateControlInfoKHR rc_info = {
    .sType        = VK_STRUCTURE_TYPE_VIDEO_ENCODE_RATE_CONTROL_INFO_KHR,
    .flags        = 0,
    .rateControlMode = VK_VIDEO_ENCODE_RATE_CONTROL_MODE_CBR_BIT_KHR,
    .layerCount   = 1,
    .pLayers      = &rc_layer,
    .virtualBufferSizeInMs        = 1500,
    .initialVirtualBufferSizeInMs = 750,
};
begin.pNext = &rc_info;
vkCmdBeginVideoCodingKHR(cmd_buf, &begin);

// On the first frame after Init/RequestEncodingParametersChange, send a
// control command to apply rate control + quality level.
if (need_control_cmd) {
    VkVideoCodingControlInfoKHR ctl = {
        .sType = VK_STRUCTURE_TYPE_VIDEO_CODING_CONTROL_INFO_KHR,
        .pNext = &rc_info,                                     // apply
        .flags = VK_VIDEO_CODING_CONTROL_RESET_BIT_KHR
               | VK_VIDEO_CODING_CONTROL_ENCODE_QUALITY_LEVEL_BIT_KHR
               | VK_VIDEO_CODING_CONTROL_ENCODE_RATE_CONTROL_BIT_KHR,
    };
    VkVideoEncodeQualityLevelInfoKHR ql = {
        .sType = VK_STRUCTURE_TYPE_VIDEO_ENCODE_QUALITY_LEVEL_INFO_KHR,
        .qualityLevel = quality_level_,
    };
    rc_info.pNext = &ql;
    vkCmdControlVideoCodingKHR(cmd_buf, &ctl);
}

// The actual encode call.
// NOTE: dstBufferOffset and precedingExternallyEncodedBytes are non-zero
// on frames that prepend app-built SPS/PPS/VPS/AUD bytes. See §8.3.
const uint32_t header_size = pic->idr ? PrepareHeaderBytes() : 0;
VkVideoEncodeInfoKHR encode_info = {
    .sType                 = VK_STRUCTURE_TYPE_VIDEO_ENCODE_INFO_KHR,
    .pNext                 = &h264_pic_info,                  // codec-specific
    .flags                 = 0,
    .dstBuffer             = bitstream_buffer_,               // VkBuffer
    .dstBufferOffset       = header_size,                     // reserve room for headers
    .dstBufferRange        = bitstream_size_ - header_size,
    .srcPictureResource    = src_pic_resource_,               // VkImageView
    .pSetupReferenceSlot   = &setup_ref_slot_,                // reconstructed
    .referenceSlotCount    = active_ref_count_,
    .pReferenceSlots       = active_reference_slots_,
    .precedingExternallyEncodedBytes = header_size,           // tell driver RC
};

// H.264 specific picture info
VkVideoEncodeH264PictureInfoKHR h264_pic_info = {
    .sType                    = VK_STRUCTURE_TYPE_VIDEO_ENCODE_H264_PICTURE_INFO_KHR,
    .naluSliceEntryCount      = 1,
    .pNaluSliceEntries        = &h264_nalu_slice,
    .pStdPictureInfo          = &std_pic_info,                // POC, frame_num, refs
    .generatePrefixNalu       = VK_FALSE,
};

// Inline query if VK_KHR_video_maintenance1 is supported (avoid begin/end query).
VkVideoInlineQueryInfoKHR inline_query = {
    .sType      = VK_STRUCTURE_TYPE_VIDEO_INLINE_QUERY_INFO_KHR,
    .queryPool  = encode_feedback_query_pool_,
    .firstQuery = query_slot,
    .queryCount = 1,
};
ChainNext(encode_info, inline_query);

vkCmdEncodeVideoKHR(cmd_buf, &encode_info);

VkVideoEndCodingInfoKHR end = { VK_STRUCTURE_TYPE_VIDEO_END_CODING_INFO_KHR };
vkCmdEndVideoCodingKHR(cmd_buf, &end);

vkEndCommandBuffer(cmd_buf);

// === 3. Submit on Vulkan video encode queue ===

VkTimelineSemaphoreSubmitInfo timeline = {
    .sType = VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO,
    .signalSemaphoreValueCount = 1,
    .pSignalSemaphoreValues    = &(++timeline_val_),
};
VkSubmitInfo si = {
    .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
    .pNext = &timeline,
    .commandBufferCount = 1,
    .pCommandBuffers    = &cmd_buf,
    .signalSemaphoreCount = 1,
    .pSignalSemaphores    = &timeline_sem_,
};
vkQueueSubmit(encode_queue_, 1, &si, VK_NULL_HANDLE);

// === 4. Bitstream readback (on encoder_task_runner_ later) ===

vkWaitSemaphores(device_, &(VkSemaphoreWaitInfo){ .semaphoreCount=1,
                                                   .pSemaphores=&timeline_sem_,
                                                   .pValues=&timeline_val_ }, UINT64_MAX);

struct {
    uint32_t offset;
    uint32_t size;
    VkQueryResultStatusKHR status;
} feedback[1];
vkGetQueryPoolResults(device_, encode_feedback_query_pool_,
                      query_slot, 1, sizeof(feedback), feedback,
                      sizeof(feedback[0]),
                      VK_QUERY_RESULT_WITH_STATUS_BIT_KHR);

// memcpy from persistently-mapped VkBuffer into shmem BitstreamBuffer.
memcpy(bitstream_shmem.memory(), mapped_bitstream + feedback[0].offset,
       feedback[0].size);

client_->BitstreamBufferReady(buffer_id, BitstreamBufferMetadata{
    .payload_size_bytes = feedback[0].size,
    .key_frame          = picture_is_idr_,
    .timestamp          = pts_,
    .qp                 = std_pic_info.QP,
    .h264 = H264Metadata{...},
});
```

### 4.5 DPB Slot Allocation

We mirror the Vulkan-Samples `VkEncoderDpbH264` strategy but tie it to Chromium's `H264DPB` so that the existing tested reference-frame eviction logic can drive slot recycling:

```cpp
class VulkanVideoEncoderDpb {
 public:
  static constexpr uint32_t kMaxSlots = 16;
  struct Slot {
    int32_t slot_index;                          // 0..15 or -1 if free
    VkSharedBaseObj<VulkanVideoImagePoolNode> image;  // reconstructed picture
    VkVideoPictureResourceInfoKHR  picture_resource;
    VkVideoReferenceSlotInfoKHR    reference_slot;    // pNext → codec-specific DPB info
    StdVideoEncodeH264ReferenceInfo std_ref_info;     // POC, frame_num, long_term flag
    bool is_long_term = false;
  };

  Slot* AcquireSlotFor(scoped_refptr<H264Picture> pic);
  void  ReleaseSlot(int32_t slot_index);
  std::span<const VkVideoReferenceSlotInfoKHR> GetActiveReferenceSlots() const;
  uint32_t active_count() const;

 private:
  std::array<Slot, kMaxSlots> slots_;
  uint32_t                    in_use_mask_ = 0;
};
```

The delegate calls `AcquireSlotFor()` exactly like the VA-API delegate's `pic->AsVaapiH264Picture()->va_surface_id()`, but instead of a `VASurfaceID` it gets a `VkImageView` + slot index.

### 4.6 Codec-Specific Delegate (H.264 example)

`media/gpu/vulkan/h264_vulkan_video_encoder_delegate.h`:

```cpp
class H264VulkanVideoEncoderDelegate : public VulkanVideoEncoderDelegate {
 public:
  H264VulkanVideoEncoderDelegate(VulkanVideoEncoderWrapper* wrapper,
                                 base::RepeatingClosure error_cb);
  ~H264VulkanVideoEncoderDelegate() override;

  bool Initialize(const VideoEncodeAccelerator::Config& config,
                  const Config& ave_config) override;
  bool UpdateRates(const VideoBitrateAllocation&, uint32_t framerate) override;
  gfx::Size GetCodedSize() const override;
  size_t GetMaxNumOfRefFrames() const override;
  std::vector<gfx::Size> GetSVCLayerResolutions() override;

 private:
  PrepareEncodeJobResult PrepareEncodeJob(EncodeJob&) override;
  BitstreamBufferMetadata GetMetadata(const EncodeJob&,
                                      size_t payload_size) override;
  void BitrateControlUpdate(const BitstreamBufferMetadata&) override;

  // Build the StdVideoH264SequenceParameterSet from Chromium's H264SPS.
  void UpdateStdSPS();
  void UpdateStdPPS();

  // Convert Chromium H264Picture into Std + Vulkan structs.
  void FillStdPictureInfo(const H264Picture& pic,
                          StdVideoEncodeH264PictureInfo* out) const;

  // Reuse h26x_annex_b_bitstream_builder.h to emit packed SPS/PPS bytes that
  // we will manually paste in front of slice NALU output.
  void EmitParameterSets(std::vector<uint8_t>* out_sps_nalu,
                         std::vector<uint8_t>* out_pps_nalu);

  // Current bitstream syntax structures (reused from media/parsers/).
  H264SPS  current_sps_;
  H264PPS  current_pps_;
  // Their Vulkan twins.
  StdVideoH264SequenceParameterSet std_sps_;
  StdVideoH264PictureParameterSet  std_pps_;
  // HRD/VUI sub-structures pointed to by std_sps_.
  StdVideoH264HrdParameters        std_hrd_;
  StdVideoH264SequenceParameterSetVui std_vui_;

  std::unique_ptr<H264RateControlWrapper> rate_ctrl_;
  VulkanVideoEncoderDpb dpb_;
  base::circular_deque<scoped_refptr<H264Picture>> ref_pic_list0_;

  VideoCodecProfile profile_ = VIDEO_CODEC_PROFILE_UNKNOWN;
  uint8_t   level_ = 0;
  gfx::Size coded_size_;
  gfx::Size visible_size_;
  unsigned int mb_width_ = 0, mb_height_ = 0;
  unsigned int prev_ref_frame_num_ = 0;
  unsigned int idr_pic_id_ = 0;
  unsigned int num_encoded_frames_ = 0;
  uint8_t   num_temporal_layers_ = 1;
};
```

**Implementation notes**:

1. `UpdateStdSPS()` is the **mirror image** of `H264VaapiVideoEncoderDelegate::UpdateSPS()` — same fields, different destination struct.
2. The `H264RateControllerRTC` software rate controller is **reused as-is**.
3. The Annex-B packed-SPS/packed-PPS path is reused from `media/gpu/h264_builder.{h,cc}` (no NAL-emulation-byte handling — the encoder appends the SPS/PPS NALUs to the front of the bitstream buffer before the slice NALU emitted by `vkCmdEncodeVideoKHR`).
4. **NEW**: The driver may emit its own SPS/PPS into the bitstream depending on the `VK_VIDEO_SESSION_CREATE_ALLOW_ENCODE_PARAMETER_OPTIMIZATIONS_BIT_KHR` flag — we use `vkGetEncodedVideoSessionParametersKHR()` to retrieve the *actually emitted* bytes and use those (rather than guessing).

---

## 5. Mapping: VA-API → Vulkan Video Encode (per codec) <a name="5-mapping"></a>

> **Applies to**: from-scratch plan and Model B fallback. §5 enumerates the field-by-field `VAEncSequenceParameterBufferH264` → `StdVideoH264SequenceParameterSet` translation (and equivalents for HEVC and AV1). **For Model D / A this work is already done inside the library** — the library's per-codec encoders (`VkVideoEncoderH264`, `H265`, `AV1` in `vk_video_encoder/libs/VkVideoEncoder/`) build the Std-Video structs from the codec-neutral `VkVideoEncoderConfig` set at session creation. Chromium does not write any of these mappings. The tables below remain useful as a *reference* for what the library does on our behalf and as the spec for Model B if we ever vendor the codec layer ourselves.

### 5.1 H.264

| VA-API struct/field | Vulkan Video / Std-Video equivalent | Notes |
|---|---|---|
| `VAProfileH264High` | `VkVideoEncodeH264ProfileInfoKHR{.stdProfileIdc=STD_VIDEO_H264_PROFILE_IDC_HIGH}` | |
| `VAEntrypointEncSlice` | `VK_VIDEO_CODEC_OPERATION_ENCODE_H264_BIT_KHR` in queue family | |
| `VAEncSequenceParameterBufferH264` (lines 1028‒1078) | `StdVideoH264SequenceParameterSet` + chained `VkVideoEncodeH264SessionParametersAddInfoKHR` | Submitted once at session-params creation |
| `VAEncPictureParameterBufferH264` (lines 1078‒1101) | `StdVideoEncodeH264PictureInfo` (chained via `VkVideoEncodeH264PictureInfoKHR.pStdPictureInfo`) | Per-frame |
| `VAEncSliceParameterBufferH264` (lines 1102‒1147) | `StdVideoEncodeH264SliceHeader` (in `VkVideoEncodeH264NaluSliceInfoKHR.pStdSliceHeader`) | Per-slice |
| `VAEncMiscParameterRateControl`  | `VkVideoEncodeRateControlInfoKHR` + `VkVideoEncodeH264RateControlInfoKHR` | Sent via `vkCmdControlVideoCodingKHR` |
| `VAEncMiscParameterFrameRate`     | `VkVideoEncodeRateControlLayerInfoKHR.frameRateNumerator/Denominator` | |
| `VAEncMiscParameterHRD`           | `VkVideoEncodeH264RateControlInfoKHR.consecutiveBFrameCount` etc. + std HRD in SPS | |
| `VAEncPackedHeaderSequence` (NAL bytes) | Manually written **into the front** of the bitstream buffer **before** `vkCmdEncodeVideoKHR` (because Vulkan encoder writes only slice NALUs by default) | Or use `vkGetEncodedVideoSessionParametersKHR` if the driver opts to emit them |
| `VABufferType::VAEncCodedBufferType` | `VkBuffer` with `VK_BUFFER_USAGE_VIDEO_ENCODE_DST_BIT_KHR` | |
| Reference: `VAPictureH264.picture_id` (= `VASurfaceID`) | `VkVideoPictureResourceInfoKHR.imageViewBinding` (= `VkImageView`) | |
| `VAPictureH264.flags = VA_PICTURE_H264_SHORT_TERM_REFERENCE` | `StdVideoEncodeH264ReferenceInfoFlags.used_for_long_term_reference = 0` and present in DPB | |

### 5.2 Slice Header Fields (H.264) — one-to-one mapping

```cpp
// VA-API source (from H264VaapiVideoEncoderDelegate)         // Vulkan dest
slice_param.macroblock_address          → std_slice.first_mb_in_slice
slice_param.slice_type                  → std_slice.slice_type
slice_param.pic_parameter_set_id        → std_slice.pic_parameter_set_id // already in StdVideoEncodeH264SliceHeader
slice_param.idr_pic_id                  → std_pic_info.idr_pic_id (in PictureInfo, not SliceHeader)
slice_param.pic_order_cnt_lsb           → std_pic_info.PicOrderCnt
slice_param.num_ref_idx_l0_active_minus1→ std_slice.num_ref_idx_l0_active_minus1
slice_param.slice_qp_delta              → std_slice.slice_qp_delta
slice_param.cabac_init_idc              → std_slice.cabac_init_idc
slice_param.disable_deblocking_filter_idc → std_slice.disable_deblocking_filter_idc
slice_param.slice_alpha_c0_offset_div2  → std_slice.slice_alpha_c0_offset_div2
slice_param.slice_beta_offset_div2      → std_slice.slice_beta_offset_div2
slice_param.RefPicList0[i].picture_id   → reference_slots_[i].imageViewBinding  (in pSetupReferenceSlot / pReferenceSlots)
```

### 5.3 H.265 (HEVC) — D3D12 + Vulkan Samples, **not** VA-API

> **Important**: VA-API in Chromium **does not** support HEVC encode. The mapping below is from the **D3D12 encode HEVC delegate** and the **Vulkan Video Samples H.265 encoder**, not VA-API.

| D3D12 / VA-API equivalent concept | Vulkan Video |
|---|---|
| D3D12: `D3D12_VIDEO_ENCODER_HEAP_DESC` + `D3D12_VIDEO_ENCODER_SEQUENCE_GOP_STRUCTURE_HEVC` | `StdVideoH265VideoParameterSet` + `StdVideoH265SequenceParameterSet` (chained via `VkVideoEncodeH265SessionParametersAddInfoKHR`) |
| D3D12: per-frame `D3D12_VIDEO_ENCODER_PICTURE_CONTROL_DESC` | `StdVideoEncodeH265PictureInfo` in `VkVideoEncodeH265PictureInfoKHR.pStdPictureInfo` |
| D3D12: `D3D12_VIDEO_ENCODER_FRAME_CODED_PICTURE_HEADER_HEVC` slice-segment fields | `StdVideoEncodeH265SliceSegmentHeader` in `VkVideoEncodeH265NaluSliceSegmentInfoKHR.pStdSliceSegmentHeader` |
| D3D12: `D3D12_VIDEO_ENCODER_RATE_CONTROL_*` (CBR/VBR/CQP) | `VkVideoEncodeRateControlInfoKHR` + `VkVideoEncodeH265RateControlInfoKHR` (see §7.1 VUID rules) |
| VPS/SPS/PPS NALU bytes | Build with `media/gpu/h265_builder.{h,cc}` **and** retrieve driver-emitted version via `vkGetEncodedVideoSessionParametersKHR()` + `VkVideoEncodeH265SessionParametersGetInfoKHR`; prefer driver bytes (see §8.3). |

Chromium-side state-machine reference: `media/gpu/windows/d3d12_video_encode_h265_delegate.{h,cc}`. Vulkan command flow reference: `vk_video_encoder/libs/VkVideoEncoder/VkVideoEncoderH265.cpp`.

### 5.4 AV1

| VA-API (`av1_vaapi_video_encoder_delegate.cc`) | Vulkan Video |
|---|---|
| `VAEncSequenceParameterBufferAV1` | `StdVideoAV1SequenceHeader` in `VkVideoEncodeAV1SessionParametersCreateInfoKHR.pStdSequenceHeader` |
| `VAEncPictureParameterBufferAV1` | `StdVideoEncodeAV1PictureInfo` + `StdVideoEncodeAV1OperatingParameters` |
| `VAEncTileGroupBufferAV1` | `VkVideoEncodeAV1PictureInfoKHR.pStdTileGroup` |

AV1 is the **simplest** to map because the entire bitstream is OBU-based and the driver emits all OBUs (sequence header, frame header, tile group) itself. We never have to splice in "packed headers" the way we do with H.264/H.265.

---

## 6. Input Path: VideoFrame → `VkImage` (VIDEO\_ENCODE\_SRC) <a name="6-input-path"></a>

> **Config vs frame storage** (Review F7): the **negotiated** input mode (`Config::StorageType::kShmem` vs `kGpuMemoryBuffer`) is **not** the same as the **frame-time** storage (`VideoFrame::storage_type()`). The VA-API encoder, for example, does:
>
> ```cpp
> native_input_mode_
>     ? frame->storage_type() == VideoFrame::STORAGE_MAPPABLE_SHARED_IMAGE
>     : frame->IsMappable();
> ```
>
> so in GPU-backed mode the backend most commonly sees `STORAGE_MAPPABLE_SHARED_IMAGE`, not `STORAGE_GPU_MEMORY_BUFFER`. We follow the same split: the sections below are named by the negotiated input mode, and each section names the platform realisation(s) that actually deliver bytes to the encoder.

### 6.1 Shmem mode (`Config::StorageType::kShmem`, CPU-mapped YUV)

The frame is mappable (`frame->IsMappable()`). We allocate a *linear* `VkImage` as staging, `vkMapMemory()` it, `memcpy()` the planes, and submit a `vkCmdCopyImage()` into a tiled `VK_IMAGE_USAGE_VIDEO_ENCODE_SRC_BIT_KHR` image. This mirrors `VkVideoEncoder::CopyLinearToOptimalImage()` in the Vulkan Samples.

Realisations:
- WebCodecs `VideoEncoder.encode(VideoFrame from ImageBitmap)` when not GPU-backed
- Legacy WebRTC software-capture paths

### 6.2 GPU-backed mode (`Config::StorageType::kGpuMemoryBuffer`)

The frame's actual storage type depends on the platform/producer:

| Realisation | `VideoFrame::storage_type()` (typical) | Backing |
|---|---|---|
| ChromeOS / Linux camera, V4L2, VA-API decoder output | `STORAGE_GPU_MEMORY_BUFFER` (legacy) **or** `STORAGE_MAPPABLE_SHARED_IMAGE` (current) | Linux DMA-BUF FD with DRM format modifier |
| Windows capture / D3D11/D3D12 surface | `STORAGE_MAPPABLE_SHARED_IMAGE` | Win32 shared NT handle |
| Android camera / ML pipeline | `STORAGE_MAPPABLE_SHARED_IMAGE` | `AHardwareBuffer` |
| Compositor (tab capture, WebGL) | `STORAGE_MAPPABLE_SHARED_IMAGE` with mailbox | `gpu::SharedImageBackingVulkan*` (see §6.3) |

The wrapper always tries the **shared-image representation path (§6.3) first**, and falls back to per-platform external-memory imports below only when the backing cannot be exposed through `SharedImageManager::ProduceVideo()`.

#### 6.2.1 Linux — DMA-BUF import (fallback)

```cpp
VulkanVideoEncoderWrapper::ImportDmaBufAsInputImage(int fd, const Layout& layout):
    VkImportMemoryFdInfoKHR import = {
        .sType      = VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
        .fd         = dmabuf_fd,                                  // takes ownership
    };
    VkImageDrmFormatModifierExplicitCreateInfoEXT drm = {
        .drmFormatModifier           = layout.modifier,
        .drmFormatModifierPlaneCount = layout.num_planes,
        .pPlaneLayouts               = layout.planes,
    };
    VkImageCreateInfo image_ci = {
        .pNext        = &drm,
        .tiling       = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
        .usage        = VK_IMAGE_USAGE_VIDEO_ENCODE_SRC_BIT_KHR,
        .pProfileList = {&profile_list},
    };
    vkCreateImage(...);
    vkAllocateMemory(... pNext = &import, ...);
    vkBindImageMemory(...);
```

#### 6.2.2 Windows — shared NT handle import (fallback)

Use `VK_KHR_external_memory_win32` and `VkImportMemoryWin32HandleInfoKHR{ .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_D3D11_TEXTURE_BIT, .handle = shared_handle }`. See `media/gpu/windows/d3d12_video_encode_accelerator.cc` for the D3D11 → D3D12 handle path; the Vulkan version follows the D3D11 → Vulkan import recipe in `gpu/vulkan/win32/`.

#### 6.2.3 Android — `AHardwareBuffer` import (fallback)

Use `VK_ANDROID_external_memory_android_hardware_buffer` + `VkImportAndroidHardwareBufferInfoANDROID`. Camera pipelines deliver AHB exclusively.

### 6.3 Shared-image representation path (preferred GPU-backed path)

> Review F6: this path does **not** reach into the shared image's `VkImage` directly. It goes through the same `CommandBufferHelper` + `SharedImageManager::ProduceVideo()` pattern that D3D12 encode uses, and binds a Vulkan-typed scoped read access. The shared image backing remains owned by `SharedImageManager`.

#### 6.3.1 Setup

`VulkanVideoEncodeAccelerator::SetCommandBufferHelperCB(...)` is called by the factory (just like `D3D12VideoEncodeAccelerator::SetCommandBufferHelperCB()`) and stores a `RepeatingCallback<scoped_refptr<CommandBufferHelper>()>` on the GPU task runner.

#### 6.3.2 Per-frame resolution

```cpp
// On the GPU task runner (not the encoder runner):
void VulkanVideoEncodeAccelerator::AcquireVulkanVideoSourceOnGpu(
    scoped_refptr<VideoFrame> frame,
    PerFrameToken token) {
  scoped_refptr<CommandBufferHelper> helper = get_command_buffer_helper_cb_.Run();
  gpu::SharedImageManager* sim = helper->GetSharedImageManager();

  // ProduceVideo() returns a typed Vulkan-aware representation, equivalent
  // to D3D12's representation->GetD3D11Texture(). Its concrete type is
  // VulkanVideoImageRepresentation (proposed; mirrors VideoImageRepresentation).
  std::unique_ptr<gpu::VulkanVideoImageRepresentation> repr =
      sim->ProduceVulkanVideo(
          frame->shared_image()->mailbox(),
          helper->GetMemoryTypeTracker(),
          vulkan_device_.get());

  if (!repr) {
    // Backing isn't usable as VIDEO_ENCODE_SRC. Fall through to staging copy.
    PostStagingFallback(std::move(frame), token);
    return;
  }

  auto access = repr->BeginScopedReadAccess();      // returns VkImage + sem
  VkImage  vk_image    = access->image();
  VkSemaphore acquire  = access->acquire_semaphore();
  VkSemaphore release  = access->release_semaphore();

  // Post to encoder task runner; transfer ownership to encode queue family.
  PostEncodeOnEncoderRunner(vk_image, acquire, release,
                            access.release(),         // keep alive
                            std::move(frame), token);
}
```

Inside the encode task we:

1. Wait on `acquire` (timeline or binary semaphore from the shared image).
2. Insert a queue-family-ownership transfer barrier (graphics/compositor → encode), changing the image layout to `VK_IMAGE_LAYOUT_VIDEO_ENCODE_SRC_KHR`.
3. Record `vkCmdEncodeVideoKHR` with `srcPictureResource.imageViewBinding = view-of(vk_image)`.
4. After the encode, insert the reverse transfer (encode → graphics) and signal `release`.
5. Drop the scoped read access (and so the representation).

If the proposed `ProduceVulkanVideo()` returns `nullptr` — for example because the backing was created without `SHARED_IMAGE_USAGE_VIDEO_ENCODE` — we copy/convert through a staging path identical to §6.1.

#### 6.3.3 Why not a direct `Mailbox` → `VkImage`?

Three reasons (Review F6):
1. **Layered ownership**: `SharedImageManager` owns the backing's lifetime. Reaching in violates ref-count invariants and can race with destroy on the GPU runner.
2. **Sync correctness**: the representation API gives us the producer's acquire/release semaphores. Without it, we'd need to invent ad-hoc synchronisation.
3. **Forward compatibility**: tomorrow the compositor may use a non-Vulkan backing on the same OS (e.g. external-memory blob). The representation path keeps that change invisible to the encoder.

---

## 7. Rate Control, GOP and QP Maps <a name="7-rate-control"></a>

### 7.1 Rate Control Modes

| Chromium `Bitrate::Mode` | VA-API counterpart | Vulkan Video counterpart | Notes |
|---|---|---|---|
| `kConstant`  | `kEncodeConstantBitrate` | `VK_VIDEO_ENCODE_RATE_CONTROL_MODE_CBR_BIT_KHR` | Common WebRTC / screen capture mode |
| `kVariable`  | `kEncodeVariableBitrate` | `VK_VIDEO_ENCODE_RATE_CONTROL_MODE_VBR_BIT_KHR` | MediaRecorder |
| `kExternal`  | `kEncodeConstantQuantizationParameter` (effectively CQ) | `VK_VIDEO_ENCODE_RATE_CONTROL_MODE_DISABLED_BIT_KHR` + per-frame QP | WebRTC `ExternalRateController` |
| (driver-managed) | n/a | `VK_VIDEO_ENCODE_RATE_CONTROL_MODE_DEFAULT_KHR` | Defer everything to ICD |

`VkVideoEncodeRateControlInfoKHR` carries:

- `rateControlMode` — above
- `layerCount` + `pLayers[VkVideoEncodeRateControlLayerInfoKHR]` — one per *temporal* layer (SVC T-mode)
- `virtualBufferSizeInMs` / `initialVirtualBufferSizeInMs` — HRD CPB / buffer fullness
- codec-specific via pNext: `VkVideoEncodeH264RateControlInfoKHR{ flags, gopFrameCount, idrPeriod, consecutiveBFrameCount, temporalLayerCount }`

#### 7.1.1 Vulkan VUID rules — `ValidateRateControl()`

> Review F4: the Vulkan spec attaches non-obvious validity rules to `VkVideoEncodeRateControlInfoKHR`. They must be enforced **before** every `vkCmdControlVideoCodingKHR` / `vkCmdBeginVideoCodingKHR` that carries a rate-control struct. We centralise them in `VulkanVideoEncoderWrapper::ValidateRateControl(...)` so per-codec delegates never set the fields incorrectly.

| Mode | `layerCount` | Per-layer constraint |
|---|---|---|
| `VK_VIDEO_ENCODE_RATE_CONTROL_MODE_DEFAULT_KHR` | **must be 0** | n/a (`pLayers` ignored) |
| `VK_VIDEO_ENCODE_RATE_CONTROL_MODE_DISABLED_BIT_KHR` | **must be 0** | n/a (per-frame QP via codec-specific `constantQp`, see §7.2) |
| `VK_VIDEO_ENCODE_RATE_CONTROL_MODE_CBR_BIT_KHR` | ≥ 1 (== temporal-layer count) | each layer: `averageBitrate == maxBitrate` |
| `VK_VIDEO_ENCODE_RATE_CONTROL_MODE_VBR_BIT_KHR` | ≥ 1 (== temporal-layer count) | each layer: `averageBitrate ≤ maxBitrate`, `maxBitrate ≤ caps.maxBitrate` |

Reference code (mirrors `VkVideoEncoder::HandleCtrlCmd()` in the Vulkan Samples):

```cpp
VkResult VulkanVideoEncoderWrapper::ValidateRateControl(
    VkVideoEncodeRateControlInfoKHR& rc) const {
  switch (rc.rateControlMode) {
    case VK_VIDEO_ENCODE_RATE_CONTROL_MODE_DEFAULT_KHR:
    case VK_VIDEO_ENCODE_RATE_CONTROL_MODE_DISABLED_BIT_KHR:
      // VUID: layerCount must be 0; pLayers must be nullptr.
      rc.layerCount = 0;
      rc.pLayers    = nullptr;
      return VK_SUCCESS;

    case VK_VIDEO_ENCODE_RATE_CONTROL_MODE_CBR_BIT_KHR:
      if (rc.layerCount == 0 || rc.pLayers == nullptr) return VK_ERROR_INITIALIZATION_FAILED;
      for (uint32_t i = 0; i < rc.layerCount; ++i) {
        if (rc.pLayers[i].averageBitrate != rc.pLayers[i].maxBitrate)
          return VK_ERROR_INITIALIZATION_FAILED;
      }
      return VK_SUCCESS;

    case VK_VIDEO_ENCODE_RATE_CONTROL_MODE_VBR_BIT_KHR:
      if (rc.layerCount == 0 || rc.pLayers == nullptr) return VK_ERROR_INITIALIZATION_FAILED;
      for (uint32_t i = 0; i < rc.layerCount; ++i) {
        if (rc.pLayers[i].averageBitrate > rc.pLayers[i].maxBitrate)
          return VK_ERROR_INITIALIZATION_FAILED;
        if (rc.pLayers[i].maxBitrate > caps_.maxBitrate)
          return VK_ERROR_INITIALIZATION_FAILED;
      }
      return VK_SUCCESS;
    default:
      return VK_ERROR_INITIALIZATION_FAILED;
  }
}
```

When temporal SVC is active, `layerCount` must match the `temporalLayerCount` field of the codec-specific RC struct chained in `rc.pNext` (e.g. `VkVideoEncodeH264RateControlInfoKHR::temporalLayerCount`). `ValidateRateControl()` asserts this when it sees the codec-specific chained struct.

### 7.2 Software-side Rate Controller + per-codec constant QP (`kExternal`)

Chromium's existing `H264RateCtrlRTC` (used by VA-API today) computes per-frame QP from a target bitrate. We reuse it for Vulkan, but the QP must be communicated through **two** fields, not one (Review F5):

1. `VkVideoEncodeRateControlInfoKHR::rateControlMode = VK_VIDEO_ENCODE_RATE_CONTROL_MODE_DISABLED_BIT_KHR` (+ `layerCount = 0` per §7.1.1).
2. The codec-specific per-slice `constantQp` field — **this is what the driver actually reads in disabled-RC mode**:

   | Codec | Constant-QP field |
   |---|---|
   | H.264 | `VkVideoEncodeH264NaluSliceInfoKHR::constantQp` |
   | H.265 | `VkVideoEncodeH265NaluSliceSegmentInfoKHR::constantQp` |
   | AV1   | `VkVideoEncodeAV1PictureInfoKHR::constantQIndex` |

3. The matching `StdVideo*SliceHeader::slice_qp_delta` (H.264/HEVC) **also** has to be kept consistent with the slice-header bytes the driver writes; we set it as `qp - pic_init_qp` so the generated slice header decodes to the same QP.

4. Clamp `qp` to `[caps.minQp, caps.maxQp]` (queried from `VkVideoEncodeH26{4,5}CapabilitiesKHR.maxQp / minQp`) and to the codec's profile-spec range.

Reference (mirrors `VkVideoEncoderH264::CodecHandleRateControlCmd` lines 617–639 in the Vulkan Samples — sets per-slice `constantQp` whenever `rateControlMode == DISABLED`):

```cpp
if (rate_ctrl_mode_ == VK_VIDEO_ENCODE_RATE_CONTROL_MODE_DISABLED_BIT_KHR) {
  const int32_t qp = ClampQp(sw_rate_controller_->GetQP(),
                              caps_.minQp, caps_.maxQp);
  for (uint32_t i = 0; i < pic_info.naluSliceEntryCount; ++i) {
    nalu_slice[i].constantQp        = qp;                 // (1) Vulkan-side
    std_slice[i].slice_qp_delta     = qp - pic_init_qp_;  // (2) std header
  }
}
```

For `Bitrate::Mode::kExternal` (WebCodecs `EncodeOptions::quantizer`), the same code path runs but the QP comes from the client instead of from the software controller.

### 7.3 GOP

`VkVideoEncodeH264RateControlInfoKHR::gopFrameCount` controls the GOP. For Chromium's default `IPPPP...IPPPP` (`gop_length` from `Config`), we set:

```cpp
h264_rc.idrPeriod          = config_.gop_length.value_or(2048);
h264_rc.gopFrameCount      = config_.gop_length.value_or(2048);
h264_rc.consecutiveBFrameCount = 0;   // no B frames for low-latency
h264_rc.temporalLayerCount = num_temporal_layers_;
h264_rc.flags = VK_VIDEO_ENCODE_H264_RATE_CONTROL_REGULAR_GOP_BIT_KHR
              | VK_VIDEO_ENCODE_H264_RATE_CONTROL_REFERENCE_PATTERN_FLAT_BIT_KHR;
```

### 7.4 Quality Level

`VK_KHR_video_encode_queue` revision 9 added `VkVideoEncodeQualityLevelInfoKHR`, an integer 0..(maxQualityLevel-1) (queried via `vkGetPhysicalDeviceVideoEncodeQualityLevelPropertiesKHR`). We map Chromium's `ContentType`:

| `ContentType` | quality_level |
|---|---|
| `kCamera` (low-latency RTC) | `maxQualityLevel - 1` (fastest) |
| `kDisplay` (screen content) | `maxQualityLevel / 2` (balanced) |

### 7.5 Quantization Maps (`VK_KHR_video_encode_quantization_map`)

Optional extension that lets the application provide a per-block delta-QP image. We expose this through `VideoEncoder::EncodeOptions::quantizer` and the WebCodecs `VideoEncoder.encode(frame, { qpMap })` extension.

```cpp
VkVideoEncodeQuantizationMapInfoKHR qp_map = {
    .sType        = VK_STRUCTURE_TYPE_VIDEO_ENCODE_QUANTIZATION_MAP_INFO_KHR,
    .quantizationMap = qp_map_image_view_,
    .quantizationMapExtent = qp_map_extent_,
};
ChainNext(encode_info, qp_map);
```

### 7.6 Intra-refresh (`VK_KHR_video_encode_intra_refresh`)

For very low-latency RTC, intra-refresh distributes I-blocks across multiple frames rather than periodic IDR. Useful for packet loss recovery. Wire it via:

```cpp
VkVideoEncodeIntraRefreshInfoKHR ir = {
    .sType = VK_STRUCTURE_TYPE_VIDEO_ENCODE_INTRA_REFRESH_INFO_KHR,
    .intraRefreshCycleDuration = intra_refresh_period_frames_,
    .intraRefreshIndex         = (frame_num_ % intra_refresh_period_frames_),
};
ChainNext(encode_info, ir);
```

This maps cleanly to `webrtc::VideoCodecMode::kRealtimeVideo` semantics.

### 7.7 Manual reference buffer control

> Review F9: Chromium already exposes a manual-reference path through `Config::manual_reference_buffer_control` and `VideoEncoder::EncodeOptions::reference_buffers` / `update_buffer`. D3D12 implements it for H.264/H.265/AV1. Our Vulkan design needs to at least describe how it maps; Vulkan's DPB is already slot-based so it is a natural target.

#### MVP behavior

For the first ship we **decline** manual reference mode:

```cpp
EncoderStatus VulkanVideoEncodeAccelerator::Initialize(const Config& config, ...) {
  if (config.manual_reference_buffer_control) {
    return EncoderStatus(EncoderStatus::Codes::kEncoderUnsupportedConfig,
                         "Vulkan encode MVP does not support manual reference "
                         "buffer control yet.");
  }
  ...
}

// And in the VideoEncoderInfo we publish:
encoder_info_.number_of_manual_reference_buffers = 0;
```

#### Phase-6 mapping (when we turn it on)

Once the automatic path is stable we add the slot mapping:

| `EncodeOptions` field | Vulkan field |
|---|---|
| `reference_buffers[i]` (uint8_t buffer id) | index into the `VulkanVideoEncoderDpb` slot table; populates `VkVideoEncodeInfoKHR.pReferenceSlots[i]` |
| `update_buffer` (optional uint8_t) | `pSetupReferenceSlot->slotIndex` |

Validation rules (per D3D12 reference):

1. Every `reference_buffers[i]` must currently be marked-as-used in our DPB. If not, return `EncoderStatus::Codes::kBadReferenceBuffer`.
2. `update_buffer` must be ≤ `number_of_manual_reference_buffers - 1`. Otherwise `kBadReferenceBuffer`.
3. The number of populated reference slots must be ≤ `caps.maxActiveReferencePictures`.
4. We never emit a P/B frame with `reference_buffers.empty()`; the encoder library forces an IDR instead.

`VideoEncoderInfo::number_of_manual_reference_buffers` is then set to `min(maxDpbSlots - 1, kReasonableUpperBound)` (so the runtime DPB always has at least one slot for the reconstructed picture).

---

## 8. Bitstream Output, Queries and Feedback <a name="8-bitstream-output"></a>

> **Important** (Review F2): bitstream composition reserves an in-buffer header region with `dstBufferOffset` and informs the driver of those externally-encoded bytes via `precedingExternallyEncodedBytes`. This is what keeps the driver rate-control model honest around IDRs that carry app-prepended SPS/PPS/VPS/AUD bytes.

### 8.1 Bitstream Buffer

```cpp
VkBufferCreateInfo buf_ci = {
    .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
    .pNext = &profile_list,                                    // mandatory
    .size  = GetEncodeBitstreamBufferSize(coded_size_)
           + kIDRBitstreamHeaderReserve,                       // see §8.3
    .usage = VK_BUFFER_USAGE_VIDEO_ENCODE_DST_BIT_KHR
           | VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
};
```

Size estimate uses the existing `GetEncodeBitstreamBufferSize()` helper from `media/gpu/gpu_video_encode_accelerator_helpers.h`; we add a small `kIDRBitstreamHeaderReserve` (e.g. 4 KiB) for the prepended VPS/SPS/PPS/AUD bytes — see §8.3.

### 8.2 Encode Feedback Query

#### 8.2.1 Capabilities required

At session creation, the wrapper queries `VkVideoEncodeCapabilitiesKHR` and refuses to initialize unless `supportedEncodeFeedbackFlags` contains at least:

```text
VK_VIDEO_ENCODE_FEEDBACK_BITSTREAM_BUFFER_OFFSET_BIT_KHR
VK_VIDEO_ENCODE_FEEDBACK_BITSTREAM_BYTES_WRITTEN_BIT_KHR
```

(If the driver also advertises `VK_VIDEO_ENCODE_FEEDBACK_BITSTREAM_HAS_OVERRIDES_BIT_KHR`, we surface that into `BitstreamBufferMetadata` and re-read driver-emitted parameter bytes for the next IDR.)

#### 8.2.2 Pool

```cpp
VkQueryPoolCreateInfo query_ci = {
    .sType      = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO,
    .pNext      = &profile_list,
    .queryType  = VK_QUERY_TYPE_VIDEO_ENCODE_FEEDBACK_KHR,
    .queryCount = kEncodeFeedbackQueryPoolSize,                // depth of in-flight encodes
};
```

#### 8.2.3 Inline-query path (preferred) and Begin/End-query fallback

```cpp
if (video_maintenance1_supported_) {
    // VK_KHR_video_maintenance1 — inline query, no round trips.
    VkVideoInlineQueryInfoKHR inline_query = {
        .sType      = VK_STRUCTURE_TYPE_VIDEO_INLINE_QUERY_INFO_KHR,
        .queryPool  = encode_feedback_query_pool_,
        .firstQuery = query_slot,
        .queryCount = 1,
    };
    ChainNext(encode_info, inline_query);
    vkCmdEncodeVideoKHR(cmd_buf, &encode_info);
} else {
    // Fallback: bracket the encode with explicit query commands
    // (same as VkVideoEncoder samples).
    vkCmdBeginQuery(cmd_buf, encode_feedback_query_pool_, query_slot, 0);
    vkCmdEncodeVideoKHR(cmd_buf, &encode_info);
    vkCmdEndQuery(cmd_buf, encode_feedback_query_pool_, query_slot);
}
```

Both paths must remain in tree because some drivers (including some current ICDs we plan to ship on) do not yet advertise `VK_KHR_video_maintenance1`.

#### 8.2.4 Result statuses

After the timeline semaphore signals, we read the query slot via `vkGetQueryPoolResults(..., VK_QUERY_RESULT_WITH_STATUS_BIT_KHR)`:

| Status | Action |
|---|---|
| `VK_QUERY_RESULT_STATUS_COMPLETE_KHR` | Read `offset` / `bytesWritten`; copy to shmem; deliver. |
| `VK_QUERY_RESULT_STATUS_NOT_READY_KHR` | Wait again. |
| `VK_QUERY_RESULT_STATUS_ERROR_KHR` | Report `EncoderStatus::Codes::kEncoderFailedEncode`. |
| `VK_QUERY_RESULT_STATUS_INSUFFICIENT_BITSTREAM_BUFFER_RANGE_KHR` | **See §8.2.5 — only replayable if state allows.** |

#### 8.2.5 `INSUFFICIENT_BITSTREAM_BUFFER_RANGE_KHR` handling

> Review F3 calls out that "grow + re-encode" is *not* always safe. We adopt the following policy:
>
> 1. Confirm the encoder state (input image, reference setup, rate-control state) has not yet advanced past this frame — i.e. no subsequent frame has been issued.
> 2. If safe: grow `bitstream_buffer_` (e.g. ×2 up to a hard ceiling), re-record the encode command for the same frame, re-submit.
> 3. If not safe (we already advanced because the runner is pipelined): fail this frame with `EncoderStatus::Codes::kEncoderFailedEncode`, **force the next frame to be a keyframe**, and grow the buffer ceiling for subsequent encodes.
> 4. Always grow for the next IDR (the buffer is sized for steady-state frames; IDRs need 4× to 8×).

This avoids the "double advance" hazard the review flags and is consistent with how `media/gpu/vaapi/h264_vaapi_video_encoder_delegate.cc` handles encoder-state drift.

### 8.3 Bitstream Composition with `dstBufferOffset` + `precedingExternallyEncodedBytes`

Vulkan Video encoders emit **only slice NALUs by default** (for H.264 and H.265). For HEVC we additionally need a VPS at the beginning of the stream / on parameter change. AV1 is different: the driver writes all OBUs and we leave the buffer alone.

> Review F2: previously we wrote that headers are prepended **after** the encode call returns. That is fine for the bytestream, but it lies to the driver's rate controller — the driver will not know about the header bytes when deciding QP for the current frame.

The fix is to reserve in-buffer space for the headers **before** the encode call and tell the driver:

```cpp
// 1. Decide if this frame needs headers (true on IDR or on parameter change).
const bool emit_headers = pic->idr || params_dirty_;

// 2. If yes, build (or retrieve) the header bytes BEFORE command recording.
std::vector<uint8_t> headers;
if (emit_headers) {
    BuildOrFetchAnnexBHeaders(&headers);    // see §8.3.2
}
const uint32_t header_size = static_cast<uint32_t>(headers.size());

// 3. Reserve header_size bytes at the start of the destination buffer.
VkVideoEncodeInfoKHR encode_info = {
    .sType                            = VK_STRUCTURE_TYPE_VIDEO_ENCODE_INFO_KHR,
    .pNext                            = &h264_pic_info,
    .dstBuffer                        = bitstream_buffer_,
    .dstBufferOffset                  = header_size,                    // <-- reserve
    .dstBufferRange                   = bitstream_size_ - header_size,
    .srcPictureResource               = src_pic_resource_,
    .pSetupReferenceSlot              = &setup_ref_slot_,
    .referenceSlotCount               = active_ref_count_,
    .pReferenceSlots                  = active_reference_slots_,
    .precedingExternallyEncodedBytes  = header_size,                    // <-- tell driver
};

// 4. Record vkCmdEncodeVideoKHR.
//    (Driver will write slice NALUs starting at offset = header_size.)

// 5. After timeline semaphore signals: feedback query gives us
//    { bitstream_start_offset = header_size,
//      bytes_written = N (slice NALUs only) }.

// 6. Compose the final Annex-B/HEVC/AV1 stream into the client shmem:
//      shmem[0 .. header_size)               = headers (from step 2)
//      shmem[header_size .. header_size + N) = vk_buf[header_size .. header_size + N)

// 7. Report payload_size_bytes = header_size + N to the VEA client.
```

The encoder's RC model now sees the real access-unit size, not just the slice size. The Vulkan Video Samples carry this concept as `bitstreamHeaderBufferSize` / `bitstreamHeaderOffset` on `VkVideoEncodeFrameInfo` and forward those values to the same Vulkan struct.

#### 8.3.1 When are headers needed?

| Codec | Per-IDR | On `RequestEncodingParametersChange()` causing IDR | Per non-IDR |
|---|---|---|---|
| H.264 | SPS + PPS (+ AUD optional) | Yes — recreate `VkVideoSessionParametersKHR`, re-emit | None (driver writes slice only) |
| H.265 | VPS + SPS + PPS | Yes | None |
| AV1 | Driver writes Temporal-Delimiter + Sequence Header + Frame Header OBUs | n/a (no app prepend) | Driver writes Frame Header OBU |

For AV1 we therefore set `header_size = 0` and `precedingExternallyEncodedBytes = 0` on every frame; the driver writes the entire stream.

#### 8.3.2 Prefer driver-emitted SPS/PPS/VPS bytes when available

We prefer the bytes returned by `vkGetEncodedVideoSessionParametersKHR(...)` over our own `H26xAnnexBBitstreamBuilder` output whenever:

- The driver advertises `VK_VIDEO_ENCODE_FEEDBACK_BITSTREAM_HAS_OVERRIDES_BIT_KHR`, **or**
- We created the session with `VK_VIDEO_SESSION_CREATE_ALLOW_ENCODE_PARAMETER_OPTIMIZATIONS_BIT_KHR`.

In that case the driver may have legitimately overridden some SPS fields (`level_idc`, `constraint_set*_flag`, VUI bits) and our app-built bytes would diverge from what is encoded by the slice NALUs.

The app-built `H26xAnnexBBitstreamBuilder` path remains for drivers that don't expose `vkGetEncodedVideoSessionParametersKHR` or for codecs where we override the bytes ourselves (rare).

### 8.4 Readback Path

```
encoder_task_runner_              child_task_runner_ (renderer-facing)
─────────────────────             ────────────────────────────────────

[Encode submitted]
       │
       │ (vkQueueSubmit signals timeline_sem_)
       │
       ▼
[vkWaitSemaphores OR Fence callback]
       │
       ▼
[vkGetQueryPoolResults → {offset,size,status}]
       │
       ▼
[memcpy mapped_bitstream+offset → shmem]
       │
       ▼
[PostTask BitstreamBufferReady(id, metadata)] ─►  client_->BitstreamBufferReady(...)
```

The wait can be implemented with a `VulkanFenceHelper` (already in `gpu/vulkan/vulkan_fence_helper.h`) that fires a callback when the fence/timeline-value is reached, avoiding a dedicated waiter thread.

---

## 9. Synchronization, DMA-BUF and Cross-Queue Handoffs <a name="9-sync"></a>

### 9.1 Three Queues, One Device

In a typical session we use up to three queues from the same `VkDevice`:

| Queue family | Purpose |
|---|---|
| `VK_QUEUE_GRAPHICS_BIT` (or `VK_QUEUE_COMPUTE_BIT`) | Compositor (SkiaRenderer), input pre-processing (NV12 conversion) |
| `VK_QUEUE_TRANSFER_BIT` | DMA-BUF imports, staging copies |
| `VK_QUEUE_VIDEO_ENCODE_BIT_KHR` | `vkCmdEncodeVideoKHR` |

Cross-queue ownership transfers are done with two-sided pipeline barriers and a `VkSemaphore` (binary or timeline).

### 9.2 Timeline Semaphore (recommended)

```cpp
VkSemaphoreTypeCreateInfo type = {
    .sType = VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO,
    .semaphoreType = VK_SEMAPHORE_TYPE_TIMELINE,
    .initialValue  = 0,
};
VkSemaphoreCreateInfo ci = {
    .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO,
    .pNext = &type,
};
vkCreateSemaphore(device_, &ci, nullptr, &timeline_sem_);
```

The producer (e.g. compositor) signals `timeline_sem_, value=N`, and the encoder waits on `value=N` before starting frame N. Symmetrically, the encoder signals `value=N+ENCODER_DONE` and the readback task waits on that.

### 9.3 DMA-BUF Import (Linux)

Same code path as the decode design (section 5.5) — `VK_EXT_external_memory_dma_buf`. The `gpu::SharedImageBackingVulkanDmaBuf` already imports DMA-BUFs as `VkImage`. We reuse it.

### 9.4 Implicit vs Explicit DMA-BUF Sync

We **explicitly** import sync FDs:

```cpp
ioctl(dmabuf_fd, DMA_BUF_IOCTL_EXPORT_SYNC_FILE, &sync_fd);
VkImportSemaphoreFdInfoKHR isf = {
    .sType = VK_STRUCTURE_TYPE_IMPORT_SEMAPHORE_FD_INFO_KHR,
    .handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT,
    .fd = sync_fd,
};
vkImportSemaphoreFdKHR(device_, &isf);
```

This avoids relying on the kernel's implicit synchronization (which can stall the entire pipeline).

### 9.5 Encode Queue Submissions Must Use sync2

`VK_KHR_video_encode_queue` depends on `VK_KHR_synchronization2`. We must use `vkQueueSubmit2` everywhere encode commands are submitted.

---

## 10. Protected (DRM) Content for Encode — **Non-goal for MVP** <a name="10-drm"></a>

> Review F12: protected encode is explicitly **not in scope** for this design's MVP and is **not in the execution plan**. It is documented here only so future work can pick it up cleanly when a real consumer materialises.

### 10.1 Why it is a non-goal

The protected-encode use cases that have been *imagined* (re-encoding HDCP content, TEE-attested camera capture) are real but **none of them have a Chrome consumer today**:

- WebRTC, MediaRecorder, WebCodecs, screen capture, tab capture — all consume clear content and produce clear bitstream.
- Widevine's CDM API exposes decryption keys only; there is no Chrome-visible *encryption* key flow.
- HDCP re-encryption for simulcast is not a feature Chrome currently asks for.

Adding protected encode is not just a Vulkan flag. It requires **all** of the following, none of which exists:

- A protected `VideoFrame` source (the input image must already be in protected memory).
- A Chrome/CDM contract that issues encryption keys to the encoder.
- A protected destination — `VkBuffer` allocated with `VK_MEMORY_PROPERTY_PROTECTED_BIT`.
- A protected-aware Mojo path so the bitstream never leaves the protected boundary.
- A protected client (e.g. an HDCP re-encryption sink) that can actually consume the protected bitstream.

Until a concrete consumer asks for it we will **not** implement protected encode and will return `EncoderStatus::Codes::kEncoderUnsupportedConfig` if a caller requests it.

### 10.2 Sketch (for whoever picks this up later)

Vulkan exposes the building blocks symmetrically with decode (see `VULKAN_VIDEO_INTEGRATION_DESIGN_DOCUMENT.md` §6 for the analogous decode design):

```cpp
VkVideoSessionCreateInfoKHR session_info = {
    .flags = VK_VIDEO_SESSION_CREATE_PROTECTED_CONTENT_BIT_KHR,
    ...
};
VkProtectedSubmitInfo protected_submit = {
    .sType            = VK_STRUCTURE_TYPE_PROTECTED_SUBMIT_INFO,
    .protectedSubmit  = VK_TRUE,
};
VkSubmitInfo si = { .pNext = &protected_submit, ... };
vkQueueSubmit(protected_encode_queue, 1, &si, fence);
```

The output bitstream lives in a protected `VkBuffer` (`VK_MEMORY_PROPERTY_PROTECTED_BIT`) which is then either re-encrypted by the CDM or sent over an HDCP-protected link.

### 10.3 Trigger to revisit

We will only re-evaluate this section when **all three** of the following land:

1. A Chrome product / extension API requests protected encode (e.g. a confidential meeting / studio relay).
2. CDM exposes encryption keys (analogous to the existing decryption-key flow).
3. At least one shipping Vulkan ICD advertises protected encode capabilities on a platform Chrome targets.

Until then, the topic stays out of the execution plan and the test plan.

---

## 11. Execution Plan <a name="11-execution-plan"></a>

> **Applies to**: from-scratch plan. The six-phase ~19–21 week plan below assumes Chromium writes Layer B itself (per-codec delegates, Std-Video conversion, DPB slot manager, codec-specific rate-control wiring). **For the recommended Model D / Model A, this plan is superseded** by `VULKAN_VIDEO_LIBRARY_INTEGRATION_ANALYSIS.md` §7, which targets ~10.5–12 weeks because Layer B is already implemented in the upstream library; Chromium only writes the codec-neutral shim, the BYO-`VkDevice` plumbing, and the SharedImage representation. Read this section for the from-scratch / Model B effort baseline; read the library analysis for the actual recommended plan.

> Reordered for v1.1 per Review F11+F12+F13. The earliest runnable milestone is now **H.264 CQP on a private Vulkan device** (Phase 2), much easier to debug than the original "zero-copy + driver RC + multi-codec all at once" mix. Protected encode is removed from the plan (Review F12).

The plan is broken into **6 phases**, ~18–21 engineer-weeks total.

### Phase 1: Foundation + private Vulkan device (3 weeks)

| Task | Files | Notes |
|---|---|---|
| **DECISION**: device ownership (Review F11) | `media/gpu/vulkan/vulkan_video_device_context.{h,cc}` | **Phase 1 ships a private media `VkDevice`** — its own `VulkanInstance`, queue selection, memory tracker. Compositor-device sharing is deferred to Phase 4. |
| Feature flag + build flag | `media/base/media_switches.h`, `media/gpu/args.gni` | `kVulkanVideoEncode`, `enable_vulkan_video_encode` |
| `VulkanVideoEncoderWrapper` skeleton | `media/gpu/vulkan/vulkan_video_encoder_wrapper.{h,cc}` | Device init, queue selection, capability enumeration, `ValidateRateControl()` (§7.1.1) |
| Capability surface (§3.7) | wrapper + accelerator | `GetSupportedProfiles()` walks `vkGetPhysicalDeviceVideoCapabilitiesKHR` for H.264/H.265/AV1; populates initial `VideoEncoderInfo` |
| Session + session-parameter lifecycle | wrapper | `VkVideoSessionKHR`, `VkVideoSessionParametersKHR`, memory binding |
| Image pools | `media/gpu/vulkan/vulkan_video_image_pool.{h,cc}` | input / DPB / staging — internal pools only at this stage (no SharedImage) |
| Bitstream buffer pool with header reserve (§8.1) | `media/gpu/vulkan/vulkan_bitstream_buffer_pool.{h,cc}` | persistent-mapped `VkBuffer`; reserves header space at start |
| Query-pool wrapper (inline + begin/end paths — §8.2) | wrapper | both maintenance1 and fallback paths must compile from day one |
| `VulkanVideoEncodeAccelerator` shell | `media/gpu/vulkan/vulkan_video_encode_accelerator.{h,cc}` | Implements `VideoEncodeAccelerator`, no codec yet; declines `manual_reference_buffer_control` (§7.7) |
| Factory wiring | `media/gpu/gpu_video_encode_accelerator_factory.cc` | guarded by `kVulkanVideoEncode` |
| **No** SharedImage zero-copy yet | — | reserved for Phase 4 |
| **No** protected encode | — | non-goal (Review F12) |

**Exit criterion**: `media_unittests --gtest_filter="*Vulkan*"` green; `chrome --enable-features=VulkanVideoEncode` reports H.264/H.265/AV1 profiles in `chrome://gpu` without crashing.

### Phase 2: H.264 CQP encode (3 weeks)

The lowest-risk first milestone: **constant-QP H.264 encode on the private Vulkan device, no driver rate control, shmem input only**, producing valid Annex-B that round-trips through software decoders.

| Task | Files | Notes |
|---|---|---|
| `VulkanVideoEncoderDelegate` base class | `media/gpu/vulkan/vulkan_video_encoder_delegate.{h,cc}` | Port of `VaapiVideoEncoderDelegate` |
| `H264VulkanVideoEncoderDelegate` | `media/gpu/vulkan/h264_vulkan_video_encoder_delegate.{h,cc}` | Port `H264VaapiVideoEncoderDelegate::Initialize/UpdateSPS/UpdatePPS/PrepareEncodeJob/SubmitFrameParameters` |
| `H264SPS → StdVideoH264SequenceParameterSet` converter | same | ~250 LOC, one-to-one field copy |
| `H264PPS → StdVideoH264PictureParameterSet` converter | same | ~150 LOC |
| `H264SliceHeader → StdVideoEncodeH264SliceHeader` | same | ~200 LOC |
| `H264Picture → StdVideoEncodeH264PictureInfo` | same | ref-pic flags, MMCO, ref-list mod ops |
| DPB slot manager | `media/gpu/vulkan/vulkan_video_encoder_dpb.{h,cc}` | mirrors `H264DPB` |
| **Rate control: `DISABLED` mode only**, with per-slice `constantQp` (§7.2) | delegate + wrapper | `layerCount = 0`; QP threaded through both `VkVideoEncodeH264NaluSliceInfoKHR::constantQp` and `StdVideoEncodeH264SliceHeader::slice_qp_delta` |
| **Bitstream composition with `dstBufferOffset` + `precedingExternallyEncodedBytes` (§8.3)** | delegate + wrapper | app-built SPS/PPS via `media/gpu/h264_builder.{h,cc}`; if driver advertises overrides, prefer driver bytes via `vkGetEncodedVideoSessionParametersKHR` |
| Shmem-only input (`Config::StorageType::kShmem`, §6.1) | wrapper | linear-staging → tiled copy; no GPU-backed path yet |
| `INSUFFICIENT_BITSTREAM_BUFFER_RANGE` handling (§8.2.5) | wrapper | replay-safe rules; force-keyframe-on-fail in pipelined mode |
| Round-trip validation | `media/gpu/test/video_encoder/vulkan_loopback_validator.{h,cc}` (NEW per test plan) | output decodes through openh264 / VA-API decoder |

**Exit criterion**: Tier-2 H.264 encode → SW-decode → PSNR ≥ 38 dB at CQP 26 on 1080p30 `bear` clip; zero validation-layer errors.

### Phase 3: H.264 CBR/VBR + RequestEncodingParametersChange (2 weeks)

Driver-managed rate control is added **after** CQP is stable.

| Task | Notes |
|---|---|
| `VkVideoEncodeRateControlInfoKHR` for CBR (`averageBitrate == maxBitrate`, §7.1.1) | reuse `H264RateCtrlRTC` only as a *bitrate target predictor*, not the QP source |
| VBR (`averageBitrate ≤ maxBitrate`) | same struct, different mode |
| HRD/CPB tuning per `virtualBufferSizeInMs`, `initialVirtualBufferSizeInMs` | calibrated against JVT decoder output (Tier 5) |
| `RequestEncodingParametersChange()` (bitrate / framerate / size) | recreate `VkVideoSessionParametersKHR` on profile-affecting change; emit IDR with fresh SPS/PPS |
| `Bitrate::Mode::kVariable` path in delegate | tested with adversarial scenes |

**Exit criterion**: 30-second WebRTC loopback at 1.5 Mbps CBR produces actual bitrate within ±10% on `linux-vulkan-video-rel`.

### Phase 4: Chrome GPU-backed input (3 weeks)

Now we touch shared images and (cautiously) consider the compositor `VkDevice`.

| Task | Files | Notes |
|---|---|---|
| `SetCommandBufferHelperCB()` plumbing | accelerator | mirrors `D3D12VideoEncodeAccelerator::SetCommandBufferHelperCB()` |
| Proposed `SharedImageManager::ProduceVulkanVideo()` (NEW upstream piece) | `gpu/command_buffer/service/shared_image/` | parallels ToT's `ProduceVideo()` path for existing video representations; emits a new `VulkanVideoImageRepresentation` |
| `VulkanVideoImageRepresentation` + scoped read access | same | acquire/release semaphores, queue-family transfer |
| `Config::StorageType::kGpuMemoryBuffer` end-to-end (§6.2, §6.3) | accelerator + wrapper | shared-image first; DMA-BUF / shared-handle / AHB fallbacks |
| **Optional**: bring up dual-device path | gated by capability check | if encode queue family is reachable from the compositor's `VkDevice` *and* image-usage compatibility is OK, switch from private device to compositor device |
| `VideoEncoderInfo` upgrade | accelerator | flip `supports_native_handle = true`, populate `gpu_supported_pixel_formats` |
| Per-platform fallbacks (Linux DMA-BUF, Win shared NT handle, Android AHB) | wrapper | only when shared-image path declines |

**Exit criterion**: tab-capture → WebRTC encode loop runs without CPU readback on at least Linux/NVIDIA + Linux/ANV.

### Phase 5: HEVC + AV1 (5 weeks)

With H.264 stable across CQP/CBR/VBR and GPU input working, we add codecs.

| Task | Files | Notes |
|---|---|---|
| `H265VulkanVideoEncoderDelegate` | `media/gpu/vulkan/h265_vulkan_video_encoder_delegate.{h,cc}` | Chromium-side state machine ported from `media/gpu/windows/d3d12_video_encode_h265_delegate.cc` (Review F1); Vulkan command flow ported from `VkVideoEncoderH265.cpp` |
|   • Reuse `media/parsers/h265_parser.h` (`H265VPS`/`SPS`/`PPS`/`SliceHeader`) | | |
|   • Reuse `media/gpu/h265_builder.{h,cc}` for VPS/SPS/PPS packed bytes | | |
|   • Wire `media/gpu/h265_dpb.{h,cc}` (currently used by decoder; encoder needs a slim copy of its logic) | | |
| HEVC main10 support | | adds `caps.lumaBitDepth = VK_VIDEO_COMPONENT_BIT_DEPTH_10_BIT_KHR` path |
| `AV1VulkanVideoEncoderDelegate` | `media/gpu/vulkan/av1_vulkan_video_encoder_delegate.{h,cc}` | VA-API AV1 delegate + Vulkan Samples AV1 encoder. Driver emits all OBUs; `precedingExternallyEncodedBytes = 0`. |
|   • Reuse `media/gpu/av1_builder.{h,cc}` | | |

**Exit criterion**: Tier 5 conformance pass-rates per the test plan (JVT H.264 ≥ 95%, JCT-VC HEVC ≥ 90%, Argon AV1 ≥ 90%) on at least 2 vendors.

### Phase 6: Advanced features and polish (3 weeks)

| Task | Notes |
|---|---|
| Temporal SVC (T=2, T=3) | `VkVideoEncodeRateControlInfoKHR.layerCount = N` + per-layer info; `temporalLayerCount` must match (§7.1.1) |
| Spatial SVC (S=2, S=3) for AV1 (and where supported) | multiple sessions / inter-layer prediction |
| `vkGetPhysicalDeviceVideoEncodeQualityLevelPropertiesKHR` → expose `VideoEncoderInfo::reports_average_qp`, `requested_resolution_alignment` (§3.7) | |
| `VK_KHR_video_encode_quantization_map` | binds `EncodeOptions::quantizer` |
| `VK_KHR_video_encode_intra_refresh` | maps `WebrtcCodecMode::kRealtimeVideo` retransmit recovery |
| Manual reference buffer control (§7.7) | turns on `EncodeOptions::reference_buffers` / `update_buffer` mapping |
| Fallback logic: if Vulkan encode init fails, pop VEA factory and fall through to VA-API/V4L2/D3D12 | `media/gpu/gpu_video_encode_accelerator_factory.cc` |
| `MemoryDumpProvider::OnMemoryDump()` | mirrors VA-API VEA |
| Validation-layer enforcement in CI | `VK_LOADER_LAYERS_ENABLE=*validation` step in CQ |

> **Protected encode is NOT in this plan.** Per Review F12 and §10, we will only revisit protected encode when a Chrome/CDM consumer actually exists (none today). Adding it requires CDM-side encryption-key API changes that are not on any roadmap.

### 11.7 Directory Structure (after Phase 6)

```
media/gpu/vulkan/
├── BUILD.gn
├── DEPS
├── OWNERS
├── vulkan_video_device_context.{h,cc}      // Phase 1: private device
├── vulkan_video_encoder_wrapper.{h,cc}     // Phase 1
├── vulkan_video_encode_accelerator.{h,cc}  // Phase 1
├── vulkan_video_encoder_delegate.{h,cc}    // Phase 2
├── vulkan_video_encoder_dpb.{h,cc}         // Phase 2
├── vulkan_video_image_pool.{h,cc}          // Phase 1
├── vulkan_bitstream_buffer_pool.{h,cc}     // Phase 1
├── h264_vulkan_video_encoder_delegate.{h,cc}  // Phase 2
├── h265_vulkan_video_encoder_delegate.{h,cc}  // Phase 5
├── av1_vulkan_video_encoder_delegate.{h,cc}   // Phase 5
├── vulkan_video_encode_caps_unittest.cc
├── vulkan_video_encoder_wrapper_unittest.cc
├── vulkan_video_encode_accelerator_unittest.cc
├── h264_vulkan_video_encoder_delegate_unittest.cc
└── test/
    ├── vulkan_video_encode_test_environment.{h,cc}
    └── vulkan_video_encode_test_runner.cc
```

(Same layout as `media/gpu/vaapi/`, just with `vulkan` and `vulkan_video_encode` in place of `vaapi`. `vulkan_video_device_context` is the only new top-level abstraction not present on the VA-API side.)

### 11.8 Timeline

```
Week     1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21
P1 Found ████████  (private VkDevice, factory wiring, capabilities)
P2 264CQP        ████████  (H.264 CQP, shmem input, app-built SPS/PPS)
P3 264 RC                ██████  (CBR/VBR, RequestEncodingParametersChange)
P4 GPU in                       ████████  (SharedImage/ProduceVulkanVideo, optional shared device)
P5 HEVC                                 ████████████████  (HEVC + AV1)
P6 Adv                                                    ████████████  (SVC, QP map, intra-refresh, manual refs)
```

Critical path: P1 → P2 → P3 → P4 → P5 → P6.

The earliest deliverable that produces a **valid Annex-B H.264 stream end-to-end** is end of Phase 2 (~6 weeks). Driver RC, GPU-backed input, and other codecs follow from a stable base.

---

## 12. Test Plan <a name="12-test-plan"></a>

### 12.1 Unit Tests

```cpp
// vulkan_video_encoder_wrapper_unittest.cc
TEST_F(VulkanVideoEncoderWrapperTest, CreateSession_H264_HighProfile) { … }
TEST_F(VulkanVideoEncoderWrapperTest, CreateSession_H265_Main10) { … }
TEST_F(VulkanVideoEncoderWrapperTest, ImportDmaBufAsInputImage) { … }
TEST_F(VulkanVideoEncoderWrapperTest, EncodeFeedbackQuery_ReportsStatus) { … }

// h264_vulkan_video_encoder_delegate_unittest.cc
// Mirrors h264_vaapi_video_encoder_delegate_unittest.cc tests:
TEST_F(H264VulkanEncoderDelegateTest, SPS_To_StdVideoSPS_RoundTrip) { … }
TEST_F(H264VulkanEncoderDelegateTest, PrepareEncodeJob_IDR_HasSPS_PPS) { … }
TEST_F(H264VulkanEncoderDelegateTest, PrepareEncodeJob_P_HasRefList) { … }
TEST_F(H264VulkanEncoderDelegateTest, UpdateRates_RecreatesSessionParams) { … }
TEST_F(H264VulkanEncoderDelegateTest, TemporalLayers_T2_T3) { … }
```

### 12.2 Integration Tests

Reuse `media/gpu/test/video_encode_accelerator_tests.{h,cc}` — the existing VEA test harness used by VA-API/V4L2/D3D12. ToT does **not** have a backend selector or `--test_video_data` flag; the input video is positional. Add a backend selector as new test-harness work:

```bash
./video_encode_accelerator_tests \
    --implementation=vulkan \
    --codec=h264high \
    bear_1280x720.yuv.webm \
    bear_1280x720.yuv.webm.json

./video_encode_accelerator_perf_tests \
    --speed \
    --implementation=vulkan \
    4k_3840x2160.yuv.webm \
    4k_3840x2160.yuv.webm.json
```

### 12.3 Cross-API Bit-Exact / Conformance

| Test | Tooling | Pass criterion |
|---|---|---|
| Bit-exact: VA-API vs Vulkan (same input, same QP) | `ffmpeg -i` + SSIM/PSNR | PSNR delta ≤ 0.5 dB |
| WebRTC interop | Chrome ↔ Firefox loopback over `pc.addTrack(...)` | No decoder-side errors |
| Decode roundtrip | `vulkan_video_decoder` (our existing decoder backend) decodes encoder output | bit-exact YUV |
| Cross-vendor | NVIDIA, AMD, Intel ARC, Adreno (Android) | Same encoder output ± driver QP rounding |
| Validation layers | `VK_LOADER_LAYERS_ENABLE=*validation`, `VK_VALIDATION_VALIDATE_SYNC=true` | Zero validation errors |
| H.264 conformance | JVT reference decoder over encoder output | All conformance assertions pass |

### 12.4 Performance KPIs

For 1080p30 H.264 encode on a target reference GPU (to be selected per CI bot pool):

| Metric | Target |
|---|---|
| Frames per second (encode-only) | ≥ 200 fps (forward-looking target; baseline measured at Phase 2) |
| End-to-end latency (Encode → BitstreamBufferReady) | ≤ 8 ms |
| Memory residency | ≤ 1.5× VA-API for the same session config |
| GPU utilization (encode queue) | ≤ 30 % (leaving headroom for compositor) |

These targets are placeholders until we capture a measured baseline on the chosen CI hardware; the actual ceilings will vary across vendors and GPU generations.

### 12.5 Stress / Fuzz

- ASan + TSan builds in CQ (existing `media_unittests` ASan job).
- Fuzz target on `H264SPS → StdVideoH264SequenceParameterSet` conversion (range: every field, full int range).
- 24-hour soak in WebRTC echo-test bot.

### 12.6 Test Matrix

| Codec | Profile | Resolution | Bit Depth | RC Mode | Layers |
|---|---|---|---|---|---|
| H.264 | Constrained Baseline | 320×240..720p | 8 | CBR, CQP | T=1 |
| H.264 | Main, High | 1080p, 4K | 8 | CBR, VBR, CQP | T=1, T=2, T=3 |
| H.265 | Main | 1080p, 4K | 8 | CBR, VBR, CQP | T=1, T=2, T=3 |
| H.265 | Main10 | 1080p, 4K | 10 | CBR, VBR, CQP | T=1 |
| AV1 | Main | 1080p, 4K | 8, 10 | CBR, VBR, CQP | T=1, T=2, T=3 |

---

## 13. Open Questions, Risks and Speculations <a name="13-risks"></a>

> Flagged speculation — these need driver / spec confirmation before locking down.

| # | Item | Risk | Mitigation |
|---|---|---|---|
| Q1 | Some drivers ignore `VK_VIDEO_SESSION_CREATE_ALLOW_ENCODE_PARAMETER_OPTIMIZATIONS_BIT_KHR` and silently re-encode SPS/PPS; if our app-built packed SPS differs from the driver's emit, downstream decoders may complain | Med | Always call `vkGetEncodedVideoSessionParametersKHR()` after session-params creation and prefer those bytes |
| Q2 | `VkVideoEncodeRateControlInfoKHR.virtualBufferSizeInMs` semantics differ subtly between NVIDIA / AMD / Intel | Med | Validate output bitstream HRD compliance against `JVT decoder` in CI |
| Q3 | Quality-level enum is unbounded per driver (`VkVideoEncodeCapabilitiesKHR.maxQualityLevels` varies 1..N) | Low | Map Chromium's binary `kCamera/kDisplay` to `(maxQualityLevels-1)` / `(maxQualityLevels/2)` |
| Q4 | Some drivers refuse `VK_FORMAT_G8_B8R8_2PLANE_420_UNORM` as `VIDEO_ENCODE_SRC` and require `VK_FORMAT_G8_B8_R8_3PLANE_420_UNORM` | Low | Negotiate format via `vkGetPhysicalDeviceVideoFormatPropertiesKHR()` at init |
| Q5 | `VK_QUERY_RESULT_STATUS_INSUFFICIENT_BITSTREAM_BUFFER_RANGE_KHR` may force us to re-encode IDRs at 4K with single bitrate-allocated buffer | High at 4K | Allocate the bitstream buffer at `GetEncodeBitstreamBufferSize(coded_size)` × `kIDRBitstreamMultiplier=4` for IDRs |
| Q6 | Long-term references (LTR) — Vulkan H.264 encode exposes them via `StdVideoEncodeH264ReferenceInfoFlags.used_for_long_term_reference`; Chromium's existing H264 LTR support is partial | Med | Initially **disable LTR**, parity with VA-API VEA which also doesn't emit LTR |
| Q7 | Android (Adreno) requires `VK_ANDROID_external_memory_android_hardware_buffer` for camera capture import. We assume Linux DMA-BUF as the default | Low | Add Android path in Phase 5 |
| Q8 | Driver may not support `VK_KHR_video_maintenance1` (inline queries). Fallback: explicit `vkCmdBeginQuery`/`vkCmdEndQuery` around `vkCmdEncodeVideoKHR` | Low | Both paths are implemented in the samples — port both |
| Q9 | The encoder samples' bitstream buffer is host-visible-coherent. For dGPU, device-local + staging-copy is faster | Med | Microbenchmark; tune in Phase 5 |
| Q10 | **Speculative**: A future `VK_KHR_video_encode_encryption` could mirror VA-API's `VAEncryptionParameters` for full encrypted-encode pipelines (matching protected decode). No proposal yet from Khronos. | Low (research) | Track Khronos working group; design protected-encode path to be drop-in |

---

## 14. References <a name="14-references"></a>

### 14.1 Vulkan Video Encode

| Resource | URL |
|---|---|
| Vulkan Spec (Chapter 49: Video Encode) | https://registry.khronos.org/vulkan/specs/latest/html/vkspec.html |
| `VK_KHR_video_encode_queue` | https://registry.khronos.org/vulkan/specs/latest/man/html/VK_KHR_video_encode_queue.html |
| `VK_KHR_video_encode_h264` | https://registry.khronos.org/vulkan/specs/latest/man/html/VK_KHR_video_encode_h264.html |
| `VK_KHR_video_encode_h265` | https://registry.khronos.org/vulkan/specs/latest/man/html/VK_KHR_video_encode_h265.html |
| `VK_KHR_video_encode_av1`  | https://registry.khronos.org/vulkan/specs/latest/man/html/VK_KHR_video_encode_av1.html |
| `VK_KHR_video_encode_quantization_map` | https://registry.khronos.org/vulkan/specs/latest/man/html/VK_KHR_video_encode_quantization_map.html |
| `VK_KHR_video_encode_intra_refresh`    | https://registry.khronos.org/vulkan/specs/latest/man/html/VK_KHR_video_encode_intra_refresh.html |
| Vulkan Video Samples (this design references) | https://github.com/nvpro-samples/vk_video_samples |
| Khronos Vulkan Video Tutorial | https://www.khronos.org/blog/vulkan-video-coming-through |

### 14.2 VA-API (Reference for Mapping)

| Resource | URL |
|---|---|
| VA-API Spec | https://github.com/intel/libva/wiki |
| `va_enc_h264.h` | https://github.com/intel/libva/blob/master/va/va_enc_h264.h |
| `va_enc_hevc.h` | https://github.com/intel/libva/blob/master/va/va_enc_hevc.h |
| Intel Media Driver Encode | https://github.com/intel/media-driver/tree/master/media_driver/agnostic/common/codec/hal/codechal_encoder_base.cpp |

### 14.3 Chromium Source (Verified Paths)

| Component | Path |
|---|---|
| Top-level VEA interface | `media/video/video_encode_accelerator.h` |
| `VideoEncoderInfo` (Review F10) | `media/video/video_encoder_info.h` |
| Encode factory | `media/gpu/gpu_video_encode_accelerator_factory.cc` |
| VA-API VEA | `media/gpu/vaapi/vaapi_video_encode_accelerator.{h,cc}` |
| **H.264** VA-API delegate (port source) | `media/gpu/vaapi/h264_vaapi_video_encoder_delegate.{h,cc}` |
| **AV1** VA-API delegate (port source) | `media/gpu/vaapi/av1_vaapi_video_encoder_delegate.{h,cc}` |
| **H.265 VA-API encode does NOT exist in tree** | n/a (Review F1) |
| **D3D12 VEA** — second reference for SharedImage / `VideoEncoderInfo` (Review F8) | `media/gpu/windows/d3d12_video_encode_accelerator.{h,cc}` |
| **D3D12 H.265 delegate (port source for HEVC)** | `media/gpu/windows/d3d12_video_encode_h265_delegate.{h,cc}` |
| D3D12 H.264 delegate | `media/gpu/windows/d3d12_video_encode_h264_delegate.{h,cc}` |
| D3D12 AV1 delegate | `media/gpu/windows/d3d12_video_encode_av1_delegate.{h,cc}` |
| D3D12 encoder wrapper | `media/gpu/windows/d3d12_video_encoder_wrapper.{h,cc}` |
| `CommandBufferHelper` / `SharedImageManager::ProduceVideo` pattern (Review F6) | `media/gpu/windows/d3d12_video_encode_accelerator.cc` (lines 140–250) |
| Mojo IPC | `media/mojo/mojom/video_encode_accelerator.mojom` |
| Reusable rate controller | `media/gpu/h264_ratectrl_rtc.{h,cc}`, `media/gpu/h264_rate_controller.{h,cc}` |
| Reusable bitstream builder | `media/gpu/h264_builder.{h,cc}`, `media/gpu/h265_builder.{h,cc}`, `media/gpu/av1_builder.{h,cc}`, `media/filters/h26x_annex_b_bitstream_builder.h` |
| Reusable DPB (decoder side) | `media/gpu/h264_dpb.{h,cc}` |

### 14.4 Companion Decode Design Document

See [`VULKAN_VIDEO_INTEGRATION_DESIGN_DOCUMENT.md`](VULKAN_VIDEO_INTEGRATION_DESIGN_DOCUMENT.md) — the companion document for **decode** integration, which this encode document mirrors structurally.

---

## Appendix A: SVG Diagrams

| Diagram | File |
|---|---|
| Current Chromium encode backend topology | [`diagrams/encode_chromium_topology.svg`](diagrams/encode_chromium_topology.svg) |
| VA-API encode sequence | [`diagrams/encode_vaapi_sequence.svg`](diagrams/encode_vaapi_sequence.svg) |
| Vulkan Video encode sequence | [`diagrams/encode_vulkan_sequence.svg`](diagrams/encode_vulkan_sequence.svg) |
| Class hierarchy (proposed) | [`diagrams/encode_vulkan_class_hierarchy.svg`](diagrams/encode_vulkan_class_hierarchy.svg) |
| Rate-control state machine | [`diagrams/encode_rate_control.svg`](diagrams/encode_rate_control.svg) |
| DMA-BUF → `VkImage` input path | [`diagrams/encode_input_path.svg`](diagrams/encode_input_path.svg) |
| Bitstream readback path | [`diagrams/encode_bitstream_readback.svg`](diagrams/encode_bitstream_readback.svg) |
| Cross-queue synchronization | [`diagrams/encode_sync_timeline.svg`](diagrams/encode_sync_timeline.svg) |
| Execution timeline (Gantt) | [`diagrams/encode_execution_timeline.svg`](diagrams/encode_execution_timeline.svg) |

---

*Document generated from Chromium source analysis at `/data/LinuxExtra/browsers/chrome/chromium/src/media/gpu/` and the Vulkan Video Samples reference implementation at `/data/nvidia/android-extra/video-apps/vulkan-video-samples/`.*
*Chromium Version: HEAD (as of May 2026)*
*Vulkan Headers Version: 1.3.296*
