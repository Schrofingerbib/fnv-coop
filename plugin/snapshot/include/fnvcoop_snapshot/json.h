// Minimal deterministic JSON value: objects are sorted maps, numbers have fixed formatting.
// Game-free (no NVSE dependency) so it is unit-testable anywhere.
#pragma once
#include <cstdint>
#include <map>
#include <string>
#include <utility>
#include <vector>

namespace fnvcoop::snapshot {

class Json {
public:
	enum class Type { Null, Bool, Int, Real, String, Array, Object };
	using Object = std::map<std::string, Json>; // std::map => keys always sorted
	using Array = std::vector<Json>;

	Json() = default;
	Json(bool v) : type_(Type::Bool), i_(v ? 1 : 0) {}
	Json(int v) : type_(Type::Int), i_(v) {}
	Json(long long v) : type_(Type::Int), i_(v) {}
	Json(unsigned v) : type_(Type::Int), i_(v) {}
	Json(double v) : type_(Type::Real), d_(v) {}
	Json(const char* v) : type_(Type::String), s_(v ? v : "") {}
	Json(std::string v) : type_(Type::String), s_(std::move(v)) {}

	static Json object() { Json j; j.type_ = Type::Object; return j; }
	static Json array() { Json j; j.type_ = Type::Array; return j; }

	Type type() const { return type_; }
	Json& operator[](const std::string& key); // converts Null -> Object
	Json& push(Json v);                       // converts Null -> Array
	size_t size() const { return type_ == Type::Object ? o_.size() : a_.size(); }
	const Object& items() const { return o_; }
	const Array& elems() const { return a_; }

	// Compact (indent<0) or pretty (indent>=0) output. Same input always yields same bytes.
	std::string dump(int indent = -1) const;

	// Real numbers: fixed 6 decimals, trailing zeros trimmed, "-0" normalised, NaN/Inf -> null.
	static std::string formatReal(double d);

private:
	void write(std::string& out, int indent, int depth) const;
	Type type_ = Type::Null;
	long long i_ = 0;
	double d_ = 0;
	std::string s_;
	Object o_;
	Array a_;
};

} // namespace fnvcoop::snapshot
