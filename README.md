# PixiText Google Cloud Storage Setup

このアプリで Google Cloud Storage (GCS) を利用する際に必要となる設定情報の一覧です。アプリ本体は `app.config.update(...)` 内で以下の環境変数／設定値を参照します。

- `GCS_PROJECT_ID` (既定値: `PixiText`)
- `GCS_BUCKET_NAME` (既定値: `pixitext-storage`)
- `GCS_UPLOAD_PREFIX` (既定値: `uploads`)
- `GCS_SAVES_PREFIX` (既定値: `saves`)
- `GCS_SERVICE_ACCOUNT_EMAIL` (既定値: `pikusaitekisuto@pixitext-475704.iam.gserviceaccount.com`)
- `GCS_SERVICE_ACCOUNT_KEY` (既定値: `pixitext-475704-6c5d65f6c0cf.json`)
- `GCS_SERVICE_ACCOUNT_JSON` (既定値: 空文字／未設定)

## 必要な準備

1. **サービスアカウントキー (JSON) の配置**
   既定ではリポジトリ直下の `pixitext-475704-6c5d65f6c0cf.json` を参照します。異なる場所に配置する場合は `GCS_SERVICE_ACCOUNT_KEY` でパスを上書きしてください。Render などでファイルを置きづらい場合は、JSON 文字列をそのまま `GCS_SERVICE_ACCOUNT_JSON` に渡すか、Base64 文字列を設定することでインライン資格情報を利用できます。

2. **環境変数の調整 (必要に応じて)**
   本番環境や別プロジェクトで利用する場合は、上記の各値を環境変数で設定することでバケット名やプロジェクト ID を変更できます。`GOOGLE_APPLICATION_CREDENTIALS` が指すパスが有効な場合も利用されます。

3. **権限の確認**  
   サービスアカウントに対象バケット (`pixitext-storage`) への読み書き権限があることを確認してください。権限が不足しているとアップロード／削除が失敗します。

4. **依存パッケージのインストール**  
   `requirements.txt` に記載の `google-cloud-storage` をインストールしてください。

これらが揃っていれば、追加のシークレットは不要で、主にサービスアカウントキー (JSON) があれば十分です。必要に応じて環境変数を設定するだけで動作します。
