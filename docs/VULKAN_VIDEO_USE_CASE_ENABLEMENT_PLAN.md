<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-FileCopyrightText: Copyright (c) 2026 The Khronos Group, Inc.
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
# Vulkan Video Encode — Use-Case Enablement Plan (v6)

**Date:** 2026-07-14 · **Owner:** visgarg · **For review by:** Tony Zlatinski
**Shape:** day-wise execution plan (§2) + open questions isolated in §3 — **none of which block the first use-case validation**.
**Basis:** live dev `a68a846` (55-commit stack audited) · two adversarial Codex reviews (plan + per-lane code audit; key claims re-verified
in source) · A4000 environment probes run 2026-07-14 (results in §3.B). Docs baseline: Tony repo @ `18b8be5`.

---

## 1. Executive summary

| Use case | User scenario | Today (verified) | End of this week | Demo vehicle |
|---|---|---|---|---|
| **U3 WebCodecs** — camera/CPU sources | web app encodes camera frames | ✅ **works E2E, zero-copy\*, HW-proven** | real-app-validated + evidence refreshed | WebCodecs page + real app w/ provenance |
| **U4 MediaRecorder** — camera | web app records camera to file | ✅ works E2E; record→file proof missing | real-app-validated + **E2E-claimed** (ffprobe) | recorder page; Loom |
| **U1 WebRTC camera call** | getUserMedia → RTCPeerConnection | ✅ works E2E (one CPU copy/frame) | real-app-validated + **zero-copy enabled** 🎯 | pc1 loopback; real Teams/Meet call |
| **U4 — tab/screen recording** | getDisplayMedia → MediaRecorder | ✅ works E2E (CPU copies) | **zero-copy enabled — marquee all-GPU demo** 🎯 | tab-record page + counters; Loom tab-record |
| **U5 Cast / tab mirroring** | cast a tab to a receiver | ⚠️ believed-works — unverified + codec hazard | negotiation verified + **zero-copy enabled** (engineering, H.264 pinned) 🎯 | local openscreen sink; Chromecast when procured |
| **U2 Screen-share (WebRTC)** | share screen in a call | ✅ works E2E (CPU copies) | real-app-validated; **zero-copy = design item** | Teams/Meet share — works today |
| **U6 Chrome Remote Desktop** | host a CRD session | ❌ **HW never engages** (verified wiring gap) | failure documented + fix scoped | n/a this week |

**\*Zero-copy semantics:** zero copies from the frame's SharedImage onward. **CPU-produced** frames (camera rows) keep one inherent CPU
write into the tagged pool — Tony's "CPU-write-once". **GPU-produced** frames (tab-capture rows) run with **no CPU touch at all**; the
first such lane lands this week (U4-tab). Canvas/WebGL GPU-RGBA remains the copy path (Week 2+, §5).

**Week-1 outcome:** 4 zero-copy verticals demo-able (U3 proven · U1, U4-tab, U5 new) + every "works today" claim re-proven in a **real
application** + 2 precisely-documented blockers (U2 design, U6 wiring) with proposals + the retrospective defect list zeroed (§4).

---

## 2. Day-wise plan

### Day 0 (today) — send, kick, prep · *no external gates*
| Task | Output |
|---|---|
| Plan v6 + the 3 decisions (§3.A) → Tony | sent; Decision-1 deadline flagged as day-3 morning |
| Procurement kicked: USB webcam (A4000 has none — verified), Cast receiver + lab-mDNS check | tickets/requests filed |
| First SSO logins on A4000 (Teams/Meet, Zoom web, Loom, Clipchamp) — the one env unknown a probe can't settle | login viability recorded per site |
| Stage provenance-bundle capture script; build/stage openscreen `cast_receiver` **locally on the A4000** (sidesteps lab mDNS) | RC harness ready |
| Environment: already probed green today (§3.B) | checklist recorded |

### Day 1 — REALITY-CHECK WAVE (first use-case validation) + hygiene CL · *no external gates*
| Task | Output / exit |
|---|---|
| **RC-1** Teams/Meet call, A4000 shares tab/screen (U2) · **RC-2** camera call, fake-cam (U1) · **RC-3** WebCodecs real app, camera (U3, expect zero-copy counters) · **RC-4** Loom camera-record + tab-record (U4) · **RC-5** Cast negotiation trace vs local sink, **unpinned** (U5 — reveals the codec Cast actually picks; evidence for Decision 3) · **RC-6** CRD host attempt (U6 — capture the software-fallback signature) | 6 provenance bundles (build hash+flags, chrome://gpu rows, client path, negotiated codec, input-path counters, nvidia-smi, output check) |
| **Failure rule:** any "works today" claim that fails its RC run **reprioritizes the week before new features land** | go/no-go recorded |
| U1 native-mode check: `kVideoCaptureUseGpuMemoryBuffer` + fake-cam → native frames? | informs U1 evidence scope (code lands either way) |
| **Hygiene CL** → publish: stale X11-override comment (`ozone_image_backing.cc:587`) · DO-NOT-EDIT banner on stale top-level `ext.h` (mirror-synced; delete proposed at next re-vendor) · `README.chromium` rev correction · dead `enable_vulkan_validation_layers` arg | fix-list #5–#8 dead |

### Day 2 — U1 vertical closed · *no hard gates*
| Task | Output / exit |
|---|---|
| Zero-copy **counter split** (direct-import vs library-GPU-copy; VEA already knows its resolve branch) — makes all week-1 evidence unambiguous | fix-list #4 dead |
| **U1 producer tag** (`rtc_video_encoder.cc:654`) + **two-stage alloc/mapping fallback** (both failure modes are stream-fatal today) + unit tests | code complete |
| Loopback validation: pc1, `encoderImplementation` proof, counters O-classified | U1 engineering demo green |
| Stage the Decision-3 truthfulness CL (pull AV1, gate HEVC) — lands on Tony's ack; **all runs pin H.264 meanwhile** (needs no approval) | fix-list #1/#2 staged |
| **Publish U1 review stack** | review overlaps development |

### Day 3 — capture-tag vertical (U4-tab + U5) · **★ FIRST HARD GATE: Decision 1, day-3 morning**
| Task | Output / exit |
|---|---|
| Implement the capture-pool tag per Decision 1 (A/B/C, §3.A) + negative tests (feature-on non-encode capture unchanged; alloc-failure path — today first-frame pool failure *stops capture*) | code complete |
| **If Tony hasn't ruled:** proceed with Option C (explicitly non-landable prototype) to keep day-4 data on schedule; flag the risk | schedule protected |

### Day 4 — capture-tag validation (the marquee)
| Task | Output / exit |
|---|---|
| **U4 tab-record demo E2E**: tab → compositor → encoder, zero CPU touches; counters say direct-import vs library-copy (new split) | marquee evidence (or precisely-diagnosed fallback) |
| U5 engineering run, **H.264 pinned**, vs local openscreen sink | U5 zero-copy through the encoder |
| Resolve the format/modifier questions the day-1 trace surfaced | O-classification final |
| **Publish capture review stack** | second stack out |

### Day 5 — FREEZE + claims + real-app re-runs
| Task | Output / exit |
|---|---|
| **Code freeze** (small review fixes only after this) | frozen hash |
| U4-camera formal E2E claim: record → stop → ffprobe demux/decode | claim closed |
| Re-run real apps **on the newly-enabled paths**: Loom tab-record (now zero-copy) · real-webcam Teams/Meet call if procurement landed | vertical evidence upgraded to real apps |
| Clipchamp provenance smoke (staging on canvas sources **expected** — pre-documented, not a surprise) | canvas caveat evidenced |

### Day 6 — final-build proof + independent review
Rebuild at the frozen hash → re-run every enabled vertical's gates → per-use-case evidence bundles → **Codex diff review** of both stacks → small fixes only.

### Day 7 — packaging + Tony review
Short confirmation run (600-frame; explicitly *not* a soak) → dev→phase1 promotion decision → review session: one evidence bundle per use-case + the two blocker reports (U2 design proposal, U6 wiring gap).

---

## 3. Open questions — **none block the first use-case validation**

> **☑ Explicitly:** Days 0–2 (the reality-check wave and the U1 vertical) run with **zero external dependencies** — no Tony decision, no
> procurement item, no environment unknown gates them. The **first and only hard gate this week is Decision 1 at day-3 morning**, and it
> has a schedule-protecting fallback (Option C). Everything below is tracked, not blocking.

### 3.A Decisions needed from Tony

| # | Decision | Options / recommendation | First thing it gates |
|---|---|---|---|
| D1 | **Capture-pool tag shape** (U4-tab + U5 zero-copy) | **A** mojom intent-variant (landing shape) · **B** copy-on-first-encode pool · **C** feature-gated non-landable prototype. **Rec: C now for week-1 data, A as landing shape** | **Day-3 morning** (only hard gate; fallback = C) |
| D2 | **U6 doc correction + fix deferral** — CRD does *not* inherit the backend (in-process factory passes no command-buffer helper → Model-D fail-fast → silent software; verified) | ack correction; fix Week-2+ | nothing this week (RC-6 documents regardless) |
| D3 | **Codec-advertisement truthfulness** — pull AV1 Main (library-incomplete), gate HEVC Main (unvalidated) until validated; protects U5 (Cast prefers HEVC>AV1>H.264) | small CL, staged day 2 | soft: CL waits for ack; **all runs pin H.264 regardless**; RC-5 (day 1) supplies him the evidence |

### 3.B Open items we own (with today's probe results)

| Item | State (probed 2026-07-14) | Blocks what |
|---|---|---|
| Sandbox-ON on A4000 | ✅ **green** — Ubuntu 24.04.4, `apparmor_restrict_unprivileged_userns=0` (no restriction; C9 workaround likely unneeded) | nothing |
| SaaS reachability from A4000 | ✅ **green** — Teams/Meet/Zoom/Loom/Clipchamp/CRD all respond | nothing |
| Audio device (call join) · binary + weston staged | ✅ **green** | nothing |
| **SSO login viability** on the lab box (2FA/device-trust — only testable interactively) | unknown → day-0 first-login test | worst case: swap RC-1/2 to a test-account Meet call — wave still runs day 1 |
| Second participant for RC-1/RC-2 | coordination (operator or a headless join from another box) | nothing material |
| U1 native-mode outcome (fake-cam may not produce native frames) | day-1 check | **evidence scope only** — U1 tag lands day 2 regardless; zero-copy validation falls to the webcam run |
| **Webcam procurement** (A4000 has none — verified) | kicked day 0 | only the *real-camera* legs (RC runs use real app + fake-cam, disclosed); engineering demos unaffected |
| **Cast receiver + lab mDNS** | kicked day 0 | only the *real-Chromecast* leg — day-4 engineering run uses the **local** openscreen sink on the A4000 |

---

## 4. Retrospective fix list (55-commit + NVPRO audit, 2026-07-14) — zeroed this week

Reality check: ~95% of shipped work is trunk/seam/correctness all use-cases ride on; ≈0 wasted commits on dev; the correction is
*sequencing* (validate/enable verticals first — §2's shape). Defects & where each dies:
**#1/#2** AV1/HEVC over-advertisement + device-facing "truthfulness" → D3 CL (day 2, staged) · **#3** enumerator probes ephemeral device,
not the BYO device → Week-2 · **#4** O1/O2 counter conflation → day-2 split · **#5** stale X11-override comment · **#6** dual-copy `ext.h`
footgun (banner; delete at next re-vendor) · **#7** README rev rot · **#8** dead gn arg → all in the day-1 hygiene CL.

## 5. Explicitly not this week (each needs Tony's steer before work starts)
**U2 zero-copy** — WebRTC hard-disables the native path for screen content and flattens GPU frames to memory pre-Mojo (verified
`rtc_video_encoder.cc:607-610`, `:1999-2006`); tagging cannot help; we bring the trace + a design proposal. · **U6 HW enablement** —
in-process BYO-device/helper wiring + sane bootstrap bitrate. · **Ship-grade rate control + SVC** (U1/U5/U6) — library G-NEW-3/G-NEW-6;
evaluate upstream `encoder-fixes+aq` (sits cleanly on our vendored base) before reimplementing. · **Canvas/WebGL GPU-RGBA** — conversion
architecture; Tony's own sample converts **producer-side** (encoder filter explicitly bypassed for dma-buf imports — block-linear
storage-read constraint, upstream `VkVideoEncoder.cpp@0c58aeb:683`); we propose the same shape for Chromium (GPU blit into a tagged NV12
pool). · **X11 zero-copy + Linux/ANV** — all week-1 zero-copy proof is NVIDIA/Wayland.

---

## Appendix — engineering reference
Vertical → mechanism: U1 = Graph-P1 producer tag · U4-tab/U5 = Graph-P2 capture-pool tag (D1) · U3/U4-camera = Graph-P0 (landed, proven) ·
U2-zero-copy = RTC frame-path design · U6 = factory/device wiring · canvas-RGBA = Graph-P3 + conversion architecture · rate-control/SVC =
Graph-P4 (G-NEW-3/G-NEW-6). Evidence taxonomy: O1 direct import / O2 zero-CPU library GPU copy / O3 CPU staging / O4 no-HW; the day-2
counter split makes O1-vs-O2 observable (until then: "O1|O2@seam").

**Appendix A — Codex plan review (v1→v2):** 2 BLOCKERs (capture-tag scoping → D1; success-definition taxonomy → O1–O4) + 10 MAJORs
accepted; SaaS-as-acceptance adapted (real apps mandatory as demonstration, pinned harnesses as gates); external-review artifact rejected.

**Appendix B — Codex code audit (v2→v3), key claims re-verified in source:** RTC blocks U2 zero-copy (both paths) · U1 tag inert without
native-camera mode · U6 CreateVEA passes no helper → fail-fast → software · O1/O2 counter merge · non-mappable untagged SI = terminal, not
staging · Cast codec-preference hazard (D3) · P1 two-stage fallback · capture-pool alloc failure stops capture · SVC hardcode cites
(`VkVideoEncoder.cpp:2875`, ext `:750-758`).

**Appendix C — Retro classification:** ~32 trunk/seam commits (all needed) · ~13 correctness/hardening (real defects; horizontally
sequenced — lesson folded into §2's shape) · ~7 test infra (Tony's TEST_PLAN) · ~2 self-regression fixes (R-1, caught by own gates) ·
waste on dev ≈ 0 (M150 X11/DMABUF detour dropped pre-port) · NVPRO: all 18 carried patches serve trunk/correctness.
