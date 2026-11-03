"""规格 v2 行为测试：规格更新后由 run-task 按新规格修改实现，改前失败、改后全过。"""

import pytest

import reading_list


def test_export_csv_sorted_by_title(tmp_path):
    books = []
    reading_list.add_book(books, "活者", "余华", "文学")
    reading_list.add_book(books, "三体", "刘慈欣", "科幻")
    reading_list.add_book(books, "an hour before", "unknown", "英语")
    out = tmp_path / "export.csv"
    reading_list.export_csv(books, out)
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "title,author,genre,rating"
    titles = [line.split(",")[0] for line in lines[1:]]
    assert titles == sorted(titles, key=str.lower)
    assert titles[0] == "an hour before"


def test_rating_range_validation():
    books = []
    with pytest.raises(ValueError):
        reading_list.add_book(books, "坏书", "某人", "科幻", rating=6)
    with pytest.raises(ValueError):
        reading_list.add_book(books, "坏书", "某人", "科幻", rating=-1)
    reading_list.add_book(books, "零分", "某人", "科幻", rating=0)
    reading_list.add_book(books, "满分", "某人", "科幻", rating=5)
