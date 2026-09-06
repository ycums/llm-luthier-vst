#pragma once

#include <stdexcept>
#include <string>

namespace luthier {

// プリセットの内容に関するあらゆる問題（不正なJSON、docs/03-preset-format.md の
// 構造規約違反：必須フィールド欠落・未知フィールド・型不一致・非対応バージョン、
// および解決できないモジュレーションのルーティング先パス）を表す単一の例外型。
//
// docs/03「未知のフィールドを見つけたエンジンはエラーで停止する。黙って無視しない」
// が要求する「止まる」挙動を、呼び出し側にとって一貫させるためにまとめている
// （AGENTS.md 第7節「未確定事項の扱い」と同じ精神：それらしく解釈して進めない）。
class PresetError : public std::runtime_error {
public:
    explicit PresetError(const std::string& message) : std::runtime_error(message) {}
};

}  // namespace luthier
