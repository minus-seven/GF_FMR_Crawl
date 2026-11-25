"""Shared interactive selector for factsheet batch folders."""

from __future__ import annotations

from pathlib import Path

from batch_layout import is_batch_root, pdf_categories_with_files, pdf_category_dir


def available_batch_roots(data_dir: Path) -> list[Path]:
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        return []
    return sorted(
        (
            child
            for child in data_dir.iterdir()
            if child.is_dir()
            and not child.name.startswith("_")
            and is_batch_root(child)
        ),
        key=lambda child: (child.name, child.stat().st_mtime),
        reverse=True,
    )


def select_batch_root(
    data_dir: Path,
    *,
    input_func=input,
    print_func=print,
    action_name: str = "处理",
) -> Path | None:
    batches = available_batch_roots(data_dir)
    if not batches:
        raise FileNotFoundError(f"找不到可处理的批次文件夹：{data_dir}")

    print_func("")
    print_func(f"请选择需要进行{action_name}的批次：")
    print_func("-" * 72)
    for index, batch in enumerate(batches, start=1):
        categories = pdf_categories_with_files(batch)
        counts = [
            len(list(pdf_category_dir(batch, category).glob("*.pdf")))
            for category in categories
        ]
        details = "｜".join(
            f"{category} {count}" for category, count in zip(categories, counts)
        )
        details = details or "暂无 PDF"
        newest = "（最新）" if index == 1 else ""
        print_func(
            f"  {index:>2}. {batch.name}{newest}  "
            f"{details}｜合计 {sum(counts)}"
        )
    print_func("-" * 72)

    while True:
        try:
            answer = str(
                input_func("输入序号；直接回车选择最新批次；输入 Q 取消：") or ""
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print_func(f"\n已取消{action_name}。")
            return None
        if not answer:
            return batches[0]
        if answer.lower() == "q":
            print_func(f"已取消{action_name}。")
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(batches):
            return batches[int(answer) - 1]
        print_func(f"请输入 1 到 {len(batches)} 之间的序号，或输入 Q 取消。")
