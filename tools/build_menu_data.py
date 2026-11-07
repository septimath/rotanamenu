#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Собирает menu-data.js из Google Таблицы.

Источник правды — таблица: колонки RU/EN заполняет владелец, колонки
Arabic/German/French/Italian/Spanish («auto») могут быть пустыми — скрипт
переведёт недостающее сам (DeepL, если задан DEEPL_API_KEY, иначе MyMemory)
и положит результат в кэш translate-cache.json, чтобы не тратить лимиты
повторно. Ручной текст в auto-колонках всегда имеет приоритет.

Нужные переменные окружения:
  SHEET_ID        — ID таблицы (из URL: /d/<SHEET_ID>/edit)          [обяз.]
  SHEET_GID       — gid листа (из URL #gid=...), по умолчанию первый  [опц.]
  DEEPL_API_KEY   — ключ api-free.deepl.com (500k симв/мес бесплатно) [опц.]
  MYMEMORY_EMAIL  — email для расширенного лимита MyMemory            [опц.]
Таблица должна быть доступна «всем, у кого есть ссылка» (просмотр).
"""
import hashlib, json, os, re, sys, time, urllib.parse, urllib.request

SHEET_ID = os.environ.get("SHEET_ID", "").strip()
SHEET_GID = os.environ.get("SHEET_GID", "").strip()
DEEPL_KEY = os.environ.get("DEEPL_API_KEY", "").strip()
MM_EMAIL = os.environ.get("MYMEMORY_EMAIL", "").strip()

TARGET_LANGS = ["ar", "de", "fr", "it", "es"]
TAG_INDEX = {"S": 27, "V": 28, "N": 29}
OPT_TEMPLATES = {
    "mezza-selection-cold-hot": {"label": 6, "max": 3,
        "items": [[58, 65], [59], [60, 66], [61], [62, 67], [63, 68], [64, 69]]},
    "your-choice-of-fajita": {"label": 7, "max": 1, "items": [[70], [71]]},
}
CACHE_FILE = "translate-cache.json"
BASE_FILE = "base-i18n.json"
OUT_FILE = "menu-data.js"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # tools/


def fail(msg):
    sys.exit(f"ОШИБКА: {msg}")


# ---------- чтение таблицы ----------
def fetch_sheet(sheet_id, gid=""):
    url = ("https://docs.google.com/spreadsheets/d/%s/gviz/tq?tqx=out:json&headers=1%s"
           % (sheet_id, ("&gid=" + gid) if gid else ""))
    req = urllib.request.Request(url, headers={"User-Agent": "menu-builder/1.0"})
    try:
        raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    except Exception as e:
        fail(f"не удалось прочитать таблицу {url!r}: {e}\n"
             "Проверьте SHEET_ID и доступ «всем, у кого есть ссылка».")
    m = re.search(r"setResponse\((.*)\);?\s*$", raw, re.S)
    if not m:
        fail(f"неожиданный ответ gviz: {raw[:200]}")
    data = json.loads(m.group(1))
    if data.get("status") == "error":
        fail(f"gviz вернул ошибку: {data}")
    cols = [c.get("label", "") for c in data["table"]["cols"]]
    table = []
    for row in data["table"]["rows"]:
        table.append([(c or {}).get("v") for c in row.get("c", [])])
    return cols, table


def col_index(cols, *needles):
    for i, c in enumerate(cols):
        cl = str(c).lower()
        if all(n.lower() in cl for n in needles):
            return i
    return -1


# ---------- перевод ----------
def cache_key(lang, src):
    return hashlib.sha1(f"{lang}\x00{src}".encode()).hexdigest()


def deepl_translate(text, lang):
    if not DEEPL_KEY or lang == "ar":  # DeepL не поддерживает арабский
        return None
    src_lang = "EN" if re.search(r"[A-Za-z]", text) else "RU"
    body = urllib.parse.urlencode({
        "auth_key": DEEPL_KEY, "text": text,
        "source_lang": src_lang, "target_lang": lang.upper(),
    }).encode()
    req = urllib.request.Request("https://api-free.deepl.com/v2/translate", data=body)
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return resp["translations"][0]["text"]
    except Exception as e:
        print(f"  ! DeepL ({lang}) не ответил: {e}")
        return None


def mymemory_translate(text, lang):
    q = urllib.parse.quote(text[:450])
    url = f"https://api.mymemory.translated.net/get?q={q}&langpair=en|{lang}"
    if MM_EMAIL:
        url += "&de=" + urllib.parse.quote(MM_EMAIL)
    try:
        resp = json.loads(urllib.request.urlopen(url, timeout=30).read())
    except Exception as e:
        print(f"  ! MyMemory ({lang}) не ответил: {e}")
        time.sleep(1)
        return None
    if resp.get("responseStatus") != 200:
        print(f"  ! MyMemory ({lang}) лимит/ошибка {resp.get('responseStatus')}: "
              f"{resp.get('responseDetails', '')[:100]}")
        time.sleep(1)
        return None
    time.sleep(0.35)  # бережём бесплатный лимит
    return resp["responseData"]["translatedText"]


def translate(text, lang, cache):
    if not text.strip():
        return ""
    k = cache_key(lang, text)
    if k in cache:
        return cache[k]["text"]
    out = deepl_translate(text, lang)
    if out is None:
        out = mymemory_translate(text, lang)
    if out:
        cache[k] = {"lang": lang, "src": text, "text": out}
    return out or ""


# ---------- сборка ----------
def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main():
    if not SHEET_ID:
        fail("не задана переменная окружения SHEET_ID")
    base = json.load(open(os.path.join(BASE_DIR, "..", BASE_FILE), encoding="utf-8"))
    BASE_LEN = len(base["ru"])
    cache_path = os.path.join(BASE_DIR, "..", CACHE_FILE)
    cache = {}
    if os.path.exists(cache_path):
        cache = json.load(open(cache_path, encoding="utf-8")).get("entries", {})

    cols, table = fetch_sheet(SHEET_ID, SHEET_GID)
    idx = {
        "cat":  col_index(cols, "категор"),
        "ru_n": col_index(cols, "название", "ru"),
        "ru_d": col_index(cols, "описание", "ru"),
        "en_n": col_index(cols, "name", "en"),
        "en_d": col_index(cols, "description", "en"),
        "tag":  col_index(cols, "аллерген"),
    }
    for lang, word in [("ar", "arabic"), ("de", "german"), ("fr", "french"),
                       ("it", "italian"), ("es", "spanish")]:
        idx[lang + "_n"] = col_index(cols, "name", word)
        idx[lang + "_d"] = col_index(cols, "description", word)
    for k, i in idx.items():
        if i < 0:
            fail(f"не нашёл колонку для {k!r} в заголовках: {cols}")

    def cell(row, i):
        v = row[i] if i < len(row) else None
        return "" if v is None else str(v).strip()

    dishes, last_cat = [], ""
    for row in table:
        cat = cell(row, idx["cat"]) or last_cat
        last_cat = cat
        d = {"cat": cat,
             "ru_n": cell(row, idx["ru_n"]), "ru_d": cell(row, idx["ru_d"]),
             "en_n": cell(row, idx["en_n"]), "en_d": cell(row, idx["en_d"]),
             "tag": cell(row, idx["tag"]).upper()}
        for lang in TARGET_LANGS:
            d[lang + "_n"] = cell(row, idx[lang + "_n"])
            d[lang + "_d"] = cell(row, idx[lang + "_d"])
        if not (d["ru_n"] or d["en_n"]):
            continue
        dishes.append(d)
    if not dishes:
        fail("в таблице нет ни одного блюда (пустые колонки названий?)")
    N = len(dishes)

    def pick(lang, field, i, fallback):
        if lang == "ru":
            v = dishes[i]["ru_" + field]
        elif lang == "en":
            v = dishes[i]["en_" + field]
        else:
            v = dishes[i][lang + "_" + field]  # ручной перевод имеет приоритет
            if not v:
                src = dishes[i]["en_" + field] or dishes[i]["ru_" + field]
                if src:
                    v = translate(src, lang, cache)
        return v or fallback

    CATS, cat_sec, cat_order = [], {}, []
    for i, d in enumerate(dishes):
        if d["cat"] not in cat_sec:
            cat_order.append(d["cat"])
            if len(cat_order) > 4:
                fail(f"больше 4 категорий, а слотов в i18n только 4: {d['cat']}")
            cat_sec[d["cat"]] = len(cat_order)  # слоты 1..4
        dish = {"id": slug(d["en_n"] or d["ru_n"]) or f"dish-{i}", "n": BASE_LEN + i}
        has_desc = any(pick(l, "d", i, "") for l in ["ru", "en"] + TARGET_LANGS)
        if has_desc:
            dish["d"] = BASE_LEN + N + i
        if d["tag"] in TAG_INDEX:
            dish["tags"] = [TAG_INDEX[d["tag"]]]
        if dish["id"] in OPT_TEMPLATES:
            dish["opt"] = OPT_TEMPLATES[dish["id"]]
        if CATS and CATS[-1]["id"] == f"cat-{cat_sec[d['cat']]}":
            CATS[-1]["dishes"].append(dish)
        else:
            CATS.append({"id": f"cat-{cat_sec[d['cat']]}",
                         "sec": cat_sec[d["cat"]], "dishes": [dish]})

    I18N = {}
    for lang in ["ru", "en"] + TARGET_LANGS:
        arr = list(base[lang]) + [""] * (BASE_LEN + 2 * N - len(base[lang]))
        for i in range(N):
            arr[BASE_LEN + i] = pick(lang, "n", i, base[lang][31 + i])
            if any(pick(l, "d", i, "") for l in ["ru", "en"] + TARGET_LANGS):
                arr[BASE_LEN + N + i] = pick(lang, "d", i, base[lang][45 + i])
        if lang == "ru":
            for cname, sec in cat_sec.items():
                arr[sec] = cname
        I18N[lang] = arr

    out = ("/* АВТОГЕНЕРАЦИЯ: не править руками. Источник данных — Google Таблица,\n"
           "   сборка: .github/workflows/update-menu.yml (tools/build_menu_data.py). */\n"
           "window.MENU = " + json.dumps({"CATS": CATS, "I18N": I18N},
                                         ensure_ascii=False, indent=1) + ";\n")
    out_path = os.path.join(BASE_DIR, "..", OUT_FILE)
    old = open(out_path, encoding="utf-8").read() if os.path.exists(out_path) else None
    if old != out:
        open(out_path, "w", encoding="utf-8").write(out)
        print(f"{OUT_FILE} обновлён: {N} блюд, {len(cat_order)} категорий.")
    else:
        print(f"{OUT_FILE} без изменений ({N} блюд).")
    json.dump({"version": 1, "entries": cache},
              open(cache_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
