# -*- coding: utf-8 -*-
"""
네이버 부동산 오피스텔/월세 매물 수집기
- 8개 구를 돌며 매물을 받아 조건으로 거르고
- 처음 보는(새) 매물만 텔레그램으로 보내고
- 전체 결과를 docs/index.html(웹페이지)와 docs/data.json으로 저장합니다.
GitHub Actions에서 3일마다 자동 실행됩니다.
"""
import os, re, json, time, html, datetime, sys
import requests
import config

SEEN_FILE = "seen.json"          # 이미 알림 보낸 매물번호 기록
DATA_FILE = "docs/data.json"     # 웹페이지가 읽는 현재 매물 목록
HTML_FILE = "docs/index.html"    # 웹 대시보드

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")
HEADERS = {"User-Agent": UA, "Referer": "https://m.land.naver.com/",
           "Accept": "application/json"}

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def log(*a):
    print(*a, flush=True)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ───────── 1. 네이버에서 한 구의 매물 목록 받기 ─────────
def fetch_district(d):
    items = []
    for page in range(1, config.MAX_PAGES_PER_DISTRICT + 1):
        params = {
            "rletTpCd": config.REAL_ESTATE_TYPE,
            "tradTpCd": config.TRADE_TYPE,
            "z": "13", "lat": d["lat"], "lon": d["lon"],
            "btm": d["btm"], "lft": d["lft"], "top": d["top"], "rgt": d["rgt"],
            "cortarNo": d["cortarNo"], "page": page,
        }
        try:
            r = requests.get("https://m.land.naver.com/cluster/ajax/articleList",
                             params=params, headers=HEADERS, timeout=20)
            data = r.json()
        except Exception as e:
            log(f"  [{d['name']}] {page}p 요청 실패: {e}")
            break
        body = data.get("body") or []
        if not body:
            break
        items.extend(body)
        if not data.get("more"):
            break
        time.sleep(config.REQUEST_DELAY_SEC)
    log(f"  [{d['name']}] 원본 {len(items)}건 수집")
    return items


# ───────── 2. 가격 조건으로 거르기 ─────────
def parse_money(text):
    """'1,500' 또는 '1억 500' 같은 표기를 만원 정수로 변환"""
    if text is None:
        return None
    text = str(text).replace(",", "").strip()
    if text in ("", "-"):
        return None
    man = 0
    m = re.search(r"(\d+)\s*억", text)
    if m:
        man += int(m.group(1)) * 10000
        text = re.sub(r"\d+\s*억", "", text)
    m = re.search(r"(\d+)", text)
    if m:
        man += int(m.group(1))
    return man


def passes_price(a):
    deposit = parse_money(a.get("prc"))        # 보증금
    rent = parse_money(a.get("rentPrc"))       # 월세
    if deposit is None or rent is None:
        return False, deposit, rent
    ok = (config.DEPOSIT_MIN <= deposit <= config.DEPOSIT_MAX
          and rent <= config.RENT_MAX)
    return ok, deposit, rent


# ───────── 3. 상세에서 사용승인연도(건물 연식) 확인 ─────────
def fetch_build_year(atcl_no):
    try:
        r = requests.get(f"https://m.land.naver.com/article/info/{atcl_no}",
                         headers={"User-Agent": UA}, timeout=20)
        txt = r.text
        # 사용승인일 또는 준공 연도 패턴 탐색
        for pat in [r"사용승인[^0-9]{0,6}(\d{4})", r"준공[^0-9]{0,6}(\d{4})",
                    r"useAprvYmd['\"]?\s*[:=]\s*['\"]?(\d{4})"]:
            m = re.search(pat, txt)
            if m:
                return int(m.group(1))
    except Exception as e:
        log(f"    상세 조회 실패({atcl_no}): {e}")
    return None


# ───────── 4. 텔레그램 발송 ─────────
def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("  텔레그램 토큰/챗ID 없음 — 발송 생략")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                            "parse_mode": "HTML", "disable_web_page_preview": "false"},
                      timeout=20)
    except Exception as e:
        log(f"  텔레그램 발송 실패: {e}")


# ───────── 5. 웹페이지(index.html) 생성 ─────────
def build_html(listings, updated_at):
    cards = ""
    for x in listings:
        yr = x["buildYear"] or "연식 확인필요"
        cards += f"""
        <div class="card">
          <div class="tag">{html.escape(x['district'])}</div>
          <h3>{html.escape(x['name'])}</h3>
          <p class="price">보증금 <b>{x['deposit']:,}</b>만 / 월 <b>{x['rent']:,}</b>만</p>
          <p class="meta">🏗️ {yr} · {html.escape(x['floor'])} · {html.escape(x['area'])}</p>
          <a href="{html.escape(x['link'])}" target="_blank">네이버에서 보기 →</a>
        </div>"""
    if not cards:
        cards = '<p style="color:#888">조건에 맞는 매물이 아직 없습니다.</p>'
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>내 오피스텔 매물 알림</title>
<style>
 body{{font-family:-apple-system,'Malgun Gothic',sans-serif;background:#f5f6f8;margin:0;padding:20px;color:#222}}
 h1{{font-size:20px}} .upd{{color:#888;font-size:13px;margin-bottom:16px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}}
 .card{{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
 .tag{{display:inline-block;background:#e8f0fe;color:#1a73e8;font-size:12px;padding:2px 8px;border-radius:8px}}
 .card h3{{margin:8px 0 6px;font-size:16px}} .price{{font-size:15px;margin:4px 0}}
 .price b{{color:#1a73e8}} .meta{{color:#666;font-size:13px;margin:4px 0}}
 .card a{{font-size:13px;color:#1a73e8;text-decoration:none}}
</style></head><body>
<h1>🏢 내 오피스텔 · 월세 매물 ({len(listings)}건)</h1>
<div class="upd">마지막 업데이트: {updated_at} · 3일마다 자동 갱신</div>
<div class="grid">{cards}</div>
</body></html>"""


# ───────── 메인 ─────────
def main():
    log("=== 수집 시작 ===")
    seen = set(load_json(SEEN_FILE, []))
    all_listings = []
    candidates = []

    for d in config.DISTRICTS:
        for a in fetch_district(d):
            ok, deposit, rent = passes_price(a)
            if not ok:
                continue
            atcl_no = str(a.get("atclNo") or a.get("atclNm", ""))
            candidates.append({
                "atclNo": atcl_no, "district": d["name"],
                "name": a.get("atclNm", "오피스텔"),
                "deposit": deposit, "rent": rent,
                "floor": a.get("flrInfo", "-"),
                "area": (a.get("spc2") and f"{a.get('spc2')}㎡") or "-",
                "link": f"https://m.land.naver.com/article/info/{atcl_no}",
            })
        time.sleep(config.REQUEST_DELAY_SEC)

    log(f"가격조건 통과 {len(candidates)}건 — 연식 확인 중")
    for c in candidates:
        yr = fetch_build_year(c["atclNo"])
        time.sleep(config.REQUEST_DELAY_SEC)
        if yr is None:
            if not config.INCLUDE_UNKNOWN_BUILD_YEAR:
                continue
            c["buildYear"] = None
        else:
            if yr < config.BUILD_YEAR_MIN:
                continue
            c["buildYear"] = yr
        all_listings.append(c)

    log(f"최종 조건 통과 {len(all_listings)}건")

    # 새 매물만 추려 텔레그램 발송
    new_ones = [x for x in all_listings if x["atclNo"] not in seen]
    log(f"이 중 새 매물 {len(new_ones)}건")

    if new_ones:
        for x in new_ones:
            yr = x["buildYear"] or "연식 확인필요"
            msg = (f"🏢 [{x['district']}] {html.escape(x['name'])}\n"
                   f"💰 보증금 {x['deposit']:,}만 / 월 {x['rent']:,}만\n"
                   f"🏗️ {yr} · {x['floor']} · {x['area']}\n"
                   f"🔗 {x['link']}")
            send_telegram(msg)
            time.sleep(0.5)
    elif config.SEND_WHEN_NO_NEW:
        send_telegram("이번 점검에서는 새 매물이 없습니다.")

    # 기록 갱신
    for x in all_listings:
        seen.add(x["atclNo"])
    save_json(SEEN_FILE, sorted(seen))

    updated_at = (datetime.datetime.utcnow() + datetime.timedelta(hours=9)
                  ).strftime("%Y-%m-%d %H:%M") + " (KST)"
    save_json(DATA_FILE, {"updated": updated_at, "listings": all_listings})
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(build_html(all_listings, updated_at))
    log("=== 완료 ===")


if __name__ == "__main__":
    main()
