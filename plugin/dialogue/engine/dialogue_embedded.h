// The dialogue data tables compiled into the DLL (plugin/hooks_ui/CMakeLists.txt generates dialogue_data_embedded.cpp from
// plugin/dialogue/data/*.txt at configure time). The installer does not need to ship them; a file next to the DLL still overrides.
#pragma once
#include <cstddef>

namespace fndlg {
struct EmbeddedFile {
    const char* name;
    const unsigned char* data;
    size_t size;
};
extern const EmbeddedFile kEmbeddedDialogueData[];
extern const size_t kEmbeddedDialogueDataCount;
}  // namespace fndlg
