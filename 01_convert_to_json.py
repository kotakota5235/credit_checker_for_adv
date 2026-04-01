#!/usr/bin/env python3
"""CSV（Kosen シラバスのエクスポート）を読み、所定の JSON スキーマに合わせた JSON を出力するスクリプト。

基本的な流れ:
 1. CSV を複数のエンコーディング候補で読み込む
 2. pandas DataFrame に変換して余分なヘッダー列を削除
 3. 各行をパースしてスキーマに合う dict を作成
 4. JSON スキーマで検証（エラーは標準エラーへ最初の数件を出力）
 5. JSON ファイルに書き出す

使い方の例:
  python convert_to_json.py --input "output copy.txt" --schema credit_scheme.json --output converted.json --department j --year 2025
"""

from typing import Iterable, List, Optional
import argparse
import csv
import io
import json
import re
import sys
import uuid

import pandas as pd
from jsonschema import Draft7Validator


# --- 設定 ---
ENCODING_CANDIDATES: List[str] = ["utf-8-sig", "utf-8", "cp932", "shift_jis"]
# CSV の「種別」列に対する内部表現
TYPE_MAP = {"一般": "general", "専門": "major"}
# CSV の「カテゴリ」列から変換するマップ
CATEGORY_MAP = {
    "必修": "required",
    "選択": "elective",
    "必修選択": "required_elective",
}
# 全学共通（一般科目）として扱う学科コード一覧
#ALL_UNDERGRAD_DEPARTMENTS = ["m", "e", "d", "j", "c"]
ALL_UNDERGRAD_DEPARTMENTS = ["me", "dj", "cc"]


def load_rows(path: str) -> List[List[str]]:
    """ファイルを読み込んで CSV の行リストを返す。

    複数のエンコーディングで試行し、最後は UTF-8 置換モードで読み込みます。
    ファイルが見つからない場合は SystemExit で終了します。
    """

    for encoding in ENCODING_CANDIDATES:
        try:
            with open(path, newline="", encoding=encoding) as fh:
                reader = csv.reader(fh)
                return [row for row in reader]
        except UnicodeDecodeError:
            # このエンコーディングでは読めなかった -> 次を試す
            continue
        except FileNotFoundError as exc:
            raise SystemExit(f"入力ファイルが見つかりません: {path}") from exc

    # 最後の手段: バイナリで読み、UTF-8 に置換付きでデコードしてから CSV パース
    with open(path, "rb") as raw_fh:
        blob = raw_fh.read()
    text = blob.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    return [row for row in reader]


def rows_to_dataframe(rows: List[List[str]]) -> pd.DataFrame:
    """CSV の行リストを pandas.DataFrame に変換して不要なヘッダーを削除する。

    - 最初の列が行番号になっている場合は削除
    - 先頭5行はヘッダ説明（空行・複数行ヘッダ）なので削除
    - 空文字列や None を pandas.NA に置き換える
    """

    if not rows:
        raise SystemExit("入力 CSV が空のようです。")

    frame = pd.DataFrame(rows)

    # 多くのエクスポートで最初の列が行番号になっている -> 削除
    if 0 in frame.columns:
        frame = frame.drop(columns=0)

    # 最初の 5 行は説明行なので削除（存在しない場合は無視）
    frame = frame.drop(index=[0, 1, 2, 3, 4], errors="ignore").reset_index(drop=True)

    # 空文字や None は pandas.NA にする（後続の判定が楽になる）
    frame = frame.map(lambda v: v if v not in ("", None) else pd.NA)

    return frame


def clean_name(raw: Optional[str]) -> str:
    """科目名などで同じ名前が空白で連結されている場合、先頭のまとまりだけ返す。

    例: "英語  英語" -> "英語"
    """

    if raw is None or pd.isna(raw):
        return ""
    text = str(raw).strip()
    parts = re.split(r"\s{2,}", text)  # 連続する空白で分割
    return parts[0].strip() if parts else text


def detect_grades(row: pd.Series) -> List[int]:
    """行データからどの学年で開講されているかを推測して [1..5] のリストで返す。

    実装のポイント:
    - CSV は学年ごとに4つ（四半期など）の列がある想定で、最初の学年列は列インデックス 6。
    - その列群に何らかの値が入っていればその学年に開講していると判断する。
    - 値がある学年が見つからなければデフォルトで [1] を返す。
    """

    grades: List[int] = []
    start_col = 6
    cols_per_grade = 4

# 本科：range(1, 6) 専攻科：range(1, 3)
    for year in range(1, 3):
        cols = [start_col + (year - 1) * cols_per_grade + offset for offset in range(cols_per_grade)]
        occupied = False
        for col in cols:
            if col not in row.index:
                continue
            value = row.iloc[col]
            if pd.isna(value):
                continue
            text = str(value).strip()
            if not text:
                continue
            # 数字や文字トークン（例: 集中講義）でも「存在する」と判断
            occupied = True
            break
        if occupied:
            grades.append(year)

    return grades or [1]


def map_category(raw_category: str, enrollment_note: Optional[str]) -> str:
    """CSV のカテゴリ記述から内部カテゴリ文字列に変換する。

    - "特別学修" が含まれる場合は special_study
    - CATEGORY_MAP に定義があればそれを使う
    - "留学生" があれば必修扱い（実運用ルール）
    - それ以外は elective
    """

    raw = (raw_category or "").strip()
    note = (enrollment_note or "").strip()

    if "特別学修" in raw or "特別学修" in note:
        return "special_study"
    if raw in CATEGORY_MAP:
        return CATEGORY_MAP[raw]
    if "留学生" in raw:
        return "required"
    return "elective"


def determine_international_flag(
    raw_category: str,
    name: str,
    subject_type: str,
    category: str,
    grades: List[int],
) -> Optional[bool]:
    """留学生向けかどうかを判定する簡易ルール。

    - カテゴリや科目名に "留学生" が含まれる場合は True
    - 2 年までの科目は留学生向けではない想定なので False
    - 3 年以上が対象かつ必修なら None
    - 判定できない場合は None
    """

    if "留学生" in raw_category or "日本語" in name:
        return True
    elif "ドイツ語" in name or "中国語" in name or "現代社会" in name:
        return False
    elif grades and max(grades) <= 2:
        return False
    elif category == "required" and any(grade >= 3 for grade in grades):
        return None
    return None


def choose_departments(subject_type: str, subject_code: str, default_department: str) -> List[str]:
    """科目の所属学科を決める。

    - 一般科目は全学科に属する（ALL_UNDERGRAD_DEPARTMENTS）
    - 専門科目で科目コードが j で始まる場合は ['j']
    - それ以外はデフォルトの学科コードを返す
    """

    if subject_type == "general":
        return ALL_UNDERGRAD_DEPARTMENTS
    cleaned_code = (subject_code or "").strip().lower()
    if cleaned_code.startswith("j"):
        return ["j"]
    return [default_department]


def build_item(row: pd.Series, curriculum_year: int, default_department: str) -> Optional[dict]:
    """DataFrame の 1 行から出力用の dict を作る。

    戻り値が None の場合、その行は無視（必須列が欠けている等）。
    """

    # 必須列のインデックス（元 CSV のレイアウトに依存）
    required_columns = [0, 1, 2, 5]
    for col in required_columns:
        if col >= len(row) or pd.isna(row.iloc[col]):
            # 必須列がない行は無視
            return None

    raw_type = str(row.iloc[0]).strip() if not pd.isna(row.iloc[0]) else ""
    raw_category = str(row.iloc[1]).strip() if not pd.isna(row.iloc[1]) else ""
    name = clean_name(row.iloc[2])
    subject_code = str(row.iloc[3]).strip() if len(row) > 3 and not pd.isna(row.iloc[3]) else ""

    subject_type = TYPE_MAP.get(raw_type, "general")
    # 備考列（履修上の注意）が 27 列目にある想定。なければ空文字。
    enrollment_note = str(row.iloc[27]).strip() if len(row) > 27 and not pd.isna(row.iloc[27]) else ""
    category = map_category(raw_category, enrollment_note)

    # 単位列は 5 列目にある想定。数字であれば整数化、失敗すれば 0 を使う。
    credits_raw = row.iloc[5] if len(row) > 5 else pd.NA
    credits_value = 0
    if not pd.isna(credits_raw):
        try:
            credits_value = int(float(str(credits_raw).strip()))
        except (ValueError, TypeError):
            credits_value = 0

    grades = detect_grades(row)
    departments = choose_departments(subject_type, subject_code, default_department)
    international_flag = determine_international_flag(
        raw_category,
        name,
        subject_type,
        category,
        grades,
    )

    details = {
        "curriculumYear": [curriculum_year],
        "department": departments,
        "grade": grades,
        "isForInternationalStudents": international_flag,
    }

    item = {
        "id": str(uuid.uuid4()),
        "name": name,
        "credits": credits_value,
        "category": category,
        "type": subject_type,
        "details": details,
    }

    if enrollment_note:
        item["conditions"] = enrollment_note

    return item


def validate_results(items: List[dict], schema_path: str) -> None:
    """JSON Schema（Draft7）で検証してエラーを標準エラーに出力するだけ。

    - 検証で得られたエラーは停止しない（運用上の柔軟性を保つため）。
    - 最初の 10 件のみを表示する。
    """

    with open(schema_path, "r", encoding="utf-8") as fh:
        schema = json.load(fh)

    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(items))
    if errors:
        print(f"⚠️ スキーマ検証で {len(errors)} 件のエラーがあります (先頭10件)。", file=sys.stderr)
        for error in errors[:10]:
            # error.message はわかりやすい文章になる
            print(f" - {error.message}", file=sys.stderr)


def write_output(items: Iterable[dict], output_path: str) -> None:
    """整形（インデント付き）で JSON を書き出す。日本語をそのまま保持するため ensure_ascii=False を使用。"""

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(list(items), fh, ensure_ascii=False, indent=2)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="シラバス CSV を JSON スキーマへ変換するツール")
    parser.add_argument("--input", default="output.csv", help="入力 CSV（もしくは txt）のパス")
    parser.add_argument("--schema", default="01_credit_scheme.json", help="JSON スキーマファイルのパス")
    parser.add_argument("--output", default="converted.json", help="出力 JSON のパス")
    parser.add_argument("--department", default="j", help="専門科目のデフォルト学科コード（例: j）")
    parser.add_argument("--year", type=int, default=2025, help="カリキュラム年（例: 2025）")
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    rows = load_rows(args.input)
    dataframe = rows_to_dataframe(rows)

    items: List[dict] = []
    skipped = 0

    for _, row in dataframe.iterrows():
        item = build_item(row, args.year, args.department)
        if item is None:
            skipped += 1
            continue
        items.append(item)

    if not items:
        raise SystemExit("有効な行がひとつも変換されませんでした。入力形式を確認してください。")

    validate_results(items, args.schema)
    write_output(items, args.output)

    print(f"✅ 出力しました: {args.output} (件数: {len(items)}, スキップ: {skipped})")


if __name__ == "__main__":
    main()
