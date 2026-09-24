"""Application-owned tool registration: no dynamic imports from model input."""

from friday.tools import local_clock
from friday.tools.registry import NoArguments, ToolRegistry, ToolSpec
from friday.tools.weather import WeatherService, register_weather


def _read_clock(_arguments: NoArguments) -> dict[str, str]:
    # Resolve through the module for deterministic monkeypatches and clock tests.
    return {"status": "ok", **local_clock.read_local_clock()}


def build_builtin_registry(
    *, enable_local_clock: bool = False, enable_weather: bool = False
) -> ToolRegistry:
    registry = ToolRegistry()
    if enable_local_clock:
        registry.register(
            ToolSpec(
                name=local_clock.CLOCK_TOOL_NAME,
                description=local_clock.CLOCK_FUNCTION_DECLARATION["description"],
                arguments=NoArguments,
                handler=_read_clock,
                notice="Read computer local clock",
            )
        )
    if enable_weather:
        register_weather(registry, WeatherService())
    return registry
