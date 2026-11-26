"""Nạp dữ liệu mẫu. Chạy lại nhiều lần được: chỉ thêm những mục còn thiếu, không xóa hay ghi đè."""
from database import SessionLocal, engine, Base
import models
from models import Rank, WasteType, DisposalGuide

Base.metadata.create_all(bind=engine)

RANKS = [("Đồng", 0), ("Bạc", 100), ("Vàng", 500), ("Kim Cương", 1500)]

# (code, tên tiếng Việt, nhóm, màu thùng, hướng dẫn). Nội dung mẫu: Người 4 sẽ thay bằng bản chuẩn.
# "code" phải khớp với LABEL_TO_CODE trong classifier.py.
WASTE = [
    ("plastic", "Nhựa", "tai_che", "Xanh dương", "Tráng sơ, bỏ nắp, ép dẹp rồi cho vào thùng tái chế."),
    ("paper", "Giấy", "tai_che", "Vàng", "Giữ khô, tránh dính dầu mỡ rồi cho vào thùng giấy."),
    ("cardboard", "Bìa carton", "tai_che", "Nâu", "Làm phẳng thùng, giữ khô rồi đem đến điểm thu gom."),
    ("metal", "Kim loại", "tai_che", "Xám", "Làm sạch lon, ép dẹp nếu được rồi cho vào thùng kim loại."),
    ("glass", "Thủy tinh", "tai_che", "Xanh lá", "Tráng sạch, bọc kỹ nếu vỡ rồi bỏ riêng."),
    ("trash", "Rác thông thường", "khong_tai_che", "Đen", "Rác không tái chế được: cho vào túi kín rồi bỏ vào thùng rác chung."),
    ("organic", "Rác hữu cơ", "huu_co", "Xanh lục", "Cho vào thùng rác hữu cơ hoặc ủ phân."),
]

db = SessionLocal()
try:
    have_ranks = {r.name for r in db.query(Rank).all()}
    for name, pts in RANKS:
        if name not in have_ranks:
            db.add(Rank(name=name, min_points=pts))

    have_codes = {w.code for w in db.query(WasteType).all()}
    added = []
    for code, name, group, bin_color, guide in WASTE:
        if code in have_codes:
            continue
        wt = WasteType(code=code, name_vi=name, group_name=group)
        wt.guide = DisposalGuide(bin_color=bin_color, instructions=guide)
        db.add(wt)
        added.append(code)

    db.commit()
    print("Đã nạp dữ liệu mẫu. Loại rác mới thêm:", added or "không có (đã đủ)")
finally:
    db.close()