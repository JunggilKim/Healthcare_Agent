from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from backend.app.domain.canonical import load_yaml
from backend.app.settings import REPOSITORY_ROOT


class UnitConversionError(ValueError):
    pass


class _UnitDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aliases: list[str]
    family: str
    multiplier: Decimal


class _UnitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    canonical_units: dict[str, _UnitDefinition]


@dataclass(frozen=True)
class UnitConverter:
    config: _UnitConfig

    @classmethod
    def from_path(cls, path: Path) -> UnitConverter:
        return cls(_UnitConfig.model_validate(load_yaml(path)))

    def canonical_unit(self, unit: str) -> str:
        normalized = unit.strip().casefold()
        for canonical, definition in self.config.canonical_units.items():
            if normalized == canonical.casefold() or normalized in {
                alias.casefold() for alias in definition.aliases
            }:
                return canonical
        raise UnitConversionError(f"unsupported unit: {unit}")

    def convert(self, value: Decimal, source_unit: str, target_unit: str) -> Decimal:
        source = self.canonical_unit(source_unit)
        target = self.canonical_unit(target_unit)
        source_definition = self.config.canonical_units[source]
        target_definition = self.config.canonical_units[target]
        if source_definition.family != target_definition.family:
            raise UnitConversionError(f"incompatible units: {source_unit} and {target_unit}")
        return value * source_definition.multiplier / target_definition.multiplier


@lru_cache(maxsize=1)
def default_unit_converter() -> UnitConverter:
    return UnitConverter.from_path(REPOSITORY_ROOT / "config" / "units.yaml")
