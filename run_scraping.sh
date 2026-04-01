#!/bin/bash

# 引数が3つ指定されているか確認
if [ $# -ne 3 ]; then
    echo "使用法: $0 <year> <department_id> <department_code>"
    echo "例: $0 2022 11 j"
    exit 1
fi

YEAR=$1
DEPT_ID=$2
DEPT_CODE=$3

# 出力ディレクトリ名は list_json_<YEAR> とする (例: list_json_2022)
# ユーザーの例にある list_json_22 に合わせたい場合はここを調整してください
OUT_DIR="${YEAR}"
mkdir -p "$OUT_DIR"

# 学科コードを大文字にしてファイル名にする (例: j -> J.json)
DEPT_UPPER=$(echo "$DEPT_CODE" | tr '[:lower:]' '[:upper:]')
OUT_FILE="${OUT_DIR}/${DEPT_UPPER}.json"

echo "========================================"
echo "開始: 年=$YEAR, 学科ID=$DEPT_ID, 学科コード=$DEPT_CODE"
echo "出力先: $OUT_FILE"
echo "========================================"

# 1. スクレイピング実行
echo ">> 00_credit_scraping.py を実行中..."
python3 00_credit_scraping.py --year="$YEAR" --department_id="$DEPT_ID" --department="$DEPT_CODE"

if [ $? -ne 0 ]; then
    echo "エラー: スクレイピングに失敗しました。"
    exit 1
fi

# 2. JSON変換実行
echo ">> 01_convert_to_json.py を実行中..."
python3 01_convert_to_json.py --department="$DEPT_CODE" --output="$OUT_FILE" --year="$YEAR"

if [ $? -ne 0 ]; then
    echo "エラー: JSON変換に失敗しました。"
    exit 1
fi

echo "========================================"
echo "完了しました！"
echo "生成ファイル: $OUT_FILE"
echo "========================================"
