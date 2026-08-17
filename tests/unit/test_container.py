import pytest

from core.config import Settings, get_settings
from core.container import Container, get_container, reset_container
from service.cloudwatch_service import CloudWatchService
from service.database_service import DatabaseService
from service.newrelic_service import NewRelicService


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    reset_container()
    get_settings.cache_clear()
    yield
    reset_container()
    get_settings.cache_clear()


def test_get_container_returns_same_instance_across_calls():
    assert get_container() is get_container()


def test_reset_container_forces_a_fresh_container():
    first = get_container()
    reset_container()
    second = get_container()
    assert first is not second


def test_container_caches_each_service_instance():
    container = Container()
    assert container.database_service is container.database_service
    assert container.cloudwatch_service is container.cloudwatch_service
    assert container.newrelic_service is container.newrelic_service


def test_container_builds_the_expected_service_types():
    container = Container()
    assert isinstance(container.database_service, DatabaseService)
    assert isinstance(container.cloudwatch_service, CloudWatchService)
    assert isinstance(container.newrelic_service, NewRelicService)


def test_container_uses_get_settings_by_default():
    container = Container()
    assert container.settings is get_settings()


def test_container_accepts_settings_override():
    custom = Settings(db_url="postgresql+asyncpg://example/test")
    container = Container(settings=custom)
    assert container.settings is custom
    assert container.database_service.settings is custom


def test_container_accepts_a_pre_built_service():
    fake_nr_service = NewRelicService(get_settings())
    container = Container(newrelic_service=fake_nr_service)
    assert container.newrelic_service is fake_nr_service
