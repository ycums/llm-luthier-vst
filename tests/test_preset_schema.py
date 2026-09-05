"""`docs/03-preset-format.schema.json`（P1-04 #49）のテスト。

このテストはエンジン実装（P1-06）ではなく、スキーマ定義そのものが完了条件（Issue #49）を
満たしていることを検証する対象である。検証手段として `jsonschema` を使うが、これは
「エンジンが未知フィールドで停止する」という実行時挙動を実装するものではない
（それはP1-06の範囲）。ここではスキーマが妥当な最小プリセットと不正なプリセットの
両方を意図どおり判定できることだけを確認する。

Issue #49 の完了条件を検証する：
- `format_version` / `engine_spec_version` が必須
- `Timeseries` 型（`unit` / `interp` / `points`）が定義され、点が1個の場合を許容する
- 4層（transient / harmonic / formant / modulation）が定義されている
- 未知のフィールドが不許可（`additionalProperties: false` 相当）
- modulation.routes の `from` / `to` が定義され、`to` はドット記法のパス文字列
- formant.bands のバンド数がv0の4として定義されている
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "03-preset-format.schema.json"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _constant_timeseries(unit: str, value: float) -> dict:
    """点が1個のTimeseries（定数として扱う、docs/03）。"""
    return {"unit": unit, "interp": "linear", "points": [{"t": 0.0, "v": value}]}


def _minimal_valid_preset() -> dict:
    """妥当な最小プリセット。4層すべてを有効にし、各配列はプリセットとして意味を持つ最小要素数。"""
    return {
        "format_version": "0.1.0",
        "engine_spec_version": "0.1.0",
        "layers": {
            "transient": {
                "enabled": True,
                "gain": -6.0,
                "duration": 20.0,
                "seed": 0,
                "spectral_envelope": [_constant_timeseries("db", 0.0)],
            },
            "harmonic": {
                "enabled": True,
                "f0": _constant_timeseries("hz", 220.0),
                "partial_amplitudes": [_constant_timeseries("linear", 1.0)],
                "inharmonicity": 0.0,
            },
            "formant": {
                "enabled": True,
                "bands": [
                    {
                        "freq": _constant_timeseries("hz", 500.0),
                        "q": _constant_timeseries("linear", 5.0),
                        "gain": _constant_timeseries("db", 0.0),
                    }
                    for _ in range(4)
                ],
            },
        },
    }


@pytest.fixture
def schema() -> dict:
    return _load_schema()


@pytest.fixture
def minimal_valid_preset() -> dict:
    return _minimal_valid_preset()


def test_schema_is_itself_valid_as_a_json_schema(schema: dict) -> None:
    jsonschema.Draft202012Validator.check_schema(schema)


def test_minimal_valid_preset_passes(schema: dict, minimal_valid_preset: dict) -> None:
    jsonschema.validate(instance=minimal_valid_preset, schema=schema)


def test_preset_with_modulation_passes(schema: dict, minimal_valid_preset: dict) -> None:
    preset = copy.deepcopy(minimal_valid_preset)
    preset["modulation"] = {
        "sources": [
            {"id": "env1", "type": "envelope", "points": [{"t": 0.0, "v": 0.0}, {"t": 0.1, "v": 1.0}]},
            {"id": "lfo1", "type": "lfo", "waveform": "sine", "rate": 5.0, "depth": 0.3, "sync": False},
        ],
        "routes": [
            {"from": "env1", "to": "formant.bands[0].freq", "depth": 0.4, "curve": "linear"},
            {"from": "lfo1", "to": "harmonic.f0", "depth": 0.1, "curve": "linear"},
        ],
    }

    jsonschema.validate(instance=preset, schema=schema)


@pytest.mark.parametrize(
    "mutate_description,mutate",
    [
        (
            "未知の最上位フィールド",
            lambda p: p.__setitem__("unrecognized_field", 1),
        ),
        (
            "format_version 欠落",
            lambda p: p.pop("format_version"),
        ),
        (
            "engine_spec_version 欠落",
            lambda p: p.pop("engine_spec_version"),
        ),
        (
            "layers 欠落",
            lambda p: p.pop("layers"),
        ),
        (
            "harmonic層に未知フィールド",
            lambda p: p["layers"]["harmonic"].__setitem__("unknown_param", 1.0),
        ),
        (
            "Timeseriesに未知フィールド",
            lambda p: p["layers"]["harmonic"]["f0"].__setitem__("unknown", 1),
        ),
        (
            "interpが許容値以外",
            lambda p: p["layers"]["harmonic"]["f0"].__setitem__("interp", "cubic"),
        ),
        (
            "formant.bandsが3バンド（v0は4固定）",
            lambda p: p["layers"]["formant"].__setitem__(
                "bands", p["layers"]["formant"]["bands"][:3]
            ),
        ),
        (
            "formant.bandsが5バンド（v0は4固定）",
            lambda p: p["layers"]["formant"]["bands"].append(p["layers"]["formant"]["bands"][0]),
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_invalid_presets_are_rejected(
    schema: dict,
    minimal_valid_preset: dict,
    mutate_description: str,
    mutate: Any,
) -> None:
    preset = copy.deepcopy(minimal_valid_preset)
    mutate(preset)

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=preset, schema=schema)


def test_modulation_routes_to_rejects_non_string(schema: dict, minimal_valid_preset: dict) -> None:
    preset = copy.deepcopy(minimal_valid_preset)
    preset["modulation"] = {
        "sources": [{"id": "env1", "type": "envelope", "points": [{"t": 0.0, "v": 0.0}]}],
        "routes": [{"from": "env1", "to": 123, "depth": 0.4, "curve": "linear"}],
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=preset, schema=schema)


def test_modulation_sources_capped_at_four(schema: dict, minimal_valid_preset: dict) -> None:
    preset = copy.deepcopy(minimal_valid_preset)
    preset["modulation"] = {
        "sources": [
            {"id": f"env{i}", "type": "envelope", "points": [{"t": 0.0, "v": 0.0}]} for i in range(5)
        ],
        "routes": [],
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=preset, schema=schema)


def test_timeseries_with_single_point_is_treated_as_constant(schema: dict) -> None:
    """docs/03『点が1個だけの場合は定数として扱う。スカラー専用の型を別に作らない』の検証。"""
    ts = _constant_timeseries("hz", 440.0)
    assert len(ts["points"]) == 1

    preset = _minimal_valid_preset()
    preset["layers"]["harmonic"]["f0"] = ts

    jsonschema.validate(instance=preset, schema=schema)


def test_partial_amplitudes_count_is_unconstrained(schema: dict, minimal_valid_preset: dict) -> None:
    """docs/03 未確定『partial_amplitudes の倍音数上限』：スキーマは上限を課さない。"""
    preset = copy.deepcopy(minimal_valid_preset)
    preset["layers"]["harmonic"]["partial_amplitudes"] = [
        _constant_timeseries("linear", 1.0) for _ in range(64)
    ]

    jsonschema.validate(instance=preset, schema=schema)


def test_spectral_envelope_band_count_is_unconstrained(schema: dict, minimal_valid_preset: dict) -> None:
    """docs/03 未確定『spectral_envelope の帯域分割方式・帯域数』：スキーマは上限を課さない。"""
    preset = copy.deepcopy(minimal_valid_preset)
    preset["layers"]["transient"]["spectral_envelope"] = [
        _constant_timeseries("db", 0.0) for _ in range(32)
    ]

    jsonschema.validate(instance=preset, schema=schema)
