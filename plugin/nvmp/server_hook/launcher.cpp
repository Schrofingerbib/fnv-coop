// nvmp_hook_launcher.exe: start nvmp_storyserver.exe suspended, inject nvmp_server_hook.dll, resume.
// No file on disk is modified. Build as x86 (the server is x86; LoadLibraryW's address is shared across
// same-bitness processes, the same technique nvmp_start.exe uses for client.dll).
//
//   nvmp_hook_launcher.exe [--dry-run] [--server <path\nvmp_storyserver.exe>] [--dll <path\nvmp_server_hook.dll>] [-- <server args...>]
//
// --dry-run only validates the plan (paths, PE machine type) and prints it; it never creates a process.
#include <windows.h>

#include <cstdio>
#include <string>
#include <vector>

static bool FileExists(const std::wstring& p) {
    DWORD a = GetFileAttributesW(p.c_str());
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

// 0x14C = x86, 0x8664 = x64, 0 = not a PE
static unsigned PeMachine(const std::wstring& p) {
    FILE* f = _wfopen(p.c_str(), L"rb");
    if (!f) return 0;
    unsigned char hdr[0x400] = {};
    size_t n = fread(hdr, 1, sizeof(hdr), f);
    fclose(f);
    if (n < 0x40 || hdr[0] != 'M' || hdr[1] != 'Z') return 0;
    unsigned pe = *(unsigned*)(hdr + 0x3C);
    if (pe + 6 > n || hdr[pe] != 'P' || hdr[pe + 1] != 'E') return 0;
    return *(unsigned short*)(hdr + pe + 4);
}

static std::wstring DirOf(const std::wstring& p) {
    size_t s = p.find_last_of(L"\\/");
    return s == std::wstring::npos ? L"." : p.substr(0, s);
}

int wmain(int argc, wchar_t** argv) {
    bool dry = false;
    std::wstring server, dll, extra;
    for (int i = 1; i < argc; ++i) {
        std::wstring a = argv[i];
        if (a == L"--dry-run") dry = true;
        else if (a == L"--server" && i + 1 < argc) server = argv[++i];
        else if (a == L"--dll" && i + 1 < argc) dll = argv[++i];
        else if (a == L"--") {
            for (int j = i + 1; j < argc; ++j) extra += L" \"" + std::wstring(argv[j]) + L"\"";
            break;
        } else {
            fwprintf(stderr, L"unknown argument: %ls\n", a.c_str());
            return 2;
        }
    }
    wchar_t self[MAX_PATH] = {};
    GetModuleFileNameW(nullptr, self, MAX_PATH);
    if (dll.empty()) dll = DirOf(self) + L"\\nvmp_server_hook.dll";
    if (server.empty()) {
        const wchar_t* cands[] = {L"nvmp_storyserver.exe", L"NVMP-StoryServer.exe"};
        for (auto c : cands)
            if (FileExists(c)) { server = c; break; }
    }
    wchar_t full[MAX_PATH * 2];
    if (!server.empty() && GetFullPathNameW(server.c_str(), MAX_PATH * 2, full, nullptr)) server = full;
    if (GetFullPathNameW(dll.c_str(), MAX_PATH * 2, full, nullptr)) dll = full;

    wprintf(L"server: %ls\nhook  : %ls\n", server.empty() ? L"(not found)" : server.c_str(), dll.c_str());
    if (server.empty() || !FileExists(server)) { fwprintf(stderr, L"server exe not found (use --server)\n"); return 3; }
    if (!FileExists(dll)) { fwprintf(stderr, L"hook dll not found (use --dll)\n"); return 3; }
    if (PeMachine(server) != 0x14C) { fwprintf(stderr, L"server is not an x86 PE\n"); return 4; }
    if (PeMachine(dll) != 0x14C) { fwprintf(stderr, L"hook dll is not an x86 PE\n"); return 4; }
    std::wstring dir = DirOf(server);
    wprintf(L"workdir: %ls\nplan  : CreateProcess(suspended) -> write dll path -> CreateRemoteThread(LoadLibraryW) -> wait -> resume\n",
            dir.c_str());
    wprintf(L"files : %ls\\hostowned.txt (FormIDs), %ls\\server_hook.cfg (optional), %ls\\server_hook.log (output)\n", dir.c_str(), dir.c_str(),
            dir.c_str());
    if (dry) { wprintf(L"dry run: nothing started\n"); return 0; }

    std::wstring cmd = L"\"" + server + L"\"" + extra;
    std::vector<wchar_t> cmdBuf(cmd.begin(), cmd.end());
    cmdBuf.push_back(0);
    STARTUPINFOW si = {sizeof(si)};
    PROCESS_INFORMATION pi = {};
    if (!CreateProcessW(server.c_str(), cmdBuf.data(), nullptr, nullptr, FALSE, CREATE_SUSPENDED, nullptr, dir.c_str(), &si, &pi)) {
        fwprintf(stderr, L"CreateProcess failed: %lu\n", GetLastError());
        return 5;
    }
    SIZE_T bytes = (dll.size() + 1) * sizeof(wchar_t);
    void* remote = VirtualAllocEx(pi.hProcess, nullptr, bytes, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    HANDLE th = nullptr;
    DWORD code = 0;
    bool ok = remote && WriteProcessMemory(pi.hProcess, remote, dll.c_str(), bytes, nullptr);
    if (ok) {
        FARPROC ll = GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "LoadLibraryW");
        th = CreateRemoteThread(pi.hProcess, nullptr, 0, (LPTHREAD_START_ROUTINE)ll, remote, 0, nullptr);
        ok = th != nullptr;
    }
    if (ok) {
        WaitForSingleObject(th, 20000);
        GetExitCodeThread(th, &code);  // low 32 bits of the HMODULE: 0 = LoadLibrary failed
        ok = code != 0;
    }
    if (!ok) {
        fwprintf(stderr, L"injection failed (remote load code=%lu, gle=%lu): terminating the suspended server\n", code, GetLastError());
        TerminateProcess(pi.hProcess, 1);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        return 6;
    }
    wprintf(L"hook loaded (module 0x%08lX); resuming server pid %lu. See server_hook.log.\n", code, pi.dwProcessId);
    ResumeThread(pi.hThread);
    CloseHandle(th);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
