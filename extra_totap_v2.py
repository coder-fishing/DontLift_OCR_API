"""Trích xuất tổng tiền VND từ JSON PaddleOCR, không cần thư viện bổ sung.

Chạy: python extra_totap_v2.py [output hoặc đường dẫn *_res.json]
Điểm ranking_score là điểm quy tắc, không phải xác suất đúng.
"""

import argparse
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median


LABELS = {
    "total": ("TONG THANH TOAN", "TONG CONG", "TONG TIEN", "THANH TOAN",
              "PHAI TRA", "CAN TRA", "BAN CAN TRA", "GRAND TOTAL", "TOTAL"),
    "weak_total": ("TONG", "TNG", "THANH TIEN"),
    "subtotal": ("TIEN HANG", "TAM TINH", "SUBTOTAL", "SUB TOTAL"),
    "discount": ("GIAM GIA", "CHIET KHAU", "DISCOUNT", "UU DAI"),
    "tax": ("THUE", "VAT", "TAX", "PHI DICH VU", "SERVICE CHARGE"),
    "tendered": ("KHACH DUA", "TIEN NHAN", "CASH TENDERED", "TIEN KHACH"),
    "change": ("TIEN THUA", "TIEN THOI", "CHANGE"),
    "balance": ("CON LAI", "CON NO", "BALANCE DUE", "AMOUNT DUE"),
    "payment": ("MOMO", "VISA", "MASTERCARD", "CHUYEN KHOAN"),
}
EXCLUDED = re.compile(r"\b(HOA DON|PHIEU|SDT|DT|DIEN THOAI|PHONE|SO|MA|PASS|WIFI|DIEM)\b")
# Không nối các số cách nhau bằng khoảng trắng; chỉ hỗ trợ tiền nguyên VND.
MONEY = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d{1,3}(?:\.\d{3})+|\d{3,12})(?![\d.,])", re.I)


def normalize(text):
    text = str(text).replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", text.upper())).strip()


def classify(text):
    clean = normalize(text)
    if EXCLUDED.search(clean):
        return "excluded", False
    # Khôi phục khoảng trắng OCR chèn giữa một từ trong nhãn thành phần.
    compact = re.sub(r"[\d ]", "", clean)
    for role in ("subtotal", "discount", "tendered", "change"):
        if any(len(label.replace(" ", "")) >= 7 and
               compact.endswith(label.replace(" ", "")) for label in LABELS[role]):
            return role, False
    # Nhãn thành phần được xét trước để TONG GIAM GIA không thành tổng tiền.
    order = ("discount", "subtotal", "tendered", "change", "balance", "tax",
             "payment", "total", "weak_total")
    for role in order:
        if role == "weak_total":
            continue
        if any(re.search(r"\b" + re.escape(label) + r"\b", clean)
               for label in LABELS[role]):
            return role, False
    words = clean.split()
    for role in order:
        for label in LABELS[role]:
            if len(label) < 8:
                continue
            size = len(label.split())
            for start in range(len(words) - size + 1):
                part = " ".join(words[start:start + size])
                if SequenceMatcher(None, part, label).ratio() >= 0.88:
                    return role, True
    if any(re.search(r"\b" + re.escape(label) + r"\b", clean)
           for label in LABELS["weak_total"]):
        return "weak_total", False
    return "unknown", False


def build_blocks(data):
    texts = data.get("rec_texts")
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        raise ValueError("rec_texts phải là danh sách chuỗi")
    boxes = data.get("rec_boxes", [])
    scores = data.get("rec_scores", [])
    polys = data.get("rec_polys", [])
    slopes = []
    if len(polys) == len(texts):
        for poly in polys:
            if len(poly) == 4 and all(len(point) == 2 for point in poly):
                dx = poly[1][0] - poly[0][0]
                if dx > 0:
                    slope = (poly[1][1] - poly[0][1]) / dx
                    if abs(slope) < 0.5:
                        slopes.append(slope)
    slope = median(slopes) if slopes else 0
    blocks = []
    for i, text in enumerate(texts):
        box = None
        if len(boxes) == len(texts):
            raw = boxes[i]
            if isinstance(raw, list) and len(raw) == 4 and all(isinstance(v, (int, float)) for v in raw):
                box = raw
        role, fuzzy = classify(text)
        if normalize(text) == "THANH TIEN" and any(
            normalize(other) in ("SL", "SO LUONG", "GIA", "DON GIA")
            for other in texts[max(0, i - 4):i + 5]
        ):
            role = "excluded"
        layout_box = None
        if box:
            correction = slope * (box[0] + box[2]) / 2
            layout_box = [box[0], box[1] - correction, box[2], box[3] - correction]
        blocks.append(dict(index=i, text=text, box=box, layout_box=layout_box, role=role, fuzzy=fuzzy,
                           ocr_score=scores[i] if len(scores) == len(texts) else None))
    return blocks


def parse_money(text):
    text = re.sub(r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}", " ", text)
    text = re.sub(r"\d{1,2}:\d{2}(?::\d{2})?", " ", text)
    result = []
    for match in MONEY.finditer(text):
        # Chấp nhận đ/d/VND và lỗi OCR 'g' của ký hiệu tiền; không nhận mã chữ-số.
        suffix = re.match(r"[^\W\d_]+", text[match.end():], re.UNICODE)
        if suffix and normalize(suffix.group()) not in ("D", "VND", "G"):
            continue
        raw = match.group()
        amount = int(re.sub(r"[.,]", "", raw))
        if 0 < amount <= 999_999_999_999:
            result.append((amount, raw))
    return result


def proximity(label, amount):
    if label["index"] == amount["index"]:
        return 3
    a, b = label["layout_box"], amount["layout_box"]
    if a and b:
        height = max(a[3] - a[1], b[3] - b[1], 1)
        same_row = abs((a[1] + a[3] - b[1] - b[3]) / 2) <= height * 0.6
        if same_row and b[0] >= a[2] - height:
            return 3
        below = 0 <= b[1] - a[3] <= height * 2
        overlap = min(a[2], b[2]) - max(a[0], b[0]) >= 0
        if below and overlap:
            return 2
        return 0
    gap = amount["index"] - label["index"]
    return 1 if 0 < gap <= 2 else 0


def extract_total(data):
    blocks = build_blocks(data)
    candidates = []
    weights = {"total": 7, "weak_total": 3, "unknown": 0, "payment": 1,
               "subtotal": -6, "discount": -8, "tax": -7, "tendered": -9,
               "change": -9, "balance": -5}
    for block in blocks:
        if block["role"] == "excluded":
            continue
        for amount, raw in parse_money(block["text"]):
            nearby = []
            for label in blocks:
                if label["role"] in ("unknown", "excluded"):
                    continue
                distance = proximity(label, block)
                if distance:
                    nearby.append((distance, -abs(label["index"] - block["index"]), label))
            label = max(nearby, key=lambda item: item[:2])[2] if nearby else block
            distance = proximity(label, block) if nearby else 0
            role = label["role"]
            score = weights.get(role, 0) + distance - int(label["fuzzy"])
            reasons = [f"Loại nhãn: {role}"]
            if distance:
                reasons.append("Ghép nhãn theo tọa độ" if label["box"] and block["box"] else "Ghép theo thứ tự OCR")
            if block["index"] >= len(blocks) * 0.55:
                score += 1
            if block["ocr_score"] is not None and block["ocr_score"] < 0.8:
                score -= 2
                reasons.append("Điểm nhận dạng OCR thấp")
            candidates.append(dict(amount=amount, ranking_score=score, role=role,
                                   evidence={"label": label["text"], "amount_text": block["text"],
                                             "index": block["index"], "box": block["box"]}, reasons=reasons))

    # Chỉ đối chiếu khi có đúng một giá trị tiền hàng và một giá trị giảm giá.
    subtotals = {c["amount"] for c in candidates if c["role"] == "subtotal"}
    discounts = {c["amount"] for c in candidates if c["role"] == "discount"}
    has_tax = any(b["role"] == "tax" for b in blocks)
    for c in candidates:
        if c["role"] in ("total", "weak_total") and len(subtotals) == len(discounts) == 1 and not has_tax:
            if next(iter(subtotals)) - next(iter(discounts)) == c["amount"]:
                c["ranking_score"] += 3
                c["reasons"].append("Khớp tiền hàng trừ giảm giá")
    # Cùng một giá trị xuất hiện nhiều lần không tạo ra đối thủ giả.
    unique = {}
    for c in sorted(candidates, key=lambda c: c["ranking_score"], reverse=True):
        unique.setdefault(c["amount"], c)
    ranked = list(unique.values())
    top = ranked[0] if ranked else None
    margin = top["ranking_score"] - ranked[1]["ranking_score"] if len(ranked) > 1 else 99
    warnings = []
    if not blocks or not all(b["box"] for b in blocks):
        warnings.append("Thiếu tọa độ; ghép theo thứ tự OCR có thể nhầm cột.")
    receipt_ids = sum(bool(re.search(r"\bSO (PH[IL]EU|HOA DON)\b", normalize(b["text"]))) for b in blocks)
    if receipt_ids > 1:
        warnings.append("Có nhiều dòng số phiếu: cần kiểm tra ảnh có nhiều hóa đơn hay không.")
    strong = bool(top and top["role"] == "total" and top["ranking_score"] >= 9 and margin >= 3 and not warnings)
    return dict(status="suggested" if strong else "needs_review", currency="VND",
                amount=top["amount"] if strong else None, needs_confirmation=True,
                proposal=top, alternatives=ranked[1:4], warnings=warnings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=Path(__file__).parent / "output")
    args = parser.parse_args()
    paths = sorted(args.path.glob("*_res.json")) if args.path.is_dir() else [args.path]
    if not paths:
        parser.error("Không có file *_res.json")
    failed = False
    for path in paths:
        print(f"\n{'=' * 40}\nXử lý file: {path.name}\n{'=' * 40}")
        try:
            result = extract_total(json.loads(path.read_text(encoding="utf-8")))
            proposal = result["proposal"]
            if proposal is None:
                print("Không xác định được tổng thanh toán.")
                continue
            print(f"Tổng thanh toán đề xuất: {proposal['amount']:,} VND")
            print(f"Nhãn: {proposal['evidence']['label']}")
            print(f"Dòng tiền: {proposal['evidence']['amount_text']}")
            if result["status"] == "needs_review":
                print("Cần xác nhận lại số tiền.")
        except (OSError, ValueError, TypeError) as exc:
            failed = True
            print(f"Lỗi: {exc}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
