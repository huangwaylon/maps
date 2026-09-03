#!/usr/bin/env python3
"""Build data/places.json for the web app.

Fetches the shared Google Maps list, derives browse facets (country / region /
city / kind) from the address and name, and writes a compact JSON payload the
static client loads in one request.
"""
import json, os, re, sys, unicodedata
from fetch_list import fetch, normalize, list_id_from_url

SHORTLINK = "https://maps.app.goo.gl/9jYW6WS4bMrH9WH39"

# Google returns country names in the viewer's locale, so the tail of an address
# is a mix of Japanese and English. Map both to one canonical English label.
COUNTRIES = {
    "日本": "Japan", "Japan": "Japan",
    "アメリカ合衆国": "United States", "United States": "United States",
    "フランス": "France", "France": "France",
    "ベトナム": "Vietnam", "Vietnam": "Vietnam",
    "韓国": "South Korea", "South Korea": "South Korea",
    "フィリピン": "Philippines", "Philippines": "Philippines",
    "台湾": "Taiwan", "Taiwan": "Taiwan",
    "インドネシア": "Indonesia", "Indonesia": "Indonesia",
    "イタリア": "Italy", "Italy": "Italy",
    "タイ": "Thailand", "Thailand": "Thailand",
    "トルコ": "Turkey", "Turkey": "Turkey",
    "メキシコ": "Mexico", "Mexico": "Mexico",
    "ニュージーランド": "New Zealand", "New Zealand": "New Zealand",
    "グアム": "Guam", "Guam": "Guam",
    "ラオス": "Laos", "Laos": "Laos",
    "スペイン": "Spain", "Spain": "Spain",
    "マレーシア": "Malaysia", "Malaysia": "Malaysia",
    "ドイツ": "Germany", "Germany": "Germany",
    "香港": "Hong Kong", "Hong Kong": "Hong Kong",
    "フィンランド": "Finland", "Finland": "Finland",
    "ギリシャ": "Greece", "Greece": "Greece",
    "オランダ": "Netherlands", "Netherlands": "Netherlands",
    "シンガポール": "Singapore", "Singapore": "Singapore",
    "中国": "China", "China": "China",
    "カンボジア": "Cambodia", "Cambodia": "Cambodia",
    "イギリス": "United Kingdom", "United Kingdom": "United Kingdom",
    "オーストラリア": "Australia", "Australia": "Australia",
    "カナダ": "Canada", "Canada": "Canada",
    "スイス": "Switzerland", "Switzerland": "Switzerland",
    "ポルトガル": "Portugal", "Portugal": "Portugal",
    "オーストリア": "Austria", "Austria": "Austria",
    "ベルギー": "Belgium", "Belgium": "Belgium",
    "スウェーデン": "Sweden", "Sweden": "Sweden",
    "ノルウェー": "Norway", "Norway": "Norway",
    "デンマーク": "Denmark", "Denmark": "Denmark",
    "アイスランド": "Iceland", "Iceland": "Iceland",
    "インド": "India", "India": "India",
    "ネパール": "Nepal", "Nepal": "Nepal",
    "スリランカ": "Sri Lanka", "Sri Lanka": "Sri Lanka",
    "ブラジル": "Brazil", "Brazil": "Brazil",
    "アルゼンチン": "Argentina", "Argentina": "Argentina",
    "ペルー": "Peru", "Peru": "Peru",
    "チリ": "Chile", "Chile": "Chile",
    "エジプト": "Egypt", "Egypt": "Egypt",
    "モロッコ": "Morocco", "Morocco": "Morocco",
    "南アフリカ": "South Africa", "South Africa": "South Africa",
}
# English names of Japanese prefectures that show up as a bare address tail.
JP_TAILS_UNUSED = {
    "tokyo","osaka","kyoto","hokkaido","okinawa","nagano","kanagawa","chiba",
    "shizuoka","kagawa","saitama","hyogo","aichi","fukuoka","hiroshima","niigata",
    "ibaraki","tochigi","gunma","yamanashi","gifu","mie","nara","wakayama",
    "shiga","fukui","ishikawa","toyama","tottori","shimane","okayama","yamaguchi",
    "tokushima","ehime","kochi","saga","nagasaki","kumamoto","oita","miyazaki",
    "kagoshima","aomori","iwate","miyagi","akita","yamagata","fukushima",
}
# Romaji aliases so an English query ("shibuya", "kanagawa") matches a kanji
# address. Google returns Japanese addresses in kanji, so without this the
# search box only works in Japanese for the ~2,100 places in Japan.
PREF_ROMAJI = {
    "北海道":"Hokkaido","青森県":"Aomori","岩手県":"Iwate","宮城県":"Miyagi",
    "秋田県":"Akita","山形県":"Yamagata","福島県":"Fukushima","茨城県":"Ibaraki",
    "栃木県":"Tochigi","群馬県":"Gunma","群馬県":"Gunma","埼玉県":"Saitama",
    "千葉県":"Chiba","東京都":"Tokyo","神奈川県":"Kanagawa","新潟県":"Niigata",
    "富山県":"Toyama","石川県":"Ishikawa","福井県":"Fukui","山梨県":"Yamanashi",
    "長野県":"Nagano","岐阜県":"Gifu","静岡県":"Shizuoka","愛知県":"Aichi",
    "三重県":"Mie","滋賀県":"Shiga","京都府":"Kyoto","大阪府":"Osaka",
    "兵庫県":"Hyogo","奈良県":"Nara","和歌山県":"Wakayama","鳥取県":"Tottori",
    "島根県":"Shimane","岡山県":"Okayama","広島県":"Hiroshima","山口県":"Yamaguchi",
    "徳島県":"Tokushima","香川県":"Kagawa","愛媛県":"Ehime","高知県":"Kochi",
    "福岡県":"Fukuoka","佐賀県":"Saga","長崎県":"Nagasaki","熊本県":"Kumamoto",
    "大分県":"Oita","宮崎県":"Miyazaki","鹿児島県":"Kagoshima","沖縄県":"Okinawa",
    "群馬県":"Gunma",
}
# Reverse lookup: an address tail Google already romanized ("Tokyo") -> kanji.
ROMAJI_PREF = {v.casefold(): k for k, v in PREF_ROMAJI.items()}

CITY_ROMAJI = {
    # Tokyo's 23 wards
    "千代田区":"Chiyoda","中央区":"Chuo","港区":"Minato","新宿区":"Shinjuku",
    "文京区":"Bunkyo","台東区":"Taito","墨田区":"Sumida","江東区":"Koto",
    "品川区":"Shinagawa","目黒区":"Meguro","大田区":"Ota","世田谷区":"Setagaya",
    "渋谷区":"Shibuya","中野区":"Nakano","杉並区":"Suginami","豊島区":"Toshima",
    "北区":"Kita","荒川区":"Arakawa","板橋区":"Itabashi","練馬区":"Nerima",
    "足立区":"Adachi","葛飾区":"Katsushika","江戸川区":"Edogawa",
    # cities and districts that appear in this list
    "横浜市":"Yokohama","川崎市":"Kawasaki","鎌倉市":"Kamakura","藤沢市":"Fujisawa",
    "浜松市":"Hamamatsu","静岡市":"Shizuoka","伊東市":"Ito","熱海市":"Atami",
    "大阪市":"Osaka","京都市":"Kyoto","神戸市":"Kobe","名古屋市":"Nagoya",
    "札幌市":"Sapporo","福岡市":"Fukuoka","那覇市":"Naha","奈良市":"Nara",
    "水戸市":"Mito","つくば市":"Tsukuba","船橋市":"Funabashi","習志野市":"Narashino",
    "松本市":"Matsumoto","長野市":"Nagano","日光市":"Nikko","宇都宮市":"Utsunomiya",
    "宮津市":"Miyazu","奄美市":"Amami","仙台市":"Sendai","広島市":"Hiroshima",
    "金沢市":"Kanazawa","高松市":"Takamatsu","軽井沢町":"Karuizawa",
    "箱根町":"Hakone","草津町":"Kusatsu","南都留郡":"Minamitsuru",
    "足柄下郡":"Ashigarashimo","大島郡":"Oshima","富士吉田市":"Fujiyoshida",
}

PREF = re.compile(r'(北海道|東京都|京都府|大阪府|.{2,3}県)')
JP_CITY = re.compile(r'(?:北海道|東京都|京都府|大阪府|.{2,3}県)\s*(.{1,8}?[市区町村郡])')
POSTAL_JP = re.compile(r'〒\s*\d{3}-?\d{4}')
US_STATE = re.compile(r'\b([A-Z]{2})\s*$')

# Coarse kind, guessed from the place name only. `ascii` keywords match on word
# boundaries; `cjk` keywords match as substrings (no word boundaries in Japanese).
KINDS = [
    ("ramen",      ["ramen","noodles"], ["ラーメン","らーめん","麺屋","中華そば","つけ麺","担々"]),
    ("sushi",      ["sushi"],           ["寿司","鮨","すし","海鮮","魚"]),
    ("cafe",       ["cafe","café","coffee","roaster","roastery","espresso","tea"],
                                        ["カフェ","珈琲","喫茶","焙煎","茶房","紅茶"]),
    ("bakery",     ["bakery","boulangerie","bread"], ["ベーカリー","パン屋","製パン"]),
    ("sweets",     ["gelato","dessert","patisserie","chocolate","ice cream"],
                                        ["スイーツ","ケーキ","パティスリー","アイス","かき氷","抹茶","チョコ","甘味","和菓子"]),
    ("bar",        ["bar","pub","brewery","brasserie","wine","beer","cocktail","sake"],
                                        ["バー","居酒屋","酒場","立呑","立飲","ビール","日本酒","ワイン"]),
    ("izakaya",    [],                  ["焼鳥","焼き鳥","串","もつ","おでん","炉端"]),
    ("yakiniku",   ["steak","bbq","barbecue"], ["焼肉","ホルモン","ステーキ"]),
    ("curry",      ["curry"],           ["カレー","スパイス"]),
    ("soba_udon",  ["soba","udon"],     ["蕎麦","そば","うどん"]),
    ("onsen",      ["onsen","spa","sauna"], ["温泉","湯","銭湯","サウナ","湯畑"]),
    ("lodging",    ["hotel","ryokan","hostel","inn","villa","camp","camping","glamping","resort","lodge"],
                                        ["ホテル","旅館","民宿","ヴィラ","キャンプ","グランピング","ロッジ"]),
    ("shrine",     ["temple","shrine"], ["神社","大社","八幡","稲荷","寺","不動","観音"]),
    ("nature",     ["park","garden","beach","falls","trail","lake","island","mountain","peak","cape","valley","forest"],
                                        ["公園","庭園","海岸","ビーチ","滝","湖","島","岳","峠","渓谷","森","展望","岬"]),
    ("museum",     ["museum","gallery","aquarium","zoo","art"],
                                        ["美術館","博物館","ギャラリー","水族館","動物園","資料館"]),
    ("station",    ["station","airport","port","terminal"], ["駅","空港","港"]),
    ("shop",       ["shop","store","market","books","bookstore","supermarket"],
                                        ["市場","商店","書店","雑貨","百貨店","土産"]),
    ("restaurant", ["restaurant","kitchen","dining","bistro","trattoria","osteria","pizza","pizzeria",
                    "burger","tacos","taco","grill","eatery","ristorante"],
                                        ["レス��ラン","食堂","料理","割烹","定食","天ぷら","とんかつ","餃子","ピザ","タコス"]),
]

def fold(s):
    """NFKC + casefold + strip accents, so 'Café'/'ＣＡＦＥ'/'cafe' all match."""
    if not s: return ""
    s = unicodedata.normalize("NFKC", s).casefold()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")

def guess_kind(name):
    hay = fold(name)
    words = set(re.findall(r'[a-z0-9]+', hay))
    for kind, ascii_keys, cjk_keys in KINDS:
        if any(k in hay for k in cjk_keys):
            return kind
        for k in ascii_keys:
            parts = k.split()
            if (len(parts) == 1 and k in words) or (len(parts) > 1 and k in hay):
                return kind
    return "other"

def split_tail(addr):
    """Split an address tail into (country, leftover) using the country table."""
    tail = re.sub(r'[〒\d\-–—]+', ' ', addr.split(",")[-1])
    tail = re.sub(r'\s+', ' ', tail).strip()
    for name, canon in COUNTRIES.items():
        if tail == name or tail.endswith(" " + name):
            return canon, tail[: len(tail) - len(name)].strip()
    return None, tail

def derive(p):
    addr = p.get("address_full") or p.get("address_short") or ""
    country, leftover = split_tail(addr)
    region = city = None

    # `.{2,3}県` can capture a leading space or digit, and can also match a
    # Japanese transliteration of a foreign province, so keep only matches that
    # are real prefectures.
    pref = next((m for m in (x.strip() for x in PREF.findall(addr))
                 if m in PREF_ROMAJI), None)
    jp_tail = ROMAJI_PREF.get((leftover or "").casefold())
    if pref or jp_tail or (POSTAL_JP.search(addr) and not country):
        country = "Japan"
        region = pref or jp_tail
        c = JP_CITY.search(addr)
        city = c.group(1).strip() if c else None
    elif country:
        st = US_STATE.search(leftover or "")
        if country == "United States" and st:
            region = st.group(1)
        elif leftover:
            region = leftover
        parts = [x.strip() for x in addr.split(",")[:-1] if x.strip()]
        if parts:
            city = re.sub(r'\s*[\d\-]+\s*$', '', parts[-1]).strip() or None

    strip = lambda v: (v.strip() or None) if isinstance(v, str) else v
    country, region, city = strip(country), strip(region), strip(city)
    return {
        "n":  p.get("name"),
        "a":  addr or None,
        "y":  round(p["lat"], 5) if p.get("lat") is not None else None,
        "x":  round(p["lng"], 5) if p.get("lng") is not None else None,
        "g":  p.get("gid"),
        "t":  (p.get("added") or "")[:10] or None,
        "by": p.get("added_by"),
        "m":  p.get("note"),
        "ma": p.get("note_author"),
        "r":  [x["emoji"] for x in (p.get("reactions") or [])] or None,
        "c":  country,
        "s":  region,
        "ct": city,
        "k":  guess_kind(p.get("name") or ""),
        # Search-only aliases; never displayed.
        "ro": " ".join(dict.fromkeys(x for x in (
                  PREF_ROMAJI.get(region or ""), CITY_ROMAJI.get(city or "")) if x)) or None,
    }

def main():
    lid = list_id_from_url(SHORTLINK)
    print("list:", lid, file=sys.stderr)
    src = normalize(fetch(lid))
    places = [derive(p) for p in src["places"]]
    places.sort(key=lambda p: p["t"] or "", reverse=True)
    out = {
        "list_name": src["list_name"],
        "owner": src["owner"],
        "count": len(places),
        "modified": src["modified"],
        "places": places,
    }
    os.makedirs("data", exist_ok=True)
    with open("data/places.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("wrote data/places.json: %d places, %.0f KB"
          % (len(places), os.path.getsize("data/places.json") / 1024), file=sys.stderr)

if __name__ == "__main__":
    main()
