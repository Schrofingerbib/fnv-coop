#include "script_engine.h"

#include <cmath>
#include <cstdio>

#include "avatar_groups.h"
#include "loco.h"

namespace nvcoop::avatar {

std::string FormatNumber(double v) {
    if (!std::isfinite(v)) v = 0;
    char buf[64];
    std::snprintf(buf, sizeof buf, "%.3f", v);
    // snprintf uses the C locale unless the host changed it; make a decimal comma impossible to leak into script text
    for (char* c = buf; *c; ++c)
        if (*c == ',') *c = '.';
    std::string s = buf;
    if (s == "-0.000") s = "0.000";
    return s;
}

std::string FormatForm(FormId f) {
    char buf[16];
    std::snprintf(buf, sizeof buf, "%08X", (unsigned)f);
    return buf;
}

double YawRadToDegrees(float yawRad) {
    return static_cast<double>(WrapRad(yawRad)) * 180.0 / 3.14159265358979323846;
}

RefId ScriptPuppetEngine::Spawn(FormId base, CellId cell, const Vec3& pos, float yawRad) {
    return run_.PlaceAtMe(base, cell, pos, static_cast<float>(YawRadToDegrees(yawRad)));
}

void ScriptPuppetEngine::Delete(RefId ref) {
    if (!ref) return;
    run_.Exec(ref, "Disable");
    run_.Exec(ref, "MarkForDelete");
}

bool ScriptPuppetEngine::SetRestrained(RefId ref, bool on) {
    return run_.Exec(ref, on ? "SetRestrained 1" : "SetRestrained 0");
}

bool ScriptPuppetEngine::IsRestrained(RefId ref) {
    double v = 0;
    return run_.Query(ref, "GetRestrained", v) && v != 0;
}

bool ScriptPuppetEngine::MakeNonCombatant(RefId ref) {
    bool a = run_.Exec(ref, "SetGhost 1");
    bool b = run_.Exec(ref, "SetIgnoreFriendlyHits 1");
    return a && b;
}

bool ScriptPuppetEngine::SetPos(RefId ref, const Vec3& pos) {
    // one command per axis: SetPos takes a single axis letter
    bool ok = run_.Exec(ref, "SetPos X " + FormatNumber(pos.x));
    ok = run_.Exec(ref, "SetPos Y " + FormatNumber(pos.y)) && ok;
    ok = run_.Exec(ref, "SetPos Z " + FormatNumber(pos.z)) && ok;
    return ok;
}

bool ScriptPuppetEngine::SetYaw(RefId ref, float yawRad) {
    return run_.Exec(ref, "SetAngle Z " + FormatNumber(YawRadToDegrees(yawRad)));
}

bool ScriptPuppetEngine::MoveToCell(RefId ref, CellId cell, const Vec3& pos, float yawRad) {
    Vec3 anchorPos;
    RefId anchor = rd_.AnchorRef(cell, anchorPos);
    if (!anchor) return false;   // nothing to anchor on in that cell: the driver retries / parks
    if (!run_.Exec(ref, "MoveToMarker " + FormatForm(anchor) + " 0 0 0")) return false;
    // the move put it at the anchor; the exact spot is applied once the rebuilt 3D is there (the driver re-sends the pose)
    (void)pos;
    (void)yawRad;
    return true;
}

bool ScriptPuppetEngine::PlayGroup(RefId ref, int baseGroup, int flags) {
    const char* name = GroupName(baseGroup);
    if (!name) return false;   // never guess a group name
    return run_.Exec(ref, std::string("PlayGroup ") + name + ((flags & 1) ? " 1" : " 0"));
}

bool ScriptPuppetEngine::SetSneak(RefId ref, bool on) {
    return run_.Exec(ref, on ? "SetForceSneak 1" : "SetForceSneak 0");
}

bool ScriptPuppetEngine::SetAlert(RefId ref, bool on) {
    return run_.Exec(ref, on ? "SetAlert 1" : "SetAlert 0");
}

bool ScriptPuppetEngine::IsWeaponDrawn(RefId ref) {
    double v = 0;
    return run_.Query(ref, "IsWeaponOut", v) && v != 0;
}

bool ScriptPuppetEngine::AddItem(RefId ref, FormId item, int count) {
    if (count <= 0) return false;
    return run_.Exec(ref, "AddItem " + FormatForm(item) + " " + std::to_string(count) + " 1");
}

bool ScriptPuppetEngine::RemoveItem(RefId ref, FormId item, int count) {
    if (count <= 0) return false;
    return run_.Exec(ref, "RemoveItem " + FormatForm(item) + " " + std::to_string(count) + " 1");
}

bool ScriptPuppetEngine::EquipItem(RefId ref, FormId item) {
    return run_.Exec(ref, "EquipItem " + FormatForm(item) + " 1 1");   // 1 = prevent the actor from unequipping it, 1 = silent
}

bool ScriptPuppetEngine::UnequipItem(RefId ref, FormId item) {
    return run_.Exec(ref, "UnequipItem " + FormatForm(item) + " 0 1");
}

}  // namespace nvcoop::avatar
