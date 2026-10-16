// Built as fake_storyserver.exe: loads nvmp_server_hook.dll the way the launcher would (same process name rule),
// but the exe contains none of the server signatures, so the hook must ABORT cleanly (install nothing) and log it.
#include <windows.h>
#include <cstdio>
int main(int argc, char** argv) {
    const char* dll = argc > 1 ? argv[1] : "nvmp_server_hook.dll";
    HMODULE h = LoadLibraryA(dll);
    if (!h) { std::printf("LoadLibrary failed: %lu\n", GetLastError()); return 2; }
    Sleep(1500);   // let the poll thread read hostowned.txt
    FreeLibrary(h);
    std::printf("smoke ok\n");
    return 0;
}
