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
# Chrome Vulkan Video Integration

Hardware-accelerated video decoding using Vulkan Video extensions for the Chromium browser.

[![GitLab Pages](https://img.shields.io/badge/docs-GitLab%20Pages-orange)](https://vulkan-video-external.gitlab-master.nvidia.com/chrome-vulkan-video-hw-accelerated/)

## Overview

This project implements Vulkan Video hardware acceleration for video decoding in Chromium, providing an alternative to VA-API and platform-specific backends. The goal is to leverage the cross-platform Vulkan Video extensions (`VK_KHR_video_queue`, `VK_KHR_video_decode_queue`, `VK_KHR_video_decode_h264`, `VK_KHR_video_decode_h265`, `VK_KHR_video_decode_av1`) for efficient GPU-accelerated video playback.

### Key Features

- **Cross-platform**: Works on any platform with Vulkan Video support
- **Modern API**: Uses Vulkan 1.3+ video extensions
- **Codec Support**: H.264, H.265/HEVC, AV1 decoding
- **Zero-copy**: DMA-BUF integration for efficient frame handling
- **Protected Content**: Future support for DRM/protected video playback

## Documentation

📚 **[View Full Documentation](https://vulkan-video-external.gitlab-master.nvidia.com/chrome-vulkan-video-hw-accelerated/)**

| Document | Description |
|----------|-------------|
| [Integration Design](docs/VULKAN_VIDEO_INTEGRATION_DESIGN_DOCUMENT.md) | Complete architecture and implementation details |
| [Development Plan](docs/VULKAN_VIDEO_DEVELOPMENT_PLAN.md) | Phased roadmap and milestones |
| [Presentation Layer](docs/CHROMIUM_PRESENTATION_LAYER.md) | Chromium graphics stack deep-dive |
| [VA-API Comparison](docs/VAAPI_vs_VULKAN_VIDEO_DETAILED_COMPARISON.md) | Detailed comparison with existing VA-API backend |
| [Test Infrastructure](docs/TEST_INFRASTRUCTURE_AND_FALLBACK.md) | Testing strategy and fallback mechanisms |
| [Encoder Use Cases & Processing Graphs](docs/VULKAN_VIDEO_ENCODER_PROCESSING_GRAPHS.md) | Browser encode use cases (WebRTC/WebCodecs/MediaRecorder/Cast), per-use-case frame-flow graphs, the SharedImage zero-copy seam and plug-in order (P0–P4), window-system matrix (Wayland/X11), GPU-RGB compute-filter path, browser test recipes and caveats C1–C14 ([HTML](docs/vulkan_video_encoder_use_cases.html)) |

## Quick Start

### Prerequisites

- **OS**: Ubuntu 20.04+ or Debian 11+
- **Disk Space**: 100GB+ free
- **RAM**: 16GB minimum (32GB recommended)
- **GPU**: Vulkan Video capable GPU (NVIDIA RTX 20+, AMD RDNA2+, Intel Arc)

### Automated Setup

```bash
# Clone the repository
git clone https://gitlab-master.nvidia.com/vulkan-video-external/chrome-vulkan-video-hw-accelerated.git
cd chrome-vulkan-video-hw-accelerated

# Run the setup script (installs deps, fetches depot_tools & chromium)
python3 setup_chromium.py
```

### Manual Setup

#### 1. Install Dependencies

```bash
# Install build essentials and Vulkan development packages
sudo apt-get update
sudo apt-get install -y \
    build-essential clang lld cmake ninja-build \
    libvulkan-dev vulkan-tools mesa-vulkan-drivers \
    libdrm-dev libgbm-dev libva-dev \
    git git-lfs curl python3
```

#### 2. Get depot_tools

```bash
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
export PATH="$PWD/depot_tools:$PATH"
```

#### 3. Fetch Chromium

```bash
mkdir chromium && cd chromium
fetch --nohooks --no-history chromium
gclient runhooks

# Install Chromium's build dependencies
cd src
./build/install-build-deps.sh --no-prompt
```

#### 4. Configure Build

```bash
cd chromium/src
gn gen out/Default --args='
  is_debug=false
  is_component_build=false
  symbol_level=0
  enable_nacl=false
  use_vulkan=true
  enable_vulkan_validation_layers=true
  use_vaapi=true
  proprietary_codecs=true
  ffmpeg_branding="Chrome"
'
```

#### 5. Build

```bash
autoninja -C out/Default chrome
```

> ⏱️ **Note**: Full build takes 2-6 hours depending on hardware.

#### 6. Run

```bash
./out/Default/chrome --enable-features=Vulkan,VaapiVideoDecoder
```

## Project Structure

```
chrome-vulkan-video-hw-accelerated/
├── setup_chromium.py       # Automated setup script
├── setup_env.sh            # Environment configuration
├── build-chrome.sh         # Quick build script
├── docs/                   # Documentation
│   ├── index.html          # Documentation portal
│   ├── diagrams/           # Architecture diagrams (SVG)
│   ├── video/              # Introduction videos
│   └── *.md                # Markdown documentation
├── chromium/               # Chromium source (after setup)
│   └── src/
│       └── media/gpu/      # Video acceleration code
└── depot_tools/            # Google's build tools (after setup)
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Chromium Browser                           │
├─────────────────────────────────────────────────────────────────┤
│  HTMLMediaElement → Media Pipeline → VideoDecoder               │
├─────────────────────────────────────────────────────────────────┤
│                    GPU Process                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              VideoDecoderPipeline                        │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │   │
│  │  │   VA-API    │  │  V4L2/VAAPI │  │  Vulkan Video   │  │   │
│  │  │  Backend    │  │   Backend   │  │    Backend      │  │   │
│  │  └──────┬──────┘  └──────┬──────┘  └────────┬────────┘  │   │
│  └─────────┼────────────────┼──────────────────┼───────────┘   │
├────────────┼────────────────┼──────────────────┼───────────────┤
│   Kernel   │                │                  │               │
│  ┌─────────▼──────┐  ┌──────▼──────┐  ┌───────▼────────────┐  │
│  │   VA-API       │  │    V4L2     │  │   Vulkan Driver    │  │
│  │   (libva)      │  │   Driver    │  │   (mesa/nvidia)    │  │
│  └────────────────┘  └─────────────┘  └────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Development

### Environment Setup

```bash
source setup_env.sh
```

### Incremental Builds

```bash
# Only rebuild changed components
autoninja -C out/Default chrome

# Build specific target
autoninja -C out/Default media/gpu:gpu
```

### Running Tests

```bash
# Media unit tests
autoninja -C out/Default media_unittests
./out/Default/media_unittests --gtest_filter="*Vulkan*"

# GPU tests
autoninja -C out/Default gpu_unittests
./out/Default/gpu_unittests
```

### Debug Build

```bash
gn gen out/Debug --args='
  is_debug=true
  symbol_level=2
  use_vulkan=true
  enable_vulkan_validation_layers=true
'
autoninja -C out/Debug chrome
```

## Contributing

1. Read the [Integration Design Document](docs/VULKAN_VIDEO_INTEGRATION_DESIGN_DOCUMENT.md)
2. Follow [Chromium's Contributing Guide](https://chromium.googlesource.com/chromium/src/+/main/docs/contributing.md)
3. Submit changes via GitLab merge requests

## Resources

- [Chromium Graphics Architecture](https://chromium.googlesource.com/chromium/src/+/main/docs/gpu/)
- [Vulkan Video Extensions](https://registry.khronos.org/vulkan/specs/1.3-extensions/html/chap46.html)
- [VA-API Documentation](https://github.com/intel/libva)
- [Skia GPU Backend](https://skia.org/docs/dev/design/gpu/)

## License

Project-owned integration components and documentation are licensed under
the [Apache License 2.0](LICENSE).

The Chromium source checkout remains governed by Chromium's BSD-style license
and applicable third-party licenses. See
[`chromium/src/LICENSE`](chromium/src/LICENSE).

---

**NVIDIA Vulkan Video External Team**

