# Material presets

For your own material, set up a clean test cut and choose **Use Tested
Settings → Save Preset**. You can also edit settings in **Blade** and use
**Save as New**. Both desktop apps use the same presets.

## Sharing a preset

Your presets are JSON files in `~/.config/cut-proxies/presets/`. Copy the
file to that folder on another machine to use it there. Keep a distinct
`name` for each material. A local preset with the same name as a factory
preset takes precedence.

Here’s an example file with made-up settings:

```json
{
  "name": "My matte cardstock",
  "description": "Matte cardstock with a manual blade.",
  "origin": "derived",
  "note": "Example settings. Never cut.",
  "settings": {
    "force": 25,
    "speed": 8,
    "passes": 2,
    "depth": null,
    "holder": 1
  }
}
```

Use a number for AutoBlade depth, or `null` when you set depth by hand.
Use `measured` only after checking a clean cut, and add the date to the
note. Factory values use `silhouette`. Other source labels used in the
project are `guide`, `recovered`, and `derived`.
