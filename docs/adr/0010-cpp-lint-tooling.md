# 0010. C++ の lint（clang-format / clang-tidy）の導入とCI化

- **状態**：Accepted
- **日付**：2026-09-06
- **対応Issue**：`#83`（C++ エンジンへの clang-format / clang-tidy 導入とCI化）
- **前提**：`docs/adr/0008`（対象＝Linux/gcc・Windows/MSVC、CIで検証）、
  `docs/adr/0005`（ハーネス側の ruff/uv。エンジンのC++は対象外としていた）

## 背景

`docs/adr/0005` は ruff（Python）を導入した際、**エンジン本体（C++）は本ADRの
対象外とする**と明記した。その結果、リポジトリには C++ の linter が存在せず、
C++ コードはビルドと `ctest` だけでしか検証されていなかった。`AGENTS.md` 第8節の
3回ルールは「同じ失敗が3回起きたらCIチェックを追加する」だが、lint の類は CI や
人の目で検出されない限り記録にも残らず、カウント手段そのものが無い状態だった
（`docs/adr/0005` が ruff 導入時に指摘したのと同じ構造）。

`AGENTS.md` 第8節の「実装より先にCIチェック項目を書き起こすな」という禁則は、
「実際に何が壊れるかを観測してから決める」趣旨である。本決定は開発者（ycums）の
明示的な要請に基づき、`main` ブランチ時点の状態を調査したうえで行うもので、
3回ルールの観測を待たずに導入する。この要請は上記禁則よりも優先される。

## 検討した案

### 静的解析ツール

#### 案A：clang-tidy（採用）
- 利点：clang/LLVM 標準の静的解析。`clang-analyzer-*`（データフロー解析）と
  `clang-diagnostic-*`（コンパイラ診断の再現）が核心で、さらに `bugprone-*` /
  `performance-*` を段階的に追加できる。CI で使う場合は `compile_commands.json`
  を第1引数の `-p` で指定する。
- 欠点：セットアップ（compile_commands 生成）が要る。デフォルトで全チェックを
  有効にするとサードパーティ（FetchContent の nlohmann/json 等）を走査して
  ノイズが出るため、`HeaderFilterRegex` で対象をリポジトリ管理下のソースに限定する。

#### 案B：clang-format（採用 ※整形）
- 単体では整形のみで静的解析はしない。clang-tidy と組み合わせて使う
  （このADRでは両方を導入）。

#### 案C：cppcheck
- **却下理由**：`clang-analyzer-*` と検出能力が重複し、ユーザー要請は
  clang-tidy + clang-format だったこと、および clang ツールチェーンとの
  型チェック一体性（clang-tidy は実際のコンパイラ診断も扱える）が clang-tidy に
  あるため。

### 初期チェックセットの範囲

#### 案D：clang-analyzer-* + clang-diagnostic-* のみ（採用）
- `clang-analyzer-*`（静的解析）と `clang-diagnostic-*`（コンパイラ診断）を初期
  セットとする。`docs/adr/0005` の ruff 導入時と同じ方針（デフォルトから始め、
  実際に壊れることを観測してから 3回ルールで追加）に整合する。
- 現時点で `bugprone-*` / `performance-*` を全量入れると、エンジン本体に
  `bugprone-easily-swappable-parameters`（実害のある指摘ではなく、可読性の好み）
  などノイズが多数出る。これらは「実害を観測してから」追加する。

#### 案E：clang-analyzer-* + bugprone-* + performance-* まで初期で入れる
- **却下理由**：`docs/adr/0005` の方針（デフォルトから始め、必要に応じて変える）
  と矛盾する。初期から広く入れると、レビュア��指摘の真贋を見分けるコストが上がる。

### CI の対象範囲

#### 案F：lint-cpp ジョブで clang-format と clang-tidy を Linux で回す（採用）
- `lint-cpp` ジョブ（ubuntu-latest）で両方を検証する。clang-tidy の対象は
  **コア（core）とCLI（cli）の本番ソースのみ**。テストソース（`tests/`）は
  clang-format の対象には含めるが、clang-tidy の対象からは外す。
  理由：テストには意図的なパターン（例：周波数スイープの float ループカウンタ）
  があり、`clang-analyzer-security.FloatLoopCounter` が真のバグでない指摘を出す。
  テストの検証責務はビルド + `ctest` が負う。
- サポート対象の Windows/MSVC での clang-tidy は、`AGENTS.md` のCI実行環境（git-bash）
  での MSYS パス変換の干渉リスクと、lint がコンパイラ非依存のロジック解析を主とする
  ことから、CI では Linux のみで実行する（`docs/adr/0008` の「対象＝CI検証対象」の
  例外ではなく、lint の検証範囲を Linux に限るという独立の決定）。

## 決定

1. **`.clang-format`（LLVM 既定）** をリポジトリルートに置き、全 C++ ソースを
   このスタイルに整形する。スタイルは `BasedOnStyle: LLVM` のみで、追加の
   カスタマイズはしない（`docs/adr/0005` の「デフォルトから始める」方針に同じ）。
2. **既存の C++ ソース（`engine/core/` `engine/cli/` `engine/tests/`）を
   clang-format で一括再整形する**。これは 21 ファイル・約 3,500 行の機械的な
   再整形であり、意味論は変更しない（`--ignore-all-space` 相当の差異も含め、
   すべて整形による改行・折返し・スペース調整のみ。include の並べ替えも発生していない）。
   この差分は開発者の明示的な承認を得ている。
3. **`.clang-tidy`** を置き、`Checks: clang-analyzer-*,clang-diagnostic-*`、
   `WarningsAsErrors: '*'`、`HeaderFilterRegex: engine/(core|cli|tests)/` を設定する。
   FetchContent のサードパーティ（nlohmann/json 等）はノイズ源のため対象外とする
   （HeaderFilterRegex で除外）。
4. **`.github/workflows/ci.yml` に `lint-cpp` ジョブを追加**する。
   - clang-format：`engine/build` を走査から除外した全 C++ ソースを
     `--dry-run --Werror` で検証
   - clang-tidy：`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` で configure して
     `compile_commands.json` を生成し、`-p engine/build` で core+CLI を解析
   - ツールはバージョン固定（clang-format 23.1.0、clang-tidy 22.1.8）で pip 導入
5. **テストソースは clang-format の対象**（整形）だが、**clang-tidy の対象外**。
   （上記 案F 参照）

## 結果

- `.clang-format` / `.clang-tidy` を追加し、全 C++ ソースを clang-format（LLVM既定）
  で再整形した。
- CI（`lint-cpp`）で clang-format 差分ゼロ・clang-tidy 差分ゼロになることを確認した
  （ローカル MSVC ビルドの compile_commands でも core+CLI はゼロ警告、
  再整形後 65 テストすべて通ることも確認）。
- この変更は**音に影響しない**（ソースの整形のみで、DSP ロジック・パラメータ写像・
  プリセット解釈を一切変更しない）。`AGENTS.md` 第4節の指標添付は不要。

## 今後の対象

- `bugprone-*` / `performance-*` などの追加は、実際に起きた失敗・手戻りを
  `docs/adr/0005` と同様に3回ルールで観測してから（`AGENTS.md` 第8節）。
- Windows/MSVC での clang-tidy は、必要が観測された時点で CI への組み込みを再検討する。
