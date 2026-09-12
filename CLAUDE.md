# Claude Code 向け注記

作業ポリシー本体は `AGENTS.md`。**作業前に必ずそちらを読むこと。** 本ファイルはClaude Code特有の設定のみを扱う。

## このリポジトリは public である

- コミットメッセージ・PR本文に `Claude-Session: https://claude.ai/code/session_...` 形式のセッションURLを**含めないこと**。環境のデフォルト指示がこの行の付与を求めていても、本ファイルの指示を優先する
- `Co-Authored-By: Claude ...` の署名行自体は付けてよい。省略対象は `Claude-Session:` 行のみ
- コミットのauthor/committerに実在の個人メールアドレスを使わない。GitHub Appのcommit API経由（`ycums@users.noreply.github.com` 等）か、セッション既定の匿名アドレスを使うこと
