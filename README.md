## Автоматизация установки Printum/PrintManager

Добавлен скрипт `install_printum.py`, который:
- подключается по SSH к одному или двум серверам;
- запускает online/offline установку Мониторинга и/или PrintManager;
- передаёт переменные окружения для `install.sh`;
- ожидает готовность сервиса по health endpoint;
- возвращает понятную ошибку при неуспехе.

### Подготовка
1. Скопируйте пример конфига:
   ```bash
   cp config.example.json config.json
   ```
2. Заполните `config.json` под вашу инфраструктуру.

### Запуск
```bash
python3 install_printum.py --config config.json
```

### Dry-run
Проверить сформированные команды без запуска:
```bash
python3 install_printum.py --config config.json --dry-run
```

### Offline-установка
Для offline режима в секции модуля (`monitoring` или `printmanager`) укажите:
- `"mode": "offline"`
- `"archive_path": "/path/to/printum-x.y.z.tar.gz"` (или printmanager)
- опционально `"checksum_path": "/path/to/*.sha512"`
- опционально `"workdir": "/tmp"`

### Расширение под web-настройку
В конфиге есть блок `post_setup` как точка расширения для будущей автоматизации через Selenium/Playwright после завершения установки.
