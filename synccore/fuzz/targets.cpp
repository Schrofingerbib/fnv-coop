// Target factory.
#include "fuzz.h"

namespace fnvcoop::fuzz {
bool g_keepMessages = false;
void setKeepMessages(bool on) { g_keepMessages = on; }
std::unique_ptr<ITarget> makeMini(const Scenario& s, std::string* err);
std::vector<std::string> miniBugs();
#ifdef FNVCOOP_FUZZ_EM
std::unique_ptr<ITarget> makeEm(const Scenario& s, std::string* err);
std::vector<std::string> emBugs();
#endif

std::unique_ptr<ITarget> makeTarget(const std::string& target, const Scenario& s, std::string* err) {
    if (target == "mini") return makeMini(s, err);
#ifdef FNVCOOP_FUZZ_EM
    if (target == "em") return makeEm(s, err);
#endif
    if (err) *err = "target '" + target + "' is not available in this build";
    return nullptr;
}
std::vector<std::string> knownBugs(const std::string& target) {
    if (target == "mini") return miniBugs();
#ifdef FNVCOOP_FUZZ_EM
    if (target == "em") return emBugs();
#endif
    return {};
}
}  // namespace fnvcoop::fuzz
