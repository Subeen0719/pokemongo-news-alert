# -*- coding: utf-8 -*-
"""
포켓몬고 새 소식 알림기
- pokemongo.com 공지(뉴스) 페이지와 X(@PokemonGoApp) RSS를 확인해
  새 게시물이 있으면 Pushover로 휴대폰 팝업 알림을 보냅니다.
"""

import json
import os
import pathlib
import re
import sys

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# 설정 (여기만 바꾸면 됩니다)
# ─────────────────────────────────────────────

# 감시할 공지 페이지 (한국어). 영어로 받고 싶으면 /en/news 로 변경
NEWS_URL = "https://pokemongo.com/ko/news"

# 알림 제목 (고정)
NOTI_TITLE = "[포켓몬고] 새 소식이 게시되었습니다."

# 한 번에 보낼 수 있는 최대 알림 개수 (한꺼번에 쏟아지는 것 방지)
MAX_NOTI_PER_RUN = 5

# 기억해 둘 게시물 개수 (오래된 것은 버림)
MAX_MEMORY = 300

# ─────────────────────────────────────────────
# 아래부터는 수정할 필요 없음
# ─────────────────────────────────────────────

STATE_FILE = pathlib.Path("seen.json")
PUSHOVER_API = "https://api.pushover.net/1/messages.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
X_RSS_URL = os.environ.get("X_RSS_URL", "").strip()  # 비워두면 X 감시는 건너뜀


def load_state():
    """지금까지 알림을 보낸 게시물 목록을 불러온다."""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return {
                "web": list(data.get("web", [])),
                "x": list(data.get("x", [])),
                "initialized": bool(data.get("initialized", False)),
            }
        except json.JSONDecodeError:
            print("[경고] seen.json 을 읽을 수 없어 새로 만듭니다.")
    return {"web": [], "x": [], "initialized": False}


def save_state(state):
    state["web"] = state["web"][:MAX_MEMORY]
    state["x"] = state["x"][:MAX_MEMORY]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_web_posts():
    """pokemongo.com 공지 페이지에서 게시물 목록을 긁어온다."""
    res = requests.get(NEWS_URL, headers=HEADERS, timeout=20)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    posts = []
    already = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # 개별 게시물 주소만 고름 (예: /news/staraptor-super-mega-raid-day-2026)
        if not re.search(r"/news/[^/?#]+$", href):
            continue

        title = a.get_text(strip=True)
        if not title:
            continue

        url = href if href.startswith("http") else "https://pokemongo.com" + href
        if url in already:
            continue
        already.add(url)

        posts.append({"id": url, "title": title, "url": url, "source": "웹사이트"})

    return posts


def fetch_x_posts():
    """X(@PokemonGoApp) RSS 주소에서 게시물 목록을 가져온다."""
    if not X_RSS_URL:
        return []

    try:
        res = requests.get(X_RSS_URL, headers=HEADERS, timeout=20)
        res.raise_for_status()
    except requests.RequestException as e:
        print(f"[경고] X RSS 를 가져오지 못했습니다: {e}")
        return []

    soup = BeautifulSoup(res.text, "xml")
    posts = []

    for item in soup.find_all("item"):
        link_tag = item.find("link")
        title_tag = item.find("title")
        if link_tag is None:
            continue

        link = link_tag.get_text(strip=True)
        if not link:
            continue

        title = title_tag.get_text(strip=True) if title_tag else "(제목 없음)"
        title = re.sub(r"\s+", " ", title)
        if len(title) > 120:
            title = title[:117] + "..."

        posts.append({"id": link, "title": title, "url": link, "source": "X(구 트위터)"})

    return posts


def send_pushover(post):
    """Pushover로 알림 1건 발송."""
    message = (
        f"{post['title']} 및 주소: {post['url']}"
    )

    payload = {
        "token": PUSHOVER_TOKEN,
        "user": PUSHOVER_USER,
        "title": NOTI_TITLE,
        "message": message,
        "url": post["url"],
        "url_title": "게시물 열어보기",
        "priority": 0,
    }

    res = requests.post(PUSHOVER_API, data=payload, timeout=20)
    if res.status_code != 200:
        print(f"[오류] 알림 발송 실패 ({res.status_code}): {res.text}")
        return False

    print(f"[발송] {post['source']} | {post['title']}")
    return True


def process(kind, posts, state, first_run):
    """새 게시물만 골라 알림을 보내고 기록한다."""
    known = set(state[kind])
    new_posts = [p for p in posts if p["id"] not in known]

    if not new_posts:
        print(f"[{kind}] 새 게시물 없음")
        return 0

    if first_run:
        # 최초 실행: 과거 글이 한꺼번에 오는 것을 막기 위해 기록만 하고 발송하지 않음
        state[kind] = [p["id"] for p in posts] + state[kind]
        print(f"[{kind}] 최초 실행 - 기존 게시물 {len(new_posts)}건을 기록만 했습니다.")
        return 0

    sent = 0
    for post in new_posts[:MAX_NOTI_PER_RUN]:
        if send_pushover(post):
            state[kind].insert(0, post["id"])
            sent += 1

    # 발송 한도를 넘긴 나머지도 중복 방지를 위해 기록
    for post in new_posts[MAX_NOTI_PER_RUN:]:
        state[kind].insert(0, post["id"])

    return sent


def main():
    if not PUSHOVER_TOKEN or not PUSHOVER_USER:
        print("[중단] PUSHOVER_TOKEN 또는 PUSHOVER_USER 가 설정되지 않았습니다.")
        sys.exit(1)

    state = load_state()
    first_run = not state["initialized"]

    total = 0

    try:
        total += process("web", fetch_web_posts(), state, first_run)
    except Exception as e:
        print(f"[오류] 웹사이트 확인 실패: {e}")

    try:
        total += process("x", fetch_x_posts(), state, first_run)
    except Exception as e:
        print(f"[오류] X 확인 실패: {e}")

    state["initialized"] = True
    save_state(state)

    print(f"완료. 이번에 보낸 알림: {total}건")


if __name__ == "__main__":
    main()
