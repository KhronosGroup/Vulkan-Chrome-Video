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
# VA-API vs Vulkan Video: Detailed Technical Comparison for Chromium Integration

<!-- DOC-STATUS -->
> **Status: CURRENT.** Comparison against the VA-API path.
> Index: [`README.md`](README.md).


**Date**: December 4, 2025  
**Purpose**: Understand API differences to guide Vulkan Video integration into Chromium

---

## Table of Contents
1. [API Architecture Comparison](#api-architecture)
2. [Feature Matrix](#feature-matrix)
3. [Surface/Buffer Management](#surface-management)
4. [DMABuf Export/Import](#dmabuf)
5. [Synchronization Mechanisms](#synchronization)
6. [Protected Content (DRM)](#protected-content)
7. [Frame Output Flow in Chromium](#frame-output)
8. [Overlays](#overlays)
9. [Code Mapping: VA-API → Vulkan Video](#code-mapping)
10. [Integration Challenges](#challenges)

---

> **Scope.** This document compares what the two APIs **do**. It does not cover how they stay
> **compatible** across versions — the handle model, struct extension and ABI strategy. For that,
> and for what it implies about this project's own encoder interface, see
> `VULKAN_VIDEO_ENCODER_INTERFACE_STYLE.md` §4b.

## 1. API Architecture Comparison <a name="api-architecture"></a>

### Abstraction Level

| Aspect | VA-API | Vulkan Video |
|--------|--------|--------------|
| **Level** | High-level, driver-managed | Low-level, explicit control |
| **Resource Management** | Driver handles internally | Application manages explicitly |
| **Synchronization** | Implicit (mostly) | Explicit (fences, semaphores) |
| **Buffer Allocation** | Driver allocates | Application allocates |
| **Memory Binding** | Automatic | Explicit binding required |
| **Error Handling** | Simple return codes | Detailed validation layers |

### Initialization Sequence

#### VA-API Initialization (from `vaapi_wrapper.cc`)

```cpp
// 1. Open DRM device
base::File drm_file(dev_path, base::File::FLAG_OPEN | FLAG_READ | FLAG_WRITE);

// 2. Get VA Display from DRM FD
VADisplay va_display = vaGetDisplayDRM(drm_fd.get());

// 3. Initialize VA-API
int major_version, minor_version;
VAStatus va_res = vaInitialize(va_display, &major_version, &minor_version);

// 4. Create config for profile/entrypoint
VAConfigID va_config_id;
vaCreateConfig(va_display, va_profile, va_entrypoint, attribs, &va_config_id);

// 5. Create context
VAContextID va_context_id;
vaCreateContext(va_display, va_config_id, width, height, 
                VA_PROGRESSIVE, surfaces, num_surfaces, &va_context_id);
```

#### Vulkan Video Initialization (equivalent)

```cpp
// 1. Create Vulkan instance and device (existing Chromium infra)
// 2. Query video capabilities
VkVideoCapabilitiesKHR video_caps = {};
vkGetPhysicalDeviceVideoCapabilitiesKHR(physical_device, &video_profile, &video_caps);

// 3. Create video session
VkVideoSessionKHR video_session;
VkVideoSessionCreateInfoKHR session_info = {
    .sType = VK_STRUCTURE_TYPE_VIDEO_SESSION_CREATE_INFO_KHR,
    .pVideoProfile = &video_profile,
    .pictureFormat = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM,  // NV12
    .maxCodedExtent = {width, height},
    .maxDpbSlots = num_dpb_slots,
    .maxActiveReferencePictures = max_refs,
};
vkCreateVideoSessionKHR(device, &session_info, nullptr, &video_session);

// 4. Allocate and bind session memory
uint32_t mem_req_count;
vkGetVideoSessionMemoryRequirementsKHR(device, video_session, &mem_req_count, nullptr);
// ... allocate and bind memory for each requirement ...

// 5. Create session parameters (SPS/PPS container)
VkVideoSessionParametersKHR session_params;
vkCreateVideoSessionParametersKHR(device, &params_info, nullptr, &session_params);
```

### Key Structural Differences

| VA-API | Vulkan Video |
|--------|--------------|
| `VADisplay` (singleton) | `VkDevice` + video queue family |
| `VAContext` | `VkVideoSessionKHR` |
| `VASurface` | `VkImage` + `VkImageView` |
| `VABuffer` (params) | Inline structs in command buffer |
| `VABuffer` (bitstream) | `VkBuffer` |
| N/A | `VkVideoSessionParametersKHR` (SPS/PPS) |

---

## 2. Feature Matrix <a name="feature-matrix"></a>

### Parsing Levels

| Feature | VA-API | Vulkan Video |
|---------|--------|--------------|
| **Bitstream parsing** | Application (Chromium) | Application |
| **SPS/PPS parsing** | Application | Application |
| **Slice header parsing** | Application | Application |
| **Picture parameter assembly** | Driver consumes VA structs | Driver consumes Std structs |
| **Reference list management** | Application + some driver help | Application |
| **DPB management** | Application | Application |

**Key Point**: Both APIs are "stateless" - Chromium does all parsing using `H264Decoder`, `H265Decoder`, etc. The hardware only does the actual decode.

### Codec Support

| Codec | VA-API | Vulkan Video | Notes |
|-------|--------|--------------|-------|
| H.264/AVC | ✅ Decode + Encode | ✅ Decode + Encode | `VK_KHR_video_decode_h264` |
| H.265/HEVC | ✅ Decode + Encode | ✅ Decode + Encode | `VK_KHR_video_decode_h265` |
| VP8 | ✅ Decode + Encode | ❌ Not available | No Vulkan extension |
| VP9 | ✅ Decode + Encode | ⏳ Coming | Provisional extension |
| AV1 | ✅ Decode + Encode | ✅ Decode | `VK_KHR_video_decode_av1` |

### Profile Support (H.264 Example)

| H.264 Profile | VA-API | Vulkan Video |
|---------------|--------|--------------|
| Baseline | `VAProfileH264Baseline` | `STD_VIDEO_H264_PROFILE_IDC_BASELINE` |
| Main | `VAProfileH264Main` | `STD_VIDEO_H264_PROFILE_IDC_MAIN` |
| High | `VAProfileH264High` | `STD_VIDEO_H264_PROFILE_IDC_HIGH` |
| High 10 | `VAProfileH264High10` | `STD_VIDEO_H264_PROFILE_IDC_HIGH_10` |
| High 4:4:4 | `VAProfileH264High444` | `STD_VIDEO_H264_PROFILE_IDC_HIGH_444_PREDICTIVE` |

---

## 3. Surface/Buffer Management <a name="surface-management"></a>

### VA-API Surface Creation (from `vaapi_wrapper.cc`)

```cpp
// Method 1: Allocate surfaces
std::vector<VASurfaceID> va_surfaces(num_surfaces);
VASurfaceAttrib attribs[] = {
    {VASurfaceAttribPixelFormat, VA_SURFACE_ATTRIB_SETTABLE, {.value.i = VA_FOURCC_NV12}},
    {VASurfaceAttribUsageHint, VA_SURFACE_ATTRIB_SETTABLE, {.value.i = VA_SURFACE_ATTRIB_USAGE_HINT_DECODER}},
};
vaCreateSurfaces(va_display, VA_RT_FORMAT_YUV420, width, height,
                 va_surfaces.data(), num_surfaces, attribs, num_attribs);

// Method 2: Import from DMABuf (external allocation)
VASurfaceAttrib import_attribs[] = {
    {VASurfaceAttribMemoryType, ..., {.value.i = VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2}},
    {VASurfaceAttribExternalBufferDescriptor, ..., {.value.p = &descriptor}},
};
vaCreateSurfaces(va_display, VA_RT_FORMAT_YUV420, width, height,
                 &va_surface_id, 1, import_attribs, num_attribs);
```

### Vulkan Video Image Creation (equivalent)

```cpp
// Create VkImage for decode output
VkImageCreateInfo image_info = {
    .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
    .pNext = &video_profile_list,  // Link to video profile
    .flags = 0,
    .imageType = VK_IMAGE_TYPE_2D,
    .format = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM,  // NV12
    .extent = {width, height, 1},
    .mipLevels = 1,
    .arrayLayers = 1,
    .samples = VK_SAMPLE_COUNT_1_BIT,
    .tiling = VK_IMAGE_TILING_OPTIMAL,
    .usage = VK_IMAGE_USAGE_VIDEO_DECODE_DST_BIT_KHR |
             VK_IMAGE_USAGE_VIDEO_DECODE_DPB_BIT_KHR |
             VK_IMAGE_USAGE_SAMPLED_BIT,
};
vkCreateImage(device, &image_info, nullptr, &image);

// Allocate and bind memory
VkMemoryRequirements mem_reqs;
vkGetImageMemoryRequirements(device, image, &mem_reqs);
// ... allocate device memory ...
vkBindImageMemory(device, image, memory, 0);

// Create image view for video session
VkImageViewCreateInfo view_info = {
    .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
    .image = image,
    .viewType = VK_IMAGE_VIEW_TYPE_2D,
    .format = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM,
    // ...
};
vkCreateImageView(device, &view_info, nullptr, &image_view);
```

### Buffer Types Comparison

| Purpose | VA-API | Vulkan Video |
|---------|--------|--------------|
| Picture params | `VAPictureParameterBufferH264` | `VkVideoDecodeH264PictureInfoKHR` |
| Slice params | `VASliceParameterBufferH264` | Inline in decode info |
| Bitstream data | `VASliceDataBufferType` | `VkBuffer` |
| Encoded output | `VACodedBufferSegment` | `VkBuffer` with encode usage |
| IQ matrix | `VAIQMatrixBufferH264` | `StdVideoH264ScalingLists` |

---

## 4. DMABuf Export/Import <a name="dmabuf"></a>

### VA-API DMABuf Export (from `vaapi_wrapper.cc`)

```cpp
std::unique_ptr<NativePixmapAndSizeInfo>
VaapiWrapper::ExportVASurfaceAsNativePixmapDmaBufUnwrapped(
    VASurfaceID va_surface_id, const gfx::Size& va_surface_size) {
  
  // Must sync surface first!
  VAStatus va_res = vaSyncSurface(va_display_, va_surface_id);
  
  // Export surface as DRM PRIME handles
  VADRMPRIMESurfaceDescriptor descriptor;
  va_res = vaExportSurfaceHandle(
      va_display_, va_surface_id,
      VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2,
      VA_EXPORT_SURFACE_READ_ONLY | VA_EXPORT_SURFACE_SEPARATE_LAYERS,
      &descriptor);
  
  // Build NativePixmapHandle from descriptor
  gfx::NativePixmapHandle handle;
  handle.modifier = descriptor.objects[0].drm_format_modifier;
  
  for (uint32_t i = 0; i < descriptor.num_layers; ++i) {
    // descriptor.objects[].fd are owned by caller
    // descriptor.layers[] describes each plane
    handle.planes.emplace_back(
        layer.pitch[0], layer.offset[0], 0,
        base::ScopedFD(descriptor.objects[object_index].fd));
  }
  
  // Create NativePixmapDmaBuf
  auto pixmap = base::MakeRefCounted<gfx::NativePixmapDmaBuf>(
      va_surface_size, si_format, std::move(handle));
  
  return exported_pixmap;
}
```

### Vulkan Video DMABuf Export (equivalent)

```cpp
// Export VkImage memory as DMABuf FD
VkMemoryGetFdInfoKHR get_fd_info = {
    .sType = VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR,
    .memory = image_memory,
    .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
};
int dmabuf_fd;
vkGetMemoryFdKHR(device, &get_fd_info, &dmabuf_fd);

// Get image subresource layout for plane offsets/strides
VkImageSubresource subresource = {VK_IMAGE_ASPECT_PLANE_0_BIT, 0, 0};
VkSubresourceLayout layout;
vkGetImageSubresourceLayout(device, image, &subresource, &layout);
// layout.offset, layout.rowPitch give plane 0 info
// Repeat for plane 1 with VK_IMAGE_ASPECT_PLANE_1_BIT
```

### DMABuf Import (Creating surface from external buffer)

```cpp
// VA-API: vaCreateSurfaces with VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2
// Already shown above

// Vulkan Video: Import DMABuf as external memory
VkExternalMemoryImageCreateInfo ext_mem_info = {
    .sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
    .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
};
// Add to image create info pNext chain

VkImportMemoryFdInfoKHR import_info = {
    .sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR,
    .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
    .fd = dmabuf_fd,  // Takes ownership
};
// Add to memory allocate info pNext chain
```

### Key DMABuf Differences

| Aspect | VA-API | Vulkan Video |
|--------|--------|--------------|
| Export API | `vaExportSurfaceHandle()` | `vkGetMemoryFdKHR()` |
| Import | Create surface with `DRM_PRIME_2` | External memory import |
| Modifiers | In `VADRMPRIMESurfaceDescriptor` | `VkImageDrmFormatModifierExplicitCreateInfoEXT` |
| Multi-plane | Separate FDs per plane possible | Typically single FD |
| Sync before export | Required (`vaSyncSurface`) | Required (explicit sync) |

---

## 5. Synchronization Mechanisms <a name="synchronization"></a>

### VA-API Synchronization

VA-API uses **implicit synchronization** for most operations:

```cpp
// From vaapi_wrapper.cc - synchronization points:

// 1. vaSyncSurface() - Wait for decode to complete
bool VaapiWrapper::SyncSurface(VASurfaceID va_surface_id) {
  VAStatus va_res = vaSyncSurface(va_display_, va_surface_id);
  // Blocks until surface is ready
  return va_res == VA_STATUS_SUCCESS;
}

// 2. vaMapBuffer() - Implicitly syncs on Intel drivers
// From comments in code:
// "vaSyncSurface() is not necessary on Intel platforms as long as 
//  there is a vaMapBuffer() like in ScopedVABufferMapping"

// 3. Before export - MUST sync
// ExportVASurfaceAsNativePixmapDmaBufUnwrapped():
VAStatus va_res = vaSyncSurface(va_display_, va_surface_id);
va_res = vaExportSurfaceHandle(...);

// 4. Between contexts - SHOULD sync
// From vaapi_jpeg_encode_accelerator.cc:
// "We should call vaSyncSurface() when passing surface between contexts"
if (!vpp_vaapi_wrapper_->SyncSurface(va_surface_id_)) {
    return;
}
```

### Vulkan Video Synchronization

Vulkan requires **explicit synchronization**:

```cpp
// 1. Semaphores - GPU-GPU sync
VkSemaphore decode_complete_semaphore;
vkCreateSemaphore(device, &semaphore_info, nullptr, &decode_complete_semaphore);

// 2. Fences - CPU-GPU sync
VkFence decode_fence;
vkCreateFence(device, &fence_info, nullptr, &decode_fence);

// 3. In decode command recording:
VkVideoBeginCodingInfoKHR begin_info = {
    .sType = VK_STRUCTURE_TYPE_VIDEO_BEGIN_CODING_INFO_KHR,
    .videoSession = video_session,
    .videoSessionParameters = session_params,
    // Reference slots for DPB
};
vkCmdBeginVideoCodingKHR(cmd_buffer, &begin_info);

// Submit decode
vkCmdDecodeVideoKHR(cmd_buffer, &decode_info);

// End coding scope
VkVideoEndCodingInfoKHR end_info = {...};
vkCmdEndVideoCodingKHR(cmd_buffer, &end_info);

// 4. Submit with synchronization
VkSubmitInfo submit_info = {
    .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
    .waitSemaphoreCount = num_wait_semaphores,
    .pWaitSemaphores = wait_semaphores,
    .pWaitDstStageMask = wait_stages,
    .commandBufferCount = 1,
    .pCommandBuffers = &cmd_buffer,
    .signalSemaphoreCount = 1,
    .pSignalSemaphores = &decode_complete_semaphore,
};
vkQueueSubmit(video_queue, 1, &submit_info, decode_fence);

// 5. Wait for completion (CPU side)
vkWaitForFences(device, 1, &decode_fence, VK_TRUE, UINT64_MAX);
```

### Sync Comparison Table

| Scenario | VA-API | Vulkan Video |
|----------|--------|--------------|
| Wait for decode | `vaSyncSurface()` | `vkWaitForFences()` / semaphore wait |
| Before export | `vaSyncSurface()` | Fence or semaphore |
| Cross-context | `vaSyncSurface()` | Timeline semaphores |
| Pipeline barrier | N/A (implicit) | `vkCmdPipelineBarrier()` |
| Memory barrier | N/A (implicit) | `VkMemoryBarrier` |
| DMA sync | Implicit | `VK_ACCESS_HOST_READ_BIT` barrier |

---

## 6. Protected Content (DRM) <a name="protected-content"></a>

### VA-API Protected Content (from `vaapi_video_decoder_delegate.cc`)

```cpp
// 1. Create protected session
bool VaapiWrapper::CreateProtectedSession(
    EncryptionScheme encryption,
    const std::vector<uint8_t>& hw_config,
    std::vector<uint8_t>* hw_identifier_out) {
  
  // Create protected config
  VAConfigID va_protected_config_id;
  vaCreateConfig(va_display_, VAProfileProtected, VAEntrypointProtectedContent,
                 attribs, num_attribs, &va_protected_config_id);
  
  // Create protected session
  VAProtectedSessionID va_protected_session_id;
  vaCreateProtectedSession(va_display_, va_protected_config_id,
                           &va_protected_session_id);
  
  // Attach to decode context
  vaAttachProtectedSession(va_display_, va_context_id_, 
                           va_protected_session_id);
  
  return true;
}

// 2. Per-frame encryption setup (from delegate)
ProtectedSessionState SetupDecryptDecode(
    bool full_sample,
    size_t size,
    VAEncryptionParameters* crypto_params,
    std::vector<VAEncryptionSegmentInfo>* segments,
    const std::vector<SubsampleEntry>& subsamples) {
  
  // Set encryption type (CENC CTR or CBC)
  crypto_params->encryption_type = 
      ctr ? VA_ENCRYPTION_TYPE_SUBSAMPLE_CTR 
          : VA_ENCRYPTION_TYPE_SUBSAMPLE_CBC;
  
  // Fill segment info for each subsample
  for (const auto& entry : subsamples) {
    VAEncryptionSegmentInfo segment_info = {};
    segment_info.segment_start_offset = offset;
    segment_info.segment_length = entry.clear_bytes + entry.cypher_bytes;
    memcpy(segment_info.aes_cbc_iv_or_ctr, iv.data(), 16);
    segment_info.init_byte_length = entry.clear_bytes;
    segments->push_back(segment_info);
  }
  
  // Copy wrapped key
  memcpy(crypto_params->wrapped_decrypt_blob, 
         hw_key_data_map_[key_id].data(), 16);
  
  return ProtectedSessionState::kCreated;
}

// 3. Submit encrypted buffer
// Encryption params submitted as additional VABuffer
vaCreateBuffer(va_display, va_context_id_,
               VAEncryptionParameterBufferType,
               sizeof(VAEncryptionParameters), 1,
               &crypto_params, &enc_params_buffer_id);
```

### Vulkan Video Protected Content

Vulkan uses **Protected Memory** extensions:

```cpp
// 1. Create protected queue
// Query queue family with VK_QUEUE_PROTECTED_BIT

// 2. Allocate protected memory
VkMemoryAllocateInfo alloc_info = {
    .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
    .allocationSize = size,
    .memoryTypeIndex = protected_memory_type_index,
};
VkMemoryAllocateFlagsInfo flags_info = {
    .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO,
    .flags = VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT,  // if needed
};
// Must use VK_MEMORY_PROPERTY_PROTECTED_BIT

// 3. Create protected image
VkImageCreateInfo image_info = {
    .flags = VK_IMAGE_CREATE_PROTECTED_BIT,
    // ...
};

// 4. Create protected command buffer
VkCommandBufferAllocateInfo alloc_info = {...};
// Use protected command pool

// 5. Submit to protected queue
VkProtectedSubmitInfo protected_submit = {
    .sType = VK_STRUCTURE_TYPE_PROTECTED_SUBMIT_INFO,
    .protectedSubmit = VK_TRUE,
};
VkSubmitInfo submit_info = {
    .pNext = &protected_submit,
    // ...
};
```

### DRM Support Comparison

| Feature | VA-API (ChromeOS) | Vulkan Video |
|---------|-------------------|--------------|
| Protected session | `VAProtectedSessionID` | Protected command pool |
| Key management | CDM → `VAEncryptionParameters` | CDM → application managed |
| Encryption modes | CENC CTR, CENC CBC | CENC (application handled) |
| Transcryption (AMD) | Handled by delegate | Application responsibility |
| Recovery | `RecoverProtectedSession()` | Recreate protected resources |

---

## 7. Frame Output Flow in Chromium <a name="frame-output"></a>

### Current VA-API Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         DECODE SIDE (GPU Process)                        │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
     VaapiVideoDecoder::SurfaceReady(va_surface_id, buffer_id, ...)
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  VaapiVideoDecoder::OnFrameDecoded()                                    │
│  - Get FrameResource from allocated_va_surfaces_ map                     │
│  - Associate VASurface with FrameResource                               │
│  - Set metadata (color space, HDR, timestamp)                           │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  VideoDecoderPipeline::OnFrameDecoded()                                 │
│  - Optional: ImageProcessor (format conversion, scaling)                │
│  - Calls FrameResourceConverter                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  MailboxVideoFrameConverter::ConvertFrameImpl()                         │
│  1. Get origin frame (unwrap if needed)                                 │
│  2. Create GpuMemoryBufferHandle from DMABuf FDs                        │
│  3. Create SharedImage via SharedImageInterface                         │
│  4. Generate Mailbox for compositor                                     │
│  5. Wrap in VideoFrame with STORAGE_OPAQUE storage                      │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Output to Client (Renderer Process via IPC)                            │
│  - VideoFrame with gpu::Mailbox                                         │
│  - Client uses Mailbox to create texture for compositing                │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         COMPOSITOR SIDE                                 │
│  - Import SharedImage as texture                                        │
│  - Use for overlay or GPU composition                                   │
│  - Release reference when done → triggers FrameResource destruction     │
└─────────────────────────────────────────────────────────────────────────┘
```

### Key Data Structures

```cpp
// FrameResource (media/gpu/chromeos/frame_resource.h)
// - Abstract base for video frames
// - NativePixmapFrameResource holds NativePixmapDmaBuf

// NativePixmapDmaBuf (ui/gfx/linux/native_pixmap_dmabuf.h)  
// - Holds DMABuf FDs and plane info
// - Can be imported into GPU as texture

// GpuMemoryBufferHandle
// - Platform handle for GPU memory
// - On Linux: wraps NativePixmapHandle (DMABuf FDs)

// SharedImage
// - GPU-accessible image with Mailbox handle
// - Created from GpuMemoryBuffer
// - Compositor references via Mailbox
```

### Surface Lifecycle

```cpp
// 1. Surface creation (during Initialize or resolution change)
CreateSurface() → VaapiWrapper::CreateVASurfaceForPixmap(pixmap)
                       │
                       ▼
                  Import NativePixmap as VASurface
                  Store in allocated_va_surfaces_ map

// 2. Decode operation
decoder_->Decode()
    │
    └─→ delegate->SubmitDecode(pic)
            │
            └─→ VaapiWrapper::ExecuteAndDestroyPendingBuffers(va_surface_id)

// 3. Surface ready
SurfaceReady(va_surface_id, ...)
    │
    └─→ output_frames_[va_surface_id] holds FrameResource
        │
        └─→ Pass to VideoDecoderPipeline

// 4. Surface release
// When VideoFrame ref count → 0:
// - Destruction observer called
// - FrameResource returned to pool
// - VASurface can be reused
```

---

## 8. Overlays <a name="overlays"></a>

### Overlay Support in Chromium

Chromium supports **hardware overlays** for efficient video playback:

```cpp
// From VulkanOverlayAdaptor (media/gpu/chromeos/vulkan_overlay_adaptor.h)
// Used for MM21/MT2T detiling on MediaTek platforms

class VulkanOverlayAdaptor {
  // Process tiled image to linear for overlay display
  void Process(gpu::VulkanImage& in_image,
               const gfx::Size& input_visible_size,
               gpu::VulkanImage& out_image,
               const gfx::RectF& display_rect,
               const gfx::RectF& crop_rect,
               gfx::OverlayTransform transform,
               std::vector<VkSemaphore>& begin_semaphores,
               std::vector<VkSemaphore>& end_semaphores);
};
```

### Overlay Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                      VIDEO DECODER                               │
│  Output: NativePixmap (DMABuf) with decoded YUV                 │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    OVERLAY PROMOTION CHECK                       │
│  - Is format overlay-compatible?                                 │
│  - Is size within hardware limits?                               │
│  - Are there blocking overlapping elements?                      │
└──────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│     OVERLAY PATH        │     │   COMPOSITOR PATH       │
│  - Direct scanout       │     │  - GPU composition      │
│  - Zero-copy            │     │  - Texture sampling     │
│  - DRM/KMS plane        │     │  - Blending             │
└─────────────────────────┘     └─────────────────────────┘
```

### Overlay-Related APIs

| Feature | VA-API | Vulkan Video |
|---------|--------|--------------|
| Output format | NV12 (overlay friendly) | NV12, P010 |
| Tiling | Driver handles | May need detiling |
| Modifier support | Yes (DRM modifiers) | Yes (external memory) |
| Overlay adaptor | N/A | VulkanOverlayAdaptor |

---

## 9. Code Mapping: VA-API → Vulkan Video <a name="code-mapping"></a>

### Class Mapping

| VA-API Class | Vulkan Video Equivalent | Purpose |
|--------------|-------------------------|---------|
| `VaapiWrapper` | `VulkanVideoWrapper` | Low-level API wrapper |
| `VaapiVideoDecoder` | `VulkanVideoDecoder` | `VideoDecoderMixin` impl |
| `VaapiVideoDecoderDelegate` | `VulkanVideoDecoderDelegate` | Base delegate class |
| `H264VaapiVideoDecoderDelegate` | `H264VulkanVideoDecoderDelegate` | H.264 accelerator |
| `VaapiDecodeSurfaceHandler` | `VulkanDecodeSurfaceHandler` | Surface management interface |
| `ScopedVASurface` | `ScopedVulkanImage` | RAII surface wrapper |
| `ScopedVABuffer` | `ScopedVulkanBuffer` | RAII buffer wrapper |

### Method Mapping

```cpp
// VaapiWrapper method → VulkanVideoWrapper equivalent

// Display/Device
vaGetDisplayDRM()          → (use existing VkDevice)
vaInitialize()             → vkGetPhysicalDeviceVideoCapabilitiesKHR()
vaTerminate()              → (device destruction)

// Config/Session
vaCreateConfig()           → vkCreateVideoSessionKHR()
vaCreateContext()          → vkCreateVideoSessionKHR() (combined)
vaDestroyContext()         → vkDestroyVideoSessionKHR()

// Surface/Image
vaCreateSurfaces()         → vkCreateImage() + vkAllocateMemory()
vaDestroySurfaces()        → vkDestroyImage() + vkFreeMemory()

// Buffers
vaCreateBuffer()           → vkCreateBuffer() or inline structs
vaMapBuffer()              → vkMapMemory()
vaUnmapBuffer()            → vkUnmapMemory()
vaDestroyBuffer()          → vkDestroyBuffer()

// Decode execution
vaBeginPicture()           → vkCmdBeginVideoCodingKHR()
vaRenderPicture()          → (part of vkCmdDecodeVideoKHR setup)
vaEndPicture()             → vkCmdDecodeVideoKHR() + vkCmdEndVideoCodingKHR()

// Synchronization
vaSyncSurface()            → vkWaitForFences() / semaphore

// Export
vaExportSurfaceHandle()    → vkGetMemoryFdKHR()
```

### H.264 Structure Mapping

```cpp
// VA-API structures → Vulkan Video structures

// Picture parameters
VAPictureParameterBufferH264 → VkVideoDecodeH264PictureInfoKHR
                              + StdVideoDecodeH264PictureInfo

// Slice parameters  
VASliceParameterBufferH264   → (inline in VkVideoDecodeInfoKHR)
                              + StdVideoDecodeH264ReferenceInfo

// Reference pictures
VAPictureH264                → StdVideoDecodeH264ReferenceInfo
                              + VkVideoReferenceSlotInfoKHR

// IQ Matrix
VAIQMatrixBufferH264         → StdVideoH264ScalingLists
                              (in session parameters)

// SPS/PPS
// VA: Parsed by app, flat struct submitted per-frame
// VK: Parsed by app, stored in VkVideoSessionParametersKHR
```

---

## 10. Integration Challenges <a name="challenges"></a>

### 1. Synchronization Model Change

**Challenge**: VA-API uses implicit sync; Vulkan requires explicit sync.

**Solution**:
- Use `VkFence` for CPU waits (equivalent to `vaSyncSurface`)
- Use semaphores for GPU-GPU sync
- Implement proper barrier management

### 2. Session Parameters (SPS/PPS)

**Challenge**: VA-API submits SPS/PPS per-frame; Vulkan caches in session parameters.

**Solution**:
```cpp
// Track active SPS/PPS IDs
// On SPS/PPS change:
vkUpdateVideoSessionParametersKHR(device, session_params, &update_info);
// Or recreate session parameters if needed
```

### 3. DPB Management

**Challenge**: Vulkan requires explicit DPB slot management.

**Solution**:
- Implement DPB slot allocator
- Track reference frame states
- Map H.264/H.265 DPB semantics to Vulkan slots

### 4. Memory Management

**Challenge**: Vulkan requires explicit memory allocation/binding.

**Solution**:
- Use existing Chromium GPU memory allocator
- Implement proper memory type selection for video
- Handle device-local vs host-visible requirements

### 5. VP8 Support Gap

**Challenge**: Vulkan Video has no VP8 extension.

**Solution**:
- Keep VA-API for VP8 (if needed)
- Or use software decode for VP8
- VP8 usage is declining (WebM → AV1)

### 6. Protected Content Complexity

**Challenge**: Different DRM approaches.

**Solution**:
- Start with unprotected content
- Add protected memory support in phase 2
- May need platform-specific CDM integration

### 7. Driver Maturity

**Challenge**: Vulkan Video drivers are newer than VA-API drivers.

**Solution**:
- Test on multiple vendors (NVIDIA, AMD, Intel)
- Implement fallback to VA-API
- Report driver issues upstream

---

## Summary

### Key Takeaways

1. **Parsing is the same**: Chromium's codec parsers (H264Decoder, etc.) are fully reusable.

2. **Major difference is control model**: VA-API = implicit, Vulkan = explicit.

3. **DMABuf interop is similar**: Both support DRM PRIME / DMABuf export/import.

4. **Synchronization needs attention**: Must implement explicit fences/semaphores.

5. **Session parameters are different**: Vulkan caches SPS/PPS in session params object.

6. **Protected content needs work**: Different approaches require careful mapping.

7. **Frame output path is reusable**: MailboxVideoFrameConverter works with any NativePixmap source.

### Recommended Implementation Order

1. **VulkanVideoWrapper** - Core API abstraction
2. **H264 unprotected decode** - Most common codec
3. **H265 decode** - Similar to H264
4. **DMABuf export/import** - For compositor integration  
5. **VP9/AV1 decode** - As extensions become available
6. **Protected content** - DRM support
7. **Encode support** - Future phase

---

*Generated from Chromium source analysis*  
*Source: `/data/LinuxExtra/chrome/chromium/src`*

