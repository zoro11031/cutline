# Bundled presets

Drop a `.json` file here to ship a preset with the project. A file in
`~/.config/cut-proxies/presets/` with the same `name` overrides it, and both
override the built-ins in `cut_proxies/presets.py`.

```json
{
  "name": "330 gsm black-core",
  "description": "One line, shown in the picker.",
  "origin": "derived",
  "note": "Where these numbers came from. A 'derived' preset must say 'never cut'.",
  "settings": {
    "force": 30,
    "speed": 3,
    "passes": 3,
    "depth": null,
    "acceleration": 1,
    "blade_diameter_mm": 0.9,
    "holder": 1,
    "lift_head": true,
    "sharpen_corners": false,
    "media": 135
  }
}
```

`origin` must be one of `silhouette`, `recovered`, `derived` or `measured`.
Only use `measured` once a sheet has actually been cut on these numbers and
the edge has been looked at; put the date in the note.

`force` and `speed` are the machine's own unitless indices, the same numbers
Silhouette Studio shows. There is no published mapping to grams or mm/s.
