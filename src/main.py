# -*- coding: utf-8 -*-
"""
モーニングラジオのパイプライン全体を順番に実行する入口。

【流れ】
1. config.yaml を読み込む
2. RSSからニュースを収集する          (collect_news.py)
3. Claude APIで掛け合い台本を作る      (generate_script.py)
4. edge-ttsで音声合成してMP3にする     (synthesize.py)
5. ポッドキャスト用のfeed.xmlを更新する (build_feed.py)

実行方法:
  ローカルテスト:    python src/main.py            → output/ フォルダに出力
  GitHub Actions:    python src/main.py --output site  → site/ フォルダに出力して公開
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

# このファイルと同じフォルダ(src/)のモジュールを読み込めるようにする
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect_news import collect_news, format_news_for_prompt  # noqa: E402
from generate_script import generate_script  # noqa: E402
from synthesize import synthesize  # noqa: E402
from build_feed import add_episode_and_build_feed  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="ラジオ番組風ニュース音声を生成する")
    parser.add_argument(
        "--output",
        default="output",
        help="出力先フォルダ(既定: output。GitHub Actionsでは site を指定)",
    )
    args = parser.parse_args()

    # .env(APIキー)と config.yaml(設定)を読み込む
    load_dotenv(PROJECT_DIR / ".env")
    config = yaml.safe_load((PROJECT_DIR / "config.yaml").read_text(encoding="utf-8"))

    site_dir = PROJECT_DIR / args.output
    site_dir.mkdir(parents=True, exist_ok=True)

    # 番組アートワーク(assets/cover.png)を公開フォルダにコピーする
    # (Apple Podcastsはアートワークが無い番組のフォローを拒否することがあるため)
    cover_src = PROJECT_DIR / "assets" / "cover.png"
    if cover_src.exists():
        shutil.copy(cover_src, site_dir / "cover.png")

    today = datetime.now(ZoneInfo("Asia/Tokyo"))
    date_str = today.strftime("%Y-%m-%d")

    # --- 1. ニュース収集 -------------------------------------------------
    print("=== 1/4 ニュース収集 ===")
    news_by_genre = collect_news(config["news"])
    total_items = sum(len(items) for items in news_by_genre.values())
    if total_items == 0:
        raise RuntimeError("ニュースが1件も取得できませんでした。ネット接続やRSSのURLを確認してください。")
    print(f"合計 {total_items} 件のニュースを取得しました")

    # --- 2. 台本生成 -----------------------------------------------------
    print("=== 2/4 台本生成(Claude API) ===")
    news_text = format_news_for_prompt(news_by_genre)
    script_lines = generate_script(news_text, config["claude"], config["program"])

    # 台本を確認・デバッグ用に保存しておく(音声を聞かなくても中身が分かるように)
    script_path = site_dir / "episodes" / f"{date_str}_script.json"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        json.dumps(script_lines, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # --- 3. 音声合成 -----------------------------------------------------
    print(f"=== 3/4 音声合成({config['tts'].get('engine', 'edge-tts')}) ===")
    mp3_filename = f"{date_str}.mp3"
    synthesize(
        script_lines,
        config["tts"],
        site_dir / "episodes" / mp3_filename,
        jingle_path=PROJECT_DIR / "assets" / "jingle.mp3",
        pause_path=PROJECT_DIR / "assets" / "pause.mp3",
    )

    # --- 4. ポッドキャストフィード更新 -------------------------------------
    print("=== 4/4 フィード更新 ===")
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]
    title = f"{today.month}月{today.day}日({weekday_ja}) {config['program']['title']}"
    # 番組説明には冒頭のセリフを少しだけ載せる
    description = script_lines[0]["text"][:100] if script_lines else ""
    add_episode_and_build_feed(site_dir, mp3_filename, title, description, config)

    print("\n完了しました。")
    print(f"  音声: {site_dir / 'episodes' / mp3_filename}")
    print(f"  台本: {script_path}")


if __name__ == "__main__":
    main()
