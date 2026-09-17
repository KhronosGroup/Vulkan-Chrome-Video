# Encoder Ext API — interface style: `sType`/`pNext` descriptors vs. C++ abstract interfaces

<!-- DOC-STATUS -->
> **Status: CURRENT.** Style rules the interface is held to.
> Index: [`README.md`](README.md).


**Status:** proposal, for after the RC-6 review and rebase land.
**Supersedes nothing.** It revisits the ABI-versioning decision in the light of
what the implementation now looks like and where the API is actually consumed.

---

## 0. The question

The encoder ext API is shaped like Vulkan: flat C structs, each stamped with a
`VkVideoEncoderStructureType sType`, extended by `pNext` chains, driven by free functions.

The rest of this library is not shaped that way. It uses virtual, abstract, reference-counted
C++ interfaces.

The question is whether the ext surface should follow the house idiom instead, and if so, where
the descriptor style still earns its place.

---

## 1. The current shape, measured

Counted at `RC-6-lib-v3+nvpro-rebase+split`:

| | |
|---|---|
| `VkVideoEncoderStructureType` enumerants | **25** |
| structs carrying `sType` + `pNext` | **24** |
| public header `vulkan_video_encoder_ext.h` | **3,096 lines** |
| internal header `vulkan_video_encoder_ext_internal.h` | **743 lines** |
| free `VkEnc*` entry points | **4** |
| `pNext` mentions in `vulkan_video_encoder_ext.cpp` | **86** |
| explicit `sType ==` / `sType !=` comparisons | **24** |

The 25 structure types:

```
CAPABILITIES              COMPLETION_INFO           CONFIG
CONTEXT_CREATE_INFO       DEVICE_IDENTITY           DIAGNOSTIC_INFO
ENCODE_RESULT             EXTERNAL_IMAGE_DESCRIPTOR FILTER_INFO
FRAME_DEADLINE_INFO       FRAME_FENCE_DESCRIPTOR    FRAME_PARAMS
FRAME_SYNC_DESCRIPTOR     IMAGE_SUPPORT             IMAGE_SUPPORT_DETAILS
IMPORT_CONTENT_INFO       IMPORT_GUARD_INFO         INPUT_FRAME
INPUT_RESIDENCY_INFO      RUNTIME_INFO              SEMAPHORE_DESCRIPTOR
STAGED_SUBMIT_INFO        STATUS                    VALIDATION_INFO
UNDEFINED
```

That is roughly **3,800 lines of header and 24 chained descriptor structs** wrapped around a
boundary that exposes **four free functions** and a session object.

## 2. The house idiom, which already exists

`common/libs/VkCodecUtils/VkVideoRefCountBase.h:22` defines the reference-counted base, and
`VkSharedBaseObj<T>` is the owning handle. Usage across the tree:

- **100 files** reference `VkSharedBaseObj`
- **26 classes** derive from `VkVideoRefCountBase`

And the encoder already has exactly the interface shape proposed here, at
`vk_video_encoder/include/vulkan_video_encoder.h:43`:

```cpp
class VulkanVideoEncoder : public virtual VkVideoRefCountBase {
public:
    virtual VkResult Initialize(VkVideoCodecOperationFlagBitsKHR, int argc, const char** argv) = 0;
    virtual int64_t  GetNumberOfFrames() = 0;
    virtual VkResult EncodeNextFrame(int64_t& frameNumEncoded) = 0;
    virtual VkResult GetBitstream() = 0;
};

extern "C" VkResult CreateVulkanVideoEncoder(..., VkSharedBaseObj<VulkanVideoEncoder>&);
```

So the ext layer is not extending the house idiom. It sits beside it in a different style.

---

## 3. What the descriptor style costs, concretely

These are observed, not speculative.

### 3.1 A whole test exists to police struct layout

`Layout pins as the struct-extension review gate` is a commit whose purpose is to pin struct
layouts so that appending a field is caught in review. A virtual interface does not need that
test: adding a method changes a vtable, and callers that were not rebuilt fail to resolve rather
than silently reading the wrong offsets.

### 3.2 A version skew reads as data, not as an error

While building TRV against this branch, `vk-nvcuvid-encoder-instance` was compiled against the
new header and linked against a five-week-old `libvkvideo-encoder.so.1`. The failure surfaced as:

```
[EncoderExt] Unsupported codec: 1447362561
```

`1447362561` is `0x56450001` — the value of `VK_VIDEO_ENCODER_STRUCTURE_TYPE_CONFIG`. The
library read the caller's `sType` at the offset where the older layout put `codec`. Flat structs
passed by pointer make a layout disagreement look like a plausible-but-wrong field value; the
diagnostic named a codec that never existed.

An interface whose surface is a vtable does not remove ABI skew, but it changes its shape: an
added or reordered virtual is a **symbol/vtable mismatch**, which fails at link or load for a
`.so` and is impossible for a static link, rather than becoming an integer misread at runtime.

### 3.3 The chain-walking is the implementation

`vulkan_video_encoder_ext.cpp` contains **86** `pNext` mentions and **24** `sType` comparisons.
Every optional input and output is found by walking a chain and switching on a tag. With
interfaces, an optional capability is a `virtual` returning `VkSharedBaseObj<T>` that is null
when unsupported — no walk, no tag, and the compiler checks the type.

### 3.4 Defaults become a trap

`VkEncoderConfig` defaults `enablePreprocessComputeFilter` to **true**, so the ext binder has to
write the field in *both* directions — the code says so at `vulkan_video_encoder_ext.cpp`:
writing it only in the true case would leave a directly encodable session with the filter
created. Flat structs with defaults require every binder to know which fields must be written
down even when the answer is "no". A method that returns an answer has no default to forget.

### 3.5 Reading cost

3,800 lines of header for four entry points is a large amount to read before the first call. The
shape encourages documenting the *mechanism* — which struct chains off which — rather than the
*contract*.

---

## 4. Why the current shape was chosen, and where that reasoning still holds

The descriptor API chose `sType`/`pNext` deliberately, and the reasoning is sound
where it applies:

> *"The **only** legal way to extend a struct in this API is a new `pNext`-chained struct with a
> new `sType` … The library **rejects any `sType` it does not know** … which is the whole point
> across an IPC seam where the two sides may ship independently."*

It also rejected a `size` field, on the grounds that its natural behaviour — read fewer bytes
than the caller wrote — is the same silent-truncation failure the API already suffered.

**Both points are correct.** The question is not whether they are true, but where they bind.

| consumer | seam | independently shipped? | descriptor style needed? |
|---|---|---|---|
| Chromium | **static link**, vendored under `third_party/vulkan_video_samples/` (Model D) | no — same compiler, same build | **no** |
| in-process library callers, tests | direct C++ calls | no | **no** |
| TRV encoder service | **IPC**, SCM_RIGHTS / named pipe | yes | **yes, at the wire** |

The IPC case is real, and it is TRV's. But that boundary is already a **serialisation** layer:
handles are passed by `SCM_RIGHTS` or `DuplicateHandle`, not by memcpy of a struct with a
`pNext` pointer in it — a pointer chain cannot cross a process boundary as-is, so the chain is
flattened by hand on both sides regardless.

So the versioning argument justifies a **tagged wire format**, which is a different artifact from
the **in-process API**. Today the two are conflated: every in-process caller pays the wire
format's ceremony.

---

## 4b. How VA-API solves the same problem — a third answer

Worth reading before choosing, because VA-API is the *other* codec interface the browser uses,
and it faces a strictly harder version problem than we do: a browser dynamically loading a
vendor driver `.so` that was built by someone else, at some other time, against some other
header.

> `VAAPI_vs_VULKAN_VIDEO_DETAILED_COMPARISON.md` compares what the two APIs **do** — surfaces,
> dma-buf, synchronisation, protected content, overlays. This section compares how they **stay
> compatible**, which that document does not cover.

Measured from `/usr/include/va/va.h` (libva 1.20.0, 5,291 lines):

### 4b.1 Opaque integer handles — no live object's layout crosses the boundary

```c
typedef void*        VADisplay;
typedef unsigned int VAGenericID;
typedef VAGenericID  VAConfigID, VAContextID, VASurfaceID, VABufferID, VAImageID;
```

Everything a client holds is an `unsigned int`. There is no object whose *layout* the two sides
must agree on, so the entire class of "header and library disagree about offsets" cannot occur.

That is precisely the failure recorded in §3.2: a caller's `sType` read at the offset where an
older layout put `codec`, reported as `Unsupported codec: 1447362561`. Under VA-API's model that
diagnostic is unreachable.

### 4b.2 Reserved padding instead of chaining

```c
#define VA_PADDING_LOW 4 / MEDIUM 8 / HIGH 16 / LARGE 32
uint32_t va_reserved[VA_PADDING_MEDIUM];   /* "must be zero" */
```

**43 structs** carry such a tail. New fields are carved out of the reserve, so `sizeof()` is
stable across versions and an old binary stays layout-compatible. The budget is finite — when a
struct's reserve is exhausted it needs a successor — but within it, extension costs nothing at
the call site and requires no walk.

**`va.h` contains zero `sType` and zero `pNext`.**

### 4b.3 Explicit size in the data path — and why we decided the opposite

```c
VAStatus vaCreateBuffer(VADisplay, VAContextID, VABufferType type,
                        unsigned int size, unsigned int num_elements,
                        void *data, VABufferID *buf_id);
```

The caller states how many bytes it wrote.

A size field was **explicitly rejected**, on the grounds that its natural behaviour
— read fewer bytes than the caller wrote — is the same silent truncation that
already lost ten fields of `VkVideoEncoderConfig`.

Both positions are defensible, and the difference is not taste. VA-API can afford an explicit
size because reserved padding means the size seldom changes; a wrong size is then a loud
mismatch rather than a routine one. Our rejection is sound **given our extension rule** — but it
is worth being clear that it is sound *because of* that rule, not on its own.

### 4b.4 Typed buffers, not typed chains

**48** `VABufferType` enumerants. A new kind of parameter is a new buffer type consumed at one
place (`vaCreateBuffer`), not a new struct chained onto an existing one and discovered by a walk
at every entry point. Ours has **86** `pNext` sites and **24** `sType` comparisons.

### 4b.5 Runtime version negotiation

```c
VAStatus vaInitialize(VADisplay dpy, int *major_version, int *minor_version);
```

`VA_MAJOR/MINOR/MICRO_VERSION` (1.20.0) is the *compiled-against* version; `vaInitialize` hands
back what the **runtime** actually is, so a client can adapt instead of assuming. Our API has no
equivalent runtime query — a caller cannot ask the library what it is talking to.

### 4b.6 What this argues

Not "copy VA-API". Its opaque-handle and reserved-padding machinery is the price of a genuine
third-party plugin boundary, which we do not have: Chromium **static-links** the vendored copy,
and in-process callers make direct calls.

The useful observation is that **all three models — VA-API, Vulkan, and a C++ interface — agree
that the layout of a live object should not cross a version boundary.** VA-API achieves it with
opaque IDs, Vulkan with tagged chains whose *tail* may grow, a C++ interface with a vtable behind
a pointer. Ours is the only one of the three that lets a caller's struct layout be the contract
at every entry point, and §3.2 is what that costs.

VA-API also separates the **plugin/wire** concern from the **call** concern: the driver ABI is
versioned and defensive, while a client that has already resolved a context passes integers. That
is the same split §5 proposes here — a tagged format at the IPC seam, interfaces in process.

---

## 5. Proposal

Model the ext surface as the rest of the library already is: abstract, reference-counted, and
versioned by inheritance.

### 5.1 Shape

```cpp
// v1 — the surface a host embeds through.
class VulkanVideoEncoderExt : public virtual VulkanVideoEncoder {
public:
    // Capability queries, answerable before a session exists.
    virtual VkResult GetCapabilities(VkSharedBaseObj<VulkanVideoEncoderCapabilities>&) = 0;
    virtual VkResult QueryInputFormatSupport(VkFormat, VulkanVideoEncoderInputFormatSupport&) = 0;

    // Per-frame submission, keyed by the caller's frame id.
    virtual VkResult SubmitFrame(const VulkanVideoEncoderInputFrame&) = 0;
    virtual VkResult AcquireEncodedFrame(uint64_t frameId, VkSharedBaseObj<VulkanVideoEncodedFrame>&) = 0;

    // Dependent interfaces, returned rather than pNext-chained.
    // Null when this build or this device does not provide them.
    virtual VkResult GetContentProbe(VkSharedBaseObj<VulkanVideoEncoderContentProbe>&) = 0;
    virtual VkResult GetDiagnostics(VkSharedBaseObj<VulkanVideoEncoderDiagnostics>&) = 0;
};

// v2 — additive, by inheritance. Old callers keep compiling against v1.
class VulkanVideoEncoderExtV2 : public virtual VulkanVideoEncoderExt {
public:
    virtual VkResult SetFrameDeadline(uint64_t frameId, uint64_t deadlineNs) = 0;
};
```

### 5.2 The three rules

1. **Each version inherits the previous one.** `V2 : public virtual V1`. A caller written
   against v1 keeps working, and `QueryInterface`-style narrowing is a `dynamic_cast` or an
   explicit `GetV2()` accessor that returns null on an older implementation.
2. **Each interface hands back its dependants.** What is a `pNext`-chained struct today becomes
   a getter returning `VkSharedBaseObj<T>&`, null when unavailable. Optionality is expressed by
   the handle, not by the absence of a tag in a chain.
3. **Lifetime is the existing one.** `VkVideoRefCountBase` + `VkSharedBaseObj`, the same as the
   other 26 classes, so there is one ownership model in the library rather than two.

### 5.3 What the descriptor structs become

| today | proposed |
|---|---|
| `VkVideoEncoderCapabilities` (sType, pNext) | `VulkanVideoEncoderCapabilities` interface |
| `VkVideoEncoderDiagnosticInfo` chained off status | `GetDiagnostics()` → interface or null |
| `VkVideoEncoderImportContentInfo` chained twice | `GetContentProbe()` → interface or null |
| `VkVideoEncoderFrameFenceDescriptor` in a chain | parameter on the submit method |
| `VkVideoEncoderConfig` (24 fields, defaults) | a builder, or a plain struct with **no defaults** |

Plain aggregates stay plain where they are pure data — an input frame descriptor is a struct,
not an interface. What goes away is the *tagging and chaining*, not every struct.

---

## 6. Migration path

This does not have to be a flag day.

1. **Land the C++ interface alongside the C surface.** The implementation already exists; the
   interface is a facade over `VulkanVideoEncoderExtImpl`. No behaviour changes.
2. **Move in-process callers first** — the 12 `encoder-ext-*` suites and the Chromium
   `VulkanVideoEncodeAccelerator`. Chromium is a static link, so this is a rebuild, not an ABI
   event.
3. **Keep the C entry points as a thin shim** for anyone who wants them, implemented by calling
   the interface. They stop being the definition and become one binding of it.
4. **Keep the tagged format at the IPC seam only** — TRV's encoder service. Give it an explicit
   wire struct that is *not* the in-process API, so the versioning discipline of V2 §5 applies
   exactly where independent shipping happens.
5. **Retire the layout-pin test** for anything that is no longer a layout contract.

Step 1 is additive and independently reviewable. Steps 2–5 can proceed per consumer.

---

## 7. Risks and open questions

- **`dynamic_cast` across a `.so`** needs consistent RTTI and one typeinfo; a `GetV2()` accessor
  avoids it and is the safer default.
- **Versioning by inheritance is not free.** A v3 that needs to *change* a v1 method's semantics
  cannot do it additively; that case still needs a new interface root. The V2 doc's point about
  never renumbering applies equally to never re-meaning a virtual.
- **The wire format still needs the sType discipline.** Nothing here relaxes it for IPC; it
  narrows where it is paid.
- **Effort is not small.** ~3,800 header lines and 86 `pNext` sites are the surface area, though
  most of the implementation is reusable behind the facade.
- **Upstreamability.** NVPRO/Khronos samples lean Vulkan-idiomatic. A C++ interface is the house
  idiom of *this* library but may read as unusual upstream; worth checking before committing.

---

## 8. Recommendation

Adopt the interface style for the **in-process** surface, keep a tagged format for the **IPC**
surface, and stop paying for the second in places that only need the first.

Do it after the RC-6 review and rebase land — the branch is 24 commits and mid-review, and this
is not a change to make underneath it.
