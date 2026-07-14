"""MCP server for Russian Rosreestr open cadastral data.

Wraps the public NSPD (Национальная Система Пространственных Данных) API
and the legacy ПКК (Публичная Кадастровая Карта) endpoint, with a local
SQLite cache and polite-bot HTTP behaviour.
"""

__version__ = "0.1.6"
__all__ = ["__version__"]
