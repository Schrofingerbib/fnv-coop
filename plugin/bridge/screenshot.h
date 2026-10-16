// D3D9 back-buffer capture -> PNG (nvcoop.dll only; needs the game's IDirect3DDevice9). Main thread only.
#pragma once
#include <string>

namespace nvcoop {

struct ShotInfo {
    int width = 0, height = 0;
};

// Finds the game's device by scanning own-process heap for D3D9 device objects (no code patching, no
// hooks), copies the back buffer to system memory and writes an RGB PNG. Returns false and fills err.
bool CaptureBackbufferPng(const std::string& path, ShotInfo& info, std::string& err);

// Pure helper (unit-testable): encode tightly packed 8-bit RGB rows as a PNG (stored deflate).
std::string EncodePngRgb(const unsigned char* rgb, int width, int height);

}  // namespace nvcoop
