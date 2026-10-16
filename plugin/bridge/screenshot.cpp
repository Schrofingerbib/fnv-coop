#include "screenshot.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <vector>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

namespace nvcoop {

// ---- PNG (uncompressed deflate) ----
namespace {
uint32_t Crc32(const unsigned char* p, size_t n) {
    static uint32_t table[256];
    static bool init = false;
    if (!init) {
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            table[i] = c;
        }
        init = true;
    }
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; ++i) crc = table[(crc ^ p[i]) & 0xFF] ^ (crc >> 8);
    return ~crc;
}
void Be32(std::string& o, uint32_t v) {
    o.push_back(static_cast<char>(v >> 24));
    o.push_back(static_cast<char>(v >> 16));
    o.push_back(static_cast<char>(v >> 8));
    o.push_back(static_cast<char>(v));
}
void Chunk(std::string& o, const char* type, const std::string& data) {
    Be32(o, static_cast<uint32_t>(data.size()));
    std::string td(type, 4);
    td += data;
    o += td;
    Be32(o, Crc32(reinterpret_cast<const unsigned char*>(td.data()), td.size()));
}
}  // namespace

std::string EncodePngRgb(const unsigned char* rgb, int w, int h) {
    std::string raw;
    raw.reserve(static_cast<size_t>(h) * (static_cast<size_t>(w) * 3 + 1));
    for (int y = 0; y < h; ++y) {
        raw.push_back(0);
        raw.append(reinterpret_cast<const char*>(rgb) + static_cast<size_t>(y) * w * 3, static_cast<size_t>(w) * 3);
    }
    std::string z = "\x78\x01";
    size_t pos = 0;
    do {
        size_t n = std::min<size_t>(65535, raw.size() - pos);
        bool last = pos + n >= raw.size();
        z.push_back(last ? 1 : 0);
        z.push_back(static_cast<char>(n & 0xFF));
        z.push_back(static_cast<char>(n >> 8));
        z.push_back(static_cast<char>(~n & 0xFF));
        z.push_back(static_cast<char>((~n >> 8) & 0xFF));
        z.append(raw, pos, n);
        pos += n;
    } while (pos < raw.size());
    uint32_t a = 1, b = 0;
    for (unsigned char c : raw) { a = (a + c) % 65521; b = (b + a) % 65521; }
#ifdef NVPNG_MUTANT
    a ^= 1;  // mutation twin: corrupt the zlib checksum
#endif
    Be32(z, (b << 16) | a);
    std::string out = "\x89PNG\r\n\x1a\n";
    std::string ihdr;
    Be32(ihdr, static_cast<uint32_t>(w));
    Be32(ihdr, static_cast<uint32_t>(h));
    ihdr += std::string("\x08\x02\x00\x00\x00", 5);
    Chunk(out, "IHDR", ihdr);
    Chunk(out, "IDAT", z);
    Chunk(out, "IEND", std::string());
    return out;
}

// ---- finding the device ----
namespace {

// Raw COM vtable use (no d3d9.h / d3d9.lib). IDirect3DDevice9 vtable indices.
constexpr int kQueryInterface = 0, kRelease = 2;
constexpr int kGetRenderTargetData = 32, kGetBackBuffer = 18, kCreateOffscreenPlainSurface = 36;
// IDirect3DSurface9: GetDesc = 12, LockRect = 13, UnlockRect = 14

const GUID kIID_IDirect3DDevice9 = {0xd0223b96, 0xbf7a, 0x43fd, {0x92, 0xbd, 0xa4, 0x3b, 0x0d, 0x82, 0xb9, 0xeb}};

struct D3DSurfaceDesc { UINT Format, Type, Usage, Pool, MultiSampleType, MultiSampleQuality, Width, Height; };
struct D3DLockedRect { INT Pitch; void* pBits; };

using QI_t = HRESULT(__stdcall*)(void*, const GUID&, void**);
using Rel_t = ULONG(__stdcall*)(void*);

void* g_device = nullptr;

template <class F> F V(void* obj, int idx) { return reinterpret_cast<F>((*reinterpret_cast<void***>(obj))[idx]); }

struct D3DPresentParams {
    UINT BackBufferWidth, BackBufferHeight, BackBufferFormat, BackBufferCount, MultiSampleType;
    DWORD MultiSampleQuality;
    UINT SwapEffect;
    HWND hDeviceWindow;
    BOOL Windowed, EnableAutoDepthStencil;
    UINT AutoDepthStencilFormat;
    DWORD Flags;
    UINT FullScreen_RefreshRateInHz, PresentationInterval;
};

// The vtable address shared by every device of the same D3D9 class: create a throw-away HAL device on a
// hidden window, read its vtable pointer, release it. Never calls anything on foreign objects.
void* ProbeDeviceVtable(void*& probeDev, void*& probeD3d, HWND& probeWnd) {
    using Create9 = void*(__stdcall*)(UINT);
    HMODULE m = GetModuleHandleA("d3d9.dll");
    if (!m) return nullptr;
    auto create = reinterpret_cast<Create9>(GetProcAddress(m, "Direct3DCreate9"));
    if (!create) return nullptr;
    HWND w = CreateWindowExA(0, "STATIC", "nvcoop-probe", WS_POPUP, 0, 0, 64, 64, nullptr, nullptr, nullptr, nullptr);
    void* d3d = create(32);
    void* vt = nullptr;
    if (d3d && w) {
        D3DPresentParams pp{};
        pp.BackBufferWidth = 64; pp.BackBufferHeight = 64; pp.BackBufferFormat = 0 /*UNKNOWN*/; pp.BackBufferCount = 1;
        pp.SwapEffect = 1 /*DISCARD*/; pp.hDeviceWindow = w; pp.Windowed = TRUE;
        void* dev = nullptr;
        using CreateDev = HRESULT(__stdcall*)(void*, UINT, UINT, HWND, DWORD, D3DPresentParams*, void**);
        // IDirect3D9::CreateDevice is vtable slot 16; behavior flag 0x20 = SOFTWARE_VERTEXPROCESSING
        if (V<CreateDev>(d3d, 16)(d3d, 0, 1 /*HAL*/, w, 0x20, &pp, &dev) == 0 && dev) {
            vt = *reinterpret_cast<void**>(dev);
            probeDev = dev;  // kept alive during the scan so its address cannot be mistaken for the game's device
        }
    }
    probeD3d = d3d; probeWnd = w;
    return vt;
}

int g_regions = 0, g_hits = 0;

bool VerifyDevice(void* obj) {
    __try {
        void* out = nullptr;
        if (V<QI_t>(obj, kQueryInterface)(obj, kIID_IDirect3DDevice9, &out) != 0 || !out) return false;
        V<Rel_t>(out, kRelease)(out);
        return out == obj;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

void* FindIn(void* vt, void* skip) {
    if (!vt) return nullptr;
    MEMORY_BASIC_INFORMATION mbi;
    BYTE* addr = nullptr;
    while (VirtualQuery(addr, &mbi, sizeof mbi) == sizeof mbi) {
        BYTE* base = static_cast<BYTE*>(mbi.BaseAddress);
        if (mbi.State == MEM_COMMIT && mbi.Type == MEM_PRIVATE && mbi.Protect == PAGE_READWRITE) {
            ++g_regions;
            auto* p = reinterpret_cast<void**>(base);
            size_t n = mbi.RegionSize / sizeof(void*);
            for (size_t i = 0; i < n; ++i)
                if (p[i] == vt && &p[i] != skip) { ++g_hits; if (VerifyDevice(&p[i])) return &p[i]; }
        }
        addr = base + mbi.RegionSize;
        if (!addr) break;
    }
    return nullptr;
}

void* FindDevice() {
    void* probeDev = nullptr;
    void* probeD3d = nullptr;
    HWND probeWnd = nullptr;
    void* vt = ProbeDeviceVtable(probeDev, probeD3d, probeWnd);
    void* found = FindIn(vt, probeDev);
    if (probeDev) V<Rel_t>(probeDev, kRelease)(probeDev);
    if (probeD3d) V<Rel_t>(probeD3d, kRelease)(probeD3d);
    if (probeWnd) DestroyWindow(probeWnd);
    return found;
}

// No __try in here: free to use C++ objects. Exceptions are caught by the caller.
bool DoCapture(const std::string& path, ShotInfo& info, std::string& err, void*& bb, void*& sys) {
    using GetBB = HRESULT(__stdcall*)(void*, UINT, UINT, UINT, void**);
    HRESULT hrbb = V<GetBB>(g_device, kGetBackBuffer)(g_device, 0, 0, 0, &bb);
    if (hrbb != 0 || !bb) { err = "GetBackBuffer failed hr=" + std::to_string(static_cast<unsigned long>(hrbb)); return false; }
    D3DSurfaceDesc d{};
    using GetDesc = HRESULT(__stdcall*)(void*, D3DSurfaceDesc*);
    V<GetDesc>(bb, 12)(bb, &d);
    if (d.Format != 22 && d.Format != 21) { err = "unsupported backbuffer format " + std::to_string(d.Format); return false; }
    using CreateOff = HRESULT(__stdcall*)(void*, UINT, UINT, UINT, UINT, void**, void*);
    if (V<CreateOff>(g_device, kCreateOffscreenPlainSurface)(g_device, d.Width, d.Height, d.Format, 3 /*SYSTEMMEM*/, &sys, nullptr) != 0 || !sys) {
        err = "CreateOffscreenPlainSurface failed";
        return false;
    }
    using GetRTD = HRESULT(__stdcall*)(void*, void*, void*);
    HRESULT hr = V<GetRTD>(g_device, kGetRenderTargetData)(g_device, bb, sys);
    if (hr != 0) { err = "GetRenderTargetData failed hr=" + std::to_string(static_cast<long>(hr)); return false; }
    D3DLockedRect lr{};
    using Lock = HRESULT(__stdcall*)(void*, D3DLockedRect*, const RECT*, DWORD);
    if (V<Lock>(sys, 13)(sys, &lr, nullptr, 0x10 /*READONLY*/) != 0) { err = "LockRect failed"; return false; }
    std::vector<unsigned char> rgb(static_cast<size_t>(d.Width) * d.Height * 3);
    for (UINT y = 0; y < d.Height; ++y) {
        const unsigned char* s = static_cast<const unsigned char*>(lr.pBits) + static_cast<size_t>(y) * lr.Pitch;
        unsigned char* o = &rgb[static_cast<size_t>(y) * d.Width * 3];
        for (UINT x = 0; x < d.Width; ++x) { o[x * 3] = s[x * 4 + 2]; o[x * 3 + 1] = s[x * 4 + 1]; o[x * 3 + 2] = s[x * 4]; }
    }
    using Unlock = HRESULT(__stdcall*)(void*);
    V<Unlock>(sys, 14)(sys);
    std::string png = EncodePngRgb(rgb.data(), static_cast<int>(d.Width), static_cast<int>(d.Height));
    FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) { err = "cannot open " + path; return false; }
    std::fwrite(png.data(), 1, png.size(), f);
    std::fclose(f);
    info.width = static_cast<int>(d.Width);
    info.height = static_cast<int>(d.Height);
    return true;
}

bool SehWrap(const std::string& path, ShotInfo& info, std::string& err, void*& bb, void*& sys) {
    __try {
        return DoCapture(path, info, err, bb, sys);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

}  // namespace

bool CaptureBackbufferPng(const std::string& path, ShotInfo& info, std::string& err) {
    if (!g_device) g_device = FindDevice();
    if (!g_device) { err = "D3D9 device not found (regions=" + std::to_string(g_regions) + " vtableHits=" + std::to_string(g_hits) + ")"; return false; }
    void* bb = nullptr;
    void* sys = nullptr;
    bool ok = SehWrap(path, info, err, bb, sys);
    if (!ok && err.empty()) { err = "exception while capturing (device pointer stale?)"; g_device = nullptr; }
    if (sys) V<Rel_t>(sys, kRelease)(sys);
    if (bb) V<Rel_t>(bb, kRelease)(bb);
    return ok;
}

}  // namespace nvcoop
