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
# Chromium HW Codec Test Infrastructure, DRM Testing, and Software Fallback

<!-- DOC-STATUS -->
> **Status: CURRENT.** Test infrastructure and fallback behaviour.
> Index: [`README.md`](README.md).


**Date**: December 4, 2025  
**Purpose**: Understand test infrastructure for HW codec development and integration

---

## Table of Contents
1. [Test Infrastructure Overview](#test-infrastructure)
2. [Test Executables and Suites](#test-executables)
3. [Test Content and Data](#test-content)
4. [Codec Test Coverage](#codec-coverage)
5. [DRM/Protected Content Testing](#drm-testing)
6. [Software Decoder Fallback](#software-fallback)
7. [Frame Validation Methods](#validation)
8. [Running Tests](#running-tests)

---

## 1. Test Infrastructure Overview <a name="test-infrastructure"></a>

### Directory Structure

```
media/gpu/test/
├── video_decode_accelerator_tests.cc      # Main decode tests
├── video_decode_accelerator_perf_tests.cc # Performance benchmarks
├── video_encode_accelerator_tests.cc      # Encode tests
├── video_encode_accelerator_perf_tests.cc # Encode benchmarks
├── video_decode_accelerator_test_suite.cc # Test environment setup
├── video_bitstream.{h,cc}                 # Video file + metadata loading
├── video_frame_validator.{h,cc}           # MD5/SSIM frame validation
├── video_frame_file_writer.{h,cc}         # Frame output for debugging
├── video_test_helpers.{h,cc}              # Common test utilities
├── video_player/                          # Video playback test framework
│   ├── decoder_wrapper.{h,cc}             # Wraps VideoDecoder for tests
│   ├── decoder_listener.{h,cc}            # Event handling
│   ├── frame_renderer_dummy.{h,cc}        # Dummy frame consumer
│   └── video_player_test_environment.{h,cc}
└── video_encoder/                         # Encoder test framework
    ├── video_encoder_client.{h,cc}
    ├── bitstream_validator.{h,cc}
    └── decoder_buffer_validator.{h,cc}
```

### Test Framework Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    VideoDecodeAcceleratorTest                        │
│  (GTest-based test fixture)                                         │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       DecoderListener                                │
│  - Wraps DecoderWrapper                                             │
│  - Handles decode events (frame ready, error, flush)                │
│  - Routes frames to validators/writers                              │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       DecoderWrapper                                 │
│  - Creates VideoDecoderPipeline (HW decode path)                    │
│  - Manages decode task queue                                        │
│  - Handles resolution changes                                       │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    VideoDecoderPipeline                             │
│  - VaapiVideoDecoder / V4L2VideoDecoder                             │
│  - Production decode path under test                                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Test Executables and Suites <a name="test-executables"></a>

### Main Test Binaries

| Binary | Purpose | Platform |
|--------|---------|----------|
| `video_decode_accelerator_tests` | HW decode correctness | ChromeOS/Linux |
| `video_decode_accelerator_perf_tests` | Decode performance | ChromeOS/Linux |
| `video_encode_accelerator_tests` | HW encode correctness | ChromeOS/Linux |
| `video_encode_accelerator_perf_tests` | Encode performance | ChromeOS/Linux |
| `vaapi_unittest` | VA-API unit tests | Linux/ChromeOS |

### Test Categories (from video_decode_accelerator_tests.cc)

```cpp
// Basic decode tests
TEST_F(VideoDecoderTest, FlushAtEndOfStream)
TEST_F(VideoDecoderTest, Reset)
TEST_F(VideoDecoderTest, ResetAfterFirstFrame)

// Resolution change tests
TEST_F(VideoDecoderTest, ResolutionChange)
TEST_F(VideoDecoderTest, DynamicResolutionChange)

// Stress tests
TEST_F(VideoDecoderTest, FlushMultipleTimes)
TEST_F(VideoDecoderTest, ResetMultipleTimes)

// Error handling
TEST_F(VideoDecoderTest, DecodeCorruptedStream)
TEST_F(VideoDecoderTest, UnsupportedProfile)

// Performance metrics
PERF_TEST(VideoDecoderTest, MeasureDecodeLatency)
PERF_TEST(VideoDecoderTest, MeasureThroughput)
```

---

## 3. Test Content and Data <a name="test-content"></a>

### Test Data Location

```
media/test/data/
├── bear-*.{mp4,webm,ivf,h264,hevc}  # Main test video "Bear"
├── av1-*.ivf                         # AV1 test streams
├── bbb-*.{mp4,vvc}                   # Big Buck Bunny variants
├── four-colors-*.{mp4,webm}          # Pattern videos for validation
├── encrypted/                         # DRM test content
└── *.json                            # Metadata files
```

### Video Bitstream Metadata Format

```json
// Example: bear-320x240.webm.json
{
  "profile": "VP9PROFILE_PROFILE0",
  "codec": "vp9",
  "bit_depth": 8,
  "frame_rate": 30,
  "num_frames": 82,
  "width": 320,
  "height": 240,
  "frame_checksums": [
    "abc123...",  // MD5 of decoded frame 0
    "def456...",  // MD5 of decoded frame 1
    // ...
  ],
  "has_keyframeless_resolution_change": false
}
```

### Required Codec Test Coverage

| Codec | Profiles | Test Files |
|-------|----------|------------|
| **H.264/AVC** | Baseline, Main, High | `bear-*.h264`, `bear-*.mp4` |
| **H.265/HEVC** | Main, Main10 | `bear-*.hevc`, `bbb.hevc` |
| **VP8** | Profile 0 | `bear-*.webm` (VP8) |
| **VP9** | Profile 0, 2 (10-bit) | `bear-*.webm`, `bear-vp9.ivf` |
| **AV1** | Main | `av1-*.ivf`, `bear-av1.webm` |
| **VVC** | Main10 | `bbb_*.vvc` |

### Bit Depths and Formats

| Bit Depth | Pixel Format | Test Files |
|-----------|--------------|------------|
| 8-bit | NV12, I420 | Most test files |
| 10-bit | P010 | `*-10bit-*`, `*-hi10p-*` |
| 12-bit | P012 | `*-hi12p-*` (VP9 only) |

### Resolution Test Coverage

| Resolution | Purpose | Example Files |
|------------|---------|---------------|
| 320x240 | Low resolution | `bear-320x240.*` |
| 640x360 | SD | `bear-640x360.*` |
| 1280x720 | HD | `bear-1280x720.*` |
| 1920x1080 | FHD | Various |
| 3840x2160 | 4K | `bear-3840x2160-hevc.mp4` |

---

## 4. Codec Test Coverage <a name="codec-coverage"></a>

### Decode Test Matrix

```cpp
// From video_decode_accelerator_tests.cc
// Test instantiation with different codecs:

// H.264 tests
INSTANTIATE_TEST_SUITE_P(H264,
    VideoDecoderTest,
    ::testing::Values(
        TestParams{"bear-320x192-baseline-*.h264", ...},
        TestParams{"bear-320x192-high-*.h264", ...},
        TestParams{"bear-320x180-10bit-*.h264", ...}
    ));

// VP9 tests  
INSTANTIATE_TEST_SUITE_P(VP9,
    VideoDecoderTest,
    ::testing::Values(
        TestParams{"bear-320x240.webm", ...},
        TestParams{"bear-320x180-hi10p-vp9.webm", ...}
    ));

// AV1 tests
INSTANTIATE_TEST_SUITE_P(AV1,
    VideoDecoderTest,
    ::testing::Values(
        TestParams{"av1-1-b8-02-allintra.ivf", ...},
        TestParams{"bear-av1-320x180-10bit.mp4", ...}
    ));
```

### Encode Test Matrix

```cpp
// From video_encode_accelerator_tests.cc
// Encode parameter combinations:

struct EncodeTestParams {
    VideoCodec codec;
    gfx::Size resolution;
    uint32_t bitrate;
    uint32_t framerate;
    BitrateMode bitrate_mode;  // CBR, VBR
    SVCMode svc_mode;          // None, L1T2, L1T3, etc.
};

// H.264 encode
// VP9 encode
// AV1 encode (where supported)
```

---

## 5. DRM/Protected Content Testing <a name="drm-testing"></a>

### Protected Content Test Infrastructure

Protected content testing is primarily done on **ChromeOS** with hardware CDM support.

### Encrypted Test Files

```
media/test/data/
├── bear-640x360-v_frag-cenc.mp4       # CENC encrypted video
├── bear-640x360-a_frag-cenc.mp4       # CENC encrypted audio  
├── bear-1280x720-v_frag-cenc.mp4      # HD CENC
├── bear-640x360-v_frag-cbcs.mp4       # CBCS pattern encryption
├── bear-640x360-v_frag-cbc1.mp4       # CBC1 encryption
├── bear-*-cenc-key_rotation.mp4       # Key rotation tests
└── bear-av1-cenc.mp4                  # AV1 encrypted
```

### DRM Testing Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Encrypted Media Testing                          │
└─────────────────────────────────────────────────────────────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            ▼                   ▼                   ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│   Clear Key CDM  │ │  Widevine CDM    │ │  Platform CDM    │
│   (Testing)      │ │  (Production)    │ │  (ChromeOS)      │
└──────────────────┘ └──────────────────┘ └──────────────────┘
```

### Clear Key CDM (For Testing)

Used for basic encrypted playback testing without a full DRM system:

```javascript
// media/test/data/eme_player.html
// Test page for EME (Encrypted Media Extensions)

const config = {
  initDataTypes: ['cenc'],
  audioCapabilities: [{contentType: 'audio/mp4; codecs="mp4a.40.2"'}],
  videoCapabilities: [{contentType: 'video/mp4; codecs="avc1.64001F"'}],
};

navigator.requestMediaKeySystemAccess('org.w3.clearkey', [config]);
```

### Protected Content Test Flow

```cpp
// From vaapi_video_decoder_delegate.cc
// Protected session setup flow:

1. CdmContext provides hw_config data
   ↓
2. VaapiWrapper::CreateProtectedSession(encryption_scheme, hw_config)
   ↓
3. vaCreateProtectedSession()
   ↓
4. vaAttachProtectedSession() to decode context
   ↓
5. Per-frame: SetupDecryptDecode() fills VAEncryptionParameters
   ↓
6. Submit encrypted slices with crypto params
```

### Encryption Schemes Tested

| Scheme | VA-API | Test Files |
|--------|--------|------------|
| **CENC CTR** | `VA_ENCRYPTION_TYPE_SUBSAMPLE_CTR` | `*-cenc.mp4` |
| **CENC CBC** | `VA_ENCRYPTION_TYPE_SUBSAMPLE_CBC` | `*-cbc1.mp4` |
| **CBCS** | Pattern encryption | `*-cbcs.mp4` |

---

## 6. Software Decoder Fallback <a name="software-fallback"></a>

### Decoder Selection Mechanism

Chromium uses `DecoderSelector` to choose between HW and SW decoders:

```cpp
// media/filters/decoder_selector.h

template<DemuxerStream::Type StreamType>
class DecoderSelector {
  // Callback to create list of decoder candidates
  using CreateDecodersCB = 
      base::RepeatingCallback<std::vector<std::unique_ptr<Decoder>>()>;
  
  // Selection priority based on resolution:
  // - Resolution >= 360p: Prefer platform (HW) decoders
  // - Resolution < 360p: Prefer software decoders
};
```

### Decoder Priority Logic

```cpp
// From decoder_selector.cc

DecoderPriority SelectDecoderPriority(const VideoDecoderConfig& config,
                                      const VideoDecoder& decoder) {
  constexpr auto kSoftwareDecoderHeightCutoff = 360;

  const auto at_or_above_software_cutoff =
      config.visible_rect().height() >= kSoftwareDecoderHeightCutoff;

  // Platform decoders deprioritized below cutoff
  // Software decoders deprioritized above cutoff
  return at_or_above_software_cutoff == decoder.IsPlatformDecoder()
             ? DecoderPriority::kNormal
             : DecoderPriority::kDeprioritized;
}
```

### Available Software Decoders

| Decoder | Codecs | Implementation |
|---------|--------|----------------|
| **FFmpegVideoDecoder** | H.264, H.265*, VP8, VP9, MPEG-2/4, etc. | `media/filters/ffmpeg_video_decoder.cc` |
| **VpxVideoDecoder** | VP8, VP9 | `media/filters/vpx_video_decoder.cc` |
| **Dav1dVideoDecoder** | AV1 | `media/filters/dav1d_video_decoder.cc` |
| **GpuVideoDecoder** | All (via GPU process) | `media/renderers/gpu_video_decoder.cc` |

*H.265 in FFmpeg requires proprietary codecs build flag

### Decoder Order (Typical)

```cpp
// Decoders are tried in order until one initializes successfully:

1. Platform/HW Decoder (VaapiVideoDecoder, D3D11VideoDecoder, etc.)
   ↓ (if fails or unsupported)
2. GPU-accelerated SW decoder (if available)
   ↓ (if fails)  
3. FFmpegVideoDecoder (software fallback)
   ↓ (if fails)
4. Specialized SW decoder (Dav1d for AV1, Vpx for VP8/9)
```

### Fallback Triggers

| Condition | Behavior |
|-----------|----------|
| Profile not supported by HW | Falls back to SW |
| Resolution exceeds HW limits | Falls back to SW |
| HW decoder init fails | Tries next decoder |
| Decode error during playback | May try fallback |
| Protected content not supported | Falls back (or fails) |
| Missing driver/library | Falls back to SW |

### Fallback Example Flow

```cpp
// User plays 8K video on system with 4K max HW decode:

1. DecoderSelector::BeginDecoderSelection()
2. Try VaapiVideoDecoder::Initialize(8K config)
   → Returns kUnsupportedConfig (exceeds max resolution)
3. Try FFmpegVideoDecoder::Initialize(8K config)
   → Returns kOk
4. FFmpegVideoDecoder used for playback
```

### Encoder Fallback

```cpp
// media/video/video_encoder_fallback.h

class VideoEncoderFallback : public VideoEncoder {
  // Wraps primary encoder with SW fallback
  // Primary: HW encoder (VaapiVideoEncodeAccelerator, etc.)
  // Fallback: Software encoder (OpenH264, libvpx, libaom)
};
```

---

## 7. Frame Validation Methods <a name="validation"></a>

### MD5 Validation

```cpp
// media/gpu/test/video_frame_validator.cc

class MD5VideoFrameValidator : public VideoFrameValidator {
  // Computes MD5 of decoded frame pixels
  // Compares against expected checksums from metadata JSON
  
  std::string ComputeMD5(const VideoFrame& frame) {
    base::MD5Context context;
    base::MD5Init(&context);
    // Hash Y, U, V planes
    for (size_t plane = 0; plane < VideoFrame::NumPlanes(format); ++plane) {
      base::MD5Update(&context, 
          {frame.data(plane), GetPlaneSize(frame, plane)});
    }
    return base::MD5DigestToBase16(base::MD5Final(&context));
  }
};
```

### SSIM Validation

```cpp
// For lossy comparison (e.g., when HW produces slightly different output)

class SSIMVideoFrameValidator : public VideoFrameValidator {
  // Structural Similarity Index
  // Threshold: typically 0.915 or higher
  
  double ComputeSSIM(const VideoFrame& decoded, const VideoFrame& reference);
};
```

### Creating Model Frames (Reference)

```cpp
// Test uses software decoder to create reference frames:

bool CreateModelFrames(const VideoBitstream* video) {
  // Use Dav1dVideoDecoder or FFmpegVideoDecoder
  // to decode video to get reference frames
  // These are compared against HW decoder output
}
```

---

## 8. Running Tests <a name="running-tests"></a>

### Build Tests

```bash
# Build decode tests
autoninja -C out/Default video_decode_accelerator_tests

# Build encode tests
autoninja -C out/Default video_encode_accelerator_tests

# Build VA-API unit tests
autoninja -C out/Default vaapi_unittest
```

### Run Decode Tests

```bash
# Basic run
./out/Default/video_decode_accelerator_tests \
  --test_video_data=/path/to/test/data

# With specific test
./out/Default/video_decode_accelerator_tests \
  --gtest_filter="*H264*" \
  --test_video_data=/path/to/bear-1280x720.h264

# With frame output for debugging
./out/Default/video_decode_accelerator_tests \
  --frame_output_mode=all \
  --frame_output_format=png \
  --output_folder=/tmp/frames

# Performance test
./out/Default/video_decode_accelerator_perf_tests \
  --test_video_data=/path/to/4k_video.webm
```

### Test Command Line Options

| Option | Description |
|--------|-------------|
| `--test_video_data=<path>` | Path to test video file |
| `--test_video_metadata=<path>` | Path to JSON metadata |
| `--output_folder=<path>` | Where to write outputs |
| `--frame_output_mode=[none\|all\|corrupt]` | Frame output mode |
| `--frame_output_format=[png\|yuv]` | Output format |
| `--frame_output_limit=<N>` | Max frames to output |
| `--disable_validator` | Skip frame validation |
| `--validator_type=[md5\|ssim]` | Validation method |
| `--linear_output` | Force linear buffer output |
| `--gtest_filter=<pattern>` | GTest filter |
| `--v=<level>` | Verbosity level |

### Running on ChromeOS

```bash
# SSH to DUT (Device Under Test)
ssh root@<chromeos-device>

# Run tests (pre-deployed)
/usr/local/libexec/chrome-binary-tests/video_decode_accelerator_tests \
  --test_video_data=/usr/local/share/chromium-test-data/bear.mp4
```

### Tast Integration Tests

ChromeOS uses Tast for integration testing:

```go
// Example Tast test structure
func VideoDecodeAccelH264(ctx context.Context, s *testing.State) {
    // Runs video_decode_accelerator_tests with H.264 content
}
```

---

## Summary

### Key Points for Vulkan Video Integration

1. **Reuse Existing Test Framework**: The test infrastructure (`DecoderListener`, `DecoderWrapper`, etc.) works with any `VideoDecoder` implementation.

2. **Test Content is Available**: Extensive test files for all codecs in `media/test/data/`.

3. **Validation Works**: MD5/SSIM validation compares HW output against reference.

4. **Fallback is Automatic**: `DecoderSelector` handles fallback to SW decoders.

5. **DRM Testing Exists**: Protected content testing infrastructure on ChromeOS.

### For Vulkan Video Testing

```cpp
// When VulkanVideoDecoder is implemented, tests would work like:

// 1. VulkanVideoDecoder implements VideoDecoder interface
// 2. VideoDecoderPipeline creates VulkanVideoDecoder (via factory)
// 3. Existing tests run against new decoder
// 4. Frame validation compares output to known checksums
// 5. Fallback to VA-API or SW if Vulkan fails

// No changes needed to test framework!
```

### Test Checklist for New Decoder

- [ ] Basic decode (flush at EOS)
- [ ] Reset during decode
- [ ] Resolution change handling
- [ ] All supported profiles (H.264 Baseline/Main/High, etc.)
- [ ] All bit depths (8-bit, 10-bit)
- [ ] Performance benchmarks
- [ ] Memory leak checks
- [ ] Error handling (corrupt streams)
- [ ] Protected content (if supported)

---

*Generated from Chromium source analysis*  
*Source: `/data/LinuxExtra/chrome/chromium/src`*

