#pragma once
// Import-table (IAT) patching for the split-screen hooks: XInputGetState / XInputSetState / GetActiveWindow in the game exe.
//
// Why the IAT and not an inline detour: the exe calls XInput through two import thunks (jmp [IAT slot]), and the DLL functions
// begin with `mov edi, edi` (no 55 8B EC prologue, which nvhooks requires). One 4-byte aligned store switches a slot atomically,
// needs no trampoline and touches no engine code. No engine address is used: the slots are found by walking the PE import table
// of the running exe, and a slot is matched by NAME or (for the ordinal imports XINPUT1_3 uses) by VALUE: the loader filled the
// slot with the export's address, which GetProcAddress(dll, "XInputGetState") returns as well.
// All memory access goes through IMemory32 and the module image is passed in as bytes, so this is tested on a synthetic PE image.
#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace nvsplit {

struct ImportRef {
    std::string dll;
    std::string name;       // empty for an ordinal import
    uint16_t ordinal = 0;
    bool byOrdinal = false;
    uint32_t slotRva = 0;   // RVA of the 4-byte IAT entry
    uint32_t value = 0;     // content of the entry when it was read
};

// `image` is a MAPPED 32-bit PE (an RVA is an offset into it), `size` its SizeOfImage. Every read is range checked; a damaged
// table returns false with a message and no crash. Entries found before the damage stay in *out.
bool EnumImports(const uint8_t* image, size_t size, std::vector<ImportRef>* out, std::string* err);

// Lays a PE file (as read from disk) out by section virtual address. Empty on error.
std::vector<uint8_t> MapPeFile(const uint8_t* file, size_t n, std::string* err);

class IExports {
public:
    virtual ~IExports() = default;
    // Address of an export of an already loaded DLL, 0 when unknown. `dll` is the name as written in the import table.
    virtual uint32_t addressOf(const std::string& dll, const std::string& func) = 0;
};

// All slots of `func` imported from a DLL whose name starts with `dllPrefix` (case-insensitive). `exports` may be null.
std::vector<ImportRef> FindImportSlots(const std::vector<ImportRef>& all, const char* dllPrefix, const char* func, IExports* exports);

class IMemory32 {
public:
    virtual ~IMemory32() = default;
    virtual bool read32(uint32_t addr, uint32_t* v) = 0;
    virtual bool write32(uint32_t addr, uint32_t v) = 0;  // handles page protection
};

class IatPatcher {
public:
    explicit IatPatcher(IMemory32& mem) : mem_(mem) {}
    // Points the slot at `detour`. *origOut receives what the slot held (the function to call through). False, nothing written, when
    // the slot cannot be read, holds 0 (unbound), or is already ours.
    bool hook(uint32_t slotAddr, uint32_t detour, uint32_t* origOut);
    // Puts the original back, but only while the slot still holds our detour. False when someone chained over it (left alone).
    bool unhook(uint32_t slotAddr);
    int live() const { return static_cast<int>(slots_.size()); }
    // True when every hooked slot still holds our detour (a reload must not free the detour code otherwise).
    bool allIntact();
    // Restores every slot that is still ours; returns how many could not be restored.
    int unhookAll();

private:
    struct Rec { uint32_t detour, orig; };
    IMemory32& mem_;
    std::map<uint32_t, Rec> slots_;
};

}  // namespace nvsplit
