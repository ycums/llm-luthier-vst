"""コーパス実行ランナ（P0-11 #11 / P1-07 #52）。

マニフェスト（`corpus/manifest.json`）の全エントリに対して
「プリセット → レンダ → ターゲットとの指標算出」を実行し、音源1件につき1つのJSONと、
全件を束ねるインデックスJSONを出力する。`docs/01-architecture.md` の
「ハーネス（スクリプト）」が担う「コーパス実行」の実体であり、CIとフィッティング
ループの両方から呼ばれる唯一の入口になる。

## 比較対象（candidate）の作り方

candidate は、レンダラ（`luthier-render`、ヘッドレスCLI）がプリセットから生成した
WAVである。マニフェストのエントリは `preset_path`（プリセットJSONのパス）を持ち、
ランナはそれをレンダラに食わせてWAVを得る。

- レンダラの起動コマンドは `render_cmd`（Sequence[str]）として与える。省略時は
  環境変数 `LUTHIER_RENDER_BIN`（ビルド済みバイナリのパス、docs/adr/0006）から
  解決する。ハーネスはビルド手順自体を知らない（責務の分離、docs/adr/0006）。
- **サンプルレートの扱い（P1-07 #52 で決定）**：レンダのサンプルレートは、ターゲット
  音源のサンプルレートに合わせて `--sample-rate <target_sr>` を渡す。これにより
  candidateのSRは常にターゲットと一致し、`harness/metrics.py` の
  `require_same_sample_rate`（暗黙のリサンプル禁止）を満たす。この方針は
  出力JSONの `calc_conditions.render.sr_mismatch_policy` として明示的に記録する
  （docs/04-metrics.schema.json 参照）。

## 失敗・欠測の扱い（Issue #11 / #52 完了条件）

- target の音声ファイルが手元に存在しない場合、そのエントリを**欠測**として記録し、
  残りのエントリの処理を継続する（同梱不可の音源が取得できない環境でも完走する）。
- **レンダラが利用できない環境**（`render_cmd` 未指定で環境変数も無く、または
  解決したバイナリが存在しない）では、その時点でレンダできない全エントリを
  **欠測**として記録し、完走する（#52 完了条件「レンダラが利用できない環境でも、
  欠測として記録したうえで完走する」。既存の欠測処理と同じ扱い）。
- レンダが非0で終了した場合（プリセットのスキーマ違反等でエンジンが停止した、
  docs/03）は**失敗**として記録し、継続する。指標算出（`compute_metrics_vector`）が
  例外を送出した場合も同様に失敗として記録する。1件の失敗が全体を止めることはない。
- 欠測・失敗のいずれも `docs/04-metrics.schema.json` の欠測表現を使い、全指標が
  欠測の指標ベクトルとして出力する（`harness.metrics.build_missing_metrics_vector`）。
- 終了コードの意味（`harness/cli.py` の `run-corpus` サブコマンド）：
  - `0`：全エントリが欠測を含めて完走した（欠測は正常系として扱う。音源が同梱・取得
    できるかどうかの判定は本ランナのスコープ外）
  - `1`：1件以上のエントリで指標算出が例外により失敗した（一部失敗）
  - `2`：マニフェスト自体が読めない・エントリが1件もない等、実行そのものが成立しな
    かった（実行不能。`ManifestError`）
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from harness.audio_io import read_sample_rate
from harness.metrics import (
    DEFAULT_ATTACK_END_S,
    DEFAULT_BAND_EDGES_HZ,
    DEFAULT_FFT_SIZES,
    build_missing_metrics_vector,
    compute_metrics_vector,
)

#: インデックスJSON自体の構造のバージョン（`docs/04-metrics.schema.json` の
#: 指標ベクトルとは別物。将来インデックスの構造を変える際に上げる）。
INDEX_SCHEMA_VERSION = "1.0.0"

#: レンダラのバイナリパスを受け取る環境変数（docs/adr/0006、テスト類と共通）。
RENDER_BIN_ENV = "LUTHIER_RENDER_BIN"

#: サンプルレート不一致の方針の名前（`calc_conditions.render` に記録する）。
SR_MISMATCH_POLICY = "render_at_target_sample_rate"


class ManifestError(RuntimeError):
    """マニフェストが読めない、または実行できる形式でないときに送出する（実行不能）。"""


def _resolve_local_path(entry: dict, repo_root: Path) -> Path:
    """マニフェストの1エントリから、target音声のローカルパスを解決する。

    `corpus/fetch_and_verify.py` の `local_path_for` と同じ規則（bundled ならその
    `bundled_path`、そうでなければ `corpus/.cache/<id>.<format>`）を使う。規則の
    実体は取得スクリプト側にあり、ここでは同じ規則を踏襲するだけに留める。
    """
    if entry.get("bundled"):
        return repo_root / entry["bundled_path"]
    return repo_root / "corpus" / ".cache" / f"{entry['id']}.{entry['format']}"


def _renderer_bin_from_env() -> Path | None:
    """環境変数 `LUTHIER_RENDER_BIN` からレンダラのバイナリパスを解決する。"""
    raw = os.environ.get(RENDER_BIN_ENV)
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def _resolve_render_cmd(render_cmd: Sequence[str] | None) -> list[str] | None:
    """レンダラの起動コマンドを解決する。利用できない場合は `None`。

    `render_cmd` が与えられていればそれを使う。与えられていない場合は環境変数
    `LUTHIER_RENDER_BIN` から解決する（ビルド済みバイナリのパス）。どちらも
    利用できない（未指定・バイナリ非存在）場合は `None` を返し、呼び出し側が
    全エントリを欠測として処理する。
    """
    if render_cmd:
        return list(render_cmd)
    binary = _renderer_bin_from_env()
    if binary is None:
        return None
    return [str(binary)]


def _build_render_info(
    renderer: str,
    preset_path: str,
    sample_rate_hz: int | None,
) -> dict:
    """`calc_conditions.render` を組み立てる（P1-07 #52）。"""
    return {
        "renderer": renderer,
        "preset_path": preset_path,
        "sample_rate_hz": sample_rate_hz,
        "sr_mismatch_policy": SR_MISMATCH_POLICY,
    }


def _attach_render_info(
    vector: dict,
    renderer: str,
    preset_path: str,
    sample_rate_hz: int | None,
) -> dict:
    """指標ベクトル（欠測含む）にレンダ情報を加えて返す。"""
    vector["calc_conditions"]["render"] = _build_render_info(
        renderer, preset_path, sample_rate_hz
    )
    return vector


class RenderError(RuntimeError):
    """レンダラの起動・実行が失敗したときに送出する。"""


def _render(
    render_cmd: list[str],
    preset_path: Path,
    out_wav: Path,
    sample_rate_hz: int,
) -> None:
    """レンダラを subprocess で起動し、`out_wav` を生成する。

    非0で終了した場合は `RenderError` を送出する。メッセージには絶対パスの出力WAV
    パスを含めない（`out_dir` の一時パスが指標JSONの欠測理由に埋まり、決定論
    「同一入力に対してビット単位で一致」を壊すため）。代わりにプリセットパス
    （マニフェストから相対）と終了コードを載せる。

    レンダラ（`luthier-render`）の標準エラー出力は常にUTF-8（`engine/CMakeLists.txt`
    の`/utf-8`でビルド）。`encoding`を指定しない`text=True`はPythonの既定テキスト
    エンコーディング（`locale.getencoding()`、実行環境のロケール依存）でデコード
    しようとするため、日本語ロケールのWindows（cp932）ではデコードに失敗し、
    `result.stderr`が`None`になって失敗理由が失われる（Issue #122）。
    `encoding="utf-8"`を明示して、ロケールに関わらずレンダラの出力を正しく読む。
    """
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            *render_cmd,
            str(preset_path),
            str(out_wav),
            "--sample-rate",
            str(sample_rate_hz),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip().splitlines()
        detail = stderr_tail[-1] if stderr_tail else "(stderrなし)"
        raise RenderError(
            f"レンダラが非0で終了（exit {result.returncode}, preset={preset_path}）"
            f": {detail}"
        )


def _run_one(
    entry: dict,
    repo_root: Path,
    out_dir: Path,
    render_cmd: list[str] | None,
    metric_kwargs: dict,
) -> tuple[dict, str]:
    """1エントリを処理し、`(指標ベクトル, status)` を返す。

    `status` は `"ok"`（実際に算出できた）/ `"missing"`（レンダ不能・音声がない）/
    `"error"`（レンダまたは算出が例外で失敗した）のいずれか。
    """
    entry_id = entry["id"]
    target_path = _resolve_local_path(entry, repo_root)

    # targetが手元にない → 欠測（取得不能な環境で完走するため、#11 完了条件）。
    if not target_path.exists():
        retrieval = entry.get("retrieval") or "取得手順は corpus/manifest.json を参照"
        reason = f"target音源が手元に存在しない: {target_path}（{retrieval}）"
        vector = build_missing_metrics_vector(entry_id, reason, **metric_kwargs)
        preset_path = repo_root / entry["preset_path"]
        return _attach_render_info(
            vector, "luthier-render", str(entry["preset_path"]), None
        ), "missing"

    # プリセットパスを解決（必須。`preset_path` は P1-07 で追加されたフィールド）。
    preset_path = repo_root / entry["preset_path"]

    # レンダラが利用できない → 全エントリを欠測として記録して完走（#52 完了条件）。
    if render_cmd is None:
        reason = (
            "レンダラが利用できない（LUTHIER_RENDER_BIN 未設定、またはバイナリ非存在）"
        )
        vector = build_missing_metrics_vector(entry_id, reason, **metric_kwargs)
        return _attach_render_info(
            vector, "luthier-render", str(entry["preset_path"]), None
        ), "missing"

    rendered_wav = out_dir / "renders" / f"{entry_id}.wav"

    sample_rate_hz: int | None = None
    try:
        # ターゲットSRに合わせてレンダする（SR差異の方針、#52 決定）。
        sample_rate_hz = read_sample_rate(target_path)
        _render(render_cmd, preset_path, rendered_wav, sample_rate_hz)
        vector = compute_metrics_vector(target_path, rendered_wav, **metric_kwargs)
    except (OSError, RenderError, subprocess.TimeoutExpired) as exc:
        reason = f"レンダ失敗: {type(exc).__name__}: {exc}"
        vector = build_missing_metrics_vector(entry_id, reason, **metric_kwargs)
        # タイムアウトもレンダ失敗。SRは確定していないためNoneを記録する（決定論を保つ）。
        return _attach_render_info(
            vector, "luthier-render", str(entry["preset_path"]), sample_rate_hz
        ), "error"
    except Exception as exc:  # noqa: BLE001 - 1件の失敗で全体を止めないため捕捉して記録する
        reason = f"指標算出が例外で失敗: {type(exc).__name__}: {exc}"
        vector = build_missing_metrics_vector(entry_id, reason, **metric_kwargs)
        return _attach_render_info(
            vector, "luthier-render", str(entry["preset_path"]), sample_rate_hz
        ), "error"

    # target フィールドはファイル名ではなくマニフェストのidに揃える。複数エントリを
    # 束ねるインデックスと突き合わせるときの一意なキーにするため。
    vector["target"] = entry_id
    return _attach_render_info(
        vector, "luthier-render", str(entry["preset_path"]), sample_rate_hz
    ), "ok"


def run_corpus(
    manifest_path: str | Path,
    out_dir: str | Path,
    attack_end_s: float = DEFAULT_ATTACK_END_S,
    fft_sizes: Sequence[int] = DEFAULT_FFT_SIZES,
    band_edges_hz: Sequence[float] = DEFAULT_BAND_EDGES_HZ,
    render_cmd: Sequence[str] | None = None,
) -> dict:
    """マニフェストの全エントリに対して「プリセット→レンダ→指標算出」を実行する。

    `<out_dir>/<id>.json`（音源1件につき1つ）と `<out_dir>/index.json`（全件を束ねる）
    を出力する。レンダ結果のWAVは `<out_dir>/renders/<id>.wav` に置かれる
    （指標JSONの計算のための中間生成物であり、コミット対象にしない）。
    戻り値は `index.json` と同じ内容の辞書。

    マニフェストパスの親の親をリポジトリルートとみなし、`bundled_path` /
    `preset_path` はそこからの相対パスとして解決する（`corpus/manifest.json` の
    既存の配置規則に合わせる）。

    `render_cmd` はレンダラの起動コマンド（Sequence[str]、先頭は実行ファイル）。
    省略時は環境変数 `LUTHIER_RENDER_BIN`（ビルド済みバイナリのパス）から解決する。
    """
    manifest_path = Path(manifest_path)
    out_dir = Path(out_dir)

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"マニフェストを読み込めない: {manifest_path}: {exc}") from exc

    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ManifestError(f"マニフェストにエントリが1件もない: {manifest_path}")

    resolved_render_cmd = _resolve_render_cmd(render_cmd)

    out_dir.mkdir(parents=True, exist_ok=True)
    repo_root = manifest_path.resolve().parent.parent
    metric_kwargs = {
        "attack_end_s": attack_end_s,
        "fft_sizes": fft_sizes,
        "band_edges_hz": band_edges_hz,
    }

    index_entries = []
    for entry in entries:
        entry_id = entry["id"]
        start = time.perf_counter()
        vector, status = _run_one(
            entry, repo_root, out_dir, resolved_render_cmd, metric_kwargs
        )
        elapsed_s = time.perf_counter() - start

        output_filename = f"{entry_id}.json"
        (out_dir / output_filename).write_text(
            json.dumps(vector, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        index_entries.append(
            {
                "id": entry_id,
                "status": status,
                "output_file": output_filename,
                "elapsed_s": elapsed_s,
            }
        )

    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "manifest_path": str(manifest_path),
        "entry_count": len(index_entries),
        "entries": index_entries,
        "summary": {
            "ok": sum(1 for e in index_entries if e["status"] == "ok"),
            "missing": sum(1 for e in index_entries if e["status"] == "missing"),
            "error": sum(1 for e in index_entries if e["status"] == "error"),
        },
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return index


__all__ = [
    "INDEX_SCHEMA_VERSION",
    "RENDER_BIN_ENV",
    "SR_MISMATCH_POLICY",
    "ManifestError",
    "run_corpus",
]