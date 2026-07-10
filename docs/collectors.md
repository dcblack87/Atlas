# Writing a collector

A collector is one Python file in `atlas/collectors/`, registered with a decorator:

```python
from atlas.collectors.base import Collector, register
from atlas.model import Observation, Sample


@register
class SwapCollector(Collector):
    name = "swap"
    interval = 120  # seconds

    async def collect(self, transport, host, inventory) -> Observation:
        result = await transport.run(["sh", "-c", "cat /proc/swaps"])
        used = _parse_swaps(result.stdout)
        return Observation(
            samples=[Sample("swap.used_bytes", used, entity=f"host:{host.name}")]
        )
```

Guidelines:

- **One composite command per run** where possible. Join reads with a
  sentinel separator and parse locally — SSH channels are cheap but not free.
- **`analyze()` must be pure.** It receives the observation plus recent
  history and returns findings. Purity makes it trivially testable against
  recorded fixtures in `tests/fixtures/`.
- **Never construct a mutating command.** Collectors are read-only by
  architectural invariant (enforced by a test). If a collector needs a fix
  applied, it emits a finding with a suggested remediation template instead.
- **Record a fixture.** Capture the real command output once, commit it under
  `tests/fixtures/<collector>/`, and write the parser test against it.
