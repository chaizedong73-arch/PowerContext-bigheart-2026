任务：规格已更新到 v2，请按更新后的规格调整实现。

v2 相对 v1 的变更：
1. `export_csv` 数据行必须按 title 不区分大小写的字母序排序；
2. `add_book` 必须校验显式传入的 rating 在 0-5 范围内（整数），否则抛出 ValueError。

目标测试 `business/reading_list/tests/test_spec_update.py` 当前失败。
请以 seekdb 检索到的最新规格（v2）为准修改实现，使目标测试全部通过，
并保持 `test_target_features.py` 与 `test_regression.py` 等回归测试全部通过。
