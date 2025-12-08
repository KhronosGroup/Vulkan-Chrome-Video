# Vulkan Video Integration for Chromium - Research Report

**Date**: December 4, 2025  
**Project Goal**: Integrate Vulkan Video decoder (and later encoder) as HW accelerated driver to Chromium  
**Status**: Research Phase - Architecture Analysis Complete

---

## Table of Contents
1. [Executive Summary](#executive-summary)
2. [Chromium Hardware Video Decode Architecture](#chromium-architecture)
3. [VA-API Integration Analysis](#vaapi-analysis)
4. [Vulkan Video API Overview](#vulkan-video-api)
5. [Google Codec 2.0](#codec-2)
6. [Integration Strategy](#integration-strategy)
7. [Key Files and Integration Points](#key-files)
8. [Design Questions to Answer](#design-questions)
9. [References](#references)

---

## 1. Executive Summary <a name="executive-summary"></a>

Chromium currently uses **VA-API** for hardware video decoding on Linux/ChromeOS. The architecture is modular, using an abstraction layer that separates:

1. **Codec parsing** (H264, H265, VP8, VP9, AV1) - platform-agnostic
2. **Hardware acceleration interface** (VA-API, V4L2, D3D11, VideoToolbox) - platform-specific

To integrate **Vulkan Video**, we need to create a new hardware acceleration backend that:
- Implements the `AcceleratedVideoDecoder` delegate interface
- Wraps Vulkan Video API calls (similar to `VaapiWrapper`)
- Integrates with Chromium's GPU memory management (DMABuf, GpuMemoryBuffer)

**NVIDIA has expressed interest** in collaborating with Google to bring Vulkan Video support to Chromium, indicating industry momentum for this integration.

---

## 2. Chromium Hardware Video Decode Architecture <a name="chromium-architecture"></a>

### High-Level Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        VideoDecoderPipeline                             │
│  (media/gpu/chromeos/video_decoder_pipeline.h)                          │
│                                                                         │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                    VideoDecoderMixin                            │   │
│   │  - Initialize(), Decode(), Reset()                              │   │
│   │  - Abstracts platform-specific decoders                         │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│              ┌───────────────┼───────────────┐                          │
│              ▼               ▼               ▼                          │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│   │VaapiVideo    │  │V4L2Video     │  │[NEW]VulkanVideo│                │
│   │Decoder       │  │Decoder       │  │Decoder        │                 │
│   └──────────────┘  └──────────────┘  └──────────────┘                  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `VideoDecoderPipeline` | `media/gpu/chromeos/video_decoder_pipeline.h` | Main entry point, manages frame pools, image processing |
| `VideoDecoderMixin` | Same file | Base class for platform decoders |
| `AcceleratedVideoDecoder` | `media/gpu/accelerated_video_decoder.h` | Abstract codec parser interface |
| `H264Decoder`, `H265Decoder`, etc. | `media/gpu/h264_decoder.h` | Codec-specific parsers (reusable!) |
| `VaapiVideoDecoder` | `media/gpu/vaapi/vaapi_video_decoder.h` | VA-API implementation of VideoDecoderMixin |
| `VaapiWrapper` | `media/gpu/vaapi/vaapi_wrapper.h` | Low-level VA-API calls wrapper |

### Decode Flow (VA-API as reference)

```
1. DecoderBuffer arrives (encrypted stream)
           │
           ▼
2. VaapiVideoDecoder::Decode()
           │
           ▼
3. AcceleratedVideoDecoder::Decode() (e.g., H264Decoder)
           │
           ├──► Parse NALUs (SPS, PPS, Slice headers)
           │
           ▼
4. H264VaapiVideoDecoderDelegate (Accelerator)
           │
           ├──► CreateH264Picture()      → Allocate VASurface
           ├──► SubmitFrameMetadata()    → vaCreateBuffer (picture params)
           ├──► SubmitSlice()            → vaCreateBuffer (slice params + data)
           └──► SubmitDecode()           → vaBeginPicture/vaRenderPicture/vaEndPicture
                     │
                     ▼
5. VaapiWrapper::ExecuteAndDestroyPendingBuffers()
           │
           ▼
6. Hardware decodes frame → VASurface contains decoded YUV
           │
           ▼
7. OutputPicture() → Frame ready for display/compositing
```

---

## 3. VA-API Integration Analysis <a name="vaapi-analysis"></a>

### VaapiWrapper (media/gpu/vaapi/vaapi_wrapper.h)

This is the **key abstraction** we need to replicate for Vulkan Video.

**Core Responsibilities:**
- VA Display/Context management
- Surface creation (VASurface ↔ DMABuf)
- Buffer submission (picture params, slice data)
- Decode execution

**Key Methods to Mirror:**

```cpp
// Surface Management
CreateContextAndSurfaces(va_format, size, hints, num, surfaces)
CreateVASurfaceForPixmap(pixmap)
CreateScopedVASurfaces(...)

// Buffer Submission  
SubmitBuffer(VABufferType, size, data)
SubmitBuffers(vector<VABufferDescriptor>)

// Decode Execution
ExecuteAndDestroyPendingBuffers(va_surface_id)

// Surface Export (for compositor)
ExportVASurfaceAsNativePixmapDmaBuf(surface)
```

### Codec-Specific Delegates

Each codec has a delegate that implements `AcceleratedVideoDecoder::Accelerator`:

| Codec | Delegate | Header |
|-------|----------|--------|
| H.264 | `H264VaapiVideoDecoderDelegate` | `h264_vaapi_video_decoder_delegate.h` |
| H.265 | `H265VaapiVideoDecoderDelegate` | `h265_vaapi_video_decoder_delegate.h` |
| VP8 | `VP8VaapiVideoDecoderDelegate` | `vp8_vaapi_video_decoder_delegate.h` |
| VP9 | `VP9VaapiVideoDecoderDelegate` | `vp9_vaapi_video_decoder_delegate.h` |
| AV1 | `AV1VaapiVideoDecoderDelegate` | `av1_vaapi_video_decoder_delegate.h` |

**Key Delegate Methods (H264 example):**

```cpp
// Create picture (allocates decode surface)
scoped_refptr<H264Picture> CreateH264Picture() override;

// Submit SPS/PPS
void ProcessSPS(const H264SPS* sps, span<const uint8_t> data) override;
void ProcessPPS(const H264PPS* pps, span<const uint8_t> data) override;

// Submit frame metadata (picture parameters)
Status SubmitFrameMetadata(sps, pps, dpb, ref_lists..., pic) override;

// Submit slice data
Status SubmitSlice(pps, slice_hdr, ref_lists..., pic, data, size) override;

// Execute decode
Status SubmitDecode(pic) override;

// Output decoded frame
bool OutputPicture(pic) override;
```

---

## 4. Vulkan Video API Overview <a name="vulkan-video-api"></a>

### Extensions Required

| Extension | Purpose |
|-----------|---------|
| `VK_KHR_video_queue` | Core video queue operations |
| `VK_KHR_video_decode_queue` | Decode-specific operations |
| `VK_KHR_video_decode_h264` | H.264/AVC decode profile |
| `VK_KHR_video_decode_h265` | H.265/HEVC decode profile |
| `VK_KHR_video_decode_av1` | AV1 decode profile (newer) |

### Vulkan Video Decode Flow

```
1. Query video decode capabilities
   vkGetPhysicalDeviceVideoCapabilitiesKHR()
   vkGetPhysicalDeviceVideoFormatPropertiesKHR()

2. Create video session
   VkVideoSessionKHR  ← vkCreateVideoSessionKHR()
   
3. Allocate session memory
   vkGetVideoSessionMemoryRequirementsKHR()
   vkBindVideoSessionMemoryKHR()

4. Create video session parameters (SPS/PPS)
   VkVideoSessionParametersKHR ← vkCreateVideoSessionParametersKHR()

5. Per-frame decode:
   a. Begin video coding scope
      vkCmdBeginVideoCodingKHR()
   
   b. Submit decode command
      vkCmdDecodeVideoKHR()
      - Bitstream buffer
      - Picture resources (DPB)
      - Reference frame info
   
   c. End video coding scope
      vkCmdEndVideoCodingKHR()

6. Submit command buffer
   vkQueueSubmit()

7. Decoded picture available in VkImage
```

### Key Vulkan Video Structures

```cpp
// Decode operation
VkVideoDecodeInfoKHR {
    VkBuffer              srcBuffer;           // Bitstream
    VkDeviceSize          srcBufferOffset;
    VkDeviceSize          srcBufferRange;
    VkVideoPictureResourceInfoKHR dstPictureResource;  // Output
    VkVideoReferenceSlotInfoKHR*  pReferenceSlots;     // DPB refs
};

// H.264 specific
VkVideoDecodeH264PictureInfoKHR {
    StdVideoDecodeH264PictureInfo* pStdPictureInfo;
    uint32_t                       sliceCount;
    uint32_t*                      pSliceOffsets;
};

// Picture resource (decode target / reference)
VkVideoPictureResourceInfoKHR {
    VkImageView  imageViewBinding;
    VkOffset2D   codedOffset;
    VkExtent2D   codedExtent;
};
```

---

## 5. Google Codec 2.0 <a name="codec-2"></a>

**Note**: Codec 2.0 is primarily the **Android** codec framework, not directly used on Linux/ChromeOS. However, it represents Google's direction for codec abstraction.

For Chromium on Linux/ChromeOS, the relevant abstraction is:
- `VideoDecoderMixin` / `VideoDecoderPipeline` 
- NOT Android's Codec 2.0

The VA-API and V4L2 backends provide the model for adding Vulkan Video.

---

## 6. Integration Strategy <a name="integration-strategy"></a>

### Proposed Architecture

```
media/gpu/vulkan/
├── vulkan_video_decoder.h/.cc           # VideoDecoderMixin implementation
├── vulkan_video_decoder_delegate.h/.cc  # Base delegate class
├── vulkan_wrapper.h/.cc                 # Low-level Vulkan Video API wrapper
├── h264_vulkan_video_decoder_delegate.h/.cc
├── h265_vulkan_video_decoder_delegate.h/.cc
├── vp9_vulkan_video_decoder_delegate.h/.cc
├── av1_vulkan_video_decoder_delegate.h/.cc
├── BUILD.gn
└── DEPS
```

### Implementation Phases

#### Phase 1: Foundation
1. Create `VulkanWrapper` class (equivalent to `VaapiWrapper`)
   - Video session management
   - Buffer/memory management
   - Decode execution
   - DMABuf export

2. Create `VulkanVideoDecoder` class (equivalent to `VaapiVideoDecoder`)
   - Implement `VideoDecoderMixin` interface
   - Surface pool management
   - State machine

#### Phase 2: H.264 Decode
3. Create `H264VulkanVideoDecoderDelegate`
   - Reuse existing `H264Decoder` parser!
   - Implement `H264Accelerator` interface
   - Map VA-API structures to Vulkan Video structures

#### Phase 3: Additional Codecs
4. Implement H.265, VP9, AV1 delegates

#### Phase 4: Encoding (Future)
5. Add encode support using `VK_KHR_video_encode_queue`

### Key Mapping: VA-API → Vulkan Video

| VA-API | Vulkan Video |
|--------|--------------|
| `VADisplay` | `VkDevice` + video queue |
| `VAContext` | `VkVideoSessionKHR` |
| `VASurface` | `VkImage` + `VkImageView` |
| `VABuffer` (PicParams) | `VkVideoSessionParametersKHR` + inline structs |
| `VABuffer` (SliceData) | `VkBuffer` (bitstream) |
| `vaBeginPicture/vaRenderPicture/vaEndPicture` | `vkCmdBeginVideoCodingKHR/vkCmdDecodeVideoKHR/vkCmdEndVideoCodingKHR` |
| DMABuf export | `VK_EXT_external_memory_dma_buf` |

---

## 7. Key Files and Integration Points <a name="key-files"></a>

### Files to Study

| File | Importance | Notes |
|------|------------|-------|
| `media/gpu/vaapi/vaapi_wrapper.h` | ⭐⭐⭐⭐⭐ | Template for VulkanWrapper |
| `media/gpu/vaapi/vaapi_video_decoder.h` | ⭐⭐⭐⭐⭐ | Template for VulkanVideoDecoder |
| `media/gpu/vaapi/vaapi_video_decoder_delegate.h` | ⭐⭐⭐⭐⭐ | Base delegate pattern |
| `media/gpu/vaapi/h264_vaapi_video_decoder_delegate.h` | ⭐⭐⭐⭐⭐ | Codec-specific implementation |
| `media/gpu/h264_decoder.h` | ⭐⭐⭐⭐ | Reusable H.264 parser |
| `media/gpu/accelerated_video_decoder.h` | ⭐⭐⭐⭐ | Core interface |
| `media/gpu/chromeos/video_decoder_pipeline.h` | ⭐⭐⭐ | High-level integration |
| `media/gpu/BUILD.gn` | ⭐⭐⭐ | Build system integration |
| `gpu/vulkan/` | ⭐⭐⭐ | Existing Vulkan infra in Chromium |

### Build Integration Points

```gn
# media/gpu/BUILD.gn additions needed:
if (enable_vulkan_video) {
  sources += [
    "vulkan/vulkan_wrapper.cc",
    "vulkan/vulkan_wrapper.h",
    "vulkan/vulkan_video_decoder.cc",
    "vulkan/vulkan_video_decoder.h",
    # ... delegates
  ]
  deps += [
    "//gpu/vulkan",
    # Vulkan Video headers
  ]
}
```

---

## 8. Design Questions to Answer <a name="design-questions"></a>

### Memory Management
1. **Q**: How to share decoded frames between Vulkan and compositor?
   - **A**: Use `VK_EXT_external_memory_dma_buf` to export VkImage as DMABuf
   - Same pattern as VA-API → Chromium uses DMABuf for zero-copy

2. **Q**: How to handle DPB (Decoded Picture Buffer) for references?
   - **A**: Allocate VkImage pool for DPB slots, track reference states

### Threading Model
3. **Q**: Which thread runs decode commands?
   - **A**: GPU process, decoder task runner (same as VA-API)

4. **Q**: Async decode - how to signal completion?
   - **A**: Vulkan fences/semaphores → integrate with Chromium's task posting

### Platform Support
5. **Q**: Which platforms to support initially?
   - **A**: Linux first (easiest), then ChromeOS, Windows

6. **Q**: How to handle driver variations (NVIDIA, AMD, Intel)?
   - **A**: Abstract via VulkanWrapper, test on multiple vendors

### Feature Parity
7. **Q**: Protected content (DRM)?
   - **A**: Phase 2 - Vulkan protected memory extensions exist

8. **Q**: Which codecs first?
   - **A**: H.264 (most common), then H.265, VP9, AV1

### Build System
9. **Q**: How to enable/disable Vulkan Video?
   - **A**: GN flag `enable_vulkan_video`, feature flag at runtime

10. **Q**: Dependencies on Vulkan SDK version?
    - **A**: Require Vulkan 1.3+ with video extensions

---

## 9. References <a name="references"></a>

### Chromium Documentation
- [Chromium VA-API Documentation](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/docs/gpu/vaapi.md)
- [Chromium VideoNG Deep-Dive](https://developer.chrome.com/docs/chromium/videong)
- [Chromium Video Design Docs](https://www.chromium.org/developers/design-documents/video/)

### Vulkan Video
- [Khronos Vulkan Video Introduction](https://www.khronos.org/blog/an-introduction-to-vulkan-video)
- [Vulkan Video Specification](https://registry.khronos.org/vulkan/specs/1.3-extensions/man/html/VK_KHR_video_queue.html)
- [Vulkan Video Samples (GitHub)](https://github.com/KhronosGroup/Vulkan-Video-Samples)
- [Vulkan Video Core APIs Presentation (PDF)](https://vulkan.org/user/pages/09.events/vulkanised-2023/vulkanised_2023_vulkan_video_core_apis.pdf)

### Industry Collaboration
- [NVIDIA Looking to Support Vulkan Video on Chrome](https://www.phoronix.com/news/NVIDIA-Vulkan-Video-Chrome-Help)
- [NVIDIA Developer Forum Discussion](https://forums.developer.nvidia.com/t/nvidia-looking-to-support-vulkan-video-on-chrome/352840)

### VA-API Reference
- [VA-API GitHub](https://github.com/intel/libva)
- [VA-API Specification](https://intel.github.io/libva/)

---

## Next Steps

1. **[PENDING]** Run `install-build-deps.sh` to get Linux build dependencies
2. **[PENDING]** Run `gclient sync` to complete source fetch
3. **[PENDING]** Build Chromium to verify environment
4. Study `VaapiWrapper` implementation in detail
5. Prototype `VulkanWrapper` class
6. Create minimal H.264 decode proof-of-concept

---

*Report generated during Chromium source analysis*  
*Chromium source location: `/data/LinuxExtra/chrome/chromium/src`*

