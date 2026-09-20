from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class DatabaseRuntime:
    """集中持有异步 SQLAlchemy 引擎和 Session 工厂。

    ``expire_on_commit=False`` 让应用层在事务提交后仍可读取刚写入对象；请求
    和 Worker 应各自创建短生命周期 Session，不共享 Session 实例。
    """

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def close(self) -> None:
        await self._engine.dispose()

    def __repr__(self) -> str:
        # 连接串可能包含数据库密码，调试输出只暴露固定占位符。
        return f"{self.__class__.__name__}(engine=<redacted>)"
