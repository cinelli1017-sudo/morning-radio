# -*- coding: utf-8 -*-
"""
台本を音声(MP3)にするモジュール。

【やっていること】
1. 台本のセリフ1つ1つを、edge-tts(Microsoft Edgeの読み上げ音声・無料)でMP3片にする
   - male / female で別の声を使い、掛け合いに聞こえるようにする
2. できたMP3片を順番につなげて、1本のMP3ファイルにする

ニュースの切り替わり(台本の new_topic が true のセリフ)の直前には、
ジングル(チャイム音)を挟んで境目が分かるようにしている。

つなげる処理はffmpegを使う。PCにffmpegが無い場合でも、
imageio-ffmpegパッケージに同梱されたffmpegを使うため追加の準備は不要。
"""

import asyncio
from pathlib import Path
import shutil
import subprocess
import tempfile

import edge_tts


def _find_ffmpeg() -> str | None:
    """ffmpegの実行ファイルを探す。PC本体→imageio-ffmpeg同梱版の順に確認する。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


# 音声が「1文字あたり最低このバイト数」を下回ったら、通信が途中で切れたと判断する
# (48kbpsのMP3では1文字=約0.2秒=約1200バイトが目安。その1/4を下限にしている)
MIN_BYTES_PER_CHAR = 300


async def _synthesize_one(text: str, voice: str, output_path: Path) -> None:
    """
    1つのセリフを1つのMP3ファイルにする。

    edge-ttsは無料サービスのため、まれに通信が途中で切れて短い音声しか
    保存されないことがある。ファイルサイズで異常を検知し、最大3回やり直す。
    """
    min_bytes = len(text) * MIN_BYTES_PER_CHAR
    last_error = None
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(output_path))
            if output_path.stat().st_size >= min_bytes:
                return
            last_error = RuntimeError(
                f"音声が短すぎます({output_path.stat().st_size}バイト < 期待{min_bytes}バイト)"
            )
        except Exception as error:
            last_error = error
        # 少し待ってからやり直す(待ち時間は回数ごとに延ばす)
        await asyncio.sleep(2 * (attempt + 1))
        print(f"[synthesize] リトライ {attempt + 1}/3: 「{text[:20]}...」")
    raise RuntimeError(f"音声合成に3回失敗しました: {last_error}")


async def _synthesize_all(script_lines: list[dict], voices: dict, part_dir: Path) -> list[Path]:
    """台本の全セリフを順番にMP3片にして、ファイルパスのリストを返す。"""
    part_paths = []
    for i, line in enumerate(script_lines):
        voice = voices.get(line["speaker"], voices["male"])
        part_path = part_dir / f"part_{i:04d}.mp3"
        await _synthesize_one(line["text"], voice, part_path)
        part_paths.append(part_path)
        # 進捗が分かるように10セリフごとに表示する
        if (i + 1) % 10 == 0 or (i + 1) == len(script_lines):
            print(f"[synthesize] 音声合成中... {i + 1}/{len(script_lines)}")
    return part_paths


def _concat_with_ffmpeg(ffmpeg: str, part_paths: list[Path], output_path: Path) -> None:
    """ffmpegでMP3片を1本に結合する(再生時間情報も正しく作られる)。"""
    # ffmpegのconcat機能は「結合するファイルの一覧」を書いたテキストファイルを渡す方式
    list_file = output_path.parent / "concat_list.txt"
    lines = [f"file '{p.as_posix()}'" for p in part_paths]
    list_file.write_text("\n".join(lines), encoding="utf-8")

    # 「-c copy(無変換で繋ぐ)」はffmpegのバージョンによって、由来の違うMP3が
    # 混ざると音声を取りこぼすことがあった(GitHub Actions上で発生)。
    # そのため一度デコードして繋ぎ直す方式にしている(10分の音声でも数秒で終わる)
    result = subprocess.run(
        [
            ffmpeg,
            "-y",                     # 出力先が既にあっても上書きする
            "-f", "concat",           # 「ファイル一覧を結合するモード」を指定
            "-safe", "0",             # 一覧内の絶対パスを許可する
            "-i", str(list_file),
            "-c:a", "libmp3lame",     # MP3として再エンコード
            "-b:a", "48k",
            "-ar", "24000",
            "-ac", "1",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    list_file.unlink()

    # 結合結果の検証: 出力が入力合計より大幅に小さければ、どこかで音声が
    # 欠落しているので、壊れた音声を配信しないようエラーで止める
    total_input = sum(p.stat().st_size for p in part_paths)
    output_size = output_path.stat().st_size
    if output_size < total_input * 0.6:
        print("[synthesize] ffmpegの警告出力(末尾):")
        print("\n".join(result.stderr.splitlines()[-15:]))
        raise RuntimeError(
            f"結合後の音声が短すぎます(入力合計{total_input}バイト → 出力{output_size}バイト)。"
        )


def _concat_binary(part_paths: list[Path], output_path: Path) -> None:
    """ffmpegが無い環境向け: MP3ファイルを単純にバイナリ連結する。"""
    with open(output_path, "wb") as out:
        for p in part_paths:
            out.write(p.read_bytes())


def synthesize(script_lines: list[dict], voices: dict, output_path: Path,
               jingle_path: Path | None = None,
               pause_path: Path | None = None) -> None:
    """
    台本全体を1本のMP3ファイル(output_path)にする。

    script_lines: [{"speaker": "male", "text": "...", "new_topic": false}, ...]
    voices: {"male": "...", "female": "..."}
    jingle_path: ニュースの切り替わりに挟むチャイム音(Noneなら挟まない)
    pause_path: セリフとセリフの間に挟む短い無音(会話の自然な間を作る)
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if jingle_path and not jingle_path.exists():
        jingle_path = None
    if pause_path and not pause_path.exists():
        pause_path = None

    # MP3片は一時フォルダに作り、結合が終わったら自動で消す
    with tempfile.TemporaryDirectory() as tmp_dir:
        part_dir = Path(tmp_dir)
        speech_parts = asyncio.run(_synthesize_all(script_lines, voices, part_dir))

        if not speech_parts:
            raise RuntimeError("台本が空のため、音声を作れませんでした。")

        # 再生順にファイルを並べる:
        #   話題の切り替わり(new_topic=true)の前 → ジングル
        #   それ以外のセリフの間               → 短い無音(会話の間)
        part_paths = []
        jingle_count = 0
        for i, (line, speech) in enumerate(zip(script_lines, speech_parts)):
            if i > 0:
                if line.get("new_topic") and jingle_path:
                    part_paths.append(jingle_path)
                    jingle_count += 1
                elif pause_path:
                    part_paths.append(pause_path)
            part_paths.append(speech)
        if jingle_path:
            print(f"[synthesize] ジングルを {jingle_count} 箇所に挿入しました")

        ffmpeg = _find_ffmpeg()
        if ffmpeg:
            _concat_with_ffmpeg(ffmpeg, part_paths, output_path)
            print(f"[synthesize] ffmpegで結合しました: {output_path}")
        else:
            _concat_binary(part_paths, output_path)
            print(f"[synthesize] ffmpegが無いため単純連結しました: {output_path}")

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"[synthesize] 完成: {output_path.name} ({size_mb:.1f} MB)")
