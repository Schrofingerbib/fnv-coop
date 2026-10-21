// Pins checked against the reference exe (<reference exe>) by tests/test_bots.cpp (BOTS_EXE=<exe>) and by the pin verifier
// tools/bots/re/verify_pins.py. The pin verifier is not in the public repo, so only the C++ test can run from a clone.
// No other engine function is patched by plugin/bots.
#pragma once
#include "hook_patch.h"

namespace nvbots::addr {
// OSInputGlobals::GetControlState(ctrl, state): thiscall (ecx = OSInputGlobals*), `ret 8`. Every player-control and weapon read goes
// through it (call sites: PlayerControls update at 0x93E-0x942xxx pass (0,0)..(3,0) for movement, (8,1) crouch edge, (5,*) activate).
// Prologue: push ebp; mov ebp,esp; sub esp,14h; mov [ebp-10h],ecx; cmp [ebp+8],1Ch; jge ...  We steal the first 6 bytes (3 whole instructions).
inline constexpr nvhooks::HookSpec GetControlState = {
    "BotGetControlState", 0xA24660U,
    {0x55, 0x8b, 0xec, 0x83, 0xec, 0x14, 0x89, 0x4d, 0xf0, 0x83, 0x7d, 0x08, 0x1c, 0x0f, 0x8d, 0x1f}, 16, 6, 8};

inline constexpr unsigned OSInputGlobalsPtr = 0x11F35CCU;   // *(OSInputGlobals**), from xNVSE GameOSDepend.cpp
inline constexpr unsigned GamepadModeFlag = 0x11F35C8U;     // byte tested at the top of GetControlState (non-zero: read pad bindings)

inline constexpr const nvhooks::HookSpec* kAllHooks[] = {&GetControlState};

// ---- engine functions called by plugin/bots (T6a). Prologues pinned from the exe; docs/re/dialogmenu-menus-vats-inventory.md.
struct CallPin { const char* name; unsigned addr; unsigned char bytes[16]; int ret; };
// cdecl(ref) -> ExtraContainerChanges::Data* (creates the data when missing)
inline constexpr CallPin GetContainerData = {"GetContainerData", 0x4BF220U, {0x55, 0x8b, 0xec, 0x6a, 0xff, 0x68, 0x13, 0x12, 0xef, 0x00, 0x64, 0xa1, 0x00, 0x00, 0x00, 0x00}, 0};
// thiscall(Data*, TESForm*) -> total count, ret 4
inline constexpr CallPin ItemTotal = {"ItemTotal", 0x4C8F30U, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x14, 0x89, 0x4d, 0xf0, 0xc7, 0x45, 0xf4, 0x00, 0x00, 0x00, 0x00}, 4};
// thiscall(InterfaceManager*) -> top menu id
inline constexpr CallPin TopMenuId = {"TopMenuId", 0x714F00U, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x08, 0x89, 0x4d, 0xf8, 0xc7, 0x45, 0xfc, 0x00, 0x00, 0x00, 0x00}, 0};
// thiscall(ListBox*, idx) -> bool, ret 4
inline constexpr CallPin LbSelect = {"LbSelect", 0x83E430U, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x10, 0x89, 0x4d, 0xf4, 0xc7, 0x45, 0xfc, 0x00, 0x00, 0x00, 0x00}, 4};
// thiscall(ListBox*) -> selected item
inline constexpr CallPin LbSelected = {"LbSelected", 0x83E4C0U, {0x55, 0x8b, 0xec, 0x51, 0x89, 0x4d, 0xfc, 0x8b, 0x45, 0xfc, 0x83, 0x38, 0x00, 0x74, 0x10, 0x8b}, 0};
// thiscall(item) -> TESTopicInfo*
inline constexpr CallPin ItemInfo = {"ItemInfo", 0x9611E0U, {0x55, 0x8b, 0xec, 0x51, 0x89, 0x4d, 0xfc, 0x8b, 0x45, 0xfc, 0x8b, 0x40, 0x18, 0x8b, 0xe5, 0x5d}, 0};
// thiscall(info) -> flags1 & 0x80
inline constexpr CallPin InfoFlag80 = {"InfoFlag80", 0x7627C0U, {0x55, 0x8b, 0xec, 0x51, 0x89, 0x4d, 0xfc, 0x8b, 0x45, 0xfc, 0x0f, 0xbe, 0x48, 0x25, 0x81, 0xe1}, 0};
// thiscall(info) -> speech-check style
inline constexpr CallPin InfoSpecialB = {"InfoSpecialB", 0x7627E0U, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x08, 0x89, 0x4d, 0xfc, 0x8b, 0x45, 0xfc, 0x83, 0x78, 0x40, 0x00}, 0};
// thiscall(info) -> speech-check style
inline constexpr CallPin InfoSpecialC = {"InfoSpecialC", 0x762820U, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x08, 0x89, 0x4d, 0xfc, 0x8b, 0x45, 0xfc, 0x83, 0x78, 0x40, 0x00}, 0};
// thiscall(DialogMenu*, item), ret 4
inline constexpr CallPin DlgItemClick = {"DlgItemClick", 0x762860U, {0x55, 0x8b, 0xec, 0x51, 0x56, 0x89, 0x4d, 0xfc, 0x8b, 0x4d, 0x08, 0xe8, 0x70, 0xe9, 0x1f, 0x00}, 4};
// thiscall(ListBox*)
inline constexpr CallPin LbAfter = {"LbAfter", 0x83EBB0U, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x0c, 0x89, 0x4d, 0xf4, 0x8b, 0x4d, 0xf4, 0xe8, 0xff, 0xf8, 0xff}, 0};
// thiscall(DialogMenu*, byte) -> next state, ret 4
inline constexpr CallPin DlgSpeak = {"DlgSpeak", 0x762FF0U, {0x55, 0x8b, 0xec, 0x6a, 0xff, 0x68, 0x77, 0xad, 0xf0, 0x00, 0x64, 0xa1, 0x00, 0x00, 0x00, 0x00}, 4};
// thiscall(clock 0x11F6394) -> ms
inline constexpr CallPin Tick = {"Tick", 0x825C00U, {0x55, 0x8b, 0xec, 0x51, 0x89, 0x4d, 0xfc, 0x8b, 0x45, 0xfc, 0x8b, 0x40, 0x14, 0x8b, 0xe5, 0x5d}, 0};
inline constexpr CallPin TermClick = {"TermClick", 0x757F70U, {0x55, 0x8b, 0xec, 0x6a, 0xff, 0x68, 0xb8, 0xa6, 0xf0, 0x00, 0x64, 0xa1, 0x00, 0x00, 0x00, 0x00}, 8};
// ---- genuine fast travel (the map menu's own path, found statically). MapMenu (*0x11DA368) +0x118 holds the clicked marker ref; after the
// "Do you want to travel" box answers Yes, the deferred worker 0x798A00 does: obj = heap(0x34) + ctor 0x456540; obj+0x30 = marker; player->SetPending(obj).
// The main loop (0x86F94F -> 0x93BEA0) then consumes player+0x1EC and moves the player. The gate 0x93D660 is the map's own "may travel" check.
// thiscall(player) -> bool (shows the engine's own refusal message), plain ret
inline constexpr CallPin PlayerCanTravel = {"PlayerCanTravel", 0x93D660U, {0x55, 0x8b, 0xec, 0x81, 0xec, 0x28, 0x02, 0x00, 0x00, 0xa1, 0xbc, 0x16, 0x1c, 0x01, 0x33, 0xc5}, 0};
// thiscall(player, travelObj*), ret 4 (frees an older pending object first)
inline constexpr CallPin PendingTravelSet = {"PendingTravelSet", 0x93BE30U, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x08, 0x89, 0x4d, 0xf8, 0x8b, 0x45, 0xf8, 0x83, 0xb8, 0xec, 0x01}, 4};
// thiscall(mem) -> mem (0x34-byte travel object)
inline constexpr CallPin TravelObjCtor = {"TravelObjCtor", 0x456540U, {0x55, 0x8b, 0xec, 0x51, 0x89, 0x4d, 0xfc, 0x8b, 0x4d, 0xfc, 0x83, 0xc1, 0x08, 0xe8, 0x6e, 0xb0}, 0};
// cdecl(size) -> game heap block
inline constexpr CallPin HeapAlloc = {"HeapAlloc", 0x401000U, {0x55, 0x8b, 0xec, 0x8b, 0x45, 0x08, 0x50, 0xe8, 0x14, 0x00, 0x00, 0x00, 0x8b, 0xc8, 0xe8, 0x2d}, 0};
inline constexpr unsigned kPlayerPending = 0x1ECU, kTravelObjSize = 0x34U, kTravelObjMarker = 0x30U;   // player+0x1EC pending object; obj+0x30 = marker ref
// ---- native message box press (bot.menu.press when the UI layer has no box). Static RE on the 1.4.0.525 exe.
// MessageMenu (menu id 1001 = 0x3E9) vtable, from MSVC RTTI (COL 0x1119C24, base Menu 0x1095484). Slot 3 (+0x0C) is HandleClick 0x7AA070,
// slot 12 (+0x30) is HandleKeyboardInput 0x7AA110, slot 13 (+0x34) returns 0x3E9. The press refuses every object whose vtable is not this one:
// the button-list offsets and the slot-3 behaviour below are only known for MessageMenu.
inline constexpr unsigned MessageMenuVtbl = 0x107566CU;
inline constexpr unsigned kMenuSlotHandleClick = 0x0CU;   // Menu vtable slot 3 (xNVSE GameUI.h: HandleClick(buttonID, Tile*))
// MessageMenu::HandleClick(id, tile), thiscall, ret 8. With id 7 and a tile: value = ListBox<int>(this+0x40) value of that tile (0x7E1110,
// 0 when the tile is not in the list); InterfaceManager+0xE4 (msgBoxButton) = value; run the message callback; 0x7AA530 closes the box
// (or shows the next queued message). The engine's own Enter / hotkey paths (0x7AA110) do the same: list index -> tile -> HandleClick(7, tile).
inline constexpr CallPin MsgMenuHandleClick = {"MsgMenuHandleClick", 0x7AA070U, {0x55, 0x8b, 0xec, 0x51, 0x53, 0x89, 0x4d, 0xfc, 0x83, 0x7d, 0x0c, 0x00, 0x75, 0x19, 0x83, 0x7d}, 8};
inline constexpr const CallPin* kAllCalls[] = {&PlayerCanTravel, &PendingTravelSet, &TravelObjCtor, &HeapAlloc, &TermClick, &GetContainerData, &ItemTotal, &TopMenuId, &LbSelect, &LbSelected, &ItemInfo, &InfoFlag80, &InfoSpecialB, &InfoSpecialC, &DlgItemClick, &LbAfter, &DlgSpeak, &Tick, &MsgMenuHandleClick};
// ---- data pins: a dword in the exe image that must hold `value` (vtable slots in .rdata). Checked at init with the call pins.
struct DataPin { const char* name; unsigned addr; unsigned value; };
inline constexpr DataPin MsgMenuSlotHandleClick = {"MessageMenu.vtbl[3]", MessageMenuVtbl + kMenuSlotHandleClick, 0x7AA070U};
inline constexpr const DataPin* kAllData[] = {&MsgMenuSlotHandleClick};

// ---- always-run state, read (never written) by bot.move so that `run` picks the speed, not the key. Static RE on the 1.4.0.525
// exe. Code pins: 16 bytes of the engine code that uses each address. Nothing is called or patched here.
// PlayerCharacter::vf2F8 (0x93E860, the player-controls update, this = PlayerCharacter in [ebp-0x18], OSInputGlobals in [ebp-0x1C]):
//   0x940C7D  GetControlState(10 AlwaysRun, 1 pressed) != 0  ->  0x940C8D  player+0x651 = !player+0x651  (the always-run toggle byte)
//   0x941A2B  if (!PadMode() 0x4B71D0 && player+0x651) { Run (9) not pressed and not held -> flags |= 0x200 run }
//             else { Run (9) pressed or held -> flags |= 0x200 run }  (forward/back/strafe then set 0x100 walk when 0x200 is clear)
// PadMode 0x4B71D0 = byte 0x11D8A84 (XInputGetState(0) == 0, refreshed every frame) && Setting 0x11D8C4C "bDisable360Controller:Interface"
// value byte (+4) == 0. The 0x651 byte starts from Setting 0x11E0B4C "bAlwaysRunByDefault:Controls" (player init 0x938A52).
struct CodePin { const char* name; unsigned addr; unsigned char bytes[16]; };
// push 1; push 0Ah; mov ecx,[ebp-1Ch]; call GetControlState; test eax,eax; jz +1Ah
inline constexpr CodePin AlwaysRunEdge = {"AlwaysRunEdge", 0x940C7DU, {0x6a, 0x01, 0x6a, 0x0a, 0x8b, 0x4d, 0xe4, 0xe8, 0xd7, 0x39, 0x0e, 0x00, 0x85, 0xc0, 0x74, 0x1a}};
// mov edx,[ebp-18h]; movzx eax,byte [edx+651h]; neg eax; sbb eax,eax; add eax,1  (then mov [ecx+651h],al)
inline constexpr CodePin AlwaysRunFlip = {"AlwaysRunFlip", 0x940C8DU, {0x8b, 0x55, 0xe8, 0x0f, 0xb6, 0x82, 0x51, 0x06, 0x00, 0x00, 0xf7, 0xd8, 0x1b, 0xc0, 0x83, 0xc0}};
// call PadMode (0x4B71D0); movzx ecx,al; test ecx,ecx; jnz <Run-key branch>
inline constexpr CodePin RunPadGate = {"RunPadGate", 0x941A2BU, {0xe8, 0xa0, 0x57, 0xb7, 0xff, 0x0f, 0xb6, 0xc8, 0x85, 0xc9, 0x0f, 0x85, 0x88, 0x00, 0x00, 0x00}};
// movzx eax,byte [edx+651h]; test eax,eax; jz <Run-key branch>; push 1; push 9; mov ecx,...  (always-run branch asks for Run (9))
inline constexpr CodePin RunAlwaysRead = {"RunAlwaysRead", 0x941A3EU, {0x0f, 0xb6, 0x82, 0x51, 0x06, 0x00, 0x00, 0x85, 0xc0, 0x74, 0x7a, 0x6a, 0x01, 0x6a, 0x09, 0x8b}};
// PadMode: push ebp; mov ebp,esp; push ecx; movzx eax,byte [11D8A84h]; test eax,eax; jz ...; call ...
inline constexpr CodePin PadModeConnected = {"PadModeConnected", 0x4B71D0U, {0x55, 0x8b, 0xec, 0x51, 0x0f, 0xb6, 0x05, 0x84, 0x8a, 0x1d, 0x01, 0x85, 0xc0, 0x74, 0x1f, 0xe8}};
// PadMode (cont.): mov ecx,11D8C4Ch (bDisable360Controller); call 0x4B9930 (returns byte [ecx+4] == 0); movzx ...
inline constexpr CodePin PadModeSetting = {"PadModeSetting", 0x4B71E0U, {0x2c, 0x00, 0x00, 0x00, 0xb9, 0x4c, 0x8c, 0x1d, 0x01, 0xe8, 0x42, 0x27, 0x00, 0x00, 0x0f, 0xb6}};
inline constexpr const CodePin* kRunPins[] = {&AlwaysRunEdge, &AlwaysRunFlip, &RunPadGate, &RunAlwaysRead, &PadModeConnected, &PadModeSetting};
}  // namespace nvbots::addr
