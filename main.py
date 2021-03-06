import datetime
import json
import pathlib
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import pandas as pd
import requests

from retry import retry
import simplejson as json

# 設定

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko"

DOWNLOAD_DIR = "data"
DATA_DIR = "data"

# ファイルダウンロード

@retry(tries=5, delay=5, backoff=3)
def get_file(url, file_name, dir="."):

    r = requests.get(url, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()

    p = pathlib.Path(dir, file_name)
    p.parent.mkdir(parents=True, exist_ok=True)

    with p.open(mode="wb") as fw:
        fw.write(r.content)

    return p

url = "https://www.pref.kumamoto.jp/soshiki/211/50632.html"

r = requests.get(url, headers={"User-Agent": USER_AGENT})
r.raise_for_status()

soup = BeautifulSoup(r.content, "html.parser")

xlsx_urls = {}

for trs in soup.find("h3", text="新型コロナウイルス感染症").find_next("table").select("tbody > tr"):
    tds = trs.find_all("td")
    s = tds[0].get_text(strip=True)

    m = re.match("(帰国者・接触者相談センター相談件数|陽性患者属性|検査件数)", s)

    if m:

        xlsx_urls[m.group(0)] = urljoin(url, tds[2].find("a").get("href"))

# ファイルダウンロード

soudan_path = get_file(xlsx_urls["帰国者・接触者相談センター相談件数"], "soudan.xlsx", DOWNLOAD_DIR)
kensa_path = get_file(xlsx_urls["検査件数"], "kensa.xlsx", DOWNLOAD_DIR)
kanja_path = get_file(xlsx_urls["陽性患者属性"], "kanja.xlsx", DOWNLOAD_DIR)

# データラングリング

JST = datetime.timezone(datetime.timedelta(hours=+9), "JST")

dt_now = datetime.datetime.now(JST)
dt_update = dt_now.strftime("%Y/%m/%d %H:%M")

data = {"lastUpdate": dt_update}

# contacts
df_soudan = pd.read_excel(soudan_path, engine="openpyxl", parse_dates=["受付_年月日"])

df_soudan.set_index("受付_年月日", inplace=True)

ser_contacts = pd.to_numeric(df_soudan["相談件数"], errors="coerce").dropna().astype(int)

df_contacts = pd.DataFrame({"小計": ser_contacts})
df_contacts["日付"] = df_contacts.index.strftime("%Y-%m-%d")

data["contacts"] = {
    "data": df_contacts.to_dict(orient="records"),
    "date": dt_update,
}

# inspections_summary

df_kensa = (
    pd.read_excel(kensa_path, engine="openpyxl", parse_dates=["実施_年月日"])
    .dropna(subset=["実施_年月日"])
    .pivot(index="実施_年月日", columns="全国地方公共団体コード", values="検査実施_件数")
    .fillna(0)
    .astype(int)
)

df_kensa.rename(columns={430005: "熊本県", 431001: "熊本市"}, inplace=True)

df_kensa.sort_index(inplace=True)

labels = df_kensa.index.map(lambda s: f"{s.month}/{s.day}")

data["inspections_summary"] = {
    "data": df_kensa.to_dict(orient="list"),
    "labels": labels.tolist(),
    "date": dt_update,
}

# patients

weeks = ["月", "火", "水", "木", "金", "土", "日"]

df_kanja = pd.read_excel(
    kanja_path,
    dtype={
        "No": "int",
        "全国地方公共団体コード": "Int64",
        "患者_渡航歴の有無フラグ": "Int64",
        "患者_退院済フラグ": "Int64",
    },
    engine="openpyxl",
)

df_kanja["公表_年月日"] = pd.to_datetime(df_kanja["公表_年月日"], errors="coerce")
df_kanja["確定_年月日"] = pd.to_datetime(df_kanja["確定_年月日"], errors="coerce")

# 確定_年月日がないものを除去
df_kanja.dropna(subset=["確定_年月日"], inplace=True)

df_kanja.columns = df_kanja.columns.map(lambda s: s.replace("患者_", ""))

df_kanja.rename(columns={"No": "県番号"}, inplace=True)

df_kanja["渡航歴の有無フラグ"].fillna(0, inplace=True)
df_kanja["退院済フラグ"].fillna(0, inplace=True)

df_kanja["公表日"] = df_kanja["公表_年月日"].dt.strftime("%Y-%m-%dT08:00:00.000Z")
df_kanja["確定日"] = df_kanja["確定_年月日"].dt.strftime("%Y-%m-%dT08:00:00.000Z")
df_kanja["date"] = df_kanja["確定_年月日"].dt.strftime("%Y-%m-%d")
df_kanja["曜日"] = df_kanja["確定_年月日"].dt.dayofweek.apply(lambda x: weeks[x])
df_kanja["退院"] = df_kanja["退院済フラグ"].replace({1: "○", 0: None})

patients = df_kanja.loc[:, ["県番号", "公表日", "確定日", "曜日", "居住地", "年代", "性別", "退院", "date"]]

data["patients"] = {
    "data": patients.to_dict(orient="records"),
    "date": dt_update,
}

# patients_summary

ser_patients_sum = df_kanja["確定_年月日"].value_counts().sort_index()
if df_kensa.index[-1] > ser_patients_sum.index[-1]:
    ser_patients_sum[df_kensa.index[-1]] = 0

ser_patients_sum.sort_index(inplace=True)

df_patients_sum = pd.DataFrame({"小計": ser_patients_sum.asfreq("D", fill_value=0)})
df_patients_sum["日付"] = df_patients_sum.index.strftime("%Y-%m-%dT08:00:00.000Z")

data["patients_summary"] = {
    "data": df_patients_sum.to_dict(orient="records"),
    "date": dt_update,
}

# patients_summary_announced

ser_patients_ann = df_kanja["公表_年月日"].value_counts().sort_index()
if df_kensa.index[-1] > ser_patients_ann.index[-1]:
    ser_patients_ann[df_kensa.index[-1]] = 0

ser_patients_ann.sort_index(inplace=True)

df_patients_ann = pd.DataFrame({"小計": ser_patients_ann.asfreq("D", fill_value=0)})
df_patients_ann["日付"] = df_patients_ann.index.strftime("%Y-%m-%dT08:00:00.000Z")

data["patients_summary_announced"] = {
    "data": df_patients_ann.to_dict(orient="records"),
    "date": dt_update,
}

# main_summary

# 状態の内死亡以外（無症状・軽症・中等症・重症・非公表）を症状にコピー
df_kanja["症状"] = df_kanja["状態"].mask(df_kanja["状態"] == "死亡")

# 状態の内死亡以外を入院中に変更
df_kanja["状況"] = df_kanja["状態"].where(df_kanja["状態"] == "死亡", "入院中")

# 状態が死亡以外でかつ退院済みフラグが1の場合を退院に変更
df_kanja["状況"] = df_kanja["状況"].mask(
    (df_kanja["退院済フラグ"] == 1) & (df_kanja["状態"] != "死亡"), "退院"
)

df_kanja["状況"] = df_kanja["状況"].mask(
    (df_kanja["状況"] == "入院中") & (df_kanja["症状"].isnull()), "確認中"
)

situation = (
    df_kanja["状況"].value_counts().reindex(["入院中", "退院", "死亡", "確認中"]).fillna(0).astype(int)
)

condition = (
    df_kanja["症状"]
    .value_counts()
    .reindex(["無症状", "軽症", "中等症", "重症"])
    .fillna(0)
    .astype(int)
)

data["main_summary"] = {
    "attr": "検査実施人数",
    "value": int(df_kensa.sum(axis=1).sum()),
    "children": [
        {
            "attr": "陽性患者数",
            "value": int(len(df_kanja)),
            "children": [
                {
                    "attr": "入院中",
                    "value": int(situation["入院中"]),
                    "children": [
                        {
                            "attr": "無症状・軽症・中等症",
                            "value": int(condition.sum() - condition["重症"]),
                        },
                        {"attr": "重症", "value": int(condition["重症"])},
                        {"attr": "その他", "value": 0},
                    ],
                },
                {"attr": "確認中", "value": int(situation["確認中"])},
                {"attr": "退院", "value": int(situation["退院"])},
                {"attr": "死亡", "value": int(situation["死亡"])},
            ],
        }
    ],
}

# JSONに保存

p = pathlib.Path(DATA_DIR, "data.json")
p.parent.mkdir(parents=True, exist_ok=True)

with p.open(mode="w", encoding="utf-8") as fw:
    json.dump(data, fw, ignore_nan=True, ensure_ascii=False, indent=4)
