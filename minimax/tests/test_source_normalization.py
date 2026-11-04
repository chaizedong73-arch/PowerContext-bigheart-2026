"""规格来源归一化单元测试：同一规格的多个版本必须归并同一 source。

回归背景（评测反馈缺陷）：import-spec 曾直接用文件名词干作 source，
reading_list_spec_v1 / reading_list_spec_v2 变成两个不同来源，
"按来源取最新版本"永远过滤不掉 v1 失效分块。
"""

from pathlib import Path

from ctxsdd.cli import default_source, normalize_source, version_from_stem


def test_default_source_strips_version_suffix():
    assert default_source(Path("specs/reading_list_spec_v1.md")) == "reading_list_spec"
    assert default_source(Path("specs/reading_list_spec_v2.md")) == "reading_list_spec"


def test_default_source_without_suffix_unchanged():
    assert default_source(Path("specs/reading_list_spec.md")) == "reading_list_spec"


def test_default_source_falls_back_to_stem_when_all_version_like():
    # 极端情况：去掉版本后缀后为空，则保留原词干
    assert default_source(Path("specs/v1.md")) == "v1"


def test_version_from_stem():
    assert version_from_stem("reading_list_spec_v2") == 2
    assert version_from_stem("spec-v12") == 12
    assert version_from_stem("reading_list_spec") is None


def test_normalize_source_merges_legacy_names():
    # 历史数据里以"词干+版本"入库的 source 也能归并到同一来源
    assert normalize_source("reading_list_spec_v1") == "reading_list_spec"
    assert normalize_source("reading_list_spec_v2") == "reading_list_spec"
    assert normalize_source("reading_list_spec") == "reading_list_spec"
    assert normalize_source(None) == ""
