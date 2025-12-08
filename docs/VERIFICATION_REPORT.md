# Document Verification Report

**Date**: December 6, 2025  
**Verified Against**: `/data/LinuxExtra/chrome/chromium/src/media/gpu/`

---

## Summary

All documents in `/data/LinuxExtra/chrome/docs/` have been verified against the actual Chromium source code. Errors found have been corrected.

---

## Verified Files and Paths

### Core Media/GPU Files (VERIFIED ✓)

| File Path | Exists | Notes |
|-----------|--------|-------|
| `media/gpu/accelerated_video_decoder.h` | ✓ | Base interface |
| `media/gpu/h264_decoder.h/cc` | ✓ | Contains H264Accelerator |
| `media/gpu/h265_decoder.h/cc` | ✓ | Contains H265Accelerator |
| `media/gpu/vp9_decoder.h/cc` | ✓ | Contains VP9Accelerator |
| `media/gpu/av1_decoder.h/cc` | ✓ | Contains AV1Accelerator |
| `media/gpu/chromeos/video_decoder_pipeline.h` | ✓ | Contains VideoDecoderMixin |
| `media/gpu/chromeos/frame_resource.h/cc` | ✓ | Frame wrapper |
| `media/gpu/chromeos/dmabuf_video_frame_pool.h/cc` | ✓ | DMA-BUF pool |

### VA-API Files (VERIFIED ✓)

| File Path | Exists | Notes |
|-----------|--------|-------|
| `media/gpu/vaapi/vaapi_wrapper.h/cc` | ✓ | libva wrapper |
| `media/gpu/vaapi/vaapi_video_decoder.h/cc` | ✓ | Implements VideoDecoderMixin |
| `media/gpu/vaapi/vaapi_video_decoder_delegate.h/cc` | ✓ | Base delegate class |
| `media/gpu/vaapi/vaapi_decode_surface_handler.h` | ✓ | Surface interface |
| `media/gpu/vaapi/h264_vaapi_video_decoder_delegate.h/cc` | ✓ | H.264 impl |
| `media/gpu/vaapi/h265_vaapi_video_decoder_delegate.h/cc` | ✓ | H.265 impl |
| `media/gpu/vaapi/vp9_vaapi_video_decoder_delegate.h/cc` | ✓ | VP9 impl |
| `media/gpu/vaapi/av1_vaapi_video_decoder_delegate.h/cc` | ✓ | AV1 impl |
| `media/gpu/vaapi/vp8_vaapi_video_decoder_delegate.h/cc` | ✓ | VP8 impl |

### V4L2 Files (VERIFIED ✓)

| File Path | Exists | Notes |
|-----------|--------|-------|
| `media/gpu/v4l2/v4l2_video_decoder.h/cc` | ✓ | V4L2 decoder |
| `media/gpu/v4l2/v4l2_video_decoder_delegate_h264.h/cc` | ✓ | H.264 impl |
| `media/gpu/v4l2/v4l2_video_decoder_delegate_h265.h/cc` | ✓ | H.265 impl |
| `media/gpu/v4l2/v4l2_video_decoder_delegate_vp9.h/cc` | ✓ | VP9 impl |
| `media/gpu/v4l2/v4l2_video_decoder_delegate_av1.h/cc` | ✓ | AV1 impl |

### Parser Files (VERIFIED ✓)

| File Path | Exists | Notes |
|-----------|--------|-------|
| `media/parsers/h264_parser.h/cc` | ✓ | H.264 NAL parsing |
| `media/parsers/h265_parser.h/cc` | ✓ | H.265 NAL parsing |
| `media/parsers/h266_parser.h/cc` | ✓ | H.266/VVC parsing |
| `media/parsers/vp8_parser.h/cc` | ✓ | VP8 parsing |
| `media/parsers/vp9_parser.h/cc` | ✓ | VP9 parsing |
| `media/parsers/ivf_parser.h/cc` | ✓ | IVF container |

### Test Infrastructure (VERIFIED ✓)

| File Path | Exists | Notes |
|-----------|--------|-------|
| `media/gpu/test/video_decode_accelerator_tests.cc` | ✓ | Main decode tests |
| `media/gpu/test/video_decode_accelerator_perf_tests.cc` | ✓ | Perf tests |
| `media/gpu/test/video_decode_accelerator_test_suite.cc` | ✓ | Test setup |
| `media/gpu/test/video_frame_validator.h/cc` | ✓ | MD5/SSIM validation |
| `media/gpu/test/video_player/decoder_wrapper.h/cc` | ✓ | Test wrapper |
| `media/gpu/test/video_player/decoder_listener.h/cc` | ✓ | Event handling |

### Software Decoders (VERIFIED ✓)

| File Path | Exists | Notes |
|-----------|--------|-------|
| `media/filters/ffmpeg_video_decoder.h/cc` | ✓ | FFmpeg fallback |
| `media/filters/vpx_video_decoder.h/cc` | ✓ | VP8/VP9 fallback |
| `media/filters/dav1d_video_decoder.h/cc` | ✓ | AV1 fallback |
| `media/filters/decoder_selector.h/cc` | ✓ | Decoder selection |

### Protected Content (VERIFIED ✓)

| Item | Verified |
|------|----------|
| `VAEncryptionParameters` in vaapi_video_decoder_delegate.h | ✓ |
| `VAEncryptionSegmentInfo` usage | ✓ |
| ChromeOS CDM context | ✓ |
| Transcryption flag for AMD | ✓ |

---

## Errors Found and Corrected

### 1. Incorrect File Path: `video_decoder_mixin.h`

- **Claimed**: `media/gpu/chromeos/video_decoder_mixin.h`
- **Reality**: `VideoDecoderMixin` class is defined inside `media/gpu/chromeos/video_decoder_pipeline.h`
- **Fixed in**:
  - `VULKAN_VIDEO_INTEGRATION_DESIGN_DOCUMENT.md`
  - `diagrams/class_hierarchy.svg`

### 2. Incorrect Naming Convention for Vulkan Delegates

- **Claimed**: `VulkanH264Accelerator`, `VulkanH265Acc`, etc.
- **Correct Pattern**: `H264VulkanVideoDecoderDelegate`, `H265VulkanVideoDecoderDelegate`
  - Matches existing: `H264VaapiVideoDecoderDelegate`
- **Fixed in**:
  - `VULKAN_VIDEO_DEVELOPMENT_PLAN.md`
  - `vulkan_video_development_plan.html`
  - `diagrams/phase2_architecture.svg`

### 3. Incorrect File Path for Proposed Vulkan Delegates

- **Changed from**: `media/gpu/vulkan/vulkan_h264_accelerator.h/cc`
- **Changed to**: `media/gpu/vulkan/h264_vulkan_video_decoder_delegate.h/cc`
- **Fixed in**:
  - `VULKAN_VIDEO_DEVELOPMENT_PLAN.md`
  - `vulkan_video_development_plan.html`

---

## Key Architecture Insights (VERIFIED)

### Class Hierarchy

```
AcceleratedVideoDecoder (media/gpu/accelerated_video_decoder.h)
       ↑ implements
   H264Decoder (media/gpu/h264_decoder.h) - contains nested H264Accelerator interface
       ↓ uses
H264Decoder::H264Accelerator (nested interface)
       ↑ implements
H264VaapiVideoDecoderDelegate (media/gpu/vaapi/h264_vaapi_video_decoder_delegate.h)
  └── also extends VaapiVideoDecoderDelegate
       ↓ uses
    VaapiWrapper (media/gpu/vaapi/vaapi_wrapper.h)
       ↓ calls
      libva
```

### Key Verified Facts

1. **Codec parsers ARE shared** - `H264Decoder`, `H265Decoder`, etc. are platform-agnostic
2. **Delegates implement the nested `*Accelerator` interface** (e.g., `H264Decoder::H264Accelerator`)
3. **Delegates ALSO extend `VaapiVideoDecoderDelegate`** for shared VA-API functionality
4. **`VideoDecoderMixin` is NOT in a separate header** - it's in `video_decoder_pipeline.h`
5. **Protected content uses `VAEncryptionParameters`** on Intel, `transcryption_` flag on AMD

---

## Documents Verified

| Document | Status |
|----------|--------|
| `CHROMIUM_VIDEO_DECODER_ARCHITECTURE_VERIFIED.md` | ✓ Created with verified info |
| `VULKAN_VIDEO_INTEGRATION_DESIGN_DOCUMENT.md` | ✓ Corrected |
| `VULKAN_VIDEO_DEVELOPMENT_PLAN.md` | ✓ Corrected |
| `vulkan_video_development_plan.html` | ✓ Corrected |
| `TEST_INFRASTRUCTURE_AND_FALLBACK.md` | ✓ Verified accurate |
| `VAAPI_vs_VULKAN_VIDEO_DETAILED_COMPARISON.md` | ✓ Verified accurate |
| `VULKAN_VIDEO_INTEGRATION_REPORT.md` | ✓ Verified accurate |
| `diagrams/class_hierarchy.svg` | ✓ Corrected |
| `diagrams/phase2_architecture.svg` | ✓ Corrected |

---

## Recommendations

1. Always verify file paths against actual source before documenting
2. Follow Chromium's naming conventions: `{Codec}{Platform}VideoDecoderDelegate`
3. Reference the `CHROMIUM_VIDEO_DECODER_ARCHITECTURE_VERIFIED.md` as ground truth
4. When proposing new files, mark them clearly as `(PROPOSED)` or `(NEW)`

