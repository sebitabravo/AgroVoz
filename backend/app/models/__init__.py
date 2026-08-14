"""Modelos SQLAlchemy para la base de datos.

Todos los modelos heredan de `app.core.database.Base` y se registran
automáticamente para migraciones con Alembic (autogenerate).
"""

from app.models.alert import Alert
from app.models.consultation import Consultation
from app.models.data_hub import DataFact, DataSource
from app.models.directorio_agricola import DirectorioAgricola
from app.models.expense import Expense
from app.models.odepa_price import OdepaPrice
from app.models.parcela import Parcela
from app.models.user_prefs import UserPrefs

__all__ = [
    "Alert",
    "Consultation",
    "DataFact",
    "DataSource",
    "DirectorioAgricola",
    "Expense",
    "OdepaPrice",
    "Parcela",
    "UserPrefs",
]
