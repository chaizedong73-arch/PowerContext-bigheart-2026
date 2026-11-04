"""读书清单（reading list）领域模块。"""

import csv


def add_book(books, title, author, genre, rating=None):
    """添加一本书并返回新书目。"""
    if rating is not None and (
        isinstance(rating, bool)
        or not isinstance(rating, int)
        or not 0 <= rating <= 5
    ):
        raise ValueError("rating must be an integer between 0 and 5")

    book = {
        "title": title,
        "author": author,
        "genre": genre,
        "rating": 3 if rating is None else rating,
    }
    books.append(book)
    return dict(book)


def list_books(books):
    """返回书目的浅拷贝列表。"""
    return [dict(b) for b in books]


def filter_by_genre(books, genre):
    """按类型筛选书目，比较不区分大小写并保持原有顺序。"""
    target_genre = genre.casefold()
    return [book for book in books if book["genre"].casefold() == target_genre]


def export_csv(books, path):
    """将书目按标题不区分大小写排序后导出为 UTF-8 编码的 CSV 文件。"""
    with open(path, "w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(("title", "author", "genre", "rating"))
        for book in sorted(books, key=lambda item: item["title"].lower()):
            writer.writerow(
                (book["title"], book["author"], book["genre"], book["rating"])
            )
