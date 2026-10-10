from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from .core import Settings
from .database import make_engine, make_session_factory
from .models import Administrator
from .security import PASSWORDS
from .seed import seed_banks


def main():
    parser = argparse.ArgumentParser(description="Bills 本地管理命令")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init-db", help="迁移数据库并初始化银行")
    create = sub.add_parser("create-admin", help="交互创建管理员，不提供默认密码")
    create.add_argument("--username", default="admin")
    reset = sub.add_parser("reset-password", help="本地管理员密码恢复，撤销旧会话")
    reset.add_argument("--username", default="admin")
    backup = sub.add_parser("backup", help="一致性备份 SQLite 数据库")
    backup.add_argument("--output", required=True, type=Path)
    sub.add_parser("export-openapi", help="导出开发接口契约")
    sub.add_parser("generate-key", help="生成主密钥；仅本机显示一次，请安全保存，勿提交仓库")
    args = parser.parse_args()
    if args.action == "generate-key":
        from cryptography.fernet import Fernet

        print(Fernet.generate_key().decode("ascii"))
        return
    settings = Settings()
    settings.validate_runtime()
    if args.action == "backup":
        from .backup import backup_database

        print(
            f"备份完成：{backup_database(settings.database_url, args.output)}。仅数据库；还需保存资料目录与主密钥。含私人数据和凭证，请安全保管。"
        )
        return
    if args.action == "export-openapi":
        from .main import create_app

        print(json.dumps(create_app(settings).openapi(), ensure_ascii=False, indent=2))
        return
    if args.action == "init-db":
        command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    engine = make_engine(settings)
    with make_session_factory(engine)() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if args.action == "init-db":
            seed_banks(db)
            print("数据库迁移及 13 家银行初始化完成；已有配置不会被覆盖。")
        else:
            username = args.username.strip()
            if not username or len(username) > 80:
                parser.error("用户名需为 1–80 个字符")
            user = db.scalar(select(Administrator).where(Administrator.username == username))
            any_user = db.scalar(select(Administrator.id))
            if args.action == "create-admin" and any_user:
                parser.error("首版仅一个管理员；如需恢复密码请使用 reset-password")
            if args.action == "reset-password" and not user:
                parser.error("管理员不存在")
            password = getpass.getpass("管理员密码（至少 12 位）：")
            if len(password) < 12 or len(password) > 256 or password != getpass.getpass("再次输入："):
                parser.error("密码长度不符合要求或两次输入不一致")
            if user:
                from .core import utc_naive
                from .models import Credential

                user.password_hash = PASSWORDS.hash(password)
                for credential in db.scalars(select(Credential)).all():
                    credential.revoked_at = utc_naive()
            else:
                db.add(Administrator(username=username, password_hash=PASSWORDS.hash(password)))
            print("管理员配置完成。密码不会保存到配置文件或命令行参数中。")
        db.commit()
    engine.dispose()


if __name__ == "__main__":
    main()
