# -*- coding: utf-8 -*-
"""
ポッドキャスト配信用のRSSフィード(feed.xml)を作るモジュール。

【やっていること】
1. エピソード一覧(episodes.json)に今日の分を追加し、古い分を削除する
2. iPhoneのポッドキャストアプリが読める形式のRSS(feed.xml)を書き出す

ポッドキャストの仕組みは単純で、
「エピソードのタイトル・日付・MP3のURLを並べたXMLファイル(RSS)」を
Webで公開し、そのURLをアプリに登録してもらうだけ。
アプリが定期的にRSSを見に来て、新しいエピソードがあれば自動で表示してくれる。
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

# edge-ttsの音声は「48kbps(1秒あたり48キロビット)」のMP3。
# この値からファイルサイズ→再生時間を計算できる(正確な長さの測定ツールが無くても済む)
MP3_BITRATE_BPS = 48000


def resolve_base_url(config: dict) -> str:
    """
    配信URLのベース(https://ユーザー名.github.io/リポジトリ名)を決める。

    GitHub Actions上では GITHUB_REPOSITORY 環境変数(例: "taro/morning-radio")から
    自動で組み立てるので、設定ファイルに書かなくてもよい。
    """
    if config.get("base_url"):
        return config["base_url"].rstrip("/")

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if repository and "/" in repository:
        owner, repo = repository.split("/", 1)
        return f"https://{owner}.github.io/{repo}"

    # ローカルテスト用の仮のURL(実際の配信では使われない)
    return "https://example.github.io/morning-radio"


def _load_episodes(episodes_json: Path) -> list[dict]:
    """エピソード一覧を読み込む。初回はファイルが無いので空リストを返す。"""
    if episodes_json.exists():
        return json.loads(episodes_json.read_text(encoding="utf-8"))
    return []


def _prune_old_episodes(episodes: list[dict], site_dir: Path, retention_days: int) -> list[dict]:
    """保持期間を過ぎたエピソードを一覧から外し、MP3ファイルも削除する。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept = []
    for ep in episodes:
        published = datetime.fromisoformat(ep["published"])
        if published >= cutoff:
            kept.append(ep)
        else:
            old_mp3 = site_dir / "episodes" / ep["filename"]
            if old_mp3.exists():
                old_mp3.unlink()
            print(f"[build_feed] 保持期間切れのため削除: {ep['filename']}")
    return kept


def add_episode_and_build_feed(site_dir: Path, mp3_filename: str, title: str,
                               description: str, config: dict) -> None:
    """
    今日のエピソードを一覧に登録し、feed.xml を作り直す。

    site_dir: 公開フォルダ(この中の episodes/ にMP3、直下に feed.xml と episodes.json を置く)
    """
    program = config.get("program", {})
    base_url = resolve_base_url(config)
    episodes_json = site_dir / "episodes.json"

    episodes = _load_episodes(episodes_json)

    # 同じ日に2回実行した場合は、古い方の登録を消して差し替える
    episodes = [ep for ep in episodes if ep["filename"] != mp3_filename]

    mp3_path = site_dir / "episodes" / mp3_filename
    size_bytes = mp3_path.stat().st_size
    duration_seconds = int(size_bytes * 8 / MP3_BITRATE_BPS)

    episodes.append(
        {
            "filename": mp3_filename,
            "title": title,
            "description": description,
            "published": datetime.now(timezone.utc).isoformat(),
            "size_bytes": size_bytes,
            "duration_seconds": duration_seconds,
        }
    )

    episodes = _prune_old_episodes(episodes, site_dir, config.get("retention_days", 14))
    # 新しい順に並べる(ポッドキャストアプリは先頭を最新として扱うことが多い)
    episodes.sort(key=lambda ep: ep["published"], reverse=True)

    episodes_json.write_text(
        json.dumps(episodes, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _write_feed_xml(site_dir / "feed.xml", episodes, base_url, program)
    _write_index_html(site_dir / "index.html", episodes, base_url, program)
    print(f"[build_feed] feed.xml を更新しました(エピソード数: {len(episodes)})")
    print(f"[build_feed] 購読用URL: {base_url}/feed.xml")


def _format_rfc2822(iso_datetime: str) -> str:
    """RSSで使う日付形式(例: Tue, 14 Jul 2026 05:00:00 +0900)に変換する。"""
    dt = datetime.fromisoformat(iso_datetime).astimezone(ZoneInfo("Asia/Tokyo"))
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")


def _write_feed_xml(feed_path: Path, episodes: list[dict], base_url: str, program: dict) -> None:
    """RSS 2.0形式(+ポッドキャスト用のitunesタグ)のXMLを書き出す。"""
    title = escape(program.get("title", "モーニングラジオ"))
    description = escape(program.get("description", ""))
    author = escape(program.get("author", "morning-radio bot"))
    language = program.get("language", "ja")

    items = []
    for ep in episodes:
        mp3_url = f"{base_url}/episodes/{ep['filename']}"
        # 再生時間を「時:分:秒」の形にする
        h, remainder = divmod(ep["duration_seconds"], 3600)
        m, s = divmod(remainder, 60)
        duration = f"{h}:{m:02d}:{s:02d}"

        items.append(f"""    <item>
      <title>{escape(ep['title'])}</title>
      <description>{escape(ep['description'])}</description>
      <pubDate>{_format_rfc2822(ep['published'])}</pubDate>
      <enclosure url="{mp3_url}" length="{ep['size_bytes']}" type="audio/mpeg"/>
      <guid isPermaLink="false">{escape(ep['filename'])}</guid>
      <itunes:duration>{duration}</itunes:duration>
    </item>""")

    # itunes:image(番組アートワーク)とitunes:categoryは、
    # Apple Podcastsが番組を受け付けるためのほぼ必須項目
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{title}</title>
    <description>{description}</description>
    <link>{base_url}</link>
    <language>{language}</language>
    <atom:link href="{base_url}/feed.xml" rel="self" type="application/rss+xml"/>
    <itunes:author>{author}</itunes:author>
    <itunes:explicit>false</itunes:explicit>
    <itunes:image href="{base_url}/cover.png"/>
    <itunes:category text="News">
      <itunes:category text="Business News"/>
    </itunes:category>
    <image>
      <url>{base_url}/cover.png</url>
      <title>{title}</title>
      <link>{base_url}</link>
    </image>
{chr(10).join(items)}
  </channel>
</rss>
"""
    feed_path.write_text(xml, encoding="utf-8")


def _write_index_html(index_path: Path, episodes: list[dict], base_url: str, program: dict) -> None:
    """
    購読用の案内ページ(index.html)を作る。

    iPhoneのSafariでこのページを開けば、URLを手で打たなくても
    ボタンのタップだけでポッドキャストアプリに登録できる。
    ブラウザ上でそのまま再生できるプレーヤーも付けている。
    """
    title = escape(program.get("title", "モーニングラジオ"))
    feed_url = f"{base_url}/feed.xml"
    # 「podcast://」で始まるリンクは、iPhoneでApple Podcastsアプリを直接開く
    podcast_link = feed_url.replace("https://", "podcast://")

    episode_items = []
    for ep in episodes:
        mp3_url = f"{base_url}/episodes/{ep['filename']}"
        minutes = ep["duration_seconds"] // 60
        episode_items.append(f"""      <li>
        <p><strong>{escape(ep['title'])}</strong>(約{minutes}分)</p>
        <audio controls preload="none" src="{mp3_url}"></audio>
      </li>""")

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 16px; background: #1a1a2e; color: #eee; }}
  img.cover {{ width: 180px; border-radius: 16px; display: block; margin: 16px auto; }}
  h1 {{ text-align: center; font-size: 1.4rem; }}
  .btn {{ display: block; text-align: center; background: #e97451; color: #fff; text-decoration: none;
         padding: 14px; border-radius: 10px; margin: 10px 0; font-weight: bold; border: none;
         width: 100%; font-size: 1rem; cursor: pointer; }}
  .note {{ color: #aaa; font-size: 0.85rem; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ background: #26264a; border-radius: 10px; padding: 12px; margin: 10px 0; }}
  audio {{ width: 100%; }}
  code {{ background: #333; padding: 2px 6px; border-radius: 4px; word-break: break-all; }}
</style>
</head>
<body>
  <img class="cover" src="cover.png" alt="番組アートワーク">
  <h1>{title}</h1>

  <a class="btn" href="{podcast_link}">Apple Podcastsでフォローする</a>
  <button class="btn" onclick="navigator.clipboard.writeText('{feed_url}').then(()=>alert('コピーしました'))">
    フィードURLをコピーする
  </button>
  <p class="note">上のボタンで開けない場合は、コピーしたURLをポッドキャストアプリの
  「URLで番組をフォロー」に貼り付けてください。<br>
  フィードURL: <code>{feed_url}</code></p>

  <h2>エピソード</h2>
  <ul>
{chr(10).join(episode_items)}
  </ul>
</body>
</html>
"""
    index_path.write_text(html, encoding="utf-8")
