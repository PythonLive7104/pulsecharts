"""Broker client implementations for auto-trade execution.

`base.py` defines the interface the execution engine codes against; concrete
exchanges (currently only Bybit) implement it. `get_client(connection)` returns
the right client for a BrokerConnection.
"""

from .base import BrokerClient, BrokerError, VerifyResult


def get_client(connection) -> "BrokerClient":
    """Return a connected broker client for a BrokerConnection."""
    from apps.auto_trade.models import BrokerConnection

    from .bybit import BybitClient

    api_key, api_secret = connection.get_credentials()
    if connection.broker == BrokerConnection.Broker.BYBIT:
        return BybitClient(api_key, api_secret, testnet=connection.testnet)
    raise BrokerError(f"unsupported broker: {connection.broker}")


__all__ = ["BrokerClient", "BrokerError", "VerifyResult", "get_client"]
