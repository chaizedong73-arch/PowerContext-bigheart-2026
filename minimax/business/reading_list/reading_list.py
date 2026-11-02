"""读书清单（reading list）领域模块。

当前版本只提供最基础的添加与列出功能。评分（rating）字段、
类型筛选与 CSV 导出尚未实现，规格见 specs/reading_list_spec_v1.md。
"""


def add_book(books, title, author, genre):
    """添加一本书并返回新书目。"""
    book = {"title": title, "author": author, "genre": genre}
    books.append(book)
    return dict(book)


def list_books(books):
    """返回书目的浅拷贝列表。"""
    return [dict(b) for b in books]
