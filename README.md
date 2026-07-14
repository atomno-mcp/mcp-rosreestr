<!-- mcp-name: io.github.atomno-mcp/mcp-rosreestr -->
# mcp-rosreestr

> MCP server for Russian Rosreestr open cadastral data — lookup by cadastral number, address or coordinates; cadastral value with history.

[![PyPI version](https://img.shields.io/pypi/v/atomno-mcp-rosreestr.svg)](https://pypi.org/project/atomno-mcp-rosreestr/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP-compatible](https://img.shields.io/badge/MCP-compatible-blue.svg)](https://modelcontextprotocol.io/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

---

MCP-сервер (Model Context Protocol — открытый протокол интеграции AI-агентов с внешними источниками данных), дающий ИИ-агенту прямой доступ к публичным открытым данным Росреестра РФ через НСПД (Национальная система пространственных данных, `nspd.gov.ru`) и Публичную кадастровую карту (ПКК) как fallback.

Часть семейства MCP-серверов **atomno** ([atomno-mcp.ru](https://atomno-mcp.ru)). Открытый MIT — никакой PII собственников, только публичные характеристики объекта.

## Что умеет

| Тул | Что делает |
|---|---|
| `lookup_by_cadastral` | По кадастровому номеру (например, `77:01:0001066:1234`) возвращает площадь, год постройки, кадастровую стоимость, ВРИ (вид разрешённого использования), адрес. |
| `lookup_by_address` | По свободному тексту адреса возвращает список найденных объектов с их КН (кадастровыми номерами) и confidence score. |
| `lookup_by_coords` | По геокоординатам (lat/lon в WGS84) показывает все объекты в этой точке (земельные участки, здания, помещения). |
| `get_cadastral_value` | Текущая кадастровая стоимость + локально кэшированная история переоценок. |

## Быстрый старт

```bash
# Через uvx (рекомендуется — без установки)
uvx atomno-mcp-rosreestr

# Или через pipx
pipx install atomno-mcp-rosreestr
mcp-rosreestr
```

### Подключение в Cursor

Добавьте в `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "rosreestr": {
      "command": "uvx",
      "args": ["atomno-mcp-rosreestr"]
    }
  }
}
```

### Подключение в Claude Desktop

`%APPDATA%/Claude/claude_desktop_config.json` (Windows) или `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "rosreestr": {
      "command": "uvx",
      "args": ["atomno-mcp-rosreestr"]
    }
  }
}
```

### CLI

```bash
atomno-mcp-rosreestr --help          # usage
atomno-mcp-rosreestr --version       # 0.1.0
atomno-mcp-rosreestr --check-config  # кэш + upstream NSPD/PKK
atomno-mcp-rosreestr --transport http --port 8000  # сетевой транспорт
```

### Health-check

```bash
atomno-mcp-rosreestr --check-config
```

Выведет состояние локального кэша и доступность upstream-сервисов (NSPD, PKK).

## Конфигурация (env vars)

Canonical-имена — с префиксом `MCP_ROSREESTR_`. Legacy `ROSREESTR_*` поддерживаются с DeprecationWarning.

| Переменная | Описание | Default |
|---|---|---|
| `MCP_ROSREESTR_HTTP_TIMEOUT` | HTTP-таймаут для upstream-запросов (секунды) | `15.0` |
| `MCP_ROSREESTR_CACHE_PATH` | Путь к локальной SQLite-базе с кэшем | `./mcp_rosreestr_cache.sqlite` |
| `MCP_ROSREESTR_CACHE_TTL_LIVE` | TTL «живых» данных (стоимость) в секундах | `86400` (24 ч) |
| `MCP_ROSREESTR_CACHE_TTL_STATIC` | TTL «статичных» данных (площадь, год) в секундах | `604800` (7 дней) |
| `MCP_ROSREESTR_USER_AGENT` | Кастомный User-Agent для upstream | `atomno-mcp-rosreestr/<version> ...` |
| `MCP_ROSREESTR_LOG_LEVEL` | Уровень логирования (`DEBUG`…`CRITICAL`) | `INFO` |
| `MCP_ROSREESTR_API_KEY` | Pro-фичи (не требуется для open client) | — |

## Источники данных

- **НСПД** (`nspd.gov.ru`) — основной источник, актуальный геопортал Росреестра.
- **ПКК** (`pkk5.rosreestr.ru`) — fallback (legacy), используется только если НСПД не отвечает или объект не найден.

API оба недокументированные и могут меняться. Клиент написан defensively: при изменении формы ответа поля деградируют до `None`, а не падают.

## Кэширование и ratelimit

- Все ответы кэшируются локально в SQLite с раздельными TTL: 24 часа для волатильных (стоимость), 7 дней для статичных (адрес, площадь, год постройки).
- История кадастровых стоимостей накапливается локально из периодических обращений (open-данные не выдают полный аудит-trail).
- Запросы к upstream — politely-paced, без агрессивного fan-out.

## Тестирование

```bash
git clone https://github.com/atomno-mcp/mcp-rosreestr
cd mcp-rosreestr
pip install -e ".[dev]"
pytest -v
```

## Disclaimer / правовые ограничения

- **Не аффилирован** с Федеральной службой государственной регистрации, кадастра и картографии (Росреестр), НСПД (`nspd.gov.ru`) или Публичной кадастровой картой (ПКК). Независимый open-source клиент к публичным API.
- **Только открытые данные** — кадастровые номера, площадь, адрес, кадастровая стоимость. **Нет ПДн** (персональных данных) собственников, паспортов, телефонов.
- **Законное использование** — due-diligence, оценка недвижимости, аналитика на основе публичных реестров. Не для сбора ПДн, спама или обхода ограничений upstream.
- Данные носят **справочный характер**; для юридически значимых решений заказывайте официальную выписку ЕГРН (Единый государственный реестр недвижимости).
- Upstream API **недокументированы** и могут меняться без предупреждения. Используете на свой риск.

## Лицензия

[MIT](LICENSE) © Atomno

## Ссылки

- [GitHub](https://github.com/atomno-mcp/mcp-rosreestr)
- [PyPI](https://pypi.org/project/atomno-mcp-rosreestr/)
- [Issues / баг-трекер](https://github.com/atomno-mcp/mcp-rosreestr/issues)
- [MCP Catalog (Glama.ai)](https://glama.ai/mcp/servers/atomno-mcp/mcp-rosreestr)
- Семейство atomno MCP: [atomno-mcp.ru](https://atomno-mcp.ru)
