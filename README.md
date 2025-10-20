# PixiText Google Cloud Storage Setup

このアプリで Google Cloud Storage (GCS) を利用する際に必要となる設定情報の一覧です。アプリ本体は `app.config.update(...)` 内で以下の環境変数／設定値を参照します。

- `GCS_PROJECT_ID` (既定値: `PixiText`)
- `GCS_BUCKET_NAME` (既定値: `pixitext-storage`)
- `GCS_UPLOAD_PREFIX` (既定値: `uploads`)
- `GCS_SAVES_PREFIX` (既定値: `saves`)
- `GCS_SERVICE_ACCOUNT_EMAIL` (既定値: `pikusaitekisuto@pixitext-475704.iam.gserviceaccount.com`)
- `GCS_SERVICE_ACCOUNT_KEY` (既定値: 空文字／未設定)
- `GCS_SERVICE_ACCOUNT_JSON` (既定値: 空文字／未設定)

## 必要な準備

1. **サービスアカウントキー (JSON) の配置**
   既定では特定のファイルパスは設定されていません。ローカル開発でファイルを使用する場合は、リポジトリ外の安全な場所に `pixitext-475704-6c5d65f6c0cf.json`（または任意のファイル名）を配置し、そのパスを `GCS_SERVICE_ACCOUNT_KEY` で指定してください。Render などでファイルを置きづらい場合は、JSON 文字列をそのまま `GCS_SERVICE_ACCOUNT_JSON` に渡すか、Base64 文字列を設定することでインライン資格情報を利用できます。

   > **注意:** サービスアカウントキーは Git リポジトリにコミットしないでください。`.gitignore` に登録して秘匿情報として扱うか、環境変数経由で注入してください。

2. **環境変数の調整 (必要に応じて)**
   本番環境や別プロジェクトで利用する場合は、上記の各値を環境変数で設定することでバケット名やプロジェクト ID を変更できます。`GOOGLE_APPLICATION_CREDENTIALS` が指すパスが有効な場合も利用されます。

3. **権限の確認**  
   サービスアカウントに対象バケット (`pixitext-storage`) への読み書き権限があることを確認してください。権限が不足しているとアップロード／削除が失敗します。

4. **依存パッケージのインストール**  
   `requirements.txt` に記載の `google-cloud-storage` をインストールしてください。

これらが揃っていれば、追加のシークレットは不要で、主にサービスアカウントキー (JSON) があれば十分です。必要に応じて環境変数を設定するだけで動作します。

## 誤って鍵を公開してしまった場合の対処

1. Google Cloud Console の [サービスアカウント](https://console.cloud.google.com/iam-admin/serviceaccounts) から該当キーを **無効化または削除** し、新しい鍵を再発行します。
2. リポジトリ履歴から公開された JSON を削除し、`.gitignore` にサービスアカウントキーのファイル名を追加します。
3. 新しい鍵を安全な保管場所に配置し、`GCS_SERVICE_ACCOUNT_KEY` か `GCS_SERVICE_ACCOUNT_JSON` で参照するよう設定を更新します。
4. Render などのホスティング環境にデプロイしている場合は、環境変数の値も新しい鍵で上書きしてください。
