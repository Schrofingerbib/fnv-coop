#include "fnvcoop_snapshot/json.h"
#include <cmath>
#include <cstdio>

namespace fnvcoop::snapshot {

Json& Json::operator[](const std::string& key) {
	if (type_ == Type::Null) type_ = Type::Object;
	return o_[key];
}

Json& Json::push(Json v) {
	if (type_ == Type::Null) type_ = Type::Array;
	a_.push_back(std::move(v));
	return a_.back();
}

std::string Json::formatReal(double d) {
	if (!std::isfinite(d)) return "null";
	char buf[64];
	std::snprintf(buf, sizeof buf, "%.6f", d);
	std::string s = buf;
	while (!s.empty() && s.back() == '0') s.pop_back();
	if (!s.empty() && s.back() == '.') s.pop_back();
	if (s == "-0" || s.empty()) s = "0";
	return s;
}

static void escape(std::string& out, const std::string& s) {
	out += '"';
	for (unsigned char c : s) {
		switch (c) {
		case '"': out += "\\\""; break;
		case '\\': out += "\\\\"; break;
		case '\n': out += "\\n"; break;
		case '\r': out += "\\r"; break;
		case '\t': out += "\\t"; break;
		default:
			if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); out += b; }
			else out += (char)c;
		}
	}
	out += '"';
}

static void nl(std::string& out, int indent, int depth) {
	if (indent < 0) return;
	out += '\n';
	out.append((size_t)indent * depth, ' ');
}

void Json::write(std::string& out, int indent, int depth) const {
	switch (type_) {
	case Type::Null: out += "null"; break;
	case Type::Bool: out += i_ ? "true" : "false"; break;
	case Type::Int: out += std::to_string(i_); break;
	case Type::Real: out += formatReal(d_); break;
	case Type::String: escape(out, s_); break;
	case Type::Array:
		if (a_.empty()) { out += "[]"; break; }
		out += '[';
		for (size_t i = 0; i < a_.size(); ++i) {
			if (i) out += ',';
			nl(out, indent, depth + 1);
			a_[i].write(out, indent, depth + 1);
		}
		nl(out, indent, depth);
		out += ']';
		break;
	case Type::Object: {
		if (o_.empty()) { out += "{}"; break; }
		out += '{';
		bool first = true;
		for (const auto& kv : o_) {
			if (!first) out += ',';
			first = false;
			nl(out, indent, depth + 1);
			escape(out, kv.first);
			out += indent < 0 ? ":" : ": ";
			kv.second.write(out, indent, depth + 1);
		}
		nl(out, indent, depth);
		out += '}';
		break;
	}
	}
}

std::string Json::dump(int indent) const {
	std::string out;
	write(out, indent, 0);
	if (indent >= 0) out += '\n';
	return out;
}

} // namespace fnvcoop::snapshot
