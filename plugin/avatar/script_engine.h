// IPuppetEngine implemented on script-command text, so the puppet layer needs no engine address of its own.
// Only commands that exist in the pinned vanilla command table (plugin/hooks/tools/re/command_table.json, the dump
// docs/engine-addresses.md is built from) are used: SetPos, SetAngle, PlayGroup, SetRestrained, GetRestrained,
// SetForceSneak, SetAlert, SetGhost, SetIgnoreFriendlyHits, AddItem, RemoveItem, EquipItem, UnequipItem, Disable,
// MarkForDelete, MoveToMarker. tests/test_avatar.cpp pins the exact text each call produces.
//
// What this backend cannot do (each is a TODO-RE; the driver treats them as ordinary refusals):
//   * SetWeaponDrawn: there is no script command to draw a weapon; the Equip / Unequip groups carry the pose.
//   * ApplyAppearance: face / hair of the template is kept as is.
//   * performance: every line is compiled by the script interpreter (Script::Run, 0x5AC400 per engine-addresses.md).
//     Cost per line is NOT measured. If it matters, replace the hot calls (SetPos, SetAngle, PlayGroup) with direct
//     member calls after RE; the interface above does not change.
//   * the exact meaning of PlayGroup's flag, EquipItem's flags and the sign of SetAngle's Z are taken from the GECK
//     wiki, not measured.
#pragma once
#include <string>
#include <vector>

#include "puppet_engine.h"

namespace nvcoop::avatar {

// Runs one script line with `ctx` as the calling reference ("<ctx>.<line>"). Implemented by the plugin glue.
class IScriptRunner {
public:
    virtual ~IScriptRunner() = default;
    virtual bool Exec(RefId ctx, const std::string& line) = 0;
    // A condition function's value (e.g. "GetRestrained"). False when the call itself failed.
    virtual bool Query(RefId ctx, const std::string& line, double& out) = 0;
    // Creates the puppet from `base` in `cell` at `pos` / `yawDeg` and TAGS it (dedicated base form or extra-data name)
    // so Reads::FindTaggedPuppets can find it again after a load. 0 = failed.
    virtual RefId PlaceAtMe(FormId base, CellId cell, const Vec3& pos, float yawDeg) = 0;
};

// Reads a script line cannot give. Implemented by the plugin glue with SEH-guarded memory reads (TODO-RE offsets).
class IEngineReads {
public:
    virtual ~IEngineReads() = default;
    virtual bool WorldReady() = 0;
    virtual uint32_t WorldEpoch() = 0;
    virtual bool MenuOpen() = 0;
    virtual bool CellLoaded(CellId cell) = 0;
    virtual bool SameSpace(CellId a, CellId b) = 0;
    virtual bool IsValid(RefId ref) = 0;
    virtual bool Has3D(RefId ref) = 0;
    virtual uint32_t Generation3D(RefId ref) = 0;
    virtual std::vector<RefId> FindTaggedPuppets() = 0;
    virtual bool GetPose(RefId ref, CellId& cell, Vec3& pos) = 0;
    virtual int CurrentGroup(RefId ref) = 0;
    virtual bool GetEquipped(RefId ref, std::vector<FormId>& out) = 0;
    virtual int ItemCount(RefId ref, FormId item) = 0;
    // A reference in `cell` (a door or marker) to anchor MoveToMarker on, and its position. 0 = none known.
    virtual RefId AnchorRef(CellId cell, Vec3& anchorPos) = 0;
};

// Number formatting used for script lines: invariant locale, 3 decimals, no exponent.
std::string FormatNumber(double v);
std::string FormatForm(FormId f);   // 8 hex digits
double YawRadToDegrees(float yawRad);   // wrapped to (-180, 180]

class ScriptPuppetEngine : public IPuppetEngine {
public:
    ScriptPuppetEngine(IScriptRunner& run, IEngineReads& reads) : run_(run), rd_(reads) {}

    bool WorldReady() override { return rd_.WorldReady(); }
    uint32_t WorldEpoch() override { return rd_.WorldEpoch(); }
    bool MenuOpen() override { return rd_.MenuOpen(); }
    bool CellLoaded(CellId c) override { return rd_.CellLoaded(c); }
    bool SameSpace(CellId a, CellId b) override { return rd_.SameSpace(a, b); }

    RefId Spawn(FormId base, CellId cell, const Vec3& pos, float yawRad) override;
    void Delete(RefId ref) override;
    bool IsValid(RefId ref) override { return rd_.IsValid(ref); }
    bool Has3D(RefId ref) override { return rd_.Has3D(ref); }
    uint32_t Generation3D(RefId ref) override { return rd_.Generation3D(ref); }
    std::vector<RefId> FindTaggedPuppets() override { return rd_.FindTaggedPuppets(); }

    bool SetRestrained(RefId ref, bool on) override;
    bool IsRestrained(RefId ref) override;
    bool MakeNonCombatant(RefId ref) override;
    bool ApplyAppearance(RefId, const Appearance&) override { return false; }   // TODO-RE

    bool SetPos(RefId ref, const Vec3& pos) override;
    bool SetYaw(RefId ref, float yawRad) override;
    bool MoveToCell(RefId ref, CellId cell, const Vec3& pos, float yawRad) override;
    bool GetPose(RefId ref, CellId& cell, Vec3& pos) override { return rd_.GetPose(ref, cell, pos); }
    bool PlayGroup(RefId ref, int baseGroup, int flags) override;
    int CurrentGroup(RefId ref) override { return rd_.CurrentGroup(ref); }
    bool SetSneak(RefId ref, bool on) override;
    bool SetAlert(RefId ref, bool on) override;
    bool SetWeaponDrawn(RefId, bool) override { return false; }   // TODO-RE: no script command
    bool IsWeaponDrawn(RefId ref) override;

    bool GetEquipped(RefId ref, std::vector<FormId>& out) override { return rd_.GetEquipped(ref, out); }
    int ItemCount(RefId ref, FormId item) override { return rd_.ItemCount(ref, item); }
    bool AddItem(RefId ref, FormId item, int count) override;
    bool RemoveItem(RefId ref, FormId item, int count) override;
    bool EquipItem(RefId ref, FormId item) override;
    bool UnequipItem(RefId ref, FormId item) override;

private:
    IScriptRunner& run_;
    IEngineReads& rd_;
};

}  // namespace nvcoop::avatar
