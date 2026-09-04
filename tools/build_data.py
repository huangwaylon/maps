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
# Romaji aliases so an English query ("shibuya", "kanagawa") matches a kanji
# address. Google returns Japanese addresses in kanji, so without this the
# search box only works in Japanese for the ~2,100 places in Japan.
PREF_ROMAJI = {
    "北海道":"Hokkaido","青森県":"Aomori","岩手県":"Iwate","宮城県":"Miyagi",
    "秋田県":"Akita","山形県":"Yamagata","福島県":"Fukushima","茨城県":"Ibaraki",
    "栃木県":"Tochigi","群馬県":"Gunma","埼玉県":"Saitama",
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
    ("cafe",       ["cafe","coffee","roaster","roastery","espresso","tea"],
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
                                        ["レストラン","食堂","料理","割烹","定食","天ぷら","とんかつ","餃子","ピザ","タコス"]),
]

def fold(s):
    """NFKC + casefold + strip Latin accents, so 'Café'/'ＣＡＦＥ'/'cafe' all match.

    Only U+0300-U+036F (Latin combining diacritics) are stripped. Dropping every
    Mn character also removes the Japanese voicing marks U+3099/U+309A, which
    turns ベーカリー into ヘーカリー and makes every voiced-kana keyword below
    unmatchable. NFC at the end recomposes, keeping the string length stable.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).casefold()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if not 0x0300 <= ord(c) <= 0x036F)
    return unicodedata.normalize("NFC", s)

# Real Google categories are far better than name keywords, so map those first
# and fall back to the keyword guess only when a place has no category.
CATEGORY_KIND = [
    ("ramen",      ["ramen", "noodle"]),
    ("sushi",      ["sushi", "seafood", "fish"]),
    ("cafe",       ["cafe", "coffee", "espresso", "tea house", "tea room"]),
    ("bakery",     ["bakery", "patisserie", "bread"]),
    ("sweets",     ["dessert", "ice cream", "confection", "sweets", "chocolate", "gelato", "cake"]),
    ("bar",        ["bar", "pub", "brewery", "izakaya", "beer", "wine", "sake", "cocktail", "nightclub"]),
    ("yakiniku",   ["barbecue", "yakiniku", "steak", "grill"]),
    ("curry",      ["curry"]),
    ("soba_udon",  ["soba", "udon", "noodle shop"]),
    ("onsen",      ["onsen", "hot spring", "spa", "sauna", "public bath", "bath house"]),
    ("lodging",    ["hotel", "ryokan", "hostel", "inn", "lodging", "resort", "campground", "guest house"]),
    ("shrine",     ["shrine", "temple", "church", "buddhist", "shinto", "place of worship"]),
    ("nature",     ["park", "garden", "beach", "mountain", "lake", "waterfall", "trail", "forest",
                    "island", "national reserve", "scenic", "campground", "hiking"]),
    ("museum",     ["museum", "art gallery", "aquarium", "zoo", "gallery", "observatory"]),
    ("station",    ["station", "airport", "port", "transit", "bus stop"]),
    ("shop",       ["store", "shop", "market", "boutique", "supermarket", "bookstore"]),
    ("restaurant", ["restaurant", "food", "diner", "bistro", "eatery", "cafeteria", "izakaya"]),
]

# Fold the keyword tables once, so a folded haystack is compared against folded
# needles. Without this every CJK keyword with a voiced kana never matches.
KINDS = [(k, [fold(x) for x in a], [fold(x) for x in c]) for k, a, c in KINDS]
CATEGORY_KIND = [(k, [fold(x) for x in v]) for k, v in CATEGORY_KIND]


def kind_from_category(cat):
    """Map a Google category string to one of our coarse kinds, or None."""
    c = fold(cat)
    for kind, keys in CATEGORY_KIND:
        if any(k in c for k in keys):
            return kind
    return None

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
        "c":  country,
        "s":  region,
        "ct": city,
        "k":  guess_kind(p.get("name") or ""),
        # Search-only aliases; never displayed.
        "ro": " ".join(dict.fromkeys(x for x in (
                  PREF_ROMAJI.get(region or ""), CITY_ROMAJI.get(city or "")) if x)) or None,
    }

def load_details():
    """Read data/details.jsonl (if present) into {key: record}. Missing file is fine."""
    path = "data/details.jsonl"
    if not os.path.exists(path):
        print("no details.jsonl yet — building without rich fields", file=sys.stderr)
        return {}
    out = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line, strict=False)
            except ValueError:
                continue  # tolerate a torn final line from an interrupted run
            if rec.get("error"):
                continue
            for k in (rec.get("gid"), rec.get("fid"), rec.get("key")):
                if k:
                    out.setdefault(k, rec)
    return out


def detail_for(place, details):
    return details.get(place.get("gid")) or details.get(place.get("fid"))


# Places per detail shard. Sized so one card tap costs ~55 KB gzipped rather
# than the ~225 KB a 250-place shard costs once reviews are included. The client
# reads this value from places.json, so it adapts automatically.
SHARD_SIZE = 60
REVIEWS_PER_PLACE = 3       # reviews kept in a shard, for the detail sheet
REVIEW_CHARS = 420          # per-review clip in a shard
DIGEST_EDITORIAL_CHARS = 200
DIGEST_REVIEW_CHARS = 240


def first(seq):
    return seq[0] if isinstance(seq, list) and seq else None


def clip(text, n):
    """Trim to n chars on a word boundary where possible."""
    if not text:
        return None
    text = " ".join(str(text).split())
    if len(text) <= n:
        return text
    cut = text[:n]
    space = cut.rfind(" ")
    return (cut[:space] if space > n * 0.6 else cut).rstrip(" ,.;:") + "…"


def shard_record(det):
    """Full per-place detail for the sheet. English, with Japanese where it differs."""
    en, ja = det.get("en") or {}, det.get("ja") or {}
    differs = lambda a, b: b if (b and b != a) else None
    reviews = [{k: v for k, v in {
        "a": r.get("author"), "s": r.get("stars"),
        "w": r.get("when"), "t": clip(r.get("text"), REVIEW_CHARS),
    }.items() if v} for r in (en.get("reviews") or [])[:REVIEWS_PER_PLACE]]

    return {k: v for k, v in {
        "a":    en.get("address"),
        "aj":   differs(en.get("address"), ja.get("address")),
        "nb":   en.get("neighborhood"),
        "cat":  en.get("categories") or None,
        "catj": differs(en.get("categories"), ja.get("categories")),
        "rt":   en.get("rating"),
        "rc":   en.get("review_count"),
        "hist": en.get("rating_histogram"),
        "pr":   en.get("price_reported"),
        "ed":   clip(en.get("editorial"), 400),
        "edj":  differs(clip(en.get("editorial"), 400), clip(ja.get("editorial"), 400)),
        "rv":   reviews or None,
        "ph":   en.get("phone"),
        "w":    en.get("website"),
        "hw":   en.get("hours_week"),
        "st":   en.get("status"),
        "stj":  differs(en.get("status"), ja.get("status")),
        "tz":   en.get("timezone"),
        "img":  (en.get("photo_urls") or [None])[0],
        "book": (en.get("booking_links") or [None])[0],
    }.items() if v not in (None, [], "")}


def digest_record(det):
    """Compact text for the model: the fields that actually change an answer.

    Kept separate from both places.json and the shards. A 40-place question can
    touch every detail shard, so the model context cannot be assembled from
    them, and this text is far too big to sit in the initial download.

    A review snippet is included only where Google has no editorial blurb (~35%
    of places). The two say similar things, and carrying both everywhere costs
    ~440 KB gzipped against ~310 KB for the complementary version.
    """
    en = det.get("en") or {}
    editorial = clip(en.get("editorial"), DIGEST_EDITORIAL_CHARS)
    review = None
    if not editorial:
        best = max((en.get("reviews") or []),
                   key=lambda r: len(r.get("text") or ""), default=None)
        if best:
            review = clip(best.get("text"), DIGEST_REVIEW_CHARS)
    return {k: v for k, v in {
        "ed": editorial,
        "rv": review,
        "pr": (en.get("price_reported") or [None])[0],
        "hw": summarise_hours(en.get("hours_week")),
    }.items() if v}


def summarise_hours(week):
    """One short line, e.g. 'Mon-Fri 11 AM-8 PM; Sat-Sun closed'-ish.

    Collapses consecutive days that share the same hours so the model sees the
    shape of the week in a few tokens rather than seven lines.
    """
    if not week:
        return None
    runs = []
    for day, spans in week:
        label = ", ".join(spans) if spans else "closed"
        if runs and runs[-1][1] == label:
            runs[-1][0].append(day[:3])
        else:
            runs.append(([day[:3]], label))
    parts = []
    for days, label in runs:
        span = days[0] if len(days) == 1 else "%s-%s" % (days[0], days[-1])
        parts.append("%s %s" % (span, label))
    return clip("; ".join(parts), 120)


def main():
    lid = list_id_from_url(SHORTLINK)
    print("list:", lid, file=sys.stderr)
    src = normalize(fetch(lid))
    details = load_details()
    print("details loaded for %d places" % len({id(v) for v in details.values()}),
          file=sys.stderr)

    places = [derive(p) for p in src["places"]]
    for place, raw in zip(places, src["places"]):
        place["_gid"], place["_fid"] = raw.get("gid"), raw.get("fid")
    places.sort(key=lambda p: p["t"] or "", reverse=True)

    # Intern category strings: they repeat heavily across 3k places, and an
    # index costs a few bytes where the string costs tens.
    cats, cat_index = [], {}
    shards, digest, enriched = {}, {}, 0

    for i, place in enumerate(places):
        # Pop both before looking up: `a or b` short-circuits, so popping
        # inside the expression leaves the second key in the payload.
        gid, fid = place.pop("_gid", None), place.pop("_fid", None)
        det = details.get(gid) or details.get(fid)
        address = place.pop("a", None)

        if det:
            enriched += 1
            rec = shard_record(det)
            rec.setdefault("a", address)
            primary = first(rec.get("cat"))
            if primary:
                if primary not in cat_index:
                    cat_index[primary] = len(cats)
                    cats.append(primary)
                place["ci"] = cat_index[primary]
                kind = kind_from_category(primary)
                if kind:
                    place["k"] = kind
            if rec.get("rt"):
                place["rt"] = rec["rt"]
        else:
            rec = {"a": address} if address else {}

        if rec:
            shards.setdefault(i // SHARD_SIZE, {})[str(i)] = rec
        if det:
            entry = digest_record(det)
            if entry:
                digest[str(i)] = entry

    # Precomputed name order: localeCompare over 3k rows costs ~170ms on a
    # throttled phone, and it never changes, so do it here instead.
    # Unnamed places sort last rather than first under an empty-string key.
    name_order = sorted(range(len(places)),
                        key=lambda i: (not places[i]["n"], (places[i]["n"] or "").casefold()))

    os.makedirs("data/details", exist_ok=True)
    for old in os.listdir("data/details"):
        os.remove(os.path.join("data/details", old))
    for shard, rec in shards.items():
        with open("data/details/%03d.json" % shard, "w") as f:
            json.dump(rec, f, ensure_ascii=False, separators=(",", ":"))

    with open("data/digest.json", "w") as f:
        json.dump(digest, f, ensure_ascii=False, separators=(",", ":"))

    out = {
        "list_name": src["list_name"],
        "owner": src["owner"],
        "count": len(places),
        "modified": src["modified"],
        "shard_size": SHARD_SIZE,
        "categories": cats,
        "name_order": name_order,
        "places": places,
    }
    with open("data/places.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    kb = lambda path: os.path.getsize(path) / 1024
    shard_kb = sum(kb("data/details/" + f) for f in os.listdir("data/details"))
    print("wrote data/places.json: %d places, %.0f KB (%d enriched, %d categories)"
          % (len(places), kb("data/places.json"), enriched, len(cats)), file=sys.stderr)
    print("wrote %d detail shards: %.0f KB total" % (len(shards), shard_kb),
          file=sys.stderr)
    print("wrote data/digest.json: %d entries, %.0f KB"
          % (len(digest), kb("data/digest.json")), file=sys.stderr)


if __name__ == "__main__":
    main()
