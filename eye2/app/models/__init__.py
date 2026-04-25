from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from app.models.asset import InfrastructureAsset  # noqa: E402, F401
from app.models.dependency import InfrastructureDependency  # noqa: E402, F401
