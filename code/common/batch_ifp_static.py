"""第五批小规模管理人共享的 HKEX IFP 静态目录适配器。"""
from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common.queue_static_adapter import build_queue_adapter

IFP = "https://ifp.hkex.com.hk/fund-repository/fund/"

MANAGER_PAGES: dict[str, dict[str, str]] = {
    "民银资本": {"CMBC Aggregate Greater China Select Bond Fund": IFP + "BPM390", "CMBC Aggregate Greater China Strategy Fund": IFP + "BRK074"},
    "永明": {"Sun Life AM Global Low Carbon Index Fund": IFP + "BTA105", "Sun Life AM Hong Kong ESG Index Fund": IFP + "BPT918"},
    "申万宏源": {"Shenyin Wanguo RMB Mainland Investment Fund": IFP + "AYH568", "Shenyin Wanguo RQFII A Share Strategy Fund": IFP + "BEZ532"},
    "罗安贝": {"Lord Abbett Ultra Short Bond Fund": IFP + "BYG171", "Lord Abbett Short Duration Income Fund": IFP + "BYG172"},
    "英仕曼": {"Man High Yield Opportunities": IFP + "BSZ457", "Man AHL Diversified Futures Ltd": IFP + "AEH595"},
    "融通基金": {"Rongtong SZSE100 Index Fund": IFP + "BHC250", "Rongtong Health Care Industry Mixed Fund": IFP + "BGU579"},
    "誉信": {"RAM Hong Kong Government Bond Fund": IFP + "BVH346", "RAM Income Fund": IFP + "BVH347"},
    "兴证国际": {"CISI Global USD Money Market Fund": IFP + "BVB648", "China Core Asset Fund": IFP + "BOG456", "CISI Global HKD Money Market Fund": IFP + "BVB649", "CISI Select Global Investment Grade Bond Fund": IFP + "BXS464"},
    "华富建业": {"Quam Plus Greater China UCITS Fund": IFP + "BUH322"},
    "国信": {"Guosen RMB Renaissance Fund": IFP + "AYJ267"},
    "国投证券": {"SDICSI USD Money Market Fund": IFP + "BVI043"},
    "国泰": {"Guotai USD Money Market Fund": IFP + "BWR480"},
    "国泰全球": {"Guotai Select Bond Fund": IFP + "BVI358"},
    "国海富兰克林": {"Franklin Sealand China Prospect Mixed Assets Fund": IFP + "BFY656"},
    "小飞": {"MicroBit Bitcoin & Gold Value ETF": IFP + "BYC020"},
    "工银": {"ICBC Asset Management (Global) Multi-Asset Fund": IFP + "BWI063"},
    "巴克莱": {"Shiller US Sector Index Fund": IFP + "BSH885"},
    "弘收投资": {"Income Partners Managed Volatility High Yield Bond Fund": IFP + "BLB993"},
    "日兴资产": {"Amova ARK Disruptive Innovation Fund": IFP + "BWS372"},
    "日兴资管": {"Amova Asian Small Mid Equity Fund": IFP + "BHY161"},
    "时富金融": {"CASH Prime Value Equity OFC": IFP + "BRS794"},
    "民银": {"CMBC Aggregate USD Money Market Fund": IFP + "BWG927"},
    "永丰金": {"SinoPac USD Money Market Fund": IFP + "BPQ737"},
    "法盛": {"Loomis Sayles Multisector Income Fund": IFP + "BXN385"},
    "简坤": {"GPTX HKD Money Market Fund": IFP + "BWG883"},
    "融通国际": {"RT USD Money Market Fund": IFP + "BVV171"},
    "资威": {"i Capital China Fund": IFP + "BJB721"},
    "辉立资本": {"Phillip HKD Money Market Fund": IFP + "BIS317"},
    "银河": {"China Galaxy Global Income Fund": IFP + "BWH919"},
    "长盛基金": {"Changsheng Electronic Information Industry Mixed Securities Investment Fund": IFP + "BHY099"},
    "鹏格斯资本": {"CFund - China Equity": IFP + "BNJ537"},
}


def bind(manager: str):
    """绑定一个管理人的静态目录；参数 manager 为工作簿页表管理人名称。"""
    pages = MANAGER_PAGES[manager]
    downloader = f'python code/companies/muzinich/download_static_factsheet.py "{{url}}" "{{english}}" {manager}'
    return build_queue_adapter(
        sheet_name=manager,
        description=f"按港交所 IFP 官方基金页补全{manager}入口",
        downloader=downloader,
        pages=pages,
        noise=("fund",),
    )
