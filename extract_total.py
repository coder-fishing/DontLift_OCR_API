import json
import re
import unicodedata
from pathlib import Path

OUTPUT_DIR = Path("output")


def normalize(text: str) -> str:
    """Normalize Vietnamese labels while retaining ASCII letters/numbers."""
    text = text.replace("Đ", "D").replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return text.upper()


def amounts_in(text: str) -> list[int]:
    # Loại bỏ các chuỗi dạng ngày tháng HH:MM hoặc DD/MM/YYYY trước khi tìm tiền
    text_clean = re.sub(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", "", text)
    text_clean = re.sub(r"\d{1,2}:\d{2}", "", text_clean)

    candidates = re.findall(r"(?<!\d)\d[\d., ]{1,15}\d|(?<!\d)\d{3,9}(?!\d)", text_clean)
    amounts = []
    for candidate in candidates:
        digits = re.sub(r"\D", "", candidate)
        if 3 <= len(digits) <= 9:
            amounts.append(int(digits))
    return amounts


def process_single_file(json_path: Path) -> None:
    print(f"\n========================================")
    print(f"Xử lý file: {json_path.name}")
    print(f"========================================")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    texts = data.get("rec_texts", [])
    normalized = [normalize(text) for text in texts]

    # Bỏ qua từ khóa giảm giá VÀ tiêu đề hóa đơn
    IGNORE_KEYWORDS = ("GIAM GIA", "DISCOUNT", "CHIET KHAU", "KHUYEN MAI", "HOA DON")

    label_groups = (
        ("TONG CONG TIEN", "TONG CONG", "TONG THANH TOAN", "GRAND TOTAL"),
        ("CAN THANH TOAN", "AMOUNT DUE", "BALANCE DUE", "TONG TIEN", "TONG", "TNG"),
        ("THANH TOAN",), # Để cuối cùng
    )

    for labels in label_groups:
        for index, line in enumerate(normalized):
            # Bỏ qua nếu dòng là tiêu đề hóa đơn hoặc thông tin giảm giá
            if any(ignore in line for ignore in IGNORE_KEYWORDS):
                continue

            line_clean = re.sub(r"[^\w\s]", "", line)
            if not any(label in line_clean for label in labels):
                continue

            # Chỉ tìm ở dòng hiện tại và tối đa 2 dòng tiếp theo
            for candidate_index in range(index, min(index + 3, len(texts))):
                found = amounts_in(texts[candidate_index])
                if found:
                    amount = found[-1]
                    print(f"Tổng thanh toán đề xuất: {amount:,} VND")
                    print(f"Nhãn: {texts[index]}")
                    print(f"Dòng tiền: {texts[candidate_index]}")
                    return

    print("Không xác định chắc chắn được tổng thanh toán.")
    print("Các dòng OCR có chứa số tiền để kiểm tra thủ công:")
    for text in texts:
        found = amounts_in(text)
        if found:
            print(f"- {text}")

    print(f"\n========================================")
    print(f"Xử lý file: {json_path.name}")
    print(f"========================================")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    texts = data.get("rec_texts", [])
    normalized = [normalize(text) for text in texts]

    IGNORE_KEYWORDS = ("GIAM GIA", "DISCOUNT", "CHIET KHAU", "KHUYEN MAI")

    # Các nhãn tổng tiền chính xác (TUYỆT ĐỐI KHÔNG ĐƯA 'HOA DON THANH TOAN' VÀO ĐÂY)
    label_groups = (
        ("TONG CONG TIEN", "TONG CONG", "TONG THANH TOAN", "GRAND TOTAL", "TOTAL"),
        ("THANH TOAN", "CAN THANH TOAN", "AMOUNT DUE", "BALANCE DUE", "TONG TIEN", "TONG", "TNG"),
    )

    for labels in label_groups:
        for index, line in enumerate(normalized):
            if any(ignore in line for ignore in IGNORE_KEYWORDS):
                continue

            # Kiểm tra từ khóa nhãn (bỏ qua dấu hai chấm và khoảng trắng thừa khi so sánh)
            line_clean = re.sub(r"[^\w\s]", "", line)
            if not any(label in line_clean for label in labels):
                continue

            # CHỈ QUÉT DÒNG HIỆN TẠI VÀ CÁC DÒNG BÊN DƯỚI (index -> index + 2)
            for candidate_index in range(index, min(index + 3, len(texts))):
                found = amounts_in(texts[candidate_index])
                if found:
                    # Lấy số tiền tìm thấy (nếu dòng có chữ 'TONG' trùng với số tiền thì ưu tiên)
                    amount = found[-1]
                    print(f"Tổng thanh toán đề xuất: {amount:,} VND")
                    print(f"Nhãn: {texts[index]}")
                    print(f"Dòng tiền: {texts[candidate_index]}")
                    return

    print("Không xác định chắc chắn được tổng thanh toán.")
    print("Các dòng OCR có chứa số tiền để kiểm tra thủ công:")
    for text in texts:
        found = amounts_in(text)
        if found:
            print(f"- {text}")


def main() -> None:
    if not OUTPUT_DIR.exists():
        raise SystemExit(f"Không tìm thấy thư mục: {OUTPUT_DIR}")

    json_files = sorted(OUTPUT_DIR.glob("*_res.json"))

    if not json_files:
        print(f"Không tìm thấy file *_res.json nào trong {OUTPUT_DIR}")
        return

    for json_path in json_files:
        process_single_file(json_path)


if __name__ == "__main__":
    main()