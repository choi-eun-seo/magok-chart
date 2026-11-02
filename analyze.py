#!/usr/bin/env python3
"""Parse magok-prod.sql and generate a self-contained HTML dashboard."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

SQL_PATH = Path("/Users/choieunseo/jejodo/html/magok/magok-prod.sql")
OUT_PATH = Path("/Users/choieunseo/jejodo/html/magok/dashboard.html")

KST = timezone(timedelta(hours=9))
UTC = timezone.utc

STAFF_IDS = set()
EXCLUDE_USER_IDS = {"developer"}

HISTORY_LABELS = {
    "yoga_program_participation": "요가 프로그램 참여",
    "art_class_participation": "아트 클래스 참여",
    "art_class_bonus": "아트 클래스 보너스",
    "art_class_reward-0": "아트 클래스 리워드 0회차",
    "art_class_reward-1": "아트 클래스 리워드 1회차",
    "art_class_reward-2": "아트 클래스 리워드 2회차",
    "art_class_reward-3": "아트 클래스 리워드 3회차",
    "yoga_bonus": "요가 보너스",
    "night_viewing_bonus": "심야 영화관 보너스",
    "night_makgoo_tour": "나이트 마곡 투어",
    "day_makgoo_tour": "데이 마곡 투어",
    "snap_makgoo_tour": "스냅 마곡 투어",
    "espresso_run": "에스프레소런",
    "pleemarket_receipt_verification": "플리마켓 영수증 인증",
    "festival_stamp_tour_completion": "스탬프 투어 완주",
    "sns_reward": "SNS 리워드",
    "sns_follow_reward": "SNS 팔로우 리워드",
    "night_cinema_survey_reward": "심야영화 설문 리워드",
    "company": "직장인(기업) 쿠폰",
}

PROGRAM_TYPE_LABELS = {
    "specialty": "스페셜티",
    "tour": "투어",
    "event": "이벤트",
}

RESERVATION_STATUS_LABELS = {
    "pending": "대기",
    "confirmed": "확정",
    "checked_in": "체크인",
    "canceled": "취소",
    "refund_completed": "환급완료",
    "refund_rejected": "환급거절",
    "rejected": "거절",
}

# 실참여는 상태값이 아니라 현장 체크인 시각 기준.
# refund_completed 중에는 미참석 후 예약금만 환불된 건이 포함됨.
CANCELED_STATUSES = {"canceled", "rejected"}
APPLIED_STATUSES = {
    "pending",
    "confirmed",
    "checked_in",
    "refund_completed",
    "refund_rejected",
}


def unescape_sql_string(s: str) -> str:
    return (
        s.replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace("\\'", "'")
        .replace('\\\\', "\\")
        .replace("''", "'")
    )


def parse_sql_rows(values_blob: str):
    """Parse MySQL VALUES tuples. Handles quoted strings spanning lines."""
    rows = []
    i = 0
    n = len(values_blob)
    while i < n:
        while i < n and values_blob[i] in " \t\r\n,":
            i += 1
        if i >= n:
            break
        if values_blob[i] == ";":
            break
        if values_blob[i] != "(":
            i += 1
            continue
        i += 1
        row = []
        while i < n:
            while i < n and values_blob[i] in " \t\r\n":
                i += 1
            if i >= n:
                break
            ch = values_blob[i]
            if ch == ")":
                i += 1
                rows.append(row)
                break
            if ch == ",":
                i += 1
                continue
            if ch == "'":
                i += 1
                buf = []
                while i < n:
                    c = values_blob[i]
                    if c == "\\" and i + 1 < n:
                        buf.append(c)
                        buf.append(values_blob[i + 1])
                        i += 2
                        continue
                    if c == "'":
                        if i + 1 < n and values_blob[i + 1] == "'":
                            buf.append("''")
                            i += 2
                            continue
                        i += 1
                        break
                    buf.append(c)
                    i += 1
                row.append(unescape_sql_string("".join(buf)))
            else:
                j = i
                while j < n and values_blob[j] not in ",)":
                    j += 1
                token = values_blob[i:j].strip()
                i = j
                if token.upper() == "NULL":
                    row.append(None)
                else:
                    row.append(token)
    return rows


def extract_inserts(content: str, table: str):
    marker = f"INSERT INTO `{table}`"
    rows = []
    start = 0
    while True:
        idx = content.find(marker, start)
        if idx < 0:
            break
        values_idx = content.find("VALUES", idx)
        if values_idx < 0:
            break
        # find matching semicolon not inside a string
        i = values_idx + 6
        in_str = False
        while i < len(content):
            c = content[i]
            if in_str:
                if c == "\\" and i + 1 < len(content):
                    i += 2
                    continue
                if c == "'":
                    if i + 1 < len(content) and content[i + 1] == "'":
                        i += 2
                        continue
                    in_str = False
                i += 1
                continue
            if c == "'":
                in_str = True
                i += 1
                continue
            if c == ";":
                blob = content[values_idx + 6 : i]
                rows.extend(parse_sql_rows(blob))
                start = i + 1
                break
            i += 1
        else:
            break
    return rows


def parse_dt(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def to_kst_date(dt):
    if not dt:
        return None
    return dt.astimezone(KST).date().isoformat()


def pct(n, d):
    if not d:
        return 0.0
    return round(n * 100.0 / d, 1)


def counter_to_list(counter: Counter, order=None):
    if order:
        items = [(k, counter.get(k, 0)) for k in order if counter.get(k, 0)]
        rest = [(k, v) for k, v in counter.most_common() if k not in order]
        return items + rest
    return list(counter.most_common())


def js(obj):
    return json.dumps(obj, ensure_ascii=False)


def load_tables(content: str):
    print("Parsing tables...")
    tables = {}
    for name in [
        "User",
        "UserMeta",
        "Coupon",
        "CouponVoucher",
        "CouponVoucherHistory",
        "Reservation",
        "Program",
        "ProgramSession",
        "Shop",
        "ShopMeta",
        "PurchaseVerification",
        "Ticket",
        "CouponMeta",
    ]:
        rows = extract_inserts(content, name)
        tables[name] = rows
        print(f"  {name}: {len(rows)}")
    return tables


def analyze(tables):
    users = {}
    for r in tables["User"]:
        uid, name, phone, nickname, created, updated, deleted = r[:7]
        users[uid] = {
            "id": uid,
            "name": name,
            "createdAt": parse_dt(created),
            "deletedAt": parse_dt(deleted),
            "meta": {},
        }

    for r in tables["UserMeta"]:
        _id, key, value, _type, _c, _u, deleted, user_id = r[:8]
        if deleted:
            continue
        if user_id not in users:
            continue
        if key.startswith("oauth:"):
            users[user_id]["meta"]["oauth"] = key.split(":", 1)[1]
            continue
        users[user_id]["meta"][key] = value

    for uid, u in users.items():
        if u["meta"].get("role") in {"staff", "admin"}:
            STAFF_IDS.add(uid)
            EXCLUDE_USER_IDS.add(uid)
        if u["name"] in {"magok_admin"}:
            EXCLUDE_USER_IDS.add(uid)

    real_users = {
        uid: u
        for uid, u in users.items()
        if uid not in EXCLUDE_USER_IDS and not u["deletedAt"]
    }

    shops = {r[0]: {"id": r[0], "name": r[1], "type": "미분류"} for r in tables["Shop"]}
    for r in tables["ShopMeta"]:
        _id, key, value, _type, _c, _u, deleted, shop_id = r[:8]
        if deleted or shop_id not in shops:
            continue
        if key == "type":
            shops[shop_id]["type"] = value or "미분류"

    programs = {
        r[0]: {"id": r[0], "name": r[1], "type": r[2]} for r in tables["Program"]
    }
    sessions = {}
    for r in tables["ProgramSession"]:
        sid, order, start_at, capacity, _c, _u, deleted, program_id, _data = r[:9]
        sessions[sid] = {
            "id": sid,
            "programId": program_id,
            "programName": programs.get(program_id, {}).get("name", "알 수 없음"),
            "programType": programs.get(program_id, {}).get("type", ""),
            "startAt": parse_dt(start_at),
            "capacity": int(capacity) if capacity else 0,
            "deleted": bool(deleted),
        }

    # reservations
    reservations = []
    for r in tables["Reservation"]:
        (
            rid,
            status,
            check_in,
            memo,
            sns,
            created,
            updated,
            deleted,
            session_id,
            user_id,
            *_rest,
        ) = r
        canceled = r[14] if len(r) > 14 else None
        reservations.append(
            {
                "id": rid,
                "status": status,
                "checkInAt": parse_dt(check_in),
                "createdAt": parse_dt(created),
                "deletedAt": parse_dt(deleted),
                "canceledAt": parse_dt(canceled),
                "sessionId": session_id,
                "userId": user_id,
                "hasSns": bool(sns),
            }
        )

    # coupons
    coupons = []
    for r in tables["Coupon"]:
        (
            cid,
            amount,
            ctype,
            issued,
            expired,
            used,
            canceled,
            created,
            updated,
            deleted,
            voucher_id,
            shop_id,
            user_id,
        ) = r[:13]
        coupons.append(
            {
                "id": cid,
                "amount": int(amount) if amount else 0,
                "type": ctype,
                "issuedAt": parse_dt(issued),
                "expiredAt": parse_dt(expired),
                "usedAt": parse_dt(used),
                "canceledAt": parse_dt(canceled),
                "deletedAt": parse_dt(deleted),
                "voucherId": voucher_id,
                "shopId": shop_id,
                "userId": user_id,
            }
        )

    vouchers = {r[0]: {"id": r[0], "userId": r[7]} for r in tables["CouponVoucher"]}
    voucher_history = defaultdict(list)
    for r in tables["CouponVoucherHistory"]:
        hid, history, created, updated, deleted, voucher_id, status = r[:7]
        voucher_history[voucher_id].append(
            {"history": history, "status": status, "deleted": bool(deleted)}
        )

    # tickets
    tickets = []
    for r in tables["Ticket"]:
        tid, order, issued, used, expired, created, updated, deleted, user_id = r[:9]
        tickets.append(
            {
                "id": tid,
                "issuedAt": parse_dt(issued),
                "usedAt": parse_dt(used),
                "deletedAt": parse_dt(deleted),
                "userId": user_id,
            }
        )

    verifications = []
    for r in tables["PurchaseVerification"]:
        (
            vid,
            status,
            verified,
            rejected,
            reason,
            created,
            updated,
            deleted,
            file_id,
            user_id,
            ticket_id,
            shop_id,
        ) = r[:12]
        verifications.append(
            {
                "status": status,
                "deletedAt": parse_dt(deleted),
                "userId": user_id,
                "shopId": shop_id,
            }
        )

    # helper
    def is_real(uid):
        return uid in real_users

    def meta(uid, key, default="미입력"):
        u = real_users.get(uid)
        if not u:
            return default
        return u["meta"].get(key) or default

    def parse_routes(raw):
        if not raw or raw == "미입력":
            return ["미입력"]
        cleaned = raw.replace('\\"', '"').replace("\\'", "'").strip()
        try:
            arr = json.loads(cleaned)
            if isinstance(arr, list):
                names = [str(x).strip() for x in arr if str(x).strip()]
                return names or ["미입력"]
            if isinstance(arr, str) and arr.strip():
                return [arr.strip()]
        except Exception:
            pass
        stripped = cleaned.strip("[]").replace('"', "").replace("'", "")
        parts = [p.strip() for p in stripped.split(",") if p.strip()]
        return parts or ["미입력"]

    # --- users ---
    gender = Counter()
    age = Counter()
    job = Counter()
    area = Counter()
    provider = Counter()
    company = Counter()
    routes = Counter()
    signup_by_day = Counter()
    office_worker_ids = set()

    for uid, u in real_users.items():
        g = u["meta"].get("gender") or "미입력"
        a = u["meta"].get("ageGroup") or "미입력"
        j = u["meta"].get("job") or "미입력"
        ar = u["meta"].get("residentArea") or "미입력"
        p = u["meta"].get("provider") or "미입력"
        gender[g] += 1
        age[a] += 1
        job[j] += 1
        area[ar] += 1
        provider[p] += 1
        if j == "직장인":
            office_worker_ids.add(uid)
        cn = u["meta"].get("companyName")
        if cn:
            company[cn.strip()] += 1
        for rt in parse_routes(u["meta"].get("awarenessRoute")):
            routes[rt] += 1
        d = to_kst_date(u["createdAt"])
        if d:
            signup_by_day[d] += 1

    # --- reservations ---
    live_reservations = [
        r for r in reservations if not r["deletedAt"] and is_real(r["userId"])
    ]
    res_status = Counter(r["status"] for r in live_reservations)
    participant_ids = set()
    applicant_ids = set()
    attended_res = []
    applied_res = []
    for r in live_reservations:
        if r["status"] in APPLIED_STATUSES:
            applicant_ids.add(r["userId"])
            applied_res.append(r)
        if r["checkInAt"]:
            participant_ids.add(r["userId"])
            attended_res.append(r)

    by_program = defaultdict(
        lambda: {
            "name": "",
            "type": "",
            "applied": 0,
            "attended": 0,
            "canceled": 0,
            "users_applied": set(),
            "users_attended": set(),
            "capacity": 0,
        }
    )
    by_session = defaultdict(
        lambda: {
            "applied": 0,
            "attended": 0,
            "canceled": 0,
            "users_applied": set(),
            "users_attended": set(),
        }
    )
    for r in live_reservations:
        sess = sessions.get(r["sessionId"], {})
        pname = sess.get("programName", "알 수 없음")
        rec = by_program[sess.get("programId") or pname]
        rec["name"] = pname
        rec["type"] = sess.get("programType", "")
        rec["capacity"] += 0
        srec = by_session[r["sessionId"]]
        if r["status"] in CANCELED_STATUSES:
            rec["canceled"] += 1
            srec["canceled"] += 1
        if r["status"] in APPLIED_STATUSES:
            rec["applied"] += 1
            rec["users_applied"].add(r["userId"])
            srec["applied"] += 1
            srec["users_applied"].add(r["userId"])
        if r["checkInAt"]:
            rec["attended"] += 1
            rec["users_attended"].add(r["userId"])
            srec["attended"] += 1
            srec["users_attended"].add(r["userId"])

    # session capacity fill
    session_rows = []
    for sid, sess in sessions.items():
        if sess["deleted"]:
            continue
        srec = by_session[sid]
        session_rows.append(
            {
                "program": sess["programName"],
                "start": to_kst_date(sess["startAt"]) or "",
                "capacity": sess["capacity"],
                "applied": srec["applied"],
                "attended": srec["attended"],
                "canceled": srec["canceled"],
                "fill": pct(srec["applied"], sess["capacity"]),
                "attendRate": pct(srec["attended"], srec["applied"]),
            }
        )
    session_rows.sort(key=lambda x: (x["start"], x["program"]))

    program_rows = []
    for pid, rec in by_program.items():
        if not rec["name"]:
            continue
        program_rows.append(
            {
                "name": rec["name"],
                "type": PROGRAM_TYPE_LABELS.get(rec["type"], rec["type"] or "-"),
                "applied": rec["applied"],
                "appliedUsers": len(rec["users_applied"]),
                "attended": rec["attended"],
                "attendedUsers": len(rec["users_attended"]),
                "canceled": rec["canceled"],
                "attendRate": pct(rec["attended"], rec["applied"]),
            }
        )
    program_rows.sort(key=lambda x: -x["attended"])

    # --- coupons ---
    live_coupons = [
        c
        for c in coupons
        if not c["deletedAt"] and is_real(c["userId"])
    ]
    coupon_used = [c for c in live_coupons if c["usedAt"] and not c["canceledAt"]]
    coupon_canceled = [c for c in live_coupons if c["canceledAt"]]
    coupon_unused = [
        c for c in live_coupons if not c["usedAt"] and not c["canceledAt"]
    ]
    used_user_ids = {c["userId"] for c in coupon_used}
    issued_user_ids = {c["userId"] for c in live_coupons}

    used_amount = sum(c["amount"] for c in coupon_used)
    issued_amount = sum(c["amount"] for c in live_coupons)
    canceled_amount = sum(c["amount"] for c in coupon_canceled)

    coupon_by_day = Counter()
    issued_by_day = Counter()
    for c in live_coupons:
        d = to_kst_date(c["issuedAt"])
        if d:
            issued_by_day[d] += 1
    for c in coupon_used:
        d = to_kst_date(c["usedAt"])
        if d:
            coupon_by_day[d] += 1

    shop_used = Counter()
    shop_type_used = Counter()
    for c in coupon_used:
        shop = shops.get(c["shopId"])
        name = shop["name"] if shop else "상점 미지정"
        shop_used[name] += 1
        st = shop["type"] if shop else "미분류"
        shop_type_used[st] += 1

    # voucher reasons
    voucher_reason = {}
    for vid, histories in voucher_history.items():
        active = [h for h in histories if not h["deleted"]]
        if active:
            voucher_reason[vid] = active[0]["history"]

    reason_issued = Counter()
    reason_used = Counter()
    reason_used_users = defaultdict(set)
    reason_issued_users = defaultdict(set)
    for c in live_coupons:
        reason = voucher_reason.get(c["voucherId"], "직접발급/기타")
        label = HISTORY_LABELS.get(reason, reason)
        reason_issued[label] += 1
        reason_issued_users[label].add(c["userId"])
        if c["usedAt"] and not c["canceledAt"]:
            reason_used[label] += 1
            reason_used_users[label].add(c["userId"])

    def demo_breakdown(user_ids, used_ids=None):
        g = Counter()
        a = Counter()
        j = Counter()
        for uid in user_ids:
            g[meta(uid, "gender")] += 1
            a[meta(uid, "ageGroup")] += 1
            j[meta(uid, "job")] += 1
        used_g = Counter()
        used_a = Counter()
        used_j = Counter()
        if used_ids is not None:
            for uid in user_ids:
                if uid in used_ids:
                    used_g[meta(uid, "gender")] += 1
                    used_a[meta(uid, "ageGroup")] += 1
                    used_j[meta(uid, "job")] += 1
        return {
            "gender": g,
            "age": a,
            "job": j,
            "used_gender": used_g,
            "used_age": used_a,
            "used_job": used_j,
        }

    all_demo = demo_breakdown(set(real_users), used_user_ids)
    office_demo = demo_breakdown(office_worker_ids, used_user_ids)
    participant_demo = demo_breakdown(participant_ids, used_user_ids)
    applicant_demo = demo_breakdown(applicant_ids, used_user_ids)

    def rate_rows(counter, used_counter, total_base=None):
        rows = []
        for k, v in counter.most_common():
            u = used_counter.get(k, 0)
            rows.append(
                {
                    "label": k,
                    "count": v,
                    "used": u,
                    "rate": pct(u, v),
                    "share": pct(v, total_base or sum(counter.values())),
                }
            )
        return rows

    # unique user coupon usage among demographic groups
    def user_use_rows(group_counter, used_ids, all_ids_by_group_fn=None, source_users=None):
        # group_counter is count of users in each group
        rows = []
        source = source_users if source_users is not None else real_users
        groups = defaultdict(set)
        for uid in source:
            if uid not in real_users:
                continue
            # can't know key; handled outside
        return rows

    def group_use(source_ids, key):
        groups = defaultdict(set)
        for uid in source_ids:
            groups[meta(uid, key)].add(uid)
        rows = []
        for label, ids in sorted(groups.items(), key=lambda x: -len(x[1])):
            used_n = len(ids & used_user_ids)
            issued_n = len(ids & issued_user_ids)
            rows.append(
                {
                    "label": label,
                    "users": len(ids),
                    "issuedUsers": issued_n,
                    "usedUsers": used_n,
                    "useRateOfGroup": pct(used_n, len(ids)),
                    "useRateOfIssued": pct(used_n, issued_n),
                }
            )
        return rows

    # tickets
    live_tickets = [
        t for t in tickets if not t["deletedAt"] and is_real(t["userId"])
    ]
    ticket_issued = [t for t in live_tickets if t["issuedAt"]]
    ticket_used = [t for t in live_tickets if t["usedAt"]]
    ticket_users = {t["userId"] for t in ticket_issued}
    ticket_used_users = {t["userId"] for t in ticket_used}

    live_verifs = [
        v for v in verifications if not v["deletedAt"] and is_real(v["userId"])
    ]
    verif_status = Counter(v["status"] for v in live_verifs)

    # overlap
    n_users = len(real_users)
    n_office = len(office_worker_ids)
    n_part = len(participant_ids)
    n_apply = len(applicant_ids)
    office_and_part = office_worker_ids & participant_ids
    office_used = office_worker_ids & used_user_ids
    part_used = participant_ids & used_user_ids
    apply_used = applicant_ids & used_user_ids
    office_part_used = office_and_part & used_user_ids
    neither_part = set(real_users) - participant_ids
    neither_part_used = neither_part & used_user_ids
    non_office_ids = set(real_users) - office_worker_ids
    non_office_used = non_office_ids & used_user_ids
    office_nonpart_ids = office_worker_ids - participant_ids
    office_nonpart_used = office_nonpart_ids & used_user_ids
    part_used_coupons = [c for c in coupon_used if c["userId"] in participant_ids]
    nonpart_used_coupons = [c for c in coupon_used if c["userId"] not in participant_ids]

    def ratio(a, b):
        if not b:
            return 0.0
        return round(a / b, 1)

    part_use_exact = (len(part_used) / n_part) if n_part else 0
    nonpart_use_exact = (len(neither_part_used) / len(neither_part)) if neither_part else 0
    office_use_exact = (len(office_used) / n_office) if n_office else 0
    nonoffice_use_exact = (len(non_office_used) / len(non_office_ids)) if non_office_ids else 0
    office_part_use_exact = (len(office_part_used) / len(office_and_part)) if office_and_part else 0
    office_nonpart_use_exact = (
        (len(office_nonpart_used) / len(office_nonpart_ids)) if office_nonpart_ids else 0
    )

    # gender x age heatmap
    age_order = ["10대", "20대", "30대", "40대", "50대", "60대 이상", "미입력"]
    gender_order = ["여성", "남성", "미입력"]
    heatmap = {g: {a: 0 for a in age_order} for g in gender_order}
    heatmap_used = {g: {a: 0 for a in age_order} for g in gender_order}
    heatmap_part = {g: {a: 0 for a in age_order} for g in gender_order}
    for uid, u in real_users.items():
        g = u["meta"].get("gender") or "미입력"
        a = u["meta"].get("ageGroup") or "미입력"
        if g not in heatmap:
            heatmap[g] = {aa: 0 for aa in age_order}
            heatmap_used[g] = {aa: 0 for aa in age_order}
            heatmap_part[g] = {aa: 0 for aa in age_order}
        if a not in heatmap[g]:
            for dct in (heatmap, heatmap_used, heatmap_part):
                dct[g][a] = 0
        heatmap[g][a] += 1
        if uid in used_user_ids:
            heatmap_used[g][a] += 1
        if uid in participant_ids:
            heatmap_part[g][a] += 1

    # coupon counts per user
    coupons_per_user = Counter()
    used_per_user = Counter()
    for c in live_coupons:
        coupons_per_user[c["userId"]] += 1
        if c["usedAt"] and not c["canceledAt"]:
            used_per_user[c["userId"]] += 1
    coupon_count_dist = Counter(coupons_per_user.values())
    used_count_dist = Counter(used_per_user.values())

    # weekday / hour of coupon use (KST)
    weekday_used = Counter()
    hour_used = Counter()
    wd_names = ["월", "화", "수", "목", "금", "토", "일"]
    for c in coupon_used:
        if c["usedAt"]:
            kst = c["usedAt"].astimezone(KST)
            weekday_used[wd_names[kst.weekday()]] += 1
            hour_used[kst.hour] += 1

    # program type of participants
    part_program_types = Counter()
    user_programs = defaultdict(set)
    for r in attended_res:
        sess = sessions.get(r["sessionId"], {})
        user_programs[r["userId"]].add(sess.get("programName", ""))
        t = PROGRAM_TYPE_LABELS.get(sess.get("programType", ""), sess.get("programType") or "-")
        part_program_types[t] += 0  # counts reservations not unique
    unique_by_ptype = Counter()
    for r in attended_res:
        sess = sessions.get(r["sessionId"], {})
        t = PROGRAM_TYPE_LABELS.get(sess.get("programType", ""), sess.get("programType") or "-")
        unique_by_ptype[t]  # placeholder
    users_by_ptype = defaultdict(set)
    for r in attended_res:
        sess = sessions.get(r["sessionId"], {})
        t = PROGRAM_TYPE_LABELS.get(sess.get("programType", ""), sess.get("programType") or "-")
        users_by_ptype[t].add(r["userId"])

    # companies among office workers (top, skip 1-person to reduce identifiability? User asked for all extractable data. I'll show companies with count>=3 to avoid identifying individuals, plus a note.)
    company_rows = []
    for name, cnt in company.most_common():
        if cnt < 2:
            continue
        company_rows.append({"label": name, "count": cnt})

    dump_date = "2026-11-02"

    stats = {
        "dumpDate": dump_date,
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "users": {
            "total": n_users,
            "office": n_office,
            "officeRate": pct(n_office, n_users),
            "participants": n_part,
            "participantRate": pct(n_part, n_users),
            "applicants": n_apply,
            "applicantRate": pct(n_apply, n_users),
            "couponIssuedUsers": len(issued_user_ids),
            "couponUsedUsers": len(used_user_ids),
            "couponUseRateOfAll": pct(len(used_user_ids), n_users),
            "couponUseRateOfIssued": pct(len(used_user_ids), len(issued_user_ids)),
        },
        "gender": counter_to_list(gender, gender_order),
        "age": counter_to_list(age, age_order),
        "job": counter_to_list(job),
        "area": counter_to_list(area),
        "provider": counter_to_list(provider),
        "routes": counter_to_list(routes),
        "companyTop": company_rows[:30],
        "signupDays": sorted(signup_by_day.items()),
        "coupons": {
            "issued": len(live_coupons),
            "used": len(coupon_used),
            "canceled": len(coupon_canceled),
            "unused": len(coupon_unused),
            "useRate": pct(len(coupon_used), len(live_coupons)),
            "issuedAmount": issued_amount,
            "usedAmount": used_amount,
            "canceledAmount": canceled_amount,
            "unusedAmount": sum(c["amount"] for c in coupon_unused),
        },
        "couponByDay": sorted(coupon_by_day.items()),
        "issuedByDay": sorted(issued_by_day.items()),
        "shopUsed": shop_used.most_common(40),
        "shopTypeUsed": shop_type_used.most_common(),
        "reasonIssued": reason_issued.most_common(),
        "reasonUsed": [(k, reason_used.get(k, 0), v, pct(reason_used.get(k, 0), v)) for k, v in reason_issued.most_common()],
        "reservations": {
            "total": len(live_reservations),
            "status": [(RESERVATION_STATUS_LABELS.get(k, k), v) for k, v in res_status.most_common()],
            "attended": len(attended_res),
            "applied": len(applied_res),
            "attendRate": pct(len(attended_res), len(applied_res)),
            "noShow": len(applied_res) - len(attended_res),
            "noShowRate": pct(len(applied_res) - len(attended_res), len(applied_res)),
        },
        "programs": program_rows,
        "sessions": session_rows,
        "overlap": {
            "officeAndPart": len(office_and_part),
            "officeAndPartRateOfOffice": pct(len(office_and_part), n_office),
            "officeAndPartRateOfPart": pct(len(office_and_part), n_part),
            "officeUsed": len(office_used),
            "officeUseRate": pct(len(office_used), n_office),
            "partUsed": len(part_used),
            "partUseRate": pct(len(part_used), n_part),
            "applyUsed": len(apply_used),
            "applyUseRate": pct(len(apply_used), n_apply),
            "officePartUsed": len(office_part_used),
            "officePartUseRate": pct(len(office_part_used), len(office_and_part)),
            "nonPartUsed": len(neither_part_used),
            "nonPartUseRate": pct(len(neither_part_used), len(neither_part)),
            "nonPart": len(neither_part),
            "nonOffice": len(non_office_ids),
            "nonOfficeUsed": len(non_office_used),
            "nonOfficeUseRate": pct(len(non_office_used), len(non_office_ids)),
            "officeNonPart": len(office_nonpart_ids),
            "officeNonPartUsed": len(office_nonpart_used),
            "officeNonPartUseRate": pct(len(office_nonpart_used), len(office_nonpart_ids)),
            "partUsedCount": len(part_used_coupons),
            "partUsedAmount": sum(c["amount"] for c in part_used_coupons),
            "nonPartUsedCount": len(nonpart_used_coupons),
            "nonPartUsedAmount": sum(c["amount"] for c in nonpart_used_coupons),
            "partShareOfUsedUsers": pct(len(part_used), len(used_user_ids)),
            "partShareOfUsedCount": pct(len(part_used_coupons), len(coupon_used)),
            "partShareOfUsedAmount": pct(
                sum(c["amount"] for c in part_used_coupons),
                sum(c["amount"] for c in coupon_used),
            ),
            "partLift": ratio(part_use_exact, nonpart_use_exact),
            "officeVsNonOfficeLift": ratio(office_use_exact, nonoffice_use_exact),
            "officePartLift": ratio(office_part_use_exact, office_nonpart_use_exact),
            "applyToPartRate": pct(n_part, n_apply),
            "partAvgUsed": round(len(part_used_coupons) / n_part, 2) if n_part else 0,
            "nonPartAvgUsed": round(len(nonpart_used_coupons) / len(neither_part), 2)
            if neither_part
            else 0,
        },
        "genderUse": group_use(real_users, "gender"),
        "ageUse": group_use(real_users, "ageGroup"),
        "jobUse": group_use(real_users, "job"),
        "areaUse": group_use(real_users, "residentArea"),
        "partGenderUse": group_use(participant_ids, "gender"),
        "partAgeUse": group_use(participant_ids, "ageGroup"),
        "partJobUse": group_use(participant_ids, "job"),
        "officeGenderUse": group_use(office_worker_ids, "gender"),
        "officeAgeUse": group_use(office_worker_ids, "ageGroup"),
        "heatmapAges": age_order,
        "heatmapGenders": [g for g in gender_order if any(heatmap.get(g, {}).values())],
        "heatmap": heatmap,
        "heatmapUsed": heatmap_used,
        "heatmapPart": heatmap_part,
        "couponCountDist": sorted(coupon_count_dist.items()),
        "usedCountDist": sorted(used_count_dist.items()),
        "weekdayUsed": [(d, weekday_used.get(d, 0)) for d in wd_names],
        "hourUsed": [(h, hour_used.get(h, 0)) for h in range(24)],
        "tickets": {
            "issued": len(ticket_issued),
            "used": len(ticket_used),
            "useRate": pct(len(ticket_used), len(ticket_issued)),
            "users": len(ticket_users),
            "usedUsers": len(ticket_used_users),
            "userUseRate": pct(len(ticket_used_users), len(ticket_users)),
        },
        "verifications": verif_status.most_common(),
        "usersByPtype": [(k, len(v), len(v & used_user_ids), pct(len(v & used_user_ids), len(v))) for k, v in users_by_ptype.items()],
        "partDemoGender": counter_to_list(participant_demo["gender"], gender_order),
        "partDemoAge": counter_to_list(participant_demo["age"], age_order),
        "partDemoJob": counter_to_list(participant_demo["job"]),
        "officeDemoAge": counter_to_list(office_demo["age"], age_order),
        "officeDemoGender": counter_to_list(office_demo["gender"], gender_order),
        "snsReviews": sum(1 for r in attended_res if r["hasSns"]),
    }
    return stats


PALETTE = [
    "#1d4ed8",
    "#0f766e",
    "#b45309",
    "#7c3aed",
    "#be123c",
    "#365314",
    "#0369a1",
    "#9a3412",
    "#6d28d9",
    "#115e59",
]


def build_html(s):
    def table(headers, rows, col_class=None):
        thead = "".join(f"<th>{escape(h)}</th>" for h in headers)
        body = []
        for row in rows:
            tds = []
            for i, cell in enumerate(row):
                tds.append(f"<td>{cell}</td>")
            body.append("<tr>" + "".join(tds) + "</tr>")
        return f"<div class='table-wrap'><table><thead><tr>{thead}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"

    def num(n):
        if isinstance(n, float):
            return f"{n:,.1f}"
        return f"{int(n):,}"

    def bar_row(label, count, total, extra=""):
        p = pct(count, total)
        return (
            f"<div class='bar-row'><div class='bar-label'>{escape(str(label))}</div>"
            f"<div class='bar-track'><div class='bar-fill' style='width:{p}%'></div></div>"
            f"<div class='bar-num'>{num(count)} <span>({p}%)</span></div>{extra}</div>"
        )

    def bars_with_other(items, total, min_share=2):
        main = []
        other = []
        for k, v in items:
            if pct(v, total) >= min_share:
                main.append((k, v))
            else:
                other.append((k, v))
        html = "".join(bar_row(k, v, total) for k, v in main)
        if other:
            other_count = sum(v for _, v in other)
            other_items = "".join(bar_row(k, v, total) for k, v in other)
            html += bar_row("기타", other_count, total)
            html += f"<div class='other-block'>{other_items}</div>"
        return html

    user_total = s["users"]["total"]
    gender_bars = "".join(bar_row(k, v, user_total) for k, v in s["gender"])
    age_bars = "".join(bar_row(k, v, user_total) for k, v in s["age"])
    job_bars = bars_with_other(s["job"], user_total)
    area_bars = "".join(bar_row(k, v, user_total) for k, v in s["area"])
    provider_bars = "".join(bar_row(k, v, user_total) for k, v in s["provider"])
    route_bars = bars_with_other(s["routes"], user_total)

    def use_table(rows):
        body = []
        for r in rows:
            body.append(
                [
                    escape(str(r["label"])),
                    num(r["users"]),
                    num(r["issuedUsers"]),
                    num(r["usedUsers"]),
                    f"{r['useRateOfGroup']}%",
                    f"{r['useRateOfIssued']}%",
                ]
            )
        return table(
            ["구분", "인원", "쿠폰 발급 인원", "쿠폰 사용 인원", "그룹 대비 사용률", "발급 대비 사용률"],
            body,
        )

    shop_rows = [[escape(k), num(v), f"{pct(v, s['coupons']['used'])}%"] for k, v in s["shopUsed"]]
    shop_type_rows = [[escape(k), num(v), f"{pct(v, s['coupons']['used'])}%"] for k, v in s["shopTypeUsed"]]
    reason_rows = [
        [escape(k), num(issued), num(used), f"{rate}%"]
        for k, used, issued, rate in s["reasonUsed"]
    ]
    program_table_rows = [
        [
            escape(p["name"]),
            escape(p["type"]),
            num(p["applied"]),
            num(p["appliedUsers"]),
            num(p["attended"]),
            num(p["attendedUsers"]),
            num(p["canceled"]),
            f"{p['attendRate']}%",
        ]
        for p in s["programs"]
    ]
    session_table_rows = [
        [
            escape(x["program"]),
            escape(x["start"]),
            num(x["capacity"]),
            num(x["applied"]),
            f"{x['fill']}%",
            num(x["attended"]),
            f"{x['attendRate']}%",
            num(x["canceled"]),
        ]
        for x in s["sessions"]
    ]
    company_table_rows = [[escape(x["label"]), num(x["count"])] for x in s["companyTop"]]
    ptype_rows = [
        [escape(k), num(n), num(u), f"{r}%"] for k, n, u, r in s["usersByPtype"]
    ]
    dist_rows = [[num(k), num(v)] for k, v in s["couponCountDist"]]
    used_dist_rows = [[num(k), num(v)] for k, v in s["usedCountDist"]]

    # heatmap html
    def heatmap_table(data, title):
        genders = s["heatmapGenders"]
        ages = [a for a in s["heatmapAges"] if any(data.get(g, {}).get(a, 0) for g in genders)]
        max_v = max((data.get(g, {}).get(a, 0) for g in genders for a in ages), default=1) or 1
        head = "<th></th>" + "".join(f"<th>{escape(a)}</th>" for a in ages)
        body = []
        for g in genders:
            cells = [f"<th>{escape(g)}</th>"]
            for a in ages:
                v = data.get(g, {}).get(a, 0)
                alpha = 0.08 + 0.72 * (v / max_v) if v else 0
                color = f"rgba(29,78,216,{alpha:.2f})" if v else "transparent"
                fg = "#0f172a" if alpha < 0.45 else "#fff"
                cells.append(f"<td style='background:{color};color:{fg}'>{v or '·'}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        return (
            f"<h3>{escape(title)}</h3>"
            f"<div class='table-wrap'><table class='heat'><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>"
        )

    heat_all = heatmap_table(s["heatmap"], "가입자 성별 × 연령")
    heat_used = heatmap_table(s["heatmapUsed"], "쿠폰 사용 인원 성별 × 연령")
    heat_part = heatmap_table(s["heatmapPart"], "프로그램 참여자 성별 × 연령")

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>마곡 미술길 데이터 대시보드</title>
<script src="chart.umd.min.js"></script>
<script src="chartjs-plugin-datalabels.min.js"></script>
<style>
  :root {{
    --bg: #f4f1ea;
    --paper: #ffffff;
    --ink: #1c1917;
    --muted: #57534e;
    --line: #e7e0d4;
    --accent: #1d4ed8;
    --accent-2: #0f766e;
    --warn: #b45309;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--ink); font-family: "Pretendard", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; line-height: 1.5; }}
  header {{
    padding: 36px 28px 20px;
    max-width: 1200px;
    margin: 0 auto;
  }}
  header h1 {{ font-size: 28px; font-weight: 700; margin: 0 0 8px; letter-spacing: -0.03em; }}
  header p {{ margin: 0; color: var(--muted); font-size: 14px; }}
  nav {{
    position: sticky; top: 0; z-index: 10;
    background: rgba(244,241,234,.92);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--line);
  }}
  nav .inner {{
    max-width: 1200px; margin: 0 auto; padding: 10px 28px;
    display: flex; gap: 16px; overflow-x: auto; font-size: 13px;
  }}
  nav a {{ color: var(--muted); text-decoration: none; white-space: nowrap; }}
  nav a:hover {{ color: var(--ink); }}
  main {{ max-width: 1200px; margin: 0 auto; padding: 20px 28px 80px; }}
  section {{ margin: 40px 0 56px; }}
  h2 {{ font-size: 20px; margin: 0 0 6px; letter-spacing: -0.02em; }}
  .lead {{ color: var(--muted); font-size: 13px; margin: 0 0 20px; }}
  .kpis {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
    margin-bottom: 24px;
  }}
  .kpi {{
    background: var(--paper);
    border: 1px solid var(--line);
    padding: 16px 16px 14px;
  }}
  .kpi .label {{ font-size: 12px; color: var(--muted); }}
  .kpi .value {{ font-size: 26px; font-weight: 700; letter-spacing: -0.04em; margin-top: 4px; }}
  .kpi .sub {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
  .kpi.primary .value {{ color: var(--accent); }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  .grid-3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }}
  @media (max-width: 860px) {{
    .grid-2, .grid-3 {{ grid-template-columns: 1fr; }}
  }}
  .panel {{
    background: var(--paper);
    border: 1px solid var(--line);
    padding: 18px 18px 14px;
  }}
  .panel h3 {{ margin: 0 0 14px; font-size: 15px; }}
  .bar-row {{ display: grid; grid-template-columns: 110px 1fr 90px; gap: 8px; align-items: center; margin: 6px 0; font-size: 13px; }}
  .bar-label {{ color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .bar-track {{ background: #ede8dc; height: 8px; }}
  .bar-fill {{ height: 8px; background: var(--accent); }}
  .bar-num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .bar-num span {{ color: var(--muted); font-size: 12px; }}
  .other-block {{
    margin-top: 8px;
    padding: 10px 12px 8px;
    background: #f3efe6;
    border: 1px solid var(--line);
  }}
  .other-block .bar-row {{
    grid-template-columns: minmax(88px, 1.3fr) 1fr 90px;
    font-size: 12px;
    margin: 4px 0;
  }}
  .other-block .bar-label {{ white-space: normal; overflow: visible; }}
  .other-block .bar-fill {{ background: #78716c; }}
  .table-wrap {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: right; font-variant-numeric: tabular-nums; }}
  th:first-child, td:first-child {{ text-align: left; }}
  thead th {{ font-size: 12px; color: var(--muted); font-weight: 600; }}
  table.heat td, table.heat th {{ text-align: center; min-width: 64px; }}
  table.heat th:first-child {{ text-align: left; }}
  .panel-block {{
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 8px;
    min-width: 0;
  }}
  .panel-block > .panel {{ width: 100%; }}
  .dl-btn {{
    display: inline-block;
    font-size: 12px;
    padding: 5px 10px;
    border: 1px solid var(--line);
    background: #fff;
    color: var(--ink);
    cursor: pointer;
  }}
  .dl-btn:hover {{ background: #f8fafc; }}
  .dl-btn:disabled {{ opacity: .6; cursor: wait; }}
  .chart-box {{
    position: relative;
    height: 280px;
    background: #fff;
  }}
  .note {{ font-size: 12px; color: var(--muted); margin-top: 10px; }}
  .compare-pair {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 12px;
  }}
  .compare-pair > div {{
    border: 1px solid var(--line);
    padding: 12px 14px;
  }}
  .compare-pair .label {{ font-size: 12px; color: var(--muted); }}
  .compare-pair .value {{ font-size: 28px; font-weight: 700; letter-spacing: -0.04em; margin-top: 2px; }}
  .compare-pair .value.accent {{ color: var(--accent); }}
  .compare-pair .sub {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
  .insight {{ font-size: 13px; margin: 0 0 16px; line-height: 1.55; }}
  .funnel {{ display: flex; flex-direction: column; gap: 6px; margin: 10px 0 14px; }}
  .funnel-step {{
    display: grid;
    grid-template-columns: 92px 1fr 78px;
    gap: 8px;
    align-items: center;
    font-size: 13px;
  }}
  .funnel-bar-wrap {{ background: #ede8dc; height: 16px; }}
  .funnel-bar {{ height: 16px; background: var(--accent); }}
  .funnel-step .n {{ text-align: right; font-variant-numeric: tabular-nums; }}
  footer {{ max-width: 1200px; margin: 0 auto; padding: 0 28px 40px; color: var(--muted); font-size: 12px; }}
</style>
</head>
<body>
<header>
  <h1>마곡 미술길 운영 데이터 대시보드</h1>
  <p>데이터 기준 2026-11-02 · 관리자·삭제 계정 제외 · 집계값만 표시 · SQL 없이 이 파일만 열면 됩니다</p>
</header>
<nav><div class="inner">
  <a href="#overview">요약</a>
  <a href="#demo">인구통계</a>
  <a href="#coupon">쿠폰</a>
  <a href="#cross">교차 비율</a>
  <a href="#program">프로그램</a>
  <a href="#shop">상점</a>
  <a href="#time">시간대별</a>
</div></nav>
<main>

<section id="overview">
  <h2>한눈에 보기</h2>
  <p class="lead">가입자 전체 대비 직장인·프로그램 참여자·쿠폰 사용 인원 비율입니다. 프로그램 참여자는 현장에서 체크인한 고유 사용자입니다. 예약금만 환불되고 체크인하지 않은 건은 참여로 치지 않습니다.</p>
  <div class="kpis">
    <div class="kpi primary"><div class="label">가입자</div><div class="value">{num(s['users']['total'])}</div><div class="sub">삭제·관리자 제외</div></div>
    <div class="kpi"><div class="label">직장인</div><div class="value">{num(s['users']['office'])}</div><div class="sub">전체의 {s['users']['officeRate']}%</div></div>
    <div class="kpi"><div class="label">프로그램 참여자</div><div class="value">{num(s['users']['participants'])}</div><div class="sub">전체의 {s['users']['participantRate']}%</div></div>
    <div class="kpi"><div class="label">프로그램 신청자</div><div class="value">{num(s['users']['applicants'])}</div><div class="sub">전체의 {s['users']['applicantRate']}%</div></div>
    <div class="kpi"><div class="label">쿠폰 사용 인원</div><div class="value">{num(s['users']['couponUsedUsers'])}</div><div class="sub">전체의 {s['users']['couponUseRateOfAll']}% · 발급자 대비 {s['users']['couponUseRateOfIssued']}%</div></div>
    <div class="kpi"><div class="label">쿠폰 사용 건수</div><div class="value">{num(s['coupons']['used'])}</div><div class="sub">발급 {num(s['coupons']['issued'])}건 중 {s['coupons']['useRate']}%</div></div>
    <div class="kpi"><div class="label">사용 금액</div><div class="value">{num(s['coupons']['usedAmount'])}</div><div class="sub">원 · 발급액 {num(s['coupons']['issuedAmount'])}원</div></div>
  </div>
  <div class="grid-2">
    <div class="panel">
      <h3>프로그램 참여 별 쿠폰</h3>
      <div class="compare-pair">
        <div>
          <div class="label">참여자 쿠폰 사용률</div>
          <div class="value accent">{s['overlap']['partUseRate']}%</div>
          <div class="sub">{num(s['overlap']['partUsed'])} / {num(s['users']['participants'])}명</div>
        </div>
        <div>
          <div class="label">프로그램 비 참여자 (sns 이벤트 등) 쿠폰 사용률</div>
          <div class="value">{s['overlap']['nonPartUseRate']}%</div>
          <div class="sub">{num(s['overlap']['nonPartUsed'])} / {num(s['overlap']['nonPart'])}명</div>
        </div>
      </div>
      <p class="insight">참여자가 프로그램 비 참여자(sns 이벤트 등)보다 쿠폰을 <b>{s['overlap']['partLift']}배</b> 더 씁니다. 가입자의 {s['users']['participantRate']}%인 참여자가 쿠폰 사용 인원의 {s['overlap']['partShareOfUsedUsers']}%, 사용 건수의 {s['overlap']['partShareOfUsedCount']}%, 사용 금액의 {s['overlap']['partShareOfUsedAmount']}%를 만듭니다. 1인당 사용 건수는 참여자 {s['overlap']['partAvgUsed']}장, 프로그램 비 참여자(sns 이벤트 등) {s['overlap']['nonPartAvgUsed']}장입니다.</p>
      <h3>전환</h3>
      <div class="funnel">
        <div class="funnel-step"><div>가입</div><div class="funnel-bar-wrap"><div class="funnel-bar" style="width:100%"></div></div><div class="n">{num(s['users']['total'])}</div></div>
        <div class="funnel-step"><div>프로그램 신청</div><div class="funnel-bar-wrap"><div class="funnel-bar" style="width:{s['users']['applicantRate']}%"></div></div><div class="n">{num(s['users']['applicants'])}</div></div>
        <div class="funnel-step"><div>체크인</div><div class="funnel-bar-wrap"><div class="funnel-bar" style="width:{s['users']['participantRate']}%"></div></div><div class="n">{num(s['users']['participants'])}</div></div>
        <div class="funnel-step"><div>참여 후 쿠폰</div><div class="funnel-bar-wrap"><div class="funnel-bar" style="width:{pct(s['overlap']['partUsed'], s['users']['total'])}%"></div></div><div class="n">{num(s['overlap']['partUsed'])}</div></div>
      </div>
      <p class="note">신청자 중 체크인 {s['overlap']['applyToPartRate']}% · 체크인한 사람 중 쿠폰 사용 {s['overlap']['partUseRate']}%</p>
      {table(
        ["비교", "인원", "비율"],
        [
          ["직장인 쿠폰 사용", f"{num(s['overlap']['officeUsed'])} / {num(s['users']['office'])}", f"{s['overlap']['officeUseRate']}%"],
          ["비직장인 쿠폰 사용", f"{num(s['overlap']['nonOfficeUsed'])} / {num(s['overlap']['nonOffice'])}", f"{s['overlap']['nonOfficeUseRate']}%"],
          ["직장인·참여자 쿠폰 사용", f"{num(s['overlap']['officePartUsed'])} / {num(s['overlap']['officeAndPart'])}", f"{s['overlap']['officePartUseRate']}%"],
          ["직장인 · 프로그램 비 참여자 (sns 이벤트 등) 쿠폰 사용", f"{num(s['overlap']['officeNonPartUsed'])} / {num(s['overlap']['officeNonPart'])}", f"{s['overlap']['officeNonPartUseRate']}%"],
          ["쿠폰 사용 인원 중 참여자", f"{num(s['overlap']['partUsed'])} / {num(s['users']['couponUsedUsers'])}", f"{s['overlap']['partShareOfUsedUsers']}%"],
          ["쿠폰 사용 건수 중 참여자", f"{num(s['overlap']['partUsedCount'])} / {num(s['coupons']['used'])}", f"{s['overlap']['partShareOfUsedCount']}%"],
        ],
      )}
      <p class="note">직장인 vs 비직장인 사용률은 비슷합니다({s['overlap']['officeUseRate']}% / {s['overlap']['nonOfficeUseRate']}%). 같은 직장인이라도 프로그램에 참여하면 쿠폰을 {s['overlap']['officePartLift']}배 더 씁니다.</p>
    </div>
    <div class="panel">
      <h3>쿠폰 상태</h3>
      <div class="chart-box"><canvas id="chartCouponStatus"></canvas></div>
    </div>
  </div>
</section>

<section id="demo">
  <h2>가입자 인구통계</h2>
  <p class="lead">회원가입 시 입력한 성별, 연령대, 직업, 거주지역, 인지 경로입니다.</p>
  <div class="grid-2">
    <div class="panel"><h3>성별</h3>{gender_bars}<div class="chart-box" style="height:220px;margin-top:16px"><canvas id="chartGender"></canvas></div></div>
    <div class="panel"><h3>연령대</h3>{age_bars}<div class="chart-box" style="height:220px;margin-top:16px"><canvas id="chartAge"></canvas></div></div>
    <div class="panel"><h3>직업</h3>{job_bars}</div>
    <div class="panel"><h3>거주 지역</h3>{area_bars}</div>
    <div class="panel"><h3>인지 경로</h3>{route_bars}</div>
    <div class="panel"><h3>가입 경로</h3>{provider_bars}</div>
  </div>
  <div class="panel" style="margin-top:16px">
    {heat_all}
  </div>
  <div class="panel" style="margin-top:16px">
    <h3>직장명 (2명 이상만 표시)</h3>
    {table(["직장명", "인원"], company_table_rows) if company_table_rows else "<p class='note'>2명 이상인 직장명이 없습니다.</p>"}
    <p class="note">개인 식별을 줄이기 위해 1명짜리 직장명은 생략했습니다.</p>
  </div>
</section>

<section id="coupon">
  <h2>쿠폰 발급·사용</h2>
  <p class="lead">쿠폰 건수 기준 사용률과, 사용자 고유 인원 기준 사용률을 함께 봅니다. 금액은 쿠폰 amount 합계입니다.</p>
  <div class="kpis">
    <div class="kpi"><div class="label">발급 건수</div><div class="value">{num(s['coupons']['issued'])}</div><div class="sub">{num(s['coupons']['issuedAmount'])}원</div></div>
    <div class="kpi primary"><div class="label">사용 건수</div><div class="value">{num(s['coupons']['used'])}</div><div class="sub">{s['coupons']['useRate']}% · {num(s['coupons']['usedAmount'])}원</div></div>
    <div class="kpi"><div class="label">미사용</div><div class="value">{num(s['coupons']['unused'])}</div><div class="sub">{num(s['coupons']['unusedAmount'])}원</div></div>
    <div class="kpi"><div class="label">취소</div><div class="value">{num(s['coupons']['canceled'])}</div><div class="sub">{num(s['coupons']['canceledAmount'])}원</div></div>
  </div>
  <div class="grid-2">
    <div class="panel">
      <h3>발급 사유별 사용률 (건수)</h3>
      {table(["사유", "발급", "사용", "사용률"], reason_rows)}
    </div>
    <div class="panel">
      <h3>1인당 발급 쿠폰 수 분포</h3>
      {table(["발급 장수", "인원"], dist_rows)}
      <h3 style="margin-top:20px">1인당 사용 쿠폰 수 분포</h3>
      {table(["사용 장수", "인원"], used_dist_rows)}
    </div>
  </div>
  <div class="grid-2" style="margin-top:16px">
    <div class="panel">
      <h3>사용 요일 (KST)</h3>
      <div class="chart-box"><canvas id="chartWeekday"></canvas></div>
    </div>
    <div class="panel">
      <h3>사용 시간대 (KST)</h3>
      <div class="chart-box"><canvas id="chartHour"></canvas></div>
    </div>
  </div>
</section>

<section id="cross">
  <h2>인구통계별 쿠폰 사용 비율</h2>
  <p class="lead">그룹 대비 사용률 = 해당 그룹 가입자 중 쿠폰을 1장이라도 사용한 사람 비율. 발급 대비 사용률 = 쿠폰을 받은 사람 중 사용한 사람 비율.</p>
  <div class="panel"><h3>전체 가입자 · 성별</h3>{use_table(s['genderUse'])}</div>
  <div class="panel" style="margin-top:16px"><h3>전체 가입자 · 연령</h3>{use_table(s['ageUse'])}</div>
  <div class="panel" style="margin-top:16px"><h3>전체 가입자 · 직업</h3>{use_table(s['jobUse'])}</div>
  <div class="panel" style="margin-top:16px"><h3>전체 가입자 · 거주지역</h3>{use_table(s['areaUse'])}</div>
  <div class="panel" style="margin-top:16px">{heat_used}</div>
  <h2 style="margin-top:40px">프로그램 참여자 중 쿠폰 사용</h2>
  <p class="lead">실제 참여(체크인 등)한 {num(s['users']['participants'])}명 기준입니다. 참여자 전체 쿠폰 사용률은 {s['overlap']['partUseRate']}%입니다.</p>
  <div class="panel"><h3>참여자 · 성별</h3>{use_table(s['partGenderUse'])}</div>
  <div class="panel" style="margin-top:16px"><h3>참여자 · 연령</h3>{use_table(s['partAgeUse'])}</div>
  <div class="panel" style="margin-top:16px"><h3>참여자 · 직업</h3>{use_table(s['partJobUse'])}</div>
  <div class="panel" style="margin-top:16px">
    <h3>프로그램 유형별 참여자 쿠폰 사용</h3>
    {table(["유형", "참여자", "쿠폰 사용", "사용률"], ptype_rows) if ptype_rows else "<p class='note'>데이터 없음</p>"}
  </div>
  <h2 style="margin-top:40px">직장인 중 쿠폰 사용</h2>
  <p class="lead">직업이 직장인인 {num(s['users']['office'])}명 기준, 쿠폰 사용률 {s['overlap']['officeUseRate']}%.</p>
  <div class="grid-2">
    <div class="panel"><h3>직장인 성별</h3>{use_table(s['officeGenderUse'])}</div>
    <div class="panel"><h3>직장인 연령</h3>{use_table(s['officeAgeUse'])}</div>
  </div>
</section>

<section id="program">
  <h2>프로그램 신청·참여</h2>
  <p class="lead">예약 건수와 고유 인원을 같이 둡니다. 한 사람이 여러 회차에 참여할 수 있습니다. 신청은 취소·거절을 제외한 유효 예약, 참여는 체크인 시각이 있는 건입니다.</p>
  <div class="kpis">
    <div class="kpi"><div class="label">예약 건수</div><div class="value">{num(s['reservations']['total'])}</div></div>
    <div class="kpi"><div class="label">신청 건수</div><div class="value">{num(s['reservations']['applied'])}</div></div>
    <div class="kpi primary"><div class="label">체크인 건수</div><div class="value">{num(s['reservations']['attended'])}</div><div class="sub">신청 대비 {s['reservations']['attendRate']}%</div></div>
    <div class="kpi"><div class="label">미체크인 신청</div><div class="value">{num(s['reservations']['noShow'])}</div><div class="sub">신청 대비 {s['reservations']['noShowRate']}%</div></div>
    <div class="kpi"><div class="label">참여 후기(SNS) 건수</div><div class="value">{num(s['snsReviews'])}</div></div>
  </div>
  <div class="grid-2">
    <div class="panel">
      <h3>예약 상태</h3>
      {table(["상태", "건수"], [[escape(k), num(v)] for k,v in s['reservations']['status']])}
    </div>
    <div class="panel">
      <h3>참여자 인구통계</h3>
      <p class="note" style="margin-top:0">고유 참여자 {num(s['users']['participants'])}명</p>
      <h3>성별</h3>
      {''.join(bar_row(k,v,s['users']['participants']) for k,v in s['partDemoGender'])}
      <h3>연령</h3>
      {''.join(bar_row(k,v,s['users']['participants']) for k,v in s['partDemoAge'])}
      <h3>직업</h3>
      {''.join(bar_row(k,v,s['users']['participants']) for k,v in s['partDemoJob'])}
    </div>
  </div>
  <div class="panel" style="margin-top:16px">
    {heat_part}
  </div>
  <div class="panel" style="margin-top:16px">
    <h3>프로그램별 신청·참여</h3>
    {table(["프로그램", "유형", "신청 건", "신청 인원", "참여 건", "참여 인원", "취소", "참여율"], program_table_rows)}
  </div>
  <div class="panel" style="margin-top:16px">
    <h3>회차별 정원 대비 신청</h3>
    {table(["프로그램", "일자(KST)", "정원", "신청", "충원율", "참여", "참여율", "취소"], session_table_rows)}
  </div>
  <div class="panel" style="margin-top:16px">
    <h3>프로그램별 참여 인원</h3>
    <div class="chart-box" style="height:320px"><canvas id="chartProgram"></canvas></div>
  </div>
</section>

<section id="shop">
  <h2>상점별 쿠폰 사용</h2>
  <p class="lead">사용된 쿠폰의 shopId 기준입니다. 상위 40개 상점을 표시합니다.</p>
  <div class="grid-2">
    <div class="panel">
      <h3>업종별 사용 건수</h3>
      {table(["업종", "사용 건", "비중"], shop_type_rows)}
    </div>
    <div class="panel">
      <h3>업종 비중</h3>
      <div class="chart-box" style="height:520px"><canvas id="chartShopType"></canvas></div>
    </div>
  </div>
  <div class="panel" style="margin-top:16px">
    <h3>상점별 사용 건수</h3>
    {table(["상점", "사용 건", "비중"], shop_rows)}
  </div>
</section>

<section id="time">
  <h2>시간ㄷ별</h2>
  <p class="lead">날짜는 한국시간(KST) 기준.</p>
  <div class="panel">
    <h3>일별 가입자</h3>
    <div class="chart-box" style="height:300px"><canvas id="chartSignup"></canvas></div>
  </div>
  <div class="panel" style="margin-top:16px">
    <h3>일별 쿠폰 발급·사용</h3>
    <div class="chart-box" style="height:320px"><canvas id="chartCouponTime"></canvas></div>
  </div>
</section>

</main>
<footer>
  개인 식별 정보(이름, 전화, 계좌, 비밀번호, OAuth ID)는 포함하지 않았습니다. 프로그램 참여자 정의: 예약의 체크인 시각(checkInAt)이 있는 사용자. 신청은 취소·거절을 제외한 예약입니다.
</footer>
<script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>
<script>
const PALETTE = {js(PALETTE)};
const genderData = {js(s['gender'])};
const ageData = {js(s['age'])};
const couponStatus = [
  ['사용', {s['coupons']['used']}],
  ['미사용', {s['coupons']['unused']}],
  ['취소', {s['coupons']['canceled']}],
];
const weekday = {js(s['weekdayUsed'])};
const hour = {js(s['hourUsed'])};
const programs = {js(s['programs'])};
const shopTypes = {js(s['shopTypeUsed'])};
const signupDays = {js(s['signupDays'])};
const couponDays = {js(s['couponByDay'])};
const issuedDays = {js(s['issuedByDay'])};

Chart.register(ChartDataLabels);
Chart.register({{
  id: 'whiteBg',
  beforeDraw(chart) {{
    const ctx = chart.ctx;
    ctx.save();
    ctx.globalCompositeOperation = 'destinationOver';
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, chart.width, chart.height);
    ctx.restore();
  }}
}});
Chart.defaults.plugins.tooltip.enabled = false;
Chart.defaults.color = '#1c1917';

function fmt(n) {{
  return Number(n).toLocaleString('ko-KR');
}}
function doughnut(id, rows) {{
  const ctx = document.getElementById(id);
  if (!ctx) return;
  const total = rows.reduce((s, r) => s + r[1], 0);
  new Chart(ctx, {{
    type: 'doughnut',
    data: {{
      labels: rows.map(r => {{
        const p = total ? (r[1] * 100 / total) : 0;
        return r[0] + '  ' + fmt(r[1]) + ' (' + p.toFixed(1) + '%)';
      }}),
      datasets: [{{ data: rows.map(r => r[1]), backgroundColor: PALETTE, borderWidth: 0 }}]
    }},
    options: {{
      plugins: {{
        legend: {{ position: 'right', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
        datalabels: {{
          color: '#fff',
          font: {{ size: 11, weight: '600' }},
          formatter: (v) => {{
            const p = total ? (v * 100 / total) : 0;
            return p >= 8 ? fmt(v) : '';
          }}
        }}
      }},
      maintainAspectRatio: false
    }}
  }});
}}
function bar(id, rows, label) {{
  const ctx = document.getElementById(id);
  if (!ctx) return;
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: rows.map(r => r[0]),
      datasets: [{{ label, data: rows.map(r => r[1]), backgroundColor: '#1d4ed8', borderWidth: 0 }}]
    }},
    options: {{
      layout: {{ padding: {{ top: 18 }} }},
      plugins: {{
        legend: {{ display: false }},
        datalabels: {{
          anchor: 'end',
          align: 'end',
          offset: -2,
          color: '#1c1917',
          font: {{ size: 10, weight: '600' }},
          formatter: (v) => v ? fmt(v) : ''
        }}
      }},
      scales: {{
        x: {{ ticks: {{ font: {{ size: 11 }} }}, grid: {{ display: false }} }},
        y: {{ beginAtZero: true, ticks: {{ precision: 0 }} }}
      }},
      maintainAspectRatio: false
    }}
  }});
}}
function lineLabelDisplay(ctx) {{
  const data = ctx.dataset.data;
  const i = ctx.dataIndex;
  const v = data[i];
  if (!v) return false;
  const prev = i > 0 ? data[i - 1] : -1;
  const next = i < data.length - 1 ? data[i + 1] : -1;
  const max = Math.max.apply(null, data);
  return v === max || v >= 20 || (v >= prev && v >= next && v >= 8);
}}
doughnut('chartGender', genderData);
bar('chartAge', ageData, '가입자');
doughnut('chartCouponStatus', couponStatus);
bar('chartWeekday', weekday, '사용 건수');
bar('chartHour', hour, '사용 건수');
bar('chartProgram', programs.map(p => [p.name, p.attendedUsers]), '참여 인원');
doughnut('chartShopType', shopTypes);

(function() {{
  const ctx = document.getElementById('chartSignup');
  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: signupDays.map(r => r[0]),
      datasets: [{{ label: '가입자', data: signupDays.map(r => r[1]), borderColor: '#1d4ed8', backgroundColor: 'rgba(29,78,216,.12)', fill: true, tension: .25, pointRadius: 2, pointHoverRadius: 2 }}]
    }},
    options: {{
      layout: {{ padding: {{ top: 16 }} }},
      plugins: {{
        legend: {{ display: false }},
        datalabels: {{
          align: 'top',
          anchor: 'end',
          color: '#1c1917',
          font: {{ size: 9, weight: '600' }},
          formatter: (v) => fmt(v),
          display: lineLabelDisplay
        }}
      }},
      scales: {{ x: {{ ticks: {{ maxTicksLimit: 12, font: {{ size: 10 }} }} }}, y: {{ beginAtZero: true, ticks: {{ precision: 0 }} }} }},
      maintainAspectRatio: false
    }}
  }});
}})();

(function() {{
  const allDays = Array.from(new Set([...issuedDays.map(r=>r[0]), ...couponDays.map(r=>r[0])])).sort();
  const issuedMap = Object.fromEntries(issuedDays);
  const usedMap = Object.fromEntries(couponDays);
  const ctx = document.getElementById('chartCouponTime');
  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: allDays,
      datasets: [
        {{ label: '발급', data: allDays.map(d => issuedMap[d] || 0), borderColor: '#57534e', tension: .25, pointRadius: 2, datalabels: {{ color: '#57534e' }} }},
        {{ label: '사용', data: allDays.map(d => usedMap[d] || 0), borderColor: '#1d4ed8', tension: .25, pointRadius: 2, datalabels: {{ color: '#1d4ed8' }} }}
      ]
    }},
    options: {{
      layout: {{ padding: {{ top: 16 }} }},
      plugins: {{
        legend: {{ position: 'top' }},
        datalabels: {{
          align: 'top',
          anchor: 'end',
          font: {{ size: 9, weight: '600' }},
          formatter: (v) => fmt(v),
          display: lineLabelDisplay
        }}
      }},
      scales: {{ x: {{ ticks: {{ maxTicksLimit: 12, font: {{ size: 10 }} }} }}, y: {{ beginAtZero: true, ticks: {{ precision: 0 }} }} }},
      maintainAspectRatio: false
    }}
  }});
}})();

function fileName(title) {{
  return (title || '카드').replace(/[\\\\/:*?"<>|]+/g, '').replace(/\\s+/g, '-');
}}
async function downloadPanel(panel, name, btn) {{
  if (typeof html2canvas !== 'function') return;
  btn.disabled = true;
  const prev = btn.textContent;
  btn.textContent = '저장 중';
  try {{
    const canvas = await html2canvas(panel, {{
      backgroundColor: '#ffffff',
      scale: 2,
      logging: false,
      useCORS: true
    }});
    const a = document.createElement('a');
    a.download = '마곡-' + name + '.png';
    a.href = canvas.toDataURL('image/png');
    a.click();
  }} finally {{
    btn.disabled = false;
    btn.textContent = prev;
  }}
}}
document.querySelectorAll('.panel').forEach((panel) => {{
  const wrap = document.createElement('div');
  wrap.className = 'panel-block';
  if (panel.style.marginTop) {{
    wrap.style.marginTop = panel.style.marginTop;
    panel.style.marginTop = '';
  }}
  panel.parentNode.insertBefore(wrap, panel);
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'dl-btn';
  btn.textContent = '이미지 저장';
  const title = panel.querySelector('h3');
  const name = fileName(title ? title.textContent.trim() : '카드');
  btn.addEventListener('click', () => downloadPanel(panel, name, btn));
  wrap.appendChild(btn);
  wrap.appendChild(panel);
}});
</script>
</body>
</html>
"""
    return html


def main():
    print("Reading SQL dump...")
    content = SQL_PATH.read_text(encoding="utf-8")
    tables = load_tables(content)
    print("Analyzing...")
    stats = analyze(tables)
    print("Writing HTML...")
    html = build_html(stats)
    chart_js = (SQL_PATH.parent / "chart.umd.min.js").read_text(encoding="utf-8")
    datalabels_js = (SQL_PATH.parent / "chartjs-plugin-datalabels.min.js").read_text(
        encoding="utf-8"
    )
    html = html.replace(
        '<script src="chart.umd.min.js"></script>',
        "<script>\n" + chart_js + "\n</script>",
        1,
    )
    html = html.replace(
        '<script src="chartjs-plugin-datalabels.min.js"></script>',
        "<script>\n" + datalabels_js + "\n</script>",
        1,
    )
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(html):,} chars)")
    print("Users:", stats["users"]["total"])
    print("Office:", stats["users"]["office"], stats["users"]["officeRate"], "%")
    print("Participants:", stats["users"]["participants"], stats["users"]["participantRate"], "%")
    print("Coupon used users:", stats["users"]["couponUsedUsers"])
    print("Coupon used / issued:", stats["coupons"]["used"], "/", stats["coupons"]["issued"])


if __name__ == "__main__":
    main()
