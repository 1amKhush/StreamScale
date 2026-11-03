"""History-only forecasting and predictive capacity control."""

HORIZONS = (10, 20, 30)
QUANTILES = (0.5, 0.8, 0.9, 0.95)
FEATURE_VERSION = 1
DEFAULT_POLICY = {"horizon": 20, "quantile": 0.9, "headroom": 0.15, "downscale": 60}
