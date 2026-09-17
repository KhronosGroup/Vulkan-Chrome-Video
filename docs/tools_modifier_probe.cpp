// Replicates the exact vkGetPhysicalDeviceImageFormatProperties2 query shape
// used by VulkanVideoEncoderExtImpl::ModifierWouldRegister(), to settle whether
// omitting VkVideoProfileListInfoKHR with VIDEO_ENCODE_SRC usage is actually a
// spec violation on this driver, or whether VIDEO_PROFILE_INDEPENDENT excuses it.
//
// Three variants:
//   A: no profile list, no VIDEO_PROFILE_INDEPENDENT   <- what the library does today
//   B: no profile list, WITH VIDEO_PROFILE_INDEPENDENT <- the maintenance1 escape hatch
//   C: WITH profile list, no VIDEO_PROFILE_INDEPENDENT <- the by-the-book form
#include <vulkan/vulkan.h>
#include <cstdio>
#include <cstring>
#include <vector>
#include <string>

static std::vector<std::string> g_msgs;

static VKAPI_ATTR VkBool32 VKAPI_CALL dbg(
    VkDebugUtilsMessageSeverityFlagBitsEXT, VkDebugUtilsMessageTypeFlagsEXT,
    const VkDebugUtilsMessengerCallbackDataEXT* d, void*) {
  if (d && d->pMessage) g_msgs.push_back(d->pMessage);
  return VK_FALSE;
}

int main() {
  const char* layers[] = {"VK_LAYER_KHRONOS_validation"};
  const char* exts[]   = {VK_EXT_DEBUG_UTILS_EXTENSION_NAME,
                          VK_KHR_GET_PHYSICAL_DEVICE_PROPERTIES_2_EXTENSION_NAME};
  VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
  app.apiVersion = VK_API_VERSION_1_3;
  VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
  ici.pApplicationInfo = &app;
  ici.enabledLayerCount = 1;       ici.ppEnabledLayerNames = layers;
  ici.enabledExtensionCount = 2;   ici.ppEnabledExtensionNames = exts;

  VkInstance inst{};
  if (vkCreateInstance(&ici, nullptr, &inst) != VK_SUCCESS) {
    printf("vkCreateInstance failed\n"); return 1;
  }
  auto pfnCreate = (PFN_vkCreateDebugUtilsMessengerEXT)
      vkGetInstanceProcAddr(inst, "vkCreateDebugUtilsMessengerEXT");
  VkDebugUtilsMessengerCreateInfoEXT dci{VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT};
  dci.messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT |
                        VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT;
  dci.messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT |
                    VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT;
  dci.pfnUserCallback = dbg;
  VkDebugUtilsMessengerEXT msgr{};
  if (pfnCreate) pfnCreate(inst, &dci, nullptr, &msgr);

  uint32_t n = 0; vkEnumeratePhysicalDevices(inst, &n, nullptr);
  std::vector<VkPhysicalDevice> devs(n);
  vkEnumeratePhysicalDevices(inst, &n, devs.data());
  VkPhysicalDevice phys = VK_NULL_HANDLE;
  for (auto d : devs) {
    VkPhysicalDeviceProperties p{}; vkGetPhysicalDeviceProperties(d, &p);
    if (p.vendorID == 0x10DE) { phys = d; printf("device: %s\n", p.deviceName); break; }
  }
  if (!phys) { printf("no NVIDIA device\n"); return 1; }

  auto pfnFmt2 = (PFN_vkGetPhysicalDeviceImageFormatProperties2)
      vkGetInstanceProcAddr(inst, "vkGetPhysicalDeviceImageFormatProperties2");

  // H.264 encode profile, as the encoder would use.
  VkVideoEncodeH264ProfileInfoKHR h264{VK_STRUCTURE_TYPE_VIDEO_ENCODE_H264_PROFILE_INFO_KHR};
  h264.stdProfileIdc = STD_VIDEO_H264_PROFILE_IDC_MAIN;
  VkVideoProfileInfoKHR prof{VK_STRUCTURE_TYPE_VIDEO_PROFILE_INFO_KHR};
  prof.pNext = &h264;
  prof.videoCodecOperation = VK_VIDEO_CODEC_OPERATION_ENCODE_H264_BIT_KHR;
  prof.chromaSubsampling = VK_VIDEO_CHROMA_SUBSAMPLING_420_BIT_KHR;
  prof.lumaBitDepth = VK_VIDEO_COMPONENT_BIT_DEPTH_8_BIT_KHR;
  prof.chromaBitDepth = VK_VIDEO_COMPONENT_BIT_DEPTH_8_BIT_KHR;
  VkVideoProfileListInfoKHR plist{VK_STRUCTURE_TYPE_VIDEO_PROFILE_LIST_INFO_KHR};
  plist.profileCount = 1; plist.pProfiles = &prof;

  struct Variant { const char* name; bool profileList; bool profileIndependent; };
  const Variant variants[] = {
    {"A: no profile list, no VIDEO_PROFILE_INDEPENDENT  (library today)", false, false},
    {"B: no profile list, WITH VIDEO_PROFILE_INDEPENDENT (maintenance1)", false, true },
    {"C: WITH profile list, no VIDEO_PROFILE_INDEPENDENT (by the book)",  true,  false},
    {"D: CONTROL usage=0 -- must trip a VUID or the harness is broken", false, false},
  };

  for (const auto& v : variants) {
    g_msgs.clear();

    // Same chain ModifierWouldRegister builds: imageInfo -> modifierInfo -> extInfo
    VkPhysicalDeviceExternalImageFormatInfo extInfo{
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO};
    extInfo.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;

    VkPhysicalDeviceImageDrmFormatModifierInfoEXT modInfo{
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT};
    modInfo.drmFormatModifier = 0x0300000000606011ULL;
    modInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    modInfo.pNext = &extInfo;

    VkPhysicalDeviceImageFormatInfo2 ii{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2};
    ii.pNext  = &modInfo;
    if (v.profileList) { plist.pNext = ii.pNext; ii.pNext = &plist; }
    ii.format = VK_FORMAT_G8_B8R8_2PLANE_420_UNORM;
    ii.type   = VK_IMAGE_TYPE_2D;
    ii.tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT;
    ii.usage  = (v.name[0] == 68) ? 0u : (VkImageUsageFlags)VK_IMAGE_USAGE_VIDEO_ENCODE_SRC_BIT_KHR;
    ii.flags  = v.profileIndependent
                  ? VK_IMAGE_CREATE_VIDEO_PROFILE_INDEPENDENT_BIT_KHR : 0;

    VkExternalImageFormatProperties extProps{
        VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES};
    VkImageFormatProperties2 out{VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2};
    out.pNext = &extProps;

    VkResult r = pfnFmt2(phys, &ii, &out);

    printf("\n--- %s\n", v.name);
    printf("    VkResult = %d (%s)\n", r,
           r == VK_SUCCESS ? "SUCCESS"
                           : r == VK_ERROR_FORMAT_NOT_SUPPORTED ? "FORMAT_NOT_SUPPORTED" : "other");
    printf("    importable = %s\n",
           (extProps.externalMemoryProperties.externalMemoryFeatures &
            VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT) ? "YES" : "no");
    if (g_msgs.empty()) {
      printf("    validation: clean\n");
    } else {
      for (auto& m : g_msgs) {
        const char* vu = strstr(m.c_str(), "VUID-");
        printf("    validation: %.*s\n", vu ? 60 : 100, vu ? vu : m.c_str());
      }
    }
  }
  printf("\n");
  return 0;
}
