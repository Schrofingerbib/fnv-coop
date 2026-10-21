// The intents engine over the running game (x86 / MSVC only, compiled into nvcoop.dll). NOTHING here has run in a game.
// Addresses come from intents_addrs.h (read from a reference exe by tools/gen_intents_addrs.py), docs/re/dialogmenu-menus-vats-inventory.md
// (terminal internals) and the xNVSE headers; every engine call is SEH-guarded and main-thread only.
#pragma once
#include <vector>

#include "intents/intent_engine.h"

namespace nvcoop::intents::game {

IIntentEngine& engine();

// The call-only pins (MarkScriptEvent, QueueUIMessage, CloseAllMenusCore, TopMenuId) match this exe. Checked once, cached. Without them
// raiseEvent / closeMenus / notify / containerMenuOpen refuse instead of calling into whatever lives there.
bool pinsOk();

// Is the container / loot menu (id 0x3F0) the menu on top right now.
bool containerMenuOpen();

// Guest hook support for terminal entries (TerminalEntryRun, thiscall(entry, terminalRef)): the entry's index path inside the terminal that
// `terminalRefId` is a reference to (entries of sub menus are reached through TerminalEntry::subMenu). false = the entry is not in that terminal.
bool terminalPathOf(uint32_t terminalRefId, void* entry, std::vector<uint8_t>* path);
// The refID of an engine form pointer (0 on a bad pointer).
uint32_t refIdOf(void* form);

}  // namespace nvcoop::intents::game
