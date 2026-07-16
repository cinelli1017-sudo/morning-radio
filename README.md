# モーニング調達ラジオ 📻

毎朝、前日のニュースを2人のパーソナリティが掛け合いで紹介する「自分専用ラジオ番組」を
自動生成し、ポッドキャストとしてiPhoneに配信するアプリです。

## 仕組み

```
[GitHub Actions] 毎朝5時ごろ(日本時間)に自動実行
   1. RSSからニュース収集(NHK / MONOist / 日経クロステック / ITmedia)
   2. Claude APIで2人の掛け合いラジオ台本を生成
   3. edge-tts(無料)で音声合成 → 1本のMP3に結合
   4. ポッドキャスト用RSS(feed.xml)を更新
   5. GitHub Pagesで公開
        ↓
[iPhone] ポッドキャストアプリが自動で新エピソードを受信
```

PCの電源が入っていなくても、クラウド(GitHub)上で毎朝自動生成されます。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `config.yaml` | 番組名・声・ニュースの取得元などの設定 |
| `src/main.py` | パイプライン全体の入口 |
| `src/collect_news.py` | RSSからニュース収集 |
| `src/generate_script.py` | Claude APIで台本生成 |
| `src/synthesize.py` | edge-ttsで音声合成 |
| `src/build_feed.py` | ポッドキャストRSS生成 |
| `.github/workflows/daily.yml` | 毎朝の自動実行設定 |

## セットアップ手順

### 1. GitHubリポジトリの準備(初回のみ)

1. このフォルダをGitHubの**公開リポジトリ**として作成しpushする
   (無料プランのGitHub Pagesは公開リポジトリのみ対応。
   ※音声ファイルはURLを知っていれば誰でも聴ける状態になります)
2. リポジトリの `Settings → Secrets and variables → Actions → New repository secret` で
   - Name: `CLAUDE_API_KEY`
   - Secret: Claude APIのキー(`sk-ant-...`)
3. `Actions` タブ → `daily-radio` → `Run workflow` で一度手動実行する
   (これで配信用の `gh-pages` ブランチが作られる)
4. `Settings → Pages` で Source を `Deploy from a branch`、
   Branch を `gh-pages` / `(root)` にして保存

### 2. iPhoneで購読する

1. iPhoneの「ポッドキャスト」アプリ(Apple標準)を開く
2. 「ライブラリ」→ 右上の「…」→「URLで番組をフォロー」
3. 次のURLを入力する:
   `https://<GitHubユーザー名>.github.io/morning-radio/feed.xml`
4. 番組ページで「フォロー」し、設定から自動ダウンロードをONにすると、
   毎朝新しいエピソードが自動で入ってくる

### 3. ローカルでのテスト実行(任意)

```
# 初回のみ: 仮想環境を作ってライブラリをインストール
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt

# .env.example をコピーして .env を作り、APIキーを書き込む

# 実行(output/ フォルダにMP3と台本ができる)
venv\Scripts\python.exe src\main.py
```

## カスタマイズ(config.yaml)

- **番組の長さ**: `program.target_minutes`(分)
- **声の変更**: `voices.male` / `voices.female`
  (使える声の一覧は `venv\Scripts\edge-tts.exe --list-voices` で確認)
- **ニュースの取得元**: `news.feeds` にRSSのURLを追加・削除
- **配信時刻**: `.github/workflows/daily.yml` の `cron`(UTC表記。日本時間−9時間)
  ※GitHub Actionsの定時実行は混雑時に数時間遅れることがあるため、
  目標時刻より3〜4時間早めに設定しておくのがコツ(現在は深夜1:23開始→朝5時までに配信)
- **エピソードの保存日数**: `retention_days`

## 費用の目安

| 項目 | 費用 |
|---|---|
| GitHub Actions / Pages | 無料枠内(1日1回・数分の実行) |
| edge-tts(音声合成) | 無料 |
| Claude API(台本生成) | 1回あたり数円程度(claude-sonnet-5使用) |
