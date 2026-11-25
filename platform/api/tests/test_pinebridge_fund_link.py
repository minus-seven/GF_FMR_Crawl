import html
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from companies.pinebridge import pinebridge_fund_link as pinebridge  # noqa: E402


def _fund(name, slug):
    """构造一条 MetLife 基金目录记录；参数 name/slug 对应中文名与链接。"""
    return {
        "zhFundName": name,
        "enFundName": name,
        "urlName": slug,
        "shareClasses": [
            {
                "title": "A",
                "sectionDocuments": {
                    "Fact_Sheet": f"https://assets.pinebridge.com/asset/test/{slug}.PDF"
                },
            }
        ],
    }


def _page(funds):
    """把目录记录编码成 MetLife 页面属性；参数 funds 为基金记录列表。"""
    payload = {"title": "PineBridge Global Funds", "funds": funds}
    encoded = html.escape(json.dumps(payload, ensure_ascii=False), quote=True)
    return (
        '<div class="pinebridge-fund-range-page" '
        f'data-fund-range="{encoded}"></div>'
    )


def test_parse_fund_range_builds_official_page_and_direct_pdf():
    catalog = pinebridge._parse_fund_range_html(
        _page([_fund("柏瑞亞洲高收益總回報債券基金", "pinebridge-asian-high-yield")])
    )

    assert len(catalog) == 1
    record = catalog[0]
    assert record["page_url"] == (
        "https://www.metlife.com/investments/zh-hk/funds/fund/"
        "?fund=pinebridge-asian-high-yield&unit=A"
    )
    assert record["run_command"].endswith(
        "/pinebridge-asian-high-yield.PDF\""
    )


def test_six_unregistered_rows_resolve_from_current_official_names():
    pairs = [
        ("柏瑞美元投资级别债券基金", "柏瑞美元投資級別債券基金", "usd-investment-grade"),
        ("柏瑞亚洲高收益总回报债券基金", "柏瑞亞洲高收益總回報債券基金", "asian-high-yield"),
        ("柏瑞全球债券基金", "柏瑞環球債券基金", "global-bond"),
        ("柏瑞大中华股票基金", "柏瑞大中華股票基金", "greater-china"),
        ("柏瑞日本股票基金", "柏瑞日本股票基金", "japan"),
        ("柏瑞印度股票基金", "柏瑞印度股票基金", "india"),
    ]
    catalog = pinebridge._parse_fund_range_html(
        _page([_fund(official_name, slug) for _, official_name, slug in pairs])
    )
    index = pinebridge._build_index(catalog)

    for row_name, _, slug in pairs:
        page_url, command, reason = pinebridge._resolve_single(
            {"fund": row_name, "umbrella": "柏瑞基金系列"},
            index,
        )
        assert reason == ""
        assert f"fund={slug}" in page_url
        assert slug in command


def test_unpublished_or_terminated_name_is_skipped_with_actionable_reason():
    index = pinebridge._build_index([])

    page_url, command, reason = pinebridge._resolve_single(
        {"fund": "柏瑞环球新兴市场当地货币债券基金", "umbrella": "柏瑞环球基金"},
        index,
    )

    assert page_url == ""
    assert command == ""
    assert "2026-10-08 终止" in reason


def test_provident_only_names_are_not_mapped_to_similar_global_funds():
    """公積金目录无月报时不得猜测为相似策略的全球基金。"""

    index = pinebridge._build_index([])

    for fund_name in ("柏瑞美国股票基金", "柏瑞亚洲基金"):
        page_url, command, reason = pinebridge._resolve_single(
            {"fund": fund_name, "umbrella": "柏瑞基金系列"},
            index,
        )
        assert page_url == ""
        assert command == ""
        assert "公積金单位目录" in reason
        assert "不映射" in reason


def test_legacy_wrong_page_or_non_metlife_link_is_selected_for_refresh():
    """旧柏瑞站链接及 AML402 的错配页链接必须触发重补。"""

    assert pinebridge._needs_refresh(
        "https://www.pinebridge.com/zh-hk/funds/pinebridge-global-bond-fund/a",
        'python download_pinebridge_factsheet.py "https://assets.pinebridge.com/old.PDF"',
    )
    assert pinebridge._needs_refresh(
        "https://www.metlife.com/investments/zh-hk/funds/fund/"
        "?fund=pinebridge-latin-america-equity-fund&unit=a",
        'python download_pinebridge_factsheet.py "'
        "https://assets.pinebridge.com/asset/test/"
        'PineBridge-US-Large-Cap-Research-Enhanced-Fund-Class-A-Units_HKC.PDF"',
    )
    assert not pinebridge._needs_refresh(
        "https://www.metlife.com/investments/zh-hk/funds/fund/"
        "?fund=pinebridge-global-bond-fund&unit=A",
        'python download_pinebridge_factsheet.py "'
        "https://assets.pinebridge.com/asset/test/"
        'PineBridge-Global-Bond-Fund-Class-A-Units_HKC.PDF"',
    )
