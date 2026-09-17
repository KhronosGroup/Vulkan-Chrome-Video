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
# Chromium Video Decoder Architecture - VERIFIED

<!-- DOC-STATUS -->
> **Status: CURRENT.** Verified account of Chromium decode architecture.
> Index: [`README.md`](README.md).


**IMPORTANT: This document is verified against actual source code at `/data/LinuxExtra/chrome/chromium/src/media/gpu/`**

## Actual File Structure

```
media/gpu/
├── accelerated_video_decoder.h         # Base interface for all codec parsers
├── h264_decoder.h/cc                   # H.264 parser + H264Accelerator interface
├── h265_decoder.h/cc                   # H.265 parser + H265Accelerator interface  
├── vp9_decoder.h/cc                    # VP9 parser + VP9Accelerator interface
├── av1_decoder.h/cc                    # AV1 parser + AV1Accelerator interface
├── h264_dpb.h/cc                       # H.264 Decoded Picture Buffer
├── h265_dpb.h/cc                       # H.265 Decoded Picture Buffer
├── codec_picture.h/cc                  # Base class for codec pictures
│
├── chromeos/
│   ├── video_decoder_pipeline.h/cc     # Contains VideoDecoderMixin class
│   ├── frame_resource.h/cc             # FrameResource (video frame wrapper)
│   ├── frame_resource_converter.h/cc   # Converts FrameResource to VideoFrame
│   ├── dmabuf_video_frame_pool.h/cc    # DMA-BUF frame pool
│   └── image_processor_with_pool.h/cc  # Image processor integration
│
└── vaapi/
    ├── vaapi_wrapper.h/cc              # VA-API wrapper (libva interface)
    ├── vaapi_video_decoder.h/cc        # Main decoder implementing VideoDecoderMixin
    ├── vaapi_video_decoder_delegate.h/cc    # Base class for codec delegates
    ├── vaapi_decode_surface_handler.h       # Surface handling interface
    ├── h264_vaapi_video_decoder_delegate.h/cc   # H.264 VA-API delegate
    ├── h265_vaapi_video_decoder_delegate.h/cc   # H.265 VA-API delegate
    ├── vp9_vaapi_video_decoder_delegate.h/cc    # VP9 VA-API delegate
    ├── av1_vaapi_video_decoder_delegate.h/cc    # AV1 VA-API delegate
    └── vp8_vaapi_video_decoder_delegate.h/cc    # VP8 VA-API delegate
```

## Key Classes and Their Relationships

### 1. AcceleratedVideoDecoder (media/gpu/accelerated_video_decoder.h)

Base interface for codec-specific parsers. **NOT a hardware accelerator itself** - it's the parser that calls into accelerators.

```cpp
// From: media/gpu/accelerated_video_decoder.h (lines 26-93)
class MEDIA_GPU_EXPORT AcceleratedVideoDecoder {
 public:
  virtual void SetStream(int32_t id, scoped_refptr<DecoderBuffer> decoder_buffer) = 0;
  virtual bool Flush() = 0;
  virtual void Reset() = 0;
  virtual DecodeResult Decode() = 0;
  virtual gfx::Size GetPicSize() const = 0;
  virtual gfx::Rect GetVisibleRect() const = 0;
  virtual VideoCodecProfile GetProfile() const = 0;
  virtual uint8_t GetBitDepth() const = 0;
  virtual size_t GetRequiredNumOfPictures() const = 0;
  // ...
};
```

### 2. H264Decoder (media/gpu/h264_decoder.h)

Implements `AcceleratedVideoDecoder`. Contains the **nested `H264Accelerator` interface** that hardware backends must implement.

```cpp
// From: media/gpu/h264_decoder.h (lines 36-191)
class MEDIA_GPU_EXPORT H264Decoder : public AcceleratedVideoDecoder {
 public:
  // NESTED CLASS - Hardware accelerator interface
  class MEDIA_GPU_EXPORT H264Accelerator {
   public:
    enum class Status { kOk, kFail, kTryAgain, kNotSupported };
    
    virtual scoped_refptr<H264Picture> CreateH264Picture() = 0;
    
    virtual Status SubmitFrameMetadata(
        const H264SPS* sps,
        const H264PPS* pps,
        const H264DPB& dpb,
        const H264Picture::Vector& ref_pic_listp0,
        const H264Picture::Vector& ref_pic_listb0,
        const H264Picture::Vector& ref_pic_listb1,
        scoped_refptr<H264Picture> pic) = 0;
    
    virtual Status SubmitSlice(
        const H264PPS* pps,
        const H264SliceHeader* slice_hdr,
        const H264Picture::Vector& ref_pic_list0,
        const H264Picture::Vector& ref_pic_list1,
        scoped_refptr<H264Picture> pic,
        const uint8_t* data,
        size_t size,
        const std::vector<SubsampleEntry>& subsamples) = 0;
    
    virtual Status SubmitDecode(scoped_refptr<H264Picture> pic) = 0;
    virtual bool OutputPicture(scoped_refptr<H264Picture> pic) = 0;
    virtual void Reset() = 0;
  };
  
  // Parser owns the accelerator
  H264Decoder(std::unique_ptr<H264Accelerator> accelerator, ...);
  // ...
};
```

### 3. VideoDecoderMixin (media/gpu/chromeos/video_decoder_pipeline.h)

**NOTE: This is NOT in a separate file!** It's defined inside `video_decoder_pipeline.h`.

```cpp
// From: media/gpu/chromeos/video_decoder_pipeline.h (lines 49-150+)
class MEDIA_GPU_EXPORT VideoDecoderMixin : public VideoDecoder {
 public:
  using PipelineOutputCB = base::RepeatingCallback<void(scoped_refptr<FrameResource>)>;
  
  class MEDIA_GPU_EXPORT Client {
   public:
    virtual DmabufVideoFramePool* GetVideoFramePool() const = 0;
    virtual void PrepareChangeResolution() = 0;
    virtual CroStatus::Or<ImageProcessor::PixelLayoutCandidate>
        PickDecoderOutputFormat(...) = 0;
    // ...
  };
  
  virtual void ApplyResolutionChange() = 0;
  virtual bool NeedsTranscryption() = 0;
  // ...
};
```

### 4. VaapiVideoDecoder (media/gpu/vaapi/vaapi_video_decoder.h)

The main VA-API decoder. Implements `VideoDecoderMixin`.

```cpp
// From: media/gpu/vaapi/vaapi_video_decoder.h (lines 49-304)
class VaapiVideoDecoder : public VideoDecoderMixin,
                          public VaapiDecodeSurfaceHandler {
 public:
  static std::unique_ptr<VideoDecoderMixin> Create(...);
  
  void Initialize(const VideoDecoderConfig& config, ...) override;
  void Decode(scoped_refptr<DecoderBuffer> buffer, DecodeCB decode_cb) override;
  void Reset(base::OnceClosure reset_cb) override;
  
  // VaapiDecodeSurfaceHandler implementation
  std::unique_ptr<VASurfaceHandle> CreateSurface() override;
  void SurfaceReady(VASurfaceID va_surface_id, ...) override;
  
 private:
  // Platform and codec specific video decoder (H264Decoder, H265Decoder, etc.)
  std::unique_ptr<AcceleratedVideoDecoder> decoder_;
  
  // VA-API abstraction
  scoped_refptr<VaapiWrapper> vaapi_wrapper_;
  
  // Codec-specific delegate (H264VaapiVideoDecoderDelegate, etc.)
  raw_ptr<VaapiVideoDecoderDelegate> decoder_delegate_;
};
```

### 5. VaapiVideoDecoderDelegate (media/gpu/vaapi/vaapi_video_decoder_delegate.h)

Base class for codec-specific VA-API delegates. Handles protected content.

```cpp
// From: media/gpu/vaapi/vaapi_video_decoder_delegate.h (lines 45-170)
class VaapiVideoDecoderDelegate {
 public:
  VaapiVideoDecoderDelegate(
      VaapiDecodeSurfaceHandler* const vaapi_dec,
      scoped_refptr<VaapiWrapper> vaapi_wrapper,
      ProtectedSessionUpdateCB on_protected_session_update_cb,
      CdmContext* cdm_context,
      EncryptionScheme encryption_scheme);
  
  void set_vaapi_wrapper(scoped_refptr<VaapiWrapper> vaapi_wrapper);
  
 protected:
  // For encrypted content (Intel platforms)
  bool SetDecryptConfig(std::unique_ptr<DecryptConfig> decrypt_config);
  
#if BUILDFLAG(IS_CHROMEOS)
  ProtectedSessionState SetupDecryptDecode(
      bool full_sample,
      size_t size,
      VAEncryptionParameters* crypto_params,
      std::vector<VAEncryptionSegmentInfo>* segments,
      const std::vector<SubsampleEntry>& subsamples);
#endif
  
  bool IsEncryptedSession() const;
  bool IsTranscrypted() const;  // AMD platforms
  
  const raw_ptr<VaapiDecodeSurfaceHandler> vaapi_dec_;
  scoped_refptr<VaapiWrapper> vaapi_wrapper_;
};
```

### 6. H264VaapiVideoDecoderDelegate (media/gpu/vaapi/h264_vaapi_video_decoder_delegate.h)

Implements BOTH `H264Decoder::H264Accelerator` AND extends `VaapiVideoDecoderDelegate`.

```cpp
// From: media/gpu/vaapi/h264_vaapi_video_decoder_delegate.h (lines 24-99)
class H264VaapiVideoDecoderDelegate : public H264Decoder::H264Accelerator,
                                      public VaapiVideoDecoderDelegate {
 public:
  H264VaapiVideoDecoderDelegate(
      VaapiDecodeSurfaceHandler* vaapi_dec,
      scoped_refptr<VaapiWrapper> vaapi_wrapper,
      ProtectedSessionUpdateCB on_protected_session_update_cb = base::DoNothing(),
      CdmContext* cdm_context = nullptr,
      EncryptionScheme encryption_scheme = EncryptionScheme::kUnencrypted);
  
  // H264Decoder::H264Accelerator implementation
  scoped_refptr<H264Picture> CreateH264Picture() override;
  void ProcessSPS(const H264SPS* sps, base::span<const uint8_t> sps_nalu_data) override;
  void ProcessPPS(const H264PPS* pps, base::span<const uint8_t> pps_nalu_data) override;
  Status SubmitFrameMetadata(...) override;
  Status SubmitSlice(...) override;
  Status SubmitDecode(scoped_refptr<H264Picture> pic) override;
  bool OutputPicture(scoped_refptr<H264Picture> pic) override;
  void Reset() override;
  
 private:
  void FillVAPicture(VAPictureH264* va_pic, scoped_refptr<H264Picture> pic);
  
#if BUILDFLAG(IS_CHROMEOS)
  std::vector<VAEncryptionSegmentInfo> encryption_segment_info_;
  VAEncryptionParameters crypto_params_;
#endif
};
```

### 7. VaapiWrapper (media/gpu/vaapi/vaapi_wrapper.h)

Low-level wrapper around libva. Handles VA-API initialization, surface management, and decode operations.

```cpp
// From: media/gpu/vaapi/vaapi_wrapper.h
class MEDIA_GPU_EXPORT VaapiWrapper : public base::RefCountedThreadSafe<VaapiWrapper> {
 public:
  // Creation
  static scoped_refptr<VaapiWrapper> CreateForVideoCodec(
      VAProfile va_profile, ...);
  
  // Surface management
  bool CreateSurfaces(unsigned int va_format, const gfx::Size& size,
                      std::vector<VASurfaceID>* va_surfaces);
  void DestroySurfaces(std::vector<VASurfaceID>* va_surfaces);
  
  // Decode operations
  bool SubmitBuffer(VABufferType va_buffer_type, size_t size, const void* data);
  bool SubmitBuffers(const std::vector<VABufferDescriptor>& va_buffers);
  bool ExecuteAndDestroyPendingBuffers(VASurfaceID va_surface_id);
  bool SyncSurface(VASurfaceID va_surface_id);
  
  // DMA-BUF export
  bool ExportVASurfaceAsNativePixmapDmaBuf(
      VASurfaceID va_surface_id,
      const gfx::Size& size,
      std::unique_ptr<NativePixmapAndSizeInfo>* native_pixmap);
  
 private:
  VADisplay va_display_;
  VAConfigID va_config_id_;
  VAContextID va_context_id_;
  // ...
};
```

## Architecture Diagram (VERIFIED)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        VideoDecoderPipeline                              │
│            (media/gpu/chromeos/video_decoder_pipeline.h)                │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         VaapiVideoDecoder                                │
│               (media/gpu/vaapi/vaapi_video_decoder.h)                   │
│                                                                         │
│  ┌────────────────────────┐    ┌────────────────────────────────────┐   │
│  │  AcceleratedVideoDecoder│   │  VaapiVideoDecoderDelegate         │   │
│  │  (e.g., H264Decoder)   │   │  (e.g., H264VaapiVideoDecoderDelegate)│ │
│  │                        │───▶│                                    │   │
│  │  - Parses bitstream    │   │  - Implements H264Accelerator      │   │
│  │  - Manages DPB         │   │  - Calls VaapiWrapper              │   │
│  │  - Calls H264Accelerator│   │  - Handles protected content      │   │
│  └────────────────────────┘    └──────────────┬─────────────────────┘   │
│                                               │                         │
│                                               ▼                         │
│                              ┌────────────────────────────────────┐     │
│                              │         VaapiWrapper               │     │
│                              │  (media/gpu/vaapi/vaapi_wrapper.h) │     │
│                              │                                    │     │
│                              │  - vaCreateSurfaces()             │     │
│                              │  - vaBeginPicture()               │     │
│                              │  - vaRenderPicture()              │     │
│                              │  - vaEndPicture()                 │     │
│                              │  - vaSyncSurface()                │     │
│                              └──────────────┬─────────────────────┘     │
└─────────────────────────────────────────────┼───────────────────────────┘
                                              │
                                              ▼
                              ┌────────────────────────────────────┐
                              │           libva (VA-API)           │
                              │     (system library - /usr/lib)    │
                              └────────────────────────────────────┘
```

## Vulkan Video Integration Points

For Vulkan Video integration, the following would need to be created:

| VA-API Component | Vulkan Video Equivalent |
|-----------------|------------------------|
| `VaapiWrapper` | `VulkanVideoWrapper` (new) |
| `VaapiVideoDecoder` | `VulkanVideoDecoder` (new) |
| `VaapiVideoDecoderDelegate` | `VulkanVideoDecoderDelegate` (new) |
| `H264VaapiVideoDecoderDelegate` | `H264VulkanVideoDecoderDelegate` (new) |
| `VaapiDecodeSurfaceHandler` | `VulkanDecodeSurfaceHandler` (new) |

The key insight is that **codec parsers are shared**:
- `H264Decoder`, `H265Decoder`, `VP9Decoder`, `AV1Decoder` remain unchanged
- Only the `*Accelerator` implementations need to be created for Vulkan

**However**, significant conversion work is still required. The NVPRO Vulkan Video sample has its own integrated parser that we **cannot** reuse because Chromium's `*Accelerator` interface receives already-parsed Chromium structures (e.g., `H264SPS`, `H264PPS`), not raw bitstream. We must write conversion code from Chromium structures to Vulkan Video's `StdVideo*` structures (~500-700 lines per codec, ~2,200 lines total for all 4 codecs).

## Protected Content (Verified from source)

From `vaapi_video_decoder_delegate.h`:

```cpp
#if BUILDFLAG(IS_CHROMEOS)
  ProtectedSessionState SetupDecryptDecode(
      bool full_sample,
      size_t size,
      VAEncryptionParameters* crypto_params,
      std::vector<VAEncryptionSegmentInfo>* segments,
      const std::vector<SubsampleEntry>& subsamples);
#endif
```

From `h264_vaapi_video_decoder_delegate.h`:

```cpp
#if BUILDFLAG(IS_CHROMEOS)
  std::vector<VAEncryptionSegmentInfo> encryption_segment_info_;
  VAEncryptionParameters crypto_params_;
#endif
```

Protected content handling uses:
- `VAEncryptionParameters` - encryption configuration
- `VAEncryptionSegmentInfo` - per-slice encryption info
- `EncryptionScheme` - CENC, CBCS, etc.
- `transcryption_` flag for AMD platforms (different handling than Intel)

## V4L2 Backend (for comparison)

The V4L2 backend follows the same pattern:

```
media/gpu/v4l2/
├── v4l2_video_decoder.h/cc                    # Implements VideoDecoderMixin
├── v4l2_video_decoder_delegate_h264.h/cc      # H264Accelerator impl
├── v4l2_video_decoder_delegate_h265.h/cc      # H265Accelerator impl
├── v4l2_video_decoder_delegate_vp9.h/cc       # VP9Accelerator impl
├── v4l2_video_decoder_delegate_av1.h/cc       # AV1Accelerator impl
└── v4l2_device.h/cc                           # V4L2 ioctl wrapper
```

---

**Document verified against Chromium source at `/data/LinuxExtra/chrome/chromium/src/media/gpu/`**

