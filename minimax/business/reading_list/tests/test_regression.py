"""回归基线测试：既有功能在每次修改前后都必须通过。"""

import reading_list


def test_add_book_appends():
    books = []
    reading_list.add_book(books, "三体", "刘慈欣", "科幻")
    assert len(books) == 1
    book = books[0]
    assert book["title"] == "三体"
    assert book["author"] == "刘慈欣"
    assert book["genre"] == "科幻"


def test_add_book_returns_copy():
    books = []
    returned = reading_list.add_book(books, "三体", "刘慈欣", "科幻")
    assert returned == books[0]
    assert returned is not books[0]


def test_list_books_empty():
    assert reading_list.list_books([]) == []


def test_list_books_returns_copy():
    books = []
    reading_list.add_book(books, "三体", "刘慈欣", "科幻")
    listed = reading_list.list_books(books)
    listed.append({"title": "x", "author": "y", "genre": "z"})
    assert len(books) == 1
