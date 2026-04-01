import requests
import pandas as pd
import io
import argparse

class CreditScraping():
    def __init__(self, year, department_id, department_name):
        self.year = year
        self.department_id = department_id
        self.department_name = department_name


    def get_credit_table(self):
        url = f"https://syllabus.kosen-k.go.jp/Pages/PublicSubjects?school_id=14&department_id={self.department_id}&year={self.year}&lang=ja"
        
        print(f"--- スクレピング開始: {url} ---")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            response.encoding = response.apparent_encoding

        except requests.exceptions.RequestException as e:
            print(f"Error: Webページへのアクセスに失敗しました。詳細:{e}")
            return None

        dfs = pd.read_html(io.StringIO(response.text))

        print(f"✅ 取得した表の数: {len(dfs)}")
        print("--- 単位の表の先頭5行 ---")
        print(dfs[2].head())
        print(type(dfs[2]))

        dfs[2].to_csv("output.csv")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2021)
    parser.add_argument("--department_id", type=int, default=14)
    parser.add_argument("--department", type=str, default='j')
    args = parser.parse_args()

    year = args.year
    department_id = args.department_id
    department = args.department

    url = f"https://syllabus.kosen-k.go.jp/Pages/PublicSubjects?school_id=14&department_id={department_id}&year={year}&lang=ja"
    credit_scraping = CreditScraping(year, department_id, department)
    
    credit_scraping.get_credit_table()
