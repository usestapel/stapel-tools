"""Инфраструктурная строчка обязана быть ЗАПУСКАЕМОЙ, а не правдоподобной.

Инцидент 07.08.2026, стенд ironmemo. В компоуз внесли
``command: ["-m", "8222", "--max_payload", "8388608"]``. Выглядит разумно,
проходит `docker compose config -q`, проходит любой YAML-линтер. Флага
``--max_payload`` у nats-server НЕ СУЩЕСТВУЕТ: он печатает
"flag provided but not defined: -max_payload", выводит usage и выходит с
нулём. Контейнер ушёл в рестарт-петлю, и всё, что к нему подключается,
начало получать ``Name or service not known``.

Поймали это не тесты и не гейты, а падение стенда.

Класс шире NATS: НИ ОДИН гейт флота не проверял, что сгенерированный
инфраструктурный сервис вообще стартует. Компоуз валидировался как
документ — что ключи на месте и YAML разбирается, — а не как программа.

Здесь пришпилены два свойства:

1. генератор не выдаёт `max_payload` флагом (тот конкретный дефект);
2. у сервиса, которому нужен конфиг-файл, этот файл РЕАЛЬНО пишется
   скаффолдом — иначе docker создал бы на его месте каталог, и
   nats-server снова не нашёл бы конфига, просто молча.

Живой запуск — в e2e-джобе ci.yml: она поднимает nats и требует, чтобы он
остался жив. Юнит-тест ловит дешевле и раньше, живой прогон — честнее.
"""
import tempfile
from pathlib import Path

import pytest
import yaml

from stapel_tools import _compose_templates as ct


class TestГенераторНеВыдаётНесуществующийФлаг:
    def test_max_payload_не_уходит_в_командную_строку(self):
        # Точная сигнатура инцидента. Смотрим на РАЗОБРАННУЮ команду, а не на
        # текст блока: в комментариях слово `--max_payload` стоит законно —
        # там объяснено, почему его там быть не должно. Первая редакция этого
        # теста искала подстроку и падала на собственном комментарии.
        command = yaml.safe_load(ct.NATS_SERVICE_BLOCK)["nats"]["command"]
        assert not any("max_payload" in arg for arg in command), command

    def test_nats_запускается_конфиг_файлом(self):
        block = yaml.safe_load(ct.NATS_SERVICE_BLOCK)["nats"]
        assert block["command"] == ["-c", "/etc/nats/nats.conf"]

    def test_потолок_объявлен_в_конфиге_а_не_потерян(self):
        # Отказ от флага не должен означать отказ от самой настройки:
        # 1 МиБ по умолчанию — это исходный дефект загрузки файлов.
        assert "max_payload: 8MB" in ct.NATS_CONF

    def test_конфиг_несёт_порты_под_healthcheck(self):
        # healthcheck компоуза стучится в 8222/healthz; конфиг обязан этот
        # порт открыть, иначе сервис поднимется и будет вечно unhealthy.
        assert "http_port: 8222" in ct.NATS_CONF
        assert "port: 4222" in ct.NATS_CONF

    def test_jetstream_не_потерян_при_переезде_с_флагов(self):
        # `--jetstream --store_dir /data` были ВЕРНЫМИ флагами; переезжая на
        # конфиг, их легко было потерять вместе с неверным.
        assert "jetstream" in ct.NATS_CONF
        assert "store_dir: /data" in ct.NATS_CONF


class TestФайлКоторыйМонтируютСуществует:
    """Смонтированный, но не созданный путь docker делает КАТАЛОГОМ."""

    @pytest.fixture
    def project(self):
        from stapel_tools.create_project import create_project

        with tempfile.TemporaryDirectory() as tmp:
            create_project(
                name="natsproj", project_type="monolith", title="Nats",
                url="https://x.dev", company_name="X", company_email="x@x.dev",
                modules=["core"], output_dir=Path(tmp), use_submodules=False,
                init_git=False, broker="nats",
            )
            yield Path(tmp) / "natsproj"

    def test_скаффолд_пишет_конфиг_который_монтирует(self, project):
        conf = project / "nats" / "nats.conf"
        assert conf.is_file(), "компоуз монтирует nats/nats.conf — файла нет"
        assert "max_payload" in conf.read_text(encoding="utf-8")

    def test_путь_монтирования_совпадает_с_написанным(self, project):
        compose = yaml.safe_load(
            (project / "docker-compose.base.yml").read_text(encoding="utf-8")
        )
        mounts = compose["services"]["nats"]["volumes"]
        source = next(m.split(":")[0] for m in mounts if "nats.conf" in m)
        # Именно эта сверка ловит опечатку в пути, которую YAML-валидатор
        # пропускает, а docker превращает в пустой каталог.
        assert (project / source.lstrip("./")).is_file()
