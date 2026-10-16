// Writes a known 3x2 RGB image as PNG to argv[1] (validated by tools/runner/tests/test_png.py).
#include <cstdio>
#include "screenshot.h"
int main(int argc, char** argv) {
    if (argc < 2) return 2;
    const unsigned char px[3 * 2 * 3] = {255, 0, 0, 0, 255, 0, 0, 0, 255,   10, 20, 30, 40, 50, 60, 250, 251, 252};
    std::string png = nvcoop::EncodePngRgb(px, 3, 2);
    FILE* f = std::fopen(argv[1], "wb");
    if (!f) return 3;
    std::fwrite(png.data(), 1, png.size(), f);
    std::fclose(f);
    return 0;
}
