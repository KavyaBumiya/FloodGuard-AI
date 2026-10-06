"""FLOODGUARD AI — external-data connector layer.

Deliberately flood-only. This project models one hazard (river flooding in
the Mithi River corridor); there is no seismic, fire, storm, traffic, social or
satellite connector, and none should be added without a corresponding data
source and a labelled use for it.

Package layout:
    integrations/
        cache.py           — Redis cache helpers for provider responses
        weather/
            open_meteo.py   — FORECAST only (never used as an observation)
"""