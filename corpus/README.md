# ゴールデン音源コーパス

`docs/04-metrics.md`「ゴールデン音源セット」の実体。選定根拠・ライセンス調査・
却下した候補は `docs/adr/0003-golden-corpus.md` を参照。

**このセットは一度確定したら変えない。** 追加・削除・差し替えには必ずADRを
書く（`docs/adr/README.md`）。

## 構成

```
corpus/
  manifest.json              マニフェスト本体（10〜20エントリ）
  fetch_and_verify.py        取得・SHA256検証スクリプト（標準ライブラリのみ）
  audio/                     リポジトリに同梱している音源（CC0 / PD-ineligible のみ）
  licenses/                  同梱音源のライセンス原文・根拠
  .cache/                    非同梱音源のダウンロード先（gitignore対象、再生成可能）
```

## 使い方

```bash
python3 corpus/fetch_and_verify.py            # 不足分をダウンロードし、全エントリのSHA256を検証
python3 corpus/fetch_and_verify.py --no-fetch # ダウンロードはせず、ローカルにあるものだけ検証
```

SHA256が一致しない、または取得に失敗した場合は非0で終了する。

## 同梱 / 非同梱の方針

- **同梱**（`corpus/audio/`）：CC0 または PD-ineligible（著作権保護対象外の
  主張）など、帰属表示なしで再配布できるものに限定した。同梱した各音源の
  ライセンス原文・根拠は `corpus/licenses/` に置いている
- **非同梱**（`corpus/.cache/` に取得）：CC BY-SA 系（帰属表示・継承が必要）
  のものは、リポジトリに同梱してよいかどうかとは別に、まずリポジトリを
  軽量に保つ目的で非同梱とした。`manifest.json` の `download_url` から
  `fetch_and_verify.py` が取得し、SHA256で内容を保証する

各エントリの `redistributable` / `bundleable_in_repo` はライセンス上の可否を
表すフィールドであり、`bundled` （実際に同梱したかどうか）とは独立している。

## 既知のリスク（フェーズ0後続Issue向けメモ）

`fetch_and_verify.py` の開発中、Wikimedia のCDN (`upload.wikimedia.org`) から
短時間に複数ファイルを取得すると `429 Too Many Requests` が実際に発生する
ことを確認した（指数バックオフによる再試行を実装済み）。CIで毎コミット
このスクリプトを実行する設計（`docs/04-metrics.md`「CIでの扱い」、Issue
#11/#13）にする場合、再試行だけでは不十分になる可能性がある。キャッシュの
永続化またはミラーの検討が必要になったら、ここを参照すること。
