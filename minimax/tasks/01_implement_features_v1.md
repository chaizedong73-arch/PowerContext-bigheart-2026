任务：按照当前有效规格实现读书清单的缺失功能。

当前 `business/reading_list/reading_list.py` 只有 add_book 与 list_books，
缺少规格 v1 要求的 rating 字段、filter_by_genre 与 export_csv。

目标测试 `business/reading_list/tests/test_target_features.py` 当前失败。
请以 seekdb 检索到的最新规格为准实现这三个能力，使目标测试全部通过，
并保持回归测试 `business/reading_list/tests/test_regression.py` 全部通过。
