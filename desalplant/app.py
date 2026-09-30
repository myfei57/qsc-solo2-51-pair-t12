"""Wiring of the console and its subsystems from a configuration."""

from dataclasses import dataclass

from .alarms.registry import AlarmRegistry
from .audit.recorder import Recorder
from .clock import LogicalClock
from .config import Config
from .console.plant import Plant
from .console.router import Console
from .store.snapshot import Snapshot
from .store.store import Store


@dataclass
class Application:
    """Everything the process needs to serve the console."""

    config: Config
    store: Store
    clock: LogicalClock
    recorder: Recorder
    registry: AlarmRegistry
    plant: Plant
    console: Console

    def snapshot(self) -> Snapshot:
        """Frozen view of the persisted document."""
        return self.store.snapshot()


def build_application(config: Config) -> Application:
    """Build the service from a configuration."""
    store = Store(config.store_path())
    clock = LogicalClock()
    recorder = Recorder(store, clock)
    registry = AlarmRegistry(store, clock, recorder)
    plant = Plant(store, clock, recorder, registry)
    console = Console(plant)
    return Application(
        config=config,
        store=store,
        clock=clock,
        recorder=recorder,
        registry=registry,
        plant=plant,
        console=console,
    )
