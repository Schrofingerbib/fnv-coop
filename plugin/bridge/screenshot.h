// D3D9 back-buffer capture -> PNG (nvcoop.dll only; needs the game's IDirect3DDevice9). Main thread only.
#pragma once
#include <string>

namespace nvcoop {

struct ShotInfo {
    int width = 0, height = 0;
};

// Records the game's device from a one-time EndScene patch in the shared D3D9 device vtable (the heap scan is the
// fallback when that patch cannot go in), copies the back buffer to system memory and writes an RGB PNG. The first
// call installs the patch and asks for a retry. Returns false and fills err.
bool CaptureBackbufferPng(const std::string& path, ShotInfo& info, std::string& err);

// Pure helper (unit-testable): encode tightly packed 8-bit RGB rows as a PNG (stored deflate).
std::string EncodePngRgb(const unsigned char* rgb, int width, int height);

}  // namespace nvcoop
