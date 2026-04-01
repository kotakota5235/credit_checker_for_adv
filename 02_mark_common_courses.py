#!/usr/bin/env python3
"""3学科分の専攻科 JSON を統合し、専門共通科目に commonCourseId を付与するスクリプト。

【アルゴリズム】
  1. 指定年度ディレクトリ内の学科 JSON（ME, DJ, CC）を読み込む
  2. type == "major" の科目のうち、
     「名前 + 単位数 + category + grade（ソート済み）」が
     2学科以上に一致するものを専門共通科目と判定する
  3. 同一グループの科目に同じ commonCourseId（UUID）を付与する
  4. 各学科 JSON を上書き保存する（--dry-run で確認のみも可能）

【使い方】
  # 通常実行（{year}/ ディレクトリの ME.json, DJ.json, CC.json を処理）
  python 02_mark_common_courses.py --year 2025

  # 対象学科を明示（専攻科以外にも使える）
  python 02_mark_common_courses.py --year 2025 --depts ME DJ CC

  # 確認だけして上書きしない
  python 02_mark_common_courses.py --year 2025 --dry-run

  # 既存の commonCourseId を一度クリアしてから再付与
  python 02_mark_common_courses.py --year 2025 --reset
"""

import argparse
import json
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple


# 照合キーの型: (名前, 単位数, category, grade タプル)
MatchKey = Tuple[str, int, str, tuple]

# 名前が複数学科に存在していても、専門専攻科目として扱う科目名の除外リスト。
# 各専攻で「別々に開講される同名科目」がここに該当する。
# --exclude-names オプションでコマンドラインからも追加可能。
SPECIALIZED_ONLY_NAMES: List[str] = [
    "特別研究I",
    "特別研究II",
    "特別実験",
    "特別演習I",
    "特別演習II",
]


def make_match_key(item: dict) -> MatchKey:
    """照合に使うキーを生成する。

    grade はリストなので順序を正規化するためソートしてタプルに変換する。
    """
    return (
        item["name"],
        item["credits"],
        item["category"],
        tuple(sorted(item["details"]["grade"])),
    )


def load_dept_json(path: Path) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_dept_json(path: Path, items: List[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def mark_common_courses(
    dept_data: Dict[str, List[dict]],
    min_depts: int = 2,
    reset: bool = False,
    exclude_names: List[str] = None,
) -> Dict[str, int]:
    """共通専門科目を検出して commonCourseId を付与する。

    Args:
        dept_data:     { 学科コード: [科目dict, ...] } の辞書（インプレース変更）
        min_depts:     何学科以上に同一科目があれば共通とするか（デフォルト2）
        reset:         既存の commonCourseId をクリアしてから付与するか
        exclude_names: 除外する科目名リスト（同名でも専門専攻科目として扱う）

    Returns:
        { "added": 付与件数, "skipped": 既存スキップ件数, "common_groups": グループ数 }
    """
    excluded = set(exclude_names or []) | set(SPECIALIZED_ONLY_NAMES)

    # --- リセット ---
    if reset:
        for items in dept_data.values():
            for item in items:
                item.pop("commonCourseId", None)

    # --- 除外科目の commonCourseId を念のりクリア（reset なしでも適用）---
    for items in dept_data.values():
        for item in items:
            if item.get("name") in excluded:
                item.pop("commonCourseId", None)

    # --- 照合キー → [(学科コード, 科目dict)] のマップを構築 ---
    # major 科目のみ対象・除外リストに含まれる科目はスキップ
    key_to_entries: Dict[MatchKey, List[Tuple[str, dict]]] = defaultdict(list)

    for dept_code, items in dept_data.items():
        for item in items:
            if item.get("type") != "major":
                continue
            if item.get("name") in excluded:
                continue  # 専門専攻科目として固定
            key = make_match_key(item)
            key_to_entries[key].append((dept_code, item))

    # --- 2学科以上に存在するキーを共通科目グループとして処理 ---
    stats = {"added": 0, "skipped": 0, "common_groups": 0}

    for key, entries in key_to_entries.items():
        # 何学科に存在するか（同一学科の複数エントリは1学科としてカウント）
        involved_depts = {dept_code for dept_code, _ in entries}
        if len(involved_depts) < min_depts:
            continue  # 1学科のみ → 専門専攻科目

        stats["common_groups"] += 1

        # このグループの commonCourseId を決定する
        # 既に付与済みのものがあればそれを使い回す（--reset なし時の冪等性）
        existing_ids = [
            item["commonCourseId"]
            for _, item in entries
            if "commonCourseId" in item
        ]
        group_id = existing_ids[0] if existing_ids else str(uuid.uuid4())

        for _, item in entries:
            if "commonCourseId" not in item:
                item["commonCourseId"] = group_id
                stats["added"] += 1
            else:
                # 既に付いているが違う ID になっているケースを統一
                if item["commonCourseId"] != group_id:
                    item["commonCourseId"] = group_id
                    stats["added"] += 1
                else:
                    stats["skipped"] += 1

    return stats


def print_summary(dept_data: Dict[str, List[dict]]) -> None:
    """共通/専攻別の件数をコンソールに表示する。"""
    print("\n📊 付与後の内訳:")
    for dept_code, items in sorted(dept_data.items()):
        major_items = [i for i in items if i.get("type") == "major"]
        common = [i for i in major_items if "commonCourseId" in i]
        specialized = [i for i in major_items if "commonCourseId" not in i]
        general = [i for i in items if i.get("type") == "general"]
        print(
            f"  {dept_code:4s}: 一般={len(general):2d}件 "
            f"専門共通={len(common):2d}件  専門専攻={len(specialized):2d}件  "
            f"合計={len(items):2d}件"
        )

    # 共通科目名一覧
    common_names: Dict[str, set] = defaultdict(set)
    for dept_code, items in dept_data.items():
        for item in items:
            if item.get("type") == "major" and "commonCourseId" in item:
                common_names[item["commonCourseId"]].add(item["name"])

    print(f"\n🔗 専門共通科目グループ ({len(common_names)}グループ):")
    for cid, names in sorted(common_names.items(), key=lambda x: sorted(x[1])[0]):
        # 名前は通常1種類だが念のため表示
        label = " / ".join(sorted(names))
        print(f"  [{cid[:8]}...] {label}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="専攻科 JSON に専門共通科目フラグ（commonCourseId）を付与する"
    )
    parser.add_argument("--year", type=int, default=2025, help="対象年度（ディレクトリ名）")
    parser.add_argument(
        "--depts",
        nargs="+",
        default=["ME", "DJ", "CC"],
        help="処理する学科コード（大文字）。デフォルト: ME DJ CC",
    )
    parser.add_argument(
        "--dir",
        default=None,
        help="JSONファイルのディレクトリパス。省略時は ./{year}/ を使用",
    )
    parser.add_argument(
        "--min-depts",
        type=int,
        default=2,
        help="何学科以上に存在すれば共通科目とするか（デフォルト: 2）",
    )
    parser.add_argument(
        "--exclude-names",
        nargs="+",
        default=[],
        metavar="NAME",
        help="追加で専門専攻科目として固定する科目名（スペース区切り）。"
             f"デフォルト除外リスト {SPECIALIZED_ONLY_NAMES} に追記される。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="確認のみ。ファイルを上書きしない",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="既存の commonCourseId をクリアしてから付与し直す",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    base_dir = Path(args.dir) if args.dir else Path(str(args.year))
    if not base_dir.exists():
        raise SystemExit(f"ディレクトリが見つかりません: {base_dir}")

    # --- JSON 読み込み ---
    dept_data: Dict[str, List[dict]] = {}
    for dept_code in args.depts:
        path = base_dir / f"{dept_code}.json"
        if not path.exists():
            print(f"⚠️  {path} が見つかりません。スキップします。")
            continue
        dept_data[dept_code] = load_dept_json(path)
        print(f"  読み込み: {path} ({len(dept_data[dept_code])}件)")

    if len(dept_data) < 2:
        raise SystemExit("比較するには JSON ファイルが2つ以上必要です。")

    # --- 共通科目フラグ付与 ---
    stats = mark_common_courses(
        dept_data,
        min_depts=args.min_depts,
        reset=args.reset,
        exclude_names=args.exclude_names,
    )

    print(f"\n✅ 専門共通グループ数: {stats['common_groups']}")
    print(f"   commonCourseId 付与: {stats['added']}件  スキップ(既存): {stats['skipped']}件")

    print_summary(dept_data)

    # --- 保存 ---
    if args.dry_run:
        print("\n（--dry-run モード: ファイルは上書きしません）")
        return

    for dept_code, items in dept_data.items():
        path = base_dir / f"{dept_code}.json"
        save_dept_json(path, items)
        print(f"  保存: {path}")

    print("\n🎉 完了しました。")


if __name__ == "__main__":
    main()
