# Desktop copy

`traffic-ui-en` selects English at application startup; `traffic-ui` and `ui` select
Chinese. No persistent preference or live global language switch is used.

- `en.json` maps exact Chinese display strings to English.
- `en-patterns.json` handles complete dynamic display strings. Patterns use full
  matches, and numbered placeholders refer to captured fields. Captured fields
  remain literal unless their index is listed in `translate` (for known UI states).
- Unknown service messages remain unchanged. Do not add broad substring replacement
  or translate model input, model responses, identifiers, or editable values.

Views use the caption adapters in `ui.localized_widgets`. Native dialogs, painter
text, and generated plain-text summaries call `tr()` explicitly. Bilingual
TrafficForge components keep their existing explicit language handling.

For user-provided labels, construct an empty widget, set `literalText=True`, then
set its text. Use `literalItems=True` for user-named combo boxes and tabs; translate
their built-in empty-state captions explicitly. Raw text editors are not adapted.

Run `pytest tests/unit/ui/test_interface_language.py` after changing the catalog
or display adapters. Extend the data-preservation checks when adding display paths.
