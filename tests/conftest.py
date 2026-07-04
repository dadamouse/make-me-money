import pytest

from app import twse


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    """測試中關閉外部 API 節流（正式環境 TWSE 1.8s／TPEx 1.0s）。"""
    monkeypatch.setattr(twse, "_TWSE_THROTTLE_SECONDS", 0)
    monkeypatch.setattr(twse, "_TPEX_THROTTLE_SECONDS", 0)
