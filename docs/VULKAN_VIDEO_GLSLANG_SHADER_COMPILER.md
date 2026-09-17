<!--
title: Replacing shaderc with glslang in the NVPRO sample library
status: implementation guide
date: 2026-07-31
scope: vk_video_samples (standalone) + third_party/vulkan_video_samples (Chromium vendored)
-->

# Replacing shaderc with glslang in `VulkanShaderCompiler`

<!-- DOC-STATUS -->
> **Status: CURRENT.** Implementation guide for the shader-compiler seam.
> Index: [`README.md`](README.md).


A concrete implementation guide for dropping the shaderc dependency from the standalone
Vulkan-Video-Samples library and compiling GLSL through glslang directly.

**Goal:** the standalone NVPRO sample library uses glslang, not shaderc.

**As implemented, shaderc was kept as an opt-in backend rather than removed outright.** The
original position here was one compiler exercised by every build. That does not survive contact
with the consumer list: `VulkanShaderCompiler.cpp` is compiled not only by this tree but by
ThreadedRenderingVk and by aq-vulkan in *both* the internal AQ repo and the customer-delivered
package, which has its own release cadence. glslang is the default everywhere and is what every
build of this tree uses; `-DVK_VIDEO_SAMPLES_SHADER_BACKEND=shaderc` lets a downstream tree
migrate on its own schedule. The seam also pays for itself in verification — the same test suite
runs on both backends and the outputs are compared directly (§7).

**Why it matters beyond tidiness:** shaderc is what keeps the encoder's preprocess compute
filter out of Chromium, and the filter is what would let the encoder accept RGBA input directly
instead of forcing a CPU or pre-pass conversion. See
`VULKAN_VIDEO_ENCODER_DEVICE_INDEPENDENCE_PLAN`
§3.4.

---

## 1. Why glslang rather than shaderc

shaderc **is** glslang. It is a convenience wrapper providing a stable C API, `#include`
resolution, and an optimisation pass driven by SPIRV-Tools. The library uses none of those
extras — no includes, no optimisation, no C-API stability requirement, since it is C++ already.

| | shaderc | glslang |
| --- | --- | --- |
| Relationship | wrapper over glslang | the actual compiler |
| In the Chromium tree | **absent** — not in `DEPS`, not in any `.gn` | **present**, `//third_party/glslang/src`, unrestricted GN visibility |
| Chromium review cost | new third-party package: ATL approval, security review, checkout-size and build-time justification | `Shipped: no → yes` flip on an already-`Security Critical: yes` component: security review + binary-size accounting |
| Standalone availability | Vulkan SDK | distro packages (`libglslang-dev`) **and** the Vulkan SDK |

Both machines in this project already have it:

```
/usr/include/glslang/Public/ShaderLang.h
/usr/include/glslang/Public/ResourceLimits.h
/usr/include/glslang/SPIRV/GlslangToSpv.h
/usr/lib/x86_64-linux-gnu/libglslang.so.15.3.0
/usr/lib/x86_64-linux-gnu/libglslang-default-resource-limits.so
/usr/lib/x86_64-linux-gnu/libSPIRV.so
```

---

## 2. What has to change — the seam is already narrow

`common/libs/VkCodecUtils/VulkanShaderCompiler.h` is 47 lines and **does not include shaderc**.
It exposes two methods and stores the compiler as an opaque `void*`:

```cpp
class VulkanShaderCompiler {
public:
    VulkanShaderCompiler();
    ~VulkanShaderCompiler();

    VkShaderModule BuildGlslShader(const char *shaderCode, size_t shaderSize,
                                   VkShaderStageFlagBits type,
                                   const VulkanDeviceContext* vkDevCtx);

    VkShaderModule BuildShaderFromFile(const char *fileName, VkShaderStageFlagBits type,
                                       const VulkanDeviceContext* vkDevCtx);
private:
    static void* GetSharedCompiler();
    static void  ReleaseSharedCompiler();
    void* compilerHandle;
};
```

**The entire dependency is confined to the 178-line `VulkanShaderCompiler.cpp`.** The header
does not change at all, and no caller changes. Callers are:

| Caller | Which method |
| --- | --- |
| `VulkanComputePipeline.cpp:51` | `BuildGlslShader` |
| `VulkanFilter.h:64` | `BuildGlslShader` — this is the compute-filter path |
| `VulkanVideoUtils.cpp:527,534` | `BuildGlslShader` |
| `VulkanVideoUtils.cpp:516,521` | `BuildShaderFromFile` — see §6 |

### The shaderc surface actually used — twelve entry points

| shaderc | Purpose |
| --- | --- |
| `shaderc_compiler_initialize` / `_release` | singleton lifetime |
| `shaderc_compile_options_initialize` / `_release` | per-compile options |
| `shaderc_compile_options_set_target_env` | `shaderc_target_env_vulkan`, `shaderc_env_version_vulkan_1_2` |
| `shaderc_compile_options_set_target_spirv` | `shaderc_spirv_version_1_5` |
| `shaderc_compile_into_spv` | the compile |
| `shaderc_result_get_compilation_status` | success check |
| `shaderc_result_get_error_message` | diagnostics |
| `shaderc_result_get_length` / `_get_bytes` | SPIR-V out |
| `shaderc_result_release` | result lifetime |
| `shaderc_glsl_*_shader` (6 enums) | stage mapping |

That is the whole contract to reproduce. Note the current targets — **Vulkan 1.2 / SPIR-V 1.5** —
must be preserved exactly, or the generated modules change behaviour.

---

## 3. The mapping

| shaderc | glslang |
| --- | --- |
| `shaderc_compiler_initialize()` | `glslang::InitializeProcess()` |
| `shaderc_compiler_release()` | `glslang::FinalizeProcess()` |
| `shaderc_compile_into_spv()` | `TShader::parse()` → `TProgram::link()` → `GlslangToSpv()` |
| `set_target_env(vulkan, vulkan_1_2)` | `setEnvClient(EShClientVulkan, EShTargetVulkan_1_2)` |
| `set_target_spirv(spirv_1_5)` | `setEnvTarget(EShTargetSpv, EShTargetSpv_1_5)` |
| `shaderc_glsl_compute_shader` | `EShLangCompute` |
| `shaderc_glsl_vertex_shader` | `EShLangVertex` |
| `shaderc_glsl_fragment_shader` | `EShLangFragment` |
| `shaderc_glsl_geometry_shader` | `EShLangGeometry` |
| `shaderc_glsl_tess_control_shader` | `EShLangTessControl` |
| `shaderc_glsl_tess_evaluation_shader` | `EShLangTessEvaluation` |
| `result_get_error_message()` | `TShader::getInfoLog()` + `getInfoDebugLog()`, `TProgram::getInfoLog()` |
| `result_get_bytes()` / `_get_length()` | `std::vector<uint32_t>` filled by `GlslangToSpv` |
| `shaderc_result_release()` | nothing — the vector owns it |

One thing shaderc does implicitly that glslang requires explicitly: **built-in resource
limits.** shaderc supplies a default `TBuiltInResource`; with glslang you must pass one, and
`GetDefaultResources()` from `glslang/Public/ResourceLimits.h` is exactly that default.
Omitting it produces spurious "too many uniform components" style errors on perfectly valid
shaders.

**It is declared at global scope, not in namespace `glslang`** — writing
`glslang::GetDefaultResources()` does not compile. Confirmed against the installed 15.3.0
headers and by the symbol itself, `_Z19GetDefaultResourcesv` in
`libglslang-default-resource-limits.so.15.3.0`, whose mangling carries no namespace.

---

## 4. Reference implementation

Replaces the body of `VulkanShaderCompiler.cpp`. The header is untouched.

```cpp
#include <glslang/Public/ShaderLang.h>
#include <glslang/Public/ResourceLimits.h>
#include <glslang/SPIRV/GlslangToSpv.h>

// glslang::InitializeProcess() is process-global and must be balanced by
// FinalizeProcess(). The existing refcount already gives us that shape, so it
// is reused verbatim -- only what it guards changes.
static std::mutex        g_compilerMutex;
static std::atomic<int>  g_compilerRefCount{0};
static bool              g_glslangInitialized = false;

static EShLanguage getGlslangStage(VkShaderStageFlagBits type)
{
    switch (type) {
    case VK_SHADER_STAGE_VERTEX_BIT:                  return EShLangVertex;
    case VK_SHADER_STAGE_FRAGMENT_BIT:                return EShLangFragment;
    case VK_SHADER_STAGE_TESSELLATION_CONTROL_BIT:    return EShLangTessControl;
    case VK_SHADER_STAGE_TESSELLATION_EVALUATION_BIT: return EShLangTessEvaluation;
    case VK_SHADER_STAGE_GEOMETRY_BIT:                return EShLangGeometry;
    case VK_SHADER_STAGE_COMPUTE_BIT:                 return EShLangCompute;
    default:
        std::cerr << "VulkanShaderCompiler: invalid VkShaderStageFlagBits type = "
                  << type << std::endl;
        return EShLangCount;   // sentinel; rejected below
    }
}

void* VulkanShaderCompiler::GetSharedCompiler() {
    std::lock_guard<std::mutex> lock(g_compilerMutex);
    if (g_compilerRefCount == 0) {
        if (!glslang::InitializeProcess()) {
            std::cerr << "VulkanShaderCompiler: glslang::InitializeProcess() failed!"
                      << std::endl;
            return nullptr;
        }
        g_glslangInitialized = true;
    }
    g_compilerRefCount++;
    // glslang has no compiler object to hand back. Return a non-null token so
    // the existing "did we get a compiler" checks keep working unchanged.
    return reinterpret_cast<void*>(0x1);
}

void VulkanShaderCompiler::ReleaseSharedCompiler() {
    std::lock_guard<std::mutex> lock(g_compilerMutex);
    if (--g_compilerRefCount == 0 && g_glslangInitialized) {
        glslang::FinalizeProcess();
        g_glslangInitialized = false;
    }
}

VkShaderModule VulkanShaderCompiler::BuildGlslShader(const char *shaderCode, size_t shaderSize,
                                                     VkShaderStageFlagBits type,
                                                     const VulkanDeviceContext* vkDevCtx)
{
    if (compilerHandle == nullptr) {
        return VK_NULL_HANDLE;
    }
    const EShLanguage stage = getGlslangStage(type);
    if (stage == EShLangCount) {
        return VK_NULL_HANDLE;
    }

    std::vector<uint32_t> spirv;
    {
        // glslang's parser is not re-entrant across threads for a shared
        // process state, and the shaderc version serialised the whole compile
        // under this same mutex, so behaviour is preserved rather than widened.
        std::lock_guard<std::mutex> lock(g_compilerMutex);

        glslang::TShader shader(stage);

        const char* sources[1]    = { shaderCode };
        const int   lengths[1]    = { static_cast<int>(shaderSize) };
        shader.setStringsWithLengths(sources, lengths, 1);

        shader.setEntryPoint("main");
        shader.setSourceEntryPoint("main");

        // Must match the shaderc settings exactly: Vulkan 1.2, SPIR-V 1.5.
        shader.setEnvInput(glslang::EShSourceGlsl, stage, glslang::EShClientVulkan, 100);
        shader.setEnvClient(glslang::EShClientVulkan, glslang::EShTargetVulkan_1_2);
        shader.setEnvTarget(glslang::EShTargetSpv,   glslang::EShTargetSpv_1_5);

        const EShMessages messages =
            static_cast<EShMessages>(EShMsgSpvRules | EShMsgVulkanRules);

        // 450 is the fallback only for source with no #version directive; the
        // library's generated shaders all declare one, which wins.
        if (!shader.parse(GetDefaultResources(), 450, ENoProfile,
                          false /*forceDefaultVersionAndProfile*/,
                          false /*forwardCompatible*/, messages)) {
            std::cerr << "Compilation error: \n"
                      << shader.getInfoLog() << "\n"
                      << shader.getInfoDebugLog() << std::endl;
            return VK_NULL_HANDLE;
        }

        glslang::TProgram program;
        program.addShader(&shader);
        if (!program.link(messages)) {
            std::cerr << "Link error: \n" << program.getInfoLog() << std::endl;
            return VK_NULL_HANDLE;
        }

        glslang::SpvOptions spvOptions;
        spvOptions.disableOptimizer = true;   // shaderc did no optimisation either
        spvOptions.generateDebugInfo = false;
        spvOptions.stripDebugInfo = false;
        spv::SpvBuildLogger logger;

        glslang::GlslangToSpv(*program.getIntermediate(stage), spirv, &logger, &spvOptions);

        const std::string spvMessages = logger.getAllMessages();
        if (!spvMessages.empty()) {
            std::cerr << "SPIR-V generation: " << spvMessages << std::endl;
        }
        if (spirv.empty()) {
            std::cerr << "SPIR-V generation produced no code" << std::endl;
            return VK_NULL_HANDLE;
        }
    }

    VkShaderModuleCreateInfo shaderModuleCreateInfo = VkShaderModuleCreateInfo();
    shaderModuleCreateInfo.sType    = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
    shaderModuleCreateInfo.pNext    = nullptr;
    shaderModuleCreateInfo.flags    = 0;
    shaderModuleCreateInfo.codeSize = spirv.size() * sizeof(uint32_t);  // BYTES, not words
    shaderModuleCreateInfo.pCode    = spirv.data();

    VkShaderModule shaderModule = VK_NULL_HANDLE;
    VkResult result = vkDevCtx->CreateShaderModule(*vkDevCtx, &shaderModuleCreateInfo,
                                                   nullptr, &shaderModule);
    assert(result == VK_SUCCESS);
    if (result != VK_SUCCESS) {
        std::cerr << "Failed to create shader module" << std::endl;
        return VK_NULL_HANDLE;
    }
    return shaderModule;
}
```

### Details that are easy to get wrong

1. **`codeSize` is bytes, shaderc's `get_length()` was already bytes.** `spirv.size()` is
   **words**. Forgetting `* sizeof(uint32_t)` yields a module that fails validation or, worse,
   silently truncates.
2. **`GetDefaultResources()` is mandatory** (§3).
3. **`EShMsgSpvRules | EShMsgVulkanRules` are mandatory.** Without them glslang compiles for
   OpenGL semantics and rejects Vulkan-only constructs such as `layout(set = …)` and push
   constants.
4. **`setEnvInput`'s trailing `100`** is the client input semantics version, not a GLSL version.
   It is `100` for Vulkan regardless of the shader's `#version`.
5. **`InitializeProcess()`/`FinalizeProcess()` are process-global**, not per-object. The existing
   refcount already has the right shape; reuse it rather than inventing a new one.
6. **`TShader` and `TProgram` must outlive `GlslangToSpv`** — `program.getIntermediate()` returns
   a pointer into the program. Keeping both in the same scope as the call, as above, is what
   makes that safe.
7. **`disableOptimizer = true`** keeps parity. Leaving the optimiser on would pull in SPIRV-Tools,
   which is a dependency we do not otherwise need.

---

## 5. Build system

### Standalone CMake

`cmake/FindVulkanAndDependencies.cmake` currently sets `USE_SYSTEM_SHADERC`. Replace the shaderc
discovery with glslang, and drop the shaderc link from every target that has it —
`vk_video_encoder/libs`, `vk_video_encoder/demos/vk-video-enc`, `common/libs/tests`,
`common/libs/tests/drm_format_mod`, `common/libs/tests/win32_opaque_import`,
`vk_video_decoder/libs`, `vk_video_decoder/demos/vk-video-dec`,
`vk_video_decoder/test/vulkan-video-dec`.

**That list is short by two.** `vk_video_decoder/test/vulkan-video-simple-dec` and
`vk_video_encoder/test/vulkan-video-enc` also link shaderc without compiling
`VulkanShaderCompiler.cpp` at all, so they need the link deleted rather than substituted — ten
sites in total. Each also carries an `add_dependencies(... ${SHADERC_LIB})` that must go with
it, or a stale `SHADERC_LIB=shaderc_shared` cache entry makes the build fail on a target that no
longer exists.

**There is a second, always-executed shaderc block** further down the same module (after the
system/from-source branches) that ends in
`message(FATAL_ERROR "Could not find or build Shaderc")`. It is easy to miss and it alone keeps
the tree unconfigurable without shaderc even after every link is gone.

```cmake
find_package(glslang CONFIG QUIET)
if(TARGET glslang::glslang AND TARGET glslang::glslang-default-resource-limits)
    set(GLSLANG_LIBS glslang::glslang glslang::glslang-default-resource-limits)
else()
    find_library(GLSLANG_LIB glslang REQUIRED)
    find_library(GLSLANG_RES glslang-default-resource-limits REQUIRED)
    find_path(GLSLANG_INCLUDE_DIR NAMES glslang/Public/ShaderLang.h REQUIRED)
    set(GLSLANG_LIBS ${GLSLANG_LIB} ${GLSLANG_RES})
endif()
```

**Do not link `SPIRV`.** An earlier draft of this guide probed for it and advised ordering
`SPIRV` before `glslang`; that is wrong for the glslang shipped here. On 15.3.0 a `libSPIRV`
still exists but is a stub: `libSPIRV.so.15.3.0` exports **zero** symbols and `libSPIRV.a` is
1244 bytes. `GlslangToSpv` is defined in `libglslang`. Adding SPIRV is useless in the plain
discovery path and, through the CONFIG package, can drag in a target that resolves to nothing.

**Version note.** glslang 14 merged the standalone `SPIRV` library into `glslang`, and
`GetDefaultResources()` moved to `glslang/Public/ResourceLimits.h` in the
`glslang-default-resource-limits` library. Both machines here carry glslang 15.3.0, so the
merged layout is what applies.

### Chromium GN

Add to `third_party/vulkan_video_samples/BUILD.gn`:

```gn
deps = [
  "//third_party/glslang/src:glslang_lib_sources",
  "//third_party/glslang/src:glslang_default_resource_limits_sources",
]
```

Note the `/src` — there is **no** `BUILD.gn` at `//third_party/glslang`, and
`//third_party/vulkan-deps/glslang` is a metadata stub with no sources. Both are easy wrong
turns.

Then add `VulkanShaderCompiler.cpp`, `VulkanComputePipeline.cpp` and
`VulkanFilterYuvCompute.cpp` to the source lists, and define
`VK_VIDEO_SAMPLES_COMPUTE_FILTER_SUPPORTED` in `private_includes`, replacing the comment that
currently explains why it is absent.

The Chromium-side prerequisite is a `Shipped: no → yes` flip in
`third_party/glslang/README.chromium`, which needs a security review and binary-size accounting.
See the device-independence plan §3.4 for the Rule-of-2 position and the two stale citations to
avoid.

---

## 6. Clean-ups to fold in

**`BuildShaderFromFile` is dead code — deleted ahead of the port** in commit `2729135a`. Its
entire body was wrapped in `#ifdef seekg`; `seekg` is an `std::istream` member function, never a
macro, so the guard could not be true and the function unconditionally returned
`VK_NULL_HANDLE`. Its call site was equally unreachable, gated on
`const bool loadShadersFromFile = false;`. Removing it first left the port one entry point to
reproduce instead of two, and kept the `#ifdef` from being carried forward. Verified safe: no
caller exists in any consumer tree (this one, ThreadedRenderingVk, aq-vulkan in either AQ repo,
or the Chromium copy), and all four compile clean with the declaration gone.

**A stale `#include <shaderc/shaderc.hpp>` in `VulkanVideoUtils.cpp`.** The file references no
shaderc symbol — it only uses `VulkanShaderCompiler` — but the include forces shaderc *headers*
onto four additional targets that would otherwise be shaderc-free. Delete it.

**The `"shaderc_error"` string literal** passed as `compile_into_spv`'s `input_file_name`
argument appears in diagnostics. glslang has no equivalent parameter; the shader name simply
does not appear in the log, which is no worse — the current value is not a filename anyway.

---

## 7. Verification

The library has direct test coverage for exactly this path, which is the reason to do the port
in the standalone tree first.

1. **`vk_filter_test`** (`common/libs/tests/`) drives `VulkanFilterYuvCompute` through
   `TestCases.cpp`, which has ~30 cases spanning `RGBA2YCBCR`, `YCBCR2RGBA`, `YCBCRCOPY`,
   `YCBCRCLEAR` and the three `XFER_*` transfer modes. Every one compiles GLSL at runtime through
   `VulkanShaderCompiler`. **This is the primary acceptance gate.**
2. **A/B the emitted SPIR-V.** Before/after on the same generated GLSL, disassembled with
   `spirv-dis`. Expect non-identical output — shaderc and a bare glslang differ in generator magic
   number and can differ in ID numbering — so compare *behaviour*, not bytes: same entry point,
   same descriptor bindings, same `LocalSize`.
3. **`vk-video-enc-test` with `--inputNumPlanes 3`.** The 3-plane I420 path is the one that
   exercises the filter for real. Note it must be run with the filter enabled: with it compiled
   out this input hangs the GPU, verified on hardware — see the device-independence plan §8.1.
4. **The decoder's post-process filter** (`--enablePostProcessFilter`) uses the same compiler and
   should be run too; it is ungated and so already active in every standalone build.

Run each on `zlat-vm-gpu-ub-test` (RTX 5080 / RTX 3080 Ti, driver 620.05), which has glslang dev
packages, Vulkan Video encode, and the project NFS-mounted at the same path.

### Result (measured)

Run on `zlat-vm-gpu-ub-test`, RTX 5080, glslang 15.3.0, Release, both backends built from the
same tree via `-DVK_VIDEO_SAMPLES_SHADER_BACKEND`:

| Gate | shaderc | glslang |
| --- | --- | --- |
| `vk_filter_test --all` | 49/49 pass | 49/49 pass |
| h264 3-plane I420 encode | md5 `4fac5fd7c1ae34d9` | **identical** |
| hevc 3-plane I420 encode | md5 `5b72505586206e48` | **identical** |
| av1 3-plane I420 encode | md5 `8e853d3f7cde68b1` | **identical** |
| h264 2-plane encode | md5 `cccf92b8c4cfd78d` | **identical** |
| decoder `--enablePostProcessFilter` | md5 `e37503f6a91b1875` | **identical** |
| build warnings | 0 | 0 |

Note what this does and does not say. The *encoded bitstreams and decoded output* are
byte-identical, which is the end-to-end property that matters: the shaders behave identically
through the whole filter path. It is not a claim that the SPIR-V modules are byte-identical —
they are not expected to be (generator magic number, ID numbering), which is exactly why the
comparison is done on encoder/decoder output rather than on the modules.

The backend swap itself is confirmed in the binaries: the shaderc build links
`libshaderc_shared.so.1` and exports `shaderc_compile_into_spv`; the glslang build has **zero**
shaderc symbols.

Two gotchas met while doing this. The decoder appends `.y4m` and renames the output when y4m is
the format, so a harness looking for the exact `-o` name finds nothing and silently reports an
empty result — check for the renamed file. And the filter's generated GLSL is only printed when
`static bool dumpShaders` in `VulkanFilterYuvCompute.cpp` is flipped by hand; there is no CLI
flag, so "the filter ran" has to be established from the 3-plane encode succeeding and from
`vk_filter_test`, not from a log line.

---

## 8. Sequencing

1. Port `VulkanShaderCompiler.cpp` to glslang; header and callers untouched.
2. Switch standalone CMake from shaderc to glslang; remove every shaderc link.
3. Green `vk_filter_test`, plus the encoder and decoder filter paths (§7).
4. Delete `BuildShaderFromFile` and its Android-path callers.
5. Roll into the Chromium vendored copy; add the GN deps and the filter sources; define the macro.
6. File the `Shipped: no → yes` flip for `third_party/glslang`.

Steps 1–4 are entirely within the standalone library and deliver the "NVPRO samples use glslang"
outcome on their own. Steps 5–6 are the Chromium consequence and can follow independently.

---

## 9. References

- `visgarg/src/docs/media/gpu/vulkan_video_encode_design.md` §9 and §9.1 — the Chromium-side
  position and review cost
- glslang API: `glslang/Public/ShaderLang.h`, `glslang/Public/ResourceLimits.h`,
  `glslang/SPIRV/GlslangToSpv.h`
