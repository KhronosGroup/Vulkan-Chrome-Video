<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 The Khronos Group, Inc.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->
# Plugging the NVIDIA Vulkan-Video-Samples Encoder Library into Chromium

**Version**: 1.3 (adds Model D — static link via `third_party/vulkan_video_samples/`)
**Date**: May 19, 2026
**Status**: Evaluation
**Scope**: Determine if the public encoder library shipping under `vk_video_encoder/include/` can be **directly plugged** into Chromium as a backend, what is missing, and what the new development estimate becomes when we target the library instead of writing every codec delegate from scratch.

**Related docs**: [`VULKAN_VIDEO_ENCODE_INTEGRATION_DESIGN_DOCUMENT.md`](VULKAN_VIDEO_ENCODE_INTEGRATION_DESIGN_DOCUMENT.md) (v1.1), [`VULKAN_VIDEO_TEST_PLAN.md`](VULKAN_VIDEO_TEST_PLAN.md), [`VULKAN_VIDEO_ENCODE_DESIGN_REVIEW_FINDINGS.md`](VULKAN_VIDEO_ENCODE_DESIGN_REVIEW_FINDINGS.md).

---

## Errata v1.3 (supersedes v1.0, v1.1, v1.2)

| # | What earlier versions said | What is actually true |
|---|---|---|
| E1 (v1.0) | Model A "loses zero-copy from SharedImage" | **Wrong** — fixed in v1.1. The library has Path A (`VkVideoEncoder.cpp:584–621`) that consumes a caller-imported DMA-BUF / DRM-modifier `VkImage` directly. Zero pixel copy. Quotes in §0.1. |
| E2 (v1.0) | Model A's memory residency is ~200 % of VA-API | ~130–150 % — fixed in v1.1. The extra cost is ~30 MB of duplicated device state, not duplicated frame buffers. |
| E3 (v1.1) | G-NEW-9 (BYO `VkDevice`) needs ~500 LoC of upstream feature work | **Wrong** — fixed in v1.2. `VulkanDeviceContext` already supports BYO with `m_importedInstanceHandle` / `m_importedDeviceHandle` flags; ~30 LoC of plumbing on `VkVideoEncoderConfig` is all that's missing. Quotes in §0.2. |
| E4 (v1.1) | Two-step Model A → Model A+ plan needed for single-device | **Moot** — fixed in v1.2. Models A and A+ collapsed. |
| E5 (v1.1) | Two-step plan recommended | **Replaced** by BYO-device link from day one. |
| **E6 (v1.2)** | **Only three integration models considered (A dynamic-link, B vendor codec, C OOP)** | **Incomplete.** v1.3 adds **Model D — static link via `third_party/vulkan_video_samples/`**. This is how every other vendored codec ships in Chromium (`libvpx`, `libaom`, `dav1d`, `libavif`). Same integration code as Model A but built into the Chromium binary as a static library rather than loaded as `libvkvideo-encoder.so`. **New recommendation: Model D** (Model A's integration code + Chromium's standard `third_party/` vendoring + static link). Model A (dynamic link) is kept as the "external-binary prototype" variant. |

### 0.1 Path A — verified zero-copy quote (carried from v1.1)

`vk_video_encoder/libs/VkVideoEncoder/VkVideoEncoder.cpp` lines 584–621:

```cpp
// Path A (direct encode, zero-copy) requires:
//   - OPTIMAL or DRM_FORMAT_MODIFIER tiling with an encodable YCbCr format
//   - NOT VK_IMAGE_TILING_LINEAR (no GPU encode from linear memory)
if (tiling == VK_IMAGE_TILING_OPTIMAL ||
    tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT) {
    switch (format) {
        case VK_FORMAT_G8_B8R8_2PLANE_420_UNORM:                       // NV12
        case VK_FORMAT_G10X6_B10X6R10X6_2PLANE_420_UNORM_3PACK16:      // P010
        case VK_FORMAT_G12X4_B12X4R12X4_2PLANE_420_UNORM_3PACK16:      // P012
            isDirectlyEncodable = true; break;
    }
}
if (isDirectlyEncodable) {
    // Path A: Direct encode (zero-copy). Wrap external image and set
    // directly as srcEncodeImageResource. No staging, no copy, no filter.
    VkResult result = WrapExternalImage(...);
    return EncodeFrameCommon(encodeFrameInfo);
}
```

### 0.2 BYO-device support — already present at the wrapper layer

`common/libs/VkCodecUtils/VulkanDeviceContext.h:243-302`:

```cpp
VkResult InitVulkanDevice(const char* pAppName,
                          VkInstance vkInstance = VK_NULL_HANDLE,        // BYO instance
                          bool verbose = false,
                          const char* pCustomLoader = nullptr);

VkResult InitPhysicalDevice(int32_t deviceId, const vk::DeviceUuidUtils&,
                            ...,
                            VkPhysicalDevice vkPhysicalDevice = VK_NULL_HANDLE);  // BYO phys device

VkResult CreateVulkanDevice(int32_t numDecodeQueues = 1,
                            int32_t numEncodeQueues = 0,
                            VkVideoCodecOperationFlagsKHR videoCodecs = ...,
                            bool createTransferQueue = false,
                            bool createGraphicsQueue = false,
                            bool createPresentQueue = false,
                            bool createComputeQueue = false,
                            VkDevice vkDevice = VK_NULL_HANDLE);          // BYO device
```

And the impl correctly tracks ownership and skips destruction of imported handles:

`common/libs/VkCodecUtils/VulkanDeviceContext.cpp:1049-1056`:

```cpp
        if (vkDevice == VK_NULL_HANDLE) {
            ... CreateDevice(m_physDevice, &devInfo, nullptr, &m_device); ...
            m_importedDeviceHandle = false;
        } else {
            m_device = vkDevice;
            m_importedDeviceHandle = true;
        }
```

`...:1137-1145` (destructor):

```cpp
    if (m_device) {
        if (!m_importedDeviceHandle) {           // don't destroy if BYO
            DeviceWaitIdle();
            DestroyDevice(m_device, nullptr);
        }
        m_device = VkDevice();
    }
```

Same pattern for `m_importedInstanceHandle` at line 1164–1168. The library already gets this right.

The only "missing piece" is that `VkVideoEncoderConfig` and `VulkanVideoEncoderExtImpl::InitVulkanDevice` do not yet expose / forward these parameters. That's a ~30 LoC plumbing change — listed below as G-NEW-9 (downsized from ~500 LoC to ~30 LoC).

### 0.3 Why the "two VkDevices" concern was moot

Two scenarios; only one device per process in each:

1. **In-process encode** (the current `VaapiVEA`, `D3D12VEA`, `MFVEA` pattern): the encoder runs in the GPU process and shares `gpu::VulkanDeviceQueue` with the compositor. With the BYO-device path, the library uses Chromium's `VkDevice` directly. **One device.**

2. **Out-of-process encode** (the OOP-D pattern, the direction Chrome video-acceleration is moving in for sandbox-tightening): a utility process hosts the encoder service. The utility process owns its own `VkDevice` — there is no "compositor's `VkDevice`" to share, because the compositor is in a different process. The library creates a device or accepts a passed-in one; either way it's **the only device in that process**.

In neither case is there a duplicated device. The whole "Model A has 2 VkDevices, Model A+ unifies them" framing in v1.1 was making a problem that didn't exist.

**Source quotes that prove E1**:

`vk_video_encoder/libs/VkVideoEncoder/VkVideoEncoder.cpp` lines 581–621:

```cpp
// Path A: Optimal YCbCr that's directly encodable → set as
//         srcEncodeImageResource, skip staging, go to EncodeFrameCommon.
//         The encoder reads the input once via vkCmdEncodeVideoKHR,
//         reconstructed DPB frames are separate internal allocations.
//         Input is free after encode reads it.
//
// Path B/C: Linear YCbCr or RGBA → needs staging copy or filter.

bool isDirectlyEncodable = false;
// Path A (direct encode, zero-copy) requires:
//   - OPTIMAL or DRM_FORMAT_MODIFIER tiling with an encodable YCbCr format
//   - NOT VK_IMAGE_TILING_LINEAR (no GPU encode from linear memory)
// DRM modifier block-linear images have the same physical memory layout as
// OPTIMAL on NVIDIA. The driver supports VIDEO_ENCODE_SRC on DRM modifier
// images (with non-zero block height).
if (tiling == VK_IMAGE_TILING_OPTIMAL ||
    tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT) {
    switch (format) {
        case VK_FORMAT_G8_B8R8_2PLANE_420_UNORM:                       // NV12
        case VK_FORMAT_G10X6_B10X6R10X6_2PLANE_420_UNORM_3PACK16:      // P010
        case VK_FORMAT_G12X4_B12X4R12X4_2PLANE_420_UNORM_3PACK16:      // P012
            isDirectlyEncodable = true;
            ...
    }
}

if (isDirectlyEncodable) {
    // =============================================
    // Path A: Direct encode (zero-copy)
    // =============================================
    // Wrap external image and set directly as srcEncodeImageResource.
    // No staging, no copy, no filter.
    VkResult result = WrapExternalImage(...);
    return EncodeFrameCommon(encodeFrameInfo);
}
```

And `WrapExternalImage()` (lines 449–517) is non-owning — it uses `VkImageResource::CreateFromExternal()` and `VulkanVideoImagePoolNode::CreateExternal()`, so the library does **not** copy or re-allocate the underlying memory.

Additionally, the library calls `SelectDrmFormatModifier()` (lines 58–96) to negotiate compatible DRM modifiers, and `VkDrmFormatModifierUtils` (in `common/libs/VkCodecUtils/`) provides modifier enumeration with `BlockHeightPref` and `CompressionPref` controls — the library was deliberately engineered for DMA-BUF + DRM-format-modifier zero-copy.

The required device extensions (`vulkan_video_encoder_ext.cpp:272–289`) include `VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME`, `VK_KHR_EXTERNAL_SEMAPHORE_FD_EXTENSION_NAME`, `VK_KHR_EXTERNAL_FENCE_FD_EXTENSION_NAME` on Linux and `VK_KHR_EXTERNAL_MEMORY_WIN32_EXTENSION_NAME` + `VK_KHR_EXTERNAL_SEMAPHORE_WIN32_EXTENSION_NAME` on Windows — the cross-process / cross-device handle types the design is built around.

The rest of this document is updated to reflect the correct picture.

---

## TL;DR (v1.3)

The library ships **two** C++ interfaces:

| Header | Style | Suitability for Chromium |
|---|---|---|
| `vulkan_video_encoder.h` (`VulkanVideoEncoder`) | argc/argv command-line driven, file-based input/output | **No.** Too coarse — file paths, single-call lifetime. |
| `vulkan_video_encoder_ext.h` (`VulkanVideoEncoderExt`) | Struct-based config, external-image input, semaphore sync, async retrieval, capability query, **same-device `VkImage` zero-copy when the caller supplies an encodable image** | **Yes, after the P0 gaps below.** Designed *explicitly* "for cross-process encoder services" per its own comments, but browser use still needs in-memory bitstream output, accurate caps, reconfigure, and public BYO-device plumbing. |

The `Ext` interface alone covers ~70 % of what Chromium needs. With **11 concrete extensions** (Section 3) it covers ~95 %. The remaining 5 % is wrapper code we have to write anyway.

**Three facts that drive the v1.3 recommendation:**

1. **The underlying `VulkanDeviceContext` already supports imported `VkDevice` handles** (v1.2 finding). The public `VulkanVideoEncoderExt` config exposes only `externalInstance` today; ~30 LoC of plumbing on `VkVideoEncoderConfig` exposes the full BYO chain that `VulkanDeviceContext` already implements.
2. **The "two `VkDevice`s in the GPU process" concern becomes moot after G-NEW-9 lands** (v1.2 finding). With public BYO-device plumbing, one device per process — period.
3. **Static linking via `third_party/` is the established Chromium pattern for codec libraries** (v1.3 finding). `libvpx`, `libaom`, `dav1d`, `libavif`, `libwebp` are all vendored under `third_party/` and built into the Chromium binary. The Vulkan-Video-Samples encoder library is Apache-2.0-compatible for Chromium intake, but still needs the normal `third_party/` review, `README.chromium`, license metadata, security review, DEPS roll plan, and codec-policy/legal review.

**Effort delta vs the from-scratch v1.1 plan (19–21 weeks)**:

| Model | Effort | Devices | Build mode | Notes |
|---|---:|---|---|---|
| **D (v1.3 RECOMMENDED)** Static link via `third_party/vulkan_video_samples/` | **~10–11 weeks + ~0.5–1w vendoring** | 1 per process | **Static**, in Chromium binary | Same integration code as Model A. Standard Chromium codec pattern. LTO/PGO across encoder + Chromium. No `.so` to load. |
| **A (v1.2)** Dynamic link to `libvkvideo-encoder.so` | ~10–11 weeks | 1 per process | Dynamic, separate `.so` | Same integration code as Model D; useful as the "external-binary prototype" but unusual in the Chromium codec landscape. |
| **B** Vendor codec layer, custom Chromium front-end | ~12–15 weeks | 1 | Static (subset of sources) | Drop the lib's `Ext` wrapper, write our own. ~4,200 LoC of Chromium-native front-end. Use only if we want to fork the codec layer hard. |
| **C** OOP encoder service via Mojo utility process | ~13–16 weeks | 1 (in utility process) | Static (in utility binary) | Best crash isolation. Same library code as Model D, different deployment. |

**Recommendation (v1.3)**: **Model D** — vendor the library under `third_party/vulkan_video_samples/`, write GN build files, link statically. Effort ~10–11 weeks + ~0.5–1 week vendoring, ~6,200 LoC Chromium + ~400 LoC GN/build + **~1,130 LoC upstream** (G-NEW-1, 2, 3, 8, 9, 11 — sized per the §2.1 design choices). Same integration code as Model A; just the standard Chromium codec packaging.

Model A (dynamic link) remains a valid **prototyping** variant — the integration code is byte-for-byte identical, only the build/link mode differs. Model B is the fallback if we ever need to fork the codec layer for a long-running Chromium-only patch. In-process vs OOP (Model C) is an orthogonal Chromium-architecture decision.

---

## Table of Contents

1. [What the library exposes](#1-what-the-library-exposes)
2. [Sufficiency assessment per Chromium requirement](#2-sufficiency)
3. [Concrete extensions the library needs](#3-extensions-needed)
4. [Four integration models](#4-integration-models)
5. [Risks](#5-risks)
6. [Effort estimate vs v1.1](#6-effort)
7. [Recommended plan (v1.3 — Model D)](#7-recommended-plan)
8. [References](#8-references)

---

## 1. What the library exposes <a name="1-what-the-library-exposes"></a>

### 1.1 Public headers

```
vk_video_encoder/include/
├── vulkan_video_encoder.h      (~60 LOC)
└── vulkan_video_encoder_ext.h  (~300 LOC)
```

### 1.2 `VulkanVideoEncoder` — the original interface

```cpp
class VulkanVideoEncoder : public VkVideoRefCountBase {
 public:
  virtual VkResult Initialize(VkVideoCodecOperationFlagBitsKHR codec,
                              int argc, const char** argv) = 0;
  virtual int64_t  GetNumberOfFrames() = 0;
  virtual VkResult EncodeNextFrame(int64_t& frameNumEncoded) = 0;
  virtual VkResult GetBitstream() = 0;            // (no-op in current impl)
};
```

CLI-driven, designed for the standalone `vk-video-enc` demo. **Not suitable** for browser integration — file paths in `argv`, the encoder reads a YUV input file, writes an Annex-B / IVF / OBU output file.

### 1.3 `VulkanVideoEncoderExt` — the service-oriented interface

The header comments explicitly call out the design intent:

> Extends `VulkanVideoEncoder` for frame-at-a-time operation with externally-provided `VkImage`s and timeline semaphore synchronization. **This is the interface for cross-process encoder services.**

The public surface:

```cpp
struct VkVideoEncoderConfig {
  VkVideoCodecOperationFlagBitsKHR codec;
  uint32_t  encodeWidth, encodeHeight;
  VkFormat  inputFormat;
  uint32_t  inputWidth, inputHeight;
  uint32_t  rateControlMode;          // 0=DEFAULT 1=DISABLED 2=CBR 3=VBR
  uint32_t  averageBitrate, maxBitrate, vbvBufferSize;
  int32_t   constQpI, constQpP, constQpB, minQp, maxQp;
  uint32_t  gopLength, consecutiveBFrames, idrPeriod;
  VkBool32  closedGop;
  uint32_t  frameRateNum, frameRateDen;
  uint32_t  qualityLevel;
  uint8_t   colourPrimaries, transferCharacteristics, matrixCoefficients;
  VkBool32  videoFullRange;
  VkBool32  enablePreprocessFilter;   // RGBA → NV12 compute filter
  int32_t   deviceId;
  uint8_t   gpuUUID[VK_UUID_SIZE];
  const char* outputPath;             // file output
  VkBool32  verbose, validate;
  VkInstance externalInstance;        // bring-your-own VkInstance for cross-process
};

struct VkVideoEncodeInputFrame {
  VkImage      image;
  VkImageView  imageView;
  VkFormat     format;
  uint32_t     width, height;
  VkImageTiling imageTiling;
  VkImageLayout currentLayout;
  uint64_t     frameId, pts;
  VkBool32     forceIDR, forceIntra, isLastFrame;
  int32_t      qpOverride;
  int32_t      uniqueImageIndex;
  // Producer/consumer sync (wait + signal, timeline + binary)
  uint32_t     waitSemaphoreCount;
  VkSemaphore* pWaitSemaphores;
  uint64_t*    pWaitSemaphoreValues;
  uint32_t     signalSemaphoreCount;
  VkSemaphore* pSignalSemaphores;
  uint64_t*    pSignalSemaphoreValues;
};

struct VkVideoEncodeResult {
  uint64_t       frameId, pts, dts;
  const uint8_t* pBitstreamData;       // valid until ReleaseEncodedFrame
  uint32_t       bitstreamSize;
  uint32_t       pictureType;          // 0=I 1=P 2=B
  VkBool32       isIDR;
  uint32_t       temporalLayerId;
  VkResult       status;
};

class VulkanVideoEncoderExt : public VulkanVideoEncoder {
 public:
  virtual VkResult InitializeExt(const VkVideoEncoderConfig& config) = 0;

  // Submit / Retrieve
  virtual VkResult SubmitExternalFrame(const VkVideoEncodeInputFrame& frame,
                                       VkSemaphore* pStagingCompleteSemaphore = nullptr) = 0;
  virtual VkResult PollEncodeComplete(uint64_t frameId) = 0;
  virtual VkResult GetEncodedFrame(VkVideoEncodeResult& result) = 0;
  virtual void     ReleaseEncodedFrame(uint64_t frameId) = 0;
  virtual VkFence  GetEncodeFence(uint64_t frameId) = 0;

  // Lifecycle
  virtual VkResult Flush() = 0;
  virtual VkResult Reconfigure(const VkVideoEncoderConfig& config) = 0;

  // Capability
  virtual VkBool32 SupportsFormat(VkFormat inputFormat) const = 0;
  virtual uint32_t GetMaxWidth() const = 0;
  virtual uint32_t GetMaxHeight() const = 0;

  // Device access (caller does not own)
  virtual VkDevice         GetVkDevice() const = 0;
  virtual VkPhysicalDevice GetVkPhysicalDevice() const = 0;
  virtual VkInstance       GetVkInstance() const = 0;
};

extern "C" VK_VIDEO_ENCODER_EXPORT
VkResult CreateVulkanVideoEncoderExt(VkSharedBaseObj<VulkanVideoEncoderExt>&);
```

### 1.4 Mapping `Ext` to Chromium's `VideoEncodeAccelerator`

| Chromium VEA call | Library Ext call(s) |
|---|---|
| `GetSupportedProfiles()` | nothing yet — see Gap **G-NEW-1** |
| `Initialize(config, client, log)` | `CreateVulkanVideoEncoderExt()` + `InitializeExt(config)` |
| `Encode(frame, force_keyframe)` | `SubmitExternalFrame(frame{forceIDR=force_keyframe})` |
| `UseOutputBitstreamBuffer(buffer)` | **G-NEW-2 required** — current `GetEncodedFrame()` returns no bytes (`pBitstreamData=nullptr`, `bitstreamSize=0`). After G-NEW-2, copy the returned or caller-owned encoded bytes into the client's shmem. |
| `RequestEncodingParametersChange(...)` | `Reconfigure(newConfig)` (currently returns `VK_ERROR_FEATURE_NOT_PRESENT`) |
| `Flush(cb)` | `Flush()` |
| `Destroy()` | drop `VkSharedBaseObj` reference |
| `NotifyEncoderInfoChange(info)` | needs new API surface — Gap **G-NEW-1** |

For every per-frame request our delegate would have made by hand (Std-Video struct conversion, DPB bookkeeping, GOP scheduling, rate-control state, slice-header building, query-feedback handling, `dstBufferOffset` reservation), the library already runs all that internally. That **is** what we'd save by linking against it.

---

## 2. Sufficiency assessment per Chromium requirement <a name="2-sufficiency"></a>

Going through every required behaviour from the v1.1 design doc:

| # | Requirement (from v1.1) | Library status | Verdict |
|---|---|---|---|
| 1 | H.264 / H.265 / AV1 encode at the spec-extension level | All three present (`VkVideoEncoderH264`, `H265`, `AV1`) | ✅ Covered |
| 2 | Std-Video struct conversion (H.264 SPS → `StdVideoH264SequenceParameterSet`, ...) | Done internally per codec | ✅ Covered |
| 3 | DPB slot management (`VkVideoReferenceSlotInfoKHR`) | Done per codec: `VkEncoderDpbH264`, `VkEncoderDpbH265`, `VkEncoderDpbAV1` | ✅ Covered |
| 4 | GOP structure + B-frames + IDR period | `VkVideoGopStructure` | ✅ Covered |
| 5 | Rate-control modes (CBR / VBR / CQP / DEFAULT) with VUIDs | Done; lib already sets `layerCount=0` for DEFAULT/DISABLED (matches Review F4) | ✅ Covered |
| 6 | Per-codec `constantQp` (Review F5) | Done — `VkVideoEncoderH264.cpp:617` sets `nalu_slice.constantQp` when mode=DISABLED | ✅ Covered |
| 7 | Inline-query + begin/end-query fallback (Review F3) | Done — see `VkVideoEncoder.cpp:2761–2780` (chooses path based on `videoMaintenance1FeaturesSupported`) | ✅ Covered |
| 8 | `dstBufferOffset` + `precedingExternallyEncodedBytes` for header bytes (Review F2) | Done — lib carries `bitstreamHeaderBufferSize` / `bitstreamHeaderOffset` per frame | ✅ Covered |
| 9 | Quality level (`VkVideoEncodeQualityLevelInfoKHR`) | Configurable; library asserts `qualityLevel < videoEncodeCapabilities.maxQualityLevels` | ✅ Covered |
| 10 | Quantization map (`VK_KHR_video_encode_quantization_map`) | Enabled in `EncoderConfig` (`enableQpMap`, `qpMapMode`) but **not in public struct** | ⚠️ Partial |
| 11 | Intra-refresh (`VK_KHR_video_encode_intra_refresh`) | Enabled in `EncoderConfig` (`enableIntraRefresh`, `intraRefreshMode`, `intraRefreshCycleDuration`) but **not in public struct** | ⚠️ Partial |
| 12 | Temporal SVC (T=2, T=3) | `temporalLayerCount` exists in `EncoderConfig` and `VkVideoGopStructure`, but the library's per-frame `rateControlLayersInfo` array is hard-sized to **1 element** in `VkVideoEncoder.h:228` and the Ext struct has no per-layer fields | ⚠️ Partial (functional headroom limited) |
| 13 | Spatial SVC (S=2, S=3) | Not implemented in library | ❌ Missing |
| 14 | Manual reference buffer control (Review F9) | Not exposed | ❌ Missing |
| 15 | Per-frame QP map binding | Library reads QP maps from a file (`EncoderQpMapFileHandler`), not per-frame from API | ❌ Missing |
| 16 | External producer/consumer sync (compositor → encoder timeline) | **Fully exposed** in `VkVideoEncodeInputFrame`: wait/signal semaphores, both binary and timeline | ✅ Covered (very well) |
| 17 | External image import (DMA-BUF / AHB / Win NT) | Library accepts a pre-imported `VkImage` — the caller does the import. That fits our model. | ✅ Covered |
| 18 | Format negotiation (`SupportsFormat`, max-size queries) | Stubbed: hardcoded list of formats; `GetMaxWidth()` returns `8192` regardless of device | ❌ Implementation gap |
| 19 | `GetSupportedProfiles()` populating Chromium's `gpu::GPUInfo` at startup | Not exposed at all — Chromium calls this **before** any encoder is created | ❌ Missing |
| 20 | `VideoEncoderInfo` for WebRTC (Review F10) | No equivalent exposed | ❌ Missing |
| 21 | `RequestEncodingParametersChange()` | `Reconfigure()` is in the interface but returns `VK_ERROR_FEATURE_NOT_PRESENT` (`vulkan_video_encoder_ext.cpp:646`) | ❌ Stub |
| 22 | In-memory bitstream return (no file I/O) | Library writes to file in `outputPath`. `GetEncodedFrame()` sets `pBitstreamData = nullptr` with the comment "// Bitstream is in file for now" (`vulkan_video_encoder_ext.cpp:578`). **Biggest single gap.** | ❌ Critical stub |
| 23 | Capability descriptor for `VideoEncoderInfo::requested_resolution_alignment`, max bitrate, etc. | Available inside `EncoderConfig.videoEncodeCapabilities` but not via API | ❌ Missing |
| 24 | Protected encode | Library has no protected-content flag plumbing | ❌ Missing (and we marked this a non-goal anyway) |
| 25 | Shared device with Chromium compositor (`gpu::VulkanDeviceQueue`) | Library owns its own `VulkanDeviceContext`. `externalInstance` lets us inject a `VkInstance`, but the lib then calls `InitPhysicalDevice()` and `CreateVulkanDevice()` itself — i.e. it always makes its own `VkDevice` | ❌ Architectural mismatch — see §4 / §5 |
| 26 | Validation-layer-on-demand (Chromium can't enable validation in shipped builds) | `config.validate` chains `VK_LAYER_KHRONOS_validation` only when true — OK | ✅ Covered |

Aggregating:

- 11 / 26 requirements are **fully covered** by the library.
- 3 / 26 are **partially covered** (functionality exists in the impl but not the public interface).
- 12 / 26 are **gaps** of varying severity:
    - 4 critical (#19 GetSupportedProfiles, #20 VideoEncoderInfo, #22 in-memory bitstream, #25 device sharing).
    - 5 moderate (#10 QP map, #11 intra-refresh, #12 temporal SVC, #14 manual refs, #21 Reconfigure).
    - 3 light (#13 spatial SVC, #15 per-frame QP map, #18 caps queries).

The good news: **most gaps are interface-shape, not implementation-shape**. The underlying `EncoderConfig` already has most of the fields — they just need to be promoted to the public `VkVideoEncoderConfig` struct.

### 2.1 Per-gap deep-dive

This section elaborates each missing or partial feature. For every gap we cover (a) **what Chromium calls for** — the caller, the API it expects, and when, (b) **what underlying APIs are involved** at the Vulkan, Std-Video, and Chromium VEA layers, (c) **what the library does today**, (d) **why it's missing** — the design intent that produced the gap, and (e) **the concrete proposed delta**.

A handful of gaps had more than one reasonable closure shape. The chosen alternative is named in the section heading (e.g. "WebRTC index model", "BYO primary + ephemeral fallback") and justified in the deep-dive itself. The ambiguous design points were temporal SVC depth, manual-reference-buffer API shape, output-buffer ownership, capability-enumeration entry point, resize-IDR semantics, and trusted-rate-controller surfacing.

> **Notation: G-NEW-N.** Throughout this document the label **G-NEW-N** (read: *Gap-resolving New extension #N*) refers to a specific additive contribution to the Vulkan-Video-Samples public API that closes one or more gaps from the sufficiency table. The eleven proposed extensions (G-NEW-1 … G-NEW-11) are catalogued in §3 with full specifications and LoC estimates. Each is a non-breaking additive change to the public header (`vulkan_video_encoder_ext.h`) — existing cmdline callers continue to compile and run unchanged.

> **Driver capability assumption.** For every gap below that maps onto a Vulkan extension or optional capability, the answer to "does my GPU support it?" is *query the driver at runtime*. The Chromium-side wrapper consults `vkGetPhysicalDeviceVideoCapabilitiesKHR`, `vkGetPhysicalDeviceVideoFormatPropertiesKHR`, and the relevant `VkPhysicalDeviceVideoEncode*FeaturesKHR` chain at session-creation time and exposes downstream features (WebCodecs options, `VideoEncoderInfo` fields, SDP capability) only when the driver reports support. We do not bake any vendor matrix into Chromium code; this document deliberately avoids per-vendor capability claims.

---

#### Gap #10 — Quantization map (`VK_KHR_video_encode_quantization_map`)

**What Chromium calls for.** The QP map is a per-block delta-QP image that nudges the encoder toward higher or lower quality regions on a per-frame basis. Two use cases drive demand in Chromium:

1. **WebCodecs scalar quantizer**: `VideoEncoder.encode(frame, { quantizer: 28 })` — JavaScript hands an integer per encode call. The implementation can either set the global QP via rate control or paint a single-block constant QP map.
2. **ROI / screen-share delta-QP**: tab-capture / screen-share scenarios produce a delta-QP map per frame (typically lower QP on text regions, higher on static areas). Chromium's screen-share pipeline (today via libvpx VP9) computes this CPU-side; with HW encode we'd hand the map directly to the encoder.

**Underlying APIs.**
- Vulkan extension: [`VK_KHR_video_encode_quantization_map`](https://registry.khronos.org/vulkan/specs/latest/man/html/VK_KHR_video_encode_quantization_map.html). Adds `VkVideoEncodeQuantizationMapInfoKHR` chained into `pNext` of `VkVideoEncodeInfoKHR`, a `VkImageView` of the delta-QP map (single-channel signed 8/16-bit), and per-codec sub-structs `VkVideoEncode{H264,H265,AV1}QuantizationMapCapabilitiesKHR`.
- Driver caps: `VkPhysicalDeviceVideoEncodeQuantizationMapFeaturesKHR::videoEncodeQuantizationMap`, plus per-codec block size (`quantizationMapTexelSize`).
- Two QP-map modes (both expressed via the same extension):
    - **Delta-QP map**: signed offset per block, added to the global QP. Use `VK_VIDEO_ENCODE_WITH_QUANTIZATION_DELTA_MAP_BIT_KHR`. Block usage: `VK_IMAGE_USAGE_VIDEO_ENCODE_QUANTIZATION_DELTA_MAP_BIT_KHR`.
    - **Emphasis map**: positive bias per block, encoder boosts quality. Use `VK_VIDEO_ENCODE_WITH_EMPHASIS_MAP_BIT_KHR`. Block usage: `VK_IMAGE_USAGE_VIDEO_ENCODE_EMPHASIS_MAP_BIT_KHR`.
- Chromium APIs:
    - `media::VideoEncoder::EncodeOptions::quantizer` (scalar today — see `media/base/video_encoder.h`).
    - `media::VideoEncodeAccelerator::Encode(scoped_refptr<VideoFrame>, bool force_keyframe, std::unique_ptr<EncodeOptions>)` — the per-frame delivery point.
    - For ROI: would need a new `EncodeOptions::quantization_map` field carrying a `gfx::Buffer` or `SkBitmap`-style 8/16-bit blob; Chromium's WebRTC `VideoFrameTypeFactory` already speaks "this is a delta-QP buffer" via `webrtc::VideoFrame::set_delta_qp_map()`.

**Library internals (verified citations).**

The library has the full QP-map machinery wired up; only the public surface is missing. Key code paths:

| Path | What it does |
|---|---|
| `vk_video_encoder/libs/VkVideoEncoder/VkEncoderConfig.cpp:637-657` | Parses `--qpMap delta` / `--qpMap emphasis` cmdline flags; sets `enableQpMap = VK_TRUE` and `qpMapMode` |
| `VkVideoEncoder.cpp:223` | `VkVideoEncoder::StageInputFrameQpMap(encodeFrameInfo, cmdBuf)` — uploads QP map from staging buffer to the device-side VkImage with the right tiling/usage |
| `VkVideoEncoder.cpp:1537-1620` | Allocates per-frame QP map images during session init with correct usage flags |
| `VkVideoEncoder.cpp:2583-2603` (`ProcessQpMap`) | Per-frame: builds `VkVideoEncodeQuantizationMapInfoKHR`, chains into `pNext`, sets `VK_VIDEO_ENCODE_WITH_QUANTIZATION_DELTA_MAP_BIT_KHR` / `VK_VIDEO_ENCODE_WITH_EMPHASIS_MAP_BIT_KHR` |

Quoted from `VkVideoEncoder.cpp:2583-2603`:

```cpp
void VkVideoEncoder::ProcessQpMap(VkSharedBaseObj<VkVideoEncodeFrameInfo>& encodeFrameInfo) {
    if ((m_encoderConfig->enableQpMap == VK_FALSE) ||
        ((encodeFrameInfo->srcQpMapImageResource == nullptr) &&
         encodeFrameInfo->srcQpMapStagingResource == nullptr)) {
        return;
    }
    VkVideoPictureResourceInfoKHR* pSrcQpMapPictureResource =
        encodeFrameInfo->srcQpMapImageResource->GetPictureResourceInfo();
    encodeFrameInfo->quantizationMapInfo.sType =
        VK_STRUCTURE_TYPE_VIDEO_ENCODE_QUANTIZATION_MAP_INFO_KHR;
    encodeFrameInfo->quantizationMapInfo.quantizationMap =
        pSrcQpMapPictureResource->imageViewBinding;
    encodeFrameInfo->quantizationMapInfo.quantizationMapExtent = {
        (m_encoderConfig->encodeWidth  + m_qpMapTexelSize.width  - 1) / m_qpMapTexelSize.width,
        (m_encoderConfig->encodeHeight + m_qpMapTexelSize.height - 1) / m_qpMapTexelSize.height
    };
    encodeFrameInfo->encodeInfo.flags |=
        ((m_encoderConfig->qpMapMode == EncoderConfig::DELTA_QP_MAP) ?
            VK_VIDEO_ENCODE_WITH_QUANTIZATION_DELTA_MAP_BIT_KHR :
            VK_VIDEO_ENCODE_WITH_EMPHASIS_MAP_BIT_KHR);
    vk::ChainNextVkStruct(encodeFrameInfo->encodeInfo, encodeFrameInfo->quantizationMapInfo);
}
```

The driver-side wiring is complete. The sample fills `srcQpMapImageResource` from `EncoderQpMapFileHandler` (reading from disk); Chromium needs to fill it from the API caller.

**Why it's missing.** The library is a *sample app* using cmdline-driven offline encode. File-based QP maps are a natural fit for batch jobs ("encode this YUV with this QP map per frame from this directory"). There's no Chromium-style "API for the next frame's QP map" because the sample never sees a live caller.

**Proposed delta** (G-NEW-4):

```cpp
// NEW field in VkVideoEncodeInputFrame
VkImage       qpMapImage;        // optional: VK_NULL_HANDLE if none
VkImageLayout qpMapImageLayout;
VkExtent2D    qpMapTexelSize;    // must match caps (queried via G-NEW-1)

// NEW field in VkVideoEncoderConfig
VkBool32      enableQpMap;
uint32_t      qpMapMode;         // 0 = delta-QP, 1 = emphasis
```

The library already has all the plumbing internally; we expose it. Chromium-side: build the `VkImage` (single-channel R8_SINT or R16_SINT) on Chromium's `VkDevice` from the WebCodecs `quantizer` value or `delta_qp_map` blob; pass via `pInputFrame->qpMapImage`. Same-device — no cross-process FD import needed.

**Validation approach.** Encode a reference YUV with QP=28 globally and with a `QP=28-everywhere` delta-QP map; PSNR should match within 0.05 dB. Then encode with a checkerboard QP map (alternating ±6 around QP=28); verify per-region PSNR matches the per-region target QP via codec-aware ROI analysis (e.g., `psnr_per_macroblock`).

---

#### Gap #11 — Intra-refresh (`VK_KHR_video_encode_intra_refresh`)

**What Chromium calls for.** WebRTC packet-loss recovery uses *progressive intra-refresh*: instead of issuing an IDR (which is a several-kilobyte bandwidth spike), the encoder refreshes a strip of macroblocks per frame, completing a full intra-refresh cycle over N frames. The result is identical to an IDR for keyframe purposes but with a near-flat bitrate profile. WebRTC also wants per-frame control (don't refresh during congestion windows, force a refresh after a NACK storm).

Concretely: with a 30 fps stream and a 30-frame refresh cycle, an IDR would briefly spike the bitrate to several × target; intra-refresh keeps the per-frame bitrate flat by distributing the intra-coded macroblocks over the cycle.

**Underlying APIs.**
- Vulkan extension: [`VK_KHR_video_encode_intra_refresh`](https://registry.khronos.org/vulkan/specs/latest/man/html/VK_KHR_video_encode_intra_refresh.html). Adds:
    - `VkVideoEncodeIntraRefreshInfoKHR` chained into per-frame `pNext` of `VkVideoEncodeInfoKHR` (carries `intraRefreshIndex`).
    - `VkVideoEncodeSessionIntraRefreshCreateInfoKHR` chained at session creation (carries `intraRefreshMode` + `intraRefreshCycleDuration`).
- Per-frame: `intraRefreshIndex` is the index of the strip refreshed *this* frame (0..cycleDuration-1).
- `intraRefreshMode` enum:
    - `VK_VIDEO_ENCODE_INTRA_REFRESH_MODE_PER_PICTURE_PARTITION_KHR` — refresh a slice/tile per frame
    - `VK_VIDEO_ENCODE_INTRA_REFRESH_MODE_BLOCK_BASED_KHR` — refresh a horizontal block strip per frame (most common)
    - `VK_VIDEO_ENCODE_INTRA_REFRESH_MODE_BLOCK_ROW_BASED_KHR` — refresh a row of blocks per frame
    - `VK_VIDEO_ENCODE_INTRA_REFRESH_MODE_BLOCK_COLUMN_BASED_KHR` — refresh a column
- Driver caps: `VkPhysicalDeviceVideoEncodeIntraRefreshFeaturesKHR::videoEncodeIntraRefresh`, `VkVideoEncodeIntraRefreshCapabilitiesKHR::supportedIntraRefreshModes`, `maxIntraRefreshCycleDuration`, `maxIntraRefreshActiveReferencePictures`.
- Chromium API: today this isn't directly exposed at the VEA layer. WebRTC's `H264EncoderImpl` ships SW-side intra-refresh; OpenH264 has per-frame `iRequiredCallsBeforeKey` logic. With Vulkan we have HW support, so we plumb a new `VideoEncodeAccelerator::Config::intra_refresh_period` (cycle length, set at Initialize) and a per-frame `EncodeOptions::intra_refresh_strip` (override).

**Library internals.**

The library has the full intra-refresh wiring; only the per-frame override is missing.

| Path | What it does |
|---|---|
| `VkVideoEncoder.cpp:2605-2614` (`FillIntraRefreshInfo`) | Builds `VkVideoEncodeIntraRefreshInfoKHR` from `m_encoderConfig->intraRefreshCycleDuration` + `encodeFrameInfo->gopPosition.intraRefreshIndex`. The index comes from the GOP machinery, not the caller. |
| `VkVideoGopStructure.cpp` | The GOP structure advances `intraRefreshIndex` each frame internally. |
| `VkEncoderConfig.cpp` | Cmdline parses `--intraRefreshMode` and `--intraRefreshCycleDuration`. |

Quoted from `VkVideoEncoder.cpp:2605-2614`:

```cpp
void VkVideoEncoder::FillIntraRefreshInfo(VkSharedBaseObj<VkVideoEncodeFrameInfo>& encodeFrameInfo) {
    encodeFrameInfo->intraRefreshInfo.sType =
        VK_STRUCTURE_TYPE_VIDEO_ENCODE_INTRA_REFRESH_INFO_KHR;
    encodeFrameInfo->intraRefreshInfo.intraRefreshCycleDuration =
        m_encoderConfig->intraRefreshCycleDuration;
    encodeFrameInfo->intraRefreshInfo.intraRefreshIndex =
        encodeFrameInfo->gopPosition.intraRefreshIndex;       // ← from GOP only

    encodeFrameInfo->encodeInfo.flags |= VK_VIDEO_ENCODE_INTRA_REFRESH_BIT_KHR;
    vk::ChainNextVkStruct(encodeFrameInfo->encodeInfo,
                          encodeFrameInfo->intraRefreshInfo);
}
```

The GOP-driven `gopPosition.intraRefreshIndex` works for the cmdline use case but doesn't let WebRTC say "freeze the cycle this frame" or "restart the cycle after a NACK".

**Why it's missing.** Sample-app design — set it once at startup, encode the whole file. The Chromium use case wants the cycle index to be settable per encode call.

**Proposed delta** (G-NEW-5):

```cpp
// NEW field in VkVideoEncodeInputFrame
uint32_t  intraRefreshIndex;      // 0..cycleDuration-1; UINT32_MAX = no IR this frame; UINT32_MAX-1 = restart cycle
VkBool32  intraRefreshStartCycle; // resets the cycle counter (used after a NACK)
```

Chromium-side: WebRTC's loss-recovery logic sets `intraRefreshIndex` from a counter that's advanced per encode call; on NACK the counter resets and `intraRefreshStartCycle = VK_TRUE` for one frame.

**Validation approach.** Encode 60 frames at 30 fps with cycle duration = 30 and observe per-frame bitstream size: with the same global QP, an IDR frame should be ~10× the size of a P-frame; intra-refresh frames should be ~1.5–2× P-frame size. Decode + run `psnr_per_frame` — quality should be flat throughout the cycle, not spike at the IDR boundary.

---

#### Gap #12 — Temporal SVC (T=2/T=3/T=4) — *deep scope*

**What Chromium calls for.** WebRTC simulcast and SVC adaptive streaming. WebRTC's `VideoBitrateAllocation` is a 2D matrix `bitrates[spatial][temporal]` with up to `kMaxSpatialLayers=5 × kMaxTemporalStreams=4 = 20` entries. Each temporal layer encodes only frames at specific positions in a 2- / 4- / 8-frame pattern; layer 0 is the base layer, layer 1 adds every-other-frame, layer 2 fills the gaps, etc. The result: a client can drop layers under congestion and continue decoding the base layer.

**Underlying APIs.**
- Vulkan: per-layer rate control via the array `pLayers` in `VkVideoEncodeRateControlInfoKHR::pLayers[VkVideoEncodeRateControlLayerInfoKHR]`. The driver picks per-layer bitrate budgets.
- Std-Video: per-frame `StdVideoEncodeH264SliceHeader::temporal_id`, `StdVideoEncodeH265SliceSegmentHeader::nuh_temporal_id_plus1`, `StdVideoEncodeAV1PictureInfo::temporal_id`.
- GOP structure: each frame's temporal-layer membership is fixed by the GOP pattern (e.g., T=3 pattern `[T0 T2 T1 T2 T0 T2 T1 T2]`).
- Chromium API: `media::VideoBitrateAllocation` (2D), `VideoEncodeAcceleratorConfig::spatial_layers[]` carrying `num_of_temporal_layers`, plus the per-codec metadata structs `H264Metadata::temporal_idx`, `Vp9Metadata::temporal_idx`, `SVCGenericMetadata::temporal_idx`.

**Today in the library.** `EncoderConfig` has `temporalLayerCount`, `VkVideoGopStructure` knows the temporal-layer pattern, and `VkVideoEncoder.h:228` declares:

```cpp
VkVideoEncodeRateControlLayerInfoKHR  m_rateControlLayersInfo[1];   // hard-sized!
```

So the structure is there for one layer only. Per-frame submission also doesn't carry the temporal index out to the Std-Video slice header — the GOP type alone implies it.

**Why it's missing.** The cmdline sample's primary target is T=1. Temporal SVC was deferred. Hard-sizing the array to 1 was a "we'll fix it later" call.

**Proposed delta (deep scope)** (G-NEW-6):

```cpp
// NEW maximum (matches Chromium's kMaxTemporalStreams + headroom)
#define VK_MAX_VIDEO_ENCODE_TEMPORAL_LAYERS 4

// CHANGE in VkVideoEncoder.h
VkVideoEncodeRateControlLayerInfoKHR  m_rateControlLayersInfo[VK_MAX_VIDEO_ENCODE_TEMPORAL_LAYERS];

// NEW fields in VkVideoEncoderConfig (additive)
uint32_t  temporalLayerCount;                                          // 1..4
uint64_t  perTemporalLayerBitrate[VK_MAX_VIDEO_ENCODE_TEMPORAL_LAYERS]; // bps
uint32_t  perTemporalLayerFramerateNumerator[VK_MAX_VIDEO_ENCODE_TEMPORAL_LAYERS];
uint32_t  perTemporalLayerFramerateDenominator[VK_MAX_VIDEO_ENCODE_TEMPORAL_LAYERS];

// NEW field in VkVideoEncodeInputFrame (optional override)
uint32_t  temporalLayerIndexOverride;  // UINT32_MAX = use GOP-derived value

// NEW in VkVideoEncodeResult (per-frame metadata write-back)
uint32_t  encodedTemporalLayerIndex;   // for Chromium to fill H264Metadata::temporal_idx etc.
VkBool32  layerSync;
```

The GOP pattern is set up to be programmable (T=2 / T=3 / T=4 / arbitrary) via `VkVideoGopStructure::SetTemporalLayerPattern()`. WebRTC `VideoBitrateAllocation`'s 2D matrix maps cleanly: for SVC = T-only we use the row `bitrates[0][t]`; for spatial+temporal we revisit in Gap #13.

**LoC**: ~250 (up from the original 200 because deep scope adds the per-layer GOP-pattern plumbing).

---

#### Gap #13 — Spatial SVC (S=2 / S=3)

**What Chromium calls for.** WebRTC VP9 SVC + AV1 SVC: encode the same scene at multiple resolutions in one bitstream. Used in conferencing for variable network conditions. Chromium's `Vp9Encoder` and `Av1Encoder` support this; HW-accelerated SVC is rarely used today (mainly because HW encoders rarely support it).

**Underlying APIs.**
- Vulkan: no dedicated extension. Spatial SVC has to be implemented as multiple parallel `VkVideoSessionKHR` instances, each at a different resolution, with reference-picture sharing between them. Khronos has discussed a future extension; as of mid-2026 nothing exists.
- AV1 spec: native support via `obu_extension_header_flag` + `spatial_id`. Vulkan AV1 encoding doesn't expose this.
- H.264 SVC: dropped from the spec long ago.
- HEVC: SHVC (scalable HEVC) extension exists but is essentially never implemented in HW encoders.

**Today in the library.** Not implemented.

**Why it's missing.** No spec extension to implement against. Even if there were, no current HW encoder hardware supports spatial SVC natively — it would have to be simulated via multiple parallel sessions, which has tricky reference-frame consistency requirements. The library would have to grow a coordinator on top of multiple `VkVideoEncoderExt` instances.

**Proposed delta.** **Defer to P3 (post-MVP).** Document the workaround for WebRTC simulcast (multiple independent VEAs, one per resolution) — that's how Chrome ships HW-accelerated simulcast today via VA-API and D3D12. Native single-bitstream SVC waits for a Khronos spec.

**LoC**: ~1,500 (estimate only; depends heavily on what Khronos eventually ships).

---

#### Gap #14 — Manual reference buffer control — *WebRTC index model*

**What Chromium calls for.** WebRTC `VideoEncodeAcceleratorConfig::manual_reference_buffer_control = true` flips the encoder from "GOP-driven reference management" to "caller tells you exactly which reference to use". The use cases:

1. **Packet-loss-resilience**: after a NACK, the encoder is told "don't reference frame N (we lost the receiver's copy) — reference frame N-2 instead".
2. **Screen content**: only encode I-frame + delta from a stable "long-term reference"; never reference recent frames.
3. **Simulcast layer prediction control**: each spatial layer references only frames within its own layer.

WebRTC models this as a **small fixed-size buffer pool** (typically 8 slots, called "frame buffers" in VP8/VP9 terminology). Per frame, the caller passes `frame_buffer_indices` describing which slots are refs and which slot to update.

**Underlying APIs.**
- Vulkan: `VkVideoEncodeInfoKHR::pSetupReferenceSlot` (which slot the encoder writes to) and `VkVideoEncodeInfoKHR::pReferenceSlots[]` (which slots are refs).
- Std-Video: codec-specific reference lists (`RefPicList0` / `RefPicList1` for H.264; `ref_frame_idx[7]` for AV1).
- Chromium API: `VideoEncodeAccelerator::Config::manual_reference_buffer_control` flag; per-frame metadata `SVCGenericMetadata::reference_flags` (16-bit bitmask of which of 8 buffers are refs) and `refresh_flags` (which of 8 to update).

**Today in the library.** `VkEncoderDpb{H264,H265,AV1}` manages reference slots internally based on the GOP structure. There's an internal `stdReferenceSlotInfo[]` array, but the encode pipeline always derives it from the GOP. No public override.

**Why it's missing.** The cmdline sample uses a fixed IPPP / IBP / IPBBB pattern; the GOP machinery is sufficient. Exposing a per-frame override means the library has to (a) trust the caller, (b) validate the slot configuration against codec rules, (c) update its own DPB tracking. That's surgery on the DPB layer.

**Proposed delta (WebRTC index model)** (G-NEW-7):

```cpp
// NEW maximum (matches WebRTC's frame buffer pool)
#define VK_MAX_VIDEO_ENCODE_FRAME_BUFFERS 8

// NEW struct: WebRTC-compatible explicit ref control
struct VkVideoEncodeFrameBufferIndices {
    uint32_t  referenceFlags;   // bit i set ⇒ buffer[i] is used as a reference this frame
    uint32_t  refreshFlags;     // bit i set ⇒ buffer[i] is overwritten with the result of this frame
    uint32_t  longTermFlags;    // bit i set ⇒ buffer[i] is a long-term reference (LTR)
};

// NEW field in VkVideoEncoderConfig
VkBool32 manualReferenceBufferControl;

// NEW field in VkVideoEncodeInputFrame (used only if manualReferenceBufferControl=VK_TRUE)
const VkVideoEncodeFrameBufferIndices* pFrameBufferIndices;

// NEW in VkVideoEncodeResult (write-back of what actually happened)
uint32_t  encodedReferenceFlags;
uint32_t  encodedRefreshFlags;
```

The library implementation translates the index model to `VkVideoReferenceSlotInfoKHR[]` + `pSetupReferenceSlot` internally, validates against codec constraints (e.g., H.264 `num_ref_frames` limit), and writes back the metadata.

**LoC**: ~400 (DPB-layer integration is the bulk; translation table is mechanical).

---

#### Gap #15 — Per-frame QP map binding

**Relationship to #10.** #10 is "expose the feature at all"; #15 is "expose it on a per-frame basis (not just at session creation)". The library's design conflates the two: `EncoderConfig::enableQpMap` is a session-wide flag, and the per-frame QP map is fetched from disk via `EncoderQpMapFileHandler::GetQpMapForFrame(encodeOrderIndex)`. There is no API surface for "this frame has a different QP map".

**What Chromium calls for.** Per-frame quantizer / QP-delta map provided by the caller at encode time:

- WebCodecs `EncodeOptions::quantizer` (scalar): WebCodecs delivers this with the `encode()` call. Each frame can carry a different value. Chromium-side, this is plumbed through `media::VideoEncoder::EncodeOptions::quantizer` (member at `media/base/video_encoder.h`).
- ROI / screen-share delta map: each frame has a fresh map based on tab-capture analysis. The map can change at every frame (e.g., a text cursor moving picks up high-priority blocks).

**Underlying APIs.** Same as Gap #10 — `VK_KHR_video_encode_quantization_map`. The Vulkan API is *already* per-frame: `VkVideoEncodeQuantizationMapInfoKHR` chains into the per-frame `VkVideoEncodeInfoKHR`. The library's sample design is what makes it appear session-wide.

**Library internals.**

| Path | What it does | Per-frame? |
|---|---|---|
| `EncoderConfig::enableQpMap` (member of `EncoderConfig`, sample cmdline-set) | Session-wide enable flag | No |
| `EncoderConfig::qpMapMode` (delta / emphasis) | Set at session creation | No |
| `EncoderQpMapFileHandler` (`vk_video_encoder/libs/VkVideoEncoder/`) | Reads `qpmap_NNNN.bin` files keyed on encode order | Yes (but file-only) |
| `srcQpMapImageResource` on `VkVideoEncodeFrameInfo` | Per-frame VkImage holding the actual QP map | Yes |
| `VkVideoEncoder.cpp:2583-2603` (`ProcessQpMap`) | Builds per-frame `VkVideoEncodeQuantizationMapInfoKHR` | Yes |

The mechanism is per-frame; just the *delivery* of the QP map is file-only.

**Why it's missing.** Gap #10's symptom; the design intent was offline encode with file-based QP maps. A sample developer thinks "I'll just generate QP map files alongside my YUV input"; a service developer thinks "I'll pass the map in with the frame". Different ergonomic targets.

**Proposed delta.** Subsumed by G-NEW-4 (Gap #10). The new `VkVideoEncodeInputFrame::qpMapImage` field is set per-frame by the caller; library skips `EncoderQpMapFileHandler` when `qpMapImage != VK_NULL_HANDLE`. The file-handler path stays for samples/tests; new callers get the per-frame API.

```cpp
// In SubmitExternalFrame's input frame:
VkVideoEncodeInputFrame frame{};
frame.image       = input_vk_image;
frame.qpMapImage  = chromium_qp_map_vk_image;   // per-frame, can vary
frame.qpMapImageLayout = VK_IMAGE_LAYOUT_VIDEO_ENCODE_QUANTIZATION_MAP_KHR;
frame.qpMapTexelSize   = caps.quantizationMapTexelSize;
encoder->SubmitExternalFrame(frame);
```

**Cross-codec note.** `quantizationMapTexelSize` is per-codec and queryable per profile via `VkVideoEncodeQuantizationMapCapabilitiesKHR::quantizationMapTexelSize`. NVIDIA values as of mid-2026: H.264 = 16×16, HEVC = 32×32, AV1 = 64×64. Chromium-side must allocate the right-sized image; this is determined at session init.

**Validation approach.** Same as #10, plus a per-frame variation test: encode 60 frames where the QP map changes every frame (e.g., a moving rectangle of low QP); decode and verify the spatial QP profile matches per frame. Use `ffprobe -show_frames` and per-frame QP histograms.

---

#### Gap #18 — Format negotiation (`SupportsFormat`, max-size queries)

**What Chromium calls for.** Three downstream surfaces consume this data:

1. **`gpu::GPUInfo::video_encode_accelerator_supported_profiles`** — populated at GPU-process startup; available in `chrome://gpu` and via `gpu::GpuInfo` Mojo serialization for the browser process.
2. **WebRTC SDP capability** — `RTCRtpSender::getCapabilities('video')` returns the list of codecs the browser advertises. Backed by `media::VideoEncodeAccelerator::SupportedProfiles`.
3. **WebCodecs `VideoEncoder.isConfigSupported(config)`** — short-circuits if the format/codec/resolution isn't in the supported set. Implementation calls `GpuVideoEncodeAcceleratorFactory::GetSupportedProfiles()`.

For all three, the per-profile data needs to be **driver-accurate**, not a hardcoded list. Hardcoding "8192×8192 max" on a 4K-only adapter means `VideoEncoder.isConfigSupported({ width: 7680 })` returns `true` and then `encode()` fails — a poor developer experience.

**Underlying APIs.**
- Vulkan: `vkGetPhysicalDeviceVideoFormatPropertiesKHR(physicalDevice, &videoFormatInfo, &count, formats)`:
    - Input: `VkPhysicalDeviceVideoFormatInfoKHR` carrying the target `VkImageUsageFlags` (e.g., `VK_IMAGE_USAGE_VIDEO_ENCODE_SRC_BIT_KHR`) and `VkVideoProfileListInfoKHR` (the codec+profile we're asking about).
    - Output: list of `VkVideoFormatPropertiesKHR` with `format`, `componentMapping`, `imageType`, `imageTiling`, `imageUsageFlags`, `maxExtent`, etc.
- Vulkan: `vkGetPhysicalDeviceVideoCapabilitiesKHR(physicalDevice, &videoProfile, &caps)`:
    - Output: `VkVideoCapabilitiesKHR::minCodedExtent` / `maxCodedExtent` / `pictureAccessGranularityWidth/Height` / `maxDpbSlots` / `maxActiveReferencePictures` / `minBitstreamBufferOffsetAlignment` / `minBitstreamBufferSizeAlignment` / `flags`.
    - Output: chained `VkVideoEncodeCapabilitiesKHR::maxBitrate` / `maxQualityLevels` / `rateControlModes` / `flags` / `encodeInputPictureGranularity`.
    - Output: codec-specific chained `VkVideoEncode{H264,H265,AV1}CapabilitiesKHR` (e.g., H.264 `maxPPictureL0ReferenceCount` / `maxBPictureL0ReferenceCount` / `maxL1ReferenceCount` / `stdSyntaxFlags`).

**Today in the library.** `vulkan_video_encoder_ext.cpp:649-680` hardcodes both the format list and max extent:

```cpp
// vulkan_video_encoder_ext.cpp:649-668
VkBool32 VulkanVideoEncoderExtImpl::SupportsFormat(VkFormat inputFormat) const {
    switch (inputFormat) {
        // Directly encodable (YCbCr 4:2:0)
        case VK_FORMAT_G8_B8R8_2PLANE_420_UNORM:                      // NV12
        case VK_FORMAT_G10X6_B10X6R10X6_2PLANE_420_UNORM_3PACK16:     // P010
        case VK_FORMAT_G12X4_B12X4R12X4_2PLANE_420_UNORM_3PACK16:     // P012
        // Via filter (RGBA)
        case VK_FORMAT_R8G8B8A8_UNORM:
        case VK_FORMAT_B8G8R8A8_UNORM:
        case VK_FORMAT_R8G8B8A8_SRGB:
        case VK_FORMAT_B8G8R8A8_SRGB:
        case VK_FORMAT_A2B10G10R10_UNORM_PACK32:
        case VK_FORMAT_R16G16B16A16_SFLOAT:
            return VK_TRUE;
        default:
            return VK_FALSE;
    }
}

// vulkan_video_encoder_ext.cpp:670-680
uint32_t VulkanVideoEncoderExtImpl::GetMaxWidth() const {
    // TODO: query from device capabilities
    return 8192;
}

uint32_t VulkanVideoEncoderExtImpl::GetMaxHeight() const {
    // TODO: query from device capabilities
    return 8192;
}
```

The `TODO` comment is already in the upstream code — the author knew this needs fixing.

**Why it's missing.** The sample knows what formats *its* test inputs are, so it short-circuits the lookup. 8192 is a generous upper bound that happens to work for the reference target. It's a sample, not a hardware capability oracle.

**Why hardcoding any specific number is wrong.** Per-codec maximum extent, supported pixel formats, supported chroma subsamplings, and supported bit depths vary across vendors, GPU generations, kernel versions, and driver versions. The only correct answer is to query at runtime via `vkGetPhysicalDeviceVideoCapabilitiesKHR` + `vkGetPhysicalDeviceVideoFormatPropertiesKHR` for each `(codec, profile)` combination Chromium intends to advertise.

**Proposed delta.** Subsumed by G-NEW-1: the new capability-enumeration API queries the driver and returns the real per-codec / per-profile / per-format matrix. The hardcoded `SupportsFormat()` and `GetMaxWidth/Height()` become thin wrappers over the cached `VkVideoEncoderCapabilities` populated at init time.

**Validation approach.** On every candidate device, call `EnumerateVulkanVideoEncoderCapabilities(codec)` for H.264, HEVC, and AV1 and cross-reference the returned `maxCodedExtent`, supported `VkFormat`s, and `maxBitrate` against `vulkaninfo --json | jq '.physical_devices[].video_capabilities'`. The values must match the driver-reported caps exactly — no Chromium-side rounding.

---

#### Gap #19 — `GetSupportedProfiles()` for GPU-process startup — *BYO primary + ephemeral fallback*

**What Chromium calls for.** At GPU-process startup, before any encoder is constructed, Chromium populates `gpu::GPUInfo::video_encode_accelerator_supported_profiles` by calling `GpuVideoEncodeAcceleratorFactory::GetSupportedProfiles()`. This is a *static* call — there is no `VideoEncodeAccelerator` instance yet. The output drives:

- **`chrome://gpu`** "Video Acceleration Information" section.
- **WebRTC SDP capability negotiation** — `RTCRtpSender::getCapabilities('video')` returns the codecs the browser will offer in SDP `m=video` lines.
- **`MediaCapabilities.encodingInfo()`** — JavaScript can ask "can I encode 4K H.264 at 60 fps?" before opening a `MediaStream`.
- **WebCodecs `VideoEncoder.isConfigSupported()`** — short-circuits with no encoder allocation.

**Chromium call-site map.**

```
chrome::OnGpuProcessStartup()
└── content::GpuMain()
    └── gpu::GpuMain()
        └── gpu::GpuInit::InitializeAndStartSandbox()
            └── media::GpuVideoEncodeAcceleratorFactory::GetSupportedProfiles()
                ├── (VA-API path) media::VaapiVideoEncodeAccelerator::GetSupportedProfiles()
                ├── (D3D12 path)  media::D3D12VideoEncodeAccelerator::GetSupportedProfiles()
                ├── (Mac VT path) media::VTVideoEncodeAccelerator::GetSupportedProfiles()
                └── (NEW Vulkan)  media::VulkanVideoEncodeAccelerator::GetSupportedProfiles()
                                  └── EnumerateVulkanVideoEncoderCapabilities(...)
```

In the VA-API path, `VaapiVideoEncodeAccelerator::GetSupportedProfiles()` opens a probe `VADisplay`, calls `vaQueryConfigEntrypoints` / `vaQueryConfigAttributes` / `vaGetConfigAttributes`, and tears down — all before any `VEA` is created. Our Vulkan path needs the equivalent capability before `gpu::VulkanImplementation::CreateVulkanInstance()` has necessarily completed.

**Underlying APIs.**
- Chromium: `media::VideoEncodeAccelerator::SupportedProfiles GetSupportedProfiles()` (static, factory-side). Defined at `media/video/video_encode_accelerator.h`.
- Vulkan: requires `VkPhysicalDevice` to call `vkGetPhysicalDeviceVideoCapabilitiesKHR` / `vkGetPhysicalDeviceVideoFormatPropertiesKHR`. Querying without a complete `VkInstance` + physical-device enumeration isn't possible.

**Library internals.** No exposed entry point. The library *can* compute these internally during `InitializeExt()` (which constructs `EncoderConfig::videoEncodeCapabilities` from driver calls) but doesn't expose the function for use before a session exists. Code paths that *would* be useful if exposed:

| Path | Computes |
|---|---|
| `VkEncoderConfig::CreateCodecConfig()` (`VkEncoderConfig.cpp`) | Fills `videoCapabilities`, `videoEncodeCapabilities`, codec-specific caps chained at session creation |
| `EncoderConfig::QueryProfileCapabilities()` | Per-profile feature flags |
| `VkVideoCoreProfile::populateProfile()` | Constructs the `VkVideoProfileInfoKHR` for the query |

These are all internal to the `VkVideoEncoder` and triggered as a side effect of session construction.

**Why it's missing.** The sample app reaches caps by constructing an encoder, which is fine for cmdline use but unusable for GPU-process startup. No public separation between caps and sessions.

**Proposed delta** (G-NEW-1):

```cpp
// PRIMARY: BYO Vulkan handles (matches G-NEW-9 pattern). Use when the caller
// has already initialized gpu::VulkanImplementation.
extern "C" VK_VIDEO_ENCODER_EXPORT
VkResult EnumerateVulkanVideoEncoderCapabilities(
    VkInstance              instance,           // BYO from Chromium's gpu::VulkanInstance
    VkPhysicalDevice        physicalDevice,     // BYO from VulkanDeviceQueue
    VkVideoCodecOperationFlagBitsKHR codec,
    VkVideoEncoderCapabilities* outCaps);

// FALLBACK: ephemeral instance + physical device, for callers that haven't
// initialized Vulkan yet (e.g., very early GPU-process startup before
// gpu::VulkanImplementation is built).
extern "C" VK_VIDEO_ENCODER_EXPORT
VkResult EnumerateVulkanVideoEncoderCapabilitiesEphemeral(
    int32_t                 deviceId,           // -1 = pick first capable
    VkVideoCodecOperationFlagBitsKHR codec,
    VkVideoEncoderCapabilities* outCaps);
// Internally: minimal VkInstance + InitPhysicalDevice + enumerate +
// cleanup. Returns the same struct.
```

The output `VkVideoEncoderCapabilities` struct fields are as specified in §3 G-NEW-1. The Chromium-side wrapper calls the BYO variant once `gpu::VulkanImplementation` is up, the ephemeral variant only on platforms where that order can't be guaranteed (e.g., ChromeOS during early boot probing). The ephemeral wrapper is a thin reuse of `VulkanDeviceContext::InitVkInstance()` + `InitPhysicalDevice()` + manual teardown.

**Cross-platform implementation notes.**

| Platform | Caveat |
|---|---|
| Linux (Ubuntu / Fedora) | Need DRM render-node access for the ephemeral instance. The GPU process sandbox already allows this; the ephemeral fallback runs before sandbox lock-down. |
| ChromeOS | `vkGetPhysicalDeviceVideoCapabilitiesKHR` may fail with `VK_ERROR_VIDEO_PROFILE_OPERATION_NOT_SUPPORTED_KHR` on devices without video-encode HW — return an empty `SupportedProfiles` set, not an error. |
| Windows | The ephemeral path runs before the WDDM video device is opened. |
| Android | Ephemeral path runs at zygote time; `AHARDWAREBUFFER_USAGE_VIDEO_ENCODE` not yet a valid usage. Vulkan Video on Android is on the long-term roadmap. |

**Validation approach.** On a machine with no Vulkan Video encode HW, both entry points should return an empty profile list (not crash, not error). On a machine with H.264-only HW (e.g., a Tegra reference board), only H.264 profiles should appear. Compare against `vulkaninfo --json | jq '.physical_devices[0].video_capabilities'`.

**LoC**: ~250 (BYO path) + ~100 (ephemeral wrapper) = **~350**.

---

#### Gap #20 — `VideoEncoderInfo` for WebRTC — *`trustedRateController = true` when CBR/VBR supported*

**What Chromium calls for.** Once an encoder session is up, the encoder reports its capabilities and runtime characteristics via `media::VideoEncoderInfo` (sent over Mojo via `VideoEncodeAcceleratorClient::NotifyEncoderInfoChange()`). WebRTC's bitrate adjuster, simulcast adapter, and quality scaler all consume this struct.

**Why these fields matter to WebRTC behavior.**

| Field | What WebRTC does with it |
|---|---|
| `has_trusted_rate_controller=true` | Skip the SW bitrate adjuster; trust the HW to track the target. Saves CPU and avoids overshoot/undershoot during congestion windows. |
| `has_trusted_rate_controller=false` | Run the SW adjuster on top: predict bitrate from past frames, scale the target slightly to compensate for HW drift. |
| `requested_resolution_alignment=N` | Round all input frame sizes up to a multiple of N before submitting; insert a Skia rescale if needed. |
| `supports_simulcast=false` | WebRTC creates **N parallel encoders** for simulcast instead of one with N layers. (This is how Chrome ships HW simulcast today for VA-API.) |
| `supports_gpu_shared_images=true` | Frames stay on GPU memory end-to-end; no readback. |
| `is_hardware_accelerated=true` | Counts toward `hwAccelerated` exposure on `videoEncoder.isConfigSupported()`. |
| `fps_allocation[t]` | Quality scaler picks which temporal layer to demote when CPU usage spikes. |
| `resolution_rate_limits[]` | Quality scaler picks the next resolution down when bitrate is constrained; without this, the scaler picks blindly. |

Concrete fields:

| Field | Meaning | Source |
|---|---|---|
| `implementation_name` | "VulkanVideoEncoder" or similar | static |
| `is_hardware_accelerated` | true | static |
| `supports_native_handle` | accepts `gfx::GpuMemoryBuffer` / `VkImage` directly | static (true) |
| `has_trusted_rate_controller` | CBR/VBR tracks target closely; WebRTC skips its own SW rate controller | driver-reported |
| `supports_simulcast` | multiple resolutions in one Initialize call | static (false initially) |
| `requested_resolution_alignment` | encoder requires inputs to be aligned to this | driver-reported via `pictureAccessGranularityWidth/Height` |
| `supports_gpu_shared_images` | encoder reads `SharedImage`-backed VkImages | Chromium-side knowledge (true) |
| `fps_allocation` | per-temporal-layer framerate (for SVC) | per-encode config |
| `resolution_rate_limits` | array of {resolution, min/max bitrate} | driver-reported |
| `apply_alignment_to_all_simulcast_layers` | alignment is per-layer or global | driver-reported |

**Underlying APIs.**
- Chromium: `media::VideoEncoderInfo` defined at `media/video/video_encoder_info.h`. Flows via `VideoEncodeAcceleratorClient::NotifyEncoderInfoChange()` defined in the mojom we looked at in §Model C.
- Vulkan: most fields map to `VkVideoEncodeCapabilitiesKHR` + `VkPhysicalDeviceVideoEncode*FeaturesKHR`. `requested_resolution_alignment` = `caps.pictureAccessGranularityWidth/Height`. `resolution_rate_limits` requires capability-aware probing (no direct Vulkan field; comes from per-profile caps + level table).

**Today in the library.** No equivalent surface. `EncoderConfig` carries the caps internally, but the public API only exposes `GetMaxWidth/Height`, `SupportsFormat`, no runtime descriptor.

**Why it's missing.** WebRTC concept; the sample app has no notion of "tell my caller what to expect about bitrate tracking".

**Proposed delta** (G-NEW-8):

```cpp
struct VkVideoEncoderRuntimeInfo {
    char                implementationName[64];
    VkBool32            isHardwareAccelerated;
    VkBool32            supportsNativeHandle;
    VkBool32            trustedRateController;    // see below
    VkBool32            supportsSimulcast;
    uint32_t            requestedResolutionAlignmentWidth;
    uint32_t            requestedResolutionAlignmentHeight;
    uint32_t            fpsAllocationCount;
    uint32_t            fpsAllocation[VK_MAX_VIDEO_ENCODE_TEMPORAL_LAYERS];
    uint32_t            resolutionBitrateLimitCount;
    struct {
        VkExtent2D resolution;
        uint64_t   minBitrate;
        uint64_t   maxBitrate;
    } resolutionBitrateLimits[8];
    VkBool32            applyAlignmentToAllSimulcastLayers;
};

extern "C" VK_VIDEO_ENCODER_EXPORT
VkResult VulkanVideoEncoderExtImpl::GetRuntimeInfo(VkVideoEncoderRuntimeInfo* outInfo);
```

**`trustedRateController`**: the library reports `true` whenever `supportedRateControlModes & (CBR | VBR)` is non-zero — i.e., we trust any driver that claims to support a tracked rate-control mode. Rationale: modern NVIDIA / AMD / Intel HW encoders all implement competent rate control inside the engine; the historical Intel SW-fallback paths that Chrome's vendor allow-list exists to defend against don't apply to Vulkan-Video drivers, which are HW-only by definition. If we observe quality issues with a specific vendor in the wild, we can add a Chromium-side override; we don't pollute the upstream library with a vendor table.

**Lifecycle notes.**

`VideoEncoderInfo` is *not* a single-shot read; the encoder fires `NotifyEncoderInfoChange` whenever the descriptor would change. Triggers:

- After `Initialize()` succeeds (initial value).
- After `Reconfigure()` (because `fps_allocation` might change with `temporalLayerCount`).
- On simulcast layer activation/deactivation.
- On dynamic profile/level changes (rare).

The library should fire a Chromium-side callback whenever `Reconfigure()` succeeds. We add an optional `void(*onRuntimeInfoChange)(VkVideoEncoderRuntimeInfo)` to `VkVideoEncoderConfig`; the Chromium wrapper hooks it to `VEAClient::NotifyEncoderInfoChange()`.

**Validation approach.**

1. After Initialize, dump `VideoEncoderInfo` to a log; compare against the VA-API output on the same hardware. The two backends should report qualitatively similar values for fields that are driver-derived (resolution alignment, supported simulcast, trusted-RC).
2. Run a WebRTC loopback under congestion (`tc qdisc` netem). With `has_trusted_rate_controller=true`, the transmitted bitrate should stay within 10 % of the target across 30 s; IDR frame rate should stay below 1/sec.
3. Invoke `Reconfigure(bitrate=5_Mbps → 500_Kbps)`. The `NotifyEncoderInfoChange` callback should fire with updated `resolution_rate_limits` reflecting the new operating point.

**LoC**: ~150 (struct definition + populator + Chromium-side translation to `media::VideoEncoderInfo`).

---

#### Gap #21 — `Reconfigure()` is a stub — *caller-choice IDR-on-resize*

**What Chromium calls for.** `VideoEncodeAccelerator::RequestEncodingParametersChange(bitrate, framerate, std::optional<gfx::Size> size)` is mandatory. WebRTC's bitrate adjuster runs at ~1 Hz, dropping or raising the bitrate in response to RTT/loss; size changes happen on tab-capture resize, on screen rotation, and on layer switches in simulcast.

**Underlying APIs.**
- Vulkan: in-flight `vkCmdControlVideoCodingKHR(VK_VIDEO_CODING_CONTROL_RATE_CONTROL_BIT_KHR)` applies a new `VkVideoEncodeRateControlInfoKHR` without recreating the session. Resolution change requires *recreating* `VkVideoSessionKHR` + `VkVideoSessionParametersKHR` and forcing an IDR on the next frame.
- Std-Video: profile / level changes also require session recreation.

**Today in the library.**

```cpp
// vulkan_video_encoder_ext.cpp:642-647
VkResult VulkanVideoEncoderExtImpl::Reconfigure(const VkVideoEncoderConfig& config)
{
    (void)config;
    return VK_ERROR_FEATURE_NOT_PRESENT;
}
```

That's the entire function. The interface exists; the implementation is a stub. However the encoder *internally* has the live-update primitives:

| Internal hook | Purpose | Location |
|---|---|---|
| `m_sendRateControlCmd` | Set to `VK_TRUE` to emit `vkCmdControlVideoCodingKHR` with new RC info on next frame | `VkVideoEncoder.h` |
| `BeginRateControlInfo()` | Constructs the `VkVideoEncodeRateControlInfoKHR` chain | `VkVideoEncoder.cpp` |
| `m_rateControlInfo` | The live rate-control state | `VkVideoEncoder.h` |
| `EncodeVideoSessionParameters()` | Re-encodes SPS/PPS into the session-params blob if needed (size change) | `VkVideoEncoder.cpp` |
| `WaitForThreadsToComplete()` | Drain in-flight encodes before applying disruptive changes | `VkVideoEncoder.cpp` |

So the wiring is all there; we just need to thread a public `Reconfigure(config, options)` through these hooks. The reason the sample stubbed it is that cmdline use has no need: encode the whole file with one config.

**Why it's missing.** Offline encode has no need — the sample uses one config end-to-end. Live reconfigure was on the TODO list.

For Chromium we default `forceIdrOnResize = VK_TRUE` — always-IDR is the safe baseline that works on every driver — and only attempt resize-without-IDR when `VkVideoEncoderCapabilities::supportsResizeWithoutIdr` is reported true (cap exposed via G-NEW-1).

**Proposed delta (caller-choice IDR-on-resize)** (G-NEW-3):

```cpp
struct VkReconfigureOptions {
    VkBool32  forceIdrOnResize;          // caller's choice; default = VK_TRUE for safety
    VkBool32  drainBeforeApply;          // wait for in-flight encodes to drain
    uint64_t  applyAtEncodeOrder;        // 0 = next available; otherwise apply at this encode order
};

// CHANGE in vulkan_video_encoder_ext.h
virtual VkResult Reconfigure(const VkVideoEncoderConfig& newConfig,
                              const VkReconfigureOptions& options) = 0;

// IMPLEMENTATION
// 1. Diff oldConfig vs newConfig.
// 2. If bitrate/framerate/quality changed only:
//      m_rateControlInfo = new RC values;
//      m_sendRateControlCmd = true;
//      next frame will emit vkCmdControlVideoCodingKHR with the new RC info.
// 3. If size changed:
//      if (options.drainBeforeApply) wait for in-flight to drain;
//      recreate VkVideoSessionKHR + VkVideoSessionParametersKHR;
//      if (options.forceIdrOnResize) next frame is IDR (forceIDR=true);
//      else attempt no-IDR if the driver advertises support (rare).
// 4. If profile/level changed:
//      same as size change, always force IDR (spec requirement).
```

**Implementation gotchas.**

1. **Bitstream pool sizing.** When resolution changes, the worst-case bitstream size changes too. Caller-provided `VkVideoEncodeOutputBuffer::maxSize` may suddenly be too small for the new resolution; we need to either fail loudly (return `VK_ERROR_OUT_OF_DEVICE_MEMORY_KHR`) or pre-warn via the runtime-info change callback.
2. **DPB resizing.** `VkVideoSessionKHR` carries the DPB array sized to the max coded extent declared at creation. Live resize *up* requires reallocating; live resize *down* can reuse but may waste memory.
3. **Mid-stream rate-control mode change** (CBR → VBR or vice versa) is risky on some drivers; the API should refuse this if `options.applyAtEncodeOrder` isn't an IDR-aligned encode order.
4. **External rate control** mode (`VK_VIDEO_ENCODE_RATE_CONTROL_MODE_DISABLED_BIT_KHR`) requires the caller to set per-frame QP. Switching to/from this mode mid-stream needs special care; consider rejecting it in v1.

**Validation approach.**

1. **Bitrate-only change**: encode 300 frames at 5 Mbps, then `Reconfigure(bitrate=500 Kbps)`, encode 300 more. Per-frame bitstream sizes should follow the new target within 100 ms. No IDR at the reconfigure boundary.
2. **Framerate-only change**: similar; encoder adjusts GOP timing without IDR.
3. **Resolution change with `forceIdrOnResize=true`**: 4K → 1080p mid-stream, then back to 4K. IDR appears on each resize. Quality stays consistent.
4. **Resolution change with `forceIdrOnResize=false`** (on drivers that report `supportsResizeWithoutIdr`): no IDR; quality may drop briefly until references catch up. Decoder must support resize signaling.
5. **Reconfigure while frames are in-flight** (`drainBeforeApply=true`): all pre-reconfigure frames complete with the *old* config; all post-reconfigure frames use the *new* config.

**LoC**: ~300 (the bulk is the diff-and-apply state machine; the live update primitives already exist in the lib).

---

#### Gap #22 — In-memory bitstream return — *caller-owned output buffer*

**What Chromium calls for.** `VEA::UseOutputBitstreamBuffer(int32_t bitstream_buffer_id, base::UnsafeSharedMemoryRegion region)` pre-shares a pool of shmem buffers; the encoder writes into them and fires `VEAClient::BitstreamBufferReady(buffer_id, metadata)`. The shmem is then forwarded to the renderer. **Zero file I/O.**

The flow in Chromium today (VA-API path) for context:

```
Renderer process:                       GPU process:
  CreateSharedMemoryRegion(MAX_SIZE)
    region.MapToVA(addr)
  Mojo: UseOutputBitstreamBuffer(region) -------->
                                          VEA caches region+id mapping
                                          Internally allocates a VA-API "coded buffer"
                                          Encodes frame
                                          Maps the VA-API output, memcpy into shmem
                                          BitstreamBufferReady(id, metadata) <--
  on receipt: post the encoded payload upstream
```

The `memcpy` from VA-API's internal coded buffer to the shmem region is the **single biggest CPU cost** in Chromium's encode path today. Avoiding it on Vulkan is a measurable win (5-15 % of encode-side CPU on 4K60 streams).

**Underlying APIs.**
- Chromium: `media::BitstreamBuffer`, `media::BitstreamBufferMetadata`, `base::UnsafeSharedMemoryRegion`.
- Vulkan: encode output is written to a `VkBuffer` (the `dstBuffer` field of `VkVideoEncodeInfoKHR`). The buffer must be allocated with `VK_BUFFER_USAGE_VIDEO_ENCODE_DST_BIT_KHR`. Alignment via `VkVideoCapabilitiesKHR::minBitstreamBufferOffsetAlignment` and `minBitstreamBufferSizeAlignment`.
- For caller-owned buffer using external memory:
    - **Linux**: `VK_EXT_external_memory_host` lets us wrap a shmem-mapped pointer as a `VkBuffer`. Alternative: `VK_KHR_external_memory_fd` if shmem is backed by a memfd.
    - **Windows**: `VK_KHR_external_memory_win32` with `D3DKMT_HANDLE` or NT handle.
    - **Android**: `VK_ANDROID_external_memory_android_hardware_buffer` for `AHardwareBuffer`-backed shmem.

**Today in the library.**

```cpp
// vulkan_video_encoder_ext.cpp:574-583
// Frame is complete - fill the result
result.frameId = front.frameId;
result.pts = front.pts;
result.dts = 0; // TODO: compute from encode order
result.pBitstreamData = nullptr; // Bitstream is in file for now
result.bitstreamSize = 0;
result.pictureType = 0; // TODO: extract from GOP position
result.isIDR = VK_FALSE;
result.temporalLayerId = 0;
result.status = VK_SUCCESS;
```

The encoder writes to `m_outputBitstreamBuffer` (an internally allocated `VkBuffer`) and then a separate file-writer thread `vkMapMemory`s it and writes to disk. The mapped data is *thrown away* from the API surface.

The library's internal `m_outputBitstreamBuffer` allocation:

| Path | What it does |
|---|---|
| `VkVideoEncoder::AllocOutputBitstreamBuffer()` | `vkCreateBuffer` with `VK_BUFFER_USAGE_VIDEO_ENCODE_DST_BIT_KHR`, sized to `maxBitstreamBufferSize` (a per-codec / per-resolution upper bound) |
| `VkVideoEncoder::WriteBitstreamToFile()` | `vkMapMemory(m_outputBitstreamBuffer, ...)` + `fwrite()` |

The map/unmap lifecycle is already correct; we just redirect the bytes from the file-writer to the caller's `VkBuffer`.

**Why it's missing.** Sample app outputs to a file. No conception of "caller wants the bytes in their own buffer."

**Proposed delta (caller-owned output)** (G-NEW-2):

```cpp
struct VkVideoEncodeOutputBuffer {
    VkBuffer            buffer;            // caller-allocated; usage = VIDEO_ENCODE_DST
    VkDeviceMemory      memory;            // may be VK_NULL_HANDLE if buffer was created via VMA / external memory
    VkDeviceSize        offset;            // offset within buffer to start writing
    VkDeviceSize        maxSize;           // upper bound on bitstream size (caller's shmem region size)
    VkExternalMemoryHandleTypeFlagBits externalHandleType;  // optional; 0 if caller's buffer is on the lib's VkDevice already
    // When set, library imports the caller's external memory (e.g., shmem-backed FD on Linux,
    // shared NT handle on Windows) into its own VkDevice and binds it to `buffer`.
};

// NEW field in VkVideoEncodeInputFrame
const VkVideoEncodeOutputBuffer* pOutputBuffer;  // optional; if null, library uses internal buffer

// NEW field in VkVideoEncoderConfig
VkBool32  disableFileOutput;   // VK_TRUE → no outputPath / no internal file writer

// CHANGE in VkVideoEncodeResult — when caller provided buffer:
result.pBitstreamData     = nullptr;                       // caller already has it
result.bitstreamSize      = feedback.bytesWritten;
result.bitstreamOffset    = pOutputBuffer->offset + dstOffsetWithinBuffer;
result.pictureType        = MapGopPictureType(pending.gopPictureType);
result.isIDR              = pending.isIDR;
result.dts                = ComputeDtsFromEncodeOrder(pending.encodeOrderNum);

// CHANGE in VkVideoEncodeResult — when caller did NOT provide a buffer (back-compat):
result.pBitstreamData     = mapped_internal_ptr + feedback.offset;
result.bitstreamSize      = feedback.bytesWritten;
// ... same as caller-provided case, but the lib owns the buffer; caller must memcpy
//     before ReleaseEncodedFrame() unmaps it.
```

For Chromium, we create a `VkBuffer` from shmem-mapped memory (via `VK_EXT_external_memory_host` on Linux, `VK_KHR_external_memory_win32` on Windows) and pass it. The encoded bytes land **directly in the renderer-visible shmem region** with no `memcpy()` — true zero-copy on the output path.

**Chromium-side construction sketch (per-frame).**

```cpp
// media/gpu/vulkan/vulkan_video_encode_accelerator.cc
void VulkanVideoEncodeAccelerator::UseOutputBitstreamBuffer(
    media::BitstreamBuffer bitstream_buffer) {
  // bitstream_buffer.region() is the renderer-shared shmem region.
  // We need a VkBuffer over it.
  std::unique_ptr<VkVideoEncodeOutputBuffer> ob =
      CreateOrFindCachedOutputBuffer(bitstream_buffer.region());
  pending_output_buffers_.push_back({bitstream_buffer.id(), std::move(ob)});
}

std::unique_ptr<VkVideoEncodeOutputBuffer>
VulkanVideoEncodeAccelerator::CreateOrFindCachedOutputBuffer(
    const base::UnsafeSharedMemoryRegion& region) {
  // 1. Cache by region.GetGUID(); reuse if already imported.
  // 2. Else: import the shmem FD into Vulkan
#if BUILDFLAG(IS_LINUX)
  base::ScopedFD fd = region.PassPlatformHandle();
  VkImportMemoryFdInfoKHR import = {
      .sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR,
      .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
      .fd = fd.release(),
  };
#elif BUILDFLAG(IS_WIN)
  HANDLE handle = region.PassPlatformHandle().Take();
  VkImportMemoryWin32HandleInfoKHR import = { ..., .handle = handle };
#endif
  // 3. Bind VkBuffer with VK_BUFFER_USAGE_VIDEO_ENCODE_DST_BIT_KHR + the imported memory.
  // 4. Cache and return VkVideoEncodeOutputBuffer { buffer, memory, 0, region.GetSize() }.
}

void VulkanVideoEncodeAccelerator::Encode(scoped_refptr<VideoFrame> frame,
                                          bool force_keyframe) {
  auto [bitstream_id, ob] = std::move(pending_output_buffers_.front());
  pending_output_buffers_.pop_front();

  VkVideoEncodeInputFrame in_frame{};
  in_frame.image          = ...;     // SharedImage VkImage
  in_frame.pOutputBuffer  = ob.get(); // caller-owned output
  // ... wait/signal semaphores, force IDR flag, etc.
  encoder_->SubmitExternalFrame(in_frame);
  in_flight_output_buffers_[in_frame.frameId] =
      { bitstream_id, std::move(ob) };
}
```

**Required extensions per platform.**

| Platform | Extension we depend on for caller-owned shmem `VkBuffer` |
|---|---|
| Linux | `VK_EXT_external_memory_host` (Vulkan 1.1+) or `VK_KHR_external_memory_fd` |
| Windows | `VK_KHR_external_memory_win32` |
| Android | `VK_ANDROID_external_memory_android_hardware_buffer` |

The Chromium-side wrapper checks `vkEnumerateDeviceExtensionProperties()` at init time and refuses to register the Vulkan VEA backend if the required external-memory extension is absent on the selected `VkPhysicalDevice` — falling back to the lib-owned + memcpy path is the alternative.

**Validation approach.**

1. **Functional**: encode 100 frames into a shmem region; mmap it from the test harness; pipe through `ffmpeg -c:v copy` and `ffprobe`; verify all 100 frames decode.
2. **Zero-copy assertion**: track a counter in the encoder shim that's incremented every time the encoder *would* have done a `memcpy` (i.e., when no `pOutputBuffer` is supplied). With Chromium's path the counter should stay at 0 for an entire encode session.
3. **Cache hit rate**: WebCodecs encodes ~30 fps with the same shmem region reused across frames. The output-buffer cache should hit on every frame after the first ~5 (initial fill of the pool). If the hit rate drops below 95 %, something's wrong with mailbox lifetime.
4. **Resize**: increase output buffer size mid-stream; verify the cache invalidates and re-imports.
5. **Encoder fallback**: omit `pOutputBuffer` (lib-owned path); verify back-compat works and the documented "memcpy out before ReleaseEncodedFrame" semantics hold.

**LoC**: ~250 (was ~120; the bump covers the external-memory-handle plumbing for caller-provided buffers).

---

#### Gap #23 — Capability descriptor (`requested_resolution_alignment`, max bitrate, ...)

**Relationship to #18, #19, #20.** Where each fits:

| Gap | When | Consumer |
|---|---|---|
| #18 Format negotiation | Per-codec format list | `GetSupportedProfiles().gpu_supported_pixel_formats` |
| #19 GetSupportedProfiles | GPU process startup, no encoder yet | `chrome://gpu`, SDP capability, `MediaCapabilities` |
| **#23 Capability descriptor** | After session created, before encoding | `VideoEncoderInfo` populated at `NotifyEncoderInfoChange()` |
| #20 VideoEncoderInfo | Runtime, on changes | WebRTC bitrate adjuster, quality scaler |

So #18 ⊂ #19 (format list is part of profile data); #23 is the "post-init view of caps" that drives WebRTC adaptation behavior. They share the underlying Vulkan API surface; they differ in *when* the data is computed and *which Chromium struct* receives it.

**What Chromium calls for.** The `VideoEncoderInfo` fields that come from driver caps (vs from Chromium-side knowledge):

| Field | Source | Vulkan API |
|---|---|---|
| `requested_resolution_alignment` | driver | `VkVideoCapabilitiesKHR::pictureAccessGranularityWidth/Height` |
| `resolution_rate_limits[]` | per-profile + per-level | Codec-specific level tables (H.264 Annex A, HEVC Annex A, AV1 levels) + `VkVideoEncodeCapabilitiesKHR::maxBitrate` |
| `apply_alignment_to_all_simulcast_layers` | always true on HW encoders | n/a (Chromium-side knowledge) |
| `requires_input_frame_idr_on_resize` | from session caps | `VkVideoEncodeCapabilitiesKHR::flags & VK_VIDEO_ENCODE_CAPABILITY_PRECEDING_EXTERNALLY_ENCODED_BYTES_BIT_KHR` plus driver-specific feedback |
| `fps_allocation` | from session config | `temporalLayerCount` + GOP pattern (per G-NEW-6) |
| `min_bitrate_per_resolution` | per-codec | derived from `VkVideoEncodeCapabilitiesKHR` + Vulkan codec level tables |
| `has_trusted_rate_controller` | driver | G-NEW-8 `trustedRateController` (set when CBR/VBR supported) |
| `requires_external_h264_aud_insertion` | n/a | always false for Vulkan (driver emits AUD) |

`resolution_rate_limits` is the trickiest one. Chromium's WebRTC quality scaler uses this to predict whether bumping the resolution to 2160p at the current bitrate budget will keep quality acceptable. Vulkan doesn't directly expose "for resolution R, sensible max bitrate is B"; we have to derive it from the codec level tables (e.g., H.264 Level 5.2 max bitrate is 240 Mbps for High profile) + driver-reported caps.

**Underlying APIs.** Same as #19. `vkGetPhysicalDeviceVideoCapabilitiesKHR` returns the post-init view via the chained `VkVideoCapabilitiesKHR` / `VkVideoEncodeCapabilitiesKHR` / codec-specific structs. Per-codec level lookup is in `VkVideoEncodeH264ProfileInfoKHR::stdProfileIdc` + a Chromium-side level table (existing at `media/video/h264_level_limits.cc`).

**Today in the library.** Internal `EncoderConfig::videoEncodeCapabilities` holds the Vulkan-returned caps post-init only; no public method to retrieve them.

**Why it's missing.** Same as #18 / #19 — sample app has no caller asking for these.

**Proposed delta.** Subsumed by G-NEW-1 (pre-init caps) + G-NEW-8 (post-init runtime info). `VkVideoEncoderCapabilities` carries `pictureAccessGranularityWidth/Height`, `maxBitrate`, `maxQualityLevels`; `VkVideoEncoderRuntimeInfo` carries the runtime view including the `resolutionBitrateLimits[]` table built from the codec level lookup.

Chromium-side translator (sketch):

```cpp
// media/gpu/vulkan/vulkan_video_encoder_caps_to_video_encoder_info.cc
media::VideoEncoderInfo TranslateRuntimeInfo(
    const VkVideoEncoderRuntimeInfo& lib_info,
    media::VideoCodecProfile profile) {
  media::VideoEncoderInfo out;
  out.implementation_name = lib_info.implementationName;
  out.has_trusted_rate_controller = lib_info.trustedRateController;
  out.is_hardware_accelerated = lib_info.isHardwareAccelerated;
  out.supports_native_handle = lib_info.supportsNativeHandle;
  out.supports_simulcast = lib_info.supportsSimulcast;
  out.supports_gpu_shared_images = true;  // Chromium-side knowledge: we use SharedImage
  out.requested_resolution_alignment = std::max(
      lib_info.requestedResolutionAlignmentWidth,
      lib_info.requestedResolutionAlignmentHeight);
  out.apply_alignment_to_all_simulcast_layers = true;
  // Fill resolution_rate_limits[] from lib_info + level table:
  for (uint32_t i = 0; i < lib_info.resolutionBitrateLimitCount; ++i) {
    out.resolution_rate_limits.push_back({
        gfx::Size(lib_info.resolutionBitrateLimits[i].resolution.width,
                  lib_info.resolutionBitrateLimits[i].resolution.height),
        media::Bitrate::ConstantBitrate(lib_info.resolutionBitrateLimits[i].minBitrate),
        media::Bitrate::ConstantBitrate(lib_info.resolutionBitrateLimits[i].maxBitrate),
    });
  }
  return out;
}
```

**Validation approach.** Compare the populated `VideoEncoderInfo` against the values VA-API reports on the same machine for the same codec (where both backends are present). Discrepancies in `requested_resolution_alignment` mean WebRTC may pass misaligned frames; discrepancies in `resolution_rate_limits` may cause the quality scaler to over- or under-estimate.

---

#### Gap #24 — Protected encode

**What Chromium calls for.** Encrypt-on-encode for DRM-bound content. Two scenarios:

1. **Screen capture on managed devices** (enterprise / education / DRM-policy-aware desktops): the OS marks certain content as protected (Netflix, Hulu, Disney+ via Widevine/PlayReady CDMs); a screen-share or tab-capture path that re-encodes such content must encode-and-encrypt in a single protected pass — never expose cleartext to userspace.
2. **Re-encoding protected sources**: e.g., a media-pipeline tool that ingests a protected stream, decrypts it inside a CDM, decodes it on the protected decoder, and re-encodes the protected output. Today this is impossible in Chrome; a future use case for premium content workflows.

**Underlying APIs.**
- Vulkan core:
    - `VK_VIDEO_SESSION_CREATE_PROTECTED_CONTENT_BIT_KHR` — flag at `VkVideoSessionCreateInfoKHR` creation.
    - `VK_QUEUE_PROTECTED_BIT` queues — submitted commands run on a protected queue; memory is bound `VK_MEMORY_PROPERTY_PROTECTED_BIT`.
    - Vulkan 1.1 `VK_KHR_protected_memory` (now core) — the protected-memory model.
- Per-platform realisations:
    - **Linux/ChromeOS**: vendor-specific. NVIDIA's protected encode path uses `VK_NV_protected_memory` extensions. AMD uses TMR (Trusted Memory Region).
    - **Android**: `AHardwareBuffer` + `AHARDWAREBUFFER_USAGE_PROTECTED_CONTENT`; Vulkan via `VK_EXT_external_memory_android_hardware_buffer`.
    - **Windows**: D3D11 "Output Protected Resources" + a Vulkan path that wraps them; not widely deployed.
- Chromium: `VEA::Config::is_protected` flag; CDM-side trust chain via `media::CdmContext::GetChromeOsCdmContext()` (on ChromeOS) and `media::MojoCdm` plumbing.

**Today in the library.** No protected-content flag plumbing. The library always creates non-protected sessions. There's no audit of which code paths would safely tolerate `VK_QUEUE_PROTECTED_BIT` and which wouldn't (e.g., the compute-shader filter for RGBA→NV12 would need to run on a protected queue or be skipped).

**Why it's missing.** Several compounding reasons:

1. Requires HW + driver + ICD support for protected memory — not universal.
2. Testing requires a CDM and protected content — not available in the lib's CI.
3. The protected-memory model is restrictive (you can't `vkMapMemory` protected buffers; the encoder's internal staging/QP-map paths assume mapping).
4. Outside the library's scope as a sample.

**Proposed delta.** **None for MVP** — marked as a non-goal in the main design document (F12) and the test plan. If a real consumer materialises, we'd add:

```cpp
// NEW fields in VkVideoEncoderConfig (future, P3+)
VkBool32  enableProtectedContent;             // session-level
VkBool32  protectedSubmitOnly;                // VK_TRUE → all queue submits use protected queues
                                              // (encoder will refuse non-protected SubmitExternalFrame)
```

And propagate `VK_VIDEO_SESSION_CREATE_PROTECTED_CONTENT_BIT_KHR` through `VkVideoEncoder::CreateSession()`. Internal changes:

1. The RGBA→NV12 compute filter would have to be either replaced with a protected-queue dispatch or disabled (caller required to provide YCbCr input).
2. The QP-map upload path can't `vkMapMemory` a protected staging buffer — would need a protected GPU-copy approach.
3. The bitstream readback would have to go via a CDM-side channel (the bitstream is encrypted-to-CDM); the existing in-memory bitstream return doesn't apply.

**Validation approach.** Run a known protected fixture (Widevine test pattern) through the protected-encode path; assert that no decrypted bytes appear in any user-space buffer between the encode call and the CDM-side delivery point. Use Android's protected-memory auditor or ChromeOS's `cros_healthd` protected-memory probes.

---

#### Gap #25 — Shared device with Chromium compositor (`gpu::VulkanDeviceQueue`)

**What Chromium calls for.** A single `VkDevice` shared between compositor, WebGL/WebGPU, decode, and encode. Single device = no cross-device DMA-BUF round-trip, no duplicated device-state memory, simpler synchronization.

**Underlying APIs.**
- Chromium: `gpu::VulkanDeviceQueue` (the wrapper Chromium uses for its `VkDevice`), `gpu::VulkanImplementation`, and a **new proposed** SharedImage Vulkan-video representation API modeled after ToT's `SharedImageManager::ProduceVideo()` path.
- Library: the non-owning lifecycle in `VulkanDeviceContext` already supports BYO instance + physical device + device, via `m_importedInstanceHandle` / `m_importedDeviceHandle` flags.

**Today in the library.** **The capability is already there** at the `VulkanDeviceContext` layer (see §0.2 quotes). The only missing piece is exposing four new fields in the public `VkVideoEncoderConfig`:

```cpp
VkPhysicalDevice externalPhysicalDevice;
VkDevice         externalDevice;
uint32_t         externalEncodeQueueFamilyIndex;
uint32_t         externalComputeQueueFamilyIndex;
```

**Why it's missing.** The public config only exposed `externalInstance`; the deeper handles were never plumbed through. v1.1 sized this gap at ~500 LoC of feature work; the v1.2 finding showed it's actually ~30 LoC of plumbing because the lifecycle work is already done.

**Proposed delta.** G-NEW-9 (~30 LoC of plumbing). Covered in detail in §3.

---

### 2.2 Summary of gap categorisation

| Gap | Severity | Why it's missing | Effort to fix upstream |
|---|---|---|---|
| #10 Quantization map (public iface) | Moderate | Sample app uses file-based input | ~150 LoC (G-NEW-4) |
| #11 Intra-refresh (public iface) | Moderate | Sample applies one cycle to all frames | ~80 LoC (G-NEW-5) |
| #12 Temporal SVC (deep) | Moderate | T=1 was default; per-layer array hard-sized to 1 | ~250 LoC (G-NEW-6) |
| #13 Spatial SVC | Light (deferred) | No Vulkan spec; no HW support | ~1,500 LoC if/when spec exists |
| #14 Manual reference control (WebRTC index) | Moderate | DPB driven by GOP only | ~400 LoC (G-NEW-7) |
| #15 Per-frame QP map binding | Light | Subsumed by #10 | 0 (covered by G-NEW-4) |
| #18 Format negotiation | Light | Subsumed by #19 | 0 (covered by G-NEW-1) |
| #19 GetSupportedProfiles (BYO + ephemeral) | **Critical** | No separation between caps and session | ~350 LoC (G-NEW-1) |
| #20 VideoEncoderInfo (trusted RC = always_true) | **Critical** | WebRTC concept absent from samples | ~150 LoC (G-NEW-8) |
| #21 Reconfigure (caller-choice IDR) | **Critical** | Live reconfigure not needed for cmdline | ~300 LoC (G-NEW-3) |
| #22 In-memory bitstream (caller-owned buffer) | **Critical** | Sample writes to file | ~250 LoC (G-NEW-2) |
| #23 Capability descriptor | Light | Subsumed by #19 / #20 | 0 (covered by G-NEW-1 + G-NEW-8) |
| #24 Protected encode | Non-goal | Out of scope for MVP | 0 (deferred) |
| #25 Shared device (BYO VkDevice) | **Critical** | Public config never plumbed it | ~30 LoC (G-NEW-9) — plumbing only |

**Total upstream effort for P0 gaps (v1.3 with design choices)**: **~1,130 LoC** (v1.2 was ~870; the +260 covers ephemeral-instance fallback in G-NEW-1, caller-owned-buffer external-memory plumbing in G-NEW-2, deep WebRTC-compatible temporal-SVC GOP patterns in G-NEW-6, and extra `VideoEncoderInfo` fields in G-NEW-8). All deltas land additively on the existing public surface — no breaking changes to current cmdline callers.

---

## 3. Concrete extensions the library needs <a name="3-extensions-needed"></a>

The labels **G-NEW-N** (read: *Gap-resolving New extension #N*) are introduced in §2.1. Each G-NEW-N is a specific additive contribution to the public Vulkan-Video-Samples API that closes one or more of the sufficiency-table gaps. Eleven extensions are catalogued below.

For each extension we list the proposed interface delta, the LoC estimate, and the priority for Chromium MVP. All deltas are upstream contributions back to the Vulkan-Video-Samples repository. None of them break existing cmdline callers — every change is additive: new struct fields default to neutral values, new functions are standalone.

### G-NEW-1 — Capability enumeration before `InitializeExt()` — *BYO primary + ephemeral fallback*

**Why**: Chromium calls `VideoEncodeAccelerator::GetSupportedProfiles()` at GPU process startup, **before** any session exists. See §2.1 Gap #19 for the full caller-side rationale.

```cpp
// NEW in vulkan_video_encoder_ext.h
struct VkVideoEncoderCapabilities {
    VkVideoCodecOperationFlagBitsKHR codec;
    uint32_t  minLevelIdc;
    uint32_t  maxLevelIdc;
    uint32_t  maxDpbSlots;
    uint32_t  maxActiveReferencePictures;
    uint32_t  maxQualityLevels;
    uint64_t  maxBitrate;
    uint32_t  pictureAccessGranularityWidth;
    uint32_t  pictureAccessGranularityHeight;
    VkVideoEncodeRateControlModeFlagsKHR supportedRateControlModes;
    VkVideoEncodeCapabilityFlagsKHR      flags;
    VkExtent2D minCodedExtent;
    VkExtent2D maxCodedExtent;
    std::vector<VkVideoEncodeH264StdFlagsKHR> h264StdFlags;
    std::vector<VkVideoEncodeH265StdFlagsKHR> h265StdFlags;
    std::vector<VkVideoEncodeAV1StdFlagsKHR>  av1StdFlags;
    std::vector<VkFormat> supportedInputFormats;
    bool      supportsQuantizationMap;
    bool      supportsIntraRefresh;
    bool      supportsMaintenance1;
    bool      supportsResizeWithoutIdr;       // for G-NEW-3 caller-choice
};

// PRIMARY: BYO Vulkan handles (matches G-NEW-9 pattern). Use when the caller has
// already initialized gpu::VulkanImplementation.
extern "C" VK_VIDEO_ENCODER_EXPORT
VkResult EnumerateVulkanVideoEncoderCapabilities(
    VkInstance               instance,        // bring-your-own (Chromium's)
    VkPhysicalDevice         physicalDevice,  // bring-your-own
    VkVideoCodecOperationFlagBitsKHR codec,
    VkVideoEncoderCapabilities* outCaps);

// FALLBACK: ephemeral instance + physical device. Use for callers that haven't
// initialized Vulkan yet (e.g., GPU-process startup before VulkanImplementation
// is built). Internally creates a minimal VkInstance + InitPhysicalDevice, enumerates,
// then tears everything down. Same output struct.
extern "C" VK_VIDEO_ENCODER_EXPORT
VkResult EnumerateVulkanVideoEncoderCapabilitiesEphemeral(
    int32_t                  deviceId,        // -1 = pick first capable device
    VkVideoCodecOperationFlagBitsKHR codec,
    VkVideoEncoderCapabilities* outCaps);
```

**LoC**: ~250 (BYO path) + ~100 (ephemeral wrapper) = **~350**. Mostly Vulkan capability struct chaining, mirroring what `EncoderConfig::CreateCodecConfig()` does internally.

### G-NEW-2 — In-memory bitstream via caller-owned `VkBuffer` — *caller-owned output*

**Why**: Chromium cannot run a file-I/O encoder. The caller passes a `VkBuffer` per frame (typically created from a shmem-backed memory region via `VK_EXT_external_memory_host` / `VK_KHR_external_memory_win32`); library writes directly into it. This is true **zero-copy on the output path** — no intermediate `memcpy()` from the lib's internal buffer to the caller's shmem.

```cpp
// NEW in vulkan_video_encoder_ext.h
struct VkVideoEncodeOutputBuffer {
    VkBuffer            buffer;          // caller-allocated; usage = VIDEO_ENCODE_DST
    VkDeviceMemory      memory;          // VK_NULL_HANDLE allowed if buffer is VMA / external
    VkDeviceSize        offset;          // offset within buffer where the encoder starts writing
    VkDeviceSize        maxSize;         // upper bound; library refuses to encode if bitstream > maxSize
    VkExternalMemoryHandleTypeFlagBits externalHandleType;  // 0 if buffer is on lib's VkDevice already
    // When externalHandleType != 0, library imports the caller's external memory (shmem FD on
    // Linux / shared NT handle on Windows / AHardwareBuffer on Android) into its own VkDevice
    // and binds it to `buffer`. The caller is expected to keep the underlying handle alive
    // until ReleaseEncodedFrame() is called for the encode order using this buffer.
};

// NEW field in VkVideoEncodeInputFrame
const VkVideoEncodeOutputBuffer* pOutputBuffer;  // optional; if null, library uses internal buffer

// NEW field in VkVideoEncoderConfig
VkBool32  disableFileOutput;     // VK_TRUE → no outputPath / no internal file-writer thread

// CHANGE in VkVideoEncodeResult — caller-provided buffer path:
result.pBitstreamData     = nullptr;                          // caller already has it
result.bitstreamSize      = feedback.bytesWritten;
result.bitstreamOffset    = pOutputBuffer->offset + dstOffsetWithinBuffer;
result.pictureType        = MapGopPictureType(pending.gopPictureType);
result.isIDR              = pending.isIDR;
result.dts                = ComputeDtsFromEncodeOrder(pending.encodeOrderNum);

// CHANGE in VkVideoEncodeResult — back-compat (lib-owned buffer path, when
// pOutputBuffer == nullptr): library returns mapped pointer for sample callers
// who don't want to manage VkBuffer themselves.
result.pBitstreamData     = mapped_internal_ptr + feedback.offset;
result.bitstreamSize      = feedback.bytesWritten;
// Caller must memcpy out before ReleaseEncodedFrame() unmaps.
```

**LoC**: ~250 (map/unmap lifecycle, external-memory-handle plumbing, DTS derivation, picture-type extraction, file-output guard). Up from the v1.2 estimate of ~120 because the caller-owned path requires external-memory-handle support; the back-compat lib-owned path is the remaining ~80 LoC.

### G-NEW-3 — Functional `Reconfigure()` — *caller-choice IDR-on-resize*

**Why**: `VEA::RequestEncodingParametersChange(bitrate, fps, size)` is mandatory. See §2.1 Gap #21.

```cpp
// NEW in vulkan_video_encoder_ext.h
struct VkReconfigureOptions {
    VkBool32  forceIdrOnResize;          // caller's choice; recommended VK_TRUE
    VkBool32  drainBeforeApply;          // VK_TRUE → wait for in-flight encodes to drain before applying
    uint64_t  applyAtEncodeOrder;        // 0 = next available; otherwise apply starting at this encode order
};

// CHANGE in vulkan_video_encoder_ext.h (signature now takes options)
virtual VkResult Reconfigure(const VkVideoEncoderConfig& newConfig,
                              const VkReconfigureOptions& options) = 0;

// IMPLEMENTATION (vulkan_video_encoder_ext.cpp::Reconfigure)
// 1. Diff oldConfig vs newConfig.
// 2. Bitrate / framerate / quality only:
//      m_rateControlInfo = new RC values;
//      m_sendRateControlCmd = true;
//      next frame emits vkCmdControlVideoCodingKHR(VK_VIDEO_CODING_CONTROL_RATE_CONTROL_BIT_KHR)
//      and the new RC info takes effect without an IDR.
// 3. Size only:
//      if (options.drainBeforeApply) WaitForInFlight();
//      recreate VkVideoSessionKHR (with new maxCodedExtent if needed)
//      recreate VkVideoSessionParametersKHR (with new SPS width/height);
//      if (options.forceIdrOnResize)         next frame is IDR (forceIDR = true)
//      else if (supportsResizeWithoutIdr)    skip IDR (driver-supported, rare today)
//      else                                  next frame is IDR (safety fallback).
// 4. Profile / level change:
//      always recreate session + parameters + always force IDR (spec requirement;
//      options.forceIdrOnResize is ignored in this case).
```

**LoC**: ~300. The encoder already exposes `BeginRateControlInfo`, `m_sendRateControlCmd`, `EncodeVideoSessionParameters()`; the new work is the diff-and-apply state machine + the drain/apply-at-order semantics.

### G-NEW-4 — Per-frame QP map input

**Why**: WebCodecs `EncodeOptions::quantizer` and screen-share scenarios want a delta-QP map without file I/O.

```cpp
// NEW field in VkVideoEncodeInputFrame
VkImage       qpMapImage;        // optional: VK_NULL_HANDLE if none
VkImageLayout qpMapImageLayout;
VkExtent2D    qpMapTexelSize;    // must match caps
```

**LoC**: ~150 LoC (lib already has `StageInputFrameQpMap()` and `ProcessQpMap()`; just expose them in the Ext path).

### G-NEW-5 — Per-frame intra-refresh window

**Why**: WebRTC packet-loss recovery wants per-frame control.

```cpp
// NEW field in VkVideoEncodeInputFrame
uint32_t  intraRefreshIndex;     // 0..cycleDuration-1; UINT32_MAX = no IR this frame
VkBool32  intraRefreshStartCycle;
```

**LoC**: ~80 LoC.

### G-NEW-6 — Temporal SVC layers in the public surface — *deep scope*

**Why**: WebRTC SVC modes `L1T2`/`L1T3`/`L1T4`, simulcast adapter, and the `VideoBitrateAllocation` 2D matrix. See §2.1 Gap #12.

```cpp
// NEW maximum (matches Chromium's kMaxTemporalStreams + headroom).
#define VK_MAX_VIDEO_ENCODE_TEMPORAL_LAYERS 4

// CHANGE in VkVideoEncoder.h:228
VkVideoEncodeRateControlLayerInfoKHR  m_rateControlLayersInfo[VK_MAX_VIDEO_ENCODE_TEMPORAL_LAYERS];

// NEW fields in VkVideoEncoderConfig (additive)
uint32_t  temporalLayerCount;                                              // 1..4
struct {
    uint64_t  averageBitrate;
    uint64_t  maxBitrate;
    uint32_t  framerateNumerator;
    uint32_t  framerateDenominator;
} temporalLayers[VK_MAX_VIDEO_ENCODE_TEMPORAL_LAYERS];

// NEW programmable GOP-temporal pattern (replaces hard-coded T=1 default).
struct VkVideoGopTemporalLayerPattern {
    uint32_t  patternLength;                                                // e.g., 4 for T=3 with pattern [T0,T2,T1,T2]
    uint8_t   temporalLayerOfFrame[VK_MAX_GOP_PATTERN_LENGTH];              // 0..temporalLayerCount-1
};
VkVideoGopTemporalLayerPattern  temporalPattern;

// NEW field in VkVideoEncodeInputFrame (optional caller override)
uint32_t  temporalLayerIndexOverride;     // UINT32_MAX = use GOP-derived index

// NEW in VkVideoEncodeResult (write-back so caller can fill Chromium metadata)
uint32_t  encodedTemporalLayerIndex;      // for H264Metadata::temporal_idx / SVCGenericMetadata::temporal_idx
VkBool32  layerSync;                      // for H264Metadata::layer_sync
```

Internal changes:

1. Expand `m_rateControlLayersInfo[1]` → `[VK_MAX_VIDEO_ENCODE_TEMPORAL_LAYERS]` in `VkVideoEncoder.h` and codec-specific subclasses (`VkVideoEncoderH264`, `H265`, `AV1`).
2. Propagate per-layer struct through `HandleCtrlCmd` and `CodecHandleRateControlCmd`.
3. Wire `VkVideoGopStructure::SetTemporalLayerPattern()` to drive `temporalPattern` instead of the hard-coded default.
4. Each codec writes the temporal-ID into the slice header (`StdVideoEncodeH264SliceHeader::temporal_id`, `StdVideoEncodeH265SliceSegmentHeader::nuh_temporal_id_plus1`, `StdVideoEncodeAV1PictureInfo::temporal_id`).

For WebRTC's 2D `VideoBitrateAllocation` matrix `bitrates[spatial][temporal]`, the Chromium-side wrapper flattens to per-layer bitrates: `lib_config.temporalLayers[t].averageBitrate = sum_over_s(bitrates[s][t])`. When spatial SVC arrives (Gap #13 / G-NEW-10), we extend to a 2D `spatialLayers[].temporalLayers[]` table.

**LoC**: ~250 (up from ~200; the bump covers programmable GOP pattern + per-codec slice-header writes).

### G-NEW-7 — Manual reference buffer control — *WebRTC index model*

**Why**: WebRTC `manual_reference_buffer_control = true` for packet-loss recovery, screen-share, and simulcast layer prediction control. See §2.1 Gap #14.

```cpp
// NEW maximum (matches WebRTC's VP8/VP9 frame buffer pool size).
#define VK_MAX_VIDEO_ENCODE_FRAME_BUFFERS 8

// NEW WebRTC-compatible index model
struct VkVideoEncodeFrameBufferIndices {
    uint32_t  referenceFlags;    // bit i set ⇒ buffer[i] is used as a reference for this frame
    uint32_t  refreshFlags;      // bit i set ⇒ buffer[i] is overwritten with this frame's reconstructed output
    uint32_t  longTermFlags;     // bit i set ⇒ buffer[i] is marked long-term reference (LTR) after this frame
};

// NEW field in VkVideoEncoderConfig
VkBool32  manualReferenceBufferControl;            // opt-in; default VK_FALSE = GOP-driven

// NEW field in VkVideoEncodeInputFrame
const VkVideoEncodeFrameBufferIndices* pFrameBufferIndices;   // null unless manualReferenceBufferControl=VK_TRUE

// NEW in VkVideoEncodeResult (write-back of what actually happened — for SVCGenericMetadata)
uint32_t  encodedReferenceFlags;
uint32_t  encodedRefreshFlags;
uint32_t  encodedLongTermFlags;
```

Internal implementation:

1. When `manualReferenceBufferControl == VK_TRUE`, the library bypasses `VkEncoderDpb*::Update()` and instead uses the caller's `pFrameBufferIndices` as the slot-occupancy oracle.
2. Translate the index model to `VkVideoReferenceSlotInfoKHR[]`: for each set bit in `referenceFlags`, add a slot referencing the corresponding internal DPB picture. `refreshFlags` picks the setup slot.
3. Validate against codec rules:
    - H.264: `referenceCount <= num_ref_frames` (from SPS).
    - H.265: respects `sps_max_dec_pic_buffering_minus1`.
    - AV1: `ref_frame_idx[7]` ordering, `frame_refs_short_signaling`.
4. If `longTermFlags` is set, emit the codec-specific LTR markers (H.264 `memory_management_control_operation` codes; AV1 LTR is implicit via `ref_frame_idx` reuse).
5. Write back `encodedReferenceFlags`/`Refresh`/`LongTerm` for the caller to populate `SVCGenericMetadata::reference_flags` / `refresh_flags`.

**Alternative slot-based API not chosen.** The Vulkan-native shape (`VkVideoReferenceSlotInfoKHR[]` + `pSetupReferenceSlot`) is more flexible but verbose and harder to maintain DPB consistency across codecs. The WebRTC index model maps cleanly to all three codecs and matches what Chromium VEA already speaks at the `SVCGenericMetadata::reference_flags` level.

**LoC**: ~400 (DPB-layer integration is the bulk; index→slot translation table is mechanical; LTR plumbing per codec adds a few dozen LoC each).

### G-NEW-8 — `VideoEncoderInfo`-style descriptor — *`trustedRateController = true` when CBR/VBR supported*

**Why**: WebRTC's bitrate adjuster, simulcast adapter, quality scaler. See §2.1 Gap #20.

```cpp
// NEW struct + method in vulkan_video_encoder_ext.h
struct VkVideoEncoderRuntimeInfo {
    char     implementationName[64];                                       // e.g., "VulkanVideoEncoder-NV"
    VkBool32 isHardwareAccelerated;                                        // always VK_TRUE
    VkBool32 supportsNativeHandle;                                         // VK_TRUE (we accept VkImages)
    VkBool32 trustedRateController;                                        // see note below
    VkBool32 supportsSimulcast;                                            // VK_FALSE initially (use parallel sessions for MVP)
    VkBool32 supportsFrameSizeChange;                                      // mirrors caps.supportsResizeWithoutIdr
    VkBool32 reportsAverageQp;                                             // VK_TRUE (lib writes back qp metadata)
    uint32_t requestedResolutionAlignmentWidth;                            // = caps.pictureAccessGranularityWidth
    uint32_t requestedResolutionAlignmentHeight;
    uint32_t numberOfManualReferenceBuffers;                               // VK_MAX_VIDEO_ENCODE_FRAME_BUFFERS when G-NEW-7
    uint32_t inputCapacity;                                                // how many frames can be in-flight
    uint32_t frameDelay;                                                   // pipeline depth at current GOP setting
    uint32_t fpsAllocationCount;
    uint32_t fpsAllocation[VK_MAX_VIDEO_ENCODE_TEMPORAL_LAYERS];           // per-temporal-layer framerate
    uint32_t resolutionBitrateLimitCount;
    struct {
        VkExtent2D resolution;
        uint64_t   minBitrate;
        uint64_t   maxBitrate;
    } resolutionBitrateLimits[8];                                          // for VideoEncoderInfo::resolution_rate_limits
    VkBool32 applyAlignmentToAllSimulcastLayers;
    std::vector<VkFormat> gpuSupportedPixelFormats;
};

virtual VkResult GetRuntimeInfo(VkVideoEncoderRuntimeInfo* out) const = 0;
```

**`trustedRateController`**: the library reports `VK_TRUE` whenever the active `rateControlMode` is `CBR` or `VBR` *and* the driver advertised support for that mode. Rationale: Vulkan Video drivers are HW-only (no SW-fallback path that Chrome's historical vendor allow-list was protecting against); modern NVIDIA / AMD / Intel HW encoders all implement competent rate control inside the engine. If we observe quality issues with a specific vendor in the wild, we override via a Chromium-side workaround flag — we don't pollute the upstream library with a vendor table.

```cpp
// In the lib's GetRuntimeInfo()
out->trustedRateController =
    ((m_rateControlMode == VK_VIDEO_ENCODE_RATE_CONTROL_MODE_CBR_BIT_KHR) ||
     (m_rateControlMode == VK_VIDEO_ENCODE_RATE_CONTROL_MODE_VBR_BIT_KHR))
    ? VK_TRUE : VK_FALSE;
```

**LoC**: ~150 (struct definition + populator + per-codec resolution-bitrate-limit table). Chromium-side translation to `media::VideoEncoderInfo` is in the Chromium shim, ~100 LoC.

### G-NEW-9 — Plumb BYO `VkDevice` into the public config (already supported internally)

> **Important (v1.2 correction)**: the underlying `VulkanDeviceContext` already supports BYO instance + physical device + device with full non-owning lifecycle. The destructor respects `m_importedInstanceHandle` / `m_importedDeviceHandle` and skips destruction of imported handles. This is **~30 LoC of plumbing** to expose the existing capability through the public `VkVideoEncoderConfig`, not the ~500 LoC of feature work the v1.1 draft claimed.

The existing `VkVideoEncoderConfig::externalInstance` is the precedent — we just add three more fields with the same pattern:

```cpp
// NEW fields in VkVideoEncoderConfig (additive — keep externalInstance)
VkPhysicalDevice externalPhysicalDevice;          // VK_NULL_HANDLE = library picks
VkDevice         externalDevice;                  // VK_NULL_HANDLE = library creates
uint32_t         externalEncodeQueueFamilyIndex;  // required if externalDevice set
uint32_t         externalComputeQueueFamilyIndex; // required if externalDevice set + preprocess filter enabled
```

The wrapper code is trivial — just forward to the existing `VulkanDeviceContext` parameters:

```cpp
// vulkan_video_encoder_ext.cpp::InitVulkanDevice — add to the existing calls:

result = m_vkDevCtx.InitPhysicalDevice(
    config.deviceId, deviceUuid, ...,
    config.externalPhysicalDevice);                    // <-- NEW: was implicit VK_NULL_HANDLE

result = m_vkDevCtx.CreateVulkanDevice(
    0, 1, codecOp, needTransferQueue, false, false, needComputeQueue,
    config.externalDevice);                            // <-- NEW: was implicit VK_NULL_HANDLE

// If externalDevice is set, also use the caller's queue-family indices.
// (Skip the internal queue-family selection logic in that case.)
```

**LoC**: ~30 LoC (config-struct fields + parameter forwarding + a single guard clause). The non-owning lifecycle is already correct in `VulkanDeviceContext`.

Because it's so cheap, **G-NEW-9 becomes a P0 item in Phase 1**, not "land later and migrate to A+". There is no migration step.

### G-NEW-10 — Spatial SVC (deferred)

**Why**: AV1 / VP9 simulcast.

Library has no spatial-layer machinery. Could be implemented as multiple `VulkanVideoEncoderExt` instances (one per layer) plus inter-layer prediction wiring — out of scope for the first ship.

**LoC**: ~1,500 LoC (whole new code path); **deferred**.

### G-NEW-11 — Disable-file-output / stderr-quiet flags

**Why**: GPU-process Chromium can't write to stdout/stderr/disk without crashing the sandbox.

```cpp
// NEW field in VkVideoEncoderConfig
VkBool32  silenceStdio;                  // VK_TRUE = no printf/fprintf in lib
```

The lib has many `std::cout`/`std::cerr` calls (e.g. `vulkan_video_encoder_ext.cpp:246–249` even dumps argv).

**LoC**: ~50 LoC (gate every stdio call on a static flag).

### Summary of contributions (v1.3 — incorporating §2.1 design choices)

| Gap | LoC | Priority for Chromium MVP | Owner | Δ vs v1.2 |
|---|---:|---|---|---|
| G-NEW-1 capability enum (BYO + ephemeral) | **350** | **P0** | Us (upstream) | +100 (ephemeral fallback) |
| G-NEW-2 in-memory bitstream (caller-owned `VkBuffer`) | **250** | **P0** | Us (upstream) | +130 (external-memory plumbing) |
| G-NEW-3 functional Reconfigure (caller-choice IDR) | 300 | **P0** | Us (upstream) | 0 |
| G-NEW-4 per-frame QP map | 150 | P2 | Us (upstream, Phase 6) | 0 |
| G-NEW-5 per-frame intra-refresh | 80 | P2 | Us (upstream, Phase 6) | 0 |
| G-NEW-6 temporal SVC layers (deep scope) | **250** | P1 | Us (upstream, Phase 5) | +50 (programmable GOP pattern) |
| G-NEW-7 manual ref control (WebRTC index model) | 400 | P2 | Us (upstream, Phase 6) | 0 |
| G-NEW-8 runtime info (`trustedRC=true` when CBR/VBR) | **150** | **P0** | Us (upstream) | +30 (more fields) |
| **G-NEW-9 BYO `VkDevice`** | **~30** | **P0** — Phase 1, no migration | Us (upstream) | 0 |
| G-NEW-10 spatial SVC | 1,500 | P3 (defer) | — | 0 |
| G-NEW-11 silence stdio | 50 | **P0** | Us (upstream) | 0 |
| **Total P0 (MVP)** | **~1,130** | | Us | +260 |
| **Total all upstream** | **~3,510** | | | +310 |

Compare to v1.1's projected ~12,130 LoC of from-scratch test/tooling code (and many more LoC of delegate code we'd have to write).

> v1.3 design choices added ~260 LoC to the P0 set (~870 → ~1,130). The deltas buy: ephemeral capability enumeration for early-startup callers (G-NEW-1), zero-copy bitstream writeout into caller shmem (G-NEW-2), caller-choice resize-without-IDR (G-NEW-3), and deep WebRTC-compatible temporal SVC (G-NEW-6).
> Calendar effort impact: still ~2 weeks for the P0 set (the deltas are bounded; G-NEW-2's external-memory plumbing reuses existing `VK_EXT_external_memory_host` / `VK_KHR_external_memory_win32` helpers in the lib).

---

## 4. Four integration models <a name="4-integration-models"></a>

> v1.3 update: added **Model D — static link via `third_party/vulkan_video_samples/`**. v1.2 simplification carries over: Models A and A+ from v1.1 are merged into Model A (BYO from day one).

### Model A — Direct in-process link with BYO `VkDevice` (dynamic link to `libvkvideo-encoder.so`)

```
Chromium GPU process (or utility process)
├── gpu::VulkanDeviceQueue        ← single VkDevice in this process
│     │
│     │ Proposed SharedImageManager::ProduceVulkanVideo()   ← NEW same-device representation
│     ▼  (returns VkImage + acquire/release semaphores)
└── libvkvideo-encoder.so (the library, as-is)
    └── VulkanDeviceContext wraps our VkDevice in non-owning mode (BYO)
    └── Path A: vkCmdEncodeVideoKHR reads the SharedImage VkImage directly — NO COPY
```

```cpp
// media/gpu/vulkan/vulkan_video_encode_accelerator.h
class VulkanVideoEncodeAccelerator : public VideoEncodeAccelerator {
 private:
  scoped_refptr<gpu::VulkanDeviceQueue> vulkan_device_queue_;       // shared with compositor
  VkSharedBaseObj<VulkanVideoEncoderExt> lib_encoder_;              // uses BYO VkDevice
  // Chromium-side shim ~6,200 LoC total (incl. tests).
};
```

**The zero-copy chain on Linux** (Windows / Android symmetric):

1. Compositor (or whoever owns the source frame) produces a `SharedImage` backed by a Vulkan image on the **same `VkDevice`** the encoder uses.
2. **NEW Chromium API**: `SharedImageManager::ProduceVulkanVideo(mailbox, memory_tracker, vulkan_device_queue)` returns a representation that exposes the existing `VkImage` (no FD export, no second image) plus acquire/release timeline semaphores. This does not exist in Chromium ToT; it is modeled after the existing D3D12 `SharedImageManager::ProduceVideo()` path.
3. We pass that `VkImage` to `SubmitExternalFrame()`. The library's `WrapExternalImage()` (non-owning) wraps the handle via `VkImageResource::CreateFromExternal()` and routes it through **Path A** (lines 584–621 of `VkVideoEncoder.cpp`): `srcEncodeImageResource = wrapped_image`, then directly to `EncodeFrameCommon()` → `vkCmdEncodeVideoKHR`.

The pixel data **never leaves DRAM**; we don't even need a second `VkImage` handle.

We share the entire Vulkan chain — instance, physical device, device, queue families — between Chromium and the library through `VkVideoEncoderConfig.externalInstance / externalPhysicalDevice / externalDevice / externalEncodeQueueFamilyIndex / externalComputeQueueFamilyIndex`. This is the "G-NEW-9" item, but the heavy lifting is already done in `VulkanDeviceContext`; it's just a plumbing change on the public config struct.

**Pros**

- Genuine zero-copy from any SharedImage backing (Vulkan-native, DMA-BUF, shared NT handle, AHardwareBuffer). The library's Path A is engineered for exactly this.
- Single `VkDevice` shared with the compositor (or, in OOP deployment, the only device in the utility process). No cross-device round-trip, no duplicated device state.
- Smallest amount of new Chromium code (~6,200 LoC total including tests).
- Library updates ship as `DEPS` bumps, not vendoring merges.
- All codec correctness, DPB, GOP, rate-control logic comes from upstream — no risk of forks drifting.
- The `Ext` interface is already designed for this use case ("for cross-process encoder services" per the header).
- Same library code works in-process and OOP — Model C is just a deployment of this implementation.

**Cons**

- We inherit the library's `std::cout` / `std::cerr` noise until G-NEW-11 lands (trivial to fix; ~50 LoC upstream).
- Validation-layer loading needs to be gated cleanly for ship builds.
- Binary update cadence is Khronos's, not ours — but that's also a pro (we don't carry the fork).
- Upstream PR for the G-NEW-9 plumbing has to land before we can ship. (~30 LoC; expected to merge in one cycle since it's matching the existing `externalInstance` pattern.)

**Effort**

- `VulkanVideoEncodeAccelerator` + config builder + capability enumerator + bitstream wrapper + factory: ~1,600 LoC.
- `VulkanVideoImageRepresentation` + proposed `SharedImageManager::ProduceVulkanVideo()` hook: ~650 LoC.
- `VideoEncoderInfo` plumbing: ~100 LoC.
- Tests (re-using the existing test plan): ~3,850 LoC.
- Upstream P0 contributions (G-NEW-1, 2, 3, 8, 9, 11): **~1,130 LoC** (sized in §3 per the §2.1 design choices), ~2 weeks in parallel.
- **Total**: ~6,200 Chromium LoC + ~1,130 upstream LoC, ~**10–11 engineer-weeks**.

### Model D — Static link via `third_party/vulkan_video_samples/` (RECOMMENDED)

This is **Model A's integration code** packaged the way every other codec library ships in Chromium: vendor the sources under `third_party/`, write a Chromium-native `BUILD.gn`, link statically into `chrome` / `chrome_renderer` / `chrome_gpu` / `headless_shell` / `content_shell`.

```
Chromium repository layout
├── third_party/vulkan_video_samples/                    ← NEW vendored tree
│   ├── README.chromium                                  ← URL, version, license, local mods
│   ├── DEPS                                             ← upstream pin
│   ├── OWNERS                                           ← media team
│   ├── BUILD.gn                                         ← we write this (~400 LoC)
│   ├── LICENSE                                          ← Apache-2.0
│   ├── chromium_patches/                                ← any short-term patches (ideally empty)
│   ├── vk_video_encoder/                                ← upstream source, drop-in
│   │   ├── include/vulkan_video_encoder.h
│   │   ├── include/vulkan_video_encoder_ext.h
│   │   ├── libs/VkVideoEncoder/*.{h,cpp}
│   │   ├── src/vulkan_video_encoder_ext.cpp
│   │   └── src/vulkan_video_encoder.cpp
│   └── common/libs/VkCodecUtils/                        ← VulkanDeviceContext, pools, etc.
│       └── *.{h,cpp}
└── media/gpu/vulkan/                                    ← same as Model A
    ├── vulkan_video_encode_accelerator.{h,cc}
    ├── vulkan_video_encoder_config_builder.{h,cc}
    ├── vulkan_video_encode_capability_enumerator.{h,cc}
    ├── vulkan_video_bitstream_buffer.{h,cc}
    └── BUILD.gn  ← depends on //third_party/vulkan_video_samples
```

`third_party/vulkan_video_samples/BUILD.gn` sketch (matching the pattern of `third_party/libvpx`, `third_party/libaom`, `third_party/dav1d`):

```python
import("//build/config/c++/c++.gni")
import("//media/media_options.gni")

declare_args() {
  enable_vulkan_video_encode_h264 = true
  enable_vulkan_video_encode_h265 = true
  enable_vulkan_video_encode_av1  = true
}

config("vulkan_video_samples_config") {
  include_dirs = [
    "vk_video_encoder/include",
    "common/include",
    "common/libs",
  ]
  defines = [ "VULKAN_VIDEO_SAMPLES_AS_STATIC_LIB" ]
  if (enable_vulkan_video_encode_h264) { defines += [ "ENABLE_H264_ENCODE" ] }
  if (enable_vulkan_video_encode_h265) { defines += [ "ENABLE_H265_ENCODE" ] }
  if (enable_vulkan_video_encode_av1)  { defines += [ "ENABLE_AV1_ENCODE"  ] }
  cflags_cc = [ "-Wno-...whatever the upstream needs" ]
}

source_set("vk_codec_utils") {
  sources = [
    "common/libs/VkCodecUtils/VulkanDeviceContext.cpp",
    "common/libs/VkCodecUtils/VkImageResource.cpp",
    "common/libs/VkCodecUtils/VkBufferResource.cpp",
    # ... ~30 cpp files
  ]
  public_configs = [ ":vulkan_video_samples_config" ]
  deps = [ "//third_party/vulkan-headers" ]
}

source_set("vk_video_encoder_core") {
  sources = [
    "vk_video_encoder/src/vulkan_video_encoder_ext.cpp",
    "vk_video_encoder/libs/VkVideoEncoder/VkVideoEncoder.cpp",
    "vk_video_encoder/libs/VkVideoEncoder/VkVideoGopStructure.cpp",
    # ... shared core files
  ]
  if (enable_vulkan_video_encode_h264) {
    sources += [
      "vk_video_encoder/libs/VkVideoEncoder/VkVideoEncoderH264.cpp",
      "vk_video_encoder/libs/VkVideoEncoder/VkEncoderDpbH264.cpp",
    ]
  }
  # ... same for H265, AV1
  public_configs = [ ":vulkan_video_samples_config" ]
  deps = [ ":vk_codec_utils", "//third_party/vulkan-headers" ]
}

# The single target Chromium media depends on:
component("vulkan_video_samples") {
  public_deps = [ ":vk_video_encoder_core" ]
  if (is_component_build) {
    # Build as a component .so to keep incremental link times sane.
  } else {
    # Default ship build: rolls into the chrome binary as a static lib.
  }
}
```

`media/gpu/vulkan/BUILD.gn` adds:

```python
source_set("vulkan_video_encode") {
  sources = [
    "vulkan_video_encode_accelerator.cc",
    "vulkan_video_encoder_config_builder.cc",
    # ...
  ]
  deps = [
    "//third_party/vulkan_video_samples",   # <-- the static dep
    "//gpu/command_buffer/service",
    "//gpu/vulkan",
    "//media/base",
  ]
}
```

#### What we get for free vs Model A

| | Model A (dynamic) | **Model D (static)** |
|---|---|---|
| `dlopen`/`dlsym` at runtime | yes | **no — direct symbol resolution at link time** |
| ABI mismatch risk between `.so` and headers | possible | **impossible — single source tree** |
| Sandbox: load `.so` from untrusted path | needs policy | **no policy needed — symbols are in `chrome`** |
| LTO/PGO of encoder + Chromium together | no (separate `.so`) | **yes — single LTO unit** |
| Crash reports point at correct symbols | needs separate symbol upload for `libvkvideo-encoder.so` | **single Crashpad upload covers everything** |
| Binary size cost | +0 (no library if user doesn't have it) | +2–5 MB per chrome binary (with all 3 codecs) |
| Per-codec build-time selection | no | **yes — `enable_vulkan_video_encode_h264 = false` etc.** |
| Update mechanism | `DEPS` bump pointing at a different `.so` | `DEPS` bump pointing at a source tree (same workflow Chromium uses for every other vendored library) |
| Short-term Chromium-only patch | not possible cleanly | possible via `chromium_patches/` |
| Fits Chromium's `third_party/` codec pattern | unusual | **standard** (`libvpx`, `libaom`, `dav1d`, `libavif`, `libwebp`) |
| Codec correctness source | binary blob (Khronos) | source tree (Khronos), reviewed at each roll |
| Vendoring overhead | none | ~50 LoC (`README.chromium`, `DEPS`, `OWNERS`); routine |

#### What's the same as Model A

- **Integration code**: `VulkanVideoEncodeAccelerator`, new `VulkanVideoImageRepresentation`, proposed `SharedImageManager::ProduceVulkanVideo`, capability enumerator, bitstream wrapper, factory wiring. **Byte-for-byte identical** between Model A and Model D — only `BUILD.gn` differs.
- **BYO `VkDevice`** chain via `VkVideoEncoderConfig.externalDevice` etc. — same upstream G-NEW-9 plumbing PR feeds both models.
- **Path A zero-copy** from SharedImage — same library code path runs.
- **All upstream contributions** (G-NEW-1, 2, 3, 8, 9, 11; ~1,130 LoC at v1.3 sizing) — same set; they ship upstream and are then pulled into the vendored tree on the next DEPS roll, exactly like every other Chromium `third_party/` library.

#### Codec build flags

Each codec encoder pulls in roughly:

| Codec | Source files | Stripped size (release LTO) |
|---|---|---|
| H.264 | `VkVideoEncoderH264.cpp`, `VkEncoderDpbH264.cpp`, `H264SeqParamSet.cpp`, etc. | ~0.8 MB |
| HEVC  | `VkVideoEncoderH265.cpp`, `VkEncoderDpbH265.cpp`, `H265SeqParamSet.cpp`, etc. | ~1.4 MB |
| AV1   | `VkVideoEncoderAV1.cpp`, `VkEncoderDpbAV1.cpp`, AV1 bitstream writer, etc. | ~1.9 MB |
| Shared core (`VulkanDeviceContext`, image/buffer resources, GOP, rate control) | ~25 .cpp files | ~0.6 MB |
| **Worst-case total** | | **~4.7 MB** |

For comparison, Chromium today statically links:

- `libvpx` (VP8 + VP9 encode + decode): ~1.5 MB
- `libaom` (AV1 encode + decode): ~3.1 MB
- `dav1d` (AV1 decode fast path): ~0.9 MB
- `libavif` (AVIF still-image): ~0.4 MB

So adding ~5 MB of Vulkan Video Samples for HW-accelerated encode of three codecs is in line with the existing codec footprint. On platforms where the GPU doesn't have video encode queues, the encoder code is unreachable but still linked — we can add `enable_vulkan_video_encode = false` for low-binary-size builds (ChromeOS Flex, headless containers).

#### Pros

- **Single binary deployment**: no `.so` to ship, no `dlopen` failure modes, no path search.
- **Standard Chromium codec pattern**: identical to `libvpx`/`libaom`/`dav1d`/`libavif`. Reviewable by every Chromium media OWNER.
- **LTO/PGO across the boundary**: inliner sees `VulkanVideoEncoderExt::SubmitExternalFrame()` and Chromium's caller together. Saves a few hundred KB and trims hot-path latency.
- **No ABI surface**: header + library always match because they come from the same checkout.
- **Per-codec build-time selection**: ship only the codecs you need.
- **Codec correctness reviewable at each roll**: each DEPS bump shows a source diff in code review, not an opaque `.so` SHA change.
- **Short-term patch lane**: `third_party/vulkan_video_samples/chromium_patches/` for the (hopefully rare) case where we need a fix that hasn't shipped upstream.
- **Same Chromium-side code as Model A**: zero risk of bifurcation; in fact we can build Model A and Model D from the same checkout via a single GN argument.

#### Cons

- **Binary size**: +2–5 MB per Chrome binary. Mitigation: per-codec build flags + Tier-0/Tier-1 distros only ship codecs the platform supports.
- **Vendoring discipline**: every upstream change has to land via DEPS roll. Same overhead as `third_party/libvpx` rolls today (~weekly automated).
- **Source review at each roll**: a real cost in OWNERS time vs Model A's opaque `.so` (but a benefit for security review).
- **Upstream API churn**: the lib's internal `EncoderConfig` is not a stable ABI today; if upstream refactors it, the next roll will require coordination. (This is also why we kept the public `VulkanVideoEncoderExt` interface stable in §3.)
- **First-time vendoring setup**: ~0.5–1 week of GN authoring, `README.chromium`, OWNERS, license vetting. Routine but not free.

#### Effort vs Model A

Everything in Model A plus:

- `third_party/vulkan_video_samples/BUILD.gn`: ~400 LoC.
- `README.chromium`, `DEPS`, `OWNERS`, `LICENSE`: ~50 LoC.
- Initial vendoring + clang-format check + 2/3 platform-bot builds green: ~0.5–1 calendar week.
- **Total**: ~6,200 Chromium LoC + ~400 GN LoC + ~1,130 upstream LoC, ~**10.5–12 engineer-weeks**.

The integration code (~6,200 LoC) is exactly the same as Model A. The +0.5–1 week is the vendoring infrastructure, which is one-time work.

### Model B — Vendor the codec layer, write the Chromium front-end

> Relationship to Model D: **both** models vendor sources under `third_party/vulkan_video_samples/`. The difference is **what** they replace. Model D keeps `vulkan_video_encoder_ext.cpp` and `VulkanDeviceContext` as the library's public surface and writes a thin Chromium-side shim. Model B *drops* those two files and writes a full Chromium-native front-end that talks directly to the codec classes (`VkVideoEncoderH264`/`H265`/`AV1`). Model B is what you do if you need to fork the encoder driver loop itself.

```
Chromium GPU process
├── third_party/vulkan_video_samples/  (vendored upstream tree — same as Model D)
│   ├── libs/VkVideoEncoder/           (per-codec delegates, GOP, DPB — KEEP)
│   ├── libs/VkCodecUtils/             (image pools, buffer pools, command pool — KEEP)
│   └── src/                           (we DROP vulkan_video_encoder_ext.cpp)
└── media/gpu/vulkan/                  (our own VEA + wrapper + device adapter)
    ├── vulkan_video_encode_accelerator.{h,cc}
    ├── vulkan_video_encoder_wrapper.{h,cc}  ← uses gpu::VulkanDeviceQueue
    └── vulkan_video_device_adapter.{h,cc}   ← bridges Chromium → lib's VulkanDeviceContext
```

Vendor the **codec implementation** files only (the `VkVideoEncoderH264`, `H265`, `AV1` classes and their DPB / GOP support). Replace `vulkan_video_encoder_ext.cpp` and `VulkanDeviceContext` with our own wrappers that talk to `gpu::VulkanDeviceQueue`.

**Pros**

- Reuses ~80 % of the per-codec implementation work (the ~2,300 LoC of Std-Video conversion + DPB code we estimated in v1.1).
- Single `VkDevice` shared with the compositor → real zero-copy.
- `VideoEncoderInfo`, `GetSupportedProfiles()`, in-memory bitstream, Reconfigure — all written *natively* in Chromium, so no upstream blocker.
- Same `gpu::VulkanFenceHelper` / `SharedContextState` semantics as decode.
- No stdout/stderr noise inherited.
- We can pull individual upstream fixes without taking the whole library.

**Cons**

- We're now tracking upstream — every Vulkan-Video-Samples release we cherry-pick the per-codec changes.
- We have to build a `VulkanDeviceContext` adapter that exposes Chromium's `VkDevice` in the shape the vendored code expects (~300 LoC).
- More Chromium code overall than Model A.
- Risk of vendored code drift; needs a code-OWNERS commitment to keep it in sync.

**Effort**

- Vendor: 0 LoC writing, but ~1 day to set up `third_party/vulkan_video_samples/` + `DEPS` + `BUILD.gn`.
- Chromium VEA + wrapper + delegate (per-codec subclass that *uses* the vendored codec classes): ~3,500 LoC.
- `VulkanDeviceContext` adapter: ~300 LoC.
- DMA-BUF / SharedImage import → upstream codec classes accept a `VkImage`, fits naturally: ~400 LoC.
- Tests: ~6,000 LoC (Tier 1 + 2 mostly; Tier 5 via the upstream test runner): see test plan.
- Total upstream contributions for shared infra: ~0 (we're not changing public lib; we use internal codec headers).
- **Total**: ~10,200 LoC of new Chromium code, ~**12–15 engineer-weeks**.

### Model C — Out-of-process encoder service (sandboxed utility process)

> **Important context (v1.3)**: Chromium **already ships an OOP-VEA path** on Linux + ChromeOS for VA-API today. The whole infrastructure — utility-process launcher, Mojo interfaces, sandbox policy, browser-side broker, command-buffer SharedImage transport — is already in tree. Model C for Vulkan is **a new backend behind the existing OOP-VEA scaffold**, not a new IPC stack.

> See architecture diagram: [`diagrams/lib_model_c_oop_architecture.svg`](diagrams/lib_model_c_oop_architecture.svg).

#### What is a Chromium "utility process"?

Chromium runs as **a multi-process browser** by design. The browser process is the parent; everything else is a child:

| Process | Owned by | Sandbox | Purpose |
|---|---|---|---|
| Browser (parent) | `chrome` binary | none — broker | UI, networking, controls children |
| Renderer | one per site | tight (`kRenderer`) | Blink, V8, layout |
| GPU | one per browser | wide (`kGpu`, talks to driver) | Vulkan/GL/Skia, compositor, today's VEA/VDA |
| **Utility** | per-service | varies (`kService`, `kHardwareVideoEncoding`, `kAudio`, ...) | Network, audio, video decode (OOP-D), video encode (OOP-VEA), CDM, on-device ML, etc. |
| Renderer-host | one per renderer | sandboxed | Renderer-process broker |

A **utility process** is a short-lived (or long-lived) sandboxed child process that hosts a single Mojo service. It's launched on demand via `content::ServiceProcessHost::Launch<T>()` and torn down when the last Mojo connection drops. Its sandbox profile is **declared inline in the `.mojom` file** via the `ServiceSandbox` attribute — at IPC time, the browser process consults the attribute and applies the matching `seccomp-bpf` (Linux) / `AppContainer` (Windows) / `seatbelt` (macOS) profile before the process executes any service code.

> The pattern in `.mojom` files:
>
> ```mojom
> [EnableIf=allow_linux_video_acceleration,
>     ServiceSandbox=sandbox.mojom.Sandbox.kHardwareVideoEncoding]
> interface VideoEncodeAcceleratorProviderFactory { ... };
> ```
>
> From `media/mojo/mojom/video_encode_accelerator.mojom:102-104`. The browser **enforces** that any binding of this interface goes to a process sandboxed at `kHardwareVideoEncoding`.

#### Existing OOP-VEA scaffold in Chromium (verified paths)

| Component | Path | Role |
|---|---|---|
| Feature flag | `media::kUseOutOfProcessVideoEncoding` | Gates the entire OOP path; on by default on some platforms, runtime-toggleable |
| Build gate | `USE_LINUX_VIDEO_ACCELERATION` | Linux + ChromeOS |
| **Mojo interfaces** | `media/mojo/mojom/video_encode_accelerator.mojom` | `VideoEncodeAccelerator`, `...Provider`, `...ProviderFactory` |
| Sandbox label | `sandbox::mojom::Sandbox::kHardwareVideoEncoding` (`sandbox/policy/sandbox_type.cc:70`) | Declared inline in mojom; enforced at process launch |
| Sandbox policy | `sandbox/policy/linux/sandbox_seccomp_bpf_linux.cc:223-227` | Currently reuses GPU-process policy as a transition; TODO to tighten |
| **Utility-process launcher** | `content/browser/media/video_encode_accelerator_provider_launcher.cc` | `ServiceProcessHost::Launch().WithDisplayName("Video Encoder")` |
| Browser-side broker | `content/browser/renderer_host/render_process_host_impl_receiver_bindings.cc:361-380` | Plumbs renderer requests to a per-renderer utility process |
| Service-side registrar | `content/utility/services.cc:436-438` | `services.Add(RunVideoEncodeAcceleratorProviderFactory)` |
| Factory | `media/mojo/services/mojo_video_encode_accelerator_provider_factory.cc` | One factory per utility process; mints providers |
| Provider | `media/mojo/services/mojo_video_encode_accelerator_provider.cc` | One provider per renderer; mints `VideoEncodeAccelerator` instances |
| VEA service-side wrapper | `media/mojo/services/mojo_video_encode_accelerator_service.cc` | Wraps the underlying `media::VideoEncodeAccelerator` for Mojo |
| Renderer-side proxy | `media/mojo/clients/mojo_video_encoder_metrics_provider.cc` (and siblings) | The Mojo client end |

**Process diagram** (today's OOP-VEA, exactly as Model C would deploy):

```
┌─────────────────┐
│ Renderer        │  WebCodecs / WebRTC / MediaRecorder
│                 │  ↓ binds receiver via RenderProcessHost
└─────┬───────────┘
      │  pending_receiver<VideoEncodeAcceleratorProvider>
      ▼
┌─────────────────┐
│ Browser process │  Per-renderer broker: launches a fresh utility
│                 │  process if needed, holds the Factory remote,
│                 │  forwards the renderer's pending_receiver to it.
│                 │  See render_process_host_impl_receiver_bindings.cc:361-380
└─────┬───────────┘
      │  ServiceProcessHost::Launch().WithDisplayName("Video Encoder")
      │  + sandbox::mojom::Sandbox::kHardwareVideoEncoding
      ▼
┌─────────────────┐      ┌────────────────────────────┐
│ Utility process │  ◀──▶│ GPU process                │
│ "Video Encoder" │ Mojo │  (SharedImage backings,    │
│                 │      │   CommandBufferStub,       │
│  Vulkan-Video-  │      │   gpu::VulkanDeviceQueue)  │
│  Samples        │      └────────────────────────────┘
│  (Model D       │
│   sources       │
│   statically    │
│   linked here)  │
│                 │  Owns its OWN VkInstance + VkDevice + VkVideoSession.
│                 │  Talks to /dev/dri/* via the sandbox broker.
└─────────────────┘
```

**Each renderer process gets its own utility process.** From the mojom doc (lines 87-101): *"each renderer process should get its own corresponding utility process."* Cross-renderer leaks through the encoder are architecturally impossible.

#### Why Model C, given Models D / A already give zero-copy in the GPU process?

| Concern | In-process (Model D / A) | OOP utility process (Model C) |
|---|---|---|
| Encoder crash kills | GPU process → entire tab + WebGL + compositor | only the "Video Encoder" utility process; the GPU process and the tab survive |
| Encoder bug exfiltrates secrets to ... | the GPU process's address space (compositor, all WebGL contexts, all SharedImages) | the utility process's address space — empty except for the in-flight frame |
| Encoder triggers driver bug | takes down GPU process → tab crash, possible system effects | takes down the utility process → next encode fails; tab keeps running |
| Encoder needs additional driver privileges | GPU sandbox is already the widest; not much room to tighten | utility process can have a **narrower** sandbox than GPU (e.g., no GL context, no Vulkan present queue, fewer ioctls). Today's `kHardwareVideoEncoding` reuses the GPU policy "as a transition" (sandbox_seccomp_bpf_linux.cc:224) — the long-term plan is a tighter profile. |
| Memory residency | encoder shares the GPU process's heap | utility process has its own heap; encoder doesn't bloat the GPU process |
| Driver replacement | replacing `libvkvideo-encoder` requires GPU process restart | restart only the utility process; GPU process keeps running |

In short: **Model C is the security/reliability hardening deployment** of Models D / A. It pays a latency cost (~0.5–2 ms p99 per frame for the Mojo round-trip, measured on production OOP-D) in exchange for crash isolation, a tighter sandbox, and per-renderer isolation.

#### Zero-copy across the process boundary

This is the part that surprises people. **Pixel data does not traverse the Mojo pipe.** The encode flow is:

1. **Renderer** has a `VideoFrame` backed by a `SharedImage` mailbox (e.g., from `OffscreenCanvas`, tab capture, or a `MediaStreamTrack`).
2. **Renderer-side `mojo_video_encode_accelerator.cc`** sends the encode call to the utility process; the payload includes the SharedImage mailbox token and an `EncodeCommandBufferId { channel_token, route_id }`. **No pixel bytes.**
3. **Utility process** uses `EncodeCommandBufferId` to talk back to the **GPU process's** `MediaGpuChannelManager` (it has a Mojo channel to the GPU process). The GPU process resolves the mailbox into the actual `VkImage` and **exports a DMA-BUF FD** (Linux) / **shared NT handle** (Windows) / `AHardwareBuffer` (Android).
4. **Kernel duplicates the FD** into the utility process's FD table. The pixel pages live in DRAM exactly once; both processes' `VkImage`s map them.
5. **Utility process imports the FD** into its own `VkDevice` via `VkImportMemoryFdInfoKHR` + `VkImageDrmFormatModifierExplicitCreateInfoEXT`. The library's **Path A** consumes the imported `VkImage` directly.
6. **`vkCmdEncodeVideoKHR` runs in the utility process**, producing bitstream into `VkVideoEncodeResult::pBitstreamData`.
7. **Bitstream flows back over Mojo** as `mojo_base.mojom.UnsafeSharedMemoryRegion` (already pre-shared via `UseOutputBitstreamBuffer()` at line 236 of the .mojom). The bitstream is small (~kilobytes per frame), so the Mojo cost is negligible.

In Mojo terms, look at the existing precedent — the same `EncodeCommandBufferId` field at `video_encode_accelerator.mojom:62-65`:

```mojom
struct EncodeCommandBufferId {
  mojo_base.mojom.UnguessableToken channel_token;
  int32 route_id;
};

interface VideoEncodeAcceleratorProvider {
  CreateVideoEncodeAccelerator(
      EncodeCommandBufferId? command_buffer_id,
      pending_receiver<VideoEncodeAccelerator> receiver);
  ...
};
```

The `command_buffer_id` is the back-channel that lets the utility process pull SharedImage backings out of the GPU process. We don't have to invent anything for Vulkan — VA-API already uses this exact mechanism.

#### Sequence diagram — first encode of a SharedImage frame

```
Renderer            Browser           GPU process       Utility ("Video Encoder")
   │                   │                   │                   │
   │  CreateVEA(...)   │                   │                   │
   ├──────────────────▶│                   │                   │
   │                   │ ServiceProcessHost                    │
   │                   │  ::Launch(...).                       │
   │                   │  WithDisplayName(                     │
   │                   │   "Video Encoder").                   │
   │                   │  sandbox=kHwVideoEncoding             │
   │                   │ ─────────────────────────fork()──────▶│
   │                   │                   │                   │ exec sandboxed
   │  pending_receiver  │ forward Mojo pipe receiver           │
   │  forwarded         │ via render_process_host_impl_...    │
   │ ─────────────────────────────────────────────────────────▶│
   │                                                           │ binds VEAProvider
   │  Initialize(...) ───────────────────────────────────────▶│ binds VEA service
   │                                                           │ Creates lib_encoder_
   │                                                           │ with externalDevice
   │                                                           │ = utility's VkDevice
   │                                                           │ (NOT GPU's)
   │                                                           │
   │  Encode(VideoFrame{mailbox, EncodeCmdBufId}, options)    │
   │ ─────────────────────────────────────────────────────────▶│
   │                                                           │ Resolve mailbox via
   │                                                           │ CommandBufferStub in
   │                                                           │ GPU process       ◀─────┐
   │                                                           │                          │
   │                                                           │                  GPU process
   │                                                           │                  exports DMA-BUF FD
   │                                                           │                  (kernel duplicates
   │                                                           │                   into utility's
   │                                                           │                   FD table)
   │                                                           │ ◀────────────────────────┘
   │                                                           │
   │                                                           │ Import FD as VkImage on
   │                                                           │ utility's VkDevice; cache.
   │                                                           │
   │                                                           │ lib_encoder_->SubmitExternalFrame(
   │                                                           │   {image=imported, ...})
   │                                                           │
   │                                                           │   → Path A: zero-copy
   │                                                           │   → vkCmdEncodeVideoKHR
   │                                                           │
   │  BitstreamBufferReady(buf_id, metadata)                   │ bitstream → pre-shared
   │ ◀─────────────────────────────────────────────────────────│ shmem region
   │                                                           │
```

First-frame latency: ~3–5 ms extra (fork + sandbox + Mojo handshake + first DMA-BUF export). Steady-state per-frame Mojo overhead: ~0.5–2 ms p99, dominated by the IPC round-trip; pixel data and bitstream traverse shmem, not the Mojo control pipe.

#### What changes vs Model D

The Chromium-side integration code is **almost identical**; the differences are:

| Concern | Model D (in-process) | Model C (OOP) |
|---|---|---|
| Where `VulkanVideoEncodeAccelerator` runs | GPU process | Utility process |
| Which `gpu::VulkanDeviceQueue` it uses | the GPU process's shared compositor `VkDevice` | a **utility-process-private** `VkDevice` created at service startup |
| Source frame access | Proposed `SharedImageManager::ProduceVulkanVideo()` directly | `CommandBufferHelper` round-trip → GPU process exports DMA-BUF / handle → utility imports it |
| Build target depending on the library | `//chrome` | `//content/utility:utility` (or a dedicated utility binary if we want isolation per Khronos's recommendation) |
| Sandbox profile | `kGpu` (existing) | `kHardwareVideoEncoding` (existing; tighter profile is on the roadmap) |
| Service registration | factory wiring in `gpu_video_encode_accelerator_factory.cc` | `services.Add(RunVulkanVideoEncodeAcceleratorProviderFactory)` in `content/utility/services.cc` (we add this) |
| Build gate | `enable_vulkan_video_encode` | same + `use_linux_video_acceleration` |

**The library code in `third_party/vulkan_video_samples/` is unchanged**; just statically linked into a different binary (utility binary instead of `chrome`).

#### Pros

- **Strong crash isolation**: a library crash takes down only the utility process, not the GPU process or the tab. The user sees encode fall back to software (or a transient WebRTC bitrate drop) instead of a tab crash.
- **Tighter sandbox profile**: the `kHardwareVideoEncoding` sandbox can be made stricter than the GPU sandbox (no GL context, no Vulkan present queue, no `mremap`, fewer ioctls). The current implementation is "GPU sandbox as transition" but Google's ChromeOS team is actively narrowing it (`TODO(b/255554267)` at `sandbox_seccomp_bpf_linux.cc:224`).
- **Per-renderer isolation**: each renderer gets its own utility process; cross-renderer encoder-state leaks are architecturally impossible.
- **No new IPC stack**: reuses the existing OOP-VEA infrastructure (`ServiceProcessHost::Launch`, the `VideoEncodeAcceleratorProvider*` Mojo trio, the `kHardwareVideoEncoding` sandbox enum, the `EncodeCommandBufferId` zero-copy bridge).
- **Same library code**: the `third_party/vulkan_video_samples/` tree is just statically linked into the utility binary instead of `chrome`. We can ship Model C and Model D side-by-side via a feature flag.
- **Aligns with Chrome's direction**: every new video service in Chrome (`OnDeviceModelService`, `OOPVideoDecoderFactoryProcessService`, `VideoEncodeAcceleratorProviderFactory`, `MediaFoundationServiceBroker`) is OOP.
- **Independent library updates**: a utility-process restart picks up a new library binary without restarting the GPU process or the tab.

#### Cons

- **~0.5–2 ms p99 per-frame Mojo overhead** (control-plane round-trip; pixel data still zero-copies through DMA-BUF). Negligible for video calls (30 fps = 33 ms/frame), measurable for 240 fps gaming streams.
- **More processes**: one extra process per active renderer that's encoding. Memory: ~30–60 MB resident per utility process (`VkDevice` + library state + Vulkan ICD).
- **First-frame latency**: ~3–5 ms extra (fork + sandbox apply + Mojo handshake + first DMA-BUF import). Mitigated by warming the process at navigation start when WebCodecs/WebRTC is detected.
- **Cross-process DMA-BUF import** is per-mailbox: we need a cache keyed on mailbox in the utility process so we don't pay the import cost every frame. Easy — the cache eviction follows SharedImage lifetime via the GPU channel.
- **Sandbox policy tightening is a separate work stream**: Google's media team is actively narrowing `kHardwareVideoEncoding`; our work would slot into that.
- **Test surface**: WebCodecs / WebRTC tests have to cover both deployment modes (in-process and OOP) for as long as we ship both.

#### Effort estimate

We **reuse** the existing Mojo interfaces, sandbox label, utility-process launcher, browser-side broker, factory, provider, and service-side wrapper — all of which already exist for VA-API. We **only** add a Vulkan backend behind them.

| Component | LoC | Notes |
|---|---:|---|
| Same Chromium-side integration code as Model D | ~2,350 | unchanged |
| Same `third_party/vulkan_video_samples/` (linked into utility binary, not `chrome`) | 0 net (`BUILD.gn` adds a target) | +~30 LoC in `BUILD.gn` |
| `VulkanVideoEncodeAcceleratorProvider` (utility-side; mirrors `VaapiVideoEncodeAccelerator`'s provider) | ~300 | wires the lib into the OOP scaffold |
| Cross-process SharedImage importer (utility-process side) | ~400 | DMA-BUF FD import + VkImage cache per mailbox; reuses the `CommandBufferHelper` plumbing for OOP that already exists |
| `services.Add(RunVulkanVideoEncodeAcceleratorProviderFactory)` registration | ~40 | one-liner in `content/utility/services.cc` |
| Sandbox policy tightening (if we narrow `kHardwareVideoEncoding`) | ~250 | optional — can ship with the existing GPU-policy-as-transition |
| Build gate (`enable_vulkan_video_encode`) hooked into `USE_LINUX_VIDEO_ACCELERATION` | ~20 | one `if` in mojom + `BUILD.gn` |
| Tests (Mojo round-trip + OOP-specific scenarios) | ~1,500 | on top of Model D's test suite |
| Upstream P0 contributions (same set as Model D) | ~1,130 | shared with Model D |
| **Total OOP-specific delta on top of Model D** | **~2,500** | |
| **Grand total Chromium-side** | **~4,850** | (~2,350 from Model D + ~2,500 OOP delta) |
| **Calendar effort** | **~13–16 weeks** | Model D (10.5–12w) + ~3–4w OOP integration + Mojo tests |

Most of the OOP overhead is **test infrastructure**, not Mojo plumbing — the Mojo plumbing is ~700 LoC because the infrastructure is already in tree.

#### When to choose Model C over Model D

Pick Model D for the MVP. Pick Model C **on top of Model D** when:

- Security review demands the encoder leave the GPU process address space.
- A particular driver/hardware combo is crashy enough that GPU-process death from an encoder bug is a regression risk.
- Chrome on a given platform is in the middle of an "everything goes OOP" migration (ChromeOS today is in that phase for video).
- We need a tighter sandbox profile than the GPU process can give us.

Concretely: **ship Model D first** (MVP at ~10.5–12 weeks). When/if the OOP requirement materializes, **add Model C** behind a `kUseOutOfProcessVulkanVideoEncoding` feature flag (~3–4 weeks of additional work). The two can ship side-by-side, gated by the flag.

### Side-by-side (v1.3)

| Aspect | **D** (static, vendored) | **A** (dynamic `.so`) | **B** (vendor codec, custom front-end) | **C** (OOP utility process) | v1.1 from-scratch |
|---|---|---|---|---|---|
| Build/link mode | **static, in chrome** | dynamic, external `.so` | static, in chrome | static, in utility binary | n/a |
| Chromium-side LoC | ~2,350 | ~2,350 (same) | ~4,200 | ~3,750 + Mojo | ~9,000 |
| GN/build LoC | ~400 (one-time) | ~50 (just dep lookup) | ~400 | ~400 | n/a |
| Upstream contribution | ~1,130 | ~1,130 | ~0 (vendored) | ~1,130 | n/a |
| Vendoring under `third_party/` | whole library (drop-in) | none | partial (codec layer only, custom front-end) | same as D | n/a |
| **Zero-copy from compositor** | ✅ same device → Path A | ✅ same device → Path A | ✅ same device | ✅ Mojo handle transport + Path A | ✅ |
| Devices in this process | 1 (shared) | 1 (shared) | 1 (shared) | 1 (utility process owns its own) | 1 |
| Single binary | ✅ | ❌ external `.so` | ✅ | ✅ in utility binary | ✅ |
| LTO/PGO across encoder + Chromium | ✅ | ❌ | ✅ | ✅ | ✅ |
| Per-codec build flags | ✅ `enable_vulkan_video_encode_*` | ❌ all-in `.so` | ✅ | ✅ | ✅ |
| Binary size cost vs A | +2–5 MB | baseline | +2–5 MB | +2–5 MB | +5–8 MB |
| Codec correctness reviewable at roll | ✅ source diff | ❌ opaque `.so` SHA change | ✅ | ✅ | n/a |
| Short-term Chromium-only patch lane | ✅ `chromium_patches/` | ❌ wait for upstream | ✅ inline edits | ✅ | n/a |
| Library update cadence | DEPS source roll | DEPS `.so` bump | we cherry-pick | DEPS source roll | n/a |
| Crash isolation | weak | weak | weak | **strong** | weak |
| Time to first runnable H.264 CQP | **~W3** | ~W3 | ~W4.5 | ~W3.5 | ~W6 |
| **Calendar effort** | **10.5–12 weeks** | 10–11 weeks | 12–15 weeks | 13–16 weeks | 19–21 weeks |
| MVP risk | Low | Low (but unusual deployment) | Low | Med (Mojo + sandbox) | Low |
| Matches Chromium codec-library pattern | ✅ (`libvpx`, `libaom`, `dav1d`, `libavif`, ...) | ❌ (would be unique) | ✅ | ✅ | n/a |

> v1.3 — Model D is the new recommendation. It is Model A's integration code packaged as Chromium's standard `third_party/` codec pattern. Model A (the v1.2 recommendation) is now positioned as the prototyping variant: same code, dynamic-link build, useful when you have a separately-distributed `.so` and want to iterate before vendoring. Model B is the fallback if we ever need to fork the codec driver loop. Model C is a deployment option orthogonal to A/D.

---

## 5. Risks <a name="5-risks"></a>

> Flagged speculation: I have *not* prototyped any of these — they are inferred from reading the library source. Items marked "verified" came from a code path I read end-to-end.

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | The library's `Reconfigure()` is a stub today (returns `VK_ERROR_FEATURE_NOT_PRESENT`, **verified** in `vulkan_video_encoder_ext.cpp:646`). Without it, all dynamic bitrate/framerate changes need session-reset → IDR. WebRTC will get visible glitches. | **High** | Land G-NEW-3 as part of MVP. |
| R2 | `GetEncodedFrame()` returns `pBitstreamData = nullptr` and the comment says "Bitstream is in file for now" (**verified** at `vulkan_video_encoder_ext.cpp:578`). MVP **cannot** ship without an in-memory bitstream. | **Critical** | G-NEW-2 is a P0 ship blocker. |
| R3 | Public `VkVideoEncoderConfig` only exposes `externalInstance` today, even though the underlying `VulkanDeviceContext` already accepts BYO physical device + device with proper non-owning lifecycle. v1.1 thought this was ~500 LoC; v1.2 confirmed it is ~30 LoC of plumbing. | Low (was High) | G-NEW-9 plumbing PR; ship before Phase 1 closes. |
| R4 | Library has many `std::cout` / `std::cerr` calls, including a per-call dump of argv (`vulkan_video_encoder_ext.cpp:246–249`). In Chromium's GPU process this would either be silenced by stdout redirection (and lost diagnostically) or potentially trip sandbox warnings. | Med | G-NEW-11. |
| R5 | `temporalLayerCount` exists in the underlying GOP code, but the per-frame `m_rateControlLayersInfo` array is hard-sized to 1 (`VkVideoEncoder.h:228`). T=2/T=3 may compile but the rate-control struct will only carry one layer. | Med | G-NEW-6 expands the arrays; the change is mechanical but invasive. |
| R6 | The library's `BuildEncoderConfig()` translates the structured config back into argc/argv strings (`vulkan_video_encoder_ext.cpp:139–252`) before parsing them in `CreateCodecConfig()`. This round-trip is fragile (string→int→string parsing). | Med | Long-term we should add direct-field assignment to `EncoderConfig` and skip the round-trip. ~150 LoC, can ship later. |
| R7 | `SupportsFormat()` returns a hardcoded list (`vulkan_video_encoder_ext.cpp:649–668`); does **not** query device capabilities. Will report support for formats the driver actually refuses. | Med | G-NEW-1 caps enumeration fixes the root cause. |
| R8 | Library asserts `m_queue != VK_NULL_HANDLE` on the compute queue used by its `VulkanFilter` for RGBA→NV12 conversion. Chromium's `gpu::VulkanDeviceQueue` may not expose a compute queue at all on every backend. | Med | Either always request `VK_QUEUE_COMPUTE_BIT` from Chromium's device or set `enablePreprocessFilter = VK_FALSE` and do the conversion ourselves via `VulkanFilterYuvCompute` (already used by the encode/decode code paths). |
| R9 | `Flush()` in the Ext impl drops the encoder (`m_encoder = nullptr;`) instead of reopening — see `vulkan_video_encoder_ext.cpp:613–638`. After Flush, the next `SubmitExternalFrame()` will fail. Chromium's `VEA::Flush()` is **not** terminal — encoding continues. | High | Wrap the lib's flush behavior in our shim: on `VEA::Flush(cb)`, wait for fences then re-`InitializeExt()` with the same config. ~80 LoC. |
| R10 | `Reconfigure()` and several capability queries are stubs (`GetMaxWidth/Height` return constant 8192). On a 4K-only encoder this would falsely advertise 8K support. | Low | G-NEW-1 caps fix. |
| R11 | The library's `EncoderConfig` is *not* a stable ABI. Vendored Model B has to track the internal API across upstream changes. | Med | Pin to a tagged release; subscribe to Khronos GitLab issues; have an OWNERS team for the vendored tree. |
| R12 | Validation layers loaded unconditionally if `config.validate = true` — needs to be conditional on Chromium's `--enable-vulkan-debug-utils` flag, not exposed to ship builds. | Low | Standard CL during shim wiring. |
| R13 | The library uses `printf`/`fprintf` for verbose mode; not `LOG()` — no integration with `chrome://media-internals`. | Low | Wrap the lib's traces with a callback hook (Model A/C: needs upstream change; Model B: replace at source). |
| R14 | Library is Apache-2.0 licensed — fine for Chromium ingestion. | n/a (good) | n/a |

---

## 6. Effort estimate vs v1.1 <a name="6-effort"></a>

### v1.1 baseline (no library reuse)

From `VULKAN_VIDEO_ENCODE_INTEGRATION_DESIGN_DOCUMENT.md` §11:

```
Phase 1 (Foundation + private VkDevice)        3w
Phase 2 (H.264 CQP)                            3w
Phase 3 (H.264 CBR/VBR + Reconfigure)          2w
Phase 4 (GPU-backed input)                     3w
Phase 5 (HEVC + AV1)                           5w
Phase 6 (SVC, QP map, intra-refresh, manual)   3w
Total                                         19w  (range: 18–21w)
```

### Re-estimated against the library (v1.3)

| Phase | v1.1 baseline | **Model D (static)** | Model A (dynamic) | Model B (vendor codec) | Model C (OOP) |
|---|---:|---:|---:|---:|---:|
| 0. Vendoring (one-time) | n/a | **0.5–1w** | 0 (DEPS bump only) | 0.5–1w | 0.5–1w |
| 1. Foundation + BYO device | 3w | 2w (lib does device setup, we plumb BYO) | 2w | 2w | 2w (utility-process scaffolding) |
| 2. H.264 CQP | 3w | **0.5w** (lib does it; same-device VkImage) | 0.5w | 1.5w | 1w (Mojo + lib) |
| 3. H.264 CBR/VBR + Reconfigure | 2w | 1.5w (we land G-NEW-3 upstream) | 1.5w | 1.5w | 1.5w |
| 4. GPU-backed input | 3w | **1w** (same-device representation accessor) | 1w | 2w | 3w (Mojo image transport) |
| 5. HEVC + AV1 | 5w | **1w** (lib has them) | 1w | **2w** (wrapper per codec) | **1w** (lib has them) |
| 6. Advanced (SVC/QP/IR/manual) | 3w | 2w (lib partial; we land G-NEW-4/5/6/7) | 2w | 3w (vendor + upstream) | 2w |
| **MVP-ready total** | **19w** | **8.5w** | **8w** | **12.5w** | **11w** |
| Add P0 upstream G-NEW-1/2/3/8/9/11 | — | 2w parallel | 2w parallel | 0w (in-tree) | 2w parallel |
| Add tests (per VULKAN_VIDEO_TEST_PLAN) | parallel 8w | parallel 5w | parallel 5w | parallel 6w | parallel 7w |
| **Calendar weeks (1 owner)** | **19–21** | **10.5–12** | **10–11** | **12–15** | **13–16** |
| **Saved vs v1.1** | — | **~8–10 weeks** | **~9–10 weeks** | **~5–7 weeks** | **~3–6 weeks** |

> Model D vs Model A delta: **+0.5–1 week of one-time vendoring effort** (`BUILD.gn`, `README.chromium`, `OWNERS`, license vetting, first-roll DEPS pin). After that, every roll is a routine `DEPS` bump same as `libvpx`/`libaom`/etc.

> All test estimates are *calendar* weeks in parallel with implementation. The total wall-clock time assumes one owner; with two owners parallelism shifts the calendar by ~40 %.

### LoC totals (v1.3)

| Model | Production code (Chromium) | GN/build LoC | Test code (Chromium) | Vendored library LoC | Upstream contribution |
|---|---:|---:|---:|---:|---:|
| v1.1 (from scratch) | ~9,000 | ~50 | ~12,130 | 0 | 0 |
| **Model D (static)** | **~2,350** | **~400** | **~3,850** | **~25,000 (drop-in, no changes)** | **~1,130** |
| Model A (dynamic) | ~2,350 | ~50 | ~3,850 | 0 (binary in DEPS) | ~1,130 |
| Model B | ~4,200 | ~400 | ~6,000 | ~12,000 (partial vendor) | ~0 |
| Model C | ~3,750 + Mojo | ~400 | ~6,500 | ~25,000 (in utility process) | ~1,130 |

> "Vendored library LoC" is code we *carry* but did not *write*. The ~25,000 LoC of Vulkan-Video-Samples is delivered to Chromium as a drop-in third_party tree; Chromium media OWNERS review it on the first commit and then track upstream diffs at each roll, exactly like `libvpx` (which is ~150,000 LoC) and `libaom` (~700,000 LoC).

---

## 7. Recommended plan (v1.3 — Model D) <a name="7-recommended-plan"></a>

### One-step plan: vendor under `third_party/`, static link, BYO `VkDevice` from day one

Model D is **Model A's integration code** packaged the way Chromium ships every other codec library. Vendor the Vulkan-Video-Samples sources under `third_party/vulkan_video_samples/`, write a Chromium-native `BUILD.gn` (~400 LoC), link statically into the Chromium binary. The lower-level `VulkanDeviceContext` accepts an imported `VkDevice` already (`VulkanDeviceContext::CreateVulkanDevice(..., vkDevice)`); we still need to expose it through `VkVideoEncoderConfig` (~30 LoC upstream). One-time vendoring overhead is ~0.5–1 week.

```cpp
// media/gpu/vulkan/vulkan_video_encode_accelerator.cc  (sketch — v1.2)

EncoderStatus VulkanVideoEncodeAccelerator::Initialize(const Config& cfg, ...) {
  VkVideoEncoderConfig lib_config = MapToLibConfig(cfg);

  // BYO chain — pass Chromium's existing Vulkan handles to the library.
  // VulkanDeviceContext already supports this; G-NEW-9 just plumbs it
  // through the public config struct.
  lib_config.externalInstance              = vk_implementation_->GetVulkanInstance();
  lib_config.externalPhysicalDevice        = vulkan_device_queue_->GetVulkanPhysicalDevice();
  lib_config.externalDevice                = vulkan_device_queue_->GetVulkanDevice();
  lib_config.externalEncodeQueueFamilyIndex  = vulkan_device_queue_->GetVideoEncodeQueueFamilyIndex();
  lib_config.externalComputeQueueFamilyIndex = vulkan_device_queue_->GetComputeQueueFamilyIndex();

  lib_config.disableFileOutput = VK_TRUE;          // G-NEW-2 + G-NEW-11
  lib_config.silenceStdio      = VK_TRUE;
  // ... codec, resolution, bitrate, etc. from cfg

  VK_CHECK(CreateVulkanVideoEncoderExt(lib_encoder_));
  VK_CHECK(lib_encoder_->InitializeExt(lib_config));
  ...
}

void VulkanVideoEncodeAccelerator::Encode(scoped_refptr<VideoFrame> frame, bool force_kf) {
  // SharedImage backing IS on our (= the library's) VkDevice. No FD export,
  // no second VkImage, no DMA-BUF round-trip. Just resolve the mailbox and
  // hand the existing VkImage to the library.
  std::unique_ptr<gpu::VulkanVideoImageRepresentation> repr =
      shared_image_manager_->ProduceVulkanVideo(frame->shared_image()->mailbox(),
                                                  memory_tracker_, vulkan_device_queue_);
  auto access = repr->BeginScopedReadAccess();

  VkVideoEncodeInputFrame ext_frame = {
      .image                = access->image(),
      .format               = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM,
      .imageTiling          = VK_IMAGE_TILING_OPTIMAL,   // or DRM_FORMAT_MODIFIER if backing is DMA-BUF
      .currentLayout        = VK_IMAGE_LAYOUT_VIDEO_ENCODE_SRC_KHR,
      .forceIDR             = force_kf,
      .waitSemaphoreCount   = 1, .pWaitSemaphores   = access->acquire_semaphore_ptr(),
      .signalSemaphoreCount = 1, .pSignalSemaphores = access->release_semaphore_ptr(),
      ...
  };
  VK_CHECK(lib_encoder_->SubmitExternalFrame(ext_frame));
}
```

This is the same flow as Chromium's D3D12 VEA `SharedImageManager::ProduceVideo()` path — just on Vulkan.

### Vendoring under `third_party/` (Model D specifics)

Drop the entire Vulkan-Video-Samples encoder + `VkCodecUtils` tree under `third_party/vulkan_video_samples/`. No source changes — just a `BUILD.gn`, a `README.chromium`, a `DEPS` pin to a specific upstream commit, an `OWNERS` file, and the upstream `LICENSE`. The Chromium-side integration code (next section) is unchanged from Model A.

```
src/third_party/vulkan_video_samples/
├── README.chromium      # URL / version / license / local mods
├── DEPS                 # upstream commit pin
├── OWNERS               # media team
├── LICENSE              # Apache-2.0 (carried from upstream)
├── BUILD.gn             # ~400 LoC, written by us
├── chromium_patches/    # ideally empty
└── (upstream tree drop-in, no edits)
    ├── vk_video_encoder/include/vulkan_video_encoder.h
    ├── vk_video_encoder/include/vulkan_video_encoder_ext.h
    ├── vk_video_encoder/libs/VkVideoEncoder/*.{h,cpp}
    ├── vk_video_encoder/src/vulkan_video_encoder_ext.cpp
    ├── vk_video_encoder/src/vulkan_video_encoder.cpp
    └── common/libs/VkCodecUtils/*.{h,cpp}
```

`//media/gpu/vulkan/BUILD.gn` adds a single dep:

```python
source_set("vulkan_video_encode") {
  sources = [ "vulkan_video_encode_accelerator.cc", ... ]
  deps   = [
    "//third_party/vulkan_video_samples",   # ← static dep
    "//gpu/command_buffer/service",
    "//gpu/vulkan",
    "//media/base",
  ]
}
```

Per-codec GN flags let downstream builds turn off codecs they don't need (e.g. embedded ChromeOS without HEVC):

```python
declare_args() {
  enable_vulkan_video_encode      = enable_hw_accelerated_video
  enable_vulkan_video_encode_h264 = enable_vulkan_video_encode
  enable_vulkan_video_encode_h265 = enable_vulkan_video_encode
  enable_vulkan_video_encode_av1  = enable_vulkan_video_encode
}
```

DEPS rolls follow the existing Chromium pattern (`AutoRoller` for `third_party/libvpx`, `third_party/libaom`, `third_party/dav1d`). First roll is manual + reviewed; subsequent rolls land via the auto-roller bot.

### What we write in Chromium (~6,200 LoC)

| Component | LoC |
|---|---:|
| `media/gpu/vulkan/vulkan_video_encode_accelerator.{h,cc}` — VEA surface, lifecycle, Initialize/Encode/Flush/etc. | ~900 |
| `media/gpu/vulkan/vulkan_video_encoder_config_builder.{h,cc}` — maps `media::VideoEncodeAccelerator::Config` to `VkVideoEncoderConfig` | ~250 |
| `gpu::VulkanVideoImageRepresentation` (in `gpu/command_buffer/service/shared_image/`) — Vulkan equivalent of D3D12's `VideoImageRepresentation` | ~400 |
| Proposed `SharedImageManager::ProduceVulkanVideo()` + the backing-side hook | ~250 |
| `media/gpu/vulkan/vulkan_video_encode_capability_enumerator.{h,cc}` — Chromium-side wrapper over G-NEW-1 | ~150 |
| `media/gpu/vulkan/vulkan_video_bitstream_buffer.{h,cc}` — wraps `VkVideoEncodeResult::pBitstreamData` for memcpy into shmem | ~150 |
| Factory wiring (`gpu_video_encode_accelerator_factory.cc`) | ~150 |
| `VideoEncoderInfo` plumbing on top of G-NEW-8 | ~100 |
| Tests (per `VULKAN_VIDEO_TEST_PLAN.md` Tier 1 + 2) | ~3,850 |
| **Total Chromium** | **~6,200** |

> Compare to v1.1's Model A estimate of ~7,050 LoC: ~850 LoC cheaper because we dropped the `vulkan_video_cross_device_importer` (~600 LoC) — same-device representation access replaces it.

### What we contribute upstream (~1,130 LoC at v1.3 sizing, all in Phase 1)

| Upstream | LoC | Why P0 for Model A |
|---|---:|---|
| G-NEW-1 capability enumeration (BYO + ephemeral) | 350 | Need it before any encoder exists; ephemeral fallback for early-startup callers |
| G-NEW-2 in-memory bitstream (caller-owned VkBuffer) | 250 | Can't ship file I/O; caller-owned buffer enables zero-copy writeout to shmem |
| G-NEW-3 functional `Reconfigure()` (caller-choice IDR) | 300 | WebRTC bitrate adjuster; resize without IDR when caller and driver agree |
| G-NEW-8 runtime info (trustedRC always_true on CBR/VBR) | 150 | WebRTC simulcast + bitrate adjuster |
| **G-NEW-9 BYO `VkDevice` (plumbing)** | **~30** | Single device from day one |
| G-NEW-11 silence stdio | 50 | Sandbox-safety |
| **Total upstream Phase 1** | **~1,130** | ~2 weeks of upstream work, parallel with Chromium impl |

### Why Model D beats Models A / B (v1.3)

| Concern | **Model D (static)** | Model A (dynamic) | Model B (custom front-end) |
|---|---|---|---|
| Build / link | one `chrome` binary, LTO across the boundary | external `.so`, no LTO across boundary | one binary, LTO |
| Vendoring shape | whole library, drop-in, no edits | nothing vendored (binary in DEPS) | partial library, custom front-end edits |
| Carrying Chromium patches | rare, lives in `chromium_patches/` | none possible | constant — front-end is the patch |
| OWNERS review per roll | source diff (auditable) | opaque `.so` SHA bump | source diff + manual cherry-picks |
| First runnable H.264 CQP | W3 | W3 | W4.5 |
| Zero-copy from SharedImage | ✓ same device + Path A | ✓ same device + Path A | ✓ same device |
| Devices in this process | 1 | 1 | 1 |
| Codec correctness source | upstream sources, reviewed at each roll | opaque binary | vendored sources + our edits |
| Per-codec build flags | ✓ | ✗ | ✓ |
| Sandbox impact | none (single binary) | needs `.so` load policy | none |
| Calendar effort | **10.5–12 weeks** | 10–11 weeks | 12–15 weeks |
| Fits Chromium's `third_party/` codec pattern | **yes** (canonical) | no (unusual) | yes (but customized) |

Model A is **byte-for-byte the same integration code** as Model D, just with the lib distributed as a separate `.so`. We can build both from the same checkout via a GN argument:

```python
declare_args() {
  # If true, build third_party/vulkan_video_samples as a static lib (Model D).
  # If false, link against a system-installed libvkvideo-encoder.so (Model A).
  vulkan_video_samples_static = true
}
```

This makes Model A a **prototype/external-binary deployment** of the same integration. Use Model A only if you have a reason to ship the library separately (e.g., a vendor driver that wants to swap the lib without rebuilding Chrome).

Model B remains a viable **fallback** only if:

- Upstream rejects or stalls the trivial G-NEW-9 plumbing PR (unlikely — it's matching an existing pattern in `VulkanDeviceContext`), **or**
- We need to carry a long-running Chromium-only patch to the codec layer (no concrete need today).

### Out-of-process deployment is orthogonal

If Chromium video acceleration moves to a utility process (the OOP-D pattern), **the library code in this plan is unchanged**. The utility process owns its own `gpu::VulkanDeviceQueue` and we pass that to the library via BYO. The only OOP-specific work is Mojo plumbing in the GPU process — costed separately under Model C.

In other words: Model A is the *implementation*; in-process vs OOP is the *deployment*. Chrome can ship in-process for the MVP and migrate to OOP later without touching the encoder integration code.

### Revised 6-phase execution plan (Model D v1.3)

```
Week    0  1  2  3  4  5  6  7  8  9 10 11 12
P0 ████             Vendor third_party/vulkan_video_samples, BUILD.gn, OWNERS, DEPS (one-time)
P1     ████████     Static-link wiring + BYO device + ProduceVulkanVideo
                    + capability enumerator + caps surface
P2             ████ H.264 CQP via Path A — SharedImage VkImage straight in
P3                  ████ H.264 CBR/VBR + Reconfigure (G-NEW-3 upstream lands)
P4                      ████ VideoEncoderInfo + remaining SharedImage hooks
P5                           ████████ HEVC + AV1 (free from library)
P6                                   ██████ SVC + QP map + intra-refresh + manual refs
```

Critical path: P0 → P1 → P2 → P3 → P4 → P5 → P6, **~10.5–12 weeks** total. Upstream P0 set (G-NEW-1/2/3/8/9/11) runs in parallel with P1/P2/P3 and is pulled into the vendored tree on the next DEPS roll.

### Phase exit criteria

- **Phase 1**: `media_unittests --gtest_filter="*Vulkan*"` green; `chrome --enable-features=VulkanVideoEncode` reports H.264/H.265/AV1 in `chrome://gpu` via the library's enumeration (G-NEW-1). Library reports it is running on Chromium's `VkDevice` (verified with a debug log on the lib side of `m_importedDeviceHandle`).
- **Phase 2**: encode → SW-decode → PSNR ≥ 38 dB at CQP 26 on `bear` 1080p30; bitstream comes from `VkVideoEncodeResult::pBitstreamData` (G-NEW-2), no file output. **Zero-copy verified** by a DCHECK counter that asserts the proposed `SharedImageManager::ProduceVulkanVideo` path returned a same-device `VkImage`.
- **Phase 3**: WebRTC loopback with bitrate adjuster bouncing 500 kbps ↔ 5 Mbps does not glitch (G-NEW-3).
- **Phase 4**: `VideoEncoderInfo::supports_gpu_shared_images = true`; tab capture → WebRTC encode without CPU readback.
- **Phase 5**: Tier-5 conformance pass rates per the test plan — H.264 ≥ 95 % JVT, HEVC ≥ 90 % JCT-VC, AV1 ≥ 90 % Argon.
- **Phase 6**: WebCodecs WPT for QP map / intra-refresh / manual refs / SVC all green.

### Upstream coordination

Phase 1 lands G-NEW-1, 2, 3, 8, 9, 11 upstream (~1,130 LoC at v1.3 sizing, ~2 weeks). All in parallel with the Chromium implementation. After the patches ship, every future Chromium roll of the library is a `DEPS` bump.

---

## 8. References <a name="8-references"></a>

### Library source (verified paths)

| Path | Lines verified |
|---|---|
| `vk_video_encoder/include/vulkan_video_encoder.h` | 1–59 (full) |
| `vk_video_encoder/include/vulkan_video_encoder_ext.h` | 1–301 (full) |
| `vk_video_encoder/src/vulkan_video_encoder_ext.cpp` | 1–713 (full) |
| `vk_video_encoder/libs/VkVideoEncoder/VkVideoEncoder.h` | 1–978 (representative read) |
| `vk_video_encoder/libs/VkVideoEncoder/VkVideoEncoder.cpp` | 2660–2806 (rate-control + command flow) |
| `vk_video_encoder/libs/VkVideoEncoder/VkEncoderConfig.h` | 760–950 (config fields) |
| `vk_video_encoder/libs/VkVideoEncoder/VkVideoEncoderH264.cpp` | 100–219, 600–650 (RC + slice constantQp) |
| `vk_video_encoder/demos/vk-video-enc/Main.cpp` | 1–321 (full) |
| `vk_video_encoder/CMakeLists.txt` | 1–590 (full) |

### Chromium files referenced

- `media/video/video_encode_accelerator.h` — VEA interface
- `media/video/video_encoder_info.h` — `VideoEncoderInfo`
- `media/gpu/gpu_video_encode_accelerator_factory.cc` — factory wiring
- `media/gpu/windows/d3d12_video_encode_accelerator.cc` (lines 140–250) — `CommandBufferHelper` / `SharedImageManager::ProduceVideo` pattern (the model for our SharedImage path)

#### Existing OOP-VEA scaffold (verified May 2026; Model C reuses all of this)

| Path | Lines | Role |
|---|---|---|
| `media/mojo/mojom/video_encode_accelerator.mojom` | 47–109 | `VideoEncodeAccelerator{,Provider,ProviderFactory}` interfaces; `ServiceSandbox=kHardwareVideoEncoding` |
| `media/mojo/mojom/video_encode_accelerator.mojom` | 62–65, 76–78 | `EncodeCommandBufferId` for cross-process SharedImage resolution |
| `media/mojo/services/mojo_video_encode_accelerator_provider_factory.{h,cc}` | full | Factory hosted in the utility process |
| `media/mojo/services/mojo_video_encode_accelerator_provider.cc` | full | Provider; calls `gpu_video_encode_accelerator_factory.cc` to create the underlying VEA |
| `media/mojo/services/mojo_video_encode_accelerator_service.{h,cc}` | full | Service-side Mojo wrapper around `media::VideoEncodeAccelerator` |
| `content/browser/media/video_encode_accelerator_provider_launcher.cc` | 10–16 | `ServiceProcessHost::Launch().WithDisplayName("Video Encoder")` |
| `content/browser/renderer_host/render_process_host_impl_receiver_bindings.cc` | 361–380 | Browser-process broker; one utility per renderer |
| `content/utility/services.cc` | 364–371, 436–438 | Registers `RunVideoEncodeAcceleratorProviderFactory` on `USE_LINUX_VIDEO_ACCELERATION` |
| `sandbox/policy/sandbox_type.cc` | 70, 144, 290–291, 407–408 | `kHardwareVideoEncodingSandbox` string + enum mapping |
| `sandbox/policy/mojom/sandbox.mojom` | 118 | `[EnableIf=enable_oop_video_accelerators] kHardwareVideoEncoding` |
| `sandbox/policy/linux/sandbox_seccomp_bpf_linux.cc` | 222–228 | Current policy = GPU-process policy (transitional per `TODO(b/255554267)`) |

### Companion docs

- [`VULKAN_VIDEO_ENCODE_INTEGRATION_DESIGN_DOCUMENT.md`](VULKAN_VIDEO_ENCODE_INTEGRATION_DESIGN_DOCUMENT.md) — main design (v1.1 baseline)
- [`VULKAN_VIDEO_TEST_PLAN.md`](VULKAN_VIDEO_TEST_PLAN.md) — test infrastructure
- [`VULKAN_VIDEO_ENCODE_DESIGN_REVIEW_FINDINGS.md`](VULKAN_VIDEO_ENCODE_DESIGN_REVIEW_FINDINGS.md) — F1–F13 review items

---

## Appendix A — SVG diagrams

| Diagram | File |
|---|---|
| Four integration models side-by-side (A dynamic / D static / B vendor codec / C OOP) | [`diagrams/lib_integration_models.svg`](diagrams/lib_integration_models.svg) |
| Library interface coverage vs Chromium requirements (gap heatmap) | [`diagrams/lib_interface_gap_heatmap.svg`](diagrams/lib_interface_gap_heatmap.svg) |
| Revised execution Gantt (all four models vs v1.1) | [`diagrams/lib_execution_gantt.svg`](diagrams/lib_execution_gantt.svg) |
| Stack: where each component lives in Model B | [`diagrams/lib_model_b_stack.svg`](diagrams/lib_model_b_stack.svg) |
| Chromium's `third_party/` codec library landscape (where Model D fits) | [`diagrams/lib_third_party_pattern.svg`](diagrams/lib_third_party_pattern.svg) |
| Model C OOP architecture (Chromium's existing OOP-VEA scaffold reused for Vulkan) | [`diagrams/lib_model_c_oop_architecture.svg`](diagrams/lib_model_c_oop_architecture.svg) |
