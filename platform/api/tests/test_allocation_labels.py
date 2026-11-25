from app.services.allocation_labels import canonical_allocation_label, merge_allocation_rows


def test_cash_labels_are_merged_across_simplified_traditional_and_english() -> None:
    labels = ["现金", "现金及其他", "現金及其他", "Cash and other net assets"]
    assert {canonical_allocation_label(label) for label in labels} == {"现金及其他"}


def test_asset_type_keyword_groups_do_not_merge_unrelated_categories() -> None:
    assert canonical_allocation_label("Debt securities") == "债券/固定收益"
    assert canonical_allocation_label("Time deposits") == "存款"
    assert canonical_allocation_label("Money market funds") == "货币市场工具"
    assert canonical_allocation_label("Unlisted/quoted investments") == "Unlisted/quoted investments"


def test_merge_allocation_rows_sums_same_keyword_group() -> None:
    rows = merge_allocation_rows(
        [
            {"label": "现金", "value": 10},
            {"label": "現金及其他", "value": 5.25},
            {"label": "现金和等价物", "value": 4.75},
            {"label": "债券", "value": 20},
            {"label": "债券/固定收益", "value": 3},
        ]
    )
    assert rows == [
        {"label": "现金及其他", "value": 20.0},
        {"label": "债券/固定收益", "value": 23.0},
    ]
