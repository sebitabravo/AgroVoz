"""Modelos SQLAlchemy para la base de datos.

Todos los modelos heredan de `app.core.database.Base` y se registran
automáticamente para migraciones con Alembic (autogenerate).
"""

from app.models.consultation import Consultation
from app.models.odepa_price import OdepaPrice

__all__ = ["Consultation", "OdepaPrice"]
