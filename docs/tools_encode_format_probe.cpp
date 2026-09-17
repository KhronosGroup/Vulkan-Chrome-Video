// What does the driver actually accept as VIDEO_ENCODE_SRC for an H.264 profile?
// Determines whether a zero-copy path exists for an RGB producer at all, or
// whether RGB always requires an adaptation step at the encode site.
#include <vulkan/vulkan.h>
#include <cstdio>
#include <vector>
#include <map>
#include <string>

static const char* fmtName(VkFormat f) {
  switch (f) {
    case VK_FORMAT_G8_B8R8_2PLANE_420_UNORM: return "NV12 (G8_B8R8_2PLANE_420)";
    case VK_FORMAT_G8_B8_R8_3PLANE_420_UNORM: return "I420 (G8_B8_R8_3PLANE_420)";
    case VK_FORMAT_G10X6_B10X6R10X6_2PLANE_420_UNORM_3PACK16: return "P010";
    case VK_FORMAT_G12X4_B12X4R12X4_2PLANE_420_UNORM_3PACK16: return "P012";
    case VK_FORMAT_G8_B8R8_2PLANE_422_UNORM: return "NV16 (422)";
    case VK_FORMAT_G8_B8_R8_3PLANE_444_UNORM: return "444 3-plane";
    case VK_FORMAT_R8G8B8A8_UNORM: return "*** RGBA8 ***";
    case VK_FORMAT_B8G8R8A8_UNORM: return "*** BGRA8 ***";
    case VK_FORMAT_A2B10G10R10_UNORM_PACK32: return "*** A2B10G10R10 ***";
    case VK_FORMAT_R16G16B16A16_SFLOAT: return "*** RGBA16F ***";
    default: return "other";
  }
}

int main() {
  VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
  app.apiVersion = VK_API_VERSION_1_3;
  const char* iexts[] = {VK_KHR_GET_PHYSICAL_DEVICE_PROPERTIES_2_EXTENSION_NAME};
  VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
  ici.pApplicationInfo = &app;
  ici.enabledExtensionCount = 1; ici.ppEnabledExtensionNames = iexts;
  VkInstance inst{};
  if (vkCreateInstance(&ici, nullptr, &inst) != VK_SUCCESS) return 1;

  uint32_t n = 0; vkEnumeratePhysicalDevices(inst, &n, nullptr);
  std::vector<VkPhysicalDevice> devs(n);
  vkEnumeratePhysicalDevices(inst, &n, devs.data());
  VkPhysicalDevice phys = VK_NULL_HANDLE;
  for (auto d : devs) {
    VkPhysicalDeviceProperties p{}; vkGetPhysicalDeviceProperties(d, &p);
    if (p.vendorID == 0x10DE) { phys = d; printf("device: %s\n\n", p.deviceName); break; }
  }
  if (!phys) { printf("no NVIDIA device\n"); return 1; }

  auto pfn = (PFN_vkGetPhysicalDeviceVideoFormatPropertiesKHR)
      vkGetInstanceProcAddr(inst, "vkGetPhysicalDeviceVideoFormatPropertiesKHR");
  if (!pfn) { printf("vkGetPhysicalDeviceVideoFormatPropertiesKHR unavailable\n"); return 1; }

  struct Codec { const char* name; VkVideoCodecOperationFlagBitsKHR op; };
  const Codec codecs[] = {
    {"H.264", VK_VIDEO_CODEC_OPERATION_ENCODE_H264_BIT_KHR},
    {"H.265", VK_VIDEO_CODEC_OPERATION_ENCODE_H265_BIT_KHR},
    {"AV1",   VK_VIDEO_CODEC_OPERATION_ENCODE_AV1_BIT_KHR},
  };

  for (const auto& c : codecs) {
    VkVideoEncodeH264ProfileInfoKHR h264{VK_STRUCTURE_TYPE_VIDEO_ENCODE_H264_PROFILE_INFO_KHR};
    h264.stdProfileIdc = STD_VIDEO_H264_PROFILE_IDC_MAIN;
    VkVideoEncodeH265ProfileInfoKHR h265{VK_STRUCTURE_TYPE_VIDEO_ENCODE_H265_PROFILE_INFO_KHR};
    h265.stdProfileIdc = STD_VIDEO_H265_PROFILE_IDC_MAIN;
    VkVideoEncodeAV1ProfileInfoKHR av1{VK_STRUCTURE_TYPE_VIDEO_ENCODE_AV1_PROFILE_INFO_KHR};
    av1.stdProfile = STD_VIDEO_AV1_PROFILE_MAIN;

    VkVideoProfileInfoKHR prof{VK_STRUCTURE_TYPE_VIDEO_PROFILE_INFO_KHR};
    prof.videoCodecOperation = c.op;
    prof.chromaSubsampling = VK_VIDEO_CHROMA_SUBSAMPLING_420_BIT_KHR;
    prof.lumaBitDepth = VK_VIDEO_COMPONENT_BIT_DEPTH_8_BIT_KHR;
    prof.chromaBitDepth = VK_VIDEO_COMPONENT_BIT_DEPTH_8_BIT_KHR;
    if (c.op == VK_VIDEO_CODEC_OPERATION_ENCODE_H264_BIT_KHR) prof.pNext = &h264;
    else if (c.op == VK_VIDEO_CODEC_OPERATION_ENCODE_H265_BIT_KHR) prof.pNext = &h265;
    else prof.pNext = &av1;

    VkVideoProfileListInfoKHR plist{VK_STRUCTURE_TYPE_VIDEO_PROFILE_LIST_INFO_KHR};
    plist.profileCount = 1; plist.pProfiles = &prof;

    VkPhysicalDeviceVideoFormatInfoKHR vfi{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VIDEO_FORMAT_INFO_KHR};
    vfi.pNext = &plist;
    vfi.imageUsage = VK_IMAGE_USAGE_VIDEO_ENCODE_SRC_BIT_KHR;

    uint32_t cnt = 0;
    VkResult r = pfn(phys, &vfi, &cnt, nullptr);
    printf("=== %s  VIDEO_ENCODE_SRC formats: %u (result %d) ===\n", c.name, cnt, r);
    if (r != VK_SUCCESS || cnt == 0) { printf("\n"); continue; }
    std::vector<VkVideoFormatPropertiesKHR> props(cnt,
        {VK_STRUCTURE_TYPE_VIDEO_FORMAT_PROPERTIES_KHR});
    pfn(phys, &vfi, &cnt, props.data());
    for (auto& p : props) {
      printf("   format=%-4d %-28s tiling=%s\n",
             (int)p.format, fmtName(p.format),
             p.imageTiling == VK_IMAGE_TILING_OPTIMAL ? "OPTIMAL"
               : p.imageTiling == VK_IMAGE_TILING_LINEAR ? "LINEAR" : "DRM_MODIFIER");
    }
    printf("\n");
  }
  return 0;
}
