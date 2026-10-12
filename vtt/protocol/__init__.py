"""Canonical wire-schema validation and strict JSON for REST and Socket.IO."""

from .boundary import ContractRegistry, ProtocolValidationError, StrictJSON

__all__ = ["ContractRegistry", "ProtocolValidationError", "StrictJSON"]
