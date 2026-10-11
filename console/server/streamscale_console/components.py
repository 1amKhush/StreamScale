"""Feature components attached to the console; later milestones add their own."""


def attach(console):
    """Attach optional components (sale, scaling, faults, sessions) when present."""
    import importlib

    for name in ("sales", "scaling", "faults", "sessions", "history"):
        try:
            module = importlib.import_module(f"streamscale_console.{name}")
        except ModuleNotFoundError as error:
            if error.name != f"streamscale_console.{name}":
                raise
            continue
        console.components.append(module.Component(console))
