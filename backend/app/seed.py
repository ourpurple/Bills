from sqlalchemy import select

from .models import Bank

BANKS = [
    ("cmb", "招商银行", "merged"),
    ("icbc", "工商银行", "merged"),
    ("hxb", "华夏银行", "merged"),
    ("cmbc", "民生银行", "merged"),
    ("spdb", "浦发银行", "merged"),
    ("abc", "农业银行", "independent"),
    ("bocom", "交通银行", "independent"),
    ("cib", "兴业银行", "independent"),
    ("citic", "中信银行", "independent"),
    ("cgb", "广发银行", "independent"),
    ("boc", "中国银行", "independent"),
    ("ccb", "建设银行", "independent"),
    ("psbc", "邮储银行", "independent"),
]


def seed_banks(db):
    for code, name, mode in BANKS:
        if not db.scalar(select(Bank).where(Bank.code == code)):
            db.add(Bank(code=code, name=name, default_mode=mode))
    db.flush()
