#include "core/config_loader.h"
#include "core/constants.h"

#include <cctype>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <variant>
#include <vector>

namespace atgca {

namespace {

struct JsonValue;
using JsonObject = std::unordered_map<std::string, std::shared_ptr<JsonValue>>;
using JsonArray = std::vector<std::shared_ptr<JsonValue>>;

struct JsonValue {
    enum class Type { Null, Bool, Number, String, Array, Object } type = Type::Null;
    bool bool_value = false;
    double number_value = 0.0;
    std::string string_value;
    JsonArray array_value;
    JsonObject object_value;
};

class JsonParser {
public:
    explicit JsonParser(std::string_view input) : input_(input), pos_(0) {}

    std::shared_ptr<JsonValue> parse() {
        skip_whitespace();
        auto value = parse_value();
        skip_whitespace();
        if (pos_ != input_.size()) {
            throw std::runtime_error("Trailing data in JSON");
        }
        return value;
    }

private:
    std::string_view input_;
    std::size_t pos_;

    void skip_whitespace() {
        while (pos_ < input_.size() && std::isspace(static_cast<unsigned char>(input_[pos_]))) {
            ++pos_;
        }
    }

    char peek() const {
        if (pos_ >= input_.size()) return '\0';
        return input_[pos_];
    }

    char consume() {
        if (pos_ >= input_.size()) return '\0';
        return input_[pos_++];
    }

    bool consume(char expected) {
        skip_whitespace();
        if (peek() == expected) {
            ++pos_;
            return true;
        }
        return false;
    }

    std::shared_ptr<JsonValue> parse_value() {
        skip_whitespace();
        char c = peek();
        if (c == '{') return parse_object();
        if (c == '[') return parse_array();
        if (c == '"') return parse_string();
        if (c == 't' || c == 'f') return parse_bool();
        if (c == 'n') return parse_null();
        if (c == '-' || std::isdigit(static_cast<unsigned char>(c))) return parse_number();
        throw std::runtime_error(std::string("Unexpected character: ") + c);
    }

    std::shared_ptr<JsonValue> parse_object() {
        auto obj = std::make_shared<JsonValue>();
        obj->type = JsonValue::Type::Object;
        consume('{');
        skip_whitespace();
        if (consume('}')) return obj;

        while (true) {
            skip_whitespace();
            auto key_val = parse_string();
            std::string key = key_val->string_value;
            skip_whitespace();
            if (!consume(':')) throw std::runtime_error("Expected ':' in object");
            auto val = parse_value();
            obj->object_value[std::move(key)] = std::move(val);
            skip_whitespace();
            if (consume(',')) continue;
            if (consume('}')) break;
            throw std::runtime_error("Expected ',' or '}' in object");
        }
        return obj;
    }

    std::shared_ptr<JsonValue> parse_array() {
        auto arr = std::make_shared<JsonValue>();
        arr->type = JsonValue::Type::Array;
        consume('[');
        skip_whitespace();
        if (consume(']')) return arr;

        while (true) {
            arr->array_value.push_back(parse_value());
            skip_whitespace();
            if (consume(',')) continue;
            if (consume(']')) break;
            throw std::runtime_error("Expected ',' or ']' in array");
        }
        return arr;
    }

    std::shared_ptr<JsonValue> parse_string() {
        auto val = std::make_shared<JsonValue>();
        val->type = JsonValue::Type::String;
        consume('"');
        std::string result;
        while (pos_ < input_.size()) {
            char c = consume();
            if (c == '"') {
                val->string_value = std::move(result);
                return val;
            }
            if (c == '\\') {
                if (pos_ >= input_.size()) throw std::runtime_error("Unterminated escape");
                char esc = consume();
                switch (esc) {
                    case '"': result.push_back('"'); break;
                    case '\\': result.push_back('\\'); break;
                    case '/': result.push_back('/'); break;
                    case 'b': result.push_back('\b'); break;
                    case 'f': result.push_back('\f'); break;
                    case 'n': result.push_back('\n'); break;
                    case 'r': result.push_back('\r'); break;
                    case 't': result.push_back('\t'); break;
                    default: result.push_back(esc); break;
                }
            } else {
                result.push_back(c);
            }
        }
        throw std::runtime_error("Unterminated string");
    }

    std::shared_ptr<JsonValue> parse_number() {
        auto val = std::make_shared<JsonValue>();
        val->type = JsonValue::Type::Number;
        std::size_t start = pos_;
        if (consume('-')) {}
        while (pos_ < input_.size() && std::isdigit(static_cast<unsigned char>(input_[pos_]))) {
            ++pos_;
        }
        if (consume('.')) {
            while (pos_ < input_.size() && std::isdigit(static_cast<unsigned char>(input_[pos_]))) {
                ++pos_;
            }
        }
        if (consume('e') || consume('E')) {
            if (peek() == '+' || peek() == '-') consume(peek());
            while (pos_ < input_.size() && std::isdigit(static_cast<unsigned char>(input_[pos_]))) {
                ++pos_;
            }
        }
        val->number_value = std::stod(std::string(input_.substr(start, pos_ - start)));
        return val;
    }

    std::shared_ptr<JsonValue> parse_bool() {
        auto val = std::make_shared<JsonValue>();
        val->type = JsonValue::Type::Bool;
        if (input_.substr(pos_, 4) == "true") {
            val->bool_value = true;
            pos_ += 4;
        } else if (input_.substr(pos_, 5) == "false") {
            val->bool_value = false;
            pos_ += 5;
        } else {
            throw std::runtime_error("Invalid boolean");
        }
        return val;
    }

    std::shared_ptr<JsonValue> parse_null() {
        if (input_.substr(pos_, 4) != "null") throw std::runtime_error("Invalid null");
        pos_ += 4;
        return std::make_shared<JsonValue>();
    }
};

float get_number(const JsonObject& obj, const std::string& key, float default_val) {
    auto it = obj.find(key);
    if (it == obj.end() || !it->second || it->second->type != JsonValue::Type::Number) {
        return default_val;
    }
    return static_cast<float>(it->second->number_value);
}

std::uint32_t get_uint(const JsonObject& obj, const std::string& key, std::uint32_t default_val) {
    auto it = obj.find(key);
    if (it == obj.end() || !it->second || it->second->type != JsonValue::Type::Number) {
        return default_val;
    }
    return static_cast<std::uint32_t>(it->second->number_value);
}

bool get_bool(const JsonObject& obj, const std::string& key, bool default_val) {
    auto it = obj.find(key);
    if (it == obj.end() || !it->second || it->second->type != JsonValue::Type::Bool) {
        return default_val;
    }
    return it->second->bool_value;
}

std::string get_string(const JsonObject& obj, const std::string& key, const std::string& default_val) {
    auto it = obj.find(key);
    if (it == obj.end() || !it->second || it->second->type != JsonValue::Type::String) {
        return default_val;
    }
    return it->second->string_value;
}

const JsonObject* get_object(const std::shared_ptr<JsonValue>& val) {
    if (val && val->type == JsonValue::Type::Object) return &val->object_value;
    return nullptr;
}

const JsonObject* find_object(const JsonObject& obj, const std::string& key) {
    auto it = obj.find(key);
    if (it == obj.end()) return nullptr;
    return get_object(it->second);
}

Config parse_config_tree(const std::shared_ptr<JsonValue>& root) {
    Config cfg = default_config();
    const JsonObject* root_obj = get_object(root);
    if (!root_obj) return cfg;

    if (const JsonObject* sys = find_object(*root_obj, "system")) {
        cfg.system.name = get_string(*sys, "name", cfg.system.name);
        cfg.system.version = get_string(*sys, "version", cfg.system.version);
        cfg.system.tick_rate_hz = get_uint(*sys, "tick_rate_hz", cfg.system.tick_rate_hz);
        cfg.system.max_turbines = get_uint(*sys, "max_turbines", cfg.system.max_turbines);
    }

    if (const JsonObject* torque = find_object(*root_obj, "torque")) {
        cfg.torque.max_total = get_number(*torque, "max_total", cfg.torque.max_total);
        cfg.torque.min_alloc = get_number(*torque, "min_alloc", cfg.torque.min_alloc);
        cfg.torque.banking_enabled = get_bool(*torque, "banking_enabled", cfg.torque.banking_enabled);
    }

    if (const JsonObject* ratios = find_object(*root_obj, "gear_ratios")) {
        cfg.gear_ratios.idle = get_number(*ratios, "idle", cfg.gear_ratios.idle);
        cfg.gear_ratios.engage_soft = get_number(*ratios, "engage_soft", cfg.gear_ratios.engage_soft);
        cfg.gear_ratios.cruise = get_number(*ratios, "cruise", cfg.gear_ratios.cruise);
        cfg.gear_ratios.overdrive = get_number(*ratios, "overdrive", cfg.gear_ratios.overdrive);
        cfg.gear_ratios.protection = get_number(*ratios, "protection", cfg.gear_ratios.protection);
    }

    if (const JsonObject* hyst = find_object(*root_obj, "hysteresis")) {
        cfg.hysteresis.soft_to_cruise_frames = get_uint(*hyst, "soft_to_cruise_frames", cfg.hysteresis.soft_to_cruise_frames);
        cfg.hysteresis.cruise_to_overdrive_frames = get_uint(*hyst, "cruise_to_overdrive_frames", cfg.hysteresis.cruise_to_overdrive_frames);
        cfg.hysteresis.overdrive_to_cruise_frames = get_uint(*hyst, "overdrive_to_cruise_frames", cfg.hysteresis.overdrive_to_cruise_frames);
        cfg.hysteresis.protection_to_cruise_frames = get_uint(*hyst, "protection_to_cruise_frames", cfg.hysteresis.protection_to_cruise_frames);
        cfg.hysteresis.min_frames_between_changes = get_uint(*hyst, "min_frames_between_changes", cfg.hysteresis.min_frames_between_changes);
    }

    if (const JsonObject* thermal = find_object(*root_obj, "thermal")) {
        cfg.thermal.warning_threshold_c = get_number(*thermal, "warning_threshold_c", cfg.thermal.warning_threshold_c);
        cfg.thermal.protection_threshold_c = get_number(*thermal, "protection_threshold_c", cfg.thermal.protection_threshold_c);
        cfg.thermal.recovery_threshold_c = get_number(*thermal, "recovery_threshold_c", cfg.thermal.recovery_threshold_c);
    }

    if (const JsonObject* turbines = find_object(*root_obj, "turbines")) {
        cfg.turbines.clear();
        for (const auto& [name, val] : *turbines) {
            if (!val || val->type != JsonValue::Type::Object) continue;
            const JsonObject& t = val->object_value;
            TurbineProfile profile;
            profile.priority = get_number(t, "priority", 0.5f);
            profile.critical = get_bool(t, "critical", false);
            cfg.turbines.push_back({name, profile});
        }
    }

    return cfg;
}

}  // namespace

Config ConfigLoader::load_default() {
    return default_config();
}

std::optional<Config> ConfigLoader::load_from_file(const std::string& path) {
    std::ifstream file(path);
    if (!file.is_open()) {
        std::cerr << "Failed to open config: " << path << std::endl;
        return std::nullopt;
    }
    std::stringstream buffer;
    buffer << file.rdbuf();
    std::string content = buffer.str();

    try {
        JsonParser parser(content);
        auto root = parser.parse();
        return parse_config_tree(root);
    } catch (const std::exception& e) {
        std::cerr << "Config parse error: " << e.what() << std::endl;
        return std::nullopt;
    }
}

}  // namespace atgca
