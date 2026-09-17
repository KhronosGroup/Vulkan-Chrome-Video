<!--
title: Vulkan Video Encoder — Ownership and Resource-Sharing Principles
status: specification
date: 2026-08-26
scope: vk_video_encoder/include/vulkan_video_encoder_ext.h — the embeddable boundary
-->

# Ownership and Resource-Sharing Principles

<!-- DOC-STATUS -->
> **Status: CURRENT.** Specification. **Cited by source comments (15 sites) — keep accurate.**
> Index: [`README.md`](README.md).


The rules that govern every object crossing the embeddable encoder boundary: who allocates it, who
releases it, what keeps it alive while the GPU is still using it, and what a new resource type must
do to be added safely.

This is the normative statement. For what the current interface actually does against these rules,
see [`VULKAN_VIDEO_ENCODER_EXT_OBJECT_LIFECYCLE.md`](VULKAN_VIDEO_ENCODER_EXT_OBJECT_LIFECYCLE.md).

---

## P0 — The boundary never hands out a lifetime the client must reason about

The library is built internally on refcounted shared objects (`VkSharedBaseObj`, a `shared_ptr`
alias). That refcount cannot cross the boundary: a `shared_ptr` does not survive a C ABI, does not
survive a process boundary, and would bind the embedder to the library's allocator and destruction
order.

**The translation is an opaque id whose referent is refcounted internally.** The client holds a
token; the library holds the object. The safety property transfers; the pointer type does not. Every
principle below is a consequence of this one.

A corollary that decides most design questions: **the client must never hold a raw Vulkan handle
whose destruction the library's correctness depends on.** If it does, no amount of documentation
makes the interface safe, because the client can always destroy it at the wrong moment.

---

## P1 — Ownership is a property of the registration, never of the outcome

An ownership mode is chosen **per registration**. It is never per frame and never per call outcome.

What is explicitly rejected is an *outcome-conditional* rule — one where who closes the handle
depends on how the call went. Under either mode the answer is identical on every exit path, success
or failure. This is what makes the rule statable in one sentence, and therefore followable.

| Mode | Rule, on **every** exit path |
| --- | --- |
| `TRANSFER` (default, and what `{}` gives you) | After the call the handle is **not the caller's**. It must not close it, use it, or look at it. |
| `BORROW` | The handle **stays the caller's**. The library takes a private duplicate (`F_DUPFD_CLOEXEC`) *before anything that can fail*, and applies the `TRANSFER` rule to its own copy. |

`TRANSFER` is the default deliberately: **the safe thing must be what zero-initialisation gives
you.** `BORROW` exists for the narrow case of a caller that must import one received handle twice —
the reference renderer imports a single frame fd into both its display device and the encoder. It is
meaningless across an IPC boundary, where the transport has already duplicated the handle.

---

## P2 — The platform decides fd rules; we only state them

The asymmetry between POSIX and Win32 is Vulkan's, not a choice this interface makes:

- **A POSIX fd is consumed.** On success it is owned by the imported `VkDeviceMemory` and released
  by `vkFreeMemory`. On failure it is closed exactly once, by whichever owner holds it at the point
  of failure — by the library when the failure preceded the `vkAllocateMemory` handoff, and by the
  **driver** when it did not, because some drivers consume the fd even when the import allocation
  fails. The library therefore never closes an fd it has already offered to `vkAllocateMemory`.
- **A Win32 NT handle is never closed by the library.** Not on success, not on failure. The caller
  retains it and closes it once the resource is unregistered. On Win32 both ownership modes are
  therefore identical and `BORROW` is accepted as a no-op.

The principle: **do not invent ownership semantics the platform does not have.** State the
platform's rule, apply it unconditionally, and make the difference visible rather than papered over.

---

## P3 — Report the rule applied; never make it an input

The library echoes which ownership rule it applied through `VkVideoEncoderStatus::handlesConsumed`,
so a caller can **assert rather than guess**.

It is a constant of `(platform, handle type, mode)` — deliberately *not* of the call's outcome,
which is the entire point of P1. It is `VK_TRUE` iff the caller's handle was consumed: always true
for a POSIX fd under `TRANSFER`, always false under `BORROW`, always false for Win32 handles and for
`VK_IMAGE`, where no handle is taken.

**It is never a control input.** A field that reports is testable; a field that steers becomes a
second source of truth that will eventually disagree with the first.

---

## P4 — Direction of ownership must be stated for exported handles too

Every fd rule above governs a handle the library is **given**. A handle the library **exports** needs
its own statement, and gets one:

> `pReleaseFenceFd` — the exported fd is the **caller's**. The library never closes it. `close(2)` it,
> or hand it to exactly one import, which consumes it.

This is the reverse of every other fd rule in the interface, which is precisely why it is written
down rather than inferred. **Name exactly one owner for every handle in both directions**, and say so
at the handle, not in a design document the caller will not have.

Two supporting rules that make an out-parameter usable:

- **Write it before anything can refuse.** The library stores `-1` into every `pReleaseFenceFd` in the
  chain up front, ahead of any import, id resolution or `sType` check, so a caller may declare
  `int fd;` and rely on it being defined on *every* return — including the error returns and the
  chains that put the refusing node ahead of the descriptor.
- **A sentinel is an answer, not a failure.** `-1` means "no fence" and must never be read as "the
  input is already released". Legitimate causes include a platform that cannot export a SYNC_FD, an
  input-consuming submission not yet issued (a frame deferred into a B-frame reorder batch), and a
  fence already signalled.

---

## P5 — Registration confers protection; an id alone confers nothing

An opaque id is the *mechanism*. The safety comes from what the library does behind it. A protected
registration has **all three** of:

1. **A refcount held for the lifetime of every submitted frame that names it.** The library's
   internal refcount is extended by the registration, not exposed through it.
2. **Deferred retire.** An unregister racing an in-flight frame **defers** rather than freeing memory
   the GPU is still reading. The client is never required to prove quiescence.
3. **Generation counting.** A stale or retired id is a typed refusal (`ERROR_RESOURCE_UNKNOWN`),
   never a silent match against a recycled slot. Use-after-retire fails loudly and deterministically.

An id with fewer than three is not a protected reference; it is a handle with extra steps. The
distinction matters because the two look identical at the call site.

---

## P6 — One token type, one lifetime rule

If two resource classes share a token type, they must share a lifetime discipline. If their
disciplines differ, the **types must differ**, so the compiler catches the confusion rather than a
paragraph of header comment.

This is the principle most easily violated by incremental growth: a second resource class is added,
the existing id type is reused because it fits, and the differing rule is documented instead of
encoded. The result is an interface where correctness depends on the caller having read the right
paragraph.

---

## P7 — Registration is once per pool slot; submission names, it does not import

The per-frame call must **name** an already-registered resource and perform no import, no allocation
and no Vulkan object creation. This is what makes the frame loop allocation-free, and it is also what
makes lifetime tractable: there is one place a resource enters the library and one place it leaves.

Two consequences worth stating explicitly:

- **Key the registration cache by producer pool slot, never by frame id.** A frame-counter key
  produced duplicate-frame bitstreams in the reference renderer. Frame ids recycle; pool slots
  identify the underlying allocation.
- **Destroy slots only behind a device-idle barrier**, or behind the deferred-retire machinery of P5.

---

## P8 — The library owns the import, not necessarily the allocation

It is tempting to state "the library allocates everything." That is not achievable and not the point.

A wait semaphore is signalled by the **producer**, on the producer's device, before the encoder
exists in that frame's story. A dma-buf is allocated by the compositor's allocator under constraints
the encoder does not control. What the library *can* own — and must — is the **import**: the Vulkan
object it creates from the handle, and the lifetime of that object relative to work in flight.

So the correct formulation is not *"the library allocates it"* but:

> **Every externally-originated resource enters through a registration that creates a
> library-owned Vulkan object, and the client thereafter refers to it only by id.**

That is achievable for images, semaphores, and any future resource class, without requiring the
library to own an allocator it has no business owning.

---

## P9 — Failure paths obey the same rule as success paths

Every rule above is stated as "on every exit path" because the failure path is where ownership bugs
live. Two specific obligations:

- **A refusal does not hand the handle back.** If an fd is imported before a limit is checked, the
  refusal consumes it exactly as success would. The ownership rule is not suspended by the refusal.
- **Out-parameters are initialised before the first possible refusal**, so a caller's cleanup path is
  valid regardless of where the call failed.

An interface whose ownership rules hold only on success has no ownership rules.

---

## Checklist for adding a resource class

A new externally-originated resource type is safe to add when all of the following are true:

- [ ] It enters through a **registration** that returns an opaque id (P0).
- [ ] Its ownership mode is fixed **per registration**, unconditional on outcome (P1).
- [ ] Its handle rule matches the **platform's** rule, stated at the handle (P2).
- [ ] The applied rule is **reported**, not accepted as input (P3).
- [ ] Any handle the library **exports** names exactly one owner, with a defined sentinel (P4).
- [ ] The registration carries **refcount + deferred retire + generation counting** (P5).
- [ ] Its id type is **distinct** from any id with a different lifetime rule (P6).
- [ ] The per-frame path **names** it and imports nothing (P7).
- [ ] Failure paths obey the same rule as success paths (P9).

Any box left unchecked should appear in the header as an explicit, named limitation — not as silence.
