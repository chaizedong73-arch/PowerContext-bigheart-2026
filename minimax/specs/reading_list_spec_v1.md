# 读书清单（Reading List）规格 v1

## 领域模型

- 书目使用字典表示，字段：`title`（字符串）、`author`（字符串）、`genre`（字符串）。
- `rating` 字段：0-5 的整数，缺省值为 **3**。
- 模块文件：`business/reading_list/reading_list.py`。

## 功能要求

### add_book(books, title, author, genre, rating=None)

- 向 `books` 追加一条书目并返回新书目。
- 返回的书目必须包含 `rating` 字段；`rating` 未提供时取缺省值 3。

### filter_by_genre(books, genre)

- 按 `genre` 筛选书目，比较不区分大小写。
- 结果保持原有顺序，返回新列表。

### export_csv(books, path)

- 写出 UTF-8 编码的 CSV 文件。
- 表头固定为：`title,author,genre,rating`。
- v1 要求：数据行顺序与 `books` 的插入顺序一致。
