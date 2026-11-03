"""目标功能测试（对应规格 v1）：实现前失败，按规格实现后应全部通过。"""

import reading_list


def test_add_book_default_rating():
    books = []
    book = reading_list.add_book(books, "三体", "刘慈欣", "科幻")
    assert book["rating"] == 3
    assert books[0]["rating"] == 3


def test_filter_by_genre_case_insensitive():
    books = []
    reading_list.add_book(books, "三体", "刘慈欣", "SciFi")
    reading_list.add_book(books, "哈利波特", "J.K.罗琳", "Fantasy")
    reading_list.add_book(books, "魔戒", "托尔金", "fantasy")
    result = reading_list.filter_by_genre(books, "FANTASY")
    assert [b["title"] for b in result] == ["哈利波特", "魔戒"]


def test_export_csv_insertion_order(tmp_path):
    books = []
    reading_list.add_book(books, "三体", "刘慈欣", "科幻")
    reading_list.add_book(books, "活着", "余华", "文学")
    out = tmp_path / "export.csv"
    reading_list.export_csv(books, out)
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "title,author,genre,rating"
    assert lines[1] == "三体,刘慈欣,科幻,3"
    assert lines[2] == "活着,余华,文学,3"
