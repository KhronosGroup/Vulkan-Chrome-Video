<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: 2026 The Khronos Group, Inc.
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
# Vulkan Video Integration into Chromium: Complete Design Document

<!-- DOC-STATUS -->
> **Status: CURRENT.** Design of record for the DECODE integration.
> Index: [`README.md`](README.md).


**Version**: 1.0  
**Date**: December 4, 2025  
**Authors**: Engineering Team  
**Status**: Draft  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Shared Codec Parsers - Deep Dive](#3-shared-codec-parsers)
4. [VA-API vs Vulkan Video Comparison](#4-api-comparison)
5. [DMA-BUF File Descriptor Management](#5-dmabuf-fd-management)
6. [Protected Content (DRM) Architecture](#6-protected-content)
7. [Synchronization Mechanisms](#7-synchronization)
8. [Frame Output and Display Pipeline](#8-frame-output)
9. [Implementation Plan](#9-implementation-plan)
10. [Test Plan](#10-test-plan)
11. [References and Links](#11-references)

---

## 1. Executive Summary <a name="1-executive-summary"></a>

### 1.1 Project Goal

Integrate Vulkan Video decode and encode extensions as a first-class hardware acceleration backend in Chromium, alongside existing VA-API support, to provide:

- Cross-platform hardware video acceleration
- Vendor-agnostic implementation
- Modern GPU integration with the Vulkan graphics pipeline
- Future-proof architecture for emerging codecs

### 1.2 Key Insight: Codec Parsers are Shared

**Critical discovery**: Chromium's architecture separates:
- **Codec Parsing** (shared) - in `media/parsers/` and `media/gpu/*_decoder.{h,cc}`
- **Hardware Acceleration** (per-backend) - in `media/gpu/vaapi/`, `media/gpu/v4l2/`

This means **Vulkan Video integration does NOT require reimplementing codec parsing**. However, significant work is still required:

**What we DON'T need to do:**
- Write bitstream parsers (NAL unit parsing, entropy decoding, etc.)
- Implement DPB management logic
- Handle reference frame tracking

**What we DO need to do:**
- Implement the `*Accelerator` interface for each codec
- **Write conversion code** from Chromium's parsed structures to Vulkan Video's `StdVideo*` structures
- This conversion is non-trivial: ~500-700 lines per codec

**Important Note on NVPRO Sample:** The official NVIDIA Vulkan Video sample (NVPRO) includes its own integrated parser. We **cannot** reuse this parser because Chromium's `*Accelerator` interface receives already-parsed Chromium structures, not raw bitstream. We must write the conversion layer:

| Codec | Chromium Structures | Vulkan Video Structures | Est. Conversion Code |
|-------|---------------------|------------------------|---------------------|
| H.264 | `H264SPS`, `H264PPS`, `H264SliceHeader` | `StdVideoH264*` | ~500 lines |
| H.265 | `H265VPS`, `H265SPS`, `H265PPS` | `StdVideoH265*` | ~700 lines |
| VP9 | `Vp9FrameHeader`, `Vp9SegmentationParams` | `StdVideoDecodeVP9*` | ~400 lines |
| AV1 | `libgav1::ObuSequenceHeader/FrameHeader` | `StdVideoAV1*` | ~600 lines |

**Total estimated conversion code: ~2,200 lines across 4 codecs.**

### 1.3 Scope

| Component | Status | Priority |
|-----------|--------|----------|
| Vulkan Video Decode (H.264) | New | P0 |
| Vulkan Video Decode (HEVC) | New | P0 |
| Vulkan Video Decode (VP9) | New | P1 |
| Vulkan Video Decode (AV1) | New | P1 |
| Vulkan Video Encode (H.264) | New | P2 |
| Protected Content | Future | P3 |

---

## 2. Architecture Overview <a name="2-architecture-overview"></a>

### 2.1 Current Chromium Media Pipeline

See diagram: [chromium_media_pipeline.svg](#diagram-media-pipeline)

### 2.2 Hardware Acceleration Backends

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        VideoDecoderPipeline                                  │
│                     (media/gpu/chromeos/video_decoder_pipeline.h)           │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│ VaapiVideo    │         │ V4L2Video     │         │ VulkanVideo   │
│ Decoder       │         │ Decoder       │         │ Decoder       │
│ (Linux/CrOS)  │         │ (ARM/CrOS)    │         │ (NEW)         │
└───────────────┘         └───────────────┘         └───────────────┘
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│  VaapiWrapper │         │  V4L2Device   │         │ VulkanWrapper │
│  (libva)      │         │  (kernel)     │         │ (libvulkan)   │
└───────────────┘         └───────────────┘         └───────────────┘
```

### 2.3 Class Hierarchy (Detailed)

```
                    ┌──────────────────────────┐
                    │     VideoDecoderMixin    │
                    │ (media/gpu/chromeos/     │
                    │  video_decoder_pipeline.h)│
                    └──────────────────────────┘
                                │
                                │ implements
                ┌───────────────┼───────────────┐
                ▼               ▼               ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│ VaapiVideoDecoder│ │ V4L2VideoDecoder │ │VulkanVideoDecoder│
│                  │ │                  │ │     (NEW)        │
└──────────────────┘ └──────────────────┘ └──────────────────┘
        │                                           │
        │ creates                                   │ creates
        ▼                                           ▼
┌──────────────────┐                     ┌──────────────────┐
│AcceleratedVideo  │◄────── shared ──────│AcceleratedVideo  │
│Decoder           │                     │Decoder           │
│(H264/VP9/AV1...) │                     │(same classes!)   │
└──────────────────┘                     └──────────────────┘
        │                                           │
        │ uses                                      │ uses
        ▼                                           ▼
┌──────────────────┐                     ┌──────────────────┐
│H264Vaapi         │                     │H264Vulkan        │
│VideoDecoder      │                     │VideoDecoder      │
│Delegate          │                     │Delegate (NEW)    │
└──────────────────┘                     └──────────────────┘
```

---

## 3. Shared Codec Parsers - Deep Dive <a name="3-shared-codec-parsers"></a>

### 3.1 Parser Location and Organization

All bitstream parsers are located in `media/parsers/`:

```
media/parsers/
├── h264_parser.{h,cc}      # H.264/AVC NAL unit parsing
├── h265_parser.{h,cc}      # H.265/HEVC NAL unit parsing  
├── h266_parser.{h,cc}      # H.266/VVC NAL unit parsing
├── vp8_parser.{h,cc}       # VP8 frame header parsing
├── vp9_parser.{h,cc}       # VP9 frame header parsing
├── ivf_parser.{h,cc}       # IVF container parsing
└── jpeg_parser.{h,cc}      # JPEG parsing
```

### 3.2 AcceleratedVideoDecoder Architecture

See diagram: [codec_parser_architecture.svg](#diagram-codec-parser)

The key abstraction is `AcceleratedVideoDecoder`:

```cpp
// media/gpu/accelerated_video_decoder.h

class AcceleratedVideoDecoder {
 public:
  // Set compressed bitstream data
  virtual void SetStream(int32_t id, const DecoderBuffer& decoder_buffer) = 0;
  
  // Decode next frame - returns DecodeResult
  virtual DecodeResult Decode() = 0;
  
  // Query decoded frame properties
  virtual gfx::Size GetPicSize() const = 0;
  virtual VideoCodecProfile GetProfile() const = 0;
  virtual uint8_t GetBitDepth() const = 0;
  virtual VideoChromaSampling GetChromaSampling() const = 0;
};
```

### 3.3 Codec-Specific Decoders (Parser + State Machine)

Each codec has a decoder class that:
1. **Parses** the bitstream using `media/parsers/*`
2. **Manages** DPB (Decoded Picture Buffer) state
3. **Calls** the Accelerator interface for HW operations

```cpp
// media/gpu/h264_decoder.h

class H264Decoder : public AcceleratedVideoDecoder {
 public:
  // The Accelerator interface - THIS is what backends implement
  class H264Accelerator {
   public:
    // Create a picture buffer for decoded output
    virtual scoped_refptr<H264Picture> CreateH264Picture() = 0;
    
    // Submit SPS/PPS for HW configuration
    virtual void ProcessSPS(const H264SPS* sps, base::span<const uint8_t> data);
    virtual void ProcessPPS(const H264PPS* pps, base::span<const uint8_t> data);
    
    // Submit frame decode parameters
    virtual Status SubmitFrameMetadata(
        const H264SPS* sps,
        const H264PPS* pps,
        const H264DPB& dpb,                    // Reference frames
        const H264Picture::Vector& ref_list_p0,
        const H264Picture::Vector& ref_list_b0,
        const H264Picture::Vector& ref_list_b1,
        scoped_refptr<H264Picture> pic) = 0;
    
    // Submit slice data
    virtual Status SubmitSlice(
        const H264PPS* pps,
        const H264SliceHeader* slice_hdr,
        const H264Picture::Vector& ref_pic_list0,
        const H264Picture::Vector& ref_pic_list1,
        scoped_refptr<H264Picture> pic,
        const uint8_t* data,
        size_t size,
        const std::vector<SubsampleEntry>& subsamples) = 0;
    
    // Execute decode operation
    virtual Status SubmitDecode(scoped_refptr<H264Picture> pic) = 0;
    
    // Output decoded picture
    virtual bool OutputPicture(scoped_refptr<H264Picture> pic) = 0;
  };
  
 private:
  H264Parser parser_;           // Shared parser from media/parsers/
  H264DPB dpb_;                 // DPB state management
  H264Accelerator* accelerator_; // HW backend (VA-API, Vulkan, etc.)
};
```

### 3.4 What VA-API Accelerator Implements

```cpp
// media/gpu/vaapi/h264_vaapi_video_decoder_delegate.h

class H264VaapiVideoDecoderDelegate 
    : public H264Decoder::H264Accelerator,
      public VaapiVideoDecoderDelegate {
 public:
  // Creates VA-API surface for decoded frame
  scoped_refptr<H264Picture> CreateH264Picture() override;
  
  // Fills VAPictureParameterBufferH264 and submits to VA-API
  Status SubmitFrameMetadata(...) override;
  
  // Fills VASliceParameterBufferH264 and submits slice data
  Status SubmitSlice(...) override;
  
  // Calls vaBeginPicture/vaRenderPicture/vaEndPicture
  Status SubmitDecode(scoped_refptr<H264Picture> pic) override;
};
```

### 3.5 What Vulkan Video Accelerator Will Implement

```cpp
// PROPOSED: media/gpu/vulkan/h264_vulkan_video_decoder_delegate.h

class H264VulkanVideoDecoderDelegate
    : public H264Decoder::H264Accelerator,
      public VulkanVideoDecoderDelegate {
 public:
  // Creates Vulkan Video picture resource
  scoped_refptr<H264Picture> CreateH264Picture() override;
  
  // Fills VkVideoDecodeH264PictureInfoKHR
  Status SubmitFrameMetadata(...) override;
  
  // Fills VkVideoDecodeH264SliceInfoKHR
  Status SubmitSlice(...) override;
  
  // Calls vkCmdDecodeVideoKHR
  Status SubmitDecode(scoped_refptr<H264Picture> pic) override;
};
```

### 3.6 Benefits of Shared Parser Architecture

| Aspect | Benefit |
|--------|---------|
| **Code Reuse** | Parsing code tested across multiple backends |
| **Consistency** | Same parsing behavior VA-API, V4L2, Vulkan |
| **Maintainability** | Parser fixes benefit all backends |
| **Testing** | Parser unit tests ensure correctness |
| **New Codecs** | Add parser once, use in all backends |

---

## 4. VA-API vs Vulkan Video Comparison <a name="4-api-comparison"></a>

### 4.1 API Philosophy Comparison

| Aspect | VA-API | Vulkan Video |
|--------|--------|--------------|
| **Design Goal** | Video-specific abstraction | Extension to graphics API |
| **Abstraction Level** | High-level (hides GPU details) | Low-level (explicit control) |
| **Memory Management** | Driver-managed | Application-managed |
| **Synchronization** | Implicit (mostly) | Explicit |
| **Cross-Platform** | Linux only | Cross-platform |
| **GPU Integration** | Separate from graphics | Native to Vulkan |

### 4.2 Decode Operation Comparison

See diagram: [decode_flow_comparison.svg](#diagram-decode-flow)

#### VA-API Decode Flow

```cpp
// 1. Create decode context
vaCreateContext(va_display, config_id, width, height, 
                VA_PROGRESSIVE, surfaces, num_surfaces, &context_id);

// 2. For each frame:
vaBeginPicture(va_display, context_id, target_surface);

// 3. Submit parameters (SPS/PPS/slice headers)
vaCreateBuffer(va_display, context_id, VAPictureParameterBufferType, ...);
vaCreateBuffer(va_display, context_id, VASliceParameterBufferType, ...);
vaCreateBuffer(va_display, context_id, VASliceDataBufferType, ...);
vaRenderPicture(va_display, context_id, buffers, num_buffers);

// 4. Execute decode
vaEndPicture(va_display, context_id);

// 5. Wait for completion (optional - often implicit)
vaSyncSurface(va_display, target_surface);

// 6. Export as DMA-BUF
vaExportSurfaceHandle(va_display, target_surface, 
                      VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2, ...);
```

#### Vulkan Video Decode Flow

```cpp
// 1. Create video session
VkVideoSessionCreateInfoKHR session_info = {...};
vkCreateVideoSessionKHR(device, &session_info, nullptr, &video_session);

// 2. Bind memory to session
vkBindVideoSessionMemoryKHR(device, video_session, ...);

// 3. Create video session parameters (SPS/PPS)
VkVideoSessionParametersCreateInfoKHR params_info = {...};
vkCreateVideoSessionParametersKHR(device, &params_info, nullptr, &params);

// 4. Begin video coding scope
VkVideoBeginCodingInfoKHR begin_info = {...};
vkCmdBeginVideoCodingKHR(command_buffer, &begin_info);

// 5. Decode frame
VkVideoDecodeInfoKHR decode_info = {
    .srcBuffer = bitstream_buffer,
    .srcBufferOffset = slice_offset,
    .srcBufferRange = slice_size,
    .dstPictureResource = {output_image, ...},
    .pSetupReferenceSlot = &setup_slot,
    .referenceSlotCount = ref_count,
    .pReferenceSlots = reference_slots,
};
vkCmdDecodeVideoKHR(command_buffer, &decode_info);

// 6. End video coding scope
vkCmdEndVideoCodingKHR(command_buffer, &end_info);

// 7. Submit command buffer with explicit sync
VkSubmitInfo submit_info = {...};
vkQueueSubmit(queue, 1, &submit_info, fence);
vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_MAX);

// 8. Export as DMA-BUF (if using external memory)
// Already handled via VK_EXT_external_memory_dma_buf
```

### 4.3 Memory and Resource Comparison

| Resource | VA-API | Vulkan Video |
|----------|--------|--------------|
| **Decode Target** | `VASurfaceID` | `VkImage` + `VkVideoSessionKHR` |
| **Parameter Buffers** | `VABufferID` | `VkVideoSessionParametersKHR` |
| **Bitstream Buffer** | `VASliceDataBufferType` | `VkBuffer` |
| **Reference Frames** | `VASurfaceID[]` in DPB | `VkVideoPictureResourceInfoKHR[]` |

### 4.4 Codec Parameter Mapping

#### H.264 Parameter Structures

| VA-API Structure | Vulkan Video Structure |
|------------------|------------------------|
| `VAPictureParameterBufferH264` | `VkVideoDecodeH264PictureInfoKHR` |
| `VASliceParameterBufferH264` | `VkVideoDecodeH264SliceInfoKHR` |
| `VAPictureH264` | `StdVideoDecodeH264ReferenceInfo` |
| N/A (in driver) | `StdVideoH264SequenceParameterSet` |
| N/A (in driver) | `StdVideoH264PictureParameterSet` |

#### Key Difference: SPS/PPS Handling

**VA-API**: Driver parses and manages SPS/PPS internally
```cpp
// Just submit raw NAL unit data
vaCreateBuffer(..., VASliceDataBufferType, sps_pps_data, ...);
```

**Vulkan Video**: Application provides parsed structures
```cpp
// Application must fill these structures
StdVideoH264SequenceParameterSet std_sps = {
    .profile_idc = sps->profile_idc,
    .level_idc = sps->level_idc,
    .seq_parameter_set_id = sps->seq_parameter_set_id,
    // ... all SPS fields
};
```

**Implication**: Chromium's existing parsers already extract all needed fields!

---

## 5. DMA-BUF File Descriptor Management <a name="5-dmabuf-fd-management"></a>

### 5.1 DMA-BUF Overview

DMA-BUF is a Linux kernel mechanism for sharing buffers between devices/processes:

See diagram: [dmabuf_architecture.svg](#diagram-dmabuf)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         User Space (Chromium)                           │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    DMA-BUF FD    ┌──────────────┐                    │
│  │   Decoder    │ ───────────────► │   Display    │                    │
│  │  (VA-API)    │                  │  Compositor  │                    │
│  └──────────────┘                  └──────────────┘                    │
│         │                                  │                            │
│         │ ioctl                           │ ioctl                       │
├─────────┼──────────────────────────────────┼────────────────────────────┤
│         ▼                                  ▼                            │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                         DMA-BUF Subsystem                        │   │
│  │                         (kernel driver)                          │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                    │                                    │
│                                    │ Physical Memory                    │
│                                    ▼                                    │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      GPU Video Memory                            │   │
│  │                    (same physical pages)                         │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                               Kernel Space                              │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.2 VA-API Surface Export

```cpp
// media/gpu/vaapi/vaapi_wrapper.cc

std::unique_ptr<NativePixmapDmaBuf>
VaapiWrapper::ExportVASurfaceAsNativePixmapDmaBufUnwrapped(
    VASurfaceID va_surface_id,
    const gfx::Size& size) {
  
  VADRMPRIMESurfaceDescriptor descriptor;
  
  // Export surface as DRM PRIME (DMA-BUF)
  VAStatus va_res = vaExportSurfaceHandle(
      va_display_,
      va_surface_id,
      VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2,  // DMA-BUF export
      VA_EXPORT_SURFACE_READ_ONLY |
      VA_EXPORT_SURFACE_SEPARATE_LAYERS,
      &descriptor);
  
  // descriptor now contains:
  // - descriptor.fourcc (DRM format)
  // - descriptor.width, descriptor.height
  // - descriptor.num_layers
  // - descriptor.layers[].drm_format
  // - descriptor.layers[].num_planes
  // - descriptor.objects[].fd  ← DMA-BUF file descriptor
  // - descriptor.objects[].size
  // - descriptor.layers[].offset[plane]
  // - descriptor.layers[].pitch[plane]
  
  // Create NativePixmapHandle from descriptor
  gfx::NativePixmapHandle handle;
  handle.modifier = descriptor.objects[0].drm_format_modifier;
  
  for (uint32_t layer = 0; layer < descriptor.num_layers; ++layer) {
    for (uint32_t plane = 0; plane < descriptor.layers[layer].num_planes; ++plane) {
      uint32_t object_idx = descriptor.layers[layer].object_index[plane];
      
      // Duplicate FD for safe ownership transfer
      base::ScopedFD dup_fd(HANDLE_EINTR(dup(descriptor.objects[object_idx].fd)));
      
      handle.planes.emplace_back(
          descriptor.layers[layer].pitch[plane],
          descriptor.layers[layer].offset[plane],
          descriptor.objects[object_idx].size,
          std::move(dup_fd));
    }
  }
  
  // Close original FDs (we have duplicates now)
  for (uint32_t i = 0; i < descriptor.num_objects; ++i) {
    close(descriptor.objects[i].fd);
  }
  
  return std::make_unique<gfx::NativePixmapDmaBuf>(size, format, std::move(handle));
}
```

### 5.3 Vulkan Video DMA-BUF Export

```cpp
// PROPOSED: media/gpu/vulkan/vulkan_wrapper.cc

std::unique_ptr<NativePixmapDmaBuf>
VulkanWrapper::ExportImageAsDmaBuf(VkImage image, const gfx::Size& size) {
  
  // Get memory requirements
  VkMemoryRequirements mem_reqs;
  vkGetImageMemoryRequirements(device_, image, &mem_reqs);
  
  // Allocate with external memory
  VkExportMemoryAllocateInfo export_info = {
      .sType = VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO,
      .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
  };
  
  VkMemoryAllocateInfo alloc_info = {
      .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
      .pNext = &export_info,
      .allocationSize = mem_reqs.size,
      .memoryTypeIndex = FindMemoryType(mem_reqs.memoryTypeBits),
  };
  
  VkDeviceMemory memory;
  vkAllocateMemory(device_, &alloc_info, nullptr, &memory);
  vkBindImageMemory(device_, image, memory, 0);
  
  // Export as DMA-BUF FD
  VkMemoryGetFdInfoKHR fd_info = {
      .sType = VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR,
      .memory = memory,
      .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
  };
  
  int dmabuf_fd;
  vkGetMemoryFdKHR(device_, &fd_info, &dmabuf_fd);
  
  // Create NativePixmapHandle
  gfx::NativePixmapHandle handle;
  
  // Query image layout for stride/offset
  VkImageSubresource subresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0};
  VkSubresourceLayout layout;
  vkGetImageSubresourceLayout(device_, image, &subresource, &layout);
  
  handle.planes.emplace_back(
      layout.rowPitch,   // stride
      layout.offset,     // offset
      layout.size,       // size
      base::ScopedFD(dmabuf_fd));
  
  return std::make_unique<gfx::NativePixmapDmaBuf>(size, format, std::move(handle));
}
```

### 5.4 DMA-BUF Import (for Compositor/Display)

```cpp
// Compositor imports DMA-BUF to create EGL/Vulkan texture

// EGL Import:
EGLImageKHR egl_image = eglCreateImageKHR(
    display, EGL_NO_CONTEXT,
    EGL_LINUX_DMA_BUF_EXT,
    nullptr,
    attribs);  // Contains DMA-BUF FD, format, stride, modifier

// Vulkan Import:
VkImportMemoryFdInfoKHR import_info = {
    .sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR,
    .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
    .fd = dmabuf_fd,  // Takes ownership!
};
```

### 5.5 Chromium Frame Resource Flow

See diagram: [frame_resource_flow.svg](#diagram-frame-flow)

```
┌─────────────────┐
│   VaapiVideo    │
│    Decoder      │
└────────┬────────┘
         │ Creates VASurfaceID
         ▼
┌─────────────────┐
│  VaapiWrapper:: │
│ExportVASurface  │
│AsNativePixmap   │
│    DmaBuf()     │
└────────┬────────┘
         │ Exports FD
         ▼
┌─────────────────┐
│NativePixmap     │
│FrameResource    │
│                 │
│ - pixmap_       │◄─── gfx::NativePixmapDmaBuf
│ - layout_       │     (owns DMA-BUF FDs)
│ - visible_rect_ │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│MailboxVideo    │
│FrameConverter   │
│                 │
│ Creates Shared  │
│ Image from      │
│ DMA-BUF         │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   VideoFrame    │
│                 │
│ STORAGE_OPAQUE  │
│ (SharedImage    │
│  backed)        │
└────────┬────────┘
         │
         ▼
    Compositor/
    Display
```

---

## 6. Protected Content (DRM) Architecture <a name="6-protected-content"></a>

### 6.1 DRM System Overview

See diagram: [drm_architecture.svg](#diagram-drm)

### 6.2 Protected Content Components

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Browser Process                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │   EME API    │───►│  CDM Host    │───►│   Widevine   │                   │
│  │ (JavaScript) │    │              │    │     CDM      │                   │
│  └──────────────┘    └──────────────┘    └──────────────┘                   │
│                              │                    │                          │
│                              │ License            │ Decryption               │
│                              │ Management         │ Keys                     │
│                              ▼                    ▼                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                            GPU Process                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │  MojoVideo   │───►│   Vaapi      │───►│  Protected   │                   │
│  │   Decoder    │    │ VideoDecoder │    │   Session    │                   │
│  │   Service    │    │  Delegate    │    │  (VA-API)    │                   │
│  └──────────────┘    └──────────────┘    └──────────────┘                   │
│                              │                    │                          │
│                              │ Encrypted          │ HW Key                   │
│                              │ Slices             │ Data                     │
│                              ▼                    ▼                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                          Hardware TEE                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Secure Video Path                                 │   │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐           │   │
│  │  │  Encrypted   │───►│   Secure     │───►│  Protected   │           │   │
│  │  │   Input      │    │   Decode     │    │   Output     │           │   │
│  │  └──────────────┘    └──────────────┘    └──────────────┘           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.3 ChromeOS Protected Session Flow

```cpp
// media/gpu/vaapi/vaapi_video_decoder_delegate.cc

VaapiVideoDecoderDelegate::ProtectedSessionState
VaapiVideoDecoderDelegate::SetupDecryptDecode(
    bool full_sample,
    size_t size,
    VAEncryptionParameters* crypto_params,
    std::vector<VAEncryptionSegmentInfo>* segments,
    const std::vector<SubsampleEntry>& subsamples) {
  
  // Step 1: Create protected session (first time)
  if (protected_session_state_ == ProtectedSessionState::kNotCreated) {
    // Get hardware configuration from CDM daemon
    chromeos_cdm_context_->GetHwConfigData(
        base::BindOnce(&VaapiVideoDecoderDelegate::OnGetHwConfigData, ...));
    return ProtectedSessionState::kInProcess;
  }
  
  // Step 2: Set encryption type
  const bool ctr = (encryption_scheme_ == EncryptionScheme::kCenc);
  crypto_params->encryption_type = ctr 
      ? VA_ENCRYPTION_TYPE_SUBSAMPLE_CTR 
      : VA_ENCRYPTION_TYPE_SUBSAMPLE_CBC;
  
  // Step 3: Get hardware key data for this content
  if (!hw_key_data_map_.contains(decrypt_config_->key_id())) {
    chromeos_cdm_context_->GetHwKeyData(
        decrypt_config_.get(), hw_identifier_,
        base::BindOnce(&VaapiVideoDecoderDelegate::OnGetHwKeyData, ...));
    return ProtectedSessionState::kInProcess;
  }
  
  // Step 4: Fill segment info for each subsample
  for (const auto& entry : subsamples) {
    VAEncryptionSegmentInfo segment_info = {};
    segment_info.segment_start_offset = offset;
    segment_info.segment_length = entry.clear_bytes + entry.cypher_bytes;
    segment_info.init_byte_length = entry.clear_bytes;
    memcpy(segment_info.aes_cbc_iv_or_ctr, 
           decrypt_config_->iv().data(), 16);
    
    // Update IV for CTR mode
    if (ctr && entry.cypher_bytes > 0) {
      ctr128_inc64(iv.data());  // Increment counter
    }
    
    segments->emplace_back(std::move(segment_info));
  }
  
  // Step 5: Copy wrapped key blob
  memcpy(crypto_params->wrapped_decrypt_blob,
         hw_key_data_map_[decrypt_config_->key_id()].data(), 16);
  
  return ProtectedSessionState::kCreated;
}
```

### 6.4 Encryption Parameters Structure

```cpp
// VA-API encryption parameters

typedef struct _VAEncryptionParameters {
    VAEncryptionType encryption_type;
    
    uint32_t num_segments;
    VAEncryptionSegmentInfo* segment_info;
    
    uint32_t blocks_stripe_encrypted;  // CBCS pattern
    uint32_t blocks_stripe_clear;      // CBCS pattern
    
    uint32_t key_blob_size;
    uint8_t wrapped_decrypt_blob[16];  // Hardware-wrapped key
    
} VAEncryptionParameters;

typedef struct _VAEncryptionSegmentInfo {
    uint32_t segment_start_offset;
    uint32_t segment_length;
    uint32_t partial_aes_block_size;
    uint32_t init_byte_length;         // Clear bytes at start
    uint8_t aes_cbc_iv_or_ctr[16];     // IV or counter
} VAEncryptionSegmentInfo;
```

### 6.5 Vulkan Video Protected Content

**📊 Diagram:** [Vulkan Protected Content Flow](diagrams/vulkan_protected_content.svg)

Vulkan has built-in protected memory infrastructure since Vulkan 1.1 that supports DRM workflows:

#### Protected Memory Features (Core Vulkan 1.1)

```cpp
// 1. Query protected memory support
VkPhysicalDeviceProtectedMemoryFeatures protected_features = {
    .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROTECTED_MEMORY_FEATURES,
};
vkGetPhysicalDeviceFeatures2(physical_device, &features2);
// Check: protected_features.protectedMemory == VK_TRUE

// 2. Create protected queue
VkDeviceQueueCreateInfo queue_info = {
    .flags = VK_DEVICE_QUEUE_CREATE_PROTECTED_BIT,
    .queueFamilyIndex = protected_queue_family,
    .queueCount = 1,
};

// 3. Create protected command pool
VkCommandPoolCreateInfo pool_info = {
    .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
    .flags = VK_COMMAND_POOL_CREATE_PROTECTED_BIT,
    .queueFamilyIndex = protected_queue_family,
};

// 4. Allocate protected memory for decode output
VkMemoryAllocateInfo alloc_info = {
    .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
    .allocationSize = image_requirements.size,
    .memoryTypeIndex = FindMemoryType(
        image_requirements.memoryTypeBits,
        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT |
        VK_MEMORY_PROPERTY_PROTECTED_BIT),  // Key: protected memory
};
```

#### Vulkan Video Protected Session

```cpp
// Create video session with protected content flag
VkVideoSessionCreateInfoKHR session_info = {
    .sType = VK_STRUCTURE_TYPE_VIDEO_SESSION_CREATE_INFO_KHR,
    .flags = VK_VIDEO_SESSION_CREATE_PROTECTED_CONTENT_BIT_KHR,
    .pVideoProfile = &video_profile,
    .pictureFormat = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM,
    .maxCodedExtent = {1920, 1080},
    .referencePictureFormat = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM,
    .maxDpbSlots = 17,
    .maxActiveReferencePictures = 16,
};
vkCreateVideoSessionKHR(device, &session_info, nullptr, &video_session);

// Submit decode on protected queue with protected command buffer
VkProtectedSubmitInfo protected_submit = {
    .sType = VK_STRUCTURE_TYPE_PROTECTED_SUBMIT_INFO,
    .protectedSubmit = VK_TRUE,
};
VkSubmitInfo submit_info = {
    .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
    .pNext = &protected_submit,
    .commandBufferCount = 1,
    .pCommandBuffers = &protected_cmd_buffer,  // From protected pool
};
vkQueueSubmit(protected_queue, 1, &submit_info, fence);
```

#### Protected Content Flow in Chromium with Vulkan

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Browser Process                                 │
│  CDM (Widevine) → License negotiation → Hardware-wrapped keys       │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ Encrypted bitstream + wrapped key info
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       GPU Process                                    │
│  VulkanVideoDecoder with VK_VIDEO_SESSION_CREATE_PROTECTED_CONTENT  │
│  - Protected VkCommandBuffer                                         │
│  - Protected VkQueue                                                 │
│  - Output to VkImage with VK_MEMORY_PROPERTY_PROTECTED_BIT          │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ Hardware TEE decrypts during decode
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Hardware Security                                 │
│  - Key unwrap in secure enclave                                     │
│  - Decrypt + decode in single HW operation                          │
│  - Clear pixels ONLY in protected memory                            │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ Protected memory → HDCP
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Display Output                                  │
│  HDCP-encrypted link to display                                     │
└─────────────────────────────────────────────────────────────────────┘
```

#### Vulkan Video DRM Architecture (Simpler than VA-API)

Unlike VA-API where the driver performs decrypt-during-decode, Vulkan can use a cleaner architecture:

```
┌──────────────────────────────────────────────────────────────────────┐
│  CDM / Protected Process                                              │
│  1. Receive encrypted stream                                          │
│  2. Decrypt slices → Protected VkBuffer (decrypted bitstream)        │
│     - CDM handles all crypto                                          │
│     - Decrypted NAL units written to protected memory                │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              │ Protected VkBuffer (clear bitstream)
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Vulkan Video Decoder                                                 │
│  - Uses protected queue + protected command buffer                    │
│  - srcBuffer = protected VkBuffer (decrypted slices)                 │
│  - dstPictureResource = protected VkImage                            │
│  - Decoder is crypto-agnostic! Just decodes from protected buffer.   │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              │ Protected VkImage (decoded frame)
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Compositor / Display                                                 │
│  - Protected queue for composition (or direct overlay scanout)       │
│  - HDCP-encrypted output to display                                  │
└──────────────────────────────────────────────────────────────────────┘
```

**Key advantages of this approach:**

1. **No special decoder integration**: The Vulkan Video decoder doesn't need
   `VAEncryptionParameters`-style crypto parameters. It just decodes.

2. **DRM boundary is protected memory**: Security is enforced by the protected
   memory type, not by the decode operation.

3. **CDM handles all crypto**: Decryption happens in the CDM/protected process
   before the bitstream reaches the decoder.

4. **Simpler implementation**: No need for vendor-specific key injection extensions.

```cpp
// CDM decrypts into protected buffer
VkBufferCreateInfo buffer_info = {...};
vkCreateBuffer(device, &buffer_info, nullptr, &protected_bitstream);

VkMemoryAllocateInfo alloc = {
    .memoryTypeIndex = FindMemoryType(VK_MEMORY_PROPERTY_PROTECTED_BIT),
};
vkAllocateMemory(device, &alloc, nullptr, &protected_memory);
vkBindBufferMemory(device, protected_bitstream, protected_memory, 0);

// CDM writes decrypted NAL units to protected_bitstream
// (via protected mapping or copy operation)

// Decoder just decodes - no crypto awareness
VkVideoDecodeInfoKHR decode_info = {
    .srcBuffer = protected_bitstream,      // Decrypted slices in protected memory
    .srcBufferOffset = slice_offset,
    .srcBufferRange = slice_size,
    .dstPictureResource = protected_output, // Protected VkImage
};
vkCmdDecodeVideoKHR(protected_cmd_buffer, &decode_info);
```

**Note**: This architecture means Vulkan Video protected content support is actually
*easier* to implement than VA-API's decrypt-during-decode approach, as long as the
CDM can write to protected Vulkan buffers.

#### Future Enhancement: Native Decrypt-During-Decode

**📊 Diagram:** [Future Vulkan Video with Native Decryption](diagrams/vulkan_protected_content_future.svg)

While the protected buffer approach works well, it would be desirable for Vulkan Video
to add native support for decryption keys, similar to VA-API's `VAEncryptionParameters`:

```cpp
// Hypothetical future Vulkan Video extension
VkVideoDecodeEncryptionInfoKHR encryption_info = {
    .sType = VK_STRUCTURE_TYPE_VIDEO_DECODE_ENCRYPTION_INFO_KHR,
    .encryptionScheme = VK_VIDEO_ENCRYPTION_SCHEME_CENC_CTR_KHR,
    .pKeyData = wrapped_key_blob,
    .keyDataSize = 16,
    .pIV = initialization_vector,
    .ivSize = 16,
    .pSubsampleInfo = subsample_entries,
    .subsampleCount = num_subsamples,
};

VkVideoDecodeInfoKHR decode_info = {
    .pNext = &encryption_info,  // Attach encryption params
    .srcBuffer = encrypted_bitstream,
    .dstPictureResource = protected_output,
};
```

**Benefits of native decrypt-during-decode:**

1. **Single hardware operation**: Decrypt and decode in one pass, avoiding intermediate buffers
2. **Keys never leave TEE**: Hardware-wrapped keys go directly to secure enclave
3. **Reduced memory bandwidth**: No separate decrypt step writing decrypted bitstream
4. **Parity with VA-API**: Easier porting of existing DRM implementations
5. **Simpler CDM integration**: CDM only provides keys, doesn't need to handle decryption

This would be a valuable addition to the Vulkan Video extension family
(e.g., `VK_KHR_video_decode_encryption` or similar).

---

## 7. Synchronization Mechanisms <a name="7-synchronization"></a>

### 7.1 VA-API Synchronization

```cpp
// Implicit sync (most common on Intel)
vaEndPicture(display, context);  // Implicitly syncs

// Explicit sync (required for multi-threaded scenarios)
vaSyncSurface(display, surface_id);
VAStatus status = vaSyncSurface(va_display_, target_surface);
// Blocks until decode completes

// Sync before export
// NOTE: vaExportSurfaceHandle may not require explicit sync
// because mapping operations provide synchronization
```

### 7.2 Vulkan Video Synchronization

```cpp
// Explicit fence-based sync
VkFence decode_fence;
vkCreateFence(device, &fence_info, nullptr, &decode_fence);

VkSubmitInfo submit = {
    .commandBufferCount = 1,
    .pCommandBuffers = &cmd_buffer,
};
vkQueueSubmit(decode_queue, 1, &submit, decode_fence);

// Wait for decode completion
vkWaitForFences(device, 1, &decode_fence, VK_TRUE, UINT64_MAX);

// Semaphore-based sync (for queue transitions)
VkSemaphore decode_complete;
VkSubmitInfo decode_submit = {
    .signalSemaphoreCount = 1,
    .pSignalSemaphores = &decode_complete,
};

VkSubmitInfo render_submit = {
    .waitSemaphoreCount = 1,
    .pWaitSemaphores = &decode_complete,
    .pWaitDstStageMask = &wait_stage,
};
```

### 7.3 Cross-API Synchronization (Decode → Display)

See diagram: [sync_timeline.svg](#diagram-sync)

```
Timeline:
─────────────────────────────────────────────────────────────────────────►
                                                                     time
  Decode Queue                    Graphics Queue
  ────────────                    ──────────────
       │                               │
       │ vkCmdDecodeVideoKHR()         │
       │◄─────────────────────►        │
       │      Decode                   │
       │         │                     │
       │         │ Signal Semaphore    │
       │         ▼                     │
       ╳═════════╳══════════════════►  │ Wait Semaphore
                                       │
                                       │ vkCmdBlitImage()
                                       │◄──────────────►
                                       │    Render
                                       │
                                       ▼
                                   Display
```

### 7.4 DMA-BUF Synchronization

```cpp
// Implicit sync via DMA-BUF
// When exporting/importing DMA-BUF, the kernel ensures
// proper synchronization between producer and consumer

// Explicit sync (Linux 4.9+)
#include <linux/sync_file.h>

// Export sync file from DMA-BUF
int sync_fd;
ioctl(dmabuf_fd, DMA_BUF_IOCTL_EXPORT_SYNC_FILE, &sync_fd);

// Import and wait
ioctl(dmabuf_fd, DMA_BUF_IOCTL_IMPORT_SYNC_FILE, &sync_fd);
```

---

## 8. Frame Output and Display Pipeline <a name="8-frame-output"></a>

### 8.1 Complete Frame Flow

See diagram: [complete_frame_flow.svg](#diagram-complete-flow)

### 8.2 FrameResource and VideoFrame Conversion

```cpp
// media/gpu/chromeos/native_pixmap_frame_resource.h

class NativePixmapFrameResource : public FrameResource {
 public:
  // Create from DMA-BUF file descriptors
  static scoped_refptr<NativePixmapFrameResource> Create(
      const VideoFrameLayout& layout,
      const gfx::Rect& visible_rect,
      const gfx::Size& natural_size,
      std::vector<base::ScopedFD> dmabuf_fds,  // Takes ownership
      base::TimeDelta timestamp);
  
  // Get underlying DMA-BUF FDs
  size_t NumDmabufFds() const;
  int GetDmabufFd(size_t i) const;
  
  // Convert to VideoFrame for display
  scoped_refptr<VideoFrame> CreateDmabufVideoFrame() const;
  
 private:
  scoped_refptr<const gfx::NativePixmapDmaBuf> pixmap_;
};
```

### 8.3 SharedImage Creation (GPU Process)

```cpp
// media/gpu/chromeos/mailbox_video_frame_converter.cc

void MailboxVideoFrameConverter::ConvertFrame(
    scoped_refptr<FrameResource> frame_resource) {
  
  auto* native_pixmap = frame_resource->AsNativePixmapFrameResource();
  auto pixmap = native_pixmap->GetNativePixmapDmaBuf();
  
  // Create SharedImage from DMA-BUF
  gpu::SharedImageInterface* sii = GetSharedImageInterface();
  
  gpu::Mailbox mailbox = sii->CreateSharedImage(
      si_format,
      frame_resource->coded_size(),
      frame_resource->ColorSpace(),
      kTopLeft_GrSurfaceOrigin,
      kPremul_SkAlphaType,
      gpu::SHARED_IMAGE_USAGE_DISPLAY |
      gpu::SHARED_IMAGE_USAGE_SCANOUT,
      "VideoDecoder",
      pixmap->CloneHandleForIPC());  // Shares DMA-BUF
  
  // Create VideoFrame wrapping SharedImage
  scoped_refptr<VideoFrame> video_frame = VideoFrame::WrapExternalGpuMemoryBuffer(
      visible_rect,
      natural_size,
      std::make_unique<GpuMemoryBufferHandleHolder>(
          pixmap->CreateGpuMemoryBufferHandle()),
      mailbox,
      release_cb,
      timestamp);
  
  output_cb_.Run(std::move(video_frame));
}
```

### 8.4 Overlay Display Path

```cpp
// Overlay scanout (zero-copy display)

// 1. Video frame has SCANOUT usage
video_frame->metadata().allow_overlay = true;

// 2. Compositor checks overlay capability
if (CanPresentAsOverlay(video_frame)) {
  // Direct scanout - no GPU composition needed
  overlay_processor_->ProcessForOverlay(video_frame);
} else {
  // GPU composition required
  compositor_->CompositeLayers();
}

// 3. DRM/KMS overlay plane assignment
drmModeSetPlane(drm_fd, plane_id, crtc_id, 
                video_frame->GetDmaBufFd(),
                0,  // flags
                crtc_x, crtc_y, crtc_w, crtc_h,
                src_x, src_y, src_w, src_h);
```

---

## 9. Implementation Plan <a name="9-implementation-plan"></a>

### 9.1 Phase 1: Foundation (4 weeks)

| Task | Description | Files |
|------|-------------|-------|
| VulkanWrapper | Core Vulkan Video abstraction | `vulkan_wrapper.{h,cc}` |
| VulkanVideoDecoder | VideoDecoderMixin implementation | `vulkan_video_decoder.{h,cc}` |
| VulkanVideoDecoderDelegate | Base delegate class | `vulkan_video_decoder_delegate.{h,cc}` |
| Build Integration | GN files, feature flags | `BUILD.gn`, `media_switches.h` |

### 9.2 Phase 2: H.264 Decode (3 weeks)

| Task | Description | Files |
|------|-------------|-------|
| H264VulkanDelegate | H.264 accelerator implementation | `h264_vulkan_video_decoder_delegate.{h,cc}` |
| Parameter Translation | H264SPS/PPS → StdVideoH264* | In delegate |
| DPB Management | Reference frame handling | In delegate |
| Testing | Verify against VA-API output | Test harness |

### 9.3 Phase 3: Additional Codecs (6 weeks)

| Codec | Priority | Effort |
|-------|----------|--------|
| HEVC | P0 | 2 weeks |
| VP9 | P1 | 2 weeks |
| AV1 | P1 | 2 weeks |

### 9.4 Phase 4: Integration & Polish (2 weeks)

| Task | Description |
|------|-------------|
| Fallback Logic | VA-API fallback when Vulkan unavailable |
| Performance Tuning | Memory pooling, async operations |
| Error Handling | Robust error recovery |
| Documentation | Design docs, code comments |

### 9.5 Directory Structure

```
media/gpu/vulkan/
├── BUILD.gn
├── DEPS
├── OWNERS
├── vulkan_wrapper.h
├── vulkan_wrapper.cc
├── vulkan_video_decoder.h
├── vulkan_video_decoder.cc
├── vulkan_video_decoder_delegate.h
├── vulkan_video_decoder_delegate.cc
├── h264_vulkan_video_decoder_delegate.h
├── h264_vulkan_video_decoder_delegate.cc
├── hevc_vulkan_video_decoder_delegate.h
├── hevc_vulkan_video_decoder_delegate.cc
├── vp9_vulkan_video_decoder_delegate.h
├── vp9_vulkan_video_decoder_delegate.cc
├── av1_vulkan_video_decoder_delegate.h
├── av1_vulkan_video_decoder_delegate.cc
└── test/
    ├── vulkan_video_decoder_unittest.cc
    └── vulkan_wrapper_unittest.cc
```

---

## 10. Test Plan <a name="10-test-plan"></a>

### 10.1 Test Infrastructure

Chromium's existing test framework will be reused:

| Component | Location | Purpose |
|-----------|----------|---------|
| Test Binaries | `media/gpu/test/` | Decode/encode tests |
| Test Data | `media/test/data/` | Test videos |
| Validators | `video_frame_validator.cc` | Frame comparison |
| Harness | `video_player_test_environment.cc` | Test environment |

### 10.2 Test Categories

#### Unit Tests

```cpp
// vulkan_wrapper_unittest.cc
TEST_F(VulkanWrapperTest, CreateVideoSession_H264) {
  auto wrapper = VulkanWrapper::Create();
  ASSERT_TRUE(wrapper);
  
  VkVideoSessionKHR session;
  EXPECT_TRUE(wrapper->CreateVideoSession(
      VK_VIDEO_CODEC_OPERATION_DECODE_H264_BIT_KHR,
      gfx::Size(1920, 1080),
      &session));
}

TEST_F(VulkanWrapperTest, AllocateVideoSessionMemory) {...}
TEST_F(VulkanWrapperTest, CreateSessionParameters_H264) {...}
TEST_F(VulkanWrapperTest, ExportImageAsDmaBuf) {...}
```

#### Integration Tests

```cpp
// vulkan_video_decoder_unittest.cc
TEST_F(VulkanVideoDecoderTest, DecodeH264_BasicStream) {
  auto decoder = CreateVulkanVideoDecoder();
  auto bitstream = LoadTestVideo("bear-1280x720.h264");
  
  decoder->Initialize(config, ...);
  
  std::vector<scoped_refptr<VideoFrame>> frames;
  DecodeAllFrames(decoder, bitstream, &frames);
  
  EXPECT_EQ(frames.size(), expected_frame_count);
  ValidateFrames(frames, expected_checksums);
}
```

#### End-to-End Tests

```bash
# Using existing test binary
./video_decode_accelerator_tests \
  --test_video_data=bear-1280x720.h264 \
  --implementation=vulkan \
  --gtest_filter="*H264*"
```

### 10.3 Test Matrix

| Codec | Resolution | Bit Depth | Test Files |
|-------|------------|-----------|------------|
| H.264 Baseline | 320x240 | 8 | `bear-320x192-baseline-*.h264` |
| H.264 Main | 720p | 8 | `bear-1280x720.h264` |
| H.264 High | 1080p | 8 | Various |
| H.264 High10 | 720p | 10 | `bear-320x180-10bit-*.h264` |
| HEVC Main | 720p | 8 | `bear-1280x720-hevc.mp4` |
| HEVC Main10 | 720p | 10 | `bear-1280x720-hevc-10bit.mp4` |
| VP9 Profile 0 | Various | 8 | `bear-*.webm` |
| VP9 Profile 2 | 720p | 10 | `bear-320x180-hi10p-vp9.webm` |
| AV1 Main | Various | 8 | `av1-*.ivf` |
| AV1 Main | Various | 10 | `bear-av1-320x180-10bit.mp4` |

### 10.4 Performance Tests

```bash
# Decode performance
./video_decode_accelerator_perf_tests \
  --test_video_data=4k_h264_video.mp4 \
  --implementation=vulkan \
  --output_folder=/tmp/perf_results

# Metrics collected:
# - Decode latency (per frame)
# - Throughput (frames/second)
# - Memory usage
# - GPU utilization
```

### 10.5 Comparison Tests

| Test | Purpose |
|------|---------|
| VA-API vs Vulkan | Bit-exact output comparison |
| SW vs Vulkan | Reference comparison |
| Cross-vendor | Consistency across GPUs |

```bash
# Compare outputs
./compare_decoder_outputs \
  --reference=vaapi \
  --test=vulkan \
  --test_video=bear.h264 \
  --tolerance=0  # Bit-exact
```

### 10.6 DRM Test Plan

| Test Case | Description | Status |
|-----------|-------------|--------|
| Clear Key | Basic encrypted decode | Future |
| Widevine L1 | Hardware-level security | Future |
| Key Rotation | Mid-stream key change | Future |
| Error Recovery | Handle decryption failures | Future |

### 10.7 Automated Testing

```yaml
# Tast test configuration (ChromeOS)
name: video.VulkanVideoDecodeH264
attr: informational
deps:
  - vulkan_video
params:
  - codec: h264
    file: bear-1280x720.h264
  - codec: h264
    file: bear-320x180-10bit.h264
```

### 10.8 Test Data Sources

| Source | URL | Content |
|--------|-----|---------|
| Chromium | `media/test/data/` | Bear videos, test patterns |
| WebM | [webmproject.org](https://www.webmproject.org/vp9/levels/) | VP9 conformance vectors |
| HEVC | [hevc.hhi.fraunhofer.de](https://hevc.hhi.fraunhofer.de/) | HEVC conformance streams |
| AV1 | [aomedia.org](https://aomedia.org/av1-features/get-started/) | AV1 test vectors |

---

## 11. References and Links <a name="11-references"></a>

### 11.1 VA-API Documentation

| Resource | URL |
|----------|-----|
| VA-API Specification | https://github.com/intel/libva/wiki |
| VA-API GitHub | https://github.com/intel/libva |
| VA-API Headers | https://github.com/intel/libva/tree/master/va |
| Intel Media Driver | https://github.com/intel/media-driver |

### 11.2 Vulkan Video Documentation

| Resource | URL |
|----------|-----|
| Vulkan Video Spec | https://registry.khronos.org/vulkan/specs/1.3-extensions/html/chap45.html |
| VK_KHR_video_queue | https://registry.khronos.org/vulkan/specs/1.3-extensions/man/html/VK_KHR_video_queue.html |
| VK_KHR_video_decode_queue | https://registry.khronos.org/vulkan/specs/1.3-extensions/man/html/VK_KHR_video_decode_queue.html |
| VK_KHR_video_decode_h264 | https://registry.khronos.org/vulkan/specs/1.3-extensions/man/html/VK_KHR_video_decode_h264.html |
| VK_KHR_video_decode_h265 | https://registry.khronos.org/vulkan/specs/1.3-extensions/man/html/VK_KHR_video_decode_h265.html |
| VK_KHR_video_decode_av1 | https://registry.khronos.org/vulkan/specs/1.3-extensions/man/html/VK_KHR_video_decode_av1.html |
| Vulkan Video Samples | https://github.com/KhronosGroup/Vulkan-Samples |

### 11.3 Chromium Documentation

| Resource | URL |
|----------|-----|
| Chromium Media | https://chromium.googlesource.com/chromium/src/+/main/media/README.md |
| GPU Video Accel | https://chromium.googlesource.com/chromium/src/+/main/media/gpu/README.md |
| VA-API in Chrome | https://chromium.googlesource.com/chromium/src/+/main/docs/gpu/vaapi.md |
| Video Test Suite | https://chromium.googlesource.com/chromium/src/+/main/media/gpu/test/README.md |

### 11.4 DRM/EME Documentation

| Resource | URL |
|----------|-----|
| EME Spec (W3C) | https://www.w3.org/TR/encrypted-media/ |
| Widevine | https://www.widevine.com/solutions/widevine-drm |
| Chrome EME | https://www.chromium.org/audio-video/eme-quick-start/ |

### 11.5 DMA-BUF Documentation

| Resource | URL |
|----------|-----|
| DMA-BUF Kernel Doc | https://www.kernel.org/doc/html/latest/driver-api/dma-buf.html |
| GBM Documentation | https://gitlab.freedesktop.org/mesa/mesa/-/tree/main/src/gbm |
| Vulkan External Memory | https://registry.khronos.org/vulkan/specs/1.3-extensions/man/html/VK_EXT_external_memory_dma_buf.html |

### 11.6 Test Resources

| Resource | URL |
|----------|-----|
| Chromium Test Data | https://chromium.googlesource.com/chromium/src/+/main/media/test/data/ |
| VP9 Test Vectors | https://storage.googleapis.com/aspect-ratio-test-vectors/ |
| AV1 Test Vectors | https://github.com/AomediaResearch/av1-spec |
| Tast Tests | https://chromium.googlesource.com/chromiumos/platform/tast-tests/ |

---

## Appendix A: SVG Diagrams

### Diagram: Media Pipeline <a name="diagram-media-pipeline"></a>

See file: [`diagrams/chromium_media_pipeline.svg`](diagrams/chromium_media_pipeline.svg)

### Diagram: Codec Parser Architecture <a name="diagram-codec-parser"></a>

See file: [`diagrams/codec_parser_architecture.svg`](diagrams/codec_parser_architecture.svg)

### Diagram: Decode Flow Comparison <a name="diagram-decode-flow"></a>

See file: [`diagrams/decode_flow_comparison.svg`](diagrams/decode_flow_comparison.svg)

### Diagram: DMA-BUF Architecture <a name="diagram-dmabuf"></a>

See file: [`diagrams/dmabuf_architecture.svg`](diagrams/dmabuf_architecture.svg)

### Diagram: DRM Architecture (VA-API) <a name="diagram-drm"></a>

See file: [`diagrams/drm_architecture.svg`](diagrams/drm_architecture.svg)

### Diagram: Vulkan Protected Content <a name="diagram-vulkan-protected"></a>

See file: [`diagrams/vulkan_protected_content.svg`](diagrams/vulkan_protected_content.svg)

### Diagram: Vulkan Protected Content (Future) <a name="diagram-vulkan-protected-future"></a>

See file: [`diagrams/vulkan_protected_content_future.svg`](diagrams/vulkan_protected_content_future.svg)

### Diagram: Sync Timeline <a name="diagram-sync"></a>

See file: [`diagrams/sync_timeline.svg`](diagrams/sync_timeline.svg)

### Diagram: Frame Flow <a name="diagram-frame-flow"></a>

See file: [`diagrams/frame_resource_flow.svg`](diagrams/frame_resource_flow.svg)

### Diagram: Complete Flow <a name="diagram-complete-flow"></a>

See file: [`diagrams/complete_frame_flow.svg`](diagrams/complete_frame_flow.svg)

---

## HTML Version

For a consolidated view with embedded diagrams, see: [`vulkan_video_integration.html`](vulkan_video_integration.html)

---

*Document generated from Chromium source analysis*  
*Chromium Version: HEAD (as of December 2025)*

