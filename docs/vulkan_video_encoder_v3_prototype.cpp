#include <memory>
#include <string_view>
#include <vector>
#include <cassert>
#include <cstdio>
#include <type_traits>

namespace vk::video::enc {

enum class Result : int32_t { Success = 0, InvalidArgument, UnsupportedFeature, NotReady };

// Trivially copyable: a code and a pointer to a string literal the library
// owns. No allocation, no STL type in the ABI, and the whole thing fits in
// two registers. Detail that needs runtime context (a frame id, a format
// number) belongs on IDiagnostics, not here.
class Status {
public:
    Status() : code_(Result::Success), detail_("") {}
    Status(Result c, const char* d = "") : code_(c), detail_(d ? d : "") {}
    bool ok() const { return code_ == Result::Success; }
    explicit operator bool() const { return ok(); }
    Result code() const { return code_; }
    const char* detail() const { return detail_; }
private:
    Result      code_;
    const char* detail_;
};

template <class T>
class Expected {
public:
    Expected(T v) : val_(std::move(v)) {}
    Expected(Status s) : st_(std::move(s)) {}
    explicit operator bool() const { return bool(st_); }
    const T& operator*() const { return val_; }
    T& operator*() { return val_; }
    const Status& status() const { return st_; }
private:
    T val_{};
    Status st_;
};

// A borrowed, contiguous, read-only view. C++20's std::span, minus the
// parts an interface header has no use for. Storage belongs to the library
// and stays valid until the owning object is released.
template <class T>
class ArrayView {
public:
    ArrayView() = default;
    ArrayView(const T* data, size_t size) : data_(data), size_(size) {}
    template <size_t N> ArrayView(const T (&a)[N]) : data_(a), size_(N) {}
    ArrayView(const std::vector<T>& v) : data_(v.data()), size_(v.size()) {}
    const T* data()  const { return data_; }
    size_t   size()  const { return size_; }
    bool     empty() const { return size_ == 0; }
    const T* begin() const { return data_; }
    const T* end()   const { return data_ + size_; }
    const T& operator[](size_t i) const { return data_[i]; }
private:
    const T* data_ = nullptr;
    size_t   size_ = 0;
};

class IObject {
public:
    virtual ~IObject() = default;
    virtual void* QueryInterface(std::string_view id) = 0;
};
template <class T> using Ref = std::shared_ptr<T>;

template <class I>
Ref<I> Query(const Ref<IObject>& obj) {
    if (!obj) return nullptr;
    void* p = obj->QueryInterface(I::kId);
    return p ? Ref<I>(obj, static_cast<I*>(p)) : nullptr;
}
// Convenience: query from any interface ref, not just IObject.
template <class I, class From>
Ref<I> Query(const Ref<From>& obj) {
    return Query<I>(std::static_pointer_cast<IObject>(obj));
}

// ---- role interfaces -------------------------------------------------------
struct FrameSubmit { uint64_t frameId; int fd; };

class IFrameSubmitter : public IObject {
public:
    static constexpr std::string_view kId = "vk.video.enc.IFrameSubmitter/1";
    virtual Status SubmitFrame(const FrameSubmit&) = 0;
};

class IFrameSubmitterV2 : public IFrameSubmitter {
public:
    static constexpr std::string_view kId = "vk.video.enc.IFrameSubmitter/2";
    virtual Status SubmitWithDeadline(const FrameSubmit&, uint64_t deadlineNs) = 0;
};

class IBitstreamSource : public IObject {
public:
    static constexpr std::string_view kId = "vk.video.enc.IBitstreamSource/1";
    virtual Expected<ArrayView<uint8_t>> Acquire(uint64_t frameId) = 0;
    virtual void Release(uint64_t frameId) = 0;
};

class IHdrMetadataConfig : public IObject {
public:
    static constexpr std::string_view kId = "vk.video.enc.IHdrMetadataConfig/1";
    virtual void SetContentLightLevel(uint16_t maxCll, uint16_t maxFall) = 0;
};

enum class InputPath { Direct, Copy, ComputeFilter };

class ICompletionSignal : public IObject {
public:
    static constexpr std::string_view kId = "vk.video.enc.ICompletionSignal/1";
    virtual uint64_t Counter() const = 0;
};

class IEncoderConfig : public IObject {
public:
    static constexpr std::string_view kId = "vk.video.enc.IEncoderConfig/1";
    virtual IEncoderConfig& SetResolution(uint32_t w, uint32_t h) = 0;
    virtual IEncoderConfig& SetFrameRate(uint32_t n, uint32_t d) = 0;
    virtual InputPath ResolveInputPath() const = 0;
    virtual Status Validate() const = 0;
};

class IEncoderSession : public IObject {
public:
    static constexpr std::string_view kId = "vk.video.enc.IEncoderSession/1";
    virtual Status Flush() = 0;
};

// ---- an implementation: one object, several roles --------------------------
class SessionImpl final : public IEncoderSession,
                          public IFrameSubmitterV2,
                          public IBitstreamSource {
public:
    void* QueryInterface(std::string_view id) override {
        if (id == IEncoderSession::kId)   return static_cast<IEncoderSession*>(this);
        if (id == IFrameSubmitter::kId)   return static_cast<IFrameSubmitter*>(this);
        if (id == IFrameSubmitterV2::kId) return static_cast<IFrameSubmitterV2*>(this);
        if (id == IBitstreamSource::kId)  return static_cast<IBitstreamSource*>(this);
        return nullptr;   // ICompletionSignal absent -> caller polls
    }
    Status Flush() override { flushed = true; return {}; }
    Status SubmitFrame(const FrameSubmit& f) override { last = f.frameId; return {}; }
    Status SubmitWithDeadline(const FrameSubmit& f, uint64_t) override { deadlineUsed = true; return SubmitFrame(f); }
    Expected<ArrayView<uint8_t>> Acquire(uint64_t id) override {
        if (id != last) return Status(Result::NotReady, "frame not encoded");
        return ArrayView<uint8_t>(bits);
    }
    void Release(uint64_t) override { released = true; }

    bool flushed = false, deadlineUsed = false, released = false;
    uint64_t last = ~0ull;
    std::vector<uint8_t> bits{1,2,3,4};
};

// H.264 config: no HDR role. H.265 config: has it.
class ConfigImpl final : public IEncoderConfig, public IHdrMetadataConfig {
public:
    explicit ConfigImpl(bool hdrCapable) : hdrCapable_(hdrCapable) {}
    void* QueryInterface(std::string_view id) override {
        if (id == IEncoderConfig::kId) return static_cast<IEncoderConfig*>(this);
        if (id == IHdrMetadataConfig::kId && hdrCapable_) return static_cast<IHdrMetadataConfig*>(this);
        return nullptr;
    }
    IEncoderConfig& SetResolution(uint32_t w, uint32_t h) override { w_=w; h_=h; return *this; }
    IEncoderConfig& SetFrameRate(uint32_t n, uint32_t d) override { n_=n; d_=d; return *this; }
    InputPath ResolveInputPath() const override { return w_ % 2 ? InputPath::ComputeFilter : InputPath::Copy; }
    Status Validate() const override {
        return w_ && h_ ? Status{} : Status(Result::InvalidArgument, "resolution unset");
    }
    void SetContentLightLevel(uint16_t a, uint16_t b) override { cll_=a; fall_=b; }
    uint16_t cll_=0, fall_=0;
private:
    bool hdrCapable_; uint32_t w_=0,h_=0,n_=0,d_=0;
};

} // namespace vk::video::enc

using namespace vk::video::enc;

int main() {
    Ref<IEncoderSession> session = std::make_shared<SessionImpl>();

    // 1. v1 role present
    auto sub = Query<IFrameSubmitter>(session);
    assert(sub && "IFrameSubmitter must resolve");
    assert(bool(sub->SubmitFrame({7, -1})));

    // 2. v2 discovered by inheritance, same object
    auto sub2 = Query<IFrameSubmitterV2>(session);
    assert(sub2 && bool(sub2->SubmitWithDeadline({8, -1}, 16000000)));

    // 3. absent role is a clean null, not an error return
    assert(Query<ICompletionSignal>(session) == nullptr);

    // 4. aliasing ref keeps the owner alive
    Ref<IBitstreamSource> bs = Query<IBitstreamSource>(session);
    session.reset();
    assert(bs.use_count() >= 1);
    auto got = bs->Acquire(8);
    assert(got && (*got).size() == 4u);
    auto notReady = bs->Acquire(999);
    assert(!notReady && notReady.status().code() == Result::NotReady);
    printf("  detail propagates: \"%s\"\n", notReady.status().detail());

    // 5. the H.264/HDR rule as a type-system fact, not a runtime refusal
    Ref<IEncoderConfig> h265 = std::make_shared<ConfigImpl>(true);
    Ref<IEncoderConfig> h264 = std::make_shared<ConfigImpl>(false);
    assert(Query<IHdrMetadataConfig>(h265) != nullptr);
    assert(Query<IHdrMetadataConfig>(h264) == nullptr);
    Query<IHdrMetadataConfig>(h265)->SetContentLightLevel(1000, 400);

    // 6. chained setters + library-side derivation
    h265->SetResolution(1920, 1080).SetFrameRate(60, 1);
    assert(bool(h265->Validate()));
    assert(h265->ResolveInputPath() == InputPath::Copy);
    assert(!h264->Validate() && h264->Validate().code() == Result::InvalidArgument);

    static_assert(sizeof(Status) <= 2 * sizeof(void*), "Status must stay register-sized");
    printf("  Status is %zu bytes, trivially copyable=%d\n",
           sizeof(Status), (int)std::is_trivially_copyable<Status>::value);
    printf("  all 7 mechanism checks passed\n");
    return 0;
}
