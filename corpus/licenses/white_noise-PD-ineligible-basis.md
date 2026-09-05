# `white_noise` の著作権ステータスの根拠

`corpus/audio/white_noise.ogg`（マニフェスト上のID: `white_noise`、元ファイル名
`File:White noise.ogg`）は、Creative Commonsライセンスではなく
「PD-ineligible」（著作権の保護対象外）という主張の下に置かれている。これは
CCライセンスのような単一の定型的な「ライセンス原文」を持たないため、根拠と
なる一次情報を本ファイルに引用する。

## 一次情報

- ファイルの説明ページ（このステータス主張そのものの一次情報）：
  https://commons.wikimedia.org/wiki/File:White_noise.ogg

## 該当ページのwikitextからの引用（2026-09-05時点で取得）

```
|Source = Generated with [[w:GNU Octave|GNU Octave]]'s random number generator
rand(), which generates uniformly distributed random values in the interval
[0,1). ...
|Date = 2008-04-21
|Author = [[User:Omegatron|Omegatron]]
|Permission = {{PD-ineligible}}
```

`{{PD-ineligible}}` はWikimedia Commonsの定型テンプレートで、「アルゴリズム的に
生成された乱数データであり、著作権保護に必要な創作性を欠く」という著作権者
自身の主張を表す。CCライセンスのような許諾（ライセンス）ではなく、著作権が
そもそも発生していないという法的地位の主張である点に注意。

## 本プロジェクトでの扱い

- 再配布可否：可（著作権保護対象外という主張を前提とする）
- リポジトリ同梱：可（本ディレクトリに同梱済み。`corpus/audio/white_noise.ogg`）
- SHA256: `dde19c997f1f74536f186a8a9f725947363a6af0c36f81089879ee7191460540`
  （`corpus/manifest.json` の `white_noise` エントリと一致することを
  `corpus/fetch_and_verify.py` で検証できる）
