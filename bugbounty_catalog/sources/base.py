from __future__ import annotations

from abc import ABC, abstractmethod

from bugbounty_catalog.models import Program


class Source(ABC):
    name: str

    @abstractmethod
    def fetch(self) -> list[Program]:
        raise NotImplementedError

    def collect_programs(self) -> list[Program]:
        """Provider-facing name retained separately from transport details."""
        return self.fetch()

    def collect_scope(self, program: Program):
        """Return the explicitly declared scope already attached to a program."""
        return program.assets
